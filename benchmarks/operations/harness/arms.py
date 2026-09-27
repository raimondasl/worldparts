"""The arms of PREREGISTRATION.md section 3, their preambles and the session prompt.

The session prompt (stdin) is, in this order (INTERFACE.md): the text of ``task.md``, the
arm's preamble and the answer-format instruction (:func:`answer_format_instruction`). The
preamble is built from files in ``benchmarks/operations/preambles/`` joined by blank lines:

- ``code+``: ``environment.txt`` (the tools and the package list);
- ``code-hint``: ``environment.txt`` and ``checklist.txt`` (the methods checklist of
  section 3.1, verbatim).

So ``code-hint`` differs from ``code+`` only by the checklist. Both run with the tools Bash,
Read, Write and Edit and the ``code-plus`` Python environment (:mod:`.env`).

The arms of Stage 1 (section 3), with the same tools:

- ``code-skill``: ``environment.txt``, ``checklist.txt`` and ``code-skill.txt`` (the
  pre-registered sentence), the code-plus environment, and the toolkit copied into the
  working directory as ``reference/`` (:mod:`.toolkit`);
- ``lib-directed``: ``environment-lib.txt`` (the same text naming worldparts among the
  packages), ``checklist.txt``, ``quick-reference.txt`` and ``lib-directed.txt`` (the
  directive), and the lib environment (:func:`.env.ensure_lib_env`);
- ``lib``: ``environment-lib.txt`` and ``quick-reference.txt``, and the lib environment;
- ``mcp-hybrid``: its preamble does not exist yet.

They are not available before freeze-1 (section 7), with one exception: the readiness runs
of section 3 ("before the test seed is drawn, ``lib-directed`` and ``code-skill`` each run
once on the development set with Sonnet 5"), for which ``code-skill``, ``lib-directed``
and ``lib`` run on the development set with ``run --readiness`` (``readiness=True``
here). ``mcp-hybrid`` stays unavailable. The quick reference is checked
(:func:`.quickref.quick_reference_problems`) whenever a preamble uses it, and its ``#!``
annotations are never shown to the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import quickref
from .bundles import PREAMBLES_DIR, OpsTask

#: Tools of every code arm (``--tools``) and their permission rules (``--allowedTools``).
CODE_TOOLS = "Bash,Read,Write,Edit"
CODE_ALLOWED = ("Bash", "Read", "Write", "Edit")

#: The sentence the code-skill preamble adds (section 3, verbatim).
CODE_SKILL_SENTENCE = "Adapt and use the tools in reference/ for this plant."
#: The lib-directed directive (section 3, verbatim).
LIB_DIRECTED_DIRECTIVE = (
    "Use worldparts for the plant model, calibration, identifiability and diagnosis where it "
    "applies; use any other package for data handling, forecasting and anything worldparts "
    "does not model."
)


@dataclass(frozen=True)
class Arm:
    """One arm: what its sessions get besides the task bundle."""

    name: str
    stages: tuple[int, ...]
    #: The Python environment: "code-plus" (numpy ... matplotlib) or "lib" (plus worldparts).
    env: str
    tools: str
    allowed_tools: tuple[str, ...]
    #: Whether the worldparts MCP server is attached (mcp-hybrid).
    mcp: bool
    #: The v0.2 contamination condition whose repository and library markers apply:
    #: "code" (no worldparts anywhere), "lib" (the installed package is allowed), "mcp".
    contamination_condition: str
    #: Preamble files in ``preambles/``, joined by blank lines.
    preamble_files: tuple[str, ...]
    available: bool
    unavailable_reason: str | None = None
    #: Directories copied into the working directory besides the bundle (code-skill:
    #: its toolkit as ``reference/``); empty for the Stage 0 arms.
    workdir_extras: tuple[str, ...] = ()
    #: Whether a readiness run on the development set may use it before freeze-1
    #: (``run --readiness``, section 3 "Readiness").
    readiness: bool = False


_FREEZE1 = "not available before freeze-1 (PREREGISTRATION.md section 7)"
#: The arms of the readiness runs (section 3), the default of ``run --readiness``.
READINESS_ARMS = ("lib-directed", "code-skill")

ARMS: dict[str, Arm] = {
    "code+": Arm(
        "code+", (0, 1), "code-plus", CODE_TOOLS, CODE_ALLOWED, False, "code",
        ("environment.txt",), True,
    ),
    "code-hint": Arm(
        "code-hint", (0, 1), "code-plus", CODE_TOOLS, CODE_ALLOWED, False, "code",
        ("environment.txt", "checklist.txt"), True,
    ),
    "code-skill": Arm(
        "code-skill", (1,), "code-plus", CODE_TOOLS, CODE_ALLOWED, False, "code",
        ("environment.txt", "checklist.txt", "code-skill.txt"), False,
        f"{_FREEZE1}: the reference toolkit is written after Stage 0",
        ("reference",), True,
    ),
    "lib-directed": Arm(
        "lib-directed", (1,), "lib", CODE_TOOLS, CODE_ALLOWED, False, "lib",
        ("environment-lib.txt", "checklist.txt", "quick-reference.txt", "lib-directed.txt"),
        False, f"{_FREEZE1}: the wheel, the quick reference and the preamble are frozen then",
        (), True,
    ),
    "lib": Arm(
        "lib", (1,), "lib", CODE_TOOLS, CODE_ALLOWED, False, "lib",
        ("environment-lib.txt", "quick-reference.txt"),
        False, f"{_FREEZE1}: the wheel, the quick reference and the preamble are frozen then",
        (), True,
    ),
    "mcp-hybrid": Arm(
        "mcp-hybrid", (1,), "code-plus", CODE_TOOLS, CODE_ALLOWED, True, "mcp",
        ("environment-mcp-hybrid.txt",),
        False, f"{_FREEZE1}: only for PIVOT-small; its preamble is frozen then",
    ),
}  # fmt: skip

#: Arms that run in Stage 0 (section 8).
STAGE0_ARMS = ("code+", "code-hint")
#: The arm the Stage 0 headroom rule reads.
HEADROOM_ARM = "code-hint"


def get_arm(name: str) -> Arm:
    """The registered arm ``name`` (ValueError for an unknown one)."""
    try:
        return ARMS[name]
    except KeyError:
        raise ValueError(f"unknown arm {name!r} (arms: {', '.join(ARMS)})") from None


def runnable(arm: Arm, readiness: bool = False) -> bool:
    """Whether ``arm`` can run now: available, or a readiness arm in a readiness run."""
    return arm.available or (readiness and arm.readiness)


def _blocked_reason(arm: Arm) -> str:
    extra = (
        " (before then, a readiness run on the development set can use it: run --readiness)"
        if arm.readiness
        else ""
    )
    return f"{arm.name}: {arm.unavailable_reason}{extra}"


def require_available(names: list[str], readiness: bool = False) -> list[Arm]:
    """The arms ``names``; ValueError naming each one that cannot run yet. With
    ``readiness``, the readiness arms (code-skill, lib-directed, lib) can run too."""
    arms = [get_arm(n) for n in names]
    blocked = [_blocked_reason(a) for a in arms if not runnable(a, readiness)]
    if blocked:
        raise ValueError("; ".join(blocked))
    return arms


def read_preamble_file(name: str, preambles_dir: Path | None = None) -> str:
    """A preamble file's text without its final line break."""
    return ((preambles_dir or PREAMBLES_DIR) / name).read_text(encoding="utf-8").rstrip("\n")


