"""Review of calibration and identifiability (design 14.1, 14.2).

Every test here demonstrates a defect found in review; each fails against the
implementation it was written for, except the narrow-bound cases of the parametrized ones,
which are their controls. The statistical ones compare with a closed form or with data
from a much finer simulation than the fit uses, the way real data come from a continuous
process.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from scipy.optimize import least_squares
from test_calibration import (
    DP_SIGMA,
    FILTER_EVENT,
    LEVEL_SIGMA,
    OPENINGS,
    WEAR_BOUNDS,
    filter_rig,
    filter_series,
    pump_circuit,
    pump_data,
)

import worldparts as wp
import worldparts.calibration as cal

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
RHO = 998.2
PRESSURES = (1.0, 2.0, 3.0, 4.0)


# ----------------------------------------------------------------------------------------
# two valves in series: a closed form
# ----------------------------------------------------------------------------------------
def valves(kv1: float, kv2: float) -> wp.System:
    """Mains -> v1 -> v2 -> drain, both fully open."""
    s = wp.System("valves")
    s.add("mains", "supply", pressure=1.0)
    s.add("v1", "valve", kv=kv1)
    s.add("v2", "valve", kv=kv2)
    s.add("out", "drain")
    s.connect("mains.port", "v1.port_a")
    s.connect("v1.port_b", "v2.port_a")
    s.connect("v2.port_b", "out.port")
    return s


def closed_form(kv: np.ndarray, pressure: float) -> tuple[float, float]:
    """Flow in L/min and the pressure between the valves in bar gauge (Kv definition)."""
    a, b = 1.0 / kv[0] ** 2, 1.0 / kv[1] ** 2
    flow = math.sqrt(1000.0 * pressure / RHO) / math.sqrt(a + b) * 1000.0 / 60.0
    return flow, pressure * b / (a + b)


def valve_data(sigma_p: float, seed: int = 3) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    points = []
    for p in PRESSURES:
        q, mid = closed_form(np.array([5.0, 7.0]), p)
        points.append(
            {
                "name": f"{p:g} bar",
                "settings": {"mains.pressure": p},
                "measured": {
                    "v1.volume_flow": {"value": q + rng.normal(0, 2.0), "sigma": 2.0},
                    "v1.port_b.p": {"value": mid + rng.normal(0, sigma_p), "sigma": sigma_p},
                },
            }
        )
    return {"points": points}


def analytic_fit(data: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Values and standard errors of an independent fit with the closed form and its exact
    Jacobian (standard errors scaled by the reduced chi-square when it exceeds 1)."""
    rows = [
        (pt["settings"]["mains.pressure"], k, m["value"], m["sigma"])
        for pt in data["points"]
        for k, m in enumerate(pt["measured"].values())
    ]

    def fun(kv: np.ndarray) -> np.ndarray:
        return np.array([(closed_form(kv, p)[k] - y) / s for p, k, y, s in rows])

    def jac(kv: np.ndarray) -> np.ndarray:
        out = []
        for p, k, _, s in rows:
            a, b = 1.0 / kv[0] ** 2, 1.0 / kv[1] ** 2
            da, db = -2.0 / kv[0] ** 3, -2.0 / kv[1] ** 3
            if k == 0:
                c = math.sqrt(1000.0 * p / RHO) * 1000.0 / 60.0
                g = -0.5 * c * (a + b) ** -1.5
                out.append([g * da / s, g * db / s])
            else:
                out.append([-p * b * da / (a + b) ** 2 / s, p * a * db / (a + b) ** 2 / s])
        return np.array(out)

    sol = least_squares(fun, [4.0, 6.0], jac=jac, xtol=1e-15, ftol=1e-15, gtol=1e-15)
    j = jac(sol.x)
    s2 = max(1.0, float(sol.fun @ sol.fun) / (len(rows) - 2))
    return sol.x, np.sqrt(np.diag(s2 * np.linalg.inv(j.T @ j)))


