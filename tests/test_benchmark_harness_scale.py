"""Tests of the benchmark harness's scale level (level 4: 20 or more components).

Covers the level rule (schema enum, loader bounds and labels), the level-4 prompt rules
(up to 1,200 words, markdown tables allowed and checked; levels 1-3 keep their rules and
contain no table), the per-level session limits of the runner (command line, recommended
table, precedence, dry run, run and outcome records) and the report's per-level effort
table. Nothing here starts a Claude session.
"""

from __future__ import annotations

import copy
import io
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import worldparts as wp

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.composition.harness import __main__ as cli  # noqa: E402
from benchmarks.composition.harness import report, runner  # noqa: E402
from benchmarks.composition.harness.reference import (  # noqa: E402
    ReferenceStepError,
    run_reference,
    time_integral,
)
from benchmarks.composition.harness.tasks import (  # noqa: E402
    JUDGEMENT_CHOICES,
    LEVEL_BOUNDS,
    SCALE_LEVEL,
    SCALE_PROMPT_WORDS,
    check_task,
    judgement_word_bounds,
    level_label,
    load_task,
    markdown_tables,
    prompt_word_count,
    table_problems,
    task_from_dict,
    task_schema,
)

FIXTURES = Path(__file__).parent / "fixtures" / "benchmark" / "tasks"
FIXTURE = FIXTURES / "lift-fixture-01.yaml"
SCALE_PATH = Path("scale-fixture-01.yaml")

#: 17 riser segments: 16 of 3.5 m rising 1.5 m and a last one of 4 m rising 1 m with the
#: exit loss, so 60 m rising 25 m with K = 1 in all, the lift fixture's single riser.
SEGMENTS = [(3.5, 1.5, 0)] * 16 + [(4.0, 1.0, 1)]

SEGMENT_TABLE = "\n".join(
    [
        "| Segment | Length (m) | Rise (m) | Minor loss K |",
        "|---|---:|---:|---:|",
        *(f"| S{i} | {length:g} | {rise:g} | {k:g} |" for i, (length, rise, k) in
          enumerate(SEGMENTS, start=1)),
    ]
)  # fmt: skip

SCALE_PROMPT = (
    "A pump draws water from the bottom outlet of an open cylindrical ground tank (2.0 m\n"
    "diameter, 3.0 m high, water 1.5 m deep; outlet nozzle Kv 200 m3/h) and lifts it through\n"
    "a riser of 17 steel pipe segments in series (each with inner diameter 50 mm and\n"
    "roughness 0.045 mm) to a free discharge. The segments, from the pump upwards:\n"
    "\n" + SEGMENT_TABLE + "\n"
    "\n"
    "Pump head at rated speed: 44 m at 0 m3/h, 43 m at 6 m3/h, 40 m at 12 m3/h, 35 m at\n"
    "18 m3/h and 28 m at 24 m3/h; fit a quadratic head-flow curve through these points and\n"
    "scale it with the affinity laws for other speeds. Water: density 998.2 kg/m3,\n"
    "viscosity 1.002e-3 Pa s. (1) Find the flow and head at full speed. (2) Find the\n"
    "relative speed that gives 12 m3/h. (3) Back at full speed, pumping from the full 1.5 m\n"
    "depth, when does the tank run dry, and what level is left after 40 minutes? (4) Is the\n"
    "tank's inflow nozzle used?\n"
)


def fixture_data() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def scale_data() -> dict[str, Any]:
    """The lift fixture as a level-4 task: its riser split into 17 segments (20 components)."""
    d = fixture_data()
    d["id"] = "scale-fixture-01"
    d["title"] = "Ground tank to roof lift through a segmented riser (harness fixture)"
    d["level"] = SCALE_LEVEL
    d["prompt"] = SCALE_PROMPT
    system = d["reference"]["system"]
    tank, pump, _, roof = system["components"]
    names = [f"seg{i:02d}" for i in range(1, len(SEGMENTS) + 1)]
    pipes = [
        {
            "name": name,
            "type": "pipe",
            "parameters": {
                "length": length,
                "diameter": 50,
                "roughness": 0.045,
                "height_difference": rise,
                "minor_loss": k,
            },
        }
        for name, (length, rise, k) in zip(names, SEGMENTS, strict=True)
    ]
    system["components"] = [tank, pump, *pipes, roof]
    ports = ["pump.outlet", *(p for n in names for p in (f"{n}.port_a", f"{n}.port_b"))]
    ports.append("roof.port")
    system["connections"] = [["tank.outlet", "pump.inlet"]] + [
        [ports[i], ports[i + 1]] for i in range(0, len(ports), 2)
    ]
    d["reference"]["expected"] = {}
    return d


