"""Reference solutions of the scale-net tasks (level 4) of the composition benchmark.

Each task file ``benchmarks/composition/tasks/scale-net-*.yaml`` is a network of 20 to 80
catalogue components (a five-zone irrigation network, a looped grid fed by two reservoirs,
a tower on a level switch over a 24 h schedule, a three-pump booster staged by header
pressure over a 24 h schedule). This module runs the reference steps with a small executor
of its own, independent of the benchmark harness, and checks that the frozen ``expected``
values still come out, that each task is level 4 with the intended component counts, and
that the answers are robust: the choice answer follows from the solution, the answers of
the controlled simulations lie clearly away from the switching thresholds, and the switching
sequence is the one described in the task notes (where the independent EPANET checks are
recorded).
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

import worldparts as wp

REPO = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO / "benchmarks" / "composition" / "tasks"
TASK_FILES = sorted(TASKS_DIR.glob("scale-net-*.yaml"))
#: Relative agreement required between the executed and the frozen reference values.
REL = 1e-6
#: Components of each reference system (controls are not components).
COMPONENTS = {
    "scale-net-irrigation-01": 57,
    "scale-net-loop-01": 40,
    "scale-net-tower-01": 26,
    "scale-net-booster-01": 30,
}
#: The only component types a scale-net task may use (the v0.1 hydraulic catalogue and leak).
CATALOGUE = {
    "supply",
    "drain",
    "pipe",
    "valve",
    "check_valve",
    "centrifugal_pump",
    "tank",
    "media_filter",
    "uv_reactor",
    "leak",
}
CATEGORIES = {"operating_point", "transient"}
DOMAINS = {"distribution", "storage", "pumping"}


def load_task(path: Path) -> dict[str, Any]:
    """The task document of one YAML file."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _types(task: Mapping[str, Any]) -> list[str]:
    return [c["type"] for c in task["reference"]["system"]["components"]]


def run_task(task: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    """Execute the reference steps; return the answers (in their units) and the last result."""
    units = {a["key"]: a.get("unit") for a in task["answers"]}
    system = wp.System.from_dict(task["reference"]["system"])
    out: dict[str, Any] = {}
    last: Any = None
    for step in task["reference"]["steps"]:
        op = step["op"]
        if op == "solve":
            last = system.solve()
            for key, path in step["read"].items():
                out[key] = float(last.get(path, unit=units[key]))
        elif op == "simulate":
            last = system.simulate(step["duration"], step.get("step", "1 s"), step.get("events"))
            for key, path in (step.get("read_final") or {}).items():
                out[key] = float(last.final.get(path, unit=units[key]))
            for field, pick in (("read_min", min), ("read_max", max)):
                for key, path in (step.get(field) or {}).items():
                    out[key] = float(pick(v for v in last.get(path, units[key]) if v is not None))
        elif op == "answer":
            out.update(step["values"])
        else:  # pragma: no cover - the structure test allows only these ops
            raise AssertionError(f"unexpected op {op}")
    return out, last


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=REL, abs_tol=1e-12)
    return a == b


@pytest.fixture(scope="module")
def tasks() -> dict[str, dict[str, Any]]:
    return {p.stem: load_task(p) for p in TASK_FILES}


@pytest.fixture(scope="module")
def runs(tasks) -> dict[str, tuple[dict[str, Any], Any]]:
    """Each task's reference run (the simulations take a few seconds, so run them once)."""
    return {tid: run_task(task) for tid, task in tasks.items()}


