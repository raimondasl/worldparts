"""Fault modes in manifests and diagnosis (design 14.3).

The diagnosis tests use synthetic data: a treatment skid (supply, suction pipe, pump,
throttling valve, pipe, media filter, UV reactor, riser, drain) put into a known fault, its
sensors read with Gaussian noise from fixed seeds and the stated uncertainty as sigma, so
every run is reproducible. A diagnosis is right when the true fault ranks first with its
magnitude within two standard errors, a healthy plant is not given a confident fault, and
a case that the sensors cannot resolve is flagged ambiguous until a sensor that separates
the explanations is added.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import numpy as np
import pytest

import worldparts as wp
from worldparts.diagnosis import AMBIGUITY_AIC, BASELINE, CONCLUSIONS

#: The skid's sensors and their standard uncertainties (display units).
SIGMA = {
    "pump.volume_flow": 0.2,  # m3/h
    "pump.inlet.p": 0.01,  # bar
    "pump.outlet.p": 0.01,
    "filter.inlet.p": 0.01,
    "filter.outlet.p": 0.01,
    "pump.shaft_power": 0.02,  # kW
    "uv.dose": 0.5,  # mJ/cm2
    "pump.speed_rpm": 5.0,  # rpm (a tachometer)
}
#: A flow meter and pressure gauges, without power or speed readings.
FLOW_AND_PRESSURES = {
    p: SIGMA[p] for p in ("pump.volume_flow", "pump.outlet.p", "filter.inlet.p", "filter.outlet.p")
}
#: The value of each varied path without its fault, in the skid (relative faults follow
#: the skid's own values: an 80 % valve opening, a 0.045 mm roughness).
HEALTHY = {
    "pump.wear_head": 0.0,
    "pump.wear_efficiency": 0.0,
    "pump.speed": 1.0,
    "filter.clogging": 0.0,
    "valve.opening": 0.8,
    "uv.lamp_output": 1.0,
    "riser.roughness": 0.045,
}


# ----------------------------------------------------------------------------------------
# the skid and synthetic readings
# ----------------------------------------------------------------------------------------
def skid() -> wp.System:
    """Supply (0.3 bar) -> 3 m suction -> pump -> valve (Kv 40, 80 % open) -> 10 m line ->
    sand filter -> UV reactor -> 40 m riser (+16 m) -> drain: about 22 m3/h."""
    s = wp.System("treatment-skid")
    s.add("src", "supply", pressure=0.3)
    s.add("suction", "pipe", length=3, diameter=80, roughness=0.045, minor_loss=0.5)
    s.add("pump", "centrifugal_pump")
    s.add("valve", "valve", kv=40, opening=0.8)
    s.add("line", "pipe", length=10, diameter=65, roughness=0.045)
    s.add(
        "filter", "media_filter", rated_flow=20, clean_pressure_drop=0.5, change_pressure_drop=1.5
    )
    s.add("uv", "uv_reactor")
    s.add(
        "riser",
        "pipe",
        length=40,
        diameter=65,
        roughness=0.045,
        height_difference=16,
        minor_loss=1,
    )
    s.add("out", "drain")
    for a, b in (
        ("src.port", "suction.port_a"),
        ("suction.port_b", "pump.inlet"),
        ("pump.outlet", "valve.port_a"),
        ("valve.port_b", "line.port_a"),
        ("line.port_b", "filter.inlet"),
        ("filter.outlet", "uv.inlet"),
        ("uv.outlet", "riser.port_a"),
        ("riser.port_b", "out.port"),
    ):
        s.connect(a, b)
    return s


def readings(
    truth: dict[str, float],
    sigma: dict[str, float] = SIGMA,
    seed: int = 1,
    leak: tuple[str, float] | None = None,
) -> dict[str, Any]:
    """One operating point of the skid at ``truth`` (and an optional leak of the given
    diameter in mm at a port), each sensor read with noise of its sigma."""
    s = skid()
    if leak is not None:
        s.add("hole", "leak", diameter=leak[1])
        s.connect("hole.port", leak[0])
    s.set_values(truth)
    r = s.solve()
    rng = np.random.default_rng(seed)
    measured = {
        path: {"value": r[path] + rng.normal(0.0, sg), "sigma": sg} for path, sg in sigma.items()
    }
    return {"points": [{"name": "p1", "measured": measured}]}


def document(s: wp.System) -> str:
    return json.dumps(s.to_dict(), sort_keys=True)


# ----------------------------------------------------------------------------------------
# fault modes in manifests
# ----------------------------------------------------------------------------------------
V03_FAULTS = {
    "centrifugal_pump": {
        "worn_impeller": ("wear_head", 0.0, 0.5, 0.0, False),
        "efficiency_loss": ("wear_efficiency", 0.0, 0.5, 0.0, False),
        "running_slow": ("speed", 0.5, 1.0, 1.0, True),
    },
    "media_filter": {"clogged": ("clogging", 0.0, 0.99, 0.0, False)},
    "valve": {"partly_closed": ("opening", 0.0, 1.0, 1.0, True)},
    "uv_reactor": {"lamp_degraded": ("lamp_output", 0.0, 1.0, 1.0, False)},
    "pipe": {"scaled": ("roughness", 1.0, 20.0, 1.0, True)},
}


def test_catalogue_declares_the_v03_fault_modes() -> None:
    catalog = wp.default_catalog()
    for alias, faults in V03_FAULTS.items():
        m = catalog.get(alias)
        got = {n: (f.path, f.lower, f.upper, f.healthy, f.relative) for n, f in m.faults.items()}
        assert got == faults, alias
        for f in m.faults.values():
            spec = m.parameters.get(f.path) or m.inputs.get(f.path)
            assert spec is not None and spec.type == "number"
            assert f.description
    # Components without a fault mode declare none.
    assert catalog.get("supply").faults == {}
    # A relative fault's range follows the value in the system being diagnosed.
    scaled = catalog.get("pipe").faults["scaled"]
    assert scaled.resolve(0.045) == pytest.approx((0.045, 0.9, 0.045))
    assert catalog.get("valve").faults["partly_closed"].to_dict()["vary"]["relative"] is True


def _valve_with(faults: Any) -> dict[str, Any]:
    data = copy.deepcopy(wp.default_catalog().get("valve").data)
    data["faults"] = faults
    return data


def _fault(**changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "stuck",
        "description": "Stuck partly closed.",
        "vary": {"path": "opening", "lower": 0.0, "upper": 1.0},
        "healthy": 1.0,
    }
    for key, value in changes.items():
        if key in ("path", "lower", "upper", "relative"):
            entry["vary"][key] = value
        else:
            entry[key] = value
    return entry


@pytest.mark.parametrize(
    ("faults", "fragment"),
    [
        ([_fault(path="nothing")], "'nothing' is not a variable of this component"),
        ([_fault(path="position")], "'position' is a state"),
        ([_fault(path="characteristic")], "'characteristic' is a string parameter"),
        ([_fault(path="volume_flow")], "'volume_flow' is an observable"),
        ([_fault(lower=1.0, upper=0.5)], "vary.lower (1) must be below vary.upper (0.5)"),
        ([_fault(upper=2.0, healthy=1.0)], "must lie within the hard limits of 'opening'"),
        ([_fault(healthy=1.5)], "healthy (1.5) must lie within the range [0, 1]"),
        ([_fault(relative=True, lower=-1.0)], "relative bounds are non-negative multiples"),
        ([_fault(relative=True, upper=0.5, healthy=1.0)], "a multiple of the current value"),
        ([_fault(), _fault()], "faults: duplicate names ['stuck']"),
        ([_fault(name="opening")], "the name is already used by an input"),
        ([_fault(name="port_a")], "the name is already used by a port"),
    ],
)
def test_manifest_rejects_bad_faults(faults: list[dict[str, Any]], fragment: str) -> None:
    with pytest.raises(wp.ManifestError) as info:
        wp.Manifest.from_dict(_valve_with(faults))
    assert fragment in str(info.value)
    assert wp.validate_manifest_data(_valve_with(faults))


@pytest.mark.parametrize(
    ("fault", "fragment"),
    [
        ({"name": "stuck", "description": "x", "healthy": 1.0}, "'vary' is a required property"),
        ({"name": "stuck", "description": "x", "vary": {"path": "opening"}, "healthy": 1}, "lower"),
        (_fault(extra=1), "Additional properties are not allowed"),
        (_fault(lower="0"), "must be of type number"),
        (_fault(name="Stuck"), "does not match"),
    ],
)
def test_schema_rejects_malformed_faults(fault: dict[str, Any], fragment: str) -> None:
    with pytest.raises(wp.ManifestError, match="does not match the schema") as info:
        wp.Manifest.from_dict(_valve_with([fault]))
    assert fragment in str(info.value)


def test_a_valid_fault_loads() -> None:
    m = wp.Manifest.from_dict(_valve_with([_fault(relative=True, lower=0.2, upper=1.0)]))
    f = m.faults["stuck"]
    assert (f.path, f.lower, f.upper, f.healthy, f.relative) == ("opening", 0.2, 1.0, 1.0, True)
    assert f.resolve(0.5) == pytest.approx((0.1, 0.5, 0.5))


# ----------------------------------------------------------------------------------------
# diagnosis
# ----------------------------------------------------------------------------------------
SINGLE_FAULTS = [
    ("pump.worn_impeller", {"pump.wear_head": 0.15}, "pump.wear_head", 0.15),
    ("pump.efficiency_loss", {"pump.wear_efficiency": 0.12}, "pump.wear_efficiency", 0.12),
    ("pump.running_slow", {"pump.speed": 0.92}, "pump.speed", 0.92),
    ("filter.clogged", {"filter.clogging": 0.5}, "filter.clogging", 0.5),
    ("valve.partly_closed", {"valve.opening": 0.5}, "valve.opening", 0.5),
    ("uv.lamp_degraded", {"uv.lamp_output": 0.7}, "uv.lamp_output", 0.7),
    ("riser.scaled", {"riser.roughness": 0.6}, "riser.roughness", 0.6),
]


@pytest.mark.parametrize(("ref", "truth", "path", "value"), SINGLE_FAULTS)
def test_the_true_fault_ranks_first(
    ref: str, truth: dict[str, float], path: str, value: float
) -> None:
    s = skid()
    before = document(s)
    result = wp.diagnose(s, readings(truth))
    assert document(s) == before  # nothing applied
    assert result.best == ref and result.conclusion == "fault", result.notes
    assert result.hypotheses[0].rank == 1 and result.ranking[0] == ref
    assert result.best_hypothesis.supported and result.best_hypothesis.weight is not None
    aics = [h.aic for h in result.hypotheses]
    assert aics == sorted(aics) and result.hypotheses[0].delta_aic == 0
    est = result[ref].estimates[ref]
    assert est.path == path and est.verdict == "identifiable"
    assert est.standard_error is not None
    assert abs(est.value - value) <= 2 * est.standard_error, (est.value, est.standard_error)
    assert est.lower <= est.value <= est.upper
    assert result[BASELINE].delta_aic > 10
    assert result.runner_up is not None and not result.ambiguous
    assert result[result.runner_up].delta_aic >= AMBIGUITY_AIC
    assert result.plausible == [ref]
    assert result.false_alarm is not None and result.false_alarm < 1e-6
    assert not result.unexplained and result.skipped == {}
    # Every default hypothesis was fitted: the fault modes of the pump (3), valve, filter,
    # UV reactor and the three pipes, plus the baseline.
    assert len(result.hypotheses) == 10
    assert result.notes[0].startswith(f"Best explanation: {ref}")
    # The measurements that tell the best from the runner-up are listed, largest first.
    seps = [abs(d.separation) for d in result.discriminating]
    assert seps and seps == sorted(seps, reverse=True) and seps[0] >= 1
    assert est.healthy == pytest.approx(HEALTHY[path])


def test_a_relative_fault_reports_its_factor() -> None:
    result = wp.diagnose(skid(), readings({"valve.opening": 0.5}), ["valve.partly_closed"])
    est = result["valve.partly_closed"].estimates["valve.partly_closed"]
    assert est.relative and est.baseline == 0.8 and est.healthy == 0.8
    assert (est.lower, est.upper) == (0.0, 0.8)
    assert est.factor == pytest.approx(est.value / 0.8)
    assert est.factor == pytest.approx(0.625, abs=3 * est.standard_error / 0.8)
    assert "times the value 0.8 of the system as given" in result.notes[0]


def test_a_healthy_plant_ranks_the_baseline_first() -> None:
    s = skid()
    result = wp.diagnose(s, readings({}, seed=1))
    assert result.best == BASELINE and result.conclusion == "no_fault"
    assert not result.ambiguous and result.false_alarm is None
    # Every fault fits no better than the 2 AIC units its parameter costs: it adds a
    # parameter to the baseline without support, so it is not a separate explanation.
    assert all(not h.supported and h.weight is None for h in result.hypotheses[1:])
    assert result.runner_up is None and result.discriminating == []
    assert result.plausible == [BASELINE] and result[BASELINE].weight == 1.0
    assert result.notes[0].startswith("No fault is detected")
    assert "The closest single fault is" in result.notes[0]


def test_a_healthy_plant_is_never_given_a_confident_fault() -> None:
    """With nine fault hypotheses, noise alone sometimes lets one beat the baseline. Such a
    fault is then flagged ambiguous (the baseline within 2 AIC units) or its false-alarm
    bound says that the evidence is weak; it is never a plain ``fault``."""
    s = skid()
    conclusions = []
    for seed in range(12):
        result = wp.diagnose(s, readings({}, seed=seed))
        conclusions.append(result.conclusion)
        assert result.conclusion in ("no_fault", "ambiguous", "weak_evidence")
        if result.conclusion == "ambiguous":
            assert BASELINE in result.plausible
        if result.conclusion == "weak_evidence":
            assert result.false_alarm is not None and result.false_alarm > 0.05
            assert "The evidence for a fault is weak" in " ".join(result.notes)
    assert conclusions.count("no_fault") >= 8


def test_ambiguous_until_a_discriminating_sensor_is_added() -> None:
    s = skid()
    worn = {"pump.wear_head": 0.15}
    # A flow meter alone: every fault that throttles the flow fits it exactly.
    flow_only = wp.diagnose(s, readings(worn, {"pump.volume_flow": 0.2}))
    assert flow_only.ambiguous and flow_only.conclusion == "ambiguous"
    assert {"pump.worn_impeller", "pump.running_slow"} <= set(flow_only.plausible)
    assert len(flow_only.plausible) >= 3
    assert "Ambiguous" in " ".join(flow_only.notes)
    # Pressures rule out the valve, the filter and the pipes, but a worn impeller and a
    # slow pump reach the same operating point on the same system curve.
    pressures = wp.diagnose(s, readings(worn, FLOW_AND_PRESSURES))
    assert pressures.ambiguous
    assert set(pressures.plausible) == {"pump.worn_impeller", "pump.running_slow"}
    assert abs(pressures[pressures.runner_up].delta_aic) < AMBIGUITY_AIC
    weights = [pressures[n].weight for n in pressures.plausible]
    assert sum(weights) == pytest.approx(1.0, abs=1e-6)
    assert pressures.discriminating == []  # no measured value tells them apart
    # ... and the sensor that would: a tachometer.
    assert pressures.suggested_sensors[0].path == "pump.speed_rpm"
    assert pressures.suggested_sensors[0].separation > 3
    assert all(
        sug.path not in ("pump.bep_flow", "pump.npsh_required")  # model quantities
        for sug in pressures.suggested_sensors
    )
    assert "measure pump.speed_rpm" in " ".join(pressures.notes)
    # Adding it resolves the diagnosis.
    resolved = wp.diagnose(s, readings(worn, {**FLOW_AND_PRESSURES, "pump.speed_rpm": 5.0}))
    assert resolved.best == "pump.worn_impeller" and resolved.conclusion == "fault"
    assert resolved.runner_up == "pump.running_slow" and not resolved.ambiguous
    assert resolved[resolved.runner_up].delta_aic > 100
    top = resolved.discriminating[0]
    assert top.path == "pump.speed_rpm" and top.favours == "best"
    assert top.delta_chi_square > 100
    assert resolved.suggested_sensors == []


def test_leak_at_a_junction() -> None:
    s = skid()
    before = document(s)
    data = readings({}, leak=("filter.outlet", 8.0))
    result = wp.diagnose(s, data, include_leaks=True)
    assert document(s) == before and "leak_at_filter_outlet" not in s.components
    assert result.best == "leak_at:filter.outlet", result.notes
    est = result.best_hypothesis.estimates["leak_at:filter.outlet"]
    assert est.path == "leak_at_filter_outlet.diameter" and est.unit == "mm"
    assert est.standard_error is not None
    assert abs(est.value - 8.0) <= 2 * est.standard_error
    assert est.baseline is None and est.healthy == pytest.approx(0.1)
    # The range ends at the bore of the pipes that meet at the junction (the UV reactor
    # and the filter have no bore; no pipe meets there, so the leak's own hard limit).
    assert est.upper == pytest.approx(1000)
    # The leak's outflow at the fitted size, against the truth.
    truth = skid()
    truth.add("hole", "leak", diameter=8.0)
    truth.connect("hole.port", "filter.outlet")
    true_flow = truth.solve().get("hole.volume_flow", unit="L/min")
    assert est.leak_flow is not None
    assert est.leak_flow["p1"] == pytest.approx(true_flow, rel=0.15)
    assert "L/min at 'p1'" in result.notes[0]
    # Every junction was tried (8 of them) besides the component faults and the baseline.
    leaks = [h for h in result.hypotheses if h.name.startswith("leak_at:")]
    assert len(leaks) == 8 and len(result.hypotheses) == 18
    # A leak at a junction whose pipes have a bore is bounded by it.
    at_riser = result["leak_at:riser.port_a"].estimates["leak_at:riser.port_a"]
    assert at_riser.upper == pytest.approx(65.0)
    # A junction can be named by any of its ports.
    alias = wp.diagnose(s, data, ["leak_at:uv.inlet", "pump.worn_impeller"])
    assert alias.best == "leak_at:uv.inlet"
    assert alias.best_hypothesis.estimates["leak_at:uv.inlet"].value == pytest.approx(
        est.value, rel=1e-4
    )
    assert document(s) == before


def test_two_faults_need_max_faults_2() -> None:
    s = skid()
    data = readings({"pump.wear_head": 0.15, "filter.clogging": 0.5})
    single = wp.diagnose(s, data)
    assert single.unexplained and single.conclusion == "unexplained"
    assert "max_faults=2" in " ".join(single.notes)
    before = document(s)
    pair = wp.diagnose(s, data, max_faults=2)
    assert document(s) == before
    assert pair.best == "pump.worn_impeller + filter.clogged", pair.notes
    assert pair.conclusion == "fault" and not pair.unexplained
    best = pair.best_hypothesis
    assert best.supported and all(h.rank == i + 1 for i, h in enumerate(pair.hypotheses))
    assert best.faults == ("pump.worn_impeller", "filter.clogged") and best.parameters == 2
    for ref, value in (("pump.worn_impeller", 0.15), ("filter.clogged", 0.5)):
        est = best.estimates[ref]
        assert est.standard_error is not None
        assert abs(est.value - value) <= 2 * est.standard_error
    # 9 singles, 36 pairs and the baseline.
    assert len(pair.hypotheses) == 46
    # A pair never fits worse than the better of its singles (it starts from that fit),
    # to within the solver's tolerance.
    for h in pair.hypotheses:
        if len(h.faults) == 2:
            better = min(pair[f].chi_square for f in h.faults)
            assert h.chi_square <= better * (1 + 1e-8) + 1e-8
    # A pair that adds a fault to a single one without beating it is not an explanation.
    for h in pair.hypotheses:
        if len(h.faults) == 2 and not h.supported:
            assert any(pair[f].aic <= h.aic + 1e-6 for f in h.faults) or (
                pair[BASELINE].aic <= h.aic
            )


def test_explicit_hypotheses() -> None:
    s = skid()
    data = readings({"pump.wear_head": 0.15})
    result = wp.diagnose(
        s,
        data,
        ["pump.worn_impeller", "pump.running_slow", "pump.worn_impeller + valve.partly_closed"],
    )
    assert set(result.ranking) == {
        BASELINE,
        "pump.worn_impeller",
        "pump.running_slow",
        "pump.worn_impeller + valve.partly_closed",
    }
    assert result.best == "pump.worn_impeller"
    combo = result["pump.worn_impeller + valve.partly_closed"]
    assert not combo.supported  # the extra fault is not supported
    # A combination may also be a list; max_faults=2 adds the pairs of the singles.
    listed = wp.diagnose(
        s, data, [["pump.worn_impeller", "filter.clogged"], "uv.lamp_degraded"], max_faults=2
    )
    assert set(listed.ranking) == {
        BASELINE,
        "pump.worn_impeller + filter.clogged",
        "uv.lamp_degraded",
    }
    single = wp.diagnose(s, data, "pump.worn_impeller")
    assert single.ranking == ["pump.worn_impeller", BASELINE]


@pytest.mark.parametrize(
    ("hypotheses", "kwargs", "error", "fragment"),
    [
        (["pump.nothing"], {}, wp.UnknownVariableError, "Fault modes of pump: worn_impeller"),
        (["pumpp.worn_impeller"], {}, wp.UnknownVariableError, "Did you mean 'pump.worn_impeller'"),
        (["src.dry"], {}, wp.UnknownVariableError, "src (supply) declares no fault modes"),
        (["leak_at:pump.nothing"], {}, wp.UnknownVariableError, "is not a port of the system"),
        (["leak_at:nowhere"], {}, wp.UnknownVariableError, "a leak is tested at a junction"),
        (["pump.worn_impeller", "pump.worn_impeller"], {}, wp.InvalidValueError, "listed twice"),
        (["pump.worn_impeller + pump.worn_impeller"], {}, wp.InvalidValueError, "repeats"),
        (
            ["leak_at:pump.outlet", "leak_at:valve.port_a"],
            {},
            wp.InvalidValueError,
            "same junction",
        ),
        ([], {}, wp.InvalidValueError, "hypotheses is empty"),
        ({"pump.worn_impeller": 1}, {}, wp.InvalidValueError, "must be a list"),
        ([3], {}, wp.InvalidValueError, "expected a fault reference"),
        (None, {"max_faults": 3}, wp.InvalidValueError, "max_faults must be 1 or 2"),
        (None, {"max_faults": 0}, wp.InvalidValueError, "max_faults must be 1 or 2"),
        (None, {"max_faults": True}, wp.InvalidValueError, "max_faults must be 1 or 2"),
    ],
)
def test_bad_hypotheses_are_errors(
    hypotheses: Any, kwargs: dict[str, Any], error: type[Exception], fragment: str
) -> None:
    s = skid()
    with pytest.raises(error) as info:
        wp.diagnose(s, readings({}, {"pump.volume_flow": 0.2}), hypotheses, **kwargs)
    assert fragment in str(info.value)


def test_faults_that_cannot_be_fitted_are_skipped_or_errors() -> None:
    s = skid()
    # A control that writes the pump speed would override a slow pump.
    s.add_control(
        "duty",
        "pi",
        measure="pump.outlet.p",
        setpoint="3 bar",
        actuate="pump.speed",
        gain=0.1,
        integral_time="10 s",
        output_min=0.5,
        output_max=1.2,
    )
    # A smooth pipe cannot be scaled by a factor of its roughness.
    s.set("line.roughness", 0.0)
    data = {
        "points": [
            {
                "name": "p1",
                "settings": {"valve.opening": 0.8},
                "measured": {"pump.volume_flow": {"value": 20.0, "sigma": 0.2}},
            }
        ]
    }
    result = wp.diagnose(s, data)
    assert "control 'duty' writes pump.speed" in result.skipped["pump.running_slow"]
    assert "set valve.opening" in result.skipped["valve.partly_closed"]
    assert "its range is empty" in result.skipped["line.scaled"]
    assert "Not tested:" in result.notes[-1]
    for ref, fragment in (
        ("pump.running_slow", "control 'duty' writes pump.speed"),
        ("valve.partly_closed", "the measurement points ('p1') set valve.opening"),
        ("line.scaled", "its range is empty"),
    ):
        with pytest.raises(wp.InvalidValueError, match="cannot be tested") as info:
            wp.diagnose(s, data, [ref])
        assert fragment in str(info.value)


def test_measurement_errors_come_first() -> None:
    s = skid()
    with pytest.raises(wp.MeasurementError, match="no measured values"):
        wp.diagnose(s, {"points": [{"name": "p1", "settings": {"valve.opening": 0.5}}]})
    with pytest.raises(wp.MeasurementError, match="not a variable"):
        wp.diagnose(s, {"points": [{"name": "p1", "measured": {"pump.flow": 20}}]})


def test_result_is_json_safe() -> None:
    result = wp.diagnose(skid(), readings({"pump.wear_head": 0.15}, FLOW_AND_PRESSURES))
    d = result.to_dict()
    text = json.dumps(d, allow_nan=False)
    assert d["conclusion"] == "ambiguous" and d["conclusion"] in CONCLUSIONS
    assert d["best"] == result.best and d["hypotheses_total"] == len(result.hypotheses)
    best = d["hypotheses"][0]
    assert best["estimates"]["pump.worn_impeller"]["unit"] == "1"
    assert best["residuals"][0]["point"] == "p1"
    assert d["suggested_sensors"][0]["path"] == "pump.speed_rpm"
    short = result.to_dict(max_hypotheses=1, residuals=False)
    assert [h["name"] for h in short["hypotheses"]] == [result.best, result.runner_up]
    assert "residuals" not in short["hypotheses"][0]
    assert "NaN" not in text
    with pytest.raises(wp.UnknownVariableError, match="not an evaluated hypothesis"):
        result["pump.nothing"]


def test_time_series_diagnosis() -> None:
    """Timed points go through calibration's simulation (with its step control)."""

    def fill() -> wp.System:
        s = wp.System("fill")
        s.add("mains", "supply", pressure=1.0)
        s.add("feed", "pipe", length=20, diameter=25, roughness=0.045)
        s.add("v", "valve", kv=4.0)
        s.add("t", "tank", diameter=1.0, height=3.0, initial_level=0.5)
        s.connect("mains.port", "feed.port_a")
        s.connect("feed.port_b", "v.port_a")
        s.connect("v.port_b", "t.inlet")
        return s

    truth = fill()
    truth.set("v.opening", 0.6)
    sim = truth.simulate(duration=300, step=0.25, variables=["t.level"])
    rng = np.random.default_rng(3)
    points = []
    for t in (60, 120, 180, 240, 300):
        level = sim.series["t.level"][sim.time.index(float(t))]
        points.append(
            {
                "time": t,
                "measured": {"t.level": {"value": level + rng.normal(0, 0.005), "sigma": 0.005}},
            }
        )
    s = fill()
    before = document(s)
    result = wp.diagnose(s, {"points": points})
    assert document(s) == before
    assert result.best == "v.partly_closed" and result.conclusion == "fault"
    est = result.best_hypothesis.estimates["v.partly_closed"]
    assert est.standard_error is not None and abs(est.value - 0.6) <= 2 * est.standard_error
    assert result.step is not None


def test_diagnosis_is_fast() -> None:
    """Nine components, nine fault modes: well under a second on a laptop; the bound
    leaves room for slow CI machines."""
    s = skid()
    data = readings({"filter.clogging": 0.5})
    started = time.perf_counter()
    result = wp.diagnose(s, data)
    elapsed = time.perf_counter() - started
    assert result.best == "filter.clogged"
    assert elapsed < 10 and result.elapsed <= elapsed
    assert result.evaluations < 1000
