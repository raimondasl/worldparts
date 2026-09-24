"""Tests of the benchmark harness's lib condition (worldparts as an installed library).

Covers the lib environment (worldparts installed from a wheel built from the repository,
not editable, with the code environment's package versions; its probe; its cache stamp),
the session setup (the code condition's tools and permissions, the lib environment first
on PATH), the preamble and its quick reference (whose every line is run here against the
package, so it cannot rot), the contamination rules of lib versus code, the report with
three conditions and the lib-versus-code line (paired by task), and the dry run. Nothing
here starts a Claude session; one test builds a wheel with ``uv build`` (a fraction of a
second). An opt-in test (``WPBENCH_LIB_ENV_TEST=1``, about 20 s) builds a real lib
environment and runs the quick reference with its own ``python`` and ``worldparts``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import worldparts as wp
import worldparts.cli

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.composition.harness import __main__ as cli  # noqa: E402
from benchmarks.composition.harness import grading, report, runner  # noqa: E402
from benchmarks.composition.harness.tasks import TASKS_DIR, load_task, load_tasks  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "benchmark" / "tasks"
FIXTURE = FIXTURES / "lift-fixture-01.yaml"


@pytest.fixture(scope="module")
def task():  # type: ignore[no-untyped-def]
    return load_task(FIXTURE)


def _reply(values: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(values) + "\n```"


def _tool_stream(tool_input: dict[str, Any], tool_result: str, final: str) -> str:
    msgs = [
        {"type": "system", "subtype": "init", "model": "claude-test", "tools": ["Bash"]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": tool_input}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": tool_result}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": final,
         "num_turns": 2, "total_cost_usd": 0.01,
         "usage": {"input_tokens": 1000, "output_tokens": 500}},
    ]  # fmt: skip
    return "\n".join(json.dumps(m) for m in msgs) + "\n"


# ----------------------------------------------------------------------------------------
# session setup and preamble
# ----------------------------------------------------------------------------------------
def test_lib_is_a_condition_with_the_code_tools_and_permissions(tmp_path: Path) -> None:
    assert runner.CONDITIONS == ("mcp", "code", "lib")
    assert runner.DEFAULT_CONDITIONS == ("mcp", "code")  # lib is opt-in
    assert runner.BUILTIN_TOOLS["lib"] == runner.BUILTIN_TOOLS["code"] == "Bash,Read,Write,Edit"
    assert runner.ALLOWED_TOOLS["lib"] == runner.ALLOWED_TOOLS["code"]
    args = (tmp_path / "m.json", tmp_path / "s.json", 40)
    code = runner.build_command("code", "opus", *args, claude="claude")
    lib = runner.build_command("lib", "opus", *args, claude="claude")
    i = code.index("--append-system-prompt")
    # The same command except for the preamble.
    assert lib[:i] == code[:i] and lib[i + 2 :] == code[i + 2 :]
    assert lib[i + 1] == runner.PREAMBLE["lib"] != code[i + 1]
    assert runner.mcp_config("lib", tmp_path) == {"mcpServers": {}}
    # A batch-file CLI would cut the multi-line preamble at its first line break.
    with pytest.raises(ValueError, match="batch file"):
        runner.build_command("lib", "opus", *args, claude=r"C:\tools\claude.cmd")


def test_child_env_puts_the_lib_environment_first_on_path(tmp_path: Path) -> None:
    env, changed = runner.child_env("lib", tmp_path / "lib-env")
    path_key = next(k for k in env if k.upper() == "PATH")
    first = env[path_key].split(os.pathsep)[0]
    assert first == str(
        tmp_path / "lib-env" / (".venv/Scripts" if os.name == "nt" else ".venv/bin")
    )
    assert env["VIRTUAL_ENV"] == str(tmp_path / "lib-env" / ".venv")
    assert env["PIP_NO_INDEX"] == "1" and env["UV_OFFLINE"] == "1"
    assert "VIRTUAL_ENV" in changed and path_key in changed
    with pytest.raises(ValueError, match="Python environment"):
        runner.child_env("lib", None)


def test_lib_preamble_names_the_packages_and_carries_the_quick_reference() -> None:
    lib, code = runner.PREAMBLE["lib"], runner.PREAMBLE["code"]
    intro, _, sheet = lib.partition("\n\n")
    assert sheet == runner.lib_cheat_sheet()
    for word in ("numpy", "scipy", "fluids", "wntr", "the package worldparts", "on PATH",
                 "no network access", "No other packages can be installed"):  # fmt: skip
        assert word in intro, word
    # Same task framing and closing sentence as code; code never names worldparts.
    assert intro.startswith(code.split(" The Python environment")[0])
    assert intro.endswith("then give the final answer as instructed.")
    assert "worldparts" not in code.lower()
    # Compact: at most about 25 lines, one statement or command per line.
    lines = sheet.splitlines()
    assert len(lines) <= 25
    for needle in (
        "$ worldparts list",
        "$ worldparts describe ",
        "import worldparts as wp",
        "wp.System(",
        "s.add(",
        's.connect("',
        "s.check()",
        "r = s.solve()",
        'r["p.volume_flow"]',
        "r.units[",
        "s.simulate(duration=",
        "events=[",
        "sim.final[",
        "s.add_control(",
        "wp.System.from_dict(",
        "declared unit",
        "strings may carry units",
        "gauge",
        "Flow units differ by part (pipes, valves, drains, supplies: L/min; pumps, tanks, "
        "filters, UV reactors: m3/h)",
    ):
        assert needle in sheet, needle
    # Neutral: it describes the package and never tells the agent to use it.
    for pushy in (r"\bmust\b", r"\bshould\b", r"\bprefer", r"instead of", r"\balways\b",
                  r"\brecommend", r"\btested\b"):  # fmt: skip
        assert not re.search(pushy, lib, re.IGNORECASE), pushy


def test_lib_quick_reference_runs_against_the_package(capsys: pytest.CaptureFixture[str]) -> None:
    """Every line of the quick reference works with the package as installed (in-process)."""
    for line in runner.LIB_CHEAT_SHEET_SHELL:
        argv = shlex.split(line.split("  #")[0])
        assert argv[0] == "worldparts"
        assert worldparts.cli.main(argv[1:]) == 0, line
        out = capsys.readouterr().out
        assert out.strip(), line
        if argv[1] == "list":
            assert "centrifugal_pump" in out and "inlet, outlet" in out
        else:
            assert "Parameters" in out and "head_curve" in out
    ns: dict[str, Any] = {}
    exec(compile("\n".join(runner.LIB_CHEAT_SHEET_PYTHON), "<quick reference>", "exec"), ns)
    printed = capsys.readouterr().out
    assert "unconnected_port" in printed  # check() reports the tank's free inlet
    s, r, sim, s2 = ns["s"], ns["r"], ns["sim"], ns["s2"]
    assert not [i for i in s.check() if i.severity == "error"]
    assert 2 < r["p.volume_flow"] < 20 and r.units["p.volume_flow"] == "m3/h"
    # The units note: a pipe's flow is in L/min, a pump's in m3/h.
    assert r.units["link.volume_flow"] == "L/min"
    assert r["link.volume_flow"] == pytest.approx(r["p.volume_flow"] * 1000 / 60)
    assert r.get("link.volume_flow", unit="m3/h") == pytest.approx(r["p.volume_flow"])
    assert r.units["p.outlet.p"] == "bar"
    # The transfer empties a into b.
    assert sim.time[-1] == 600.0 and sim.final["a.level"] < 2 and sim.final["b.level"] > 0.5
    assert isinstance(s2, wp.System) and [c.name for c in s2.controls.values()] == ["hold"]
    held = s2.solve()  # the PI loop of the document holds 2.5 bar
    assert held["p.outlet.p"] == pytest.approx(2.5, rel=1e-4)
    assert 0.3 < held["p.speed"] < 1.2
    # The notes: plain numbers in declared units, strings with units, gauge pressures.
    info = {v.path: v for v in s.variables()}
    assert info["link.diameter"].unit == "mm" and s.get("link.diameter") == pytest.approx(40)
    assert info["p.outlet.p"].pressure_reference == "gauge"
    assert r.get("p.outlet.p", unit="bar absolute") > r["p.outlet.p"]


def _layout_graph(doc: dict[str, Any]) -> Any:
    """A system document's layout as a labelled graph: component nodes (labelled with the
    type alias) joined to port nodes (labelled with the port name), and an edge per
    connection between port nodes. Instance names do not count."""
    import networkx as nx

    g = nx.Graph()
    for c in doc["components"]:
        g.add_node(("c", c["name"]), label=str(c["type"]).rsplit(".", 1)[-1])
    for pair in doc["connections"]:
        for end in pair:
            comp, port = end.split(".", 1)
            g.add_node(("p", end), label="port " + port)
            g.add_edge(("c", comp), ("p", end))
        g.add_edge(("p", pair[0]), ("p", pair[1]))
    return g


def test_lib_quick_reference_is_no_template_of_a_task() -> None:
    """The quick reference's example system shares neither its layout (component types
    and connections) nor any instance name with a benchmark task's reference system, so it
    is no ready-made template for one task."""
    from networkx.algorithms.isomorphism import categorical_node_match, is_isomorphic

    ns: dict[str, Any] = {}
    lines = [x for x in runner.LIB_CHEAT_SHEET_PYTHON if not x.startswith("print(")]
    exec(compile("\n".join(lines[: lines.index("r = s.solve()  # steady operating point")]),
                 "<quick reference>", "exec"), ns)  # fmt: skip
    doc = ns["s"].to_dict()
    sheet = _layout_graph(doc)
    names = {c["name"] for c in doc["components"]}
    tasks, errors = load_tasks(TASKS_DIR)
    assert tasks and not errors
    match = categorical_node_match("label", None)
    for t in tasks:
        assert not is_isomorphic(sheet, _layout_graph(t.system), node_match=match), t.id
        assert not names & {c["name"] for c in t.system["components"]}, t.id
    # The graph tells layouts apart: the task example of the README (pump-lift-01) is its
    # own layout, and renaming instances does not change a layout.
    lift = next(t for t in tasks if t.id == "pump-lift-01").system
    renamed = json.loads(json.dumps(lift).replace("riser", "x1").replace("roof", "x2"))
    assert is_isomorphic(_layout_graph(lift), _layout_graph(renamed), node_match=match)


# ----------------------------------------------------------------------------------------
# lib environment
# ----------------------------------------------------------------------------------------
class _FakeRun:
    """Stands in for subprocess.run: records argv, fakes the wheel and the probe."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.stamp_during_build: list[bool] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append([str(c) for c in cmd])
        if cmd[1:3] == ["build", "--wheel"]:
            out_dir = Path(cmd[cmd.index("--out-dir") + 1])
            self.stamp_during_build.append((out_dir.parent / "stamp.json").exists())
            (out_dir / "worldparts-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        if cmd[1:2] == ["venv"]:
            py = runner.code_env_python(Path(cmd[-1]).parent)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")


def test_lib_env_installs_worldparts_from_a_wheel_not_editable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeRun()
    monkeypatch.setattr(runner.subprocess, "run", fake)
    cli_exe = str(tmp_path / "worldparts.exe")
    monkeypatch.setattr(
        runner.shutil, "which", lambda name, path=None: "uv" if name == "uv" else cli_exe
    )
    env_dir = tmp_path / "lib-env"
    assert runner.ensure_lib_env(env_dir, log=lambda *a: None) == env_dir
    build, venv, install, probe, version = fake.calls
    assert build == ["uv", "build", "--wheel", "--out-dir", str(env_dir / "dist"),
                     str(runner.REPO_ROOT)]  # fmt: skip
    assert venv[:4] == ["uv", "venv", "--python", "3.12"]
    wheel = str(env_dir / "dist" / "worldparts-0.1.0-py3-none-any.whl")
    assert install[:4] == ["uv", "pip", "install", "--python"] and install[-1] == wheel
    assert "-e" not in install and "--editable" not in install
    assert not any(a == str(runner.REPO_ROOT) or a.startswith("worldparts") for a in install[:-1])
    versions = runner.locked_versions()
    for name in runner.CODE_ENV_PACKAGES:  # the code environment's versions
        assert f"{name}=={versions[name]}" in install
    constraints = (env_dir / "constraints.txt").read_text(encoding="utf-8")
    assert install[install.index("--constraint") + 1] == str(env_dir / "constraints.txt")
    assert "pint==" in constraints.lower() and "worldparts" not in constraints.lower()
    assert probe[1:] == ["-c", runner.LIB_ENV_PROBE] and version[-1] == "--version"
    stamp = json.loads((env_dir / "stamp.json").read_text(encoding="utf-8"))
    assert stamp == runner.lib_env_stamp()
    assert stamp["packages"] == versions
    assert stamp["worldparts"]["source_sha256"] == runner.worldparts_source_hash()
    # Cached: an unchanged stamp builds nothing; a change of the sources rebuilds.
    fake.calls.clear()
    runner.ensure_lib_env(env_dir, log=lambda *a: None)
    assert fake.calls == []
    stamp["worldparts"]["source_sha256"] = "0" * 64
    (env_dir / "stamp.json").write_text(json.dumps(stamp), encoding="utf-8")
    fake.stamp_during_build.clear()
    runner.ensure_lib_env(env_dir, log=lambda *a: None)
    assert fake.calls[0][1:3] == ["build", "--wheel"]
    # The stale stamp is gone before the rebuild starts (a half-built environment is never
    # taken as current), and the new one is written at the end.
    assert fake.stamp_during_build == [False]
    assert json.loads((env_dir / "stamp.json").read_text(encoding="utf-8")) == want_stamp()
    # Never inside the repository (the agent's Python would see the sources).
    with pytest.raises(RuntimeError, match="outside the repository"):
        runner.ensure_lib_env(runner.REPO_ROOT / "lib-env-test", log=lambda *a: None)
    assert not (runner.REPO_ROOT / "lib-env-test").exists()
    # Not even an in-repository directory whose stamp is current is accepted.
    fake_repo = tmp_path / "repo"
    inside = fake_repo / "lib-env"
    runner.code_env_python(inside).parent.mkdir(parents=True)
    runner.code_env_python(inside).write_bytes(b"")
    (inside / "stamp.json").write_text(json.dumps(runner.lib_env_stamp()), encoding="utf-8")
    monkeypatch.setattr(runner, "REPO_ROOT", fake_repo)
    with pytest.raises(RuntimeError, match="outside the repository"):
        runner.ensure_lib_env(inside, log=lambda *a: None)


def want_stamp() -> dict[str, Any]:
    return runner.lib_env_stamp()


def test_default_lib_env_is_keyed_by_what_it_is_built_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Another source tree (a source edit, another checkout) gets its own default
    directory, so building it never clears an environment a running benchmark uses."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    fake = _FakeRun()
    monkeypatch.setattr(runner.subprocess, "run", fake)
    monkeypatch.setattr(runner.shutil, "which", lambda name, path=None: name)
    first = runner.ensure_lib_env(log=lambda *a: None)
    stamp = runner.lib_env_stamp()
    assert first == tmp_path / "worldparts-bench" / f"lib-env-{runner.lib_env_key(stamp)}"
    assert first == runner.default_lib_env_dir()
    info = runner.lib_env_info(first)
    assert info is not None and info["key"] == runner.lib_env_key(stamp)
    assert info["source_sha256"] == stamp["worldparts"]["source_sha256"]
    monkeypatch.setattr(runner, "worldparts_source_hash", lambda *a: "f" * 64)
    second = runner.ensure_lib_env(log=lambda *a: None)
    assert second != first and second.parent == first.parent
    assert runner.lib_env_info(first) == info  # the first environment is untouched
    assert runner.lib_env_info(tmp_path / "nowhere") is None


def test_lib_env_default_and_overrides(tmp_path: Path) -> None:
    key = runner.lib_env_key(runner.lib_env_stamp())
    assert runner.default_lib_env_dir().name == f"lib-env-{key}"
    assert runner.default_lib_env_dir().parent == runner.default_code_env_dir().parent
    envs = runner.python_env_dirs(["mcp", "code", "lib"], tmp_path / "c", tmp_path / "l",
                                  ensure=False)  # fmt: skip
    assert envs == {"code": tmp_path / "c", "lib": tmp_path / "l"}
    assert runner.python_env_dirs(["mcp"], ensure=False) == {}
    assert runner.python_env_dirs(["lib"], ensure=False) == {"lib": runner.default_lib_env_dir()}


def test_lib_env_probe_rejects_the_editable_repository_install() -> None:
    """The probe fails in the repository's own environment, where worldparts is editable."""
    r = subprocess.run(
        [sys.executable, "-c", runner.LIB_ENV_PROBE], capture_output=True, text=True, cwd=REPO
    )
    assert r.returncode != 0
    assert "worldparts is imported from" in r.stderr or "editable" in r.stderr


def test_source_hash_follows_the_package_sources(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "worldparts"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    h1 = runner.worldparts_source_hash(tmp_path)
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "a.pyc").write_bytes(b"x")
    assert runner.worldparts_source_hash(tmp_path) == h1  # bytecode does not count
    (pkg / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    assert runner.worldparts_source_hash(tmp_path) != h1


# ----------------------------------------------------------------------------------------
# contamination: lib versus code
# ----------------------------------------------------------------------------------------
def test_contamination_rules_of_lib_versus_code(task: Any) -> None:
    final = _reply(task.expected)
    site = r"C:\Users\x\AppData\Local\worldparts-bench\lib-env\.venv\Lib\site-packages"
    allowed_in_lib = [
        ({"command": "python -c \"import worldparts; print(worldparts.__file__)\""},
         f"{site}\\worldparts\\__init__.py"),
        ({"command": "worldparts describe centrifugal_pump"},
         "worldparts.hydraulic.centrifugal_pump  (alias 'centrifugal_pump')"),
        ({"file_path": f"{site}\\worldparts\\catalog\\hydraulic\\pump.yaml"},
         "id: worldparts.hydraulic.centrifugal_pump"),
        ({"command": f"type {site}\\worldparts-0.1.0.dist-info\\METADATA"}, "Name: worldparts"),
    ]  # fmt: skip
    for tool_input, result in allowed_in_lib:
        s = grading.parse_stream(_tool_stream(tool_input, result, final))
        assert grading.contamination(s, "lib", task.id) == [], tool_input
        assert any("worldparts library" in r for r in grading.contamination(s, "code", task.id))
    windows = str(REPO)
    flagged_in_both = {
        "repository path": ({"command": f"type {windows}\\README.md"}, "x"),
        "task directory": ({"command": "dir"}, r"benchmarks\composition\tasks\x.yaml"),
        "schema": ({"command": "type task.schema.json"}, "{}"),
        "task file": ({"command": f"type {task.id}.yaml"}, ""),
        "frozen answers": ({"command": "python s.py"}, "reference:\n  expected: {flow: 18.6}"),
    }  # fmt: skip
    for name, (tool_input, result) in flagged_in_both.items():
        s = grading.parse_stream(_tool_stream(tool_input, result, final))
        for condition in ("code", "lib"):
            assert grading.contamination(s, condition, task.id), (name, condition)
    s = grading.parse_stream(_tool_stream({"command": f"dir {windows}"}, "src", final))
    assert grading.contamination(s, "lib", task.id) == [
        f"tool input mentions the repository path {windows.replace(os.sep, '/').lower()}"
    ]
    # Spellings without the drive letter: home-relative, relative, environment variables.
    home = REPO.parent.parent
    rel = f"{REPO.parent.name}/{REPO.name}"
    other = REPO.parent / f"{REPO.name}-bench"
    by_marker = {
        "tilde": (f"cat ~/{rel}/docs/benchmark-results-v0.2.md", "relative to home"),
        "relative": (f"cd ../../../../{rel} && ls", "relative to home"),
        "userprofile": (f"cat $USERPROFILE/{rel}/README.md", "relative to home"),
        "percent": (f"type %USERPROFILE%\\{rel.replace('/', chr(92))}\\README.md",
                    "relative to home"),
        "sources": ("cat ./x/src/worldparts/system.py", "src/worldparts/"),
        "lock file": ("cat uv.lock", "uv.lock"),
        "fixtures": ("ls ../tests/fixtures/benchmark/tasks", "tests/fixtures/benchmark"),
        "clone": ("git clone https://github.com/raimondasl/worldparts x", "address"),
        "raw": ("curl https://raw.githubusercontent.com/raimondasl/worldparts/main/x", "address"),
    }  # fmt: skip
    for name, (command, marker) in by_marker.items():
        s = grading.parse_stream(_tool_stream({"command": command}, "ok", final))
        for condition in ("code", "lib"):
            reasons = grading.contamination(s, condition, task.id, REPO, home=home)
            assert any(marker in r for r in reasons), (name, condition, reasons)
        assert grading.contamination(s, "mcp", task.id, REPO, home=home) == [], name
    # Whole names only: a sibling checkout whose name starts with the repository's is not
    # the repository, and neither is a longer relative name.
    for command in (f"ls {other}", f"ls {other.as_posix()}", f"cat ~/{rel}-bench/x.txt",
                    f"cat ~/x{rel}/y.txt"):  # fmt: skip
        s = grading.parse_stream(_tool_stream({"command": command}, "ok", final))
        assert grading.contamination(s, "lib", task.id, REPO, home=home) == [], command
    # The address may appear in a result (the installed metadata names it), not in an input.
    s = grading.parse_stream(_tool_stream(
        {"command": "python -m pip show worldparts"},
        "Home-page: https://github.com/raimondasl/worldparts", final))  # fmt: skip
    assert grading.contamination(s, "lib", task.id, REPO, home=home) == []
    # A repository that is not under the home directory has no home-relative form.
    assert grading._home_relative(REPO, home=REPO / "elsewhere") is None
    assert grading._home_relative(REPO, home=REPO.parent) is None  # a bare name is too loose


def test_installed_package_files_are_not_contamination_in_lib(task: Any, tmp_path: Path) -> None:
    """Reading every file the wheel installs (sources, manifests, schemas, metadata with the
    README) is ordinary work in the lib condition: none of them names the repository path,
    the benchmark's files or frozen answers."""
    wheel = runner.build_worldparts_wheel(tmp_path / "dist", log=lambda *a: None)
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        texts = [z.read(n).decode("utf-8", errors="replace") for n in names if not n.endswith("/")]
    assert any(n.endswith(".dist-info/METADATA") for n in names)
    assert any(n.startswith("worldparts/catalog/") and n.endswith(".yaml") for n in names)
    assert not any(n.endswith(".pth") or "__pycache__" in n for n in names)
    s = grading.StreamSummary(tool_input_texts=["python read_all.py"], tool_result_texts=texts)
    assert grading.contamination(s, "lib", task.id) == []
    assert grading.contamination(s, "code", task.id) == [
        "tool result mentions the worldparts library"
    ]


# ----------------------------------------------------------------------------------------
# report with three conditions
# ----------------------------------------------------------------------------------------
def _write_run(run_dir: Path, task: Any, cases: dict[tuple[str, int], dict[str, Any]]) -> None:
    for (cond, rep), values in cases.items():
        d = run_dir / task.id / f"{cond}-{rep}"
        d.mkdir(parents=True)
        stream = _tool_stream({"command": "python solve.py"}, f"flow {task.expected['flow']}",
                              _reply(values))  # fmt: skip
        (d / "stream.jsonl").write_text(stream, encoding="utf-8")
        outcome = {"exit_code": 0, "timed_out": False, "wall_s": 5.0}
        (d / "outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
        cli.grade_run(task, d)


def test_report_with_three_conditions(task: Any, tmp_path: Path) -> None:
    exp = task.expected
    wrong = dict(exp, flow=exp["flow"] * 2)
    run_dir = tmp_path / "run-3"
    _write_run(run_dir, task, {
        ("lib", 1): exp, ("lib", 2): exp,
        ("code", 1): exp, ("code", 2): wrong,
        ("mcp", 1): wrong,
    })  # fmt: skip
    summary = report.write_report(run_dir)
    assert list(summary["conditions"]) == ["mcp", "code", "lib"]
    rates = {c: (r["passed"], r["runs"]) for c, r in summary["conditions"].items()}
    assert rates == {"mcp": (0, 1), "code": (1, 2), "lib": (2, 2)}
    assert summary["by_level"]["lib"]["1"]["pass_rate"] == 1.0
    assert summary["usage"]["lib"]["median_total_tokens"] == 1500
    assert summary["usage_by_level"]["lib"]["1"]["runs"] == 2
    assert summary["per_task"][0]["conditions"]["lib"]["passed"] == 2
    cmp_ = summary["lib_vs_code"]
    assert cmp_["level"] == 1 and cmp_["difference"] == pytest.approx(0.5)
    assert (cmp_["lib"]["passed"], cmp_["code"]["passed"]) == (2, 1)
    # The decision gate stays mcp on level 1; the new line compares lib with code.
    assert summary["decision_gate"]["runs"] == 1
    line = report.comparison_line(summary)
    assert line is not None
    assert line.startswith("Library versus code: level-1 pass rate lib 100 % (2/2, 95 % CI")
    assert "versus code 50 % (1/2, 95 % CI" in line and "+50 points for lib" in line
    assert "median tokens 1,500 (lib) versus 1,500 (code)" in line
    md = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert "Decision gate: level-1 pass rate for condition mcp is 0 % (0/1" in md
    assert f"**{line}**" in md
    assert "| mcp | 0 | 1 |" in md and "| code | 1 | 2 |" in md and "| lib | 2 | 2 |" in md
    assert "| Level | mcp | code | lib |" in md
    assert "| Task | Level | Category | Domain | mcp | code | lib |" in md
    assert "| Answer | mcp | code | lib |" in md
    assert "| 1 (2-4 components) | lib | 100 % (2/2) |" in md
    effort = md.split("## Effort and cost\n")[1].split("##")[0]
    assert effort.index("| mcp |") < effort.index("| code |") < effort.index("| lib |")
    by_level = md.split("## Effort and cost by level\n")[1].split("##")[0]
    level_1 = "| 1 (2-4 components) |"
    assert (by_level.index(f"{level_1} mcp |") < by_level.index(f"{level_1} code |")
            < by_level.index(f"{level_1} lib |"))  # fmt: skip
    assert cmp_["tasks_compared"] == [task.id] and cmp_["tasks_left_out"] == []
    assert line.endswith("; on 1 task(s) with valid runs in both conditions.")
    # The CLI prints both lines.
    assert cli.main(["grade", str(run_dir), "--tasks-dir", str(FIXTURES)]) == 0


def test_report_without_lib_has_no_comparison_and_orders_other_conditions(
    task: Any, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run-2"
    _write_run(run_dir, task, {("code", 1): task.expected, ("mcp", 1): task.expected})
    summary = report.write_report(run_dir)
    assert summary["lib_vs_code"] is None and report.comparison_line(summary) is None
    assert "Library versus code" not in (run_dir / "summary.md").read_text(encoding="utf-8")
    assert report.order_conditions(["oracle", "lib", "zeta", "code", "mcp"]) == [
        "mcp", "code", "lib", "oracle", "zeta"
    ]  # fmt: skip
    # lib without code on level 1: reported, not compared.
    lib_only = tmp_path / "run-lib"
    _write_run(lib_only, task, {("lib", 1): task.expected})
    line = report.comparison_line(report.write_report(lib_only))
    assert line is not None and "code: no level-1 runs" in line and line.endswith("not compared.")


def _write_record(
    run_dir: Path,
    task: Any,
    cond: str,
    rep: int,
    values: dict[str, Any],
    command: str = "python solve.py",
    lib_env: str | None = None,
) -> None:
    d = run_dir / task.id / f"{cond}-{rep}"
    d.mkdir(parents=True)
    stream = _tool_stream({"command": command}, f"flow {task.expected['flow']}", _reply(values))
    (d / "stream.jsonl").write_text(stream, encoding="utf-8")
    outcome: dict[str, Any] = {"exit_code": 0, "timed_out": False, "wall_s": 5.0}
    if lib_env:
        outcome["lib_env"] = {"key": lib_env, "source_sha256": lib_env * 5}
    (d / "outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
    cli.grade_run(task, d)


def test_lib_versus_code_pairs_runs_by_task(task: Any, tmp_path: Path) -> None:
    """Contamination and infrastructure errors can remove a task from one condition only;
    the comparison then leaves that task out instead of comparing different task sets."""
    exp = task.expected
    wrong = dict(exp, flow=exp["flow"] * 2)
    lift = task
    drain = dataclasses.replace(task, id="drain-fixture-01")
    third = dataclasses.replace(task, id="third-fixture-01")
    run_dir = tmp_path / "run-paired"
    _write_record(run_dir, lift, "lib", 1, exp, lib_env="aaa")
    _write_record(run_dir, lift, "code", 1, exp)
    # code-1 on drain read the task files: contaminated, so drain has no valid code run.
    _write_record(run_dir, drain, "lib", 1, exp, lib_env="aaa")
    _write_record(run_dir, drain, "code", 1, exp, command="cat benchmarks/composition/tasks/x")
    # third: lib has repeats 1 and 2, code only 2 (repeat 1 ran into an infrastructure error).
    _write_record(run_dir, third, "lib", 1, exp, lib_env="aaa")
    _write_record(run_dir, third, "lib", 2, wrong, lib_env="aaa")
    _write_record(run_dir, third, "code", 2, wrong)
    summary = report.write_report(run_dir)
    assert [c["task"] for c in summary["contaminated"]] == ["drain-fixture-01"]
    cmp_ = summary["lib_vs_code"]
    assert cmp_["tasks_compared"] == ["lift-fixture-01", "third-fixture-01"]
    assert cmp_["tasks_left_out"] == ["drain-fixture-01"]
    # Paired: lift (1 v 1) and third repeat 2 (wrong v wrong); lib's other runs do not count.
    assert (cmp_["lib"]["passed"], cmp_["lib"]["runs"]) == (1, 2)
    assert (cmp_["code"]["passed"], cmp_["code"]["runs"]) == (1, 2)
    assert cmp_["difference"] == 0
    # All valid level-1 runs, unpaired, are kept for reference.
    assert (cmp_["all_runs"]["lib"]["passed"], cmp_["all_runs"]["lib"]["runs"]) == (3, 4)
    assert cmp_["lib_envs"] == ["aaa"]
    line = report.comparison_line(summary)
    assert line is not None
    assert "lib 50 % (1/2" in line and "versus code 50 % (1/2" in line and "+0 points" in line
    assert line.endswith(
        "; on 2 task(s) with valid runs in both conditions (1 left out: drain-fixture-01)."
    )
    # Pairing: matching repeats first, then the rest in repeat order.
    runs = [{"task": "t", "repeat": k} for k in (1, 2, 3)]
    a, b, _, _ = report.pair_runs(runs, [runs[0], runs[2]])
    assert [r["repeat"] for r in a] == [1, 3] and [r["repeat"] for r in b] == [1, 3]
    a, b, _, _ = report.pair_runs([runs[0]], [{"task": "t", "repeat": 2}])
    assert [r["repeat"] for r in a] == [1] and [r["repeat"] for r in b] == [2]
    # lib runs from two different lib environments are flagged in the line.
    _write_record(run_dir, drain, "lib", 2, exp, lib_env="bbb")
    line = report.comparison_line(report.write_report(run_dir))
    assert line is not None and "2 different lib environments (aaa, bbb)" in line


# ----------------------------------------------------------------------------------------
# CLI: dry run and run
# ----------------------------------------------------------------------------------------
def test_dry_run_shows_the_lib_command_and_preamble(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry run must not start a process")

    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    lib_env = tmp_path / "my-lib-env"
    rc = cli.main(["run", "--dry-run", "--model", "opus", "--tasks-dir", str(FIXTURES),
                   "--conditions", "mcp", "code", "lib", "--lib-env", str(lib_env),
                   "--run-id", "dry-lib"])  # fmt: skip
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 session(s); nothing was run (--dry-run)" in out
    sessions = out.split("=" * 88)
    lib = next(s for s in sessions if "condition=lib" in s)
    code = next(s for s in sessions if "condition=code" in s)
    assert "claude -p --output-format stream-json --verbose --model opus" in lib
    assert "--tools Bash,Read,Write,Edit --allowedTools Bash Read Write Edit" in lib
    assert f"VIRTUAL_ENV={lib_env / '.venv'}" in lib
    assert "preamble (--append-system-prompt):" in lib
    for line in runner.PREAMBLE["lib"].splitlines():
        assert "  | " + line in lib
    assert "  | $ worldparts list  # component types" in lib
    assert "worldparts quick reference" not in code and "code-env" in code
    assert not (REPO / "benchmarks" / "composition" / "results" / "dry-lib").exists()
    # Without --conditions, the v0.2 pair runs.
    cli.main(["run", "--dry-run", "--tasks-dir", str(FIXTURES), "--run-id", "dry-default"])
    out = capsys.readouterr().out
    assert "condition=mcp" in out and "condition=code" in out and "condition=lib" not in out
    with pytest.raises(SystemExit, match="unknown condition 'library'"):
        cli.main(["run", "--dry-run", "--tasks-dir", str(FIXTURES), "--conditions", "library"])


def test_run_gives_each_condition_its_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(cli, "claude_version", lambda: "test")
    monkeypatch.setenv("WPBENCH_CLAUDE", "claude")
    stamp = runner.lib_env_stamp()
    (tmp_path / "lib-env").mkdir()
    (tmp_path / "lib-env" / "stamp.json").write_text(json.dumps(stamp), encoding="utf-8")
    wanted: list[tuple[list[str], Any, Any]] = []

    def fake_envs(conditions: Any, code_env: Any, lib_env: Any, ensure: bool = True) -> Any:
        wanted.append((list(conditions), code_env, lib_env))
        return {"code": tmp_path / "code-env", "lib": tmp_path / "lib-env"}

    seen: dict[str, Any] = {}

    def fake_execute(spec: Any, model: str, max_turns: int, timeout_s: float, env_dir: Any,
                     *a: Any, **k: Any) -> runner.RunOutcome:  # fmt: skip
        seen[spec.condition] = env_dir
        spec.out_dir.mkdir(parents=True, exist_ok=True)
        (spec.out_dir / "stream.jsonl").write_text("", encoding="utf-8")
        return runner.RunOutcome(0, False, 1.0)

    monkeypatch.setattr(cli, "python_env_dirs", fake_envs)
    monkeypatch.setattr(cli, "execute", fake_execute)
    argv = ["run", "--tasks-dir", str(FIXTURES), "--conditions", "mcp", "lib", "--run-id", "r",
            "--lib-env", str(tmp_path / "x")]  # fmt: skip
    assert cli.main(argv) == 0
    assert wanted == [(["mcp", "lib"], None, tmp_path / "x")]
    assert seen == {"mcp": None, "lib": tmp_path / "lib-env"}
    meta = json.loads((tmp_path / "results" / "r" / "run.json").read_text(encoding="utf-8"))
    assert meta["conditions"] == ["mcp", "lib"]
    # run.json records which lib environment (worldparts build) the run used.
    assert meta["lib_env"]["key"] == runner.lib_env_key(stamp)
    assert meta["lib_env"]["source_sha256"] == stamp["worldparts"]["source_sha256"]
    # Resuming keeps the start time, and refuses lib sessions from another build.
    assert cli.main([*argv, "--rerun"]) == 0
    again = json.loads((tmp_path / "results" / "r" / "run.json").read_text(encoding="utf-8"))
    assert again["started"] == meta["started"] and "resumed" in again
    again["lib_env"]["key"] = "000000000000"
    (tmp_path / "results" / "r" / "run.json").write_text(json.dumps(again), encoding="utf-8")
    seen.clear()
    with pytest.raises(SystemExit, match="start a new run"):
        cli.main(argv)
    assert seen == {}
    capsys.readouterr()


def test_a_batch_file_cli_stops_a_lib_run_before_any_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A .cmd wrapper cannot pass the multi-line lib preamble; the run stops before any
    session (of any condition) starts or any environment is built."""
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setenv("WPBENCH_CLAUDE", r"C:\tools\claude.cmd")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing may start")

    monkeypatch.setattr(cli, "python_env_dirs", forbidden)
    monkeypatch.setattr(cli, "execute", forbidden)
    base = ["--tasks-dir", str(FIXTURES), "--run-id", "r"]
    with pytest.raises(SystemExit, match="batch file"):
        cli.main(["run", *base, "--conditions", "mcp", "code", "lib"])
    with pytest.raises(SystemExit, match="batch file"):
        cli.main(["smoke", "--condition", "lib", "--prompt", "hi", "--run-id", "s"])
    assert not (tmp_path / "results" / "r").exists()
    runner.check_executable(["mcp", "code"], claude=r"C:\tools\claude.cmd")  # one-line ones


def test_execute_records_the_lib_environment_of_each_session(
    monkeypatch: pytest.MonkeyPatch, task: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("WPBENCH_CLAUDE", "claude")
    stamp = runner.lib_env_stamp()
    env_dir = tmp_path / "lib-env"
    env_dir.mkdir()
    (env_dir / "stamp.json").write_text(json.dumps(stamp), encoding="utf-8")

    def no_start(*args: Any, **kwargs: Any) -> Any:
        raise OSError("not started in tests")

    monkeypatch.setattr(runner.subprocess, "Popen", no_start)
    out = tmp_path / "run" / task.id / "lib-1"
    spec = runner.RunSpec(task.id, "lib", 1, "prompt", out)
    runner.execute(spec, "opus", 5, 10.0, env_dir)
    outcome = json.loads((out / "outcome.json").read_text(encoding="utf-8"))
    assert outcome["lib_env"]["key"] == runner.lib_env_key(stamp)
    record = cli.grade_run(task, out)
    assert record["outcome"]["lib_env"]["source_sha256"] == stamp["worldparts"]["source_sha256"]
    code_out = tmp_path / "run" / task.id / "code-1"
    runner.execute(runner.RunSpec(task.id, "code", 1, "p", code_out), "opus", 5, 10.0, env_dir)
    assert "lib_env" not in json.loads((code_out / "outcome.json").read_text(encoding="utf-8"))


def test_lib_env_subcommand(monkeypatch: pytest.MonkeyPatch, capsys: Any, tmp_path: Path) -> None:
    got: list[Any] = []
    monkeypatch.setattr(cli, "ensure_lib_env", lambda d: got.append(d) or (d or tmp_path))
    assert cli.main(["lib-env", "--lib-env", str(tmp_path / "e")]) == 0
    assert got == [tmp_path / "e"] and str(tmp_path / "e") in capsys.readouterr().out
    assert cli.main(["lib-env"]) == 0 and got[-1] is None


# ----------------------------------------------------------------------------------------
# opt-in: a real lib environment
# ----------------------------------------------------------------------------------------
@pytest.mark.skipif(
    os.environ.get("WPBENCH_LIB_ENV_TEST") != "1",
    reason="builds a real lib environment (about 20 s); set WPBENCH_LIB_ENV_TEST=1",
)
def test_real_lib_env_runs_the_quick_reference_without_the_repository(tmp_path: Path) -> None:
    """Build a lib environment, then run the probe and every quick-reference line with its
    own ``worldparts`` command and ``python`` in the session environment of a lib run: they
    work, and nothing they print names the repository."""
    env_dir = runner.ensure_lib_env(tmp_path / "lib-env", log=lambda *a: None)
    env, _ = runner.child_env("lib", env_dir)
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = runner._bin_dir(env_dir)
    py = runner.code_env_python(env_dir)
    probe = subprocess.run([str(py), "-c", runner.LIB_ENV_PROBE], env=env, cwd=work,
                           capture_output=True, text=True)  # fmt: skip
    assert probe.returncode == 0, probe.stderr
    outputs = [probe.stdout]
    exe = runner.shutil.which("worldparts", path=env["PATH"])
    assert exe is not None and Path(exe).parent == bin_dir
    for line in runner.LIB_CHEAT_SHEET_SHELL:
        argv = shlex.split(line.split("  #")[0])
        r = subprocess.run([exe, *argv[1:]], env=env, cwd=work, capture_output=True, text=True,
                           encoding="utf-8")  # fmt: skip
        assert r.returncode == 0, (line, r.stderr)
        outputs.append(r.stdout + r.stderr)
    script = work / "sheet.py"
    lines = [*runner.LIB_CHEAT_SHEET_PYTHON, "import worldparts; print(worldparts.__file__)"]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    python = runner.shutil.which("python", path=env["PATH"])
    assert python is not None and Path(python).parent == bin_dir
    r = subprocess.run([python, str(script)], env=env, cwd=work, capture_output=True,
                       text=True, encoding="utf-8")  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert str(env_dir.resolve()).lower() in r.stdout.lower()  # imported from the env
    outputs.append(r.stdout + r.stderr)
    s = grading.StreamSummary(tool_input_texts=["python sheet.py"], tool_result_texts=outputs)
    assert grading.contamination(s, "lib", "x") == []
    repo = str(REPO).lower()
    assert not any(repo in o.lower() or repo.replace("\\", "/") in o.lower() for o in outputs)
