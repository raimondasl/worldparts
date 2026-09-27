"""Pass/fail evaluation of scripted answers for a firewalled builder session.

Usage (from the repository root)::

    uv run python tools/ops_passfail.py DIR [--session NAME]

``DIR`` holds the builder's scripted answers, one JSON object per task realisation:
``DIR/<task_id>/r<k>.json`` in the answer format of the prompt (INTERFACE.md, "Scripted
answers"). The wrapper runs ``python -m benchmarks.operations.harness score DIR --set dev
--pass-fail``, which prints PASS or FAIL for every realisation of every validated
development task and the number of passes, and nothing else. ``DIR`` gets no file.

The rules of the build phase (PREREGISTRATION.md section 7; FREEZES.md, "Build phase after
Stage 0"):

- **At most 3 evaluations per session.** The owner sets the session's name
  (``WPBENCH_OPS_SESSION``) when launching the session; ``--session``, if given, must be
  that name. Every evaluation is counted and logged in the owner's log, under a lock and
  before anything is graded, so one that fails counts too; a fourth is refused. The
  firewall audit counts the calls in the session's transcript as well.
- **Never read the truth.** The owner also sets the truth folder in a variable only this
  wrapper uses. A builder never reads, lists, prints, sets or searches the ``WPBENCH_OPS_*``
  variables (other than the bundle folder), the truth folder, the private benchmark folder
  (``worldparts-opsbench``), ``reports/``, ``research_notes/``, the Claude memory folder,
  the owner's benchmark folder under ``%LOCALAPPDATA%`` (``worldparts-bench``), any
  ``record.json``, ``summary.*``, ``headroom*.json``, ``score.json`` or ``pass-fail.log``,
  or the draft history of PREREGISTRATION.md, and never runs the owner's commands
  (``harness score`` without ``--pass-fail``, ``grade``, ``headroom``, ``report``) or the
  harness's truth functions. A builder never changes the harness, its committed lists or
  this wrapper.
- **Searches.** ``rg``, the Grep tool and ``git grep`` skip the git-ignored run folders; a
  ``grep -r`` (or a recursive copy or archive) of the repository root, ``..`` or any folder
  above a run folder reads grader files: restrict it (``--include='*.py'``) or search a
  subfolder. A search of a Stage 0 run directory is restricted to its transcripts
  (``grep -r ... --include='*.jsonl'``). The transcripts are audited for such reads
  (``harness firewall-audit``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # so that ``benchmarks`` imports when run as a script
    sys.path.insert(0, str(ROOT))

from benchmarks.operations.harness import __main__ as harness  # noqa: E402
from benchmarks.operations.harness.score import PASSFAIL_TRUTH_ENV, SESSION_ENV  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="tools/ops_passfail.py",
        description="Pass/fail of scripted answers DIR/<task>/r<k>.json on the development "
        "set (at most 3 logged evaluations per session).",
    )
    p.add_argument("dir", help="The answer directory, DIR/<task_id>/r<k>.json.")
    p.add_argument(
        "--session", default=None, help="This session's name (optional; the owner sets it)."
    )
    args = p.parse_args(argv)
    missing = [v for v in (PASSFAIL_TRUTH_ENV, SESSION_ENV) if not os.environ.get(v)]
    if missing:
        print(
            f"error: {' and '.join(missing)} not set: the owner sets them when launching a "
            "builder session; ask the owner (never look for the truth yourself)",
            file=sys.stderr,
        )
        return 2
    argv2 = ["score", args.dir, "--set", "dev", "--pass-fail"]
    if args.session:
        argv2 += ["--session", args.session]
    return harness.main(argv2)


if __name__ == "__main__":
    sys.exit(main())
