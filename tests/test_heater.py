"""Physics of the instantaneous water heater (design 8.7) against hand calculations.

Line used throughout: supply (3 bar gauge, 12 degC) -> heater (Kv 1.5) -> valve (Kv v) ->
drain. The heater and valve are in series: ``1 / Kv_eq**2 = 1 / 1.5**2 + 1 / v**2`` and
``Q [m3/h] = Kv_eq * sqrt(dp [bar] / SG)`` with ``SG = 0.9982``.
"""

from __future__ import annotations

import math

import pytest
from scipy.optimize import brentq

import worldparts as wp
from worldparts.components import heater as heater_module
from worldparts.media import CP, RHO

SG = RHO / 1000.0
T_IN = 12.0
SETPOINT = 55.0
P_MAX = 18000.0  # W


def line(valve_kv: float, pressure: float = 3.0, **heater: object) -> wp.System:
    s = wp.System("heater-line")
    s.add("src", "supply", pressure=pressure, temperature=T_IN)
    s.add("dut", "instantaneous_water_heater", **heater)
    s.add("v", "valve", kv=valve_kv)
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def hand_mass_flow(valve_kv: float, pressure: float = 3.0) -> float:
    """Mass flow in kg/s through the series heater + valve line."""
    kv_eq = 1.0 / math.sqrt(1.0 / 1.5**2 + 1.0 / valve_kv**2)
    q_m3h = kv_eq * math.sqrt(pressure / SG)
    return q_m3h / 3600.0 * RHO


def codes(result: wp.SolveResult) -> set[str]:
    return {w.path for w in result.warnings}


def test_low_flow_reaches_setpoint() -> None:
    # Valve Kv 0.2: Kv_eq = 1 / sqrt(1/2.25 + 1/0.04) = 0.198236 m3/h
    #   Q = 0.198236 * sqrt(3 / 0.9982) = 0.343681 m3/h = 5.728 L/min (> 2 L/min activation)
    #   m = 0.343681 / 3600 * 998.2 = 0.095295 kg/s
    #   power to reach the setpoint: m * cp * (55 - 12) = 0.095295 * 4182 * 43 = 17.137 kW
    #   < 18 kW, so T_out = 55 degC and heat_rate = m * cp * (T_set - T_in).
    m = hand_mass_flow(0.2)
    need = m * CP * (SETPOINT - T_IN)
    assert need == pytest.approx(17136.5, rel=1e-4)
    assert need < P_MAX
    r = line(0.2).solve()
    assert r["dut.volume_flow"] == pytest.approx(m / RHO * 60000, rel=1e-6)
    assert r["dut.outlet_temperature"] == pytest.approx(SETPOINT, abs=1e-9)
    assert r["dut.temperature_rise"] == pytest.approx(SETPOINT - T_IN, abs=1e-9)
    assert r["dut.heat_rate"] == pytest.approx(need / 1000, rel=1e-6)
    assert r["sink.port.T"] == pytest.approx(SETPOINT, abs=1e-9)  # propagated downstream
    assert r.modes["dut"] == "heating"
    assert codes(r) == set()


def test_high_flow_saturates() -> None:
    # Valve Kv 0.3: Kv_eq = 0.294174 m3/h, Q = 8.4997 L/min, m = 0.141407 kg/s
    #   needed: 0.141407 * 4182 * 43 = 25.43 kW > 18 kW -> saturated
    #   T_out = T_in + P_max / (m * cp) = 12 + 18000 / (0.141407 * 4182) = 42.438 degC
    m = hand_mass_flow(0.3)
    t_out = T_IN + P_MAX / (m * CP)
    assert t_out == pytest.approx(42.438, abs=1e-3)
    r = line(0.3).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(t_out, abs=1e-6)
    assert r["dut.heat_rate"] == pytest.approx(P_MAX / 1000, rel=1e-9)
    assert r.modes["dut"] == "saturated"
    assert "dut.setpoint_not_met" in codes(r)


def test_enabled_scales_available_power() -> None:
    # Half power at the same flow: T_out = 12 + 9000 / (m * cp).
    m = hand_mass_flow(0.3)
    r = line(0.3, enabled=0.5).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(T_IN + 0.5 * P_MAX / (m * CP), abs=1e-6)
    assert r["dut.heat_rate"] == pytest.approx(9.0, rel=1e-9)
    r = line(0.3, enabled=0.0).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(T_IN, abs=1e-9)
    assert r["dut.heat_rate"] == pytest.approx(0.0, abs=1e-12)


def test_switched_off_heater_is_idle_not_saturated() -> None:
    # enabled = 0 at 8.5 L/min (above the activation flow): nothing is heated, so the mode
    # is idle (not "firing at the power limit"); setpoint_not_met is still raised because
    # water flows and the outlet is 43 K below the setpoint, and its message names the
    # switched-off case.
    r = line(0.3, enabled=0.0).solve()
    assert r["dut.volume_flow"] > 2.0
    assert r.modes["dut"] == "idle"
    assert codes(r) == {"dut.setpoint_not_met"}
    message = next(w.message for w in r.warnings if w.code == "setpoint_not_met")
    assert "enabled = 0" in message


