"""Tests of the build-phase tooling of the operations benchmark (FREEZES.md, "Build phase
after Stage 0").

Covers the scoring of scripted answers (``score DIR``, full and ``--pass-fail``), the builder
wrapper ``tools/ops_passfail.py``, the firewall audit of builder transcripts, the quick
reference's limits (PREREGISTRATION.md section 3), the code-skill toolkit, the lib
environment, and readiness runs of ``lib-directed``, ``lib`` and ``code-skill`` against the
fake Claude executable (tests/fixtures/opsbench/fake_claude.py: no model is ever called, and
the real CLI is never run). Synthetic bundles, truth, transcripts and toolkits only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from test_ops_bench_harness import (  # noqa: E402
    FAKE,
    build_set,
    correct_answer,
    reply,
    truth_keys,
    wrong_answer,
)

from benchmarks.operations.harness import __main__ as cli  # noqa: E402
from benchmarks.operations.harness import (  # noqa: E402
    arms,
    bundles,
    env,
    firewall,
    markers,
    quickref,
    report,
    runner,
    toolkit,
)
from benchmarks.operations.harness import score as scoring  # noqa: E402

OPS = REPO / "benchmarks" / "operations"
PRE = OPS / "preambles"


def _manifest(troot: Path, skip: tuple[str, ...] = ()) -> str:
    return "".join(
        f"{bundles._sha256(p)}  {p.relative_to(troot).as_posix()}\n"
        for p in sorted((troot / "dev").glob("*.truth.json"))
        if not p.name.startswith(skip)
    )


# ----------------------------------------------------------------------------------------
# the preambles of the Stage 1 arms
# ----------------------------------------------------------------------------------------
def test_lib_and_code_skill_preambles_are_the_preregistered_text() -> None:
    assert arms.read_preamble_file("lib-directed.txt") == arms.LIB_DIRECTED_DIRECTIVE
    assert arms.read_preamble_file("code-skill.txt") == arms.CODE_SKILL_SENTENCE
    code = (PRE / "environment.txt").read_text("utf-8")
    lib = (PRE / "environment-lib.txt").read_text("utf-8")
    # the lib environment text states the same packages plus worldparts, nothing else
    assert (
        lib != code
        and lib.replace(
            "fluids, wntr, matplotlib and worldparts installed",
            "fluids, wntr and matplotlib installed",
        )
        == code
    )
    assert "checklist" not in lib.lower() and "reference/" not in lib


def test_readiness_arms_run_only_in_a_readiness_run() -> None:
    for name in ("code-skill", "lib-directed", "lib"):
        with pytest.raises(ValueError, match=r"freeze-1.*--readiness"):
            arms.require_available([name])
        assert [a.name for a in arms.require_available([name], readiness=True)] == [name]
    with pytest.raises(ValueError, match="freeze-1"):
        arms.require_available(["mcp-hybrid"], readiness=True)
    assert arms.READINESS_ARMS == ("lib-directed", "code-skill")
    with pytest.raises(SystemExit, match="development set"):
        cli.main(["run", "--readiness", "--set", "test", "--dry-run", "--bundles", "nowhere"])
    with pytest.raises(SystemExit, match="freeze-1"):
        cli.main(["run", "--readiness", "--arms", "mcp-hybrid", "--dry-run", "--bundles", "x"])
    with pytest.raises(ValueError, match="freeze-1"):
        env.python_env_for("lib", ensure=False)
    assert env.python_env_for("lib", ensure=False, readiness=True).name.startswith("<ops-lib")


# ----------------------------------------------------------------------------------------
# the quick reference (section 3)
# ----------------------------------------------------------------------------------------
CHECKLIST = (PRE / "checklist.txt").read_text("utf-8")
GOOD_QR = """\
worldparts quick reference
`import worldparts as wp`
`wp.System.from_dict(doc)` -> `System`
`system.solve()` -> `Result` with fields `values`, `warnings`
#! section 14
`wp.load_measurements(path)` -> `Measurements`
`wp.calibrate(system, data, unknowns)` -> `Calibration`: `estimates`, `sigma`, `correlation`
`wp.identifiability(system, data, unknowns)` -> `singular_values`, `determined`
`wp.diagnose(system, data, faults)` -> `Diagnosis`: `verdict`, `candidates`, `resolving`
Prefer none, then single faults
#! end
"""


def test_a_quick_reference_within_the_limits_passes() -> None:
    assert quickref.quick_reference_problems(GOOD_QR, CHECKLIST) == []
    ref = quickref.parse(GOOD_QR)
    assert len(ref.section14) == 5 and len(ref.shown) == 9
    assert "#!" not in quickref.shown_text(GOOD_QR)
    assert sorted(set(quickref.numbers_in(CHECKLIST)), key=float) == ["1", "90"]


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("`wp.diagnose(system, data, faults, dchi2=10)`", "the number 10"),
        ("Fields `lo90`, `hi90` at 0.95", "the number 0.95"),
        ("Clean spikes with a Hampel filter", "hampel"),
        ("Aggregate hourly, then fit", "method statement (then)"),
        ("Prefer pairs, then single faults.", "method statement (prefer, then)"),
        ("`value` is None if the fit is not determined", "method statement (if)"),
        ("`wp.calibrate(system, use weighted least squares first)`",
         "method statement (first, least)"),
        ("`use the Hampel filter`", "hampel"),
        ("A bootstrap gives `sigma`", "bootstrap"),
    ],
)  # fmt: skip
def test_numbers_and_method_statements_absent_from_the_checklist_fail(
    line: str, needle: str
) -> None:
    text = GOOD_QR.replace("#! end\n", "#! end\n" + line + "\n")
    problems = quickref.quick_reference_problems(text, CHECKLIST)
    assert any(needle in p for p in problems), problems


def test_quick_reference_line_limits_and_the_section14_part() -> None:
    body = "worldparts quick reference\n"
    long = body + "".join(f"`wp.f{i}(x)` -> `y`\n" for i in range(45)) + "#! section 14\n#! end\n"
    assert quickref.quick_reference_problems(long, CHECKLIST) == [
        "46 lines; section 3 allows at most 45"
    ]
    wide = body + "#! section 14\n" + "".join(f"`wp.g{i}(x)`\n" for i in range(21)) + "#! end\n"
    assert any("21 lines for section 14" in p
               for p in quickref.quick_reference_problems(wide, CHECKLIST))  # fmt: skip
    ok = body + "#! section 14\n" + "".join(f"`wp.g{i}(x)`\n" for i in range(20)) + "#! end\n"
    assert quickref.quick_reference_problems(ok, CHECKLIST) == []
    for bad, needle in (
        (body, "no '#! section 14' part"),
        (body + "#! section 14\n#! end\n#! section 14\n", "a second"),
        (body + "#! end\n#! section 14\n", "without"),
        (body + "#! section 14\n#! appendix\n", "unknown annotation"),
        (body + "`wp.diagnose(s, d)`\n#! section 14\n#! end\n", "outside the '#! section 14'"),
    ):
        assert any(needle in p for p in quickref.quick_reference_problems(bad, CHECKLIST)), bad
    # list labels are not numbers, in either text
    assert quickref.numbers_in("3. `wp.x(a)`\n12) `b`") == []


def test_the_committed_quick_reference_meets_section_3() -> None:
    """The test PREREGISTRATION.md section 3 describes, on the committed file (written by
    build track A; skipped until it exists)."""
    path = PRE / quickref.QUICK_REFERENCE
    if not path.is_file():
        pytest.skip("quick-reference.txt is written in the build phase")
    assert quickref.quick_reference_problems(path.read_text("utf-8"), CHECKLIST) == []
    assert arms.preamble_problems("lib-directed") == []


def _preambles(tmp: Path, quick_reference: str | None = GOOD_QR) -> Path:
    d = tmp / "preambles"
    shutil.copytree(PRE, d)
    (d / quickref.QUICK_REFERENCE).unlink(missing_ok=True)
    if quick_reference is not None:
        (d / quickref.QUICK_REFERENCE).write_text(quick_reference, "utf-8")
    return d


def test_lib_preambles_need_a_valid_quick_reference(tmp_path: Path) -> None:
    missing = _preambles(tmp_path / "a", None)
    with pytest.raises(ValueError, match=r"missing.*quick-reference.txt"):
        arms.preamble("lib-directed", missing, readiness=True)
    bad = _preambles(tmp_path / "b", GOOD_QR + "Use a Hampel filter\n")
    with pytest.raises(ValueError, match="hampel"):
        arms.preamble("lib", bad, readiness=True)
    good = _preambles(tmp_path / "c")
    text = arms.preamble("lib-directed", good, readiness=True)
    parts = text.split("\n\n")
    assert parts[0] == arms.read_preamble_file("environment-lib.txt", good)
    assert "Method checklist" in parts[1] and parts[-1] == arms.LIB_DIRECTED_DIRECTIVE
    assert quickref.shown_text(GOOD_QR) in text and "#!" not in text
    lib = arms.preamble("lib", good, readiness=True)
    assert "Method checklist" not in lib and arms.LIB_DIRECTED_DIRECTIVE not in lib
    skill = arms.preamble("code-skill", good, readiness=True)
    assert skill == arms.preamble("code-hint", good) + "\n\n" + arms.CODE_SKILL_SENTENCE
    assert "worldparts" not in skill


# ----------------------------------------------------------------------------------------
# the code-skill toolkit
# ----------------------------------------------------------------------------------------
def _toolkit(root: Path) -> Path:
    (root / "fitting").mkdir(parents=True)
    (root / "README.md").write_text("Reference toolkit: pump and filter fits.\n", "utf-8")
    (root / "fitting" / "wls.py").write_text(
        "import numpy as np\n\n\ndef wls(a, b, w):\n    return np.linalg.lstsq(a * w, b * w)\n",
        "utf-8",
    )
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", "utf-8")
    (root / "fitting" / "__pycache__").mkdir()
    (root / "fitting" / "__pycache__" / "wls.cpython-312.pyc").write_bytes(b"\0\1")
    return root


def test_toolkit_manifest_copy_and_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _toolkit(tmp_path / "tk")
    monkeypatch.delenv(toolkit.TOOLKIT_ENV, raising=False)
    with pytest.raises(bundles.ConfigError, match="WPBENCH_OPS_TOOLKIT"):
        toolkit.load_toolkit()
    monkeypatch.setenv(toolkit.TOOLKIT_ENV, str(root))
    tk = toolkit.load_toolkit()
    assert sorted(tk.files) == ["README.md", "fitting/wls.py"] and tk.lines == 6
    assert tk.files["README.md"] == hashlib.sha256((root / "README.md").read_bytes()).hexdigest()
    assert tk.manifest()["digest"] == tk.digest and tk.manifest()["n_files"] == 2
    copied = toolkit.copy_toolkit(tk, tmp_path / "work" / "reference")
    assert copied == ["README.md", "fitting/wls.py"]
    assert not (tmp_path / "work" / "reference" / ".git").exists()
    (root / "fitting" / "wls.py").write_text("changed\n", "utf-8")
    with pytest.raises(RuntimeError, match="changed since the run started"):
        toolkit.copy_toolkit(tk, tmp_path / "work2")
    # a toolkit whose text would contaminate a code-skill session is refused
    for text, needle in (
        ("import worldparts as wp\n", "worldparts library"),
        ("# see x.truth.json\n", "truth.json"),
        ("# benchmarks/operations/harness\n", "benchmarks/operations"),
    ):
        (root / "fitting" / "wls.py").write_text(text, "utf-8")
        with pytest.raises(bundles.ConfigError, match=re.escape(needle)):
            toolkit.load_toolkit()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(bundles.ConfigError, match="no files"):
        toolkit.load_toolkit(empty)


def test_code_arms_still_flag_worldparts_and_lib_arms_may_use_it() -> None:
    from benchmarks.composition.harness.grading import StreamSummary

    s = StreamSummary(tool_input_texts=[json.dumps({"command": 'python -c "import worldparts"'})])
    for arm in ("code+", "code-hint", "code-skill"):
        assert any("worldparts library" in r for r in markers.contamination(s, arm)), arm
    for arm in ("lib-directed", "lib"):
        assert markers.contamination(s, arm) == []
        repo = StreamSummary(tool_input_texts=[json.dumps({"command": f"type {REPO}\\uv.lock"})])
        assert markers.contamination(repo, arm)
        truth = StreamSummary(tool_input_texts=[json.dumps({"command": "cat ../x.truth.json"})])
        assert markers.contamination(truth, arm)


# ----------------------------------------------------------------------------------------
# the lib environment
# ----------------------------------------------------------------------------------------
class _FakeRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append([str(a) for a in argv])
        out = ""
        if "freeze" in argv:
            out = "numpy==2.5.3\nworldparts==0.1.0\n"
        elif "-c" in argv:
            out = '{"numpy": "2.5.3", "worldparts": "0.1.0"}'
        return subprocess.CompletedProcess(argv, 0, out, "")


def _fake_wheel(out_dir: Path, *a: Any, **k: Any) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wheel = out_dir / "worldparts-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("worldparts/__init__.py", "x = 1\n")
        z.writestr("worldparts-0.1.0.dist-info/METADATA", "Name: worldparts\n")
    return wheel


def _code_plus(d: Path) -> Path:
    (d / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    (d / "stamp.json").write_text(json.dumps({
        "request": {"python": "3.12", "packages": {"numpy": "2.5.3", "lmfit": None}},
        "installed": {"numpy": "2.5.3", "lmfit": "1.3.4", "joblib": "1.5.0"},
    }), "utf-8")  # fmt: skip
    return d


def test_lib_env_is_code_plus_plus_the_wheel_keyed_by_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeRun()
    monkeypatch.setattr(env.subprocess, "run", fake)
    monkeypatch.setattr(env.shutil, "which", lambda name, **k: f"{name}.exe")
    monkeypatch.setattr(env, "build_worldparts_wheel", _fake_wheel)
    monkeypatch.setattr(env, "installed_constraints",
                        lambda: {"PyYAML": "6.0.2", "numpy": "2.5.3",
                                 "worldparts": "0.1.0"})  # fmt: skip
    monkeypatch.setattr(env, "worldparts_source_hash", lambda repo: "src-sha")
    state = {"commit": "abc123", "dirty": False}
    monkeypatch.setattr(env, "git_state", lambda: dict(state))
    cp = _code_plus(tmp_path / "cp")
    request = env.lib_request(cp)
    assert request["packages"] == {"numpy": "2.5.3", "lmfit": "1.3.4"}
    assert request["constraints"] == {"joblib": "1.5.0", "lmfit": "1.3.4", "numpy": "2.5.3",
                                      "pyyaml": "6.0.2"}  # fmt: skip
    assert request["worldparts"]["commit"] == "abc123" and request["code_plus_key"]
    d = tmp_path / "lib"
    assert env.ensure_lib_env(cp, d, log=lambda *a: None) == d
    venv, install, probe, version, freeze = fake.calls
    assert venv[1:3] == ["venv", "--python"]
    assert "--constraint" in install and install[-1].endswith("worldparts-0.1.0-py3-none-any.whl")
    assert {"numpy==2.5.3", "lmfit==1.3.4"} <= set(install)
    assert probe[1] == "-c" and "worldparts" in probe[2] and version[-1] == "--version"
    assert freeze[1:3] == ["pip", "freeze"]
    constraints = (d / "constraints.txt").read_text("utf-8")
    assert "pyyaml==6.0.2" in constraints and "worldparts" not in constraints
    info = env.lib_env_info(d)
    assert info is not None and info["commit"] == "abc123" and info["dirty"] is False
    wheel = d / "dist" / "worldparts-0.1.0-py3-none-any.whl"
    assert info["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert info["key"] == env.env_key(request) and info["code_plus_key"] == request["code_plus_key"]
    assert env.default_lib_dir(request).name == f"ops-lib-{info['key']}"
    env.code_env_python(d).parent.mkdir(parents=True, exist_ok=True)
    env.code_env_python(d).write_text("", "utf-8")
    fake.calls.clear()
    assert env.ensure_lib_env(cp, d, log=lambda *a: None) == d and fake.calls == []  # reused
    state["commit"] = "def456"  # another commit: another environment
    assert env.default_lib_dir(env.lib_request(cp)) != env.default_lib_dir(request)
    with pytest.raises(RuntimeError, match="not built"):
        env.lib_request(tmp_path / "nowhere")


def test_a_wheel_that_would_trip_a_marker_is_refused(tmp_path: Path) -> None:
    clean = _fake_wheel(tmp_path / "a")
    assert env.wheel_problems(clean) == []
    bad = tmp_path / "b.whl"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("worldparts/diag.py", "# tuned on benchmarks/operations/harness\n")
        z.writestr("worldparts/opsim_forms.py", "x = 1\n")
    problems = env.wheel_problems(bad)
    assert any("benchmarks/operations" in p for p in problems)
    assert any("opsim" in p for p in problems)


def test_the_package_sources_pass_the_wheel_check(tmp_path: Path) -> None:
    """What a wheel of this repository holds (the package, the README as its metadata, the
    licence) trips no marker of the lib arms; the Apache licence's 'APPENDIX' is exempt."""
    wheel = tmp_path / "worldparts-0.0.0-py3-none-any.whl"
    src = REPO / "src" / "worldparts"
    with zipfile.ZipFile(wheel, "w") as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, "worldparts/" + p.relative_to(src).as_posix())
        z.write(REPO / "README.md", "worldparts-0.0.0.dist-info/METADATA")
        z.write(REPO / "LICENSE", "worldparts-0.0.0.dist-info/licenses/LICENSE")
    assert "APPENDIX" in (REPO / "LICENSE").read_text("utf-8")
    assert env.wheel_problems(wheel) == []


