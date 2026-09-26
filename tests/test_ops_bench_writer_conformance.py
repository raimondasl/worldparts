"""The private generator's task.json and truth.json formats against the public loader and grader.

``tests/fixtures/opsbench/writer-documents.json`` holds one task.json and truth.json per family
and F3 stratum as the private writers make them (a private test keeps the file equal to their
output): F2 keys named ``hours_to_trigger_f_1``, a vocabulary whose ``reverse_rotation`` and
``pressure_sensor_fault`` have no task-level bounds (m_min and range null), signed sensor-fault
magnitudes, ``r2_applies`` false on filtration and train plants. Each must load, validate and
grade as INTERFACE.md and PREREGISTRATION.md section 6.6 say. Also here: the loader rules these
documents rely on (logged fix of 2026-09-26 in FREEZES.md). Synthetic documents only.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.operations.harness import arms, bundles  # noqa: E402
from benchmarks.operations.harness import grading as G  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "opsbench" / "writer-documents.json"
DOCS: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))["documents"]


def write_bundle(root: Path, doc: dict[str, Any], truth: dict[str, Any]) -> bundles.OpsTask:
    """r1..r3 with the writer's task.json, and the truth file; the task as the loader reads it."""
    tdir = root / "bundles" / "dev" / doc["task_id"]
    for k in (1, 2, 3):
        r = tdir / f"r{k}"
        r.mkdir(parents=True)
        (r / "task.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        (r / "task.md").write_text(f"# Ticket {doc['task_id']}\n", encoding="utf-8")
        (r / "plant.md").write_text("# Plant\n", encoding="utf-8")
    (root / "truth" / "dev").mkdir(parents=True, exist_ok=True)
    (root / "truth" / "dev" / f"{doc['task_id']}.truth.json").write_text(
        json.dumps(truth), encoding="utf-8"
    )
    return bundles.load_task(tdir, "dev")


def correct(tkeys: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, t in tkeys.items():
        if t["kind"] == "estimate_or_undetermined" and not t["determinable"]:
            out[k] = "cannot_determine"
        elif t["kind"] in ("estimate", "estimate_or_undetermined"):
            out[k] = {"value": t["value"], "lo90": t["value"] - 1, "hi90": t["value"] + 1}
        elif t["kind"] == "diagnosis" and t["label"] == "identified":
            mags = {f: {"value": m["value"]} for f, m in t["magnitudes"].items()}
            out[k] = {"verdict": "identified", "faults": t["faults"], "magnitudes": mags}
        elif t["kind"] == "diagnosis" and t["label"] == "ambiguous":
            out[k] = {"verdict": "ambiguous", "faults": t["faults"],
                      "resolving_measurement": t["resolving"][0]}  # fmt: skip
        elif t["kind"] == "diagnosis":
            out[k] = {"verdict": "no_fault", "faults": []}
        else:
            out[k] = t["value"]
    return out


def wrong(t: dict[str, Any], spec: bundles.KeySpec) -> Any:
    if t["kind"] == "diagnosis":
        if t["label"] == "no_fault":
            return {"verdict": "identified", "faults": ["leak"], "magnitudes": {"leak": 5.0}}
        return {"verdict": "no_fault", "faults": []}
    if t["kind"] == "boolean":
        return not t["value"]
    if t["kind"] == "choice":
        return next(o for o in spec.options if o != t["value"])
    if t["kind"] == "set":
        return [] if t["value"] else [spec.options[0]]
    assert spec.range is not None
    return {"value": spec.range[1] + 1e6}


def reply(values: dict[str, Any]) -> str:
    return "Done.\n\n```json\n" + json.dumps(values, indent=2) + "\n```\n"


def test_the_fixture_covers_every_family_and_stratum() -> None:
    fams = {d["task"]["family"] for d in DOCS.values()}
    strata = {d["task"]["stratum"] for d in DOCS.values() if d["task"]["family"] == "F3"}
    assert fams == set(bundles.FAMILIES) and strata == set(bundles.STRATA)


@pytest.mark.parametrize("case", sorted(DOCS))
def test_writer_documents_load_validate_and_grade(tmp_path: Path, case: str) -> None:
    d = DOCS[case]
    task = write_bundle(tmp_path, d["task"], d["truth"])
    truth = bundles.load_truth(task, tmp_path / "truth")
    assert list(task.keys) == d["keys_md"] and set(truth["keys"]) == set(task.keys)
    instruction = arms.answer_format_instruction(task)
    for k in task.keys:  # the answer-format instruction names the keys as task.md does
        assert re.fullmatch(r"[a-z][a-z0-9_]*", k) and f"{k} (" in instruction
    g = G.grade_answer(task, truth, reply(correct(truth["keys"])))
    assert g.passed and not g.format_issues, (g.to_dict(), g.format_issues)
    for k, spec in task.keys.items():
        ans = correct(truth["keys"])
        ans[k] = wrong(truth["keys"][k], spec)
        assert not G.grade_answer(task, truth, reply(ans)).passed, k


def test_f2_keys_are_read_in_the_filter_spelling_with_a_format_issue(tmp_path: Path) -> None:
    d = DOCS["F2"]
    task = write_bundle(tmp_path, d["task"], d["truth"])
    hours = [k for k in task.keys if k.startswith("hours_to_trigger_")]
    assert hours == ["hours_to_trigger_f_1", "hours_to_trigger_f_2", "hours_to_trigger_f_3"]
    ans = {k.replace("_f_", "_F-"): v for k, v in correct(d["truth"]["keys"]).items()}
    g = G.grade_answer(task, d["truth"], reply(ans))
    assert g.passed
    assert "key 'hours_to_trigger_F-1' read as 'hours_to_trigger_f_1'" in g.format_issues


def test_the_draws_f2_key_names_are_refused(tmp_path: Path) -> None:
    """Keys with a capital or a hyphen would never match a normalised answer key."""
    doc = copy.deepcopy(DOCS["F2"]["task"])
    doc["keys"] = {k.replace("_f_", "_F-"): v for k, v in doc["keys"].items()}
    with pytest.raises(bundles.BundleError) as exc:
        write_bundle(tmp_path, doc, DOCS["F2"]["truth"])
    assert any("must be lower-case snake_case" in p for p in exc.value.problems)


def test_faults_without_task_level_bounds_load(tmp_path: Path) -> None:
    d = DOCS["F3-sensor_pressure"]
    task = write_bundle(tmp_path, d["task"], d["truth"])
    vocab = task.keys["diagnosis"].vocabulary
    assert vocab["reverse_rotation"] == {"unit": "identity", "m_min": None, "range": None}
    psf = vocab["pressure_sensor_fault"]
    assert psf["m_min"] is None and psf["range"] is None and psf["unit"] == "bar (signed)"
    assert "bounds" in psf  # extra fields are kept
    lo, hi = vocab["flow_sensor_fault"]["range"]
    assert lo == -hi and vocab["flow_sensor_fault"]["m_min"] > 0  # signed; m_min bounds |m|


def test_an_identified_truth_needs_a_magnitude_also_without_bounds(tmp_path: Path) -> None:
    d = DOCS["F3-sensor_pressure"]
    task = write_bundle(tmp_path, d["task"], d["truth"])
    truth = copy.deepcopy(d["truth"])
    truth["keys"]["diagnosis"].update(faults=["reverse_rotation"], magnitudes={})
    assert any("magnitude of 'reverse_rotation'" in p for p in bundles.truth_problems(task, truth))


def test_sensor_magnitudes_are_signed(tmp_path: Path) -> None:
    """The truth is negative when the meter reads low; 6.6 grades |value - truth| <= tol."""
    d = DOCS["F3-sensor"]
    task = write_bundle(tmp_path, d["task"], d["truth"])
    truth = d["truth"]
    assert truth["keys"]["diagnosis"]["magnitudes"]["flow_sensor_fault"]["value"] < 0

    def category(value: float, name: str = "flow_sensor_fault") -> str | None:
        ans = {"diagnosis": {"verdict": "identified", "faults": [name],
                             "magnitudes": {name: {"value": value}}}}  # fmt: skip
        return G.grade_answer(task, truth, reply(ans)).keys["diagnosis"].category

    assert category(-1.5) == G.PASS
    assert category(1.5) == G.WRONG_MAGNITUDE
    # a fault named with its instrument is not a vocabulary name (6.6): confident wrong
    assert category(-1.5, "flow_sensor_fault@FT-12") == G.CONFIDENT_WRONG
