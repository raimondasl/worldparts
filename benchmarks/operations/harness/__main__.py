"""Command line of the operations benchmark harness.

Run from the repository root::

    uv run python -m benchmarks.operations.harness run --set dev --arms code+ code-hint \\
        --models sonnet opus --realisation 1 [--jobs N] [--out DIR] [--dry-run]
    uv run python -m benchmarks.operations.harness audit RUN
    uv run python -m benchmarks.operations.harness run --out RUN --rerun-flagged
    uv run python -m benchmarks.operations.harness grade RUN
    uv run python -m benchmarks.operations.harness headroom RUN [RUN ...]
    uv run python -m benchmarks.operations.harness report RUN [--print]
    uv run python -m benchmarks.operations.harness manifest --set dev [--check]
    uv run python -m benchmarks.operations.harness code-env

``run`` never grades: it runs sessions and then the blind audit. ``grade`` runs the audit
again before it computes any grade. A real ``run`` starts paid Claude Code sessions; use
``--dry-run`` first. Sessions of the same task never run at the same time (``--jobs``
runs different tasks in parallel), so no session can read another arm's work on its task.

``grade RUN --pass-fail`` is the only grader output a firewalled session may receive
(PREREGISTRATION.md section 7): it prints pass or fail per session, writes no record and
appends each evaluation to ``<run>/pass-fail.log``. Records and summaries are for the owner
only, since a numeric key's truth can be worked out from a record.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.grading import parse_stream
from benchmarks.composition.harness.runner import SessionLimits, claude_executable, format_command

from .arms import ARMS, STAGE0_ARMS, build_prompt, get_arm, require_available
from .bundles import (
    RESULTS_DIR,
    SETS,
    BundleError,
    ConfigError,
    OpsTask,
    bundles_root,
    format_manifest,
    load_task,
    load_tasks,
    load_truth,
    manifest_differences,
    manifest_entries,
    manifest_path,
    parse_manifest,
    set_problems,
    task_manifest_prefixes,
    truth_root,
)
from .env import default_code_plus_dir, ensure_code_plus_env, env_info
from .grading import final_reply, grade_answer, tool_numbers_of
from .headroom import HEADROOM_SET, headroom, headroom_lines
from .infra import (
    MAX_ATTEMPTS,
    attempt_dirs,
    audit_attempt,
    audit_lines,
    audit_run,
    stop_reason,
)
from .markers import contamination
from .report import load_records, to_markdown, write_report
from .runner import (
    INDEX_FILE,
    PINNED_LIMITS,
    RECORD_FILE,
    RUN_FILE,
    SessionSpec,
    attempts_done,
    build_command,
    child_env,
    claude_version,
    execute_session,
    read_json,
    session_dir,
    session_mcp_config,
    write_json,
)

#: Default models of ``run`` (aliases; the stream records the model id each resolved to).
DEFAULT_MODELS = ("sonnet", "opus")
#: Where ``grade --pass-fail`` logs every evaluation (the firewall's evaluation log).
PASS_FAIL_LOG = "pass-fail.log"


def utf8_stdout() -> None:
    """Print the rule's symbols (≤) even when stdout is redirected on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _split(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out.extend(x for x in re.split(r"[,\s]+", v) if x)
    return out


def _fail(msg: str) -> SystemExit:
    return SystemExit(f"error: {msg}")


def _run_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    q = RESULTS_DIR / arg
    if q.is_dir():
        return q
    raise _fail(f"no run directory {arg}")


# ----------------------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------------------
def _specs(args: argparse.Namespace) -> list[SessionSpec]:
    try:
        arms = require_available(_split(args.arms) or list(STAGE0_ARMS))
    except ValueError as exc:
        raise _fail(str(exc)) from None
    models = _split(args.models) or list(DEFAULT_MODELS)
    try:
        reals = [int(x) for x in _split(args.realisation) or ["1"]]
    except ValueError:
        raise _fail("--realisation takes integers such as 1 or 1 2 3") from None
    try:
        tasks = load_tasks(args.set, args.bundles, _split(args.tasks) or None)
    except (BundleError, ConfigError) as exc:
        raise _fail(str(exc)) from None
    for t in tasks:
        for k in reals:
            if k not in t.realisations:
                raise _fail(f"{t.task_id} has no realisation r{k}")
    return [
        SessionSpec(args.set, model, t, arm.name, k)
        for model in models
        for t in tasks
        for arm in arms
        for k in reals
    ]