def preamble_problems(arm: Arm | str, preambles_dir: Path | None = None) -> list[str]:
    """Why the arm's preamble cannot be built, or ``[]``: a missing file, or a quick
    reference that breaks section 3's limits (:mod:`.quickref`)."""
    a = get_arm(arm) if isinstance(arm, str) else arm
    d = preambles_dir or PREAMBLES_DIR
    missing = [n for n in a.preamble_files if not (d / n).is_file()]
    if missing:
        return [f"preamble file(s) missing in {d}: {', '.join(missing)}"]
    if quickref.QUICK_REFERENCE not in a.preamble_files:
        return []
    return [
        f"{quickref.QUICK_REFERENCE}: {p}"
        for p in quickref.quick_reference_problems(
            (d / quickref.QUICK_REFERENCE).read_text(encoding="utf-8"),
            read_preamble_file("checklist.txt", d),
        )
    ]


def preamble(arm: Arm | str, preambles_dir: Path | None = None, readiness: bool = False) -> str:
    """The arm's preamble: its files joined by blank lines (a quick reference without its
    ``#!`` annotations)."""
    a = get_arm(arm) if isinstance(arm, str) else arm
    if not runnable(a, readiness):
        raise ValueError(f"arm {_blocked_reason(a)}")
    problems = preamble_problems(a, preambles_dir)
    if problems:
        raise ValueError(f"arm {a.name}: " + "; ".join(problems))
    parts = []
    for n in a.preamble_files:
        text = read_preamble_file(n, preambles_dir)
        parts.append(quickref.shown_text(text) if n == quickref.QUICK_REFERENCE else text)
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------------------
# answer-format instruction
# ----------------------------------------------------------------------------------------
_KIND_LABEL = {
    "estimate": "estimate",
    "estimate_or_undetermined": "estimate or cannot_determine",
    "boolean": "boolean",
    "choice": "choice",
    "set": "set",
    "diagnosis": "diagnosis",
}
_KIND_FORMAT = {
    "estimate": '- estimate: {"value": x, "lo90": a, "hi90": b}, a point estimate and its '
    "90 % interval, in the key's unit.",
    "estimate_or_undetermined": "- estimate or cannot_determine: an estimate as above, or the "
    'string "cannot_determine" when the data cannot determine the quantity.',
    "boolean": "- boolean: true or false.",
    "choice": "- choice: one of the listed options, as a string.",
    "set": "- set: a JSON list of listed options (it may be empty).",
    "diagnosis": '- diagnosis: {"verdict": "identified" | "ambiguous" | "no_fault", '
    '"faults": [...], "magnitudes": {"<fault>": {"value": x, "lo90": a, "hi90": b}}, '
    '"resolving_measurement": "<measurement>"}; magnitudes, in each fault\'s unit, are '
    "required for identified, and resolving_measurement for ambiguous.",
}
_KIND_ORDER = tuple(_KIND_FORMAT)


