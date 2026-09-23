"""Physics tests of the centrifugal pump (design 8.8) against hand calculations."""

from __future__ import annotations

import math

import numpy as np
import pytest
from fluids.friction import Churchill_1977

import worldparts as wp
from worldparts.components.pump import CentrifugalPump, fit_pump_curves
from worldparts.errors import InvalidValueError
from worldparts.laws import kv_to_k
from worldparts.media import RHO, G, vapour_pressure
from worldparts.units import P_ATM

H_PER_S = 3600.0  # m3/h per m3/s

# Default head curve at rated speed (design 8.8), flow in m3/h and head in m.
Q_TAB = np.array([0.0, 10.0, 20.0, 30.0, 36.0])
H_TAB = np.array([34.0, 32.5, 28.5, 21.5, 16.0])

# Hand calculation of the bounded fit H0 = a + b Q + c Q**2 (a > 0, b <= 0, c < 0).
# The unconstrained least-squares quadratic has b = +0.8587 m/(m3/s) > 0 (the drop from 0 to
# 10 m3/h, 1.5 m, is slightly less than the pure-quadratic trend predicts), so b = 0 is
# active and a, c are the ordinary regression of H on x = Q**2 (Q in m3/h):
#   x = [0, 100, 400, 900, 1296], mean x = 2696 / 5 = 539.2, mean H = 132.5 / 5 = 26.5
#   Sxx = sum(x**2) - 5 * 539.2**2 = 2659616 - 1453683.2 = 1205932.8
#   SxH = sum(x * H) - 5 * 539.2 * 26.5 = 54736 - 71444 = -16708
#   c = SxH / Sxx = -0.0138548350 m/(m3/h)**2 = -179558.662 m/(m3/s)**2
#   a = 26.5 - c * 539.2 = 33.9705270 m
# Residuals H0 - H: [-0.02947, +0.08504, -0.07141, +0.00118, +0.01466] m
#   RMS = sqrt(mean(residual**2)) = 0.0518003 m
A_HAND = 33.970527048
C_HAND_H = -16708.0 / 1205932.8  # m/(m3/h)**2
C_HAND = C_HAND_H * H_PER_S**2  # m/(m3/s)**2
RMS_HAND = 0.05180026


def _pump(system: wp.System, name: str = "dut") -> CentrifugalPump:
    comp = system.component(name)
    assert isinstance(comp, CentrifugalPump)
    return comp


def _circuit(kv: float = 15.0, opening: float = 1.0, speed: float = 1.0) -> wp.System:
    """Supply (0 bar) -> pump -> valve (Kv) -> drain."""
    s = wp.System("pump-circuit")
    s.add("src", "supply", pressure=0)
    s.add("dut", "centrifugal_pump", inputs={"speed": speed})
    s.add("v", "valve", parameters={"kv": kv}, inputs={"opening": opening})
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def test_unconstrained_fit_would_have_positive_b() -> None:
    # Why the bound is active: numpy's unconstrained quadratic fit in SI.
    coef = np.polyfit(Q_TAB / H_PER_S, H_TAB, 2)
    assert coef[1] > 0  # b = +0.8587 m/(m3/s)


def test_default_head_curve_fit() -> None:
    s = _circuit()
    fit = _pump(s).fit
    a, b, c = fit.head
    assert a == pytest.approx(A_HAND, rel=1e-7)
    assert b == 0.0  # the b <= 0 bound is active (bounded-variable least squares)
    assert c == pytest.approx(C_HAND, rel=1e-7)
    assert c <= -1e-9
    assert fit.head_rms == pytest.approx(RMS_HAND, rel=1e-5)
    # The fitted head at every curve point is within 1 m (here within 0.09 m).
    fitted = np.array([fit.head_at(q / H_PER_S) for q in Q_TAB])
    assert np.max(np.abs(fitted - H_TAB)) < 1.0
    assert np.max(np.abs(fitted - H_TAB)) == pytest.approx(0.0850435, abs=1e-6)
    # Reported in results in declared units.
    r = s.solve()
    assert r["dut.curve_fit_rms"] == pytest.approx(RMS_HAND, rel=1e-5)


