"""Scoring of scripted answers: ``python -m benchmarks.operations.harness score DIR``.

The readiness check of PREREGISTRATION.md section 7 ("a scripted worldparts pipeline with
no LLM must pass at least 90 % of development realisations") and the build sessions of
FREEZES.md ("Build phase after Stage 0") score answers written by a program, not by an
agent session. ``DIR`` holds one answer file per task realisation (INTERFACE.md,
"Scripted answers")::

    DIR/<task_id>/r<k>.json    one JSON object in the answer format of the prompt

Each file is graded exactly as a session's final reply is (:func:`grade_file`): its text
is the body of a fenced ``json`` block, which :func:`.grading.grade_answer` parses with
:func:`.grading.parse_answer` (the same lenient reading of one JSON object) and grades
against the task's truth. A missing file, one that is not a JSON object, and one the
grader cannot grade (an integer too large for a float, an array nested thousands deep)
fail; the rest of the run is still scored.

**What is scored.** Every realisation ``r<k>`` of the bundle of every task whose truth
file is listed, with its SHA-256, in the committed ``truth-<set>.sha256`` (r1 to r3 for a
validated task). Other tasks are not validated yet and are not scored; the output says
how many realisations were scored. The bundles of the scored tasks must be the committed
ones (``bundles-<set>.sha256``), so a changed ``task.json`` can never be checked against
the truth.

**Two modes.**

- *Full* (the owner only): prints each task's results with the failing keys, the
  realisation pass rate and whether section 7's readiness threshold is met, and writes
  every grade to an owner-chosen file outside ``DIR`` (``--out``). Like a session record,
  a grade holds no truth value, but a numeric key's truth can be worked out from it, so it
  is never shown to a builder session.
- *Pass/fail* (``--pass-fail``, the only output a firewalled builder session may receive,
  PREREGISTRATION.md section 7): prints PASS or FAIL per task realisation and the number
  of passes. Everything it needs comes from the owner, who sets it when launching the
  session: the truth folder (``$WPBENCH_OPS_PASSFAIL_TRUTH``, a variable the owner's own
  commands never read), the session's name (``$WPBENCH_OPS_SESSION``) and optionally the
  log (``$WPBENCH_OPS_PASSFAIL_LOG``, by default ``pass-fail.log`` in the benchmark's
  folder under ``%LOCALAPPDATA%``, never in a builder's ``DIR``). An evaluation is logged,
  under a file lock, before any truth file is opened, so a failed or crashed evaluation
  counts too; a fourth one for the session is refused (:func:`begin_evaluation`).
  Builders run it through ``tools/ops_passfail.py``.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.runner import default_code_env_dir

from .bundles import ConfigError, OpsTask, load_truth, realisation_digest, truth_listed
from .grading import SessionGrade, grade_answer

SCORE_FILE = "score.json"
PASS_FAIL_LOG = "pass-fail.log"
#: The truth folder of the pass/fail mode, set by the owner when launching a builder
#: session. :func:`.bundles.truth_root` never reads it, so the owner's commands (full
#: scores, grade, headroom) do not work in a builder session without naming the truth.
PASSFAIL_TRUTH_ENV = "WPBENCH_OPS_PASSFAIL_TRUTH"
#: The pass/fail log (optional; see :func:`log_path`).
PASSFAIL_LOG_ENV = "WPBENCH_OPS_PASSFAIL_LOG"
#: The builder session's name, set by the owner at launch; evaluations are counted by it.
SESSION_ENV = "WPBENCH_OPS_SESSION"
#: Logged pass/fail evaluations allowed per builder session (PREREGISTRATION.md section 7).
MAX_EVALUATIONS = 3
#: The readiness threshold of section 7 (share of development realisations).
READINESS_RATE = 0.90
ANSWER_FILE = re.compile(r"^r([1-9][0-9]*)\.json$")
SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def answer_path(answers: Path, task_id: str, k: int) -> Path:
    return answers / task_id / f"r{k}.json"


def _ungradable(task: OpsTask, truth: dict[str, Any], why: str) -> SessionGrade:
    g = grade_answer(task, truth, None)
    g.parse_error = why
    return g


def grade_file(task: OpsTask, truth: dict[str, Any], path: Path) -> SessionGrade:
    """Grade one scripted answer file as a final reply (see the module docstring). A file
    the grader cannot grade fails with a ``parse_error`` instead of stopping the run."""
    if not path.is_file():
        return _ungradable(task, truth, f"no answer file {path.parent.name}/{path.name}")
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    try:
        return grade_answer(task, truth, "```json\n" + text + "\n```\n")
    except Exception as exc:  # e.g. OverflowError, RecursionError
        return _ungradable(task, truth, f"the answer could not be graded ({type(exc).__name__})")


@dataclass
class RealisationScore:
    task: str
    realisation: int
    passed: bool
    missing: bool
    grade: dict[str, Any]
    digest: str


@dataclass
class ScoreResult:
    """Scores of every realisation of the validated tasks."""

    rows: list[RealisationScore] = field(default_factory=list)
    scored_tasks: list[str] = field(default_factory=list)
    not_validated: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)  # answer files of no scored realisation

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.rows)

    @property
    def rate(self) -> float | None:
        return self.passed / self.n if self.rows else None


def score_answers(
    answers: Path, tasks: list[OpsTask], truth_dir: Path, listed: dict[str, str]
) -> ScoreResult:
    """Grade ``answers`` (DIR) on every realisation of the tasks whose truth is ``listed``
    (the committed truth manifest). Raises :class:`.bundles.BundleError` for a truth file
    that does not load."""
    res = ScoreResult()
    wanted: set[str] = set()
    for task in sorted(tasks, key=lambda t: t.task_id):
        if not truth_listed(task, truth_dir, listed):
            res.not_validated.append(task.task_id)
            continue
        truth = load_truth(task, truth_dir)
        res.scored_tasks.append(task.task_id)
        for k in task.realisations:
            path = answer_path(answers, task.task_id, k)
            wanted.add(f"{task.task_id}/r{k}.json")
            g = grade_file(task, truth, path)
            gd = g.to_dict()
            gd.pop("task")
            res.rows.append(
                RealisationScore(
                    task.task_id, k, g.passed, not path.is_file(), gd, realisation_digest(task, k)
                )
            )
    for p in sorted(answers.glob("*/*.json")):
        rel = f"{p.parent.name}/{p.name}"
        if ANSWER_FILE.match(p.name) and rel not in wanted:
            res.ignored.append(rel)
    return res


def readiness_needed(n: int) -> int:
    """Passes needed out of ``n`` realisations for section 7's readiness threshold (exact:
    the smallest integer at or above 90 % of ``n``)."""
    return math.ceil(Fraction(str(READINESS_RATE)) * n)


def readiness_met(res: ScoreResult) -> bool | None:
    """Whether the realisation pass rate meets section 7's threshold (None: nothing scored)."""
    return None if not res.rows else res.passed >= readiness_needed(res.n)