def _limits(args: argparse.Namespace) -> SessionLimits:
    lim = SessionLimits(int(args.max_turns), float(args.timeout))
    if lim != PINNED_LIMITS:
        print(
            f"warning: limits {lim.max_turns} turns / {lim.timeout_s:g} s differ from the "
            f"pre-registered {PINNED_LIMITS.max_turns} / {PINNED_LIMITS.timeout_s:g} s",
            file=sys.stderr,
        )
    return lim


def dry_run(specs: list[SessionSpec], args: argparse.Namespace) -> int:
    """Print the exact claude command lines, environment changes and prompts."""
    limits = _limits(args)
    env_dir = args.code_env or default_code_plus_dir()
    tmp = Path("<tmp-session-dir>")
    for spec in specs:
        arm = get_arm(spec.arm)
        cmd = build_command(
            arm,
            spec.model,
            tmp / "mcp.json",
            tmp / "settings.json",
            limits.max_turns,
            args.effort,
            args.max_budget_usd,
            claude=claude_executable(),
        )
        _, changed = child_env(env_dir, _forbidden_roots(args))
        print("=" * 88)
        print(f"session {spec.sid}: {spec.label()}")
        print(f"cwd: {tmp / 'work'}  (a copy of r{spec.realisation}/ without task.json)")
        print("env: " + " ".join(f"{k}={v}" for k, v in changed.items() if v is not None))
        removed = [k for k, v in changed.items() if v is None]
        if removed:
            print("env removed: " + " ".join(removed))
        print("mcp.json: " + json.dumps(session_mcp_config(arm, tmp / "systems")))
        print(f"limits: max-turns {limits.max_turns}, timeout {limits.timeout_s:g} s")
        print("command (prompt on stdin):")
        print("  " + format_command(cmd))
        print("prompt:")
        for line in build_prompt(spec.task, arm, spec.realisation).splitlines():
            print("  | " + line)
    print("=" * 88)
    print(f"{len(specs)} session(s); nothing was run (--dry-run)")
    return 0


def _check_manifest(tasks: list[OpsTask], args: argparse.Namespace) -> None:
    path = manifest_path(args.set)
    if not path.is_file():
        print(f"warning: no manifest {path.name}; the bundles are not checked", file=sys.stderr)
        return
    expected = parse_manifest(path.read_text(encoding="utf-8"))
    root = bundles_root(args.bundles)
    diffs = manifest_differences(
        expected, manifest_entries(root, args.set), task_manifest_prefixes(tasks)
    )
    if diffs:
        raise _fail(f"the bundles differ from {path.name}: " + "; ".join(diffs[:20]))


def _unique_tasks(tasks: Any) -> list[OpsTask]:
    return sorted({t.task_id: t for t in tasks}.values(), key=lambda t: t.task_id)


def _env_key(info: dict[str, Any] | None) -> str | None:
    return (info or {}).get("key")


def _forbidden_roots(args: argparse.Namespace) -> list[Path]:
    roots = [bundles_root(args.bundles)]
    with contextlib.suppress(ConfigError):
        roots.append(truth_root(getattr(args, "truth", None)))
    return roots


