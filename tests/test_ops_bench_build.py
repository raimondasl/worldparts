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
import pickle
import re
import shutil
import subprocess
import sys
import threading
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
#: The folder that holds the repository (and, on the owner's machine, the private folder).
WM = REPO.parent.as_posix()
UNCOMMITTED_PREAMBLES = cli.uncommitted_preambles


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
#! end
"""


def test_a_quick_reference_within_the_limits_passes() -> None:
    assert quickref.quick_reference_problems(GOOD_QR, CHECKLIST) == []
    ref = quickref.parse(GOOD_QR)
    assert len(ref.section14) == 4 and len(ref.shown) == 8
    assert "#!" not in quickref.shown_text(GOOD_QR)
    assert sorted(set(quickref.numbers_in(CHECKLIST)), key=float) == ["1", "90"]
    # names, versions and option names are code, not numbers or prose
    for line in ("`python3.12` `wp.System.from_dict(doc)` -> `System`", "`--max-iter`",
                 '`wp.load(path, format="long")`'):  # fmt: skip
        text = GOOD_QR.replace("#! end\n", "#! end\n" + line + "\n")
        assert quickref.quick_reference_problems(text, CHECKLIST) == [], line


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
        # the review's findings: numbers written as .5 or 1_000
        ("`wp.fit(x, level=.95)` -> `Fit`", "the number .95"),
        ("`sigma` .05", "the number .05"),
        ("`wp.fit(x, max_iter=1_000)`", "the number 1_000"),
        ("`wp.fit(x, dchi2=.5)`", "the number .5"),
        # prose in string literals, hyphen-joined backtick phrases and long names
        ('`wp.fit(x, "prefer pairs unless chi-square above two; drop spikes first")`',
         "method statement (above, first, prefer, unless)"),
        ('wp.fit(x, "always use AIC and never BIC when noisy")', "method statement (always"),
        ('`mode="hampel"`', "hampel"),
        ("`always-drop-the-first-day-then-fit-hourly-means`", "method statement (always, first"),
        ("`always_drop_the_first_day()`", "method statement (always, first)"),
        # prose joined by '/' or ',', and words that are not ASCII
        ("Spikes with a Hampel/median filter", "hampel, median"),
        ("aggregate,hourly,then,fit", "method statement (then)"),
        ("the naïve fit", "not ASCII: naïve"),
        # the checklist's own procedure (section 3: no procedure at all)
        ("Prefer none, then single faults; fit pairs of faults only when no single",
         "not even the checklist's"),
        # a few long lines cannot replace many short ones
        ("`wp.f(x)` " * 15, "150 characters; at most 120"),
    ],
)  # fmt: skip
def test_numbers_and_method_statements_absent_from_the_checklist_fail(
    line: str, needle: str
) -> None:
    text = GOOD_QR.replace("#! end\n", "#! end\n" + line + "\n")
    problems = quickref.quick_reference_problems(text, CHECKLIST)
    assert any(needle in p for p in problems), problems


def test_section14_names_come_from_the_worldparts_api(tmp_path: Path) -> None:
    names = quickref.section14_names()
    assert set(quickref.SECTION14_BASE) <= set(names)
    assert {"MeasurementSet", "CalibrationResult", "DiagnosisResult"} <= set(names)
    assert "System" not in names and "convert" not in names
    # a name build track A adds to a section-14 module counts at once (a SCADA reader)
    src = tmp_path / "worldparts"
    src.mkdir()
    (src / "__init__.py").write_text(
        "from worldparts.measurements import read_scada_long\nfrom .scada import Tags\n"
        "from worldparts.system import System\n",
        "utf-8",
    )
    assert {"read_scada_long", "Tags"} <= set(quickref.section14_names(src))
    assert "System" not in quickref.section14_names(src)
    outside = GOOD_QR.replace("#! section 14\n", "`wp.MeasurementSet.load(p)`\n#! section 14\n")
    assert any("section-14 name outside" in p
               for p in quickref.quick_reference_problems(outside, CHECKLIST))  # fmt: skip
    assert (
        quickref.section14_lines()
        == sum(len(p.read_text("utf-8").splitlines()) for p in quickref.section14_sources())
        and quickref.section14_lines() > 800
    )


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


def test_the_toolkit_size_cap_and_text_only(tmp_path: Path) -> None:
    """Section 3: at most 800 lines or worldparts' section-14 line count, whichever is
    larger; a binary file (an archive holding code) would escape the cap and the markers."""
    assert toolkit.size_cap() == max(800, quickref.section14_lines()) > 800
    root = _toolkit(tmp_path / "tk")
    assert toolkit.load_toolkit(root, cap=6).lines == 6
    (root / "big.py").write_text("x = 1\n" * 5000, "utf-8")
    with pytest.raises(bundles.ConfigError, match="5006 lines; section 3 caps the toolkit at 5"):
        toolkit.load_toolkit(root, cap=5)
    (root / "big.py").write_text("x = 1\n" * (toolkit.size_cap() + 1), "utf-8")
    with pytest.raises(bundles.ConfigError, match="caps the toolkit"):
        toolkit.load_toolkit(root)
    (root / "big.py").unlink()
    # a pickle (its first byte, 0x80, is never UTF-8) holding code the markers never see
    (root / "impl.pkl").write_bytes(pickle.dumps({"src": "import worldparts\n" * 3000}))
    with pytest.raises(bundles.ConfigError, match=r"impl\.pkl: not UTF-8 text"):
        toolkit.load_toolkit(root)


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
    state = {"commit": "abc123", "head": "head1", "dirty": False}
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
    assert info["repo_head"] == "head1" and "head" not in request["worldparts"]
    wheel = d / "dist" / "worldparts-0.1.0-py3-none-any.whl"
    assert info["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert info["key"] == env.env_key(request) and info["code_plus_key"] == request["code_plus_key"]
    assert env.default_lib_dir(request).name == f"ops-lib-{info['key']}"
    env.code_env_python(d).parent.mkdir(parents=True, exist_ok=True)
    env.code_env_python(d).write_text("", "utf-8")
    fake.calls.clear()
    assert env.ensure_lib_env(cp, d, log=lambda *a: None) == d and fake.calls == []  # reused
    # a commit that leaves the wheel's sources alone (a FREEZES.md entry) keeps the key, so
    # a stopped readiness run can resume and re-run on its environment
    state["head"] = "head2"
    assert env.lib_request(cp) == request
    assert env.ensure_lib_env(cp, d, log=lambda *a: None, rebuild=False) == d
    assert fake.calls == []
    state["commit"] = "def456"  # the sources changed: another environment
    assert env.default_lib_dir(env.lib_request(cp)) != env.default_lib_dir(request)
    # ... and a directory that holds the recorded one is never rebuilt in place
    with pytest.raises(RuntimeError, match="never rebuilt in place"):
        env.ensure_lib_env(cp, d, log=lambda *a: None)
    with pytest.raises(RuntimeError, match="never rebuilt in place"):
        env.ensure_lib_env(cp, d, log=lambda *a: None, rebuild=False)
    assert fake.calls == [] and (env.lib_env_info(d) or {})["commit"] == "abc123"
    with pytest.raises(RuntimeError, match="not built"):
        env.lib_request(tmp_path / "nowhere")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout  # fmt: skip


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_dirty_check_sees_ignored_files_the_wheel_would_pack(tmp_path: Path) -> None:
    """A git-ignored file under src/worldparts (an example .inp) goes into the wheel, so it
    makes the sources differ from the commit; caches do not."""
    repo = tmp_path / "repo"
    (repo / "src" / "worldparts" / "__pycache__").mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n*.py[oc]\n*.inp\n", "utf-8")
    (repo / "src" / "worldparts" / "__init__.py").write_text("x = 1\n", "utf-8")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        (repo / name).write_text(name + "\n", "utf-8")
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", "x")  # fmt: skip
    (repo / "src" / "worldparts" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    assert env.uncommitted(list(env.WHEEL_SOURCES), repo) == []
    state = env.git_state(repo)
    assert state["dirty"] is False and state["commit"] == state["head"]
    (repo / "src" / "worldparts" / "example.inp").write_text("[JUNCTIONS]\n", "utf-8")
    assert _git(repo, "status", "--porcelain", "--", *env.WHEEL_SOURCES) == ""  # the old check
    changed = env.uncommitted(list(env.WHEEL_SOURCES), repo)
    assert changed == ["!! src/worldparts/example.inp"]
    assert env.git_state(repo)["dirty"] is True


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
        self.lib_calls: list[tuple[Any, dict[str, Any]]] = []

        def lib(cp: Path, d: Path | None = None, **k: Any) -> Path:
            self.lib_calls.append((d, k))
            return self.lib_env

        mp.setattr(cli, "ensure_lib_env", lib)
        mp.setattr(cli, "_session_bash", lambda args: None)
        # the preambles are a scratch copy here, outside git (a real run checks the commit)
        mp.setattr(cli, "uncommitted_preambles", lambda arms: [])
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
        # the re-run used the run's lib environment, never rebuilt in place
        assert f.lib_calls[0] == (None, {}) and all(
            d == f.lib_env and k == {"rebuild": False} for d, k in f.lib_calls[1:]
        )
        # the toolkit changed: the run cannot be resumed with it
        (f.toolkit / "README.md").write_text("another toolkit\n", "utf-8")
        with pytest.raises(SystemExit, match="toolkit_digest"):
            f.run("--tasks", ta, "--arms", "code-skill")
        # the quick reference changed: neither a resume nor a re-run takes it
        qr = f.preambles / quickref.QUICK_REFERENCE
        qr_sha = hashlib.sha256(qr.read_bytes()).hexdigest()
        qr.write_text(GOOD_QR.replace("`values`", "`value`"), "utf-8")
        with pytest.raises(SystemExit, match=r"preamble quick-reference\.txt"):
            f.run("--tasks", ta, "--arms", "lib")
        with pytest.raises(SystemExit, match=r"quick-reference\.txt differ from the run's"):
            f.run("--rerun", runner.session_id("dev", "sonnet", ta, "lib", 1))
        # a quick reference broken since the run started: the re-run path checks it too
        qr.write_text(GOOD_QR + "Use 3 starts\n", "utf-8")
        with pytest.raises(SystemExit, match="the number 3"):
            f.run("--rerun", runner.session_id("dev", "sonnet", ta, "lib", 1))
    assert set(meta["preambles"]) == {"checklist.txt", "code-skill.txt", "environment.txt",
                                      "environment-lib.txt", "lib-directed.txt",
                                      quickref.QUICK_REFERENCE}  # fmt: skip
    assert meta["preambles"][quickref.QUICK_REFERENCE] == qr_sha
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
        # a readiness run's preambles must be those of the commit (the scratch copy is not)
        mp.setattr(cli, "uncommitted_preambles", UNCOMMITTED_PREAMBLES)
        with pytest.raises(SystemExit, match="preambles must be committed"):
            f.run("--tasks", ta, "--arms", "code-skill")
        mp.setattr(arms, "PREAMBLES_DIR", PRE)
        assert UNCOMMITTED_PREAMBLES([arms.get_arm("code-hint")]) == []
        mp.setattr(arms, "PREAMBLES_DIR", f.preambles)
        mp.setattr(cli, "uncommitted_preambles", lambda arms: [])
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
    bmanifest = tmp / "bundles-dev.sha256"
    bmanifest.write_text(bundles.format_manifest(bundles.manifest_entries(broot, "dev")), "utf-8")
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
    return {"ids": ids, "broot": broot, "troot": troot, "answers": answers, "tmp": tmp,
            "manifest": manifest, "bmanifest": bmanifest, "unvalidated": unvalidated,
            "wrong": (a, b, c)}  # fmt: skip


def _owner_lists(mp: pytest.MonkeyPatch, s: dict[str, Any]) -> None:
    """The committed lists of the synthetic set: its truths and its bundles."""
    mp.setattr(cli, "truth_manifest_path", lambda set_name: s["manifest"])
    mp.setattr(cli, "manifest_path", lambda set_name: s["bmanifest"])


def _builder_env(mp: pytest.MonkeyPatch, s: dict[str, Any], session: str = "track-a-1") -> Path:
    """What the owner sets when launching a builder session; returns the owner's log."""
    log = s["tmp"] / "owner" / scoring.PASS_FAIL_LOG
    mp.setenv("WPBENCH_OPS_BUNDLES", str(s["broot"]))
    mp.setenv(scoring.PASSFAIL_TRUTH_ENV, str(s["troot"]))
    mp.setenv(scoring.PASSFAIL_LOG_ENV, str(log))
    mp.setenv(scoring.SESSION_ENV, session)
    mp.delenv("WPBENCH_OPS_TRUTH", raising=False)
    _owner_lists(mp, s)
    return log