@pytest.mark.parametrize(
    "bounds",
    [
        {"v1.kv": [1, 20], "v2.kv": [1, 20]},
        ["v1.kv", "v2.kv"],  # the manifest's hard limits, [1e-4, 1e5] m3/h
        {"v1.kv": [0.5, 2000], "v2.kv": [0.5, 2000]},
    ],
)
def test_wide_bounds_do_not_change_the_fit(bounds: Any) -> None:
    """The finite-difference step and the start margin were fractions of the bound range:
    with plain paths a Kv of 5 was differenced with steps of 100 m3/h, the fit stalled at
    its start and both valves were called not identifiable. The estimate and its standard
    error must not depend on how wide the bounds are."""
    data = valve_data(0.02)
    values, errors = analytic_fit(data)
    res = wp.calibrate(valves(4.0, 6.0), data, bounds)
    assert res.success
    for k, path in enumerate(("v1.kv", "v2.kv")):
        est = res.parameters[path]
        assert est.verdict == "identifiable"
        assert est.value == pytest.approx(values[k], abs=1e-4 * errors[k])
        assert est.standard_error == pytest.approx(errors[k], rel=1e-4)
    assert res.null_directions == [] and res.dof == 8 - 2


def test_identifiability_with_plain_paths_matches_the_closed_form() -> None:
    s = valves(5.0, 7.0)
    points = [{"mains.pressure": p} for p in PRESSURES]
    sensors = {"v1.volume_flow": "2 L/min", "v1.port_b.p": "0.02 bar"}
    report = wp.identifiability(s, sensors, ["v1.kv", "v2.kv"], points=points, candidates=[])
    rows = [(p, k, s_) for p in PRESSURES for k, s_ in ((0, 2.0), (1, 0.02))]
    j = []
    for p, k, sg in rows:  # exact derivatives by central differences of the closed form
        h = 1e-6
        d1 = closed_form(np.array([5 + h, 7.0]), p)[k] - closed_form(np.array([5 - h, 7]), p)[k]
        d2 = closed_form(np.array([5.0, 7 + h]), p)[k] - closed_form(np.array([5, 7 - h]), p)[k]
        j.append([d1 / (2 * h) / sg, d2 / (2 * h) / sg])
    ja = np.array(j)
    expected = np.sqrt(np.diag(np.linalg.inv(ja.T @ ja)))
    for k, path in enumerate(("v1.kv", "v2.kv")):
        assert report.parameters[path].verdict == "identifiable"
        assert report.parameters[path].standard_error == pytest.approx(expected[k], rel=1e-4)


def test_poorly_known_pair_with_wide_bounds_keeps_its_standard_errors() -> None:
    """With a 0.5 bar pressure sigma and bounds [0.5, 2000] the numerical-error estimate of
    the lower-order difference (13 times the real error) exceeded the smaller singular
    value, so both valves were called not identifiable. Their standard errors are 9 % and
    30 % of the values, a small fraction of the range."""
    data = valve_data(0.5)
    values, errors = analytic_fit(data)
    res = wp.calibrate(valves(4.0, 6.0), data, {"v1.kv": [0.5, 2000], "v2.kv": [0.5, 2000]})
    for k, path in enumerate(("v1.kv", "v2.kv")):
        est = res.parameters[path]
        assert est.verdict != "not_identifiable"
        assert est.value == pytest.approx(values[k], abs=1e-3 * errors[k])
        assert est.standard_error == pytest.approx(errors[k], rel=1e-3)


# ----------------------------------------------------------------------------------------
# one badly differenced parameter must not change the others' verdicts
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("kv_bounds", [[0.5, 50], [0.5, 5000], None])
def test_null_threshold_is_per_direction(kv_bounds: list[float] | None) -> None:
    """The null threshold was the Frobenius norm of the whole Jacobian error: a Kv with
    wide bounds pushed both wear parameters into the null space."""
    sigma = {"pump.volume_flow": 0.3, "pump.outlet.p": 0.02, "pump.shaft_power": 0.03}
    s = pump_circuit()
    s.set_values({"v.kv": 4.0, "pump.wear_head": 0.1, "pump.wear_efficiency": 0.1})
    points = [{"v.opening": y} for y in OPENINGS]
    reference = wp.identifiability(
        s, sigma, {"v.kv": [0.5, 50], **WEAR_BOUNDS}, points=points, candidates=[]
    )
    report = wp.identifiability(
        s, sigma, {"v.kv": kv_bounds, **WEAR_BOUNDS}, points=points, candidates=[]
    )
    for path, est in report.parameters.items():
        assert est.verdict == "identifiable"
        assert est.standard_error == pytest.approx(
            reference.parameters[path].standard_error, rel=1e-4
        )


