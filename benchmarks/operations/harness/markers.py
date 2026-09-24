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
  the ``WPBENCH_OPS_*`` variables, and the configured bundle and truth folders by path.

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


def _folder_patterns(label: str, root: Path | str | None) -> list[tuple[str, re.Pattern[str]]]:
    if not root:
        return []
    return [(f"the {label} {form}", _path_pattern(form)) for form in _path_forms(root)]


def contamination(
    s: StreamSummary,
    arm: Arm | str,
    bundles_root: Path | str | None = None,
    truth_root: Path | str | None = None,
    extra_texts: Iterable[str] = (),
) -> list[str]:
    """Why the session is contaminated, or ``[]`` (see the module docstring)."""
    a = get_arm(arm) if isinstance(arm, str) else arm
    extra = list(extra_texts)
    reasons = v02_contamination(s, a.contamination_condition, None, extra_texts=extra)
    folders = _folder_patterns("bundle folder", bundles_root) + _folder_patterns(
        "truth folder", truth_root
    )
    inputs = list(s.tool_input_texts)
    results = [*s.tool_result_texts, *s.tool_error_texts, *extra]
    for where, texts in (("tool input", inputs), ("tool result", results)):
        for raw in texts:
            norm = _normalise(raw)
            found = [what for what, pat, case in OPS_MARKERS if pat.search(raw if case else norm)]
            found += [what for what, pat in folders if pat.search(norm)]
            for f in found:
                reason = f"{where} mentions {f}"
                if reason not in reasons:
                    reasons.append(reason)
    return reasons
