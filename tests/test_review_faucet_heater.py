"""Independent review of the mixing faucet (design 8.6) and the instantaneous heater (8.7).

Every expected value here is derived by hand (or by an independent root solve with scipy)
from the Kv definition and the steady-flow energy balance, not taken from the model:

* Kv law: ``Q [m3/h] = Kv * sqrt(dp [bar] / SG)`` with ``SG = rho / 1000 = 0.9982``.
* Series elements (same flow): ``1 / Kv_eq**2 = sum(1 / Kv_i**2)``.
* Parallel elements (same drop): ``Kv_eq = sum(Kv_i)``.
* Adiabatic mixing with constant cp: ``T_mix = sum(Q_i * T_i) / sum(Q_i)``.
* Heater: ``Q_heat = m * cp * (T_out - T_in)`` with ``T_out = min(T_set, T_in + P / (m cp))``.

Tests whose name starts with ``test_defect_`` document defects found in the review and are
expected to FAIL until the implementation, manifest or core is fixed; their docstrings give
the expected physics and how the expected value was derived.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml
from scipy.optimize import brentq, fsolve

import worldparts as wp
from worldparts.components import heater as heater_module

RHO = 998.2
CP = 4182.0
SG = RHO / 1000.0
LEAK = 1e-6
CATALOG = Path(wp.__file__).parent / "catalog" / "hydraulic"


# -- helpers -------------------------------------------------------------------------------


def kv_flow_m3h(kv: float, dp_bar: float) -> float:
    """Signed Kv flow in m3/h for a signed pressure drop in bar."""
    return math.copysign(kv * math.sqrt(abs(dp_bar) / SG), dp_bar)


def lmin(q_m3h: float) -> float:
    """m3/h to L/min."""
    return q_m3h * 1000.0 / 60.0


def cartridges(lift: float, mix: float, kv: float = 0.6) -> tuple[float, float]:
    """Hot and cold cartridge Kv (m3/h) of design 8.6."""
    return (
        kv * (LEAK + (1 - LEAK) * lift * mix),
        kv * (LEAK + (1 - LEAK) * lift * (1 - mix)),
    )


def faucet(
    p_hot: float | None = 3.0,
    p_cold: float | None = 3.0,
    t_hot: float = 55.0,
    t_cold: float = 12.0,
    lift: float = 1.0,
    mix: float = 0.5,
    **params: Any,
) -> wp.System:
    """Faucet fed by two supplies; ``None`` caps that inlet."""
    s = wp.System("review-faucet")
    s.add("dut", "mixing_faucet", inputs={"lift": lift, "mix": mix}, **params)
    if p_hot is not None:
        s.add("hsup", "supply", pressure=p_hot, temperature=t_hot)
        s.connect("hsup.port", "dut.hot")
    if p_cold is not None:
        s.add("csup", "supply", pressure=p_cold, temperature=t_cold)
        s.connect("csup.port", "dut.cold")
    return s


def heater_line(
    valve_kv: float, pressure: float = 3.0, t_in: float = 12.0, **heater: Any
) -> wp.System:
    """supply -> heater -> valve -> drain."""
    s = wp.System("review-heater")
    s.add("src", "supply", pressure=pressure, temperature=t_in)
    s.add("dut", "instantaneous_water_heater", **heater)
    s.add("v", "valve", kv=valve_kv)
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def codes(r: wp.SolveResult) -> set[str]:
    return {w.path for w in r.warnings}


def manifest_yaml(alias: str) -> dict[str, Any]:
    return yaml.safe_load((CATALOG / f"{alias}.yaml").read_text(encoding="utf-8"))


# -- faucet: flow ----------------------------------------------------------------------------


def test_faucet_flow_3_bar_series_parallel() -> None:
    """3 bar both sides, lift 1, mix 0.5: cartridges 0.3 + 0.3 = 0.6 in parallel, in series
    with the 0.8 spout: Kv_eq = 0.6 * 0.8 / 1.0 = 0.48 m3/h;
    Q = 0.48 * sqrt(3 / 0.9982) = 0.83213 m3/h = 13.869 L/min (design: about 14 L/min)."""
    kh, kc = cartridges(1.0, 0.5)
    kv_eq = 1.0 / math.sqrt(1.0 / (kh + kc) ** 2 + 1.0 / 0.8**2)
    expected = lmin(kv_flow_m3h(kv_eq, 3.0))
    assert expected == pytest.approx(13.869, abs=1e-3)
    r = faucet().solve()
    assert r.units["dut.flow"] == "L/min"
    assert r["dut.flow"] == pytest.approx(expected, rel=0.005)
    assert r["dut.flow"] == pytest.approx(r["dut.hot_flow"] + r["dut.cold_flow"], rel=1e-9)


# -- faucet: temperature ---------------------------------------------------------------------


@pytest.mark.parametrize(("p_hot", "p_cold"), [(3.0, 3.0), (2.0, 4.0), (4.5, 1.5)])
@pytest.mark.parametrize("mix", [0.0, 0.13, 0.37, 0.5, 0.81, 1.0])
def test_faucet_mixed_temperature_energy_balance(p_hot: float, p_cold: float, mix: float) -> None:
    """Mixed temperature is the flow-weighted mean of the inlet temperatures. Inlet flows
    come from an independent brentq solve of the mixing-node mass balance
    ``Q(Kv_h, p_h - pC) + Q(Kv_c, p_c - pC) = Q(0.8, pC)``."""
    kh, kc = cartridges(1.0, mix)

    def balance(pc: float) -> float:
        return kv_flow_m3h(kh, p_hot - pc) + kv_flow_m3h(kc, p_cold - pc) - kv_flow_m3h(0.8, pc)

    pc = brentq(balance, 0.0, max(p_hot, p_cold), xtol=1e-14)
    qh, qc = lmin(kv_flow_m3h(kh, p_hot - pc)), lmin(kv_flow_m3h(kc, p_cold - pc))
    r = faucet(p_hot, p_cold, t_hot=70.0, t_cold=5.0, mix=mix).solve()
    assert r["dut.hot_flow"] == pytest.approx(qh, rel=1e-5, abs=1e-7)
    assert r["dut.cold_flow"] == pytest.approx(qc, rel=1e-5, abs=1e-7)
    if qh >= 0 and qc >= 0:  # no crossflow: both supplies feed the mixing node
        t_hand = (qh * 70.0 + qc * 5.0) / (qh + qc)
        assert r["dut.temperature"] == pytest.approx(t_hand, abs=1e-6)


def test_faucet_temperature_none_when_closed() -> None:
    """Lever closed: only leakage (Kv 1.2e-6 m3/h, about 3.5e-5 L/min) passes, below the
    undefined-temperature threshold, so the outlet temperature is None."""
    r = faucet(lift=0.0).solve()
    assert r["dut.temperature"] is None
    assert r["dut.flow"] == pytest.approx(lmin(kv_flow_m3h(1.2e-6, 3.0)), rel=0.01)
    assert r.modes["dut"] == "closed"


# -- faucet: crossflow -----------------------------------------------------------------------


@pytest.mark.parametrize("mix", [0.0, 0.1, 0.2, 0.3, 0.33, 0.35, 0.4, 0.5, 0.7, 1.0])
def test_faucet_crossflow_iff_backflow(mix: float) -> None:
    """Hot 1 bar, cold 5 bar, lever open: when the mixing-node pressure pC (independent
    brentq) exceeds 1 bar, water flows backward into the hot supply; the crossflow warning
    fires exactly when a hand-computed inlet flow is below -0.01 L/min."""
    kh, kc = cartridges(1.0, mix)

    def balance(pc: float) -> float:
        return kv_flow_m3h(kh, 1.0 - pc) + kv_flow_m3h(kc, 5.0 - pc) - kv_flow_m3h(0.8, pc)

    pc = brentq(balance, 0.0, 5.0, xtol=1e-14)
    qh = lmin(kv_flow_m3h(kh, 1.0 - pc))
    r = faucet(1.0, 5.0, mix=mix).solve()
    assert r["dut.hot_flow"] == pytest.approx(qh, rel=1e-5, abs=1e-7)
    assert (pc > 1.0) == (r["dut.hot_flow"] < 0)
    assert ("dut.crossflow" in codes(r)) == (qh < -0.01)
    if qh < -0.01:
        assert r["hsup.volume_flow"] < 0  # the hot supply absorbs water
        assert r["dut.temperature"] == pytest.approx(12.0, abs=1e-6)  # only cold enters C


# -- faucet: capped inlet --------------------------------------------------------------------


@pytest.mark.parametrize("capped", ["hot", "cold"])
def test_faucet_capped_inlet(capped: str) -> None:
    """One inlet capped: the other feeds the spout through its cartridge, Kv 0.3 in series
    with 0.8 at mix 0.5: Kv_eq = 0.24 / sqrt(0.73) = 0.2809 m3/h, 8.1162 L/min at 3 bar.
    The capped port sits at the mixing-node pressure (0.37 bar < 0.5 bar) but
    low_supply_pressure ignores it."""
    s = faucet(p_hot=None) if capped == "hot" else faucet(p_cold=None)
    r = s.solve()
    kv_eq = 0.3 * 0.8 / math.sqrt(0.3**2 + 0.8**2)
    assert r["dut.flow"] == pytest.approx(lmin(kv_flow_m3h(kv_eq, 3.0)), rel=1e-4)
    p_c = (kv_flow_m3h(kv_eq, 3.0) / 0.8) ** 2 * SG
    assert r[f"dut.{capped}.p"] == pytest.approx(p_c, rel=1e-4)
    assert r[f"dut.{capped}.p"] < 0.5
    assert "dut.low_supply_pressure" not in codes(r)
    # The connected inlet below min_flow_pressure does raise it.
    other = "csup" if capped == "hot" else "hsup"
    s.set(f"{other}.pressure", 0.3)
    assert "dut.low_supply_pressure" in codes(s.solve())


# -- heater ----------------------------------------------------------------------------------


def hand_heater(valve_kv: float, pressure: float = 3.0, t_in: float = 12.0) -> tuple[float, float]:
    """(mass flow kg/s, outlet temperature degC) of the default heater in the test line."""
    kv_eq = 1.0 / math.sqrt(1.0 / 1.5**2 + 1.0 / valve_kv**2)
    m = kv_flow_m3h(kv_eq, pressure) / 3600.0 * RHO
    if m / RHO * 60000.0 <= 2.0:
        return m, t_in
    return m, min(55.0, t_in + 18000.0 / (m * CP))


@pytest.mark.parametrize("valve_kv", [0.1, 0.2])
def test_heater_low_flow_reaches_setpoint(valve_kv: float) -> None:
    """Low flow: m cp (55 - 12) < 18 kW, so T_out = 55 degC and heat_rate = m cp 43 K."""
    m, t_out = hand_heater(valve_kv)
    assert t_out == 55.0
    r = heater_line(valve_kv).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(55.0, abs=1e-9)
    assert r["dut.heat_rate"] == pytest.approx(m * CP * 43.0 / 1000.0, rel=1e-6)
    assert r["sink.port.T"] == pytest.approx(55.0, abs=1e-9)
    assert r.modes["dut"] == "heating"
    assert "dut.setpoint_not_met" not in codes(r)


@pytest.mark.parametrize("valve_kv", [0.3, 0.5, 1.0])
def test_heater_high_flow_saturates(valve_kv: float) -> None:
    """High flow: T_out = T_in + P_max / (m cp) < setpoint; heat_rate = 18 kW."""
    m, t_out = hand_heater(valve_kv)
    assert t_out < 54.0
    r = heater_line(valve_kv).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(12.0 + 18000.0 / (m * CP), abs=1e-6)
    assert r["dut.heat_rate"] == pytest.approx(18.0, rel=1e-9)
    assert r["sink.port.T"] == pytest.approx(t_out, abs=1e-6)
    assert r.modes["dut"] == "saturated"
    assert "dut.setpoint_not_met" in codes(r)


def test_heater_no_heating_below_activation() -> None:
    """Kv 0.05 valve: Q = 1.444 L/min < 2 L/min activation flow: no heating."""
    m, _ = hand_heater(0.05)
    assert m / RHO * 60000 == pytest.approx(1.44387, rel=1e-4)
    r = heater_line(0.05).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(12.0, abs=1e-12)
    assert r["dut.heat_rate"] == 0.0
    assert r.modes["dut"] == "idle"
    assert "dut.below_activation_flow" in codes(r)


def test_heater_feeds_faucet_energy_balance() -> None:
    """mains 3.5 bar 12 degC -> heater -> faucet.hot; mains -> faucet.cold; lift 1, mix 0.5.
    Independent fsolve of the two unknown pressures (heater outlet, mixing node); the
    enthalpy rise of the spout discharge equals the heater's heat rate (18 kW, saturated)."""
    kc = cartridges(1.0, 0.5)[0]

    def eqs(x: list[float]) -> list[float]:
        ph, pc = x
        q1 = kv_flow_m3h(1.5, 3.5 - ph)
        q2 = kv_flow_m3h(kc, ph - pc)
        q3 = kv_flow_m3h(kc, 3.5 - pc)
        return [q1 - q2, q2 + q3 - kv_flow_m3h(0.8, pc)]

    ph, pc = fsolve(eqs, [3.0, 1.5], xtol=1e-14)
    qh = lmin(kv_flow_m3h(1.5, 3.5 - ph))
    qc = lmin(kv_flow_m3h(kc, 3.5 - pc))
    m_h = qh / 60000 * RHO
    t_h = min(55.0, 12.0 + 18000.0 / (m_h * CP))
    t_mix = (qh * t_h + qc * 12.0) / (qh + qc)

    s = wp.System("bathroom")
    s.add("mains", "supply", pressure="3.5 bar", temperature="12 degC")
    s.add("dut", "instantaneous_water_heater")
    s.add("tap", "mixing_faucet", inputs={"lift": 1, "mix": 0.5})
    s.connect("mains.port", "dut.inlet")
    s.connect("mains.port", "tap.cold")
    s.connect("dut.outlet", "tap.hot")
    r = s.solve()
    assert r["dut.volume_flow"] == pytest.approx(qh, rel=1e-6)
    assert r["tap.flow"] == pytest.approx(qh + qc, rel=1e-6)
    assert r["tap.temperature"] == pytest.approx(t_mix, abs=1e-6)
    enthalpy_rise_kw = r["tap.flow"] / 60000 * RHO * CP * (r["tap.temperature"] - 12.0) / 1000
    assert enthalpy_rise_kw == pytest.approx(r["dut.heat_rate"], rel=1e-9)
    assert r["dut.heat_rate"] == pytest.approx(18.0, rel=1e-9)


