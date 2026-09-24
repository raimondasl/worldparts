"""Tests of the operations-benchmark harness (benchmarks/operations/harness).

Covers the frozen checklist and preambles, bundle loading, the prompt, the claude command
line and session environment, the dry run, a whole Stage 0 run against a fake Claude
executable (tests/fixtures/opsbench/fake_claude.py: no model is ever called), the blind
audit and its re-runs, grading, the headroom rule, contamination markers, the manifest
and the code-plus environment builder. Synthetic bundles and truth only.
"""

from __future__ import annotations

import builtins
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.composition.harness.grading import StreamSummary  # noqa: E402
from benchmarks.operations.harness import __main__ as cli  # noqa: E402
from benchmarks.operations.harness import (  # noqa: E402
    arms,
    bundles,
    env,
    headroom,
    infra,
    markers,
    report,
    runner,
)

OPS = REPO / "benchmarks" / "operations"
PREREG = (OPS / "PREREGISTRATION.md").read_text(encoding="utf-8")
FAKE = Path(__file__).parent / "fixtures" / "opsbench" / "fake_claude.py"


# ----------------------------------------------------------------------------------------
# the checklist and the preambles
# ----------------------------------------------------------------------------------------
def test_checklist_is_the_preregistered_text_verbatim() -> None:
    section = PREREG.split("### 3.1 Methods checklist (verbatim)", 1)[1]
    fenced = re.search(r"```text\n(.*?)\n```", section, re.S)
    assert fenced is not None
    text = (OPS / "preambles" / "checklist.txt").read_text(encoding="utf-8")
    assert text == fenced.group(1) + "\n"
    assert arms.read_preamble_file("checklist.txt") == fenced.group(1)
    assert 20 <= len(text.splitlines()) <= 30


def test_checklist_names_neither_worldparts_nor_its_functions() -> None:
    """Section 3.1: the checklist names neither worldparts nor its functions. Item 7 of the
    frozen text says "Check identifiability ...", the method, in plain English; the
    function names are therefore checked as identifiers (a call, an attribute, code
    quotes or an argument), and the others as whole words as well."""
    text = (OPS / "preambles" / "checklist.txt").read_text(encoding="utf-8")
    assert not re.search(r"worldparts", text, re.I)
    assert "load_measurements" not in text
    assert not re.search(r"\bSystem\b", text)  # the class; case-sensitive
    for name in ("calibrate", "diagnose"):
        assert not re.search(rf"\b{name}\b", text, re.I), name
    for name in ("calibrate", "identifiability", "diagnose", "load_measurements", "System"):
        assert not re.search(rf"(?:\.|`|\bwp\.){name}\b|\b{name}\s*[(=`]", text), name


def test_preambles_differ_only_by_the_checklist() -> None:
    plus, hint = arms.preamble("code+"), arms.preamble("code-hint")
    checklist = arms.read_preamble_file("checklist.txt")
    assert hint == plus + "\n\n" + checklist
    for name in ("numpy", "scipy", "pandas", "statsmodels", "scikit-learn", "lmfit",
                 "fluids", "wntr", "matplotlib", "Agg"):  # fmt: skip
        assert name in plus
    assert not re.search(r"worldparts|opsim|truth|appendix", plus, re.I)


def test_only_the_stage0_arms_are_available() -> None:
    assert arms.STAGE0_ARMS == ("code+", "code-hint")
    assert [a.name for a in arms.require_available(["code+", "code-hint"])] == list(
        arms.STAGE0_ARMS
    )
    for name in ("code-skill", "lib-directed", "lib", "mcp-hybrid"):
        with pytest.raises(ValueError, match="freeze-1"):
            arms.require_available([name])
        with pytest.raises(ValueError, match="freeze-1"):
            arms.preamble(name)
    with pytest.raises(ValueError, match="unknown arm"):
        arms.get_arm("code")
    with pytest.raises(SystemExit, match="freeze-1"):
        cli.main(["run", "--arms", "lib-directed", "--dry-run", "--bundles", "nowhere"])


def test_preregistered_sentences_are_verbatim() -> None:
    flat = re.sub(r"\s+", " ", PREREG)
    assert f'"{arms.CODE_SKILL_SENTENCE}"' in flat
    assert f'"{arms.LIB_DIRECTED_DIRECTIVE}"' in flat
    for line in headroom.RULE_LINES:
        assert line in PREREG.splitlines(), line


# ----------------------------------------------------------------------------------------
# synthetic bundles and truth
# ----------------------------------------------------------------------------------------
VOCAB = {
    "none": {},
    "worn_pump": {"unit": "pp", "m_min": 5, "range": [0, 40]},
    "throttled_valve": {"unit": "%", "m_min": 10, "range": [0, 90]},
    "leak": {"unit": "m3/h", "m_min": 1, "range": [0, 20]},
    "low_suction_level": {"unit": "m", "m_min": 0.5, "range": [0, 5]},
    "pressure_sensor_fault": {"unit": "bar", "m_min": 0.05, "range": [-1, 1]},
}
RESOLVING = ["suction_pressure", "sump_level", "motor_power"]
F3_TRUTH = {
    "single": {"label": "identified", "faults": ["worn_pump"],
               "magnitudes": {"worn_pump": {"value": 8.0, "tol": 1.0}}},
    "double": {"label": "identified", "faults": ["worn_pump", "throttled_valve"],
               "magnitudes": {"worn_pump": {"value": 8.0, "tol": 1.0},
                              "throttled_valve": {"value": 30.0, "tol": 5.0}}},
    "ambiguous": {"label": "ambiguous", "faults": ["low_suction_level", "worn_pump"],
                  "magnitudes": {}, "resolving": ["suction_pressure"]},
    "no_fault": {"label": "no_fault", "faults": [], "magnitudes": {}},
    "sensor": {"label": "identified", "faults": ["pressure_sensor_fault"],
               "magnitudes": {"pressure_sensor_fault": {"value": 0.12, "tol": 0.025}}},
}  # fmt: skip
F3_STRATA = ["single", "single", "double", "ambiguous", "no_fault", "sensor"]


