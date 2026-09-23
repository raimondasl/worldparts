"""Tests for the primitive network and the steady solver."""

from __future__ import annotations

import math
import random

import numpy as np
import pytest
from scipy.optimize import fsolve

from worldparts import network as network_module
from worldparts.errors import SolverError
from worldparts.laws import (
    CheckValveLaw,
    GateLaw,
    Law,
    LinearQuadraticResistance,
    PipeLaw,
    PumpLaw,
    QuadraticResistance,
    kv_to_k,
)
from worldparts.media import CP, RHO, G
from worldparts.network import Branch, Injection, Network, Node
from worldparts.units import P_ATM

BAR = 1e5


def kv_si(kv_m3h: float) -> float:
    return kv_m3h / 3600.0


def q_m3h(m: float) -> float:
    return m / RHO * 3600.0


def two_node_net(
    *laws: Law, p_in: float = BAR, series: bool = True
) -> tuple[Network, list[Branch]]:
    """Fixed source at gauge ``p_in`` and sink at atmosphere with ``laws`` in series or parallel."""
    net = Network()
    src = net.add_node(Node("src", True, P_ATM + p_in, 285.15))
    dst = net.add_node(Node("dst", True, P_ATM, 293.15))
    branches = []
    if series:
        prev = src
        for i, law in enumerate(laws):
            nxt = dst if i == len(laws) - 1 else net.add_node(Node(f"n{i}"))
            branches.append(net.add_branch(Branch(prev, nxt, law, label=f"b{i}")))
            prev = nxt
    else:
        for i, law in enumerate(laws):
            branches.append(net.add_branch(Branch(src, dst, law, label=f"b{i}")))
    return net, branches


def test_single_kv_at_1_bar() -> None:
    net, (br,) = two_node_net(QuadraticResistance(kv_to_k(kv_si(2.5))))
    sol = net.solve()
    assert sol.converged
    assert q_m3h(sol.m[br.index]) == pytest.approx(2.5 * math.sqrt(1000 / RHO), rel=1e-6)


def test_series_kv_combination() -> None:
    kvs = [2.5, 4.0, 1.6]
    net, branches = two_node_net(
        *(QuadraticResistance(kv_to_k(kv_si(k))) for k in kvs), p_in=3 * BAR
    )
    sol = net.solve()
    kv_total = 1.0 / math.sqrt(sum(1.0 / k**2 for k in kvs))
    expected = kv_total * math.sqrt(3 * 1000 / RHO)
    for br in branches:
        assert q_m3h(sol.m[br.index]) == pytest.approx(expected, rel=1e-6)
    # intermediate pressures: each drop follows its own Kv
    q = expected
    p = 3.0
    for i, k in enumerate(kvs[:-1]):
        p -= (q / k) ** 2 * RHO / 1000
        assert (sol.p[net.nodes[2 + i].index] - P_ATM) / BAR == pytest.approx(p, rel=1e-6)


def test_parallel_kv_combination() -> None:
    kvs = [2.5, 4.0, 1.6]
    net, branches = two_node_net(
        *(QuadraticResistance(kv_to_k(kv_si(k))) for k in kvs), p_in=2 * BAR, series=False
    )
    sol = net.solve()
    total = sum(q_m3h(sol.m[b.index]) for b in branches)
    assert total == pytest.approx(sum(kvs) * math.sqrt(2 * 1000 / RHO), rel=1e-6)


