"""Task bundles, ``task.json``, truth files and the bundle manifest (see ../INTERFACE.md).

Layout::

    $WPBENCH_OPS_BUNDLES/<set>/<task_id>/r<k>/   task.md, task.json, plant.md, ...
    $WPBENCH_OPS_TRUTH/<set>/<task_id>.truth.json

A task is loaded from its realisation directories: every ``r<k>/`` must hold ``task.md``,
``task.json`` and ``plant.md``, and the ``task.json`` files of all realisations must be
equal (realisations differ only in data noise and artefacts). The session's working
directory receives a copy of ``r<k>/`` without ``task.json``; truth is read only by the
grader and the headroom rule, never by the runner.

Loading is strict: any problem raises :class:`BundleError` with the full list, because a
malformed bundle must be fixed before a session runs, not graded around.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = OPS_DIR.parents[1]
PREAMBLES_DIR = OPS_DIR / "preambles"
RESULTS_DIR = OPS_DIR / "results"
PREREGISTRATION = OPS_DIR / "PREREGISTRATION.md"

BUNDLES_ENV = "WPBENCH_OPS_BUNDLES"
TRUTH_ENV = "WPBENCH_OPS_TRUTH"
#: The shared bundle folder when ``$WPBENCH_OPS_BUNDLES`` is not set (INTERFACE.md).
DEFAULT_BUNDLES = Path("C:/Users/raimo/world-model/opsbench-bundles")

SETS = ("dev", "test")
FAMILIES = ("F1", "F2", "F3", "F4")
GENERATORS = ("G-ind", "G-epa")
STRATA = ("single", "double", "ambiguous", "no_fault", "sensor")
KINDS = ("estimate", "estimate_or_undetermined", "boolean", "choice", "set", "diagnosis")
NUMERIC_KINDS = ("estimate", "estimate_or_undetermined")
VERDICTS = ("identified", "ambiguous", "no_fault")
#: The no-fault member of the F3 vocabulary.
NO_FAULT = "none"
TASK_JSON = "task.json"
REQUIRED_FILES = ("task.md", TASK_JSON, "plant.md")
#: Most bytes a bundle (one realisation) may have (PREREGISTRATION.md section 5).
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
_REALISATION = re.compile(r"^r([1-9][0-9]*)$")

#: Exact cell counts of each set (PREREGISTRATION.md section 4); a test-set cell may fall
#: short when it is dropped after 100 draws, which is reported, not an error.
PREREGISTERED_CELLS = {
    "dev": {
        "F1/G-ind": 2,
        "F1/G-epa": 2,
        "F2/G-ind": 3,
        "F3/G-ind": 4,
        "F3/G-epa": 2,
        "F4/G-ind": 2,
        "F4/G-epa": 1,
    },
    "test": {
        "F1/G-ind": 5,
        "F1/G-epa": 5,
        "F2/G-ind": 8,
        "F3/G-ind": 8,
        "F3/G-epa": 8,
        "F4/G-ind": 4,
        "F4/G-epa": 4,
    },
}
#: F3 strata per set (PREREGISTRATION.md section 4).
PREREGISTERED_STRATA = {
    "dev": {"single": 2, "double": 1, "ambiguous": 1, "no_fault": 1, "sensor": 1},
    "test": {"single": 6, "double": 2, "ambiguous": 4, "no_fault": 2, "sensor": 2},
}


class BundleError(Exception):
    """A bundle, task.json or truth file is invalid; ``problems`` lists every problem."""

    def __init__(self, where: str, problems: list[str]) -> None:
        self.where = where
        self.problems = list(problems)
        super().__init__(f"{where}: " + "; ".join(self.problems))


class ConfigError(Exception):
    """A required location (truth folder, bundle folder) is not configured or missing."""


def norm_token(text: Any) -> str:
    """A name as compared by the grader: lower case, runs of spaces, hyphens and
    underscores as one underscore, surrounding spaces and punctuation removed."""
    s = re.sub(r"[\s\-_]+", "_", str(text).strip().lower())
    return s.strip("_.,;:!?\"'`")


# ----------------------------------------------------------------------------------------
# locations
# ----------------------------------------------------------------------------------------
def bundles_root(override: Path | str | None = None) -> Path:
    """The bundle folder: ``override``, else ``$WPBENCH_OPS_BUNDLES``, else the default."""
    if override:
        return Path(override)
    env = os.environ.get(BUNDLES_ENV)
    return Path(env) if env else DEFAULT_BUNDLES


def truth_root(override: Path | str | None = None) -> Path:
    """The truth folder: ``override``, else ``$WPBENCH_OPS_TRUTH`` (no default: the truth
    lives in the private benchmark folder and is never guessed)."""
    if override:
        root = Path(override)
    else:
        env = os.environ.get(TRUTH_ENV)
        if not env:
            raise ConfigError(f"set {TRUTH_ENV} (or pass --truth) to the truth folder")
        root = Path(env)
    if not root.is_dir():
        raise ConfigError(f"the truth folder {root} does not exist")
    return root


def manifest_path(set_name: str) -> Path:
    """Where the committed manifest of a bundle set lives."""
    return OPS_DIR / f"bundles-{set_name}.sha256"


# ----------------------------------------------------------------------------------------
# task.json
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class KeySpec:
    """One answer key of a task (from task.json; no truth)."""

    key: str
    kind: str
    unit: str | None = None
    range: tuple[float, float] | None = None
    options: tuple[str, ...] = ()
    vocabulary: dict[str, dict[str, Any]] = field(default_factory=dict)
    excluded: dict[str, str] = field(default_factory=dict)
    resolving_options: tuple[str, ...] = ()

    def faults(self) -> tuple[str, ...]:
        """The diagnosis vocabulary with ``none`` (the names a fault set may hold)."""
        names = [NO_FAULT] if NO_FAULT not in self.vocabulary else []
        return tuple(names + list(self.vocabulary))


@dataclass(frozen=True)
class OpsTask:
    """One task: its metadata (task.json) and its realisation directories."""

    task_id: str
    set_name: str
    family: str
    generator: str
    cell: str
    stratum: str | None
    keys: dict[str, KeySpec]
    task_dir: Path
    realisations: tuple[int, ...]

    def realisation_dir(self, k: int) -> Path:
        if k not in self.realisations:
            raise BundleError(
                f"{self.set_name}/{self.task_id}",
                [f"no realisation r{k} (has {', '.join(f'r{x}' for x in self.realisations)})"],
            )
        return self.task_dir / f"r{k}"

    def task_md(self, k: int) -> str:
        return (self.realisation_dir(k) / "task.md").read_text(encoding="utf-8")


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _range(value: Any, where: str, problems: list[str]) -> tuple[float, float] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(_is_number(v) for v in value)
    ):
        problems.append(f"{where}: range must be [L, U] with two numbers")
        return None
    lo, hi = float(value[0]), float(value[1])
    if not lo < hi:
        problems.append(f"{where}: range [{lo:g}, {hi:g}] needs L < U")
        return None
    return (lo, hi)


def _options(value: Any, where: str, problems: list[str], least: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(o, str) and o for o in value):
        problems.append(f"{where}: options must be a list of non-empty strings")
        return ()
    if len(value) < least:
        problems.append(f"{where}: needs at least {least} options")
    if len({norm_token(o) for o in value}) != len(value):
        problems.append(f"{where}: options must be distinct after normalisation")
    return tuple(value)


def key_spec(key: str, d: Any, problems: list[str]) -> KeySpec | None:
    """A validated :class:`KeySpec` from a task.json ``keys`` entry (problems appended)."""
    where = f"key {key!r}"
    if not isinstance(d, dict):
        problems.append(f"{where}: must be an object")
        return None
    kind = d.get("kind")
    if kind not in KINDS:
        problems.append(f"{where}: kind {kind!r} is not one of {', '.join(KINDS)}")
        return None
    if kind in NUMERIC_KINDS:
        unit = d.get("unit")
        if not isinstance(unit, str) or not unit:
            problems.append(f"{where}: a numeric key needs a unit")
        rng = _range(d.get("range"), where, problems)
        return KeySpec(key, kind, unit=unit if isinstance(unit, str) else None, range=rng)
    if kind == "boolean":
        return KeySpec(key, kind)
    if kind in ("choice", "set"):
        opts = _options(d.get("options"), where, problems, 2 if kind == "choice" else 1)
        return KeySpec(key, kind, options=opts)
    # diagnosis
    vocab = d.get("vocabulary")
    vocabulary: dict[str, dict[str, Any]] = {}
    if not isinstance(vocab, dict) or not vocab:
        problems.append(f"{where}: a diagnosis needs a vocabulary (fault -> unit, m_min, range)")
    else:
        for fault, spec in vocab.items():
            if fault == NO_FAULT:
                vocabulary[fault] = dict(spec or {})
                continue
            fw = f"{where}: fault {fault!r}"
            if not isinstance(spec, dict):
                problems.append(f"{fw}: must be an object")
                continue
            if (
                isinstance(spec.get("unit"), str)
                and "m_min" in spec
                and "range" in spec
                and spec["m_min"] is None
                and spec["range"] is None
            ):
                # no task-level magnitude bounds (INTERFACE.md): m_min and range null together
                vocabulary[fault] = dict(spec)
                continue
            if not isinstance(spec.get("unit"), str) or not _is_number(spec.get("m_min")):
                problems.append(
                    f"{fw}: needs a unit and a numeric m_min (or m_min and range both null)"
                )
            rng = _range(spec.get("range"), fw, problems)
            vocabulary[fault] = {**spec, "range": list(rng) if rng else spec.get("range")}
        if len({norm_token(f) for f in vocabulary}) != len(vocabulary):
            problems.append(f"{where}: fault names must be distinct after normalisation")
    excluded = d.get("excluded_from_candidates", {})
    if not isinstance(excluded, dict):
        problems.append(f"{where}: excluded_from_candidates must map a fault to a reason")
        excluded = {}
    resolving = _options(d.get("resolving_options", []), f"{where} resolving", problems, 0)
    return KeySpec(
        key,
        kind,
        vocabulary=vocabulary,
        excluded={str(k): str(v) for k, v in excluded.items()},
        resolving_options=resolving,
    )


def task_from_json(
    data: Any, task_dir: Path, set_name: str, realisations: tuple[int, ...]
) -> OpsTask:
    """Validate a task.json document; raise :class:`BundleError` listing every problem."""
    where = f"{set_name}/{task_dir.name}/{TASK_JSON}"
    problems: list[str] = []
    if not isinstance(data, dict):
        raise BundleError(where, ["must be a JSON object"])
    task_id = data.get("task_id")
    if task_id != task_dir.name:
        problems.append(f"task_id {task_id!r} differs from the directory name {task_dir.name!r}")
    if data.get("set") != set_name:
        problems.append(f"set {data.get('set')!r} differs from the set directory {set_name!r}")
    family, generator = data.get("family"), data.get("generator")
    if family not in FAMILIES:
        problems.append(f"family {family!r} is not one of {', '.join(FAMILIES)}")
    if generator not in GENERATORS:
        problems.append(f"generator {generator!r} is not one of {', '.join(GENERATORS)}")
    cell = data.get("cell")
    if cell != f"{family}/{generator}":
        problems.append(f"cell {cell!r} is not '{family}/{generator}'")
    stratum = data.get("stratum")
    if family == "F3":
        if stratum not in STRATA:
            problems.append(f"an F3 task needs a stratum in {', '.join(STRATA)}, not {stratum!r}")
    elif stratum is not None:
        problems.append(f"stratum must be null outside F3, not {stratum!r}")
    keys_raw = data.get("keys")
    keys: dict[str, KeySpec] = {}
    if not isinstance(keys_raw, dict) or not keys_raw:
        problems.append("keys must be a non-empty object")
    else:
        for key, d in keys_raw.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                problems.append(f"key {key!r} must be lower-case snake_case")
            spec = key_spec(key, d, problems)
            if spec is not None:
                keys[key] = spec
        n_diag = sum(1 for s in keys.values() if s.kind == "diagnosis")
        if (family == "F3") != (n_diag == 1) or n_diag > 1:
            problems.append("an F3 task has exactly one diagnosis key and other tasks none")
    if problems:
        raise BundleError(where, problems)
    return OpsTask(
        task_id=str(task_id),
        set_name=set_name,
        family=str(family),
        generator=str(generator),
        cell=str(cell),
        stratum=stratum,
        keys=keys,
        task_dir=task_dir,
        realisations=realisations,
    )


def load_task(task_dir: Path, set_name: str) -> OpsTask:
    """Load and check one task directory ``<root>/<set>/<task_id>/``."""
    where = f"{set_name}/{task_dir.name}"
    reals: list[int] = []
    for p in sorted(task_dir.iterdir()) if task_dir.is_dir() else []:
        m = _REALISATION.match(p.name)
        if m and p.is_dir():
            reals.append(int(m.group(1)))
    reals.sort()
    if not reals:
        raise BundleError(where, ["no realisation directories r1, r2, ..."])
    if reals != list(range(1, len(reals) + 1)):
        raise BundleError(where, [f"realisations must be r1..r{len(reals)} without gaps"])
    problems: list[str] = []
    docs: list[Any] = []
    for k in reals:
        rdir = task_dir / f"r{k}"
        for name in REQUIRED_FILES:
            if not (rdir / name).is_file():
                problems.append(f"r{k}/{name} is missing")
        if (rdir / TASK_JSON).is_file():
            try:
                docs.append(json.loads((rdir / TASK_JSON).read_text(encoding="utf-8")))
            except json.JSONDecodeError as exc:
                problems.append(f"r{k}/{TASK_JSON} is not JSON: {exc}")
    if any(d != docs[0] for d in docs[1:]):
        problems.append("task.json differs between realisations")
    if problems or not docs:
        raise BundleError(where, problems or ["no task.json"])
    return task_from_json(docs[0], task_dir, set_name, tuple(reals))


def load_tasks(
    set_name: str, root: Path | str | None = None, patterns: list[str] | None = None
) -> list[OpsTask]:
    """Every task of ``set_name`` (optionally only ids matching ``patterns``), sorted by id.

    Raises :class:`BundleError` with the problems of every invalid task, and
    :class:`ConfigError` when the set directory does not exist.
    """
    if set_name not in SETS:
        raise ConfigError(f"unknown set {set_name!r} (choose from {', '.join(SETS)})")
    base = bundles_root(root) / set_name
    if not base.is_dir():
        raise ConfigError(f"the bundle set {base} does not exist (see {BUNDLES_ENV})")
    tasks: list[OpsTask] = []
    errors: list[str] = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        if patterns and not any(fnmatch.fnmatchcase(d.name, pat) for pat in patterns):
            continue
        try:
            tasks.append(load_task(d, set_name))
        except BundleError as exc:
            errors.append(str(exc))
    if errors:
        raise BundleError(f"{base}", errors)
    if patterns and not tasks:
        raise ConfigError(f"no task in {base} matches {' '.join(patterns)}")
    return tasks


def set_problems(tasks: list[OpsTask], set_name: str) -> list[str]:
    """Differences between a loaded set and its pre-registered cell and stratum counts,
    and bundles over the size limit (PREREGISTRATION.md sections 4 and 5)."""
    out: list[str] = []
    want = PREREGISTERED_CELLS.get(set_name, {})
    have: dict[str, int] = {}
    for t in tasks:
        have[t.cell] = have.get(t.cell, 0) + 1
    for cell in sorted(set(want) | set(have)):
        if have.get(cell, 0) != want.get(cell, 0):
            out.append(
                f"cell {cell}: {have.get(cell, 0)} task(s), pre-registered {want.get(cell, 0)}"
            )
    strata_want = PREREGISTERED_STRATA.get(set_name, {})
    strata_have: dict[str, int] = {}
    for t in tasks:
        if t.stratum:
            strata_have[t.stratum] = strata_have.get(t.stratum, 0) + 1
    for s in STRATA:
        if strata_have.get(s, 0) != strata_want.get(s, 0):
            out.append(
                f"F3 stratum {s}: {strata_have.get(s, 0)} task(s), "
                f"pre-registered {strata_want.get(s, 0)}"
            )
    for t in tasks:
        for k in t.realisations:
            size = sum(p.stat().st_size for p in t.realisation_dir(k).rglob("*") if p.is_file())
            if size > MAX_BUNDLE_BYTES:
                out.append(f"{t.task_id}/r{k}: {size} bytes, over the 5 MB limit")
    return out


def preregistered_slots(set_name: str) -> int:
    """The number of task slots of a set's pre-registered cells."""
    return sum(PREREGISTERED_CELLS.get(set_name, {}).values())


