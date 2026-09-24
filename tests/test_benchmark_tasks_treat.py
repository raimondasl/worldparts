"""Reference solutions of the treatment and distribution tasks of the composition benchmark.

Each task file ``benchmarks/composition/tasks/treat-*.yaml`` carries a reference system and
the steps (solve, set, solve_for, simulate, answer) that produce its answers. This module
runs those steps with a small executor of its own, independent of the benchmark harness,
and checks that the frozen ``expected`` values still come out, so a change in the runtime
that moves a benchmark answer fails here first. The harness loader also has to accept every
file, and the answers that follow from closed-form hand calculations are checked against
those formulas (no worldparts), which shows the tasks are solvable without the library.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from scipy.optimize import brentq

import worldparts as wp
from worldparts.units import convert, parse_value

REPO = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO / "benchmarks" / "composition" / "tasks"
TASK_FILES = sorted(TASKS_DIR.glob("treat-*.yaml"))
#: Relative agreement required between the executed and the frozen reference values.
REL = 1e-6
LEVEL_RANGES = {1: (2, 4), 2: (5, 7), 3: (8, 10_000)}
CATEGORIES = {"operating_point", "sizing", "what_if", "transient", "diagnosis"}
DOMAINS = {"treatment", "distribution"}
FAULTS = {"clogged_filter", "worn_pump", "partly_closed_valve", "uv_lamp_degradation"}
#: Water properties stated in every prompt.
RHO, G, SG = 998.2, 9.80665, 0.9982


def load_task(path: Path) -> dict[str, Any]:
    """The task document of one YAML file."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read(result: wp.SolveResult, path: str, unit: str | None) -> float:
    value = result.get(path, unit=unit) if unit else result[path]
    assert value is not None, f"{path} is undefined"
    return float(value)


def solve_for(
    system: wp.System, target: str, value: Any, vary: str, lower: Any, upper: Any
) -> wp.SolveResult:
    """Goal seek with the semantics of the MCP tool ``solve_for`` (Brent's method).

    ``value`` is a number in the target's reported unit or a string with a unit; ``vary``
    stays at the root and the steady result there is returned.
    """
    inst, _, local = vary.partition(".")
    spec = system.manifest(inst).variable(local)
    lo = float(spec.parse(lower, "lower"))
    hi = float(spec.parse(upper, "upper"))
    goal: list[float] = []

    def miss(x: float) -> float:
        system.set_values({vary: x})
        result = system.solve()
        if not goal:
            unit = result.units[target]
            goal.append(
                parse_value(value, unit, "value", result.references.get(target))
                if isinstance(value, str)
                else float(value)
            )
        got = result[target]
        assert got is not None, f"{target} is undefined at {vary} = {x}"
        return float(got) - goal[0]

    f_lo, f_hi = miss(lo), miss(hi)
    assert f_lo * f_hi <= 0.0, f"{target} does not cross {value} between {lo} and {hi}"
    root = brentq(miss, lo, hi, xtol=1e-12 * max(abs(lo), abs(hi), 1e-12), rtol=1e-12)
    system.set_values({vary: float(root)})
    return system.solve()


def run_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Execute the reference steps; return {answer key: value in the answer's unit}."""
    units = {a["key"]: a.get("unit") for a in task["answers"]}
    system = wp.System.from_dict(task["reference"]["system"])
    out: dict[str, Any] = {}
    for step in task["reference"]["steps"]:
        op = step["op"]
        if op == "solve":
            result = system.solve()
            for key, path in step["read"].items():
                out[key] = _read(result, path, units[key])
        elif op == "set":
            system.set_values(step["values"])
        elif op == "solve_for":
            result = solve_for(
                system, step["target"], step["value"], step["vary"], step["lower"], step["upper"]
            )
            for key, path in (step.get("read") or {}).items():
                out[key] = _read(result, path, units[key])
        elif op == "simulate":
            sim = system.simulate(
                step["duration"], step.get("step", "1 s"), step.get("events") or []
            )
            for key, path in (step.get("read_final") or {}).items():
                out[key] = _read(sim.final, path, units[key])
            for key, spec in (step.get("read_first_warning") or {}).items():
                times = [float(w.time or 0.0) for w in sim.warnings if w.path == spec["warning"]]
                assert times, f"warning {spec['warning']} is never raised"
                out[key] = float(convert(min(times), "s", spec.get("unit", "s")))
        elif op == "answer":
            out.update(step["values"])
        else:  # pragma: no cover - guarded by the structure test
            raise AssertionError(f"unknown op {op}")
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=REL, abs_tol=1e-12)
    return a == b


@pytest.fixture(scope="module")
def tasks() -> dict[str, dict[str, Any]]:
    return {p.stem: load_task(p) for p in TASK_FILES}