def test_loop_network_matches_independent_solution() -> None:
    """A Wheatstone bridge (a loop with a cross branch) against scipy on the node equations."""
    net = Network()
    s = net.add_node(Node("s", True, P_ATM + 3 * BAR, 290.0))
    d = net.add_node(Node("d", True, P_ATM, 290.0))
    a = net.add_node(Node("a"))
    b = net.add_node(Node("b"))
    kvs = {"s-a": 3.0, "s-b": 1.0, "a-b": 2.0, "a-d": 1.2, "b-d": 4.0}
    nodes = {"s": s, "d": d, "a": a, "b": b}
    brs = {}
    for key, kv in kvs.items():
        u, v = key.split("-")
        brs[key] = net.add_branch(
            Branch(nodes[u], nodes[v], QuadraticResistance(kv_to_k(kv_si(kv))))
        )
    sol = net.solve()

    def flow(kv: float, dp: float) -> float:
        return math.copysign(kv_to_k(kv_si(kv)) * math.sqrt(abs(dp)), dp)

    def residual(x: np.ndarray) -> list[float]:
        p = {"s": P_ATM + 3 * BAR, "d": P_ATM, "a": x[0] * BAR, "b": x[1] * BAR}
        f = {k: flow(kv, p[k[0]] - p[k[2]]) for k, kv in kvs.items()}
        return [f["s-a"] - f["a-b"] - f["a-d"], f["s-b"] + f["a-b"] - f["b-d"]]

    pa, pb = fsolve(residual, [2.5, 1.5], xtol=1e-13)
    assert sol.p[a.index] / BAR == pytest.approx(pa, rel=1e-6)
    assert sol.p[b.index] / BAR == pytest.approx(pb, rel=1e-6)
    # Kirchhoff: mass balance at every free node
    for n in (a, b):
        bal = sum(sol.m[br.index] for br in brs.values() if br.b is n) - sum(
            sol.m[br.index] for br in brs.values() if br.a is n
        )
        assert abs(bal) < 1e-9


def test_closed_valve_passes_only_leakage() -> None:
    k_open = kv_to_k(kv_si(2.5))
    net, (br,) = two_node_net(QuadraticResistance(k_open * 1e-4), p_in=3 * BAR)
    sol = net.solve()
    expected = 1e-4 * k_open * math.sqrt(3 * BAR)
    assert sol.m[br.index] == pytest.approx(expected, rel=1e-5)


def test_check_valve_reverse_flow_is_leakage() -> None:
    k = kv_to_k(kv_si(3.0))
    net, (br,) = two_node_net(CheckValveLaw(k, 1e-6), p_in=-0.5 * BAR)
    sol = net.solve()
    assert sol.m[br.index] == pytest.approx(-1e-6 * k * math.sqrt(0.5 * BAR), rel=1e-4)


def test_no_pressure_reference_is_detected() -> None:
    net = Network()
    a = net.add_node(Node("a"))
    b = net.add_node(Node("b"))
    net.add_branch(Branch(a, b, QuadraticResistance(1.0)))
    ref = net.add_node(Node("ref", True, P_ATM, 293.15))
    c = net.add_node(Node("c"))
    net.add_branch(Branch(ref, c, QuadraticResistance(1.0)))
    bad = net.unreferenced_subnetworks()
    assert [sorted(n.label for n in g) for g in bad] == [["a", "b"]]
    with pytest.raises(SolverError, match="No pressure reference"):
        net.solve()


def test_mixing_junction_energy_balance() -> None:
    net = Network()
    hot = net.add_node(Node("hot", True, P_ATM + 3 * BAR, 273.15 + 60))
    cold = net.add_node(Node("cold", True, P_ATM + 2 * BAR, 273.15 + 10))
    out = net.add_node(Node("out", True, P_ATM, 273.15 + 20))
    mix = net.add_node(Node("mix"))
    bh = net.add_branch(Branch(hot, mix, QuadraticResistance(kv_to_k(kv_si(1.0)))))
    bc = net.add_branch(Branch(cold, mix, QuadraticResistance(kv_to_k(kv_si(1.5)))))
    bo = net.add_branch(Branch(mix, out, QuadraticResistance(kv_to_k(kv_si(2.0)))))
    sol = net.solve()
    mh, mc, mo = sol.m[bh.index], sol.m[bc.index], sol.m[bo.index]
    assert mh > 0 and mc > 0
    assert mh + mc == pytest.approx(mo, rel=1e-12)
    t_mix = sol.T[mix.index]
    assert t_mix is not None
    # energy in = energy out
    assert mh * CP * (273.15 + 60) + mc * CP * (273.15 + 10) == pytest.approx(mo * CP * t_mix)