def test_power_and_npsh_fits() -> None:
    # Plain least-squares quadratics; compare with numpy (Q in m3/h, power in kW):
    #   P0 = 1.48557 + 0.0586066 Q - 3.35343e-4 Q**2 kW
    #   NPSHr0 = 1.43546 - 0.0374449 Q + 3.46916e-3 Q**2 m
    fit = _pump(_circuit()).fit
    p0, p1, p2 = fit.power
    assert p0 == pytest.approx(1485.57082, rel=1e-6)
    assert p1 / H_PER_S / 1e3 == pytest.approx(0.0586065595, rel=1e-6)
    assert p2 / H_PER_S**2 / 1e3 == pytest.approx(-3.35342667e-4, rel=1e-6)
    n0, n1, n2 = fit.npsh
    assert n0 == pytest.approx(1.43546256, rel=1e-6)
    assert n1 / H_PER_S == pytest.approx(-0.03744493, rel=1e-6)
    assert n2 / H_PER_S**2 == pytest.approx(0.00346916, rel=1e-5)


def test_operating_point_closed_form() -> None:
    # Supply (0 bar) -> pump -> valve Kv -> drain (0 bar). The valve needs
    #   dp = (m / k)**2 with k = Kv_SI * sqrt(rho * 1000 / 1e5), m = rho Q
    #   => H_valve = dp / (rho g) = Q**2 * 1e5 / (1000 * g * Kv_SI**2)
    # and the pump gives H = a + b Q + c Q**2, so
    #   (c - 1e5 / (1000 g Kv_SI**2)) Q**2 + b Q + a = 0
    # With Kv = 15 m3/h (hand numbers in m3/h): Q**2 (0.0138548 + 100 / (g * 225)) = 33.9705
    #   => Q = 23.9596 m3/h, H = 26.0170 m.
    for kv_h in (5.0, 15.0, 30.0):
        s = _circuit(kv=kv_h)
        a, b, c = _pump(s).fit.head
        kv_si = kv_h / H_PER_S
        cc = c - 1e5 / (1000.0 * G * kv_si**2)
        q = (-b - math.sqrt(b * b - 4.0 * cc * a)) / (2.0 * cc)
        h = a + b * q + c * q * q
        r = s.solve()
        assert r.converged
        assert r["dut.volume_flow"] == pytest.approx(q * H_PER_S, rel=0.005)
        assert r["dut.head"] == pytest.approx(h, rel=0.005)
        if kv_h == 15.0:
            assert q * H_PER_S == pytest.approx(23.95962, rel=1e-6)
            assert h == pytest.approx(26.01697, rel=1e-6)
            # Actually far tighter than 0.5 %: only the 1 Pa/(kg/s) law term differs.
            assert r["dut.volume_flow"] == pytest.approx(q * H_PER_S, rel=5e-5)


def test_shutoff_head_scales_with_speed_squared() -> None:
    # Valve closed (seat leakage only): H = a s**2 (+ c Q|Q| with Q ~ 0.003 m3/h: < 2e-6 m).
    s = _circuit(opening=0.0)
    for speed in (0.25, 0.5, 0.8, 1.0, 1.2):
        s.set("dut.speed", speed)
        r = s.solve()
        assert r["dut.head"] == pytest.approx(A_HAND * speed**2, abs=1e-4)
        assert r.modes["dut"] == "low_flow"
        assert r.has_warning("dut.low_flow")
        assert r["dut.speed_rpm"] == pytest.approx(2900 * speed, rel=1e-12)