def test_four_scale_net_tasks(tasks):
    assert sorted(tasks) == sorted(COMPONENTS)


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_task_structure(path):
    task = load_task(path)
    assert task["id"] == path.stem and task["id"].startswith("scale-net-")
    assert task["level"] == 4
    assert task["category"] in CATEGORIES and task["domain"] in DOMAINS
    types = _types(task)
    assert len(types) == COMPONENTS[task["id"]]
    assert 20 <= len(types) <= 80
    assert set(types) <= CATALOGUE
    assert all(s["op"] in {"solve", "simulate", "answer"} for s in task["reference"]["steps"])
    keys = [a["key"] for a in task["answers"]]
    assert set(task["reference"]["expected"]) == set(keys)
    for a in task["answers"]:
        if a.get("kind", "number") == "number":
            assert a["unit"] and (a.get("rel_tol") or a.get("abs_tol"))
            assert a.get("rel_tol", 0.0) <= 0.05


def test_component_counts_match_the_task_briefs(tasks):
    irrigation = _types(tasks["scale-net-irrigation-01"])
    assert 40 <= len(irrigation) <= 60
    assert irrigation.count("centrifugal_pump") == 1 and irrigation.count("valve") == 5
    assert irrigation.count("leak") == 22  # 4 to 5 sprinklers in each of the five zones
    loop = _types(tasks["scale-net-loop-01"])
    assert loop.count("supply") == 2 and 25 <= loop.count("pipe") <= 35
    assert 10 <= loop.count("leak") <= 15
    tower = _types(tasks["scale-net-tower-01"])
    assert 25 <= len(tower) <= 40 and tower.count("tank") == 2
    assert len(tasks["scale-net-tower-01"]["reference"]["system"]["controls"]) == 1
    booster = _types(tasks["scale-net-booster-01"])
    assert booster.count("centrifugal_pump") == 3 and booster.count("check_valve") == 3
    assert len(tasks["scale-net-booster-01"]["reference"]["system"]["controls"]) == 2


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_system_has_no_errors(path):
    system = wp.System.from_dict(load_task(path)["reference"]["system"])
    errors = [i for i in system.check() if i.severity == "error"]
    assert errors == []


@pytest.mark.parametrize("tid", sorted(COMPONENTS))
def test_reference_steps_reproduce_expected(tasks, runs, tid):
    got, _ = runs[tid]
    for key, value in tasks[tid]["reference"]["expected"].items():
        assert _same(got[key], value), f"{key}: executed {got[key]!r}, frozen {value!r}"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_harness_loader_accepts_task(path):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    tasks_mod = pytest.importorskip("benchmarks.composition.harness.tasks")
    task = tasks_mod.load_task(path)
    assert task.expected is not None and task.level == 4
    assert task.component_count == COMPONENTS[task.id]
    assert tasks_mod.prompt_word_count(task.prompt) <= tasks_mod.SCALE_PROMPT_WORDS
    assert tasks_mod.markdown_tables(task.prompt), "a scale-net prompt gives its data in tables"


# -- robustness of the answers --------------------------------------------------------------


def test_irrigation_lowest_sprinkler_is_clear_cut(tasks, runs):
    task = tasks["scale-net-irrigation-01"]
    _, result = runs["scale-net-irrigation-01"]
    sprinklers = [
        c["name"] for c in task["reference"]["system"]["components"] if c["type"] == "leak"
    ]
    pressures = {n: result[f"{n}.pressure"] for n in sprinklers}
    lowest = min(pressures, key=pressures.get)
    assert lowest == "spr_e4"
    zone = task["reference"]["expected"]["lowest_pressure_zone"]
    assert zone == f"zone_{lowest[4]}"
    others = min(p for n, p in pressures.items() if n[4] != lowest[4])
    assert others > 1.3 * pressures[lowest]  # 2.56 against 1.79 bar
    # every sprinkler runs well above zero, and the pump delivers exactly what they discharge
    assert min(pressures.values()) > 1.0
    total = sum(result.get(f"{n}.volume_flow", unit="m3/h") for n in sprinklers)
    assert total == pytest.approx(task["reference"]["expected"]["pump_flow"], rel=1e-6)


