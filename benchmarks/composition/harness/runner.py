"""Run agent sessions with the Claude Code CLI, headless, one fresh directory per run.

Each run gets a temporary directory outside the repository with ``work/`` (the agent's
working directory) and ``systems/`` (MCP autosave). The CLI is called as::

    claude -p --output-format stream-json --verbose --model M --max-turns N \\
        --setting-sources "" --strict-mcp-config --mcp-config <run>/mcp.json \\
        --settings <run>/settings.json --permission-mode dontAsk \\
        --disable-slash-commands --no-session-persistence --no-chrome \\
        --append-system-prompt <condition preamble> \\
        --tools <built-ins> --allowedTools <rules>

with the prompt on stdin. Isolation:

- ``--setting-sources ""`` loads no user, project or local settings (so no hooks,
  permission rules, plugins or env from them); ``--settings`` adds only
  ``disableAllHooks``. Managed (policy) settings, if any, still apply.
- ``CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`` and ``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`` keep
  CLAUDE.md files and memory out; ``--disable-slash-commands`` disables skills.
- ``--strict-mcp-config`` with our ``--mcp-config`` loads only the worldparts server (mcp)
  or no server at all (code).
- ``--tools`` limits the built-in tools (none for mcp; Bash, Read, Write, Edit for code)
  and ``--permission-mode dontAsk`` denies every call that ``--allowedTools`` does not
  allow: ``mcp__worldparts`` for mcp; ``Bash(python *)`` and Read/Write/Edit inside the
  working directory for code.
- ``ENABLE_TOOL_SEARCH=false`` loads tool schemas up front (no deferred tool search).
- The environment passed to the CLI drops the parent's CLAUDE*/ANTHROPIC* variables
  (except credentials) and the worldparts virtual environment; for code, ``python`` on
  PATH is a separate environment with numpy, scipy, fluids and wntr but not worldparts.

Limits: a Python interpreter can do anything the user can (read the repository, open
sockets), so the code condition is restricted, not sandboxed. The default Claude Code
system prompt is kept for both conditions.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tasks import REPO_ROOT

CONDITIONS = ("mcp", "code")

MCP_SERVER_NAME = "worldparts"

PREAMBLE = {
    "mcp": (
        "You are solving an engineering calculation about a water system. Your only tools "
        "are those of the 'worldparts' MCP server: tested hydraulic component models that "
        "you select, parameterise, connect and solve. You cannot run code, read or write "
        "files, or use any other tool. Work until you have the requested values, then give "
        "the final answer as instructed."
    ),
    "code": (
        "You are solving an engineering calculation about a water system. You can write and "
        "read files in the current directory and run Python with the shell: `python "
        "script.py` or `python -c ...`. The Python environment has numpy, scipy, fluids and "
        "wntr installed; no other packages can be installed and there is no network access. "
        "Work until you have the requested values, then give the final answer as instructed."
    ),
}

#: Built-in tools per condition (--tools) and permission rules (--allowedTools).
BUILTIN_TOOLS = {"mcp": "", "code": "Bash,Read,Write,Edit"}
ALLOWED_TOOLS = {
    "mcp": [f"mcp__{MCP_SERVER_NAME}"],
    "code": ["Bash(python *)", "Read(./**)", "Write(./**)", "Edit(./**)"],
}

#: Parent environment variables passed through although they match a dropped prefix.
_KEEP_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_GIT_BASH_PATH",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}
#: Dropped: the parent Claude session's variables and the harness's own virtual env.
_DROP_PREFIXES = ("CLAUDE", "ANTHROPIC_")
_DROP_EXACT = {
    # A parent Claude session may set these; inherited, they make the child start its first
    # turn before the worldparts server has connected (no tools; the model then invents answers).
    "MCP_CONNECTION_NONBLOCKING",
    "MCP_SERVER_CONNECTION_BATCH_SIZE",
    # A parent desktop session may route its own traffic through a local endpoint; the child
    # uses its own CLI login against the default endpoint.
    "ANTHROPIC_BASE_URL",
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "UV_RUN_RECURSION_DEPTH",
    "PYTHONPATH",
    "PYTHONHOME",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
}

#: Added to the CLI environment for every run.
ISOLATION_ENV = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
    "DISABLE_AUTOUPDATER": "1",
    "ENABLE_TOOL_SEARCH": "false",
    "MCP_TIMEOUT": "60000",
}

CODE_ENV_PACKAGES = ("numpy", "scipy", "fluids", "wntr")


# ----------------------------------------------------------------------------------------
# code-condition Python environment
# ----------------------------------------------------------------------------------------
def default_code_env_dir() -> Path:
    """Shared cached environment for the code condition, outside the repository."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    return Path(base) / "worldparts-bench" / "code-env"


def _bin_dir(env_dir: Path) -> Path:
    return env_dir / (".venv/Scripts" if os.name == "nt" else ".venv/bin")


