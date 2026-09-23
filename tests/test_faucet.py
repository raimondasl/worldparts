"""Physics of the mixing faucet (design 8.6) against hand calculations.

Kv convention: ``Q [m3/h] = Kv * sqrt(dp [bar] / SG)`` with ``SG = rho / 1000 = 0.9982``.
Series elements with the same flow combine as ``1 / Kv**2 = sum(1 / Kv_i**2)``; parallel
elements with the same pressure drop combine as ``Kv = sum(Kv_i)``.
"""

from __future__ import annotations

import math

import pytest
from scipy.optimize import brentq

import worldparts as wp
from worldparts.media import CP, RHO

SG = RHO / 1000.0
LEAK = 1e-6


def kv_q_lmin(kv: float, dp_bar: float) -> float:
    """Signed flow in L/min through a Kv element at a signed pressure drop in bar."""
    return math.copysign(kv * math.sqrt(abs(dp_bar) / SG), dp_bar) * 1000.0 / 60.0


def cartridge_kv(lift: float, mix: float, kv: float = 0.6) -> tuple[float, float]:
    """Hot and cold cartridge Kv in m3/h (design 8.6)."""
    return (
        kv * (LEAK + (1 - LEAK) * lift * mix),
        kv * (LEAK + (1 - LEAK) * lift * (1 - mix)),
    )


def faucet_system(
    p_hot: float | None = 3.0,
    p_cold: float | None = 3.0,
    t_hot: float = 55.0,
    t_cold: float = 12.0,
    lift: float = 1.0,
    mix: float = 0.5,
) -> wp.System:
    """Faucet fed by a hot and a cold supply; ``None`` leaves that inlet capped."""
    s = wp.System("basin")
    s.add("dut", "mixing_faucet", inputs={"lift": lift, "mix": mix})
    if p_hot is not None:
        s.add("hsup", "supply", pressure=p_hot, temperature=t_hot)
        s.connect("hsup.port", "dut.hot")
    if p_cold is not None:
        s.add("csup", "supply", pressure=p_cold, temperature=t_cold)
        s.connect("csup.port", "dut.cold")
    return s


def codes(result: wp.SolveResult) -> set[str]:
    return {w.path for w in result.warnings}


# -- flow ------------------------------------------------------------------------------------


def test_flow_at_3_bar_from_series_parallel_kv() -> None:
    # Hand calculation, 3 bar gauge on both inlets, lift 1, mix 0.5, defaults:
    #   Kv_hot  = 0.6 * (1e-6 + (1 - 1e-6) * 1 * 0.5) = 0.3000003 m3/h
    #   Kv_cold = 0.3000003 m3/h (same)
    #   both inlets see the same drop (equal supply pressures), so they are in parallel:
    #   Kv_in = 0.6000006 m3/h
    #   in series with the spout Kv 0.8: Kv_eq = 1 / sqrt(1/0.6**2 + 1/0.8**2) = 0.48 m3/h
    #   (0.6, 0.8, 1.0 is a 3-4-5 triangle: 0.6 * 0.8 / 1.0)
    #   Q = 0.48 * sqrt(3 / 0.9982) = 0.48 * 1.733612 = 0.832134 m3/h = 13.869 L/min
    kv_h, kv_c = cartridge_kv(1.0, 0.5)
    kv_eq = 1.0 / math.sqrt(1.0 / (kv_h + kv_c) ** 2 + 1.0 / 0.8**2)
    expected = kv_q_lmin(kv_eq, 3.0)
    assert expected == pytest.approx(13.869, abs=0.001)  # "about 14 L/min" (design 8.6)
    r = faucet_system().solve()
    assert r["dut.flow"] == pytest.approx(expected, rel=0.005)
    assert r["dut.flow"] == pytest.approx(expected, rel=1e-6)  # the solver is much tighter
    assert r["dut.hot_flow"] == pytest.approx(expected / 2, rel=1e-6)
    assert r["dut.cold_flow"] == pytest.approx(expected / 2, rel=1e-6)
    assert r.modes["dut"] == "mixing"
    assert codes(r) == set()


def test_mass_balance_across_the_faucet() -> None:
    r = faucet_system(p_hot=2.0, p_cold=4.0, mix=0.7).solve()
    m_in = r["dut.hot.m_flow"] + r["dut.cold.m_flow"]  # kg/s into the faucet
    assert m_in == pytest.approx(r["dut.flow"] * RHO / 60000.0, rel=1e-9)
    assert r["dut.flow"] == pytest.approx(r["dut.hot_flow"] + r["dut.cold_flow"], rel=1e-9)