def test_thermal_map_and_undefined_temperature() -> None:
    net = Network()
    src = net.add_node(Node("src", True, P_ATM + BAR, 283.15))
    dst = net.add_node(Node("dst", True, P_ATM, 293.15))
    a = net.add_node(Node("a"))
    dead = net.add_node(Node("dead"))
    heater_power = 5000.0

    def heat(t_in: float, m: float) -> float:
        return t_in + heater_power / (m * CP) if m > 0 else t_in

    k = kv_to_k(kv_si(1.0))
    b1 = net.add_branch(Branch(src, a, QuadraticResistance(k), thermal=heat))
    net.add_branch(Branch(a, dst, QuadraticResistance(k)))
    net.add_branch(Branch(a, dead, QuadraticResistance(k)))  # dead end: no inflow
    sol = net.solve()
    m = sol.m[b1.index]
    assert sol.T[a.index] == pytest.approx(283.15 + heater_power / (m * CP))
    assert sol.T[dead.index] is None


def test_injection_mass_and_temperature() -> None:
    net = Network()
    ref = net.add_node(Node("ref", True, P_ATM, 293.15))
    a = net.add_node(Node("a"))
    br = net.add_branch(Branch(a, ref, QuadraticResistance(kv_to_k(kv_si(1.0)))))
    net.add_injection(Injection(a, 0.2, 330.0))
    sol = net.solve()
    assert sol.m[br.index] == pytest.approx(0.2)
    assert sol.T[a.index] == pytest.approx(330.0)


def test_pump_operating_point_intersection_of_quadratics() -> None:
    """Pump H = a + bQ + cQ|Q| against a Kv resistance: solve the quadratic by hand."""
    a, b, c = 34.0, -150.0, -6e4  # SI (m, m per m3/s, m per (m3/s)^2)
    k = kv_to_k(kv_si(20.0))
    net = Network()
    tank = net.add_node(Node("tank", True, P_ATM, 293.15))
    out = net.add_node(Node("out", True, P_ATM, 293.15))
    mid = net.add_node(Node("mid"))
    pump = net.add_branch(Branch(tank, mid, PumpLaw(a, b, c, speed=1.0, eps=1e-9)))
    net.add_branch(Branch(mid, out, QuadraticResistance(k)))
    sol = net.solve()
    # rho g (a + bQ + cQ^2) = (rho Q / k)^2  ->  (c rho g - rho^2/k^2) Q^2 + b rho g Q + a rho g = 0
    qa = c * RHO * G - RHO**2 / k**2
    qb = b * RHO * G
    qc = a * RHO * G
    q = (-qb - math.sqrt(qb * qb - 4 * qa * qc)) / (2 * qa)
    assert sol.m[pump.index] / RHO == pytest.approx(q, rel=1e-6)


def test_fixed_node_pressure_can_change_between_solves() -> None:
    net, (br,) = two_node_net(QuadraticResistance(kv_to_k(kv_si(1.0))))
    first = net.solve()
    net.nodes[0].p = P_ATM + 4 * BAR
    second = net.solve(first.x)
    assert second.m[br.index] == pytest.approx(2 * first.m[br.index], rel=1e-6)
    assert second.iterations <= first.iterations + 5


def test_fallback_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    net, (br, _) = two_node_net(PipeLaw(20.0, 0.02, 1e-5), QuadraticResistance(kv_to_k(kv_si(3.0))))
    ref = net.solve()
    assert ref.method == "newton"
    sol = net.solve(max_iter=1)  # branch-flow Newton cut short -> node-pressure Newton
    assert sol.converged and sol.method == "node-newton"
    assert sol.m[br.index] == pytest.approx(ref.m[br.index], rel=1e-8)
    monkeypatch.setattr(
        network_module._Problem,
        "node_newton",
        lambda self, x0, tol, max_iter=200: (x0, 1, 1.0, False),
    )
    sol = net.solve(max_iter=1)  # ... and then scipy's hybr
    assert sol.converged and sol.method == "hybr"
    assert sol.m[br.index] == pytest.approx(ref.m[br.index], rel=1e-8)