def test_no_heating_below_activation_flow() -> None:
    # Valve Kv 0.05: Kv_eq = 1 / sqrt(1/2.25 + 400) = 0.049972 m3/h,
    #   Q = 0.049972 * sqrt(3 / 0.9982) m3/h = 1.4439 L/min < 2 L/min -> heater off.
    m = hand_mass_flow(0.05)
    assert m / RHO * 60000 == pytest.approx(1.4439, abs=1e-4)
    r = line(0.05).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(T_IN, abs=1e-9)
    assert r["dut.heat_rate"] == 0.0
    assert r["dut.temperature_rise"] == 0.0
    assert r.modes["dut"] == "idle"
    assert codes(r) == {"dut.below_activation_flow"}
    # Lowering the activation flow below the actual flow makes it fire and reach setpoint
    # (needed power m * cp * 43 = 4.32 kW < 18 kW).
    r = line(0.05, activation_flow=1.0).solve()
    assert r["dut.outlet_temperature"] == pytest.approx(SETPOINT, abs=1e-9)
    assert r["dut.heat_rate"] == pytest.approx(m * CP * 43 / 1000, rel=1e-6)


def test_reverse_flow_is_not_heated() -> None:
    s = wp.System()
    s.add("src", "supply", pressure=3.0, temperature=20.0)
    s.add("dut", "instantaneous_water_heater")
    s.add("sink", "drain")
    s.connect("src.port", "dut.outlet")  # backwards
    s.connect("dut.inlet", "sink.port")
    r = s.solve()
    assert r["dut.volume_flow"] < -2.0
    assert r["dut.outlet_temperature"] == pytest.approx(20.0, abs=1e-9)
    assert r["dut.heat_rate"] == 0.0
    assert r["sink.port.T"] == pytest.approx(20.0, abs=1e-9)
    assert r.modes["dut"] == "idle"


def test_never_cools_water_above_setpoint() -> None:
    # Water arriving at 60 degC with a 40 degC setpoint leaves at 60 degC (design 8.7's
    # min(setpoint, ...) alone would cool it to 40 degC; see the heater module docstring).
    s = line(0.2, setpoint=40.0)
    s.set("src.temperature", 60.0)
    res = s.solve()
    assert res["dut.outlet_temperature"] == pytest.approx(60.0, abs=1e-9)
    assert res["dut.heat_rate"] == pytest.approx(0.0, abs=1e-12)
    assert res.modes["dut"] == "idle"  # nothing to heat
    assert codes(res) == set()


def test_dead_ended_heater_is_idle_with_zero_activation_flow() -> None:
    # Outlet capped: exactly zero flow, so the flow switch cannot close even when
    # activation_flow = 0 (the idle mode must not fall through to "heating").
    s = wp.System("dead-end")
    s.add("src", "supply", pressure=3.0, temperature=T_IN)
    s.add("dut", "instantaneous_water_heater", activation_flow=0)
    s.connect("src.port", "dut.inlet")
    r = s.solve()
    assert r["dut.volume_flow"] == 0.0
    assert r["dut.heat_rate"] == 0.0
    assert r.modes["dut"] == "idle"
    assert codes(r) == set()


@pytest.mark.parametrize("valve_kv", [0.05, 0.1, 0.2, 0.3, 0.5, 1.0])
def test_flow_equal_to_activation_flow_does_not_fire(valve_kv: float) -> None:
    # The flow switch fires only above activation_flow ("at or below which the flow switch
    # keeps the heater off"). With activation_flow set to the line's own hand-calculated
    # flow, the heater stays off and the mode and warnings agree with that.
    q = hand_mass_flow(valve_kv) / RHO * 60000
    s = line(valve_kv)
    assert s.solve()["dut.volume_flow"] == pytest.approx(q, rel=1e-6)
    s.set("dut.activation_flow", s.solve()["dut.volume_flow"])
    r = s.solve()
    assert r["dut.outlet_temperature"] == pytest.approx(T_IN, abs=1e-12)
    assert r["dut.heat_rate"] == 0.0
    assert r.modes["dut"] == "idle"
    assert codes(r) == {"dut.below_activation_flow"}


@pytest.mark.parametrize("pressure", [0.05, 0.3, 1.0, 2.0, 3.0, 4.5, 6.0])
def test_heat_rate_energy_balance(pressure: float) -> None:
    # heat_rate = m * cp * (T_out - T_in) at every operating point.
    r = line(0.3, pressure=pressure).solve()
    m = r["dut.inlet.m_flow"]
    assert m == pytest.approx(-r["dut.outlet.m_flow"], abs=1e-12)  # mass conservation
    q = m * CP * (r["dut.outlet_temperature"] - T_IN) / 1000
    assert r["dut.heat_rate"] == pytest.approx(q, rel=1e-9, abs=1e-12)
    assert r["dut.heat_rate"] <= P_MAX / 1000 * (1 + 1e-12)


