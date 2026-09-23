"""WNTR adapter: mapping to a WNTR model, .inp export and cross-validation against EPANET.

The four cross-validation cases of design section 11 are solved by the reference runtime
and by EPANET 2.2 (through WNTR's EpanetSimulator). Each states the tolerance it asserts
and why; the measured values are recorded in docs/wntr-adapter.md. The whole module is
skipped when the optional ``wntr`` package is not installed.
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pytest
from scipy.optimize import brentq

import worldparts as wp

wntr = pytest.importorskip("wntr")

from worldparts.adapters.wntr_adapter import (  # noqa: E402
    EPANET_G_MINOR,
    KNOWN_DIVERGENCE_SOURCES,
    MAX_ID,
    SUPPORTED_COMPONENTS,
    ComparisonReport,
    UnsupportedComponentError,
    WntrExportError,
    compare_with_wntr,
    export_inp,
    to_wntr,
    translate,
)
from worldparts.media import RHO, G  # noqa: E402


# ----------------------------------------------------------------------------------------
# systems
# ----------------------------------------------------------------------------------------
def line_system() -> wp.System:
    """Case 1: supply -> pipe -> valve -> drain."""
    s = wp.System("line")
    s.add("mains", "supply", pressure="3 bar")
    s.add("p", "pipe", length=20, diameter="25 mm", roughness="0.05 mm")
    s.add("v", "valve", kv=4.0, opening=0.6)
    s.add("out", "drain")
    s.connect("mains.port", "p.port_a")
    s.connect("p.port_b", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


def lift_system(speed: float = 1.0, head_curve: list[list[float]] | None = None) -> wp.System:
    """Case 2: tank -> pump -> riser (12 m up) -> elevated open reservoir (a drain)."""
    s = wp.System("lift")
    s.add("tank", "tank", initial_level=1.5, height=3, diameter=2)
    extra = {"head_curve": head_curve} if head_curve else {}
    s.add("pump", "centrifugal_pump", speed=speed, **extra)
    s.add("riser", "pipe", length=40, diameter="80 mm", roughness="0.05 mm",
          height_difference=12, minor_loss=1)  # fmt: skip
    s.add("top", "drain")
    s.connect("tank.outlet", "pump.inlet")
    s.connect("pump.outlet", "riser.port_a")
    s.connect("riser.port_b", "top.port")
    return s


def loop_system(p4_rise: float = 0.5) -> wp.System:
    """Case 3: two supplies feeding a looped network (with consistent elevations) and a
    valve to a drain."""
    s = wp.System("loop")
    s.add("a", "supply", pressure="3 bar")
    s.add("b", "supply", pressure="2.5 bar")
    rises = {"p1": 0.0, "p2": 0.0, "p3": 1.0, "p4": p4_rise, "p5": -0.5}
    for name, length in (("p1", 30), ("p2", 50), ("p3", 40), ("p4", 25), ("p5", 35)):
        s.add(name, "pipe", length=length, diameter="32 mm", roughness="0.02 mm",
              height_difference=rises[name])  # fmt: skip
    s.add("v", "valve", kv=6, opening=0.8)
    s.add("out", "drain")
    s.connect("a.port", "p1.port_a")
    s.connect("b.port", "p2.port_a")
    s.connect("p1.port_b", "p3.port_a")  # node N1: p1, p3, p4
    s.connect("p1.port_b", "p4.port_a")
    s.connect("p2.port_b", "p3.port_b")  # node N2: p2, p3, p5
    s.connect("p2.port_b", "p5.port_a")
    s.connect("p4.port_b", "p5.port_b")  # node N3: p4, p5, valve
    s.connect("p4.port_b", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


def train_system(pressure: float = 1.5) -> wp.System:
    """Case 4: raw water -> pipe -> media filter -> UV reactor -> pipe -> drain."""
    s = wp.System("train")
    s.add("raw", "supply", pressure=pressure)
    s.add("p1", "pipe", length=10, diameter="63 mm", roughness="0.01 mm")
    s.add("f", "media_filter")
    s.add("uv", "uv_reactor")
    s.add("p2", "pipe", length=10, diameter="63 mm", roughness="0.01 mm")
    s.add("out", "drain")
    s.connect("raw.port", "p1.port_a")
    s.connect("p1.port_b", "f.inlet")
    s.connect("f.outlet", "uv.inlet")
    s.connect("uv.outlet", "p2.port_a")
    s.connect("p2.port_b", "out.port")
    return s


def train_at_rated_flow() -> wp.System:
    """The treatment train with the supply pressure set so both units run at 20 m3/h."""
    s = train_system()

    def excess(p: float) -> float:
        s.set("raw.pressure", p)
        return float(s.solve()["f.volume_flow"]) - 20.0

    s.set("raw.pressure", brentq(excess, 0.05, 1.5, xtol=1e-12))
    return s


def assert_consistent(report: ComparisonReport, system: wp.System) -> None:
    """Every supported flow and connected node is compared; values carry the right units."""
    r = system.solve()
    for c in report.links:
        if c.path.endswith(".m_flow"):
            expected = float(r[c.path]) / RHO * 3600.0
        else:
            expected = float(r.get(c.path, unit="m3/h"))
        assert c.worldparts == pytest.approx(expected, rel=1e-12, abs=1e-12)
        assert c.abs_diff == pytest.approx(c.wntr_value - c.worldparts)
    for n in report.nodes:
        assert n.worldparts == pytest.approx(float(r[n.node.split(" = ")[0] + ".p"]), abs=1e-12)


# ----------------------------------------------------------------------------------------
# the four cross-validation cases
# ----------------------------------------------------------------------------------------
def test_case1_line_supply_pipe_valve_drain() -> None:
    """Measured: flow 0.006 %, pressure 3.4e-4 bar.

    Tolerance 0.05 % flow and 2 mbar: the TCV reproduces the valve's Kv exactly (see
    test_equivalent_elements_are_exact), so what remains is the pipe: Churchill (worldparts)
    against Swamee-Jain (EPANET) at Re = 5e4, and EPANET's g = 32.2 ft/s2 in the friction
    term (0.08 % of the pipe head).
    """
    s = line_system()
    report = compare_with_wntr(s)
    assert_consistent(report, s)
    paths = {c.path for c in report.links}
    assert paths == {"mains.volume_flow", "p.volume_flow", "v.volume_flow", "out.volume_flow"}
    assert report.max_flow_rel_diff < 5e-4
    assert report.max_pressure_abs_diff < 2e-3
    assert report.node("mains.port").abs_diff == pytest.approx(0.0, abs=1e-6)
    assert report.node("out.port").wntr_value == pytest.approx(0.0, abs=1e-6)


def test_case2_pump_lift() -> None:
    """Measured: flow 0.0068 %, pressure 1.7e-4 bar (0.012 %).

    Tolerance 0.05 % flow and 2 mbar: the default pump curve has a fitted linear term of 0,
    so EPANET's three-point power curve is the quadratic exactly (deviation 1e-14 m); the
    static lift (12 m) is exact through the node elevations; the tank level is exact; the
    remainder is the riser's friction factor.
    """
    s = lift_system()
    tr = translate(s)
    wn = tr.model
    # Elevations: the drain on top is the datum; the pump and the tank bottom sit 12 m lower.
    top = tr.boundaries["top"]
    assert wn.get_node(top).base_head == pytest.approx(0.0)
    inlet = tr.node_of_port("pump.inlet")
    outlet = tr.node_of_port("pump.outlet")
    assert inlet is not None and outlet is not None
    assert inlet.elevation == pytest.approx(-12.0) and outlet.elevation == pytest.approx(-12.0)
    tank = wn.get_node("tank")
    assert tank.elevation == pytest.approx(-12.0)
    assert tank.init_level == pytest.approx(1.5) and tank.max_level == pytest.approx(3.0)
    assert tank.diameter == pytest.approx(2.0)
    assert tr.pumps["pump"].form == "three_point"
    assert tr.pumps["pump"].max_deviation < 1e-9

    report = compare_with_wntr(s)
    assert_consistent(report, s)
    assert {c.path for c in report.links} >= {"pump.volume_flow", "tank.outlet.m_flow"}
    assert report.link("pump").worldparts == pytest.approx(38.53, abs=0.01)
    assert report.max_flow_rel_diff < 5e-4
    assert report.max_pressure_abs_diff < 2e-3


@pytest.mark.parametrize("speed", [1.0, 0.8])
def test_case2_pump_speed_and_multi_point_curve(speed: float) -> None:
    """A head curve with a falling start (fitted b < 0) is exported as a multi-point curve.

    Measured: flow 0.0051 % (speed 1) and 0.0065 % (speed 0.8), pressure 1.8e-4 bar. The
    multi-point curve is at most 4 mm
    of head below the quadratic (a three-point power curve would be 1.6 m off), and EPANET
    scales it with the speed by the same affinity law, so the same tolerance holds.
    """
    curve = [[0, 40], [10, 36], [20, 30], [30, 22], [36, 16]]
    s = lift_system(speed, curve)
    tr = translate(s)
    pump = tr.pumps["pump"]
    # 41 points from zero flow to the run-out flow (head 0), then the same spacing with
    # negative heads to three times the run-out flow (121 points in all).
    assert pump.form == "multi_point" and len(pump.points) == 121
    assert pump.points[40] == (pytest.approx(pump.runout_flow), 0.0)
    assert pump.points[-1][0] == pytest.approx(3.0 * pump.runout_flow)
    heads = [h for _, h in pump.points]
    assert all(h1 < h0 for h0, h1 in itertools.pairwise(heads))
    assert pump.coefficients[1] < 0.0
    assert pump.multi_point_deviation < 0.01
    assert pump.three_point_deviation is not None and pump.three_point_deviation > 1.0
    assert tr.model.get_link("pump").base_speed == pytest.approx(speed)
    report = compare_with_wntr(s)
    assert report.max_flow_rel_diff < 5e-4
    assert report.max_pressure_abs_diff < 2e-3


def test_case3_looped_network_with_two_supplies() -> None:
    """Measured: flow 0.09 % on the smallest loop flow (p3, 1.5 m3/h), 0.002 m3/h at most,
    pressure 3.9e-4 bar.

    Tolerance 0.5 % relative (0.01 m3/h absolute) and 2 mbar: in a loop the per-pipe
    friction-factor differences redistribute flow, and a small branch flow is the
    difference of larger ones, so its relative error is amplified several times.
    """
    s = loop_system()
    tr = translate(s)
    n2 = tr.node_of_port("p3.port_b")
    n3 = tr.node_of_port("v.port_a")
    assert n2 is not None and n3 is not None
    assert n2.elevation - n3.elevation == pytest.approx(0.5)  # p5 falls 0.5 m
    report = compare_with_wntr(s)
    assert_consistent(report, s)
    assert len(report.links) == 9 and len(report.nodes) == 6
    assert report.max_flow_rel_diff < 5e-3
    assert report.max_flow_abs_diff < 0.01
    assert report.max_pressure_abs_diff < 2e-3
    # Mass balance in EPANET too: both supplies feed the drain.
    flows = {c.path: c.wntr_value for c in report.links}
    assert flows["a.volume_flow"] + flows["b.volume_flow"] == pytest.approx(
        flows["out.volume_flow"], rel=1e-5
    )


def test_case3_inconsistent_loop_elevations_are_rejected() -> None:
    with pytest.raises(WntrExportError, match="do not add up to zero"):
        to_wntr(loop_system(p4_rise=2.0))


def test_case4_treatment_train_at_rated_point() -> None:
    """Measured: flow 0.015 %, pressure 4.6e-5 bar, with the filter matched at its rated
    flow ('rated') or at the operating point ('operating'), which coincide here.

    Tolerance 0.05 % flow and 1 mbar: the UV reactor's law is quadratic, so its TCV is
    exact; the filter's TCV matches its pressure drop at 20 m3/h exactly; the two short
    pipes carry the only formula difference.
    """
    s = train_at_rated_flow()
    for reference in ("rated", "operating"):
        report = compare_with_wntr(s, reference=reference)
        assert_consistent(report, s)
        assert report.link("f").worldparts == pytest.approx(20.0, rel=1e-9)
        assert report.max_flow_rel_diff < 5e-4
        assert report.max_pressure_abs_diff < 1e-3
        assert any("media_filter" in a for a in report.approximations)


def test_case4_filter_linearisation_away_from_the_reference_flow() -> None:
    """The filter's linear media term (70 % of the clean drop) is not representable.

    At 1.5 bar the train runs at 44 m3/h (2.2 times rated). Matched at the rated flow, the
    EPANET filter overestimates the drop and the flow is about 11 % low; matched at the
    operating point the difference falls back to the pipes' 0.01 %.
    """
    s = train_system(1.5)
    rated = compare_with_wntr(s, reference="rated")
    operating = compare_with_wntr(s, reference="operating")
    assert 0.05 < rated.link("f").rel_diff < 0.2  # type: ignore[operator]
    assert rated.link("f").abs_diff < 0.0  # EPANET's filter resists more above the reference
    assert operating.max_flow_rel_diff < 5e-4
    assert operating.max_pressure_abs_diff < 1e-3


# ----------------------------------------------------------------------------------------
# element mapping
# ----------------------------------------------------------------------------------------
def test_equivalent_elements_are_exact() -> None:
    """Without pipes, a valve, check valve, UV reactor, filter (operating point) and tank
    port reproduce worldparts to EPANET's single-precision output (about 1e-6)."""
    s = wp.System("elements")
    s.add("tank", "tank", initial_level=2.0, port_kv=30)
    s.add("cv", "check_valve", kv=12)
    s.add("v", "valve", kv=10, opening=0.7, characteristic="equal_percentage")
    s.add("f", "media_filter", rated_flow=10, clogging=0.4)
    s.add("uv", "uv_reactor", rated_flow=10)
    s.add("mains", "supply", pressure="2 bar")
    s.add("out", "drain")
    s.connect("mains.port", "tank.inlet")
    s.connect("tank.outlet", "cv.port_a")
    s.connect("cv.port_b", "v.port_a")
    s.connect("v.port_b", "f.inlet")
    s.connect("f.outlet", "uv.inlet")
    s.connect("uv.outlet", "out.port")
    report = compare_with_wntr(s)
    assert_consistent(report, s)
    assert report.max_flow_rel_diff < 1e-5
    assert report.max_pressure_abs_diff < 1e-5