def blocking_set_problems(tasks: list[OpsTask], set_name: str) -> list[str]:
    """The differences of a partly filled set that block the Stage 0 rule: a cell or F3
    stratum with more tasks than pre-registered, a task outside the cells, a bundle over the
    size limit. Missing tasks are not among them; they are undecided slots."""
    want = PREREGISTERED_CELLS.get(set_name, {})
    have: dict[str, int] = {}
    for t in tasks:
        have[t.cell] = have.get(t.cell, 0) + 1
    out = [
        f"cell {c}: {n} task(s), pre-registered {want.get(c, 0)}"
        for c, n in sorted(have.items())
        if n > want.get(c, 0)
    ]
    strata_want = PREREGISTERED_STRATA.get(set_name, {})
    strata_have: dict[str, int] = {}
    for t in tasks:
        if t.stratum:
            strata_have[t.stratum] = strata_have.get(t.stratum, 0) + 1
    out += [
        f"F3 stratum {st}: {n} task(s), pre-registered {strata_want.get(st, 0)}"
        for st, n in sorted(strata_have.items())
        if n > strata_want.get(st, 0)
    ]
    out += [p for p in set_problems(tasks, set_name) if "over the 5 MB limit" in p]
    return out


def copy_realisation(task: OpsTask, k: int, dest: Path) -> list[str]:
    """Copy ``r<k>/`` without ``task.json`` into ``dest`` (which must be empty or absent).
    Returns the copied files as posix paths relative to ``dest``."""
    src = task.realisation_dir(k)
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise RuntimeError(f"the working directory {dest} is not empty")
    copied: list[str] = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if rel.as_posix() == TASK_JSON:
            continue
        target = dest / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            copied.append(rel.as_posix())
    return copied


