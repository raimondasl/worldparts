"""Physics tests of the open tank (design 8.9) against hand calculations."""

from __future__ import annotations

import math

import numpy as np
import pytest

import worldparts as wp
from worldparts.errors import InvalidValueError
from worldparts.laws import kv_to_k
from worldparts.media import RHO, G

H_PER_S = 3600.0  # m3/h per m3/s


def _drain_system(diameter: float, level: float, kv: float = 2.5) -> wp.System:
    """Tank outlet -> valve (Kv) -> drain; tank inlet capped."""
    s = wp.System("drain-down")
    s.add("dut", "tank", diameter=diameter, height=max(3.0, level), initial_level=level)
    s.add("v", "valve", kv=kv)
    s.add("sink", "drain")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def test_drain_down_matches_analytic_solution() -> None:
    # Quasi-steady draining through the tank port (Kv 200) and a valve (Kv 2.5) in series:
    #   m = k sqrt(rho g h), 1/k**2 = 1/k_valve**2 + 1/k_port**2, k = kv_to_k(Kv_SI, rho)
    #   A dh/dt = -m / rho  =>  d sqrt(h)/dt = -k sqrt(rho g) / (2 rho A)
    #   sqrt(h) = sqrt(h0) - k sqrt(rho g) / (2 rho A) * t
    # D = 0.5 m (A = 0.19635 m2), h0 = 2 m: k = 0.0021941 kg/(s Pa^0.5), so the slope is
    # 0.0021941 * 98.94 / (2 * 998.2 * 0.19635) = 5.538e-4 sqrt(m)/s; h = 0.5 m after 1277 s.
    diameter, h0 = 0.5, 2.0
    area = math.pi * diameter**2 / 4
    k_valve = kv_to_k(2.5 / H_PER_S, RHO)
    k_port = kv_to_k(200.0 / H_PER_S, RHO)
    k = 1.0 / math.sqrt(1.0 / k_valve**2 + 1.0 / k_port**2)
    slope = k * math.sqrt(RHO * G) / (2.0 * RHO * area)
    assert slope == pytest.approx(5.538e-4, rel=1e-3)
    s = _drain_system(diameter, h0)
    sim = s.simulate(duration="1500 s", step="1 s", variables=["dut.level"])
    t = np.array(sim.time)
    level = np.array(sim["dut.level"], dtype=float)
    analytic = (math.sqrt(h0) - slope * t) ** 2
    mask = analytic > 0.2
    assert mask.sum() > 1000
    rel = np.abs(level[mask] - analytic[mask]) / analytic[mask]
    assert rel.max() < 0.01
    # Explicit Euler with a 1 s step is in fact much closer than 1 %.
    assert rel.max() < 1e-3


def test_volume_balance_over_simulation() -> None:
    # Fill and drain at once: supply (0.4 bar) -> valve -> inlet; outlet -> valve -> drain.
    # Explicit Euler: V_N - V_0 = sum_{k<N} net_inflow_k * dt exactly (no spill, no clamp).
    s = wp.System("balance")
    s.add("src", "supply", pressure=0.4)
    s.add("vin", "valve", kv=2.5)
    s.add("dut", "tank", diameter=0.6, initial_level=1.0)
    s.add("vout", "valve", kv=1.5)
    s.add("sink", "drain")
    s.connect("src.port", "vin.port_a")
    s.connect("vin.port_b", "dut.inlet")
    s.connect("dut.outlet", "vout.port_a")
    s.connect("vout.port_b", "sink.port")
    dt = 2.0
    sim = s.simulate(duration="600 s", step=f"{dt} s")
    vol = np.array(sim["dut.volume"], dtype=float)
    net = np.array(sim["dut.net_inflow"], dtype=float) / H_PER_S  # m3/s
    assert vol[-1] - vol[0] == pytest.approx(float(np.sum(net[:-1]) * dt), rel=1e-9)
    assert vol[-1] > vol[0]  # this configuration fills
    # The port mass flows add up to the net inflow at every sample.
    m_in = np.array(sim["dut.inlet.m_flow"], dtype=float)
    m_out = np.array(sim["dut.outlet.m_flow"], dtype=float)
    assert np.allclose((m_in + m_out) / RHO, net, rtol=1e-12, atol=1e-15)
    assert all(v == 0.0 for v in sim["dut.overflow_rate"])