def test_loop_both_reservoirs_supply_and_balance(tasks, runs):
    task = tasks["scale-net-loop-01"]
    expected = task["reference"]["expected"]
    _, result = runs["scale-net-loop-01"]
    outlets = [c["name"] for c in task["reference"]["system"]["components"] if c["type"] == "leak"]
    total = sum(result.get(f"{n}.volume_flow", unit="m3/h") for n in outlets)
    assert expected["west_supply_flow"] + expected["east_supply_flow"] == pytest.approx(total)
    assert expected["east_supply_flow"] > 0.25 * total  # a substantial share, not a trickle
    assert min(result[f"{n}.pressure"] for n in outlets) == pytest.approx(expected["pressure_j14"])


def _switches(sim: Any, name: str) -> list[tuple[int, float]]:
    """(sample index, new output) of every change of a control's output."""
    out = sim[f"control.{name}.output"]
    return [(i, out[i]) for i in range(1, len(out)) if out[i] != out[i - 1]]


def _hours(sim: Any, switches: list[tuple[int, float]]) -> list[float]:
    return [sim.time[i] / 3600 for i, _ in switches]


def test_tower_switching_sequence_and_margins(tasks, runs):
    task = tasks["scale-net-tower-01"]
    expected = task["reference"]["expected"]
    system = task["reference"]["system"]
    ctl = system["controls"][0]
    tower = next(c for c in system["components"] if c["name"] == "tower")
    _, sim = runs["scale-net-tower-01"]
    switches = _switches(sim, ctl["name"])
    # on at about 2.2 h, off at about 4.4 h, on at about 6.6 h and running to the end
    assert [s for _, s in switches] == [1.0, 0.0, 1.0]
    times = _hours(sim, switches)
    assert times == pytest.approx([2.167, 4.433, 6.6], abs=0.02)
    schedule = [float(str(e["at"]).split()[0]) for e in task["reference"]["steps"][0]["events"]]
    assert min(abs(t - h) for t in times for h in schedule) > 0.3  # no switch at a demand step
    # the lowest level is set by the morning peak, far below the switch-on level, and the
    # evening peak's low is clearly higher, so the minimum is not a threshold value
    assert expected["min_tower_level"] < ctl["on_below"] - 1.0
    level = sim["tower.level"]
    evening = min(v for t, v in zip(sim.time, level, strict=True) if t >= 17 * 3600)
    assert evening > expected["min_tower_level"] + 0.25
    assert max(level) < tower["parameters"]["height"]  # never overflows
    # the pump runs without a break for the last 17 h, so the 24 h levels do not hinge on
    # the phase of a switching cycle
    assert times[-1] < 7.0


def test_booster_staging_sequence_and_margins(tasks, runs):
    task = tasks["scale-net-booster-01"]
    expected = task["reference"]["expected"]
    controls = task["reference"]["system"]["controls"]
    stage2, stage3 = controls
    _, sim = runs["scale-net-booster-01"]
    s2, s3 = _switches(sim, stage2["name"]), _switches(sim, stage3["name"])
    assert [s for _, s in s2] == [1.0] and [s for _, s in s3] == [1.0, 0.0]
    assert _hours(sim, s2) + _hours(sim, s3) == pytest.approx([5.633, 6.583, 23.217], abs=0.02)
    # one pump switches at a time, and the state after each switch lies inside every dead
    # band with a margin, so a one-pass controller and a cascading one (EPANET) agree
    header = sim["nrv1.port_b.p"]
    margin = 0.15
    for i, _ in s2 + s3:
        for ctl in controls:
            if sim[f"control.{ctl['name']}.output"][i] > 0.5:
                assert header[i] < ctl["off_above"] - margin
            else:
                assert header[i] > ctl["on_below"] + margin
    # the extremes are plateaus away from every threshold: the night's lowest demand with
    # one pump and the peak with all three
    assert expected["max_header_pressure"] > stage2["off_above"] + margin
    assert expected["min_header_pressure"] < stage3["on_below"] - margin
    assert expected["min_pressure_n9"] > 0.5
