"""Recompute every task's ``reference.expected`` from its reference steps.

Usage, from the repository root::

    uv run python -m benchmarks.composition.harness.regen            # write expected
    uv run python -m benchmarks.composition.harness.regen --check    # fail on drift

Writing replaces only the ``expected:`` entry of the ``reference`` block in the YAML text
(as a one-line flow mapping), so comments and formatting elsewhere in the file survive.
``--check`` exits with status 1 when any task is invalid, fails to run, or has an
``expected`` that differs from what its steps produce by more than 1e-6 relative.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from .reference import ReferenceStepError, run_reference, same_value
from .tasks import TASKS_DIR, Task, TaskError, load_task, task_files

SIGNIFICANT = 10  # digits written for numeric expected values


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        x = float(f"{float(value):.{SIGNIFICANT}g}")
        if x.is_integer() and abs(x) < 1e15:
            return repr(int(x))
        text = repr(x)
        mantissa, e, exponent = text.partition("e")
        if e and "." not in mantissa:  # YAML 1.1 needs a dot: 1.0e-05, not 1e-05
            text = f"{mantissa}.0e{exponent}"
        return text
    return yaml.safe_dump(value, default_style=None).strip().removesuffix("...").strip()


def expected_flow(values: dict[str, Any], keys: list[str]) -> str:
    """``{key: value, ...}`` in answer order."""
    return "{" + ", ".join(f"{k}: {_fmt(values[k])}" for k in keys) + "}"


def rounded(values: dict[str, Any]) -> dict[str, Any]:
    """The values exactly as written to the file."""
    return {k: yaml.safe_load(_fmt(v)) for k, v in values.items()}


def replace_expected(text: str, flow: str) -> str:
    """Put ``expected: <flow>`` into the top-level ``reference`` block of a task's YAML.

    Replaces an existing ``expected`` entry (flow or block style, with its indented
    continuation lines) or appends one at the end of the ``reference`` block.
    """
    lines = text.splitlines(keepends=True)
    ref = next((i for i, ln in enumerate(lines) if re.match(r"^reference:\s*(#.*)?$", ln)), None)
    if ref is None:
        raise ValueError("no top-level 'reference:' block")
    # The reference block: following lines that are indented, blank or comments.
    end = ref + 1
    child_indent: int | None = None
    while end < len(lines):
        ln = lines[end]
        if ln.strip() and not ln[0].isspace():
            break
        if ln.strip() and child_indent is None:
            child_indent = len(ln) - len(ln.lstrip(" "))
        end += 1
    if child_indent is None:
        raise ValueError("the 'reference:' block is empty")
    pad = " " * child_indent
    new_line = f"{pad}expected: {flow}\n"
    for i in range(ref + 1, end):
        ln = lines[i]
        if ln.startswith(pad + "expected:") and not ln[child_indent].isspace():
            j = i + 1
            while j < end and (not lines[j].strip() or _indent(lines[j]) > child_indent):
                j += 1
            # Keep trailing blank lines that separate the next entry.
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            return "".join([*lines[:i], new_line, *lines[j:]])
    # Append after the last non-blank line of the block.
    last = end
    while last > ref + 1 and not lines[last - 1].strip():
        last -= 1
    if last > 0 and not lines[last - 1].endswith("\n"):
        lines[last - 1] += "\n"
    return "".join([*lines[:last], new_line, *lines[last:]])


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def drift(task: Task, produced: dict[str, Any]) -> list[str]:
    """Differences between the task's expected answers and ``produced``."""
    if task.expected is None:
        return ["no expected values"]
    out = []
    for k in task.keys:
        e, p = task.expected.get(k), produced[k]
        if not same_value(e, p):
            out.append(f"{k}: expected {e!r}, steps give {p!r}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.composition.harness.regen",
        description="Recompute reference.expected for every task (or check it).",
    )
    parser.add_argument("--check", action="store_true", help="Fail if expected drifted.")
    parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    parser.add_argument("--tasks", nargs="*", help="Task ids or glob patterns (default: all).")
    args = parser.parse_args(argv)
    return regen(args.tasks_dir, args.tasks, check=args.check)


def regen(tasks_dir: Path, select: list[str] | None = None, check: bool = False) -> int:
    """Regenerate or check; returns the process exit status."""
    import fnmatch

    files = [
        p
        for p in task_files(tasks_dir)
        if not select or any(fnmatch.fnmatchcase(p.stem, s) for s in select)
    ]
    if not files:
        print(f"no task files in {tasks_dir}")
        return 0
    failures = 0
    for path in files:
        try:
            task = load_task(path)
            produced = run_reference(task)
        except (TaskError, ReferenceStepError) as exc:
            failures += 1
            problems = exc.problems if isinstance(exc, TaskError) else [str(exc)]
            print(f"FAIL {path.name}")
            for p in problems:
                print(f"     {p}")
            continue
        diffs = drift(task, produced)
        if check:
            if diffs:
                failures += 1
                print(f"DRIFT {task.id}")
                for d in diffs:
                    print(f"     {d}")
            else:
                print(f"ok   {task.id}")
            continue
        if not diffs:
            print(f"same {task.id}")
            continue
        text = path.read_text(encoding="utf-8")
        new = replace_expected(text, expected_flow(produced, task.keys))
        path.write_text(new, encoding="utf-8")
        reloaded = load_task(path)  # must still be a valid task with the new values
        assert reloaded.expected == rounded(produced), (reloaded.expected, produced)
        print(f"wrote {task.id}: {expected_flow(produced, task.keys)}")
    if failures:
        print(f"{failures} of {len(files)} task(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
