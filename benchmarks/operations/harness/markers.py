"""Contamination markers (PREREGISTRATION.md section 6.6, "Contamination").

A session is contaminated when a tool input or tool result (errors included) shows that
the agent looked at the benchmark itself. Two sets of markers apply:

- the v0.2 markers (:func:`benchmarks.composition.harness.grading.contamination`), with
  the arm's contamination condition: for the code arms, the repository (its path in the
  Windows, Git Bash, WSL and home-relative spellings, ``src/worldparts/``, ``uv.lock``,
  the test fixtures, its GitHub address in a tool input), the v0.2 task files and frozen
  answers, and any mention of the worldparts library;
- the operations markers (:data:`OPS_MARKERS`): the private benchmark folder
  (``worldparts-opsbench``), the generator (``opsim``), truth files (``truth.json``), the
  sealed appendix (``APPENDIX``, case-sensitive), this benchmark's directory
  (``benchmarks/operations``), the bundle folder (``opsbench-bundles``: the session gets a
  copy of one realisation; the folder holds the other realisations and ``task.json``),
  the ``WPBENCH_OPS_*`` variables, and the configured bundle and truth folders by path;
- another session's temporary directory (``wpbench-ops-<random>``, any but the session's
  own, which ``outcome.json`` records as ``tmp_dir``): named in a tool input, or shown in a
  tool result as a path into it (``wpbench-ops-<random>/...``). A bare listing of the
  temporary folder, which names such a directory without looking inside, is not a marker.

Detection is by markers, so a deliberately disguised read would pass; the transcripts are
kept for audit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from benchmarks.composition.harness.grading import (
    StreamSummary,
    _normalise,
    _path_forms,
    _path_pattern,
)
from benchmarks.composition.harness.grading import contamination as v02_contamination

from .arms import Arm, get_arm

#: (what, pattern, case_sensitive). Case-insensitive patterns are matched on lower-case
#: text with backslashes turned into slashes; case-sensitive ones on the raw text.
OPS_MARKERS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("the private folder 'worldparts-opsbench'", re.compile(r"worldparts-opsbench"), False),
    ("the generator 'opsim'", re.compile(r"(?<![a-z0-9])opsim(?![a-z])"), False),
    ("a truth file ('truth.json')", re.compile(r"truth\.json"), False),
    ("the sealed appendix ('APPENDIX')", re.compile(r"(?<![A-Za-z])APPENDIX(?![A-Za-z])"), True),
    ("the directory 'benchmarks/operations'", re.compile(r"benchmarks/operations"), False),
    ("the bundle folder 'opsbench-bundles'", re.compile(r"opsbench-bundles"), False),
    ("a 'WPBENCH_OPS_' variable", re.compile(r"wpbench_ops_"), False),
)
#: A session's temporary directory (runner.TMP_PREFIX and mkdtemp's random suffix).
SESSION_DIR = re.compile(r"wpbench-ops-[a-z0-9_]+")


def _folder_patterns(label: str, root: Path | str | None) -> list[tuple[str, re.Pattern[str]]]:
    if not root:
        return []
    return [(f"the {label} {form}", _path_pattern(form)) for form in _path_forms(root)]


def other_session_dirs(norm: str, own: str | None, inside_only: bool) -> list[str]:
    """Names of other sessions' temporary directories in ``norm`` (normalised text); with
    ``inside_only``, only those followed by ``/`` (a path into the directory)."""
    out = []
    for m in SESSION_DIR.finditer(norm):
        if m.group(0) == own or (inside_only and not norm.startswith("/", m.end())):
            continue
        if m.group(0) not in out:
            out.append(m.group(0))
    return out


def contamination(
    s: StreamSummary,
    arm: Arm | str,
    bundles_root: Path | str | None = None,
    truth_root: Path | str | None = None,
    extra_texts: Iterable[str] = (),
    own_tmp: Path | str | None = None,
) -> list[str]:
    """Why the session is contaminated, or ``[]`` (see the module docstring).

    ``own_tmp`` is the session's own temporary directory (``outcome.json``'s ``tmp_dir``).
    """
    a = get_arm(arm) if isinstance(arm, str) else arm
    extra = list(extra_texts)
    reasons = v02_contamination(s, a.contamination_condition, None, extra_texts=extra)
    folders = _folder_patterns("bundle folder", bundles_root) + _folder_patterns(
        "truth folder", truth_root
    )
    own = Path(str(own_tmp).replace("\\", "/")).name.lower() if own_tmp else None
    inputs = list(s.tool_input_texts)
    results = [*s.tool_result_texts, *s.tool_error_texts, *extra]
    for where, texts in (("tool input", inputs), ("tool result", results)):
        for raw in texts:
            norm = _normalise(raw)
            found = [what for what, pat, case in OPS_MARKERS if pat.search(raw if case else norm)]
            found += [what for what, pat in folders if pat.search(norm)]
            found += [
                f"another session's directory '{d}'"
                for d in other_session_dirs(norm, own, inside_only=where == "tool result")
            ]
            for f in found:
                reason = f"{where} mentions {f}"
                if reason not in reasons:
                    reasons.append(reason)
    return reasons
