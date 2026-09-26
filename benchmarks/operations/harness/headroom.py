"""The Stage 0 headroom rule (PREREGISTRATION.md section 8, "Rule").

For each frontier model m, F(m) counts the development tasks on which the model's
``code-hint`` session on realisation 1 fails while at least one reference estimator passes
realisation 1 (``realisations.r1.reference_pass`` of the task's truth: any estimator whose
value is ``true``; ``null`` means the estimator does not apply, or R2 was not needed because
R-a or R-b passes). The room is closed if and only if F(Sonnet 5) <= 3 and F(Opus 5.5) <= 3.
``code+`` does not enter the rule.

Tasks are validated while Stage 0 runs, so the rule is evaluated in its bound form. A task
is decided for m when it has a truth (it is validated) and exactly one current code-hint
session of m; F-(m) counts the failures over the decided tasks and u(m) the pre-registered
task slots not decided for m. The room is OPEN as soon as F-(m) >= 4 for either model,
CLOSED as soon as F-(m) + u(m) <= 3 for both, and PENDING otherwise; with every task
decided this is the rule itself. A session on a bundle that was replaced since it ran (its
recorded digest differs from the task's current realisation 1, or it recorded none) is
superseded and never counted.

The rule is not computed (and says why) when a task has more than one current code-hint
session for a model, a session is waiting for an infrastructure re-run, a record is older
than its latest attempt, more than 5 % of an arm's sessions are contaminated (section 6.6:
the gate waits until that arm is re-run), the development set has more tasks than its
pre-registered cells or a bundle over the size limit, or a validated task's truth has no r1
reference result (a missing one is never read as "no estimator passes"). A contaminated
code-hint session within the 5 % allowance is left out, as in v0.2; the output says what
F would be if it counted as a failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .arms import HEADROOM_ARM

#: The rule as PREREGISTRATION.md prints it (a test checks every line is in the file).
RULE_LINES = (
    "- F(m) is the number of development tasks on which `code-hint` fails while at least "
    "one reference estimator (R-a, R-b, or R2 where it applies) passes the same realisation.",
    "- **The room is closed if and only if F(Sonnet 5) ≤ 3 and F(Opus 5.5) ≤ 3.**",
    "- `code+` is recorded but does not enter the rule.",
    "- A task is decided for m when it is validated and has exactly one current, graded "
    "`code-hint` session of m on its realisation 1. While tasks are undecided, F⁻(m) counts "
    "the failures above over the decided tasks, and u(m) is the number of the 16 task slots "
    "that are not decided for m.",
    "- The room is open as soon as F⁻(m) ≥ 4 for either model, and closed as soon as "
    "F⁻(m) + u(m) ≤ 3 for both models; otherwise the verdict is pending. With every task "
    "decided this is the rule above, so a verdict reached early is the one the complete data "
    "give.",
    "- A session waiting for a re-run blocks the verdict. The contamination check of 6.6 covers "
    "every graded Stage 0 session, superseded ones included. After an early verdict, the "
    "remaining sessions still run and are graded as their tasks are validated. The verdict "
    "computed at the end, with `headroom --final`, is binding, and any change from the early "
    "verdict is reported.",
    "- If Stage 0 ends with the verdict pending, because a slot cannot be filled or the owner "
    "does not re-approve the cost, the report says so and names the undecided slots. The room "
    "then counts as open, because it was not shown closed.",
)
THRESHOLD = 3
FRONTIER = ("Sonnet 5", "Opus 5.5")
HEADROOM_SET = "dev"
HEADROOM_REALISATION = 1
#: Contamination share above which the gate is not computed (section 6.6).
CONTAMINATION_LIMIT = 0.05


def reference_result(truth: dict[str, Any], realisation: int = HEADROOM_REALISATION) -> bool | None:
    """Whether at least one reference estimator passes the realisation; None when the
    truth has no reference result for it (no such realisation, or an empty or malformed
    ``reference_pass``): the rule is then not computed, never read as "no pass"."""
    reals = truth.get("realisations")
    r = reals.get(f"r{realisation}") if isinstance(reals, dict) else None
    ref = r.get("reference_pass") if isinstance(r, dict) else None
    if not isinstance(ref, dict) or not ref:
        return None
    if not all(v is None or isinstance(v, bool) for v in ref.values()):
        return None
    return any(v is True for v in ref.values())


def reference_passes(truth: dict[str, Any], realisation: int = HEADROOM_REALISATION) -> bool:
    """Whether at least one reference estimator passes the realisation (False when the
    truth has no reference result for it; :func:`headroom` refuses such a truth)."""
    return bool(reference_result(truth, realisation))


def contamination_shares(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per arm (pooled over models): sessions, contaminated sessions and their share."""
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        a = out.setdefault(r["arm"], {"sessions": 0, "contaminated": 0})
        a["sessions"] += 1
        a["contaminated"] += bool(r.get("contamination"))
    for a in out.values():
        a["share"] = a["contaminated"] / a["sessions"] if a["sessions"] else 0.0
    return out