def test_tcv_setting_reproduces_the_kv() -> None:
    """K = 2 g_E A**2 r / (rho G) with r = 100 rho / Kv**2 (Kv in m3/s)."""
    s = line_system()
    tr = translate(s)
    link = tr.links["v"]
    d = 0.025  # the adjacent pipe's diameter
    assert link.kind == "tcv" and link.diameter == pytest.approx(d)
    kv = 4.0 * 0.6 / 3600.0  # linear characteristic; the leakage adds 0.0001 * 0.4
    kv += 4.0 * 1e-4 * 0.4 / 3600.0
    area = math.pi * d * d / 4.0
    expected = 2.0 * EPANET_G_MINOR * area**2 * (1e5 * RHO / (1000.0 * kv * kv)) / (RHO * G)
    assert link.setting == pytest.approx(expected, rel=1e-12)
    assert tr.model.get_link("v").initial_setting == pytest.approx(expected, rel=1e-12)


def test_model_options_and_pipe_units() -> None:
    wn = to_wntr(line_system())
    assert wn.options.hydraulic.headloss == "D-W"
    assert wn.options.hydraulic.inpfile_units == "CMH"
    assert wn.options.hydraulic.viscosity == pytest.approx(1.002e-3 / 998.2 / 1.02193e-6, 1e-4)
    pipe = wn.get_link("p")
    assert pipe.length == pytest.approx(20.0)
    assert pipe.diameter == pytest.approx(0.025)
    assert pipe.roughness == pytest.approx(5e-5)  # SI metres inside WNTR
    mains = wn.get_node("mains")
    assert mains.base_head == pytest.approx(3e5 / (RHO * G))
    assert sorted(wn.junction_name_list) == ["J1", "J2", "J3"]
    joint = wn.get_link("mains")  # the supply's lossless joint to its port junction
    assert joint.valve_type == "TCV" and joint.initial_setting == 0.0
    assert (joint.start_node_name, joint.end_node_name) == ("mains", "J1")