def family_keys(family: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(task.json keys, truth keys) of a synthetic task of ``family``."""
    if family == "F1":
        return (
            {"head_deficit_bep_pp": {"kind": "estimate_or_undetermined", "unit": "pp",
                                     "range": [0, 30]},
             "extra_energy_mwh_per_yr": {"kind": "estimate", "unit": "MWh/yr",
                                         "range": [0, 500]},
             "deterioration_real": {"kind": "boolean"}},
            {"head_deficit_bep_pp": {"kind": "estimate_or_undetermined", "determinable": True,
                                     "value": 8.4, "tol": 1.0, "range": [0, 30]},
             "extra_energy_mwh_per_yr": {"kind": "estimate", "value": 37.5, "tol": 3.75},
             "deterioration_real": {"kind": "boolean", "value": True}},
        )  # fmt: skip
    if family == "F2":
        return (
            {"first_to_trigger": {"kind": "choice", "options": ["F-1", "F-2", "F-3"]},
             "clean_bed_dp_bar": {"kind": "estimate", "unit": "bar", "range": [0, 2]},
             "fouling_real": {"kind": "boolean"}},
            {"first_to_trigger": {"kind": "choice", "value": "F-2"},
             "clean_bed_dp_bar": {"kind": "estimate", "value": 0.31, "tol": 0.01},
             "fouling_real": {"kind": "boolean", "value": False}},
        )  # fmt: skip
    if family == "F4":
        return (
            {"determinable_set": {"kind": "set",
                                  "options": ["pump_head_deficit", "valve_kv", "filter_dp"]},
             "best_added_sensor": {"kind": "choice", "options": ["suction_pressure", "flow"]},
             "two_point_test_helps": {"kind": "boolean"}},
            {"determinable_set": {"kind": "set", "value": ["valve_kv"]},
             "best_added_sensor": {"kind": "choice", "value": "flow"},
             "two_point_test_helps": {"kind": "boolean", "value": True}},
        )  # fmt: skip
    raise ValueError(family)


def correct_answer(truth_keys: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, t in truth_keys.items():
        if t["kind"] == "estimate_or_undetermined" and not t["determinable"]:
            out[k] = "cannot_determine"
        elif t["kind"] in ("estimate", "estimate_or_undetermined"):
            out[k] = {"value": t["value"], "lo90": t["value"] - 1, "hi90": t["value"] + 1}
        elif t["kind"] == "diagnosis":
            if t["label"] == "identified":
                out[k] = {
                    "verdict": "identified",
                    "faults": t["faults"],
                    "magnitudes": {f: t["magnitudes"][f]["value"] for f in t["faults"]},
                }
            elif t["label"] == "ambiguous":
                out[k] = {
                    "verdict": "ambiguous",
                    "faults": t["faults"],
                    "resolving_measurement": t["resolving"][0],
                }
            else:
                out[k] = {"verdict": "no_fault", "faults": []}
        else:
            out[k] = t["value"]
    return out  # fmt: skip


def wrong_answer(truth_keys: dict[str, Any]) -> dict[str, Any]:
    out = correct_answer(truth_keys)
    for k, t in truth_keys.items():
        if t["kind"] == "boolean":
            out[k] = not t["value"]
        elif t["kind"] == "diagnosis":
            out[k] = (
                {"verdict": "identified", "faults": ["leak"], "magnitudes": {"leak": 3}}
                if t["label"] == "no_fault"
                else {"verdict": "no_fault", "faults": []}
            )
    return out


def reply(values: dict[str, Any]) -> str:
    return "Done.\n\n```json\n" + json.dumps(values, indent=2) + "\n```\n"


def build_set(root: Path, truth_root: Path, no_reference: tuple[str, ...] = ()) -> list[str]:
    """Write the 16 development bundles (the pre-registered cells and strata) and their
    truth; return the task ids in sorted order."""
    ids: list[str] = []
    strata = iter(F3_STRATA)
    for cell, n in bundles.PREREGISTERED_CELLS["dev"].items():
        family, generator = cell.split("/")
        for i in range(n):
            tid = f"ops-{family.lower()}-{generator[2:].lower()}-{i + 1:02d}"
            ids.append(tid)
            stratum = next(strata) if family == "F3" else None
            if family == "F3":
                keys = {"diagnosis": {"kind": "diagnosis", "vocabulary": VOCAB,
                                      "excluded_from_candidates": {"uv_lamp_degraded": "no UV"},
                                      "resolving_options": RESOLVING}}  # fmt: skip
                tkeys = {"diagnosis": {"kind": "diagnosis", **copy.deepcopy(F3_TRUTH[stratum])}}
            else:
                keys, tkeys = family_keys(family)
            task_json = {"task_id": tid, "set": "dev", "family": family,
                         "generator": generator, "cell": cell, "stratum": stratum,
                         "keys": keys}  # fmt: skip
            for k in (1, 2, 3):
                r = root / "dev" / tid / f"r{k}"
                (r / "tables").mkdir(parents=True)
                (r / "data").mkdir()
                (r / "task.md").write_text(
                    f"# Ticket\nTASK-ID: {tid}\nPlease answer the keys below.\n", "utf-8"
                )
                (r / "task.json").write_text(json.dumps(task_json), "utf-8")
                (r / "plant.md").write_text("# Plant\nOne pump.\n", "utf-8")
                (r / "events.csv").write_text("timestamp,event\n", "utf-8")
                (r / "tables" / "pump.csv").write_text("q,h\n0,30\n", "utf-8")
                (r / "data" / "scada.csv").write_text(
                    f"timestamp,tag,value,quality\n2026-01-01T00:00,Q1,{10 + k},good\n", "utf-8"
                )
            ref = {"R-a": tid not in no_reference, "R-b": False, "R2": None}
            truth = {
                "task_id": tid,
                "keys": tkeys,
                "realisations": {f"r{k}": {"oracle_pass": True, "reference_pass": ref,
                                           "naive_pass": False} for k in (1, 2, 3)},
            }  # fmt: skip
            (truth_root / "dev").mkdir(parents=True, exist_ok=True)
            (truth_root / "dev" / f"{tid}.truth.json").write_text(json.dumps(truth), "utf-8")
    return sorted(ids)


def truth_keys(truth_root: Path, tid: str) -> dict[str, Any]:
    return json.loads((truth_root / "dev" / f"{tid}.truth.json").read_text("utf-8"))["keys"]


def test_bundles_load_and_match_the_preregistered_cells(tmp_path: Path) -> None:
    ids = build_set(tmp_path / "b", tmp_path / "t")
    tasks = bundles.load_tasks("dev", tmp_path / "b")
    assert [t.task_id for t in tasks] == ids and len(ids) == 16
    assert bundles.set_problems(tasks, "dev") == []
    assert all(t.realisations == (1, 2, 3) for t in tasks)
    for t in tasks:
        assert bundles.truth_problems(t, bundles.load_truth(t, tmp_path / "t")) == []
    only = bundles.load_tasks("dev", tmp_path / "b", ["ops-f3-*"])
    assert len(only) == 6 and {t.stratum for t in only} == set(bundles.STRATA)
    problems = bundles.set_problems(tasks[:-1], "dev")
    assert problems and "pre-registered" in problems[0]


def test_bundle_problems_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "b"
    ids = build_set(root, tmp_path / "t")
    d = root / "dev" / ids[0]
    doc = json.loads((d / "r2" / "task.json").read_text("utf-8"))
    doc["keys"]["extra"] = {"kind": "boolean"}
    (d / "r2" / "task.json").write_text(json.dumps(doc), "utf-8")
    (root / "dev" / ids[1] / "r1" / "plant.md").unlink()
    with pytest.raises(bundles.BundleError) as exc:
        bundles.load_tasks("dev", root)
    text = str(exc.value)
    assert "task.json differs between realisations" in text and "r1/plant.md is missing" in text
    with pytest.raises(bundles.ConfigError):
        bundles.load_tasks("dev", tmp_path / "nowhere")
    with pytest.raises(bundles.ConfigError):
        bundles.load_tasks("dev", root, ["no-such-task"])


def test_copy_leaves_task_json_out(tmp_path: Path) -> None:
    build_set(tmp_path / "b", tmp_path / "t")
    task = bundles.load_tasks("dev", tmp_path / "b")[0]
    copied = bundles.copy_realisation(task, 2, tmp_path / "w")
    assert "task.json" not in copied and not (tmp_path / "w" / "task.json").exists()
    assert set(copied) == {"task.md", "plant.md", "events.csv", "tables/pump.csv",
                           "data/scada.csv"}  # fmt: skip
    assert "Q1,12,good" in (tmp_path / "w" / "data" / "scada.csv").read_text("utf-8")
    with pytest.raises(RuntimeError, match="not empty"):
        bundles.copy_realisation(task, 1, tmp_path / "w")


def test_truth_root_needs_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(bundles.TRUTH_ENV, raising=False)
    with pytest.raises(bundles.ConfigError, match="WPBENCH_OPS_TRUTH"):
        bundles.truth_root()
    monkeypatch.setenv(bundles.TRUTH_ENV, str(tmp_path))
    assert bundles.truth_root() == tmp_path
    monkeypatch.setenv(bundles.BUNDLES_ENV, str(tmp_path / "b"))
    assert bundles.bundles_root() == tmp_path / "b"
    monkeypatch.delenv(bundles.BUNDLES_ENV)
    assert bundles.bundles_root() == bundles.DEFAULT_BUNDLES


# ----------------------------------------------------------------------------------------
# prompt, command, environment, dry run
# ----------------------------------------------------------------------------------------
def test_prompt_is_task_md_then_preamble_then_answer_format(tmp_path: Path) -> None:
    build_set(tmp_path / "b", tmp_path / "t")
    tasks = {t.family: t for t in bundles.load_tasks("dev", tmp_path / "b")}
    t = tasks["F1"]
    prompt = arms.build_prompt(t, "code-hint", 1)
    md = t.task_md(1).rstrip()
    assert prompt.startswith(md + "\n\n" + arms.preamble("code-hint") + "\n\n")
    fmt = arms.answer_format_instruction(t)
    assert prompt.endswith(fmt + "\n")
    assert "head_deficit_bep_pp (estimate or cannot_determine, pp)" in fmt
    assert "extra_energy_mwh_per_yr (estimate, MWh/yr)" in fmt
    assert "deterioration_real (boolean)" in fmt and "```json" in fmt
    assert '"cannot_determine"' in fmt and "diagnosis" not in fmt
    f2 = arms.answer_format_instruction(tasks["F2"])
    assert "first_to_trigger (choice of: F-1, F-2, F-3)" in f2
    f3 = arms.answer_format_instruction(tasks["F3"])
    assert '"verdict": "identified" | "ambiguous" | "no_fault"' in f3
    # nothing about the answer, the stratum or the candidate list beyond task.md
    for word in ("stratum", "worn_pump", "suction_pressure", "8.4", "37.5"):
        assert word not in f3 + fmt
    plus = arms.build_prompt(t, "code+", 1)
    assert "Method checklist" in prompt and "Method checklist" not in plus


def test_command_line(tmp_path: Path) -> None:
    cmd = runner.build_command(
        "code-hint", "opus", tmp_path / "mcp.json", tmp_path / "s.json", 120, claude="claude"
    )
    assert cmd[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert cmd[cmd.index("--max-turns") + 1] == "120"
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                 "--no-chrome"):  # fmt: skip
        assert flag in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--tools") + 1] == "Bash,Read,Write,Edit"
    assert cmd[cmd.index("--allowedTools") :] == ["--allowedTools", "Bash", "Read", "Write", "Edit"]
    assert "--append-system-prompt" not in cmd and "--effort" not in cmd
    with_effort = runner.build_command("code+", "sonnet", Path("m"), Path("s"), 120, "high", 5.0)
    assert with_effort[with_effort.index("--effort") + 1] == "high"
    assert with_effort[with_effort.index("--max-budget-usd") + 1] == "5"
    assert runner.session_mcp_config("code-hint", tmp_path) == {"mcpServers": {}}
    assert runner.PINNED_LIMITS.max_turns == 120 and runner.PINNED_LIMITS.timeout_s == 2400.0


def test_session_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    venv_bin = str(REPO / ".venv" / ("Scripts" if os.name == "nt" else "bin"))
    monkeypatch.setenv("PATH", os.pathsep.join([venv_bin, str(tmp_path / "bin")]))
    monkeypatch.setenv("WPBENCH_OPS_TRUTH", str(tmp_path / "truth"))
    monkeypatch.setenv("WPBENCH_OPS_BUNDLES", str(tmp_path / "bundles"))
    monkeypatch.setenv("WPBENCH_CLAUDE", "C:/tools/claude.exe")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("MCP_CONNECTION_NONBLOCKING", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VIRTUAL_ENV", str(REPO / ".venv"))
    monkeypatch.setenv("KEEP_ME", "yes")
    envdir = tmp_path / "ops-code-plus-abc"
    full, changed = runner.child_env(envdir)
    upper = {k.upper() for k in full}
    assert not {"WPBENCH_OPS_TRUTH", "WPBENCH_OPS_BUNDLES", "WPBENCH_CLAUDE"} & upper
    assert "CLAUDE_CODE_SESSION_ID" not in upper and "MCP_CONNECTION_NONBLOCKING" not in upper
    assert full["ANTHROPIC_API_KEY"] == "sk-test" and full["KEEP_ME"] == "yes"
    assert full["MPLBACKEND"] == "Agg" and changed["MPLBACKEND"] == "Agg"
    assert full["VIRTUAL_ENV"] == str(envdir / ".venv")
    assert full["PIP_NO_INDEX"] == "1" and full["UV_OFFLINE"] == "1"
    assert full["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    path_key = next(k for k in full if k.upper() == "PATH")
    parts = full[path_key].split(os.pathsep)
    assert Path(parts[0]).parent.parent == envdir
    assert venv_bin not in parts


def test_dry_run_prints_everything_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    ids = build_set(tmp_path / "b", tmp_path / "t")

    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError("nothing may start in a dry run")

    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    monkeypatch.setattr(cli, "ensure_code_plus_env", forbidden)
    monkeypatch.setenv("WPBENCH_CLAUDE", "claude")
    argv = ["run", "--dry-run", "--bundles", str(tmp_path / "b"), "--tasks", ids[0],
            "--models", "sonnet", "opus", "--effort", "high"]  # fmt: skip
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "4 session(s); nothing was run (--dry-run)" in out
    assert out.count("command (prompt on stdin):") == 4
    assert "--max-turns 120" in out and "--effort high" in out and "MPLBACKEND=Agg" in out
    assert "TASK-ID: " + ids[0] in out and "Method checklist" in out
    assert "timeout 2400 s" in out
    sid = runner.session_id("dev", "sonnet", ids[0], "code-hint", 1)
    assert f"session {sid}: sonnet {ids[0]} code-hint r1" in out


# ----------------------------------------------------------------------------------------
# a whole Stage 0 run against the fake executable
# ----------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def stage0(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run Stage 0 on 16 synthetic tasks with the fake CLI, audit, re-run, grade."""
    tmp = tmp_path_factory.mktemp("ops-stage0")
    broot, troot, run_dir = tmp / "bundles", tmp / "truth", tmp / "run"
    ids = build_set(broot, troot, no_reference=())
    T = ids
    # T[4]'s reference estimators fail realisation 1, so a code-hint failure there does
    # not count toward F.
    t4 = json.loads((troot / "dev" / f"{T[4]}.truth.json").read_text("utf-8"))
    t4["realisations"]["r1"]["reference_pass"] = {"R-a": False, "R-b": False, "R2": None}
    (troot / "dev" / f"{T[4]}.truth.json").write_text(json.dumps(t4), "utf-8")
    good = {t: {"reply": reply(correct_answer(truth_keys(troot, t)))} for t in T}
    bad = {t: {"reply": reply(wrong_answer(truth_keys(troot, t)))} for t in T}
    plan: dict[str, list[Any]] = {}
    for model in ("sonnet", "opus"):
        for arm in ("code+", "code-hint"):
            for t in T:
                plan[f"{model}|{t}|{arm}"] = [good[t]]
    for t in T[:5]:  # Sonnet 5 code-hint fails T0-T4: F = 4 (T4 has no reference pass)
        plan[f"sonnet|{t}|code-hint"] = [bad[t]]
    for t in T[:2]:  # Opus 5.5 code-hint fails T0, T1: F = 2
        plan[f"opus|{t}|code-hint"] = [bad[t]]
    # A contaminated (but passing) Opus code-hint session: left out, with a warning.
    plan[f"opus|{T[5]}|code-hint"] = [{**good[T[5]], "command": "type ..\\truth\\x.truth.json"}]
    plan[f"sonnet|{T[7]}|code-hint"] = ["silent", good[T[7]]]  # infrastructure, then fine
    plan[f"opus|{T[8]}|code+"] = ["api_error", good[T[8]]]
    plan[f"opus|{T[9]}|code-hint"] = ["killed", good[T[9]]]
    plan[f"sonnet|{T[6]}|code+"] = ["killed", "killed", "killed"]  # never recovers
    plan[f"sonnet|{T[9]}|code+"] = [bad[T[9]]]  # an F3 single fault called no_fault
    plan_path, state = tmp / "plan.json", tmp / "state"
    plan_path.write_text(json.dumps(plan), "utf-8")
    state.mkdir()
    envdir = tmp / "env"
    (envdir / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    (envdir / "stamp.json").write_text(
        json.dumps({"request": {"python": "3.12", "packages": {}},
                    "installed": {"numpy": "0"}}), "utf-8"
    )  # fmt: skip
    real_popen = subprocess.Popen
    commands: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> Any:
        assert cmd[0] == str(FAKE)
        commands.append(cmd)
        return real_popen([sys.executable, str(FAKE), *cmd[1:]], **kwargs)

    out: dict[str, Any] = {"ids": T, "run": run_dir, "truth": troot, "bundles": broot}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner.subprocess, "Popen", fake_popen)
        mp.setattr(cli, "ensure_code_plus_env", lambda d=None: envdir)
        mp.setattr(cli, "claude_version", lambda: "2.1.280 (fake)")
        mp.setenv("WPBENCH_CLAUDE", str(FAKE))
        mp.setenv("WPBENCH_OPS_TRUTH", str(troot))
        mp.setenv("OPSFAKE_PLAN", str(plan_path))
        mp.setenv("OPSFAKE_STATE", str(state))
        base = ["--bundles", str(broot), "--no-manifest-check"]
        assert cli.main(["run", "--set", "dev", "--models", "sonnet", "opus", "--jobs", "8",
                         "--out", str(run_dir), *base]) == 0  # fmt: skip
        out["audit1"] = json.loads((run_dir / "audit.json").read_text("utf-8"))
        # Grading before the re-runs is provisional; the headroom rule refuses.
        assert cli.main(["grade", str(run_dir)]) == 0
        out["headroom_before"] = cli.main(["headroom", str(run_dir), "--bundles", str(broot)])
        for _ in range(3):
            assert cli.main(["run", "--out", str(run_dir), "--rerun-flagged", *base]) == 0
        # A defect found without grades: named sessions are re-run (within the cap).
        explicit = runner.session_id("dev", "opus", T[10], "code+", 1)
        never = runner.session_id("dev", "sonnet", T[6], "code+", 1)
        assert cli.main(["run", "--out", str(run_dir), "--rerun", explicit, never, *base]) == 0
        out["explicit"] = explicit
        with pytest.raises(SystemExit, match="unknown session"):
            cli.main(["run", "--out", str(run_dir), "--rerun", "0123456789abcdef", *base])
        out["audit_final"] = json.loads((run_dir / "audit.json").read_text("utf-8"))
        assert cli.main(["grade", str(run_dir)]) == 0
        out["headroom_json"] = tmp / "headroom.json"
        out["headroom_after"] = cli.main(
            ["headroom", str(run_dir), "--bundles", str(broot), "--json",
             str(out["headroom_json"])]
        )  # fmt: skip
        # Resuming the run with other settings is refused.
        with pytest.raises(SystemExit, match="other settings"):
            cli.main(["run", "--set", "dev", "--models", "sonnet", "--out", str(run_dir),
                      "--max-turns", "50", *base])  # fmt: skip
    out["commands"] = commands
    out["index"] = json.loads((run_dir / "index.json").read_text("utf-8"))
    out["records"] = report.load_records([run_dir])
    return out


def _sid(model: str, task: str, arm: str) -> str:
    return runner.session_id("dev", model, task, arm, 1)


def test_stage0_runs_every_session_once(stage0: dict[str, Any]) -> None:
    assert len(stage0["index"]) == 64
    # 64 sessions, 4 re-runs after the first audit, 1 after the second, none after the
    # third (the session that never recovers has had its 3 attempts), 1 named re-run
    assert len(stage0["commands"]) == 64 + 4 + 1 + 1
    run_meta = json.loads((stage0["run"] / "run.json").read_text("utf-8"))
    assert run_meta["max_turns"] == 120 and run_meta["timeout_s"] == 2400.0
    assert run_meta["claude_version"] == "2.1.280 (fake)"
    assert run_meta["arms"] == ["code+", "code-hint"] and run_meta["set"] == "dev"


def test_sessions_see_the_bundle_and_nothing_else(stage0: dict[str, Any]) -> None:
    T = stage0["ids"]
    for model, arm in (("sonnet", "code-hint"), ("opus", "code+")):
        adir = stage0["run"] / "sessions" / _sid(model, T[10], arm) / "attempt-1"
        probe = json.loads((adir / "workdir" / "fake_probe.json").read_text("utf-8"))
        assert "task.json" not in probe["cwd_files"]
        assert {"task.md", "plant.md", "events.csv", "data/scada.csv"} <= set(probe["cwd_files"])
        assert not any(k.upper().startswith("WPBENCH") for k in probe["env"])
        assert probe["env"]["MPLBACKEND"] == "Agg"
        assert Path(probe["path_head"]).parent.parent.name == "env"
        assert probe["prompt"] == (adir / "prompt.txt").read_text("utf-8")
        assert probe["prompt"].startswith("# Ticket\nTASK-ID: " + T[10])
        assert ("Method checklist" in probe["prompt"]) is (arm == "code-hint")
        argv = probe["argv"]
        assert argv[argv.index("--model") + 1] == model
        outcome = json.loads((adir / "outcome.json").read_text("utf-8"))
        assert outcome["max_turns"] == 120 and outcome["timeout_s"] == 2400.0
        text = json.dumps(outcome)  # the audit reads it: no arm label in it
        assert "code+" not in text and "code-hint" not in text and "hint" not in text
        # only the file the agent wrote is kept, not the bundle
        kept = {p.name for p in (adir / "workdir").rglob("*") if p.is_file()}
        assert kept == {"fake_probe.json"}


def test_blind_audit_flags_the_infrastructure_errors(stage0: dict[str, Any]) -> None:
    T = stage0["ids"]
    flagged = {
        _sid("sonnet", T[7], "code-hint"): ["no_model_turn"],
        _sid("opus", T[8], "code+"): ["api_or_cli_error"],
        _sid("opus", T[9], "code-hint"): ["killed"],
        _sid("sonnet", T[6], "code+"): ["killed"],
    }
    a1 = stage0["audit1"]
    assert set(a1["flagged"]) == set(flagged) == set(a1["pending_reruns"])
    for sid, sigs in flagged.items():
        assert a1["sessions"][sid]["signatures"] == sigs
    final = stage0["audit_final"]
    never = _sid("sonnet", T[6], "code+")
    assert final["flagged"] == [never] and final["pending_reruns"] == []
    assert final["sessions"][never]["attempt"] == infra.MAX_ATTEMPTS
    for sid in set(flagged) - {never}:
        assert final["sessions"][sid]["attempt"] == 2 and not final["sessions"][sid]["flagged"]
    assert [r["name"] for r in final["rules"]] == list(infra.SIGNATURE_NAMES)


def test_audit_reads_no_arm_labels_or_grades(
    stage0: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    read: list[str] = []
    real_read_text, real_read_bytes, real_open = Path.read_text, Path.read_bytes, builtins.open

    def spy_read_text(self: Path, *a: Any, **k: Any) -> str:
        read.append(self.name)
        return real_read_text(self, *a, **k)

    def spy_read_bytes(self: Path) -> bytes:
        read.append(self.name)
        return real_read_bytes(self)

    def spy_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if "r" in mode:
            read.append(Path(file).name)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(builtins, "open", spy_open)
    assert cli.main(["audit", str(stage0["run"])]) == 0
    monkeypatch.undo()
    assert read and set(read) <= set(infra.AUDITED_FILES)
    out = capsys.readouterr().out
    for label in ("code", "sonnet", "opus", "ops-f"):
        assert label not in out.lower()
    audit_text = (stage0["run"] / "audit.json").read_text("utf-8")
    for label in ("code+", "code-hint", "sonnet", "opus", "ops-f"):
        assert label not in audit_text


def test_grades_and_records(stage0: dict[str, Any]) -> None:
    T = stage0["ids"]
    recs = {r["sid"]: r for r in stage0["records"]}
    assert len(recs) == 64 and not any(r["stale"] for r in recs.values())
    r = recs[_sid("sonnet", T[0], "code-hint")]
    assert r["passed"] is False and r["model"] == "sonnet" and r["arm"] == "code-hint"
    assert r["model_id"] == "claude-fake-sonnet" and r["cost_usd"] == 0.25 and r["turns"] == 3
    assert r["duration_s"] == 4.2 and r["tokens"] == 150 and r["infra_error"] is None
    ok = recs[_sid("opus", T[10], "code+")]
    assert ok["sid"] == stage0["explicit"] and ok["attempt"] == 2  # the named re-run
    assert ok["passed"] is True and ok["n_passed"] == ok["n_keys"]
    never = recs[_sid("sonnet", T[6], "code+")]
    assert never["attempt"] == 3 and never["infra_error"] == ["killed"]
    assert never["rerun_allowed"] is False and never["passed"] is False  # never excluded
    rerun = recs[_sid("sonnet", T[7], "code-hint")]
    assert rerun["attempt"] == 2 and rerun["passed"] is True and rerun["infra_error"] is None
    tainted = recs[_sid("opus", T[5], "code-hint")]
    assert tainted["contamination"] and any("truth" in c for c in tainted["contamination"])
    cw = recs[_sid("sonnet", T[9], "code+")]
    assert cw["family"] == "F3" and cw["stratum"] == "single"
    assert cw["cw_category"] == "confident_wrong" and cw["confident_wrong"] is True
    right = recs[_sid("opus", T[9], "code+")]
    assert right["cw_category"] == "pass" and right["passed"] is True
    assert recs[_sid("opus", T[0], "code+")]["cw_category"] is None  # not an F3 task
    summary = json.loads((stage0["run"] / "summary.json").read_text("utf-8"))
    g = summary["groups"]["sonnet"]["code-hint"]
    assert g["tasks"] == 16 and g["sessions_passed"] == 11 and g["rate"] == pytest.approx(11 / 16)
    assert summary["groups"]["opus"]["code-hint"]["contaminated"] == 1
    assert summary["pending_reruns"] == [] and summary["gate_blocked_by_contamination"] == []


def test_headroom_rule_on_the_run(stage0: dict[str, Any]) -> None:
    assert stage0["headroom_before"] == 2  # not computed while re-runs are pending
    assert stage0["headroom_after"] == 0
    res = json.loads(stage0["headroom_json"].read_text("utf-8"))
    T = stage0["ids"]
    assert res["computed"] and res["F"] == {"Sonnet 5": 4, "Opus 5.5": 2}
    assert res["failing_tasks"]["Sonnet 5"] == T[:4] and res["closed"] is False
    assert any("would be 3" in w for w in res["warnings"])


def test_headroom_printout(stage0: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["headroom", str(stage0["run"]), "--bundles", str(stage0["bundles"]),
                     "--truth", str(stage0["truth"])])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    for line in headroom.RULE_LINES:
        assert line in out.splitlines()
    assert "F(Sonnet 5) = 4 > 3 and F(Opus 5.5) = 2 ≤ 3: the room is OPEN." in out


def test_report_command(stage0: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["report", str(stage0["run"]), "--print"]) == 0
    out = capsys.readouterr().out
    assert "| sonnet | code-hint | 16 | 11/16 | 69 % |" in out
    assert "## Contaminated sessions (left out of the rates)" in out
    assert "no re-run left: graded as is" in out


# ----------------------------------------------------------------------------------------
# infrastructure signatures
# ----------------------------------------------------------------------------------------
def _stream(*msgs: dict[str, Any]) -> str:
    return "\n".join(json.dumps(m) for m in msgs) + "\n"


INIT = {"type": "system", "subtype": "init", "model": "m", "tools": ["Bash"], "mcp_servers": []}
TURN = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working."}]}}
ANSWER = {
    "type": "assistant",
    "message": {"content": [{"type": "text", "text": '```json\n{"a": 1}\n```'}]},
}
OK_RESULT = {"type": "result", "subtype": "success", "is_error": False,
             "result": '```json\n{"a": 1}\n```', "num_turns": 2}  # fmt: skip
OK = {"exit_code": 0, "timed_out": False}


@pytest.mark.parametrize(
    ("stream", "outcome", "expected"),
    [
        (_stream(INIT, TURN, ANSWER, OK_RESULT), OK, []),
        (_stream(INIT, TURN, ANSWER, OK_RESULT), None, ["harness_interrupted"]),
        ("", {"error": "could not start claude: [WinError 2]"}, ["harness_start", "no_model_turn"]),
        ("", {"exit_code": 1}, ["no_model_turn"]),
        ("", {"exit_code": None, "timed_out": True}, ["no_model_turn"]),  # hung before a turn
        (_stream(INIT, {"type": "system", "subtype": "api_retry", "error_status": 401,
                        "error": "authentication_failed"},
                 {"type": "result", "subtype": "success", "is_error": True,
                  "result": "Not logged in · Please run /login"}), OK,
         ["authentication", "no_model_turn"]),
        # a 401 retry the session recovered from is not an error
        (_stream(INIT, {"type": "system", "subtype": "api_retry", "error_status": 401,
                        "error": "authentication_failed"}, TURN, ANSWER, OK_RESULT), OK, []),
        (_stream(INIT, TURN, {"type": "result", "subtype": "success", "is_error": True,
                              "api_error_status": 529, "result": "API Error: 529"}), OK,
         ["api_or_cli_error"]),
        (_stream(INIT, TURN, {"type": "result", "subtype": "error_during_execution",
                              "is_error": True, "result": ""}), OK, ["api_or_cli_error"]),
        (_stream(INIT, TURN), {"exit_code": 1, "timed_out": False}, ["killed"]),
        (_stream({**INIT, "mcp_servers": [{"name": "worldparts", "status": "pending"}]}, TURN,
                 ANSWER, OK_RESULT), OK, ["mcp_not_connected"]),
        (_stream(INIT, TURN, {"type": "result", "subtype": "success", "is_error": False,
                              "result": "I could not use the tool.",
                              "permission_denials": [{"tool_name": "WebFetch"}]}), OK,
         ["permission_denied"]),
        # the agent's failures, never infrastructure errors:
        (_stream(INIT, TURN), {"exit_code": None, "timed_out": True}, []),  # timeout
        (_stream(INIT, TURN, {"type": "result", "subtype": "error_max_turns", "is_error": True,
                              "result": ""}), OK, []),
        (_stream(INIT, TURN, {"type": "result", "subtype": "error_max_budget_usd",
                              "is_error": True}), OK, []),
        (_stream(INIT, TURN, {"type": "result", "subtype": "success", "is_error": True,
                              "result": "Prompt is too long"}), OK, []),
        (_stream(INIT, TURN, {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True,
             "content": "Traceback ...\nValueError: worldparts could not converge"}]}},
                 ANSWER, OK_RESULT), OK, []),
        (_stream(INIT, TURN, {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True,
             "content": "cat: /root/x: Permission denied"}]}}, ANSWER, OK_RESULT), OK, []),
        # a CLI denial the agent recovered from is graded
        (_stream(INIT, TURN, {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True,
             "content": "Claude requested permissions to use WebFetch, but you haven't "
                        "granted it yet."}]}}, ANSWER, OK_RESULT), OK, []),
        (_stream(INIT, TURN, {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True,
             "content": "Claude requested permissions to use WebFetch, but you haven't "
                        "granted it yet."}]}}, TURN,
                 {"type": "result", "subtype": "success", "is_error": False,
                  "result": "Stopped."}), OK, ["permission_denied"]),
    ],
)  # fmt: skip
def test_infrastructure_signatures(stream: str, outcome: Any, expected: list[str]) -> None:
    assert infra.audit_session(stream, "", outcome) == expected