def _fill_system(height: float = 1.0, level: float = 0.99) -> wp.System:
    s = wp.System("fill")
    s.add("src", "supply", pressure=0.5)
    s.add("v", "valve", kv=2.5)
    s.add("dut", "tank", diameter=0.5, height=height, initial_level=level)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    return s


def test_overflow_when_full() -> None:
    # 0.5 bar through Kv 2.5 (valve) and Kv 200 (port) in series into a full 1 m tank:
    #   Kv_eff = 1/sqrt(1/2.5**2 + 1/200**2) = 2.4998047 m3/h
    #   Q = Kv_eff * sqrt((0.5 - 998.2 * g * 1 / 1e5) * 1000 / 998.2) = 1.586608 m3/h
    q_hand = (
        1 / math.sqrt(1 / 2.5**2 + 1 / 200**2) * math.sqrt((0.5 - RHO * G * 1.0 / 1e5) * 1000 / RHO)
    )
    assert q_hand == pytest.approx(1.586608, rel=1e-6)
    s = _fill_system()
    dt = 1.0
    sim = s.simulate(duration="60 s", step=f"{dt} s")
    assert sim["dut.level"][-1] == pytest.approx(1.0, abs=1e-12)
    assert sim["dut.overflow_rate"][-1] == pytest.approx(q_hand, rel=1e-5)
    assert sim["dut.overflow_rate"][-1] == pytest.approx(sim["dut.net_inflow"][-1], rel=1e-12)
    assert sim.final.modes["dut"] == "overflowing"
    assert sim.final.has_warning("dut.tank_overflow")
    first = {(w.component, w.code): w.time for w in sim.warnings}
    assert 0 < first[("dut", "tank_overflow")] < 10
    # Volume balance including the spill: the spill over step k is reported at sample k+1.
    vol = np.array(sim["dut.volume"], dtype=float)
    net = np.array(sim["dut.net_inflow"], dtype=float) / H_PER_S
    spill = np.array(sim["dut.overflow_rate"], dtype=float) / H_PER_S
    assert vol[-1] - vol[0] == pytest.approx(
        float(np.sum(net[:-1]) * dt - np.sum(spill[1:]) * dt), abs=1e-12
    )
    # A steady solve of the full tank reports the net inflow as overflow.
    r = s.solve()
    assert r["dut.overflow_rate"] == pytest.approx(r["dut.net_inflow"], rel=1e-12)
    assert r["dut.overflow_rate"] == pytest.approx(q_hand, rel=1e-5)
    assert r.modes["dut"] == "overflowing"
    # ... and none below the rim.
    s.set("dut.level", 0.9)
    r = s.solve()
    assert r["dut.overflow_rate"] == 0.0
    assert r.modes["dut"] == "filling"
    assert not r.has_warning("dut.tank_overflow")


def test_outflow_blocked_when_empty() -> None:
    s = _drain_system(1.0, 0.0)
    r = s.solve()
    assert abs(r["dut.net_inflow"]) < 1e-9
    assert r.modes["dut"] == "empty"
    assert r.has_warning("dut.tank_empty")
    assert r.has_warning("dut.low_level")
    # A pump drawing from an empty tank cannot pull water out of it (leakage 1e-6 of the Kv).
    p = wp.System("dry")
    p.add("dut", "tank", initial_level=0.0)
    p.add("pump", "centrifugal_pump")
    p.add("sink", "drain")
    p.connect("dut.outlet", "pump.inlet")
    p.connect("pump.outlet", "sink.port")
    r = p.solve()
    assert r["dut.net_inflow"] > -1e-3  # m3/h: only leakage
    # The pump pulls its inlet below the vapour pressure: cavitation (running dry).
    assert r["pump.npsh_available"] < 0
    assert r.has_warning("pump.cavitation")
    assert r.modes["pump"] == "cavitating"
    # An empty tank still accepts inflow (only outflow is blocked).
    f = _fill_system(level=0.0)
    r = f.solve()
    assert r["dut.net_inflow"] > 1.0
    assert r.modes["dut"] == "empty"  # the mode reports the state; the level will rise
    sim = f.simulate(duration="10 s", step="1 s")
    assert sim["dut.level"][-1] > 0.001
    assert sim.final.modes["dut"] == "filling"


