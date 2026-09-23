"""Tests for the branch laws: hand calculations, derivatives and monotonicity."""

from __future__ import annotations

import math

import numpy as np
import pytest
from fluids.friction import Churchill_1977

from worldparts.errors import InvalidValueError
from worldparts.laws import (
    GATE_STIFFNESS,
    CheckValveLaw,
    GateLaw,
    Law,
    LinearQuadraticResistance,
    PipeLaw,
    PumpLaw,
    QuadraticResistance,
    churchill_friction_factor,
    flow_at_1bar,
    ideal_connection,
    k_to_kv,
    kv_to_k,
)
from worldparts.media import MU, RHO, G

KV = 2.5 / 3600.0  # 2.5 m3/h in SI


def make_laws() -> dict[str, Law]:
    """One instance of every law, with realistic coefficients."""
    k = kv_to_k(KV)
    return {
        "quadratic": QuadraticResistance(k),
        "quadratic_fixed_eps": QuadraticResistance(k, m_eps=1e-3),
        "linear_quadratic": LinearQuadraticResistance(2e7, 5e9),
        "quadratic_only_lq": LinearQuadraticResistance(0.0, 5e9),
        "pipe_smooth": PipeLaw(5.0, 0.016, 1.5e-6),
        "pipe_rough_minor_head": PipeLaw(50.0, 0.1, 4.5e-5, minor_loss=3.0, height_difference=12.0),
        "pump": PumpLaw(34.0, -50.0, -8e5, speed=1.0),
        "pump_stopped": PumpLaw(34.0, -50.0, -8e5, speed=0.0),
        "check_valve": CheckValveLaw(k, 1e-6),
        "gate_open": GateLaw(k),
        "gate_forward_blocked": GateLaw(k, "forward"),
        "gate_reverse_blocked": GateLaw(k, "reverse"),
        "gate_reverse_capped": GateLaw(k, "reverse", limit=0.05),
        "gate_reverse_closed": GateLaw(k, "reverse", limit=0.0),
        "gate_forward_capped": GateLaw(k, "forward", limit=1e-4),
        "ideal": ideal_connection(),
    }


# Mass flows from -10 kg/s to 10 kg/s with dense sampling around zero.
M_GRID = np.unique(
    np.concatenate(
        [
            -np.logspace(-12, 1, 80),
            [0.0],
            np.logspace(-12, 1, 80),
        ]
    )
)


def test_kv_flow_at_1_bar_equals_kv() -> None:
    """Kv is the flow in m3/h at 1 bar with specific gravity relative to 1000 kg/m3."""
    for kv_m3h in (0.1, 2.5, 100.0):
        k = kv_to_k(kv_m3h / 3600.0)
        m = QuadraticResistance(k).flow(1e5)
        q_m3h = m / RHO * 3600.0
        assert q_m3h == pytest.approx(kv_m3h * math.sqrt(1000.0 / RHO), rel=1e-6)
        assert q_m3h == pytest.approx(kv_m3h, rel=1e-3)  # "Kv m3/h at 1 bar"
        assert k_to_kv(k) == pytest.approx(kv_m3h / 3600.0)
        assert flow_at_1bar(k) == pytest.approx(RHO * q_m3h / 3600.0, rel=1e-6)


def test_quadratic_away_from_regularisation() -> None:
    k = kv_to_k(KV)
    law = QuadraticResistance(k)
    m = 0.5
    dp, _ = law.dp(m)
    assert dp == pytest.approx((m / k) ** 2, rel=1e-5)
    assert law.dp(-m)[0] == pytest.approx(-dp)
    assert law.m_eps == pytest.approx(1e-3 * k * math.sqrt(1e5))


@pytest.mark.parametrize("name", list(make_laws()))
def test_derivative_matches_finite_difference(name: str) -> None:
    law = make_laws()[name]
    for m in [-7.3, -1.1, -0.2, -3e-3, -2e-5, 2e-5, 3e-3, 0.2, 1.1, 7.3]:
        h = 1e-6 * max(abs(m), 1e-6)
        dp, d = law.dp(m)
        fd = (law.dp(m + h)[0] - law.dp(m - h)[0]) / (2 * h)
        # round-off noise of the central difference (large constant terms such as static
        # head or pump shut-off head cancel)
        noise = 50 * np.finfo(float).eps * (abs(dp) + abs(law.dp(m + h)[0])) / h
        assert d == pytest.approx(fd, rel=2e-5, abs=noise + 1e-12), (name, m)


@pytest.mark.parametrize("name", list(make_laws()))
def test_strictly_monotone_including_zero(name: str) -> None:
    law = make_laws()[name]
    values = np.array([law.dp(float(m))[0] for m in M_GRID])
    derivs = np.array([law.dp(float(m))[1] for m in M_GRID])
    assert np.all(np.diff(values) > 0), name
    assert np.all(derivs > 0), name
    assert law.dp(0.0)[1] > 0


