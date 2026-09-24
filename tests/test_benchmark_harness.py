"""Tests of the composition benchmark harness (benchmarks/composition/harness).

Covers the task schema and loader rules, the reference executor (against the MCP tools it
mirrors), regen, the oracle and corrupted-oracle modes (on the fixture and on every task
file with frozen answers), final-JSON parsing edge cases, scoring, traceability, stream
parsing, the report and the claude command lines. Nothing here starts a Claude session.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest
import yaml
from mcp import Client

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.composition.harness import __main__ as cli  # noqa: E402
from benchmarks.composition.harness import grading, regen, report, runner  # noqa: E402
from benchmarks.composition.harness.reference import (  # noqa: E402
    ReferenceStepError,
    run_reference,
    same_value,
)
from benchmarks.composition.harness.tasks import (  # noqa: E402
    TASKS_DIR,
    Answer,
    TaskError,
    check_task,
    load_task,
    task_files,
    task_from_dict,
    task_schema,
)
from worldparts.mcp_server import create_server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "benchmark" / "tasks"
FIXTURE = FIXTURES / "lift-fixture-01.yaml"


def fixture_data() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def task():  # type: ignore[no-untyped-def]
    return load_task(FIXTURE)


# ----------------------------------------------------------------------------------------
# schema and loader
# ----------------------------------------------------------------------------------------
def test_schema_is_valid_and_fixture_loads(task: Any) -> None:
    jsonschema.Draft202012Validator.check_schema(task_schema())
    assert task.id == "lift-fixture-01" and task.level == 1
    assert task.keys == [
        "flow",
        "head",
        "speed",
        "empty_time",
        "final_level",
        "inlet_used",
        "limit",
    ]
    assert task.answer("limit").choices == ("tank_runs_dry", "pump_overload", "none")
    assert task.expected is not None and task.expected["limit"] == "tank_runs_dry"


def _problems(mutate: Any) -> list[str]:
    data = fixture_data()
    mutate(data)
    return check_task(data, FIXTURE)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.update(id="other-id"), "does not match the file name"),
        (lambda d: d.update(level=2), "level 2 needs 5-7 components"),
        (lambda d: d.update(id="Bad_Id"), "schema: id"),
        (lambda d: d.update(category="guessing"), "schema: category"),
        (lambda d: d["answers"][0].pop("unit"), "schema: answers/0"),
        (lambda d: d["answers"][0].pop("rel_tol"), "schema: answers/0"),
        (lambda d: d["answers"][6].update(unit="m"), "schema: answers/6"),
        (lambda d: d["answers"].append(dict(d["answers"][0])), "duplicate answer keys"),
        (lambda d: d["answers"][0].update(unit="blorps"), "does not parse"),
        (
            lambda d: d["reference"]["steps"][0]["read"].pop("flow"),
            "answer flow is not produced by any step",
        ),
        (
            lambda d: d["reference"]["steps"][4]["values"].update(flow=1.0),
            "answer flow is produced by 2 steps",
        ),
        (
            lambda d: d["reference"]["steps"][0]["read"].update(extra="pump.head"),
            "'extra', which is not an answer key",
        ),
        (
            lambda d: d["reference"]["steps"][4]["values"].update(limit="meteor"),
            "'meteor' is not one of",
        ),
        (
            lambda d: d["reference"]["steps"][4]["values"].update(inlet_used="no"),
            "is not a boolean",
        ),
        (lambda d: d["reference"]["steps"].append({"op": "levitate"}), "schema: reference"),
        (lambda d: d["reference"]["steps"][0].update(duration=5), "schema: reference/steps/0"),
        (lambda d: d["reference"]["expected"].pop("head"), "expected lacks head"),
        (lambda d: d["reference"]["expected"].update(bogus=1), "unknown keys bogus"),
        (lambda d: d["reference"]["expected"].update(flow="fast"), "is not a number"),
        (lambda d: d.update(prompt=d["prompt"] + " Use worldparts."), "'worldparts'"),
        (lambda d: d.update(prompt=d["prompt"] + " Add a centrifugal_pump."), "centrifugal_pump"),
        (lambda d: d.update(prompt=d["prompt"] + " Watch tank_empty."), "'tank_empty'"),
        (lambda d: d.update(prompt=d["prompt"] + " Reply in JSON."), "answer format"),
        # The answer keys and descriptions reach the agent through the appended format line.
        (
            lambda d: d["answers"][0].update(key="shaft_power"),
            "an answer key or description names the worldparts identifier 'shaft_power'",
        ),
        (
            lambda d: d["answers"][0].update(description="The pump's volume_flow"),
            "names the worldparts identifier 'volume_flow'",
        ),
        (
            lambda d: d["answers"][0].update(key="npsh_available_20c"),
            "answer key 'npsh_available_20c' contains the worldparts identifier",
        ),
    ],
)
def test_loader_rejects_inconsistent_tasks(mutate: Any, needle: str) -> None:
    problems = _problems(mutate)
    assert any(needle in p for p in problems), problems


def test_loader_accepts_the_fixture_and_missing_expected() -> None:
    assert check_task(fixture_data(), FIXTURE) == []
    data = fixture_data()
    data["reference"]["expected"] = {}  # not generated yet
    t = task_from_dict(data, FIXTURE)
    assert t.expected is None
    with pytest.raises(TaskError) as err:
        task_from_dict({**data, "level": 3}, FIXTURE)
    assert "level 3" in str(err.value)


def test_answer_format_instruction(task: Any) -> None:
    text = grading.answer_format_instruction(task.answers)
    assert text.startswith(
        "When you are done, end your reply with a single JSON object in a ```json block "
        "with exactly these keys: flow (number, m3/h: Pump flow at full speed), "
    )
    assert "speed (number, dimensionless: Relative pump speed for 12 m3/h)" in text
    assert "inlet_used (true or false: " in text
    assert "limit (one of: tank_runs_dry, pump_overload, none: What ends the pumping)" in text
    prompt = grading.build_prompt(task)
    assert prompt.startswith(task.prompt.rstrip()) and prompt.endswith(text)


# ----------------------------------------------------------------------------------------
# reference executor and regen
# ----------------------------------------------------------------------------------------
def test_reference_reproduces_the_fixture(task: Any) -> None:
    produced = run_reference(task)
    assert list(produced) == task.keys
    for k in task.keys:
        assert same_value(produced[k], task.expected[k]), k
    # The hand-checkable facts behind the fixture: the tank (1.5 m of a 2 m cylinder,
    # 4.71 m3) empties at about 19 m3/h in 15 minutes and stops at the 1 mm empty level.
    assert produced["empty_time"] == pytest.approx(15.0)
    assert produced["final_level"] == pytest.approx(1.0, abs=0.01)  # mm


def _mcp(calls: Any) -> Any:
    out: dict[str, Any] = {}

    async def main() -> None:
        async with Client(create_server()) as client:
            await calls(client, out)

    anyio.run(main)
    return out


def test_reference_solve_for_matches_the_mcp_tool(task: Any) -> None:
    async def calls(client: Any, out: dict[str, Any]) -> None:
        sid = await client.call_tool("load_system", {"document": task.system})
        sid = sid.structured_content["system_id"]
        r = await client.call_tool(
            "solve_for",
            {"system_id": sid, "target": "pump.volume_flow", "value": "12 m3/h",
             "vary": "pump.speed", "lower": 0.5, "upper": 1.2},
        )  # fmt: skip
        out["speed"] = r.structured_content["found"]["value"]

    got = _mcp(calls)
    assert got["speed"] == pytest.approx(run_reference(task)["speed"], rel=1e-9)


def test_reference_ramp_events_match_the_mcp_tool(task: Any) -> None:
    data = fixture_data()
    ramp = {"at": "0 s", "ramp": {"pump.speed": [1.0, 0.8]}, "over": "5 min"}
    data["answers"] = [{"key": "level", "description": "x", "unit": "m", "abs_tol": 0.01}]
    data["reference"]["steps"] = [
        {"op": "simulate", "duration": "10 min", "step": "10 s", "events": [ramp],
         "read_final": {"level": "tank.level"}},
    ]  # fmt: skip
    data["reference"].pop("expected")
    ref = run_reference(task_from_dict(data, FIXTURE))["level"]

    async def calls(client: Any, out: dict[str, Any]) -> None:
        sid = await client.call_tool("load_system", {"document": task.system})
        sid = sid.structured_content["system_id"]
        r = await client.call_tool(
            "simulate",
            {"system_id": sid, "duration": "10 min", "step": "10 s", "events": [ramp],
             "variables": ["tank.level"]},
        )  # fmt: skip
        out["level"] = r.structured_content["variables"]["tank.level"]["final"]

    assert _mcp(calls)["level"] == pytest.approx(ref, rel=1e-5)


def test_reference_errors_name_the_step() -> None:
    data = fixture_data()
    data["reference"]["steps"][1]["value"] = "90 m3/h"  # unreachable
    with pytest.raises(ReferenceStepError, match=r"steps\[1\] \(solve_for\).*does not cross"):
        run_reference(task_from_dict(data, FIXTURE))
    data = fixture_data()
    data["reference"]["steps"][3]["read_first_warning"]["empty_time"]["warning"] = (
        "tank.tank_overflow"
    )
    with pytest.raises(ReferenceStepError, match="never raised"):
        run_reference(task_from_dict(data, FIXTURE))


@pytest.mark.parametrize(
    "expected_block",
    [
        "  expected: {flow: 1, head: 2}\n",
        "  expected:\n    flow: 1\n    head: 2\n\n",
        "",
    ],
)
def test_replace_expected_keeps_the_rest_of_the_file(expected_block: str) -> None:
    text = (
        "id: x\n# a comment\nreference:\n  system: {a: 1}\n  steps:\n    - {op: solve}\n"
        + expected_block
        + "notes: |\n  expected: this line is prose, not a key\n"
    )
    new = regen.replace_expected(text, "{flow: 3.5, head: 4}")
    doc = yaml.safe_load(new)
    assert doc["reference"]["expected"] == {"flow": 3.5, "head": 4}
    assert doc["notes"] == "expected: this line is prose, not a key\n"
    assert "# a comment" in new and doc["reference"]["steps"] == [{"op": "solve"}]


def test_regen_writes_and_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "tasks"
    d.mkdir()
    target = d / FIXTURE.name
    shutil.copy(FIXTURE, target)
    assert regen.main(["--check", "--tasks-dir", str(d)]) == 0
    # Drift is detected ...
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("flow: 19.26", "flow: 18.26"), encoding="utf-8")
    assert regen.main(["--check", "--tasks-dir", str(d)]) == 1
    assert "DRIFT lift-fixture-01" in capsys.readouterr().out
    # ... and fixed by writing; comments survive.
    assert regen.main(["--tasks-dir", str(d)]) == 0
    assert regen.main(["--check", "--tasks-dir", str(d)]) == 0
    assert target.read_text(encoding="utf-8").startswith("# Test fixture")
    assert load_task(target).expected == load_task(FIXTURE).expected
    # Missing expected fails the check and is filled by writing.
    doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "  expected: " + regen.expected_flow(doc["reference"]["expected"], list(
                doc["reference"]["expected"])), "  expected: {}"),
        encoding="utf-8",
    )  # fmt: skip
    assert load_task(target).expected is None
    assert regen.main(["--check", "--tasks-dir", str(d)]) == 1
    assert regen.main(["--tasks-dir", str(d)]) == 0
    assert load_task(target).expected == load_task(FIXTURE).expected


def test_regen_number_format_round_trips() -> None:
    for x in (1e-05, 3.0e-7, 19.263597891165844, 15.0, -2.5e20, 0.0):
        assert yaml.safe_load(regen._fmt(x)) == pytest.approx(x, rel=1e-9)
    assert regen._fmt(True) == "true" and regen._fmt("clogged_filter") == "clogged_filter"


# ----------------------------------------------------------------------------------------
# oracle and corrupted oracle
# ----------------------------------------------------------------------------------------
def test_oracle_scores_100_and_corrupted_oracle_scores_0(task: Any) -> None:
    good = cli.oracle_records([task])
    assert [r["passed"] for r in good] == [True]
    bad = cli.oracle_records([task], corrupt=True)
    assert [r["passed"] for r in bad] == [False]
    assert all(not a["passed"] for a in bad[0]["grade"]["answers"])
    corrupted = cli.corrupt_expected(task)
    assert corrupted["inlet_used"] is True and corrupted["limit"] == "pump_overload"
    tol = task.answer("flow").tolerance(task.expected["flow"])
    assert corrupted["flow"] == pytest.approx(task.expected["flow"] + 3 * tol)
    assert cli.main(["oracle", "--tasks-dir", str(FIXTURES)]) == 0
    assert cli.main(["oracle", "--corrupt", "--tasks-dir", str(FIXTURES)]) == 0


TASK_FILES = task_files(TASKS_DIR)


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_every_benchmark_task_is_consistent(path: Path) -> None:
    """Every task file: valid, expected frozen and reproduced, oracle 100 %, corrupt 0 %."""
    t = load_task(path)
    assert t.expected is not None, f"{t.id}: run regen to freeze expected"
    produced = run_reference(t)
    assert not regen.drift(t, produced), regen.drift(t, produced)
    assert cli.oracle_records([t])[0]["passed"]
    corrupted = cli.oracle_records([t], corrupt=True)[0]
    assert not any(a["passed"] for a in corrupted["grade"]["answers"])


# ----------------------------------------------------------------------------------------
# final-JSON parsing and scoring
# ----------------------------------------------------------------------------------------
def test_parse_fenced_json_block() -> None:
    p = grading.parse_final_json('Result:\n\n```json\n{"flow": 18.6, "ok": true}\n```')
    assert p.data == {"flow": 18.6, "ok": True} and p.issues == [] and p.error is None


@pytest.mark.parametrize(
    ("text", "data", "issue"),
    [
        ('```json\n{"a": 1}\n```\nsome text\n```json\n{"a": 2}\n```', {"a": 2}, None),
        ('```\n{"a": 1}\n```', {"a": 1}, "tagged 'none'"),
        ('```python\n{"a": 1}\n```', {"a": 1}, "tagged 'python'"),
        ('The answer is {"a": 1, "b": {"c": "}"}} as computed.', {"a": 1, "b": {"c": "}"}},
         "bare object"),
        ('```json\n{"a": 1,}\n```', {"a": 1}, "trailing comma"),
        ('```json\n{"a": 1 // metres\n}\n```', {"a": 1}, "comments removed"),
        ("```json\n{'a': True, 'b': None}\n```", {"a": True, "b": None}, "not strict JSON"),
        ('```json\n{"a": 1}\n```\nThanks!', {"a": 1}, "text after the final code block"),
        ('```json\nAnswer: {"a": 1}\n```', {"a": 1}, "text around the object"),
        ('```json\n{"a": 1}\n```\n```json\nnot json\n```', {"a": 1}, "earlier one was used"),
    ],
)  # fmt: skip
def test_parse_tolerates_minor_format_problems(text: str, data: dict, issue: str | None) -> None:
    p = grading.parse_final_json(text)
    assert p.data == data, p
    if issue is None:
        assert p.issues == []
    else:
        assert any(issue in i for i in p.issues), p.issues


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (None, "empty"),
        ("   ", "empty"),
        ("I could not solve it.", "no JSON object"),
        ("```json\n[1, 2]\n```", "not an object"),
        ("```json\n{flow: ???}\n```", "not JSON"),
    ],
)
def test_parse_failures(text: str | None, error: str) -> None:
    p = grading.parse_final_json(text)
    assert p.data is None and error in (p.error or ""), p


def _reply(values: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(values) + "\n```"


def test_grade_scores_every_answer(task: Any) -> None:
    exp = task.expected
    ok = grading.grade(task, _reply(exp))
    assert ok.passed and ok.n_passed == 7 and ok.format_issues == []
    # Tolerances: flow rel 3 %, head max(3 %, 0.1 m), final_level abs 5 mm.
    values = dict(exp, flow=exp["flow"] * 1.029, final_level=exp["final_level"] + 4.9)
    assert grading.grade(task, _reply(values)).passed
    values = dict(exp, flow=exp["flow"] * 1.031)
    g = grading.grade(task, _reply(values))
    assert not g.passed and [a.key for a in g.answers if not a.passed] == ["flow"]
    flow = g.answers[0]
    assert flow.error == pytest.approx(0.031 * exp["flow"]) and flow.tolerance == pytest.approx(
        0.03 * exp["flow"]
    )
    # Missing and wrong-typed answers fail.
    partial = {k: v for k, v in exp.items() if k != "speed"}
    g = grading.grade(task, _reply(partial))
    assert not g.passed and g.answers[2].issue == "missing"
    g = grading.grade(task, _reply(dict(exp, inlet_used="maybe", limit=3)))
    assert [a.issue for a in g.answers[5:]] == ["not a boolean", "not a string"]
    # Nothing parseable: every answer fails.
    g = grading.grade(task, "no idea")
    assert not g.passed and g.parse_error and g.n_passed == 0


def test_grade_records_tolerated_formatting(task: Any) -> None:
    exp = task.expected
    values = {
        "Flow": f"{exp['flow']:.3f} m3/h",
        "head": str(exp["head"]),
        "speed": exp["speed"],
        "empty time": exp["empty_time"],
        "final-level": exp["final_level"],
        "inlet_used": "false",
        "limit": "Tank Runs Dry",
        "comment": "extra",
    }
    g = grading.grade(task, _reply(values))
    assert g.passed, g
    issues = " | ".join(g.format_issues)
    for needle in (
        "key 'Flow' read as 'flow'",
        "key 'empty time' read as 'empty_time'",
        "extra keys: comment",
        "number given as a string",
        "boolean given as a string",
        "choice normalised",
    ):
        assert needle in issues, issues


def test_choice_must_match_a_listed_choice() -> None:
    a = Answer("fault", "which", kind="choice", choices=("clogged_filter", "worn_pump"))
    assert grading.score_answer(a, "worn_pump", "worn_pump", True).passed
    assert not grading.score_answer(a, "worn_pump", "clogged_filter", True).passed
    s = grading.score_answer(a, "worn_pump", "worn pump impeller", True)
    assert not s.passed and s.issue == "not a choice"
    n = Answer("flow", "q", unit="m3/h", rel_tol=0.0, abs_tol=0.5)
    assert grading.score_answer(n, 10.0, 10.5, True).passed
    assert not grading.score_answer(n, 10.0, 10.51, True).passed
    assert not grading.score_answer(n, 10.0, True, True).passed  # a boolean is not a number
    assert not grading.score_answer(n, 10.0, float("nan"), True).passed
    assert grading.score_answer(n, 1234.0, "1,234 m3/h", True).passed


# ----------------------------------------------------------------------------------------
# traceability and stream parsing
# ----------------------------------------------------------------------------------------
def test_numbers_and_traceability() -> None:
    nums = grading.numbers_in('{"value": 18.6512, "unit": "m3/h"} p=-1.5e-3 at 2900 rpm, s2')
    assert nums == [18.6512, 3.0, -0.0015, 2900.0, 2.0]
    assert grading.traceable(18.65, nums)  # 0.01 % off
    assert grading.traceable(18.6512 * 1.0049, nums)
    assert not grading.traceable(18.6512 * 1.006, nums)
    assert not grading.traceable(0.0015, nums)  # the sign counts
    assert grading.traceable(0.0, [0.0]) and not grading.traceable(0.0, [1e-9])


def _stream(tool_result: Any, final: str, is_error: bool = False) -> str:
    msgs = [
        {"type": "system", "subtype": "init", "model": "claude-test", "session_id": "abc",
         "tools": ["mcp__worldparts__solve"], "mcp_servers": [{"name": "worldparts",
                                                               "status": "connected"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Solving."},
            {"type": "tool_use", "id": "t1", "name": "mcp__worldparts__solve", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": tool_result}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "mcp__worldparts__solve", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
             "content": "Permission to use Bash has been denied."}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": final}]}},
        {"type": "result", "subtype": "success", "is_error": is_error, "result": final,
         "num_turns": 4, "duration_ms": 12345, "duration_api_ms": 10000,
         "total_cost_usd": 0.1234,
         "usage": {"input_tokens": 100, "output_tokens": 50,
                   "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000}},
    ]  # fmt: skip
    return "\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"


def test_parse_stream_and_traceable_grading(task: Any) -> None:
    exp = task.expected
    result = [{"type": "text", "text": json.dumps({"values": {
        "pump.volume_flow": {"value": round(exp["flow"], 4), "unit": "m3/h"},
        "pump.head": {"value": round(exp["head"], 4), "unit": "m"}}})}]  # fmt: skip
    final = _reply(exp)
    s = grading.parse_stream(_stream(result, final))
    assert s.model == "claude-test" and s.session_id == "abc"
    assert (
        s.tool_calls == 2 and s.tool_errors == 1 and s.tool_names == {"mcp__worldparts__solve": 2}
    )
    assert s.denied and "denied" in s.denied[0]
    assert s.final_text == final and s.cost_usd == pytest.approx(0.1234)
    assert s.num_turns == 4 and s.total_tokens == 3150 and s.bad_lines == 1
    assert s.mcp_servers[0]["status"] == "connected"
    g = grading.grade(task, s.final_text, s.tool_numbers)
    assert g.passed
    trace = {a.key: a.traceable for a in g.answers}
    assert trace["flow"] is True and trace["head"] is True
    assert trace["speed"] is False  # never appeared in a tool result
    assert trace["inlet_used"] is None  # not a number
    # A string tool result is read too; without a result message the last text is final.
    lines = [ln for ln in _stream("speed 0.8479", final).splitlines() if '"result"' not in ln]
    s2 = grading.parse_stream(lines)
    assert s2.final_text == final and s2.cost_usd is None and s2.tool_numbers == [0.8479]


def test_grade_run_and_report_end_to_end(task: Any, tmp_path: Path) -> None:
    run_dir = tmp_path / "run-x"
    exp = task.expected
    cases = {
        ("mcp", 1): (exp, 0.10),
        ("mcp", 2): (dict(exp, flow=exp["flow"] * 2), 0.20),
        ("code", 1): (dict(exp, limit="none"), 0.30),
    }
    for (cond, rep), (values, _) in cases.items():
        d = run_dir / task.id / f"{cond}-{rep}"
        d.mkdir(parents=True)
        (d / "stream.jsonl").write_text(_stream(f"flow {exp['flow']}", _reply(values)), "utf-8")
        (d / "outcome.json").write_text(json.dumps({"exit_code": 0, "timed_out": False,
                                                    "wall_s": 12.0}), "utf-8")  # fmt: skip
        rec = cli.grade_run(task, d)
        assert rec["condition"] == cond and rec["repeat"] == rep
        assert (d / "record.json").exists() and (d / "grade.json").exists()
    summary = report.write_report(run_dir)
    assert summary["conditions"]["mcp"]["passed"] == 1 and summary["conditions"]["mcp"]["runs"] == 2
    assert summary["conditions"]["code"]["pass_rate"] == 0.0
    assert summary["by_level"]["mcp"]["1"]["pass_rate"] == 0.5
    assert summary["traceability"]["mcp"]["numeric_answers"] == 10
    assert summary["usage"]["mcp"]["median_tool_calls"] == 2
    assert summary["usage"]["mcp"]["total_cost_usd"] == pytest.approx(0.2468)
    gate = summary["decision_gate"]
    assert gate["runs"] == 2 and gate["pass_rate"] == 0.5 and gate["met"] is False
    md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "Decision gate: level-1 pass rate for condition mcp is 50 % (1/2" in md
    assert "versus the 80 % threshold: NOT MET" in md
    assert f"| {task.id} | 1 | what_if | pumping |" in md
    assert json.loads((run_dir / "summary.json").read_text("utf-8"))["runs"] == 3
    # The grade command re-grades from the saved streams.
    assert cli.main(["grade", str(run_dir), "--tasks-dir", str(FIXTURES)]) == 0


def test_report_gate_and_intervals() -> None:
    lo, hi = report.wilson(8, 10)  # type: ignore[misc]
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.95
    assert report.wilson(0, 0) is None
    base = {"level": 1, "category": "sizing", "domain": "pumping", "repeat": 1,
            "grade": {"answers": [], "format_issues": [], "parse_error": None},
            "stream": {}, "outcome": {}}  # fmt: skip
    recs = [
        {**copy.deepcopy(base), "task": f"t{i}", "condition": "mcp", "passed": i < 9}
        for i in range(10)
    ]
    s = report.summarise(recs, "r")
    assert s["decision_gate"]["met"] is True and "MET" in report.gate_line(s)
    s = report.summarise([{**r, "level": 2} for r in recs], "r")
    assert "not evaluated" in report.gate_line(s)


# ----------------------------------------------------------------------------------------
# runner: command lines, isolation, dry run
# ----------------------------------------------------------------------------------------
def test_claude_command_lines(tmp_path: Path) -> None:
    mcp = runner.build_command("mcp", "opus", tmp_path / "m.json", tmp_path / "s.json", 40)
    code = runner.build_command(
        "code", "opus", tmp_path / "m.json", tmp_path / "s.json", 40, max_budget_usd=2.5
    )
    for cmd in (mcp, code):
        assert cmd[1:8] == ["-p", "--output-format", "stream-json", "--verbose", "--model",
                            "opus", "--max-turns"]  # fmt: skip
        i = cmd.index("--setting-sources")
        assert cmd[i + 1] == ""
        for flag in ("--strict-mcp-config", "--disable-slash-commands",
                     "--no-session-persistence", "--no-chrome"):  # fmt: skip
            assert flag in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
        assert cmd.index("--allowedTools") > cmd.index("--tools")  # variadic flag last
    assert mcp[mcp.index("--tools") + 1] == ""
    assert mcp[mcp.index("--allowedTools") + 1 :] == ["mcp__worldparts"]
    assert code[code.index("--tools") + 1] == "Bash,Read,Write,Edit"
    assert code[code.index("--allowedTools") + 1 :] == [
        "Bash(python *)",
        "Read(./**)",
        "Write(./**)",
        "Edit(./**)",
    ]
    assert code[code.index("--max-budget-usd") + 1] == "2.5"
    with pytest.raises(ValueError):
        runner.build_command("both", "opus", tmp_path, tmp_path, 1)


def test_mcp_config_loads_only_worldparts_with_autosave(tmp_path: Path) -> None:
    cfg = runner.mcp_config("mcp", tmp_path / "systems")
    assert list(cfg["mcpServers"]) == ["worldparts"]
    server = cfg["mcpServers"]["worldparts"]
    assert server["command"] == "uv"
    assert server["args"] == ["run", "--directory", REPO.as_posix(), "worldparts", "mcp"]
    assert server["env"] == {"WORLDPARTS_AUTOSAVE_DIR": str(tmp_path / "systems")}
    assert runner.mcp_config("code", tmp_path) == {"mcpServers": {}}


def test_child_environment_is_scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    venv_bin = str(REPO / ".venv" / "Scripts")
    monkeypatch.setenv("PATH", os.pathsep.join([venv_bin, str(tmp_path / "bin")]))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk")
    monkeypatch.setenv("ANTHROPIC_MODEL", "something-else")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VIRTUAL_ENV", str(REPO / ".venv"))
    monkeypatch.setenv("KEEP_ME", "yes")
    env, changed = runner.child_env("mcp", None)
    for gone in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
                 "ANTHROPIC_MODEL", "VIRTUAL_ENV"):  # fmt: skip
        assert gone not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-test" and env["KEEP_ME"] == "yes"
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1" and env["ENABLE_TOOL_SEARCH"] == "false"
    path_key = next(k for k in env if k.upper() == "PATH")
    assert venv_bin not in env[path_key].split(os.pathsep)
    code_env, code_changed = runner.child_env("code", tmp_path / "code-env")
    first = code_env[path_key].split(os.pathsep)[0]
    assert first.startswith(str(tmp_path / "code-env"))
    assert code_env["PIP_NO_INDEX"] == "1" and "VIRTUAL_ENV" in code_changed
    assert "PATH" not in changed


def test_prepare_run_uses_a_temp_dir_outside_the_repo(tmp_path: Path) -> None:
    spec = runner.RunSpec("t", "mcp", 1, "hello", tmp_path / "out")
    cmd, _, _, tmp = runner.prepare_run(spec, "opus", 3, None)
    try:
        assert REPO.resolve() not in tmp.resolve().parents
        assert (tmp / "work").is_dir() and (tmp / "systems").is_dir()
        cfg = json.loads((tmp / "mcp.json").read_text(encoding="utf-8"))
        assert cfg["mcpServers"]["worldparts"]["env"]["WORLDPARTS_AUTOSAVE_DIR"] == str(
            tmp / "systems"
        )
        assert json.loads((tmp / "settings.json").read_text("utf-8")) == {"disableAllHooks": True}
        assert cmd[cmd.index("--mcp-config") + 1] == str(tmp / "mcp.json")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_dry_run_prints_commands_and_prompts_without_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry run must not start a process")

    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    rc = cli.main(["run", "--dry-run", "--model", "sonnet", "--tasks-dir", str(FIXTURES),
                   "--repeats", "2", "--run-id", "dry"])  # fmt: skip
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("claude -p --output-format stream-json --verbose --model sonnet") == 4
    assert "--allowedTools mcp__worldparts" in out
    assert "'Bash(python *)'" in out
    assert "When you are done, end your reply with a single JSON object" in out
    assert "4 session(s); nothing was run (--dry-run)" in out
    assert not (REPO / "benchmarks" / "composition" / "results" / "dry").exists()


AUTH_FAILURE_STREAM = "\n".join(
    json.dumps(m)
    for m in [
        {"type": "system", "subtype": "init", "model": "claude-haiku-4-5", "tools": [],
         "mcp_servers": [], "apiKeySource": "none"},
        {"type": "system", "subtype": "api_retry", "attempt": 1, "error_status": 401,
         "error": "authentication_failed"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Not logged in · Please run /login"}]}},
        {"type": "result", "subtype": "success", "is_error": True, "api_error_status": None,
         "result": "Not logged in · Please run /login", "num_turns": 1, "total_cost_usd": 0,
         "usage": {"input_tokens": 0, "output_tokens": 0}},
    ]
)  # fmt: skip


def test_infrastructure_errors_are_detected_and_excluded(task: Any, tmp_path: Path) -> None:
    s = grading.parse_stream(AUTH_FAILURE_STREAM)
    assert s.api_errors == ["401 authentication_failed"]
    assert "authentication failed" in (grading.infra_error(s, {"exit_code": 1}) or "")
    assert grading.infra_error(grading.parse_stream(""), {"exit_code": 1}) == (
        "no output from the CLI (exit code 1)"
    )
    assert "could not start" in (grading.infra_error(s, {"error": "could not start claude"}))
    ok = grading.parse_stream(_stream("x", _reply(task.expected)))
    assert grading.infra_error(ok, {"exit_code": 0}) is None
    # A timeout is the agent's failure, not the infrastructure's.
    assert grading.infra_error(grading.parse_stream(""), {"timed_out": True}) is None

    run_dir = tmp_path / "run-infra"
    for name, text in (("mcp-1", AUTH_FAILURE_STREAM), ("mcp-2", _stream("x", "no answer"))):
        d = run_dir / task.id / name
        d.mkdir(parents=True)
        (d / "stream.jsonl").write_text(text, encoding="utf-8")
        cli.grade_run(task, d)
    summary = report.write_report(run_dir)
    assert summary["runs"] == 1 and summary["conditions"]["mcp"]["runs"] == 1
    assert [e["repeat"] for e in summary["infra_errors"]] == [1]
    md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "## Infrastructure errors (excluded)" in md and "authentication failed" in md


def test_load_tasks_defaults_to_the_benchmark_task_directory() -> None:
    from benchmarks.composition.harness.tasks import load_tasks

    tasks, errors = load_tasks(None, ["pump-lift-*"])
    assert [t.id for t in tasks] == ["pump-lift-01"] and errors == []
    assert task_files(None) == task_files(TASKS_DIR)


# ----------------------------------------------------------------------------------------
# contamination and prompt-constant traceability
# ----------------------------------------------------------------------------------------
def _tool_stream(tool_input: dict[str, Any], tool_result: str, final: str) -> str:
    msgs = [
        {"type": "system", "subtype": "init", "model": "claude-test", "tools": ["Bash"]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": tool_input}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": tool_result}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": final,
         "num_turns": 2, "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]  # fmt: skip
    return "\n".join(json.dumps(m) for m in msgs) + "\n"


def test_contamination_flags_a_look_at_the_benchmark(task: Any) -> None:
    final = _reply(task.expected)
    windows = str(REPO)  # e.g. C:\Users\...\worldparts
    posix = REPO.as_posix()
    git_bash = "/" + posix[0].lower() + posix[2:] if posix[1:2] == ":" else posix
    cases = {
        "repo path": ({"command": f"type {windows}\\README.md"}, "x"),
        "git-bash path": ({"command": f"ls {git_bash}"}, "README.md"),
        "task dir": ({"command": "python -c \"import glob; print(glob.glob('**/*.yaml'))\""},
                     r"benchmarks\composition\tasks\lift-fixture-01.yaml"),
        "expected block": ({"command": "python solve.py"},
                           "reference:\n  expected: {flow: 18.6, head: 30.1}"),
        "library": ({"command": "python -c \"import worldparts\""}, "ModuleNotFoundError"),
    }  # fmt: skip
    for name, (tool_input, result) in cases.items():
        s = grading.parse_stream(_tool_stream(tool_input, result, final))
        reasons = grading.contamination(s, "code", task.id)
        assert reasons, name
    # The task file name alone is enough.
    s = grading.parse_stream(_tool_stream({"command": "type lift-fixture-01.yaml"}, "", final))
    assert any("task file" in r for r in grading.contamination(s, "code", task.id))


def test_contamination_ignores_ordinary_work(task: Any) -> None:
    final = _reply(task.expected)
    code_env = r"C:\Users\x\AppData\Local\worldparts-bench\code-env\Lib\site-packages"
    clean = [
        ({"command": "python solve.py"}, f'File "{code_env}\\scipy\\optimize\\_zeros.py", ...'),
        ({"command": "python -c \"print({'expected': 3})\""}, "{'expected': 3}\nexpected: 18"),
        ({"file_path": r"C:\Temp\wpbench-code-abc\work\solve.py"}, "flow = 18.6 m3/h"),
    ]
    for tool_input, result in clean:
        s = grading.parse_stream(_tool_stream(tool_input, result, final))
        assert grading.contamination(s, "code", task.id) == [], (tool_input, result)
    # The MCP condition talks to worldparts by design, and its messages may carry paths.
    s = grading.parse_stream(
        _tool_stream({"system": "s1"}, f"worldparts error in {REPO / 'src' / 'x.py'}", final)
    )
    assert grading.contamination(s, "mcp", task.id) == []
    assert grading.contamination(s, "code", task.id) != []


def test_contaminated_runs_are_excluded_from_the_report(task: Any, tmp_path: Path) -> None:
    run_dir = tmp_path / "run-tainted"
    final = _reply(task.expected)
    streams = {
        "code-1": _tool_stream({"command": r"type C:\bench\composition\tasks\x.yaml"},
                               "expected: {flow: 1.0}", final),
        "code-2": _tool_stream({"command": "python solve.py"}, f"flow {task.expected['flow']}",
                               final),
    }  # fmt: skip
    for name, text in streams.items():
        d = run_dir / task.id / name
        d.mkdir(parents=True)
        (d / "stream.jsonl").write_text(text, encoding="utf-8")
        rec = cli.grade_run(task, d)
        assert bool(rec["contamination"]) is (name == "code-1")
    summary = report.write_report(run_dir)
    assert summary["runs"] == 1 and summary["conditions"]["code"]["runs"] == 1
    assert [(e["repeat"], e["passed"]) for e in summary["contaminated"]] == [(1, True)]
    md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "## Contaminated runs (excluded)" in md and "composition/tasks" in md
    assert "1 contaminated run(s) are excluded" in md


def test_answers_equal_to_prompt_constants_are_marked(task: Any) -> None:
    exp = dict(task.expected)
    g = grading.grade(task, _reply(dict(exp, flow=24.0)), tool_numbers=[])
    marks = {a.key: (a.traceable, a.in_prompt) for a in g.answers}
    assert marks["flow"] == (False, True)  # 24 m3/h is a number in the prompt
    assert marks["head"] == (False, False)
    assert marks["limit"] == (None, None)
    oracle = grading.grade(task, _reply(exp), tool_numbers=None)
    assert all(a.in_prompt is None for a in oracle.answers)
    rec = {
        "task": task.id,
        "level": 1,
        "category": "c",
        "domain": "d",
        "condition": "code",
        "repeat": 1,
        "passed": g.passed,
        "grade": g.to_dict(),
        "stream": {},
        "outcome": {},
    }
    tr = report.summarise([rec], "r")["traceability"]["code"]  # fmt: skip
    assert tr["in_prompt"] == 1
    assert tr["correct_untraceable_not_in_prompt"] == sum(
        1 for a in g.answers if a.kind == "number" and a.passed and not a.in_prompt
    )


def test_session_that_started_before_mcp_connected_is_an_infrastructure_error() -> None:
    """A pending MCP server at session start means the agent had no tools: not its failure."""
    from benchmarks.composition.harness.grading import StreamSummary, infra_error

    s = StreamSummary()
    s.mcp_servers = [{"name": "worldparts", "status": "pending"}]
    s.final_text = '```json\n{"n": 23}\n```'
    s.usage = {"input_tokens": 3000, "output_tokens": 690}
    s.result_subtype = "success"
    s.last_assistant_text = s.final_text
    assert "not connected" in (infra_error(s, {}) or "")
    s.mcp_servers = [{"name": "worldparts", "status": "connected"}]
    assert infra_error(s, {}) is None


def test_child_env_drops_nonblocking_mcp_and_parent_base_url(monkeypatch) -> None:
    """The child CLI must wait for the worldparts server and use its own login endpoint."""
    from benchmarks.composition.harness import runner

    monkeypatch.setenv("MCP_CONNECTION_NONBLOCKING", "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    env, _ = runner.child_env("mcp", None)
    assert "MCP_CONNECTION_NONBLOCKING" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    cfg = runner.mcp_config("mcp", runner.REPO_ROOT / "tmp-systems")
    assert cfg["mcpServers"][runner.MCP_SERVER_NAME]["alwaysLoad"] is True
