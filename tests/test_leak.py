"""Physics tests of the leak (design 13.4) against hand calculations."""

from __future__ import annotations

import math

import numpy as np
import pytest
from fluids.friction import Churchill_1977
from scipy.optimize import brentq

import worldparts as wp
from worldparts.components.leak import CLOSED_FRACTION, Leak, orifice_coefficient
from worldparts.laws import kv_to_k
from worldparts.media import MU, RHO

L_PER_MIN = 60000.0  # L/min per m3/s
CD = 0.6
AREA = math.pi * 0.005**2 / 4.0  # default 5 mm hole: 1.9634954e-5 m2


def orifice_flow(p_gauge_pa: float, cd: float = CD, area: float = AREA) -> float:
    """Hand formula Q = Cd A sqrt(2 dp / rho) (m3/s), negative for a negative gauge pressure."""
    return math.copysign(cd * area * math.sqrt(2.0 * abs(p_gauge_pa) / RHO), p_gauge_pa)


def _on_main(pressure: float, **leak: float) -> wp.System:
    s = wp.System("leak-on-main")
    s.add("src", "supply", pressure=pressure)
    s.add("dut", "leak", **leak)
    s.connect("src.port", "dut.port")
    return s


@pytest.mark.parametrize("bar", [0.01, 0.2, 0.5, 1.0, 3.0, 6.0, 10.0])
def test_orifice_equation_at_several_pressures(bar: float) -> None:
    """Q = Cd A sqrt(2 dp / rho). At 3 bar: 0.6 * 1.9634954e-5 * sqrt(6e5 / 998.2) =
    0.6 * 1.9634954e-5 * 24.51674 = 2.888338e-4 m3/s = 17.330027 L/min."""
    r = _on_main(bar).solve()
    expected = orifice_flow(bar * 1e5) * L_PER_MIN
    assert r["dut.volume_flow"] == pytest.approx(expected, rel=1e-9)
    assert r["dut.pressure"] == pytest.approx(bar, abs=1e-9)
    assert r["src.volume_flow"] == pytest.approx(r["dut.volume_flow"], rel=1e-12)
    if bar == 3.0:
        assert expected == pytest.approx(17.330027, rel=1e-7)
    assert r.modes["dut"] == "leaking" and not r.has_warning("dut.backflow")


def test_orifice_coefficient_is_the_mass_flow_form() -> None:
    """m = rho Q = Cd A f sqrt(2 rho dp), so k = Cd A f sqrt(2 rho) in kg/(s Pa^0.5)."""
    k = orifice_coefficient(0.005, 0.6, 1.0, RHO)
    assert k == pytest.approx(0.6 * AREA * math.sqrt(2 * RHO), rel=1e-15)
    assert k == pytest.approx(5.2638672e-4, rel=1e-7)
    assert k * math.sqrt(3e5) / RHO == pytest.approx(orifice_flow(3e5), rel=1e-12)


@pytest.mark.parametrize(("diameter", "cd"), [(0.5, 0.6), (5.0, 1.0), (50.0, 0.62), (300.0, 0.1)])
def test_diameter_and_discharge_coefficient(diameter: float, cd: float) -> None:
    r = _on_main(2.0, diameter=diameter, discharge_coefficient=cd).solve()
    area = math.pi * (diameter / 1000) ** 2 / 4
    assert r["dut.volume_flow"] == pytest.approx(orifice_flow(2e5, cd, area) * L_PER_MIN, rel=1e-9)


def test_opening_scales_the_area() -> None:
    """opening f: Q = Cd (f A) sqrt(2 dp / rho); a closed leak keeps 1e-6 of the area."""
    s = _on_main(3.0)
    full = s.solve()["dut.volume_flow"]
    for f in (0.01, 0.25, 0.5, 0.9):
        s.set("dut.opening", f)
        assert s.solve()["dut.volume_flow"] == pytest.approx(f * full, rel=1e-9)
    s.set("dut.opening", 0.0)
    r = s.solve()
    assert r["dut.volume_flow"] == pytest.approx(CLOSED_FRACTION * full, rel=1e-6)
    assert r["dut.volume_flow"] < 1e-4
    assert r.modes["dut"] == "closed"


