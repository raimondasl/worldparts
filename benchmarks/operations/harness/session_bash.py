"""A private copy of Git Bash for the sessions, whose ``/tmp`` is a benchmark-only folder.

Claude Code runs a session's shell commands in Git Bash. Git's mount table maps ``/tmp`` to
the user's temporary folder (``usertemp``), which every session shares and which does not
follow ``TEMP``. One session could therefore read, or append to, a file another session left
there: the freeze-0a pilot found a session writing its scripts to ``/tmp/a.py`` and appending
to ``/tmp/f.py``. Every session therefore runs with a copy of the Git installation whose
``etc/fstab`` maps ``/tmp`` to ``%LOCALAPPDATA%/worldparts-bench/session-tmp``
(``CLAUDE_CODE_GIT_BASH_PATH``). ``TEMP``, ``TMP`` and ``TMPDIR`` point there too. The
harness empties that folder before each session and moves what the session left there into
its attempt folder afterwards. Sessions then run one at a time (``--jobs 1``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

#: The Git installation Claude Code uses by default on Windows.
DEFAULT_SOURCE = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git"
#: Where the copy and the session /tmp live (outside the repository and benchmark folders).
DEFAULT_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "worldparts-bench"
#: The line of Git's fstab that maps /tmp to the shared user temp folder.
USERTEMP_LINE = "none /tmp usertemp binary,posix=0,noacl 0 0"


@dataclass(frozen=True)
class SessionBash:
    bash: Path
    tmp: Path
    key: str


def fstab_text(source_fstab: str, tmp: Path) -> str:
    """Git's fstab with /tmp mapped to ``tmp`` instead of the user temp folder."""
    mount = (
        f"{tmp.as_posix().replace(' ', chr(92) + '040')} /tmp ntfs binary,posix=0,noacl,user 0 0"
    )
    lines = source_fstab.splitlines()
    if USERTEMP_LINE not in lines:
        raise RuntimeError(f"Git's fstab has no line {USERTEMP_LINE!r}; /tmp cannot be remapped")
    return "\n".join(mount if ln == USERTEMP_LINE else ln for ln in lines) + "\n"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bash_key(source: Path, tmp: Path) -> str:
    """12 hex digits over the source's bash and MSYS runtime and the fstab of the copy."""
    h = hashlib.sha256()
    for rel in ("bin/bash.exe", "usr/bin/bash.exe", "usr/bin/msys-2.0.dll"):
        p = source / rel
        h.update(rel.encode() + b"\0" + (_sha(p).encode() if p.is_file() else b"-"))
    h.update(fstab_text((source / "etc" / "fstab").read_text("utf-8"), tmp).encode())
    return h.hexdigest()[:12]


def ensure_session_bash(source: Path | None = None, root: Path | None = None) -> SessionBash:
    """The copy of Git Bash for the sessions (made once per key; about 360 MB)."""
    source = source or DEFAULT_SOURCE
    root = root or DEFAULT_ROOT
    tmp = root / "session-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    key = bash_key(source, tmp)
    dest = root / f"git-bash-{key}"
    if not (dest / "stamp.json").is_file():
        stage = root / f".git-bash-{key}.partial"
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(source, stage, symlinks=True)
        (stage / "etc" / "fstab").write_text(
            fstab_text((source / "etc" / "fstab").read_text("utf-8"), tmp), "utf-8"
        )
        (stage / "stamp.json").write_text(
            json.dumps({"source": str(source), "tmp": str(tmp), "key": key}, indent=2), "utf-8"
        )
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(stage, dest)
    return SessionBash(dest / "bin" / "bash.exe", tmp, key)


def take_tmp(tmp: Path, dest: Path) -> int:
    """Move every entry of the session /tmp into ``dest``; return how many were moved."""
    if not tmp.is_dir():
        return 0
    entries = list(tmp.iterdir())
    if entries:
        dest.mkdir(parents=True, exist_ok=True)
    for p in entries:
        shutil.move(str(p), str(dest / p.name))
    return len(entries)