def test_signature_list_is_documented() -> None:
    assert len(set(infra.SIGNATURE_NAMES)) == len(infra.SIGNATURES) == 8
    assert all(s.description.endswith(".") for s in infra.SIGNATURES)
    assert infra.MAX_ATTEMPTS == 3


# ----------------------------------------------------------------------------------------
# contamination
# ----------------------------------------------------------------------------------------
def _summary(tool_input: str, result: str = "") -> StreamSummary:
    return StreamSummary(tool_input_texts=[json.dumps({"command": tool_input})],
                         tool_result_texts=[result])  # fmt: skip


@pytest.mark.parametrize(
    ("tool_input", "result", "needle"),
    [
        (r"dir C:\Users\raimo\world-model\worldparts-opsbench", "", "worldparts-opsbench"),
        ('python -c "import opsim"', "", "opsim"),
        ("ls", "opsim.py  gen.py", "opsim"),
        ("cat ../x.truth.json", "", "truth.json"),
        ("cat APPENDIX.md", "", "APPENDIX"),
        ("ls ~/world-model/opsbench-bundles/dev", "", "opsbench-bundles"),
        ("echo $WPBENCH_OPS_TRUTH", "", "WPBENCH_OPS_"),
        ("type benchmarks\\operations\\harness\\grading.py", "", "benchmarks/operations"),
        ('python -c "import worldparts"', "ModuleNotFoundError", "worldparts library"),
        (f"type {REPO}\\README.md", "", "repository path"),
    ],
)
def test_contamination_markers(tool_input: str, result: str, needle: str) -> None:
    reasons = markers.contamination(_summary(tool_input, result), "code-hint")
    assert any(needle in r for r in reasons), reasons


