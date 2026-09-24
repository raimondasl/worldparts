"""The arms of PREREGISTRATION.md section 3, their preambles and the session prompt.

The session prompt (stdin) is, in this order (INTERFACE.md): the text of ``task.md``, the
arm's preamble and the answer-format instruction (:func:`answer_format_instruction`). The
preamble is built from files in ``benchmarks/operations/preambles/`` joined by blank lines:

- ``code+``: ``environment.txt`` (the tools and the package list);
- ``code-hint``: ``environment.txt`` and ``checklist.txt`` (the methods checklist of
  section 3.1, verbatim).

So ``code-hint`` differs from ``code+`` only by the checklist. Both run with the tools Bash,
Read, Write and Edit and the ``code-plus`` Python environment (:mod:`.env`).

``code-skill``, ``lib``, ``lib-directed`` and ``mcp-hybrid`` are registered with their
pre-registered sentences but are not available before freeze-1 (section 7): their toolkit,
quick reference, wheel and preambles do not exist yet. Adding one means writing its
preamble files, setting ``available=True`` and, for the lib arms, adding the lib
environment to :func:`benchmarks.operations.harness.env.python_env_for`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


_FREEZE1 = "not available before freeze-1 (PREREGISTRATION.md section 7)"

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
        ("reference",),
    ),
    "lib-directed": Arm(
        "lib-directed", (1,), "lib", CODE_TOOLS, CODE_ALLOWED, False, "lib",
        ("environment-lib.txt", "checklist.txt", "quick-reference.txt", "lib-directed.txt"),
        False, f"{_FREEZE1}: the wheel, the quick reference and the preamble are frozen then",
    ),
    "lib": Arm(
        "lib", (1,), "lib", CODE_TOOLS, CODE_ALLOWED, False, "lib",
        ("environment-lib.txt", "quick-reference.txt"),
        False, f"{_FREEZE1}: the wheel, the quick reference and the preamble are frozen then",
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


def require_available(names: list[str]) -> list[Arm]:
    """The arms ``names``; ValueError naming each one that cannot run yet."""
    arms = [get_arm(n) for n in names]
    blocked = [f"{a.name}: {a.unavailable_reason}" for a in arms if not a.available]
    if blocked:
        raise ValueError("; ".join(blocked))
    return arms


def read_preamble_file(name: str, preambles_dir: Path = PREAMBLES_DIR) -> str:
    """A preamble file's text without its final line break."""
    return (preambles_dir / name).read_text(encoding="utf-8").rstrip("\n")


def preamble(arm: Arm | str, preambles_dir: Path = PREAMBLES_DIR) -> str:
    """The arm's preamble: its files joined by blank lines."""
    a = get_arm(arm) if isinstance(arm, str) else arm
    if not a.available:
        raise ValueError(f"arm {a.name}: {a.unavailable_reason}")
    return "\n\n".join(read_preamble_file(n, preambles_dir) for n in a.preamble_files)


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


def build_prompt(task: OpsTask, arm: Arm | str, realisation: int) -> str:
    """task.md text + the arm's preamble + the answer-format instruction."""
    return (
        task.task_md(realisation).rstrip()
        + "\n\n"
        + preamble(arm)
        + "\n\n"
        + answer_format_instruction(task)
        + "\n"
    )