def test_drain_to_empty_stops_at_zero() -> None:
    s = _drain_system(0.1, 0.05)
    sim = s.simulate(duration="60 s", step="0.25 s")
    levels = np.array(sim["dut.level"], dtype=float)
    assert levels.min() >= 0.0
    assert levels[-1] <= 0.001
    assert np.all(np.diff(levels) <= 1e-15)  # never refills from the drain
    assert sim.final.modes["dut"] == "empty"


def test_perfect_mixing_without_outflow() -> None:
    # No outflow: every step conserves V T exactly, so V_N T_N = V_0 T_0 + (V_N - V_0) T_in.
    s = wp.System("mix")
    s.add("src", "supply", pressure=1.0, temperature=60)
    s.add("v", "valve", kv=2.5)
    s.add("dut", "tank", diameter=1.0, initial_level=0.5, initial_temperature=10)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    sim = s.simulate(duration="20 min", step="5 s")
    v0 = math.pi / 4 * 0.5
    vn = sim["dut.volume"][-1]
    tn = sim["dut.temperature"][-1]
    assert vn * tn == pytest.approx(v0 * 10 + (vn - v0) * 60, rel=1e-12)


def test_perfect_mixing_through_flow_matches_exponential() -> None:
    # Supply (0.2 bar, 40 degC) -> Kv 2.5 -> tank -> Kv 2.5 -> drain. With identical
    # resistances the level is steady at h = 0.2e5 / (2 rho g) = 1.02150 m, where inflow
    # equals outflow q. A perfectly mixed tank then follows
    #   T(t) = T_in + (T0 - T_in) exp(-q t / V)
    # (explicit Euler multiplies T - T_in by (1 - q dt / V) per step instead).
    h_eq = 0.2e5 / (2.0 * RHO * G)
    s = wp.System("through")
    s.add("src", "supply", pressure=0.2, temperature=40)
    s.add("vin", "valve", kv=2.5)
    s.add("dut", "tank", diameter=1.0, initial_level=h_eq, initial_temperature=15)
    s.add("vout", "valve", kv=2.5)
    s.add("sink", "drain")
    s.connect("src.port", "vin.port_a")
    s.connect("vin.port_b", "dut.inlet")
    s.connect("dut.outlet", "vout.port_a")
    s.connect("vout.port_b", "sink.port")
    sim = s.simulate(duration="1 h", step="10 s")
    levels = np.array(sim["dut.level"], dtype=float)
    assert np.allclose(levels, h_eq, rtol=1e-6)
    q = sim["dut.inlet.m_flow"][0] / RHO  # m3/s
    vol = math.pi / 4 * h_eq
    t = np.array(sim.time)
    analytic = 40 + (15 - 40) * np.exp(-q * t / vol)
    temps = np.array(sim["dut.temperature"], dtype=float)
    assert np.max(np.abs(temps - analytic)) < 0.05  # K
    assert temps[-1] == pytest.approx(analytic[-1], abs=0.05)
    assert 25 < temps[-1] < 35