def _run_meta(args: argparse.Namespace, specs: list[SessionSpec], env_dir: Path) -> dict[str, Any]:
    info = env_info(env_dir) or {}
    return {
        "set": args.set,
        "models": sorted({s.model for s in specs}),
        "arms": sorted({s.arm for s in specs}),
        "realisations": sorted({s.realisation for s in specs}),
        "tasks": sorted({s.task.task_id for s in specs}),
        "max_turns": int(args.max_turns),
        "timeout_s": float(args.timeout),
        "effort": args.effort,
        "max_budget_usd": args.max_budget_usd,
        "claude_executable": claude_executable(),
        "claude_version": claude_version(),
        "code_plus_env": info,
        "code_plus_key": _env_key(info),
        "bundles": str(bundles_root(args.bundles)),
    }


_PINNED_KEYS = (
    "set",
    "max_turns",
    "timeout_s",
    "effort",
    "max_budget_usd",
    "claude_version",
    "code_plus_key",
)


def cmd_run(args: argparse.Namespace) -> int:
    if args.rerun_flagged or args.rerun:
        return _cmd_rerun(args)
    specs = _specs(args)
    if args.dry_run:
        return dry_run(specs, args)
    limits = _limits(args)
    if not args.no_manifest_check:
        _check_manifest(_unique_tasks(s.task for s in specs), args)
    exe = claude_executable()
    if shutil.which(exe) is None and not Path(exe).is_file():
        raise _fail(f"the Claude CLI {exe!r} was not found (set WPBENCH_CLAUDE)")
    run_dir = Path(args.out) if args.out else RESULTS_DIR / _run_id(args.set)
    env_dir = ensure_code_plus_env(args.code_env)
    meta = _run_meta(args, specs, env_dir)
    previous = read_json(run_dir / RUN_FILE, {})
    if previous:
        changed = [k for k in _PINNED_KEYS if (previous.get(k) or None) != (meta.get(k) or None)]
        if changed:
            raise _fail(
                f"run {run_dir} was started with other settings ({', '.join(changed)}); "
                "all sessions of a run use the same ones: start a new run (--out)"
            )
        for k in ("models", "arms", "realisations", "tasks"):
            meta[k] = sorted(set(previous.get(k) or []) | set(meta[k]))
        meta["started"] = previous.get("started")
        meta["resumed"] = datetime.now().isoformat(timespec="seconds")
    else:
        meta["started"] = datetime.now().isoformat(timespec="seconds")
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / RUN_FILE, meta)
    index = read_json(run_dir / INDEX_FILE, {})
    for s in specs:
        index[s.sid] = s.index_entry()
    write_json(run_dir / INDEX_FILE, index)
    todo = [(s, 1) for s in specs if attempts_done(run_dir, s.sid) == 0]
    for s in specs:
        if attempts_done(run_dir, s.sid):
            print(f"skip {s.sid} {s.label()} (already run; use --rerun-flagged after audit)")
    return _execute_all(todo, run_dir, limits, env_dir, args)