def test_backflow_at_negative_gauge_pressure() -> None:
    """At -0.3 bar the orifice draws water in: Q = -Cd A sqrt(2 * 0.3e5 / rho) =
    -5.480236 L/min, and the backflow warning is raised; at 0 bar nothing flows."""
    r = _on_main(-0.3).solve()
    assert r["dut.volume_flow"] == pytest.approx(orifice_flow(-0.3e5) * L_PER_MIN, rel=1e-9)
    assert r["dut.volume_flow"] == pytest.approx(-5.480236, rel=1e-6)
    assert r.has_warning("dut.backflow") and r.modes["dut"] == "backflow"
    idle = _on_main(0.0).solve()
    assert idle["dut.volume_flow"] == 0.0
    assert not idle.has_warning("dut.backflow") and idle.modes["dut"] == "idle"


def test_leak_on_a_pump_suction_draws_water_in() -> None:
    """A leak at a pump inlet fed through a throttled suction valve sits below atmospheric
    pressure: water is drawn in through it, and the pump delivers the suction flow plus the
    ingress (mass balance at the inlet junction)."""
    s = wp.System("suction-leak")
    s.add("src", "supply", pressure=0)
    s.add("vs", "valve", kv=12)
    s.add("dut", "leak")
    s.add("pump", "centrifugal_pump")
    s.add("vd", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("src.port", "vs.port_a")
    s.connect("vs.port_b", "pump.inlet")
    s.connect("vs.port_b", "dut.port")
    s.connect("pump.outlet", "vd.port_a")
    s.connect("vd.port_b", "sink.port")
    r = s.solve()
    p = r["dut.pressure"]
    assert p < -0.1
    assert r["dut.volume_flow"] == pytest.approx(orifice_flow(p * 1e5) * L_PER_MIN, rel=1e-9)
    assert r.has_warning("dut.backflow")
    pump_q = r["pump.volume_flow"] * 1000 / 60  # m3/h -> L/min
    assert pump_q == pytest.approx(r["vs.volume_flow"] - r["dut.volume_flow"], rel=1e-9)


def _pipe_dp(q: float, length: float, d: float, rough: float) -> float:
    """Darcy-Weisbach pressure drop (Pa) with the Churchill (1977) factor from ``fluids``."""
    if q == 0.0:
        return 0.0
    area = math.pi * d * d / 4
    v = q / area
    re = RHO * abs(v) * d / MU
    f = Churchill_1977(re, rough / d)
    return math.copysign(f * length / d * RHO * v * v / 2.0, q)


def _pipe_network(opening: float) -> wp.System:
    s = wp.System("pipe-leak")
    s.add("src", "supply", pressure=4.0)
    s.add("p1", "pipe", length=100, diameter="32 mm", roughness="0.05 mm")
    s.add("dut", "leak", diameter=8, opening=opening)
    s.add("p2", "pipe", length=100, diameter="32 mm", roughness="0.05 mm")
    s.add("v", "valve", kv=3)
    s.add("sink", "drain")
    s.connect("src.port", "p1.port_a")
    s.connect("p1.port_b", "dut.port")
    s.connect("p1.port_b", "p2.port_a")
    s.connect("p2.port_b", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def _hand_pipe_network(leak: bool) -> tuple[float, float, float]:
    """Independent solution with fluids' friction factor: returns (p_J, p_down, Q_leak) in
    Pa gauge and m3/s. Mass balance at the junction J: Q1(p_J) = Q_leak(p_J) + Q2(p_J), with
    Q1 from the upstream pipe under 4 bar - p_J, Q2 from the downstream pipe and the Kv 3
    valve in series under p_J, and Q_leak = Cd A sqrt(2 p_J / rho) (8 mm hole)."""
    d, rough, length = 0.032, 5e-5, 100.0
    k_valve = kv_to_k(3.0 / 3600.0, RHO)
    area = math.pi * 0.008**2 / 4

    def q1(pj: float) -> float:
        return brentq(lambda q: _pipe_dp(q, length, d, rough) - (4e5 - pj), 1e-9, 0.1, xtol=1e-15)

    def q2(pj: float) -> float:
        def f(q: float) -> float:
            return _pipe_dp(q, length, d, rough) + (RHO * q / k_valve) ** 2 - pj

        return brentq(f, 1e-9, 0.1, xtol=1e-15)

    def ql(pj: float) -> float:
        return orifice_flow(pj, CD, area) if leak else 0.0

    pj = brentq(lambda p: q1(p) - ql(p) - q2(p), 1e3, 3.99e5, xtol=1e-9)
    p_down = pj - _pipe_dp(q2(pj), length, d, rough)
    return pj, p_down, ql(pj)


def test_leak_on_a_pipe_network_lowers_the_downstream_pressure() -> None:
    """The leak takes water at the junction, so more flows through the upstream pipe and
    less through the downstream one: both the junction pressure and the valve's inlet
    pressure fall. worldparts agrees with the independent solution (fluids' Churchill
    friction factor, nested brentq) to 1e-6."""
    pj0, pdown0, _ = _hand_pipe_network(leak=False)
    pj, pdown, ql = _hand_pipe_network(leak=True)
    assert pj < pj0 and pdown < pdown0
    closed = _pipe_network(0.0).solve()
    r = _pipe_network(1.0).solve()
    assert closed["dut.pressure"] == pytest.approx(pj0 / 1e5, rel=1e-5)
    assert closed["v.port_a.p"] == pytest.approx(pdown0 / 1e5, rel=1e-5)
    assert r["dut.pressure"] == pytest.approx(pj / 1e5, rel=1e-6)
    assert r["v.port_a.p"] == pytest.approx(pdown / 1e5, rel=1e-6)
    assert r["dut.volume_flow"] == pytest.approx(ql * L_PER_MIN, rel=1e-6)
    # How much the leak costs downstream (about 0.8 bar at the valve here).
    assert r["v.port_a.p"] < closed["v.port_a.p"] - 0.5
    assert r.unit("p1.volume_flow") == r.unit("dut.volume_flow") == "L/min"
    assert r["p1.volume_flow"] == pytest.approx(
        r["p2.volume_flow"] + r["dut.volume_flow"], rel=1e-9
    )


def test_burst_during_a_simulation() -> None:
    """opening is an input: an event opens the leak mid-run and the pressure drops."""
    s = _pipe_network(0.0)
    sim = s.simulate(
        duration="60 s", step="10 s", events=[{"at": "30 s", "set": {"dut.opening": 1.0}}]
    )
    t = np.array(sim.time)
    p = np.array(sim["dut.pressure"], dtype=float)
    q = np.array(sim["dut.volume_flow"], dtype=float)
    before, after = t < 30, t >= 30
    assert np.all(q[before] < 1e-3) and np.all(q[after] > 10)
    assert p[after][0] < p[before][-1] - 0.5
    assert any(m.component == "dut" and m.mode == "leaking" for m in sim.mode_changes)


def test_law_is_exact_above_the_regularisation_band_and_monotone_through_zero() -> None:
    """Exactly the orifice equation above about 0.4 Pa (0.2 % of the flow at 1 bar); a
    C1 cubic below it, so the flow is continuous and strictly increasing through zero."""
    s = _on_main(0.0)
    comp = s.component("dut")
    assert isinstance(comp, Leak)
    flows = []
    for pa in (-100.0, -1.0, -0.01, 0.0, 0.01, 1.0, 100.0):
        s.set("src.pressure", pa / 1e5)
        q = s.solve()["dut.volume_flow"] / L_PER_MIN
        flows.append(q)
        if abs(pa) >= 1.0:
            assert q == pytest.approx(orifice_flow(pa), rel=1e-9)
    assert all(np.diff(flows) > 0)
