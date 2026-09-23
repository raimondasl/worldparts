"""Independent review of the treatment components: media_filter (design 8.10) and uv_reactor
(design 8.11).

Every expected value here comes from a hand calculation or from an independent root find
(``scipy.optimize.brentq`` on the closed-form laws of design 8.10 and 8.11), never from the
model under test. Units in the comments: flows in m3/h, pressures in bar, x = Q / Q_rated,
doses in mJ/cm2.

Most tests pass and document what was verified. The tests marked "REVIEW FINDING" fail on
purpose: they show a gap in the manifests' scenarios and contracts (a physically wrong
implementation that ``worldparts check-catalog`` would still accept).
"""

from __future__ import annotations

import itertools
import math
import warnings
from collections.abc import Callable
from typing import Any

import pytest
from scipy.optimize import brentq

import worldparts as wp
from worldparts.components.treatment import MediaFilter, UVReactor

SG = 0.9982  # specific gravity of the constant-property water (998.2 / 1000)


def line(component: str, pressure: float, **values: Any) -> wp.System:
    """supply (gauge ``pressure`` bar) -> component -> drain (atmosphere)."""
    s = wp.System(f"review-{component}")
    s.add("src", "supply", pressure=pressure)
    s.add("dut", component, **values)
    s.add("sink", "drain")
    s.connect("src.port", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    return s


def between(component: str, p_in: float, p_out: float, **values: Any) -> wp.System:
    """Two supplies: ``p_in`` at the inlet, ``p_out`` at the outlet (drives reverse flow)."""
    s = wp.System(f"review-{component}-2")
    s.add("a", "supply", pressure=p_in)
    s.add("dut", component, **values)
    s.add("b", "supply", pressure=p_out)
    s.connect("a.port", "dut.inlet")
    s.connect("dut.outlet", "b.port")
    return s


def train(pressure: float, clogging: float = 0.0, lamp: float = 1.0) -> wp.System:
    """supply -> media_filter -> uv_reactor -> drain, all defaults."""
    s = wp.System("review-train")
    s.add("src", "supply", pressure=pressure)
    s.add("filt", "media_filter", clogging=clogging)
    s.add("uv", "uv_reactor", lamp_output=lamp)
    s.add("sink", "drain")
    s.connect("src.port", "filt.inlet")
    s.connect("filt.outlet", "uv.inlet")
    s.connect("uv.outlet", "sink.port")
    return s


def filter_dp(x: float, dp_clean: float = 0.2, h: float = 0.3, c: float = 0.0) -> float:
    """Design 8.10 in bar at x = Q / Q_rated (signed)."""
    return dp_clean * ((1 - h) * x / (1 - c) + h * x * abs(x))


def uv_dp(x: float, dp_rated: float = 0.1) -> float:
    """Design 8.11 quadratic law through the rated point, in bar."""
    return dp_rated * x * abs(x)


# -- interface against design 8.10 / 8.11 -----------------------------------------------------

# (unit, default, minimum, maximum, pressure_reference) from design.md 8.10 and 8.11.
DESIGN = {
    "media_filter": {
        "parameters": {
            "rated_flow": ("m3/h", 20, 0.01, 100000, None),
            "clean_pressure_drop": ("bar", 0.2, 0.001, 20, "difference"),
            "housing_fraction": ("1", 0.3, 0, 1, None),
            "change_pressure_drop": ("bar", 1.0, 0.01, 50, "difference"),
        },
        "inputs": {"clogging": ("1", 0.0, 0, 0.99, None)},
        "observables": {"volume_flow": "m3/h", "pressure_drop": "bar", "dp_ratio": "1"},
        "modes": ["idle", "needs_change", "loaded", "clean"],
        "envelope": {
            "change_required": "pressure_drop > change_pressure_drop",
            "over_rated_flow": "volume_flow > 1.25 * rated_flow",
        },
    },
    "uv_reactor": {
        "parameters": {
            "rated_flow": ("m3/h", 20, 0.01, 100000, None),
            "rated_pressure_drop": ("bar", 0.1, 0.001, 10, "difference"),
            "volume": ("L", 15, 0.1, 100000, None),
            "fluence_rate": ("mW/cm2", 20, 0.1, 1000, None),
            "required_dose": ("mJ/cm2", 40, 1, 1000, None),
        },
        "inputs": {"lamp_output": ("1", 1.0, 0, 1, None)},
        "observables": {
            "volume_flow": "m3/h",
            "pressure_drop": "bar",
            "residence_time": "s",
            "dose": "mJ/cm2",
        },
        "modes": ["idle", "underdosing", "disinfecting"],
        "envelope": {
            "underdose": "volume_flow > 0.01 and dose < required_dose",
            "lamp_off": "lamp_output < 0.01 and volume_flow > 0.01",
        },
    },
}


@pytest.mark.parametrize("alias", sorted(DESIGN))
def test_interface_matches_design(alias: str) -> None:
    """Every parameter, input, observable, mode and envelope rule of design 8.10/8.11 exists
    with the design's name, unit, default, hard limits and pressure reference."""
    m = wp.default_catalog().get(alias)
    spec = DESIGN[alias]
    for group in ("parameters", "inputs"):
        declared = getattr(m, group)
        assert set(declared) == set(spec[group])
        for name, (unit, default, lo, hi, ref) in spec[group].items():
            v = declared[name]
            assert (v.unit, v.default, v.minimum, v.maximum) == (unit, default, lo, hi), name
            assert v.pressure_reference == ref, name
    assert {k: v.unit for k, v in m.observables.items()} == spec["observables"]
    assert m.observables["pressure_drop"].pressure_reference == "difference"
    assert [md.name for md in m.modes] == spec["modes"]
    assert {w.code: w.condition for w in m.envelope} == spec["envelope"]
    assert all(w.severity == "warning" for w in m.envelope)


def test_units_at_the_boundary() -> None:
    """Strings with units convert to the declared unit; references are enforced.

    20 kPa = 0.2 bar difference; 0.015 m3 = 15 L; 200 W/m2 = 20 mW/cm2; 50 % = 0.5; a gauge
    or absolute reference on a pressure difference is rejected; kg/s for a volume flow is a
    dimension error.
    """
    r = line("media_filter", 0.2, clean_pressure_drop="20 kPa").solve()
    assert r["dut.volume_flow"] == pytest.approx(20.0, rel=1e-7)
    assert r.units["dut.pressure_drop"] == "bar"
    assert r.to_dict()["values"]["dut.pressure_drop"]["reference"] == "difference"
    r = line("uv_reactor", 0.1, volume="0.015 m3", fluence_rate="200 W/m**2").solve()
    assert r["dut.dose"] == pytest.approx(54.0, rel=1e-7)
    assert r.units["dut.dose"] == "mJ/cm2"
    assert r.get("dut.dose", unit="J/m**2") == pytest.approx(540.0, rel=1e-7)
    assert line("uv_reactor", 0.1, lamp_output="50 %").solve()["dut.dose"] == pytest.approx(
        27.0, rel=1e-7
    )
    for bad in ("1.2 bara", "0.2 barg"):
        with pytest.raises(wp.WorldpartsError):
            line("media_filter", 0.2, clean_pressure_drop=bad)
    with pytest.raises(wp.WorldpartsError):
        line("media_filter", 0.2, rated_flow="20 kg/s")
    for comp, key, value in (
        ("media_filter", "clogging", 0.995),
        ("media_filter", "housing_fraction", 1.01),
        ("uv_reactor", "lamp_output", 1.1),
    ):
        with pytest.raises(wp.WorldpartsError):
            line(comp, 0.1, **{key: value})


# -- media filter -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rated_flow", "dp_clean", "h", "c"),
    [
        (20, 0.2, 0.3, 0.0),
        (20, 0.2, 0.3, 0.5),
        (0.01, 5, 0.0, 0.9),
        (0.01, 0.001, 0.3, 0.99),
        (1e5, 1.0, 0.5, 0.99),
    ],
)
def test_filter_rated_point_and_dp_ratio(
    rated_flow: float, dp_clean: float, h: float, c: float
) -> None:
    """At the rated flow dp = dp_clean * ((1 - h) / (1 - c) + h) and dp_ratio = (1-h)/(1-c)+h.

    A supply at exactly that pressure over a drain therefore drives exactly the rated flow
    (the ideal supply and drain joints add at most 1e-6 Pa per kg/s).
    """
    ratio = (1 - h) / (1 - c) + h
    r = line(
        "media_filter",
        dp_clean * ratio,
        rated_flow=rated_flow,
        clean_pressure_drop=dp_clean,
        housing_fraction=h,
        clogging=c,
        change_pressure_drop=50,
    ).solve()
    assert r["dut.volume_flow"] == pytest.approx(rated_flow, rel=1e-6)
    assert r["dut.pressure_drop"] == pytest.approx(dp_clean * ratio, rel=1e-6)
    assert r["dut.dp_ratio"] == pytest.approx(ratio, rel=1e-6)


