"""The ``code-plus`` Python environment of the code arms (PREREGISTRATION.md section 3).

It has numpy, scipy, pandas, statsmodels, scikit-learn, lmfit, fluids, wntr and matplotlib
and never worldparts. It lives outside the repository in
``%LOCALAPPDATA%/worldparts-bench/ops-code-plus-<key>``, where ``<key>`` is a hash of what
the environment is built from (:func:`code_plus_request`): numpy, scipy, pandas, fluids,
wntr and matplotlib at the versions of the repository's environment (uv.lock), and
statsmodels, scikit-learn and lmfit at :data:`EXTRA_PINS` (``None``: the newest version
compatible with the others at build time). A different request gets its own directory,
so building one never clears an environment that a running benchmark uses.

``stamp.json`` holds the request and the installed distributions (``uv pip freeze``); it
is removed before a build and written last, so a half-built environment is never taken as
current. ``run.json`` and every session's ``outcome.json`` record the key.

The session environment puts the environment's scripts directory first on PATH (so
``python`` is its interpreter) and sets ``MPLBACKEND=Agg``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.runner import (
    _bin_dir,
    _clean_parent_env,
    code_env_python,
    default_code_env_dir,
)

from .bundles import REPO_ROOT

PYTHON_VERSION = "3.12"
#: Taken at the version the repository's environment has (uv.lock).
LOCKED_PACKAGES = ("numpy", "scipy", "pandas", "fluids", "wntr", "matplotlib")
#: Not in uv.lock: pinned here (None: the newest compatible version at build time; the
#: installed version is recorded in stamp.json and run.json).
EXTRA_PINS: dict[str, str | None] = {"statsmodels": None, "scikit-learn": None, "lmfit": None}
#: Import names of every package the preamble lists, in its order.
IMPORTS = (
    "numpy",
    "scipy",
    "pandas",
    "statsmodels",
    "sklearn",
    "lmfit",
    "fluids",
    "wntr",
    "matplotlib",
)

#: Run with the environment's python: every package imports, worldparts does not, and
#: matplotlib picks the Agg backend.
PROBE = (
    "import importlib, importlib.util, json\n"
    f"mods = {{name: importlib.import_module(name) for name in {IMPORTS!r}}}\n"
    'assert importlib.util.find_spec("worldparts") is None, "worldparts is importable"\n'
    "import matplotlib\n"
    'assert matplotlib.get_backend().lower() == "agg", matplotlib.get_backend()\n'
    'print(json.dumps({n: getattr(m, "__version__", "?") for n, m in mods.items()}))\n'
)

#: Session variables of every code-plus session (besides PATH and the isolation set).
SESSION_ENV = {
    "PIP_NO_INDEX": "1",
    "UV_OFFLINE": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "MPLBACKEND": "Agg",
}


def code_plus_request() -> dict[str, Any]:
    """What a current code-plus environment is built from."""
    locked = {name: metadata.version(name) for name in LOCKED_PACKAGES}
    return {"python": PYTHON_VERSION, "packages": {**locked, **EXTRA_PINS}}


def env_key(request: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def default_code_plus_dir(request: dict[str, Any] | None = None) -> Path:
    request = code_plus_request() if request is None else request
    return default_code_env_dir().parent / f"ops-code-plus-{env_key(request)}"


def env_info(env_dir: Path) -> dict[str, Any] | None:
    """Directory, key, request and installed versions of a built environment, or None."""
    try:
        stamp = json.loads((Path(env_dir) / "stamp.json").read_text(encoding="utf-8"))
        return {
            "dir": str(env_dir),
            "key": env_key(stamp["request"]),
            "request": stamp["request"],
            "installed": stamp["installed"],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _requirement(name: str, version: str | None) -> str:
    return f"{name}=={version}" if version else name


def _parse_freeze(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, version = line.strip().partition("==")
        if sep:
            out[name] = version
    return out


def ensure_code_plus_env(env_dir: Path | None = None, log: Any = print) -> Path:
    """Create the code-plus environment unless a current one exists; return its directory."""
    request = code_plus_request()
    env_dir = env_dir or default_code_plus_dir(request)
    repo = REPO_ROOT.resolve()
    if env_dir.resolve() == repo or repo in env_dir.resolve().parents:
        raise RuntimeError(f"the code-plus environment {env_dir} must be outside the repository")
    stamp = env_dir / "stamp.json"
    py = code_env_python(env_dir)
    if stamp.exists() and py.exists():
        try:
            if json.loads(stamp.read_text(encoding="utf-8")).get("request") == request:
                return env_dir
        except (json.JSONDecodeError, AttributeError):
            pass
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH; it is needed to build the code-plus environment")
    env_dir.mkdir(parents=True, exist_ok=True)
    stamp.unlink(missing_ok=True)
    clean = _clean_parent_env()
    log(f"creating the code-plus environment in {env_dir}")
    subprocess.run(
        [uv, "venv", "--python", PYTHON_VERSION, "--clear", str(env_dir / ".venv")],
        check=True,
        env=clean,
        cwd=env_dir,
    )
    reqs = [_requirement(n, v) for n, v in sorted(request["packages"].items())]
    subprocess.run(
        [uv, "pip", "install", "--python", str(py), *reqs], check=True, env=clean, cwd=env_dir
    )
    probe_env = {**clean, "MPLBACKEND": "Agg"}
    out = subprocess.run(
        [str(py), "-c", PROBE],
        check=True,
        env=probe_env,
        cwd=env_dir,
        capture_output=True,
        text=True,
    )
    log(out.stdout.strip())
    frozen = subprocess.run(
        [uv, "pip", "freeze", "--python", str(py)],
        check=True,
        env=clean,
        cwd=env_dir,
        capture_output=True,
        text=True,
    )
    installed = _parse_freeze(frozen.stdout)
    stamp.write_text(json.dumps({"request": request, "installed": installed}), encoding="utf-8")
    return env_dir


def python_env_for(arm_env: str, code_plus: Path | None = None, ensure: bool = True) -> Path:
    """The Python environment directory of an arm's environment kind."""
    if arm_env == "code-plus":
        d = code_plus or default_code_plus_dir()
        return ensure_code_plus_env(d) if ensure else d
    raise ValueError(f"the {arm_env!r} environment is not available before freeze-1")


def session_path(env_dir: Path, parent_path: str) -> str:
    """PATH for a session: the environment's scripts directory first."""
    return str(_bin_dir(env_dir)) + os.pathsep + parent_path