def test_error_bound_is_per_column() -> None:
    """A large numerical error in one column (here set by hand) leaves the other
    parameters' directions alone."""
    params = [cal._Param(p, "1", None, 0.0, 1.0, 0.5) for p in ("a", "b")]
    jz = np.array([[100.0, 0.0], [0.0, 5.0], [1.0, 1.0]])
    ez = np.array([[500.0, 0.0], [0.0, 1e-9], [0.0, 1e-9]])
    an = cal._analyse(jz, ez, params, 1.0)
    assert an.verdicts[1] == "identifiable" and an.verdicts[0] == "not_identifiable"


# ----------------------------------------------------------------------------------------
# blind parameters
# ----------------------------------------------------------------------------------------
def booster() -> wp.System:
    doc = yaml.safe_load((EXAMPLES / "booster_station.yaml").read_text(encoding="utf-8"))
    return wp.System.from_dict(doc)


def test_parameter_hidden_by_a_pressure_loop_is_not_identifiable() -> None:
    """A PI loop holds the zone pressure, so flow and outlet pressure do not see head wear
    (only solver residue, 9e-9 over the whole range). It was reported weak, with a standard
    error of 1e10 % of its range and the start margin as its value."""
    truth = booster()
    truth.set("pump.wear_head", 0.15)
    points = []
    for y in (0.5, 0.75, 1.0):
        truth.set("demand.opening", y)
        r = truth.solve()
        measured = {
            "pump.volume_flow": {"value": r["pump.volume_flow"], "sigma": 0.2},
            "pump.outlet.p": {"value": r["pump.outlet.p"], "sigma": 0.02},
        }
        points.append({"name": f"d{y}", "settings": {"demand.opening": y}, "measured": measured})
    res = wp.calibrate(booster(), {"points": points}, {"pump.wear_head": [0, 0.5]})
    est = res.parameters["pump.wear_head"]
    assert est.verdict == "not_identifiable" and est.standard_error is None
    assert est.value == 0.0 and est.at_bound is None
    assert "No measured value responds" in est.reason
    report = wp.identifiability(
        booster(),
        ["pump.volume_flow", "pump.outlet.p"],
        {"pump.wear_head": [0, 0.5]},
        points=[{"demand.opening": y} for y in (0.5, 0.75, 1.0)],
        candidates=[],
    )
    assert report.parameters["pump.wear_head"].verdict == "not_identifiable"


def test_blind_parameter_on_its_bound_is_not_flagged() -> None:
    """Efficiency wear without a power reading goes back to its start, 0, on the lower
    bound. It was flagged at_bound 'lower' with a note that the data push it further."""
    data = pump_data(1, sigma={"pump.volume_flow": 0.3, "pump.outlet.p": 0.02})
    res = wp.calibrate(pump_circuit(), data, WEAR_BOUNDS)
    est = res.parameters["pump.wear_efficiency"]
    assert est.verdict == "not_identifiable" and est.value == 0.0
    assert est.at_bound is None
    assert not any("bound" in n and "wear_efficiency" in n for n in res.notes)
    assert "at_bound" not in res.to_dict()["parameters"]["pump.wear_efficiency"]
    # A start clipped onto the upper bound is not flagged either.
    s = pump_circuit()
    s.set("pump.wear_efficiency", 0.45)
    res = wp.calibrate(s, data, {**WEAR_BOUNDS, "pump.wear_efficiency": [0, 0.3]})
    assert res.parameters["pump.wear_efficiency"].at_bound is None