@dataclass
class HeadroomResult:
    computed: bool
    not_computed: list[str] = field(default_factory=list)
    F: dict[str, int] = field(default_factory=dict)
    failing: dict[str, list[str]] = field(default_factory=dict)
    contaminated_excluded: dict[str, list[str]] = field(default_factory=dict)
    per_task: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_ids: dict[str, list[str]] = field(default_factory=dict)
    #: Task slots not decided for each model (u(m)); all zero once every task is decided.
    u: dict[str, int] = field(default_factory=dict)
    undecided: dict[str, list[str]] = field(default_factory=dict)
    superseded: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str | None:
        """'open', 'closed' or 'pending' (the bound form of the rule); None if not computed."""
        if not self.computed:
            return None
        if any(self.F[m] > THRESHOLD for m in FRONTIER):
            return "open"
        if all(self.F[m] + self.u.get(m, 0) <= THRESHOLD for m in FRONTIER):
            return "closed"
        return "pending"

    @property
    def closed(self) -> bool | None:
        """True or False once the verdict is decided; None while pending or not computed."""
        v = self.verdict
        return None if v in (None, "pending") else v == "closed"


def headroom(
    records: list[dict[str, Any]],
    task_ids: list[str],
    truths: dict[str, dict[str, Any]],
    models: dict[str, str],
    set_problems: list[str] | None = None,
    *,
    n_slots: int | None = None,
    current_digests: dict[str, str] | None = None,
) -> HeadroomResult:
    """Evaluate the rule in its bound form (module docstring).

    Args:
        records: Session records (``record.json``) of the Stage 0 run(s): every arm and
            model, used for the contamination shares; the rule reads the code-hint ones.
        task_ids: The development tasks whose bundles exist.
        truths: Truth documents of the validated tasks, by task id (only
            ``realisations.r1`` is read); a task without one is undecided.
        models: The requested model string of each frontier model, e.g.
            ``{"Sonnet 5": "claude-sonnet-5", "Opus 5.5": "claude-opus-5-5"}``.
        set_problems: Differences of the development set from its pre-registered cells
            that block the rule (a cell over its count, a bundle over the size limit);
            missing tasks are not problems, they are undecided slots.
        n_slots: The pre-registered number of development task slots (default: the number
            of ``task_ids``).
        current_digests: Each task's current realisation-1 digest
            (``bundles.realisation_digest``); a record whose ``bundle_digest`` differs, or
            is missing, or whose task has no entry (its realisation 1 is not committed), is
            superseded. None skips the check (records marked ``superseded`` are still left
            out).
    """
    res = HeadroomResult(computed=False)
    slots = len(task_ids) if n_slots is None else n_slots
    if len(task_ids) > slots:
        res.not_computed.append(f"{len(task_ids)} development tasks for {slots} slots")
    if set(models) != set(FRONTIER):
        res.not_computed.append(f"models must name exactly {', '.join(FRONTIER)}")
        return res
    res.not_computed.extend(set_problems or [])

    def superseded(r: dict[str, Any]) -> bool:
        if r.get("superseded"):
            return True
        if current_digests is None:
            return False
        # a task whose realisation 1 is not committed has no current bundle yet
        return r.get("bundle_digest") != current_digests.get(r.get("task"), "")

    stage0 = [
        r
        for r in records
        if r.get("set") == HEADROOM_SET and r.get("realisation") == HEADROOM_REALISATION
    ]
    rel = [r for r in stage0 if not superseded(r)]
    res.superseded = sorted(r["sid"] for r in stage0 if superseded(r))
    if res.superseded:
        res.warnings.append(
            "session(s) on bundles replaced since they ran, never scored: "
            + ", ".join(res.superseded)
        )
    # every Stage 0 session run so far, superseded ones included (section 8)
    for arm, share in sorted(contamination_shares(stage0).items()):
        if share["share"] > CONTAMINATION_LIMIT:
            res.not_computed.append(
                f"{share['contaminated']} of {share['sessions']} {arm} sessions are "
                f"contaminated (over 5 %): fix the cause and re-run that arm"
            )
    pending = sorted({r["sid"] for r in rel if r.get("rerun_allowed")})
    if pending:
        res.not_computed.append(
            f"{len(pending)} session(s) wait for an infrastructure re-run: {', '.join(pending)}"
        )
    stale = sorted({r["sid"] for r in rel if r.get("stale")})
    if stale:
        res.not_computed.append(f"record(s) older than their latest attempt: {', '.join(stale)}")
    for t in task_ids:
        if t in truths and reference_result(truths[t]) is None:
            res.not_computed.append(f"no r{HEADROOM_REALISATION} reference result for {t}")
    for label in FRONTIER:
        model = models[label]
        mine = [r for r in rel if r["model"] == model and r["arm"] == HEADROOM_ARM]
        res.model_ids[label] = sorted({str(r.get("model_id")) for r in mine})
        by_task: dict[str, list[dict[str, Any]]] = {}
        for r in mine:
            by_task.setdefault(r["task"], []).append(r)
        extra = sorted(set(by_task) - set(task_ids))
        dupes = sorted(t for t, rs in by_task.items() if len(rs) > 1)
        if extra:
            res.warnings.append(f"{label}: sessions for tasks outside the set ignored: {extra}")
        if dupes:
            res.not_computed.append(f"{label}: more than one code-hint session for {dupes}")
        failing: list[str] = []
        excluded: list[str] = []
        undecided = [t for t in task_ids if t not in truths or len(by_task.get(t) or []) != 1]
        res.undecided[label] = undecided
        res.u[label] = slots - (len(task_ids) - len(undecided))
        for t in task_ids:
            rs = by_task.get(t) or []
            if len(rs) != 1 or t not in truths:
                continue
            r = rs[0]
            ref = reference_passes(truths[t])
            row = {"model": label, "task": t, "passed": bool(r["passed"]), "reference_r1": ref}
            if r.get("contamination"):
                row["contaminated"] = True
                excluded.append(t)
            elif not r["passed"] and ref:
                failing.append(t)
            res.per_task.append(row)
        res.F[label] = len(failing)
        res.failing[label] = failing
        res.contaminated_excluded[label] = excluded
        if excluded:
            worst = len(failing) + sum(1 for t in excluded if reference_passes(truths[t]))
            res.warnings.append(
                f"{label}: contaminated code-hint session(s) left out on {excluded}; "
                f"F({label}) would be {worst} if they counted as failures"
            )
    res.computed = not res.not_computed
    return res


