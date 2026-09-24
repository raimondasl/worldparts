"""Command line of the composition benchmark harness.

Run from the repository root::

    uv run python -m benchmarks.composition.harness validate
    uv run python -m benchmarks.composition.harness regen [--check]
    uv run python -m benchmarks.composition.harness oracle [--corrupt]
    uv run python -m benchmarks.composition.harness run --model M [--tasks ...] \\
        [--conditions mcp code] [--repeats N] [--dry-run]
    uv run python -m benchmarks.composition.harness grade RUN_ID
    uv run python -m benchmarks.composition.harness report RUN_ID
    uv run python -m benchmarks.composition.harness smoke --condition code --prompt ...
    uv run python -m benchmarks.composition.harness code-env
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from . import regen as regen_mod
from .grading import Grade, build_prompt, contamination, grade, infra_error, parse_stream
from .reference import ReferenceStepError
from .report import gate_line, summarise, to_markdown, write_report
from .runner import (
    CONDITIONS,
    RunSpec,
    build_command,
    child_env,
    claude_version,
    default_code_env_dir,
    ensure_code_env,
    execute,
    format_command,
    mcp_config,
)
from .tasks import RESULTS_DIR, TASKS_DIR, Task, load_tasks


# ----------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------
def _split(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out: list[str] = []
    for v in values:
        out.extend(x for x in re.split(r"[,\s]+", v) if x)
    return out or None


def _select_tasks(args: argparse.Namespace) -> list[Task]:
    tasks, errors = load_tasks(args.tasks_dir, _split(args.tasks))
    for e in errors:
        print(f"INVALID {e.path}", file=sys.stderr)
        for p in e.problems:
            print(f"        {p}", file=sys.stderr)
    levels = {int(x) for x in _split(getattr(args, "levels", None)) or []}
    if levels:
        tasks = [t for t in tasks if t.level in levels]
    if errors and not getattr(args, "allow_invalid", False):
        raise SystemExit(f"{len(errors)} invalid task file(s); fix them or pass --allow-invalid")
    return tasks


def _record(
    task: Task, condition: str, repeat: int, run_dir: Path, g: Grade, stream: Any, outcome: Any
) -> dict[str, Any]:
    return {
        "task": task.id,
        "level": task.level,
        "category": task.category,
        "domain": task.domain,
        "condition": condition,
        "repeat": repeat,
        "passed": g.passed,
        "model": stream.model if stream else None,
        "grade": g.to_dict(),
        "stream": stream.to_dict() if stream else {},
        "outcome": outcome or {},
        "infra_error": infra_error(stream, outcome) if stream else None,
        "contamination": (contamination(stream, condition, task.id) or None) if stream else None,
        "dir": str(run_dir),
    }


def grade_run(task: Task, run_dir: Path) -> dict[str, Any]:
    """(Re)grade one run directory ``<task>/<condition>-<repeat>``; write its record."""
    condition, _, rep = run_dir.name.rpartition("-")
    stream_path = run_dir / "stream.jsonl"
    text = stream_path.read_text(encoding="utf-8", errors="replace") if stream_path.exists() else ""
    stream = parse_stream(text)
    outcome_path = run_dir / "outcome.json"
    outcome = json.loads(outcome_path.read_text("utf-8")) if outcome_path.exists() else {}
    g = grade(task, stream.final_text, stream.tool_numbers)
    (run_dir / "final.txt").write_text(stream.final_text or "", encoding="utf-8")
    (run_dir / "usage.json").write_text(json.dumps(stream.to_dict(), indent=2), "utf-8")
    (run_dir / "grade.json").write_text(json.dumps(g.to_dict(), indent=2), "utf-8")
    record = _record(task, condition, int(rep), run_dir, g, stream, outcome)
    (run_dir / "record.json").write_text(json.dumps(record, indent=2), "utf-8")
    return record


def _run_dir(run_id: str) -> Path:
    p = Path(run_id)
    return p if p.is_absolute() or p.exists() else RESULTS_DIR / run_id


# ----------------------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    tasks, errors = load_tasks(args.tasks_dir, _split(args.tasks))
    for t in tasks:
        missing = " (no expected yet)" if t.expected is None else ""
        print(f"ok      {t.id}: level {t.level}, {t.category}, {t.domain}{missing}")
    for e in errors:
        print(f"INVALID {e.path}")
        for p in e.problems:
            print(f"        {p}")
    print(f"{len(tasks)} valid, {len(errors)} invalid")
    return 1 if errors else 0


def cmd_regen(args: argparse.Namespace) -> int:
    return regen_mod.regen(args.tasks_dir, _split(args.tasks), check=args.check)


def corrupt_expected(task: Task) -> dict[str, Any]:
    """Expected answers pushed out of tolerance: numbers by 3x their tolerance, choices to
    the next choice, booleans negated."""
    assert task.expected is not None
    out: dict[str, Any] = {}
    for a in task.answers:
        e = task.expected[a.key]
        if a.kind == "number":
            out[a.key] = float(e) + 3.0 * a.tolerance(float(e))
        elif a.kind == "boolean":
            out[a.key] = not e
        else:
            i = a.choices.index(e)
            out[a.key] = a.choices[(i + 1) % len(a.choices)]
    return out


def oracle_reply(values: dict[str, Any]) -> str:
    """A final reply that returns ``values`` in the requested format."""
    return "Done.\n\n```json\n" + json.dumps(values, indent=2) + "\n```\n"


def oracle_records(tasks: list[Task], corrupt: bool = False) -> list[dict[str, Any]]:
    """Grade the frozen expected answers (or corrupted ones) as if an agent returned them."""
    records = []
    for t in tasks:
        if t.expected is None:
            raise SystemExit(f"{t.id} has no expected answers; run regen first")
        values = corrupt_expected(t) if corrupt else dict(t.expected)
        g = grade(t, oracle_reply(values), tool_numbers=None)
        name = "oracle-corrupt" if corrupt else "oracle"
        records.append(_record(t, name, 1, Path("-"), g, None, {}))
    return records


def cmd_oracle(args: argparse.Namespace) -> int:
    tasks = _select_tasks(args)
    if not tasks:
        print("no tasks")
        return 1
    records = oracle_records(tasks, corrupt=args.corrupt)
    for r in records:
        status = "pass" if r["passed"] else "FAIL"
        print(f"{status} {r['task']} ({r['grade']['n_passed']}/{len(r['grade']['answers'])})")
    s = summarise(records, "oracle")
    rate = next(iter(s["conditions"].values()))["pass_rate"]
    want = 0.0 if args.corrupt else 1.0
    print(f"pass rate {100 * rate:.0f} % (required {100 * want:.0f} %)")
    return 0 if rate == want else 1


def _run_id(model: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{re.sub(r'[^A-Za-z0-9.]+', '-', model)}"


def cmd_run(args: argparse.Namespace) -> int:
    tasks = _select_tasks(args)
    if not tasks:
        print("no tasks selected")
        return 1
    conditions = _split(args.conditions) or list(CONDITIONS)
    for c in conditions:
        if c not in CONDITIONS:
            raise SystemExit(f"unknown condition '{c}' (choose from {', '.join(CONDITIONS)})")
    missing = [t.id for t in tasks if t.expected is None]
    if missing and not args.dry_run:
        raise SystemExit(f"tasks without expected answers (run regen): {', '.join(missing)}")
    run_id = args.run_id or _run_id(args.model)
    run_dir = RESULTS_DIR / run_id
    specs = [
        (t, RunSpec(t.id, c, k, build_prompt(t), run_dir / t.id / f"{c}-{k}"))
        for t in tasks
        for c in conditions
        for k in range(1, args.repeats + 1)
    ]
    if args.dry_run:
        return dry_run(list(specs), args)
    code_env = ensure_code_env(args.code_env) if "code" in conditions else None
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "model": args.model,
        "conditions": conditions,
        "repeats": args.repeats,
        "tasks": [t.id for t in tasks],
        "max_turns": args.max_turns,
        "timeout_s": args.timeout,
        "max_budget_usd": args.max_budget_usd,
        "claude_version": claude_version(),
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"run {run_id}: {len(specs)} session(s) -> {run_dir}")

    stop: list[str] = []

    def one(item: tuple[Task, RunSpec]) -> dict[str, Any] | None:
        task, spec = item
        if stop:
            return None
        done = spec.out_dir / "record.json"
        if done.exists() and not args.rerun:
            rec = json.loads(done.read_text("utf-8"))
            if not rec.get("infra_error"):  # runs that hit an infrastructure error are redone
                print(f"skip {spec.label} {spec.condition}-{spec.repeat} (already graded)")
                return rec
            shutil.rmtree(spec.out_dir, ignore_errors=True)
        outcome = execute(
            spec,
            args.model,
            args.max_turns,
            args.timeout,
            code_env,
            args.max_budget_usd,
            keep_tmp=args.keep_tmp,
        )
        rec = grade_run(task, spec.out_dir)
        if rec["infra_error"]:
            print(f"INFRA {spec.label} {spec.condition}-{spec.repeat}: {rec['infra_error']}")
            if "authentication" in rec["infra_error"] or "could not start" in rec["infra_error"]:
                stop.append(rec["infra_error"])
            return rec
        if rec.get("contamination"):
            print(
                f"CONTAMINATED {spec.label} {spec.condition}-{spec.repeat} (excluded from the "
                f"rates): {'; '.join(rec['contamination'])}",
                flush=True,
            )
        cost = rec["stream"].get("cost_usd")
        print(
            f"{'PASS' if rec['passed'] else 'fail'} {spec.label} {spec.condition}-{spec.repeat}"
            f" ({rec['grade']['n_passed']}/{len(rec['grade']['answers'])} answers, "
            f"{rec['stream'].get('tool_calls')} tool calls, "
            f"{'n/a' if cost is None else f'${cost:.3f}'}, {outcome.wall_s:.0f} s"
            f"{', TIMEOUT' if outcome.timed_out else ''})",
            flush=True,
        )
        return rec

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        list(pool.map(one, specs))
    summary = write_report(run_dir)
    if stop:
        print(
            f"STOPPED: {stop[0]}. Fix it and rerun with --run-id {run_id} (graded runs are kept)."
        )
    print(gate_line(summary))
    print(f"report: {run_dir / 'summary.md'}")
    return 0


def dry_run(specs: list[tuple[Task | None, RunSpec]], args: argparse.Namespace) -> int:
    """Print the exact claude command lines and prompts without running anything."""
    code_env = args.code_env or default_code_env_dir()
    for _, spec in specs:
        run = Path("<tmp-run-dir>")
        cmd = build_command(
            spec.condition,
            args.model,
            run / "mcp.json",
            run / "settings.json",
            args.max_turns,
            args.max_budget_usd,
            claude="claude",
        )
        _, changed = child_env(spec.condition, code_env)
        print("=" * 88)
        print(f"{spec.label}  condition={spec.condition}  repeat={spec.repeat}  -> {spec.out_dir}")
        print(f"cwd: {run / 'work'}   (a fresh temporary directory outside the repository)")
        print("env: " + " ".join(f"{k}={v}" for k, v in changed.items()))
        print("mcp.json: " + json.dumps(mcp_config(spec.condition, run / "systems")))
        print(f"timeout: {args.timeout} s")
        print("command (prompt on stdin):")
        print("  " + format_command(cmd))
        print("prompt:")
        for line in spec.prompt.splitlines():
            print("  | " + line)
    print("=" * 88)
    print(f"{len(specs)} session(s); nothing was run (--dry-run)")
    return 0


def cmd_grade(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_id)
    tasks, _ = load_tasks(args.tasks_dir)
    by_id = {t.id: t for t in tasks}
    n = 0
    for d in sorted(run_dir.glob("*/*/stream.jsonl")):
        rd = d.parent
        task = by_id.get(rd.parent.name)
        if task is None:
            print(f"skip {rd} (no valid task '{rd.parent.name}')")
            continue
        rec = grade_run(task, rd)
        n += 1
        print(f"{'PASS' if rec['passed'] else 'fail'} {task.id} {rd.name}")
    summary = write_report(run_dir)
    print(f"graded {n} run(s)")
    print(gate_line(summary))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run_id)
    summary = write_report(run_dir)
    if args.print:
        print(to_markdown(summary))
    else:
        print(gate_line(summary))
        print(f"wrote {run_dir / 'summary.md'} and summary.json")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """One plumbing check with a raw prompt (no task, no answer format)."""
    run_id = args.run_id or ("smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out = RESULTS_DIR / run_id / "smoke" / f"{args.condition}-1"
    spec = RunSpec("smoke", args.condition, 1, args.prompt, out)
    if args.dry_run:
        return dry_run([(None, spec)], args)
    code_env = ensure_code_env(args.code_env) if args.condition == "code" else None
    outcome = execute(
        spec, args.model, args.max_turns, args.timeout, code_env, args.max_budget_usd,
        keep_tmp=args.keep_tmp,
    )  # fmt: skip
    text = (out / "stream.jsonl").read_text(encoding="utf-8", errors="replace")
    s = parse_stream(text)
    (out / "final.txt").write_text(s.final_text or "", encoding="utf-8")
    (out / "usage.json").write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")
    report = {
        "condition": args.condition,
        "infra_error": infra_error(s, outcome.__dict__),
        "contamination": contamination(s, args.condition) or None,
        "exit_code": outcome.exit_code,
        "timed_out": outcome.timed_out,
        "error": outcome.error,
        "model": s.model,
        "result_subtype": s.result_subtype,
        "is_error": s.is_error,
        "final_text": s.final_text,
        "cost_usd": s.cost_usd,
        "num_turns": s.num_turns,
        "duration_ms": s.duration_ms,
        "total_tokens": s.total_tokens,
        "tool_calls": s.tool_names,
        "tools_available": s.tools_available,
        "mcp_servers": s.mcp_servers,
        "denied": s.denied,
        "dir": str(out),
    }
    (out / "smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if outcome.exit_code == 0 and not s.is_error else 1


def cmd_code_env(args: argparse.Namespace) -> int:
    print(ensure_code_env(args.code_env))
    return 0


# ----------------------------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------------------------
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.composition.harness",
        description="worldparts v0.2 composition benchmark: tasks, runs, grading, reports.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def tasks_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
        sp.add_argument(
            "--tasks", nargs="*", help="Task ids or glob patterns (comma or space separated)."
        )
        sp.add_argument("--levels", nargs="*", help="Only these levels, e.g. --levels 1.")
        sp.add_argument("--allow-invalid", action="store_true", help="Skip invalid tasks.")

    def claude_opts(sp: argparse.ArgumentParser, default_turns: int) -> None:
        sp.add_argument("--model", default="opus", help="Claude model alias or id.")
        sp.add_argument("--max-turns", type=int, default=default_turns)
        sp.add_argument("--timeout", type=float, default=1800.0, help="Seconds per session.")
        sp.add_argument("--max-budget-usd", type=float, default=None, help="Per session.")
        sp.add_argument("--code-env", type=Path, default=None, help="Code-condition env dir.")
        sp.add_argument("--run-id", default=None)
        sp.add_argument("--dry-run", action="store_true", help="Print commands; run nothing.")
        sp.add_argument("--keep-tmp", action="store_true", help="Keep the temp run dirs.")

    sp = sub.add_parser("validate", help="Validate task files.")
    sp.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    sp.add_argument("--tasks", nargs="*")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("regen", help="Recompute reference.expected (or --check it).")
    sp.add_argument("--check", action="store_true")
    sp.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    sp.add_argument("--tasks", nargs="*")
    sp.set_defaults(fn=cmd_regen)

    sp = sub.add_parser("oracle", help="Grade the expected answers (must score 100 %%).")
    tasks_opts(sp)
    sp.add_argument("--corrupt", action="store_true", help="Perturbed answers (must score 0 %%).")
    sp.set_defaults(fn=cmd_oracle)

    sp = sub.add_parser("run", help="Run agent sessions and grade them.")
    tasks_opts(sp)
    claude_opts(sp, default_turns=80)
    sp.add_argument("--conditions", nargs="*", help="mcp, code (default: both).")
    sp.add_argument("--repeats", type=int, default=1)
    sp.add_argument("--jobs", type=int, default=1, help="Sessions in parallel.")
    sp.add_argument("--rerun", action="store_true", help="Redo runs that already have a record.")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("grade", help="(Re)grade a run from its saved streams.")
    sp.add_argument("run_id")
    sp.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    sp.set_defaults(fn=cmd_grade)

    sp = sub.add_parser("report", help="Write summary.md and summary.json for a run.")
    sp.add_argument("run_id")
    sp.add_argument("--print", action="store_true", help="Print the markdown report.")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("smoke", help="One plumbing check with a raw prompt.")
    claude_opts(sp, default_turns=2)
    sp.add_argument("--condition", choices=CONDITIONS, required=True)
    sp.add_argument("--prompt", required=True)
    sp.set_defaults(fn=cmd_smoke)

    sp = sub.add_parser("code-env", help="Create or check the code-condition environment.")
    sp.add_argument("--code-env", type=Path, default=None)
    sp.set_defaults(fn=cmd_code_env)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except ReferenceStepError as exc:  # pragma: no cover - surfaced by regen/validate
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