# ----------------------------------------------------------------------------------------
# readiness runs through the fake CLI
# ----------------------------------------------------------------------------------------
class _Readiness:
    """A readiness run of lib-directed, lib and code-skill with the fake executable."""

    def __init__(self, tmp: Path, mp: pytest.MonkeyPatch, dirty: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.broot, self.troot, self.run_dir = tmp / "bundles", tmp / "truth", tmp / "run"
        self.ids = build_set(self.broot, self.troot)
        self.plan_path, state = tmp / "plan.json", tmp / "state"
        state.mkdir()
        self.code_env = _code_plus(tmp / "cp-env")
        self.lib_env = tmp / "lib-env"
        (self.lib_env / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
        self.lib_stamp = {
            "request": {"python": "3.12", "code_plus_key": "cpk", "packages": {},
                        "constraints": {}, "worldparts": {"version": "0.1.0",
                                                           "source_sha256": "src",
                                                           "commit": "abc123", "dirty": dirty}},
            "installed": {"worldparts": "0.1.0"},
            "wheel": {"file": "worldparts-0.1.0-py3-none-any.whl", "sha256": "f" * 64},
        }  # fmt: skip
        (self.lib_env / "stamp.json").write_text(json.dumps(self.lib_stamp), "utf-8")
        self.toolkit = _toolkit(tmp / "toolkit")
        self.preambles = _preambles(tmp)
        real_popen = subprocess.Popen

        def fake_popen(cmd: list[str], **kwargs: Any) -> Any:
            if cmd[0] != str(FAKE):
                return real_popen(cmd, **kwargs)
            self.commands.append(cmd)
            return real_popen([sys.executable, str(FAKE), *cmd[1:]], **kwargs)

        mp.setattr(runner.subprocess, "Popen", fake_popen)
        mp.setattr(cli, "ensure_code_plus_env", lambda d=None: self.code_env)
        mp.setattr(cli, "ensure_lib_env", lambda cp, d=None: self.lib_env)
        mp.setattr(cli, "_session_bash", lambda args: None)
        mp.setattr(cli, "claude_version", lambda: "2.1.280 (fake)")
        mp.setattr(arms, "PREAMBLES_DIR", self.preambles)
        mp.setenv("WPBENCH_CLAUDE", str(FAKE))
        mp.setenv("WPBENCH_OPS_TRUTH", str(self.troot))
        mp.setenv(toolkit.TOOLKIT_ENV, str(self.toolkit))
        mp.setenv("OPSFAKE_PLAN", str(self.plan_path))
        mp.setenv("OPSFAKE_STATE", str(state))

    def plan(self, plan: dict[str, Any]) -> None:
        self.plan_path.write_text(json.dumps(plan), "utf-8")

    def good(self, tid: str, command: str = "python fit.py") -> dict[str, Any]:
        return {"reply": reply(correct_answer(truth_keys(self.troot, tid))), "command": command}

    def run(self, *extra: str) -> int:
        return cli.main(["run", "--readiness", "--set", "dev", "--models", "sonnet",
                         "--out", str(self.run_dir), "--bundles", str(self.broot),
                         "--no-manifest-check", "--jobs", "1", *extra])  # fmt: skip

    def attempt(self, tid: str, arm: str, n: int = 1) -> Path:
        sid = runner.session_id("dev", "sonnet", tid, arm, 1)
        return self.run_dir / "sessions" / sid / f"attempt-{n}"


def _bin(d: Path) -> Path:
    return d / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def test_readiness_run_gives_each_arm_its_prompt_environment_and_workdir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.MonkeyPatch.context() as mp:
        f = _Readiness(tmp_path, mp)
        ta, tb = f.ids[0], f.ids[-1]
        uses_wp = 'python -c "import worldparts; print(worldparts.__version__)"'
        f.plan({
            f"sonnet|{ta}|lib-directed": [f.good(ta, uses_wp)],
            f"sonnet|{ta}|code-skill": [f.good(ta, uses_wp)],
            f"sonnet|{ta}|lib": [f.good(ta)],
            f"sonnet|{tb}|lib-directed": [f.good(tb)],
            f"sonnet|{tb}|code-skill": ["killed", f.good(tb, "python reference/fitting/wls.py")],
            f"sonnet|{tb}|lib": [f.good(tb)],
        })  # fmt: skip
        assert f.run("--tasks", ta, tb, "--arms", "lib-directed", "lib", "code-skill") == 0
        assert len(f.commands) == 6
        assert f.run("--rerun-flagged") == 0  # the killed code-skill session, same toolkit
        assert len(f.commands) == 7
        assert cli.main(["grade", str(f.run_dir)]) == 0
        capsys.readouterr()
        meta = json.loads((f.run_dir / "run.json").read_text("utf-8"))
        # the toolkit changed: the run cannot be resumed with it
        (f.toolkit / "README.md").write_text("another toolkit\n", "utf-8")
        with pytest.raises(SystemExit, match="toolkit_digest"):
            f.run("--tasks", ta, "--arms", "code-skill")
    assert meta["readiness"] is True and meta["arms"] == ["code-skill", "lib", "lib-directed"]
    assert meta["models"] == ["sonnet"]
    assert meta["lib_env"]["wheel_sha256"] == "f" * 64 and meta["lib_env"]["commit"] == "abc123"
    lib_key = env.env_key(f.lib_stamp["request"])
    assert meta["lib_key"] == lib_key == meta["lib_env"]["key"]
    assert meta["toolkit_digest"] == meta["toolkit"]["digest"]
    assert sorted(meta["toolkit"]["files"]) == ["README.md", "fitting/wls.py"]
    code_key = env.env_key(json.loads((f.code_env / "stamp.json").read_text("utf-8"))["request"])
    assert meta["code_plus_key"] == code_key
    for arm, envdir, key in (("lib-directed", f.lib_env, lib_key), ("lib", f.lib_env, lib_key),
                             ("code-skill", f.code_env, code_key)):  # fmt: skip
        adir = f.attempt(ta, arm)
        probe = json.loads((adir / "workdir" / "fake_probe.json").read_text("utf-8"))
        assert Path(probe["path_head"]) == _bin(envdir), arm
        assert probe["env"]["VIRTUAL_ENV"] == str(envdir / ".venv")
        assert not any(k.upper().startswith("WPBENCH") for k in probe["env"])
        outcome = json.loads((adir / "outcome.json").read_text("utf-8"))
        assert outcome["env_key"] == key
        # the audit reads it: no arm label (the random tmp_dir name aside)
        assert arm not in json.dumps({k: v for k, v in outcome.items() if k != "tmp_dir"})
        prompt = probe["prompt"]
        has_reference = any(p.startswith("reference/") for p in probe["cwd_files"])
        assert has_reference is (arm == "code-skill")
        kept = {p.relative_to(adir / "workdir").as_posix()
                for p in (adir / "workdir").rglob("*") if p.is_file()}  # fmt: skip
        assert kept == {"fake_probe.json"}  # the unchanged toolkit is not kept
        assert "#!" not in prompt and "task.json" not in probe["cwd_files"]
        if arm == "code-skill":
            assert {"reference/README.md", "reference/fitting/wls.py"} <= set(probe["cwd_files"])
            assert "worldparts" not in prompt and "Method checklist" in prompt
            assert arms.CODE_SKILL_SENTENCE in prompt
            extras = json.loads((adir / runner.EXTRAS_FILE).read_text("utf-8"))
            assert extras == {"reference": {"digest": meta["toolkit_digest"], "files": 2}}
        else:
            assert quickref.shown_text(GOOD_QR) in prompt
            assert arms.read_preamble_file("environment-lib.txt", f.preambles) in prompt
            assert ("Method checklist" in prompt) is (arm == "lib-directed")
            assert (arms.LIB_DIRECTED_DIRECTIVE in prompt) is (arm == "lib-directed")
            assert not (adir / runner.EXTRAS_FILE).exists()
    rerun = f.attempt(tb, "code-skill", 2)
    probe = json.loads((rerun / "workdir" / "fake_probe.json").read_text("utf-8"))
    assert "reference/fitting/wls.py" in probe["cwd_files"]
    recs = {(r["task"], r["arm"]): r for r in report.load_records([f.run_dir])}
    assert len(recs) == 6 and all(r["passed"] for r in recs.values())
    assert recs[(ta, "lib-directed")]["contamination"] is None
    assert any("worldparts library" in c for c in recs[(ta, "code-skill")]["contamination"])
    assert recs[(tb, "code-skill")]["attempt"] == 2


def test_readiness_run_refusals_and_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.MonkeyPatch.context() as mp:
        f = _Readiness(tmp_path, mp, dirty=True)
        ta = f.ids[0]
        f.plan({})
        # a dry run prints the prompts, the environment and the toolkit copy; runs nothing
        assert f.run("--tasks", ta, "--arms", "lib-directed", "code-skill", "--dry-run") == 0
        out = capsys.readouterr().out
        assert "2 session(s); nothing was run (--dry-run)" in out
        assert "reference/: a copy of the toolkit" in out and arms.LIB_DIRECTED_DIRECTIVE in out
        assert str(cli.LIB_ENV_PLACEHOLDER) in out and not f.commands
        # the wheel's sources differ from the commit: refused
        with pytest.raises(SystemExit, match="commit the worldparts sources"):
            f.run("--tasks", ta, "--arms", "lib")
        # without the toolkit, code-skill is refused
        mp.delenv(toolkit.TOOLKIT_ENV)
        with pytest.raises(SystemExit, match="WPBENCH_OPS_TOOLKIT"):
            f.run("--tasks", ta, "--arms", "code-skill")
        # a quick reference over the limits: the lib arms are refused
        (f.preambles / quickref.QUICK_REFERENCE).write_text(GOOD_QR + "Use 3 starts\n", "utf-8")
        with pytest.raises(SystemExit, match="the number 3"):
            f.run("--tasks", ta, "--arms", "lib", "--dry-run")
        (f.preambles / quickref.QUICK_REFERENCE).unlink()
        with pytest.raises(SystemExit, match="missing"):
            f.run("--tasks", ta, "--dry-run")
        # without --readiness the arms stay unavailable
        with pytest.raises(SystemExit, match="freeze-1"):
            cli.main(["run", "--arms", "code-skill", "--dry-run", "--bundles", str(f.broot)])
    assert not f.commands


# ----------------------------------------------------------------------------------------
# scoring scripted answers
# ----------------------------------------------------------------------------------------
def _scripted(tmp: Path) -> dict[str, Any]:
    broot, troot, answers = tmp / "bundles", tmp / "truth", tmp / "answers"
    ids = build_set(broot, troot)
    unvalidated = ids[5]
    manifest = tmp / "truth-dev.sha256"
    manifest.write_text(_manifest(troot, (unvalidated,)), "utf-8")
    for tid in ids:
        for k in (1, 2, 3):
            p = answers / tid / f"r{k}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(correct_answer(truth_keys(troot, tid))), "utf-8")
    a, b, c = ids[0], ids[8], ids[12]
    (answers / a / "r2.json").write_text(json.dumps(wrong_answer(truth_keys(troot, a))), "utf-8")
    (answers / b / "r3.json").unlink()
    (answers / c / "r1.json").write_text("not json at all", "utf-8")
    (answers / a / "r9.json").write_text("{}", "utf-8")  # no such realisation
    return {"ids": ids, "broot": broot, "troot": troot, "answers": answers,
            "manifest": manifest, "unvalidated": unvalidated, "wrong": (a, b, c)}  # fmt: skip


def test_score_full_mode_for_the_owner(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = _scripted(tmp_path)
    args = ["score", str(s["answers"]), "--bundles", str(s["broot"]), "--truth", str(s["troot"]),
            "--truth-manifest", str(s["manifest"])]  # fmt: skip
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    a, b, c = s["wrong"]
    assert f"{a}: 2/3 realisation(s) passed; r1 PASS; r2 FAIL [failed: " in out
    assert f"{b}: 2/3 realisation(s) passed" in out and "r3 FAIL [no answer file]" in out
    assert f"{c}: 2/3 realisation(s) passed; r1 FAIL [no answer (" in out
    assert "45 realisation(s) of 15 validated task(s) scored" in out
    assert f"not validated yet (not scored): {s['unvalidated']}" in out
    assert f"ignored): {a}/r9.json" in out
    assert "realisation pass rate: 93 % (42/45)" in out and "at least 90 %" in out
    doc = json.loads((s["answers"] / scoring.SCORE_FILE).read_text("utf-8"))
    assert doc["realisations_scored"] == 45 and doc["passed"] == 42
    assert doc["rate"] == pytest.approx(42 / 45) and doc["not_validated"] == [s["unvalidated"]]
    row = next(r for r in doc["realisations"] if r["task"] == a and r["realisation"] == 2)
    assert row["passed"] is False and row["keys"] and len(row["bundle_digest"]) == 64
    missing = next(r for r in doc["realisations"] if r["task"] == b and r["realisation"] == 3)
    assert missing["missing"] is True and "no answer file" in missing["parse_error"]


def test_scripted_answers_are_graded_as_a_final_reply(tmp_path: Path) -> None:
    build_set(tmp_path / "b", tmp_path / "t")
    task = bundles.load_tasks("dev", tmp_path / "b")[0]
    truth = bundles.load_truth(task, tmp_path / "t")
    answer = correct_answer(truth["keys"])
    p = tmp_path / "r1.json"
    p.write_text(json.dumps(answer, indent=2) + "\n", "utf-8")
    from benchmarks.operations.harness.grading import grade_answer

    graded = scoring.grade_file(task, truth, p).to_dict()
    assert graded == grade_answer(task, truth, reply(answer)).to_dict()
    assert graded["passed"] is True and graded["format_issues"] == []
    p.write_text('{"head_deficit_bep_pp": "8.4 pp",}', "utf-8")  # the same lenient reading
    g = scoring.grade_file(task, truth, p)
    assert g.parse_error is None and any("number given as a string" in i for i in g.format_issues)
    p.write_text("[1, 2]", "utf-8")
    assert scoring.grade_file(task, truth, p).parse_error == "the answer block is not a JSON object"


def test_score_pass_fail_prints_only_verdicts_and_logs_at_most_three_evaluations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _scripted(tmp_path)
    monkeypatch.setenv("WPBENCH_OPS_TRUTH", str(s["troot"]))
    args = ["score", str(s["answers"]), "--bundles", str(s["broot"]), "--truth-manifest",
            str(s["manifest"]), "--pass-fail", "--session", "track-a-1"]  # fmt: skip
    for n in (1, 2, 3):
        assert cli.main(args) == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        verdicts = [ln for ln in lines if re.fullmatch(r"ops-\S+ r[123] (PASS|FAIL)", ln)]
        assert len(verdicts) == 45 and sum(v.endswith("FAIL") for v in verdicts) == 3
        assert lines[-2] == (
            "42 of 45 realisation(s) passed (45 realisation(s) of 15 task(s) scored)"
        )
        assert lines[-1] == f"evaluation {n} of 3 for session track-a-1"
        assert len(lines) == 47
        for word in ("error", "interval", "8.4", "37.5", "head_deficit", "missing", "truth",
                     str(s["troot"]), "cw_category", s["unvalidated"]):  # fmt: skip
            assert word not in out
    assert not (s["answers"] / scoring.SCORE_FILE).exists()
    with pytest.raises(SystemExit, match="has had its 3 evaluations"):
        cli.main(args)
    log = (s["answers"] / scoring.PASS_FAIL_LOG).read_text("utf-8").splitlines()
    heads = [ln for ln in log if " evaluation=" in ln]
    assert len(heads) == 3 and all("session=track-a-1" in ln for ln in heads)
    assert heads[0].split(" ", 1)[1] == "session=track-a-1 evaluation=1 realisations=45 passed=42"
    assert len(log) == 3 * (1 + 45) + 1 and "refused" in log[-1]
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d session=track-a-1 ops-\S+/r1 (PASS|FAIL)$",
                    log[1])  # fmt: skip
    # another session name has its own three
    other = [a if a != "track-a-1" else "track-b-1" for a in args]
    assert cli.main(other) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "evaluation 1 of 3 for session track-b-1"
    # the truth cannot be read: an error that names nothing, and no evaluation is logged
    monkeypatch.delenv("WPBENCH_OPS_TRUTH")
    third = [a if a != "track-a-1" else "track-c-1" for a in args]
    with pytest.raises(SystemExit) as exc:
        cli.main(third)
    assert "truth could not be read" in str(exc.value) and str(s["troot"]) not in str(exc.value)
    assert scoring.evaluations_logged(s["answers"] / scoring.PASS_FAIL_LOG, "track-c-1") == 0
    with pytest.raises(SystemExit, match="--session"):
        cli.main(args[:-2])
    with pytest.raises(SystemExit, match="development set"):
        cli.main([*args, "--set", "test"])


def test_the_builder_wrapper_reads_the_truth_location_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _scripted(tmp_path)
    path = REPO / "tools" / "ops_passfail.py"
    spec = importlib.util.spec_from_file_location("ops_passfail", path)
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setattr(cli, "truth_manifest_path", lambda set_name: s["manifest"])
    monkeypatch.setenv("WPBENCH_OPS_BUNDLES", str(s["broot"]))
    monkeypatch.delenv("WPBENCH_OPS_TRUTH", raising=False)
    assert wrapper.main([str(s["answers"]), "--session", "w1"]) == 2
    assert "ask the owner" in capsys.readouterr().err
    monkeypatch.setenv("WPBENCH_OPS_TRUTH", str(s["troot"]))
    assert wrapper.main([str(s["answers"]), "--session", "w1"]) == 0
    out = capsys.readouterr().out
    assert "42 of 45 realisation(s) passed" in out and "evaluation 1 of 3 for session w1" in out
    with pytest.raises(SystemExit):
        wrapper.main([str(s["answers"]), "--session", "w1", "--truth", str(s["troot"])])
    with pytest.raises(SystemExit):
        wrapper.main([str(s["answers"])])  # --session is required


# ----------------------------------------------------------------------------------------
# the firewall audit
# ----------------------------------------------------------------------------------------
def _use(name: str, sid: str = "S1", **inp: Any) -> str:
    return json.dumps({"type": "assistant", "sessionId": sid, "message": {"content": [
        {"type": "text", "text": "Working."},
        {"type": "tool_use", "id": "t", "name": name, "input": inp}]}})  # fmt: skip


def _transcript(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path


ALLOWED = [
    _use("Read", file_path="C:/Users/raimo/world-model/opsbench-bundles/dev/ops-f1-001/r1/task.md"),
    _use("Read", file_path="C:\\Users\\raimo\\world-model\\worldparts\\benchmarks\\operations"
                           "\\results\\stage0\\sessions\\ab12\\attempt-1\\stream.jsonl"),
    _use("Bash", command="uv run pytest tests/test_diagnosis.py -q"),
    _use("Grep", pattern="record", path="src/worldparts"),
    _use("Bash", command="cat tools/ops_passfail.py && uv run python tools/ops_passfail.py --help"),
    _use("Bash", command="ls benchmarks/composition/reports/"),
    _use("Bash", command="python -c \"print('truth')\" && git log --oneline -5"),
    _use("Bash", command="git diff src/worldparts/diagnosis.py"),
    _use("Read", file_path="C:/Users/raimo/world-model/worldparts/benchmarks/operations/"
                           "PREREGISTRATION.md"),
    _use("TodoWrite", todos=[{"content": "read the truth.json? no"}]),
    json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                "tool_use_id": "t", "content": "see worldparts-opsbench/truth"}]}}),
    # the Stage 0 transcripts, read without the grader's files
    _use("Bash", command="grep -rn cannot_determine benchmarks/operations/results/stage0 "
                         "--include='*.jsonl'"),
    _use("Grep", pattern="x", path="C:/Users/raimo/world-model/worldparts/benchmarks/operations/"
                                   "results/stage0", glob="*.jsonl"),
    _use("Bash", command="cat benchmarks/operations/results/stage0/sessions/*/attempt-1/"
                         "prompt.txt"),
    _use("Bash", command="grep -rn x benchmarks/operations/results/stage0/sessions/ab12/"
                         "attempt-1/"),
    _use("Bash", command="ls -R benchmarks/operations/results/stage0"),
    _use("Bash", command="cat benchmarks/operations/results/stage0/index.json"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (_use("Read", file_path="C:\\Users\\raimo\\world-model\\worldparts-opsbench\\APPENDIX.md"),
         "worldparts-opsbench"),
        (_use("Bash", command="cat ../worldparts/benchmarks/operations/results/stage0/sessions/"
                              "ab12/record.json"), "record.json"),
        (_use("Read", file_path="benchmarks/operations/results/stage0/summary.md"), "summary"),
        (_use("Glob", pattern="**/headroom*.json"), "headroom"),
        (_use("Bash", command="tail benchmarks/operations/results/stage0/pass-fail.log"),
         "pass-fail.log"),
        (_use("Grep", pattern="calibrate", path="C:/Users/raimo/world-model/reports"),
         "reports"),
        (_use("Bash", command="ls ../research_notes"), "research_notes"),
        (_use("Read", file_path="C:/Users/raimo/.claude/projects/C--x/memory/MEMORY.md"),
         ".claude/projects"),
        (_use("Bash", command="echo $WPBENCH_OPS_TRUTH"), "WPBENCH_OPS_TRUTH"),
        (_use("PowerShell", command="Get-Content $env:WPBENCH_OPS_TRUTH\\dev\\x.truth.json"),
         "truth file"),
        (_use("Bash", command="ls /c/secret-truth/dev"), "the truth folder"),
        (_use("Bash", command="python -c 'import opsim'"), "opsim"),
        (_use("Bash", command="git log -p -- benchmarks/operations/PREREGISTRATION.md"),
         "draft history of PREREGISTRATION.md (git log)"),
        (_use("Bash", command="git show HEAD~9:benchmarks/operations/PREREGISTRATION.md | head"),
         "(git show)"),
        (_use("PowerShell", command="git -C C:\\x diff 9cb3564 HEAD -- "
                                    "benchmarks\\operations\\PREREGISTRATION.md"), "(git diff)"),
        (_use("Bash", command="ls C:/shared/elsewhere/notes"), "the forbidden path"),
        (_use("Bash", command="grep -rn cannot_determine benchmarks/operations/results/stage0"),
         "run directory"),
        (_use("Bash", command="rg -n lo90 results/stage0"), "run directory"),
        (_use("Grep", pattern="x", path="C:/Users/raimo/world-model/worldparts/benchmarks/"
                                        "operations/results/stage0"), "run directory"),
        (_use("Bash", command="cat benchmarks/operations/results/stage0/sessions/*/*"),
         "run directory"),
        (_use("PowerShell", command="Get-ChildItem -Recurse benchmarks\\operations\\results\\"
                                    "stage0 | Select-String cannot"), "run directory"),
    ],
)  # fmt: skip
def test_forbidden_reads_are_violations(tmp_path: Path, line: str, reason: str) -> None:
    path = _transcript(tmp_path / "s.jsonl", [*ALLOWED, line])
    res = firewall.audit_transcripts(
        [path], truth_roots=["C:/secret-truth"], extra=["C:/shared/elsewhere"]
    )
    assert {f.line for f in res.violations} == {len(ALLOWED) + 1}, res.findings
    assert any(reason.lower() in f.reason.lower() for f in res.violations), res.violations
    assert res.verdict.startswith("VIOLATIONS") and res.warnings == []