# ----------------------------------------------------------------------------------------
# truth
# ----------------------------------------------------------------------------------------
def truth_path(task: OpsTask, root: Path) -> Path:
    return root / task.set_name / f"{task.task_id}.truth.json"


def truth_problems(task: OpsTask, truth: Any) -> list[str]:
    """Why ``truth`` cannot grade ``task`` (INTERFACE.md, ``<task_id>.truth.json``)."""
    if not isinstance(truth, dict):
        return ["the truth file must hold a JSON object"]
    problems: list[str] = []
    if truth.get("task_id") != task.task_id:
        problems.append(f"task_id {truth.get('task_id')!r} is not {task.task_id!r}")
    keys = truth.get("keys")
    if not isinstance(keys, dict):
        return [*problems, "keys must be an object"]
    if set(keys) != set(task.keys):
        missing = sorted(set(task.keys) - set(keys))
        extra = sorted(set(keys) - set(task.keys))
        problems.append(f"truth keys differ from task.json (missing {missing}, extra {extra})")
    for key, spec in task.keys.items():
        t = keys.get(key)
        if not isinstance(t, dict):
            continue
        where = f"truth key {key!r}"
        if t.get("kind") != spec.kind:
            problems.append(f"{where}: kind {t.get('kind')!r} is not {spec.kind!r}")
            continue
        problems += [f"{where}: {p}" for p in _truth_key_problems(spec, t)]
    problems += _realisation_problems(task, truth.get("realisations"), truth.get("r2_applies"))
    return problems