def test_level_round_trips_through_system_document() -> None:
    s = _drain_system(0.5, 2.0)
    s.simulate(duration="300 s", step="1 s")
    level = s.get("dut.level")
    assert level < 1.9
    doc = s.to_dict()
    entry = next(c for c in doc["components"] if c["name"] == "dut")
    assert entry["states"]["level"] == pytest.approx(level, rel=1e-15)
    s2 = wp.System.from_dict(doc)
    assert s2.get("dut.level") == pytest.approx(level, rel=1e-15)
    r1, r2 = s.solve(), s2.solve()
    # Same state, same answer (to the solver tolerance; the warm starts differ).
    assert r2["dut.net_inflow"] == pytest.approx(r1["dut.net_inflow"], rel=1e-8)
    assert r2["dut.level"] == pytest.approx(level, rel=1e-15)


def test_steady_solve_holds_the_level() -> None:
    s = _drain_system(0.5, 2.0)
    s.solve()
    s.solve()
    assert s.get("dut.level") == 2.0


def test_initial_level_must_not_exceed_height() -> None:
    s = wp.System("bad")
    s.add("dut", "tank")
    with pytest.raises(InvalidValueError, match="must not exceed height"):
        s.set("dut.initial_level", 3.5)
    with pytest.raises(InvalidValueError, match="must not exceed height"):
        s.set("dut.height", 1.0)
    s.set_values({"dut.height": 1.0, "dut.initial_level": 0.5})
    assert s.get("dut.level") == 0.5


