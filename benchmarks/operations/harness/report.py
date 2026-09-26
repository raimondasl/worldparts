"""Summaries of a graded operations run (``summary.json`` and ``summary.md``).

Per model and arm: the pass rate with the task as the unit (a task's score is its mean
pass over its sessions; the rate is the mean over tasks), by family and generator, the
confident-wrong rate CW (the mean over F3 tasks of the share of the task's sessions whose
diagnosis counts as confident wrong), under-commitment, abstentions, 90 % interval
coverage, tokens, cost, turns and time, cost per pass (with the rule of
:func:`benchmarks.operations.analysis.outcome.arm_cost` for sessions whose cost the CLI did
not report, and their number), infrastructure errors and contamination. Contaminated
sessions are left out of every rate (as in v0.2) and listed; sessions with an
infrastructure error and no re-run left are graded as they are (never excluded); sessions
still waiting for a re-run are listed and make the summary provisional.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..analysis.outcome import arm_cost
from .headroom import CONTAMINATION_LIMIT, contamination_shares
from .infra import attempt_dirs
from .runner import RECORD_FILE, SESSIONS_DIR

ARM_ORDER = ("code+", "code-hint", "code-skill", "lib-directed", "lib", "mcp-hybrid")


def load_records(run_dirs: list[Path]) -> list[dict[str, Any]]:
    """Every session record of the runs; a record graded from an attempt that is no longer
    the latest (a re-run happened since) is marked ``stale``, and one whose bundle was
    replaced since it ran is marked ``superseded`` (never scored)."""
    out = []
    for run_dir in run_dirs:
        for p in sorted((run_dir / SESSIONS_DIR).glob(f"*/{RECORD_FILE}")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec["stale"] = rec.get("attempt") != len(attempt_dirs(p.parent))
            # a session on a bundle that was replaced since (bundles.realisation_digest)
            rec["superseded"] = rec.get("bundle_current") is False
            out.append(rec)
    out.sort(key=lambda r: (r["model"], r["arm"], r["task"], r["realisation"]))
    return out


def _median(xs: list[Any]) -> float | None:
    vals = [float(x) for x in xs if x is not None]
    return statistics.median(vals) if vals else None


def task_rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Task-unit pass rate: mean over tasks of the mean pass over each task's sessions."""
    by_task: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        by_task[r["task"]].append(bool(r["passed"]))
    scores = {t: sum(v) / len(v) for t, v in by_task.items()}
    return {
        "tasks": len(scores),
        "sessions": len(records),
        "sessions_passed": sum(bool(r["passed"]) for r in records),
        "rate": (sum(scores.values()) / len(scores)) if scores else None,
    }


def cw_rate(records: list[dict[str, Any]], category: str = "confident_wrong") -> float | None:
    """Mean over F3 tasks of the share of the task's sessions in ``category``."""
    by_task: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        if r.get("family") == "F3":
            by_task[r["task"]].append(r.get("cw_category") == category)
    if not by_task:
        return None
    return sum(sum(v) / len(v) for v in by_task.values()) / len(by_task)


def _group(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in records if not r.get("contamination")]
    cost = [r.get("cost_usd") for r in valid]
    passes = sum(bool(r["passed"]) for r in valid)
    # The rule of the outcome analysis: a session without a reported cost is estimated.
    total_cost, n_estimated = arm_cost(valid)
    intervals = [r.get("intervals") or {} for r in valid]
    n_int = sum(int(i.get("n", 0)) for i in intervals)
    fam: dict[str, Any] = {}
    for f in sorted({r.get("family") for r in valid if r.get("family")}):
        fam[f] = task_rate([r for r in valid if r.get("family") == f])["rate"]
    gen: dict[str, Any] = {}
    for g in sorted({r.get("generator") for r in valid if r.get("generator")}):
        gen[g] = task_rate([r for r in valid if r.get("generator") == g])["rate"]
    return {
        **task_rate(valid),
        "by_family": fam,
        "by_generator": gen,
        "cw": cw_rate(valid),
        "under_commitment": cw_rate(valid, "under_commitment"),
        "abstentions": sum(len(r.get("abstentions") or []) for r in valid),
        "unjustified_abstentions": sum(len(r.get("unjustified_abstentions") or []) for r in valid),
        "intervals": n_int,
        "interval_coverage": (
            sum(int(i.get("covered", 0)) for i in intervals) / n_int if n_int else None
        ),
        "median_tokens": _median([r.get("tokens") for r in valid]),
        "median_cost_usd": _median(cost),
        "median_turns": _median([r.get("turns") for r in valid]),
        "median_duration_s": _median([r.get("duration_s") for r in valid]),
        "total_cost_usd": None if total_cost is None else round(total_cost, 4),
        "cost_per_pass_usd": (total_cost / passes) if passes and total_cost is not None else None,
        "costs_estimated": n_estimated,
        "timeouts": sum(1 for r in valid if r.get("timed_out")),
        "turn_limit_hits": sum(1 for r in valid if r.get("result_subtype") == "error_max_turns"),
        "infra_errors_graded": sum(1 for r in valid if r.get("infra_error")),
        "contaminated": len(records) - len(valid),
    }