def _run_id(set_name: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{set_name}"


def interleave_by_task(todo: list[tuple[SessionSpec, int]]) -> list[tuple[SessionSpec, int]]:
    """``todo`` reordered round-robin over tasks (each task's items keep their order), so
    that parallel workers take different tasks."""
    groups: dict[str, list[tuple[SessionSpec, int]]] = {}
    for item in todo:
        groups.setdefault(item[0].task.task_id, []).append(item)
    out: list[tuple[SessionSpec, int]] = []
    queues = list(groups.values())
    for i in range(max((len(q) for q in queues), default=0)):
        out += [q[i] for q in queues if i < len(q)]
    return out


def _execute_all(
    todo: list[tuple[SessionSpec, int]],
    run_dir: Path,
    limits: SessionLimits,
    env_dir: Path,
    args: argparse.Namespace,
) -> int:
    print(f"run {run_dir}: {len(todo)} session attempt(s)")
    stop: list[str] = []
    forbidden = _forbidden_roots(args)
    key = _env_key(env_info(env_dir))
    # Sessions of one task never overlap: a session cannot read another's working
    # directory on the same task (they are removed when a session ends).
    locks = {spec.task.task_id: threading.Lock() for spec, _ in todo}

    def one(item: tuple[SessionSpec, int]) -> None:
        spec, attempt = item
        with locks[spec.task.task_id]:
            if stop:
                return
            adir = session_dir(run_dir, spec.sid) / f"attempt-{attempt}"
            prompt = build_prompt(spec.task, spec.arm, spec.realisation)
            outcome = execute_session(
                spec,
                adir,
                prompt,
                limits,
                env_dir,
                args.effort,
                args.max_budget_usd,
                keep_tmp=args.keep_tmp,
                forbidden_roots=forbidden,
                env_key=key,
            )
        sigs = audit_attempt(adir)
        why = stop_reason(adir)
        if why:
            stop.append(f"{why} in {spec.sid}")
        s = parse_stream((adir / "stream.jsonl").read_text(encoding="utf-8", errors="replace"))
        cost = "n/a" if s.cost_usd is None else f"${s.cost_usd:.3f}"
        print(
            f"done {spec.sid} {spec.label()} attempt {attempt}: {s.num_turns} turns, {cost}, "
            f"{outcome['wall_s']:.0f} s{', TIMEOUT' if outcome['timed_out'] else ''}"
            f"{' [infrastructure signature: ' + ', '.join(sigs) + ']' if sigs else ''}",
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=max(1, int(args.jobs))) as pool:
        list(pool.map(one, interleave_by_task(todo)))
    if stop:
        print(
            f"STOPPED after {stop[0]}: fix it (for a usage or rate limit, wait until it has "
            "reset), then resume with the same --out (sessions not yet started run then; "
            "flagged ones need --rerun-flagged)."
        )
    doc = audit_run(run_dir)
    for line in audit_lines(doc):
        print(line)
    return 0


def _cmd_rerun(args: argparse.Namespace) -> int:
    """Re-run flagged sessions (``--rerun-flagged``) or named ones (``--rerun SID ...``,
    for a defect found without grades), each with its own realisation, as a new attempt."""
    if not args.out:
        raise _fail("--rerun-flagged and --rerun need the run directory (--out)")
    run_dir = _run_dir(args.out)
    meta = read_json(run_dir / RUN_FILE, {})
    index = read_json(run_dir / INDEX_FILE, {})
    if not meta or not index:
        raise _fail(f"{run_dir} has no run.json or index.json")
    doc = audit_run(run_dir)
    targets = list(doc["pending_reruns"]) if args.rerun_flagged else []
    for sid in _split(args.rerun):
        if sid not in index:
            raise _fail(f"unknown session {sid}")
        if sid not in targets:
            targets.append(sid)
    for p in ("max_turns", "timeout_s", "effort", "max_budget_usd"):
        setattr(args, {"timeout_s": "timeout"}.get(p, p), meta.get(p))
    args.set = meta["set"]
    args.bundles = args.bundles or meta.get("bundles")
    if claude_version() != meta.get("claude_version"):
        raise _fail("the Claude CLI version differs from the run's; re-runs must use the same")
    limits = SessionLimits(int(meta["max_turns"]), float(meta["timeout_s"]))
    env_dir = ensure_code_plus_env(args.code_env)
    if _env_key(env_info(env_dir)) != meta.get("code_plus_key"):
        raise _fail("the code-plus environment differs from the run's")
    todo: list[tuple[SessionSpec, int]] = []
    root = bundles_root(args.bundles)
    for sid in targets:
        e = index[sid]
        n = attempts_done(run_dir, sid)
        if n >= MAX_ATTEMPTS:
            print(f"skip {sid}: already {n} attempts (at most {MAX_ATTEMPTS}); graded as is")
            continue
        task = load_task(root / e["set"] / e["task"], e["set"])
        spec = SessionSpec(e["set"], e["model"], task, e["arm"], int(e["realisation"]))
        if spec.sid != sid:
            raise _fail(f"index entry of {sid} does not match its id")
        todo.append((spec, n + 1))
    if not args.no_manifest_check:
        _check_manifest(_unique_tasks(s.task for s, _ in todo), args)
    return _execute_all(todo, run_dir, limits, env_dir, args)


# ----------------------------------------------------------------------------------------
# audit, grade, report
# ----------------------------------------------------------------------------------------
def cmd_audit(args: argparse.Namespace) -> int:
    doc = audit_run(_run_dir(args.run))
    for line in audit_lines(doc):
        print(line)
    return 0


def grade_session(
    run_dir: Path,
    sid: str,
    entry: dict[str, Any],
    task: OpsTask,
    truth: dict[str, Any],
    audit_doc: dict[str, Any],
    bundles: Path | None,
    truth_dir: Path | None,
    write: bool = True,
) -> dict[str, Any] | None:
    """Grade the latest attempt of a session and write its record.json (None: not run).

    The answer is read from the session's final reply only (:func:`final_reply`): a
    session that ended without a success result (a timeout or a kill before the result,
    the turn limit, the budget cap, an error) has none and fails. ``write=False`` writes
    nothing (``grade --pass-fail``).
    """
    attempts = attempt_dirs(session_dir(run_dir, sid))
    if not attempts:
        return None
    adir = attempts[-1]
    text = (adir / "stream.jsonl").read_text(encoding="utf-8", errors="replace")
    s = parse_stream(text)
    outcome = read_json(adir / "outcome.json", {}) or {}
    final, no_reply = final_reply(text, outcome)
    g = grade_answer(task, truth, final, tool_numbers_of(s.tool_result_texts))
    if no_reply:
        g.parse_error = f"no final reply: {no_reply}"
    a = audit_doc["sessions"].get(sid) or {}
    reasons = contamination(s, entry["arm"], bundles, truth_dir, own_tmp=outcome.get("tmp_dir"))
    duration = (s.duration_ms / 1000.0) if s.duration_ms is not None else outcome.get("wall_s")
    gd = g.to_dict()
    gd.pop("task")
    record = {
        "sid": sid,
        "set": entry["set"],
        "model": entry["model"],
        "model_id": s.model,
        "task": task.task_id,
        "family": task.family,
        "generator": task.generator,
        "cell": task.cell,
        "stratum": task.stratum,
        "arm": entry["arm"],
        "realisation": int(entry["realisation"]),
        "attempt": len(attempts),
        **gd,
        "final_reply": final is not None,
        "no_final_reply": no_reply,
        "tokens": s.total_tokens,
        "usage": s.usage,
        "cost_usd": s.cost_usd,
        "turns": s.num_turns,
        "duration_s": duration,
        "wall_s": outcome.get("wall_s"),
        "timed_out": bool(outcome.get("timed_out")),
        "exit_code": outcome.get("exit_code"),
        "result_subtype": s.result_subtype,
        "tool_calls": s.tool_calls,
        "infra_error": a.get("signatures") or None,
        "rerun_allowed": bool(a.get("rerun_allowed")),
        "contamination": reasons or None,
        "attempt_dir": str(adir),
    }
    if write:
        (adir / "final.txt").write_text(final or "", encoding="utf-8")
        write_json(session_dir(run_dir, sid) / RECORD_FILE, record)
    return record


def cmd_grade(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run)
    index = read_json(run_dir / INDEX_FILE, {})
    meta = read_json(run_dir / RUN_FILE, {})
    if not index:
        raise _fail(f"{run_dir} has no index.json")
    doc = audit_run(run_dir)  # always before any grade
    for line in audit_lines(doc):
        print(line)
    try:
        troot = truth_root(args.truth)
    except ConfigError as exc:
        raise _fail(str(exc)) from None
    broot = bundles_root(args.bundles or meta.get("bundles"))
    tasks: dict[tuple[str, str], OpsTask] = {}
    truths: dict[tuple[str, str], dict[str, Any]] = {}
    n = 0
    not_run = 0
    verdicts: list[str] = []
    for sid, entry in sorted(index.items()):
        key = (entry["set"], entry["task"])
        try:
            if key not in tasks:
                tasks[key] = load_task(broot / entry["set"] / entry["task"], entry["set"])
                truths[key] = load_truth(tasks[key], troot)
        except BundleError as exc:
            raise _fail(str(exc)) from None
        rec = grade_session(
            run_dir, sid, entry, tasks[key], truths[key], doc, broot, troot,
            write=not args.pass_fail,
        )  # fmt: skip
        if rec is None:
            not_run += 1
            continue
        n += 1
        verdicts.append(f"{sid} {'PASS' if rec['passed'] else 'FAIL'}")
    if args.pass_fail:
        # Pass or fail only (section 7, firewall): nothing else the grader computed.
        for line in verdicts:
            print(line)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(run_dir / PASS_FAIL_LOG, "a", encoding="utf-8") as log:
            log.writelines(f"{stamp} {line}\n" for line in verdicts)
        print(f"{n} session(s) evaluated; {not_run} not run; logged in {PASS_FAIL_LOG}")
        return 0
    s = write_report(run_dir)
    print(f"graded {n} session(s); {not_run} not run; report: {run_dir / 'summary.md'}")
    if s["pending_reruns"]:
        n_wait = len(s["pending_reruns"])
        print(f"{n_wait} session(s) wait for a re-run: the grades are provisional")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args.run)
    s = write_report(run_dir)
    if args.print:
        print(to_markdown(s))
    else:
        print(f"wrote {run_dir / 'summary.md'} and summary.json")
    return 0


