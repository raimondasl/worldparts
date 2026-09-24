"""Reference solutions of the pumping and storage tasks of the composition benchmark.

Each task file ``benchmarks/composition/tasks/pump-*.yaml`` carries a reference system and
the steps (solve, set, solve_for, simulate, answer) that produce its answers. This module
runs those steps with a small executor of its own, independent of the benchmark harness,
and checks that the frozen ``expected`` values still come out, so a change in the runtime
that moves a benchmark answer fails here first. It also cross-checks the tasks with the
harness loader and reference executor, which must accept them and agree.
"""

from __future__ import annotations

import importlib
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

TASKS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "composition" / "tasks"
TASK_FILES = sorted(TASKS_DIR.glob("pump-*.yaml"))
#: Relative agreement required between the executed and the frozen reference values.
REL = 1e-6
LEVEL_RANGES = {1: (2, 4), 2: (5, 7), 3: (8, 19), 4: (20, 10_000)}
CATEGORIES = {"operating_point", "sizing", "what_if", "transient", "diagnosis"}
DOMAINS = {"pumping", "storage"}
FORBIDDEN_IN_PROMPT = ("worldparts", "centrifugal_pump", "solve_for", "port_kv", "npsh_margin")


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
    """Build the reference system and execute its steps; returns the answers."""
    units = {a["key"]: a.get("unit") for a in task["answers"]}
    system = wp.System.from_dict(task["reference"]["system"])
    out: dict[str, Any] = {}

    def put(key: str, value: Any) -> None:
        assert key in units, f"step produces '{key}', which is not an answer"
        assert key not in out, f"answer '{key}' is produced twice"
        out[key] = value

    for step in task["reference"]["steps"]:
        op = step["op"]
        if op == "solve":
            result = system.solve()
            for key, path in step.get("read", {}).items():
                put(key, _read(result, path, units[key]))
        elif op == "set":
            system.set_values(step["values"])
        elif op == "solve_for":
            result = solve_for(
                system, step["target"], step["value"], step["vary"], step["lower"], step["upper"]
            )
            for key, path in step.get("read", {}).items():
                put(key, _read(result, path, units[key]))
        elif op == "simulate":
            sim = system.simulate(
                step["duration"],
                step["step"],
                step.get("events") or [],
                restore=bool(step.get("restore", False)),
            )
            for key, path in step.get("read_final", {}).items():
                put(key, _read(sim.final, path, units[key]))
            for reads, pick in (("read_min", min), ("read_max", max)):
                for key, path in step.get(reads, {}).items():
                    series = sim.get(path, unit=units[key])
                    put(key, float(pick(v for v in series if v is not None)))
            first = {w.path: w.time for w in sim.warnings}
            for key, spec in step.get("read_first_warning", {}).items():
                assert spec["warning"] in first, f"{spec['warning']} never occurs"
                put(key, convert(first[spec["warning"]], "s", spec.get("unit", units[key])))
        elif op == "answer":
            for key, value in step["values"].items():
                put(key, value)
        else:  # pragma: no cover - a malformed task
            raise AssertionError(f"unknown op '{op}'")
    assert set(out) == set(units), f"answers not produced: {set(units) - set(out)}"
    return out


def count_components(task: Mapping[str, Any]) -> int:
    """Number of components in the reference system."""
    return len(task["reference"]["system"]["components"])


def test_task_files_exist() -> None:
    """The pumping and storage share of the benchmark: 12 tasks, 4 + 5 + 3 by level."""
    assert len(TASK_FILES) == 12
    levels = [load_task(p)["level"] for p in TASK_FILES]
    assert (levels.count(1), levels.count(2), levels.count(3)) == (4, 5, 3)
    categories = {load_task(p)["category"] for p in TASK_FILES}
    assert categories == CATEGORIES


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_task_is_well_formed(path: Path) -> None:
    """Id, level, category, domain, prompt length and answer declarations."""
    task = load_task(path)
    assert task["id"] == path.stem and task["id"].startswith("pump-")
    lo, hi = LEVEL_RANGES[task["level"]]
    assert lo <= count_components(task) <= hi
    assert task["category"] in CATEGORIES
    assert task["domain"] in DOMAINS
    words = len(task["prompt"].split())
    assert 80 <= words <= 250, f"prompt has {words} words"
    lowered = task["prompt"].lower()
    for word in FORBIDDEN_IN_PROMPT:
        assert word not in lowered
    keys = [a["key"] for a in task["answers"]]
    assert len(keys) == len(set(keys))
    assert set(task["reference"]["expected"]) == set(keys)
    for answer in task["answers"]:
        kind = answer.get("kind", "number")
        if kind == "number":
            assert answer.get("unit"), f"{answer['key']} needs a unit"
            assert "rel_tol" in answer or "abs_tol" in answer
        elif kind == "choice":
            assert task["reference"]["expected"][answer["key"]] in answer["choices"]


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_system_is_clean(path: Path) -> None:
    """The reference system has no error-level check issues."""
    system = wp.System.from_dict(load_task(path)["reference"]["system"])
    errors = [i for i in system.check() if i.severity == "error"]
    assert not errors


def _harness() -> Any:
    """The benchmark harness modules (reference executor and task loader)."""
    repo = str(TASKS_DIR.parents[2])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    reference = importlib.import_module("benchmarks.composition.harness.reference")
    tasks = importlib.import_module("benchmarks.composition.harness.tasks")
    return reference, tasks


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_harness_accepts_task_and_agrees(path: Path) -> None:
    """The harness loader finds no problem and its executor gives the same answers."""
    reference, tasks = _harness()
    assert tasks.check_task(load_task(path), path) == []
    theirs = reference.run_reference(tasks.load_task(path))
    ours = run_task(load_task(path))
    assert set(theirs) == set(ours)
    for key, value in ours.items():
        assert reference.same_value(theirs[key], value), key


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_reproduces_expected(path: Path) -> None:
    """Running the reference steps gives the frozen expected values."""
    task = load_task(path)
    got = run_task(task)
    expected = task["reference"]["expected"]
    for key, value in expected.items():
        if isinstance(value, (bool, str)):
            assert got[key] == value, key
        else:
            assert math.isclose(got[key], value, rel_tol=REL, abs_tol=1e-9), (
                f"{key}: got {got[key]!r}, expected {value!r}"
            )
