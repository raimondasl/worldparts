"""Tests for scenario and contract evaluation."""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest

import worldparts as wp
from worldparts.contracts import _evaluate_check
from worldparts.manifest import Manifest, validate_manifest_data
from worldparts.results import ComponentWarning, SolveResult


def fake(values: dict[str, Any], warnings: list[str] = ()) -> SolveResult:  # type: ignore[assignment]
    comps = [ComponentWarning(w.split(".")[0], w.split(".")[1], "warning", "") for w in warnings]
    return SolveResult(True, 1, 0.0, values, {k: "1" for k in values}, warnings=comps)


def points(values: list[Any], path: str = "dut.y") -> list[tuple[Any, SolveResult]]:
    return [(i, fake({path: v})) for i, v in enumerate(values)]


def test_monotonic_check() -> None:
    inc = {"type": "monotonic", "variable": "dut.y", "direction": "increasing"}
    assert _evaluate_check(inc, points([1, 2, 2, 3]), "x") == []
    assert _evaluate_check({**inc, "strict": True}, points([1, 2, 2, 3]), "x")
    assert _evaluate_check(inc, points([1, 2, 1.5]), "x")
    assert _evaluate_check(inc, points([1, None, 2]), "x") == []  # None is skipped
    assert _evaluate_check(inc, points([1, 1 - 1e-12]), "x") == []  # within tolerance
    dec = {**inc, "direction": "decreasing", "strict": True}
    assert _evaluate_check(dec, points([3, 2, 1]), "x") == []
    assert _evaluate_check(inc, points([None, 1]), "x")  # too few points
    assert _evaluate_check(inc, points([1, 2], "dut.z"), "x") == ["unknown variable dut.y"]


def test_bounds_check_with_expressions() -> None:
    chk = {"type": "bounds", "variable": "dut.y", "min": 0, "max": "dut.limit * 2"}
    pts = [(0, fake({"dut.y": 3.0, "dut.limit": 2.0})), (1, fake({"dut.y": 5.0, "dut.limit": 2.0}))]
    failures = _evaluate_check(chk, pts, "x")
    assert len(failures) == 1 and "above maximum 4" in failures[0] and "x = 1" in failures[0]


def test_equal_check_tolerances() -> None:
    chk = {"type": "equal", "left": "dut.a", "right": "dut.b * 2", "abs_tol": 1e-6}
    assert _evaluate_check(chk, [(0, fake({"dut.a": 2.0, "dut.b": 1.0}))], None) == []
    assert _evaluate_check(chk, [(0, fake({"dut.a": 2.1, "dut.b": 1.0}))], None)
    rel = {"type": "equal", "left": "dut.a", "right": "dut.b", "rel_tol": 0.01}
    assert _evaluate_check(rel, [(0, fake({"dut.a": 100.0, "dut.b": 100.5}))], None) == []
    assert _evaluate_check(rel, [(0, fake({"dut.a": None, "dut.b": 1.0}))], None) == [
        "no sweep point could be evaluated (all values undefined)"
    ]


def test_warning_iff_check() -> None:
    chk = {"type": "warning_iff", "code": "dut.hot", "condition": "dut.t > 50"}
    good = [(0, fake({"dut.t": 60.0}, ["dut.hot"])), (1, fake({"dut.t": 40.0}))]
    assert _evaluate_check(chk, good, "x") == []
    bad = [(0, fake({"dut.t": 60.0})), (1, fake({"dut.t": 40.0}, ["dut.hot"]))]
    assert len(_evaluate_check(chk, bad, "x")) == 2


def valve() -> Manifest:
    return wp.default_catalog().get("valve")


def test_run_scenario_reports_failures() -> None:
    scen = copy.deepcopy(valve().scenario("kv-at-1-bar"))
    scen["expect"] = [
        {"variable": "dut.volume_flow", "value": 10.0, "abs_tol": 0.1},
        {"variable": "dut.volume_flow", "min": 50},
        {"variable": "dut.volume_flow", "max": 5},
        {"warning": "dut.high_pressure_drop", "present": True},
        {"mode": "dut", "is": "closed"},
        {"variable": "dut.nothing", "value": 1.0},
    ]
    out = wp.run_scenario(valve(), scen)
    assert not out.passed
    text = "\n".join(out.failures)
    assert "expected 10" in text
    assert "below the expected minimum 50" in text
    assert "above the expected maximum 5" in text
    assert "expected present" in text
    assert "expected 'closed', got 'open'" in text
    assert "unknown variable dut.nothing" in text


def test_run_scenario_catches_system_errors() -> None:
    scen = copy.deepcopy(valve().scenario("kv-at-1-bar"))
    scen["system"]["connections"] = []
    out = wp.run_scenario(valve(), scen)
    assert not out.passed
    assert "no_pressure_reference" in out.failures[0]


