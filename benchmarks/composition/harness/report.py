"""Aggregate graded runs into ``summary.json`` and ``summary.md``.

Input: the ``record.json`` of every run under ``results/<run-id>/<task>/<cond>-<rep>/``
(written by the grader). Output: pass rates by condition, level, category and domain,
per-answer accuracy, traceability, medians of tool calls, tokens, cost and duration, a
per-task table and the v0.2 decision-gate line (level-1 pass rate of condition mcp versus
80 %).
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

GATE_THRESHOLD = 0.80
GATE_LEVEL = 1
GATE_CONDITION = "mcp"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilson score interval for k successes in n trials."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    k = sum(1 for r in records if r["passed"])
    ci = wilson(k, n)
    return {
        "runs": n,
        "passed": k,
        "pass_rate": (k / n) if n else None,
        "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
    }


def _median(values: list[float | None]) -> float | None:
    xs = [float(v) for v in values if v is not None]
    return statistics.median(xs) if xs else None


def load_records(run_dir: Path) -> list[dict[str, Any]]:
    """Every run record under ``run_dir`` (sorted by task, condition, repeat)."""
    records = []
    for p in sorted(run_dir.glob("*/*/record.json")):
        records.append(json.loads(p.read_text(encoding="utf-8")))
    records.sort(key=lambda r: (r["task"], r["condition"], r["repeat"]))
    return records


def summarise(records: list[dict[str, Any]], run_id: str = "") -> dict[str, Any]:
    """The summary document (``summary.json``).

    Runs with an infrastructure error (``record["infra_error"]``, e.g. the CLI was not
    logged in) are left out of every rate and listed under ``infra_errors``. Contaminated
    runs (``record["contamination"]``: the agent's tools touched the benchmark's own files,
    see :func:`benchmarks.composition.harness.grading.contamination`) are left out as well
    and listed under ``contaminated``.
    """
    infra = [r for r in records if r.get("infra_error")]
    records = [r for r in records if not r.get("infra_error")]
    tainted = [r for r in records if r.get("contamination")]
    records = [r for r in records if not r.get("contamination")]
    conds = sorted({r["condition"] for r in records})
    by_cond: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_cond[r["condition"]].append(r)

    def grouped(field: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in conds:
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in by_cond[c]:
                groups[str(r[field])].append(r)
            out[c] = {g: _rate(rs) for g, rs in sorted(groups.items())}
        return out

    answers: dict[str, dict[str, Any]] = {}
    trace: dict[str, dict[str, Any]] = {}
    costs: dict[str, dict[str, Any]] = {}
    fmt: dict[str, dict[str, Any]] = {}
    for c in conds:
        per: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "passed": 0})
        kinds: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "passed": 0})
        tr = {
            "numeric_answers": 0,
            "traceable": 0,
            "correct": 0,
            "correct_traceable": 0,
            "in_prompt": 0,
            "correct_untraceable_not_in_prompt": 0,
        }
        for r in by_cond[c]:
            for a in r["grade"]["answers"]:
                slot = per[f"{r['task']}/{a['key']}"]
                slot["n"] += 1
                slot["passed"] += int(a["passed"])
                kinds[a["kind"]]["n"] += 1
                kinds[a["kind"]]["passed"] += int(a["passed"])
                if a["kind"] == "number" and a.get("traceable") is not None:
                    tr["numeric_answers"] += 1
                    tr["traceable"] += int(a["traceable"])
                    tr["correct"] += int(a["passed"])
                    tr["correct_traceable"] += int(a["passed"] and a["traceable"])
                    tr["in_prompt"] += int(bool(a.get("in_prompt")))
                    tr["correct_untraceable_not_in_prompt"] += int(
                        a["passed"] and not a["traceable"] and not a.get("in_prompt")
                    )
        n_ans = sum(v["n"] for v in per.values())
        answers[c] = {
            "overall": {
                "n": n_ans,
                "passed": sum(v["passed"] for v in per.values()),
                "accuracy": (sum(v["passed"] for v in per.values()) / n_ans) if n_ans else None,
            },
            "by_kind": {
                k: {**v, "accuracy": v["passed"] / v["n"] if v["n"] else None}
                for k, v in sorted(kinds.items())
            },
            "by_answer": {
                k: {**v, "accuracy": v["passed"] / v["n"] if v["n"] else None}
                for k, v in sorted(per.items())
            },
        }
        tr["traceable_rate"] = (
            tr["traceable"] / tr["numeric_answers"] if tr["numeric_answers"] else None
        )
        trace[c] = tr
        rs = by_cond[c]
        costs[c] = {
            "median_tool_calls": _median([r["stream"].get("tool_calls") for r in rs]),
            "median_tool_errors": _median([r["stream"].get("tool_errors") for r in rs]),
            "median_turns": _median([r["stream"].get("num_turns") for r in rs]),
            "median_total_tokens": _median([r["stream"].get("total_tokens") for r in rs]),
            "median_output_tokens": _median(
                [(r["stream"].get("usage") or {}).get("output_tokens") for r in rs]
            ),
            "median_cost_usd": _median([r["stream"].get("cost_usd") for r in rs]),
            "total_cost_usd": round(sum(float(r["stream"].get("cost_usd") or 0) for r in rs), 4),
            "median_duration_s": _median(
                [
                    (r["stream"]["duration_ms"] / 1000.0)
                    if r["stream"].get("duration_ms") is not None
                    else r["outcome"].get("wall_s")
                    for r in rs
                ]
            ),
            "timeouts": sum(1 for r in rs if r["outcome"].get("timed_out")),
            "cli_errors": sum(
                1 for r in rs if r["stream"].get("is_error") or r["outcome"].get("error")
            ),
        }
        fmt[c] = {
            "runs_with_format_issues": sum(1 for r in rs if r["grade"]["format_issues"]),
            "unparseable": sum(1 for r in rs if r["grade"]["parse_error"]),
        }

    tasks: dict[str, dict[str, Any]] = {}
    for r in records:
        t = tasks.setdefault(
            r["task"],
            {
                "task": r["task"],
                "level": r["level"],
                "category": r["category"],
                "domain": r["domain"],
                "conditions": {},
            },
        )
        slot = t["conditions"].setdefault(
            r["condition"], {"runs": 0, "passed": 0, "answers_passed": 0, "answers": 0}
        )
        slot["runs"] += 1
        slot["passed"] += int(r["passed"])
        slot["answers"] += len(r["grade"]["answers"])
        slot["answers_passed"] += sum(int(a["passed"]) for a in r["grade"]["answers"])

    gate_records = [r for r in by_cond.get(GATE_CONDITION, []) if int(r["level"]) == GATE_LEVEL]
    gate = _rate(gate_records)
    gate["threshold"] = GATE_THRESHOLD
    gate["met"] = (gate["pass_rate"] >= GATE_THRESHOLD) if gate["pass_rate"] is not None else None
    models = sorted({str(r.get("model")) for r in records if r.get("model")})
    return {
        "run_id": run_id,
        "models": models,
        "runs": len(records),
        "tasks": len(tasks),
        "conditions": {c: _rate(by_cond[c]) for c in conds},
        "by_level": grouped("level"),
        "by_category": grouped("category"),
        "by_domain": grouped("domain"),
        "answers": answers,
        "traceability": trace,
        "usage": costs,
        "format": fmt,
        "per_task": sorted(tasks.values(), key=lambda t: (t["level"], t["task"])),
        "decision_gate": gate,
        "infra_errors": [
            {
                "task": r["task"],
                "condition": r["condition"],
                "repeat": r["repeat"],
                "error": r["infra_error"],
            }
            for r in infra
        ],
        "contaminated": [
            {
                "task": r["task"],
                "condition": r["condition"],
                "repeat": r["repeat"],
                "passed": r["passed"],
                "reasons": r["contamination"],
            }
            for r in tainted
        ],
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f} %"


def _num(x: float | None, digits: int = 0) -> str:
    if x is None:
        return "n/a"
    return f"{x:,.{digits}f}"


def gate_line(summary: dict[str, Any]) -> str:
    g = summary["decision_gate"]
    if not g["runs"]:
        return (
            f"Decision gate: no level-{GATE_LEVEL} runs in condition {GATE_CONDITION}; "
            "the gate is not evaluated."
        )
    verdict = "MET" if g["met"] else "NOT MET"
    ci = g["ci95"]
    return (
        f"Decision gate: level-{GATE_LEVEL} pass rate for condition {GATE_CONDITION} is "
        f"{_pct(g['pass_rate'])} ({g['passed']}/{g['runs']}, 95 % CI {_pct(ci[0])} to "
        f"{_pct(ci[1])}) versus the {_pct(GATE_THRESHOLD)} threshold: {verdict}."
    )


def to_markdown(summary: dict[str, Any]) -> str:
    """The human-readable report (``summary.md``)."""
    conds = list(summary["conditions"])
    lines = [
        f"# Composition benchmark: {summary['run_id']}",
        "",
        f"Models: {', '.join(summary['models']) or 'n/a'}. Runs: {summary['runs']} over "
        f"{summary['tasks']} task(s)."
        + (
            f" {len(summary['infra_errors'])} run(s) with infrastructure errors are excluded "
            "(listed at the end)."
            if summary["infra_errors"]
            else ""
        )
        + (
            f" {len(summary.get('contaminated', []))} contaminated run(s) are excluded "
            "(listed at the end)."
            if summary.get("contaminated")
            else ""
        ),
        "",
        f"**{gate_line(summary)}**",
        "",
        "## Pass rate by condition",
        "",
        "| Condition | Passed | Runs | Pass rate | 95 % CI |",
        "|---|---:|---:|---:|---|",
    ]
    for c, r in summary["conditions"].items():
        ci = r["ci95"]
        ci_text = f"{_pct(ci[0])} to {_pct(ci[1])}" if ci else "n/a"
        lines.append(f"| {c} | {r['passed']} | {r['runs']} | {_pct(r['pass_rate'])} | {ci_text} |")
    for title, key in (("level", "by_level"), ("category", "by_category"), ("domain", "by_domain")):
        groups = sorted({g for c in conds for g in summary[key].get(c, {})})
        lines += [
            "",
            f"## Pass rate by {title}",
            "",
            f"| {title.capitalize()} | " + " | ".join(conds) + " |",
            "|---|" + "---:|" * len(conds),
        ]
        for g in groups:
            cells = []
            for c in conds:
                r = summary[key].get(c, {}).get(g)
                cells.append(f"{_pct(r['pass_rate'])} ({r['passed']}/{r['runs']})" if r else "-")
            lines.append(f"| {g} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Answers and traceability",
        "",
        "A numeric answer is traceable when its value is within 0.5 % of a number in some tool "
        "result of the session (it came from a computation, not from thin air). This is a "
        "diagnostic: an answer equal to a prompt constant (column 'In prompt') or a "
        "closed-form result of prompt constants can be right without any tool result, so "
        "an untraceable correct answer is not by itself evidence of fabrication.",
        "",
        "| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable "
        "| In prompt | Correct, untraceable, not in prompt |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in conds:
        a = summary["answers"][c]["overall"]
        t = summary["traceability"][c]
        lines.append(
            f"| {c} | {_pct(a['accuracy'])} ({a['passed']}/{a['n']}) | {t['numeric_answers']} | "
            f"{t['correct']} | {t['traceable']} ({_pct(t['traceable_rate'])}) | "
            f"{t['correct_traceable']} | {t['in_prompt']} | "
            f"{t['correct_untraceable_not_in_prompt']} |"
        )
    lines += [
        "",
        "## Effort and cost",
        "",
        "| Condition | Median tool calls | Median turns | Median tokens | Median cost | "
        "Total cost | Median duration | Timeouts | CLI errors | Format issues |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in conds:
        u = summary["usage"][c]
        f = summary["format"][c]
        med_cost = u["median_cost_usd"]
        lines.append(
            f"| {c} | {_num(u['median_tool_calls'], 1)} | {_num(u['median_turns'], 1)} | "
            f"{_num(u['median_total_tokens'])} | "
            f"{'n/a' if med_cost is None else f'${med_cost:.3f}'} | "
            f"${u['total_cost_usd']:.2f} | {_num(u['median_duration_s'], 0)} s | "
            f"{u['timeouts']} | {u['cli_errors']} | {f['runs_with_format_issues']} |"
        )
    lines += [
        "",
        "## Per task",
        "",
        "| Task | Level | Category | Domain | " + " | ".join(conds) + " |",
        "|---|---:|---|---|" + "---|" * len(conds),
    ]
    for t in summary["per_task"]:
        cells = []
        for c in conds:
            s = t["conditions"].get(c)
            cells.append(
                f"{s['passed']}/{s['runs']} runs, {s['answers_passed']}/{s['answers']} answers"
                if s
                else "-"
            )
        lines.append(
            f"| {t['task']} | {t['level']} | {t['category']} | {t['domain']} | "
            + " | ".join(cells)
            + " |"
        )
    lines += ["", "## Per answer", "", "| Answer | " + " | ".join(conds) + " |"]
    lines.append("|---|" + "---:|" * len(conds))
    keys = sorted({k for c in conds for k in summary["answers"][c]["by_answer"]})
    for k in keys:
        cells = []
        for c in conds:
            v = summary["answers"][c]["by_answer"].get(k)
            cells.append(f"{v['passed']}/{v['n']}" if v else "-")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    if summary["infra_errors"]:
        lines += ["", "## Infrastructure errors (excluded)", ""]
        for e in summary["infra_errors"]:
            lines.append(f"- {e['task']} {e['condition']}-{e['repeat']}: {e['error']}")
    if summary.get("contaminated"):
        lines += [
            "",
            "## Contaminated runs (excluded)",
            "",
            "The agent's tools touched the benchmark itself (repository, task files or frozen "
            "answers), so these runs say nothing about solving the task.",
            "",
        ]
        for e in summary["contaminated"]:
            lines.append(
                f"- {e['task']} {e['condition']}-{e['repeat']} "
                f"({'passed' if e['passed'] else 'failed'}): {'; '.join(e['reasons'])}"
            )
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path) -> dict[str, Any]:
    """Write ``summary.json`` and ``summary.md`` into ``run_dir``; return the summary."""
    records = load_records(run_dir)
    summary = summarise(records, run_dir.name)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(to_markdown(summary), encoding="utf-8")
    return summary
