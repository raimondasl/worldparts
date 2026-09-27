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
against the task's truth. A missing file, or one that is not a JSON object, fails.

**What is scored.** Every realisation ``r<k>`` of the bundle of every task whose truth
file is listed, with its SHA-256, in the committed ``truth-<set>.sha256`` (r1 to r3 for a
validated task). Other tasks are not validated yet and are not scored; the output says
how many realisations were scored.

**Two modes.**

- *Full* (the owner only): prints each task's results with the failing keys and the
  realisation pass rate, and writes ``DIR/score.json`` with every grade. Like a session
  record, a grade holds no truth value, but a numeric key's truth can be worked out from
  it, so it is never shown to a builder session.
- *Pass/fail* (``--pass-fail --session NAME``, the only output a firewalled builder
  session may receive, PREREGISTRATION.md section 7): prints PASS or FAIL per task
  realisation and the number of passes, writes nothing but a line per evaluation and per
  realisation in ``DIR/pass-fail.log`` (with a time stamp and the session name), and
  refuses a fourth evaluation of the same session name: at most 3 logged evaluations per
  session. Builders run it through ``tools/ops_passfail.py``, which reads the truth folder
  from ``$WPBENCH_OPS_TRUTH`` itself; they never read the truth folder or the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .bundles import OpsTask, load_truth, realisation_digest, truth_listed
from .grading import SessionGrade, grade_answer

SCORE_FILE = "score.json"
PASS_FAIL_LOG = "pass-fail.log"
#: Logged pass/fail evaluations allowed per builder session (PREREGISTRATION.md section 7).
MAX_EVALUATIONS = 3
#: The readiness threshold of section 7 (share of development realisations).
READINESS_RATE = 0.90
ANSWER_FILE = re.compile(r"^r([1-9][0-9]*)\.json$")
SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def answer_path(answers: Path, task_id: str, k: int) -> Path:
    return answers / task_id / f"r{k}.json"


def grade_file(task: OpsTask, truth: dict[str, Any], path: Path) -> SessionGrade:
    """Grade one scripted answer file as a final reply (see the module docstring)."""
    if not path.is_file():
        g = grade_answer(task, truth, None)
        g.parse_error = f"no answer file {path.parent.name}/{path.name}"
        return g
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return grade_answer(task, truth, "```json\n" + text + "\n```\n")


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


def _rate(res: ScoreResult) -> str:
    if res.rate is None:
        return "no realisation scored"
    return f"{100 * res.rate:.0f} % ({res.passed}/{res.n})"


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


_EVALUATION = re.compile(r"^\S+ session=(\S+) evaluation=\d+ ")


def evaluations_logged(log: Path, session: str) -> int:
    """How many evaluations ``DIR/pass-fail.log`` records for ``session``."""
    if not log.is_file():
        return 0
    n = 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _EVALUATION.match(line)
        if m and m.group(1) == session:
            n += 1
    return n


def log_evaluation(log: Path, session: str, number: int, res: ScoreResult) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    lines = [
        f"{stamp} session={session} evaluation={number} realisations={res.n} passed={res.passed}"
    ]
    lines += [
        f"{stamp} session={session} {r.task}/r{r.realisation} {'PASS' if r.passed else 'FAIL'}"
        for r in res.rows
    ]
    with open(log, "a", encoding="utf-8") as f:
        f.writelines(line + "\n" for line in lines)


def log_refusal(log: Path, session: str) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(log, "a", encoding="utf-8") as f:
        f.write(
            f"{stamp} session={session} refused: {MAX_EVALUATIONS} evaluations already logged\n"
        )
