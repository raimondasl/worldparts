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
  or no server at all (code, lib).
- ``--tools`` limits the built-in tools (none for mcp; Bash, Read, Write, Edit for code
  and lib) and ``--permission-mode dontAsk`` denies every call that ``--allowedTools``
  does not allow: ``mcp__worldparts`` for mcp; Bash, Read, Write and Edit for code and lib.
- ``ENABLE_TOOL_SEARCH=false`` loads tool schemas up front (no deferred tool search).
- The environment passed to the CLI drops the parent's CLAUDE*/ANTHROPIC* variables
  (except credentials) and the worldparts virtual environment; for code, ``python`` on
  PATH is a separate environment with numpy, scipy, fluids and wntr but not worldparts;
  for lib, another one with the same packages plus worldparts installed from a wheel built
  from this repository (not editable, so the package does not point back to the
  repository), whose ``worldparts`` command is on PATH too.

Limits: a Python interpreter can do anything the user can (read the repository, open
sockets), so the code and lib conditions are restricted, not sandboxed. The default Claude
Code system prompt is kept for every condition.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tasks import LEVEL_BOUNDS, REPO_ROOT

CONDITIONS = ("mcp", "code", "lib")
#: What ``run`` runs without ``--conditions`` (the v0.2 pair; lib is opt-in).
DEFAULT_CONDITIONS = ("mcp", "code")
#: Conditions whose agent writes and runs its own code in a separate Python environment.
PYTHON_CONDITIONS = ("code", "lib")

MCP_SERVER_NAME = "worldparts"

#: The lib condition's quick reference. Each line is run by the tests (the shell lines
#: through ``worldparts.cli.main``, the Python lines in order as one script), so it cannot
#: drift from the package.
LIB_CHEAT_SHEET_SHELL = (
    "worldparts list  # component types (short aliases) and their ports",
    "worldparts describe centrifugal_pump  # ports; parameters and inputs with units, "
    "defaults and limits; outputs; warnings",
)
LIB_CHEAT_SHEET_PYTHON = (
    "import worldparts as wp",
    's = wp.System("demo")',
    's.add("a", "tank", diameter="1.2 m", initial_level="2 m")  # instance name, '
    "component alias, parameters and inputs",
    's.add("p", "centrifugal_pump", head_curve=[[0, 32], [10, 29], [20, 21]], speed=1.0)'
    "  # a table is a list of rows",
    's.add("link", "pipe", length="40 m", diameter="40 mm", height_difference="8 m", minor_loss=2)',
    's.add("v", "valve", kv=6, opening=0.6); s.add("b", "tank", diameter="1.2 m", '
    'initial_level="0.5 m")',
    's.connect("a.outlet", "p.inlet")  # several ports on one node form a junction',
    's.connect("p.outlet", "link.port_a"); s.connect("link.port_b", "v.port_a"); '
    's.connect("v.port_b", "b.inlet")',
    "print(s.check())  # issues before solving (errors; warnings such as an unconnected port)",
    "r = s.solve()  # steady operating point",
    'print(r["p.volume_flow"], r.units["p.volume_flow"], r.units["link.volume_flow"], '
    'r.get("link.volume_flow", unit="m3/h"))',
    "print([(w.path, w.severity, w.message) for w in r.warnings])",
    's.set("v.opening", 0.8)  # change an input or parameter, then solve or simulate again',
    'sim = s.simulate(duration="10 min", step="10 s", '
    'events=[{"at": "5 min", "set": {"p.speed": 0.9}}])',
    'print(sim.final["b.level"], sim.time[-1], sim.series["a.level"][:3], '
    "[(w.time, w.path) for w in sim.warnings])",
    's.add_control("hold", "pi", measure="p.outlet.p", setpoint="2.5 bar", '
    'actuate="p.speed", gain=0.1, integral_time="2 s", output_min=0.3, output_max=1.2)'
    '  # or "hysteresis": on_below, off_above, on_value, off_value',
    "doc = s.to_dict(); s2 = wp.System.from_dict(doc)  # a whole system document: "
    'worldparts_system "0.1", components, connections, controls',
    "print([v.path for v in s.variables()])  # every readable path",
)
LIB_CHEAT_SHEET_NOTES = (
    "Plain numbers are in each variable's declared unit (as describe shows); strings may "
    'carry units ("40 m3/h", "50 mm"). Pressures are gauge unless stated ("2 bar absolute").',
    "Flow units differ by part (pipes, valves, drains, supplies: L/min; pumps, tanks, "
    "filters, UV reactors: m3/h); r.units[path] gives each result's unit and "
    "r.get(path, unit=...) converts; port pressures are in bar gauge.",
)