def test_check_valve_blocks_reverse_flow() -> None:
    """Forward: agrees like case 1. Reverse: EPANET's CV passes nothing, worldparts only its
    1e-6 leakage."""
    s = wp.System("cv")
    s.add("low", "supply", pressure="0.5 bar")
    s.add("p", "pipe", length=5, diameter="25 mm")
    s.add("cv", "check_valve")
    s.add("high", "supply", pressure="2 bar")
    s.connect("high.port", "p.port_a")
    s.connect("p.port_b", "cv.port_a")
    s.connect("cv.port_b", "low.port")
    tr = translate(s)
    assert tr.links["cv"].kind == "cv_pipe" and tr.model.get_link("cv").check_valve
    forward = compare_with_wntr(s)
    assert forward.max_flow_rel_diff < 5e-4
    s.set("high.pressure", 0.2)
    reverse = compare_with_wntr(s)
    assert abs(reverse.link("cv").worldparts) < 1e-3
    assert abs(reverse.link("cv").wntr_value) < 1e-3


def test_stopped_pump_is_closed_and_noted() -> None:
    s = lift_system(speed=0.0)
    tr = translate(s)
    assert str(tr.model.get_link("pump").initial_status) == "Closed"
    assert any("stopped" in a for a in tr.approximations)


@pytest.mark.parametrize("component", ["mixing_faucet", "instantaneous_water_heater"])
def test_unsupported_components_raise(component: str) -> None:
    s = wp.System("building")
    s.add("mains", "supply")
    s.add("x", component)
    s.connect("mains.port", f"x.{next(iter(s.manifest('x').ports))}")
    with pytest.raises(UnsupportedComponentError) as info:
        to_wntr(s)
    assert info.value.code == "unsupported_component"
    assert info.value.components == {"x": f"worldparts.hydraulic.{component}"}
    text = str(info.value)
    for alias in ("supply", "pipe", "valve", "centrifugal_pump", "tank", "uv_reactor"):
        assert alias in text
    assert len(SUPPORTED_COMPONENTS) == 9


