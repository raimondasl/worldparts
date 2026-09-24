"""Task files: loading, schema validation and the cross-field consistency rules.

A task is one YAML file in ``benchmarks/composition/tasks/<id>.yaml`` validated against
``benchmarks/composition/task.schema.json``. Beyond the schema, :func:`check_task` enforces:

- the id equals the file name, answer keys are unique, every answer unit parses;
- the level matches the number of components in the reference system
  (1: 2-4, 2: 5-7, 3: 8 or more);
- every answer key is produced by exactly one read or ``answer`` op, and no op produces a
  key that is not an answer;
- ``answer`` ops give a valid choice for choice answers and a boolean for boolean answers;
- ``expected`` has exactly the answer keys with values of the right kind;
- the prompt the agent sees (the task prompt plus the appended answer-format instruction,
  i.e. including the answer keys and descriptions) does not name worldparts, its component
  types, multi-word parameter, input or observable names or warning codes; the task prompt
  itself does not describe the answer format (the harness appends that);
- a ``judgement`` task has exactly two answers, ``acceptable`` (boolean) and
  ``primary_problem`` (choice among exactly :data:`JUDGEMENT_CHOICES`, in that order), both
  given by the last step, an ``answer`` op; ``acceptable`` is true exactly when
  ``primary_problem`` is ``none``; its prompt has 100-250 words and names none of the
  problems (see :func:`judgement_problems`).

Whether ``expected`` equals what the steps produce is checked by running them
(:mod:`benchmarks.composition.harness.reference`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

import worldparts as wp
from worldparts.units import same_dimension

COMPOSITION_DIR = Path(__file__).resolve().parents[1]
TASKS_DIR = COMPOSITION_DIR / "tasks"
SCHEMA_PATH = COMPOSITION_DIR / "task.schema.json"
RESULTS_DIR = COMPOSITION_DIR / "results"
REPO_ROOT = COMPOSITION_DIR.parents[1]

#: Level by component count in the reference system.
LEVEL_BOUNDS = {1: (2, 4), 2: (5, 7), 3: (8, 10**9)}

#: Task categories (the schema's enum).
CATEGORIES = ("operating_point", "sizing", "what_if", "transient", "diagnosis", "judgement")

#: The fixed choices of a judgement task's ``primary_problem`` answer, shared by every
#: judgement task (in this order) so that the choices never reveal which problem a task has.
JUDGEMENT_CHOICES = (
    "none",
    "cavitation",
    "motor_overload",
    "pump_far_from_best_efficiency",
    "pump_beyond_end_of_curve",
    "pump_below_minimum_flow",
    "insufficient_uv_dose",
    "filter_needs_cleaning",
    "excessive_pipe_velocity",
    "tank_runs_dry",
    "tank_overflows",
    "insufficient_delivery_pressure",
)

#: Words a judgement prompt may have (inclusive).
JUDGEMENT_WORDS = (100, 250)

#: Phrases that would name or hint at a judgement task's problem (matched case-insensitively
#: at the start of a word): every choice with its underscores read as spaces, plus common
#: names of the same problems. A judgement prompt gives datasheet data, never these.
JUDGEMENT_HINTS = (
    "cavitat",
    "overload",
    "best efficiency",
    "best-efficiency",
    "bep",
    "preferred operating",
    "operating region",
    "end of curve",
    "end of the curve",
    "end-of-curve",
    "runout",
    "run-out",
    "underdos",
    "under-dos",
    "run dry",
    "runs dry",
    "running dry",
    "run empty",
    "runs empty",
    "overflow",
    "insufficient",
    "excessive",
    "velocity",
    "erosion",
    "npsh available",
    "npsha",
    "margin",
    "check whether",
    "check that",
    "verify",
)

#: Default relative tolerance when an answer gives only abs_tol (and vice versa, 0).
DEFAULT_REL_TOL = 0.0
DEFAULT_ABS_TOL = 0.0


class TaskError(Exception):
    """A task file is invalid; ``problems`` lists every problem found."""

    def __init__(self, path: Path | str, problems: list[str]) -> None:
        self.path = str(path)
        self.problems = list(problems)
        super().__init__(f"{self.path}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class Answer:
    """One answer the agent must return."""

    key: str
    description: str
    kind: str = "number"
    unit: str | None = None
    rel_tol: float = DEFAULT_REL_TOL
    abs_tol: float = DEFAULT_ABS_TOL
    choices: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Answer:
        return cls(
            key=d["key"],
            description=d["description"],
            kind=d.get("kind", "number"),
            unit=d.get("unit"),
            rel_tol=float(d.get("rel_tol", DEFAULT_REL_TOL)),
            abs_tol=float(d.get("abs_tol", DEFAULT_ABS_TOL)),
            choices=tuple(d.get("choices", ())),
        )

    def tolerance(self, expected: float) -> float:
        """The pass band half-width around ``expected``: max(rel_tol*|e|, abs_tol)."""
        return max(self.rel_tol * abs(expected), self.abs_tol)

    def format_hint(self) -> str:
        """How this key is described in the appended answer-format instruction."""
        if self.kind == "choice":
            return f"{self.key} (one of: {', '.join(self.choices)}: {self.description})"
        if self.kind == "boolean":
            return f"{self.key} (true or false: {self.description})"
        unit = "dimensionless" if self.unit in ("1", "", None) else self.unit
        return f"{self.key} (number, {unit}: {self.description})"


def answer_format_instruction(answers: list[Answer]) -> str:
    """The instruction appended to every task prompt."""
    return (
        "When you are done, end your reply with a single JSON object in a ```json block "
        "with exactly these keys: "
        + ", ".join(a.format_hint() for a in answers)
        + ". Give numbers as plain JSON numbers in the stated units."
    )


def build_prompt_text(prompt: str, answers: list[Answer]) -> str:
    """Task prompt plus the generated answer-format instruction (what the agent reads)."""
    return prompt.rstrip() + "\n\n" + answer_format_instruction(answers)


def forbidden_terms_in(text: str) -> list[str]:
    """The worldparts identifiers named in ``text`` (sorted)."""
    low = text.lower()
    return [
        term
        for term in sorted(_forbidden_prompt_terms())
        if re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", low)
    ]


@dataclass
class Task:
    """A loaded, validated task."""

    id: str
    title: str
    level: int
    category: str
    domain: str
    prompt: str
    answers: list[Answer]
    system: dict[str, Any]
    steps: list[dict[str, Any]]
    expected: dict[str, Any] | None
    notes: str | None = None
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def answer(self, key: str) -> Answer:
        for a in self.answers:
            if a.key == key:
                return a
        raise KeyError(key)

    @property
    def keys(self) -> list[str]:
        return [a.key for a in self.answers]

    @property
    def component_count(self) -> int:
        return len(self.system.get("components", []))


@cache
def task_schema() -> dict[str, Any]:
    """The task JSON Schema."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_errors(data: Any) -> list[str]:
    validator = jsonschema.Draft202012Validator(task_schema())
    out = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(top)"
        out.append(f"schema: {where}: {err.message}")
    return out