def code_env_python(env_dir: Path) -> Path:
    return _bin_dir(env_dir) / ("python.exe" if os.name == "nt" else "python")


def locked_versions() -> dict[str, str]:
    """Versions of the code-condition packages installed with worldparts (uv.lock).

    Read from the running environment (``uv run`` syncs it from uv.lock), so both
    conditions compute with the same numpy, scipy, fluids and wntr.
    """
    from importlib import metadata

    return {name: metadata.version(name) for name in CODE_ENV_PACKAGES}


def ensure_code_env(env_dir: Path | None = None, log: Any = print) -> Path:
    """Create (once) a venv with numpy, scipy, fluids and wntr, without worldparts.

    Uses the versions installed with worldparts, so uv installs them from its cache. Returns the
    environment directory. Verifies that the packages import and worldparts does not.
    """
    env_dir = env_dir or default_code_env_dir()
    versions = locked_versions()
    stamp = env_dir / "stamp.json"
    want = {"packages": versions, "python": "3.12"}
    py = code_env_python(env_dir)
    if stamp.exists() and py.exists():
        try:
            if json.loads(stamp.read_text(encoding="utf-8")) == want:
                return env_dir
        except json.JSONDecodeError:
            pass
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH; it is needed to build the code environment")
    env_dir.mkdir(parents=True, exist_ok=True)
    clean = _clean_parent_env()
    log(f"creating the code-condition environment in {env_dir}")
    subprocess.run(
        [uv, "venv", "--python", "3.12", "--clear", str(env_dir / ".venv")],
        check=True,
        env=clean,
        cwd=env_dir,
    )
    reqs = [f"{name}=={v}" for name, v in sorted(versions.items())]
    subprocess.run(
        [uv, "pip", "install", "--python", str(py), *reqs], check=True, env=clean, cwd=env_dir
    )
    probe = (
        "import importlib.util, numpy, scipy, fluids, wntr;"
        "assert importlib.util.find_spec('worldparts') is None, 'worldparts is importable';"
        "print('ok', numpy.__version__, scipy.__version__, fluids.__version__, wntr.__version__)"
    )
    out = subprocess.run(
        [str(py), "-c", probe], check=True, env=clean, cwd=env_dir, capture_output=True, text=True
    )
    log(out.stdout.strip())
    stamp.write_text(json.dumps(want), encoding="utf-8")
    return env_dir


# ----------------------------------------------------------------------------------------
# command and environment
# ----------------------------------------------------------------------------------------
def _clean_parent_env() -> dict[str, str]:
    """The parent environment without Claude session variables and the repo's venv."""
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        key = k.upper()
        if key in _DROP_EXACT:
            continue
        if key in _KEEP_ENV or not key.startswith(_DROP_PREFIXES):
            env[k] = v
    venv = str(REPO_ROOT / ".venv").lower().replace("\\", "/")
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    parts = env.get(path_key, "").split(os.pathsep)
    env[path_key] = os.pathsep.join(
        p for p in parts if p and not p.lower().replace("\\", "/").startswith(venv)
    )
    return env


def child_env(condition: str, code_env_dir: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """(full environment for the CLI, the variables the harness set or changed)."""
    env = _clean_parent_env()
    changed = dict(ISOLATION_ENV)
    if condition == "code":
        assert code_env_dir is not None
        path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
        bin_dir = str(_bin_dir(code_env_dir))
        changed[path_key] = bin_dir + os.pathsep + "<parent PATH without the worldparts venv>"
        env[path_key] = bin_dir + os.pathsep + env.get(path_key, "")
        extra = {
            "VIRTUAL_ENV": str(code_env_dir / ".venv"),
            "PIP_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        changed.update(extra)
        env.update(extra)
    env.update(ISOLATION_ENV)
    return env, changed


def mcp_config(condition: str, systems_dir: Path) -> dict[str, Any]:
    """The --mcp-config document: only the worldparts server (mcp) or nothing (code)."""
    if condition != "mcp":
        return {"mcpServers": {}}
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "stdio",
                # Connect before the first turn even if the CLI defaults to non-blocking MCP.
                "alwaysLoad": True,
                "command": "uv",
                "args": ["run", "--directory", REPO_ROOT.as_posix(), "worldparts", "mcp"],
                "env": {"WORLDPARTS_AUTOSAVE_DIR": str(systems_dir)},
            }
        }
    }


SETTINGS = {"disableAllHooks": True}


def claude_executable() -> str:
    return shutil.which("claude") or "claude"