# ----------------------------------------------------------------------------------------
# headroom
# ----------------------------------------------------------------------------------------
def cmd_headroom(args: argparse.Namespace) -> int:
    run_dirs = [_run_dir(r) for r in args.runs]
    records = load_records(run_dirs)
    models = {"Sonnet 5": args.sonnet, "Opus 5.5": args.opus}
    try:
        tasks = load_tasks(HEADROOM_SET, args.bundles)
        troot = truth_root(args.truth)
        truths = {t.task_id: load_truth(t, troot) for t in tasks}
    except (BundleError, ConfigError) as exc:
        raise _fail(str(exc)) from None
    res = headroom(
        records, [t.task_id for t in tasks], truths, models, set_problems(tasks, HEADROOM_SET)
    )
    for line in headroom_lines(res):
        print(line)
    out = {
        "computed": res.computed,
        "not_computed": res.not_computed,
        "models": models,
        "model_ids": res.model_ids,
        "F": res.F,
        "failing_tasks": res.failing,
        "closed": res.closed,
        "per_task": res.per_task,
        "warnings": res.warnings,
        "runs": [str(d) for d in run_dirs],
    }
    if args.json:
        write_json(Path(args.json), out)
    return 0 if res.computed else 2


# ----------------------------------------------------------------------------------------
# manifest and environment
# ----------------------------------------------------------------------------------------
def cmd_manifest(args: argparse.Namespace) -> int:
    root = bundles_root(args.bundles)
    out = Path(args.out) if args.out else manifest_path(args.set)
    try:
        entries = manifest_entries(root, args.set)
    except ConfigError as exc:
        raise _fail(str(exc)) from None
    if args.check:
        if not out.is_file():
            raise _fail(f"no manifest {out}")
        diffs = manifest_differences(parse_manifest(out.read_text(encoding="utf-8")), entries)
        for d in diffs:
            print(d)
        print(f"{len(entries)} file(s); {len(diffs)} difference(s) from {out.name}")
        return 1 if diffs else 0
    out.write_bytes(format_manifest(entries).encode("utf-8"))
    print(f"wrote {out} ({len(entries)} files of {root / args.set})")
    return 0


