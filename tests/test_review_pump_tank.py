"""Independent review of the centrifugal pump (design 8.8) and the tank (design 8.9).

Every expected value here comes from a hand calculation written out in the docstring, not
from running the component. Tests that document defects found in the review are marked in
their docstrings with "DEFECT" and are expected to fail until the defect is fixed.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np
import pytest
from scipy.optimize import lsq_linear

import worldparts as wp
from worldparts.components.tank import Tank
from worldparts.contracts import run_component, run_contract
from worldparts.errors import InvalidValueError
from worldparts.laws import kv_to_k
from worldparts.media import RHO, G, vapour_pressure
from worldparts.units import P_ATM

H_PER_S = 3600.0  # m3/h per m3/s
Q_TAB = np.array([0.0, 10.0, 20.0, 30.0, 36.0])  # default head curve, m3/h
H_TAB = np.array([34.0, 32.5, 28.5, 21.5, 16.0])  # m


def _bounded_head_fit() -> tuple[float, float, float]:
    """Independent bounded fit in unscaled SI with scipy's trust-region solver."""
    q = Q_TAB / H_PER_S
    design = np.vstack([np.ones_like(q), q, q * q]).T
    res = lsq_linear(
        design, H_TAB, bounds=([0.0, -np.inf, -np.inf], [np.inf, 0.0, -1e-9]), tol=1e-14
    )
    a, b, c = (float(x) for x in res.x)
    return a, b, c


def _pump_circuit(kv: float = 15.0, speed: float = 1.0, **params: object) -> wp.System:
    """Supply (0 bar gauge) -> pump -> valve (Kv, fully open) -> drain."""
    s = wp.System("review-pump")
    s.add("src", "supply", pressure=0)
    s.add("dut", "centrifugal_pump", parameters=dict(params), inputs={"speed": speed})
    s.add("v", "valve", parameters={"kv": kv})
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def _stopped_pump(pressure_bar: float, **params: object) -> wp.System:
    s = wp.System("review-stopped")
    s.add("src", "supply", pressure=pressure_bar)
    s.add("dut", "centrifugal_pump", parameters=dict(params), inputs={"speed": 0})
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    return s


def _drain_tank(diameter: float, level: float, kv: float, port_kv: float = 200.0) -> wp.System:
    s = wp.System("review-drain")
    s.add(
        "dut",
        "tank",
        diameter=diameter,
        height=max(3.0, level),
        initial_level=level,
        port_kv=port_kv,
    )
    s.add("v", "valve", kv=kv)
    s.add("sink", "drain")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


# ---------------------------------------------------------------------------------------
# Pump: independent verification (expected to pass)
# ---------------------------------------------------------------------------------------


def test_pump_fit_matches_independent_bounded_least_squares() -> None:
    """The fitted a, b, c and RMS equal an independent scipy ``trf`` bounded fit.

    Independent fit (unscaled SI, bounds a >= 0, b <= 0, c <= -1e-9):
    a = 33.970527 m, b = 0 (bound active), c = -179558.66 m/(m3/s)**2,
    RMS 0.0518003 m, largest point deviation 0.08504 m (< 1 m).
    """
    a, b, c = _bounded_head_fit()
    s = _pump_circuit()
    fit = s.component("dut").fit  # type: ignore[attr-defined]
    fa, fb, fc = fit.head
    assert fa == pytest.approx(a, rel=1e-8)
    assert fb == pytest.approx(b, abs=1e-6)
    assert fc == pytest.approx(c, rel=1e-8)
    fitted = fa + fb * Q_TAB / H_PER_S + fc * (Q_TAB / H_PER_S) ** 2
    assert np.max(np.abs(fitted - H_TAB)) < 1.0
    rms = float(np.sqrt(np.mean((fitted - H_TAB) ** 2)))
    assert s.solve()["dut.curve_fit_rms"] == pytest.approx(rms, rel=1e-9)


