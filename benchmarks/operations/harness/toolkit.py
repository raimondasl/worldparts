"""The ``code-skill`` reference toolkit (PREREGISTRATION.md section 3, "The code-skill
toolkit").

Build track B writes the toolkit after Stage 0, in a workspace without the worldparts
sources; it is frozen at freeze-1. Its folder is given by ``$WPBENCH_OPS_TOOLKIT`` (or
``run --toolkit DIR``), and every ``code-skill`` session gets a copy of it as
``reference/`` in its working directory, next to the bundle. The preamble adds the
checklist and the sentence "Adapt and use the tools in reference/ for this plant."

- **Manifest.** The toolkit is recorded by the SHA-256 of every file (``.git``, caches and
  ``*.pyc`` left out) and a digest over them, in the same form as a bundle's
  (:func:`benchmarks.operations.harness.bundles.realisation_digest`). ``run.json`` holds
  the manifest (``toolkit``) and its digest (``toolkit_digest``); a run refuses to resume,
  and a re-run refuses to start, with another toolkit. Each session's copy is checked
  against the run's digest when it is made (``extras.json`` in the attempt folder).
- **Contamination.** A code-skill session is contaminated by any mention of worldparts, as
  a ``code+`` session is. So the toolkit is refused when one of its files, or a file name,
  would itself trip a contamination marker of the code arms (worldparts, the repository,
  the private folder, truth files, ...): a session reading its own ``reference/`` must
  never be flagged by the toolkit's text.
- **Size.** ``lines`` counts the lines of its files. Section 3 caps the toolkit at 800
  lines or the line count of worldparts' section-14 code (:func:`.quickref.section14_lines`,
  the modules of :data:`.quickref.SECTION14_MODULES`), whichever is larger; a larger
  toolkit is refused (:func:`size_cap`).
- **Text only.** Every file must be UTF-8 text: a binary file (a pickle, an archive holding
  code, an image) would escape both the size cap and the contamination check, so it is
  refused.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.grading import StreamSummary

from .bundles import ConfigError, _sha256
from .markers import contamination
from .quickref import section14_lines

TOOLKIT_ENV = "WPBENCH_OPS_TOOLKIT"
#: Where each code-skill session finds the toolkit (its working directory).
TOOLKIT_DIRNAME = "reference"
#: Left out of the manifest and the copy.
SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv"}
)
SKIP_SUFFIXES = (".pyc", ".pyo")
#: Section 3's floor of the toolkit's size cap (lines).
MIN_SIZE_CAP = 800


def size_cap(src: Path | None = None) -> int:
    """The toolkit's size cap of section 3: 800 lines or the line count of worldparts'
    section-14 code (in ``src``, default the repository's ``src/worldparts``), whichever is
    larger."""
    return max(MIN_SIZE_CAP, section14_lines(src))


@dataclass(frozen=True)
class Toolkit:
    """A toolkit folder and its manifest."""

    dir: Path
    files: dict[str, str]  # relative posix path -> SHA-256
    digest: str
    lines: int

    def manifest(self) -> dict[str, Any]:
        return {
            "dir": str(self.dir),
            "digest": self.digest,
            "n_files": len(self.files),
            "lines": self.lines,
            "files": dict(self.files),
        }


def toolkit_root(override: Path | str | None = None) -> Path:
    """The toolkit folder: ``override``, else ``$WPBENCH_OPS_TOOLKIT`` (no default)."""
    if override:
        root = Path(override)
    else:
        env = os.environ.get(TOOLKIT_ENV)
        if not env:
            raise ConfigError(
                f"set {TOOLKIT_ENV} (or pass --toolkit) to the code-skill toolkit folder"
            )
        root = Path(env)
    if not root.is_dir():
        raise ConfigError(f"the code-skill toolkit folder {root} does not exist")
    return root


def toolkit_files(root: Path) -> list[Path]:
    """The toolkit's files, sorted by relative path."""
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if p.is_file() and not p.name.endswith(SKIP_SUFFIXES):
            out.append(p)
    return sorted(out, key=lambda q: q.relative_to(root).as_posix())


def _digest(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for rel, sha in sorted(files.items()):
        h.update(f"{rel}\0{sha}\n".encode())
    return h.hexdigest()


def _text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def toolkit_problems(root: Path, cap: int | None = None) -> list[str]:
    """Why the toolkit cannot be given to code-skill sessions, or ``[]``: no files, a file
    that is not UTF-8 text, more lines than the size cap (``cap``, default
    :func:`size_cap`), or a file (or file name) that would trip a contamination marker of
    the code arms."""
    files = toolkit_files(root)
    if not files:
        return [f"the toolkit folder {root} has no files"]
    problems: list[str] = []
    texts: list[tuple[str, str]] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        text = _text(p)
        if text is None:
            problems.append(f"{rel}: not UTF-8 text (the toolkit holds text files only)")
        else:
            texts.append((rel, text))
    lines = sum(len(t.splitlines()) for _, t in texts)
    limit = size_cap() if cap is None else cap
    if lines > limit:
        problems.append(
            f"{lines} lines; section 3 caps the toolkit at {limit} (800 or the line count of "
            "worldparts' section-14 code, whichever is larger)"
        )
    names = "\n".join(f"{TOOLKIT_DIRNAME}/{p.relative_to(root).as_posix()}" for p in files)
    for rel, text in [("(file names)", names), *texts]:
        s = StreamSummary(tool_result_texts=[text])
        for reason in contamination(s, "code-skill"):
            problems.append(f"{rel}: {reason.replace('tool result mentions', 'mentions')}")
    return problems


def load_toolkit(override: Path | str | None = None, cap: int | None = None) -> Toolkit:
    """The toolkit (raises :class:`ConfigError` when it is not given, missing or refused;
    ``cap`` overrides the size cap, default :func:`size_cap`)."""
    root = toolkit_root(override)
    problems = toolkit_problems(root, cap)
    if problems:
        raise ConfigError("the code-skill toolkit is refused: " + "; ".join(problems[:20]))
    files = {p.relative_to(root).as_posix(): _sha256(p) for p in toolkit_files(root)}
    lines = 0
    for p in toolkit_files(root):
        text = _text(p)
        if text is not None:
            lines += len(text.splitlines())
    return Toolkit(root, files, _digest(files), lines)


def copy_toolkit(tk: Toolkit, dest: Path) -> list[str]:
    """Copy the toolkit into ``dest`` (absent or empty) after checking that its folder
    still holds exactly the files of ``tk``; returns the copied relative paths."""
    now = {p.relative_to(tk.dir).as_posix(): _sha256(p) for p in toolkit_files(tk.dir)}
    if _digest(now) != tk.digest:
        raise RuntimeError(
            f"the toolkit in {tk.dir} changed since the run started (digest "
            f"{_digest(now)[:16]}, the run's {tk.digest[:16]})"
        )
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise RuntimeError(f"{dest} is not empty")
    for rel in sorted(now):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tk.dir / rel, target)
    return sorted(now)