def test_score_full_mode_for_the_owner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _scripted(tmp_path)
    _owner_lists(monkeypatch, s)
    scores = tmp_path / "owner" / "scores.json"
    args = ["score", str(s["answers"]), "--bundles", str(s["broot"]), "--truth", str(s["troot"]),
            "--truth-manifest", str(s["manifest"]), "--out", str(scores)]  # fmt: skip
    # every grade goes to an owner file outside the answers, never into DIR
    with pytest.raises(SystemExit, match="--out FILE"):
        cli.main(args[:-2])
    with pytest.raises(SystemExit, match="inside the answer directory"):
        cli.main([*args[:-1], str(s["answers"] / scoring.SCORE_FILE)])
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    a, b, c = s["wrong"]
    assert f"{a}: 2/3 realisation(s) passed; r1 PASS; r2 FAIL [failed: " in out
    assert f"{b}: 2/3 realisation(s) passed" in out and "r3 FAIL [no answer file]" in out
    assert f"{c}: 2/3 realisation(s) passed; r1 FAIL [no answer (" in out
    assert "45 realisation(s) of 15 validated task(s) scored" in out
    assert f"not validated yet (not scored): {s['unvalidated']}" in out
    assert f"ignored): {a}/r9.json" in out
    assert "realisation pass rate: 93.3 % (42/45)" in out and "at least 90 %" in out
    assert "readiness (section 7, scripted pipeline): MET (42 passed; 41 of 45 needed)" in out
    assert not (s["answers"] / scoring.SCORE_FILE).exists()
    doc = json.loads(scores.read_text("utf-8"))
    assert doc["realisations_scored"] == 45 and doc["passed"] == 42
    assert doc["readiness_met"] is True and doc["readiness_needed"] == 41
    assert doc["rate"] == pytest.approx(42 / 45) and doc["not_validated"] == [s["unvalidated"]]
    row = next(r for r in doc["realisations"] if r["task"] == a and r["realisation"] == 2)
    assert row["passed"] is False and row["keys"] and len(row["bundle_digest"]) == 64
    missing = next(r for r in doc["realisations"] if r["task"] == b and r["realisation"] == 3)
    assert missing["missing"] is True and "no answer file" in missing["parse_error"]
    # a scored task's bundle that is not the committed one is refused
    tj = s["broot"] / "dev" / a / "r1" / "task.json"
    tj.write_text(tj.read_text("utf-8").replace('"F1"', '"F1" '), "utf-8")
    with pytest.raises(SystemExit, match=f"differ from bundles-dev.sha256: changed: dev/{a}/r1"):
        cli.main(args)
    assert cli.main([*args, "--no-manifest-check"]) == 0