# -- temperature -----------------------------------------------------------------------------


@pytest.mark.parametrize("mix", [0.0, 0.1, 0.25, 0.5, 0.8, 1.0])
def test_mixed_temperature_is_flow_weighted_mean(mix: float) -> None:
    # Energy balance of adiabatic mixing with constant cp:
    #   (Q_h + Q_c) * cp * T = Q_h * cp * T_h + Q_c * cp * T_c
    #   => T = (Q_h * T_h + Q_c * T_c) / (Q_h + Q_c)
    # With equal pressures, Q_h / Q_c = Kv_h / Kv_c (parallel branches, same drop).
    t_h, t_c = 60.0, 10.0
    r = faucet_system(t_hot=t_h, t_cold=t_c, mix=mix).solve()
    kv_h, kv_c = cartridge_kv(1.0, mix)
    t_hand = (kv_h * t_h + kv_c * t_c) / (kv_h + kv_c)
    q_h, q_c = r["dut.hot_flow"], r["dut.cold_flow"]
    assert r["dut.temperature"] == pytest.approx((q_h * t_h + q_c * t_c) / (q_h + q_c), abs=1e-9)
    assert r["dut.temperature"] == pytest.approx(t_hand, abs=1e-6)
    # Enthalpy flow out equals enthalpy flow in (W, relative to 0 degC).
    h_out = r["dut.flow"] / 60000 * RHO * CP * r["dut.temperature"]
    h_in = (q_h * t_h + q_c * t_c) / 60000 * RHO * CP
    assert h_out == pytest.approx(h_in, rel=1e-9)


def test_temperature_is_none_when_closed() -> None:
    r = faucet_system(lift=0.0).solve()
    assert r["dut.temperature"] is None
    assert r.modes["dut"] == "closed"
    # Only leakage passes: Kv 6e-7 m3/h per cartridge, 1.2e-6 in parallel, so
    # Q = 1.2e-6 * sqrt(3 / 0.9982) m3/h = 3.47e-5 L/min.
    assert r["dut.flow"] == pytest.approx(kv_q_lmin(1.2e-6, 3.0), rel=0.01)
    assert codes(r) == set()


def test_scald_warning_follows_temperature() -> None:
    r = faucet_system(t_hot=60.0, mix=1.0).solve()
    assert r["dut.temperature"] == pytest.approx(60.0, abs=1e-3)
    assert "dut.scald_risk" in codes(r)
    assert r.modes["dut"] == "hot_only"
    r = faucet_system(t_hot=60.0, mix=0.5).solve()  # (60 + 12) / 2 = 36 degC
    assert r["dut.temperature"] == pytest.approx(36.0, abs=1e-6)
    assert "dut.scald_risk" not in codes(r)


# -- crossflow -------------------------------------------------------------------------------


def hand_crossflow(p_hot: float, p_cold: float, mix: float) -> tuple[float, float, float]:
    """Mixing-node pressure (bar gauge) and hot/cold inlet flows (L/min) by bisection.

    Mass balance at the mixing node C (signed Kv flows):
        Q(Kv_h, p_hot - pC) + Q(Kv_c, p_cold - pC) = Q(Kv_spout, pC - 0)
    """
    kv_h, kv_c = cartridge_kv(1.0, mix)

    def balance(pc: float) -> float:
        return kv_q_lmin(kv_h, p_hot - pc) + kv_q_lmin(kv_c, p_cold - pc) - kv_q_lmin(0.8, pc)

    pc = brentq(balance, 0.0, max(p_hot, p_cold), xtol=1e-14)
    return pc, kv_q_lmin(kv_h, p_hot - pc), kv_q_lmin(kv_c, p_cold - pc)


def test_crossflow_into_the_lower_pressure_supply() -> None:
    # Hot 1 bar, cold 5 bar, mix 0.2: Kv_h = 0.12, Kv_c = 0.48 m3/h. At pC = 1 bar the cold
    # branch alone would give 0.48 * 2 = 0.96 > 0.8 = spout flow (in sqrt(bar) units), so
    # pC must rise above the hot supply pressure: water flows backward into the hot line.
    pc, q_h, q_c = hand_crossflow(1.0, 5.0, 0.2)
    assert pc > 1.0 and q_h < 0
    r = faucet_system(p_hot=1.0, p_cold=5.0, mix=0.2).solve()
    assert r["dut.hot_flow"] == pytest.approx(q_h, rel=1e-4)  # about -0.915 L/min
    assert r["dut.cold_flow"] == pytest.approx(q_c, rel=1e-4)
    assert r["hsup.volume_flow"] == pytest.approx(q_h, rel=1e-4)  # the hot supply absorbs
    assert r.modes["hsup"] == "absorbing"
    assert r["dut.temperature"] == pytest.approx(12.0, abs=1e-9)  # only cold water enters C
    assert "dut.crossflow" in codes(r)