@cache
def _forbidden_prompt_terms() -> frozenset[str]:
    """Identifiers that must not appear in a prompt.

    Component aliases, parameter/input/observable names and warning codes that contain an
    underscore (single words such as 'pump' or 'diameter' are ordinary English), plus the
    library name.
    """
    terms: set[str] = {"worldparts"}
    cat = wp.default_catalog()
    for m in cat:
        terms.add(m.alias)
        data = m.data
        for group in ("parameters", "inputs", "states", "observables"):
            for spec in data.get(group, []) or []:
                terms.add(spec["name"])
        for w in data.get("envelope", []) or []:
            terms.add(w["code"])
        for w in data.get("warnings", []) or []:
            terms.add(w["code"])
    return frozenset(t for t in terms if "_" in t or t == "worldparts")


_ANSWER_FORMAT_HINTS = re.compile(r"```|\bjson\b", re.IGNORECASE)


def _produced_keys(steps: list[dict[str, Any]]) -> list[tuple[int, str, Any]]:
    """(step index, key, source) for every key a step produces."""
    out: list[tuple[int, str, Any]] = []
    for i, step in enumerate(steps):
        for field_name in ("read", "read_final", "read_min", "read_max", "read_first_warning"):
            for key, src in (step.get(field_name) or {}).items():
                out.append((i, key, src))
        if step.get("op") == "answer":
            for key, value in (step.get("values") or {}).items():
                out.append((i, key, value))
    return out