def test_twelve_tasks_spread_over_levels_and_categories(tasks):
    assert len(tasks) == 12
    levels = [t["level"] for t in tasks.values()]
    assert (levels.count(1), levels.count(2), levels.count(3)) == (4, 5, 3)
    assert {t["category"] for t in tasks.values()} == CATEGORIES
    assert {t["domain"] for t in tasks.values()} == DOMAINS


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_task_structure(path):
    task = load_task(path)
    assert task["id"] == path.stem and task["id"].startswith("treat-")
    assert task["category"] in CATEGORIES
    assert task["domain"] in DOMAINS
    lo, hi = LEVEL_RANGES[task["level"]]
    assert lo <= len(task["reference"]["system"]["components"]) <= hi
    words = len(task["prompt"].split())
    assert 80 <= words <= 250, f"prompt has {words} words"
    keys = [a["key"] for a in task["answers"]]
    assert set(task["reference"]["expected"]) == set(keys)
    produced: list[str] = []
    for step in task["reference"]["steps"]:
        for field in ("read", "read_final", "read_first_warning"):
            produced += list((step.get(field) or {}).keys())
        if step["op"] == "answer":
            produced += list(step["values"])
    assert sorted(produced) == sorted(keys), "every answer comes from exactly one op"
    for a in task["answers"]:
        if a.get("kind") == "choice":
            assert set(a["choices"]) == FAULTS
            assert task["reference"]["expected"][a["key"]] in FAULTS
        elif a.get("kind", "number") == "number":
            assert a["unit"] and (a.get("rel_tol") or a.get("abs_tol"))


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_system_has_no_errors(path):
    system = wp.System.from_dict(load_task(path)["reference"]["system"])
    errors = [i for i in system.check() if i.severity == "error"]
    assert errors == []


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_steps_reproduce_expected(path):
    task = load_task(path)
    got = run_task(task)
    expected = task["reference"]["expected"]
    for key, value in expected.items():
        assert _same(got[key], value), f"{key}: executed {got[key]!r}, frozen {value!r}"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_harness_loader_accepts_task(path):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    tasks_mod = pytest.importorskip("benchmarks.composition.harness.tasks")
    task = tasks_mod.load_task(path)
    assert task.expected is not None


# -- hand checks without worldparts ------------------------------------------------------------


def _within(value: float, expected: float, answer: Mapping[str, Any]) -> bool:
    band = max(answer.get("rel_tol", 0.0) * abs(expected), answer.get("abs_tol", 0.0))
    return abs(value - expected) <= band


def _check(task: Mapping[str, Any], key: str, value: float) -> None:
    answer = next(a for a in task["answers"] if a["key"] == key)
    expected = task["reference"]["expected"][key]
    assert _within(value, expected, answer), f"{key}: hand {value:.6g}, expected {expected:.6g}"


def test_uv_dose_by_hand(tasks):
    task = tasks["treat-uv-dose-01"]
    q = 30 * math.sqrt(1.5 / (SG + 0.20))  # valve Kv 30 and reactor 0.2 bar at 30 m3/h
    _check(task, "flow", q)
    _check(task, "dose", 16 * 0.022 / (q / 3600))
    assert task["reference"]["expected"]["meets_dose"] is (16 * 0.022 / (q / 3600) >= 40)


def test_uv_throttle_by_hand(tasks):
    task = tasks["treat-uv-throttle-01"]
    q_open = math.sqrt(2.2 / (SG / 50**2 + 0.25 / 40**2))
    _check(task, "dose_full_open", 22 * 0.020 / (q_open / 3600))
    q = 22 * 0.020 / 40 * 3600
    _check(task, "flow", q)
    kv_eff = q / math.sqrt((2.2 - 0.25 * (q / 40) ** 2) / SG)
    r = 50.0
    y = 1 + math.log(kv_eff / 50 * (1 - 1 / r) + 1 / r) / math.log(r)
    _check(task, "opening", y)


def test_check_valve_holds_static_head(tasks):
    task = tasks["treat-check-backflow-01"]
    _check(task, "check_outlet_pressure", RHO * G * (22 + 3.5) / 1e5)
    _check(task, "flow_after_drop", 0.0)


def test_uv_lamp_required_flow_by_hand(tasks):
    task = tasks["treat-uv-lamp-01"]
    _check(task, "flow", 0.65 * 20 * 0.045 / 40 * 3600)


def test_parallel_uv_split_by_hand(tasks):
    task = tasks["treat-parallel-uv-01"]
    k_a, k_b = 30 / math.sqrt(0.15), 30 / math.sqrt(0.25)  # Q = k sqrt(dp) through each
    q_a = 20 * 0.015 / 40 * 3600  # UV A limits: 40 mJ/cm2 exactly
    q = q_a * (k_a + k_b) / k_a
    _check(task, "plant_flow", q)
    _check(task, "dose_a", 40.0)
    _check(task, "dose_b", 16 * 0.018 / ((q - q_a) / 3600))