def test_configured_folders_are_markers(tmp_path: Path) -> None:
    b, t = tmp_path / "my-bundles", tmp_path / "secret"
    s = _summary(f"ls {t}", "")
    assert any("truth folder" in r for r in markers.contamination(s, "code+", b, t))
    s = _summary(f"ls {b}/dev/x/r2", "")
    assert any("bundle folder" in r for r in markers.contamination(s, "code+", b, t))


@pytest.mark.parametrize(
    ("tool_input", "result"),
    [
        ("python analyse.py", "head deficit 8.9 pp"),
        ("cat plant.md", "# Plant\nSee the appendix for the pump curve.\n"),
        ("python fit.py", r'File "C:\Users\x\AppData\Local\worldparts-bench\ops-code-plus-abc'
                          r'\.venv\Lib\site-packages\scipy\optimize\_lsq.py", line 3'),
        ("python -c \"loopsim = 1; print(loopsim)\"", "1"),
        ("cat data/scada.csv | head", "timestamp,tag,value,quality"),
        ("python -c \"print('truth')\"", "truth"),
    ],
)  # fmt: skip
def test_ordinary_work_is_not_contamination(tool_input: str, result: str) -> None:
    assert markers.contamination(_summary(tool_input, result), "code-hint") == []


# ----------------------------------------------------------------------------------------
# headroom rule (pure function)
# ----------------------------------------------------------------------------------------
def _records(fails: dict[str, list[int]], n: int = 16, **extra: Any) -> list[dict[str, Any]]:
    out = []
    for model in ("sonnet", "opus"):
        for arm in ("code+", "code-hint"):
            for i in range(n):
                failed = arm == "code-hint" and i in fails.get(model, [])
                out.append({"sid": f"{model}-{arm}-{i}", "set": "dev", "realisation": 1,
                            "model": model, "arm": arm, "task": f"t{i:02d}",
                            "passed": not failed, "model_id": model})  # fmt: skip
    return out