def _problems(data: dict[str, Any]) -> list[str]:
    return check_task(data, SCALE_PATH)


def _with_components(data: dict[str, Any], n: int) -> dict[str, Any]:
    """``data`` with its reference system cut or padded (with extra pipes) to n components."""
    d = copy.deepcopy(data)
    comps = d["reference"]["system"]["components"]
    extra = [
        {"name": f"spare{i}", "type": "pipe", "parameters": {"length": 1, "diameter": 50}}
        for i in range(max(0, n - len(comps)))
    ]
    d["reference"]["system"]["components"] = (comps + extra)[:n]
    return d


# ----------------------------------------------------------------------------------------
# the level rule
# ----------------------------------------------------------------------------------------
def test_level_four_is_in_the_schema_and_the_bounds_cover_every_count() -> None:
    assert task_schema()["properties"]["level"]["enum"] == [1, 2, 3, 4] == list(LEVEL_BOUNDS)
    assert SCALE_LEVEL == 4
    assert LEVEL_BOUNDS == {1: (2, 4), 2: (5, 7), 3: (8, 19), 4: (20, 10**9)}
    for n in range(2, 200):  # every component count has exactly one level
        assert [lv for lv, (lo, hi) in LEVEL_BOUNDS.items() if lo <= n <= hi] == [
            1 if n <= 4 else 2 if n <= 7 else 3 if n <= 19 else 4
        ]
    assert level_label(3) == "3 (8-19 components)"
    assert level_label("4") == "4 (20+ components)"
    assert level_label("oracle") == "oracle"


def test_the_scale_fixture_is_a_valid_level_four_task() -> None:
    data = scale_data()
    assert len(data["reference"]["system"]["components"]) == 20
    assert _problems(data) == []
    task = task_from_dict(data, SCALE_PATH)
    assert task.level == 4 and task.component_count == 20 and task.expected is None


@pytest.mark.parametrize(
    ("level", "n", "needle"),
    [
        (4, 19, "level 4 needs 20 or more components, the reference system has 19"),
        (3, 20, "level 3 needs 8-19 components, the reference system has 20"),
        (3, 45, "level 3 needs 8-19 components"),
    ],
)
def test_the_loader_checks_the_level_against_the_component_count(
    level: int, n: int, needle: str
) -> None:
    data = _with_components(scale_data(), n)
    data["level"] = level
    if level != SCALE_LEVEL:
        data["prompt"] = fixture_data()["prompt"]  # no table below level 4
    assert any(needle in p for p in _problems(data)), _problems(data)
    ok = _with_components(scale_data(), 80)
    assert _problems(ok) == []


def test_levels_one_to_three_keep_their_rules() -> None:
    """Every existing level still loads with its own component range and prompt rules."""
    for level, n in ((1, 4), (2, 5), (2, 7), (3, 8), (3, 19)):
        data = _with_components(fixture_data(), n)
        data["level"] = level
        assert check_task(data, FIXTURE) == [], (level, n)


# ----------------------------------------------------------------------------------------
# level-4 prompts: length and markdown tables
# ----------------------------------------------------------------------------------------
def test_table_markup_is_not_counted_as_words() -> None:
    table = "| Node | Elevation (m) |\n|---|---:|\n| J1 | 12.5 |\n| J2 | 14 |"
    assert prompt_word_count(table) == 7  # Node, Elevation, (m), J1, 12.5, J2, 14
    assert prompt_word_count("Two words.\n" + table) == 9
    prose = fixture_data()["prompt"]
    assert prompt_word_count(prose) == len(prose.split())  # unchanged without tables
    assert markdown_tables("text\n" + table + "\nmore") == [(2, table.splitlines())]