def lib_cheat_sheet() -> str:
    """The quick reference appended to the lib preamble (shell lines prefixed with $)."""
    lines = ["worldparts quick reference:"]
    lines += [f"$ {line}" for line in LIB_CHEAT_SHEET_SHELL]
    lines += list(LIB_CHEAT_SHEET_PYTHON)
    lines += list(LIB_CHEAT_SHEET_NOTES)
    return "\n".join(lines)


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
        "read files in the current directory and run any shell command there, including "
        "Python: `python script.py` or `python -c ...`. The Python environment has numpy, "
        "scipy, fluids and wntr installed; no other packages can be installed and there is "
        "no network access. "
        "Work until you have the requested values, then give the final answer as instructed."
    ),
    "lib": (
        "You are solving an engineering calculation about a water system. You can write and "
        "read files in the current directory and run any shell command there, including "
        "Python: `python script.py` or `python -c ...`. The Python environment has numpy, "
        "scipy, fluids and wntr installed, and the package worldparts (hydraulic "
        "component models, such as pumps, pipes, valves, tanks, filters and UV reactors, with "
        "a steady solver and a time simulator; its command `worldparts` is on PATH). No other "
        "packages can be installed and there is no network access. "
        "Work until you have the requested values, then give the final answer as instructed."
        "\n\n" + lib_cheat_sheet()
    ),
}