def test_scenario_with_a_ramp_event() -> None:
    """A scenario's simulate.events accept the ramp form (design 13.2): the schema allows it
    and the scenario runner hands it to System.simulate.

    Hand calculation: the command ramps 0 -> 0.5 over 10 s, so it is 0.05 k during the
    step that starts at k s; the 10 s actuator follows each step exactly,
    x_(k+1) = u_k + (x_k - u_k) a with a = exp(-1 s / 10 s), from x_0 = 0.
    """
    data = copy.deepcopy(valve().data)
    scen = copy.deepcopy(valve().scenario("actuator-lag"))
    a = math.exp(-0.1)
    position = sum(0.05 * k * (1 - a) * a ** (9 - k) for k in range(10))
    assert position == pytest.approx(0.05 * (10 - 1 / (1 - a) + a**10 / (1 - a)), rel=1e-12)
    scen.update(
        id="actuator-ramp",
        description="The command ramps from closed to half open over 10 s.",
        simulate={
            "duration": "10 s",
            "step": "1 s",
            "events": [{"at": "0 s", "ramp": {"dut.opening": [0, 0.5]}, "over": "10 s"}],
        },
        expect=[
            {"variable": "dut.opening", "value": 0.5, "abs_tol": 1e-12},
            {"variable": "dut.position", "value": position, "abs_tol": 1e-9},
            {"mode": "dut", "is": "throttling"},
        ],
    )
    data["scenarios"] = [*data["scenarios"], scen]
    assert validate_manifest_data(data) == []
    m = Manifest.from_dict(data)
    out = wp.run_scenario(m, "actuator-ramp")
    assert out.passed, out.failures
    # The same ramp written as one set event per step gives the same final sample.
    steps = [{"at": k, "set": {"dut.opening": 0.05 * k}} for k in range(11)]
    explicit = {**scen, "simulate": {**scen["simulate"], "events": steps}}
    assert wp.run_scenario(m, explicit).passed
    # The schema still rejects malformed events: a ramp without 'over', set and ramp together.
    for event in (
        {"at": "0 s", "ramp": {"dut.opening": [0, 0.5]}},
        {"at": "0 s", "set": {"dut.opening": 1}, "ramp": {"dut.opening": [0, 1]}, "over": 5},
        {"at": "0 s", "ramp": {"dut.opening": [0]}, "over": 5},
    ):
        bad = copy.deepcopy(data)
        bad["scenarios"][-1]["simulate"]["events"] = [event]
        assert validate_manifest_data(bad), event
    # A ramp that ends after the simulation fails the scenario with the reason.
    late = copy.deepcopy(scen)
    late["simulate"]["events"][0]["over"] = "20 s"
    failed = wp.run_scenario(m, late)
    assert not failed.passed and "after the end of the simulation" in failed.failures[0]


def test_run_contract_by_id_and_failure() -> None:
    m = valve()
    assert wp.run_contract(m, "kv-law").passed
    contract = copy.deepcopy(
        next(c for c in m.contracts if c["id"] == "flow-increases-with-opening")
    )
    contract["check"]["direction"] = "decreasing"
    out = wp.run_contract(m, contract)
    assert not out.passed and out.points == 11
    assert "not strictly decreasing" in out.failures[0]
    with pytest.raises(wp.WorldpartsError, match="no contract"):
        wp.run_contract(m, "nope")


def test_contract_sweep_values_with_units() -> None:
    contract = {
        "id": "units",
        "description": "Sweep values carry units.",
        "scenario": "kv-at-1-bar",
        "sweep": {"variable": "src.pressure", "values": ["50 kPa", "1 bar", "150000 Pa"]},
        "check": {"type": "monotonic", "variable": "dut.volume_flow", "direction": "increasing",
                  "strict": True},
    }  # fmt: skip
    out = wp.run_contract(valve(), contract)
    assert out.passed and out.points == 3


def test_simulated_scenario_contract(test_catalog: wp.Catalog) -> None:
    """Contracts on simulated scenarios rebuild the system for every sweep point."""
    m = valve()
    contract = {
        "id": "lag",
        "description": "Slower actuators are further from the command after 30 s.",
        "scenario": "actuator-lag",
        "sweep": {"variable": "dut.actuator_time", "values": [1, 5, 10, 20]},
        "check": {"type": "monotonic", "variable": "dut.position", "direction": "decreasing",
                  "strict": True},
    }  # fmt: skip
    out = wp.run_contract(m, contract, test_catalog)
    assert out.passed, out.failures


def test_run_component_and_report(test_catalog: wp.Catalog) -> None:
    report = wp.run_component("heated_tank", test_catalog)
    assert report.passed, report.summary()
    d = report.to_dict()
    assert d["component"] == "worldparts.testing.heated_tank"
    assert len(d["scenarios"]) == 2 and len(d["contracts"]) == 4
    seen = set().union(*(o.warnings for o in report.outcomes))
    assert ("worldparts.testing.heated_tank", "overheat") in seen
    assert "PASS" in report.summary()


def test_check_catalog_single_component() -> None:
    reports = wp.check_catalog(component="check_valve")
    assert [r.component for r in reports] == ["worldparts.hydraulic.check_valve"]
    assert reports[0].passed


def test_warning_expectation_on_unknown_instance_fails() -> None:
    m = wp.default_catalog().get("valve")
    scen = copy.deepcopy(m.scenario("kv-at-1-bar"))
    scen["expect"] = [{"warning": "dutt.high_pressure_drop", "present": False}]
    out = wp.run_scenario(m, scen)
    assert not out.passed and "no instance 'dutt'" in out.failures[0]