def _rate(res: ScoreResult) -> str:
    if res.rate is None:
        return "no realisation scored"
    # One decimal, never rounded up across the threshold: 43/48 is 89.5 %, not 90 %.
    return f"{(1000 * res.passed // res.n) / 10:.1f} % ({res.passed}/{res.n})"


def full_lines(res: ScoreResult) -> list[str]:
    """The owner's printout: per task, each realisation with its failing keys."""
    lines: list[str] = []
    by_task: dict[str, list[RealisationScore]] = {}
    for r in res.rows:
        by_task.setdefault(r.task, []).append(r)
    for tid, rows in by_task.items():
        parts = []
        for r in rows:
            if r.passed:
                parts.append(f"r{r.realisation} PASS")
                continue
            if r.missing:
                why = "no answer file"
            elif r.grade.get("parse_error"):
                why = f"no answer ({r.grade['parse_error']})"
            else:
                failed = []
                for key, kr in r.grade["keys"].items():
                    if not kr["passed"]:
                        failed.append(f"{key} ({kr['category']})" if kr.get("category") else key)
                why = "failed: " + ", ".join(failed)
            parts.append(f"r{r.realisation} FAIL [{why}]")
        n_pass = sum(r.passed for r in rows)
        lines.append(f"{tid}: {n_pass}/{len(rows)} realisation(s) passed; " + "; ".join(parts))
    lines.append(
        f"{res.n} realisation(s) of {len(res.scored_tasks)} validated task(s) scored"
        + (
            f"; not validated yet (not scored): {', '.join(res.not_validated)}"
            if res.not_validated
            else ""
        )
    )
    if res.ignored:
        lines.append(f"answer files of no scored realisation (ignored): {', '.join(res.ignored)}")
    lines.append(
        f"realisation pass rate: {_rate(res)}; the readiness check of section 7 needs at "
        f"least {100 * READINESS_RATE:.0f} %"
    )
    met = readiness_met(res)
    if met is not None:
        lines.append(
            f"readiness (section 7, scripted pipeline): {'MET' if met else 'NOT MET'} "
            f"({res.passed} passed; {readiness_needed(res.n)} of {res.n} needed)"
        )
    return lines


def score_document(res: ScoreResult, answers: Path, set_name: str) -> dict[str, Any]:
    """``score.json``: every grade (owner only)."""
    return {
        "set": set_name,
        "answers": str(answers),
        "scored": datetime.now().isoformat(timespec="seconds"),
        "realisations_scored": res.n,
        "passed": res.passed,
        "rate": res.rate,
        "readiness_threshold": READINESS_RATE,
        "readiness_needed": readiness_needed(res.n),
        "readiness_met": readiness_met(res),
        "scored_tasks": res.scored_tasks,
        "not_validated": res.not_validated,
        "ignored": res.ignored,
        "realisations": [
            {
                "task": r.task,
                "realisation": r.realisation,
                "passed": r.passed,
                "missing": r.missing,
                "bundle_digest": r.digest,
                **r.grade,
            }
            for r in res.rows
        ],
    }


