"""The examples in examples/ run, stay short and keep their key numbers.

Each system document is validated against the system schema and solved through the CLI;
each script is imported and its ``main()`` called, and the numbers it reports are checked
against hand calculations, so the examples cannot silently rot.
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from importlib import resources
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest
import yaml

import worldparts as wp
from worldparts import cli

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DOCUMENTS = [
    "purification_skid.yaml",
    "pump_lift.yaml",
    "domestic_hot_water.yaml",
    "booster_station.yaml",
]
SCRIPTS = ["purification_skid.py", "pump_lift.py", "calibrate_pump_wear.py"]
MAX_LINES = 60
RHO, G, CP = 998.2, 9.80665, 4182.0
#: check() issues each document is expected to have: the lift's tanks are capped on purpose.
EXPECTED_ISSUES = {
    "purification_skid.yaml": set(),
    "pump_lift.yaml": {("unconnected_port", "ground.inlet"), ("unconnected_port", "roof.outlet")},
    "domestic_hot_water.yaml": set(),
    "booster_station.yaml": set(),
}


def load_system(name: str) -> wp.System:
    return wp.System.from_dict(yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8")))


def load_script(name: str) -> ModuleType:
    path = EXAMPLES / name
    spec = importlib.util.spec_from_file_location(f"example_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def skid_run() -> tuple[dict[str, Any], list[str]]:
    """Results and printed lines of examples/purification_skid.py."""
    return _run("purification_skid.py")


@pytest.fixture(scope="module")
def lift_run() -> tuple[dict[str, Any], list[str]]:
    """Results and printed lines of examples/pump_lift.py."""
    return _run("pump_lift.py")


@pytest.fixture(scope="module")
def wear_run() -> tuple[dict[str, Any], list[str]]:
    """Results and printed lines of examples/calibrate_pump_wear.py."""
    return _run("calibrate_pump_wear.py")


def _run(name: str) -> tuple[dict[str, Any], list[str]]:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        results = load_script(name).main()
    return results, buffer.getvalue().splitlines()


# ----------------------------------------------------------------------------------------
# system documents
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", DOCUMENTS)
def test_document_matches_system_schema(name: str) -> None:
    schema = json.loads(
        (resources.files("worldparts") / "schemas" / "system.schema.json").read_text("utf-8")
    )
    doc = yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_document_checks_clean(name: str) -> None:
    issues = load_system(name).check()
    assert {(i.code, i.where) for i in issues} == EXPECTED_ISSUES[name]
    assert all(i.severity == "warning" for i in issues)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_cli_validates_and_solves_document(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = str(EXAMPLES / name)
    assert cli.main(["validate", path]) == 0
    capsys.readouterr()
    assert cli.main(["solve", path, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["converged"]


@pytest.mark.parametrize("name", DOCUMENTS)
def test_document_round_trips(name: str) -> None:
    s = load_system(name)
    again = wp.System.from_dict(s.to_dict())
    assert again.solve().values == pytest.approx(s.solve().values, rel=1e-9, abs=1e-12)


def test_cli_simulates_skid_default_run(capsys: pytest.CaptureFixture[str]) -> None:
    """The skid document's own 48 h run (clogging in 6 h steps) works from the CLI."""
    assert cli.main(["simulate", str(EXAMPLES / "purification_skid.yaml"), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    first = {w["component"] + "." + w["code"]: w["time"] for w in out["warnings"]}
    assert set(first) == {"filter.change_required", "pump.outside_preferred_region"}
    assert min(first.values()) > 24 * 3600  # clean for the first day


# ----------------------------------------------------------------------------------------
# purification skid
# ----------------------------------------------------------------------------------------
def test_skid_design_point_by_hand() -> None:
    """Head balance, pump efficiency and UV dose at the design point, from first principles."""
    s = load_system("purification_skid.yaml")
    r = s.solve()
    assert r.warnings == []
    assert r.modes["pump"] == "running" and r.modes["uv"] == "disinfecting"
    q_h = r["pump.volume_flow"]  # m3/h
    q = q_h / 3600
    assert q_h == pytest.approx(21.89, abs=0.05)
    assert 0.7 < q_h / r["pump.bep_flow"] < 1.2  # preferred operating region
    # Kv losses in bar, (Q / Kv)**2 times the specific gravity: the valve (kv 40, linear,
    # 80 % open, 1e-4 leakage) and the tank nozzle (port_kv 200); the UV reactor's rated point.
    sg = RHO / 1000
    valve = sg * (q_h / (40 * (1e-4 + (1 - 1e-4) * 0.8))) ** 2
    nozzle = sg * (q_h / 200) ** 2
    uv = 0.1 * (q_h / 20) ** 2
    # Filter: 0.5 bar at 20 m3/h, 30 % quadratic housing, 70 % linear media.
    filt = 0.5 * (0.7 * q_h / 20 + 0.3 * (q_h / 20) ** 2)
    assert r["valve.pressure_drop"] == pytest.approx(valve, rel=1e-6)
    assert r["filter.pressure_drop"] == pytest.approx(filt, rel=1e-6)
    assert r["uv.pressure_drop"] == pytest.approx(uv, rel=1e-6)

    # Pipes: Darcy-Weisbach with the solver's friction factor, plus minor losses.
    def pipe_head(name: str, d: float, length: float, k: float) -> float:
        v = q / (math.pi * d * d / 4)
        return (r[f"{name}.friction_factor"] * length / d + k) * v * v / (2 * G)

    friction = pipe_head("suction", 0.08, 3, 0.5) + pipe_head("riser", 0.065, 40, 1)
    static = 16 - 3  # clearwell inlet above the tank bottom, minus the tank level
    fittings = (valve + nozzle + uv + filt) * 1e5 / (RHO * G)
    assert r["pump.head"] == pytest.approx(static + friction + fittings, abs=0.02)
    hydraulic = RHO * G * q * r["pump.head"] / 1000
    assert r["pump.efficiency"] == pytest.approx(100 * hydraulic / r["pump.shaft_power"], rel=1e-6)
    assert r["pump.specific_energy"] == pytest.approx(r["pump.shaft_power"] / q_h, rel=1e-6)
    # Dose = fluence rate x residence time = 20 mW/cm2 x 15 L / Q.
    assert r["uv.dose"] == pytest.approx(20 * 0.015 / q, rel=1e-9)
    assert r["uv.dose"] == pytest.approx(49.3, abs=0.1)


def test_skid_script_output(skid_run: tuple[dict[str, Any], list[str]]) -> None:
    results, lines = skid_run
    assert 20 < len(lines) <= MAX_LINES
    assert lines[1] == "check(): 0 issue(s)"
    assert results["design"].warnings == []


def test_skid_clogging_run(skid_run: tuple[dict[str, Any], list[str]]) -> None:
    sim = skid_run[0]["run"]
    assert isinstance(sim, wp.SimulationResult)
    flow = sim["pump.volume_flow"]
    dp = sim["filter.pressure_drop"]
    dose = sim["uv.dose"]
    assert all(a >= b for a, b in pairwise(flow))  # flow falls
    assert all(a <= b for a, b in pairwise(dp))  # filter dp rises
    assert all(a <= b for a, b in pairwise(dose))  # dose rises with it
    first = {w.path: w for w in sim.warnings}
    assert set(first) == {"filter.change_required", "pump.outside_preferred_region"}
    change = first["filter.change_required"]
    assert 44 * 3600 <= change.time <= 48 * 3600
    assert change.last_time == 48 * 3600 and change.active_at_end
    assert 38 * 3600 <= first["pump.outside_preferred_region"].time < change.time


def test_skid_diagnosis(skid_run: tuple[dict[str, Any], list[str]]) -> None:
    ranking = skid_run[0]["ranking"]
    best, *others = ranking
    assert best["vary"] == "filter.clogging"
    assert best["ratio"] == pytest.approx(2.0, abs=0.15)
    assert {h["vary"] for h in others} == {"valve.opening", "pump.speed"}
    # A closed valve or a slower pump lowers the flow through a clean filter, so its dp falls
    # with the flow: 0.5 bar x (0.7 x 17.51/20 + 0.3 x (17.51/20)**2) over the design value.
    for h in others:
        assert h["ratio"] == pytest.approx(0.749, abs=0.005)
        assert h["value"] < 1.0


def test_skid_goal_seek_restores_dose(skid_run: tuple[dict[str, Any], list[str]]) -> None:
    results, lines = skid_run
    r = results["restored"]
    assert r["uv.dose"] == pytest.approx(42.0, abs=1e-4)
    assert r.warnings == []
    # With a 75 % lamp, 42 mJ/cm2 needs a residence time of 42 / 15 = 2.8 s in 15 L.
    assert r["pump.volume_flow"] == pytest.approx(15 / 2.8 * 3.6, rel=1e-4)
    assert results["opening"] == pytest.approx(0.545, abs=0.005)
    assert any("uv.underdose" in line for line in lines)


# ----------------------------------------------------------------------------------------
# pump lift
# ----------------------------------------------------------------------------------------
def test_lift_full_speed(lift_run: tuple[dict[str, Any], list[str]]) -> None:
    results, lines = lift_run
    assert len(lines) <= MAX_LINES
    full = results["full"]
    assert full.warnings == []
    assert full["pump.volume_flow"] == pytest.approx(18.65, abs=0.05)
    assert 0.9 < full["pump.volume_flow"] / full["pump.bep_flow"] < 1.1
    assert full["riser.velocity"] < 3  # below the pipe's high_velocity limit
    # The script's hand check (Swamee-Jain, static lift, Kv losses) closes the head balance.
    assert results["hand"]["total"] == pytest.approx(full["pump.head"], abs=0.05)
    assert results["hand"]["static"] == pytest.approx(25 + 0.3 - 1.5)


def test_lift_speed_against_throttling(lift_run: tuple[dict[str, Any], list[str]]) -> None:
    results, _ = lift_run
    by_speed, by_valve = results["by_speed"], results["by_valve"]
    assert by_speed["pump.volume_flow"] == pytest.approx(15, abs=1e-6)
    assert by_valve["pump.volume_flow"] == pytest.approx(15, abs=1e-6)
    assert results["speed"] == pytest.approx(0.917, abs=0.002)
    assert by_speed.warnings == [] and by_valve.warnings == []
    # Same flow, same system curve downstream: the throttled pump's extra head is burnt in
    # the valve (Kv 150 at opening y: dp = (15 / (150 y))**2 bar).
    extra = (by_valve["pump.head"] - by_speed["pump.head"]) * RHO * G / 1e5
    open_dp = RHO / 1000 * (15 / 150) ** 2
    assert extra == pytest.approx(by_valve["valve.pressure_drop"] - open_dp, abs=1e-6)
    saving = 1 - by_speed["pump.specific_energy"] / by_valve["pump.specific_energy"]
    assert saving == pytest.approx(0.20, abs=0.02)


def test_lift_drain_down(lift_run: tuple[dict[str, Any], list[str]]) -> None:
    results, _ = lift_run
    volume = math.pi * 2**2 / 4 * 1.5  # m3 in the ground tank
    assert results["estimate_min"] == pytest.approx(volume / 15 * 60)
    assert results["empty_min"] == pytest.approx(results["refined_min"], rel=0.03)
    sim = results["sim"]
    moved = math.pi * 2.5**2 / 4 * (sim.final["roof.level"] - 0.3)
    assert moved == pytest.approx(volume, rel=2e-3)  # the water ends up on the roof


# ----------------------------------------------------------------------------------------
# pump wear calibration (design 14.1 and 14.2)
# ----------------------------------------------------------------------------------------
#: The wear the survey readings were made with (examples/pump_wear_readings.yaml).
WEAR_TRUTH = {"pump.wear_head": 0.10, "pump.wear_efficiency": 0.12}


def test_wear_readings_are_a_valid_measurement_set() -> None:
    survey = wp.load_measurements(EXAMPLES / "pump_wear_readings.yaml")
    assert len(survey) == 4 and survey.n_values == 12
    resolved = survey.resolve(load_system("pump_lift.yaml"))
    assert {v.sigma for v in resolved.values} == {0.2, 0.02, 0.03}
    assert not any(v.sigma_default for v in resolved.values)


def test_wear_plan_needs_a_power_reading(wear_run: tuple[dict[str, Any], list[str]]) -> None:
    """Pressure and flow determine head wear only; the recommended extra sensor is one
    that sees the shaft power."""
    results, lines = wear_run
    assert len(lines) <= MAX_LINES
    plan = results["plan"]
    assert plan.parameters["pump.wear_head"].verdict == "identifiable"
    assert plan.parameters["pump.wear_efficiency"].verdict == "not_identifiable"
    rec = plan.recommendation
    assert rec is not None and rec.parameter == "pump.wear_efficiency"
    assert rec.sensor in ("pump.efficiency", "pump.shaft_power", "pump.specific_energy")
    assert rec.verdict == "identifiable"


def test_wear_fit_recovers_the_survey_wear(wear_run: tuple[dict[str, Any], list[str]]) -> None:
    """The fit finds the wear the readings were made with, within two standard errors,
    and the misfit is what the instrument uncertainties explain."""
    fit = wear_run[0]["fit"]
    for path, truth in WEAR_TRUTH.items():
        est = fit.parameters[path]
        assert est.verdict == "identifiable"
        assert abs(est.value - truth) < 2 * est.standard_error
        assert est.standard_error < 0.01
    assert fit.dof == 10 and 0.3 < fit.reduced_chi_square < 2.0
    # Without the power readings efficiency wear has no estimate, and says so.
    partial = wear_run[0]["partial"]
    assert partial.parameters["pump.wear_efficiency"].verdict == "not_identifiable"
    assert partial.parameters["pump.wear_efficiency"].standard_error is None
    assert partial.parameters["pump.wear_efficiency"].value == 0.0  # left at the start
    # ... which is not a fit pushed onto the lower bound (review finding).
    assert partial.parameters["pump.wear_efficiency"].at_bound is None
    assert not any("bound" in note for note in partial.notes)
    assert partial["pump.wear_head"] == pytest.approx(fit["pump.wear_head"], abs=0.005)


def test_wear_costs_flow_and_energy(wear_run: tuple[dict[str, Any], list[str]]) -> None:
    """The worn pump: head (1 - w_h) H_new, efficiency (1 - w_e) at the same flow (design
    13.3); on the lift's system curve it delivers less water at more energy per m3."""
    results, lines = wear_run
    new, worn, fit = results["new"], results["worn"], results["fit"]
    assert new["pump.volume_flow"] == pytest.approx(18.65, abs=0.05)  # the lift example
    assert worn["pump.volume_flow"] < new["pump.volume_flow"]
    assert worn["pump.specific_energy"] > new["pump.specific_energy"]
    assert worn["pump.wear_head"] == pytest.approx(fit["pump.wear_head"], rel=1e-12)
    assert any("more energy per m3" in line for line in lines)


# ----------------------------------------------------------------------------------------
# domestic hot water
# ----------------------------------------------------------------------------------------
def test_hot_water_heater_saturates() -> None:
    s = load_system("domestic_hot_water.yaml")
    r = s.solve()
    assert r.modes["heater"] == "saturated"
    assert [w.path for w in r.warnings] == ["heater.setpoint_not_met"]
    assert r["heater.heat_rate"] == pytest.approx(18)
    m = r["heater.volume_flow"] / 60000 * RHO  # kg/s
    assert r["heater.outlet_temperature"] == pytest.approx(12 + 18000 / (m * CP), rel=1e-6)
    assert r["heater.outlet_temperature"] == pytest.approx(38.9, abs=0.2)
    # The shower alone stays within the heater's power.
    s.set("basin.lift", 0)
    alone = s.solve()
    assert alone.modes["heater"] == "heating"
    assert alone.warnings == []
    assert alone["heater.outlet_temperature"] == pytest.approx(55, abs=0.01)


# ----------------------------------------------------------------------------------------
# booster station (a PI pressure loop, design 13.1; a ramp in the document, 13.2)
# ----------------------------------------------------------------------------------------
def test_booster_holds_zone_pressure_by_hand() -> None:
    """At full demand the zone valve (Kv 12) passes 12 sqrt(2.5 / sg) m3/h at the 2.5 bar
    setpoint, and the pump head is the 1.5 bar lift over the mains plus the main's
    Darcy-Weisbach and fitting losses."""
    s = load_system("booster_station.yaml")
    s.set("demand.opening", 1.0)
    r = s.solve()
    assert r["main.port_b.p"] == pytest.approx(2.5, abs=1e-8)
    q_h = 12 * math.sqrt(2.5 / (RHO / 1000))
    assert r["pump.volume_flow"] == pytest.approx(q_h, rel=1e-8)
    v = q_h / 3600 / (math.pi * 0.065**2 / 4)
    friction = (r["main.friction_factor"] * 120 / 0.065 + 2) * v * v / (2 * G)
    assert r["pump.head"] == pytest.approx(1.5e5 / (RHO * G) + friction, abs=1e-3)
    assert r["pump.speed"] == pytest.approx(0.868, abs=1e-3)
    assert r.controls["zone_pressure"].saturated is False
    assert r.warnings == []


def test_booster_simulation_rides_through_the_demand_ramp() -> None:
    s = load_system("booster_station.yaml")
    steady_low = load_system("booster_station.yaml").solve()["pump.speed"]
    full = load_system("booster_station.yaml")
    full.set("demand.opening", 1.0)
    steady_high = full.solve()["pump.speed"]
    sim = s.simulate()  # the document's 10 min run: the ramp from 2 to 5 min
    t = sim.time
    zone = sim["main.port_b.p"]
    speed = sim["control.zone_pressure.output"]
    assert sim["demand.opening"][t.index(120.0)] == 0.5
    assert sim["demand.opening"][t.index(300.0)] == 1.0
    # Settled before the ramp; during it the pressure droops by less than 2 %; afterwards
    # the loop is back at the setpoint at the full-demand speed.
    assert zone[t.index(119.0)] == pytest.approx(2.5, abs=1e-4)
    assert speed[t.index(119.0)] == pytest.approx(steady_low, rel=1e-4)
    assert 2.45 < min(zone[t.index(120.0) :]) < 2.5
    assert zone[-1] == pytest.approx(2.5, abs=1e-6)
    assert speed[-1] == pytest.approx(steady_high, rel=1e-6)
    assert all(0.3 < u < 1.2 for u in speed)
    assert [w.path for w in sim.warnings] == ["pump.outside_preferred_region"]
    assert sim.warnings[0].active_at_end is False  # low demand only
    assert sim.controls["zone_pressure"].saturated is False


def test_cli_simulates_booster_default_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["simulate", str(EXAMPLES / "booster_station.yaml")]) == 0
    out = capsys.readouterr().out
    assert "Controls at the end" in out and "zone_pressure" in out


# ----------------------------------------------------------------------------------------
# scripts as documented
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", SCRIPTS)
def test_script_runs_as_documented(name: str) -> None:
    """``python examples/<name>`` exits 0 from any working directory."""
    done = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=EXAMPLES.parent.parent,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert len(done.stdout.splitlines()) <= MAX_LINES


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_uses_public_api_only(name: str) -> None:
    source = (EXAMPLES / name).read_text(encoding="utf-8")
    assert "import worldparts as wp" in source
    assert "from worldparts" not in source
    assert "import worldparts." not in source
    assert "wp._" not in source
