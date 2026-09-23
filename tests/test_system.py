"""Tests for the System composition API, results and the core features for later components."""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

import worldparts as wp
import worldparts.components
from worldparts.errors import ContractError
from worldparts.manifest import Table, _schema_errors
from worldparts.media import CP, RHO, G


def kv_flow_lmin(kv: float, dp_bar: float) -> float:
    """Volume flow in L/min through Kv (m3/h) at dp (bar), water at 998.2 kg/m3."""
    return math.copysign(kv * math.sqrt(abs(dp_bar) * 1000 / RHO) * 1000 / 60, dp_bar)


def basic(pressure: object = 3, **valve: object) -> wp.System:
    s = wp.System("basic")
    s.add("mains", "supply", pressure=pressure, temperature="15 degC")
    s.add("v", "valve", **valve)
    s.add("out", "drain")
    s.connect("mains.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


# -- composition ----------------------------------------------------------------------------


def test_add_by_alias_and_full_id() -> None:
    s = wp.System()
    s.add("a", "valve")
    s.add("b", "worldparts.hydraulic.valve")
    assert s.components == {"a": "worldparts.hydraulic.valve", "b": "worldparts.hydraulic.valve"}


def test_add_unknown_type_lists_alternatives() -> None:
    s = wp.System()
    with pytest.raises(wp.UnknownComponentError) as exc:
        s.add("x", "valv")
    msg = str(exc.value)
    assert "valve" in msg and "check_valve" in msg and "Did you mean" in msg


def test_add_unknown_parameter_lists_names() -> None:
    s = wp.System()
    with pytest.raises(wp.UnknownVariableError) as exc:
        s.add("v", "valve", kvs=3)
    assert "kv" in str(exc.value) and "opening" in str(exc.value)


def test_add_out_of_range_names_range() -> None:
    s = wp.System()
    with pytest.raises(wp.OutOfRangeError) as exc:
        s.add("v", "valve", opening=1.5)
    assert "v.opening" in str(exc.value) and "[0, 1]" in str(exc.value)
    assert exc.value.code == "parameter_out_of_range"


@pytest.mark.parametrize("name", ["1abc", "a.b", "", "with space"])
def test_invalid_instance_names(name: str) -> None:
    with pytest.raises(wp.InvalidValueError):
        wp.System().add(name, "valve")


def test_duplicate_instance_name() -> None:
    s = wp.System()
    s.add("v", "valve")
    with pytest.raises(wp.InvalidValueError, match="already exists"):
        s.add("v", "pipe")


def test_values_with_units_are_converted() -> None:
    s = wp.System()
    s.add("mains", "supply", pressure="350 kPa", temperature="300 K")
    assert s.get("mains.pressure") == pytest.approx(3.5)
    assert s.get("mains.temperature") == pytest.approx(26.85)
    assert s.get("mains.pressure", unit="kPa") == pytest.approx(350.0)
    s.add("p", "pipe", parameters={"diameter": "0.02 m"}, length="300 cm")
    assert s.get("p.diameter") == pytest.approx(20.0)
    assert s.component("p").parameters["diameter"] == pytest.approx(0.02)  # SI inside
    assert s.component("mains").parameters["pressure"] == pytest.approx(wp.P_ATM + 3.5e5)


def test_connect_errors() -> None:
    s = basic()
    with pytest.raises(wp.UnknownPortError) as exc:
        s.connect("v.inlet", "out.port")
    assert "v.port_a" in str(exc.value) and "v.port_b" in str(exc.value)
    with pytest.raises(wp.UnknownComponentError, match="mains"):
        s.connect("main.port", "v.port_a")
    with pytest.raises(wp.SelfConnectionError):
        s.connect("v.port_a", "v.port_a")
    with pytest.raises(wp.UnknownPortError, match=r"<instance>\.<port>"):
        s.connect("v", "out.port")


def test_disconnect_and_remove() -> None:
    s = basic()
    s.disconnect("out.port", "v.port_b")
    assert s.connections == [("mains.port", "v.port_a")]
    with pytest.raises(wp.InvalidValueError, match="not connected"):
        s.disconnect("out.port", "v.port_b")
    s.remove("v")
    assert s.connections == []
    assert set(s.components) == {"mains", "out"}
    with pytest.raises(wp.UnknownComponentError):
        s.remove("v")


def test_set_errors_and_state_setting() -> None:
    s = basic()
    with pytest.raises(wp.UnknownVariableError, match="read-only"):
        s.set("v.volume_flow", 3)
    with pytest.raises(wp.UnknownVariableError, match="read-only"):
        s.set("v.port_a.p", 3)
    with pytest.raises(wp.UnknownVariableError, match=r"v\.opening"):
        s.set("v.openin", 3)
    with pytest.raises(wp.InvalidValueError):
        s.set("v.characteristic", "parabolic")
    s.set("v.characteristic", "quick_opening")
    s.set("v.position", 0.3)  # states can be set too
    assert s.get("v.position") == pytest.approx(0.3)
    with pytest.raises(wp.UnknownVariableError):
        s.get("v.volume_flow")


def test_set_values_is_atomic() -> None:
    s = basic()
    with pytest.raises(wp.OutOfRangeError):
        s.set_values({"v.opening": 0.5, "mains.pressure": 1000})
    assert s.get("v.opening") == 1.0
    s.set_values({"v.opening": "50 %", "mains.pressure": "2 bar"})
    assert s.get("v.opening") == pytest.approx(0.5)


# -- check ----------------------------------------------------------------------------------


def codes(issues: list[wp.Issue]) -> dict[str, str]:
    return {i.code: i.severity for i in issues}


def test_check_clean_system() -> None:
    assert basic().check() == []


def test_check_unconnected_port_is_warning_and_capped() -> None:
    s = wp.System()
    s.add("mains", "supply", pressure=2)
    s.add("v", "valve")
    s.connect("mains.port", "v.port_a")
    issues = s.check()
    assert codes(issues) == {"unconnected_port": "warning"}
    assert issues[0].where == "v.port_b"
    r = s.solve()
    assert r["v.volume_flow"] == pytest.approx(0.0, abs=1e-9)
    assert r["v.port_b.p"] == pytest.approx(2.0, abs=1e-6)
    assert r["v.port_b.T"] is None  # no inflow into a capped port
    view_builder = s._instances["v"].builder
    assert view_builder is not None
    assert view_builder.is_connected("port_a") and not view_builder.is_connected("port_b")
    assert r.issues and r.issues[0].code == "unconnected_port"


def test_check_no_pressure_reference() -> None:
    s = wp.System()
    s.add("p1", "pipe")
    s.add("p2", "pipe")
    s.connect("p1.port_b", "p2.port_a")
    issues = s.check()
    assert codes(issues)["no_pressure_reference"] == "error"
    bad = next(i for i in issues if i.code == "no_pressure_reference")
    assert "p1.port_a" in bad.where and "p2.port_b" in bad.where
    with pytest.raises(wp.SystemCheckError) as exc:
        s.solve()
    assert any(i.code == "no_pressure_reference" for i in exc.value.issues)
    assert "no_pressure_reference" in str(exc.value)


def test_from_dict_reports_every_problem() -> None:
    doc = {
        "worldparts_system": "0.1",
        "name": "broken",
        "components": [
            {"name": "mains", "type": "supply", "parameters": {"pressure": 500}},
            {"name": "v", "type": "valve", "parameters": {"kv": "3 bar", "nope": 1}},
            {"name": "ghost", "type": "flux_capacitor"},
            {"name": "out", "type": "drain"},
        ],
        "connections": [
            ["mains.port", "v.port_a"],
            ["v.port_c", "out.port"],
            ["v.port_b", "v.port_b"],
            ["ghost.port", "out.port"],
            ["nobody.port", "out.port"],
        ],
    }
    s = wp.System.from_dict(doc)
    found = {(i.code, i.where) for i in s.check()}
    assert ("parameter_out_of_range", "mains.pressure") in found
    assert ("invalid_value", "v.kv") in found
    assert ("invalid_value", "v.nope") in found
    assert ("unknown_component", "ghost") in found
    assert ("unknown_port", "v.port_c -- out.port") in found
    assert ("self_connection", "v.port_b -- v.port_b") in found
    assert ("unknown_component", "nobody.port -- out.port") in found
    with pytest.raises(wp.SystemCheckError):
        s.solve()
    # the document round-trips with its problems preserved
    again = s.to_dict()
    assert again["components"][1]["parameters"]["kv"] == "3 bar"
    assert {"name": "ghost", "type": "flux_capacitor"} in again["components"]
    assert _schema_errors("system", again) == []
    # fixing the values clears the issues
    s.set("mains.pressure", 3)
    s.set("v.kv", 2)
    assert ("parameter_out_of_range", "mains.pressure") not in {
        (i.code, i.where) for i in s.check()
    }


def test_from_dict_rejects_schema_violations() -> None:
    with pytest.raises(wp.InvalidValueError, match=r"system\.schema\.json"):
        wp.System.from_dict({"worldparts_system": "0.2", "name": "x", "components": []})
    with pytest.raises(wp.InvalidValueError, match="connections/0"):
        wp.System.from_dict(
            {"worldparts_system": "0.1", "name": "x", "components": [], "connections": [["a"]]}
        )


def test_incompatible_ports(test_catalog: wp.Catalog) -> None:
    # All v0.1 ports are water; build a fake port type to exercise the check.
    s = wp.System(catalog=test_catalog)
    s.add("a", "valve")
    s.add("b", "valve")
    spec = s.manifest("b").ports["port_a"]
    object.__setattr__(spec, "medium", "oil")
    try:
        with pytest.raises(wp.IncompatiblePortsError, match="medium"):
            s.connect("a.port_b", "b.port_a")
    finally:
        object.__setattr__(spec, "medium", "water")


# -- solve and results ----------------------------------------------------------------------


def test_solve_values_units_modes() -> None:
    s = basic(pressure="2 bar", opening=0.5)
    r = s.solve()
    assert r.converged and r.max_residual < 1e-9
    expected = kv_flow_lmin(2.5 * (1e-4 + (1 - 1e-4) * 0.5), 2.0)
    assert r["v.volume_flow"] == pytest.approx(expected, rel=1e-6)
    assert r.units["v.volume_flow"] == "L/min"
    assert r.get("v.volume_flow", unit="L/s") == pytest.approx(expected / 60, rel=1e-6)
    assert r.get("v.volume_flow", unit="m3/h") == pytest.approx(expected * 0.06, rel=1e-6)
    assert r["mains.port.p"] == pytest.approx(2.0, abs=1e-6)
    assert r.units["mains.port.p"] == "bar"
    assert r.get("mains.port.p", unit="kPa") == pytest.approx(200.0, abs=1e-4)  # gauge kept
    assert r.get("v.pressure_drop", unit="kPa") == pytest.approx(200.0, abs=1e-4)
    assert r["v.port_a.m_flow"] == pytest.approx(expected / 60000 * RHO, rel=1e-6)
    assert r.units["v.port_a.m_flow"] == "kg/s"
    assert r["out.port.T"] == pytest.approx(15.0)
    assert r.units["out.port.T"] == "degC"
    assert r["v.opening"] == 0.5 and r["v.position"] == 0.5
    assert r["mains.pressure"] == pytest.approx(2.0)
    assert r.modes == {"mains": "supplying", "v": "throttling", "out": "receiving"}
    assert r.warnings == []
    d = r.to_dict()
    assert d["values"]["v.volume_flow"] == {"value": r["v.volume_flow"], "unit": "L/min"}
    with pytest.raises(wp.UnknownVariableError, match=r"v\.volume_flow"):
        r["v.volume_flw"]
    with pytest.raises(wp.UnitError):
        r.get("v.volume_flow", unit="bar")


def test_envelope_warning() -> None:
    r = basic(pressure=5).solve()
    assert [w.path for w in r.warnings] == ["v.high_pressure_drop"]
    assert r.warnings[0].severity == "warning"
    assert r.has_warning("v.high_pressure_drop")


def test_junction_of_three_ports() -> None:
    """A tee: one supply feeding two valves in parallel into one drain."""
    s = wp.System("tee")
    s.add("mains", "supply", pressure=3)
    s.add("a", "valve", kv=1.0)
    s.add("b", "valve", kv=2.0)
    s.add("out", "drain")
    s.connect("mains.port", "a.port_a")
    s.connect("mains.port", "b.port_a")  # junction formed by connecting a second port
    s.connect("a.port_b", "out.port")
    s.connect("b.port_b", "out.port")
    r = s.solve()
    assert r["mains.volume_flow"] == pytest.approx(r["a.volume_flow"] + r["b.volume_flow"])
    assert r["mains.volume_flow"] == pytest.approx(kv_flow_lmin(3.0, 3.0), rel=1e-6)
    assert r["a.port_a.p"] == r["b.port_a.p"] == r["mains.port.p"]


def test_series_valves_through_system() -> None:
    s = wp.System()
    s.add("mains", "supply", pressure=3)
    s.add("a", "valve", kv=2.5)
    s.add("b", "valve", kv=4.0)
    s.add("out", "drain")
    s.connect("mains.port", "a.port_a")
    s.connect("a.port_b", "b.port_a")
    s.connect("b.port_b", "out.port")
    r = s.solve()
    kv = 1 / math.sqrt(1 / 2.5**2 + 1 / 4.0**2)
    assert r["a.volume_flow"] == pytest.approx(kv_flow_lmin(kv, 3.0), rel=1e-6)


def test_settle_versus_hold(test_catalog: wp.Catalog) -> None:
    """solve() settles 'settle' states and keeps 'hold' states."""
    s = basic(actuator_time=30, opening=0.2)
    s.set("v.position", 0.9)
    r = s.solve()
    assert r["v.position"] == pytest.approx(0.2)  # settled to the opening
    t = wp.System(catalog=test_catalog)
    t.add("src", "supply", pressure=1)
    t.add("dut", "heated_tank")
    t.connect("src.port", "dut.inlet")
    t.set("dut.level", 1.2)
    assert t.solve()["dut.level"] == pytest.approx(1.2)  # held


# -- simulation -----------------------------------------------------------------------------


def test_simulate_with_event_and_units() -> None:
    s = basic(pressure=1, actuator_time="2 s")
    sim = s.simulate(
        "20 s",
        "0.5 s",
        events=[{"at": "5 s", "set": {"v.opening": 0, "mains.pressure": "350 kPa"}}],
    )
    assert sim.time[0] == 0.0 and sim.time[-1] == pytest.approx(20.0)
    assert len(sim.time) == 41
    flow = sim["v.volume_flow"]
    i5 = sim.time.index(5.0)
    assert flow[i5 - 1] == pytest.approx(kv_flow_lmin(2.5, 1.0), rel=1e-5)
    # the actuator closes with a 2 s time constant after the event
    pos = sim["v.position"]
    # fast states advance over the step ending at t with the command that held during it,
    # then the events due at t apply: the lagged position is continuous at the event
    assert pos[i5 - 1] == pytest.approx(1.0)
    assert pos[i5] == pytest.approx(1.0)
    assert pos[i5 + 1] == pytest.approx(math.exp(-0.25), rel=1e-12)
    assert pos[i5 + 4] == pytest.approx(math.exp(-1.0), rel=1e-12)  # 4 steps = 2 s = tau
    assert sim.final["v.position"] < 1e-3
    assert sim.final["v.volume_flow"] < 0.1
    # the pressure change and the warning
    assert sim["mains.pressure"][i5] == pytest.approx(3.5)
    warn = [w for w in sim.warnings if w.code == "high_pressure_drop"]
    assert len(warn) == 1 and warn[0].time == pytest.approx(5.0)
    # mode changes
    v_modes = [(c.time, c.mode) for c in sim.mode_changes if c.component == "v"]
    assert v_modes[0] == (0.0, "open")
    assert (5.5, "throttling") in v_modes  # the position starts to move after the event
    assert v_modes[-1][1] == "closed"
    # summary and downsampling
    d = sim.to_dict(max_points=5)
    assert len(d["time"]) == 5 and d["time"][0] == 0.0 and d["time"][-1] == pytest.approx(20.0)
    assert d["summary"]["v.volume_flow"]["max"] == pytest.approx(max(flow))
    assert sim.get("v.volume_flow", unit="L/s")[0] == pytest.approx(flow[0] / 60)
    # the system is left at the final state
    assert s.get("v.opening") == 0.0


def test_simulate_selected_variables_and_errors() -> None:
    s = basic()
    sim = s.simulate(3, variables=["v.volume_flow"])
    assert list(sim.series) == ["v.volume_flow"] and len(sim.time) == 4
    with pytest.raises(wp.UnknownVariableError):
        basic().simulate(3, variables=["v.flow"])
    with pytest.raises(wp.UnknownVariableError, match="cannot be set"):
        basic().simulate(3, events=[{"at": 1, "set": {"v.volume_flow": 1}}])
    with pytest.raises(wp.OutOfRangeError):
        basic().simulate(3, events=[{"at": 1, "set": {"v.opening": 2}}])
    with pytest.raises(wp.InvalidValueError):
        basic().simulate(3, events=[{"when": 1}])
    with pytest.raises(wp.InvalidValueError):
        basic().simulate(3, step=0)


# -- documents ------------------------------------------------------------------------------


def test_to_dict_from_dict_round_trip(test_catalog: wp.Catalog) -> None:
    s = wp.System("bath", description="Round trip.", catalog=test_catalog)
    s.add("mains", "supply", pressure="3.5 bar", temperature="12 degC")
    s.add("v", "valve", characteristic="equal_percentage", opening=0.4)
    s.add("t", "heated_tank", heater_curve=[[0, 1], [1, 2], [3, "5000 W"]], heater="off")
    s.add("out", "drain")
    s.connect("mains.port", "v.port_a")
    s.connect("v.port_b", "t.inlet")
    s.connect("t.outlet", "out.port")
    s.simulation = {
        "duration": "5 min",
        "step": "1 s",
        "events": [{"at": "60 s", "set": {"v.opening": 0}}],
    }
    doc = s.to_dict()
    assert _schema_errors("system", doc) == []
    assert doc["components"][0] == {
        "name": "mains",
        "type": "worldparts.hydraulic.supply",
        "parameters": {"pressure": 3.5, "temperature": 12},
    }
    assert doc["components"][2]["parameters"]["heater_curve"] == [[0, 1], [1, 2], [3, 5]]
    s2 = wp.System.from_dict(doc, catalog=test_catalog)
    assert s2.to_dict() == doc
    r1, r2 = s.solve(), s2.solve()
    assert r1.values == r2.values


def test_variables_and_describe() -> None:
    s = basic()
    infos = {v.path: v for v in s.variables()}
    assert infos["v.opening"].kind == "input" and infos["v.opening"].settable
    assert infos["v.volume_flow"].kind == "observable" and not infos["v.volume_flow"].settable
    assert infos["v.port_a.p"].unit == "bar" and infos["v.port_a.p"].pressure_reference == "gauge"
    assert infos["v.pressure_drop"].pressure_reference == "difference"
    assert infos["v.position"].kind == "state"
    d = s.describe("v")
    assert d["parameters"]["kv"] == {"value": 2.5, "unit": "m3/h"}
    assert d["ports"] == ["port_a", "port_b"]


# -- the test-only component: tables, hold states, thermal maps, code warnings ---------------


def tank_system(
    cat: wp.Catalog, pressure: float = 1.0, outlet: bool = True, **params: object
) -> wp.System:
    s = wp.System("tank", catalog=cat)
    s.add("src", "supply", pressure=pressure, temperature=20)
    s.add("dut", "heated_tank", **params)
    s.connect("src.port", "dut.inlet")
    if outlet:
        s.add("v", "valve", opening=0)
        s.add("out", "drain")
        s.connect("dut.outlet", "v.port_a")
        s.connect("v.port_b", "out.port")
    return s


def test_table_parameter_arrives_in_si(test_catalog: wp.Catalog) -> None:
    s = tank_system(test_catalog, heater_curve=[[0, 1], [1, "2000 W"], [2, 4]])
    table = s.component("dut").parameters["heater_curve"]
    assert isinstance(table, Table)
    np.testing.assert_allclose(table["power"], [1000.0, 2000.0, 4000.0])
    assert s.get("dut.heater_curve") == [[0.0, 1.0], [1.0, 2.0], [2.0, 4.0]]
    with pytest.raises(wp.InvalidValueError, match="at least 2 rows"):
        s.set("dut.heater_curve", [[0, 1]])
    with pytest.raises(wp.InvalidValueError, match="each row needs 2"):
        s.set("dut.heater_curve", [[0, 1, 2], [1, 2, 3]])
    with pytest.raises(wp.InvalidValueError, match="not compatible"):
        s.set("dut.heater_curve", [[0, "1 bar"], [1, 2]])
    with pytest.raises(wp.InvalidValueError, match="Valid: off, on"):
        s.set("dut.heater", "auto")


def test_cross_parameter_validation(test_catalog: wp.Catalog) -> None:
    with pytest.raises(wp.InvalidValueError, match="must not exceed height"):
        tank_system(test_catalog, initial_level=3, height=2)
    s = tank_system(test_catalog)
    with pytest.raises(wp.InvalidValueError, match="must not exceed height"):
        s.set("dut.height", 0.2)
    assert s.get("dut.height") == 2.0


def test_thermal_map_heats_the_inflow(test_catalog: wp.Catalog) -> None:
    r = tank_system(test_catalog).solve()
    m = r["dut.inlet.m_flow"]
    power = r["dut.heater_power"] * 1000
    assert power == pytest.approx(3000.0)  # interpolated from the table at level 0.5 m
    assert r["dut.inlet_temperature"] == pytest.approx(20 + power / (m * CP), rel=1e-12)
    # mutable fixed-node pressure: the tank bottom is at rho g level
    assert r["dut.outlet.p"] == pytest.approx(RHO * G * 0.5 / 1e5, abs=1e-6)


def test_code_emitted_warning(test_catalog: wp.Catalog) -> None:
    s = tank_system(test_catalog, pressure=0.052)
    r = s.solve()
    assert r["dut.inlet_temperature"] > 60
    assert [w.path for w in r.warnings] == ["dut.overheat"]
    s.set("dut.heater", "off")
    assert s.solve().warnings == []


def test_undeclared_warning_code_is_a_contract_error(
    test_catalog: wp.Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = tank_system(test_catalog)
    comp = s.component("dut")
    monkeypatch.setattr(
        type(comp),
        "extra_warnings",
        lambda self, sol: [wp.ComponentWarning("dut", "boom", "warning", "x")],
    )
    with pytest.raises(ContractError, match="boom"):
        s.solve()
    with pytest.raises(ContractError, match="undeclared"):
        comp.warning("boom")


def test_hold_state_integration_and_mutable_node_pressure(test_catalog: wp.Catalog) -> None:
    area = 0.2
    s = tank_system(test_catalog, outlet=False, area=area, initial_level=0.1)
    sim = s.simulate("30 s", "1 s")
    level = sim["dut.level"]
    inflow = [q / 3600 for q in sim["dut.net_inflow"]]  # m3/s
    # explicit Euler: level[k+1] = level[k] + net_inflow[k] * dt / area
    for k in range(len(level) - 1):
        assert level[k + 1] == pytest.approx(level[k] + inflow[k] * 1.0 / area, rel=1e-12)
    assert level[-1] > level[0]
    # the tank pressure follows the level between steps
    np.testing.assert_allclose(sim["dut.outlet.p"], [RHO * G * h / 1e5 for h in level], atol=1e-6)
    # well-mixed energy balance with the heated inflow
    temps = sim["dut.temperature"]
    t_in = sim["dut.inlet_temperature"]
    for k in range(len(level) - 1):
        v0, v1 = area * level[k], area * level[k + 1]
        assert temps[k + 1] == pytest.approx((v0 * temps[k] + inflow[k] * t_in[k]) / v1, rel=1e-9)


def test_gate_switches_when_tank_runs_empty(test_catalog: wp.Catalog) -> None:
    s = wp.System(catalog=test_catalog)
    s.add("dut", "heated_tank", area=0.01, initial_level=0.05, heater="off")
    s.add("out", "drain")
    s.connect("dut.outlet", "out.port")
    sim = s.simulate("200 s", "1 s", variables=["dut.level", "dut.net_inflow"])
    assert min(sim["dut.level"]) >= 0.0
    assert sim.final["dut.level"] < 0.002
    assert sim.final.modes["dut"] == "empty"
    assert sim.final["dut.net_inflow"] == pytest.approx(0.0, abs=1e-6)  # outflow blocked
    first_empty = next(w for w in sim.warnings if w.code == "tank_empty")
    assert 0 < first_empty.time < 200
    modes = [c.mode for c in sim.mode_changes if c.component == "dut"]
    assert modes[0] == "draining" and modes[-1] == "empty"


def test_parameter_change_reinitialises_states(test_catalog: wp.Catalog) -> None:
    s = tank_system(test_catalog)
    s.simulate("5 s")
    assert s.get("dut.level") > 0.5
    s.set("dut.initial_level", 0.3)
    assert s.get("dut.level") == pytest.approx(0.3)
    s.simulate("2 s")
    s.reset_states()
    assert s.get("dut.level") == pytest.approx(0.3)


def test_simulation_error_names_the_time(monkeypatch: pytest.MonkeyPatch) -> None:
    s = basic()
    cls = type(s.component("v"))
    original = cls.update_laws

    def broken(self: wp.components.Component) -> None:
        original(self)
        if self.inputs["opening"] < 0.5:
            self.law.k = float("nan")

    monkeypatch.setattr(cls, "update_laws", broken)
    with pytest.raises(wp.SolverError, match="At t = 2 s") as exc:
        s.simulate(5, events=[{"at": 2, "set": {"v.opening": 0.1}}])
    assert exc.value.worst


# -- review regressions: time grid, events, documents, batches -------------------------------


def test_simulate_ends_exactly_at_duration() -> None:
    sim = basic().simulate(1, step=0.3, variables=["v.volume_flow"])
    assert sim.time == pytest.approx([0.0, 0.3, 0.6, 0.9, 1.0])
    assert sim.final.time == pytest.approx(1.0)
    assert basic().simulate(0.5, step=2).time == [0.0, 0.5]


def test_simulate_splits_the_step_at_an_off_grid_event() -> None:
    s = basic(opening=0.2)
    sim = s.simulate(10, step=1, events=[{"at": 5.5, "set": {"v.opening": 0.8}}])
    assert 5.5 in sim.time and len(sim.time) == 12
    i = sim.time.index(5.5)
    assert sim["v.position"][i - 1] == pytest.approx(0.2)
    assert sim["v.position"][i] == pytest.approx(0.8)


def test_simulate_rejects_events_after_the_end() -> None:
    with pytest.raises(wp.InvalidValueError, match="after the end"):
        basic().simulate(1, events=[{"at": 5, "set": {"v.opening": 0}}])
    sim = basic().simulate(2, events=[{"at": 2, "set": {"v.opening": 0}}])  # at the end: ok
    assert sim["v.opening"][-1] == 0.0


def test_simulate_uses_the_document_simulation_block() -> None:
    s = basic()
    with pytest.raises(wp.InvalidValueError, match="duration"):
        s.simulate()
    s.simulation = {
        "duration": "4 s",
        "step": "2 s",
        "events": [{"at": 2, "set": {"v.opening": 0}}],
    }
    sim = s.simulate()
    assert sim.time == [0.0, 2.0, 4.0]
    assert sim["v.opening"] == [1.0, 0.0, 0.0]


def test_start_simulation_hook_runs_once_per_simulation() -> None:
    s = basic()
    calls: list[str] = []
    comp = s.component("v")
    comp.start_simulation = lambda: calls.append("start")  # type: ignore[method-assign]
    s.simulate(3)
    s.simulate(2)
    assert calls == ["start", "start"]


def test_document_carries_state_values(test_catalog: wp.Catalog) -> None:
    s = wp.System("t", catalog=test_catalog)
    s.add("src", "supply", pressure=1)
    s.add("tk", "heated_tank")
    s.add("v", "valve", actuator_time="10 s", opening=0)
    s.add("out", "drain")
    s.connect("src.port", "tk.inlet")
    s.connect("tk.outlet", "v.port_a")
    s.connect("v.port_b", "out.port")
    assert "states" not in s.to_dict()["components"][1]  # nothing to record yet
    s.simulate(5, events=[{"at": 0, "set": {"v.opening": 1}}])
    doc = s.to_dict()
    assert _schema_errors("system", doc) == []
    tk_states = doc["components"][1]["states"]
    assert tk_states["level"] == pytest.approx(s.get("tk.level"))
    assert doc["components"][2]["states"]["position"] == pytest.approx(1 - math.exp(-0.5))
    s2 = wp.System.from_dict(doc, catalog=test_catalog)
    assert s2.get("tk.level") == pytest.approx(s.get("tk.level"))
    assert s2.get("tk.temperature") == pytest.approx(s.get("tk.temperature"))
    assert s2.get("v.position") == pytest.approx(s.get("v.position"))
    assert s2.to_dict() == doc
    bad = copy.deepcopy(doc)
    bad["components"][1]["states"] = {"levle": 1}
    issues = wp.System.from_dict(bad, catalog=test_catalog).check()
    assert any(i.code == "invalid_value" and "levle" in i.message for i in issues)


def test_set_values_applies_parameters_before_inputs_and_states() -> None:
    s = basic(actuator_time="10 s")
    s.set_values({"v.kv": 3.0, "v.opening": 0.25})  # parameters re-initialise states first
    assert s.get("v.position") == pytest.approx(0.25)
    s.set_values({"v.position": 0.5, "v.kv": 2.0})
    assert s.get("v.position") == pytest.approx(0.5)


def test_variables_mark_unreported_parameters_and_results_carry_references() -> None:
    s = basic()
    info = {v.path: v for v in s.variables()}
    assert info["v.characteristic"].reported is False
    assert info["v.kv"].reported is True
    d = s.solve().to_dict()["values"]
    assert d["mains.port.p"]["reference"] == "gauge"
    assert d["v.pressure_drop"]["reference"] == "difference"
    assert "reference" not in d["v.volume_flow"]
    r = s.solve()
    assert r.get("mains.port.p", unit="bar absolute") == pytest.approx(3 + 1.01325)


# -- state re-initialisation, check_states and time_step (core changes for the tank) ---------


def test_parameter_change_keeps_states_whose_initial_value_is_unchanged() -> None:
    """A parameter batch re-initialises only the states whose initial value it changes.

    Valve with a 10 s actuator closed at t = 0: after 5 s the position is exp(-0.5) =
    0.60653. Changing kv does not change the initial position (the opening, 0), so the
    actuator keeps its position; a batch that also changes the opening resets it.
    """
    s = basic(actuator_time="10 s")
    s.simulate(5, events=[{"at": 0, "set": {"v.opening": 0}}])
    lagged = math.exp(-0.5)
    assert s.get("v.position") == pytest.approx(lagged)
    s.set("v.kv", 3.0)
    assert s.get("v.position") == pytest.approx(lagged)
    s.set_values({"v.kv": 2.0, "v.opening": 0.25})
    assert s.get("v.position") == pytest.approx(0.25)
    s.set("v.position", 0.5)
    s.reset_states()
    assert s.get("v.position") == pytest.approx(0.25)


def test_check_states_rejects_a_batch_and_is_reported_by_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Component.check_states`` sees the trial parameters and states of a batch; a failed
    check leaves the system unchanged, and ``check()`` reports the current states."""
    s = basic()
    cls = type(s.component("v"))

    def check_states(parameters: dict, states: dict) -> list[str]:
        if states["position"] > parameters["kv"] * 3600.0 / 5.0:  # position <= kv (m3/h) / 5
            return ["position too large for this kv"]
        return []

    monkeypatch.setattr(cls, "check_states", classmethod(lambda c, p, st: check_states(p, st)))
    s.set_values({"v.kv": 10.0, "v.position": 1.0})  # 1.0 <= 10 / 5: accepted in one batch
    with pytest.raises(wp.InvalidValueError, match="position too large"):
        s.set("v.kv", 2.5)  # the kept position 1.0 > 0.5
    assert s.get("v.kv") == 10.0
    assert s.get("v.position") == 1.0
    monkeypatch.setattr(cls, "check_states", classmethod(lambda c, p, st: ["always"]))
    assert any(i.code == "invalid_value" and i.message == "always" for i in s.check())


def test_time_step_is_the_step_that_follows_each_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Component.time_step`` is the step over which the solution will be integrated (the
    step that led to the last sample at the end), and None in a steady solve."""
    s = basic()
    cls = type(s.component("v"))
    seen: list[float | None] = []
    original = cls.update_laws

    def update_laws(self: wp.components.Component) -> None:
        seen.append(self.time_step)
        original(self)

    monkeypatch.setattr(cls, "update_laws", update_laws)
    s.simulate(2.5, step=1, events=[{"at": 1.5, "set": {"v.opening": 0.5}}])
    assert seen[-5:] == pytest.approx([1.0, 0.5, 0.5, 0.5, 0.5])
    assert s.component("v").time_step is None
    seen.clear()
    s.solve()
    assert seen and all(t is None for t in seen)


def _heater_line() -> wp.System:
    """3 bar, 12 degC mains -> heater -> Kv 0.2 valve -> drain (heater holds 55 degC)."""
    s = wp.System("heater-line")
    s.add("mains", "supply", pressure=3, temperature=12)
    s.add("h", "instantaneous_water_heater")
    s.add("v", "valve", kv=0.2)
    s.add("out", "drain")
    s.connect("mains.port", "h.inlet")
    s.connect("h.outlet", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


def test_temperature_difference_results_convert_as_differences() -> None:
    """A rise of 43 K is 43 degC and 77.4 degF; absolute temperatures keep their offset."""
    s = _heater_line()
    r = s.solve()
    assert r["h.temperature_rise"] == pytest.approx(43.0, abs=1e-6)
    assert r.references["h.temperature_rise"] == "difference"
    assert r.get("h.temperature_rise", unit="degC") == pytest.approx(43.0, abs=1e-6)
    assert r.get("h.temperature_rise", unit="degF") == pytest.approx(77.4, abs=1e-6)
    assert r.get("h.outlet_temperature", unit="K") == pytest.approx(328.15, abs=1e-6)
    assert "h.outlet_temperature" not in r.references
    entry = r.to_dict(["h.temperature_rise"])["values"]["h.temperature_rise"]
    assert entry == {"value": pytest.approx(43.0, abs=1e-6), "unit": "K", "reference": "difference"}
    sim = s.simulate(duration=2, variables=["h.temperature_rise"])
    assert sim.references == {"h.temperature_rise": "difference"}
    assert sim.get("h.temperature_rise", unit="degC") == pytest.approx([43.0] * 3, abs=1e-6)
    info = {v.path: v for v in s.variables()}["h.temperature_rise"]
    assert info.quantity == "temperature_difference" and info.pressure_reference == "difference"
    assert {v.path: v for v in s.variables()}["h.setpoint"].quantity is None