def test_the_readiness_line_never_rounds_up_to_the_threshold() -> None:
    rows = [scoring.RealisationScore("t", k, k <= 43, False, {"keys": {}}, "d")
            for k in range(1, 49)]  # fmt: skip
    res = scoring.ScoreResult(rows=rows, scored_tasks=["t"])
    lines = scoring.full_lines(res)
    assert "realisation pass rate: 89.5 % (43/48)" in lines[-2]
    assert lines[-1] == (
        "readiness (section 7, scripted pipeline): NOT MET (43 passed; 44 of 48 needed)"
    )
    assert scoring.readiness_met(res) is False
    assert [scoring.readiness_needed(n) for n in (10, 30, 45, 48)] == [9, 27, 41, 44]
    rows[43].passed = True
    assert scoring.readiness_met(res) is True and "MET (44 passed" in scoring.full_lines(res)[-1]
    assert scoring.readiness_met(scoring.ScoreResult()) is None


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
    # what the grader cannot grade fails that file (the run goes on): an integer too large
    # for a float (grading.as_number) and an array nested deeper than Python recurses
    for text, error in (
        ('{"extra_energy_mwh_per_yr": 1' + "0" * 400 + "}", "OverflowError"),
        ("[" * 5000 + "]" * 5000, "RecursionError"),
    ):
        p.write_text(text, "utf-8")
        g = scoring.grade_file(task, truth, p)
        assert g.passed is False
        assert g.parse_error == f"the answer could not be graded ({error})"