# -- interface vs design 8.6 / 8.7 -----------------------------------------------------------


def _by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i.get("name", i.get("code")): i for i in items}


def test_faucet_interface_matches_design() -> None:
    """Design 8.6 names, units, defaults and limits."""
    m = manifest_yaml("mixing_faucet")
    assert [p["name"] for p in m["ports"]] == ["hot", "cold"]
    params = _by_name(m["parameters"])
    expected = {
        "kv_hot": ("m3/h", 0.6, 0.001, 100),
        "kv_cold": ("m3/h", 0.6, 0.001, 100),
        "kv_spout": ("m3/h", 0.8, 0.001, 100),
        "leakage": ("1", 1e-6, 1e-9, 0.01),
        "min_flow_pressure": ("bar", 0.5, 0, 10),
        "scald_temperature": ("degC", 49, 30, 90),
    }
    assert set(params) == set(expected)
    for name, (unit, default, lo, hi) in expected.items():
        p = params[name]
        assert (p["unit"], p["default"], p["minimum"], p["maximum"]) == (unit, default, lo, hi)
    assert params["min_flow_pressure"].get("pressure_reference", "gauge") == "gauge"
    inputs = _by_name(m["inputs"])
    assert (inputs["lift"]["default"], inputs["lift"]["minimum"], inputs["lift"]["maximum"]) == (
        0,
        0,
        1,
    )
    assert (inputs["mix"]["default"], inputs["mix"]["minimum"], inputs["mix"]["maximum"]) == (
        0.5,
        0,
        1,
    )
    obs = {o["name"]: o["unit"] for o in m["observables"]}
    assert obs == {
        "flow": "L/min",
        "temperature": "degC",
        "hot_flow": "L/min",
        "cold_flow": "L/min",
    }
    assert [x["name"] for x in m["modes"]] == ["closed", "cold_only", "hot_only", "mixing"]
    # back_siphonage was added after this review, following its own usability finding (a
    # sub-atmospheric supply drawing water in through the spout); design 8.6 is to follow.
    assert {e["code"] for e in m["envelope"]} == {"scald_risk", "crossflow", "back_siphonage"}
    assert {w["code"] for w in m["warnings"]} == {"low_supply_pressure"}