def pass_fail_lines(res: ScoreResult) -> list[str]:
    """The builder's printout: PASS or FAIL per task realisation, then the count."""
    lines = [f"{r.task} r{r.realisation} {'PASS' if r.passed else 'FAIL'}" for r in res.rows]
    lines.append(
        f"{res.passed} of {res.n} realisation(s) passed "
        f"({res.n} realisation(s) of {len(res.scored_tasks)} task(s) scored)"
    )
    return lines


# ----------------------------------------------------------------------------------------
# the pass/fail log (owner side)
# ----------------------------------------------------------------------------------------
def passfail_truth_root() -> Path:
    """The truth folder of the pass/fail mode: ``$WPBENCH_OPS_PASSFAIL_TRUTH`` only."""
    env = os.environ.get(PASSFAIL_TRUTH_ENV)
    if not env:
        raise ConfigError(f"{PASSFAIL_TRUTH_ENV} is not set")
    root = Path(env)
    if not root.is_dir():
        raise ConfigError(f"{PASSFAIL_TRUTH_ENV} is not a folder")
    return root


def log_path() -> Path:
    """The pass/fail log: ``$WPBENCH_OPS_PASSFAIL_LOG``, else ``pass-fail.log`` in the
    benchmark's folder under ``%LOCALAPPDATA%`` (``worldparts-bench/ops-build/``), which
    the owner controls and no builder ``DIR`` contains."""
    env = os.environ.get(PASSFAIL_LOG_ENV)
    if env:
        return Path(env)
    return default_code_env_dir().parent / "ops-build" / PASS_FAIL_LOG


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _started(session: str) -> re.Pattern[str]:
    # Found anywhere in the text (not only at a line start), so that a line without its
    # line break cannot hide the next evaluation.
    return re.compile(rf"(?<!\S)session={re.escape(session)} evaluation=\d+ started(?!\S)")


def evaluations_logged(log: Path, session: str) -> int:
    """How many evaluations the log records for ``session`` (each counted when it starts)."""
    if not log.is_file():
        return 0
    return len(_started(session).findall(log.read_text(encoding="utf-8", errors="replace")))


def _append(log: Path, lines: list[str]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if log.is_file() and log.stat().st_size:
        with open(log, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"  # never continue a line that lost its line break
    with open(log, "a", encoding="utf-8") as f:
        f.write(prefix + "".join(line + "\n" for line in lines))


@contextlib.contextmanager
def log_lock(log: Path, timeout: float = 30.0, stale: float = 120.0) -> Iterator[None]:
    """An exclusive lock on the log (a ``.lock`` file created with O_EXCL), so that two
    evaluations started at once are counted one after the other. A lock older than
    ``stale`` seconds (a process that died holding it) is broken."""
    lock = log.with_name(log.name + ".lock")
    log.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink()
                    continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"the pass/fail log is locked ({lock})") from None
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def begin_evaluation(log: Path, session: str, timeout: float = 30.0) -> int | None:
    """Count and log a new evaluation of ``session`` under the lock, before anything is
    graded; its number, or None (logged as a refusal) when the session has had its
    :data:`MAX_EVALUATIONS`."""
    with log_lock(log, timeout):
        done = evaluations_logged(log, session)
        if done >= MAX_EVALUATIONS:
            _append(log, [f"{_stamp()} session={session} refused: {MAX_EVALUATIONS} "
                          "evaluations already logged"])  # fmt: skip
            return None
        _append(log, [f"{_stamp()} session={session} evaluation={done + 1} started"])
        return done + 1


def log_evaluation(log: Path, session: str, number: int, res: ScoreResult) -> None:
    """The verdicts of a finished evaluation (it was counted when it started)."""
    stamp = _stamp()
    lines = [
        f"{stamp} session={session} {r.task}/r{r.realisation} {'PASS' if r.passed else 'FAIL'}"
        for r in res.rows
    ]
    lines.append(
        f"{stamp} session={session} evaluation={number} realisations={res.n} passed={res.passed}"
    )
    with log_lock(log):
        _append(log, lines)


def log_failure(log: Path, session: str, number: int) -> None:
    """An evaluation that could not be completed (it still counts)."""
    with log_lock(log):
        _append(log, [f"{_stamp()} session={session} evaluation={number} failed"])


def log_refusal(log: Path, session: str, why: str) -> None:
    with log_lock(log):
        _append(log, [f"{_stamp()} session={session} refused: {why}"])