def _truths(n: int = 16, no_ref: tuple[int, ...] = ()) -> dict[str, dict[str, Any]]:
    return {f"t{i:02d}": {"realisations": {"r1": {"reference_pass": {
        "R-a": i not in no_ref, "R-b": None}}}} for i in range(n)}  # fmt: skip


MODELS = {"Sonnet 5": "sonnet", "Opus 5.5": "opus"}
TASKS = [f"t{i:02d}" for i in range(16)]


@pytest.mark.parametrize(
    ("fails", "no_ref", "F", "closed"),
    [
        ({"sonnet": [0, 1, 2], "opus": [0, 1, 2]}, (), (3, 3), True),
        ({"sonnet": [0, 1, 2, 3], "opus": []}, (), (4, 0), False),
        ({"sonnet": [], "opus": [5, 6, 7, 8]}, (), (0, 4), False),
        ({"sonnet": [0, 1, 2, 3], "opus": [0]}, (3,), (3, 1), True),  # no reference on t03
        ({"sonnet": list(range(16)), "opus": list(range(16))}, tuple(range(16)), (0, 0), True),
    ],
)
def test_headroom_rule(fails: Any, no_ref: Any, F: tuple[int, int], closed: bool) -> None:
    res = headroom.headroom(_records(fails), TASKS, _truths(no_ref=no_ref), MODELS)
    assert res.computed and (res.F["Sonnet 5"], res.F["Opus 5.5"]) == F
    assert res.closed is closed
    lines = headroom.headroom_lines(res)
    assert lines[1:4] == list(headroom.RULE_LINES)
    assert lines[-1].endswith(f"the room is {'CLOSED' if closed else 'OPEN'}.")


