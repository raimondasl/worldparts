"""Tests of the operations-benchmark grader (benchmarks/operations/harness/grading.py).

Synthetic task metadata and truth only: every answer kind, the answer-format tolerances,
cannot_determine spellings, malformed intervals, bare numbers, and every row of the
diagnosis table of PREREGISTRATION.md section 6.6 (plus the two rows it leaves out).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.operations.harness import grading as G  # noqa: E402
from benchmarks.operations.harness.bundles import (  # noqa: E402
    BundleError,
    task_from_json,
    truth_problems,
)

VOCAB: dict[str, Any] = {
    "none": {},
    "worn_pump": {"unit": "pp", "m_min": 5, "range": [0, 40]},
    "pump_running_slow": {"unit": "%", "m_min": 3, "range": [0, 30]},
    "low_suction_level": {"unit": "m", "m_min": 0.5, "range": [0, 5]},
    "throttled_valve": {"unit": "%", "m_min": 10, "range": [0, 90]},
    "leak": {"unit": "m3/h", "m_min": 1, "range": [0, 20]},
    "pressure_sensor_fault": {"unit": "bar", "m_min": 0.05, "range": [-1, 1]},
    "reverse_rotation": {"unit": "1", "m_min": 1, "range": [0, 1]},
}
RESOLVING = ["suction_pressure", "sump_level", "motor_power", "downstream_flow"]

TASK_JSON: dict[str, Any] = {
    "task_id": "ops-f3-001",
    "set": "dev",
    "family": "F3",
    "generator": "G-ind",
    "cell": "F3/G-ind",
    "stratum": "single",
    "keys": {
        "head_deficit_bep_pp": {
            "kind": "estimate_or_undetermined",
            "unit": "pp",
            "range": [0, 30],
        },
        "wire_to_water_efficiency_pct": {
            "kind": "estimate_or_undetermined",
            "unit": "%",
            "range": [20, 90],
        },
        "extra_energy_mwh_per_yr": {"kind": "estimate", "unit": "MWh/yr", "range": [0, 500]},
        "deterioration_real": {"kind": "boolean"},
        "first_to_trigger": {"kind": "choice", "options": ["F-1", "F-2", "F-3"]},
        "determinable_set": {
            "kind": "set",
            "options": ["pump_head_deficit", "valve_kv", "filter_clean_dp"],
        },
        "diagnosis": {
            "kind": "diagnosis",
            "vocabulary": VOCAB,
            "excluded_from_candidates": {"uv_lamp_degraded": "no UV reactor in this plant"},
            "resolving_options": RESOLVING,
        },
    },
}

SINGLE = {
    "kind": "diagnosis",
    "label": "identified",
    "faults": ["worn_pump"],
    "magnitudes": {"worn_pump": {"value": 8.0, "tol": 1.0}},
}
PAIR = {
    "kind": "diagnosis",
    "label": "identified",
    "faults": ["worn_pump", "throttled_valve"],
    "magnitudes": {
        "worn_pump": {"value": 8.0, "tol": 1.0},
        "throttled_valve": {"value": 30.0, "tol": 5.0},
    },
}
AMBIGUOUS = {
    "kind": "diagnosis",
    "label": "ambiguous",
    "faults": ["low_suction_level", "worn_pump", "pump_running_slow"],
    "magnitudes": {},
    "resolving": ["suction_pressure", "sump_level"],
}
NONE = {"kind": "diagnosis", "label": "no_fault", "faults": [], "magnitudes": {}}
SENSOR = {
    "kind": "diagnosis",
    "label": "identified",
    "faults": ["pressure_sensor_fault"],
    "magnitudes": {"pressure_sensor_fault": {"value": 0.12, "tol": 0.025}},
}


def make_truth(diagnosis: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    keys = {
        "head_deficit_bep_pp": {
            "kind": "estimate_or_undetermined",
            "determinable": True,
            "value": 8.4,
            "tol": 1.0,
            "range": [0, 30],
        },
        "wire_to_water_efficiency_pct": {
            "kind": "estimate_or_undetermined",
            "determinable": False,
            "value": 61.2,
            "range": [20, 90],
        },
        "extra_energy_mwh_per_yr": {"kind": "estimate", "value": 37.5, "tol": 3.75},
        "deterioration_real": {"kind": "boolean", "value": True},
        "first_to_trigger": {"kind": "choice", "value": "F-2"},
        "determinable_set": {"kind": "set", "value": ["valve_kv", "pump_head_deficit"]},
        "diagnosis": copy.deepcopy(diagnosis or SINGLE),
    }
    for k, v in over.items():
        keys[k] = {**keys[k], **v}
    return {
        "task_id": "ops-f3-001",
        "keys": keys,
        "realisations": {
            "r1": {
                "oracle_pass": True,
                "reference_pass": {"R-a": True, "R-b": True, "R2": None},
                "naive_pass": False,
            }
        },
    }


GOOD: dict[str, Any] = {
    "head_deficit_bep_pp": {"value": 8.9, "lo90": 7.5, "hi90": 10.2},
    "wire_to_water_efficiency_pct": "cannot_determine",
    "extra_energy_mwh_per_yr": {"value": 36.0, "lo90": 30.0, "hi90": 45.0},
    "deterioration_real": True,
    "first_to_trigger": "F-2",
    "determinable_set": ["pump_head_deficit", "valve_kv"],
    "diagnosis": {
        "verdict": "identified",
        "faults": ["worn_pump"],
        "magnitudes": {"worn_pump": {"value": 8.2, "lo90": 6.9, "hi90": 9.4}},
        "resolving_measurement": None,
    },
}


@pytest.fixture(scope="module")
def task() -> Any:
    return task_from_json(TASK_JSON, Path("ops-f3-001"), "dev", (1, 2, 3))


def reply(values: dict[str, Any], fence: str = "json") -> str:
    return f"Summary of the work.\n\n```{fence}\n" + json.dumps(values, indent=2) + "\n```\n"


def grade(task: Any, answer: dict[str, Any], truth: dict[str, Any] | None = None) -> Any:
    return G.grade_answer(task, truth or make_truth(), reply(answer))


def key(task: Any, name: str, given: Any, truth: dict[str, Any] | None = None) -> G.KeyResult:
    answer = dict(GOOD, **{name: given})
    return grade(task, answer, truth).keys[name]


# ----------------------------------------------------------------------------------------
# the whole answer
# ----------------------------------------------------------------------------------------
def test_truth_fixture_is_valid(task: Any) -> None:
    for d in (SINGLE, PAIR, AMBIGUOUS, NONE, SENSOR):
        assert truth_problems(task, make_truth(d)) == []


def test_a_fully_correct_answer_passes(task: Any) -> None:
    g = grade(task, GOOD)
    assert g.passed and g.n_passed == 7 and g.parse_error is None
    assert g.cw_category == G.PASS and not g.confident_wrong and not g.under_commitment
    assert g.abstentions() == (["wire_to_water_efficiency_pct"], [])
    ints = g.intervals()
    # head deficit and energy intervals, and the magnitude interval: all hold the truth
    assert ints["n"] == 3 and ints["covered"] == 3


@pytest.mark.parametrize(
    ("text", "issue"),
    [
        (reply(GOOD, fence=""), "code block tagged 'none'"),
        ("Final answer: " + json.dumps(GOOD), "bare object"),
        (reply(GOOD).replace("true", "True").replace("null", "None"), "python-style"),
        (reply(GOOD) + "\nThat is all.", "text after the final code block"),
    ],
)
def test_tolerated_answer_formats(task: Any, text: str, issue: str) -> None:
    g = G.grade_answer(task, make_truth(), text)
    assert g.passed, g.format_issues
    assert any(issue in i for i in g.format_issues), g.format_issues


def test_trailing_comma_and_key_spelling_are_tolerated(task: Any) -> None:
    body = json.dumps({**GOOD}, indent=2)
    body = body.replace('"first_to_trigger"', '"First to trigger"').rstrip("}") + ",\n}"
    g = G.grade_answer(task, make_truth(), f"```json\n{body}\n```")
    assert g.passed
    assert any("trailing comma" in i for i in g.format_issues)
    assert any("read as 'first_to_trigger'" in i for i in g.format_issues)


def test_missing_keys_unparseable_and_extra_keys(task: Any) -> None:
    g = grade(task, {k: v for k, v in GOOD.items() if k != "deterioration_real"})
    assert not g.passed and g.keys["deterioration_real"].issues == ["missing"]
    g = G.grade_answer(task, make_truth(), "I could not finish.")
    assert not g.passed and g.parse_error and g.cw_category == G.MISSING
    assert not g.confident_wrong  # no answer is not a confident wrong verdict
    g = grade(task, {**GOOD, "notes": "x"})
    assert g.passed and any("extra keys ignored: notes" in i for i in g.format_issues)
    g = G.grade_answer(task, make_truth(), None)
    assert not g.passed and g.parse_error == "empty final reply"


def test_records_hold_no_truth_values(task: Any) -> None:
    truth = make_truth(head_deficit_bep_pp={"value": 8.4321})
    rec = json.dumps(G.grade_answer(task, truth, reply(GOOD)).to_dict())
    assert "8.4321" not in rec and "61.2" not in rec and "37.5" not in rec


# ----------------------------------------------------------------------------------------
# numbers
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "passed"),
    [
        ({"value": 8.9, "lo90": 7.0, "hi90": 10.0}, True),
        (8.9, True),  # a bare number is a point with no interval
        (9.4, True),  # |9.4 - 8.4| = tol exactly (1.0000000000000009 in floating point)
        (7.4, True),
        (9.41, False),
        (7.39, False),
        ("8.9 pp", True),  # a number given as a string, with a unit
        ({"value": "8.9"}, True),
        ({"lo90": 7.0, "hi90": 10.0}, False),  # no point on a determinable key
        ("cannot_determine", False),  # unjustified abstention
        (True, False),
        ([8.9], False),
        (None, False),
        ({"value": None}, False),
    ],
)
def test_determinable_estimate(task: Any, given: Any, passed: bool) -> None:
    r = key(task, "head_deficit_bep_pp", given)
    assert r.passed is passed, r
    assert r.determinable is True


def test_unjustified_abstention_is_recorded(task: Any) -> None:
    g = grade(task, dict(GOOD, head_deficit_bep_pp="cannot_determine"))
    assert not g.passed
    assert g.abstentions() == (
        ["head_deficit_bep_pp", "wire_to_water_efficiency_pct"],
        ["head_deficit_bep_pp"],
    )


@pytest.mark.parametrize(
    ("given", "passed", "issue"),
    [
        ("cannot_determine", True, None),
        ("Cannot determine", True, "cannot_determine read from"),
        ("cannot be determined", True, "cannot_determine read from"),
        ("Cannot-Determine.", True, "cannot_determine read from"),
        ("undetermined", True, "cannot_determine read from"),
        ("not determinable", True, "cannot_determine read from"),
        ({"value": "cannot_determine"}, True, "value of an object"),
        ("unknown", False, None),
        ({"value": 61.0}, False, "needs cannot_determine or a valid interval"),
        (61.2, False, None),  # a point, even exactly right, does not pass
        ({"value": 60, "lo90": 30, "hi90": 70}, True, None),  # 40 of 70: >= half, holds truth
        ({"lo90": 55, "hi90": 90}, True, None),  # exactly half of [20, 90], holds 61.2
        ({"lo90": 55.1, "hi90": 90}, False, "less than half"),
        ({"lo90": 20, "hi90": 55}, False, None),  # half, but misses 61.2
        ({"value": 50, "lo90": 40, "hi90": 60}, False, "less than half"),  # narrow
        ({"value": 30, "lo90": 21, "hi90": 60}, False, None),  # wide, misses 61.2
        ({"lo90": 60, "hi90": 200}, False, "less than half"),  # only [60, 90] counts
        ({"lo90": 0, "hi90": 100}, True, None),  # the whole range
        ({"value": 60, "lo90": 70, "hi90": 30}, False, "lo90 70 > hi90 30"),  # malformed
    ],
)
def test_non_determinable_estimate(task: Any, given: Any, passed: bool, issue: str | None) -> None:
    r = key(task, "wire_to_water_efficiency_pct", given)
    assert r.passed is passed, r
    assert r.determinable is False
    if issue:
        assert any(issue in i for i in r.issues), r.issues
    assert r.abstained is ("cannot" in str(given).lower() or "determin" in str(given).lower())


def test_non_determinable_without_truth_value_only_abstention_passes(task: Any) -> None:
    truth = make_truth(wire_to_water_efficiency_pct={"value": None})
    assert key(task, "wire_to_water_efficiency_pct", "cannot_determine", truth).passed
    r = key(task, "wire_to_water_efficiency_pct", {"lo90": 20, "hi90": 90}, truth)
    assert not r.passed and any("truth value is unknown" in i for i in r.issues)


@pytest.mark.parametrize(
    ("given", "passed", "issue"),
    [
        (37.5, True, None),
        ({"value": 41.25, "lo90": 30, "hi90": 50}, True, None),  # at +tol
        ({"value": 41.26}, False, None),
        ("cannot_determine", False, "not an answer for an estimate key"),
        ({"value": 36, "lo90": 50, "hi90": 30}, True, "malformed interval"),  # point still graded
        ({"value": 36, "lo90": 30}, True, "lo90 or hi90 missing"),
        ({"value": 36, "lo90": 30, "hi90": "wide"}, True, "hi90 'wide' is not a number"),
        ({"value": 36, "lo90": None, "hi90": None}, True, None),  # explicitly no interval
        ({"Value": 36, "Lower": 30, "Upper": 45}, True, "read as value"),
    ],
)
def test_plain_estimate_and_malformed_intervals(
    task: Any, given: Any, passed: bool, issue: str | None
) -> None:
    r = key(task, "extra_energy_mwh_per_yr", given)
    assert r.passed is passed, r
    if issue:
        assert any(issue in i for i in r.issues), r.issues
    if isinstance(given, dict) and "malformed" in (issue or ""):
        assert r.interval is None and r.covered is None


def test_interval_coverage_and_score() -> None:
    assert G.interval_score(1.0, 3.0, 2.0) == pytest.approx(2.0)
    assert G.interval_score(1.0, 3.0, 4.0) == pytest.approx(2.0 + 20.0)
    assert G.interval_score(1.0, 3.0, 0.5) == pytest.approx(2.0 + 10.0)


def test_interval_that_misses_the_truth_is_not_covered(task: Any) -> None:
    r = key(task, "head_deficit_bep_pp", {"value": 8.9, "lo90": 8.6, "hi90": 9.5})
    assert r.passed and r.covered is False and r.interval == [8.6, 9.5]


# ----------------------------------------------------------------------------------------
# booleans, choices, sets
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "passed"),
    [(True, True), (False, False), ("true", True), ("Yes", True), ("no", False), (1, False)],
)
def test_boolean(task: Any, given: Any, passed: bool) -> None:
    assert key(task, "deterioration_real", given).passed is passed


@pytest.mark.parametrize(
    ("given", "passed"),
    [
        ("F-2", True),
        ("f-2", True),
        ("F 2", True),
        ("F_2", True),
        ("F-1", False),
        ("Filter 2", False),
        (2, False),
        (["F-2"], False),
    ],
)
def test_choice(task: Any, given: Any, passed: bool) -> None:
    assert key(task, "first_to_trigger", given).passed is passed


@pytest.mark.parametrize(
    ("given", "passed"),
    [
        (["pump_head_deficit", "valve_kv"], True),
        (["valve_kv", "pump_head_deficit"], True),
        (["Valve KV", "pump-head-deficit"], True),
        (["valve_kv", "valve_kv", "pump_head_deficit"], True),  # duplicate removed
        (["valve_kv"], False),
        (["valve_kv", "pump_head_deficit", "filter_clean_dp"], False),
        (["valve_kv", "pump_head_deficit", "flow_meter"], False),  # not an option
        ([], False),
        ("valve_kv", False),  # one-element set
        ({"valve_kv": True}, False),
    ],
)
def test_set(task: Any, given: Any, passed: bool) -> None:
    assert key(task, "determinable_set", given).passed is passed


def test_empty_set_truth(task: Any) -> None:
    truth = make_truth(determinable_set={"value": []})
    assert key(task, "determinable_set", [], truth).passed
    assert not key(task, "determinable_set", ["valve_kv"], truth).passed


# ----------------------------------------------------------------------------------------
# the diagnosis table (section 6.6), row by row
# ----------------------------------------------------------------------------------------
def mag(v: float) -> dict[str, float]:
    return {"value": v, "lo90": v - 1, "hi90": v + 1}


def ident(*faults: str, **mags: float) -> dict[str, Any]:
    return {"verdict": "identified", "faults": list(faults),
            "magnitudes": {f: mag(v) for f, v in mags.items()}}  # fmt: skip


def amb(*faults: str, rm: str | None = "suction_pressure") -> dict[str, Any]:
    d: dict[str, Any] = {"verdict": "ambiguous", "faults": list(faults)}
    if rm is not None:
        d["resolving_measurement"] = rm
    return d


NO = {"verdict": "no_fault", "faults": []}
S3 = ("low_suction_level", "worn_pump", "pump_running_slow")

TABLE = [
    # Single X
    ("single: identified X, magnitude within tol", SINGLE, ident("worn_pump", worn_pump=8.9),
     G.PASS),
    ("single: identified X, magnitude outside tol", SINGLE, ident("worn_pump", worn_pump=9.2),
     G.WRONG_MAGNITUDE),
    ("single: identified X, magnitude missing", SINGLE, ident("worn_pump"), G.WRONG_MAGNITUDE),
    ("single: ambiguous with a set containing X", SINGLE, amb("worn_pump", "leak"),
     G.UNDER_COMMITMENT),
    ("single: identified Y", SINGLE, ident("leak", leak=3), G.CONFIDENT_WRONG),
    ("single: a pair", SINGLE, ident("worn_pump", "leak", worn_pump=8, leak=3),
     G.CONFIDENT_WRONG),
    ("single: no_fault", SINGLE, NO, G.CONFIDENT_WRONG),
    ("single: ambiguous without X (not in the table)", SINGLE, amb("leak", "throttled_valve"),
     G.WRONG_SET),
    # Pair {X, Y}
    ("pair: identified X, Y, both within tol", PAIR,
     ident("worn_pump", "throttled_valve", worn_pump=8.5, throttled_valve=33), G.PASS),
    ("pair: order does not matter", PAIR,
     ident("throttled_valve", "worn_pump", worn_pump=7.5, throttled_valve=26), G.PASS),
    ("pair: identified X only", PAIR, ident("worn_pump", worn_pump=8), G.INCOMPLETE),
    ("pair: identified Y only", PAIR, ident("throttled_valve", throttled_valve=30), G.INCOMPLETE),
    ("pair: other identified (X, Z)", PAIR, ident("worn_pump", "leak", worn_pump=8, leak=2),
     G.CONFIDENT_WRONG),
    ("pair: other identified (Z)", PAIR, ident("leak", leak=2), G.CONFIDENT_WRONG),
    ("pair: other identified (X, Y, Z)", PAIR,
     ident("worn_pump", "throttled_valve", "leak", worn_pump=8, throttled_valve=30, leak=2),
     G.CONFIDENT_WRONG),
    ("pair: no_fault", PAIR, NO, G.CONFIDENT_WRONG),
    ("pair: ambiguous containing both", PAIR, amb("worn_pump", "throttled_valve"),
     G.UNDER_COMMITMENT),
    ("pair: ambiguous with other faults", PAIR, amb("leak", "pump_running_slow"),
     G.UNDER_COMMITMENT),
    ("pair: X, Y with a magnitude outside tol (not in the table)", PAIR,
     ident("worn_pump", "throttled_valve", worn_pump=8, throttled_valve=40), G.WRONG_MAGNITUDE),
    # Ambiguous S
    ("ambiguous: S and a resolving measurement", AMBIGUOUS, amb(*S3), G.PASS),
    ("ambiguous: S plus one and the other resolving measurement", AMBIGUOUS,
     amb(*S3, "leak", rm="sump_level"), G.PASS),
    ("ambiguous: measurement spelled differently", AMBIGUOUS, amb(*S3, rm="Sump level"), G.PASS),
    ("ambiguous: S plus two", AMBIGUOUS, amb(*S3, "leak", "throttled_valve"), G.WRONG_SET),
    ("ambiguous: a member missing", AMBIGUOUS, amb(*S3[:2]), G.WRONG_SET),
    ("ambiguous: a measurement that does not resolve", AMBIGUOUS, amb(*S3, rm="motor_power"),
     G.WRONG_SET),
    ("ambiguous: a measurement that is not an option", AMBIGUOUS, amb(*S3, rm="vibration"),
     G.WRONG_SET),
    ("ambiguous: no measurement", AMBIGUOUS, amb(*S3, rm=None), G.WRONG_SET),
    ("ambiguous: identified", AMBIGUOUS, ident("worn_pump", worn_pump=8), G.CONFIDENT_WRONG),
    ("ambiguous: identified with the whole set", AMBIGUOUS,
     ident(*S3, low_suction_level=1, worn_pump=8, pump_running_slow=5), G.CONFIDENT_WRONG),
    ("ambiguous: no_fault", AMBIGUOUS, NO, G.CONFIDENT_WRONG),
    # None
    ("none: no_fault", NONE, NO, G.PASS),
    ("none: no_fault listing none", NONE, {"verdict": "no_fault", "faults": ["none"]}, G.PASS),
    ("none: ambiguous", NONE, amb("none", "leak"), G.UNDER_COMMITMENT),
    ("none: identified", NONE, ident("leak", leak=2), G.CONFIDENT_WRONG),
    ("none: identified 'none'", NONE, ident("none"), G.CONFIDENT_WRONG),
    ("none: identified nothing", NONE, ident(), G.CONFIDENT_WRONG),
    # Sensor-fault stratum: a single fault like any other
    ("sensor: identified with the offset within tol", SENSOR,
     ident("pressure_sensor_fault", pressure_sensor_fault=0.14), G.PASS),
    ("sensor: offset outside tol", SENSOR,
     ident("pressure_sensor_fault", pressure_sensor_fault=0.2), G.WRONG_MAGNITUDE),
    ("sensor: blamed on the pump", SENSOR, ident("worn_pump", worn_pump=6), G.CONFIDENT_WRONG),
]  # fmt: skip


@pytest.mark.parametrize(("name", "truth", "answer", "category"), TABLE, ids=[r[0] for r in TABLE])
def test_diagnosis_table(
    task: Any, name: str, truth: dict[str, Any], answer: dict[str, Any], category: str
) -> None:
    g = grade(task, dict(GOOD, diagnosis=answer), make_truth(truth))
    r = g.keys["diagnosis"]
    assert r.category == category, (name, r.issues)
    assert r.passed is (category == G.PASS)
    assert g.passed is (category == G.PASS)  # every other key is right
    assert g.confident_wrong is (category == G.CONFIDENT_WRONG)
    assert g.under_commitment is (category == G.UNDER_COMMITMENT)


def test_every_diagnosis_category_is_exercised() -> None:
    covered = {row[3] for row in TABLE}
    assert covered == set(G.CATEGORIES) - {G.MALFORMED, G.MISSING}


@pytest.mark.parametrize(
    ("answer", "category"),
    [
        ("worn_pump", G.MALFORMED),
        (["identified", "worn_pump"], G.MALFORMED),
        ({"verdict": "probably worn", "faults": ["worn_pump"]}, G.MALFORMED),
        ({"faults": ["worn_pump"]}, G.MALFORMED),
        (
            {"verdict": "Identified", "faults": "worn_pump", "magnitudes": {"Worn pump": 8.3}},
            G.PASS,
        ),
        (
            {
                "Verdict": "identified",
                "Faults": ["WORN-PUMP", "worn_pump"],
                "Magnitudes": {"worn_pump": "8.3 pp"},
            },
            G.PASS,
        ),
        (
            {"verdict": "identified", "faults": ["worn impeller"], "magnitudes": {"worn_pump": 8}},
            G.CONFIDENT_WRONG,
        ),
        (
            {"verdict": "identified", "faults": ["worn_pump"], "magnitudes": [8.0]},
            G.WRONG_MAGNITUDE,
        ),
        (
            {
                "verdict": "identified",
                "faults": ["worn_pump"],
                "magnitudes": {"worn_pump": "cannot_determine"},
            },
            G.WRONG_MAGNITUDE,
        ),
        # a fault list that is not a list of names is a format error, not a verdict
        (
            {"verdict": "identified", "faults": [{"fault": "worn_pump", "magnitude": 8}]},
            G.MALFORMED,
        ),
        ({"verdict": "ambiguous", "faults": {"worn_pump": 1}}, G.MALFORMED),
        ({"verdict": "identified", "faults": ["worn_pump", 3]}, G.MALFORMED),
        ({"verdict": "no_fault", "faults": [{"x": 1}]}, G.CONFIDENT_WRONG),  # verdict governs
        ({"verdict": "identified", "faults": None}, G.CONFIDENT_WRONG),  # identified nothing
    ],
)
def test_diagnosis_parsing(task: Any, answer: Any, category: str) -> None:
    r = grade(task, dict(GOOD, diagnosis=answer), make_truth(SINGLE)).keys["diagnosis"]
    assert r.category == category, r.issues


def test_no_fault_verdict_spellings(task: Any) -> None:
    for verdict in ("no_fault", "No fault", "no-fault", "NO_FAULT", "nofault"):
        r = grade(task, dict(GOOD, diagnosis={"verdict": verdict}), make_truth(NONE))
        assert r.keys["diagnosis"].category == G.PASS, verdict


def test_missing_diagnosis_is_missing_not_confident_wrong(task: Any) -> None:
    g = grade(task, {k: v for k, v in GOOD.items() if k != "diagnosis"}, make_truth(SINGLE))
    assert g.cw_category == G.MISSING and not g.confident_wrong and not g.passed


def test_magnitude_boundary_and_traceability(task: Any) -> None:
    answer = dict(GOOD, diagnosis=ident("worn_pump", worn_pump=9.0))  # at +tol exactly
    g = G.grade_answer(task, make_truth(SINGLE), reply(answer), tool_numbers=[8.9, 36.0])
    assert g.keys["diagnosis"].category == G.PASS
    assert g.keys["diagnosis"].traceable is False  # 9.0 appeared in no tool result
    assert g.keys["head_deficit_bep_pp"].traceable is True
    assert g.keys["extra_energy_mwh_per_yr"].traceable is True
    assert g.keys["deterioration_real"].traceable is None


# ----------------------------------------------------------------------------------------
# task.json and truth validation
# ----------------------------------------------------------------------------------------
def test_truth_problems_are_found(task: Any) -> None:
    t = make_truth()
    t["keys"].pop("deterioration_real")
    assert any("missing ['deterioration_real']" in p for p in truth_problems(task, t))
    t = make_truth()
    t["keys"]["first_to_trigger"]["value"] = "F-9"
    assert any("is not an option" in p for p in truth_problems(task, t))
    t = make_truth({**AMBIGUOUS, "resolving": ["vibration"]})
    assert any("resolving_options" in p for p in truth_problems(task, t))
    t = make_truth({**SINGLE, "magnitudes": {}})
    assert any("magnitude of 'worn_pump'" in p for p in truth_problems(task, t))
    t = make_truth(head_deficit_bep_pp={"range": [0, 40]})
    assert any("differs from task.json" in p for p in truth_problems(task, t))
    t = make_truth(head_deficit_bep_pp={"determinable": "yes"})
    assert any("determinable must be" in p for p in truth_problems(task, t))
    t = make_truth()
    t["realisations"]["r1"]["reference_pass"]["R-a"] = "pass"
    assert any("reference_pass" in p for p in truth_problems(task, t))


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.update(task_id="other"), "differs from the directory"),
        (lambda d: d.update(set="test"), "differs from the set directory"),
        (lambda d: d.update(family="F9"), "family"),
        (lambda d: d.update(cell="F3/G-epa"), "cell"),
        (lambda d: d.update(stratum=None), "needs a stratum"),
        (lambda d: d["keys"]["first_to_trigger"].update(options=["F-1"]), "at least 2"),
        (lambda d: d["keys"]["first_to_trigger"].update(options=["F-1", "f 1"]), "distinct"),
        (lambda d: d["keys"]["head_deficit_bep_pp"].update(range=[30, 0]), "L < U"),
        (lambda d: d["keys"]["head_deficit_bep_pp"].pop("unit"), "needs a unit"),
        (lambda d: d["keys"]["deterioration_real"].update(kind="bool"), "kind 'bool'"),
        (lambda d: d["keys"].pop("diagnosis"), "exactly one diagnosis key"),
        (lambda d: d["keys"]["diagnosis"]["vocabulary"]["leak"].pop("m_min"), "m_min"),
    ],
)
def test_task_json_validation(mutate: Any, needle: str) -> None:
    data = copy.deepcopy(TASK_JSON)
    mutate(data)
    with pytest.raises(BundleError) as exc:
        task_from_json(data, Path("ops-f3-001"), "dev", (1,))
    assert any(needle in p for p in exc.value.problems), exc.value.problems
