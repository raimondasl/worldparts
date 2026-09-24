"""Reference executor: run a task's reference steps on worldparts and return the answers.

Ops (executed in order on one :class:`worldparts.System` built from ``reference.system``):

- ``solve``: steady solve; ``read`` maps answer keys to variable paths.
- ``set``: ``values`` as for the MCP ``set_values`` tool.
- ``solve_for``: goal seek with exactly the MCP ``solve_for`` semantics (Brent's method on
  ``vary`` between ``lower`` and ``upper``; ``vary`` stays at the root); ``read`` is taken
  from the steady result at the root.
- ``simulate``: ``duration``, ``step`` (default 1 s), ``events`` (set or ramp events, passed
  to ``System.simulate`` unchanged, as the MCP ``simulate`` tool does), ``restore`` (default
  false, as in MCP); reads ``read_final`` / ``read_min`` / ``read_max`` (paths),
  ``read_total`` (a path or a list of paths: the sum of their time integrals, e.g. a pumped
  volume or an energy, see :func:`time_integral`) and ``read_first_warning``
  (``{warning: <instance>.<code>, unit: min}``: time of the first occurrence).
- ``answer``: constant ``values`` (choice and boolean answers, constants).

Every read is converted to the answer's unit. Paths that are not in a solve result
(string or table parameters) are read with ``System.get``.
"""

from __future__ import annotations

from typing import Any

from scipy.optimize import brentq

import worldparts as wp
from worldparts.units import (
    convert,
    is_pressure_unit,
    is_temperature_unit,
    parse_value,
    same_dimension,
)

from .tasks import Task


class ReferenceStepError(Exception):
    """A reference step failed; the message names the task and the step."""


def _read(system: wp.System, result: wp.SolveResult, path: str, unit: str | None) -> float:
    if path in result:
        value = result.get(path, unit) if unit else result[path]
    else:
        value = system.get(path, unit) if unit else system.get(path)
    if value is None:
        raise ReferenceStepError(f"'{path}' is undefined (null)")
    return float(value)


def time_integral(sim: wp.SimulationResult, path: str, unit: str | None) -> float:
    """The time integral of ``path`` over a simulation, in ``unit`` (or its unit times s).

    Each sample's value holds over the step that follows it: the solve at a sample drives
    the explicit-Euler storage integration to the next sample (design 5.5), so the integral
    is ``sum(v[k] * (t[k+1] - t[k]))`` and the last sample drives no step. For a flow into a
    tank this is exactly the volume the tank receives. ``unit`` must have the dimension of
    the variable's unit times time (a flow in m3/h integrates to m3, a power in kW to kWh).
    Pressures and temperatures (offset units) are rejected.
    """
    if path not in sim.series:
        raise ReferenceStepError(f"'{path}' is not recorded")
    var_unit = sim.units[path]
    if is_pressure_unit(var_unit) or is_temperature_unit(var_unit):
        raise ReferenceStepError(
            f"read_total integrates flows and powers; '{path}' is in {var_unit}"
        )
    values = [float(v) for v in sim.get(path) if v is not None]
    t = sim.time
    if len(values) != len(t):
        raise ReferenceStepError(f"'{path}' is undefined at some samples")
    total = sum(values[k] * (t[k + 1] - t[k]) for k in range(len(t) - 1))
    integral_unit = f"{var_unit}*s"
    if unit is None:
        return total
    if not same_dimension(integral_unit, unit):
        raise ReferenceStepError(
            f"the time integral of '{path}' ({var_unit} x time) cannot be given in '{unit}'"
        )
    return float(convert(total, integral_unit, unit))  # type: ignore[arg-type]