def check_task(data: dict[str, Any], path: Path | None = None) -> list[str]:
    """All problems with a parsed task document (empty when it is valid)."""
    problems = _schema_errors(data)
    if problems:
        return problems
    if path is not None and path.stem != data["id"]:
        problems.append(f"id '{data['id']}' does not match the file name '{path.stem}'")

    answers = [Answer.from_dict(a) for a in data["answers"]]
    keys = [a.key for a in answers]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        problems.append(f"duplicate answer keys: {', '.join(dupes)}")
    by_key = {a.key: a for a in answers}
    for a in answers:
        if a.kind == "number":
            try:
                same_dimension(a.unit or "", a.unit or "")
            except Exception as exc:
                problems.append(f"answer {a.key}: unit '{a.unit}' does not parse ({exc})")
            if a.rel_tol == 0 and a.abs_tol == 0:
                problems.append(f"answer {a.key}: rel_tol and abs_tol are both zero")

    ref = data["reference"]
    n = len(ref["system"].get("components", []))
    lo, hi = LEVEL_BOUNDS[data["level"]]
    if not lo <= n <= hi:
        problems.append(
            f"level {data['level']} needs {lo}-{hi if hi < 10**9 else 'any'} components, the "
            f"reference system has {n}"
        )

    produced = _produced_keys(ref["steps"])
    counts: dict[str, int] = {}
    for _, key, _ in produced:
        counts[key] = counts.get(key, 0) + 1
    for key in keys:
        if counts.get(key, 0) == 0:
            problems.append(f"answer {key} is not produced by any step")
        elif counts[key] > 1:
            problems.append(f"answer {key} is produced by {counts[key]} steps (must be one)")
    for key in sorted(set(counts) - set(keys)):
        problems.append(f"steps produce '{key}', which is not an answer key")
    for i, key, src in produced:
        a = by_key.get(key)
        if a is None:
            continue
        op = ref["steps"][i]["op"]
        if op == "answer":
            problems.extend(_value_kind_problems(a, src, f"steps[{i}].values.{key}"))
        elif a.kind != "number":
            problems.append(f"answer {key} is a {a.kind} but steps[{i}] reads it from a variable")
        if op == "simulate" and isinstance(src, dict) and a.kind == "number":
            unit = src.get("unit") or "s"
            if unit != a.unit:
                problems.append(
                    f"steps[{i}].read_first_warning.{key}: unit '{unit}' differs from the "
                    f"answer unit '{a.unit}'"
                )

    expected = ref.get("expected") or None  # absent or {}: not generated yet (run regen)
    if expected is not None:
        missing = [k for k in keys if k not in expected]
        extra = [k for k in expected if k not in by_key]
        if missing:
            problems.append(f"expected lacks {', '.join(missing)}")
        if extra:
            problems.append(f"expected has unknown keys {', '.join(extra)}")
        for k, v in expected.items():
            if k in by_key:
                problems.extend(_value_kind_problems(by_key[k], v, f"expected.{k}"))

    prompt = data["prompt"]
    in_prompt = set(forbidden_terms_in(prompt))
    for term in sorted(in_prompt):
        problems.append(f"prompt names the worldparts identifier '{term}'")
    # The answer keys and descriptions reach the agent through the appended format line.
    # The fixed judgement choices are exempt: they are the same for every judgement task
    # (one of them, motor_overload, is also a warning code).
    exempt = set(JUDGEMENT_CHOICES) if data["category"] == "judgement" else set()
    for term in forbidden_terms_in(answer_format_instruction(answers)):
        if term not in in_prompt and term not in exempt:
            problems.append(
                f"an answer key or description names the worldparts identifier '{term}' "
                "(the agent sees it in the appended answer format)"
            )
    # A key such as 'npsh_available_20c' still hands over the identifier 'npsh_available'.
    for a in answers:
        for term in sorted(_forbidden_prompt_terms()):
            if f"_{term}_" in f"_{a.key.lower()}_" and a.key.lower() != term:
                problems.append(f"answer key '{a.key}' contains the worldparts identifier '{term}'")
    if _ANSWER_FORMAT_HINTS.search(prompt):
        problems.append("prompt describes an answer format (JSON); the harness appends it")
    if data["category"] == "judgement":
        problems.extend(judgement_problems(data))
    return problems


