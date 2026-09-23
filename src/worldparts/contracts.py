"""Run manifest scenarios and contracts; catalogue self-test (design section 7).

Scenarios are small systems that exercise one component (named ``dut`` by convention) with
expected results. Contracts sweep one variable of a scenario and check a behavioural property
at every sweep point: ``monotonic``, ``bounds``, ``equal`` or ``warning_iff``.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from worldparts.catalog import Catalog, default_catalog
from worldparts.errors import WorldpartsError, format_choices
from worldparts.expressions import compile_expression, is_true
from worldparts.manifest import Manifest
from worldparts.results import SolveResult
from worldparts.system import System

__all__ = [
    "CheckOutcome",
    "ComponentReport",
    "check_catalog",
    "run_component",
    "run_contract",
    "run_scenario",
]


@dataclass
class CheckOutcome:
    """Result of one scenario or contract.

    Attributes:
        kind: ``"scenario"`` or ``"contract"``.
        id: Scenario or contract id.
        passed: Whether every expectation or check held.
        failures: Explanations of what failed.
        points: Number of evaluated points (sweep points for contracts).
        warnings: Every ``<instance>.<code>`` warning seen, with the component id that
            emitted it.
    """

    kind: str
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    points: int = 0
    warnings: set[tuple[str, str]] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form."""
        return {
            "kind": self.kind,
            "id": self.id,
            "passed": self.passed,
            "failures": list(self.failures),
            "points": self.points,
        }


