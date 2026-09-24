"""Headless Claude Code sessions for the operations benchmark, and the run layout.

Each session attempt gets a fresh temporary directory outside the repository (prefix
``wpbench-ops-``, which names no arm) whose ``work/`` holds a copy of the task's
realisation without ``task.json``. The CLI is called as in v0.2 (see
:mod:`benchmarks.composition.harness.runner` for why each flag is there)::

    claude -p --output-format stream-json --verbose --model M --max-turns 120 \\
        --setting-sources "" --strict-mcp-config --mcp-config <tmp>/mcp.json \\
        --settings <tmp>/settings.json --permission-mode dontAsk \\
        --disable-slash-commands --no-session-persistence --no-chrome \\
        [--effort E] [--max-budget-usd X] \\
        --tools Bash,Read,Write,Edit --allowedTools Bash Read Write Edit

with the prompt (task.md, the arm's preamble, the answer-format instruction) on stdin and
a wall-clock limit of 2,400 s. The CLI's environment is the v0.2 scrubbed environment
(no parent CLAUDE*/ANTHROPIC* variables except credentials, no repository virtual
environment, :data:`_DROP_EXACT`), without any ``WPBENCH*`` variable (so neither the truth
folder nor the bundle folder is named), with the arm's Python environment first on PATH
and ``MPLBACKEND=Agg``.

Run directory::

    <run>/run.json             run settings (models, arms, set, limits, CLI version, env)
    <run>/index.json           session id -> set, model, task, arm, realisation
    <run>/sessions/<sid>/attempt-<n>/   prompt.txt, command.json, stream.jsonl,
                                        stderr.txt, outcome.json, workdir/
    <run>/sessions/<sid>/record.json    the grade of the latest attempt
    <run>/audit.json           the blind infrastructure audit

Session ids are opaque (a hash of set, model, task, arm and realisation), so the audit,
which reads only ``sessions/<sid>/attempt-<n>/{stream.jsonl,stderr.txt,outcome.json}``,
never sees an arm label.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.runner import (
    ISOLATION_ENV,
    SETTINGS,
    SessionLimits,
    _clean_parent_env,
    _copy_small_tree,
    _kill_tree,
    claude_executable,
    mcp_config,
)

from .arms import Arm, get_arm
from .bundles import REPO_ROOT, OpsTask, copy_realisation
from .env import SESSION_ENV, session_path
from .infra import attempt_dirs

#: Pinned limits (PREREGISTRATION.md section 3, "Pinned settings").
DEFAULT_MAX_TURNS = 120
DEFAULT_TIMEOUT_S = 2400.0
PINNED_LIMITS = SessionLimits(max_turns=DEFAULT_MAX_TURNS, timeout_s=DEFAULT_TIMEOUT_S)

RUN_FILE = "run.json"
INDEX_FILE = "index.json"
SESSIONS_DIR = "sessions"
RECORD_FILE = "record.json"
TMP_PREFIX = "wpbench-ops-"
#: Parent variables never passed to a session (truth and bundle locations, the CLI path).
DROP_PREFIXES = ("WPBENCH",)
#: At most this many files (each at most 1 MB) the agent wrote are kept per attempt.
KEEP_FILES, KEEP_BYTES = 200, 1_000_000


# ----------------------------------------------------------------------------------------
# sessions and ids
# ----------------------------------------------------------------------------------------
def session_id(set_name: str, model: str, task_id: str, arm: str, realisation: int) -> str:
    """The opaque, deterministic id of a session."""
    key = "|".join([set_name, model, task_id, arm, f"r{realisation}"])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SessionSpec:
    """One session: a task realisation, an arm and a model."""

    set_name: str
    model: str
    task: OpsTask
    arm: str
    realisation: int

    @property
    def sid(self) -> str:
        return session_id(self.set_name, self.model, self.task.task_id, self.arm, self.realisation)

    def index_entry(self) -> dict[str, Any]:
        return {
            "set": self.set_name,
            "model": self.model,
            "task": self.task.task_id,
            "arm": self.arm,
            "realisation": self.realisation,
        }

    def label(self) -> str:
        return f"{self.model} {self.task.task_id} {self.arm} r{self.realisation}"


def session_dir(run_dir: Path, sid: str) -> Path:
    return run_dir / SESSIONS_DIR / sid


def attempts_done(run_dir: Path, sid: str) -> int:
    d = session_dir(run_dir, sid)
    return len(attempt_dirs(d)) if d.is_dir() else 0


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------------------
# command and environment
# ----------------------------------------------------------------------------------------
def build_command(
    arm: Arm | str,
    model: str,
    mcp_config_path: Path,
    settings_path: Path,
    max_turns: int,
    effort: str | None = None,
    max_budget_usd: float | None = None,
    claude: str | None = None,
) -> list[str]:
    """The claude argv of one session (the prompt goes to stdin)."""
    a = get_arm(arm) if isinstance(arm, str) else arm
    cmd = [
        claude or claude_executable(),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path),
        "--settings",
        str(settings_path),
        "--permission-mode",
        "dontAsk",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--no-chrome",
    ]
    if effort:
        cmd += ["--effort", effort]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", f"{max_budget_usd:g}"]
    allowed = list(a.allowed_tools) + (["mcp__worldparts"] if a.mcp else [])
    cmd += ["--tools", a.tools, "--allowedTools", *allowed]  # variadic: last
    return cmd


def session_mcp_config(arm: Arm | str, systems_dir: Path) -> dict[str, Any]:
    """No MCP server for the code arms; the worldparts server for mcp-hybrid."""
    a = get_arm(arm) if isinstance(arm, str) else arm
    return mcp_config("mcp" if a.mcp else "code", systems_dir)


def child_env(env_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(full environment of the CLI, the variables the harness set or changed)."""
    env = {k: v for k, v in _clean_parent_env().items() if not k.upper().startswith(DROP_PREFIXES)}
    changed = dict(ISOLATION_ENV)
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = session_path(env_dir, env.get(path_key, ""))
    changed[path_key] = session_path(env_dir, "<parent PATH without the worldparts venv>")
    extra = {"VIRTUAL_ENV": str(env_dir / ".venv"), **SESSION_ENV}
    env.update(extra)
    changed.update(extra)
    env.update(ISOLATION_ENV)
    return env, changed


