"""Reference situations of the judgement tasks of the composition benchmark.

A judgement task (``benchmarks/composition/tasks/judge-*.yaml``) gives datasheet-level data
and asks an open review question; its answers, ``acceptable`` and ``primary_problem``, are
stated by an ``answer`` op. This module checks that the reference system really is in the
stated situation: solving it (or, for a tank that runs dry within the duty period,
simulating the task's ``simulate`` step) raises the worldparts warning that corresponds to
the primary problem and no warning-level problem that corresponds to any other choice. A
sound design (``none``) raises no warning-level result at all and not the
continuous-duty info ``outside_preferred_region`` either.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import worldparts as wp

TASKS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "composition" / "tasks"
TASK_FILES = sorted(TASKS_DIR.glob("judge-*.yaml"))

#: (component type, warning code) -> the judgement choice it signals. The pump's
#: ``outside_preferred_region`` is an info (a continuous-duty flag), all others are warnings.
PROBLEM_OF_WARNING: dict[tuple[str, str], str] = {
    ("centrifugal_pump", "cavitation"): "cavitation",
    ("pipe", "below_vapour_pressure"): "cavitation",
    ("centrifugal_pump", "motor_overload"): "motor_overload",
    ("centrifugal_pump", "outside_preferred_region"): "pump_far_from_best_efficiency",
    ("centrifugal_pump", "beyond_curve"): "pump_beyond_end_of_curve",
    ("centrifugal_pump", "low_flow"): "pump_below_minimum_flow",
    ("uv_reactor", "underdose"): "insufficient_uv_dose",
    ("uv_reactor", "lamp_off"): "insufficient_uv_dose",
    ("media_filter", "change_required"): "filter_needs_cleaning",
    ("pipe", "high_velocity"): "excessive_pipe_velocity",
    ("tank", "tank_empty"): "tank_runs_dry",
    ("tank", "drawing_air"): "tank_runs_dry",
    ("tank", "tank_overflow"): "tank_overflows",
}
#: Choices without a worldparts warning: a required delivery pressure is a number the
#: reviewer compares by hand (no judgement task states one).
UNSIGNALLED = {"none", "insufficient_delivery_pressure"}
#: Problems whose situation is a transient over the stated duty period (a simulate step).
SIMULATED = {"tank_runs_dry", "tank_overflows"}


def _harness() -> tuple[Any, Any]:
    repo = str(TASKS_DIR.parents[2])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    return (
        importlib.import_module("benchmarks.composition.harness.tasks"),
        importlib.import_module("benchmarks.composition.harness.reference"),
    )


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def primary(task: dict[str, Any]) -> str:
    (step,) = [s for s in task["reference"]["steps"] if s["op"] == "answer"]
    return str(step["values"]["primary_problem"])


def component_types(task: dict[str, Any]) -> dict[str, str]:
    return {c["name"]: c["type"] for c in task["reference"]["system"]["components"]}


def problems_of(warnings: list[Any], types: dict[str, str]) -> set[str]:
    """The judgement choices signalled by ``warnings``."""
    out = set()
    for w in warnings:
        problem = PROBLEM_OF_WARNING.get((types[w.component], w.code))
        if problem is not None:
            out.add(problem)
    return out


def unexplained(warnings: list[Any], types: dict[str, str], allowed: set[str]) -> list[str]:
    """Warning-level results that do not signal one of ``allowed``."""
    return [
        f"{w.component}.{w.code}"
        for w in warnings
        if w.severity == "warning"
        and PROBLEM_OF_WARNING.get((types[w.component], w.code)) not in allowed
    ]


def test_task_set() -> None:
    """Eight tasks: two sound designs and six single problems, levels 1-3, mixed domains."""
    tasks_mod, _ = _harness()
    assert [p.stem for p in TASK_FILES] == [f"judge-0{i}" for i in range(1, 9)]
    tasks = [load(p) for p in TASK_FILES]
    problems = [primary(t) for t in tasks]
    assert problems.count("none") == 2
    faults = [p for p in problems if p != "none"]
    assert len(faults) == len(set(faults)) == 6
    required = {
        "cavitation",
        "motor_overload",
        "pump_far_from_best_efficiency",
        "insufficient_uv_dose",
        "tank_runs_dry",
    }
    assert required <= set(faults)
    assert set(faults) - required <= {"excessive_pipe_velocity", "pump_beyond_end_of_curve"}
    assert {t["level"] for t in tasks} == {1, 2, 3}
    assert {"pumping", "storage", "treatment"} <= {t["domain"] for t in tasks}
    assert all(t["category"] == "judgement" for t in tasks)
    # Every choice but none and a delivery-pressure shortfall has a worldparts signal.
    assert set(tasks_mod.JUDGEMENT_CHOICES) - UNSIGNALLED == set(PROBLEM_OF_WARNING.values())


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_task_is_a_valid_open_question(path: Path) -> None:
    """The loader accepts it, and the prompt is an open review question of 100-250 words."""
    tasks_mod, reference = _harness()
    task = load(path)
    assert tasks_mod.check_task(task, path) == []
    words = len(task["prompt"].split())
    assert 100 <= words <= 250, words
    assert " ".join(task["prompt"].split()).endswith(
        "Review this design. Is it acceptable for continuous duty as specified, and what is "
        "its primary problem, if any?"
    )
    produced = reference.run_reference(tasks_mod.load_task(path))
    assert produced == task["reference"]["expected"]
    assert produced["acceptable"] is (produced["primary_problem"] == "none")
    assert "hand check" in task["notes"].lower()


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_system_shows_exactly_the_primary_problem(path: Path) -> None:
    """Solving (or simulating) the reference raises the primary problem's warning only."""
    task = load(path)
    want = primary(task)
    types = component_types(task)
    system = wp.System.from_dict(task["reference"]["system"])
    assert not [i for i in system.check() if i.severity == "error"]
    result = system.solve()
    assert result.converged
    steady = problems_of(result.warnings, types)

    if want in SIMULATED:
        # The duty-period transient: the steady start point is sound, the run shows the
        # tank problem and nothing else.
        assert steady == set(), steady
        assert unexplained(result.warnings, types, set()) == []
        (sim_step,) = [s for s in task["reference"]["steps"] if s["op"] == "simulate"]
        sim = system.simulate(sim_step["duration"], sim_step.get("step", "1 s"), [])
        found = problems_of(sim.warnings, types)
        assert found == {want}, found
        assert unexplained(sim.warnings, types, {want}) == []
        return

    assert not [s for s in task["reference"]["steps"] if s["op"] == "simulate"]
    if want == "none":
        assert steady == set(), steady
        assert [f"{w.component}.{w.code}" for w in result.warnings if w.severity == "warning"] == []
    else:
        assert steady == {want}, steady
        assert unexplained(result.warnings, types, {want}) == []