def test_heater_interface_matches_design() -> None:
    """Design 8.7 names, units, defaults and limits."""
    m = manifest_yaml("instantaneous_water_heater")
    assert [p["name"] for p in m["ports"]] == ["inlet", "outlet"]
    params = _by_name(m["parameters"])
    expected = {
        "setpoint": ("degC", 55, 20, 95),
        "max_power": ("kW", 18, 0.1, 1000),
        "activation_flow": ("L/min", 2.0, 0, 100),
        "kv": ("m3/h", 1.5, 0.001, 1000),
    }
    assert set(params) == set(expected)
    for name, (unit, default, lo, hi) in expected.items():
        p = params[name]
        assert (p["unit"], p["default"], p["minimum"], p["maximum"]) == (unit, default, lo, hi)
    inputs = _by_name(m["inputs"])
    assert set(inputs) == {"enabled"}
    e = inputs["enabled"]
    assert (e["unit"], e["default"], e["minimum"], e["maximum"]) == ("1", 1.0, 0, 1)
    obs = {o["name"]: o["unit"] for o in m["observables"]}
    assert obs == {
        "volume_flow": "L/min",
        "outlet_temperature": "degC",
        "temperature_rise": "K",
        "heat_rate": "kW",
    }
    assert [x["name"] for x in m["modes"]] == ["idle", "saturated", "heating"]
    env = {x["code"]: x["severity"] for x in m["envelope"]}
    assert env == {"setpoint_not_met": "warning", "below_activation_flow": "info"}