def test_stopped_pump_is_a_resistance() -> None:
    # speed 0: dp = -rho g c Q|Q| + eps m (eps = 1 Pa per kg/s), so between 1 bar and
    # atmosphere rho g |c| Q**2 + eps rho Q = 1e5 Pa:
    #   Q = (-eps rho + sqrt((eps rho)**2 + 4 rho g |c| 1e5)) / (2 rho g |c|) = 27.1528 m3/h
    s = wp.System("stopped")
    s.add("src", "supply", pressure=1.0)
    s.add("dut", "centrifugal_pump", inputs={"speed": 0})
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    r = s.solve()
    assert r.converged
    k2 = RHO * G * -C_HAND
    q = (-RHO + math.sqrt(RHO**2 + 4.0 * k2 * 1e5)) / (2.0 * k2)
    assert r["dut.volume_flow"] == pytest.approx(q * H_PER_S, rel=1e-6)
    assert q * H_PER_S == pytest.approx(27.15275, rel=1e-6)
    assert r["dut.head"] == pytest.approx(-1e5 / (RHO * G), rel=1e-6)
    assert r["dut.shaft_power"] == 0.0
    assert r["dut.efficiency"] == 0.0
    assert r.modes["dut"] == "off"
    # No driving pressure at all: zero flow, still converges (the law slope at zero is eps).
    s.set("src.pressure", 0.0)
    r = s.solve()
    assert r.converged
    assert abs(r["dut.volume_flow"]) < 1e-9
    # Reverse driving pressure through the stopped pump: symmetric resistance.
    s.set("src.pressure", -0.5)
    r = s.solve()
    assert r.converged
    assert r["dut.volume_flow"] < 0
    assert r.modes["dut"] == "off"
    assert r.has_warning("dut.reverse_flow")


def test_best_efficiency_point_and_efficiency_definition() -> None:
    # BEP from a dense grid of eta = rho g Q H0(Q) / P0(Q) over [0, 36] m3/h with the fitted
    # coefficients (independent of the component's optimiser): Q_bep = 24.111 m3/h,
    # eta_bep = 62.844 %.
    fit = _pump(_circuit()).fit
    qs = np.linspace(0.0, 36.0, 360001) / H_PER_S
    a, b, c = fit.head
    p0, p1, p2 = fit.power
    eta = RHO * G * qs * (a + b * qs + c * qs**2) / (p0 + p1 * qs + p2 * qs**2)
    k = int(np.argmax(eta))
    assert fit.bep_flow == pytest.approx(qs[k], abs=2e-4 / H_PER_S)
    assert fit.bep_efficiency == pytest.approx(eta[k], rel=1e-9)
    assert fit.bep_flow * H_PER_S == pytest.approx(24.111, abs=0.001)
    assert fit.bep_efficiency == pytest.approx(0.628442, abs=1e-6)

    # A valve sized so the operating point is the BEP: H0(Q_bep) = Q_bep**2 1e5 / (1000 g Kv**2)
    # => Kv = Q_bep * sqrt(1e5 / (1000 g H0(Q_bep))).
    q_bep = fit.bep_flow
    kv_si = q_bep * math.sqrt(1e5 / (1000.0 * G * fit.head_at(q_bep)))
    s = _circuit(kv=kv_si * H_PER_S)
    r = s.solve()
    q = r["dut.volume_flow"] / H_PER_S
    h = r["dut.head"]
    p_shaft = r["dut.shaft_power"] * 1e3
    assert q == pytest.approx(q_bep, rel=1e-4)
    assert r["dut.bep_flow"] == pytest.approx(q_bep * H_PER_S, rel=1e-12)
    assert p_shaft == pytest.approx(fit.power_at(q_bep), rel=1e-4)
    # eta = rho g Q H / P, both from the reported values and at the BEP.
    assert r["dut.hydraulic_power"] * 1e3 == pytest.approx(RHO * G * q * h, rel=1e-12)
    assert r["dut.efficiency"] / 100 == pytest.approx(RHO * G * q * h / p_shaft, rel=1e-12)
    assert r["dut.efficiency"] / 100 == pytest.approx(fit.bep_efficiency, rel=1e-4)

    # Affinity: at 80 % speed the BEP flow scales by 0.8 and the efficiency at the scaled
    # point is unchanged (H ~ s**2, P ~ s**3 at Q ~ s).
    s.set("dut.speed", 0.8)
    r = s.solve()
    assert r["dut.bep_flow"] == pytest.approx(0.8 * q_bep * H_PER_S, rel=1e-12)
    assert r["dut.volume_flow"] == pytest.approx(0.8 * q_bep * H_PER_S, rel=1e-4)
    assert r["dut.efficiency"] / 100 == pytest.approx(fit.bep_efficiency, rel=1e-4)
    assert r["dut.shaft_power"] * 1e3 == pytest.approx(0.8**3 * fit.power_at(q_bep), rel=1e-3)


