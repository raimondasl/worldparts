"""Adversarial physics and numerics review of the core engine.

Every test in this module demonstrates a defect found in review: it fails against the current
implementation and states the expected physics and the source of the expected value.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from fluids.friction import Churchill_1977
from scipy.optimize import brentq

import worldparts as wp
from worldparts.errors import InvalidValueError
from worldparts.laws import PumpLaw, QuadraticResistance, flow_at_1bar, kv_to_k
from worldparts.media import MU, RHO
from worldparts.network import Branch, Injection, Network, Node
from worldparts.units import P_ATM

SG = RHO / 1000.0  # specific gravity used by the Kv definition


def _supply_valve_drain(pressure_bar: float, kv_m3h: float) -> wp.System:
    s = wp.System("review")
    s.add("src", "supply", pressure=pressure_bar)
    s.add("v", "valve", kv=kv_m3h)
    s.add("out", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


def _valve_with_actuator() -> wp.System:
    s = wp.System("lag")
    s.add("src", "supply", pressure=1.0)
    s.add("v", "valve", actuator_time="10 s", opening=0)
    s.add("out", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


# --- Kv law and regularisation ----------------------------------------------------------------


@pytest.mark.parametrize("fraction", [0.01, 0.0125])
def test_regularisation_distortion_below_0p1_percent_above_1_percent_of_nominal(
    fraction: float,
) -> None:
    """At 1 % or more of the nominal flow, regularisation should shift the flow by 0.1 % at most.

    Expected value: the ideal Kv law ``m = k * sqrt(dp)`` (IEC 60534 Kv definition), with
    ``dp = (fraction * m_1bar / k)**2``, where ``m_1bar`` is the flow at 1 bar.
    The review criterion is "distortion <= 0.1 % above 1 % of nominal flow". With
    ``m_eps = 1e-3 * m_1bar`` and ``dp = m * sqrt(m**2 + m_eps**2) / k**2``, the flow error is
    about ``-(m_eps / m)**2 / 4``, which is -0.25 % at 1 % of nominal flow. The 0.1 % limit is
    only met above about 1.6 % of nominal. Two possible fixes: a regularisation that is exactly
    quadratic for ``|m| > m_eps``, like Modelica's regSquare2 or a cubic blend, or a smaller
    ``m_eps``.
    """
    k = kv_to_k(2.5 / 3600.0)
    law = QuadraticResistance(k)
    m_ideal = fraction * flow_at_1bar(k)
    dp = (m_ideal / k) ** 2
    m = law.flow(dp)
    assert abs(m / m_ideal - 1.0) <= 1e-3


# --- boundary "ideal" joints -------------------------------------------------------------------


@pytest.mark.parametrize("kv_m3h", [1000.0, 100000.0])
def test_supply_port_pressure_equals_setting_for_large_valves(kv_m3h: float) -> None:
    """An ideal supply holds its port at the set pressure; a valve then passes Kv*sqrt(dp/SG).

    Expected values: the supply manifest says the port stays within 1e-6 bar of ``pressure``
    and its contract ``port-pressure-equals-setting`` uses abs_tol 1e-5 bar. The valve flow
    comes from the Kv definition, ``Q = kv * sqrt(3 bar / SG)`` in m3/h. Kv up to 1e5 m3/h is
    inside the valve's hard limits. Today each boundary is joined to its port by a
    "near-ideal" branch of Kv 1e5 m3/h. With a Kv 1e5 valve the circuit is then three equal
    resistances in series: the supply port sits at 2 bar instead of 3, and the flow is 42 %
    low. With Kv 1000 the port is still 3e-4 bar off.
    """
    s = _supply_valve_drain(3.0, kv_m3h)
    r = s.solve()
    q_expected = kv_m3h * math.sqrt(3.0 / SG) * 1000.0 / 60.0  # L/min
    assert r["src.port.p"] == pytest.approx(3.0, abs=1e-5)
    assert r["out.port.p"] == pytest.approx(0.0, abs=1e-5)
    assert r["v.volume_flow"] == pytest.approx(q_expected, rel=1e-4)


def test_large_pipe_flow_matches_independent_darcy_weisbach() -> None:
    """A DN1000, 1 km pipe under 1 bar should carry the Darcy-Weisbach/Churchill flow.

    Expected value: an independent root solve of
    ``dp = f(Re, e/D) * L/D * rho * v**2 / 2`` with ``fluids.friction.Churchill_1977``, for
    1 bar across the pipe. The pipe limits allow diameters up to 5000 mm. Today the supply
    and drain joints (Kv 1e5 m3/h each) take 0.014 bar each at this flow of about
    3.3 m3/s. The pipe then sees only 0.972 bar, and the flow is about 1.4 % low.
    """
    length, d, rough = 1000.0, 1.0, 0.045e-3
    area = math.pi * d * d / 4.0

    def residual(q: float) -> float:
        v = q / area
        f = Churchill_1977(RHO * v * d / MU, rough / d)
        return f * length / d * RHO * v * v / 2.0 - 1e5

    q_expected = brentq(residual, 1e-6, 100.0, xtol=1e-14) * 60000.0  # L/min

    s = wp.System("main")
    s.add("src", "supply", pressure=1.0)
    s.add("p", "pipe", length=length, diameter=1000, roughness=0.045)
    s.add("out", "drain")
    s.connect("src.port", "p.port_a")
    s.connect("p.port_b", "out.port")
    r = s.solve()
    assert r["p.pressure_drop"] == pytest.approx(1.0, abs=1e-5)
    assert r["p.volume_flow"] == pytest.approx(q_expected, rel=1e-4)


# --- temperature mixing ------------------------------------------------------------------------


def test_mixing_converges_in_recirculating_loop() -> None:
    """Node temperatures in a recirculating loop must satisfy the steady energy balance.

    A pump circulates about 7.5 kg/s around a two-node loop (l1 -> l2 -> l1). The loop
    receives 0.01 kg/s at 350 K at l1 and 0.01 kg/s at 280 K at l2, and 0.02 kg/s leave from
    l2 to a fixed node. Expected values: the exact solution of the two mixing equations
    ``(m_h + R) T1 = m_h Th + R T2`` and ``(M + m_c) T2 = M T1 + m_c Tc``. With M the pump
    flow and R the return flow, this gives T2 = 315 K, which equals the overall energy
    balance ``(m_h Th + m_c Tc) / (m_h + m_c)``. Today the Gauss-Seidel sweeps contract by
    about 0.997 per sweep and stop silently at the 500-sweep limit, 9 K off and with a 3 %
    energy imbalance. A heating circuit or recirculated treatment loop hits this as soon as
    pumps are added. The fix is to solve the linear mixing system directly, or to raise an
    error when the sweeps do not converge.
    """
    net = Network()
    out = net.add_node(Node("o", fixed=True, p=P_ATM, T=290.0))
    l1 = net.add_node(Node("l1"))
    l2 = net.add_node(Node("l2"))
    net.add_branch(Branch(l1, l2, PumpLaw(20.0, 0.0, -2e4), label="pump"))
    net.add_branch(Branch(l2, l1, QuadraticResistance(kv_to_k(20.0 / 3600.0)), label="ret"))
    net.add_branch(Branch(l2, out, QuadraticResistance(kv_to_k(2.0 / 3600.0)), label="drain"))
    m_h, m_c, t_h, t_c = 0.01, 0.01, 350.0, 280.0
    net.add_injection(Injection(l1, m_h, t_h))
    net.add_injection(Injection(l2, m_c, t_c))
    sol = net.solve()
    big_m, ret = float(sol.m[0]), float(sol.m[1])
    a = np.array([[m_h + ret, -ret], [-big_m, big_m + m_c]])
    t1, t2 = np.linalg.solve(a, np.array([m_h * t_h, m_c * t_c]))
    assert t2 == pytest.approx((m_h * t_h + m_c * t_c) / (m_h + m_c), abs=1e-6)
    assert sol.T[l1.index] == pytest.approx(t1, abs=1e-6)
    assert sol.T[l2.index] == pytest.approx(t2, abs=1e-6)


# --- time stepping -----------------------------------------------------------------------------


def test_actuator_lag_is_time_shift_invariant() -> None:
    """The actuator responds the same way to an event at 10 s as to one at 0 s.

    Expected values: the exact first-order response ``x(t) = 1 - exp(-(t - t_e) / tau)``
    for a command step at ``t_e``. The position at ``t_e`` is still 0. The valve manifest's
    ``actuator-lag`` scenario already expects 1 - exp(-3) = 0.950213 at 30 s after an event
    at 0 s. Today an event at t_e > 0 is applied and then followed by a full ``dt`` of lag
    update in the same step. The position therefore leads by one step: it reads 0.0952 at
    t_e and 1 - exp(-3.1) = 0.9550 at t_e + 30 s.
    """
    s = _valve_with_actuator()
    sim = s.simulate(
        duration=40, step=1, events=[{"at": 10, "set": {"v.opening": 1}}], variables=["v.position"]
    )
    pos = dict(zip(sim.time, sim["v.position"], strict=True))
    assert pos[10.0] == pytest.approx(0.0, abs=1e-12)
    assert pos[40.0] == pytest.approx(1.0 - math.exp(-3.0), abs=1e-6)


@pytest.mark.parametrize("step", [0.5, 1.0, 3.0, 7.5])
def test_exact_actuator_lag_does_not_depend_on_step(step: float) -> None:
    """``first_order`` is the exact lag solution, so the position must not depend on the step.

    Expected value: ``1 - exp(-(30 - 15) / 10) = 0.776870`` at t = 30 s for a command step at
    t = 15 s. Every step size here puts both 15 s and 30 s on the time grid. Today the
    position is 0.7878, 0.7981, 0.8347 and 0.8946 for steps of 0.5, 1, 3 and 7.5 s, because
    of the one-step lead after an event.
    """
    s = _valve_with_actuator()
    sim = s.simulate(
        duration=30,
        step=step,
        events=[{"at": 15, "set": {"v.opening": 1}}],
        variables=["v.position"],
    )
    assert sim.time[-1] == pytest.approx(30.0)
    assert sim["v.position"][-1] == pytest.approx(1.0 - math.exp(-1.5), abs=1e-6)


# --- pipe parameter validity -------------------------------------------------------------------


def test_pipe_flow_does_not_increase_with_roughness() -> None:
    """A rougher wall never passes more water, or the system rejects the geometry.

    Physics: at a fixed pressure difference, Darcy-Weisbach flow does not increase with wall
    roughness (Moody chart). The pipe's hard limits accept roughness up to 10 mm with a
    1 mm bore, so e/D can reach 10. Churchill (1977) is fitted only up to e/D of about 0.05
    and becomes singular near e/D = 3.7. For 1 cm of 1 mm pipe at 100 bar, today's flow is
    1.15 L/min at 5 mm roughness but 1.82 L/min at 10 mm, with friction factors above 3. The
    expected behaviour is either a monotone result or a rejection of roughness > diameter / 2
    by ``Pipe.check_parameters``.
    """

    def flow(roughness_mm: float) -> float | None:
        s = wp.System("rough")
        s.add("src", "supply", pressure=100)
        try:
            s.add("p", "pipe", length=0.01, diameter=1, roughness=roughness_mm)
        except InvalidValueError:
            return None
        s.add("out", "drain")
        s.connect("src.port", "p.port_a")
        s.connect("p.port_b", "out.port")
        return float(s.solve()["p.volume_flow"])

    q5, q10 = flow(5.0), flow(10.0)
    rejected = q5 is None or q10 is None
    assert rejected or q10 <= q5, f"flow {q10} L/min at 10 mm roughness > {q5} L/min at 5 mm"