def test_filter_split_follows_housing_fraction() -> None:
    """Independent root find of 0.2 * (0.7 x + 0.3 x**2) = p for several p, and the split:
    at half rated flow the linear part halves (0.14 -> 0.07) and the quadratic part quarters
    (0.06 -> 0.015), total 0.085 bar."""
    for p in (0.01, 0.085, 0.2, 0.5, 3.0):
        x = brentq(lambda x, p=p: filter_dp(x) - p, 0, 100, xtol=1e-14)
        r = line("media_filter", p).solve()
        assert r["dut.volume_flow"] == pytest.approx(20 * x, rel=1e-6)
    assert filter_dp(0.5) == pytest.approx(0.085, rel=1e-12)


def test_filter_dp_ratio_rises_with_clogging_at_rated_flow() -> None:
    """dp_ratio(c) = 0.7 / (1 - c) + 0.3 at rated flow: 1 clean, strictly rising."""
    got = []
    for c in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99):
        ratio = 0.7 / (1 - c) + 0.3
        r = line("media_filter", 0.2 * ratio, clogging=c).solve()
        assert r["dut.dp_ratio"] == pytest.approx(ratio, rel=1e-6)
        got.append(r["dut.dp_ratio"])
    assert got[0] == pytest.approx(1.0, abs=1e-9)
    assert all(b > a for a, b in itertools.pairwise(got))


