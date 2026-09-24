"""Reference solutions of the scale-plant tasks (level 4) of the composition benchmark.

Each task file ``benchmarks/composition/tasks/scale-plant-*.yaml`` describes a treatment
and pumping plant of 22 to 48 components, three of them over 24 h with controls. This
module runs the reference steps with a small executor of its own, independent of the
benchmark harness, and checks that the frozen ``expected`` values still come out, that
each task is level 4 with the stated component and control counts, and that the reference
systems are free of error-level check issues and simulate in reasonable time. It also
checks the facts the prompts and notes rely on: the steady plant solved without worldparts
(own Colebrook solver), the filter that crosses its backwash drop first, lag units that
never start, clearwell extremes away from switch thresholds, and the tanks' volume
balances behind the pumped-volume totals.
"""

from __future__ import annotations

import math
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from scipy.optimize import brentq, fsolve

import worldparts as wp
from worldparts.units import convert

REPO = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO / "benchmarks" / "composition" / "tasks"
TASK_FILES = sorted(TASKS_DIR.glob("scale-plant-*.yaml"))
#: Relative agreement required between the executed and the frozen reference values.
REL = 1e-6
#: (components, controls) of each reference system.
SIZES = {
    "scale-plant-steady-01": (22, 0),
    "scale-plant-clog-01": (27, 1),
    "scale-plant-tower-01": (22, 2),
    "scale-plant-transfer-01": (48, 7),
}
CATEGORIES = {"operating_point", "transient"}
DOMAINS = {"treatment", "storage", "distribution"}
#: Water properties stated in every prompt.
RHO, MU, G, SG = 998.2, 1.002e-3, 9.80665, 0.9982
BAR_M = 1e5 / (RHO * G)  # metres of water per bar
#: Generous wall-clock bounds (s) for one reference: a regression guard, not a benchmark.
MAX_SOLVE_S, MAX_SIMULATE_S = 5.0, 120.0


