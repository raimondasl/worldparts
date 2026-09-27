"""Pass/fail evaluation of scripted answers for a firewalled builder session.

Usage (from the repository root)::

    uv run python tools/ops_passfail.py DIR --session NAME

``DIR`` holds the builder's scripted answers, one JSON object per task realisation:
``DIR/<task_id>/r<k>.json`` in the answer format of the prompt (INTERFACE.md, "Scripted
answers"). The wrapper runs ``python -m benchmarks.operations.harness score DIR --set dev
--pass-fail --session NAME``, which prints PASS or FAIL for every realisation of every
validated development task and the number of passes, and nothing else.

The rules of the build phase (PREREGISTRATION.md section 7; FREEZES.md, "Build phase after
Stage 0"):

- **At most 3 evaluations per session.** Every call is logged in ``DIR/pass-fail.log``
  with a time stamp and the session name; a fourth call for the same name is refused, and
  the firewall audit counts the calls in the session's transcript as well. Use one
  ``NAME`` per session.
- **Never read the truth.** The owner sets ``WPBENCH_OPS_TRUTH`` when launching the
  session and the wrapper reads it itself. A builder never reads, lists, prints or
  searches the truth folder or that variable, the private benchmark folder
  (``worldparts-opsbench``), ``reports/``, ``research_notes/``, the Claude memory folder,
  any ``record.json``, ``summary.*``, ``headroom*.json`` or ``pass-fail.log``, or the
  draft history of PREREGISTRATION.md. A search of a Stage 0 run directory is restricted
  to its transcripts (``grep -r ... --include='*.jsonl'``). The transcripts are audited
  for such reads (``harness firewall-audit``).
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
from benchmarks.operations.harness.bundles import TRUTH_ENV  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="tools/ops_passfail.py",
        description="Pass/fail of scripted answers DIR/<task>/r<k>.json on the development "
        "set (at most 3 logged evaluations per session).",
    )
    p.add_argument("dir", help="The answer directory, DIR/<task_id>/r<k>.json.")
    p.add_argument("--session", required=True, help="This builder session's name.")
    args = p.parse_args(argv)
    if not os.environ.get(TRUTH_ENV):
        print(
            f"error: {TRUTH_ENV} is not set: the owner sets it when launching a builder "
            "session; ask the owner (never look for the truth yourself)",
            file=sys.stderr,
        )
        return 2
    return harness.main(
        ["score", args.dir, "--set", "dev", "--pass-fail", "--session", args.session]
    )


if __name__ == "__main__":
    sys.exit(main())