def _key_hint(spec: Any) -> str:
    label = _KIND_LABEL[spec.kind]
    if spec.kind in ("estimate", "estimate_or_undetermined") and spec.unit:
        return f"{spec.key} ({label}, {spec.unit})"
    if spec.kind in ("choice", "set"):
        return f"{spec.key} ({label} of: {', '.join(spec.options)})"
    return f"{spec.key} ({label})"


def answer_format_instruction(task: OpsTask) -> str:
    """The instruction appended to every prompt: the keys of ``task.json`` with their kinds
    (and the options of choices and sets, which task.md lists as well) and the format of
    each kind the task uses. It carries nothing that task.md does not state."""
    kinds = [k for k in _KIND_ORDER if any(s.kind == k for s in task.keys.values())]
    lines = [
        "Answer format: end your final reply with one fenced ```json block holding a single "
        "JSON object with exactly these keys: "
        + "; ".join(_key_hint(s) for s in task.keys.values())
        + ".",
        "Formats:",
        *(_KIND_FORMAT[k] for k in kinds),
        "Use the units, plausible ranges and names stated in the task above.",
    ]
    return "\n".join(lines)


def build_prompt(
    task: OpsTask,
    arm: Arm | str,
    realisation: int,
    readiness: bool = False,
    preambles_dir: Path | None = None,
) -> str:
    """task.md text + the arm's preamble + the answer-format instruction."""
    return (
        task.task_md(realisation).rstrip()
        + "\n\n"
        + preamble(arm, preambles_dir, readiness)
        + "\n\n"
        + answer_format_instruction(task)
        + "\n"
    )