def test_system_errors_and_unknown_simulator() -> None:
    s = wp.System("broken")
    s.add("p", "pipe")
    with pytest.raises(wp.SystemCheckError):
        to_wntr(s)
    doc = line_system().to_dict()
    doc["components"][1]["type"] = "flux_capacitor"  # unknown: a check error, not unsupported
    with pytest.raises(wp.SystemCheckError, match="unknown_component"):
        to_wntr(wp.System.from_dict(doc))
    with pytest.raises(WntrExportError, match="Darcy-Weisbach"):
        compare_with_wntr(line_system(), simulator="wntr")


def test_long_names_become_valid_epanet_ids() -> None:
    s = wp.System("names")
    long = "a_very_long_instance_name_for_the_main_supply_line"
    s.add("mains", "supply")
    s.add(long, "pipe")
    s.add(long + "_2", "pipe")
    s.add("out", "drain")
    s.connect("mains.port", f"{long}.port_a")
    s.connect(f"{long}.port_b", f"{long}_2.port_a")
    s.connect(f"{long}_2.port_b", "out.port")
    tr = translate(s)
    names = [n for n, link in tr.links.items() if link.kind == "pipe"]
    assert len(set(names)) == 2 and all(len(n) <= MAX_ID for n in names)
    assert {tr.links[n].component for n in names} == {long, long + "_2"}
    # Default pipes are hydraulically smooth (0.0015 mm): Churchill and Swamee-Jain differ
    # a little more there (measured 0.07 %).
    assert compare_with_wntr(s).max_flow_rel_diff < 2e-3