def _suction_system(level: float) -> wp.System:
    s = wp.System("suction")
    s.add("tank", "tank", initial_level=level)
    s.add(
        "suction",
        "pipe",
        length=100,
        diameter=65,
        roughness=0.045,
        height_difference=3,
    )
    s.add("dut", "centrifugal_pump")
    s.add("v", "valve", kv=15)
    s.add("sink", "drain")
    s.connect("tank.outlet", "suction.port_a")
    s.connect("suction.port_b", "dut.inlet")
    s.connect("dut.outlet", "v.port_a")
    s.connect("v.port_b", "sink.port")
    return s


def test_npsh_available_and_cavitation() -> None:
    # Hand calculation at the solved flow Q (the flow itself is the pump/system intersection):
    #   p_inlet_abs = P_ATM + rho g h_tank - dp_port - (f L/D + 0) rho v**2 / 2 - rho g * 3
    # with dp_port = (m / k_port)**2 (Kv 200 m3/h), f from fluids' Churchill (1977) and
    #   NPSHa = (p_inlet_abs - p_v(15 degC)) / (rho g), p_v(15 degC) = 1705.8 Pa (IAPWS-IF97).
    # At h_tank = 0.3 m, Q = 21.0 m3/h: v = 1.758 m/s, f = 0.02091, friction 5.06 m,
    #   NPSHa = (101325 - 1705.8) / 9789.0 + 0.3 - 3 - 5.06 - 0.001 = 2.30 m,
    # below NPSHr(21.0) = 2.18 m plus the 0.5 m margin, so the pump cavitates.
    s = _suction_system(0.3)
    r = s.solve()
    q = r["dut.volume_flow"] / H_PER_S
    m = RHO * q
    d, length = 0.065, 100.0
    area = math.pi * d**2 / 4
    v = q / area
    re = RHO * v * d / 1.002e-3
    f = Churchill_1977(re, 0.045e-3 / d)
    dp_port = (m / kv_to_k(200.0 / H_PER_S, RHO)) ** 2
    p_in = P_ATM + RHO * G * 0.3 - dp_port - f * length / d * RHO * v * v / 2 - RHO * G * 3.0
    p_v = vapour_pressure(273.15 + 15.0)
    assert p_v == pytest.approx(1705.8, abs=0.5)
    npsh_a = (p_in - p_v) / (RHO * G)
    assert r["dut.npsh_available"] == pytest.approx(npsh_a, rel=0.005)
    assert npsh_a == pytest.approx(2.30, abs=0.02)
    assert r["dut.inlet.p"] == pytest.approx((p_in - P_ATM) / 1e5, abs=1e-4)
    npsh_r = r["dut.npsh_required"]
    assert npsh_r == pytest.approx(_pump(s).fit.npsh_required_at(q), rel=1e-9)
    assert npsh_a < npsh_r + 0.5
    assert r.has_warning("dut.cavitation")
    assert r.modes["dut"] == "cavitating"


@pytest.mark.parametrize("level", [0.0, 0.05, 0.3, 0.8, 1.5, 2.5, 3.0])
def test_cavitation_warning_and_mode_agree(level: float) -> None:
    r = _suction_system(level).solve()
    npsh_a = r["dut.npsh_available"]
    cond = npsh_a < 0 or (
        r["dut.volume_flow"] > 0.01 and npsh_a < r["dut.npsh_required"] + r["dut.npsh_margin"]
    )
    assert r.has_warning("dut.cavitation") == cond
    assert (r.modes["dut"] == "cavitating") == cond


def test_cavitation_goes_away_with_a_full_tank() -> None:
    assert _suction_system(0.3).solve().has_warning("dut.cavitation")
    r = _suction_system(3.0).solve()
    assert not r.has_warning("dut.cavitation")
    assert r.modes["dut"] == "running"