def judgement_problems(data: dict[str, Any]) -> list[str]:
    """The rules of a ``judgement`` task (the document is already schema-valid).

    - exactly two answers: ``acceptable`` (boolean) and ``primary_problem`` (choice whose
      choices are exactly :data:`JUDGEMENT_CHOICES`, in that order);
    - the last reference step is an ``answer`` op giving both (so the verdict is stated,
      not read from a variable);
    - ``acceptable`` is true exactly when ``primary_problem`` is ``none``, in that op and
      in ``expected``;
    - the prompt has :data:`JUDGEMENT_WORDS` words and contains no choice (underscores read
      as spaces) and none of :data:`JUDGEMENT_HINTS`: the question is open.
    """
    problems: list[str] = []
    answers = {a["key"]: a for a in data["answers"]}
    if [a["key"] for a in data["answers"]] != ["acceptable", "primary_problem"]:
        problems.append(
            "judgement: the answers must be exactly 'acceptable' and 'primary_problem' "
            f"(in that order), not {[a['key'] for a in data['answers']]}"
        )
    acc = answers.get("acceptable")
    if acc is not None and acc.get("kind") != "boolean":
        problems.append("judgement: 'acceptable' must be a boolean answer")
    prim = answers.get("primary_problem")
    if prim is not None:
        if prim.get("kind") != "choice":
            problems.append("judgement: 'primary_problem' must be a choice answer")
        elif tuple(prim.get("choices", ())) != JUDGEMENT_CHOICES:
            problems.append(
                "judgement: the choices of 'primary_problem' must be exactly "
                f"{list(JUDGEMENT_CHOICES)}"
            )

    steps = data["reference"]["steps"]
    last = steps[-1]
    if last.get("op") != "answer" or set(last.get("values") or {}) != {
        "acceptable",
        "primary_problem",
    }:
        problems.append(
            "judgement: the last step must be an answer op giving exactly acceptable and "
            "primary_problem"
        )
    sources = [("the answer op", last.get("values") or {})]
    if data["reference"].get("expected"):
        sources.append(("expected", data["reference"]["expected"]))
    for where, values in sources:
        if (
            "acceptable" in values
            and "primary_problem" in values
            and values["acceptable"] is not (values["primary_problem"] == "none")
        ):
            problems.append(
                f"judgement: in {where}, acceptable is {values['acceptable']!r} but "
                f"primary_problem is {values['primary_problem']!r} (acceptable must be "
                "true exactly when primary_problem is none)"
            )

    prompt = data["prompt"]
    words = len(prompt.split())
    lo, hi = JUDGEMENT_WORDS
    if not lo <= words <= hi:
        problems.append(f"judgement: the prompt has {words} words (must be {lo}-{hi})")
    low = " ".join(prompt.lower().split())
    phrases = [c.replace("_", " ") for c in JUDGEMENT_CHOICES if c != "none"]
    for phrase in [*phrases, *JUDGEMENT_HINTS]:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}", low):
            problems.append(f"judgement: the prompt names or hints at a problem ('{phrase}')")
    return problems


def _value_kind_problems(a: Answer, value: Any, where: str) -> list[str]:
    if a.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{where}: {value!r} is not a number"]
    elif a.kind == "boolean":
        if not isinstance(value, bool):
            return [f"{where}: {value!r} is not a boolean"]
    elif value not in a.choices:
        return [f"{where}: {value!r} is not one of {list(a.choices)}"]
    return []


def task_from_dict(data: dict[str, Any], path: Path | None = None) -> Task:
    """Build a :class:`Task` from a parsed document, raising :class:`TaskError` if invalid."""
    problems = check_task(data, path)
    if problems:
        raise TaskError(path or data.get("id", "<task>"), problems)
    ref = data["reference"]
    return Task(
        id=data["id"],
        title=data["title"],
        level=data["level"],
        category=data["category"],
        domain=data["domain"],
        prompt=data["prompt"],
        answers=[Answer.from_dict(a) for a in data["answers"]],
        system=ref["system"],
        steps=list(ref["steps"]),
        expected=dict(ref["expected"]) if ref.get("expected") else None,
        notes=data.get("notes"),
        path=path,
        raw=data,
    )


def load_task(path: Path | str) -> Task:
    """Load and validate one task file."""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskError(p, [f"not valid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise TaskError(p, ["a task file must be a mapping"])
    return task_from_dict(data, p)


def task_files(tasks_dir: Path | str | None = TASKS_DIR) -> list[Path]:
    """Task files in ``tasks_dir`` (None: the benchmark's task directory), sorted by name."""
    d = Path(tasks_dir) if tasks_dir is not None else TASKS_DIR
    return sorted(d.glob("*.yaml")) if d.is_dir() else []


def load_tasks(
    tasks_dir: Path | str | None = TASKS_DIR, select: list[str] | None = None
) -> tuple[list[Task], list[TaskError]]:
    """Load every task (or those whose id matches one of ``select``, glob patterns allowed).

    ``tasks_dir`` None means the benchmark's own task directory.

    Returns the valid tasks and the errors of the invalid ones.
    """
    import fnmatch

    tasks: list[Task] = []
    errors: list[TaskError] = []
    for p in task_files(tasks_dir):
        if select and not any(fnmatch.fnmatchcase(p.stem, pat) for pat in select):
            continue
        try:
            tasks.append(load_task(p))
        except TaskError as exc:
            errors.append(exc)
    ids = [t.id for t in tasks]
    for tid in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(TaskError(tid, ["duplicate task id"]))
    return tasks, errors