def _realisation_problems(task: OpsTask, reals: Any, r2_applies: Any = None) -> list[str]:
    """The truth's ``realisations`` must hold exactly the bundle's r1..rK, each with
    ``oracle_pass`` true (INTERFACE.md: they are realisations the oracle passes) and a
    non-empty ``reference_pass`` mapping estimators to true, false or null (the Stage 0
    headroom rule reads r1's). Where R-a and R-b both fail a realisation, R2 must have run
    (PREREGISTRATION.md 6.5) unless the truth says ``r2_applies: false``; otherwise a missing
    R2 result would be read as "no reference passes"."""
    want = [f"r{k}" for k in task.realisations]
    if not isinstance(reals, dict):
        return [f"realisations must be an object with {', '.join(want)}"]
    out: list[str] = []
    missing = [n for n in want if n not in reals]
    if missing:
        out.append(f"realisations {missing} are missing (the bundle has {', '.join(want)})")
    extra = sorted(str(n) for n in reals if n not in want)
    if extra:
        out.append(f"realisations {extra} are not in the bundle ({', '.join(want)})")
    for name in want:
        r = reals.get(name)
        if name not in reals:
            continue
        if not isinstance(r, dict):
            out.append(f"realisation {name}: must be an object")
            continue
        if r.get("oracle_pass") is not True:
            out.append(f"realisation {name}: oracle_pass must be true")
        ref = r.get("reference_pass")
        if (
            not isinstance(ref, dict)
            or not ref
            or not all(v is None or isinstance(v, bool) for v in ref.values())
        ):
            out.append(
                f"realisation {name}: reference_pass must map one or more estimators to "
                "true, false or null"
            )
        elif (
            ref.get("R-a") is False
            and ref.get("R-b") is False
            and ref.get("R2") is None
            and r2_applies is not False
        ):
            out.append(
                f"realisation {name}: R-a and R-b fail and R2 has no result; R2 runs there "
                "(PREREGISTRATION.md 6.5) unless the truth says r2_applies: false"
            )
    return out