def test_curve_refit_after_table_change() -> None:
    # Scaling every head by 1.5 scales a and c by 1.5 (b stays at its bound 0).
    s = _circuit(opening=0.0)
    s.set("dut.head_curve", [[q, 1.5 * h] for q, h in zip(Q_TAB, H_TAB, strict=True)])
    r = s.solve()
    assert r["dut.head"] == pytest.approx(1.5 * A_HAND, abs=1e-4)
    assert r["dut.curve_fit_rms"] == pytest.approx(1.5 * RMS_HAND, rel=1e-5)
    # Cells may carry units (L/s -> m3/h is a factor of 3.6).
    s.set("dut.head_curve", [["0 L/s", 34], ["5 L/s", 30], ["10 L/s", 16]])
    fit = _pump(s).fit
    assert fit.max_flow == pytest.approx(0.010, rel=1e-12)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ([[0, 34], [20, 30], [10, 20]], "strictly increasing"),
        ([[0, 20], [10, 25], [20, 30]], "fall with flow"),
    ],
)
def test_invalid_head_curves_are_rejected(table: list[list[float]], message: str) -> None:
    s = _circuit()
    with pytest.raises(InvalidValueError, match=message):
        s.set("dut.head_curve", table)


def test_non_positive_power_fit_rejected() -> None:
    s = _circuit()
    with pytest.raises(InvalidValueError, match="power"):
        s.set("dut.power_curve", [[0, 0], [10, 0], [36, 0]])


def test_fit_function_is_pure() -> None:
    fit_a = _pump(_circuit()).fit
    comp = _pump(_circuit())
    p = comp.parameters
    fit_b = fit_pump_curves(p["head_curve"], p["power_curve"], p["npsh_curve"], RHO)
    assert fit_a == fit_b


def test_specific_energy_hand_calculation() -> None:
    """Specific energy = shaft power / flow, in kWh/m3.

    Operating point through Kv 15: Q = 23.95962 m3/h (closed form above). Shaft power from
    the power fit P0 = 1.48557 + 0.0586066 Q - 3.35343e-4 Q**2 kW at Q = 23.95962 is
    1.48557 + 1.40418 - 0.19250 = 2.69725 kW, so E = 2.69725 / 23.95962 = 0.112576 kWh/m3.
    Cross-check with E = rho g H / eta = 998.2 * 9.80665 * 26.01697 / 0.62840 / 3.6e6.
    """
    s = _circuit()
    r = s.solve()
    p0, p1, p2 = _pump(s).fit.power
    q = r["dut.volume_flow"] / H_PER_S
    p_hand = p0 + p1 * q + p2 * q * q  # W
    e_hand = p_hand / q / 3.6e6  # J/m3 -> kWh/m3
    assert r.unit("dut.specific_energy") == "kWh/m3"
    assert r["dut.specific_energy"] == pytest.approx(e_hand, rel=1e-12)
    assert r["dut.specific_energy"] == pytest.approx(0.1125757, rel=1e-5)
    assert r["dut.specific_energy"] == pytest.approx(RHO * G * 26.01697 / 0.62840 / 3.6e6, rel=2e-4)
    # Energy per volume is not a pressure: no gauge offset, no reference.
    assert "dut.specific_energy" not in r.references
    assert r.get("dut.specific_energy", unit="kJ/L") == pytest.approx(
        r["dut.specific_energy"] * 3.6, rel=1e-12
    )
    assert r.get("dut.specific_energy", unit="MJ/m3") == pytest.approx(
        r["dut.specific_energy"] * 3.6, rel=1e-12
    )


def test_specific_energy_is_none_without_forward_flow() -> None:
    """No delivered water, no energy per volume: None at zero or reverse flow.

    A stopped pump passing water (1 bar supply, the stopped-pump-resistance scenario) takes
    no shaft power, so its specific energy is 0.
    """
    s = wp.System("reverse")
    s.add("sink", "drain")
    s.add("dut", "centrifugal_pump")
    s.add("src", "supply", pressure=4)
    s.connect("sink.port", "dut.inlet")
    s.connect("dut.outlet", "src.port")
    r = s.solve()
    assert r["dut.volume_flow"] < 0 and r["dut.specific_energy"] is None
    idle = _circuit(speed=0.0).solve()  # 0 bar on both sides: no flow
    assert idle["dut.volume_flow"] == 0.0 and idle["dut.specific_energy"] is None
    s = wp.System("stopped")
    s.add("src", "supply", pressure=1)
    s.add("dut", "centrifugal_pump", inputs={"speed": 0})
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    stopped = s.solve()
    assert stopped["dut.volume_flow"] > 0 and stopped["dut.specific_energy"] == 0.0