def summarise(records: list[dict[str, Any]], run_ids: list[str]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = defaultdict(dict)
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by[(r["model"], r["arm"])].append(r)
    for (model, arm), rs in sorted(by.items()):
        groups[model][arm] = _group(rs)
    shares = contamination_shares(records)
    return {
        "runs": run_ids,
        "sessions": len(records),
        "groups": groups,
        "contamination_by_arm": shares,
        "gate_blocked_by_contamination": sorted(
            a for a, s in shares.items() if s["share"] > CONTAMINATION_LIMIT
        ),
        "pending_reruns": sorted(r["sid"] for r in records if r.get("rerun_allowed")),
        "stale_records": sorted(r["sid"] for r in records if r.get("stale")),
        "contaminated": [
            {**_pick(r, _WHO), "reasons": r["contamination"]}
            for r in records
            if r.get("contamination")
        ],
        "infra_errors": [
            {**_pick(r, (*_WHO, "attempt", "rerun_allowed")), "signatures": r["infra_error"]}
            for r in records
            if r.get("infra_error")
        ],
        "sessions_table": [_pick(r, _TABLE) for r in records],
    }


_WHO = ("sid", "model", "task", "arm")
_TABLE = (
    *_WHO,
    "family",
    "realisation",
    "attempt",
    "passed",
    "n_passed",
    "n_keys",
    "cw_category",
    "cost_usd",
    "turns",
    "duration_s",
    "timed_out",
)


def _pick(r: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: r.get(k) for k in keys}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f} %"


def _num(x: float | None, digits: int = 0) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def to_markdown(s: dict[str, Any]) -> str:
    lines = [f"# Operations benchmark run {', '.join(s['runs'])}", ""]
    if s["pending_reruns"] or s["stale_records"]:
        lines += [
            "**Provisional:** sessions wait for an infrastructure re-run or a re-grade: "
            + ", ".join(s["pending_reruns"] + s["stale_records"]),
            "",
        ]
    if s["gate_blocked_by_contamination"]:
        lines += [
            "**Gate blocked:** more than 5 % of the sessions of "
            + ", ".join(s["gate_blocked_by_contamination"])
            + " are contaminated; fix the cause and re-run that arm.",
            "",
        ]
    lines += [
        "| Model | Arm | Tasks | Sessions passed | Pass rate (task unit) | CW (F3) | "
        "Under-commitment (F3) | 90 % coverage | Median tokens | Median cost | Median turns | "
        "Median time | Cost per pass | Timeouts | Turn limits | Contaminated |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, arms in s["groups"].items():
        for arm in sorted(arms, key=lambda a: (ARM_ORDER.index(a) if a in ARM_ORDER else 99, a)):
            g = arms[arm]
            lines.append(
                f"| {model} | {arm} | {g['tasks']} | {g['sessions_passed']}/{g['sessions']} | "
                f"{_pct(g['rate'])} | {_pct(g['cw'])} | {_pct(g['under_commitment'])} | "
                f"{_pct(g['interval_coverage'])} ({g['intervals']}) | {_num(g['median_tokens'])} | "
                f"${_num(g['median_cost_usd'], 3)} | {_num(g['median_turns'])} | "
                f"{_num(g['median_duration_s'])} s | ${_num(g['cost_per_pass_usd'], 3)}"
                f"{' (' + str(g['costs_estimated']) + ' est.)' if g['costs_estimated'] else ''} | "
                f"{g['timeouts']} | {g['turn_limit_hits']} | {g['contaminated']} |"
            )
    lines += ["", "## By family (pass rate, task unit)", ""]
    fams = sorted(
        {f for arms in s["groups"].values() for g in arms.values() for f in g["by_family"]}
    )
    lines += [
        "| Model | Arm | " + " | ".join(fams) + " |",
        "|---|---|" + "---:|" * len(fams),
    ]
    for model, arms in s["groups"].items():
        for arm, g in arms.items():
            lines.append(
                f"| {model} | {arm} | "
                + " | ".join(_pct(g["by_family"].get(f)) for f in fams)
                + " |"
            )
    lines += ["", "## Sessions", "", "| Session | Model | Task | Arm | r | Attempt | Pass | "
              "Keys | Diagnosis | Cost | Turns | Time |",
              "|---|---|---|---|---:|---:|---|---:|---|---:|---:|---:|"]  # fmt: skip
    for r in s["sessions_table"]:
        lines.append(
            f"| {r['sid']} | {r['model']} | {r['task']} | {r['arm']} | {r['realisation']} | "
            f"{r['attempt']} | {'yes' if r['passed'] else 'no'} | {r['n_passed']}/{r['n_keys']} | "
            f"{r['cw_category'] or ''} | ${_num(r['cost_usd'], 3)} | {_num(r['turns'])} | "
            f"{_num(r['duration_s'])} s |"
        )
    if s["infra_errors"]:
        lines += ["", "## Infrastructure errors", ""]
        for e in s["infra_errors"]:
            state = "re-run pending" if e["rerun_allowed"] else "no re-run left: graded as is"
            lines.append(
                f"- {e['sid']} ({e['model']} {e['task']} {e['arm']}, attempt {e['attempt']}): "
                f"{', '.join(e['signatures'])} ({state})"
            )
    if s["contaminated"]:
        lines += ["", "## Contaminated sessions (left out of the rates)", ""]
        for c in s["contaminated"]:
            lines.append(
                f"- {c['sid']} ({c['model']} {c['task']} {c['arm']}): {'; '.join(c['reasons'])}"
            )
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path) -> dict[str, Any]:
    all_records = load_records([run_dir])
    records = [r for r in all_records if not r.get("superseded")]
    s = summarise(records, [run_dir.name])
    s["superseded"] = sorted(r["sid"] for r in all_records if r.get("superseded"))
    (run_dir / "summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(to_markdown(s), encoding="utf-8")
    return s