def _inside(path: Path, roots: list[Path]) -> Path | None:
    p = path.resolve()
    for r in roots:
        rr = r.resolve()
        if p == rr or rr in p.parents:
            return r
    return None


def _snapshot(work: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for p in work.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(work).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def _copy_agent_files(work: Path, before: dict[str, tuple[int, int]], dest: Path) -> int:
    """Copy the files the agent created or changed (bundle files left as they were are
    skipped); at most :data:`KEEP_FILES` files of at most :data:`KEEP_BYTES` each."""
    n = 0
    for p in sorted(work.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(work).as_posix()
        st = p.stat()
        if before.get(rel) == (st.st_size, st.st_mtime_ns) or st.st_size > KEEP_BYTES:
            continue
        n += 1
        if n > KEEP_FILES:
            break
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return n


# ----------------------------------------------------------------------------------------
# one attempt
# ----------------------------------------------------------------------------------------
def execute_session(
    spec: SessionSpec,
    attempt_dir: Path,
    prompt: str,
    limits: SessionLimits,
    env_dir: Path,
    effort: str | None = None,
    max_budget_usd: float | None = None,
    keep_tmp: bool = False,
    tmp_root: Path | None = None,
    forbidden_roots: list[Path] | None = None,
    env_key: str | None = None,
) -> dict[str, Any]:
    """Run one attempt and save everything in ``attempt_dir``; return its outcome.

    ``forbidden_roots`` (the repository is always one) are folders the temporary
    directory must not be inside, such as the bundle and truth folders.
    """
    attempt_dir.mkdir(parents=True, exist_ok=False)
    arm = get_arm(spec.arm)
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.monotonic()
    error: str | None = None
    code: int | None = None
    timed_out = False
    tmp: Path | None = None
    kept_files = 0
    before: dict[str, tuple[int, int]] = {}
    (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    try:
        tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=str(tmp_root) if tmp_root else None))
        bad = _inside(tmp, [REPO_ROOT, *(forbidden_roots or [])])
        if bad is not None:
            raise RuntimeError(f"the session directory {tmp} is inside {bad}")
        work = tmp / "work"
        copy_realisation(spec.task, spec.realisation, work)
        if arm.workdir_extras:  # pragma: no cover - hook for code-skill's reference/ (freeze-1)
            raise RuntimeError(f"{arm.name}: working-directory extras are not available yet")
        before = _snapshot(work)
        (tmp / "systems").mkdir()
        mcp_path = tmp / "mcp.json"
        mcp_doc = session_mcp_config(arm, tmp / "systems")
        mcp_path.write_text(json.dumps(mcp_doc, indent=2), encoding="utf-8")
        settings_path = tmp / "settings.json"
        settings_path.write_text(json.dumps(SETTINGS), encoding="utf-8")
        cmd = build_command(
            arm, spec.model, mcp_path, settings_path, limits.max_turns, effort, max_budget_usd
        )
        env, changed = child_env(env_dir)
        write_json(
            attempt_dir / "command.json",
            {
                "argv": cmd,
                "stdin": "prompt.txt",
                "cwd": str(work),
                "env_changes": changed,
                "mcp_config": mcp_doc,
                "settings": SETTINGS,
            },
        )
    except Exception as exc:  # the harness could not prepare the session
        error = f"could not prepare the session: {exc}"
        cmd = []
        env = {}
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:  # pragma: no cover - Windows is the development platform
        kwargs["start_new_session"] = True
    stream_path, stderr_path = attempt_dir / "stream.jsonl", attempt_dir / "stderr.txt"
    with open(stream_path, "wb") as so, open(stderr_path, "wb") as se:
        proc = None
        if error is None and tmp is not None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=tmp / "work",
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=so,
                    stderr=se,
                    **kwargs,
                )
            except OSError as exc:
                error = f"could not start claude: {exc}"
        if proc is not None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
                code = proc.wait(timeout=limits.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                try:
                    code = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    code = None
    wall = time.monotonic() - t0
    if tmp is not None and (tmp / "work").is_dir() and error is None:
        kept_files = _copy_agent_files(tmp / "work", before, attempt_dir / "workdir")
        if (tmp / "systems").is_dir() and any((tmp / "systems").iterdir()):
            _copy_small_tree(tmp / "systems", attempt_dir / "systems")
    if tmp is not None and not keep_tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    outcome = {
        "exit_code": code,
        "timed_out": timed_out,
        "wall_s": wall,
        "error": error,
        "attempt": int(attempt_dir.name.rpartition("-")[2]),
        "started": started,
        **limits.to_dict(),
        "env_key": env_key,
        "agent_files_kept": kept_files,
        "tmp_dir": str(tmp) if tmp else None,
        "kept": keep_tmp,
    }
    write_json(attempt_dir / "outcome.json", outcome)
    return outcome


def claude_version(claude: str | None = None) -> str | None:
    try:
        r = subprocess.run(
            [claude or claude_executable(), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
