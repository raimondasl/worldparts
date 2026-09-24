"""Independent physics review of the v0.3 part 1 components (design 13.3 to 13.5).

Covers pump wear inputs, the leak (orifice to atmosphere) and its EPANET emitter mapping,
and the top-fed tank inlet. Every expected value comes from a hand calculation or an
independent fit written out here, not from the component's own helpers. Tests that document
defects found in the review say "DEFECT" in their docstrings and are expected to fail until
the defect is fixed; the others are regression checks for behaviour that no manifest
contract pins (each one kills a code mutation that `check-catalog` did not catch).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize import brentq, lsq_linear

import worldparts as wp
from worldparts.components.tank import DRAW_LEVEL, EMPTY_LEVEL
from worldparts.media import RHO, G

H_PER_S = 3600.0  # m3/h per m3/s
#: A head curve whose bounded fit has a clearly negative linear term b (the default curve's
#: b is about -1.4e-5 m/(m3/s), effectively 0, so wear on b is never exercised by it).
FALLING_CURVE = [[0, 40], [10, 36], [20, 30], [30, 22], [36, 16]]


def _bounded_fit(table: list[list[float]]) -> tuple[float, float, float]:
    """Independent bounded least-squares fit H = a + b Q + c Q**2 in SI (design 8.8)."""
    arr = np.asarray(table, dtype=float)
    q = arr[:, 0] / H_PER_S
    design = np.vstack([np.ones_like(q), q, q * q]).T
    res = lsq_linear(
        design, arr[:, 1], bounds=([0.0, -np.inf, -np.inf], [np.inf, 0.0, -1e-9]), tol=1e-14
    )
    a, b, c = (float(x) for x in res.x)
    return a, b, c


# ----------------------------------------------------------------------------------------
# pump wear (design 13.3)
# ----------------------------------------------------------------------------------------
def _pump_through_valve(kv: float, **pump: float | list[list[float]]) -> wp.System:
    s = wp.System("worn")
    s.add("src", "supply", pressure=0)
    s.add("dut", "centrifugal_pump", **pump)
    s.add("v", "valve", kv=kv, leakage=1e-6)
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


@pytest.mark.parametrize(("speed", "wear"), [(1.0, 0.3), (0.8, 0.3), (0.8, 0.5)])
def test_wear_scales_the_linear_head_term_too(speed: float, wear: float) -> None:
    """H = (1 - w) (a s**2 + b s Q + c Q |Q|) for a curve with b < 0 (design 13.3).

    Operating point on a Kv 15 valve (dp_valve = SG * 1e5 * (Q / Kv)**2, SG = rho / 1000):
    rho g H(Q) = dp_valve(Q), solved with brentq on the independent fit of FALLING_CURVE.
    No manifest contract uses a curve with b != 0, so scaling only a and c passes
    check-catalog; this pins the b term at the component level.
    """
    a, b, c = _bounded_fit(FALLING_CURVE)
    assert b < -1.0  # clearly non-zero linear term (m per m3/s)
    kv_si = 15.0 / H_PER_S

    def head(q: float) -> float:
        return (1 - wear) * (a * speed**2 + b * speed * q + c * q * abs(q))

    def residual(q: float) -> float:
        return RHO * G * head(q) - RHO / 1000 * 1e5 * (q / kv_si) ** 2

    q = brentq(residual, 1e-9, 0.05, xtol=1e-14)
    r = _pump_through_valve(15, head_curve=FALLING_CURVE, speed=speed, wear_head=wear).solve()
    # 2e-5 relative covers the law's 1 Pa per kg/s term and the valve's leakage floor
    assert r["dut.volume_flow"] == pytest.approx(q * H_PER_S, rel=2e-5)
    assert r["dut.head"] == pytest.approx(head(q), rel=5e-5)


def test_head_wear_can_raise_the_efficiency_at_the_operating_point() -> None:
    """Design 13.3 says "head and efficiency never increase with wear". That holds at the
    same flow and speed (efficiency is (1 - wear_efficiency) times the new pump's), but
    not on a fixed system curve: wear_head moves the operating point to lower flow, and to
    the right of the best-efficiency flow (24.1 m3/h) that raises the efficiency.

    Kv 22 valve, default pump: new 31.19 m3/h at 58.18 %, wear_head 0.5 24.63 m3/h at
    62.82 %. This documents the physics the design text should state precisely ("at the
    same flow and speed"); it is not an implementation defect.
    """
    effs = []
    for wear in (0.0, 0.25, 0.5):
        r = _pump_through_valve(22, wear_head=wear).solve()
        effs.append(r["dut.efficiency"])
    assert effs[0] < effs[1] < effs[2]
    assert effs[0] == pytest.approx(58.18, abs=0.05)
    assert effs[2] == pytest.approx(62.82, abs=0.05)


# ----------------------------------------------------------------------------------------
# leak and its EPANET emitter (design 13.4)
# ----------------------------------------------------------------------------------------
_INP = """[TITLE]
hand-written emitter check
[JUNCTIONS]
 J1  0  0
[RESERVOIRS]
 R1  {head}
[PIPES]
 P1  R1  J1  0.01  1000  0.001  0  Open
[EMITTERS]
 J1  {coef}
[OPTIONS]
 Units CMH
 Headloss D-W
 Emitter Exponent 0.5
 Accuracy 0.000001
 Trials 200
[TIMES]
 Duration 0
[END]
"""


@pytest.mark.parametrize("bar", [3.0, 0.5, -0.3])
def test_hand_written_epanet_emitter_is_the_orifice_equation(tmp_path: Path, bar: float) -> None:
    """A hand-written CMH .inp: reservoir at the pressure head of `bar`, a 10 mm long 1 m
    pipe (no loss) and a junction at elevation 0 with the emitter C = 3600 Cd A sqrt(2 g)
    (m3/h per m**0.5). EPANET 2.2 must give Q = Cd A sqrt(2 dp / rho), including the
    backflow at negative pressure, and the adapter must write the same coefficient.
    """
    wntr = pytest.importorskip("wntr")
    from worldparts.adapters.wntr_adapter import translate

    area = math.pi * 0.005**2 / 4
    c_si = 0.6 * area * math.sqrt(2 * G)  # m3/s per m**0.5
    head = bar * 1e5 / (RHO * G)
    inp = tmp_path / "emitter.inp"
    inp.write_text(_INP.format(head=repr(head), coef=repr(c_si * 3600)))
    wn = wntr.network.WaterNetworkModel(str(inp))
    assert wn.get_node("J1").emitter_coefficient == pytest.approx(c_si, rel=1e-12)
    res = wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(tmp_path / "em"))
    q_epanet = float(res.node["demand"].loc[0, "J1"])
    q_orifice = math.copysign(0.6 * area * math.sqrt(2 * abs(bar) * 1e5 / RHO), bar)
    assert q_epanet == pytest.approx(q_orifice, rel=1e-5)

    s = wp.System("leak")
    s.add("src", "supply", pressure=bar)
    s.add("dut", "leak")
    s.connect("src.port", "dut.port")
    assert translate(s).emitters["dut"].coefficient == pytest.approx(c_si, rel=1e-12)
    assert s.solve()["dut.volume_flow"] == pytest.approx(q_orifice * 60000, rel=1e-9)


# ----------------------------------------------------------------------------------------
# top-fed tank (design 13.5)
# ----------------------------------------------------------------------------------------
def _top_fed(pressure: float, level: float, mouth: float, diameter: float = 2.0) -> wp.System:
    s = wp.System("top-fed")
    s.add("src", "supply", pressure=pressure)
    s.add("v", "valve", leakage=1e-6)
    s.add("dut", "tank", initial_level=level, inlet_height=mouth, diameter=diameter)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    return s


def test_filling_across_the_mouth_follows_an_independent_euler_integration() -> None:
    """0.5 bar supply, Kv 2.5 valve in series with the Kv 200 port (Kv_eff = 1/sqrt(1/2.5**2
    + 1/200**2)), 0.5 m tank from 0.7 m with the mouth at 1.0 m, 900 s at 5 s. Inflow
    Q = Kv_eff sqrt((0.5 - rho g max(L, 1.0) / 1e5) / SG) m3/h, explicit Euler
    L += Q dt / A. The level crosses the mouth near 135 s; the whole run must match to
    1e-8 m and close the water balance.
    """
    kv_eff = 1 / math.sqrt(1 / 2.5**2 + 1 / 200**2)
    area = math.pi * 0.5**2 / 4
    sim = _top_fed(0.5, 0.7, 1.0, diameter=0.5).simulate(duration="900 s", step="5 s")
    level, own = 0.7, [0.7]
    for _ in range(len(sim.time) - 1):
        dp_bar = 0.5 - RHO * G * max(level, 1.0) / 1e5
        q = kv_eff * math.sqrt(dp_bar * 1000 / RHO) / H_PER_S
        level += q * 5.0 / area
        own.append(level)
    levels = np.asarray(sim.series["dut.level"])
    assert levels[0] < 1.0 < levels[-1]
    assert np.max(np.abs(levels - np.asarray(own))) < 1e-8
    inflow = np.asarray(sim.series["v.volume_flow"]) / 60000  # m3/s
    gained = area * (levels[-1] - 0.7)
    assert gained == pytest.approx(float(np.sum(inflow[:-1] * 5.0)), rel=1e-12)


def test_a_dry_top_inlet_takes_no_share_of_the_outlet_cap() -> None:
    """Design 8.9 revision and tank.update_laws: a simulation step draws at most the water
    above the empty level, shared by the ports that can draw. A top inlet whose mouth is
    above the water cannot draw, so the outlet gets the whole cap.

    0.5 m tank at 10 mm, mouth at 2 m fed by a 0 bar supply (no inflow), outlet drained
    by a pump into a drain (it would take ~30 m3/h, far above the cap). After one 10 s step
    the tank holds only the water below DRAW_LEVEL: level = DRAW_LEVEL to 5e-6 m (the closed
    gates pass about 1e-5 kg/s per bar of suction). If the dry inlet kept half the cap, the
    level would be (10 + 1) / 2 = 5.5 mm.
    """
    s = _top_fed(0.0, 0.010, 2.0, diameter=0.5)
    s.add("p", "centrifugal_pump")
    s.add("sink", "drain")
    s.connect("dut.outlet", "p.inlet")
    s.connect("p.outlet", "sink.port")
    sim = s.simulate(duration="10 s", step="10 s")
    assert sim.series["dut.level"][-1] == pytest.approx(DRAW_LEVEL, abs=5e-6)
    assert sim.series["dut.level"][-1] <= EMPTY_LEVEL


def test_dry_top_inlet_under_positive_gauge_pressure_is_not_drawing_air() -> None:
    """DEFECT: design 8.9 defines drawing_air as a port of an empty tank "held more than
    100 Pa below atmospheric pressure, the signature of a pump losing prime". The top-inlet
    extension raises it whenever the inlet port is below P_ATM + rho g inlet_height.

    A 0.2 bar gauge supply feeds a top inlet at 2.5 m (rho g 2.5 = 0.2447 bar) of a tank
    holding 1 m: the supply cannot lift water to the mouth, the water in the inlet pipe
    stands at 0.2e5 / (rho g) = 2.04 m, nothing flows. That result is exact and nothing is
    under suction (the port is at +0.2 bar gauge), yet the tank warns that the port "is
    under suction (0.2 bar gauge ...)" and that "the results downstream of this port are
    not physical". The same false warning fires for a stopped fill pump on a top-fed tank
    (the normal off state of the level switch in design 13.1).
    """
    r = _top_fed(0.2, 1.0, 2.5).solve()
    assert abs(r["dut.net_inflow"]) < 1e-3  # no flow either way (gate leakage only)
    assert r["dut.inlet.p"] == pytest.approx(0.2, abs=1e-6)  # above atmospheric
    assert not r.has_warning("dut.drawing_air"), [w.message for w in r.warnings]