def load_task(path: Path) -> dict[str, Any]:
    """The task document of one YAML file."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _series_total(sim: wp.SimulationResult, path: str, unit: str) -> float:
    """Left sum of ``path`` over the run (each sample holds over the step it drives)."""
    values = sim.get(path)
    t = sim.time
    total = sum(float(values[k]) * (t[k + 1] - t[k]) for k in range(len(t) - 1))  # type: ignore[arg-type]
    return float(convert(total, f"{sim.units[path]}*s", unit))  # type: ignore[arg-type]


def run_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Execute the reference steps; return the answers, the system, the simulation and times."""
    units = {a["key"]: a.get("unit") for a in task["answers"]}
    t0 = time.perf_counter()
    system = wp.System.from_dict(task["reference"]["system"])
    out: dict[str, Any] = {}
    timing = {"load": time.perf_counter() - t0}
    sim = None
    for step in task["reference"]["steps"]:
        op = step["op"]
        if op == "solve":
            t0 = time.perf_counter()
            result = system.solve()
            timing["solve"] = time.perf_counter() - t0
            for key, path in step["read"].items():
                value = result.get(path, units[key])
                assert value is not None, f"{path} is undefined"
                out[key] = float(value)
        elif op == "simulate":
            t0 = time.perf_counter()
            sim = system.simulate(step["duration"], step["step"], step.get("events") or [])
            timing["simulate"] = time.perf_counter() - t0
            for key, path in (step.get("read_final") or {}).items():
                out[key] = float(sim.final.get(path, units[key]))  # type: ignore[arg-type]
            for field, pick in (("read_min", min), ("read_max", max)):
                for key, path in (step.get(field) or {}).items():
                    out[key] = float(pick(v for v in sim.get(path, units[key]) if v is not None))
            for key, src in (step.get("read_total") or {}).items():
                paths = [src] if isinstance(src, str) else src
                out[key] = sum(_series_total(sim, p, units[key]) for p in paths)
            for key, spec in (step.get("read_first_warning") or {}).items():
                times = [float(w.time or 0.0) for w in sim.warnings if w.path == spec["warning"]]
                assert times, f"warning {spec['warning']} is never raised"
                out[key] = float(convert(min(times), "s", spec.get("unit", "s")))
        elif op == "answer":
            out.update(step["values"])
        else:  # pragma: no cover - the tasks use only these ops
            raise AssertionError(f"unexpected op {op}")
    return {"answers": out, "system": system, "sim": sim, "timing": timing}


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
def runs(tasks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each reference executed once (the three 24-hour simulations take a few seconds each)."""
    return {tid: run_task(task) for tid, task in tasks.items()}


# -- structure --------------------------------------------------------------------------------


def test_four_scale_plant_tasks(tasks):
    assert set(tasks) == set(SIZES)
    assert {t["category"] for t in tasks.values()} == CATEGORIES
    assert {t["domain"] for t in tasks.values()} == DOMAINS


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_task_structure(path):
    task = load_task(path)
    assert task["id"] == path.stem and task["id"].startswith("scale-plant-")
    assert task["level"] == 4
    system = task["reference"]["system"]
    n, n_controls = SIZES[task["id"]]
    assert len(system["components"]) == n and 20 <= n <= 80
    assert len(system.get("controls", [])) == n_controls
    assert {c["type"] for c in system["components"]} <= {
        "supply", "drain", "pipe", "valve", "check_valve", "centrifugal_pump", "tank",
        "media_filter", "uv_reactor", "leak",
    }  # fmt: skip
    assert task["category"] in CATEGORIES and task["domain"] in DOMAINS
    keys = [a["key"] for a in task["answers"]]
    assert set(task["reference"]["expected"]) == set(keys)
    for a in task["answers"]:
        if a.get("kind", "number") == "number":
            assert a["unit"] and a.get("rel_tol") in (0.03, 0.05), a["key"]
    assert "|---" in task["prompt"], "a scale prompt gives its data in tables"


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_harness_loader_accepts_task(path):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    tasks_mod = pytest.importorskip("benchmarks.composition.harness.tasks")
    task = tasks_mod.load_task(path)
    assert task.level == 4 and task.component_count == SIZES[task.id][0]
    assert task.expected is not None
    assert tasks_mod.prompt_word_count(task.prompt) <= tasks_mod.SCALE_PROMPT_WORDS
    assert tasks_mod.table_problems(task.prompt) == []


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_reference_system_has_no_errors(path):
    system = wp.System.from_dict(load_task(path)["reference"]["system"])
    errors = [i for i in system.check() if i.severity == "error"]
    assert errors == []


# -- the frozen answers -----------------------------------------------------------------------


@pytest.mark.parametrize("tid", sorted(SIZES))
def test_reference_steps_reproduce_expected(tid, tasks, runs):
    got = runs[tid]["answers"]
    for key, value in tasks[tid]["reference"]["expected"].items():
        assert _same(got[key], value), f"{key}: executed {got[key]!r}, frozen {value!r}"


@pytest.mark.parametrize("tid", sorted(SIZES))
def test_reference_solves_and_simulates_in_reasonable_time(tid, runs):
    timing = runs[tid]["timing"]
    assert timing["load"] + timing.get("solve", 0.0) < MAX_SOLVE_S, timing
    assert timing.get("simulate", 0.0) < MAX_SIMULATE_S, timing


# -- facts the prompts and notes rely on --------------------------------------------------------


def _colebrook(re: float, rel: float) -> float:
    f = 0.02
    for _ in range(60):
        f = (-2.0 * math.log10(rel / 3.7 + 2.51 / (re * math.sqrt(f)))) ** -2
    return f


def _pipe_head(q_m3h: float, length: float, d_mm: float, k: float) -> float:
    """Darcy-Weisbach loss (m) with Colebrook friction, 0.1 mm roughness."""
    d = d_mm / 1000
    v = q_m3h / 3600 / (math.pi * d * d / 4)
    if v == 0:
        return 0.0
    f = _colebrook(RHO * abs(v) * d / MU, 0.1 / d_mm)
    return (f * length / d + k) * v * abs(v) / (2 * G)


def _kv_head(q_m3h: float, kv: float) -> float:
    return SG * q_m3h * abs(q_m3h) / kv**2 * BAR_M


def test_steady_plant_by_hand(tasks):
    """scale-plant-steady-01 without worldparts: header heads by fsolve, train flows by brentq."""
    task = tasks["scale-plant-steady-01"]
    trains = {"a": (1.0, 250.0), "b": (1.8, 250.0), "c": (1.0, 70.0)}  # media factor, valve Kv

    def train_flow(h_s: float, h_c: float, media: float, kv: float) -> float:
        def surplus(q: float) -> float:
            x = q / 100
            loss = (
                _pipe_head(q, 12, 150, 1.5)
                + _kv_head(q, 300)
                + (0.175 * media * x + 0.075 * x * x) * BAR_M  # filter
                + 0.12 * x * x * BAR_M  # UV reactor
                + _kv_head(q, kv)
            )
            return h_s + 24.0 - 0.0012 * q * q - loss - h_c

        return brentq(surplus, 1e-6, 141.4)

    def flows(heads: Any) -> dict[str, float]:
        return {x: train_flow(heads[0], heads[1], *trains[x]) for x in trains}

    def residual(heads: Any) -> list[float]:
        total = sum(flows(heads).values())
        return [
            3.0 - _pipe_head(total, 25, 300, 1.0) - heads[0],
            heads[1] - _pipe_head(total, 120, 300, 3.0) - (5.0 + 2.8),
        ]

    heads = fsolve(residual, [2.9, 8.3], xtol=1e-12)
    q = flows(heads)
    hand = {f"flow_{x}": q[x] for x in "abc"}
    hand["dose_a"] = 25 * 0.8 * 0.060 / (q["a"] / 3600)
    hand["dose_b"] = 25 * 0.060 / (q["b"] / 3600)
    hand["dose_c"] = 25 * 0.060 / (q["c"] / 3600)
    hand["total_flow"] = sum(q.values())
    hand["header_pressure"] = heads[1] / BAR_M
    expected = task["reference"]["expected"]
    for key, value in hand.items():
        assert value == pytest.approx(expected[key], rel=0.002), key  # within 0.2 %


def test_first_filter_and_the_trim_pump(runs):
    """Filter A crosses 0.45 bar first, well before B; C never does; the loop saturates."""
    sim = runs["scale-plant-clog-01"]["sim"]
    first = {w.path: float(w.time or 0.0) / 3600 for w in sim.warnings}
    assert first["filter_a.change_required"] < first["filter_b.change_required"] - 2.0
    assert "filter_c.change_required" not in first
    assert 11.0 < first["control.total_flow.control_saturated"] < 13.0
    total = sim.get("collector.volume_flow", "m3/h")
    before = [q for t, q in zip(sim.time, total, strict=True) if t <= 11 * 3600]
    assert all(abs(q - 300.0) < 0.5 for q in before)  # held until pump A saturates
    assert max(sim.get("pump_a.speed")) == pytest.approx(1.1)


def _switch_times(sim: wp.SimulationResult, path: str) -> list[float]:
    v = sim.get(path)
    return [sim.time[i] / 3600 for i in range(1, len(v)) if (v[i] > 0.5) != (v[i - 1] > 0.5)]


@pytest.mark.parametrize(
    ("tid", "lag", "clearwell_starts"),
    [
        ("scale-plant-tower-01", ["pump_2.speed"], []),
        ("scale-plant-transfer-01", ["intake_2.speed", "pump_c.speed", "lift_2.speed"],
         [3.5, 3.0, 2.5]),
    ],
)  # fmt: skip
def test_lag_units_never_start_and_extremes_avoid_thresholds(tid, lag, clearwell_starts, runs):
    sim = runs[tid]["sim"]
    for path in lag:
        assert max(sim.get(path)) == 0.0, path
    assert min(sim.get("tower.level")) > 1.5  # far above the lag start depth (1.0 m)
    low = min(sim.get("clearwell.level"))
    assert all(abs(low - start) > 0.15 for start in clearwell_starts)
    assert max(sim.get("clearwell.level")) < 5.5  # no overflow, top inlet (5.8 m) dry


def test_tower_balances(runs, tasks):
    """Pumped = plant feed + clearwell drawdown; delivered = pumped - tower rise."""
    sim = runs["scale-plant-tower-01"]["sim"]
    expected = tasks["scale-plant-tower-01"]["reference"]["expected"]
    feed = sim.get("plant_main.volume_flow", "m3/h")
    assert max(feed) - min(feed) < 1e-6  # the free mouth makes the plant feed constant
    area_cw, area_tw = math.pi * 24**2 / 4, math.pi * 12**2 / 4
    cw, tw = sim.get("clearwell.level"), sim.get("tower.level")
    pumped = feed[0] * 24 + area_cw * (cw[0] - cw[-1])
    assert pumped == pytest.approx(expected["pumped_volume"], rel=1e-6)
    delivered = expected["pumped_volume"] - area_tw * (tw[-1] - tw[0])
    assert delivered == pytest.approx(expected["delivered_volume"], rel=1e-6)
    spec = [e for e in sim.get("pump_1.specific_energy") if e is not None]
    mid = (min(spec) + max(spec)) / 2  # the hand estimate in the notes
    assert mid * expected["pumped_volume"] == pytest.approx(expected["pump_energy"], rel=0.02)


def test_transfer_balances(runs, tasks):
    """Each stage's total = the next stage's total + the rise of the tank between them."""
    sim = runs["scale-plant-transfer-01"]["sim"]
    e = tasks["scale-plant-transfer-01"]["reference"]["expected"]
    rise = {
        tank: math.pi * d**2 / 4 * (sim.get(f"{tank}.level")[-1] - sim.get(f"{tank}.level")[0])
        for tank, d in (("settling", 20), ("clearwell", 24), ("tower", 12))
    }
    delivered = sum(
        _series_total(sim, f"out_{n}.volume_flow", "m3") for n in ("j2", "j3", "j4", "j5", "j6")
    )
    assert e["intake_volume"] == pytest.approx(e["treated_volume"] + rise["settling"], rel=1e-6)
    assert e["treated_volume"] == pytest.approx(
        e["distributed_volume"] + rise["clearwell"], rel=1e-6
    )
    assert e["distributed_volume"] == pytest.approx(delivered + rise["tower"], rel=1e-6)


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_prompt_states_the_roughness_of_every_pipe(path):
    """Every reference pipe is 0.1 mm rough, and the prompt says so for all pipes.

    The audit of scale-plant-tower-01 found the station pipes' roughness unstated; the
    answers depend on it (smooth station pipes move the highest clearwell depth by 13 %).
    """
    task = load_task(path)
    pipes = [c for c in task["reference"]["system"]["components"] if c["type"] == "pipe"]
    assert pipes and all(c["parameters"]["roughness"] == 0.1 for c in pipes)
    prose = " ".join(task["prompt"].split()).lower()
    assert re.search(r"all pipes[^.]*roughness of 0\.1 mm|roughness 0\.1 mm for all pipes", prose)


def test_transfer_train_a_never_stops(runs, tasks):
    """The clearwell peak stays well below train A's stop depth, so train A runs all day."""
    sim = runs["scale-plant-transfer-01"]["sim"]
    controls = tasks["scale-plant-transfer-01"]["reference"]["system"]["controls"]
    stop = next(c["off_above"] for c in controls if c["name"] == "train_a")
    assert stop == 5.2
    assert max(sim.get("clearwell.level")) < stop - 0.25
    assert min(sim.get("pump_a.speed")) == 1.0