@dataclass
class ComponentReport:
    """All scenario and contract outcomes of one component."""

    component: str
    outcomes: list[CheckOutcome]

    @property
    def passed(self) -> bool:
        """True when every scenario and contract passed."""
        return all(o.passed for o in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form."""
        return {
            "component": self.component,
            "passed": self.passed,
            "scenarios": [o.to_dict() for o in self.outcomes if o.kind == "scenario"],
            "contracts": [o.to_dict() for o in self.outcomes if o.kind == "contract"],
        }

    def summary(self) -> str:
        """Human-readable multi-line summary."""
        lines = [f"{'PASS' if self.passed else 'FAIL'} {self.component}"]
        for o in self.outcomes:
            lines.append(f"  {'ok  ' if o.passed else 'FAIL'} {o.kind} {o.id}")
            lines.extend(f"       {f}" for f in o.failures)
        return "\n".join(lines)


def _system_for(scenario: Mapping[str, Any], catalog: Catalog) -> System:
    doc = {"worldparts_system": "0.1", "name": str(scenario["id"]), **dict(scenario["system"])}
    return System.from_dict(doc, catalog)


def _run(system: System, scenario: Mapping[str, Any]) -> SolveResult:
    sim = scenario.get("simulate")
    if sim:
        return system.simulate(sim["duration"], sim.get("step", "1 s"), sim.get("events")).final
    return system.solve()


def _seen(system: System, result: SolveResult) -> set[tuple[str, str]]:
    return {(system.components.get(w.component, "?"), w.code) for w in result.warnings}


def _undeclared_warning(system: System, code: str) -> str | None:
    """Explain why ``<instance>.<code>`` cannot be emitted, or None when it is declared.

    An expectation or ``warning_iff`` check on a misspelled instance or code would otherwise
    pass vacuously (the warning is simply never present).
    """
    inst, _, name = str(code).partition(".")
    if not name:
        return f"warning '{code}' must be written '<instance>.<code>', e.g. 'dut.backflow'"
    if inst not in system.components:
        return f"warning '{code}' names no instance '{inst}'. " + format_choices(
            inst, system.components
        )
    try:
        declared = system.manifest(inst).warnings
    except WorldpartsError as exc:
        return f"warning '{code}': {exc}"
    if name not in declared:
        alias = system.manifest(inst).alias
        codes = ", ".join(sorted(declared)) or "none"
        return (
            f"warning code '{name}' is not declared by {inst} ({alias}), so it can never be "
            f"present; declared codes: {codes}. " + format_choices(name, declared)
        )
    return None


def _num(x: Any) -> str:
    return "None" if x is None else (f"{x:.6g}" if isinstance(x, (int, float)) else repr(x))


def run_scenario(
    manifest: Manifest, scenario: str | Mapping[str, Any], catalog: Catalog | None = None
) -> CheckOutcome:
    """Run one scenario and evaluate its expectations."""
    cat = catalog or default_catalog()
    scen = manifest.scenario(scenario) if isinstance(scenario, str) else scenario
    out = CheckOutcome("scenario", str(scen["id"]), True)
    try:
        system = _system_for(scen, cat)
        result = _run(system, scen)
    except WorldpartsError as exc:
        out.passed = False
        out.failures.append(f"{type(exc).__name__}: {exc}")
        return out
    out.points = 1
    out.warnings = _seen(system, result)
    for exp in scen["expect"]:
        msg = _undeclared_warning(system, exp["warning"]) if "warning" in exp else None
        msg = msg or _check_expectation(exp, result)
        if msg:
            out.passed = False
            out.failures.append(msg)
    return out


def _check_expectation(exp: Mapping[str, Any], r: SolveResult) -> str | None:
    if "warning" in exp:
        present = r.has_warning(exp["warning"])
        if present != bool(exp["present"]):
            seen = ", ".join(w.path for w in r.warnings) or "none"
            return (
                f"warning {exp['warning']} expected {'present' if exp['present'] else 'absent'}"
                f" but it is {'present' if present else 'absent'} (warnings: {seen})"
            )
        return None
    if "mode" in exp:
        mode = r.modes.get(exp["mode"], "<unknown instance>")
        if mode != exp["is"]:
            return f"mode of {exp['mode']} expected {exp['is']!r}, got {mode!r}"
        return None
    path = exp["variable"]
    if path not in r.values:
        return f"unknown variable {path}"
    v = r.values[path]
    unit = r.units.get(path, "")
    if "value" in exp:
        target = exp["value"]
        if target is None or v is None:
            if target is not v:
                return f"{path} expected {_num(target)}, got {_num(v)} {unit}"
            return None
        tol = max(exp.get("abs_tol", 0.0), exp.get("rel_tol", 0.0) * abs(target))
        if "abs_tol" not in exp and "rel_tol" not in exp:
            tol = 1e-9 * max(1.0, abs(target))
        if not abs(v - target) <= tol:
            return f"{path} = {_num(v)} {unit}, expected {_num(target)} +/- {_num(tol)}"
        return None
    if v is None:
        return (
            f"{path} is undefined (None), expected a value in [{exp.get('min')}, {exp.get('max')}]"
        )
    if "min" in exp and v < exp["min"]:
        return f"{path} = {_num(v)} {unit} is below the expected minimum {exp['min']}"
    if "max" in exp and v > exp["max"]:
        return f"{path} = {_num(v)} {unit} is above the expected maximum {exp['max']}"
    return None


def _sweep_values(sweep: Mapping[str, Any] | None) -> list[Any]:
    if not sweep:
        return [None]
    if "values" in sweep:
        return list(sweep["values"])
    return [float(x) for x in np.linspace(sweep["from"], sweep["to"], int(sweep["steps"]))]


def _expr(value: Any) -> Any:
    if isinstance(value, bool):
        return compile_expression("true" if value else "false")
    return compile_expression(str(value))


def run_contract(
    manifest: Manifest, contract: str | Mapping[str, Any], catalog: Catalog | None = None
) -> CheckOutcome:
    """Run one contract: sweep a variable of its scenario and check a property."""
    cat = catalog or default_catalog()
    if isinstance(contract, str):
        matches = [c for c in manifest.contracts if c["id"] == contract]
        if not matches:
            raise WorldpartsError(f"{manifest.id} has no contract '{contract}'.")
        contract = matches[0]
    out = CheckOutcome("contract", str(contract["id"]), True)
    check = contract["check"]
    sweep = contract.get("sweep")
    try:
        scen = manifest.scenario(contract["scenario"])
        points: list[tuple[Any, SolveResult]] = []
        system: System | None = None
        for value in _sweep_values(sweep):
            if system is None or scen.get("simulate"):
                system = _system_for(scen, cat)
            if sweep:
                system.set(sweep["variable"], value)
            result = _run(system, scen)
            out.warnings |= _seen(system, result)
            points.append((value, result))
    except WorldpartsError as exc:
        out.passed = False
        out.failures.append(f"{type(exc).__name__}: {exc}")
        return out
    out.points = len(points)
    if check["type"] == "warning_iff" and system is not None:
        msg = _undeclared_warning(system, check["code"])
        if msg:
            out.passed = False
            out.failures.append(msg)
            return out
    try:
        failures = _evaluate_check(check, points, sweep["variable"] if sweep else None)
    except WorldpartsError as exc:  # e.g. a misspelled name in an expression
        failures = [f"{type(exc).__name__}: {exc}"]
    if failures:
        out.passed = False
        out.failures.extend(failures[:10])
        if len(failures) > 10:
            out.failures.append(f"... and {len(failures) - 10} more")
    return out


def _evaluate_check(
    check: Mapping[str, Any], points: list[tuple[Any, SolveResult]], swept: str | None
) -> list[str]:
    kind = check["type"]
    failures: list[str] = []

    def at(value: Any) -> str:
        return f" at {swept} = {value}" if swept else ""

    if kind == "monotonic":
        path = check["variable"]
        tol = float(check.get("tolerance", 1e-9))
        strict = bool(check.get("strict", False))
        sign = 1.0 if check["direction"] == "increasing" else -1.0
        series = [(x, r.values.get(path)) for x, r in points]
        if any(path not in r.values for _, r in points):
            return [f"unknown variable {path}"]
        defined = [(x, y) for x, y in series if y is not None]
        if len(defined) < 2:
            return [f"{path}: fewer than two defined points; monotonicity cannot be checked"]
        for (x0, y0), (x1, y1) in itertools.pairwise(defined):
            diff = sign * (y1 - y0)
            scale = tol * max(abs(y0), abs(y1), 1e-300)
            ok = diff > scale if strict else diff >= -scale - 1e-15
            if not ok:
                failures.append(
                    f"{path} is not {'strictly ' if strict else ''}{check['direction']}: "
                    f"{_num(y0)} at {x0} then {_num(y1)} at {x1}"
                )
        return failures
    evaluated = 0
    for x, r in points:
        env = r.values
        if kind == "bounds":
            path = check["variable"]
            if path not in env:
                return [f"unknown variable {path}"]
            v = env[path]
            if v is None:
                continue
            evaluated += 1
            lo = _expr(check["min"]).evaluate(env) if "min" in check else None
            hi = _expr(check["max"]).evaluate(env) if "max" in check else None
            tol = 1e-12 * max(1.0, abs(v))
            if lo is not None and v < lo - tol:
                failures.append(f"{path} = {_num(v)} below minimum {_num(lo)}{at(x)}")
            if hi is not None and v > hi + tol:
                failures.append(f"{path} = {_num(v)} above maximum {_num(hi)}{at(x)}")
        elif kind == "equal":
            left = _expr(check["left"]).evaluate(env)
            right = _expr(check["right"]).evaluate(env)
            if left is None or right is None:
                continue
            evaluated += 1
            tol = max(
                float(check.get("abs_tol", 0.0)),
                float(check.get("rel_tol", 0.0)) * max(abs(left), abs(right)),
            )
            if not abs(left - right) <= tol or math.isnan(left - right):
                failures.append(
                    f"{check['left']} = {_num(left)} but {check['right']} = {_num(right)} "
                    f"(difference {_num(left - right)}, tolerance {_num(tol)}){at(x)}"
                )
        elif kind == "warning_iff":
            evaluated += 1
            present = r.has_warning(check["code"])
            cond = is_true(_expr(check["condition"]).evaluate(env))
            if present != cond:
                failures.append(
                    f"warning {check['code']} is {'present' if present else 'absent'} but "
                    f"condition '{check['condition']}' is {cond}{at(x)}"
                )
        else:  # pragma: no cover - schema prevents this
            return [f"unknown check type {kind}"]
    if evaluated == 0:
        failures.append("no sweep point could be evaluated (all values undefined)")
    return failures


def run_component(component: str | Manifest, catalog: Catalog | None = None) -> ComponentReport:
    """Run every scenario and contract of one component."""
    cat = catalog or default_catalog()
    m = component if isinstance(component, Manifest) else cat.get(component)
    outcomes = [run_scenario(m, s, cat) for s in m.scenarios]
    outcomes += [run_contract(m, c, cat) for c in m.contracts]
    return ComponentReport(m.id, outcomes)


def check_catalog(
    catalog: Catalog | None = None, component: str | None = None
) -> list[ComponentReport]:
    """Run scenarios and contracts of every component (or one) in the catalogue."""
    cat = catalog or default_catalog()
    manifests = [cat.get(component)] if component else list(cat)
    return [run_component(m, cat) for m in manifests]