@pytest.mark.parametrize("mix", [0.0, 0.05, 0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.7, 1.0])
def test_crossflow_warning_fires_exactly_on_backflow(mix: float) -> None:
    _, q_h, q_c = hand_crossflow(1.0, 5.0, mix)
    backflow = (q_h < -0.01 and q_c > 0.01) or (q_c < -0.01 and q_h > 0.01)
    r = faucet_system(p_hot=1.0, p_cold=5.0, mix=mix).solve()
    assert ("dut.crossflow" in codes(r)) is backflow


def test_crossflow_sweep_is_not_vacuous() -> None:
    """The warning_iff contract's sweep contains points on both sides of the condition."""
    seen = {
        "dut.crossflow" in codes(faucet_system(p_hot=1.0, p_cold=5.0, mix=m).solve())
        for m in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
    }
    assert seen == {True, False}


@pytest.mark.parametrize("mix", [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0])
def test_crossflow_contract_threshold_matches_hand_solve(mix: float) -> None:
    # The crossflow-iff-hand-threshold contract's condition: at pC = p_hot the hot inlet
    # carries no flow, so backflow into the hot line happens exactly when the cold path alone
    # overfeeds the spout, Kv_c * sqrt(5 - 1) > 0.8 * sqrt(1). Cross-check with brentq.
    _, kv_c = cartridge_kv(1.0, mix)
    _, q_h, _ = hand_crossflow(1.0, 5.0, mix)
    assert (kv_c * 2.0 > 0.8) is (q_h < -0.01)


# -- back-siphonage --------------------------------------------------------------------------


def hand_general(
    p_hot: float, p_cold: float, mix: float, lift: float = 1.0
) -> tuple[float, float, float]:
    """Discharge, hot and cold inlet flows (L/min) for any supply pressures (bar gauge)."""
    kv_h, kv_c = cartridge_kv(lift, mix)

    def balance(pc: float) -> float:
        return kv_q_lmin(kv_h, p_hot - pc) + kv_q_lmin(kv_c, p_cold - pc) - kv_q_lmin(0.8, pc)

    lo, hi = min(p_hot, p_cold, 0.0), max(p_hot, p_cold, 0.0)
    pc = brentq(balance, lo, hi, xtol=1e-14)
    return kv_q_lmin(0.8, pc), kv_q_lmin(kv_h, p_hot - pc), kv_q_lmin(kv_c, p_cold - pc)


def test_back_siphonage_with_sub_atmospheric_supplies() -> None:
    # Both supplies at -0.5 bar: Kv_eq = 0.48 m3/h (0.6 in parallel, 0.8 spout in series),
    # Q = -0.48 * sqrt(0.5 / 0.9982) m3/h = -5.662 L/min, drawn in through the spout. Both
    # supplies absorb, so this is back-siphonage and not crossflow.
    q, q_h, q_c = hand_general(-0.5, -0.5, 0.5)
    assert q == pytest.approx(-0.48 * math.sqrt(0.5 / SG) * 1000 / 60, rel=1e-5)
    r = faucet_system(p_hot=-0.5, p_cold=-0.5).solve()
    assert r["dut.flow"] == pytest.approx(q, rel=1e-5)
    assert r["dut.hot_flow"] == pytest.approx(q_h, rel=1e-5)
    assert r["dut.cold_flow"] == pytest.approx(q_c, rel=1e-5)
    assert r["dut.temperature"] is None
    assert codes(r) == {"dut.back_siphonage", "dut.low_supply_pressure"}


def test_back_siphonage_and_crossflow_together() -> None:
    # Hot at -0.5 bar, cold at +0.2 bar, lever open and half mixed: the cold supply feeds the
    # faucet, the hot line absorbs (crossflow) and the mixing chamber is below atmospheric
    # pressure, so water is also drawn in through the spout (back-siphonage).
    q, q_h, q_c = hand_general(-0.5, 0.2, 0.5)
    assert q < -0.01 and q_h < -0.01 and q_c > 0.01
    r = faucet_system(p_hot=-0.5, p_cold=0.2).solve()
    assert r["dut.flow"] == pytest.approx(q, rel=1e-5)
    assert r["dut.hot_flow"] == pytest.approx(q_h, rel=1e-5)
    assert {"dut.back_siphonage", "dut.crossflow"} <= codes(r)