# -- units at the boundary -------------------------------------------------------------------


def test_units_at_the_boundary() -> None:
    """Strings with units convert to the declared unit; gauge references are honoured."""
    s = wp.System("units")
    s.add(
        "dut",
        "instantaneous_water_heater",
        setpoint="140 degF",  # 60 degC
        max_power="18000 W",
        activation_flow="0.05 L/s",  # 3 L/min
    )
    s.add("f", "mixing_faucet", min_flow_pressure="1.5 bara", scald_temperature="320 K")
    assert s.get("dut.setpoint") == pytest.approx(60.0, abs=1e-9)
    assert s.get("dut.max_power") == pytest.approx(18.0)
    assert s.get("dut.activation_flow") == pytest.approx(3.0)
    assert s.get("f.min_flow_pressure") == pytest.approx(1.5 - 1.01325)
    assert s.get("f.scald_temperature") == pytest.approx(46.85)
    with pytest.raises(wp.UnitError):
        s.set("dut.setpoint", "3 bar")
    with pytest.raises(wp.InvalidValueError):
        s.set("f.min_flow_pressure", "1 atm")
    with pytest.raises(wp.OutOfRangeError):
        s.set("dut.setpoint", "100 degC")


# -- robustness ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"kv_hot": 0.001, "kv_cold": 100, "kv_spout": 0.001, "leakage": 1e-9},
        {"kv_hot": 100, "kv_cold": 100, "kv_spout": 100, "leakage": 0.01},
        {"kv_hot": 100, "kv_cold": 0.001, "kv_spout": 100, "leakage": 1e-9},
    ],
)
@pytest.mark.parametrize(("p_hot", "p_cold"), [(-0.9, 100.0), (100.0, 0.0), (0.0, 0.0)])
@pytest.mark.parametrize("lift", [0.0, 0.0015, 1.0])
def test_faucet_extreme_values_stay_finite(
    params: dict[str, float], p_hot: float, p_cold: float, lift: float
) -> None:
    """Extreme legal values solve with finite results, conserved mass and a mixed
    temperature inside the supply range."""
    r = faucet(p_hot, p_cold, t_hot=99.0, t_cold=0.5, lift=lift, **params).solve()
    for k in ("dut.flow", "dut.hot_flow", "dut.cold_flow"):
        assert math.isfinite(r[k])
    assert r["dut.flow"] == pytest.approx(
        r["dut.hot_flow"] + r["dut.cold_flow"], rel=1e-9, abs=1e-12
    )
    t = r["dut.temperature"]
    assert t is None or 0.5 - 1e-6 <= t <= 99.0 + 1e-6