def _truth_key_problems(spec: KeySpec, t: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if spec.kind in NUMERIC_KINDS:
        determinable = True
        if spec.kind == "estimate_or_undetermined":
            determinable = t.get("determinable")
            if not isinstance(determinable, bool):
                return ["determinable must be true or false"]
        if determinable:
            if not _is_number(t.get("value")):
                out.append("value must be a number")
            if not _is_number(t.get("tol")) or t["tol"] < 0:
                out.append("tol must be a number >= 0")
        elif t.get("value") is not None and not _is_number(t.get("value")):
            out.append("value must be a number or null")
        rng = t.get("range")
        if rng is not None:
            probs: list[str] = []
            r = _range(rng, "range", probs)
            out += probs
            if r and spec.range and tuple(r) != tuple(spec.range):
                out.append(f"range {list(r)} differs from task.json {list(spec.range)}")
        elif spec.range is None:
            out.append("no plausible range in the truth or task.json")
    elif spec.kind == "boolean":
        if not isinstance(t.get("value"), bool):
            out.append("value must be true or false")
    elif spec.kind == "choice":
        if t.get("value") not in spec.options:
            out.append(f"value {t.get('value')!r} is not an option")
    elif spec.kind == "set":
        v = t.get("value")
        if not isinstance(v, list) or any(x not in spec.options for x in v):
            out.append("value must be a list of options")
    else:
        out += _diagnosis_truth_problems(spec, t)
    return out


def _diagnosis_truth_problems(spec: KeySpec, t: dict[str, Any]) -> list[str]:
    out: list[str] = []
    label = t.get("label")
    faults = t.get("faults")
    if label not in VERDICTS:
        return [f"label {label!r} is not one of {', '.join(VERDICTS)}"]
    if not isinstance(faults, list) or not all(isinstance(f, str) for f in faults):
        return ["faults must be a list of fault names"]
    known = set(spec.faults())
    unknown = [f for f in faults if f not in known]
    if unknown:
        out.append(f"faults {unknown} are not in the vocabulary")
    if len(set(faults)) != len(faults):
        out.append("faults must be distinct")
    if label == "identified":
        if len(faults) not in (1, 2) or NO_FAULT in faults:
            out.append("an identified truth has one or two faults (not none)")
        mags = t.get("magnitudes")
        if not isinstance(mags, dict):
            out.append("an identified truth needs magnitudes")
        else:
            for f in faults:
                m = mags.get(f)
                if (
                    not isinstance(m, dict)
                    or not _is_number(m.get("value"))
                    or not _is_number(m.get("tol"))
                    or m["tol"] < 0
                ):
                    out.append(f"magnitude of {f!r} needs a value and a tol >= 0")
    elif label == "ambiguous":
        if len(faults) < 2:
            out.append("an ambiguous truth has at least two members")
        res = t.get("resolving")
        if not isinstance(res, list) or not res:
            out.append("an ambiguous truth needs a non-empty resolving list")
        elif any(r not in spec.resolving_options for r in res):
            out.append("resolving measurements must be among the resolving_options")
    elif any(f != NO_FAULT for f in faults):
        out.append("a no_fault truth has no faults (or only 'none')")
    return out


def load_truth(task: OpsTask, root: Path) -> dict[str, Any]:
    """The validated truth of ``task`` from ``root`` (raises :class:`BundleError`)."""
    path = truth_path(task, root)
    where = f"truth {task.set_name}/{task.task_id}"
    if not path.is_file():
        raise BundleError(where, [f"{path} is missing"])
    try:
        truth = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleError(where, [f"not JSON: {exc}"]) from None
    problems = truth_problems(task, truth)
    if problems:
        raise BundleError(where, problems)
    return truth


# ----------------------------------------------------------------------------------------
# manifest
# ----------------------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def realisation_digest(task: OpsTask, k: int) -> str:
    """SHA-256 over the relative path and SHA-256 of every file in ``r<k>/`` (``task.json``
    included): what a session of realisation k saw. A session whose recorded digest differs
    from the current one ran on a bundle that was replaced since (a task redrawn by
    validation, or a realisation 1 that moved), and it is never scored (PREREGISTRATION.md
    section 8, Stage 0)."""
    src = task.realisation_dir(k)
    h = hashlib.sha256()
    for p in sorted(src.rglob("*"), key=lambda q: q.relative_to(src).as_posix()):
        if p.is_file():
            h.update(f"{p.relative_to(src).as_posix()}\0{_sha256(p)}\n".encode())
    return h.hexdigest()


def manifest_digest(entries: dict[str, str], set_name: str, task_id: str, k: int) -> str | None:
    """:func:`realisation_digest` of ``r<k>`` computed from manifest entries
    (``{"<set>/<task>/r<k>/<file>": sha256}``), so that a session's recorded digest can be
    compared with the committed bundle rather than the local one (None: not listed)."""
    prefix = f"{set_name}/{task_id}/r{k}/"
    rows = sorted((p[len(prefix) :], sha) for p, sha in entries.items() if p.startswith(prefix))
    if not rows:
        return None
    h = hashlib.sha256()
    for rel, sha in rows:
        h.update(f"{rel}\0{sha}\n".encode())
    return h.hexdigest()


def truth_manifest_path(set_name: str) -> Path:
    """The committed list of validated truth files (``truth-<set>.sha256``, sha256sum format,
    paths relative to the truth folder): a truth file counts only if its SHA-256 is listed."""
    return OPS_DIR / f"truth-{set_name}.sha256"


def truth_listed(task: OpsTask, root: Path, listed: dict[str, str]) -> bool:
    """Whether the task's truth file exists and its SHA-256 is the one ``listed`` gives."""
    p = truth_path(task, root)
    rel = p.relative_to(root).as_posix()
    return p.is_file() and listed.get(rel) == _sha256(p)


def manifest_entries(root: Path, set_name: str) -> dict[str, str]:
    """``{"<set>/<task>/r<k>/<file>": sha256}`` for every file of a bundle set."""
    base = root / set_name
    if not base.is_dir():
        raise ConfigError(f"the bundle set {base} does not exist")
    return {
        p.relative_to(root).as_posix(): _sha256(p)
        for p in sorted(base.rglob("*"), key=lambda q: q.relative_to(root).as_posix())
        if p.is_file()
    }


def format_manifest(entries: dict[str, str]) -> str:
    """``sha256sum`` format (two spaces, forward slashes), sorted by path."""
    return "".join(f"{sha}  {path}\n" for path, sha in sorted(entries.items()))


def parse_manifest(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line.strip())
        if not m:
            raise ValueError(f"manifest line {n} is not '<sha256>  <path>'")
        out[m.group(2)] = m.group(1)
    return out


def manifest_differences(
    expected: dict[str, str], actual: dict[str, str], prefixes: list[str] | None = None
) -> list[str]:
    """Missing, changed and unlisted files (only paths under ``prefixes`` when given)."""

    def keep(path: str) -> bool:
        return not prefixes or any(path.startswith(p) for p in prefixes)

    out: list[str] = []
    for path in sorted(set(expected) | set(actual)):
        if not keep(path):
            continue
        if path not in actual:
            out.append(f"missing: {path}")
        elif path not in expected:
            out.append(f"not in the manifest: {path}")
        elif expected[path] != actual[path]:
            out.append(f"changed: {path}")
    return out


def task_manifest_prefixes(tasks: list[OpsTask]) -> list[str]:
    return [f"{t.set_name}/{t.task_id}/" for t in tasks]