def test_a_level_four_prompt_may_have_up_to_1200_words() -> None:
    data = scale_data()
    base = prompt_word_count(data["prompt"])
    assert 150 < base < 400
    data["prompt"] = SCALE_PROMPT + " filler" * (SCALE_PROMPT_WORDS - base)
    assert prompt_word_count(data["prompt"]) == SCALE_PROMPT_WORDS == 1200
    assert _problems(data) == []
    data["prompt"] += " one"
    assert any(
        "the prompt has 1201 words (a level-4 prompt may have at most 1200)" in p
        for p in _problems(data)
    ), _problems(data)


def test_tables_are_allowed_only_at_level_four() -> None:
    data = fixture_data()
    data["prompt"] = data["prompt"] + "\n" + SEGMENT_TABLE + "\n"
    problems = check_task(data, FIXTURE)
    assert any("tables are allowed only in level-4 prompts" in p for p in problems), problems
    # Levels 1-3 have no general word limit in the loader (judgement tasks keep theirs).
    long = fixture_data()
    long["prompt"] = long["prompt"] + " filler" * 1500
    assert check_task(long, FIXTURE) == []


@pytest.mark.parametrize(
    ("table", "needle"),
    [
        ("| A | B |\n| 1 | 2 |\n| 3 | 4 |", "second row must be a delimiter row"),
        ("| A | B |\n|---|---|", "needs a header row, a delimiter row and a body row"),
        ("| A | B |\n|---|---|\n| 1 | 2 | 3 |", "has 3 cells, the header 2"),
        ("| A | B |\n|---|---|\n| 1 |  |", "has an empty cell"),
        ("| A | B |\n|---|---|\n| 1 | 2", "must start and end with |"),
        ("| A | B |\n|---|---|---|\n| 1 | 2 |", "has 3 cells, the header 2"),
        ("| A |  |\n|---|---|\n| 1 | 2 |", "has an empty cell"),
    ],
)
def test_malformed_tables_are_rejected(table: str, needle: str) -> None:
    assert any(needle in p for p in table_problems(table)), table_problems(table)
    data = scale_data()
    data["prompt"] = SCALE_PROMPT + "\nAlso:\n\n" + table + "\n"
    assert any(needle in p for p in _problems(data)), _problems(data)


def test_well_formed_tables_pass() -> None:
    assert table_problems(SEGMENT_TABLE) == []
    aligned = "  | Hour | Demand factor |\n  | :--- | :---: |\n  | 0-6 | 0.6 |\n"
    assert table_problems(aligned) == []
    assert table_problems("No table here | only a pipe in prose.") == []


@pytest.mark.parametrize(
    ("addition", "needle"),
    [
        ("\n| Item | Value |\n|---|---|\n| centrifugal_pump | 1 |\n", "'centrifugal_pump'"),
        ("\n| Item | Value |\n|---|---|\n| tank_empty | 1 |\n", "'tank_empty'"),
        ("\nModel it with worldparts.\n", "'worldparts'"),
        ("\nReply in JSON.\n", "answer format"),
        ("\n```\nflow = 1\n```\n", "answer format"),
    ],
)
def test_the_other_prompt_rules_still_apply_at_level_four(addition: str, needle: str) -> None:
    data = scale_data()
    data["prompt"] = SCALE_PROMPT + addition
    assert any(needle in p for p in _problems(data)), _problems(data)


def _scale_judgement(prompt: str, level: int = SCALE_LEVEL) -> dict[str, Any]:
    d = scale_data() if level == SCALE_LEVEL else fixture_data()
    d["level"] = level
    d["category"] = "judgement"
    d["prompt"] = prompt
    d["answers"] = [
        {"key": "acceptable", "description": "Whether the design is acceptable", "kind": "boolean"},
        {
            "key": "primary_problem",
            "description": "The primary problem, or none",
            "kind": "choice",
            "choices": list(JUDGEMENT_CHOICES),
        },
    ]
    d["reference"]["steps"] = [
        {"op": "simulate", "duration": "30 min", "step": "10 s"},
        {"op": "answer", "values": {"acceptable": False, "primary_problem": "tank_runs_dry"}},
    ]
    d["reference"]["expected"] = {}
    return d