def test_long_simulation_no_nan() -> None:
    """Two hours at 1 s with lever and power events: finite series throughout."""
    s = wp.System("bathroom")
    s.add("mains", "supply", pressure=3.5, temperature=12)
    s.add("dut", "instantaneous_water_heater")
    s.add("tap", "mixing_faucet", inputs={"lift": 1, "mix": 0.5})
    s.connect("mains.port", "dut.inlet")
    s.connect("mains.port", "tap.cold")
    s.connect("dut.outlet", "tap.hot")
    sim = s.simulate(
        duration="2 h",
        step="1 s",
        events=[
            {"at": "10 min", "set": {"tap.lift": 0}},
            {"at": "20 min", "set": {"tap.lift": 0.3, "tap.mix": 1}},
            {"at": "1 h", "set": {"dut.enabled": 0.5}},
        ],
    )
    for key in ("tap.flow", "tap.temperature", "dut.outlet_temperature", "dut.heat_rate"):
        assert all(v is None or math.isfinite(v) for v in sim.series[key])
    assert sim.series["dut.heat_rate"][-1] <= 9.0 + 1e-9


# -- defects (expected to fail) --------------------------------------------------------------


def test_defect_heater_idle_at_zero_flow_with_zero_activation_flow() -> None:
    """A heater with no water flowing is idle, whatever activation_flow is.

    activation_flow = 0 is a legal value (limits [0, 100] L/min). A dead-ended heater
    (outlet capped) carries exactly zero flow, so the flow switch cannot close and nothing
    is heated (heat_rate 0). The manifest's ``idle`` condition ``volume_flow <
    activation_flow`` reads ``0 < 0`` = false; ``saturated`` is false because the outlet
    temperature is None, so the mode falls through to ``heating`` ("Firing and holding the
    setpoint") while heat_rate is 0. Expected mode: idle (e.g. condition ``volume_flow <=
    activation_flow``, which also matches the "at or below" wording of activation_flow).
    """
    s = wp.System("dead-end")
    s.add("src", "supply", pressure=3, temperature=12)
    s.add("dut", "instantaneous_water_heater", activation_flow=0)
    s.connect("src.port", "dut.inlet")
    r = s.solve()
    assert r["dut.volume_flow"] == 0.0
    assert r["dut.heat_rate"] == 0.0
    assert r.modes["dut"] == "idle"


