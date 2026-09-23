"""Physics of the treatment components: media filter and UV reactor (design 8.10 and 8.11).

Expected values are hand calculations written out in the comments, not model output.
Units in the comments: flows in m3/h, pressures in bar, x = Q / Q_rated.
"""

from __future__ import annotations

import itertools
import math
import warnings

import pytest

import worldparts as wp
from worldparts.components.treatment import filter_coefficients
from worldparts.laws import kv_to_k
from worldparts.media import RHO


def line(component: str, pressure: float, **values: object) -> wp.System:
    """supply -> component (inlet, outlet) -> drain."""
    s = wp.System(component)
    s.add("src", "supply", pressure=pressure)
    s.add("dut", component, **values)
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    return s


def filter_dp(x: float, dp_clean: float, h: float, c: float) -> float:
    """Hand formula of design 8.10 in bar at x = Q / Q_rated (forward flow)."""
    return dp_clean * ((1 - h) * x / (1 - c) + h * x * x)


# -- media filter ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rated_flow", "dp_clean", "h"),
    [(20.0, 0.2, 0.3), (5.0, 0.5, 0.0), (100.0, 0.05, 1.0), (0.5, 1.2, 0.6)],
)
def test_filter_clean_drop_at_rated_flow(rated_flow: float, dp_clean: float, h: float) -> None:
    """A clean filter drops exactly clean_pressure_drop at the rated flow.

    Hand calculation: dp(Q_rated) = (1 - h) * dp_clean + h * dp_clean = dp_clean. So a supply
    at dp_clean bar gauge discharging to atmosphere drives exactly Q_rated.
    """
    s = line(
        "media_filter",
        pressure=dp_clean,
        rated_flow=rated_flow,
        clean_pressure_drop=dp_clean,
        housing_fraction=h,
        change_pressure_drop=50,
    )
    r = s.solve()
    assert r["dut.volume_flow"] == pytest.approx(rated_flow, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(dp_clean, rel=1e-7)
    assert r["dut.dp_ratio"] == pytest.approx(1.0, abs=1e-12)
    assert r.modes["dut"] == "clean"


def test_filter_linear_quadratic_split() -> None:
    """The split of the clean drop follows housing_fraction.

    Coefficients (SI) with Q_r = 20 m3/h = 0.0055556 m3/s, dp_clean = 0.2 bar = 20000 Pa,
    h = 0.3: r_lin * Q_r = 0.7 * 20000 = 14000 Pa and r_quad * Q_r**2 = 0.3 * 20000 = 6000 Pa.
    At half the rated flow the drop is 14000 * 0.5 + 6000 * 0.25 = 8500 Pa = 0.085 bar (the
    linear part is halved, the quadratic part quartered), so a 0.085 bar supply gives 10 m3/h.
    """
    q_r = 20.0 / 3600.0
    r_lin, r_quad = filter_coefficients(q_r, 20000.0, 0.3, 0.0)
    assert r_lin * q_r == pytest.approx(14000.0, rel=1e-12)
    assert r_quad * q_r**2 == pytest.approx(6000.0, rel=1e-12)
    # Clogging only scales the linear part: c = 0.75 multiplies it by 4.
    r_lin_c, r_quad_c = filter_coefficients(q_r, 20000.0, 0.3, 0.75)
    assert r_lin_c == pytest.approx(4.0 * r_lin, rel=1e-12)
    assert r_quad_c == r_quad

    r = line("media_filter", pressure=0.085).solve()
    assert r["dut.volume_flow"] == pytest.approx(10.0, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(0.085, rel=1e-7)

    # Pure media (h = 0) is linear: 0.1 bar drives half of 20 m3/h. Pure housing (h = 1) is
    # quadratic: 0.05 bar drives 20 * sqrt(0.05 / 0.2) = 10 m3/h.
    assert line("media_filter", 0.1, housing_fraction=0.0).solve()[
        "dut.volume_flow"
    ] == pytest.approx(10.0, rel=1e-7)
    assert line("media_filter", 0.05, housing_fraction=1.0).solve()[
        "dut.volume_flow"
    ] == pytest.approx(10.0, rel=1e-7)


@pytest.mark.parametrize("clogging", [0.0, 0.1, 0.3, 0.5, 0.8, 0.9, 0.99])
def test_filter_dp_ratio_at_rated_flow(clogging: float) -> None:
    """At rated flow dp_ratio = (1 - h) / (1 - c) + h.

    Hand calculation (defaults h = 0.3, dp_clean = 0.2 bar): driving the filter with a
    supply of 0.2 * ((1 - h) / (1 - c) + h) bar gives exactly 20 m3/h, and the clean drop at
    that flow is 0.2 bar, so dp_ratio = 0.7 / (1 - c) + 0.3: 1 at c = 0, 1.07778 at c = 0.1,
    1.7 at c = 0.5, 7.3 at c = 0.9 and 70.3 at c = 0.99.
    """
    ratio = 0.7 / (1.0 - clogging) + 0.3
    r = line("media_filter", pressure=0.2 * ratio, clogging=clogging).solve()
    assert r["dut.volume_flow"] == pytest.approx(20.0, rel=1e-7)
    assert r["dut.dp_ratio"] == pytest.approx(ratio, rel=1e-9)
    assert r["dut.pressure_drop"] == pytest.approx(0.2 * ratio, rel=1e-7)


def test_filter_dp_ratio_rises_with_clogging() -> None:
    """dp_ratio is 1 when clean and rises strictly with clogging at a fixed flow."""
    ratios = []
    for c in (0.0, 0.2, 0.4, 0.6, 0.8, 0.99):
        ratio = 0.7 / (1.0 - c) + 0.3
        ratios.append(line("media_filter", 0.2 * ratio, clogging=c).solve()["dut.dp_ratio"])
    assert ratios[0] == pytest.approx(1.0, abs=1e-12)
    assert all(b > a for a, b in itertools.pairwise(ratios))


def test_filter_dp_ratio_at_other_flows() -> None:
    """Away from rated flow the clean filter still has dp_ratio 1 and a clogged one follows
    ((1 - h) / (1 - c) + h x) / (1 - h + h x).

    Example: c = 0.5, x = 0.5: (1.4 + 0.15) / (0.7 + 0.15) = 1.55 / 0.85 = 1.823529.
    The supply that drives x = 0.5 is 0.2 * (1.4 * 0.5 + 0.3 * 0.25) = 0.155 bar.
    """
    for p in (0.01, 0.2, 1.0, 5.0):
        assert line("media_filter", p).solve()["dut.dp_ratio"] == pytest.approx(1.0, abs=1e-12)
    r = line("media_filter", 0.155, clogging=0.5).solve()
    assert r["dut.volume_flow"] == pytest.approx(10.0, rel=1e-7)
    assert r["dut.dp_ratio"] == pytest.approx(1.55 / 0.85, rel=1e-9)


def test_filter_zero_flow_is_idle_and_finite() -> None:
    """At zero flow the filter is idle and dp_ratio is the limit of the slope ratio.

    Slope at zero flow: r_lin / (1 - c) + r_quad * q_eps versus r_lin + r_quad * q_eps; with
    q_eps = 1e-4 * Q_r: (0.7 / (1 - c) + 0.3e-4) / (0.7 + 0.3e-4) in rated-point units, so
    for c = 0.5: (1.4 + 0.00003) / (0.70003) = 1.9999571.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        r = line("media_filter", 0.0, clogging=0.5).solve()
    assert r["dut.volume_flow"] == pytest.approx(0.0, abs=1e-12)
    assert r["dut.dp_ratio"] == pytest.approx((1.4 + 0.3e-4) / (0.7 + 0.3e-4), rel=1e-9)
    assert r.modes["dut"] == "idle"
    assert not r.warnings


def test_filter_reverse_flow_is_symmetric() -> None:
    """Reverse flow uses the same law: -0.2 bar gives -20 m3/h through a clean filter."""
    r = line("media_filter", -0.2).solve()
    assert r["dut.volume_flow"] == pytest.approx(-20.0, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(-0.2, rel=1e-7)
    assert r["dut.dp_ratio"] == pytest.approx(1.0, abs=1e-12)
    # Only the code-emitted reverse_flow warning; 20 m3/h and 0.2 bar exceed neither limit.
    dut = [w for w in r.warnings if w.component == "dut"]
    assert [w.code for w in dut] == ["reverse_flow"]
    assert "exceeds" not in dut[0].message


def between(component: str, p_in: float, p_out: float, **values: object) -> wp.System:
    """supply ``p_in`` -> component -> supply ``p_out`` (reverse flow when p_out > p_in)."""
    s = wp.System(component)
    s.add("a", "supply", pressure=p_in)
    s.add("dut", component, **values)
    s.add("b", "supply", pressure=p_out)
    s.connect("a.port", "dut.inlet")
    s.connect("dut.outlet", "b.port")
    return s


def test_filter_reverse_flow_warning_names_exceeded_limits() -> None:
    """Clogging 0.9, outlet at 3 bar: |dp| = 3 bar > 1 bar change drop, and
    0.2 * (7 y + 0.3 y**2) = 3, i.e. 0.06 y**2 + 1.4 y - 3 = 0, gives
    y = (-1.4 + sqrt(1.96 + 0.72)) / 0.12 = 1.97556, |Q| = 39.51 m3/h > 25 m3/h. The forward
    rules stay silent (design 8.10); reverse_flow names both."""
    y = (-1.4 + math.sqrt(1.96 + 0.72)) / 0.12
    r = between("media_filter", 0.0, 3.0, clogging=0.9).solve()
    assert r["dut.volume_flow"] == pytest.approx(-20 * y, rel=1e-7)
    assert r.modes["dut"] == "loaded"
    dut = [w for w in r.warnings if w.component == "dut"]
    assert [w.code for w in dut] == ["reverse_flow"]
    assert "change pressure drop" in dut[0].message
    assert "125 % of the rated flow" in dut[0].message
    # Just below the 0.01 m3/h threshold (supply -1e-5 bar: 0.14 y = 1e-5, |Q| = 0.0014)
    # the filter raises nothing (the drain raises its own backflow warning).
    r = line("media_filter", -1e-5).solve()
    assert -0.01 < r["dut.volume_flow"] < 0
    assert not [w for w in r.warnings if w.component == "dut"]


def throttled_filter() -> wp.System:
    """supply 3 bar -> valve Kv 12 -> media_filter -> drain."""
    s = wp.System("throttled")
    s.add("src", "supply", pressure=3.0)
    s.add("v", "valve", kv=12)
    s.add("dut", "media_filter")
    s.add("sink", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    return s


def test_filter_change_required_iff_over_clogging_sweep() -> None:
    """change_required is present exactly when pressure_drop > change_pressure_drop.

    Hand calculation (x = Q / 20, the Kv law carries the specific gravity 0.9982):
    3 = 0.9982 * (20 x / 12)**2 + 0.2 * (0.7 x / (1 - c) + 0.3 x**2).
    The filter drop is 1.0 bar when the valve takes 2 bar: Q = 12 * sqrt(2 / 0.9982)
    = 16.98586 m3/h, x = 0.849293; then 0.7 / (1 - c) = (5 - 0.3 x**2) / x = 5.632504 and
    c* = 1 - 0.7 / 5.632504 = 0.875720. Below c* no warning, above it the warning.
    """
    s = throttled_filter()
    q_star = 12 * math.sqrt(2 / 0.9982)
    x = q_star / 20
    c_star = 1 - 0.7 / ((5 - 0.3 * x * x) / x)
    assert c_star == pytest.approx(0.875720, abs=1e-6)
    seen = []
    for c in [i / 100 for i in range(0, 100)]:
        s.set("dut.clogging", c)
        r = s.solve()
        present = r.has_warning("dut.change_required")
        assert present == (r["dut.pressure_drop"] > 1.0)
        assert present == (c > c_star)
        assert (r.modes["dut"] == "needs_change") == present
        # The hand formula holds at each point.
        x_c = r["dut.volume_flow"] / 20
        assert r["dut.pressure_drop"] == pytest.approx(filter_dp(x_c, 0.2, 0.3, c), rel=1e-7)
        seen.append(present)
    assert seen[0] is False and seen[-1] is True
    # At c* itself the drop is 1.0 bar.
    s.set("dut.clogging", c_star)
    r = s.solve()
    assert r["dut.pressure_drop"] == pytest.approx(1.0, rel=1e-6)
    assert r["dut.volume_flow"] == pytest.approx(q_star, rel=1e-6)


def test_filter_over_rated_flow_warning() -> None:
    """over_rated_flow above 1.25 * 20 = 25 m3/h, reached at 0.2 * (0.875 + 0.46875)
    = 0.26875 bar for the clean filter."""
    below = line("media_filter", 0.26).solve()
    above = line("media_filter", 0.28).solve()
    assert below["dut.volume_flow"] < 25 < above["dut.volume_flow"]
    assert not below.has_warning("dut.over_rated_flow")
    assert above.has_warning("dut.over_rated_flow")
    edge = line("media_filter", 0.26875).solve()
    assert edge["dut.volume_flow"] == pytest.approx(25.0, rel=1e-7)


# -- UV reactor -----------------------------------------------------------------------------


def test_uv_rated_point_dose_and_pressure_drop() -> None:
    """Defaults at rated flow: dose 54 mJ/cm2, drop 0.1 bar.

    Hand calculation: a 0.1 bar supply over the 0.1 bar rated drop gives Q = 20 m3/h
    = 0.0055556 m3/s. residence_time = 0.015 m3 / 0.0055556 m3/s = 2.7 s.
    dose = 20 mW/cm2 * 2.7 s = 54 mJ/cm2.
    """
    r = line("uv_reactor", 0.1).solve()
    assert r["dut.volume_flow"] == pytest.approx(20.0, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(0.1, rel=1e-7)
    assert r["dut.residence_time"] == pytest.approx(2.7, rel=1e-7)
    assert r["dut.dose"] == pytest.approx(54.0, rel=1e-7)
    assert r.modes["dut"] == "disinfecting"
    assert not r.warnings


def test_uv_law_matches_kv_through_rated_point() -> None:
    """The quadratic law passes through the rated point: Kv = Q_r / sqrt(dp_r * 1000 / rho)
    with dp_r in bar, so Kv = 20 / sqrt(0.1 / 0.9982) = 63.18861 m3/h and k = kv_to_k(Kv).
    At 1 bar the flow is Kv * sqrt(1 / 0.9982) = 63.24555 m3/h (= 20 * sqrt(10))."""
    kv_si = (20 / 3600) / math.sqrt(0.1 * 1000 / RHO)
    assert kv_si * 3600 == pytest.approx(63.18861, abs=1e-5)
    s = wp.System("kv")
    s.add("src", "supply", pressure=1.0)
    uv = s.add("dut", "uv_reactor")
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    r = s.solve()
    assert uv.law.k == pytest.approx(kv_to_k(kv_si, RHO), rel=1e-12)
    assert r["dut.volume_flow"] == pytest.approx(20 * math.sqrt(10), rel=1e-7)


@pytest.mark.parametrize("pressure", [0.02, 0.05, 0.1, 0.18, 0.3, 0.6, 1.0, 2.0])
def test_uv_dose_inversely_proportional_to_flow(pressure: float) -> None:
    """dose * Q is constant: 54 mJ/cm2 * 20 m3/h = 1080; and dp = 0.1 * (Q / 20)**2.

    Q = 20 * sqrt(p / 0.1), so dose = 1080 / Q.
    """
    r = line("uv_reactor", pressure).solve()
    q = 20 * math.sqrt(pressure / 0.1)
    assert r["dut.volume_flow"] == pytest.approx(q, rel=1e-7)
    assert r["dut.dose"] * r["dut.volume_flow"] == pytest.approx(1080.0, rel=1e-9)
    assert r["dut.dose"] == pytest.approx(1080.0 / q, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(0.1 * (q / 20) ** 2, rel=1e-7)


def test_uv_underdose_above_27_m3h_pressure_sweep() -> None:
    """underdose fires exactly above Q* = 3.6 * V[L] * F / D = 3.6 * 15 * 20 / 40 = 27 m3/h.

    Driven by the supply pressure: Q* is reached at 0.1 * (27 / 20)**2 = 0.18225 bar.
    """
    s = line("uv_reactor", 0.0)
    for i in range(0, 41):
        p = i * 0.01
        s.set("src.pressure", p)
        r = s.solve()
        present = r.has_warning("dut.underdose")
        assert present == (r["dut.volume_flow"] > 27.0), p
        assert present == (p > 0.18225), p
        if r["dut.volume_flow"] > 0.01:
            assert (r.modes["dut"] == "underdosing") == present
    s.set("src.pressure", 0.18225)
    r = s.solve()
    assert r["dut.volume_flow"] == pytest.approx(27.0, rel=1e-7)
    assert r["dut.dose"] == pytest.approx(40.0, rel=1e-7)


def test_uv_underdose_valve_sweep() -> None:
    """The same threshold when a valve throttles a 1 bar supply.

    With a Kv 40 valve fully open: 1 = 0.9982 * (Q / 40)**2 + 0.1 * (Q / 20)**2, so
    Q = 1 / sqrt(0.9982 / 1600 + 0.1 / 400) = 33.852 m3/h > 27: underdose. Closing the
    valve lowers the flow continuously; the warning follows Q > 27 at every opening.
    """
    s = wp.System("uv-valve")
    s.add("src", "supply", pressure=1.0)
    s.add("v", "valve", kv=40)
    s.add("dut", "uv_reactor")
    s.add("sink", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    q_open = 1 / math.sqrt(0.9982 / 1600 + 0.1 / 400)
    assert s.solve()["dut.volume_flow"] == pytest.approx(q_open, rel=1e-6)
    flags = []
    for i in range(0, 21):
        s.set("v.opening", i / 20)
        r = s.solve()
        present = r.has_warning("dut.underdose")
        assert present == (r["dut.volume_flow"] > 27.0)
        flags.append(present)
    assert flags[0] is False and flags[-1] is True


def test_uv_lamp_off_with_flow() -> None:
    """lamp_output 0 with 20 m3/h: dose 0, lamp_off and underdose warnings."""
    r = line("uv_reactor", 0.1, lamp_output=0.0).solve()
    assert r["dut.dose"] == 0.0
    assert r.has_warning("dut.lamp_off")
    assert r.has_warning("dut.underdose")
    assert r.modes["dut"] == "underdosing"
    # Lamp at 1 %: dose 0.54 mJ/cm2, underdose but not lamp_off.
    r = line("uv_reactor", 0.1, lamp_output=0.01).solve()
    assert r["dut.dose"] == pytest.approx(0.54, rel=1e-7)
    assert not r.has_warning("dut.lamp_off")
    assert r.has_warning("dut.underdose")


def test_uv_idle_at_zero_flow_has_no_division_errors() -> None:
    """At zero flow residence_time and dose are capped at 1e6 (s, mJ/cm2); idle, no warnings.

    Also with the lamp off: dose 0 * 1e6 = 0, still idle and no lamp_off (no flow).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for lamp in (1.0, 0.0):
            r = line("uv_reactor", 0.0, lamp_output=lamp).solve()
            assert r["dut.volume_flow"] == 0.0
            assert r["dut.residence_time"] == pytest.approx(1e6)
            assert r["dut.dose"] == pytest.approx(1e6 if lamp else 0.0)
            assert r.modes["dut"] == "idle"
            assert not r.warnings
        # A tiny flow (valve closed to seat leakage) stays finite and continuous.
        s = wp.System("leak")
        s.add("src", "supply", pressure=0.001)
        s.add("v", "valve", inputs={"opening": 0.0})
        s.add("dut", "uv_reactor")
        s.add("sink", "drain")
        s.connect("src.port", "v.port_a")
        s.connect("v.port_b", "dut.inlet")
        s.connect("dut.outlet", "sink.port")
        r = s.solve()
        assert 0 < r["dut.volume_flow"] < 0.01
        assert 0 < r["dut.residence_time"] <= 1e6
        assert r.modes["dut"] == "idle"


def test_uv_reverse_flow() -> None:
    """Reverse flow is hydraulically symmetric and the dose uses |Q|; underdose only
    applies to forward flow per design 8.11, so only reverse_flow is raised."""
    r = line("uv_reactor", -0.1).solve()
    assert r["dut.volume_flow"] == pytest.approx(-20.0, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(-0.1, rel=1e-7)
    assert r["dut.dose"] == pytest.approx(54.0, rel=1e-7)
    dut = [w for w in r.warnings if w.component == "dut"]
    assert [w.code for w in dut] == ["reverse_flow"]
    assert "below the required dose" not in dut[0].message
    assert r.modes["dut"] == "disinfecting"  # dose 54 >= 40 over |Q|


def test_uv_reverse_underdose_and_lamp_off_are_not_silent() -> None:
    """Outlet at 1 bar: Q = -20 * sqrt(10) = -63.25 m3/h, dose 1080 / 63.25 = 17.08 < 40.
    The mode reads underdosing; the forward-flow underdose rule is silent (design 8.11), so
    reverse_flow carries it in its message. With the lamp off the dose is 0 and the message
    says the lamp is off (lamp_off itself is a forward-flow rule)."""
    q = 20 * math.sqrt(10)
    r = between("uv_reactor", 0.0, 1.0).solve()
    assert r["dut.volume_flow"] == pytest.approx(-q, rel=1e-7)
    assert r["dut.dose"] == pytest.approx(1080 / q, rel=1e-7)
    assert r.modes["dut"] == "underdosing"
    dut = [w for w in r.warnings if w.component == "dut"]
    assert [w.code for w in dut] == ["reverse_flow"]
    assert "17.1 mJ/cm2" in dut[0].message and "below the required dose" in dut[0].message
    r = between("uv_reactor", 0.0, 1.0, lamp_output=0.0).solve()
    assert r.modes["dut"] == "underdosing"
    dut = [w for w in r.warnings if w.component == "dut"]
    assert [w.code for w in dut] == ["reverse_flow"]
    assert "the lamp is off" in dut[0].message


def test_uv_residence_time_cap_at_a_flowing_point() -> None:
    """The 1e6 s cap (design 8.11) can bind while water flows: 100 m3 at 0.05 m3/h is truly
    100 / (0.05 / 3600) = 7.2e6 s, reported as 1e6 s (a lower bound, documented in the
    manifest). Rated 0.05 m3/h at 0.1 bar, so a 0.1 bar supply drives exactly 0.05 m3/h.

    The underdose verdict is unaffected for lamp_output >= 0.01: the capped dose is at least
    0.1 mW/cm2 * 0.01 * 1e6 s = 1000 mJ/cm2, the largest legal required_dose.
    """
    params = {"rated_flow": 0.05, "volume": 100000, "required_dose": 1000}
    r = line("uv_reactor", 0.1, **params).solve()
    assert r["dut.volume_flow"] == pytest.approx(0.05, rel=1e-7)
    assert r["dut.residence_time"] == pytest.approx(1e6)
    assert r["dut.dose"] == pytest.approx(1e6)  # 20 mW/cm2 * 1e6 s, capped at 1e6
    r = line("uv_reactor", 0.1, fluence_rate=0.1, lamp_output=0.01, **params).solve()
    # Capped: 0.1 * 0.01 * 1e6 = 1000; true: 0.1 * 0.01 * 7.2e6 = 7200. Both >= 1000.
    assert r["dut.dose"] == pytest.approx(1000.0, rel=1e-9)
    assert not r.has_warning("dut.underdose")
    assert r.modes["dut"] == "disinfecting"


def test_uv_lamp_failure_simulation() -> None:
    """Lamp fails at 10 s: the dose steps from 54 to 0 and lamp_off first appears at 10 s."""
    s = line("uv_reactor", 0.1)
    sim = s.simulate(
        duration="20 s", step="1 s", events=[{"at": "10 s", "set": {"dut.lamp_output": 0}}]
    )
    doses = sim.series["dut.dose"]
    times = sim.time
    for t, d in zip(times, doses, strict=True):
        assert d == pytest.approx(54.0 if t < 10 else 0.0, rel=1e-7, abs=1e-12)
    first = {(w.component, w.code): w.time for w in sim.warnings}
    assert first[("dut", "lamp_off")] == pytest.approx(10.0)
    assert first[("dut", "underdose")] == pytest.approx(10.0)


# -- treatment train ------------------------------------------------------------------------


def train(pressure: float = 0.3, clogging: float = 0.0) -> wp.System:
    """supply -> media_filter -> uv_reactor -> drain."""
    s = wp.System("train")
    s.add("src", "supply", pressure=pressure)
    s.add("filt", "media_filter", clogging=clogging)
    s.add("uv", "uv_reactor")
    s.add("sink", "drain")
    s.connect("src.port", "filt.inlet")
    s.connect("filt.outlet", "uv.inlet")
    s.connect("uv.outlet", "sink.port")
    return s


def test_train_rated_point() -> None:
    """At 0.3 bar the train passes exactly 20 m3/h: 0.2 bar (filter) + 0.1 bar (UV) = 0.3."""
    r = train().solve()
    assert r["filt.volume_flow"] == pytest.approx(20.0, rel=1e-7)
    assert r["uv.volume_flow"] == pytest.approx(20.0, rel=1e-7)
    assert r["filt.pressure_drop"] == pytest.approx(0.2, rel=1e-7)
    assert r["uv.pressure_drop"] == pytest.approx(0.1, rel=1e-7)
    assert r["uv.inlet.p"] == pytest.approx(0.1, abs=1e-7)
    assert r["uv.dose"] == pytest.approx(54.0, rel=1e-7)
    assert r.modes == {
        "src": "supplying",
        "filt": "clean",
        "uv": "disinfecting",
        "sink": "receiving",
    }


@pytest.mark.parametrize(
    ("pressure", "clogging"), [(0.05, 0.0), (0.3, 0.0), (0.3, 0.9), (1.0, 0.5), (2.5, 0.99)]
)
def test_train_mass_conservation_and_drop_additivity(pressure: float, clogging: float) -> None:
    """Every element carries the same mass flow and the drops add up to the supply pressure.

    Mass: src delivers (L/min) = filter (m3/h) = UV (m3/h) = drain receives (L/min), with
    1 m3/h = 1000 / 60 L/min; port m_flow sums to zero across each component.
    Pressure: src.port.p - sink.port.p = filt.pressure_drop + uv.pressure_drop.
    """
    r = train(pressure, clogging).solve()
    q = r["filt.volume_flow"]
    assert r["uv.volume_flow"] == pytest.approx(q, rel=1e-9)
    assert r["src.volume_flow"] == pytest.approx(q * 1000 / 60, rel=1e-9)
    assert r["sink.volume_flow"] == pytest.approx(q * 1000 / 60, rel=1e-9)
    for c in ("filt", "uv"):
        assert r[f"{c}.inlet.m_flow"] == pytest.approx(-r[f"{c}.outlet.m_flow"], abs=1e-9)
    assert r["filt.inlet.m_flow"] == pytest.approx(q / 3600 * RHO, rel=1e-9)
    total = r["src.port.p"] - r["sink.port.p"]
    assert total == pytest.approx(pressure, abs=1e-8)
    assert r["filt.pressure_drop"] + r["uv.pressure_drop"] == pytest.approx(total, abs=1e-8)
    # And each drop matches its own hand formula at the common flow.
    x = q / 20
    assert r["filt.pressure_drop"] == pytest.approx(filter_dp(x, 0.2, 0.3, clogging), rel=1e-7)
    assert r["uv.pressure_drop"] == pytest.approx(0.1 * x * x, rel=1e-7)


def test_train_clogging_raises_dose() -> None:
    """A clogged filter throttles the train: less flow, a longer residence time, more dose.

    Hand calculation for c = 0.9 at 0.3 bar: 0.3 = 0.2 * (7 x + 0.3 x**2) + 0.1 x**2, so
    0.16 x**2 + 1.4 x - 0.3 = 0, x = (-1.4 + sqrt(1.96 + 0.192)) / 0.32 = 0.209558,
    Q = 4.19117 m3/h and dose = 1080 / Q = 257.685 mJ/cm2.
    """
    x = (-1.4 + math.sqrt(1.96 + 0.192)) / 0.32
    r = train(0.3, 0.9).solve()
    assert r["uv.volume_flow"] == pytest.approx(20 * x, rel=1e-7)
    assert r["uv.dose"] == pytest.approx(1080 / (20 * x), rel=1e-7)
    assert r.modes["filt"] == "loaded"
    assert not r.warnings
