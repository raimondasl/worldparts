"""Measurements (design 14.1), calibration and identifiability (design 14.2).

The statistical tests use synthetic data: the model at known parameter values plus Gaussian
noise from fixed seeds, so every run is reproducible. A calibration is statistically right
when the truth lies within about two standard errors about 95 % of the time, the standard
errors match the scatter of repeated fits, and the reduced chi-square is about 1.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import worldparts as wp
from worldparts.calibration import _sample
from worldparts.measurements import SIGMA_FLOORS, quantity_kind

TRUTH = {"pump.wear_head": 0.12, "pump.wear_efficiency": 0.08}
WH, WE = "pump.wear_head", "pump.wear_efficiency"
WEAR_BOUNDS = {WH: [0, 0.5], WE: [0, 0.5]}
OPENINGS = (1.0, 0.75, 0.5, 0.35)
PUMP_SIGMA = {"pump.volume_flow": 0.3, "pump.outlet.p": 0.02, "pump.shaft_power": 0.03}


# ----------------------------------------------------------------------------------------
# systems and synthetic data
# ----------------------------------------------------------------------------------------
def pump_circuit() -> wp.System:
    """Supply (0 bar) -> pump -> valve (Kv 15) -> 20 m of 60 mm pipe -> drain."""
    s = wp.System("pump-circuit")
    s.add("src", "supply", pressure=0)
    s.add("pump", "centrifugal_pump")
    s.add("v", "valve", kv=15.0)
    s.add("pipe", "pipe", length=20, diameter=60)
    s.add("sink", "drain")
    s.connect("src.port", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "pipe.port_a")
    s.connect("pipe.port_b", "sink.port")
    return s


def pump_data(
    seed: int | None,
    truth: dict[str, float] = TRUTH,
    sigma: dict[str, float] = PUMP_SIGMA,
    noise: float = 1.0,
) -> dict[str, Any]:
    """Valve-opening points measured on the pump at ``truth``, with noise ``noise * sigma``
    (none for ``seed=None``) and the stated uncertainty ``sigma``."""
    s = pump_circuit()
    s.set_values(truth)
    rng = np.random.default_rng(seed)
    points = []
    for y in OPENINGS:
        s.set("v.opening", y)
        r = s.solve()
        measured = {}
        for path, sg in sigma.items():
            e = 0.0 if seed is None else rng.normal(0.0, noise * sg)
            measured[path] = {"value": r[path] + e, "sigma": sg}
        points.append({"name": f"open-{y:g}", "settings": {"v.opening": y}, "measured": measured})
    return {"points": points}


def two_valves() -> wp.System:
    """Mains (3 bar) -> v1 (Kv 4, 60 % open) -> v2 (Kv 4, 80 % open) -> drain."""
    s = wp.System("two-valves")
    s.add("mains", "supply", pressure=3)
    s.add("v1", "valve", kv=4.0, opening=0.6)
    s.add("v2", "valve", kv=4.0, opening=0.8)
    s.add("out", "drain")
    s.connect("mains.port", "v1.port_a")
    s.connect("v1.port_b", "v2.port_a")
    s.connect("v2.port_b", "out.port")
    return s


def filter_rig(clogging: float = 0.0, housing: float = 0.3) -> wp.System:
    """A 1 m tank (3.5 m of water) draining through a media filter, a valve and a pipe."""
    s = wp.System("filter-rig")
    s.add("tank", "tank", diameter=1.0, height=4.0, initial_level=3.5)
    s.add(
        "filt",
        "media_filter",
        rated_flow=20,
        clean_pressure_drop=0.2,
        housing_fraction=housing,
        clogging=clogging,
    )
    s.add("v", "valve", kv=40)
    s.add("pipe", "pipe", length=5, diameter=50, minor_loss=1)
    s.add("out", "drain")
    s.connect("tank.outlet", "filt.inlet")
    s.connect("filt.outlet", "v.port_a")
    s.connect("v.port_b", "pipe.port_a")
    s.connect("pipe.port_b", "out.port")
    return s


#: The valve is throttled to half open 4 minutes into the filter run (a timed setting).
FILTER_EVENT = {"at": 240, "set": {"v.opening": 0.5}}
LEVEL_SIGMA, DP_SIGMA = 0.01, 0.005


def filter_series(seed: int | None, clogging: float = 0.6, housing: float = 0.3) -> dict[str, Any]:
    """Tank level and filter pressure drop every minute for 8 minutes, the valve throttled
    at 4 minutes (a settings-only point at that time)."""
    s = filter_rig(clogging, housing)
    sim = s.simulate(
        duration="8 min",
        step="60 s",
        events=[FILTER_EVENT],
        variables=["tank.level", "filt.pressure_drop"],
    )
    rng = np.random.default_rng(seed)
    points: list[dict[str, Any]] = []
    for t, level, dp in zip(sim.time, sim["tank.level"], sim["filt.pressure_drop"], strict=True):
        e1, e2 = (0.0, 0.0) if seed is None else rng.normal(0.0, [LEVEL_SIGMA, DP_SIGMA])
        points.append(
            {
                "name": f"t{t:g}",
                "time": t,
                "measured": {
                    "tank.level": {"value": level + e1, "sigma": LEVEL_SIGMA},
                    "filt.pressure_drop": {"value": dp + e2, "sigma": DP_SIGMA},
                },
            }
        )
    points[4]["settings"] = dict(FILTER_EVENT["set"])  # t = 240 s
    return {"points": points}


def within(result: wp.CalibrationResult, truth: dict[str, float], k: float = 2.0) -> bool:
    return all(
        abs(result[p] - v) < k * (result.parameters[p].standard_error or 0.0)
        for p, v in truth.items()
    )


# ----------------------------------------------------------------------------------------
# calibration: statistics
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_recovers_pump_wear_from_noisy_measurements(seed: int) -> None:
    """Head and efficiency wear from flow, outlet pressure and shaft power at four valve
    openings: the truth is inside two standard errors and the fit is statistically sound."""
    s = pump_circuit()
    before = s.to_dict()
    res = wp.calibrate(s, pump_data(seed), WEAR_BOUNDS)
    assert res.success
    assert within(res, TRUTH)
    for path in TRUTH:
        est = res.parameters[path]
        assert est.verdict == "identifiable"
        assert est.unit == "1" and est.initial == 0.0 and est.at_bound is None
        assert 0.001 < est.standard_error < 0.02  # a few thousandths of wear
    assert res.dof == 12 - 2
    assert res.reduced_chi_square is not None and 0.1 < res.reduced_chi_square < 3.0
    assert res.initial_chi_square > 20 * res.chi_square
    assert res.singular_values[0] >= res.singular_values[1] > 0
    assert abs(res.correlation["pump.wear_head"]["pump.wear_efficiency"]) < 0.95
    assert res.null_directions == []
    assert res.failures == 0
    assert s.to_dict() == before  # restored
    assert s.get("pump.wear_head") == 0.0


def test_standard_errors_match_the_scatter_of_repeated_fits() -> None:
    """Over 16 independent noisy data sets the estimates scatter as the standard errors
    say, centre on the truth, cover it about 95 % of the time at two standard errors, and
    the reduced chi-square averages about 1."""
    estimates, errors, chi, covered = [], [], [], 0
    for seed in range(100, 116):
        res = wp.calibrate(pump_circuit(), pump_data(seed), WEAR_BOUNDS)
        estimates.append([res[p] for p in TRUTH])
        errors.append([res.parameters[p].standard_error for p in TRUTH])
        chi.append(res.reduced_chi_square)
        covered += within(res, TRUTH)
    est, se = np.array(estimates), np.array(errors)
    truth = np.array(list(TRUTH.values()))
    n = len(est)
    assert np.all(np.abs(est.mean(axis=0) - truth) < 3 * se.mean(axis=0) / math.sqrt(n))
    ratio = est.std(axis=0, ddof=1) / se.mean(axis=0)
    assert np.all((ratio > 0.6) & (ratio < 1.4)), ratio
    assert covered >= 13  # 16 * 0.95 = 15.2 expected
    assert 0.6 < float(np.mean(chi)) < 1.5


def test_understated_uncertainties_inflate_the_standard_errors() -> None:
    """Noise four times the stated sigma gives a reduced chi-square near 16 and a tiny
    p-value; the standard errors are scaled by its square root (design 14.2), so the truth
    stays inside two of them."""
    honest = wp.calibrate(pump_circuit(), pump_data(7), WEAR_BOUNDS)
    over = wp.calibrate(pump_circuit(), pump_data(7, noise=4.0), WEAR_BOUNDS)
    assert over.reduced_chi_square is not None and over.reduced_chi_square > 5
    assert over.error_scale == pytest.approx(math.sqrt(over.reduced_chi_square))
    for p in TRUTH:
        ratio = over.parameters[p].standard_error / honest.parameters[p].standard_error
        assert ratio == pytest.approx(over.error_scale / honest.error_scale, rel=0.05)
    assert within(over, TRUTH)
    assert over.p_value is not None and over.p_value < 0.01
    assert any("misses the data" in note for note in over.notes)


def test_identifiability_predicts_the_calibration_standard_errors() -> None:
    """At the truth, with the same sigmas, identifiability() gives the standard errors that
    calibrating noise-free data gives (same Jacobian, error scale 1)."""
    fit = wp.calibrate(pump_circuit(), pump_data(None), WEAR_BOUNDS)
    assert fit.chi_square < 1e-12 and fit.error_scale == 1.0
    s = pump_circuit()
    s.set_values(TRUTH)
    report = wp.identifiability(
        s,
        {"pump.volume_flow": "0.3 m3/h", "pump.outlet.p": "0.02 bar", "pump.shaft_power": 0.03},
        WEAR_BOUNDS,
        points=[{"v.opening": y} for y in OPENINGS],
    )
    for p in TRUTH:
        assert fit[p] == pytest.approx(TRUTH[p], abs=1e-7)
        assert report.parameters[p].standard_error == pytest.approx(
            fit.parameters[p].standard_error, rel=1e-4
        )
        assert report.parameters[p].value == TRUTH[p]
    assert report.correlation["pump.wear_head"]["pump.wear_efficiency"] == pytest.approx(
        fit.correlation["pump.wear_head"]["pump.wear_efficiency"], abs=1e-4
    )


@pytest.mark.parametrize("seed", [11, 12])
def test_media_filter_clogging_from_a_time_series(seed: int) -> None:
    """A tank drains through a filter; the level and the filter pressure drop, logged every
    minute with the valve throttled at 4 minutes, determine the clogging."""
    s = filter_rig(0.0)
    res = wp.calibrate(s, filter_series(seed), {"filt.clogging": [0, 0.95]})
    est = res.parameters["filt.clogging"]
    assert abs(est.value - 0.6) < 2 * est.standard_error
    assert est.verdict == "identifiable" and est.standard_error < 0.02
    assert res.reduced_chi_square is not None and res.reduced_chi_square < 3
    assert {r.time for r in res.residuals} == {60.0 * k for k in range(9)}
    assert res.paths["tank.level"].count == 9 and res.paths["tank.level"].unit == "m"
    assert s.get("tank.level") == 3.5 and s.get("v.opening") == 1.0  # restored


def test_time_series_settings_are_events() -> None:
    """Without the valve event at 4 minutes the same data cannot be fitted: the settings of
    a timed point act from its time on."""
    data = filter_series(None)
    good = wp.calibrate(filter_rig(), data, {"filt.clogging": [0, 0.95]})
    assert good["filt.clogging"] == pytest.approx(0.6, abs=1e-6)
    del data["points"][4]["settings"]
    bad = wp.calibrate(filter_rig(), data, {"filt.clogging": [0, 0.95]})
    assert bad.chi_square > 1e3 * max(good.chi_square, 1e-6)
    assert bad.p_value is not None and bad.p_value < 1e-6


def test_steady_and_timed_points_together() -> None:
    """A steady point (settings applied, then solved from the starting state) can sit next
    to a time series."""
    data = filter_series(None)
    truth = filter_rig(0.6)
    truth.set("v.opening", 0.3)
    dp = truth.solve()["filt.pressure_drop"]
    data["points"].append(
        {
            "name": "throttled",
            "settings": {"v.opening": 0.3},
            "measured": {"filt.pressure_drop": dp},
        }
    )
    res = wp.calibrate(filter_rig(), data, {"filt.clogging": [0, 0.95]})
    assert res["filt.clogging"] == pytest.approx(0.6, abs=1e-6)
    steady = [r for r in res.residuals if r.point == "throttled"]
    assert len(steady) == 1 and steady[0].time is None and steady[0].sigma_default


# ----------------------------------------------------------------------------------------
# identifiability of pairs
# ----------------------------------------------------------------------------------------
def _valve_flow_data() -> dict[str, Any]:
    s = two_valves()
    rng = np.random.default_rng(0)
    points = []
    for p in (1.0, 2.0, 3.0, 4.0):
        s.set("mains.pressure", p)
        q = s.solve()["v1.volume_flow"]
        points.append(
            {
                "name": f"{p:g} bar",
                "settings": {"mains.pressure": p},
                "measured": {"v1.volume_flow": {"value": q + rng.normal(0, 0.2), "sigma": 0.2}},
            }
        )
    return {"points": points}


def test_valves_in_series_seen_only_through_their_sum_are_not_identifiable() -> None:
    """Two valves in series measured only by their flow: the flow depends on the openings
    only through 1/Kv1**2 + 1/Kv2**2, a sum, at every supply pressure."""
    res = wp.calibrate(
        two_valves(), _valve_flow_data(), {"v1.opening": [0.1, 1], "v2.opening": [0.1, 1]}
    )
    for p in ("v1.opening", "v2.opening"):
        assert res.parameters[p].verdict in ("not_identifiable", "weak")
    # Exactly: the null direction is found and named.
    assert [e.verdict for e in res.parameters.values()] == ["not_identifiable"] * 2
    assert res.parameters["v1.opening"].standard_error is None
    assert "v2.opening" in res.parameters["v1.opening"].reason
    (null,) = res.null_directions
    assert set(null["combination"]) == {"v1.opening", "v2.opening"}
    assert res.singular_values[1] < 1e-8 * res.singular_values[0]
    assert res.condition_number is None or res.condition_number > 1e8
    assert res.dof == 4 - 1  # one determined combination
    assert any("Not identifiable" in n for n in res.notes)
    json.dumps(res.to_dict(), allow_nan=False)


def test_filter_seen_only_through_a_product_is_not_identifiable() -> None:
    """With no housing loss the filter's drop is clean_pressure_drop / (1 - clogging) times
    the flow: the data fix only that ratio."""
    data = filter_series(None, housing=0.0)
    res = wp.calibrate(
        filter_rig(0.3, housing=0.0),
        data,
        {"filt.clogging": [0, 0.95], "filt.clean_pressure_drop": ["0.05 bar", "1 bar"]},
    )
    verdicts = {p: e.verdict for p, e in res.parameters.items()}
    assert set(verdicts.values()) <= {"not_identifiable", "weak"}
    assert verdicts == dict.fromkeys(verdicts, "not_identifiable")
    # The fitted pair still reproduces the ratio: 0.2 / (1 - 0.6) = 0.5 bar.
    ratio = res["filt.clean_pressure_drop"] / (1 - res["filt.clogging"])
    assert ratio == pytest.approx(0.5, rel=1e-5)
    # With the housing loss the linear and quadratic parts separate over the flow range:
    # determined, but so strongly correlated that it is weak.
    both = wp.calibrate(
        filter_rig(0.3),
        filter_series(5),
        {"filt.clogging": [0, 0.95], "filt.clean_pressure_drop": ["0.05 bar", "1 bar"]},
    )
    assert {e.verdict for e in both.parameters.values()} == {"weak"}
    assert abs(both.correlation["filt.clogging"]["filt.clean_pressure_drop"]) > 0.95


def test_identifiability_recommends_a_pressure_between_the_valves() -> None:
    """Flow alone cannot split the two valves; a pressure between them can, and nothing
    upstream or downstream of both helps."""
    s = two_valves()
    before = s.to_dict()
    report = wp.identifiability(
        s,
        ["v1.volume_flow"],
        {"v1.opening": [0.1, 1], "v2.opening": [0.1, 1]},
        candidates=["mains.port.p", "v2.volume_flow", "v1.port_b.p", "out.port.p"],
    )
    assert {e.verdict for e in report.parameters.values()} == {"not_identifiable"}
    assert report.worst in ("v1.opening", "v2.opening")
    rec = report.recommendation
    assert rec is not None and rec.sensor == "v1.port_b.p"
    assert rec.verdict == "identifiable" and rec.identifiable == 2
    assert rec.standard_error is not None and rec.relative_error < 0.05
    others = {c.sensor: c for c in report.candidates if c.sensor != "v1.port_b.p"}
    assert all(c.verdict == "not_identifiable" for c in others.values())
    assert s.to_dict() == before
    # Default candidates: every observable, state and port pressure; the pressure between
    # the valves and its aliases (the same node) are equivalent, and each resolves the pair
    # (so does a valve position readback).
    report = wp.identifiability(
        s, ["v1.volume_flow"], {"v1.opening": [0.1, 1], "v2.opening": [0.1, 1]}
    )
    rec = report.recommendation
    assert rec is not None and rec.verdict == "identifiable"
    between = next(c for c in report.candidates if c.sensor == "v1.port_b.p")
    assert between.verdict == "identifiable"
    assert "v2.port_a.p" in between.equivalent
    json.dumps(report.to_dict(), allow_nan=False)


def test_identifiability_names_a_power_sensor_for_efficiency_wear() -> None:
    """Pressure and flow see head wear but not efficiency wear, which changes only the
    shaft power; a power-related sensor makes it identifiable."""
    s = pump_circuit()
    s.set_values({"pump.wear_head": 0.1, "pump.wear_efficiency": 0.1})
    report = wp.identifiability(
        s,
        ["pump.outlet.p", "pump.volume_flow"],
        WEAR_BOUNDS,
        points=[{"v.opening": 1.0}, {"name": "half", "settings": {"v.opening": 0.5}}],
    )
    head, eff = report.parameters["pump.wear_head"], report.parameters["pump.wear_efficiency"]
    assert head.verdict == "identifiable" and head.standard_error < 0.02
    assert eff.verdict == "not_identifiable" and eff.standard_error is None
    assert "No measured value responds" in eff.reason
    assert report.null_directions[0]["combination"] == {"pump.wear_efficiency": 1.0}
    assert report.worst == "pump.wear_efficiency"
    assert report.points == ["point_1", "half"]
    rec = report.recommendation
    assert rec is not None
    assert rec.sensor in ("pump.shaft_power", "pump.efficiency", "pump.specific_energy")
    assert rec.parameter == "pump.wear_efficiency" and rec.verdict == "identifiable"
    hydraulic = next(c for c in report.candidates if c.sensor == "pump.hydraulic_power")
    assert hydraulic.verdict == "not_identifiable"  # rho g Q H does not see efficiency
    assert s.get("pump.wear_efficiency") == 0.1 and s.get("v.opening") == 1.0
    # A worse sensor (0.05 bar instead of 1 % of the reading) gives a larger standard error.
    coarse = wp.identifiability(
        s, {"pump.outlet.p": "0.05 bar", "pump.volume_flow": None}, WEAR_BOUNDS
    )
    fine = wp.identifiability(s, ["pump.outlet.p", "pump.volume_flow"], WEAR_BOUNDS)
    assert (
        coarse.parameters["pump.wear_head"].standard_error
        > fine.parameters["pump.wear_head"].standard_error
    )


# ----------------------------------------------------------------------------------------
# units, defaults and loading
# ----------------------------------------------------------------------------------------
def test_measured_values_convert_to_the_declared_units() -> None:
    s = pump_circuit()
    s.set("pump.wear_head", 0.1)
    r = s.solve()
    s.set("pump.wear_head", 0.0)
    native = {"pump.volume_flow": r["pump.volume_flow"], "pump.outlet.p": r["pump.outlet.p"]}
    converted = {
        "pump.volume_flow": {
            "value": f"{r['pump.volume_flow'] * 1000 / 60!r} L/min",
            "sigma": "5 L/min",
        },
        "pump.outlet.p": f"{(r['pump.outlet.p'] + 1.01325) * 100!r} kPa absolute",
    }
    fits = []
    for measured in (native, converted):
        ms = wp.MeasurementSet.from_dict({"points": [{"name": "a", "measured": measured}]})
        values = {v.path: v for v in ms.resolve(s).values}
        assert values["pump.volume_flow"].value == pytest.approx(r["pump.volume_flow"])
        assert values["pump.outlet.p"].value == pytest.approx(r["pump.outlet.p"])
        assert (
            values["pump.outlet.p"].reference == "gauge" and values["pump.outlet.p"].unit == "bar"
        )
        fits.append(wp.calibrate(s, ms, {"pump.wear_head": [0, 0.5]}))
    assert fits[0]["pump.wear_head"] == pytest.approx(0.1, abs=1e-6)
    assert fits[1]["pump.wear_head"] == pytest.approx(0.1, abs=1e-6)
    flow = next(v for v in fits[1].residuals if v.path == "pump.volume_flow")
    assert flow.sigma == pytest.approx(0.3) and flow.unit == "m3/h" and not flow.sigma_default
    # A sigma is a difference: 0.2 degC is 0.2 K, never 273.35 K; a temperature in K
    # converts with its offset.
    ms = wp.MeasurementSet.from_dict(
        {
            "points": [
                {
                    "name": "a",
                    "measured": {"src.temperature": {"value": "300 K", "sigma": "0.2 degC"}},
                }
            ]
        }
    )
    (value,) = ms.resolve(s).values
    assert value.value == pytest.approx(26.85) and value.sigma == pytest.approx(0.2)


def test_default_sigma_floors() -> None:
    """1 % of the value, floored per kind of quantity (design 14.1)."""
    cases = [
        ((2.0, "bar", "gauge"), 0.02),
        ((0.2, "bar", "gauge"), 0.01),  # floor 0.01 bar
        ((0.0, "kPa", "difference"), 1.0),  # 0.01 bar = 1 kPa
        ((30.0, "m3/h", None), 0.3),
        ((1.0, "m3/h", None), 0.05),
        ((10.0, "L/min", None), 0.05 * 1000 / 60),
        ((0.001, "kg/s", None), 0.05 / 3600 * 998.2),
        ((55.0, "degC", None), 0.55),  # 1 % of the Celsius value
        ((328.15, "K", None), 0.55),  # the same temperature in kelvin
        ((131.0, "degF", None), 0.99),  # 55 degC; 0.55 K is 0.99 degF
        ((2.0, "degC", None), 0.1),  # floor 0.1 K
        ((35.0, "K", "difference"), 0.35),
        ((2.5, "kW", None), 0.025),
        ((0.1, "kW", None), 0.01),
        ((0.5, "m", None), 0.01),
        ((0.5, "1", None), 0.005),
        ((0.0, "1", None), 0.001),
        ((50.0, "%", None), 0.5),
        ((0.02, "%", None), 0.1),  # 0.001 as a fraction is 0.1 %
        ((0.12, "kWh/m3", None), 0.0012),
        ((40.0, "mJ/cm2", None), 0.4),
        ((1.0, "mJ/cm2", None), 0.1),
        ((20.0, "mW/cm2", None), 0.2),
        ((2900.0, "rpm", None), 29.0),
        ((15.0, "L", None), 1.0),
        ((2.5, "s", None), 0.1),
        ((1.2, "m/s", None), 0.012),
    ]
    for (value, unit, ref), expected in cases:
        assert wp.default_sigma(value, unit, ref) == pytest.approx(expected, rel=1e-9), unit
    with pytest.raises(wp.WorldpartsError, match=r"give 'sigma'"):
        wp.default_sigma(1.0, "J")


def test_every_reported_catalogue_unit_has_a_default_sigma(catalog: wp.Catalog) -> None:
    for m in catalog:
        for group in (m.parameters, m.inputs, m.states, m.observables):
            for spec in group.values():
                if spec.is_numeric:
                    kind = quantity_kind(spec.unit, spec.reference)
                    assert kind in SIGMA_FLOORS
                    assert wp.default_sigma(0.0, spec.unit, spec.reference) > 0


def _write_all_formats(tmp_path: Path) -> dict[str, Path]:
    data = {
        "name": "field readings",
        "points": [
            {
                "name": "open",
                "settings": {"v.opening": 1.0},
                "measured": {
                    "pump.volume_flow": {"value": 22.3, "unit": "m3/h", "sigma": 0.3},
                    "pump.outlet.p": {"value": 2.35, "unit": "bar"},
                },
            },
            {
                "name": "half",
                "settings": {"v.opening": 0.5},
                "measured": {"pump.volume_flow": {"value": 12.4, "unit": "m3/h", "sigma": 0.3}},
            },
        ],
    }
    paths = {"yaml": tmp_path / "m.yaml", "json": tmp_path / "m.json", "csv": tmp_path / "m.csv"}
    paths["yaml"].write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    paths["json"].write_text(json.dumps(data), encoding="utf-8")
    paths["csv"].write_text(
        "point,time,path,value,unit,sigma,kind\n"
        "open,,v.opening,1.0,,,setting\n"
        "open,,pump.volume_flow,22.3,m3/h,0.3,\n"
        "open,,pump.outlet.p,2.35,bar,,\n"
        "\n"
        "half,,v.opening,0.5,,,setting\n"
        "half,,pump.volume_flow,12.4,m3/h,0.3,measured\n",
        encoding="utf-8",
    )
    return paths


def test_csv_yaml_and_json_load_the_same_set(tmp_path: Path) -> None:
    paths = _write_all_formats(tmp_path)
    sets = {k: wp.load_measurements(p) for k, p in paths.items()}
    points = {k: ms.to_dict()["points"] for k, ms in sets.items()}
    assert points["yaml"] == points["json"] == points["csv"]
    assert sets["yaml"].name == "field readings" and sets["csv"].name is None
    assert sets["csv"].n_values == 3 and sets["csv"].paths == ["pump.volume_flow", "pump.outlet.p"]
    again = wp.MeasurementSet.from_dict(sets["yaml"].to_dict())
    assert again.to_dict() == sets["yaml"].to_dict()
    s = pump_circuit()
    resolved = [ms.resolve(s) for ms in sets.values()]
    assert resolved[0] == resolved[1] == resolved[2]
    # calibrate() takes a file path directly.
    res = wp.calibrate(s, paths["csv"], {"pump.wear_head": [0, 0.5]})
    assert res.parameters["pump.wear_head"].verdict == "identifiable"


def test_csv_time_series_without_point_names(tmp_path: Path) -> None:
    path = tmp_path / "series.csv"
    path.write_text(
        "time,path,value,unit,sigma,kind\n"
        "0,tank.level,3.5,m,0.01,\n"
        "60 s,tank.level,3.21,m,0.01,\n"
        "2 min,tank.level,2.95,m,0.01,\n"
        "2 min,v.opening,0.5,,,setting\n",
        encoding="utf-8",
    )
    ms = wp.load_measurements(path)
    assert [(p.name, p.time) for p in ms] == [("t=0 s", 0.0), ("t=60 s", 60.0), ("t=120 s", 120.0)]
    assert ms.points[2].settings == {"v.opening": 0.5}
    ms.validate(filter_rig())


def test_malformed_measurement_files_list_every_problem(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "point,time,path,value,unit,sigma\n"
        "a,0,pump.head,30,m,\n"
        "a,5,pump.volume_flow,20,m3/h,\n"
        ",,pump.volume_flow,20,m3/h,\n",
        encoding="utf-8",
    )
    with pytest.raises(wp.MeasurementError) as info:
        wp.load_measurements(bad)
    assert info.value.code == "invalid_measurements"
    assert len(info.value.problems) == 2
    assert "row 3" in str(info.value) and "a point has one time" in str(info.value)
    assert "give a point name or a time" in str(info.value)
    columns = tmp_path / "columns.csv"
    columns.write_text("point,path,value,flow\n", encoding="utf-8")
    with pytest.raises(wp.MeasurementError, match=r"unknown CSV column.*'flow'"):
        wp.load_measurements(columns)
    with pytest.raises(wp.MeasurementError, match=r"Use .yaml, .yml, .json or .csv"):
        wp.load_measurements(tmp_path / "m.txt")
    with pytest.raises(wp.MeasurementError, match=r"Cannot read"):
        wp.load_measurements(tmp_path / "missing.yaml")
    with pytest.raises(wp.MeasurementError) as info:
        wp.MeasurementSet.from_dict(
            {
                "points": [
                    {"name": "a", "measured": {"pump.volume_flow": "fast"}},
                    {"name": "a", "measured": {"pump.volume_flow": {"value": 3, "sigma": 0}}},
                ],
                "units": "SI",
            }
        )
    text = str(info.value)
    assert "points[0] ('a').measured['pump.volume_flow']" in text and "'fast'" in text
    assert "sigma must be positive" in text and "used 2 times" in text and "'units'" in text


# ----------------------------------------------------------------------------------------
# restore, apply and errors
# ----------------------------------------------------------------------------------------
def test_restore_by_default_and_apply_on_request() -> None:
    data = pump_data(4)
    s = pump_circuit()
    before, solved = s.to_dict(), s.solve().values
    res = wp.calibrate(s, data, WEAR_BOUNDS)
    assert not res.applied and s.to_dict() == before and s.solve().values == solved
    applied = wp.calibrate(s, data, WEAR_BOUNDS, apply=True)
    assert applied.applied
    assert applied.values == res.values
    for p in TRUTH:
        assert s.get(p) == pytest.approx(res[p], rel=1e-12)
    assert s.get("v.opening") == 1.0  # point settings are not kept
    pump_inputs = s.to_dict()["components"][1]["inputs"]
    assert pump_inputs == pytest.approx({"wear_head": res[WH], "wear_efficiency": res[WE]})
    # Timed runs restore the tank level that the simulation drained.
    rig = filter_rig()
    wp.calibrate(rig, filter_series(3), {"filt.clogging": [0, 0.95]})
    assert rig.get("tank.level") == 3.5 and rig.get("filt.clogging") == 0.0


def test_clear_errors() -> None:
    s = pump_circuit()
    point = {"name": "a", "measured": {"pump.volume_flow": 20}}
    data = {"points": [point]}
    with pytest.raises(wp.MeasurementError) as info:
        wp.calibrate(
            s,
            {"points": [{"name": "a", "measured": {"pump.volume_flo": 20, "pump.head_curve": 1}}]},
            ["pump.wear_head"],
        )
    text = str(info.value)
    assert "'pump.volume_flo'" in text and "Did you mean 'pump.volume_flow'" in text
    assert "pump.head_curve is a table parameter" in text
    with pytest.raises(wp.MeasurementError, match=r"not compatible with the declared unit bar"):
        wp.calibrate(s, {"points": [{"measured": {"pump.outlet.p": "3 m3/h"}}]}, ["pump.wear_head"])
    with pytest.raises(
        wp.MeasurementError, match=r"settable variables of v: Did you mean 'v.opening'"
    ):
        wp.calibrate(s, {"points": [{**point, "settings": {"v.openin": 1}}]}, ["pump.wear_head"])
    with pytest.raises(wp.UnknownVariableError, match=r"Did you mean 'pump.wear_head'"):
        wp.calibrate(s, data, ["pump.wear_hed"])
    with pytest.raises(wp.UnknownVariableError, match=r"read-only"):
        wp.calibrate(s, data, ["pump.head"])
    with pytest.raises(wp.InvalidValueError, match=r"table parameter.*pump.wear_head"):
        wp.calibrate(s, data, ["pump.head_curve"])
    with pytest.raises(wp.InvalidValueError, match=r"string parameter"):
        wp.calibrate(s, data, ["v.characteristic"])
    with pytest.raises(wp.InvalidValueError, match=r"lower < upper.*hard limits are \[0, 0\.5\]"):
        wp.calibrate(s, data, {"pump.wear_head": [0.4, 0.1]})
    with pytest.raises(wp.OutOfRangeError, match=r"upper = 0.7 is outside"):
        wp.calibrate(s, data, {"pump.wear_head": [0, 0.7]})
    with pytest.raises(wp.MeasurementError, match=r"no measured values"):
        wp.calibrate(s, {"points": []}, ["pump.wear_head"])
    with pytest.raises(wp.MeasurementError, match=r"no measured values"):
        wp.calibrate(s, {"points": [{"settings": {"v.opening": 1}}]}, ["pump.wear_head"])
    with pytest.raises(wp.InvalidValueError, match=r"No parameters"):
        wp.calibrate(s, data, [])
    with pytest.raises(wp.InvalidValueError, match=r"method must be one of trf, dogbox"):
        wp.calibrate(s, data, ["pump.wear_head"], method="lm")
    with pytest.raises(
        wp.InvalidValueError, match=r"sets pump.wear_head, which is being calibrated"
    ):
        wp.calibrate(
            s, {"points": [{**point, "settings": {"pump.wear_head": 0.1}}]}, ["pump.wear_head"]
        )
    with pytest.raises(wp.InvalidValueError, match=r"is a state"):
        wp.calibrate(
            filter_rig(), {"points": [{"measured": {"filt.pressure_drop": 0.2}}]}, ["tank.level"]
        )
    with pytest.raises(wp.InvalidValueError, match=r"not a measurable"):
        wp.identifiability(s, ["pump.volume_flo"], ["pump.wear_head"])
    with pytest.raises(wp.InvalidValueError, match=r"outside its bounds"):
        wp.identifiability(s, ["pump.volume_flow"], {"pump.wear_head": [0.1, 0.5]})
    assert s.get("pump.wear_head") == 0.0


def test_model_failures_inside_the_fit_are_reported() -> None:
    """A tank level above the tank's height cannot be evaluated. A flow that needs such a
    level pushes the fit to the edge of the valid region, which is reported."""

    def drain_tank() -> wp.System:
        s = wp.System("drain-tank")
        s.add("tank", "tank", diameter=1.0, height=4.0, initial_level=2.0)
        s.add("pipe", "pipe", length=5, diameter=50, minor_loss=1)
        s.add("out", "drain")
        s.connect("tank.outlet", "pipe.port_a")
        s.connect("pipe.port_b", "out.port")
        return s

    probe = drain_tank()
    probe.set("tank.initial_level", 4.0)
    too_much = probe.solve()["pipe.volume_flow"] * 1.05
    data = {
        "points": [{"name": "a", "measured": {"pipe.volume_flow": {"value": too_much, "sigma": 1}}}]
    }
    res = wp.calibrate(drain_tank(), data, {"tank.initial_level": [0.5, 6]})
    est = res.parameters["tank.initial_level"]
    assert est.value == pytest.approx(4.0, abs=1e-3)
    assert est.at_bound == "model_limit"
    assert res.failures > 0 and "must not exceed height" in res.failure_examples[0]
    assert any("cannot be evaluated" in n for n in res.notes)
    # A model that fails at the starting values is an error that says so.
    bad_point = {
        "name": "a",
        "settings": {"tank.height": 1.0},
        "measured": {"pipe.volume_flow": 100},
    }
    with pytest.raises(wp.CalibrationError, match=r"cannot be evaluated at the starting values"):
        wp.calibrate(drain_tank(), {"points": [bad_point]}, {"pipe.minor_loss": [0, 10]})


def test_fit_at_a_bound_is_flagged() -> None:
    """A new pump whose outlet reads 0.03 bar high wants negative head wear: the fit stops
    at the lower bound and says so."""
    s = pump_circuit()
    points = []
    for y in OPENINGS:
        s.set("v.opening", y)
        p = s.solve()["pump.outlet.p"]
        points.append(
            {
                "name": f"{y}",
                "settings": {"v.opening": y},
                "measured": {"pump.outlet.p": {"value": p + 0.03, "sigma": 0.02}},
            }
        )
    res = wp.calibrate(pump_circuit(), {"points": points}, {"pump.wear_head": [0, 0.5]})
    est = res.parameters["pump.wear_head"]
    assert est.value == 0.0 and est.at_bound == "lower"
    assert any("lower bound" in n for n in res.notes)


def test_results_are_json_safe_with_units() -> None:
    res = wp.calibrate(pump_circuit(), pump_data(9), WEAR_BOUNDS)
    d = json.loads(json.dumps(res.to_dict(), allow_nan=False))
    assert d["parameters"]["pump.wear_head"]["unit"] == "1"
    assert all("unit" in r for r in d["residuals"])
    assert all("unit" in f for f in d["paths"].values())
    pressure = next(r for r in d["residuals"] if r["path"] == "pump.outlet.p")
    assert pressure["unit"] == "bar" and pressure["reference"] == "gauge"
    assert d["failures"] == {"count": 0, "examples": []}
    report = wp.identifiability(pump_circuit(), ["pump.outlet.p"], WEAR_BOUNDS)
    r = json.loads(json.dumps(report.to_dict(), allow_nan=False))
    assert r["parameters"]["pump.wear_efficiency"]["standard_error"] is None
    assert r["recommendation"]["unit"] and len(r["candidates"]) <= 10


def test_sample_interpolates_between_simulation_samples() -> None:
    times, series = [0.0, 60.0, 120.0], [3.0, 2.0, None]
    assert _sample(times, series, 60.0, 1e-9) == 2.0
    assert _sample(times, series, 15.0, 1e-9) == pytest.approx(2.75)
    assert _sample(times, series, 90.0, 1e-9) is None