def test_judgement_word_limits_by_level() -> None:
    assert judgement_word_bounds(1) == judgement_word_bounds(3) == (100, 250)
    assert judgement_word_bounds(4) == (100, 1200)
    review = (
        " Review this design. Is it acceptable for continuous duty as specified, and what is "
        "its primary problem, if any?"
    )
    body = SCALE_PROMPT.split("(1)")[0].rstrip()
    prompt = body + " The data above are complete." * 10 + review
    words = prompt_word_count(prompt)
    assert 250 < words < 1200
    assert _problems(_scale_judgement(prompt)) == []
    # The same length at level 3 breaks the 100-250 rule (and the table rule).
    level3 = _with_components(_scale_judgement(prompt, level=3), 8)
    problems = check_task(level3, FIXTURE)
    assert any(f"{words} words (must be 100-250)" in p for p in problems), problems
    too_long = prompt + " more" * (1201 - words)
    problems = _problems(_scale_judgement(too_long))
    assert any("(must be 100-1200)" in p for p in problems), problems
    # The hint rules are unchanged at level 4, tables included.
    hinted = body + "\n\n| Check | Value |\n|---|---|\n| NPSH margin | 1 m |\n" + review
    problems = _problems(_scale_judgement(hinted))
    assert any("('margin')" in p for p in problems), problems


# ----------------------------------------------------------------------------------------
# a level-4 system through the reference executor, regen and the oracle
# ----------------------------------------------------------------------------------------
def test_a_level_four_reference_runs_and_the_oracle_grades_it(tmp_path: Path) -> None:
    fixture = load_task(FIXTURE)
    assert fixture.expected is not None
    scale = task_from_dict(scale_data(), SCALE_PATH)
    produced = run_reference(scale)
    # The segmented riser is hydraulically the fixture's single riser.
    for key in ("flow", "head", "speed"):
        assert produced[key] == pytest.approx(fixture.expected[key], rel=1e-4), key
    assert produced["limit"] == "tank_runs_dry" and produced["inlet_used"] is False

    data = scale_data()
    data["reference"]["expected"] = produced
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "scale-fixture-01.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    assert cli.main(["regen", "--check", "--tasks-dir", str(tasks_dir)]) == 0
    assert cli.main(["validate", "--tasks-dir", str(tasks_dir)]) == 0
    task = load_task(tasks_dir / "scale-fixture-01.yaml")
    assert [r["passed"] for r in cli.oracle_records([task])] == [True]
    assert [r["passed"] for r in cli.oracle_records([task], corrupt=True)] == [False]


# ----------------------------------------------------------------------------------------
# runner: per-level session limits
# ----------------------------------------------------------------------------------------
def test_default_limits_are_unchanged() -> None:
    defaults = runner.DEFAULT_LIMITS
    assert defaults == runner.SessionLimits(max_turns=80, timeout_s=1800.0)
    args = cli.parser().parse_args(["run"])
    assert (args.max_turns, args.timeout) == (80, 1800.0)
    assert args.level_max_turns is None and args.level_timeout is None
    assert args.recommended_level_limits is False
    smoke = cli.parser().parse_args(["smoke", "--condition", "mcp", "--prompt", "x"])
    assert (smoke.max_turns, smoke.timeout) == (2, 1800.0)
    for level in LEVEL_BOUNDS:  # no override, no recommendation: the global limits
        assert cli._limits(level, args) == runner.DEFAULT_LIMITS


def test_recommended_level_four_limits() -> None:
    recommended = runner.RECOMMENDED_LEVEL_LIMITS
    assert recommended == {4: runner.SessionLimits(max_turns=120, timeout_s=2400.0)}


def test_parse_level_values() -> None:
    parse = runner.parse_level_values
    assert parse(None, "--x", integer=True) == {}
    assert parse(["4=120"], "--x", integer=True) == {4: 120}
    assert parse(["4=120,3=100", "2=90"], "--x", integer=True) == {4: 120, 3: 100, 2: 90}
    assert parse(["4=2400.5"], "--x", integer=False) == {4: 2400.5}
    for bad, needle in (
        (["4"], "is not LEVEL=VALUE"),
        (["four=1"], "is not LEVEL=VALUE"),
        (["4=1.5"], "with an integer VALUE"),
        (["5=100"], "unknown level 5 (levels are 1, 2, 3, 4)"),
        (["4=0"], "must be positive"),
        (["4=-3"], "must be positive"),
    ):
        with pytest.raises(ValueError, match=r"^--x: ") as err:
            parse(bad, "--x", integer=True)
        assert needle in str(err.value)
    with pytest.raises(ValueError, match="positive and finite"):
        parse(["4=inf"], "--level-timeout", integer=False)