def test_stiff_network_needs_the_node_pressure_stage() -> None:
    """A large pipe started from zero flow makes the branch-flow Newton step explode; the
    convex node-pressure stage must still converge (regression for a fuzzing failure)."""
    net = Network()
    src = net.add_node(Node("src", True, P_ATM + 9.8 * BAR, 290.0))
    dst = net.add_node(Node("dst", True, P_ATM, 290.0))
    a = net.add_node(Node("a"))
    b = net.add_node(Node("b"))
    net.add_branch(Branch(dst, a, QuadraticResistance(kv_to_k(kv_si(1e5)))))
    big = net.add_branch(Branch(a, b, PipeLaw(164.0, 0.206, 1.5e-6, height_difference=13.6)))
    net.add_branch(Branch(b, src, QuadraticResistance(kv_to_k(kv_si(1e5)))))
    net.add_branch(Branch(b, dst, CheckValveLaw(kv_to_k(kv_si(80.0)), 1.4e-9)))
    sol = net.solve()
    assert sol.converged and sol.max_residual < 1e-9
    law = net.branches[big.index].law
    assert law.dp(sol.m[big.index])[0] == pytest.approx(sol.p[a.index] - sol.p[b.index], abs=1e-3)


def test_random_networks_always_converge() -> None:
    """Seeded fuzz over every law type, including closed and blocked elements and pumps."""
    rng = random.Random(20260923)

    def law() -> Law:
        t = rng.random()
        k = kv_to_k(10 ** rng.uniform(-4, 4) / 3600)
        if t < 0.3:
            return QuadraticResistance(k)
        if t < 0.45:
            return CheckValveLaw(k, 10 ** rng.uniform(-9, -1))
        if t < 0.6:
            return GateLaw(k, rng.choice([None, "forward", "reverse"]), 10 ** rng.uniform(-9, -3))
        if t < 0.8:
            return PipeLaw(
                10 ** rng.uniform(-1, 3.5),
                10 ** rng.uniform(-3, 0.3),
                10 ** rng.uniform(-7, -3),
                rng.choice([0, 3]),
                rng.uniform(-50, 50),
            )
        if t < 0.9:
            return LinearQuadraticResistance(10 ** rng.uniform(4, 9), 10 ** rng.uniform(6, 12))
        return PumpLaw(
            rng.uniform(5, 80),
            -rng.uniform(0, 500),
            -(10 ** rng.uniform(3, 7)),
            speed=rng.choice([0, 0.5, 1, 1.2]),
        )

    for _ in range(300):
        net = Network()
        fixed = [
            net.add_node(Node(f"F{i}", True, P_ATM + rng.uniform(-0.9e5, 10e5), 290.0))
            for i in range(rng.randint(1, 3))
        ]
        free = [net.add_node(Node(f"n{i}")) for i in range(rng.randint(1, 10))]
        connected = list(fixed)
        for n in free:
            other = rng.choice(connected)
            pair = (other, n) if rng.random() < 0.5 else (n, other)
            net.add_branch(Branch(*pair, law()))
            connected.append(n)
        for _ in range(rng.randint(0, len(free) + 2)):
            u, v = rng.sample(fixed + free, 2)
            if not (u.fixed and v.fixed):
                net.add_branch(Branch(u, v, law()))
        sol = net.solve()
        assert sol.converged and sol.max_residual < 1e-9


class _BrokenLaw(Law):
    def dp(self, m: float) -> tuple[float, float]:
        return float("nan"), 1.0


def test_solver_error_names_worst_equation() -> None:
    net, _ = two_node_net(_BrokenLaw())
    with pytest.raises(SolverError) as exc:
        net.solve()
    assert "b0" in str(exc.value)
    assert exc.value.worst