def test_asymmetric_laws_are_continuous_at_zero() -> None:
    for law in (CheckValveLaw(kv_to_k(KV), 1e-6), GateLaw(kv_to_k(KV), "forward")):
        assert law.dp(0.0)[0] == 0.0
        assert abs(law.dp(1e-15)[0]) < 1e-6
        assert abs(law.dp(-1e-15)[0]) < 1e-6


def test_check_valve_leakage() -> None:
    k = kv_to_k(KV)
    law = CheckValveLaw(k, 1e-4)
    m_fwd = law.flow(1e5)
    m_rev = law.flow(-1e5)
    assert m_fwd == pytest.approx(k * math.sqrt(1e5), rel=1e-5)
    assert m_rev == pytest.approx(-1e-4 * k * math.sqrt(1e5), rel=1e-5)


def test_gate_direction_can_be_switched() -> None:
    k = kv_to_k(KV)
    gate = GateLaw(k)
    open_flow = gate.flow(1e4)
    gate.blocked_direction = "forward"
    assert gate.flow(1e4) == pytest.approx(open_flow * 1e-6, rel=1e-4)
    assert gate.flow(-1e4) == pytest.approx(-open_flow, rel=1e-6)
    gate.blocked_direction = "reverse"
    assert gate.flow(1e4) == pytest.approx(open_flow, rel=1e-6)
    gate.blocked_direction = None
    assert gate.flow(-1e4) == pytest.approx(-open_flow, rel=1e-6)
    with pytest.raises(InvalidValueError):
        gate.blocked_direction = "sideways"


def test_gate_limit_caps_the_blocked_direction() -> None:
    """Core change for the tank (design 8.9): a capped gate follows the open law up to
    ``limit`` in its blocked direction and closes steeply beyond it.

    Hand calculation: with k for Kv 2.5 m3/h, the open law passes k sqrt(1e4) = 0.0695 kg/s
    at 0.1 bar. Capped at 0.01 kg/s, the flow at dp is 0.01 + dp_excess / GATE_STIFFNESS with
    dp_excess = dp - (0.01 / k)**2 (the open drop at the cap), under 1e-5 kg/s past the cap
    at 1 bar. The other direction is untouched; limit 0 closes the gate at zero flow, and
    limit None is the old leakage-only gate.
    """
    k = kv_to_k(KV)
    gate = GateLaw(k, "reverse", limit=0.01)
    open_flow = GateLaw(k).flow(1e4)
    assert gate.flow(1e4) == pytest.approx(open_flow, rel=1e-9)  # forward: open
    assert gate.flow(-10.0) == pytest.approx(GateLaw(k).flow(-10.0), rel=1e-9)  # under the cap
    for dp in (1e4, 1e5, 1e6):
        m = gate.flow(-dp)
        past = -m - 0.01
        expected = (dp - (0.01 / k) ** 2) / GATE_STIFFNESS
        assert past == pytest.approx(expected, rel=1e-3, abs=1e-8)
        assert past < 1.001 * dp / GATE_STIFFNESS + 1e-7  # 1e-5 kg/s per bar
    gate.blocked_direction = None  # unblocked: the cap does not apply
    assert gate.flow(-1e4) == pytest.approx(-open_flow, rel=1e-9)
    gate.blocked_direction = "reverse"
    gate.limit = 0.0  # closed at zero flow: only the stiff term, 1e-5 kg/s per bar
    assert gate.flow(-1e5) == pytest.approx(-1e5 / GATE_STIFFNESS, rel=1e-3)
    gate.limit = None  # leakage only, as before
    assert gate.flow(-1e4) == pytest.approx(-open_flow * 1e-6, rel=1e-4)


@pytest.mark.parametrize("rel_rough", [0.0, 1e-6, 1e-4, 1e-3, 1e-2, 5e-2])
@pytest.mark.parametrize(
    "re",
    [
        # laminar
        1e-3, 1.0, 50.0, 500.0, 1500.0, 2000.0,
        # transitional
        2300.0, 2800.0, 3500.0, 4000.0,
        # turbulent
        6000.0, 1e4, 5e4, 1e5, 1e6, 1e7, 1e8,
    ],
)  # fmt: skip
def test_churchill_matches_fluids(re: float, rel_rough: float) -> None:
    assert churchill_friction_factor(re, rel_rough) == pytest.approx(
        Churchill_1977(re, rel_rough), rel=1e-10
    )