def test_bound_note_says_how_far_the_data_push() -> None:
    """A parameter pushed onto its bound is flagged with how far the data push it, in
    standard errors, so a healthy value at the bound can be told from a wrong hypothesis."""
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
    assert est.at_bound == "lower"
    note = next(n for n in res.notes if "lower bound" in n)
    # 0.03 bar above the model at four points of 0.02 bar: 3 standard errors below 0.
    assert "(3 standard errors of that unconstrained fit)" in note


# ----------------------------------------------------------------------------------------
# the fit falling back to its start
# ----------------------------------------------------------------------------------------
def test_fit_worse_than_its_start_is_not_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the optimiser ends worse than the start, the start is reported: that must not
    look like a successful fit."""
    real = cal.least_squares

    def astray(*args: Any, **kwargs: Any) -> Any:
        sol = real(*args, **kwargs)
        sol.x = np.full_like(sol.x, 0.9)  # wear 0.45: far worse than the start
        return sol

    monkeypatch.setattr(cal, "least_squares", astray)
    res = wp.calibrate(pump_circuit(), pump_data(1), WEAR_BOUNDS)
    assert not res.success
    assert res.values == {"pump.wear_head": 0.0, "pump.wear_efficiency": 0.0}
    assert res.chi_square == res.initial_chi_square
    assert "worse" in res.message
    assert any("not a fit" in n and "0.45" in n for n in res.notes)


# ----------------------------------------------------------------------------------------
# the simulation step of timed points
# ----------------------------------------------------------------------------------------
TRUTH_STEP = 0.25  # s: a continuous process, as far as a fit to minute readings can tell


@functools.lru_cache(maxsize=4)
def _continuous_truth(duration: float) -> tuple[list[float], list[float]]:
    """Tank level and filter pressure drop of the filter rig at clogging 0.6, simulated with
    a fine step, with the valve throttled at 240 s."""
    sim = filter_rig(0.6).simulate(
        duration=duration,
        step=TRUTH_STEP,
        events=[FILTER_EVENT],
        variables=["tank.level", "filt.pressure_drop"],
    )
    return list(sim["tank.level"]), list(sim["filt.pressure_drop"])


def continuous_series(times: np.ndarray, seed: int | None) -> dict[str, Any]:
    """The continuous-process data read at ``times`` (s), with noise from ``seed``."""
    level, drop = _continuous_truth(float(times.max()))
    rng = np.random.default_rng(seed)
    points = []
    for t in times:
        k = round(float(t) / TRUTH_STEP)
        e1, e2 = (0.0, 0.0) if seed is None else rng.normal(0.0, [LEVEL_SIGMA, DP_SIGMA])
        point: dict[str, Any] = {
            "name": f"t{t:g}",
            "time": float(t),
            "measured": {
                "tank.level": {"value": level[k] + e1, "sigma": LEVEL_SIGMA},
                "filt.pressure_drop": {"value": drop[k] + e2, "sigma": DP_SIGMA},
            },
        }
        if t == 240:
            point["settings"] = dict(FILTER_EVENT["set"])
        points.append(point)
    return {"points": points}


@pytest.mark.parametrize("interval", [60.0, 240.0])
def test_timed_fit_is_not_biased_by_the_integration_step(interval: float) -> None:
    """The default step was the logging interval: with explicit Euler the estimate was 5.6
    (a reading a minute for 8 minutes) and 19 (every 4 minutes for 16 minutes) standard
    errors off on noise-free continuous data, with a good-looking chi-square. It must be
    within a fraction of a standard error."""
    times = np.arange(0.0, 961.0 if interval > 60 else 481.0, interval)
    res = wp.calibrate(filter_rig(), continuous_series(times, None), {"filt.clogging": [0, 0.95]})
    est = res.parameters["filt.clogging"]
    assert abs(est.value - 0.6) < 0.3 * est.standard_error
    assert res.step_extrapolated and res.step is not None
    assert res.step_change is not None and res.step_change <= cal.STEP_TOLERANCE
    assert res.to_dict()["step"]["unit"] == "s"
    assert not any("Check the hypothesis" in n for n in res.notes)


def test_timed_fits_cover_the_truth() -> None:
    """Over noisy continuous data the truth lies within two standard errors about 95 % of
    the time (it was 0 of 20 with the logging interval as the step), and the errors in
    standard errors centre on 0 with a spread of about 1."""
    times = np.arange(0.0, 481.0, 60.0)
    z = []
    for seed in range(20, 30):
        res = wp.calibrate(
            filter_rig(), continuous_series(times, seed), {"filt.clogging": [0, 0.95]}
        )
        est = res.parameters["filt.clogging"]
        z.append((est.value - 0.6) / est.standard_error)
    z_arr = np.array(z)
    assert np.sum(np.abs(z_arr) < 2) >= 8
    assert abs(z_arr.mean()) < 3 / math.sqrt(len(z)) and 0.5 < z_arr.std(ddof=1) < 1.5


def test_a_given_coarse_step_is_used_and_flagged() -> None:
    """A step given explicitly is used as System.simulate uses it; when halving it moves the
    predictions by more than the tolerance, a note says the estimate may be biased."""
    times = np.arange(0.0, 481.0, 60.0)
    res = wp.calibrate(
        filter_rig(), continuous_series(times, None), {"filt.clogging": [0, 0.95]}, step="60 s"
    )
    assert res.step == 60.0 and not res.step_extrapolated
    assert res.step_change is not None and res.step_change > cal.STEP_TOLERANCE
    assert any("halving the step moves the predictions" in n for n in res.notes)


def test_close_timestamps_do_not_set_the_step() -> None:
    """One reading 0.5 s after another set a 0.5 s step for the whole run (75 times
    slower), and one 1e-6 s after raised a misleading 'starting values' error."""
    data = filter_series(11)
    base = wp.calibrate(filter_rig(), data, {"filt.clogging": [0, 0.95]})
    for extra in (120.5, 120.000001):
        jittered = json.loads(json.dumps(data))
        jittered["points"].append(dict(jittered["points"][2], name="again", time=extra))
        res = wp.calibrate(filter_rig(), jittered, {"filt.clogging": [0, 0.95]})
        assert res.step == base.step
        assert res.parameters["filt.clogging"].value == pytest.approx(0.6, abs=0.02)


def test_request_errors_are_not_blamed_on_the_starting_values() -> None:
    """A step that makes the simulation too long is a bad request, reported up front; it
    was a CalibrationError telling the user to change the starting values."""
    data = {
        "points": [
            {"time": 0, "measured": {"tank.level": 3.5}},
            {"time": "20 min", "measured": {"tank.level": 2.0}},
        ]
    }
    with pytest.raises(wp.InvalidValueError, match=r"step=.*limit of 1,000,000") as info:
        wp.calibrate(filter_rig(), data, {"filt.clogging": [0, 0.95]}, step="0.001 s")
    assert not isinstance(info.value, wp.CalibrationError)


def test_identifiability_of_a_time_series_chooses_an_accurate_step() -> None:
    """identifiability() with timed points uses the same step choice as calibrate(); at
    the truth it predicts the standard error of a noise-free fit."""
    times = np.arange(0.0, 481.0, 60.0)
    fit = wp.calibrate(filter_rig(), continuous_series(times, None), {"filt.clogging": [0, 0.95]})
    points = [
        {"name": f"t{t:g}", "time": float(t)}
        | ({"settings": {"v.opening": 0.5}} if t == 240 else {})
        for t in times
    ]
    report = wp.identifiability(
        filter_rig(0.6),
        {"tank.level": LEVEL_SIGMA, "filt.pressure_drop": DP_SIGMA},
        {"filt.clogging": [0, 0.95]},
        points=points,
        candidates=[],
    )
    assert report.step is not None
    assert report.parameters["filt.clogging"].standard_error == pytest.approx(
        fit.parameters["filt.clogging"].standard_error, rel=0.02
    )


# ----------------------------------------------------------------------------------------
# settings a control would override
# ----------------------------------------------------------------------------------------
def test_settings_of_a_controlled_input_are_rejected() -> None:
    """A PI loop writes pump.speed. A steady point's speed setting was silently overridden
    (the fit then blamed the model), and a timed one raised a CalibrationError about the
    starting values. Both are measurement problems that name the point and the control."""
    steady = {
        "points": [
            {
                "name": f"d{y}",
                "settings": {"demand.opening": y, "pump.speed": sp},
                "measured": {"pump.volume_flow": {"value": 20.0, "sigma": 0.2}},
            }
            for y, sp in ((0.5, 0.6), (1.0, 0.9))
        ]
    }
    with pytest.raises(wp.MeasurementError, match=r"point 'd0.5' sets pump.speed, which control "):
        wp.MeasurementSet.from_dict(steady).validate(booster())
    timed = {
        "points": [
            {"time": 0, "measured": {"pump.volume_flow": 12.0}},
            {"time": 60, "settings": {"pump.speed": 0.6}, "measured": {"pump.volume_flow": 10.0}},
        ]
    }
    with pytest.raises(wp.MeasurementError, match=r"'zone_pressure' writes"):
        wp.calibrate(booster(), timed, {"pump.wear_head": [0, 0.5]})
    with pytest.raises(wp.MeasurementError, match=r"'zone_pressure' writes"):
        wp.identifiability(
            booster(),
            ["pump.volume_flow"],
            {"pump.wear_head": [0, 0.5]},
            points=[{"pump.speed": 1}],
        )


def test_steady_setting_of_a_settling_state_is_rejected() -> None:
    """A valve's position settles to its opening in a steady solve, so a steady point that
    sets it would be ignored."""
    ms = {
        "points": [{"name": "a", "settings": {"v.position": 0.5}, "measured": {"v.volume_flow": 1}}]
    }
    with pytest.raises(wp.MeasurementError, match=r"sets v.position, a state that a steady solve"):
        wp.MeasurementSet.from_dict(ms).validate(pump_circuit())


# ----------------------------------------------------------------------------------------
# measurement sets
# ----------------------------------------------------------------------------------------
def test_candidate_standard_error_carries_the_parameter_unit() -> None:
    s = pump_circuit()
    report = wp.identifiability(
        s,
        ["pump.volume_flow"],
        {"pipe.roughness": [0.001, 2], "v.kv": [5, 50]},
        points=[{"v.opening": y} for y in OPENINGS],
        candidates=["pump.outlet.p", "v.port_b.p"],
    )
    d = report.to_dict()["candidates"][0]
    assert d["sensor_unit"] == "bar" and d["sensor_reference"] == "gauge"
    assert d["parameter_unit"] == report.parameters[d["parameter"]].unit
    assert "unit" not in d


def test_default_names_of_timed_points_keep_every_digit(tmp_path: Path) -> None:
    ms = wp.MeasurementSet.from_dict(
        {
            "points": [
                {"time": 12345.25, "measured": {"pump.volume_flow": 18}},
                {"time": 12345.21, "measured": {"pump.volume_flow": 18}},
                {"time": 60, "measured": {"pump.volume_flow": 18}},
            ]
        }
    )
    assert [p.name for p in ms] == ["t=12345.25 s", "t=12345.21 s", "t=60 s"]
    path = tmp_path / "t.csv"
    path.write_text(
        "time,path,value,unit\n1000000,pump.volume_flow,18,m3/h\n1000001,pump.volume_flow,18,m3/h\n",
        encoding="utf-8",
    )
    assert [p.name for p in wp.load_measurements(path)] == ["t=1000000 s", "t=1000001 s"]


def test_duplicate_keys_and_settings_are_reported(tmp_path: Path) -> None:
    """YAML and JSON keep the last of two equal keys, and a CSV setting given twice was
    overwritten; each lost a value silently."""
    files = {
        "a.yaml": "points:\n  - name: a\n    measured:\n      pump.volume_flow: 20\n"
        "      pump.volume_flow: 25\n",
        "e.yaml": "points:\n  - {name: a, measured: {pump.volume_flow: 20, "
        "pump.volume_flow: 25}}\n",
        "b.json": '{"points": [{"name": "a", "measured": {"pump.volume_flow": 20, '
        '"pump.volume_flow": 25}}]}',
        "c.csv": "point,path,value,unit,sigma,kind\na,v.opening,1,,,setting\n"
        "a,v.opening,0.5,,,setting\na,pump.volume_flow,20,m3/h,,\n",
        "d.csv": "time,path,value,unit,kind\n60,v.opening,0.5,,setting\n"
        "60,v.opening,0.2,,setting\n60,pump.volume_flow,18,m3/h,\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        with pytest.raises(wp.MeasurementError, match=r"given twice|twice \(rows 2 and 3\)"):
            wp.load_measurements(path)
    # A YAML merge key may be overridden: that is not a duplicate.
    merged = tmp_path / "merged.yaml"
    merged.write_text(
        "points:\n  - &a {name: a, measured: {pump.volume_flow: 20}}\n  - {<<: *a, name: b}\n",
        encoding="utf-8",
    )
    assert [p.name for p in wp.load_measurements(merged)] == ["a", "b"]


def test_directly_built_sets_are_validated() -> None:
    s = pump_circuit()

    def point(name: str, sigma: float) -> wp.MeasurementPoint:
        value = wp.MeasuredValue(16.7, "m3/h", sigma=sigma)
        return wp.MeasurementPoint(name, {"pump.volume_flow": value}, {"v.opening": 1.0})

    for sigma in (0.0, float("nan"), -0.2):
        ms = wp.MeasurementSet([point("a", sigma), point("b", 0.2)])
        with pytest.raises(wp.MeasurementError, match=r"sigma must be positive and finite"):
            ms.validate(s)
    with pytest.raises(wp.MeasurementError, match=r"'a' is used 2 times"):
        wp.MeasurementSet([point("a", 0.2), point("a", 0.2)]).validate(s)
    with pytest.raises(wp.MeasurementError, match=r"time must be finite"):
        wp.MeasurementSet([wp.MeasurementPoint("a", {}, {}, math.inf)]).validate(s)


def test_temperature_difference_units() -> None:
    """A sigma is a difference, and so is a temperature rise: delta_degC is right for both.
    An absolute temperature in delta_degC is refused with a WorldpartsError."""
    s = wp.System("h")
    s.add("src", "supply")
    s.add("h", "instantaneous_water_heater")
    s.add("d", "drain")
    s.connect("src.port", "h.inlet")
    s.connect("h.outlet", "d.port")
    ms = wp.MeasurementSet.from_dict(
        {
            "points": [
                {
                    "measured": {
                        "h.outlet.T": {"value": "18 degC", "sigma": "0.1 delta_degC"},
                        "h.temperature_rise": "5 delta_degC",
                    }
                }
            ]
        }
    )
    values = {v.path: (v.value, v.sigma) for v in ms.resolve(s).values}
    assert values["h.outlet.T"] == pytest.approx((18.0, 0.1))
    assert values["h.temperature_rise"][0] == pytest.approx(5.0)
    bad = wp.MeasurementSet.from_dict({"points": [{"measured": {"h.outlet.T": "18 delta_degC"}}]})
    with pytest.raises(wp.MeasurementError, match=r"temperature difference, but the variable"):
        bad.resolve(s)


def test_a_single_candidate_path_is_one_candidate() -> None:
    report = wp.identifiability(
        pump_circuit(), "pump.outlet.p", WEAR_BOUNDS, candidates="pump.shaft_power"
    )
    assert [c.sensor for c in report.candidates] == ["pump.shaft_power"]


def test_infinite_times_and_vanishing_sigmas_are_measurement_errors() -> None:
    with pytest.raises(wp.MeasurementError, match=r"time must be finite"):
        wp.calibrate(
            pump_circuit(),
            {"points": [{"time": "1e400 s", "measured": {"pump.volume_flow": 20}}]},
            {"pump.wear_head": [0, 0.5]},
        )
    tiny = {
        "points": [
            {
                "settings": {"v.opening": y},
                "measured": {"pump.volume_flow": {"value": 20, "sigma": 1e-160}},
            }
            for y in (1.0, 0.5)
        ]
    }
    with pytest.raises(wp.MeasurementError, match=r"No instrument is that accurate"):
        wp.calibrate(pump_circuit(), tiny, {"pump.wear_head": [0, 0.5]})