def test_large_network_converges() -> None:
    """A ladder of 40 pipes with cross connections solves quickly."""
    net = Network()
    src = net.add_node(Node("src", True, P_ATM + 4 * BAR, 290.0))
    dst = net.add_node(Node("dst", True, P_ATM, 290.0))
    top = [net.add_node(Node(f"t{i}")) for i in range(20)]
    bot = [net.add_node(Node(f"b{i}")) for i in range(20)]
    net.add_branch(Branch(src, top[0], PipeLaw(5, 0.05)))
    net.add_branch(Branch(src, bot[0], PipeLaw(7, 0.04)))
    for i in range(19):
        net.add_branch(Branch(top[i], top[i + 1], PipeLaw(10, 0.05)))
        net.add_branch(Branch(bot[i], bot[i + 1], PipeLaw(10, 0.03)))
        net.add_branch(Branch(top[i], bot[i], PipeLaw(3, 0.02, height_difference=1.0)))
    net.add_branch(Branch(top[-1], dst, PipeLaw(5, 0.05)))
    net.add_branch(Branch(bot[-1], dst, PipeLaw(5, 0.05)))
    sol = net.solve()
    assert sol.converged and sol.max_residual < 1e-9
    assert all(t is not None for t in sol.T)


# -- temperature mixing with recirculation (review regression) -------------------------------


def _loop(thermal: object = None) -> tuple[Network, Node, Node]:
    net = Network()
    out = net.add_node(Node("o", fixed=True, p=P_ATM, T=290.0))
    l1 = net.add_node(Node("l1"))
    l2 = net.add_node(Node("l2"))
    net.add_branch(Branch(l1, l2, PumpLaw(20.0, 0.0, -2e4), label="pump"))
    net.add_branch(
        Branch(l2, l1, QuadraticResistance(kv_to_k(kv_si(20.0))), thermal=thermal, label="ret")
    )
    net.add_branch(Branch(l2, out, QuadraticResistance(kv_to_k(kv_si(2.0))), label="drain"))
    net.add_injection(Injection(l1, 0.01, 350.0))
    net.add_injection(Injection(l2, 0.01, 280.0))
    return net, l1, l2


def test_mixing_loop_with_heater_satisfies_energy_balance() -> None:
    """A heater on the recirculating branch adds its power to the outflow exactly."""
    power = 500.0

    def heat(t_in: float, m: float) -> float:
        return t_in + power / (m * CP) if m > 0 else t_in

    net, _, l2 = _loop(heat)
    sol = net.solve()
    expected = (0.01 * 350.0 + 0.01 * 280.0 + power / CP) / 0.02
    assert sol.T[l2.index] == pytest.approx(expected, abs=1e-6)


def test_mixing_loop_with_saturating_heater_solves_the_mixing_equations() -> None:
    """A piecewise-affine thermal map (heater limited by a setpoint) inside a loop."""
    setpoint = 320.0

    def heat(t_in: float, m: float) -> float:
        return min(setpoint, t_in + 5e4 / (m * CP)) if m > 0 else t_in

    net, l1, l2 = _loop(heat)
    sol = net.solve()
    big_m, ret = float(sol.m[0]), float(sol.m[1])
    t1, t2 = sol.T[l1.index], sol.T[l2.index]
    assert t1 is not None and t2 is not None
    assert (0.01 + ret) * t1 == pytest.approx(0.01 * 350.0 + ret * heat(t2, ret), rel=1e-12)
    assert (big_m + 0.01) * t2 == pytest.approx(big_m * t1 + 0.01 * 280.0, rel=1e-12)


def test_mixing_in_a_loop_without_temperature_source_is_undefined() -> None:
    """A loop no temperature source reaches reports None, not an arbitrary value."""
    net = Network()
    ref = net.add_node(Node("ref", fixed=True, p=P_ATM, T=None))
    a = net.add_node(Node("a"))
    b = net.add_node(Node("b"))
    net.add_branch(Branch(a, b, PumpLaw(20.0, 0.0, -2e4)))
    net.add_branch(Branch(b, a, QuadraticResistance(kv_to_k(kv_si(20.0)))))
    net.add_branch(Branch(a, ref, QuadraticResistance(kv_to_k(kv_si(1.0)))))
    sol = net.solve()
    assert sol.T[a.index] is None and sol.T[b.index] is None