def test_filter_change_required_iff_over_clogging_sweep() -> None:
    """change_required fires exactly when the drop exceeds change_pressure_drop.

    supply 3 bar -> valve Kv 12 -> filter -> drain. The Kv law gives dp_valve = SG * (Q/12)**2.
    Root find of SG*(20x/12)**2 + filter_dp(x, c=c) = 3 for x, then of filter_dp = 1 for c
    gives the crossing c* = 0.875720 (independent of the model).
    """

    def drop(c: float) -> float:
        x = brentq(lambda x: SG * (20 * x / 12) ** 2 + filter_dp(x, c=c) - 3, 0, 10, xtol=1e-15)
        return filter_dp(x, c=c)

    c_star = brentq(lambda c: drop(c) - 1.0, 0, 0.99, xtol=1e-14)
    assert c_star == pytest.approx(0.875720, abs=1e-6)
    s = wp.System("throttled")
    s.add("src", "supply", pressure=3.0)
    s.add("v", "valve", kv=12)
    s.add("dut", "media_filter")
    s.add("sink", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    for c in [i / 200 for i in range(0, 199)] + [c_star - 1e-4, c_star + 1e-4]:
        s.set("dut.clogging", c)
        r = s.solve()
        assert r["dut.pressure_drop"] == pytest.approx(drop(c), rel=1e-6)
        present = r.has_warning("dut.change_required")
        assert present == (r["dut.pressure_drop"] > 1.0) == (c > c_star), c
        assert (r.modes["dut"] == "needs_change") == present


# -- UV reactor -------------------------------------------------------------------------------


def test_uv_rated_point() -> None:
    """Q = 20 m3/h at 0.1 bar; t = 0.015 m3 / (20/3600 m3/s) = 2.7 s; dose = 20 * 2.7 = 54."""
    r = line("uv_reactor", 0.1).solve()
    assert r["dut.volume_flow"] == pytest.approx(20.0, rel=1e-7)
    assert r["dut.pressure_drop"] == pytest.approx(0.1, rel=1e-7)
    assert r["dut.residence_time"] == pytest.approx(2.7, rel=1e-7)
    assert r["dut.dose"] == pytest.approx(54.0, rel=1e-7)


@pytest.mark.parametrize("pressure", [0.02, 0.1, 0.18225, 0.5, 2.0, 10.0, 100.0])
def test_uv_dose_inverse_to_flow(pressure: float) -> None:
    """Q = 20 * sqrt(p / 0.1); dose = 20 mW/cm2 * 3.6 * 15 / Q = 1080 / Q."""
    q = 20 * math.sqrt(pressure / 0.1)
    r = line("uv_reactor", pressure).solve()
    assert r["dut.volume_flow"] == pytest.approx(q, rel=1e-6)
    assert r["dut.dose"] == pytest.approx(1080 / q, rel=1e-6)


def test_uv_underdose_iff_over_valve_sweep() -> None:
    """underdose fires exactly above Q* = 1080 / 40 = 27 m3/h when a valve throttles.

    1 bar supply -> valve Kv 40 (linear, leakage 1e-4) -> UV -> drain. With
    Kv_eff = 40 * (1e-4 + (1 - 1e-4) y): 1 = SG * (Q / Kv_eff)**2 + 0.1 * (Q / 20)**2.
    The crossing opening y* solves Q(y*) = 27 (root find, not the model).
    """

    def q_of(y: float) -> float:
        kv = 40 * (1e-4 + (1 - 1e-4) * y)
        return 1 / math.sqrt(SG / kv**2 + 0.1 / 400)

    y_star = brentq(lambda y: q_of(y) - 27, 0, 1, xtol=1e-14)
    s = wp.System("uv-valve")
    s.add("src", "supply", pressure=1.0)
    s.add("v", "valve", kv=40)
    s.add("dut", "uv_reactor")
    s.add("sink", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "dut.inlet")
    s.connect("dut.outlet", "sink.port")
    for y in [i / 40 for i in range(41)] + [y_star - 1e-5, y_star + 1e-5]:
        s.set("v.opening", y)
        r = s.solve()
        assert r["dut.volume_flow"] == pytest.approx(q_of(y), rel=1e-6)
        assert r.has_warning("dut.underdose") == (y > y_star), y


def test_uv_lamp_off_and_idle() -> None:
    """Lamp off with 20 m3/h: dose 0, lamp_off + underdose. Zero flow: capped values, idle,
    no warnings and no floating-point warnings (division by zero)."""
    r = line("uv_reactor", 0.1, lamp_output=0).solve()
    assert r["dut.dose"] == 0.0
    assert r.has_warning("dut.lamp_off") and r.has_warning("dut.underdose")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for lamp in (0.0, 1.0):
            r = line("uv_reactor", 0.0, lamp_output=lamp).solve()
            assert r["dut.volume_flow"] == 0.0
            assert r["dut.residence_time"] == pytest.approx(1e6)
            assert r["dut.dose"] == pytest.approx(1e6 * lamp)
            assert r.modes["dut"] == "idle"
            assert not r.warnings


# -- treatment train and robustness -----------------------------------------------------------


@pytest.mark.parametrize("pressure", [-0.9, -0.3, 0.05, 0.3, 1.0, 5.0, 100.0])
@pytest.mark.parametrize("clogging", [0.0, 0.5, 0.99])
def test_train_matches_root_find(pressure: float, clogging: float) -> None:
    """supply -> filter -> UV -> drain: one flow, drops add up to the supply pressure.

    Independent: root find of filter_dp(x, c) + uv_dp(x) = p. The tolerance is 1e-4 because
    at the smallest flows (c = 0.99, p = 0.05: Q = 0.071 m3/h) the UV law is inside its
    documented C1 regularisation band (below 0.2 % of the 63 m3/h flow at 1 bar).
    """
    x = brentq(lambda x: filter_dp(x, c=clogging) + uv_dp(x) - pressure, -1e3, 1e3, xtol=1e-15)
    r = train(pressure, clogging).solve()
    assert r["filt.volume_flow"] == pytest.approx(20 * x, rel=1e-4)
    assert r["uv.volume_flow"] == pytest.approx(r["filt.volume_flow"], rel=1e-12)
    for c in ("filt", "uv"):
        assert r[f"{c}.inlet.m_flow"] == pytest.approx(-r[f"{c}.outlet.m_flow"], abs=1e-12)
    assert r["filt.outlet.m_flow"] == pytest.approx(-r["uv.inlet.m_flow"], abs=1e-12)
    total = r["src.port.p"] - r["sink.port.p"]
    assert r["filt.pressure_drop"] + r["uv.pressure_drop"] == pytest.approx(total, abs=1e-9)


def test_reverse_flow_physics() -> None:
    """Reverse flow (outlet above inlet) follows the same laws with negative sign.

    Filter, c = 0.9, outlet 3 bar: root of filter_dp(x, c=0.9) = -3 gives x = -1.97556
    (Q = -39.51 m3/h), dp_ratio = (7 + 0.3 |x|) / (0.7 + 0.3 |x|) = 5.8736.
    UV, outlet 1 bar: Q = -20 * sqrt(10) = -63.246 m3/h, drop -1 bar, dose 1080 / 63.246 =
    17.076 mJ/cm2 (the |Q| in design 8.11).
    Design 8.10/8.11 restrict every envelope rule to forward flow, so no warning is raised
    even though the filter is at 158 % of rated flow with 3x the change drop and the UV mode
    reads ``underdosing``; recorded as a design-level risk in the review, not asserted here.
    """
    x = brentq(lambda x: filter_dp(x, c=0.9) + 3, -100, 0, xtol=1e-15)
    r = between("media_filter", 0.0, 3.0, clogging=0.9).solve()
    assert r["dut.volume_flow"] == pytest.approx(20 * x, rel=1e-6)
    assert r["dut.pressure_drop"] == pytest.approx(-3.0, rel=1e-6)
    ratio = (7 + 0.3 * abs(x)) / (0.7 + 0.3 * abs(x))
    assert r["dut.dp_ratio"] == pytest.approx(ratio, rel=1e-6)
    r = between("uv_reactor", 0.0, 1.0).solve()
    assert r["dut.volume_flow"] == pytest.approx(-20 * math.sqrt(10), rel=1e-6)
    assert r["dut.pressure_drop"] == pytest.approx(-1.0, rel=1e-6)
    assert r["dut.dose"] == pytest.approx(1080 / (20 * math.sqrt(10)), rel=1e-6)


def test_extreme_legal_parameters_stay_finite() -> None:
    """Corners of the hard limits at negative, zero, tiny and maximal supply pressure: no
    exception, no NaN or inf, no floating-point warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for qr, dpr, vol, fl, lamp, p in itertools.product(
            [0.01, 1e5], [0.001, 10], [0.1, 1e5], [0.1, 1000], [0, 1], [-0.9, 0, 1e-6, 100]
        ):
            r = line(
                "uv_reactor",
                p,
                rated_flow=qr,
                rated_pressure_drop=dpr,
                volume=vol,
                fluence_rate=fl,
                lamp_output=lamp,
            ).solve()
            assert all(math.isfinite(v) for v in r.values.values() if isinstance(v, float))
        for qr, dpc, h, c, p in itertools.product(
            [0.01, 1e5], [0.001, 20], [0, 0.3, 1], [0, 0.99], [-0.9, 0, 1e-6, 100]
        ):
            r = line(
                "media_filter",
                p,
                rated_flow=qr,
                clean_pressure_drop=dpc,
                housing_fraction=h,
                clogging=c,
            ).solve()
            assert all(math.isfinite(v) for v in r.values.values() if isinstance(v, float))


def test_long_simulation_with_clogging_ramp() -> None:
    """Two hours at 1 s with clogging stepped 0 -> 0.99: finite, and the dose at the end
    matches the root find: 0.3 = 0.2 * (70 x + 0.3 x**2) + 0.1 x**2 -> dose = 1080 / (20 x)."""
    s = train(0.3)
    events = [
        {"at": f"{i * 360} s", "set": {"filt.clogging": min(0.99, i * 0.05)}} for i in range(1, 21)
    ]
    sim = s.simulate(duration="2 h", step="1 s", events=events)
    assert len(sim.time) == 7201
    for key in ("uv.dose", "filt.dp_ratio", "uv.volume_flow", "filt.pressure_drop"):
        assert all(math.isfinite(v) for v in sim.series[key])
    x = brentq(lambda x: filter_dp(x, c=0.99) + uv_dp(x) - 0.3, 0, 10, xtol=1e-15)
    assert sim.series["uv.dose"][-1] == pytest.approx(1080 / (20 * x), rel=1e-6)


# -- REVIEW FINDINGS: manifest contracts that cannot see reverse-flow errors ------------------


def _mutant(cls: type, change: Callable[[Any, dict[str, float | None]], None]) -> Any:
    """An ``observables`` that post-processes the real one with ``change(self, values)``."""
    original = cls.observables

    def observables(self: Any, sol: Any) -> dict[str, float | None]:
        values = original(self, sol)
        change(self, values)
        return values

    return observables


def _uv_abs_flow(self: Any, v: dict[str, float | None]) -> None:
    v["volume_flow"] = abs(v["volume_flow"])  # type: ignore[arg-type]
    v["pressure_drop"] = abs(v["pressure_drop"])  # type: ignore[arg-type]


def _uv_no_dose_reverse(self: Any, v: dict[str, float | None]) -> None:
    if v["volume_flow"] < 0:  # type: ignore[operator]
        v["dose"] = 0.0


@pytest.mark.parametrize(
    "change",
    [_uv_abs_flow, _uv_no_dose_reverse],
    ids=["abs-flow-and-drop", "zero-dose-on-reverse"],
)
def test_uv_manifest_catches_reverse_flow_errors(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[Any, dict[str, float | None]], None]
) -> None:
    """REVIEW FINDING: the uv_reactor manifest never exercises reverse flow on its observables.

    Design 3.2: ``volume_flow`` is positive from inlet to outlet, so with the outlet at 1 bar
    and the inlet at 0 bar the reactor must report Q = -20 * sqrt(10) = -63.2 m3/h and a
    pressure drop of -1 bar. The manifest description also claims "Reverse flow is
    hydraulically symmetric and receives the same average dose" (54 mJ/cm2 at -20 m3/h).

    The only contract that sweeps into reverse flow is ``mass-conservation``
    (``dut.inlet.m_flow == -dut.outlet.m_flow``), which is identically true for a one-branch
    component because the system derives both port flows from the same branch flow. So an
    implementation that reports ``abs(volume_flow)`` and ``abs(pressure_drop)`` (sign
    convention broken) or no dose in reverse (contradicting the description) still passes
    every uv_reactor scenario and contract. Expected: ``run_component`` reports a failure.
    Fix: add a reverse-flow scenario (e.g. supply 0 bar at the inlet, supply 0.1 bar at the
    outlet: volume_flow -20, pressure_drop -0.1, dose 54, no underdose) or extend
    ``quadratic-pressure-drop`` / ``dose-inverse-to-flow`` to negative supply pressures with
    ``abs()`` in the right-hand side.
    """
    monkeypatch.setattr(UVReactor, "observables", _mutant(UVReactor, change))
    report = wp.run_component("uv_reactor")
    assert not report.passed, "a reverse-flow error survived every uv_reactor contract"


def test_filter_manifest_catches_reverse_dp_ratio_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """REVIEW FINDING (minor): dp_ratio is never checked in reverse flow.

    The manifest defines dp_ratio as the drop divided by the clean drop at the same flow, so
    in reverse flow both are negative and the ratio is positive: at -0.3 bar with c = 0.5,
    |x| = 0.8985 and dp_ratio = (1.4 + 0.3 |x|) / (0.7 + 0.3 |x|) = 1.7220. The
    ``dp-ratio-definition`` contract only sweeps clogging at +0.2 bar and
    ``dp-ratio-at-least-one`` runs on a forward-flow scenario, so an implementation that
    returns a negative dp_ratio for reverse flow passes every media_filter contract.
    Expected: ``run_component`` reports a failure.
    """

    def change(self: Any, v: dict[str, float | None]) -> None:
        if v["volume_flow"] < 0:  # type: ignore[operator]
            v["dp_ratio"] = -v["dp_ratio"]  # type: ignore[operator]

    monkeypatch.setattr(MediaFilter, "observables", _mutant(MediaFilter, change))
    report = wp.run_component("media_filter")
    assert not report.passed, "a negative reverse-flow dp_ratio survived every contract"


def test_mutation_harness_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for the two findings above: the same harness does catch a forward-flow error
    (the dose halved at every flow fails the ``rated-flow`` scenario, 27 != 54 mJ/cm2), so
    the survivors above are gaps in the manifests, not in the harness."""

    def change(self: Any, v: dict[str, float | None]) -> None:
        v["dose"] = 0.5 * v["dose"]  # type: ignore[operator]

    monkeypatch.setattr(UVReactor, "observables", _mutant(UVReactor, change))
    assert not wp.run_component("uv_reactor").passed