#: Built-in tools per condition (--tools) and permission rules (--allowedTools).
BUILTIN_TOOLS = {"mcp": "", "code": "Bash,Read,Write,Edit", "lib": "Bash,Read,Write,Edit"}
ALLOWED_TOOLS = {
    "mcp": [f"mcp__{MCP_SERVER_NAME}"],
    # Unrestricted within the session: a pattern such as Bash(python *) denied ordinary
    # commands (`sed ...; python s.py`) and made agents give up, which measured the harness,
    # not the agent. Reads of the benchmark files are caught by grading.contamination().
    "code": ["Bash", "Read", "Write", "Edit"],
    # The same tools and permissions as code: the conditions differ only in the environment.
    "lib": ["Bash", "Read", "Write", "Edit"],
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
# per-session limits
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionLimits:
    """The turn and wall-clock limits of one session."""

    max_turns: int
    timeout_s: float

    def to_dict(self) -> dict[str, Any]:
        return {"max_turns": self.max_turns, "timeout_s": self.timeout_s}


#: Defaults of ``run`` (``--max-turns``, ``--timeout``) for every level.
DEFAULT_LIMITS = SessionLimits(max_turns=80, timeout_s=1800.0)

#: Recommended per-level limits, applied only with ``run --recommended-level-limits``: a
#: level-4 (scale) task means building a 20-80 component network and simulating it for up
#: to 24 hours, which takes a from-scratch agent more turns and time than the defaults.
RECOMMENDED_LEVEL_LIMITS = {4: SessionLimits(max_turns=120, timeout_s=2400.0)}


def parse_level_values(values: list[str] | None, what: str, integer: bool) -> dict[int, Any]:
    """``["4=120", "3=100,2=90"]`` -> {4: 120, 3: 100, 2: 90} (positive, finite numbers).

    Raises ValueError naming ``what`` (e.g. '--level-max-turns') for a malformed item, an
    unknown level or a value that is not positive.
    """
    out: dict[int, Any] = {}
    for item in (x for v in values or [] for x in re.split(r"[,\s]+", v) if x):
        level_text, sep, value_text = item.partition("=")
        try:
            if not sep:
                raise ValueError
            level = int(level_text)
            value = int(value_text) if integer else float(value_text)
        except ValueError:
            kind = "an integer" if integer else "a number"
            raise ValueError(f"{what}: '{item}' is not LEVEL=VALUE with {kind} VALUE") from None
        if level not in LEVEL_BOUNDS:
            raise ValueError(
                f"{what}: unknown level {level} (levels are {', '.join(map(str, LEVEL_BOUNDS))})"
            )
        if not 0 < value < float("inf"):
            raise ValueError(f"{what}: the value for level {level} must be positive and finite")
        out[level] = value
    return out


def session_limits(
    level: int | None,
    base: SessionLimits,
    max_turns: dict[int, int] | None = None,
    timeout_s: dict[int, float] | None = None,
    recommended: bool = False,
) -> SessionLimits:
    """The limits of a session on a task of ``level`` (None: no task, e.g. smoke).

    Precedence, per limit: an explicit per-level override (``max_turns``/``timeout_s``,
    from ``--level-max-turns``/``--level-timeout``), then :data:`RECOMMENDED_LEVEL_LIMITS`
    when ``recommended``, then ``base`` (``--max-turns``/``--timeout``).
    """
    if level is None:
        return base
    turns, secs = base.max_turns, base.timeout_s
    rec = RECOMMENDED_LEVEL_LIMITS.get(level) if recommended else None
    if rec is not None:
        turns, secs = rec.max_turns, rec.timeout_s
    turns = (max_turns or {}).get(level, turns)
    secs = float((timeout_s or {}).get(level, secs))
    return SessionLimits(max_turns=int(turns), timeout_s=secs)


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
# lib-condition Python environment
# ----------------------------------------------------------------------------------------
def lib_env_key(stamp: dict[str, Any]) -> str:
    """A short hash of a lib environment's stamp (:func:`lib_env_stamp`): its identity."""
    import hashlib

    return hashlib.sha256(json.dumps(stamp, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def default_lib_env_dir(stamp: dict[str, Any] | None = None) -> Path:
    """Cached environment for the lib condition, next to the code environment, keyed by
    what it is built from (``lib-env-<key>``, :func:`lib_env_key` of ``stamp``, by default
    :func:`lib_env_stamp`). A change of the sources or versions, or another checkout with
    other sources, gets its own directory, so building it never clears an environment that
    a running benchmark uses; old ones can be deleted when no run uses them."""
    stamp = lib_env_stamp() if stamp is None else stamp
    return default_code_env_dir().parent / f"lib-env-{lib_env_key(stamp)}"


def lib_env_info(env_dir: Path) -> dict[str, Any] | None:
    """What a built lib environment holds (from its stamp.json), for run.json and each lib
    session's outcome: its directory, key, worldparts version and source hash; None when
    the environment has no readable stamp."""
    try:
        stamp = json.loads((Path(env_dir) / "stamp.json").read_text(encoding="utf-8"))
        wp_info = stamp["worldparts"]
        return {
            "dir": str(env_dir),
            "key": lib_env_key(stamp),
            "worldparts_version": wp_info["version"],
            "source_sha256": wp_info["source_sha256"],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


#: What the worldparts wheel is built from besides src/worldparts (hashed to decide whether
#: the lib environment is stale).
_WHEEL_SOURCES = ("pyproject.toml", "README.md", "LICENSE")

#: Run with the lib environment's python: the packages import, worldparts comes from that
#: environment's site-packages, and nothing (an editable install, a .pth file) points back
#: to the repository.
LIB_ENV_PROBE = """\
import json, os, sysconfig
from importlib import metadata
import numpy, scipy, fluids, wntr, worldparts
site = os.path.normcase(os.path.realpath(sysconfig.get_paths()["purelib"]))
where = os.path.normcase(os.path.realpath(os.path.dirname(worldparts.__file__)))
assert where.startswith(site + os.sep), f"worldparts is imported from {where}, not {site}"
direct = metadata.distribution("worldparts").read_text("direct_url.json") or "{}"
info = json.loads(direct)
assert not info.get("dir_info", {}).get("editable"), f"worldparts is installed editable: {info}"
for name in os.listdir(site):
    if name.endswith(".pth"):
        with open(os.path.join(site, name), encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines():
                pkg = os.path.join(line.strip(), "worldparts")
                assert not os.path.isdir(pkg), f"{name} puts worldparts sources ({pkg}) on sys.path"
print("ok", numpy.__version__, scipy.__version__, fluids.__version__, wntr.__version__,
      "worldparts", metadata.version("worldparts"))
"""


def worldparts_source_hash(repo_root: Path = REPO_ROOT) -> str:
    """SHA-256 over the files the wheel is built from (the package sources and metadata)."""
    import hashlib

    h = hashlib.sha256()
    pkg = repo_root / "src" / "worldparts"
    files = [p for p in pkg.rglob("*") if p.is_file()]
    files = [p for p in files if "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
    files += [repo_root / name for name in _WHEEL_SOURCES if (repo_root / name).is_file()]
    for p in sorted(files, key=lambda q: q.relative_to(repo_root).as_posix()):
        h.update(p.relative_to(repo_root).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return h.hexdigest()


def installed_constraints() -> dict[str, str]:
    """Name and version of every distribution in the running environment but worldparts.

    Used as constraints for the lib environment, so worldparts' own dependencies (pint,
    jsonschema, mcp, ...) get the versions that uv.lock gives the repository.
    """
    from importlib import metadata

    out: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name and re.sub(r"[-_.]+", "-", name).lower() != "worldparts":
            out[name] = dist.version
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def build_worldparts_wheel(out_dir: Path, uv: str | None = None, log: Any = print) -> Path:
    """Build a worldparts wheel from REPO_ROOT into ``out_dir`` (emptied first); its path."""
    uv = uv or shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH; it is needed to build the worldparts wheel")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out_dir), str(REPO_ROOT)],
        check=True,
        env=_clean_parent_env(),
        cwd=out_dir,
    )
    wheels = sorted(out_dir.glob("worldparts-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one worldparts wheel in {out_dir}, found {len(wheels)}")
    log(f"built {wheels[0].name}")
    return wheels[0]


def lib_env_stamp() -> dict[str, Any]:
    """What a current lib environment is built from (compared with its stamp.json)."""
    from importlib import metadata

    return {
        "packages": locked_versions(),
        "python": "3.12",
        "worldparts": {
            "version": metadata.version("worldparts"),
            "source_sha256": worldparts_source_hash(),
        },
        "constraints": installed_constraints(),
    }


def ensure_lib_env(env_dir: Path | None = None, log: Any = print) -> Path:
    """Create (or refresh) a venv with numpy, scipy, fluids, wntr and worldparts.

    The packages have the versions of the code environment (the repository's uv.lock), and
    worldparts is installed from a wheel built from this repository, not editable: the
    agent's Python sees an ordinary installed package, not the repository. The environment
    is rebuilt when the worldparts sources or the locked versions change. Verifies the
    imports, the non-editable install (:data:`LIB_ENV_PROBE`) and the ``worldparts``
    command. Returns the environment directory.

    Without ``env_dir``, the directory is keyed by the stamp (:func:`default_lib_env_dir`),
    so a rebuild only happens in a directory that is missing or half built. The stamp is
    removed before a rebuild starts and written last, so a half-built environment is never
    taken as current.
    """
    want = lib_env_stamp()
    env_dir = env_dir or default_lib_env_dir(want)
    repo = REPO_ROOT.resolve()
    if env_dir.resolve() == repo or repo in env_dir.resolve().parents:
        raise RuntimeError(f"the lib environment {env_dir} must be outside the repository")
    stamp = env_dir / "stamp.json"
    py = code_env_python(env_dir)
    if stamp.exists() and py.exists():
        try:
            if json.loads(stamp.read_text(encoding="utf-8")) == want:
                return env_dir
        except json.JSONDecodeError:
            pass
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH; it is needed to build the lib environment")
    env_dir.mkdir(parents=True, exist_ok=True)
    stamp.unlink(missing_ok=True)
    clean = _clean_parent_env()
    log(f"creating the lib-condition environment in {env_dir}")
    wheel = build_worldparts_wheel(env_dir / "dist", uv, log)
    subprocess.run(
        [uv, "venv", "--python", "3.12", "--clear", str(env_dir / ".venv")],
        check=True,
        env=clean,
        cwd=env_dir,
    )
    constraints = env_dir / "constraints.txt"
    constraints.write_text(
        "".join(f"{name}=={v}\n" for name, v in want["constraints"].items()), encoding="utf-8"
    )
    reqs = [f"{name}=={v}" for name, v in sorted(want["packages"].items())]
    subprocess.run(
        [uv, "pip", "install", "--python", str(py), "--constraint", str(constraints),
         *reqs, str(wheel)],
        check=True,
        env=clean,
        cwd=env_dir,
    )  # fmt: skip
    out = subprocess.run(
        [str(py), "-c", LIB_ENV_PROBE],
        check=True,
        env=clean,
        cwd=env_dir,
        capture_output=True,
        text=True,
    )
    log(out.stdout.strip())
    cli = shutil.which("worldparts", path=str(_bin_dir(env_dir)))
    if cli is None:
        raise RuntimeError(f"the worldparts command is missing from {_bin_dir(env_dir)}")
    version = subprocess.run(
        [cli, "--version"], check=True, env=clean, cwd=env_dir, capture_output=True, text=True
    )
    log(f"{cli}: {version.stdout.strip()}")
    stamp.write_text(json.dumps(want), encoding="utf-8")
    return env_dir


def python_env_dirs(
    conditions: list[str] | tuple[str, ...],
    code_env: Path | None = None,
    lib_env: Path | None = None,
    ensure: bool = True,
) -> dict[str, Path]:
    """The Python environment of each code-running condition among ``conditions``.

    With ``ensure``, each one is created or refreshed first (:func:`ensure_code_env`,
    :func:`ensure_lib_env`); without it, the directories are only resolved (dry runs).
    """
    out: dict[str, Path] = {}
    if "code" in conditions:
        d = code_env or default_code_env_dir()
        out["code"] = ensure_code_env(d) if ensure else d
    if "lib" in conditions:
        if ensure:
            out["lib"] = ensure_lib_env(lib_env)
        else:
            out["lib"] = lib_env or default_lib_env_dir()
    return out


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


def child_env(condition: str, env_dir: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """(full environment for the CLI, the variables the harness set or changed).

    ``env_dir`` is the condition's Python environment (code or lib; None for mcp): its
    scripts directory goes first on PATH, so ``python`` (and, for lib, ``worldparts``) are
    that environment's.
    """
    env = _clean_parent_env()
    changed = dict(ISOLATION_ENV)
    if condition in PYTHON_CONDITIONS:
        if env_dir is None:
            raise ValueError(f"condition '{condition}' needs its Python environment directory")
        path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
        bin_dir = str(_bin_dir(env_dir))
        changed[path_key] = bin_dir + os.pathsep + "<parent PATH without the worldparts venv>"
        env[path_key] = bin_dir + os.pathsep + env.get(path_key, "")
        extra = {
            "VIRTUAL_ENV": str(env_dir / ".venv"),
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
    """The --mcp-config document: only the worldparts server (mcp) or nothing (code, lib)."""
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
    """The Claude Code CLI to run: $WPBENCH_CLAUDE if set, else `claude` on PATH.

    Set WPBENCH_CLAUDE when the CLI on PATH is too old for the model under test (for example
    to the newer executable bundled with the Claude desktop app).
    """
    return os.environ.get("WPBENCH_CLAUDE") or shutil.which("claude") or "claude"


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
    exe = claude or claude_executable()
    if "\n" in PREAMBLE[condition] and Path(exe).suffix.lower() in (".cmd", ".bat"):
        # cmd.exe ends an argument at a line break, which would cut the preamble short.
        raise ValueError(
            f"{exe} is a batch file, which cannot pass the multi-line {condition} preamble; "
            "set WPBENCH_CLAUDE to the claude executable"
        )
    cmd = [
        exe,
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


def check_executable(conditions: list[str] | tuple[str, ...], claude: str | None = None) -> None:
    """Raise ValueError when the CLI cannot run one of ``conditions`` (a batch-file wrapper
    cannot pass the multi-line lib preamble); called before any session starts."""
    exe = claude or claude_executable()
    for condition in conditions:
        build_command(condition, "model", Path("mcp.json"), Path("settings.json"), 1, claude=exe)


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
    env_dir: Path | None,
    max_budget_usd: float | None = None,
    tmp_root: Path | None = None,
) -> tuple[list[str], dict[str, str], dict[str, str], Path]:
    """Create the run's temporary directory and config files.

    ``env_dir`` is the condition's Python environment (code, lib) or None (mcp). Returns
    (argv, full env, env changes, temporary directory).
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
    env, changed = child_env(spec.condition, env_dir)
    return cmd, env, changed, tmp


def execute(
    spec: RunSpec,
    model: str,
    max_turns: int,
    timeout_s: float,
    env_dir: Path | None,
    max_budget_usd: float | None = None,
    keep_tmp: bool = False,
) -> RunOutcome:
    """Run one session and save everything under ``spec.out_dir``.

    ``env_dir`` is the condition's Python environment (code, lib) or None (mcp).
    """
    out = spec.out_dir
    out.mkdir(parents=True, exist_ok=True)
    cmd, env, changed, tmp = prepare_run(spec, model, max_turns, env_dir, max_budget_usd)
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
    extra: dict[str, Any] = SessionLimits(max_turns, timeout_s).to_dict()
    if spec.condition == "lib" and env_dir is not None:
        extra["lib_env"] = lib_env_info(env_dir)  # which worldparts the session had
    (out / "outcome.json").write_text(
        json.dumps({**outcome.__dict__, **extra, "tmp_dir": str(tmp), "kept": keep_tmp}, indent=2),
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