@pytest.mark.parametrize("p_cold", [-0.5, -0.2, 0.0, 0.2, 0.4, 0.6, 1.0, 3.0])
def test_back_siphonage_iff_reverse_discharge(p_cold: float) -> None:
    # Hot at -0.5 bar; the spout flows backward exactly when the hand-solved discharge is
    # below -0.01 L/min, and crossflow needs one inlet feeding the faucet.
    q, q_h, q_c = hand_general(-0.5, p_cold, 0.5)
    r = faucet_system(p_hot=-0.5, p_cold=p_cold).solve()
    assert ("dut.back_siphonage" in codes(r)) is (q < -0.01)
    crossflow = (q_h < -0.01 and q_c > 0.01) or (q_c < -0.01 and q_h > 0.01)
    assert ("dut.crossflow" in codes(r)) is crossflow


def test_closed_faucet_has_no_crossflow_warning() -> None:
    # Closed: only leakage paths (Kv 6e-7 m3/h each) join the supplies; the crossflow is
    # about 6e-7 / sqrt(2) * sqrt(4 / 0.9982) m3/h, far below the 0.01 L/min threshold.
    r = faucet_system(p_hot=1.0, p_cold=5.0, lift=0.0).solve()
    assert abs(r["dut.hot_flow"]) < 1e-4
    assert "dut.crossflow" not in codes(r)


# -- capped inlet and low supply pressure ----------------------------------------------------


def test_capped_hot_inlet_works_from_cold_alone() -> None:
    # Cold Kv 0.3 in series with the spout 0.8: Kv_eq = 0.24 / sqrt(0.73) = 0.280899 m3/h;
    # Q = 0.280899 * sqrt(3 / 0.9982) m3/h = 8.1162 L/min.
    # Mixing-chamber (and capped hot port) pressure: (Q / 0.8)**2 * SG = 0.3699 bar gauge,
    # below min_flow_pressure 0.5 bar, yet no low_supply_pressure: the port is capped.
    s = faucet_system(p_hot=None)
    assert "unconnected_port" in {i.code for i in s.check()}
    r = s.solve()
    kv_eq = 0.6 * 0.5 * 0.8 / math.sqrt((0.6 * 0.5) ** 2 + 0.8**2)
    q = kv_q_lmin(kv_eq, 3.0)
    assert r["dut.flow"] == pytest.approx(q, rel=1e-5)
    assert r["dut.hot_flow"] == pytest.approx(0.0, abs=1e-9)
    assert r["dut.temperature"] == pytest.approx(12.0, abs=1e-9)
    p_c = (q * 60 / 1000 / 0.8) ** 2 * SG
    assert r["dut.hot.p"] == pytest.approx(p_c, rel=1e-4)
    assert r["dut.hot.p"] < 0.5
    assert codes(r) == set()


def test_capped_cold_inlet_works_from_hot_alone() -> None:
    r = faucet_system(p_cold=None, mix=1.0).solve()
    kv_eq = 0.6 * 0.8 / 1.0
    assert r["dut.flow"] == pytest.approx(kv_q_lmin(kv_eq, 3.0), rel=1e-5)
    assert r["dut.temperature"] == pytest.approx(55.0, abs=1e-9)
    assert "dut.low_supply_pressure" not in codes(r)


@pytest.mark.parametrize(
    ("p_cold", "low"), [(0.2, True), (0.45, True), (0.55, False), (2.0, False)]
)
def test_low_supply_pressure_on_connected_inlet(p_cold: float, low: bool) -> None:
    r = faucet_system(p_hot=None, p_cold=p_cold).solve()
    assert ("dut.low_supply_pressure" in codes(r)) is low


def test_low_supply_pressure_needs_open_lever() -> None:
    s = faucet_system(p_hot=3.0, p_cold=0.2, lift=0.0)
    assert "dut.low_supply_pressure" not in codes(s.solve())
    s.set("dut.lift", 1.0)
    r = s.solve()
    assert "dut.low_supply_pressure" in codes(r)
    warning = next(w for w in r.warnings if w.code == "low_supply_pressure")
    assert "at cold 0.2 bar" in warning.message  # names only the low inlet