def build_command(
    condition: str,
    model: str,
    mcp_config_path: Path,
    settings_path: Path,
    max_turns: int,
    max_budget_usd: float | None = None,
    claude: str | None = None,
) -> list[str]:
    """The claude argv for one run (the prompt goes to stdin)."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition '{condition}'")
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
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", f"{max_budget_usd:g}"]
    cmd += [
        "--append-system-prompt",
        PREAMBLE[condition],
        "--tools",
        BUILTIN_TOOLS[condition],
        "--allowedTools",  # variadic, so it comes last; the prompt goes to stdin
        *ALLOWED_TOOLS[condition],
    ]
    return cmd


def format_command(cmd: list[str]) -> str:
    """A copy-pasteable rendering of ``cmd`` (POSIX quoting)."""
    import shlex

    return " ".join(shlex.quote(c) for c in cmd)


# ----------------------------------------------------------------------------------------
# one run
# ----------------------------------------------------------------------------------------
@dataclass
class RunSpec:
    """One agent session to run."""

    label: str  # e.g. "pump-lift-01"
    condition: str
    repeat: int
    prompt: str
    out_dir: Path  # results/<run-id>/<task>/<condition>-<repeat>


@dataclass
class RunOutcome:
    exit_code: int | None
    timed_out: bool
    wall_s: float
    error: str | None = None


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:  # pragma: no cover - Windows is the development platform
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _copy_small_tree(src: Path, dst: Path, max_files: int = 200, max_bytes: int = 1_000_000):
    n = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.stat().st_size > max_bytes or "__pycache__" in p.parts:
            continue
        n += 1
        if n > max_files:
            break
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)


def prepare_run(
    spec: RunSpec,
    model: str,
    max_turns: int,
    code_env_dir: Path | None,
    max_budget_usd: float | None = None,
    tmp_root: Path | None = None,
) -> tuple[list[str], dict[str, str], dict[str, str], Path]:
    """Create the run's temporary directory and config files.

    Returns (argv, full env, env changes, temporary directory).
    """
    tmp = Path(
        tempfile.mkdtemp(
            prefix=f"wpbench-{spec.condition}-", dir=str(tmp_root) if tmp_root else None
        )
    )
    if REPO_ROOT.resolve() in tmp.resolve().parents:
        raise RuntimeError(f"run directory {tmp} is inside the repository")
    (tmp / "work").mkdir()
    (tmp / "systems").mkdir()
    mcp_path = tmp / "mcp.json"
    mcp_path.write_text(json.dumps(mcp_config(spec.condition, tmp / "systems"), indent=2), "utf-8")
    settings_path = tmp / "settings.json"
    settings_path.write_text(json.dumps(SETTINGS), encoding="utf-8")
    cmd = build_command(spec.condition, model, mcp_path, settings_path, max_turns, max_budget_usd)
    env, changed = child_env(spec.condition, code_env_dir)
    return cmd, env, changed, tmp


def execute(
    spec: RunSpec,
    model: str,
    max_turns: int,
    timeout_s: float,
    code_env_dir: Path | None,
    max_budget_usd: float | None = None,
    keep_tmp: bool = False,
) -> RunOutcome:
    """Run one session and save everything under ``spec.out_dir``."""
    out = spec.out_dir
    out.mkdir(parents=True, exist_ok=True)
    cmd, env, changed, tmp = prepare_run(spec, model, max_turns, code_env_dir, max_budget_usd)
    work = tmp / "work"
    (out / "prompt.txt").write_text(spec.prompt, encoding="utf-8")
    (out / "command.json").write_text(
        json.dumps(
            {
                "argv": cmd,
                "stdin": "prompt.txt",
                "cwd": str(work),
                "env_changes": changed,
                "mcp_config": json.loads((tmp / "mcp.json").read_text(encoding="utf-8")),
                "settings": SETTINGS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    t0 = time.monotonic()
    timed_out = False
    error = None
    code: int | None = None
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:  # pragma: no cover
        kwargs["start_new_session"] = True
    with open(out / "stream.jsonl", "wb") as so, open(out / "stderr.txt", "wb") as se:
        try:
            proc = subprocess.Popen(
                cmd, cwd=work, env=env, stdin=subprocess.PIPE, stdout=so, stderr=se, **kwargs
            )
        except OSError as exc:
            error = f"could not start claude: {exc}"
            proc = None
        if proc is not None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(spec.prompt.encode("utf-8"))
                proc.stdin.close()
                code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                try:
                    code = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    code = None
    wall = time.monotonic() - t0
    for sub, name in ((tmp / "systems", "systems"), (work, "workdir")):
        if sub.exists() and any(sub.iterdir()):
            _copy_small_tree(sub, out / name)
    if not keep_tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    outcome = RunOutcome(code, timed_out, wall, error)
    (out / "outcome.json").write_text(
        json.dumps({**outcome.__dict__, "tmp_dir": str(tmp), "kept": keep_tmp}, indent=2),
        encoding="utf-8",
    )
    return outcome


def claude_version() -> str | None:
    try:
        r = subprocess.run(
            [claude_executable(), "--version"], capture_output=True, text=True, timeout=60
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


if __name__ == "__main__":  # pragma: no cover
    sys.exit("use: python -m benchmarks.composition.harness run ...")