def headroom_lines(res: HeadroomResult) -> list[str]:
    """The printout: the rule verbatim, then the numbers and the verdict."""
    lines = ["Stage 0 headroom rule (PREREGISTRATION.md section 8), verbatim:", *RULE_LINES, ""]
    for label in FRONTIER:
        ids = ", ".join(res.model_ids.get(label) or []) or "n/a"
        if label in res.F:
            fails = ", ".join(res.failing[label]) or "none"
            lines.append(f"F({label}) = {res.F[label]}  (model id(s): {ids}; tasks: {fails})")
    lines += [f"warning: {w}" for w in res.warnings]
    if not res.computed:
        lines.append("The rule is NOT COMPUTED:")
        lines += [f"  - {p}" for p in res.not_computed]
        return lines
    s, o = (res.F[m] for m in FRONTIER)
    if all(res.u.get(m, 0) == 0 for m in FRONTIER):
        verdict = "CLOSED" if res.closed else "OPEN"
        lines.append(
            f"F(Sonnet 5) = {s} {'≤' if s <= THRESHOLD else '>'} {THRESHOLD} and "
            f"F(Opus 5.5) = {o} {'≤' if o <= THRESHOLD else '>'} {THRESHOLD}: "
            f"the room is {verdict}."
        )
        return lines
    for label in FRONTIER:
        und = ", ".join(res.undecided.get(label) or []) or "none"
        lines.append(f"u({label}) = {res.u[label]} undecided slot(s)  (tasks present: {und})")
    su, ou = (res.u[m] for m in FRONTIER)
    verdict = (res.verdict or "").upper()
    lines.append(
        f"F-(Sonnet 5) = {s}, F-(Sonnet 5) + u = {s + su}; F-(Opus 5.5) = {o}, "
        f"F-(Opus 5.5) + u = {o + ou}: the room is {verdict} (bound form, some tasks undecided)."
    )
    return lines