@pytest.mark.parametrize("kv", [3.0, 8.0, 40.0])
@pytest.mark.parametrize("speed", [0.5, 0.8, 1.2])
def test_pump_operating_point_closed_form(kv: float, speed: float) -> None:
    """Pump curve meets valve curve: a s**2 + b s Q + c Q**2 = Q**2 1e5 / (1000 g Kv**2).

    With C = c - 1e5 / (1000 g Kv_SI**2): Q = (-b s - sqrt((b s)**2 - 4 C a s**2)) / (2 C).
    The solver agrees within 0.5 % (the 1 Pa per kg/s law term makes it ~1e-5).
    """
    a, b, c = _bounded_head_fit()
    kv_si = kv / H_PER_S
    cc = c - 1e5 / (1000.0 * G * kv_si**2)
    q = (-b * speed - math.sqrt((b * speed) ** 2 - 4.0 * cc * a * speed**2)) / (2.0 * cc)
    r = _pump_circuit(kv=kv, speed=speed).solve()
    assert r.converged
    assert r["dut.volume_flow"] == pytest.approx(q * H_PER_S, rel=0.005)


def test_pump_bep_power_efficiency_consistent() -> None:
    """At the BEP, efficiency = rho g Q H / P_shaft, and it is invariant under affinity.

    Dense grid of eta0(Q) = rho g Q H0(Q) / P0(Q) with the independent head fit and a
    plain numpy quadratic power fit: Q_bep = 24.111 m3/h, eta_bep = 62.844 %. A valve with
    Kv = Q_bep sqrt(1e5 / (1000 g H0(Q_bep))) puts the operating point there.
    """
    a, _, c = _bounded_head_fit()
    pq = np.array([0.0, 10.0, 20.0, 30.0, 36.0]) / H_PER_S
    pw = np.array([1.5, 2.0, 2.55, 2.95, 3.15]) * 1e3
    p2, p1, p0 = np.polyfit(pq, pw, 2)
    qs = np.linspace(0.0, 36.0, 200001) / H_PER_S
    eta = RHO * G * qs * (a + c * qs**2) / (p0 + p1 * qs + p2 * qs**2)
    k = int(np.argmax(eta))
    q_bep, eta_bep = qs[k], eta[k]
    assert q_bep * H_PER_S == pytest.approx(24.111, abs=0.002)
    kv = q_bep * math.sqrt(1e5 / (1000.0 * G * (a + c * q_bep**2))) * H_PER_S
    for speed in (1.0, 0.7):
        s = _pump_circuit(kv=kv, speed=speed)
        r = s.solve()
        q = r["dut.volume_flow"] / H_PER_S
        h = r["dut.head"]
        p = r["dut.shaft_power"] * 1e3
        assert q == pytest.approx(speed * q_bep, rel=1e-3)
        assert p == pytest.approx(speed**3 * (p0 + p1 * q_bep + p2 * q_bep**2), rel=1e-3)
        assert r["dut.efficiency"] / 100 == pytest.approx(RHO * G * q * h / p, rel=1e-9)
        assert r["dut.efficiency"] / 100 == pytest.approx(eta_bep, rel=1e-3)
        assert r["dut.bep_flow"] == pytest.approx(speed * q_bep * H_PER_S, abs=0.002)