def test_headroom_is_not_computed_when_it_cannot_be() -> None:
    recs = _records({})
    assert headroom.headroom(recs[:-1], TASKS, _truths(), MODELS).not_computed  # a session gone
    recs = _records({})
    recs[20]["rerun_allowed"] = True
    res = headroom.headroom(recs, TASKS, _truths(), MODELS)
    assert not res.computed and any("re-run" in p for p in res.not_computed)
    assert res.closed is None and "NOT COMPUTED" in "\n".join(headroom.headroom_lines(res))
    recs = _records({})
    for r in recs[:2]:  # 2 of 32 code+ sessions: 6 % > 5 %
        r["contamination"] = ["tool input mentions 'opsim'"]
    res = headroom.headroom(recs, TASKS, _truths(), MODELS)
    assert any("contaminated (over 5 %)" in p for p in res.not_computed)
    recs = _records({})
    recs.append(dict(recs[-1], sid="dup"))
    assert any(
        "more than one" in p for p in headroom.headroom(recs, TASKS, _truths(), MODELS).not_computed
    )
    res = headroom.headroom(_records({}), TASKS, _truths(), MODELS, ["cell F1/G-ind: 1 task"])
    assert not res.computed
    res = headroom.headroom(_records({}), TASKS, _truths(), {"Sonnet 5": "sonnet"})
    assert not res.computed
    recs = _records({})
    recs[40]["stale"] = True
    assert not headroom.headroom(recs, TASKS, _truths(), MODELS).computed