def _break_tank(port_kv: float = 200.0) -> wp.System:
    """Supply (1 bar) -> Kv 2.5 -> empty tank (D 1 m) -> default pump -> Kv 15 -> drain."""
    s = wp.System("break-tank")
    s.add("src", "supply", pressure=1.0)
    s.add("vin", "valve", kv=2.5)
    s.add("dut", "tank", diameter=1.0, initial_level=0.0, port_kv=port_kv)
    s.add("pump", "centrifugal_pump")
    s.add("v", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("src.port", "vin.port_a")
    s.connect("vin.port_b", "dut.inlet")
    s.connect("dut.outlet", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


@pytest.mark.parametrize("dt", [0.1, 1.0, 5.0])
def test_break_tank_conserves_water_at_any_step(dt: float) -> None:
    # The pump (23.9 m3/h through Kv 15) can draw far more than the 2.502 m3/h feed
    # (2.4998047 * sqrt(1000 / 998.2)). Volume balance over the run, explicit Euler:
    #   V_N - V_0 = sum_k (fed_k - pumped_k) dt, with V_0 = 0, so pumped <= fed.
    # Each step may draw the water above the 1 mm empty level shared by two ports, so the
    # level settles at 1 mm + 2 Q dt / A and the pump then delivers exactly the feed.
    s = _break_tank()
    sim = s.simulate(duration="600 s", step=f"{dt} s")
    fed = np.array(sim["dut.inlet.m_flow"][:-1], dtype=float) / RHO * dt
    pumped = np.array(sim["pump.volume_flow"][:-1], dtype=float) / H_PER_S * dt
    d_vol = sim["dut.volume"][-1] - sim["dut.volume"][0]
    assert d_vol == pytest.approx(float(fed.sum() - pumped.sum()), abs=1e-6)  # 1 mL
    assert pumped.sum() <= fed.sum()
    q_feed = 2.4998047 * math.sqrt(1000 / RHO)
    assert sim.final["pump.volume_flow"] == pytest.approx(q_feed, rel=1e-3)
    area = math.pi / 4
    assert sim.final["dut.level"] == pytest.approx(
        0.001 + 2 * q_feed / H_PER_S * dt / area, rel=0.01
    )
    assert min(sim["dut.level"]) >= 0.0
    # No chattering between an empty and a drawn-down tank once it has settled.
    late = [c for c in sim.mode_changes if c.component == "dut" and c.time > 60]
    assert late == []


def test_pump_emptying_a_tank_takes_only_what_is_there() -> None:
    # A default pump empties a 1 m tank holding 0.5 m (0.3927 m3) at a 1 s step. The last
    # step may draw only the water above the 1 mm empty level; after that the closed gate
    # passes only 1e-5 kg/s per bar of suction, taken from the 1 mm left. The pump delivers
    # exactly what left the tank, never more than it held (previously the level clamp at 0
    # made up about 4 L).
    s = wp.System("empty-by-pump")
    s.add("dut", "tank", diameter=1.0, initial_level=0.5)
    s.add("pump", "centrifugal_pump")
    s.add("v", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("dut.outlet", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    sim = s.simulate(duration="120 s", step="1 s")
    pumped = float(np.sum(np.array(sim["pump.volume_flow"][:-1], dtype=float)) / H_PER_S)
    v0 = math.pi / 4 * 0.5
    assert pumped == pytest.approx(v0 - sim.final["dut.volume"], abs=1e-6)
    assert pumped < v0
    assert 0.0 < sim.final["dut.level"] <= 0.001
    assert sim.final.modes["dut"] == "empty"


def test_very_stiff_tank_cannot_overshoot_empty() -> None:
    # D 0.05 m, 1 m of water, port Kv 1e6 and a Kv 1e3 valve to atmosphere: unrestricted,
    # one 1 s step would drain about 44 times the 1.96 L contents (k = kv_to_k(1e3 m3/h)
    # = 0.877 kg/(s Pa^0.5), m = k sqrt(rho g h) = 87 kg/s). The cap stops the step at the
    # empty level, so no step makes the volume negative and no water is created.
    s = wp.System("stiff")
    s.add("dut", "tank", diameter=0.05, height=1.0, initial_level=1.0, port_kv=1e6)
    s.add("v", "valve", kv=1e3)
    s.add("sink", "drain")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    sim = s.simulate(duration="3 s", step="1 s")
    area = math.pi * 0.05**2 / 4
    out = -float(np.sum(np.array(sim["dut.net_inflow"][:-1], dtype=float))) / H_PER_S
    assert out == pytest.approx(area * (1.0 - sim.final["dut.level"]), rel=1e-6)
    assert 0.0 < sim.final["dut.level"] <= 0.001
    assert min(sim["dut.level"]) > 0.0
    assert sim.final.modes["dut"] == "empty"


def test_level_state_cannot_exceed_height() -> None:
    s = _drain_system(2.0, 2.0)
    with pytest.raises(InvalidValueError, match="must not exceed height"):
        s.set("dut.level", 3.5)
    assert s.get("dut.level") == 2.0
    s.set("dut.level", 3.0)  # full to the rim is fine
    # Lowering the rim below the water is rejected unless the level goes down with it.
    with pytest.raises(InvalidValueError, match="must not exceed height"):
        s.set("dut.height", 2.5)  # keeps the 3 m of water
    assert s.get("dut.height") == 3.0
    s.set_values({"dut.height": 2.5, "dut.initial_level": 1.0, "dut.level": 2.5})
    assert s.get("dut.level") == 2.5
    # A system document with water above the rim is reported by check().
    doc = s.to_dict()
    entry = next(c for c in doc["components"] if c["name"] == "dut")
    entry["states"] = {"level": 2.6}
    issues = wp.System.from_dict(doc).check()
    assert any(i.code == "invalid_value" and "level" in i.message for i in issues)


def test_parameter_change_keeps_the_water() -> None:
    # Changing port_kv, diameter or height keeps the level and temperature (only
    # initial_level and initial_temperature reset their states).
    s = _drain_system(0.5, 2.0)
    s.simulate(duration="300 s", step="1 s")
    level = s.get("dut.level")
    assert level < 1.9
    s.set("dut.initial_temperature", 30)
    assert s.get("dut.temperature") == pytest.approx(30)
    assert s.get("dut.level") == level
    for path, value in (("dut.port_kv", 150), ("dut.diameter", 0.6), ("dut.height", 2.5)):
        s.set(path, value)
        assert s.get("dut.level") == level, path
        assert s.get("dut.temperature") == pytest.approx(30), path
    s.set("dut.initial_level", 1.0)
    assert s.get("dut.level") == 1.0


# ----------------------------------------------------------------------------------------
# top-fed tank (design 13.5)
# ----------------------------------------------------------------------------------------
KV_EFF = 1.0 / math.sqrt(1.0 / 2.5**2 + 1.0 / 200.0**2)  # valve Kv 2.5 and port Kv 200


def _kv_flow(dp_bar: float) -> float:
    """m3/h through the valve and the port in series (Kv_eff 2.4998047) under dp_bar."""
    return KV_EFF * math.sqrt(dp_bar * 1000.0 / RHO)


def _top_fed(
    level: float, inlet_height: float = 2.5, pressure: float = 0.5, **tank: float
) -> wp.System:
    """Supply -> Kv 2.5 valve -> tank inlet (top-fed when inlet_height > 0); outlet capped."""
    s = wp.System("top-fed")
    s.add("src", "supply", pressure=pressure)
    s.add("v", "valve", kv=2.5)
    s.add("dut", "tank", initial_level=level, inlet_height=inlet_height, **tank)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    return s


def test_top_inlet_flow_is_independent_of_level_below_the_mouth() -> None:
    """Hand calculation: the mouth at 2.5 m holds a back-pressure of rho g 2.5 =
    0.2447250 bar whatever the level, so Q = 2.4998047 * sqrt((0.5 - 0.2447250) * 1000 /
    998.2) = 1.264158 m3/h at every level below the mouth. A bottom inlet at the same levels
    takes more: 1.586608 m3/h at 1 m, 1.769 m3/h into an empty tank."""
    q_hand = _kv_flow(0.5 - RHO * G * 2.5 / 1e5)
    assert q_hand == pytest.approx(1.264158, rel=1e-6)
    for level in (0.0, 0.5, 1.0, 2.0, 2.49):
        r = _top_fed(level).solve()
        assert r["dut.net_inflow"] == pytest.approx(q_hand, rel=1e-9), level
        bottom = _top_fed(level, inlet_height=0.0).solve()
        expected_bottom = _kv_flow(0.5 - RHO * G * level / 1e5)
        assert bottom["dut.net_inflow"] == pytest.approx(expected_bottom, rel=1e-6)
        assert bottom["dut.net_inflow"] > r["dut.net_inflow"]


def test_submerged_top_inlet_matches_a_bottom_inlet() -> None:
    """Once the mouth is under water, max(level, inlet_height) = level: the same results as a
    bottom-fed tank, to rounding."""
    for level in (2.5, 2.6, 2.9, 3.0):
        top = _top_fed(level).solve()
        bottom = _top_fed(level, inlet_height=0.0).solve()
        for path in ("dut.net_inflow", "dut.inlet.p", "v.volume_flow"):
            assert top[path] == pytest.approx(bottom[path], rel=1e-12, abs=1e-15), (level, path)
        assert top["dut.net_inflow"] == pytest.approx(
            _kv_flow(0.5 - RHO * G * level / 1e5), rel=1e-6
        )


def test_inlet_height_zero_is_the_v01_tank() -> None:
    """inlet_height 0 keeps one fixed tank node for both ports (the v0.1 network)."""
    s = _top_fed(1.0, inlet_height=0.0)
    s.solve()
    tank = s.component("dut")
    assert tank.inlet_node is tank.node  # type: ignore[attr-defined]
    s.set("dut.inlet_height", 2.0)
    s.solve()
    tank = s.component("dut")
    assert tank.inlet_node is not tank.node  # type: ignore[attr-defined]


def _siphon(level: float, inlet_height: float = 2.0) -> wp.System:
    """Tank inlet (mouth at inlet_height) -> Kv 2.5 valve -> drain at the bottom's datum."""
    s = wp.System("siphon")
    s.add("dut", "tank", initial_level=level, inlet_height=inlet_height)
    s.add("v", "valve", kv=2.5)
    s.add("sink", "drain")
    s.connect("dut.inlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def test_no_backflow_out_of_a_dry_inlet() -> None:
    """With the mouth (2 m) above 1 m of water the drain below cannot pull water out through
    the inlet: steady flow is leakage only, and over a 10 min simulation the level does not
    move (a bottom port would drain 1.107 m3/h, the Torricelli-Kv flow at 1 m). Once the
    mouth is submerged the same pipe siphons like a bottom port."""
    r = _siphon(1.0).solve()
    assert -1e-3 < r["dut.net_inflow"] <= 0.0
    # The inlet pipe drains to the drain and fills with air, but nothing pulls on it (the
    # port is at atmospheric pressure): no flow is the exact result, so no drawing_air.
    assert not r.has_warning("dut.drawing_air")
    bottom = _siphon(1.0, inlet_height=0.0).solve()
    assert bottom["dut.net_inflow"] == pytest.approx(-_kv_flow(RHO * G * 1.0 / 1e5), rel=1e-6)
    sim = _siphon(1.0).simulate(duration="600 s", step="5 s")
    assert sim.final["dut.level"] == pytest.approx(1.0, abs=1e-6)
    assert max(abs(q) for q in sim["v.volume_flow"]) < 1e-2  # L/min
    submerged = _siphon(2.5).solve()
    assert submerged["dut.net_inflow"] == pytest.approx(-_kv_flow(RHO * G * 2.5 / 1e5), rel=1e-6)
    assert not submerged.has_warning("dut.drawing_air")


def test_siphon_through_a_top_inlet_stops_at_the_mouth() -> None:
    """A submerged top inlet siphons the tank down to its mouth and no further: each step
    may draw only the water above the mouth, so the level never falls below 2 m (1 m
    diameter: the 0.157 m3 above the mouth leaves at about 1.1 m3/h, in under 10 min)."""
    s = _siphon(2.2)
    s.set("dut.diameter", 1.0)
    sim = s.simulate(duration="1800 s", step="10 s")
    levels = np.array(sim["dut.level"], dtype=float)
    # Once the mouth is uncovered the closed gate still passes GATE_STIFFNESS leakage, about
    # 1e-5 kg/s per bar: some 2e-6 kg/s here, 4 micrometres of level over the rest of the run.
    assert levels.min() >= 2.0 - 1e-5
    assert sim.final["dut.level"] == pytest.approx(2.0, abs=1e-4)
    assert levels[0] - levels[-1] == pytest.approx(0.2, abs=1e-4)


def test_top_fed_mass_balance_over_a_simulation() -> None:
    """Fill a 0.5 m tank from 0.5 m through a top inlet at 2 m while it drains through a
    Kv 0.5 outlet valve, for 20 min at a 5 s step, crossing the mouth (no overflow). Explicit Euler
    conserves the water exactly: V_N - V_0 = sum_k (inflow_k - outflow_k) dt, with the
    inflow what the supply delivered. Below the mouth the inflow is the constant
    1.380039 m3/h (0.5 bar less rho g 2 m through Kv_eff 2.4998047); above it the inflow
    falls with the level."""
    s = _top_fed(0.5, inlet_height=2.0, diameter=0.5)
    s.add("vo", "valve", kv=0.5)
    s.add("sink", "drain")
    s.connect("dut.outlet", "vo.port_a")
    s.connect("vo.port_b", "sink.port")
    dt = 5.0
    sim = s.simulate(duration="1200 s", step=f"{dt} s")
    assert max(sim["dut.overflow_rate"]) == 0.0 and max(sim["dut.level"]) < 3.0
    vol = np.array(sim["dut.volume"], dtype=float)
    supplied = np.array(sim["src.volume_flow"][:-1], dtype=float) / 60000.0 * dt  # m3
    drained = np.array(sim["sink.volume_flow"][:-1], dtype=float) / 60000.0 * dt
    assert vol[-1] - vol[0] == pytest.approx(float(supplied.sum() - drained.sum()), rel=1e-9)
    net = np.array(sim["dut.net_inflow"][:-1], dtype=float) / H_PER_S * dt
    assert vol[-1] - vol[0] == pytest.approx(float(net.sum()), rel=1e-9)
    levels = np.array(sim["dut.level"], dtype=float)
    inflow = np.array(sim["dut.inlet.m_flow"], dtype=float) / RHO * H_PER_S
    below = levels < 2.0
    assert below.any() and (~below).any()  # the run crosses the mouth
    q_hand = _kv_flow(0.5 - RHO * G * 2.0 / 1e5)
    assert q_hand == pytest.approx(1.380039, rel=1e-6)
    assert np.allclose(inflow[below], q_hand, rtol=1e-9)
    assert np.all(inflow[~below] < q_hand)


def test_top_fed_filling_follows_the_analytic_level() -> None:
    """No outflow: below the mouth the level rises linearly, dh/dt = Q / A with Q =
    1.380039 m3/h and A = 0.1963495 m2, and reaches the 2 m mouth at t1 = 1.5 A / Q =
    768.30 s; above it A dh/dt = c sqrt(H - h) with H = 0.5e5 / (rho g) = 5.107775 m and c
    the series Kv as a volume coefficient (2.1745226e-4 m3/s per sqrt(m)), so
    sqrt(H - h) = sqrt(H - 2) - c (t - t1) / (2 A): 2.785683 m at 1200 s. Explicit Euler at
    1 s is within 1 mm of it."""
    area = math.pi * 0.5**2 / 4
    q = _kv_flow(0.5 - RHO * G * 2.0 / 1e5) / H_PER_S
    h_sup = 0.5e5 / (RHO * G)
    c = KV_EFF / H_PER_S * math.sqrt(RHO * G * 1000.0 / RHO / 1e5)
    t1 = 1.5 * area / q
    assert t1 == pytest.approx(768.30, abs=0.01)
    s = _top_fed(0.5, inlet_height=2.0, diameter=0.5)
    sim = s.simulate(duration="1200 s", step="1 s", variables=["dut.level"])
    t = np.array(sim.time)
    levels = np.array(sim["dut.level"], dtype=float)
    early = t < t1 - 1
    assert np.allclose(levels[early], 0.5 + q * t[early] / area, rtol=1e-9)
    root = math.sqrt(h_sup - 2.0) - c / (2 * area) * (1200.0 - t1)
    assert h_sup - root**2 == pytest.approx(2.785683, abs=1e-6)
    assert levels[-1] == pytest.approx(h_sup - root**2, abs=1e-3)


def test_pump_on_a_dry_top_inlet_draws_air() -> None:
    """A pump drawing from a top inlet whose mouth is above the water pulls on an inlet
    pipe open to the air: drawing_air, and the pump gets nothing (leakage only). With the
    mouth submerged the pump draws normally and nothing is flagged."""
    s = wp.System("pump-on-top-inlet")
    s.add("dut", "tank", initial_level=1.0, inlet_height=2.0)
    s.add("pump", "centrifugal_pump")
    s.add("v", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("dut.inlet", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    r = s.solve()
    assert r.has_warning("dut.drawing_air")
    assert abs(r["pump.volume_flow"]) < 1e-3
    s.set("dut.initial_level", 2.5)
    r = s.solve()
    assert not r.has_warning("dut.drawing_air")
    assert r["pump.volume_flow"] > 20.0


def test_inlet_height_must_not_exceed_height() -> None:
    s = wp.System("bad-inlet")
    s.add("dut", "tank")
    with pytest.raises(InvalidValueError, match="inlet_height"):
        s.set("dut.inlet_height", 3.5)
    s.set_values({"dut.height": 4.0, "dut.inlet_height": 3.5})
    assert s.get("dut.inlet_height") == 3.5
    with pytest.raises(InvalidValueError, match="inlet_height"):
        s.set("dut.height", 3.0)