def cmd_code_env(args: argparse.Namespace) -> int:
    d = ensure_code_plus_env(args.code_env)
    print(d)
    print(json.dumps(env_info(d), indent=2))
    return 0


# ----------------------------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------------------------
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.operations.harness",
        description="worldparts operations benchmark (v0.3): runs, audit, grading, headroom.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("run", help="Run sessions (never grades); then the blind audit.")
    sp.add_argument("--set", choices=SETS, default="dev")
    sp.add_argument(
        "--arms",
        nargs="*",
        help=f"Arms (default {' '.join(STAGE0_ARMS)}; registered: {', '.join(ARMS)}).",
    )
    sp.add_argument("--models", nargs="*", help=f"Models (default {' '.join(DEFAULT_MODELS)}).")
    sp.add_argument("--realisation", nargs="*", help="Realisation number(s) k of r<k> (default 1).")
    sp.add_argument("--tasks", nargs="*", help="Task ids or glob patterns (default: all).")
    sp.add_argument("--jobs", type=int, default=1, help="Sessions in parallel.")
    sp.add_argument("--out", default=None, help="Run directory (default results/<stamp>-<set>).")
    sp.add_argument("--dry-run", action="store_true", help="Print the commands; run nothing.")
    sp.add_argument("--max-turns", type=int, default=PINNED_LIMITS.max_turns)
    sp.add_argument("--timeout", type=float, default=PINNED_LIMITS.timeout_s, help="Seconds.")
    sp.add_argument("--effort", default=None, help="--effort passed to the CLI (same per model).")
    sp.add_argument("--max-budget-usd", type=float, default=None, help="Per session.")
    sp.add_argument("--bundles", default=None, help="Bundle folder ($WPBENCH_OPS_BUNDLES).")
    sp.add_argument("--code-env", type=Path, default=None, help="code-plus environment dir.")
    sp.add_argument(
        "--keep-tmp",
        action="store_true",
        help="Keep the temporary directories (debugging only: a later session could read "
        "them; the contamination markers flag one that does).",
    )
    sp.add_argument(
        "--rerun-flagged",
        action="store_true",
        help="Re-run the sessions the blind audit flags (same realisation; at most twice).",
    )
    sp.add_argument(
        "--rerun", nargs="*", help="Re-run these session ids (a defect found without grades)."
    )
    sp.add_argument(
        "--no-manifest-check", action="store_true", help="Do not check the bundle manifest."
    )
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("audit", help="Blind infrastructure audit (reads no grades or arms).")
    sp.add_argument("run")
    sp.set_defaults(fn=cmd_audit)

    sp = sub.add_parser("grade", help="Audit, then grade the latest attempt of every session.")
    sp.add_argument("run")
    sp.add_argument("--truth", default=None, help="Truth folder ($WPBENCH_OPS_TRUTH).")
    sp.add_argument("--bundles", default=None, help="Bundle folder (default: the run's).")
    sp.add_argument(
        "--pass-fail",
        action="store_true",
        help="Print only pass or fail per session, write no record and log the evaluation "
        "(the only grader output a firewalled session may receive).",
    )
    sp.set_defaults(fn=cmd_grade)

    sp = sub.add_parser("headroom", help="The Stage 0 headroom rule on graded run(s).")
    sp.add_argument("runs", nargs="+")
    sp.add_argument("--sonnet", default="sonnet", help="Model string of Sonnet 5 in the runs.")
    sp.add_argument("--opus", default="opus", help="Model string of Opus 5.5 in the runs.")
    sp.add_argument("--truth", default=None)
    sp.add_argument("--bundles", default=None)
    sp.add_argument("--json", default=None, help="Also write the result to this JSON file.")
    sp.set_defaults(fn=cmd_headroom)

    sp = sub.add_parser("report", help="Write summary.md and summary.json.")
    sp.add_argument("run")
    sp.add_argument("--print", action="store_true")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("manifest", help="Write (or --check) bundles-<set>.sha256.")
    sp.add_argument("--set", choices=SETS, default="dev")
    sp.add_argument("--bundles", default=None)
    sp.add_argument("--out", default=None, help="Manifest path (default in the repository).")
    sp.add_argument("--check", action="store_true")
    sp.set_defaults(fn=cmd_manifest)

    sp = sub.add_parser("code-env", help="Build or check the code-plus environment.")
    sp.add_argument("--code-env", type=Path, default=None)
    sp.set_defaults(fn=cmd_code_env)
    return p


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    args = parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
