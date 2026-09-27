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

**The lib environment** of ``lib-directed`` and ``lib`` (section 3: "``code+`` plus the
frozen worldparts wheel") lives in ``%LOCALAPPDATA%/worldparts-bench/ops-lib-<key>``. It
holds the code-plus packages at exactly the versions the code-plus environment has (its
``stamp.json``), worldparts installed from a wheel built from this repository (``uv build
--wheel``, not editable), and worldparts' own dependencies at the versions of the
repository's environment. ``<key>`` hashes :func:`lib_request`: the code-plus key, the
versions, the worldparts version, the SHA-256 of the wheel's sources and the repository
commit (with whether those sources differ from it). ``stamp.json`` adds the wheel's file
name and SHA-256. ``run.json`` records all of it (``lib_env``, ``lib_key``), and a real
run refuses a lib environment whose sources differ from the commit. The wheel is refused
when one of its files would trip a contamination marker of the lib arms
(:func:`wheel_problems`). Before freeze-1 the lib environment serves only readiness runs
(``run --readiness``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.grading import StreamSummary
from benchmarks.composition.harness.runner import (
    _bin_dir,
    _clean_parent_env,
    build_worldparts_wheel,
    code_env_python,
    default_code_env_dir,
    installed_constraints,
    worldparts_source_hash,
)

from .bundles import REPO_ROOT, _sha256
from .markers import contamination

PYTHON_VERSION = "3.12"
#: Taken at the version the repository's environment has (uv.lock).
LOCKED_PACKAGES = ("numpy", "scipy", "pandas", "fluids", "wntr", "matplotlib")
#: Not in uv.lock: pinned here at freeze-0a (None would mean the newest compatible version
#: at build time); the installed versions are recorded in stamp.json and run.json.
EXTRA_PINS: dict[str, str | None] = {
    "statsmodels": "0.15.0",
    "scikit-learn": "1.9.1",
    "lmfit": "1.3.4",
}
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


def python_env_for(
    arm_env: str,
    code_plus: Path | None = None,
    ensure: bool = True,
    readiness: bool = False,
    lib: Path | None = None,
) -> Path:
    """The Python environment directory of an arm's environment kind. The lib environment
    is available before freeze-1 only to readiness runs (``readiness=True``)."""
    if arm_env == "code-plus":
        d = code_plus or default_code_plus_dir()
        return ensure_code_plus_env(d) if ensure else d
    if arm_env == "lib" and readiness:
        if not ensure:
            return lib or Path("<ops-lib environment, built by run>")
        return ensure_lib_env(ensure_code_plus_env(code_plus or default_code_plus_dir()), lib)
    raise ValueError(
        f"the {arm_env!r} environment is not available before freeze-1 (except to a "
        "readiness run on the development set)"
    )


# ----------------------------------------------------------------------------------------
# the lib environment (lib-directed, lib)
# ----------------------------------------------------------------------------------------
#: What the worldparts wheel is built from; the commit identifies the wheel only while
#: these are unchanged in the working tree.
WHEEL_SOURCES = ("src/worldparts", "pyproject.toml", "README.md", "LICENSE")
LIB_PREFIX = "ops-lib-"

#: Run with the lib environment's python: every code-plus package and worldparts import,
#: worldparts comes from the environment's site-packages, is not editable and no .pth file
#: puts its sources on the path, and matplotlib picks the Agg backend.
LIB_PROBE = (
    "import importlib, json, os, sysconfig\n"
    "from importlib import metadata\n"
    f"mods = {{name: importlib.import_module(name) for name in {(*IMPORTS, 'worldparts')!r}}}\n"
    "import matplotlib\n"
    'assert matplotlib.get_backend().lower() == "agg", matplotlib.get_backend()\n'
    'site = os.path.normcase(os.path.realpath(sysconfig.get_paths()["purelib"]))\n'
    'where = os.path.normcase(os.path.realpath(os.path.dirname(mods["worldparts"].__file__)))\n'
    'assert where.startswith(site + os.sep), f"worldparts is imported from {where}"\n'
    'direct = metadata.distribution("worldparts").read_text("direct_url.json") or "{}"\n'
    'assert not json.loads(direct).get("dir_info", {}).get("editable"), "editable install"\n'
    "for name in os.listdir(site):\n"
    '    if name.endswith(".pth"):\n'
    '        with open(os.path.join(site, name), encoding="utf-8", errors="replace") as f:\n'
    "            for line in f.read().splitlines():\n"
    '                assert not os.path.isdir(os.path.join(line.strip(), "worldparts")), name\n'
    'print(json.dumps({n: getattr(m, "__version__", "?") for n, m in mods.items()}))\n'
)


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def git_state(repo: Path = REPO_ROOT) -> dict[str, Any]:
    """``{"commit": HEAD, "dirty": whether the wheel's sources differ from it}`` (both None
    without git)."""
    git = shutil.which("git")
    if git is None:
        return {"commit": None, "dirty": None}
    try:
        head = subprocess.run(
            [git, "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout.strip()  # fmt: skip
        status = subprocess.run(
            [git, "-C", str(repo), "status", "--porcelain", "--", *WHEEL_SOURCES],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": head or None, "dirty": bool(status.strip())}


def lib_request(code_plus_dir: Path) -> dict[str, Any]:
    """What a current lib environment is built from (its key is :func:`env_key` of it)."""
    info = env_info(code_plus_dir)
    if info is None:
        raise RuntimeError(
            f"the code-plus environment {code_plus_dir} is not built; the lib environment "
            "takes its package versions (run code-env first)"
        )
    installed = {_canon(k): v for k, v in (info.get("installed") or {}).items()}
    packages = {
        name: installed.get(_canon(name), version)
        for name, version in info["request"]["packages"].items()
    }
    constraints = {_canon(k): v for k, v in installed_constraints().items()}
    constraints.update(installed)  # the code-plus environment's own versions win
    constraints.pop("worldparts", None)
    return {
        "python": PYTHON_VERSION,
        "code_plus_key": info["key"],
        "packages": packages,
        "constraints": dict(sorted(constraints.items())),
        "worldparts": {
            "version": metadata.version("worldparts"),
            "source_sha256": worldparts_source_hash(REPO_ROOT),
            **git_state(),
        },
    }


def default_lib_dir(request: dict[str, Any]) -> Path:
    return default_code_env_dir().parent / f"{LIB_PREFIX}{env_key(request)}"


def lib_env_info(env_dir: Path) -> dict[str, Any] | None:
    """What a built lib environment holds (for run.json), or None: directory, key, the
    code-plus key, the worldparts version, source SHA-256, commit and dirty flag, the
    wheel's file name and SHA-256, and the installed versions."""
    try:
        stamp = json.loads((Path(env_dir) / "stamp.json").read_text(encoding="utf-8"))
        req, wp = stamp["request"], stamp["request"]["worldparts"]
        return {
            "dir": str(env_dir),
            "key": env_key(req),
            "code_plus_key": req["code_plus_key"],
            "worldparts_version": wp["version"],
            "source_sha256": wp["source_sha256"],
            "commit": wp.get("commit"),
            "dirty": wp.get("dirty"),
            "wheel": stamp["wheel"]["file"],
            "wheel_sha256": stamp["wheel"]["sha256"],
            "installed": stamp["installed"],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


#: Licence files of a wheel, which :func:`wheel_problems` does not scan: the Apache licence
#: text has the word "APPENDIX" in capitals, the marker of the sealed appendix, so a lib
#: session that opens the installed licence is flagged for review, as a session that opens
#: the licence files of numpy or matplotlib (which have the word too) is in every arm.
LICENCE_FILE = re.compile(r"(?:^|/)(?:licen[cs]e|copying|notice)[^/]*$|\.dist-info/licenses/", re.I)


def wheel_problems(wheel: Path) -> list[str]:
    """Files of the wheel (and their names) that would trip a contamination marker of the
    lib arms: a lib session that reads the installed package must never be flagged by it
    (licence files aside, :data:`LICENCE_FILE`)."""
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if LICENCE_FILE.search(name):
                continue
            try:
                text = z.read(name).decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            for reason in contamination(StreamSummary(tool_result_texts=[name, text]), "lib"):
                problems.append(f"{name}: {reason.replace('tool result mentions', 'mentions')}")
    return problems


def ensure_lib_env(code_plus_dir: Path, env_dir: Path | None = None, log: Any = print) -> Path:
    """Create the lib environment unless a current one exists; return its directory.

    ``code_plus_dir`` is a built code-plus environment: the lib environment installs its
    packages at the versions it holds, so the two differ only by worldparts (and
    worldparts' own dependencies)."""
    request = lib_request(code_plus_dir)
    env_dir = env_dir or default_lib_dir(request)
    repo = REPO_ROOT.resolve()
    if env_dir.resolve() == repo or repo in env_dir.resolve().parents:
        raise RuntimeError(f"the lib environment {env_dir} must be outside the repository")
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
        raise RuntimeError("uv is not on PATH; it is needed to build the lib environment")
    env_dir.mkdir(parents=True, exist_ok=True)
    stamp.unlink(missing_ok=True)
    clean = _clean_parent_env()
    log(f"creating the lib environment in {env_dir}")
    wheel = build_worldparts_wheel(env_dir / "dist", uv, log)
    problems = wheel_problems(wheel)
    if problems:
        raise RuntimeError("the worldparts wheel is refused: " + "; ".join(problems[:20]))
    wheel_sha = _sha256(wheel)
    subprocess.run(
        [uv, "venv", "--python", PYTHON_VERSION, "--clear", str(env_dir / ".venv")],
        check=True,
        env=clean,
        cwd=env_dir,
    )
    constraints = env_dir / "constraints.txt"
    constraints.write_text(
        "".join(f"{n}=={v}\n" for n, v in request["constraints"].items() if v), encoding="utf-8"
    )
    reqs = [_requirement(n, v) for n, v in sorted(request["packages"].items())]
    subprocess.run(
        [uv, "pip", "install", "--python", str(py), "--constraint", str(constraints),
         *reqs, str(wheel)],
        check=True, env=clean, cwd=env_dir,
    )  # fmt: skip
    out = subprocess.run(
        [str(py), "-c", LIB_PROBE],
        check=True,
        env={**clean, "MPLBACKEND": "Agg"},
        cwd=env_dir,
        capture_output=True,
        text=True,
    )
    log(out.stdout.strip())
    cli = shutil.which("worldparts", path=str(_bin_dir(env_dir)))
    if cli is None:
        raise RuntimeError(f"the worldparts command is missing from {_bin_dir(env_dir)}")
    subprocess.run([cli, "--version"], check=True, env=clean, cwd=env_dir, capture_output=True)
    frozen = subprocess.run(
        [uv, "pip", "freeze", "--python", str(py)],
        check=True,
        env=clean,
        cwd=env_dir,
        capture_output=True,
        text=True,
    )
    stamp.write_text(
        json.dumps(
            {
                "request": request,
                "installed": _parse_freeze(frozen.stdout),
                "wheel": {"file": wheel.name, "sha256": wheel_sha},
            }
        ),
        encoding="utf-8",
    )
    return env_dir


def session_path(env_dir: Path, parent_path: str) -> str:
    """PATH for a session: the environment's scripts directory first."""
    return str(_bin_dir(env_dir)) + os.pathsep + parent_path