def test_headroom_leaves_out_one_contaminated_session_and_says_so() -> None:
    recs = _records({"sonnet": [0, 1, 2]})
    tainted = next(r for r in recs if r["model"] == "sonnet" and r["arm"] == "code-hint"
                   and r["task"] == "t05")  # fmt: skip
    tainted["contamination"] = ["x"]
    tainted["passed"] = True
    res = headroom.headroom(recs, TASKS, _truths(), MODELS)
    assert res.computed and res.F["Sonnet 5"] == 3 and res.closed is True
    assert res.contaminated_excluded["Sonnet 5"] == ["t05"]
    assert any("would be 4" in w for w in res.warnings)


def test_headroom_matches_the_power_simulation_rule() -> None:
    """The vectorised rule of analysis/power.py and the headroom function agree."""
    import numpy as np

    from benchmarks.operations.analysis.power import threshold_closed

    rng = np.random.default_rng(1)
    for _ in range(200):
        passed = rng.random((2, 16)) < 0.8
        ref = rng.random(16) < 0.9
        fails = {m: [i for i in range(16) if not passed[j, i]]
                 for j, m in enumerate(("sonnet", "opus"))}  # fmt: skip
        truths = _truths(no_ref=tuple(i for i in range(16) if not ref[i]))
        res = headroom.headroom(_records(fails), TASKS, truths, MODELS)
        F = (~passed & ref).sum(axis=1)
        assert (res.F["Sonnet 5"], res.F["Opus 5.5"]) == tuple(F)
        assert res.closed == bool(threshold_closed(F).all())