def test_score_pass_fail_prints_only_verdicts_and_logs_at_most_three_evaluations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _scripted(tmp_path)
    log = _builder_env(monkeypatch, s)
    args = ["score", str(s["answers"]), "--pass-fail"]
    for n in (1, 2, 3):
        assert cli.main(args if n != 2 else [*args, "--session", "track-a-1"]) == 0
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
    # DIR gets no file: no scores and no log (the log is the owner's)
    assert sorted(p.name for p in s["answers"].iterdir()) == s["ids"]
    with pytest.raises(SystemExit, match="has had its 3 evaluations"):
        cli.main(args)
    text = log.read_text("utf-8").splitlines()
    starts = [ln for ln in text if ln.endswith(" started")]
    ends = [ln for ln in text if " realisations=" in ln]
    assert len(starts) == 3 and len(ends) == 3
    assert starts[0].split(" ", 1)[1] == "session=track-a-1 evaluation=1 started"
    assert ends[0].split(" ", 1)[1] == "session=track-a-1 evaluation=1 realisations=45 passed=42"
    assert len(text) == 3 * (1 + 45 + 1) + 1 and "refused" in text[-1]
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d session=track-a-1 ops-\S+/r1 (PASS|FAIL)$",
                    text[1])  # fmt: skip
    # the session's name is the owner's: another one is refused (and logged), never counted
    with pytest.raises(SystemExit, match="not this session's name"):
        cli.main([*args, "--session", "track-a-2"])
    assert "refused: --session 'track-a-2' is another name" in log.read_text("utf-8")
    assert scoring.evaluations_logged(log, "track-a-2") == 0
    # regenerating DIR does not reset the count
    shutil.rmtree(s["answers"])
    s["answers"].mkdir()
    with pytest.raises(SystemExit, match="has had its 3 evaluations"):
        cli.main(args)
    # the owner's options are refused
    for extra in (
        ["--truth", str(s["troot"])],
        ["--truth-manifest", str(s["manifest"])],
        ["--bundles", str(s["broot"])],
        ["--out", "x.json"],
        ["--no-manifest-check"],
    ):
        with pytest.raises(SystemExit, match="--pass-fail takes no"):
            cli.main([*args, *extra])  # fmt: skip
    # another session (launched by the owner with its own name) has its own three
    monkeypatch.setenv(scoring.SESSION_ENV, "track-b-1")
    assert cli.main(args) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "evaluation 1 of 3 for session track-b-1"
    monkeypatch.delenv(scoring.SESSION_ENV)
    with pytest.raises(SystemExit, match="WPBENCH_OPS_SESSION is not set"):
        cli.main(args)
    monkeypatch.setenv(scoring.SESSION_ENV, "track-b-1")
    with pytest.raises(SystemExit, match="development set"):
        cli.main([*args, "--set", "test"])