def test_a_clean_transcript_and_the_warnings(tmp_path: Path) -> None:
    path = _transcript(tmp_path / "s.jsonl", ALLOWED)
    res = firewall.audit_transcripts([path])
    assert res.findings == [] and res.verdict == "CLEAN"
    t = res.transcripts[0]
    assert t.session == "S1" and t.tool_calls == len(ALLOWED) - 1
    assert t.unscanned == {"TodoWrite": 1}
    assert t.evaluations == 0  # reading the wrapper and --help are not evaluations
    warn = _transcript(tmp_path / "w.jsonl", [
        *ALLOWED,
        _use("Bash", command="git log -p -3"),
        _use("Bash", command="git show 08044ea"),
        _use("Bash", command="git show --stat 08044ea"),
        _use("Write", file_path="notes/summary.md", content="record.json is not read"),
        "{not json",
    ])  # fmt: skip
    res = firewall.audit_transcripts([warn])
    n = len(ALLOWED)
    assert res.violations == [] and [f.line for f in res.warnings] == [n + 1, n + 2, n + 4, 0]
    assert res.warnings[2].tool == "Write" and "summary" in res.warnings[2].reason
    assert res.verdict == "CLEAN, with 4 warning(s) to review"


def test_evaluations_are_counted_per_session_across_subagents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    call = "uv run python tools/ops_passfail.py out --session a1"
    main = _transcript(tmp_path / "S1.jsonl", [
        _use("Bash", command=call),
        _use("Bash", command=f"{call} && echo done"),
        _use("PowerShell", command="uv run python -m benchmarks.operations.harness score out "
                                   "--pass-fail --session a1"),
    ])  # fmt: skip
    sub = _transcript(tmp_path / "S1" / "subagents" / "agent-1.jsonl", [
        _use("Bash", command="uv run python tools\\ops_passfail.py out --session a2")])  # fmt: skip
    other = _transcript(tmp_path / "S2.jsonl", [_use("Bash", sid="S2", command=call)] * 3)
    res = firewall.audit_transcripts([main, sub, other])
    assert res.evaluations == {"S1": 4, "S2": 3}
    assert [f.reason for f in res.violations] == [
        "session S1: 4 pass/fail evaluations (at most 3 per session)"
    ]
    assert any("several session names" in f.reason for f in res.warnings)
    code = cli.main(["firewall-audit", str(main), str(sub), str(other), "--json",
                     str(tmp_path / "fw.json")])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 1 and "verdict: VIOLATIONS (1)" in out and "S1: 4 of at most 3" in out
    doc = json.loads((tmp_path / "fw.json").read_text("utf-8"))
    assert doc["clean"] is False and doc["evaluations"] == {"S1": 4, "S2": 3}
    assert cli.main(["firewall-audit", str(other)]) == 0
    assert "verdict: CLEAN" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no transcript"):
        cli.main(["firewall-audit", str(tmp_path / "missing.jsonl")])