# ----------------------------------------------------------------------------------------
# manifest
# ----------------------------------------------------------------------------------------
def test_manifest_write_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "b"
    ids = build_set(root, tmp_path / "t")
    out = tmp_path / "bundles-dev.sha256"
    assert cli.main(["manifest", "--set", "dev", "--bundles", str(root), "--out", str(out)]) == 0
    lines = out.read_text("utf-8").splitlines()
    assert len(lines) == 16 * 3 * 6
    assert lines == sorted(lines, key=lambda s: s.split("  ", 1)[1])
    assert re.fullmatch(r"[0-9a-f]{64}  dev/ops-[^ ]+/r[123]/[^ ]+", lines[0])
    assert any(line.endswith("/r1/task.json") for line in lines)
    assert cli.main(["manifest", "--set", "dev", "--bundles", str(root), "--out", str(out),
                     "--check"]) == 0  # fmt: skip
    (root / "dev" / ids[0] / "r2" / "data" / "scada.csv").write_text("changed", "utf-8")
    (root / "dev" / ids[1] / "r1" / "events.csv").unlink()
    (root / "dev" / ids[2] / "r3" / "new.txt").write_text("x", "utf-8")
    capsys.readouterr()
    assert cli.main(["manifest", "--set", "dev", "--bundles", str(root), "--out", str(out),
                     "--check"]) == 1  # fmt: skip
    text = capsys.readouterr().out
    assert f"changed: dev/{ids[0]}/r2/data/scada.csv" in text
    assert f"missing: dev/{ids[1]}/r1/events.csv" in text
    assert f"not in the manifest: dev/{ids[2]}/r3/new.txt" in text
    assert "3 difference(s)" in text
    parsed = bundles.parse_manifest(out.read_text("utf-8"))
    assert bundles.manifest_differences(parsed, parsed) == []
    with pytest.raises(ValueError):
        bundles.parse_manifest("not a manifest line")


def test_manifest_path_is_in_the_repository() -> None:
    assert bundles.manifest_path("dev") == OPS / "bundles-dev.sha256"


def test_run_refuses_bundles_that_differ_from_the_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "b"
    ids = build_set(root, tmp_path / "t")
    manifest = tmp_path / "bundles-dev.sha256"
    manifest.write_bytes(bundles.format_manifest(bundles.manifest_entries(root, "dev")).encode())
    monkeypatch.setattr(cli, "manifest_path", lambda s: manifest)
    (root / "dev" / ids[0] / "r1" / "task.md").write_text("edited", "utf-8")

    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError("nothing may start")

    monkeypatch.setattr(cli, "ensure_code_plus_env", forbidden)
    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    with pytest.raises(SystemExit, match=f"changed: dev/{ids[0]}/r1/task.md"):
        cli.main(["run", "--bundles", str(root), "--out", str(tmp_path / "run")])


# ----------------------------------------------------------------------------------------
# the code-plus environment
# ----------------------------------------------------------------------------------------
class _FakeRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append([str(a) for a in argv])
        self.envs.append(dict(kwargs.get("env") or {}))
        stdout = ""
        if "freeze" in argv:
            stdout = "numpy==2.5.3\nlmfit==1.3.4\n"
        elif "-c" in argv:
            stdout = '{"numpy": "2.5.3"}'
        return subprocess.CompletedProcess(argv, 0, stdout, "")


def test_code_plus_env_is_keyed_and_built_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeRun()
    monkeypatch.setattr(env.subprocess, "run", fake)
    monkeypatch.setattr(env.shutil, "which", lambda name, **k: "uv")
    request = env.code_plus_request()
    assert set(request["packages"]) == {"numpy", "scipy", "pandas", "statsmodels",
                                        "scikit-learn", "lmfit", "fluids", "wntr",
                                        "matplotlib"}  # fmt: skip
    d = tmp_path / "ops-env"
    py = env.code_env_python(d)
    py.parent.mkdir(parents=True)
    py.write_text("", "utf-8")
    assert env.ensure_code_plus_env(d, log=lambda *a: None) == d
    venv, install, probe, freeze = fake.calls
    assert venv[1:3] == ["venv", "--python"]
    assert f"numpy=={request['packages']['numpy']}" in install and "lmfit" in install
    assert probe[1] == "-c" and "worldparts" in probe[2] and fake.envs[2]["MPLBACKEND"] == "Agg"
    assert freeze[1:3] == ["pip", "freeze"]
    info = env.env_info(d)
    assert info is not None and info["installed"]["lmfit"] == "1.3.4"
    assert info["key"] == env.env_key(request)
    fake.calls.clear()
    assert env.ensure_code_plus_env(d, log=lambda *a: None) == d and fake.calls == []  # reused
    assert env.default_code_plus_dir(request).name == f"ops-code-plus-{env.env_key(request)}"
    other = {**request, "python": "3.13"}
    assert env.env_key(other) != env.env_key(request)
    with pytest.raises(RuntimeError, match="outside the repository"):
        env.ensure_code_plus_env(REPO / "ops-env", log=lambda *a: None)
    with pytest.raises(ValueError, match="freeze-1"):
        env.python_env_for("lib")