def test_session_limit_precedence() -> None:
    base = runner.SessionLimits(max_turns=60, timeout_s=900.0)
    limits = runner.session_limits
    assert limits(None, base, {4: 1}, {4: 1.0}, recommended=True) == base  # smoke: no level
    assert limits(4, base) == base
    assert limits(4, base, recommended=True) == runner.SessionLimits(120, 2400.0)
    assert limits(2, base, recommended=True) == base  # no recommendation below level 4
    assert limits(4, base, {4: 150}, recommended=True) == runner.SessionLimits(150, 2400.0)
    assert limits(4, base, None, {4: 3000}, recommended=True) == runner.SessionLimits(120, 3000.0)
    assert limits(3, base, {4: 150}, {3: 1200}) == runner.SessionLimits(60, 1200.0)


def _scale_tasks_dir(tmp_path: Path, with_expected: bool = False) -> Path:
    """A task directory with the level-1 lift fixture and the level-4 scale fixture."""
    d = tmp_path / "tasks"
    d.mkdir()
    shutil.copy(FIXTURE, d / FIXTURE.name)
    data = scale_data()
    if with_expected:
        data["reference"]["expected"] = run_reference(task_from_dict(data, SCALE_PATH))
    (d / "scale-fixture-01.yaml").write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    return d


def _no_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry run must not start a process")

    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)