def test_warning_iff_sweeps_are_not_vacuous() -> None:
    seen_nm, seen_ba = set(), set()
    for i in range(13):
        r = line(0.2, pressure=0.05 + i * (6 - 0.05) / 12).solve()
        seen_nm.add("dut.setpoint_not_met" in codes(r))
    for pressure in (0, 0.5, 1, 2, 3, 4, 5, 5.7, 5.8, 6):
        r = line(0.05, pressure=pressure).solve()
        seen_ba.add("dut.below_activation_flow" in codes(r))
    assert seen_nm == {True, False}
    assert seen_ba == {True, False}


# -- heater + faucet chain -------------------------------------------------------------------


def test_heater_faucet_chain_energy_balance() -> None:
    # Bathroom: mains (3.5 bar, 12 degC) -> heater -> faucet.hot, and mains -> faucet.cold;
    # faucet lever fully open, half mixed.
    s = wp.System("bathroom")
    s.add("mains", "supply", pressure=3.5, temperature=T_IN)
    s.add("heater", "instantaneous_water_heater")
    s.add("tap", "mixing_faucet", inputs={"lift": 1.0, "mix": 0.5})
    s.connect("mains.port", "heater.inlet")
    s.connect("mains.port", "tap.cold")
    s.connect("heater.outlet", "tap.hot")
    r = s.solve()

    # Hand solve. Hot path = heater Kv 1.5 in series with the hot cartridge Kv 0.3:
    #   Kv_hp = 1 / sqrt(1/1.5**2 + 1/0.3**2) = 0.294174 m3/h. Cold path Kv 0.3.
    #   Both paths run from 3.5 bar to the mixing pressure pC; spout Kv 0.8 from pC to 0:
    #   (Kv_hp + Kv_c) * sqrt((3.5 - pC) / SG) = 0.8 * sqrt(pC / SG)
    #   => pC = 3.5 * (Kv_hp + Kv_c)**2 / ((Kv_hp + Kv_c)**2 + 0.64)
    kv_c = 0.6 * (1e-6 + (1 - 1e-6) * 0.5)
    kv_hp = 1.0 / math.sqrt(1 / 1.5**2 + 1 / kv_c**2)
    s_in = (kv_hp + kv_c) ** 2
    pc = 3.5 * s_in / (s_in + 0.64)
    assert pc == pytest.approx(
        brentq(lambda x: (kv_hp + kv_c) * math.sqrt(3.5 - x) - 0.8 * math.sqrt(x), 0, 3.5),
        rel=1e-9,
    )
    m_hot = kv_hp * math.sqrt((3.5 - pc) / SG) / 3600 * RHO  # kg/s, about 0.1226
    m_cold = kv_c * math.sqrt((3.5 - pc) / SG) / 3600 * RHO
    # m_hot * cp * 43 = 22.05 kW > 18 kW: the heater saturates.
    t_hot = T_IN + P_MAX / (m_hot * CP)  # 47.10 degC
    t_mix = (m_hot * t_hot + m_cold * T_IN) / (m_hot + m_cold)  # 29.38 degC

    assert r["heater.volume_flow"] == pytest.approx(m_hot / RHO * 60000, rel=1e-5)
    assert r["heater.outlet_temperature"] == pytest.approx(t_hot, abs=1e-4)
    assert r["tap.hot.T"] == pytest.approx(t_hot, abs=1e-4)
    assert r["tap.temperature"] == pytest.approx(t_mix, abs=1e-4)
    assert r.modes["heater"] == "saturated"

    # Energy balance over the whole chain (enthalpy relative to T_IN):
    #   heat added by the heater = enthalpy leaving the spout - enthalpy entering from mains
    m_mains = r["mains.volume_flow"] / 60000 * RHO
    m_spout = r["tap.flow"] / 60000 * RHO
    assert m_spout == pytest.approx(m_mains, rel=1e-9)
    heat_out = m_spout * CP * (r["tap.temperature"] - T_IN)  # W
    assert heat_out == pytest.approx(r["heater.heat_rate"] * 1000, rel=1e-9)
    assert heat_out == pytest.approx(P_MAX, rel=1e-9)


# -- contracts can fail ----------------------------------------------------------------------


@pytest.mark.parametrize("contract", ["energy-balance", "rise-matches-port-temperatures"])
def test_thermal_contracts_detect_undelivered_heat(
    contract: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: the network's thermal map delivers half the power while the observables
    still report full power. Contracts that compare the observables with network
    temperatures must fail; with the real model they pass."""
    manifest = wp.default_catalog().get("instantaneous_water_heater")
    assert wp.run_contract(manifest, contract).passed

    heater_cls = heater_module.InstantaneousWaterHeater
    real_build = heater_cls.build

    def half_power_build(self: heater_module.InstantaneousWaterHeater, nb: object) -> None:
        real_build(self, nb)  # type: ignore[arg-type]

        def half(t_in: float, m: float) -> float:
            return t_in + 0.5 * (self.heat(t_in, m) - t_in)

        self.branch.thermal = half

    monkeypatch.setattr(heater_cls, "build", half_power_build)
    assert not wp.run_contract(manifest, contract).passed
