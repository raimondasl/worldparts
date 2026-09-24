"""The grader of the operations benchmark (PREREGISTRATION.md section 6.6, INTERFACE.md).

**The final reply** (:func:`final_reply`) is the ``result`` text of the session's result
message when that message is a success that is not an error. A session without one has no
final reply and fails: a timeout or a kill (no result message), the turn limit
(``error_max_turns``), the budget cap (``error_max_budget_usd``) and any error result.
Text the agent wrote in an earlier message is never graded.

**The answer** (:func:`parse_answer`) is read from exactly one place in the final reply,
the first that exists of: the last fenced ``json`` block; the last untagged fenced block
holding ``{``; the last fenced block with another tag holding ``{``; the last bare
``{...}`` object. Anything but a ``json`` block is recorded as a format issue. If that
one candidate is not a JSON object (after the v0.2 repairs of
:func:`benchmarks.composition.harness.grading._loads_lenient`: trailing commas, comments,
Python literals, or an object with stray text inside its block), the reply has no answer:
an earlier block, such as a draft, is never graded instead.

Keys are matched exactly, then after normalisation (case, spaces, hyphens), which is
recorded as a format issue; an exact key always wins over a normalised variant, and a
variant that is not read (the key is already given) is recorded. The same holds for the
fields of an estimate and of a diagnosis and for the names in ``magnitudes``. Extra keys
are recorded and ignored. A missing key fails.

Answer kinds:

- ``estimate``: ``{"value": x, "lo90": a, "hi90": b}``, or a bare number (a point with no
  interval). It passes when ``|value - truth| <= tol``. The interval never affects a point's
  pass; a malformed interval (``lo90 > hi90``, one bound missing, a bound that is not a
  number) is recorded and left out of the interval metrics. A number given as a string is
  read (with a format issue) only when the whole string is one number, optionally
  followed by a unit (``"8.9"``, ``"8.9 pp"``, ``"1,250 MWh/yr"``); a range, a hedge, an
  approximation or a decimal comma (``"8 to 12"``, ``"8.4-9.0"``, ``"~8.4"``,
  ``"7.5 (or 12)"``, ``"8,4"``) is not a number.
- ``estimate_or_undetermined``: an estimate or ``"cannot_determine"`` (also accepted, with
  a format issue: other spellings such as "Cannot determine", "cannot be determined",
  "undetermined", "not determinable", "indeterminate", and the string as an object's value).
  On a determinable key only a point within tol passes. On a non-determinable key
  ``cannot_determine`` passes, and so does a valid interval that contains the truth and
  covers at least 50 % of the plausible range [L, U] (the length of its intersection with
  [L, U] is at least half of U - L).
- ``boolean``: ``true``/``false`` (strings "true", "yes", "false", "no" with an issue).
- ``choice``: one option; case, spaces, hyphens and underscores are normalised (with an
  issue) when that identifies exactly one option.
- ``set``: a list of options, compared as a set (duplicates removed with an issue; a single
  string is read as a one-element set with an issue). Every element must be an option.
- ``diagnosis``: graded by the table of section 6.6 (:func:`grade_diagnosis`).

Numeric comparisons use a guard of 1e-9 relative to the magnitudes involved, so that a
value exactly at ``truth +/- tol`` is not failed by floating-point rounding.

Diagnosis categories: ``pass``, ``wrong_magnitude``, ``under_commitment``,
``confident_wrong``, ``incomplete``, ``wrong_set``, and, outside the table, ``malformed``
(the answer is not a diagnosis object, or its verdict is not one of the three) and
``missing`` (no answer). Neither of the last two is a confident wrong verdict. The verdict
governs: an ``identified`` verdict on an ambiguous or no-fault truth is confident wrong
whatever its ``faults`` hold (the table's "identified (anything)"), and a ``faults`` that
is missing, null or not a list of names is read, with a format issue, as naming no fault,
under every verdict and truth (so an ``identified`` verdict with such a list is confident
wrong on a single fault or a pair as well: it is not [X], [X, Y] or one of the pair). A
fault name outside the vocabulary is a name like any other: it never matches a true
fault. Two cases that the table does not list are resolved as follows:

- truth a single fault X, answer ``ambiguous`` with a set that does not contain X:
  ``wrong_set`` (an ambiguous verdict is never confident, and its set is wrong);
- truth a pair {X, Y}, answer ``identified`` [X, Y] with a magnitude outside tol:
  ``wrong_magnitude`` (the verdict and fault set are right; as for a single fault).

Records hold no truth value as such, but they are **for the owner only**: a numeric key's
truth can be worked out from what they do hold (the answer, the absolute error, and the
coverage and interval score of an interval that misses it), for a non-determinable key
as well. A firewalled session (PREREGISTRATION.md section 7) receives only the pass or
fail of ``grade RUN --pass-fail``, which writes no record.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from benchmarks.composition.harness.grading import (
    _FENCE,
    ParsedAnswer,
    _as_bool,
    _balanced_objects,
    _loads_lenient,
    numbers_in,
    traceable,
)

from .bundles import NO_FAULT, NUMERIC_KINDS, VERDICTS, KeySpec, OpsTask, norm_token

#: Interval-score level: the intervals are 90 % intervals.
ALPHA = 0.1
_EPS = 1e-9

CANNOT_DETERMINE = "cannot_determine"
_CANNOT_DETERMINE_VARIANTS = frozenset(
    {
        "cannot_determine",
        "can_not_determine",
        "cannot_be_determined",
        "can't_determine",
        "cant_determine",
        "undetermined",
        "undeterminable",
        "not_determinable",
        "indeterminate",
    }
)

PASS = "pass"
WRONG_MAGNITUDE = "wrong_magnitude"
UNDER_COMMITMENT = "under_commitment"
CONFIDENT_WRONG = "confident_wrong"
INCOMPLETE = "incomplete"
WRONG_SET = "wrong_set"
MALFORMED = "malformed"
MISSING = "missing"
CATEGORIES = (
    PASS,
    WRONG_MAGNITUDE,
    UNDER_COMMITMENT,
    CONFIDENT_WRONG,
    INCOMPLETE,
    WRONG_SET,
    MALFORMED,
    MISSING,
)

_VALUE_FIELDS = ("value", "point", "estimate", "best_estimate")
_LO_FIELDS = ("lo90", "lo_90", "lo", "low", "lower", "lower90", "lower_90", "lower_bound")
_HI_FIELDS = ("hi90", "hi_90", "hi", "high", "upper", "upper90", "upper_90", "upper_bound")
_VERDICT_ALIASES = {"nofault": "no_fault", "no_faults": "no_fault"}


def _within(err: float, tol: float, *scale: float) -> bool:
    """``err <= tol`` with a 1e-9 relative guard against floating-point rounding."""
    return err <= tol + _EPS * max(1.0, abs(tol), *(abs(s) for s in scale))


# ----------------------------------------------------------------------------------------
# the final reply and the answer object
# ----------------------------------------------------------------------------------------
def final_reply(
    stream_text: str, outcome: dict[str, Any] | None = None
) -> tuple[str | None, str | None]:
    """``(final reply, None)``, or ``(None, why the session has no final reply)``.

    The final reply is the ``result`` text of the stream's (last) result message when its
    subtype is ``success`` and it is not an error. A timeout or a kill leaves no result
    message; the turn limit, the budget cap and an error end with a result that has no
    reply. Those sessions have no final reply, so they fail (section 6.6).
    """
    result: dict[str, Any] | None = None
    for line in stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("type") == "result":
            result = msg
    if result is None:
        if (outcome or {}).get("timed_out"):
            return None, "the session timed out (no result message)"
        return None, "no result message (the session was stopped or crashed)"
    subtype = result.get("subtype")
    if subtype != "success":
        return None, f"the session ended with {subtype}"
    if result.get("is_error"):
        return None, "the session ended with an error result"
    text = result.get("result")
    if not isinstance(text, str) or not text.strip():
        return None, "the result message has no reply text"
    return text, None


def parse_answer(text: str | None) -> ParsedAnswer:
    """The answer object of a final reply (see the module docstring): one candidate only,
    the last ``json`` block, else the last untagged block, else the last block with another
    tag (blocks holding ``{``), else the last bare object."""
    if not text or not text.strip():
        return ParsedAnswer(None, error="empty final reply")
    fences = list(_FENCE.finditer(text))
    tagged = [m for m in fences if m.group(1).lower() == "json"]
    untagged = [m for m in fences if not m.group(1) and "{" in m.group(2)]
    other = [m for m in fences if m.group(1) and m.group(1).lower() != "json" and "{" in m.group(2)]
    issues: list[str] = []
    chosen = (tagged or untagged or other or [None])[-1]
    if chosen is not None:
        body = chosen.group(2).strip()
        if not tagged:
            issues.append(f"code block tagged '{chosen.group(1) or 'none'}' instead of json")
        if chosen is not fences[-1]:
            issues.append("code block(s) after the answer block ignored")
        elif text[chosen.end() :].strip():
            issues.append("text after the final code block")
    else:
        objects = _balanced_objects(text)
        if not objects:
            return ParsedAnswer(None, error="no JSON object found in the final reply")
        body = objects[-1]
        issues.append("no fenced json block (bare object)")
    try:
        value, repairs = _loads_lenient(body)
    except ValueError as exc:
        inner = _balanced_objects(body)
        if not inner or inner[-1] == body:
            return ParsedAnswer(None, issues, error=f"the answer block is not JSON ({exc})")
        try:
            value, repairs = _loads_lenient(inner[-1])
        except ValueError:
            return ParsedAnswer(None, issues, error=f"the answer block is not JSON ({exc})")
        repairs = [*repairs, "text around the object in its block"]
    if not isinstance(value, dict):
        return ParsedAnswer(None, issues, error="the answer block is not a JSON object")
    return ParsedAnswer(value, issues + repairs)


_NUMBER_TEXT = r"[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d*)?|\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
#: A unit after a number: one word starting with a letter, %, ° or µ (``pp``, ``%``,
#: ``MWh/yr``, ``m3/h``, ``°C``); no spaces, signs, dashes, commas or brackets.
_UNIT_TEXT = r"(?:[%°µ]|[^\W\d_])(?:[^\W_]|[%°µ/·^³²])*"
_NUMBER_STRING = re.compile(rf"({_NUMBER_TEXT})(?:\s*{_UNIT_TEXT})?")


def as_number(v: Any) -> tuple[float | None, str | None]:
    """(number, issue) from a JSON value. A string is a number only when all of it is one
    number, optionally with a unit (thousands separators ``1,250`` allowed); the issue is
    set then. Booleans, ranges, hedges and decimal commas are not numbers."""
    if isinstance(v, bool) or v is None:
        return None, None
    if isinstance(v, (int, float)):
        return (float(v), None) if math.isfinite(float(v)) else (None, None)
    if isinstance(v, str):
        m = _NUMBER_STRING.fullmatch(v.strip())
        if m:
            x = float(m.group(1).replace(",", ""))
            if math.isfinite(x):
                return x, f"number given as a string ({v!r})"
    return None, None


def _pick(
    d: dict[str, Any], canonical: str, names: tuple[str, ...], issues: list[str], what: str
) -> tuple[Any, bool]:
    """(value, present) of ``canonical`` in ``d``: the exact key, else the first key whose
    normalised spelling is one of ``names`` (a format issue). Other spellings that are
    present are recorded as ignored."""
    hits = [k for k in d if k == canonical]
    hits += [k for k in d if k != canonical and norm_token(k) in names]
    if not hits:
        return None, False
    first = hits[0]
    if first != canonical:
        issues.append(f"{what} {first!r} read as {canonical}")
    for k in hits[1:]:
        other = ", with another value" if d[k] != d[first] else ""
        issues.append(f"{what} {k!r} ignored ({canonical} is already given{other})")
    return d[first], True


def is_cannot_determine(v: Any) -> tuple[bool, str | None]:
    """(True, issue) when ``v`` spells cannot_determine (issue None for the exact string)."""
    if not isinstance(v, str):
        return False, None
    if v == CANNOT_DETERMINE:
        return True, None
    if norm_token(v) in _CANNOT_DETERMINE_VARIANTS:
        return True, f"cannot_determine read from {v!r}"
    return False, None


# ----------------------------------------------------------------------------------------
# estimates
# ----------------------------------------------------------------------------------------
@dataclass
class Estimate:
    """A parsed estimate answer."""

    value: float | None = None
    lo: float | None = None
    hi: float | None = None
    abstained: bool = False
    valid: bool = True  # False: not an estimate at all
    issues: list[str] = field(default_factory=list)

    @property
    def interval(self) -> tuple[float, float] | None:
        if self.lo is None or self.hi is None:
            return None
        return (self.lo, self.hi)


def _field(
    d: dict[str, Any], names: tuple[str, ...], canonical: str, issues: list[str]
) -> tuple[Any, bool]:
    """(value, present) of the field ``canonical`` or one of its accepted spellings."""
    return _pick(d, canonical, names, issues, "field")


def parse_estimate(given: Any) -> Estimate:
    """Parse an ``estimate`` or ``estimate_or_undetermined`` answer (never raises)."""
    cd, issue = is_cannot_determine(given)
    if cd:
        return Estimate(abstained=True, issues=[issue] if issue else [])
    if isinstance(given, dict):
        e = Estimate()
        known = set(_VALUE_FIELDS) | set(_LO_FIELDS) | set(_HI_FIELDS)
        unknown = [k for k in given if norm_token(k) not in known]
        if unknown:
            e.issues.append("fields ignored: " + ", ".join(map(str, unknown)))
        raw_value, has_value = _field(given, _VALUE_FIELDS, "value", e.issues)
        cd, issue = is_cannot_determine(raw_value)
        if cd:
            e.abstained = True
            e.issues.append("cannot_determine given as the value of an object")
            if issue:
                e.issues.append(issue)
            return e
        if has_value and raw_value is not None:
            e.value, issue = as_number(raw_value)
            if e.value is None:
                e.issues.append(f"value {raw_value!r} is not a number")
            elif issue:
                e.issues.append(f"value: {issue}")
        raw_lo, has_lo = _field(given, _LO_FIELDS, "lo90", e.issues)
        raw_hi, has_hi = _field(given, _HI_FIELDS, "hi90", e.issues)
        lo = hi = None
        if has_lo and raw_lo is not None:
            lo, issue = as_number(raw_lo)
            if lo is None:
                e.issues.append(f"lo90 {raw_lo!r} is not a number")
            elif issue:
                e.issues.append(f"lo90: {issue}")
        if has_hi and raw_hi is not None:
            hi, issue = as_number(raw_hi)
            if hi is None:
                e.issues.append(f"hi90 {raw_hi!r} is not a number")
            elif issue:
                e.issues.append(f"hi90: {issue}")
        if lo is not None and hi is not None:
            if lo > hi:
                e.issues.append(f"malformed interval: lo90 {lo:g} > hi90 {hi:g} (ignored)")
            else:
                e.lo, e.hi = lo, hi
        elif (has_lo and raw_lo is not None) or (has_hi and raw_hi is not None):
            e.issues.append("malformed interval: lo90 or hi90 missing or not a number (ignored)")
        if e.value is None and e.interval is None:
            e.valid = False
            e.issues.append("no value and no valid interval")
        return e
    value, issue = as_number(given)
    if value is None:
        return Estimate(valid=False, issues=[f"not an estimate ({type(given).__name__})"])
    return Estimate(value=value, issues=[issue] if issue else [])


def interval_score(lo: float, hi: float, x: float, alpha: float = ALPHA) -> float:
    """Gneiting-Raftery interval score of a central (1 - alpha) interval (lower is better)."""
    return (hi - lo) + (2 / alpha) * max(0.0, lo - x) + (2 / alpha) * max(0.0, x - hi)


# ----------------------------------------------------------------------------------------
# key results
# ----------------------------------------------------------------------------------------
@dataclass
class KeyResult:
    """The grade of one answer key. It holds no truth value, but the truth of a numeric key
    follows from ``value`` and ``error`` (and ``covered`` and ``interval_score``): for the
    owner only (see the module docstring)."""

    key: str
    kind: str
    passed: bool
    given: Any = None
    value: Any = None  # the parsed answer: a point, bool, option, sorted set or diagnosis
    category: str | None = None  # diagnosis only
    error: float | None = None  # |point - truth|
    tol: float | None = None
    determinable: bool | None = None
    abstained: bool = False
    interval: list[float] | None = None
    covered: bool | None = None
    interval_score: float | None = None
    traceable: bool | None = None
    magnitudes: dict[str, dict[str, Any]] | None = None
    issues: list[str] = field(default_factory=list)


def _interval_metrics(r: KeyResult, e: Estimate, truth_value: Any) -> None:
    if e.interval is None:
        return
    r.interval = [e.interval[0], e.interval[1]]
    if isinstance(truth_value, (int, float)) and not isinstance(truth_value, bool):
        x = float(truth_value)
        lo, hi = e.interval
        tol = _EPS * max(1.0, abs(x))
        r.covered = lo - tol <= x <= hi + tol
        r.interval_score = interval_score(lo, hi, x)


def grade_numeric(spec: KeySpec, truth: dict[str, Any], given: Any) -> KeyResult:
    """Grade an ``estimate`` or ``estimate_or_undetermined`` key."""
    e = parse_estimate(given)
    r = KeyResult(spec.key, spec.kind, False, given=given, issues=list(e.issues))
    determinable = True if spec.kind == "estimate" else bool(truth["determinable"])
    r.determinable = determinable
    tv = truth.get("value")
    r.abstained = e.abstained
    if e.abstained:
        r.value = CANNOT_DETERMINE
        if spec.kind == "estimate":
            r.issues.append("cannot_determine is not an answer for an estimate key")
        r.passed = spec.kind == "estimate_or_undetermined" and not determinable
        return r
    if not e.valid:
        return r
    r.value = e.value
    _interval_metrics(r, e, tv)
    if determinable:
        if e.value is None:
            r.issues.append("no point estimate")
            return r
        r.tol = float(truth["tol"])
        r.error = abs(e.value - float(tv))
        r.passed = _within(r.error, r.tol, float(tv), e.value)
        return r
    # Not determinable: cannot_determine (above) or a wide interval that holds the truth.
    rng = truth.get("range") or spec.range
    lo_u, hi_u = float(rng[0]), float(rng[1])
    if e.interval is None:
        r.issues.append("a non-determinable key needs cannot_determine or a valid interval")
        return r
    if tv is None:
        r.issues.append("the truth value is unknown, so only cannot_determine can pass")
        return r
    lo, hi = e.interval
    overlap = max(0.0, min(hi, hi_u) - max(lo, lo_u))
    wide = _within(0.5 * (hi_u - lo_u) - overlap, 0.0, hi_u, lo_u)
    r.passed = bool(r.covered) and wide
    if not wide:
        r.issues.append("the interval covers less than half of the plausible range")
    return r


def match_option(given: Any, options: tuple[str, ...] | list[str]) -> tuple[str | None, str | None]:
    """(option, issue): exact match, else the one option equal after :func:`norm_token`."""
    if not isinstance(given, str):
        return None, f"not a string ({type(given).__name__})"
    if given in options:
        return given, None
    n = norm_token(given)
    hits = [o for o in options if norm_token(o) == n]
    if len(hits) == 1:
        return hits[0], f"{given!r} read as {hits[0]!r}"
    return None, f"{given!r} is not an option"


def grade_boolean(spec: KeySpec, truth: dict[str, Any], given: Any) -> KeyResult:
    value, issue = _as_bool(given)
    r = KeyResult(spec.key, spec.kind, False, given=given, value=value)
    if value is None:
        r.issues.append("not a boolean")
        return r
    if issue:
        r.issues.append(issue)
    r.passed = value is truth["value"]
    return r


def grade_choice(spec: KeySpec, truth: dict[str, Any], given: Any) -> KeyResult:
    value, issue = match_option(given, spec.options)
    r = KeyResult(spec.key, spec.kind, False, given=given, value=value)
    if issue:
        r.issues.append(issue)
    r.passed = value is not None and value == truth["value"]
    return r


def grade_set(spec: KeySpec, truth: dict[str, Any], given: Any) -> KeyResult:
    r = KeyResult(spec.key, spec.kind, False, given=given)
    items = given
    if isinstance(given, str):
        items = [given]
        r.issues.append("a single string read as a one-element set")
    if not isinstance(items, (list, tuple)):
        r.issues.append(f"not a list ({type(given).__name__})")
        return r
    chosen: list[str] = []
    bad = False
    for item in items:
        opt, issue = match_option(item, spec.options)
        if issue:
            r.issues.append(issue)
        if opt is None:
            bad = True
        elif opt not in chosen:
            chosen.append(opt)
        else:
            r.issues.append(f"duplicate {opt!r} removed")
    r.value = sorted(chosen)
    r.passed = not bad and set(chosen) == set(truth["value"])
    return r


# ----------------------------------------------------------------------------------------
# diagnosis (the table of section 6.6)
# ----------------------------------------------------------------------------------------
def _diag_field(d: dict[str, Any], name: str, issues: list[str]) -> Any:
    return _pick(d, name, (name,), issues, "field")[0]


def _fault_names(raw: Any, known: tuple[str, ...], issues: list[str]) -> list[str]:
    """The fault names of an answer (normalised to the vocabulary where possible). A
    ``faults`` that is missing, null or not a list of strings names no fault (recorded as a
    format issue when it is given but is not a list of names): the verdict governs."""
    if raw is None:
        return []
    items = raw
    if isinstance(raw, str):
        items = [raw]
        issues.append("faults given as a single string")
    if not isinstance(items, (list, tuple)) or not all(isinstance(i, str) for i in items):
        issues.append(f"faults is not a list of fault names, read as no fault ({raw!r:.80})")
        return []
    out: list[str] = []
    for item in items:
        name, issue = match_option(item, known)
        if name is None:
            name = norm_token(item)
            issues.append(f"fault {item!r} is not in the vocabulary")
        elif issue:
            issues.append(f"fault {issue}")
        if name in out:
            issues.append(f"duplicate fault {name!r} removed")
        else:
            out.append(name)
    return out


def _magnitudes(
    raw: Any, faults: set[str], truth_mags: dict[str, Any], known: tuple[str, ...]
) -> tuple[bool, dict[str, dict[str, Any]], list[str]]:
    """(every true fault's magnitude within tol, per-fault results, issues)."""
    issues: list[str] = []
    given: dict[str, Any] = {}
    if isinstance(raw, dict):
        # Exact names first, so that a normalised variant never overrides one.
        for k, v in raw.items():
            if isinstance(k, str) and k in known:
                given[k] = v
        for k, v in raw.items():
            if isinstance(k, str) and k in known:
                continue
            name, issue = match_option(k, known)
            if name is None:
                issues.append(f"magnitude of an unknown fault {k!r} ignored")
                continue
            if name in given:
                other = ", with another value" if v != given[name] else ""
                issues.append(f"magnitude key {k!r} ignored ({name!r} is already given{other})")
                continue
            if issue:
                issues.append(f"magnitude key {issue}")
            given[name] = v
    elif raw is not None:
        issues.append(f"magnitudes is not an object ({type(raw).__name__})")
    ok = True
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(faults):
        t = truth_mags[f]
        res: dict[str, Any] = {"passed": False, "tol": float(t["tol"])}
        if f not in given:
            issues.append(f"magnitude of {f!r} missing")
            ok = False
            out[f] = res
            continue
        e = parse_estimate(given[f])
        issues += [f"magnitude of {f!r}: {i}" for i in e.issues]
        if e.value is None:
            issues.append(f"magnitude of {f!r} has no point value")
            ok = False
            out[f] = res
            continue
        res["value"] = e.value
        res["error"] = abs(e.value - float(t["value"]))
        res["passed"] = _within(res["error"], res["tol"], float(t["value"]), e.value)
        if e.interval is not None:
            lo, hi = e.interval
            x = float(t["value"])
            res["interval"] = [lo, hi]
            res["covered"] = lo - _EPS * max(1.0, abs(x)) <= x <= hi + _EPS * max(1.0, abs(x))
            res["interval_score"] = interval_score(lo, hi, x)
        ok = ok and res["passed"]
        out[f] = res
    return ok, out, issues


def grade_diagnosis(spec: KeySpec, truth: dict[str, Any], given: Any) -> KeyResult:
    """Grade a diagnosis by the table of PREREGISTRATION.md section 6.6."""
    r = KeyResult(spec.key, spec.kind, False, given=given)
    label = truth["label"]
    true_set = {f for f in truth["faults"] if not (label == "no_fault" and f == NO_FAULT)}
    known = tuple(dict.fromkeys([*spec.faults(), *truth["faults"]]))
    if not isinstance(given, dict):
        r.category = MALFORMED
        r.issues.append(f"not a diagnosis object ({type(given).__name__})")
        return r
    raw_verdict = _diag_field(given, "verdict", r.issues)
    verdict = norm_token(raw_verdict) if isinstance(raw_verdict, str) else None
    verdict = _VERDICT_ALIASES.get(verdict or "", verdict)
    if verdict not in VERDICTS:
        r.category = MALFORMED
        r.issues.append(f"verdict {raw_verdict!r} is not identified, ambiguous or no_fault")
        return r
    if isinstance(raw_verdict, str) and raw_verdict != verdict:
        r.issues.append(f"verdict {raw_verdict!r} read as {verdict!r}")
    raw_faults = _diag_field(given, "faults", r.issues)
    if raw_faults is None and verdict != "no_fault":
        r.issues.append("faults missing, read as no fault")
    answer = set(_fault_names(raw_faults, known, r.issues))
    rm_raw = _diag_field(given, "resolving_measurement", r.issues)
    r.value = {"verdict": verdict, "faults": sorted(answer)}

    if verdict == "no_fault":
        if answer - {NO_FAULT}:
            r.issues.append("faults listed with a no_fault verdict are ignored")
        r.category = PASS if label == "no_fault" else CONFIDENT_WRONG
    elif verdict == "identified" and label in ("no_fault", "ambiguous"):
        r.category = CONFIDENT_WRONG  # "identified (anything)", whatever faults holds
    elif label == "no_fault":  # an ambiguous verdict
        r.category = UNDER_COMMITMENT
    elif label == "ambiguous":  # an ambiguous verdict
        options = tuple(dict.fromkeys([*spec.resolving_options, *truth["resolving"]]))
        rm, issue = match_option(rm_raw, options) if rm_raw is not None else (None, None)
        if rm_raw is None:
            r.issues.append("resolving_measurement missing")
        elif issue:
            r.issues.append(f"resolving_measurement: {issue}")
        r.value["resolving_measurement"] = rm
        good_set = answer >= true_set and len(answer) <= len(true_set) + 1
        good_rm = rm is not None and rm in truth["resolving"]
        r.category = PASS if good_set and good_rm else WRONG_SET
    else:  # identified truth: one fault or a pair
        if verdict == "ambiguous":
            if len(true_set) == 2 or true_set <= answer:
                r.category = UNDER_COMMITMENT
            else:
                r.category = WRONG_SET  # not in the table: see the module docstring
        elif answer == true_set:
            ok, mags, issues = _magnitudes(
                _diag_field(given, "magnitudes", r.issues), true_set, truth["magnitudes"], known
            )
            r.magnitudes = mags
            r.issues += issues
            r.category = PASS if ok else WRONG_MAGNITUDE
        elif len(true_set) == 2 and len(answer) == 1 and answer < true_set:
            r.category = INCOMPLETE
        else:
            r.category = CONFIDENT_WRONG
    r.passed = r.category == PASS
    return r


# ----------------------------------------------------------------------------------------
# a whole answer
# ----------------------------------------------------------------------------------------
_GRADERS = {
    "estimate": grade_numeric,
    "estimate_or_undetermined": grade_numeric,
    "boolean": grade_boolean,
    "choice": grade_choice,
    "set": grade_set,
    "diagnosis": grade_diagnosis,
}


@dataclass
class SessionGrade:
    """The grade of one session's final reply."""

    task: str
    passed: bool
    keys: dict[str, KeyResult]
    parse_error: str | None = None
    format_issues: list[str] = field(default_factory=list)

    @property
    def n_passed(self) -> int:
        return sum(k.passed for k in self.keys.values())

    @property
    def cw_category(self) -> str | None:
        """The diagnosis category (F3 tasks), else None."""
        cats = [k.category for k in self.keys.values() if k.kind == "diagnosis"]
        return cats[0] if cats else None

    @property
    def confident_wrong(self) -> bool:
        return self.cw_category == CONFIDENT_WRONG

    @property
    def under_commitment(self) -> bool:
        return self.cw_category == UNDER_COMMITMENT

    def abstentions(self) -> tuple[list[str], list[str]]:
        """(keys answered cannot_determine, those of them on a determinable key)."""
        abst = [k for k, r in self.keys.items() if r.abstained]
        return abst, [k for k in abst if self.keys[k].determinable]

    def intervals(self) -> dict[str, Any]:
        """Valid 90 % intervals given (numeric keys and magnitudes), and how many held the
        truth; with the sum of their interval scores (not normalised: the truth files do
        not carry the oracle's)."""
        covered: list[bool] = []
        scores: list[float] = []
        for r in self.keys.values():
            if r.covered is not None:
                covered.append(r.covered)
                scores.append(float(r.interval_score or 0.0))
            for m in (r.magnitudes or {}).values():
                if m.get("covered") is not None:
                    covered.append(bool(m["covered"]))
                    scores.append(float(m.get("interval_score") or 0.0))
        return {"n": len(covered), "covered": sum(covered), "interval_score_sum": sum(scores)}

    def to_dict(self) -> dict[str, Any]:
        abst, unjustified = self.abstentions()
        return {
            "task": self.task,
            "passed": self.passed,
            "n_keys": len(self.keys),
            "n_passed": self.n_passed,
            "keys": {k: asdict(v) for k, v in self.keys.items()},
            "cw_category": self.cw_category,
            "confident_wrong": self.confident_wrong,
            "under_commitment": self.under_commitment,
            "abstentions": abst,
            "unjustified_abstentions": unjustified,
            "intervals": self.intervals(),
            "parse_error": self.parse_error,
            "format_issues": self.format_issues,
        }


def _points(r: KeyResult) -> list[float]:
    pts: list[float] = []
    if r.kind in NUMERIC_KINDS and isinstance(r.value, float) and math.isfinite(r.value):
        pts.append(r.value)
    for m in (r.magnitudes or {}).values():
        if isinstance(m.get("value"), float):
            pts.append(m["value"])
    return pts


def grade_answer(
    task: OpsTask,
    truth: dict[str, Any],
    final_text: str | None,
    tool_numbers: list[float] | None = None,
) -> SessionGrade:
    """Grade a final reply against ``truth`` (a validated truth document).

    ``tool_numbers`` (every number in the session's tool results) sets the traceability of
    numeric answers, as in v0.2: a point within 0.5 % of such a number. None leaves it unset.
    """
    parsed = parse_answer(final_text)
    issues = list(parsed.issues)
    data = parsed.data or {}
    # Exact keys first, so that a normalised variant never overrides one.
    mapped: dict[str, Any] = {k: v for k, v in data.items() if k in task.keys}
    for k, v in data.items():
        nk = norm_token(k)
        if k in task.keys or nk not in task.keys:
            continue
        if nk in mapped:
            other = ", with another value" if v != mapped[nk] else ""
            issues.append(f"key {k!r} ignored ({nk!r} is already given{other})")
            continue
        mapped[nk] = v
        issues.append(f"key {k!r} read as {nk!r}")
    extra = [k for k in data if k not in task.keys and norm_token(k) not in task.keys]
    if extra:
        issues.append("extra keys ignored: " + ", ".join(map(str, extra)))
    results: dict[str, KeyResult] = {}
    for key, spec in task.keys.items():
        tkey = truth["keys"][key]
        if key not in mapped:
            r = KeyResult(key, spec.kind, False, issues=["missing"])
            if spec.kind == "diagnosis":
                r.category = MISSING
            if spec.kind == "estimate_or_undetermined":
                r.determinable = bool(tkey["determinable"])
            results[key] = r
            continue
        r = _GRADERS[spec.kind](spec, tkey, mapped[key])
        if tool_numbers is not None:
            pts = _points(r)
            if pts:
                r.traceable = all(traceable(p, tool_numbers) for p in pts)
        issues += [f"{key}: {i}" for i in r.issues]
        results[key] = r
    passed = parsed.data is not None and all(r.passed for r in results.values())
    return SessionGrade(task.task_id, passed, results, parsed.error, issues)


def tool_numbers_of(tool_result_texts: list[str]) -> list[float]:
    out: list[float] = []
    for t in tool_result_texts:
        out.extend(numbers_in(t))
    return out