def test_pass_fail_never_checks_the_truth_against_other_bundles_and_counts_every_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's oracle: bundles whose task.json lost a fault (or an option) made the
    run fail exactly when that was the truth, and a crash left no log. Now the bundles
    must be the committed ones before any truth is opened, and a run counts once started."""
    s = _scripted(tmp_path)
    log = _builder_env(monkeypatch, s, "probe")
    args = ["score", str(s["answers"]), "--pass-fail"]
    # a wrong truth folder (the owner's mistake): nothing scored, nothing logged, no path
    for wrong in (s["troot"] / "dev", tmp_path / "nowhere"):
        monkeypatch.setenv(scoring.PASSFAIL_TRUTH_ENV, str(wrong))
        with pytest.raises(SystemExit) as exc:
            cli.main(args)
        assert str(exc.value) == f"error: {cli.TRUTH_UNREADABLE}"
    # the owner's own variable is never read by the pass/fail mode
    monkeypatch.delenv(scoring.PASSFAIL_TRUTH_ENV)
    monkeypatch.setenv("WPBENCH_OPS_TRUTH", str(s["troot"]))
    with pytest.raises(SystemExit, match="truth could not be read"):
        cli.main(args)
    monkeypatch.delenv("WPBENCH_OPS_TRUTH")
    monkeypatch.setenv(scoring.PASSFAIL_TRUTH_ENV, str(s["troot"]))
    assert scoring.evaluations_logged(log, "probe") == 0
    # drop each fault in turn from an F3 vocabulary: the same refusal whatever the truth
    f3 = next(t for t in s["ids"] if (truth_keys(s["troot"], t).get("diagnosis") or {}).get(
        "faults") == ["worn_pump"])  # fmt: skip
    messages = set()
    for fault in ("worn_pump", "leak", "throttled_valve"):
        crafted = tmp_path / f"crafted-{fault}"
        shutil.copytree(s["broot"], crafted)
        for k in (1, 2, 3):
            tj = crafted / "dev" / f3 / f"r{k}" / "task.json"
            doc = json.loads(tj.read_text("utf-8"))
            del doc["keys"]["diagnosis"]["vocabulary"][fault]
            tj.write_text(json.dumps(doc), "utf-8")
        monkeypatch.setenv("WPBENCH_OPS_BUNDLES", str(crafted))
        with pytest.raises(SystemExit) as exc:
            cli.main(args)
        messages.add(str(exc.value).split(" (")[0])
    assert messages == {"error: the bundles differ from the committed bundles-dev.sha256"}
    assert scoring.evaluations_logged(log, "probe") == 0
    monkeypatch.setenv("WPBENCH_OPS_BUNDLES", str(s["broot"]))
    # an answer the grader cannot grade fails; the evaluation is scored and logged
    (s["answers"] / s["ids"][0] / "r1.json").write_text("[" * 5000 + "]" * 5000, "utf-8")
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert f"{s['ids'][0]} r1 FAIL" in out and "evaluation 1 of 3 for session probe" in out
    # a crash after the evaluation started counts, and its message says nothing of the truth

    def boom(*a: Any, **k: Any) -> Any:
        raise ValueError(f"the truth in {s['troot']} says 8.4")

    monkeypatch.setattr(scoring, "score_answers", boom)
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert "it counts as evaluation 2 of 3 for session probe" in str(exc.value)
    assert "8.4" not in str(exc.value) and str(s["troot"]) not in str(exc.value)
    assert scoring.evaluations_logged(log, "probe") == 2
    assert "session=probe evaluation=2 failed" in log.read_text("utf-8")


def test_evaluations_are_counted_under_a_lock(tmp_path: Path) -> None:
    log = tmp_path / "owner" / scoring.PASS_FAIL_LOG
    got: list[int | None] = []
    lock = threading.Lock()

    def one() -> None:
        n = scoring.begin_evaluation(log, "racer")
        with lock:
            got.append(n)

    threads = [threading.Thread(target=one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(n for n in got if n) == [1, 2, 3] and got.count(None) == 3
    assert scoring.evaluations_logged(log, "racer") == 3
    assert not log.with_name(log.name + ".lock").exists()
    with scoring.log_lock(log), pytest.raises(TimeoutError):
        scoring.begin_evaluation(log, "other", timeout=0.2)
    # a line that lost its line break hides no evaluation
    with open(log, "a", encoding="utf-8") as f:
        f.write("# a note without a line break ")
    assert scoring.begin_evaluation(log, "other") == 1
    with open(log, "a", encoding="utf-8") as f:
        f.write("# another 2026-09-27T10:00:00 session=other evaluation=9 started\n")
    assert scoring.evaluations_logged(log, "other") == 2


def test_the_builder_wrapper_takes_everything_from_the_owner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _scripted(tmp_path)
    path = REPO / "tools" / "ops_passfail.py"
    spec = importlib.util.spec_from_file_location("ops_passfail", path)
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    _builder_env(monkeypatch, s, "w1")
    monkeypatch.delenv(scoring.PASSFAIL_TRUTH_ENV)
    assert wrapper.main([str(s["answers"])]) == 2
    assert "ask the owner" in capsys.readouterr().err
    monkeypatch.setenv(scoring.PASSFAIL_TRUTH_ENV, str(s["troot"]))
    assert wrapper.main([str(s["answers"])]) == 0
    out = capsys.readouterr().out
    assert "42 of 45 realisation(s) passed" in out and "evaluation 1 of 3 for session w1" in out
    assert wrapper.main([str(s["answers"]), "--session", "w1"]) == 0
    assert "evaluation 2 of 3 for session w1" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="not this session's name"):
        wrapper.main([str(s["answers"]), "--session", "w2"])
    with pytest.raises(SystemExit):
        wrapper.main([str(s["answers"]), "--truth", str(s["troot"])])  # no such option
    # the owner's full mode does not run in a builder session: its variable is not there
    with pytest.raises(SystemExit, match="set WPBENCH_OPS_TRUTH"):
        cli.main(["score", str(s["answers"]), "--out", str(tmp_path / "o.json")])


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
    _use("Read", file_path=f"{WM}/opsbench-bundles/dev/ops-f1-001/r1/task.md"),
    _use("Read", file_path="C:\\Users\\raimo\\world-model\\worldparts\\benchmarks\\operations"
                           "\\results\\stage0\\sessions\\ab12\\attempt-1\\stream.jsonl"),
    _use("Bash", command="uv run pytest tests/test_diagnosis.py -q"),
    _use("Grep", pattern="record", path="src/worldparts"),
    _use("Bash", command="cat tools/ops_passfail.py && uv run python tools/ops_passfail.py --help"),
    _use("Bash", command="ls benchmarks/composition/reports/"),
    _use("Bash", command="python -c \"print('truth')\" && git log --oneline -5"),
    _use("Bash", command="git diff src/worldparts/diagnosis.py"),
    _use("Read", file_path=f"{WM}/worldparts/benchmarks/operations/"
                           "PREREGISTRATION.md"),
    _use("TodoWrite", todos=[{"content": "read the truth.json? no"}]),
    json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                "tool_use_id": "t", "content": "see worldparts-opsbench/truth"}]}}),
    # the Stage 0 transcripts, read without the grader's files
    _use("Bash", command="grep -rn cannot_determine benchmarks/operations/results/stage0 "
                         "--include='*.jsonl'"),
    _use("Grep", pattern="x", path=f"{WM}/worldparts/benchmarks/operations/"
                                   "results/stage0", glob="*.jsonl"),
    _use("Bash", command="cat benchmarks/operations/results/stage0/sessions/*/attempt-1/"
                         "prompt.txt"),
    _use("Bash", command="grep -rn x benchmarks/operations/results/stage0/sessions/ab12/"
                         "attempt-1/"),
    _use("Bash", command="ls -R benchmarks/operations/results/stage0"),
    _use("Bash", command="cat benchmarks/operations/results/stage0/index.json"),
    # everyday build work: searches that skip the git-ignored run folders, or a subfolder
    _use("Bash", command="rg -n 'def calibrate' src/ && rg foo . && git grep -n foo"),
    _use("Grep", pattern="calibrate"),
    _use("Glob", pattern="**/*.py"),
    _use("Bash", command="grep -rn foo src/ --include='*.py' && grep -rn foo src/"),
    _use("Bash", command="find src -name '*.py' | xargs grep foo && cd src && grep -rn foo ."),
    _use("PowerShell", command="Get-ChildItem -Recurse src -Filter *.py | Select-String calib"),
    _use("Bash", command="git diff --stat HEAD~5 && git log -p -- src/worldparts/diagnosis.py"),
    _use("Bash", command="git checkout main && git checkout -b scada && git diff HEAD"),
    _use("Bash", command="ls ../opsbench-bundles/dev && cp -r ../opsbench-bundles/dev/x /tmp/x"),
    _use("Write", file_path="src/worldparts/scada.py", content="def read_long(path):\n    ...\n"),
    _use("Write", file_path="benchmarks/operations/preambles/quick-reference.txt",
         content="worldparts quick reference\n"),
    _use("Bash", command="cat benchmarks/operations/harness/score.py > /tmp/score.py"),
    _use("Bash", command="uv run python -m benchmarks.operations.harness run --readiness "
                         "--dry-run && uv run python tools/ops_passfail.py --help"),
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
        (_use("Grep", pattern="calibrate", path=f"{WM}/reports"),
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
        (_use("Grep", pattern="x", path=f"{WM}/worldparts/benchmarks/"
                                        "operations/results/stage0"), "run directory"),
        (_use("Bash", command="cat benchmarks/operations/results/stage0/sessions/*/*"),
         "run directory"),
        (_use("PowerShell", command="Get-ChildItem -Recurse benchmarks\\operations\\results\\"
                                    "stage0 | Select-String cannot"), "run directory"),
        # the review's findings: the owner's files, commands and variables
        (_use("Bash", command="cat answers/score.json"), "score.json"),
        (_use("Read", file_path="C:/work/answers/score.json"), "score.json"),
        (_use("Bash", command="uv run python -m benchmarks.operations.harness score answers2"),
         "harness score"),
        (_use("Bash", command="uv run python -m benchmarks.operations.harness grade results/x"),
         "harness grade"),
        (_use("PowerShell", command="uv run python -m benchmarks.operations.harness headroom "
                                    "benchmarks\\operations\\results\\stage0"), "harness headroom"),
        (_use("Bash", command="python -m benchmarks.operations.harness report RUN --print"),
         "harness report"),
        (_use("Bash", command="uv run python -c \"from benchmarks.operations.harness.bundles "
                              "import truth_root; print(truth_root())\""), "'truth_root'"),
        (_use("Bash", command="uv run python - <<'EOF'\nfrom benchmarks.operations.harness "
                              "import score as s\nres = s.score_answers(a, b, c, d)\nEOF"),
         "'score_answers'"),
        (_use("Bash", command="uv run python -m benchmarks.operations.harness score out "
                              "--pass-fail --bundles crafted"), "the owner's options"),
        (_use("Bash", command="export WPBENCH_OPS_SESSION=b2; uv run python tools/ops_passfail.py "
                              "out"), "setting WPBENCH_OPS_SESSION"),
        (_use("Bash", command="echo $WPBENCH_OPS_PASSFAIL_TRUTH"), "WPBENCH_OPS_PASSFAIL"),
        (_use("Bash", command="rm -rf ~/AppData/Local/worldparts-bench/ops-build"),
         "LOCALAPPDATA"),
        # searches, copies and wildcard reads above a run directory or the repository
        (_use("Bash", command="grep -rn X ."), "holds run directories"),
        (_use("Bash", command="grep -rn cannot_determine"), "holds run directories"),
        (_use("Bash", command="grep -rn X benchmarks/"), "holds run directories"),
        (_use("Bash", command="grep -rn X .."), "above the repository"),
        (_use("Bash", command=f"grep -rn X {WM}"), "above the repository"),
        (_use("Bash", command=f"grep -rn X {WM}/worldparts-bench"), "holds run directories"),
        (_use("Grep", pattern="x", path=f"{WM}"), "above the repository"),
        (_use("Bash", command="grep -h passed benchmarks/operations/results/stage0/sessions/*/"
                              "*.json"), "run directory"),
        (_use("Bash", command="jq . benchmarks/operations/results/stage0/sessions/*/*.json"),
         "run directory"),
        (_use("Bash", command="python - <<'EOF'\nimport glob\nfor p in glob.glob('benchmarks/"
                              "operations/results/stage0/sessions/*/*.json'):\n    print(open(p)"
                              ".read())\nEOF"), "run directory"),
        (_use("Bash", command="cp -r benchmarks/operations/results/stage0 /tmp/s0"),
         "run directory"),
        (_use("PowerShell", command="robocopy benchmarks\\operations\\results\\stage0 C:\\tmp "
                                    "/E"), "run directory"),
        (_use("Bash", command="tar czf /tmp/s0.tgz benchmarks/operations/results/stage0"),
         "run directory"),
        (_use("PowerShell", command="Copy-Item -Recurse -Path benchmarks\\operations\\results "
                                    "-Destination C:\\tmp\\r"), "run directory"),
        (_use("Bash", command="cp -r . /tmp/copy"), "recursive copy over '.'"),
        (_use("Bash", command="ls -R ../*opsbench*"), "wildcard spelling of 'worldparts-opsbench'"),
        (_use("Bash", command="cat ../worldparts-ops*/tru*/dev/*"), "wildcard spelling of 'truth'"),
        (_use("Glob", pattern="*-ops*/**/*.json", path=f"{WM}"), "wildcard spelling"),
        (_use("Bash", command=f"cd {WM}; cd reports; ls"),
         "change of directory into 'reports'"),
        (_use("Bash", command="cd ~/.claude && cat projects/*/memory/*.md"), "into '.claude'"),
        (_use("Bash", command=f"cd {WM} && grep -rn X . --include='*.py'"),
         "above the repository"),
        # a script written with Write runs what it says
        (_use("Write", file_path="scratch/peek.py", content="import os\nprint(open(os.environ["
              "'WPBENCH_OPS_TRUTH'] + '/dev/x.truth.json').read())\n"), "WPBENCH_OPS_TRUTH"),
        (_use("Write", file_path="scratch/p2.py", content="from benchmarks.operations.harness."
              "bundles import load_truth\n"), "'load_truth'"),
        # other sessions' transcripts, the web history of PREREGISTRATION.md
        (_use("mcp__ccd_session_mgmt__search_session_transcripts", query="truth"),
         "another session's transcript"),
        (_use("mcp__ccd_session_mgmt__get_session", session_id="x"), "another session"),
        (_use("Bash", command="claude -p --resume abc123 'what was the truth'"), "claude --resume"),
        (_use("WebFetch", url="https://github.com/raimondasl/worldparts/commits/main/benchmarks/"
                              "operations/PREREGISTRATION.md", prompt="x"), "fetched from GitHub"),
        (_use("Bash", command="gh api 'repos/raimondasl/worldparts/commits?path=benchmarks/"
                              "operations/PREREGISTRATION.md'"), "fetched from GitHub"),
        (_use("Bash", command="curl https://raw.githubusercontent.com/raimondasl/worldparts/"
                              "9cb3564/benchmarks/operations/PREREGISTRATION.md"),
         "fetched from GitHub"),
        (_use("Bash", command="git log -p -- benchmarks/operations/"), "folder that holds it"),
        (_use("Bash", command="git show 08044ea -- benchmarks/operations"), "folder that holds it"),
        (_use("Bash", command="git checkout 9cb3564 -- benchmarks/operations"),
         "folder that holds it"),
        # a change to the harness, its committed lists, the wrapper or its tests
        (_use("Edit", file_path="benchmarks/operations/harness/score.py", old_string="a",
              new_string="b"), "change to the benchmark harness"),
        (_use("Write", file_path="tests/test_ops_bench_build.py", content="x = 1\n"),
         "change to the benchmark harness"),
        (_use("Bash", command="echo x > benchmarks/operations/bundles-dev.sha256"),
         "change to the benchmark harness"),
        (_use("Bash", command="sed -i 's/a/b/' benchmarks/operations/harness/score.py"),
         "change to the benchmark harness"),
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
        _use("Bash", command="git diff 9cb3564 HEAD"),
        _use("Bash", command="git worktree add ../old 9cb3564"),
        _use("Bash", command="WPBENCH_OPS_BUNDLES=../copy uv run python tools/ops_passfail.py x"),
        _use("Bash", command="for i in 1 2; do uv run python tools/ops_passfail.py out; done"),
        _use("Write", file_path="scratch/eval.sh", content="uv run python tools/ops_passfail.py x"),
        "{not json",
    ])  # fmt: skip
    res = firewall.audit_transcripts([warn])
    n = len(ALLOWED)
    assert res.violations == []
    assert [f.line for f in res.warnings] == [n + k for k in (1, 2, 4, 4, 5, 6, 7, 8, 9)] + [0]
    assert res.warnings[2].tool == "Write" and "summary" in res.warnings[2].reason
    assert "record.json" in res.warnings[3].reason and "notes/summary.md" in res.warnings[3].reason
    assert "WPBENCH_OPS_BUNDLES" in res.warnings[6].reason
    assert "inside a loop" in res.warnings[7].reason
    assert "runs the evaluation wrapper" in res.warnings[8].reason
    assert res.evaluations == {"S1": 2}
    assert res.verdict == "CLEAN, with 10 warning(s) to review"


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
    # every launcher counts; a loop is flagged for review (the owner's log has the count)
    launchers = _transcript(tmp_path / "S3.jsonl", [_use("Bash", sid="S3", command=c) for c in (
        "uv run -m benchmarks.operations.harness score out --pass-fail",
        "py tools/ops_passfail.py out",
        "$PY tools/ops_passfail.py out",
        "./tools/ops_passfail.py out",
        "python -c \"import runpy; runpy.run_path('tools/ops_passfail.py', run_name='__main__')\"",
        "python -c \"from benchmarks.operations.harness.__main__ import main; "
        "main(['score', 'out', '--pass-fail'])\"",
        "for i in 1 2 3 4 5; do uv run python tools/ops_passfail.py out --session s$i; done",
    )])  # fmt: skip
    res = firewall.audit_transcripts([launchers])
    assert res.evaluations == {"S3": 7} and len(res.violations) == 1
    assert any("inside a loop" in f.reason for f in res.warnings)
    with pytest.raises(SystemExit, match="no transcript"):
        cli.main(["firewall-audit", str(tmp_path / "missing.jsonl")])
