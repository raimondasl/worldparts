"""The Stage 0 headroom rule (PREREGISTRATION.md section 8, "Rule").

For each frontier model m, F(m) counts the development tasks on which the model's
``code-hint`` session on realisation 1 fails while at least one reference estimator passes
realisation 1 (``realisations.r1.reference_pass`` of the task's truth: any estimator whose
value is ``true``; ``null`` means the estimator does not apply or was not run). The room is
closed if and only if F(Sonnet 5) <= 3 and F(Opus 5.5) <= 3. ``code+`` does not enter the rule.

The rule is not computed (and says why) when a development task lacks its code-hint
session for a model, a task has more than one such session, a session is waiting for an
infrastructure re-run, a record is older than its latest attempt, more than 5 % of an arm's
sessions are contaminated (section 6.6: the gate waits until that arm is re-run), the
development set does not have its pre-registered cells, or a task's truth has no r1
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
    "one valid reference estimator passes the same realisation.",
    "- **The room is closed if and only if F(Sonnet 5) ≤ 3 and F(Opus 5.5) ≤ 3.**",
    "- `code+` is recorded but does not enter the rule.",
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

    @property
    def closed(self) -> bool | None:
        if not self.computed:
            return None
        return all(self.F[m] <= THRESHOLD for m in FRONTIER)


def headroom(
    records: list[dict[str, Any]],
    task_ids: list[str],
    truths: dict[str, dict[str, Any]],
    models: dict[str, str],
    set_problems: list[str] | None = None,
) -> HeadroomResult:
    """Evaluate the rule.

    Args:
        records: Session records (``record.json``) of the Stage 0 run(s): every arm and
            model, used for the contamination shares; the rule reads the code-hint ones.
        task_ids: The development tasks (all of them).
        truths: Truth documents by task id (only ``realisations.r1`` is read).
        models: The requested model string of each frontier model, e.g.
            ``{"Sonnet 5": "claude-sonnet-5", "Opus 5.5": "claude-opus-5-5"}``.
        set_problems: Differences of the development set from its pre-registered cells.
    """
    res = HeadroomResult(computed=False)
    if set(models) != set(FRONTIER):
        res.not_computed.append(f"models must name exactly {', '.join(FRONTIER)}")
        return res
    for p in set_problems or []:
        res.not_computed.append(f"development set: {p}")
    rel = [
        r
        for r in records
        if r.get("set") == HEADROOM_SET and r.get("realisation") == HEADROOM_REALISATION
    ]
    for arm, share in sorted(contamination_shares(rel).items()):
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
        if t not in truths:
            res.not_computed.append(f"no truth for {t}")
        elif reference_result(truths[t]) is None:
            res.not_computed.append(f"no r{HEADROOM_REALISATION} reference result for {t}")
    for label in FRONTIER:
        model = models[label]
        mine = [r for r in rel if r["model"] == model and r["arm"] == HEADROOM_ARM]
        res.model_ids[label] = sorted({str(r.get("model_id")) for r in mine})
        by_task: dict[str, list[dict[str, Any]]] = {}
        for r in mine:
            by_task.setdefault(r["task"], []).append(r)
        missing = [t for t in task_ids if t not in by_task]
        extra = sorted(set(by_task) - set(task_ids))
        dupes = sorted(t for t, rs in by_task.items() if len(rs) > 1)
        if missing:
            res.not_computed.append(f"{label} ({model}): no code-hint session for {missing}")
        if extra:
            res.warnings.append(f"{label}: sessions for tasks outside the set ignored: {extra}")
        if dupes:
            res.not_computed.append(f"{label}: more than one code-hint session for {dupes}")
        failing: list[str] = []
        excluded: list[str] = []
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
    verdict = "CLOSED" if res.closed else "OPEN"
    lines.append(
        f"F(Sonnet 5) = {s} {'≤' if s <= THRESHOLD else '>'} {THRESHOLD} and "
        f"F(Opus 5.5) = {o} {'≤' if o <= THRESHOLD else '>'} {THRESHOLD}: the room is {verdict}."
    )
    return lines