def test_pump_npsh_available_from_inlet_pressure() -> None:
    """npsh_available = (p_inlet_abs - p_v(15 degC)) / (rho g), from the reported inlet p.

    Tank (0.2 m of 15 degC water) -> 80 m of 65 mm pipe rising 3.5 m -> pump -> Kv 15
    valve -> drain. The warning and the mode agree with the declared condition.
    """
    s = wp.System("review-npsh")
    s.add("tank", "tank", initial_level=0.2)
    s.add("suction", "pipe", length=80, diameter=65, roughness=0.045, height_difference=3.5)
    s.add("dut", "centrifugal_pump")
    s.add("v", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("tank.outlet", "suction.port_a")
    s.connect("suction.port_b", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    r = s.solve()
    p_abs = r["dut.inlet.p"] * 1e5 + P_ATM
    expected = (p_abs - vapour_pressure(288.15)) / (RHO * G)
    assert r["dut.npsh_available"] == pytest.approx(expected, rel=1e-9)
    cav = r["dut.npsh_available"] < r["dut.npsh_required"] + 0.5
    assert cav  # 2.66 m available against 2.19 m required plus the 0.5 m margin (2.69 m)
    assert r.has_warning("dut.cavitation")
    assert r.modes["dut"] == "cavitating"


def test_pump_stopped_zero_driving_pressure_is_clean() -> None:
    """Speed 0 with no driving pressure and a closed valve: zero flow, no NaN, mode off."""
    s = _pump_circuit(speed=0.0)
    s.set("v.opening", 0.0)
    r = s.solve()
    assert r.converged
    assert abs(r["dut.volume_flow"]) < 1e-9
    assert r.modes["dut"] == "off"
    assert all(v is None or math.isfinite(v) for v in r.values.values() if isinstance(v, float))


# ---------------------------------------------------------------------------------------
# Pump: regressions for defects found in the review (fixed in pump.py; now expected to pass)
# ---------------------------------------------------------------------------------------


def test_stopped_pump_with_linear_head_curve_is_not_a_short_circuit() -> None:
    """Regression (was a defect): a linear head curve made a stopped pump a short circuit.

    Curve [[0, 34], [18, 17], [36, 0.5]] (same shut-off head and run-out as the default,
    falling linearly) is accepted. The bounded fit gives b < 0 and c = C_MAX = -1e-9 in SI.
    At speed 0 the pump law is dp = -rho g c Q|Q| + 1 Pa per kg/s * m, so under 1 bar the
    flow is set by the 1 Pa/(kg/s) term: m = 1e5 kg/s, Q = 360,648 m3/h, 10,000 times the
    curve range, reported as mode ``off`` with no warning about the flow. With the default
    curve the same stopped pump passes 27 m3/h. A stopped pump of this size should pass a
    flow of the order of its curve range (well under 10 x 36 m3/h) at 1 bar. Since the
    STOP_RESISTANCE_FACTOR fix the stopped pump passes at most about 1.41 Q_max at a.
    """
    r = _stopped_pump(1.0, head_curve=[[0, 34], [18, 17], [36, 0.5]]).solve()
    assert r["dut.volume_flow"] < 360.0


def test_power_curve_inconsistent_with_head_curve_is_flagged() -> None:
    """Regression (was a defect): an efficiency above 100 % was reported silently.

    With power_curve [[0, 0.1], [10, 0.12], [36, 0.15]] kW (for example a power curve entered
    in the wrong unit) and the default head curve, the Kv 15 operating point is 23.96 m3/h
    at 26.02 m: hydraulic power rho g Q H = 998.2 * 9.80665 * 23.96 / 3600 * 26.02 = 1.695 kW
    against about 0.14 kW of shaft power, efficiency about 1210 %. No error, no warning.
    Expected: the pump rejects curves whose best efficiency exceeds 100 %
    (check_parameters) or at least never reports an efficiency above 100 %.
    """
    bad = [[0, 0.1], [10, 0.12], [36, 0.15]]
    try:
        s = _pump_circuit(power_curve=bad)
    except InvalidValueError:
        return
    r = s.solve()
    assert r["dut.efficiency"] <= 100.0 or r.warnings


# ---------------------------------------------------------------------------------------
# Tank: independent verification (expected to pass)
# ---------------------------------------------------------------------------------------


def test_tank_drain_down_matches_analytic_solution() -> None:
    """sqrt(h) = sqrt(h0) - k sqrt(rho g) / (2 rho A) t within 1 %.

    D = 0.3 m, h0 = 1.5 m, valve Kv 1.0, port Kv 50: k = 1/sqrt(1/k_v**2 + 1/k_p**2) with
    k = Kv_SI sqrt(rho * 1000 / 1e5); slope 6.15e-4 sqrt(m)/s, empty after 1991 s.
    """
    d, h0, kv, pk = 0.3, 1.5, 1.0, 50.0
    k_v, k_p = kv_to_k(kv / H_PER_S, RHO), kv_to_k(pk / H_PER_S, RHO)
    k = 1.0 / math.sqrt(1.0 / k_v**2 + 1.0 / k_p**2)
    slope = k * math.sqrt(RHO * G) / (2.0 * RHO * math.pi * d**2 / 4)
    sim = _drain_tank(d, h0, kv, pk).simulate(duration="1500 s", step="0.5 s")
    t = np.array(sim.time)
    level = np.array(sim["dut.level"], dtype=float)
    analytic = (math.sqrt(h0) - slope * t) ** 2
    mask = analytic > 0.05
    assert np.max(np.abs(level[mask] - analytic[mask]) / analytic[mask]) < 0.01


def test_communicating_tanks_conserve_volume_and_equalise() -> None:
    """Two tanks joined by a pipe: total volume constant, common level (V_a + V_b) / A_tot.

    A: D 1 m, 2.5 m; B: D 0.5 m, 0.5 m. Equal level = (0.25 pi 2.5 + 0.0625 pi 0.5) /
    (0.25 pi + 0.0625 pi) = 2.1 m.
    """
    s = wp.System("review-vessels")
    s.add("a", "tank", diameter=1.0, initial_level=2.5)
    s.add("b", "tank", diameter=0.5, initial_level=0.5)
    s.add("p", "pipe", length=10, diameter=50)
    s.connect("a.outlet", "p.port_a")
    s.connect("p.port_b", "b.inlet")
    sim = s.simulate(duration="1 h", step="1 s", variables=["a.volume", "b.volume"])
    total = np.array(sim["a.volume"], dtype=float) + np.array(sim["b.volume"], dtype=float)
    assert np.ptp(total) < 1e-12
    assert sim.final["a.level"] == pytest.approx(2.1, abs=1e-6)
    assert sim.final["b.level"] == pytest.approx(2.1, abs=1e-6)


def test_tank_level_and_temperature_round_trip() -> None:
    """Both storage states survive to_dict()/from_dict() after a mixing simulation."""
    s = wp.System("review-rt")
    s.add("src", "supply", pressure=1.0, temperature=60)
    s.add("v", "valve", kv=2.5)
    s.add("dut", "tank", diameter=1.0, initial_level=0.5, initial_temperature=10)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    s.simulate(duration="10 min", step="5 s")
    s2 = wp.System.from_dict(s.to_dict())
    assert s2.get("dut.level") == s.get("dut.level")
    assert s2.get("dut.temperature") == s.get("dut.temperature")
    assert 0.9 < s.get("dut.level") < 1.1


def test_tank_perfect_mixing_temperature() -> None:
    """Enthalpy balance of a fed tank with no outflow: T = (V0 T0 + (V - V0) T_in) / V.

    D = 1 m, 0.5 m of 10 degC water, fed with 60 degC water (1 bar through Kv 2.5). The
    mixing rule conserves V*T exactly, so it holds at every sample regardless of the step.
    """
    s = wp.System("review-mix")
    s.add("src", "supply", pressure=1.0, temperature=60)
    s.add("v", "valve", kv=2.5)
    s.add("dut", "tank", diameter=1.0, initial_level=0.5, initial_temperature=10)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    sim = s.simulate(duration="10 min", step="5 s")
    area = math.pi / 4.0
    v0 = area * 0.5
    vol = area * np.array(sim["dut.level"], dtype=float)
    expected = (v0 * 10.0 + (vol - v0) * 60.0) / vol
    assert np.max(np.abs(np.array(sim["dut.temperature"], dtype=float) - expected)) < 1e-9


def test_tank_overflow_mass_balance() -> None:
    """Filling a 1 m tank from 0.9 m past the rim: dV = sum(net_inflow dt) - sum(spill dt).

    D = 0.5 m, 1 bar through Kv 2.5 (about 2.38 m3/h against the full tank's head). The tank
    fills in about 30 s, then spills the full feed: final overflow_rate equals net_inflow,
    tank_overflow is raised and the mode is ``overflowing``. With explicit Euler the net
    inflow sample at t_k is integrated over [t_k, t_k+1] and the spill of that step is
    reported at t_k+1.
    """
    s = wp.System("review-overflow")
    s.add("src", "supply", pressure=1.0)
    s.add("v", "valve", kv=2.5)
    s.add("dut", "tank", diameter=0.5, height=1.0, initial_level=0.9)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    sim = s.simulate(duration="300 s", step="1 s")
    level = np.array(sim["dut.level"], dtype=float)
    net = np.array(sim["dut.net_inflow"], dtype=float) / H_PER_S
    spill = np.array(sim["dut.overflow_rate"], dtype=float) / H_PER_S
    d_vol = math.pi * 0.25**2 * (level[-1] - level[0])
    assert d_vol == pytest.approx(float(np.sum(net[:-1]) - np.sum(spill[1:])), abs=1e-12)
    assert sim.final["dut.overflow_rate"] == pytest.approx(sim.final["dut.net_inflow"])
    assert sim.final["dut.overflow_rate"] > 2.0
    assert sim.final["dut.fill_fraction"] == pytest.approx(100.0)
    assert s.solve().modes["dut"] == "overflowing"


def test_tank_units_at_the_boundary() -> None:
    """Strings with units convert to the declared units; ports report bar gauge.

    diameter "200 cm" = 2 m, initial_level "1500 mm" = 1.5 m, initial_temperature "300 K"
    = 26.85 degC; volume pi * 1.5 = 4.712 m3; fill 50 %; bottom port pressure
    998.2 * 9.80665 * 1.5 / 1e5 = 0.146835 bar gauge. A pressure for port_kv is an error.
    """
    s = wp.System("review-units")
    s.add(
        "dut",
        "tank",
        diameter="200 cm",
        initial_level="1500 mm",
        initial_temperature="300 K",
    )
    r = s.solve()
    assert s.get("dut.level") == pytest.approx(1.5)
    assert s.get("dut.temperature") == pytest.approx(26.85)
    assert r["dut.volume"] == pytest.approx(math.pi * 1.5)
    assert r["dut.fill_fraction"] == pytest.approx(50.0)
    assert r["dut.inlet.p"] == pytest.approx(RHO * G * 1.5 / 1e5, rel=1e-9)
    with pytest.raises(wp.UnitError):
        s.set("dut.port_kv", "5 bar")


# ---------------------------------------------------------------------------------------
# Tank: regressions for defects found in the review (fixed; now expected to pass)
# ---------------------------------------------------------------------------------------


def test_nearly_empty_break_tank_does_not_create_water() -> None:
    """Regression (was a defect): a pump drawing from a slowly fed empty tank delivered 3x
    the water fed in. Fixed by capping each step's outflow at the water in the tank
    (tank.py, GateLaw.limit, Component.time_step).

    Supply (1 bar) -> Kv 2.5 valve -> tank (D 1 m, empty) -> pump -> Kv 15 valve -> drain,
    default 1 s step. The feed is about 2.5 m3/h. The tank opens its outlet gate once the
    level exceeds 1 mm (0.79 L); the pump then draws 23.9 m3/h (6.6 L) over the whole step,
    the level is clamped at 0 and the missing water is created. Over 600 s the pump delivers
    about 1.33 m3 while only about 0.42 m3 entered; the tank's volume change (0) differs from
    its integrated net inflow (-0.91 m3), and the pump flow chatters 0 / 23.9 / 0 m3/h with
    1200 mode changes. Physically the pump can deliver at most what entered the tank
    (initial volume 0), so over the run: pumped volume <= fed volume (+2 %).
    """
    s = wp.System("review-break-tank")
    s.add("src", "supply", pressure=1.0)
    s.add("vin", "valve", kv=2.5)
    s.add("t", "tank", diameter=1.0, initial_level=0.0)
    s.add("pump", "centrifugal_pump")
    s.add("v", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("src.port", "vin.port_a")
    s.connect("vin.port_b", "t.inlet")
    s.connect("t.outlet", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    dt = 1.0
    sim = s.simulate(duration="600 s", step=f"{dt} s")
    pumped = float(np.sum(np.array(sim["pump.volume_flow"][:-1]) / H_PER_S) * dt)
    fed = float(np.sum(np.array(sim["t.inlet.m_flow"][:-1]) / RHO) * dt)
    assert fed == pytest.approx(0.417, rel=0.02)
    assert pumped <= fed * 1.02


def test_level_state_cannot_exceed_height() -> None:
    """Regression (was a defect): ``level`` could be set above the rim (up to its 100 m
    state limit). Fixed with the Tank.check_states rule (new Component.check_states hook).

    Default tank (height 3 m): set level 3.5 m. It is accepted with no error and no
    check() issue; the steady result reports fill_fraction 116.7 %, volume above capacity
    (pi * 3.5 = 11.0 m3 > pi * 3 = 9.42 m3), mode ``draining`` and no ``tank_overflow``; the
    next simulation step then spills 0.5 m at once and reports an overflow spike of 352 m3/h
    at the first sample. The initial_level rule (<= height) should hold for the state too:
    reject the value, or clamp it to the height.
    """
    s = _drain_tank(2.0, 2.0, 2.5)
    try:
        s.set("dut.level", 3.5)
    except InvalidValueError:
        return
    assert s.get("dut.level") <= 3.0
    r = s.solve()
    assert r["dut.fill_fraction"] <= 100.0


def test_changing_port_kv_keeps_the_water_in_the_tank() -> None:
    """Regression (was a defect in core behaviour): changing an unrelated parameter refilled
    the tank. Fixed in System.set_values: only states whose initial value changes are reset.

    Drain a 0.5 m tank from 2 m for 300 s (level about 1.56 m), then change ``port_kv``
    (a nozzle change). ``System.set_values`` calls ``init_states()`` for any parameter
    change, so the level jumps back to initial_level = 2 m: 0.086 m3 of water is created.
    The tank manifest says only that setting ``initial_level`` resets the level. Expected:
    the level after the port_kv change equals the level before it.
    """
    s = _drain_tank(0.5, 2.0, 2.5)
    s.simulate(duration="300 s", step="1 s")
    before = s.get("dut.level")
    assert before < 1.9
    s.set("dut.port_kv", 150)
    assert s.get("dut.level") == pytest.approx(before, rel=1e-12)


# ---------------------------------------------------------------------------------------
# Manifest contracts that could never fail
# ---------------------------------------------------------------------------------------


@pytest.fixture
def tank_manifest() -> wp.Manifest:
    return wp.default_catalog().get("tank")


@pytest.fixture
def overflow_never_reported(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mutant tank that never reports any overflow."""
    original = Tank.observables

    def observables(self: Tank, sol: object) -> dict[str, float | None]:
        values = original(self, sol)  # type: ignore[arg-type]
        values["overflow_rate"] = 0.0
        return values

    monkeypatch.setattr(Tank, "observables", observables)
    yield


@pytest.fixture
def empty_tank_never_blocks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mutant tank whose gates never block outflow (design 8.9 requirement removed)."""
    original = Tank.update_laws

    def update_laws(self: Tank) -> None:
        original(self)
        for gate in self.gates.values():
            gate.blocked_direction = None

    monkeypatch.setattr(Tank, "update_laws", update_laws)
    yield


@pytest.mark.usefixtures("overflow_never_reported")
def test_overflow_contract_detects_a_tank_that_never_overflows(
    tank_manifest: wp.Manifest,
) -> None:
    """Regression (was a weak contract): ``overflow-warning`` was a tautology; its condition
    is now the supply pressure against the full tank's head.

    Its check is warning_iff(tank_overflow, "dut.overflow_rate > 0"), the envelope
    condition itself, so it holds for any implementation. Its description claims the tank
    overflows exactly when the supply pressure exceeds the full tank's head (0.0979 bar at
    1 m). A mutant that never reports overflow still passes it (the fill-to-overflow
    scenario catches the mutant, the contract does not). A meaningful check would be e.g.
    condition "src.pressure > 0.0979" over the same sweep.
    """
    assert not run_contract(tank_manifest, "overflow-warning").passed


@pytest.mark.usefixtures("empty_tank_never_blocks")
def test_catalogue_detects_a_tank_that_does_not_block_when_empty(
    tank_manifest: wp.Manifest,
) -> None:
    """Regression (was a weak contract): nothing in the manifests tested "outflow blocked
    when empty"; the pump-from-empty-tank and break-tank scenarios now do.

    The drain-to-empty scenario claims "outflow is blocked" but uses a gravity drain, which
    has no driving head at level 0, so net_inflow is 0 with or without the gate. The
    ``tank-empty-warning`` and ``low-level-info`` contracts restate their envelope
    conditions verbatim. A mutant that never blocks outflow passes every tank scenario and
    contract (and the pump manifest too). A scenario with a pump drawing from an empty tank,
    expecting net_inflow above -1e-3 m3/h, would catch it.
    """
    assert not run_component(tank_manifest).passed


# ---------------------------------------------------------------------------------------
# Interface conformance with design 8.8 and 8.9
# ---------------------------------------------------------------------------------------

PUMP_SPEC = {
    "parameters": {
        "rated_speed": ("rpm", 2900, 100, 20000),
        "npsh_margin": ("m", 0.5, 0, 10),
        "min_flow_fraction": ("1", 0.15, 0, 1),
        # Added after the agent trial (pending design 8.8 revision): preferred operating
        # region (ANSI/HI 9.6.3) and driver rating.
        "preferred_min_fraction": ("1", 0.7, 0, 1),
        "preferred_max_fraction": ("1", 1.2, 1, 5),
        "motor_power": ("kW", 4.0, 0.01, 100000),
    },
    "inputs": {"speed": ("1", 1.0, 0, 1.2)},
    "observables": {
        "volume_flow": "m3/h",
        "head": "m",
        "shaft_power": "kW",
        "hydraulic_power": "kW",
        "efficiency": "%",
        # Added at integration: the coordinator extended design 8.8 with specific_energy
        # (shaft power / volume flow, kWh/m3, None without forward flow).
        "specific_energy": "kWh/m3",
        "npsh_available": "m",
        "npsh_required": "m",
        "speed_rpm": "rpm",
        "bep_flow": "m3/h",
        "curve_fit_rms": "m",
    },
    "modes": ["off", "reverse_flow", "cavitating", "low_flow", "running"],
    "codes": {
        "cavitation",
        "low_flow",
        "beyond_curve",
        "reverse_flow",
        # Added after the agent trial (pending design 8.8 revision).
        "outside_preferred_region",
        "motor_overload",
    },
}
TANK_SPEC = {
    "parameters": {
        "diameter": ("m", 2.0, 0.05, 100),
        "height": ("m", 3.0, 0.1, 100),
        "initial_level": ("m", 2.0, 0, 100),
        "initial_temperature": ("degC", 15, 0.5, 99),
        "port_kv": ("m3/h", 200, 0.01, 1e6),
    },
    "inputs": {},
    "observables": {
        "volume": "m3",
        "fill_fraction": "%",
        "net_inflow": "m3/h",
        "overflow_rate": "m3/h",
    },
    "modes": ["empty", "overflowing", "filling", "draining", "steady"],
    # drawing_air added after the agent trial (pending design 8.9 revision).
    "codes": {"tank_empty", "low_level", "tank_overflow", "drawing_air"},
}


@pytest.mark.parametrize(("alias", "spec"), [("centrifugal_pump", PUMP_SPEC), ("tank", TANK_SPEC)])
def test_interface_matches_design(alias: str, spec: dict) -> None:
    """Names, units, defaults, limits, modes and codes as listed in design 8.8 and 8.9."""
    m = wp.default_catalog().get(alias)
    for group in ("parameters", "inputs"):
        declared = getattr(m, group)
        for name, (unit, default, lo, hi) in spec[group].items():
            v = declared[name]
            assert (v.unit, v.default, v.minimum, v.maximum) == (unit, default, lo, hi), name
    assert {n: o.unit for n, o in m.observables.items()} == spec["observables"]
    assert [md.name for md in m.modes] == spec["modes"]
    assert set(m.warnings) == spec["codes"]
    if alias == "tank":
        assert {n: (s.unit, s.steady) for n, s in m.states.items()} == {
            "level": ("m", "hold"),
            "temperature": ("degC", "hold"),
        }
    else:
        defaults = {n: m.parameters[n].default for n in ("head_curve", "power_curve")}
        assert defaults["head_curve"] == [[0, 34], [10, 32.5], [20, 28.5], [30, 21.5], [36, 16]]
        assert defaults["power_curve"] == [[0, 1.5], [10, 2.0], [20, 2.55], [30, 2.95], [36, 3.15]]
        assert m.parameters["npsh_curve"].default == [[10, 1.4], [20, 2.1], [30, 3.4], [36, 4.6]]