def test_dry_run_prints_the_limits_of_each_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_processes(monkeypatch)
    tasks = _scale_tasks_dir(tmp_path)
    argv = ["run", "--dry-run", "--tasks-dir", str(tasks), "--conditions", "mcp"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "limits: level 1, max-turns 80, timeout 1800 s" in out
    assert "limits: level 4, max-turns 80, timeout 1800 s" in out  # defaults unchanged

    assert cli.main([*argv, "--level-max-turns", "4=120", "--level-timeout", "4=2400"]) == 0
    out = capsys.readouterr().out
    assert "limits: level 1, max-turns 80, timeout 1800 s" in out
    assert "limits: level 4, max-turns 120, timeout 2400 s" in out
    assert out.count("--max-turns 120") == 1 and out.count("--max-turns 80") == 1
    assert "| Segment | Length (m) | Rise (m) | Minor loss K |" in out  # the table reaches it

    assert cli.main([*argv, "--recommended-level-limits", "--max-turns", "50"]) == 0
    out = capsys.readouterr().out
    assert "limits: level 1, max-turns 50, timeout 1800 s" in out
    assert "limits: level 4, max-turns 120, timeout 2400 s" in out

    with pytest.raises(SystemExit, match="--level-timeout: unknown level 7"):
        cli.main([*argv, "--level-timeout", "7=10"])


def test_run_passes_per_level_limits_and_records_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    tasks = _scale_tasks_dir(tmp_path, with_expected=True)
    results = tmp_path / "results"
    monkeypatch.setattr(cli, "RESULTS_DIR", results)
    monkeypatch.setattr(cli, "claude_version", lambda: "test")
    seen: dict[str, tuple[int, float]] = {}

    def fake_execute(spec: Any, model: str, max_turns: int, timeout_s: float, *a: Any,
                     **k: Any) -> runner.RunOutcome:  # fmt: skip
        seen[spec.label] = (max_turns, timeout_s)
        spec.out_dir.mkdir(parents=True, exist_ok=True)
        (spec.out_dir / "stream.jsonl").write_text("", encoding="utf-8")
        outcome = {"exit_code": 0, "timed_out": False, "wall_s": 1.0,
                   "max_turns": max_turns, "timeout_s": timeout_s}  # fmt: skip
        (spec.out_dir / "outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
        return runner.RunOutcome(0, False, 1.0)

    monkeypatch.setattr(cli, "execute", fake_execute)
    argv = ["run", "--tasks-dir", str(tasks), "--conditions", "mcp", "--run-id", "scale",
            "--recommended-level-limits", "--level-max-turns", "4=150"]  # fmt: skip
    assert cli.main(argv) == 0
    assert seen == {"lift-fixture-01": (80, 1800.0), "scale-fixture-01": (150, 2400.0)}
    meta = json.loads((results / "scale" / "run.json").read_text(encoding="utf-8"))
    assert (meta["max_turns"], meta["timeout_s"]) == (80, 1800.0)
    assert meta["level_limits"] == {
        "1": {"max_turns": 80, "timeout_s": 1800.0},
        "4": {"max_turns": 150, "timeout_s": 2400.0},
    }


def test_execute_records_the_limits_it_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    argvs: list[list[str]] = []

    class FakeProc:
        pid = 0

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            argvs.append(cmd)
            self.stdin = io.BytesIO()
            self.timeout: float | None = None

        def wait(self, timeout: float | None = None) -> int:
            self.timeout = timeout
            assert timeout == 2400.0
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProc)
    spec = runner.RunSpec("scale-fixture-01", "mcp", 1, "hello", tmp_path / "out")
    outcome = runner.execute(spec, "opus", 120, 2400.0, None)
    assert outcome.exit_code == 0 and not outcome.timed_out
    assert argvs[0][argvs[0].index("--max-turns") + 1] == "120"
    saved = json.loads((tmp_path / "out" / "outcome.json").read_text(encoding="utf-8"))
    assert saved["max_turns"] == 120 and saved["timeout_s"] == 2400.0
    assert saved["exit_code"] == 0 and saved["timed_out"] is False


# ----------------------------------------------------------------------------------------
# report: per-level effort
# ----------------------------------------------------------------------------------------
def _rec(task: str, level: int, cond: str, passed: bool, **stream_outcome: Any) -> dict[str, Any]:
    outcome = {k: v for k, v in stream_outcome.items() if k in ("timed_out", "max_turns",
                                                                 "timeout_s")}  # fmt: skip
    stream = {k: v for k, v in stream_outcome.items() if k not in outcome}
    return {
        "task": task, "level": level, "category": "operating_point", "domain": "distribution",
        "condition": cond, "repeat": 1, "passed": passed,
        "grade": {"answers": [], "format_issues": [], "parse_error": None},
        "stream": stream, "outcome": outcome,
    }  # fmt: skip


def test_report_breaks_effort_down_by_level() -> None:
    recs = [
        _rec("a", 1, "mcp", True, num_turns=4, cost_usd=0.1, max_turns=80, timeout_s=1800.0),
        _rec("a", 1, "code", True, num_turns=2, cost_usd=0.02, max_turns=80, timeout_s=1800.0),
        _rec("big", 4, "mcp", True, num_turns=30, cost_usd=1.0, duration_ms=600_000,
             max_turns=120, timeout_s=2400.0),
        _rec("big", 4, "code", False, num_turns=120, result_subtype="error_max_turns",
             cost_usd=2.0, max_turns=120, timeout_s=2400.0),
        _rec("big2", 4, "code", False, timed_out=True, max_turns=120, timeout_s=2400.0),
        _rec("old", 3, "code", True, num_turns=3),  # a v0.2 record: no limits recorded
    ]  # fmt: skip
    s = report.summarise(recs, "scale")
    big = s["usage_by_level"]["code"]["4"]
    assert (big["runs"], big["passed"], big["pass_rate"]) == (2, 0, 0.0)
    assert big["timeouts"] == 1 and big["turn_limit_hits"] == 1
    assert big["limits"] == {"max_turns": [120], "timeout_s": [2400.0]}
    assert s["usage_by_level"]["mcp"]["4"]["median_turns"] == 30
    assert s["usage_by_level"]["code"]["3"]["limits"] == {"max_turns": [], "timeout_s": []}
    assert s["usage"]["code"]["turn_limit_hits"] == 1
    assert s["by_level"]["code"]["4"]["pass_rate"] == 0.0
    assert s["decision_gate"]["runs"] == 1  # still level 1, condition mcp

    md = report.to_markdown(s)
    assert "## Effort and cost by level" in md
    assert "| 4 (20+ components) | code | 0 % (0/2) | 120.0 |" in md
    assert "| 1 | 1 | 120 turns, 2400 s |" in md  # timeouts, turn-limit hits, limits
    assert "| 3 (8-19 components) | code | 100 % (1/1) |" in md
    assert "| n/a turns, n/a s |" in md
    by_level = md.split("## Pass rate by level")[1].split("##")[0]
    rows = [ln.split(" |")[0] for ln in by_level.splitlines() if ln.startswith("| ")][1:]
    assert rows == ["| 1 (2-4 components)", "| 3 (8-19 components)", "| 4 (20+ components)"]
    effort = md.split("## Effort and cost by level")[1].split("\n## ")[0]
    assert effort.index("| 1 (2-4 components) | code") < effort.index("| 4 (20+ components) | code")


# ----------------------------------------------------------------------------------------
# read_total: time integrals over a simulation (pumped volumes and energies of scale tasks)
# ----------------------------------------------------------------------------------------
def _total_answers(reads: dict[str, Any], units: dict[str, str]) -> dict[str, Any]:
    """Run the lift fixture for 10 min at 10 s with ``read_total`` reads (and the level)."""
    data = fixture_data()
    data["answers"] = [
        {"key": key, "description": "x", "unit": units[key], "rel_tol": 0.05} for key in reads
    ] + [{"key": "level", "description": "x", "unit": "m", "rel_tol": 0.05}]
    data["reference"]["steps"] = [
        {"op": "simulate", "duration": "10 min", "step": "10 s", "read_total": reads,
         "read_final": {"level": "tank.level"}},
    ]  # fmt: skip
    data["reference"].pop("expected")
    return run_reference(task_from_dict(data, FIXTURE))


def test_read_total_of_a_tank_outflow_is_the_volume_the_tank_loses() -> None:
    got = _total_answers(
        {"pumped": "pump.volume_flow", "litres": "riser.volume_flow"},
        {"pumped": "m3", "litres": "L"},
    )
    area = math.pi * 2.0**2 / 4  # the fixture tank: 2 m diameter, 1.5 m of water
    assert got["pumped"] == pytest.approx(area * (1.5 - got["level"]), rel=1e-9)
    assert got["litres"] == pytest.approx(1000 * got["pumped"], rel=1e-9)
    assert 2.5 < got["pumped"] < 3.5  # about 19 m3/h for 10 min


def test_read_total_holds_each_sample_over_the_step_it_drives() -> None:
    """Left sums, in the answer unit; a list of paths adds their totals."""
    task = task_from_dict(fixture_data(), FIXTURE)
    sim = wp.System.from_dict(task.system).simulate("10 min", "10 s")
    t, p = sim.time, sim.get("pump.shaft_power", "kW")
    kwh = sum(p[k] * (t[k + 1] - t[k]) for k in range(len(t) - 1)) / 3600
    got = _total_answers(
        {"energy": "pump.shaft_power", "double": ["pump.volume_flow", "riser.volume_flow"],
         "single": "pump.volume_flow"},
        {"energy": "kWh", "double": "m3", "single": "m3"},
    )  # fmt: skip
    assert got["energy"] == pytest.approx(kwh, rel=1e-12)
    assert got["double"] == pytest.approx(2 * got["single"], rel=1e-9)
    assert time_integral(sim, "pump.volume_flow", None) == pytest.approx(
        got["single"] * 3600, rel=1e-9
    )  # no unit: the variable's unit (m3/h) times seconds


@pytest.mark.parametrize(
    ("reads", "unit", "needle"),
    [
        ({"x": "pump.volume_flow"}, "bar", "cannot be given in 'bar'"),
        ({"x": "pump.outlet.p"}, "bar*s", "integrates flows and powers"),
        ({"x": "tank.temperature"}, "K*s", "integrates flows and powers"),
        ({"x": ["pump.volume_flow", "nothing.volume_flow"]}, "m3", "nothing.volume_flow"),
    ],
)
def test_read_total_errors_name_the_step(reads: dict[str, Any], unit: str, needle: str) -> None:
    with pytest.raises(ReferenceStepError, match=r"steps\[0\] \(simulate\)") as err:
        _total_answers(reads, {"x": unit})
    assert needle in str(err.value)


def test_read_total_is_a_producing_read_in_the_schema_and_the_loader() -> None:
    data = fixture_data()
    sim = data["reference"]["steps"][3]
    sim["read_total"] = {"final_level": "pump.volume_flow"}  # final_level also read_final
    assert any("final_level is produced by 2 steps" in p for p in check_task(data, FIXTURE))
    for bad in (5, [], ["pump.volume_flow", "pump.volume_flow"], "pump"):
        sim["read_total"] = {"total": bad}
        assert any(p.startswith("schema:") for p in check_task(data, FIXTURE)), bad
    sim["read_total"] = {"total": ["pump.volume_flow", "riser.volume_flow"]}
    assert any("'total', which is not an answer key" in p for p in check_task(data, FIXTURE))