# ----------------------------------------------------------------------------------------
# export and report
# ----------------------------------------------------------------------------------------
@pytest.mark.filterwarnings("ignore:Changing the headloss formula")
def test_export_inp_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "lift.inp"
    text = export_inp(lift_system(), target)
    assert target.read_text(encoding="utf-8") == text
    for section in ("[JUNCTIONS]", "[RESERVOIRS]", "[TANKS]", "[PIPES]", "[PUMPS]",
                    "[VALVES]", "[CURVES]"):  # fmt: skip
        assert section in text
    assert "D-W" in text and "CMH" in text and "worldparts system 'lift'" in text
    assert "Created:" not in text  # deterministic output
    wn = wntr.network.WaterNetworkModel(str(target))
    assert wn.get_link("riser").roughness == pytest.approx(5e-5)
    assert wn.get_link("riser").diameter == pytest.approx(0.08)
    assert wn.get_node("tank").elevation == pytest.approx(-12.0)
    assert wn.get_node("tank").overflow  # a full tank keeps receiving water, as in worldparts
    assert sorted(wn.link_name_list) == sorted(to_wntr(lift_system()).link_name_list)


def booster_system(pressure: str) -> wp.System:
    """Case 2c: a pump with a falling curve on a pressurised main, 10 m DN80 to a drain."""
    s = wp.System("booster")
    s.add("m", "supply", pressure=pressure)
    s.add("pu", "centrifugal_pump", head_curve=[[0, 40], [10, 36], [20, 30], [30, 22], [36, 16]])
    s.add("p", "pipe", length=10, diameter="80 mm", roughness="0.05 mm")
    s.add("o", "drain")
    s.connect("m.port", "pu.inlet")
    s.connect("pu.outlet", "p.port_a")
    s.connect("p.port_b", "o.port")
    return s