def test_defect_heater_mode_consistent_with_flow_switch_at_activation_flow() -> None:
    """At a flow exactly equal to activation_flow the heater does not fire.

    The parameter is documented as the flow "at or below which the flow switch keeps the
    heater off", and the code fires only for ``m / rho > activation_flow``: with
    activation_flow set to the line's own flow (5.728 L/min for a Kv 0.2 valve, from the
    series Kv 1.5 / 0.2 hand calculation) the outlet stays at the 12 degC inlet
    temperature. The mode must then be ``idle``; the manifest's strict ``<`` comparison
    instead reports ``saturated`` ("Firing at the power limit") with no heat delivered.
    """
    s = heater_line(0.2)
    q = s.solve()["dut.volume_flow"]
    assert q == pytest.approx(
        lmin(kv_flow_m3h(1.0 / math.sqrt(1 / 1.5**2 + 1 / 0.2**2), 3.0)), rel=1e-6
    )
    s.set("dut.activation_flow", q)
    r = s.solve()
    assert r["dut.outlet_temperature"] == pytest.approx(12.0, abs=1e-12)  # not firing
    assert r["dut.heat_rate"] == 0.0
    assert r.modes["dut"] == "idle"


def test_defect_temperature_rise_converts_as_a_difference() -> None:
    """temperature_rise is a temperature difference (declared unit K).

    In the bathroom chain the rise is 35.10 K (47.10 - 12 degC, hand-solved in
    test_heater_feeds_faucet_energy_balance). A difference of 35.10 K is a difference of
    35.10 degC; converting it as an absolute temperature gives 35.10 - 273.15 = -238.05,
    which is silent nonsense. ``SolveResult.get(path, unit="degC")`` must either return
    35.10 or raise a unit error (core: results/units should treat K-valued differences as
    deltas, or the vocabulary needs a delta unit).
    """
    s = wp.System("bathroom")
    s.add("mains", "supply", pressure=3.5, temperature=12)
    s.add("dut", "instantaneous_water_heater")
    s.add("tap", "mixing_faucet", inputs={"lift": 1, "mix": 0.5})
    s.connect("mains.port", "dut.inlet")
    s.connect("mains.port", "tap.cold")
    s.connect("dut.outlet", "tap.hot")
    r = s.solve()
    rise = r["dut.temperature_rise"]
    assert rise == pytest.approx(r["dut.outlet_temperature"] - 12.0, abs=1e-9)
    try:
        converted = r.get("dut.temperature_rise", unit="degC")
    except wp.WorldpartsError:
        return  # refusing the conversion is acceptable
    assert converted == pytest.approx(rise, abs=1e-9)