def test_judge_06_runs_dry_well_inside_the_shift() -> None:
    """The tank is drawn empty after about 3.5 h of the 8-hour shift (hand check 3.6 h)."""
    task = load(TASKS_DIR / "judge-06.yaml")
    system = wp.System.from_dict(task["reference"]["system"])
    sim = system.simulate("8 h", "20 s", [])
    first = min(w.time for w in sim.warnings if w.code in ("tank_empty", "drawing_air"))
    assert 2.5 * 3600 < first < 4.5 * 3600
    assert sim.get("break_tank.level")[-1] < 0.05  # m, of 2.6 m at the start


def test_judge_08_stays_sound_until_backwash() -> None:
    """With the filter loaded to its 1.0 bar backwash drop, nothing is raised either."""
    task = load(TASKS_DIR / "judge-08.yaml")
    system = wp.System.from_dict(task["reference"]["system"])
    _, reference = _harness()
    result = reference.solve_for(system, "filter.pressure_drop", 1.0, "filter.clogging", 0.59, 0.95)
    assert result["filter.pressure_drop"] == pytest.approx(1.0, rel=1e-6)
    # At exactly the backwash drop the filter may or may not flag it; nothing else is raised.
    assert {w.code for w in result.warnings} <= {"change_required"}
    flow_ratio = result["pump.volume_flow"] / result["pump.bep_flow"]
    assert 0.8 < flow_ratio < 0.9
    assert result["uv.dose"] > 50