@pytest.mark.parametrize("pressure", ["0.5 bar", "6 bar"])
def test_case2c_booster_past_the_run_out_flow(pressure: str) -> None:
    """Measured 0.0022 % (0.5 bar, 52.6 m3/h) and 0.0015 % (6 bar, 84.4 m3/h), 1.5e-4 bar:
    the multi-point curve continues past the run-out flow (about 49.6 m3/h) with negative
    heads, so EPANET follows the quadratic there too."""
    s = booster_system(pressure)
    report = compare_with_wntr(s)
    assert report.link("pu").worldparts > 50.0
    assert report.max_flow_rel_diff < 5e-4
    assert report.max_pressure_abs_diff < 2e-3
    assert not any("beyond the end" in a for a in report.approximations)


def test_operating_point_beyond_the_exported_curve_is_noted() -> None:
    """At 40 bar the booster runs at 191 m3/h, past the end of the curve (3 times the
    run-out flow): EPANET extrapolates linearly there, and the export says so."""
    tr = translate(booster_system("40 bar"))
    assert any("beyond the end of the exported curve" in a for a in tr.approximations)


def test_report_to_dict_and_summary() -> None:
    report = compare_with_wntr(line_system())
    data = report.to_dict()
    json.dumps(data)
    assert data["flow_unit"] == "m3/h" and data["pressure_unit"] == "bar"
    assert data["pressure_reference"] == "gauge"
    assert data["max_flow_rel_diff"] == report.max_flow_rel_diff
    assert {c["path"] for c in data["links"]} == {c.path for c in report.links}
    assert data["divergence_sources"] == list(KNOWN_DIVERGENCE_SOURCES)
    text = report.summary()
    for piece in ("worldparts vs WNTR (epanet)", "p.volume_flow", "p.port_b = v.port_a",
                  "Known divergence sources", "Swamee-Jain"):  # fmt: skip
        assert piece in text
    with pytest.raises(KeyError):
        report.link("nope")


@pytest.mark.parametrize(
    ("pressure", "diameter", "roughness", "low", "high"),
    [
        # Laminar (Re about 155): both use 64/Re; measured 0.08 % (EPANET's g = 32.2 ft/s2).
        ("0.001 bar", "10 mm", "0.0015 mm", 0.0, 3e-3),
        # Transitional (Re about 3100): EPANET interpolates f with a cubic between Re 2000
        # and 4000, Churchill blends smoothly; measured 8.3 %, the largest divergence.
        ("0.01 bar", "16 mm", "0.0015 mm", 0.02, 0.2),
        # Fully rough turbulent (Re about 7e5, e/D = 0.01): measured 0.013 %.
        ("2 bar", "100 mm", "1 mm", 0.0, 1e-3),
    ],
)
def test_friction_regimes(
    pressure: str, diameter: str, roughness: str, low: float, high: float
) -> None:
    """A supply -> pipe -> drain line has no junction besides the port nodes."""
    s = wp.System("pipe_only")
    s.add("m", "supply", pressure=pressure)
    s.add("p", "pipe", length=20, diameter=diameter, roughness=roughness)
    s.add("o", "drain")
    s.connect("m.port", "p.port_a")
    s.connect("p.port_b", "o.port")
    rel = compare_with_wntr(s).link("p").rel_diff
    assert rel is not None and low <= rel < high