def test_defect_energy_balance_contract_detects_undelivered_heat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heater manifest's ``energy-balance`` contract must be able to fail.

    It compares heat_rate with ``m cp (dut.outlet_temperature - src.temperature)``, but
    both heat_rate and outlet_temperature are computed by the same ``heat()`` call in
    ``observables()``, so the check restates the definition of heat_rate. Mutation: the
    thermal map on the network branch delivers only half the power while the observables
    are unchanged. The water actually leaving the heater (sink.port.T) is then colder than
    reported, i.e. energy is not conserved (reported 17.14 kW at 3 bar, delivered about
    9 kW), yet the contract passes at every sweep point. An energy balance should use the
    network temperature downstream (e.g. ``sink.port.T``) instead of the heater's own
    outlet_temperature observable.
    """
    Heater = heater_module.InstantaneousWaterHeater

    def half_power_build(self: Any, nb: Any) -> None:
        from worldparts.laws import QuadraticResistance

        self.law = QuadraticResistance(1.0)

        def wrong(t_in: float, m: float) -> float:
            if not self.firing(m):
                return t_in
            p = self.parameters
            rise = 0.5 * self.inputs["enabled"] * p["max_power"] / (m * self.cp)
            return max(t_in, min(p["setpoint"], t_in + rise))

        self.branch = nb.branch(nb.port("inlet"), nb.port("outlet"), self.law, thermal=wrong)

    monkeypatch.setattr(Heater, "build", half_power_build)
    manifest = wp.default_catalog().get("instantaneous_water_heater")
    # Sanity: the mutation really breaks conservation of energy.
    r = heater_line(0.2).solve()
    delivered = r["dut.volume_flow"] / 60000 * RHO * CP * (r["sink.port.T"] - 12.0) / 1000
    assert delivered < 0.6 * r["dut.heat_rate"]
    outcome = wp.run_contract(manifest, "energy-balance")
    assert not outcome.passed