def solve_for(
    system: wp.System, target: str, value: Any, vary: str, lower: Any, upper: Any
) -> wp.SolveResult:
    """The MCP ``solve_for`` tool's algorithm, on a Python ``System``.

    Kept identical to ``worldparts.mcp_server`` (same bracket evaluation order, Brent's
    method with the same tolerances), so the reference answers are what an agent gets from
    the tool.
    """
    original = system.get(vary)
    inst, _, local = vary.partition(".")
    spec = system.manifest(inst).variable(local)
    if not spec.is_numeric:
        raise ReferenceStepError(f"vary '{vary}' is not numeric")
    lo = float(spec.parse(lower, "lower"))
    hi = float(spec.parse(upper, "upper"))
    if not lo < hi:
        raise ReferenceStepError(f"lower ({lo:g}) must be below upper ({hi:g})")
    goal: list[float] = []

    def reached(x: float) -> float:
        system.set_values({vary: x})
        result = system.solve()
        got = result[target]
        if not goal:
            goal.append(
                parse_value(value, result.units[target], "value", result.references.get(target))
                if isinstance(value, str)
                else float(value)
            )
        if got is None:
            raise ReferenceStepError(f"'{target}' is undefined at {vary} = {x:g}")
        return float(got) - goal[0]

    try:
        f_lo = reached(lo)
        f_hi = reached(hi)
        if f_lo * f_hi > 0.0:
            raise ReferenceStepError(
                f"solve_for: '{target}' does not cross {goal[0]:g} between {vary} = {lo:g} "
                f"({f_lo + goal[0]:.6g}) and {hi:g} ({f_hi + goal[0]:.6g})"
            )
        if f_lo == 0.0:
            root = lo
        elif f_hi == 0.0:
            root = hi
        else:
            root = float(
                brentq(reached, lo, hi, xtol=1e-12 * max(abs(lo), abs(hi), 1e-12), rtol=1e-12)
            )
        system.set_values({vary: root})
        return system.solve()
    except BaseException:
        system.set_values({vary: original})
        raise


def run_reference(task: Task) -> dict[str, Any]:
    """Execute the task's reference steps; return {answer key: value in the answer unit}."""
    try:
        system = wp.System.from_dict(task.system)
    except wp.WorldpartsError as exc:
        raise ReferenceStepError(f"{task.id}: reference.system: {exc}") from exc
    out: dict[str, Any] = {}

    def unit_of(key: str) -> str | None:
        return task.answer(key).unit

    for i, step in enumerate(task.steps):
        op = step["op"]
        where = f"{task.id}: steps[{i}] ({op})"
        try:
            if op == "solve":
                result = system.solve()
                for key, path in step["read"].items():
                    out[key] = _read(system, result, path, unit_of(key))
            elif op == "set":
                system.set_values(step["values"])
            elif op == "solve_for":
                result = solve_for(
                    system,
                    step["target"],
                    step["value"],
                    step["vary"],
                    step["lower"],
                    step["upper"],
                )
                for key, path in (step.get("read") or {}).items():
                    out[key] = _read(system, result, path, unit_of(key))
            elif op == "simulate":
                sim = system.simulate(
                    step["duration"],
                    step.get("step", "1 s"),
                    step.get("events"),
                    restore=bool(step.get("restore", False)),
                )
                for key, path in (step.get("read_final") or {}).items():
                    out[key] = _read(system, sim.final, path, unit_of(key))
                for field_name, pick in (("read_min", min), ("read_max", max)):
                    for key, path in (step.get(field_name) or {}).items():
                        series = [v for v in sim.get(path, unit_of(key)) if v is not None]
                        if not series:
                            raise ReferenceStepError(f"'{path}' is undefined over the run")
                        out[key] = float(pick(series))
                for key, src in (step.get("read_total") or {}).items():
                    paths = [src] if isinstance(src, str) else list(src)
                    out[key] = sum(time_integral(sim, p, unit_of(key)) for p in paths)
                for key, spec in (step.get("read_first_warning") or {}).items():
                    code = spec["warning"]
                    hits = [w for w in sim.warnings if w.path == code]
                    if not hits:
                        raise ReferenceStepError(f"warning '{code}' is never raised")
                    t = min(float(w.time or 0.0) for w in hits)
                    out[key] = float(convert(t, "s", unit_of(key) or "s"))  # type: ignore[arg-type]
            elif op == "answer":
                out.update(step["values"])
            else:  # pragma: no cover - the schema rejects other ops
                raise ReferenceStepError(f"unknown op '{op}'")
        except ReferenceStepError as exc:
            raise ReferenceStepError(f"{where}: {exc}") from exc
        except wp.WorldpartsError as exc:
            raise ReferenceStepError(f"{where}: [{exc.code}] {exc}") from exc
    return {key: out[key] for key in task.keys}


def same_value(a: Any, b: Any, rel: float = 1e-6) -> bool:
    """Equality used for expected-vs-reference checks (numbers within ``rel``)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= rel * max(abs(a), abs(b)) or abs(a - b) <= 1e-12
    return a == b