def test_churchill_laminar_limit() -> None:
    for re in (1e-6, 1.0, 100.0, 1000.0):
        assert churchill_friction_factor(re) == pytest.approx(64.0 / re, rel=1e-3)
    with pytest.raises(InvalidValueError):
        churchill_friction_factor(0.0)


def test_pipe_hagen_poiseuille() -> None:
    length, d = 10.0, 0.016
    law = PipeLaw(length, d)
    q = 1e-6  # m3/s, Re ~ 80
    dp, _ = law.dp(RHO * q)
    assert dp == pytest.approx(128 * MU * length * q / (math.pi * d**4), rel=1e-6)
    assert law.dp(0.0)[1] == pytest.approx(
        64 * length * MU / (2 * d**2) / (RHO * math.pi * d**2 / 4), rel=1e-12
    )


def test_pipe_turbulent_darcy_weisbach_against_fluids() -> None:
    length, d, eps = 30.0, 0.05, 4.5e-5
    law = PipeLaw(length, d, eps, minor_loss=2.0, height_difference=-3.0)
    m = 4.0
    v = m / (RHO * math.pi * d**2 / 4)
    re = RHO * v * d / MU
    f = Churchill_1977(re, eps / d)
    expected = f * length / d * RHO * v**2 / 2 + 2.0 * RHO * v**2 / 2 + RHO * G * -3.0
    assert law.dp(m)[0] == pytest.approx(expected, rel=1e-10)
    assert law.friction_factor(m) == pytest.approx(f, rel=1e-10)
    assert law.reynolds(-m) == pytest.approx(re)
    assert law.friction_factor(0.0) is None


def test_pipe_static_head_at_zero_flow() -> None:
    law = PipeLaw(12.0, 0.02, height_difference=10.0)
    assert law.dp(0.0)[0] == pytest.approx(RHO * G * 10.0)


def test_pump_law_head_and_resistance_when_stopped() -> None:
    pump = PumpLaw(30.0, -100.0, -1e6, speed=1.0)
    q = 0.005
    assert pump.head(RHO * q) == pytest.approx(30 - 0.5 - 25)
    dp, _ = pump.dp(RHO * q)
    assert dp == pytest.approx(-RHO * G * (30 - 0.5 - 25) + RHO * q, rel=1e-12)
    pump.speed = 0.0
    assert pump.dp(RHO * q)[0] > 0  # a stopped pump resists flow
    with pytest.raises(InvalidValueError):
        PumpLaw(30.0, 1.0, -1.0).dp(0.1)
    with pytest.raises(InvalidValueError):
        PumpLaw(30.0, -1.0, 0.0).dp(0.1)


def test_linear_quadratic_resistance() -> None:
    law = LinearQuadraticResistance(1e7, 4e9)
    q = 0.002
    assert law.dp(RHO * q)[0] == pytest.approx(1e7 * q + 4e9 * q * q, rel=1e-6)


def test_invalid_coefficients() -> None:
    with pytest.raises(InvalidValueError):
        QuadraticResistance(0.0).dp(1.0)
    with pytest.raises(InvalidValueError):
        CheckValveLaw(0.0).dp(1.0)


def test_law_inverse() -> None:
    law = PipeLaw(5.0, 0.016, 1.5e-6, height_difference=2.0)
    for dp in (-5e5, -1e3, 0.0, 1e3, 5e5):
        m = law.flow(dp)
        assert law.dp(m)[0] == pytest.approx(dp, abs=1e-6 * max(1.0, abs(dp)))


# -- regularisation band and ideal joint (review regressions) --------------------------------


def test_quadratic_is_exact_outside_regularisation_band() -> None:
    """Design 5.2's m_eps keeps the zero-flow slope; outside 2 * m_eps the law is exact."""
    k = kv_to_k(KV)
    law = QuadraticResistance(k)
    eps = law.m_eps
    assert law.dp(0.0)[1] == pytest.approx(eps / k**2, rel=1e-12)
    for m in (2 * eps, 2.5 * eps, 0.01 * flow_at_1bar(k), -3 * eps):
        dp, d = law.dp(m)
        assert dp == pytest.approx(m * abs(m) / k**2, rel=1e-14)
        assert d == pytest.approx(2 * abs(m) / k**2, rel=1e-14)
    below, above = law.dp(2 * eps * (1 - 1e-12)), law.dp(2 * eps * (1 + 1e-12))
    assert below[0] == pytest.approx(above[0], rel=1e-9)
    assert below[1] == pytest.approx(above[1], rel=1e-9)


def test_ideal_joint_is_negligible_up_to_100_m3_per_s() -> None:
    law = ideal_connection()
    assert law.ideal
    assert abs(law.dp(1e5)[0]) <= 0.1 * (1 + 1e-12)  # at most 1e-6 bar at 1e5 kg/s
