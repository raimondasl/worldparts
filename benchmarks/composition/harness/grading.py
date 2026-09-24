"""Answer format, final-JSON parsing, scoring, traceability and stream parsing.

Grading rules:

- The agent's final reply must end with a JSON object in a fenced ``json`` block. The
  parser also accepts, and records as format issues, a block without the ``json`` tag, a
  bare object at the end of the reply, trailing commas, comments, single-quoted strings,
  Python literals (True/False/None), numbers given as strings (optionally with a unit),
  keys in another case or with spaces or hyphens, and extra keys.
- A number passes when ``|a - e| <= max(rel_tol * |e|, abs_tol)``; a choice passes on an
  exact match (case, spaces and hyphens normalised, recorded as a format issue); a boolean
  passes on the same truth value. Missing or unparseable answers fail.
- A task passes only when every answer passes.
- Traceability: a numeric answer is traceable when its value is within 0.5 % of a number
  that appears in any tool result of the session, i.e. it came out of a computation the
  agent ran (an MCP tool result or the output of the agent's own code) and was not typed
  in from thin air. It is a diagnostic, not a verdict: an answer that is a prompt constant
  (``in_prompt``, e.g. a required dose the design just meets) or a closed-form result of
  prompt constants can be right without appearing in any tool result.
- Contamination: a session whose tool inputs or results touch the benchmark itself (the
  repository path, the task files, a frozen ``expected:`` mapping, or, in the code
  condition, the worldparts library) is contaminated; the report leaves it out of every
  rate and lists it separately.
"""

from __future__ import annotations

import ast
import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from .tasks import REPO_ROOT, Answer, Task, build_prompt_text
from .tasks import answer_format_instruction as answer_format_instruction  # re-export

TRACE_REL_TOL = 0.005


# ----------------------------------------------------------------------------------------
# prompt
# ----------------------------------------------------------------------------------------
def build_prompt(task: Task) -> str:
    """Task prompt plus the generated answer-format instruction (see
    :func:`benchmarks.composition.harness.tasks.answer_format_instruction`, which lives
    with the task rules because the loader checks the appended text too)."""
    return build_prompt_text(task.prompt, task.answers)


# ----------------------------------------------------------------------------------------
# final JSON parsing
# ----------------------------------------------------------------------------------------
@dataclass
class ParsedAnswer:
    """The object found in the final reply, and what had to be tolerated to read it."""

    data: dict[str, Any] | None
    issues: list[str] = field(default_factory=list)
    error: str | None = None


_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


def _balanced_objects(text: str) -> list[str]:
    """Top-level ``{...}`` substrings (braces inside strings are respected)."""
    out: list[str] = []
    depth = 0
    start = -1
    quote: str | None = None
    escape = False
    for i, ch in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'" and depth > 0:
            quote = ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out


def _loads_lenient(text: str) -> tuple[Any, list[str]]:
    """json.loads, falling back to a few repairs; returns (value, repairs made)."""
    try:
        return json.loads(text), []
    except json.JSONDecodeError:
        pass
    issues: list[str] = []
    fixed = re.sub(r"(?m)//[^\n\"]*$", "", text)
    fixed = re.sub(r"/\*.*?\*/", "", fixed, flags=re.DOTALL)
    if fixed != text:
        issues.append("comments removed")
    no_trailing = re.sub(r",(\s*[}\]])", r"\1", fixed)
    if no_trailing != fixed:
        issues.append("trailing comma")
    try:
        return json.loads(no_trailing), issues
    except json.JSONDecodeError:
        pass
    # Python-literal style: single quotes, True/False/None.
    py = re.sub(r"\btrue\b", "True", no_trailing)
    py = re.sub(r"\bfalse\b", "False", py)
    py = re.sub(r"\bnull\b", "None", py)
    try:
        value = ast.literal_eval(py)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    issues.append("not strict JSON (python-style literals or quotes)")
    return value, issues


def parse_final_json(text: str | None) -> ParsedAnswer:
    """Find and parse the answer object at the end of the agent's final reply.

    Fenced blocks are tried from the last one backwards (a ``json`` tag is expected; other
    tags holding an object are accepted with a format issue); without any fenced block,
    the last bare ``{...}`` object in the text is used.
    """
    if not text or not text.strip():
        return ParsedAnswer(None, error="empty final reply")
    fences = list(_FENCE.finditer(text))
    candidates: list[tuple[str, list[str]]] = []
    for m in reversed(fences):
        lang = m.group(1).lower()
        body = m.group(2).strip()
        if lang == "json":
            candidates.append((body, []))
        elif "{" in body:
            candidates.append((body, [f"code block tagged '{lang or 'none'}' instead of json"]))
    if not candidates:
        for obj in reversed(_balanced_objects(text)):
            candidates.append((obj, ["no fenced json block (bare object)"]))
    if not candidates:
        return ParsedAnswer(None, error="no JSON object found in the final reply")
    errors: list[str] = []
    for i, (body, issues) in enumerate(candidates):
        try:
            value, repairs = _loads_lenient(body)
        except ValueError as exc:
            # A block may hold an object plus stray text: try the last object inside it.
            objs = _balanced_objects(body)
            if not objs or objs[-1] == body:
                errors.append(str(exc))
                continue
            try:
                value, repairs = _loads_lenient(objs[-1])
            except ValueError:
                errors.append(str(exc))
                continue
            repairs = [*repairs, "text around the object in its block"]
        if not isinstance(value, dict):
            errors.append("the JSON value is not an object")
            continue
        found = issues + repairs
        if i > 0:
            found.append("the last candidate block was unusable; an earlier one was used")
        if fences and text[fences[-1].end() :].strip():
            found.append("text after the final code block")
        return ParsedAnswer(value, found)
    return ParsedAnswer(None, error="; ".join(errors) or "unparseable")


# ----------------------------------------------------------------------------------------
# scoring
# ----------------------------------------------------------------------------------------
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _norm_key(k: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(k).strip()).lower()


def _as_number(v: Any) -> tuple[float | None, str | None]:
    """(number, issue) from a JSON value; issue is set when a repair was needed."""
    if isinstance(v, bool) or v is None:
        return None, None
    if isinstance(v, (int, float)):
        return (float(v), None) if math.isfinite(float(v)) else (None, None)
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d*)?", s.split(" ")[0]):
            s = s.replace(",", "")
        m = _NUMBER.match(s)
        if m:
            return float(m.group(0)), f"number given as a string ({v!r})"
    return None, None


def _as_bool(v: Any) -> tuple[bool | None, str | None]:
    if isinstance(v, bool):
        return v, None
    if isinstance(v, str) and v.strip().lower() in ("true", "yes", "false", "no"):
        return v.strip().lower() in ("true", "yes"), f"boolean given as a string ({v!r})"
    return None, None


@dataclass
class AnswerScore:
    """The grade of one answer."""

    key: str
    kind: str
    expected: Any
    given: Any
    value: Any
    passed: bool
    error: float | None = None  # |a - e| for numbers
    tolerance: float | None = None
    issue: str | None = None
    traceable: bool | None = None  # numbers only; None when there is no stream
    in_prompt: bool | None = None  # numbers only: within 0.5 % of a number in the prompt


@dataclass
class Grade:
    """The grade of one run of one task."""

    task: str
    passed: bool
    answers: list[AnswerScore]
    parse_error: str | None = None
    format_issues: list[str] = field(default_factory=list)

    @property
    def n_passed(self) -> int:
        return sum(a.passed for a in self.answers)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["n_passed"] = self.n_passed
        return d


def score_answer(a: Answer, expected: Any, given: Any, present: bool) -> AnswerScore:
    """Score one answer value against the expected one."""
    if not present:
        return AnswerScore(a.key, a.kind, expected, None, None, False, issue="missing")
    if a.kind == "number":
        value, issue = _as_number(given)
        if value is None:
            return AnswerScore(a.key, a.kind, expected, given, None, False, issue="not a number")
        tol = a.tolerance(float(expected))
        err = abs(value - float(expected))
        return AnswerScore(a.key, a.kind, expected, given, value, err <= tol, err, tol, issue)
    if a.kind == "boolean":
        value, issue = _as_bool(given)
        if value is None:
            return AnswerScore(a.key, a.kind, expected, given, None, False, issue="not a boolean")
        return AnswerScore(a.key, a.kind, expected, given, value, value is expected, issue=issue)
    if not isinstance(given, str):
        return AnswerScore(a.key, a.kind, expected, given, None, False, issue="not a string")
    issue = None
    value = given
    if given not in a.choices:
        norm = _norm_key(given)
        if norm in a.choices:
            value, issue = norm, f"choice normalised from {given!r}"
        else:
            return AnswerScore(a.key, a.kind, expected, given, given, False, issue="not a choice")
    return AnswerScore(a.key, a.kind, expected, given, value, value == expected, issue=issue)


def grade(
    task: Task,
    final_text: str | None,
    tool_numbers: list[float] | None = None,
    expected: dict[str, Any] | None = None,
) -> Grade:
    """Grade a final reply against the task's expected answers.

    Args:
        task: The task.
        final_text: The agent's final reply.
        tool_numbers: Every number found in the session's tool results (for traceability);
            None when there is no stream (oracle mode), which leaves traceability unset.
        expected: Override for ``task.expected`` (tests).
    """
    exp = expected if expected is not None else task.expected
    if exp is None:
        raise ValueError(f"task {task.id} has no expected answers; run regen first")
    parsed = parse_final_json(final_text)
    issues = list(parsed.issues)
    data = parsed.data or {}
    # Map the agent's keys onto the answer keys (exact first, then normalised).
    mapped: dict[str, Any] = {}
    for k, v in data.items():
        if k in task.keys:
            mapped[k] = v
    for k, v in data.items():
        nk = _norm_key(k)
        if k not in task.keys and nk in task.keys and nk not in mapped:
            mapped[nk] = v
            issues.append(f"key {k!r} read as {nk!r}")
    extra = [k for k in data if k not in task.keys and _norm_key(k) not in task.keys]
    if extra:
        issues.append("extra keys: " + ", ".join(map(str, extra)))
    scores = []
    prompt_numbers = numbers_in(task.prompt) if tool_numbers is not None else []
    for a in task.answers:
        s = score_answer(a, exp[a.key], mapped.get(a.key), a.key in mapped)
        if a.kind == "number" and tool_numbers is not None:
            s.traceable = s.value is not None and traceable(s.value, tool_numbers)
            s.in_prompt = s.value is not None and traceable(s.value, prompt_numbers)
        if s.issue and s.issue not in ("missing", "not a number", "not a boolean"):
            issues.append(f"{a.key}: {s.issue}")
        scores.append(s)
    passed = parsed.data is not None and all(s.passed for s in scores)
    return Grade(task.id, passed, scores, parsed.error, issues)


# ----------------------------------------------------------------------------------------
# traceability and stream parsing
# ----------------------------------------------------------------------------------------
def numbers_in(text: str) -> list[float]:
    """Every decimal number in ``text``."""
    out = []
    for m in _NUMBER.finditer(text):
        try:
            x = float(m.group(0))
        except ValueError:  # pragma: no cover - the pattern only matches numbers
            continue
        if math.isfinite(x):
            out.append(x)
    return out


def traceable(value: float, numbers: list[float], rel_tol: float = TRACE_REL_TOL) -> bool:
    """True when ``value`` is within ``rel_tol`` of one of ``numbers`` (sign included)."""
    for n in numbers:
        if value == n:
            return True
        scale = max(abs(value), abs(n))
        if scale > 0 and abs(value - n) <= rel_tol * scale:
            return True
    return False


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif "content" in item:
                    parts.append(_content_text(item["content"]))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content)


@dataclass
class StreamSummary:
    """What a stream-json session did."""

    final_text: str | None = None
    result_subtype: str | None = None
    is_error: bool | None = None
    num_turns: int | None = None
    duration_ms: float | None = None
    duration_api_ms: float | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model_usage: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    session_id: str | None = None
    tools_available: list[str] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    tool_errors: int = 0
    tool_names: dict[str, int] = field(default_factory=dict)
    tool_result_texts: list[str] = field(default_factory=list, repr=False)
    tool_input_texts: list[str] = field(default_factory=list, repr=False)
    tool_error_texts: list[str] = field(default_factory=list, repr=False)
    denied: list[str] = field(default_factory=list)
    last_assistant_text: str | None = None
    bad_lines: int = 0
    api_errors: list[str] = field(default_factory=list)
    api_error_status: Any = None

    @property
    def tool_numbers(self) -> list[float]:
        nums: list[float] = []
        for t in self.tool_result_texts:
            nums.extend(numbers_in(t))
        return nums

    @property
    def total_tokens(self) -> int | None:
        if not self.usage:
            return None
        keys = (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        return int(sum(int(self.usage.get(k) or 0) for k in keys))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("tool_result_texts")
        d.pop("tool_input_texts")
        d.pop("tool_error_texts")
        d["total_tokens"] = self.total_tokens
        return d


def parse_stream(lines: list[str] | str) -> StreamSummary:
    """Summarise a ``claude -p --output-format stream-json --verbose`` transcript."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    s = StreamSummary()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            s.bad_lines += 1
            continue
        if not isinstance(msg, dict):
            continue
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "api_retry":
            s.api_errors.append(f"{msg.get('error_status')} {msg.get('error')}")
        elif kind == "system" and msg.get("subtype") == "init":
            s.model = msg.get("model")
            s.session_id = msg.get("session_id")
            s.tools_available = list(msg.get("tools") or [])
            s.mcp_servers = list(msg.get("mcp_servers") or [])
        elif kind == "assistant":
            content = (msg.get("message") or {}).get("content") or []
            texts = []
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    s.tool_calls += 1
                    name = str(item.get("name"))
                    s.tool_names[name] = s.tool_names.get(name, 0) + 1
                    s.tool_input_texts.append(json.dumps(item.get("input"), default=str))
                elif item.get("type") == "text":
                    texts.append(str(item.get("text", "")))
            if texts:
                s.last_assistant_text = "\n".join(texts)
        elif kind == "user":
            content = (msg.get("message") or {}).get("content") or []
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    text = _content_text(item.get("content"))
                    if item.get("is_error"):
                        s.tool_errors += 1
                        s.tool_error_texts.append(text)
                        if "permission" in text.lower() or "denied" in text.lower():
                            s.denied.append(text[:300])
                    else:
                        s.tool_result_texts.append(text)
        elif kind == "result":
            s.result_subtype = msg.get("subtype")
            s.api_error_status = msg.get("api_error_status")
            s.is_error = msg.get("is_error")
            s.final_text = msg.get("result")
            s.num_turns = msg.get("num_turns")
            s.duration_ms = msg.get("duration_ms")
            s.duration_api_ms = msg.get("duration_api_ms")
            s.cost_usd = msg.get("total_cost_usd")
            s.usage = dict(msg.get("usage") or {})
            s.model_usage = dict(msg.get("modelUsage") or {})
            for d in msg.get("permission_denials") or []:
                s.denied.append(json.dumps(d)[:300])
    if s.final_text is None:
        s.final_text = s.last_assistant_text
    return s


def infra_error(s: StreamSummary, outcome: dict[str, Any] | None = None) -> str | None:
    """Why a session failed for reasons outside the agent's control, or None.

    Authentication failures, a CLI that could not start or crashed without output, and API
    errors before any token was used are infrastructure errors: such runs are excluded
    from pass rates and listed separately. Timeouts, turn limits and wrong answers are the
    agent's failures.
    """
    outcome = outcome or {}
    if outcome.get("error"):
        return str(outcome["error"])
    text = (s.final_text or "").strip()
    if any("authentication" in e or e.startswith("401") for e in s.api_errors) or (
        s.is_error and text.startswith(("Not logged in", "Invalid API key"))
    ):
        return "authentication failed (log in to the Claude CLI or set ANTHROPIC_API_KEY)"
    if outcome.get("timed_out"):
        return None
    if s.denied and parse_final_json(s.final_text).data is None:
        # Every tool the harness offers is allowed, so a denial is a harness fault. It only
        # voids the run when the agent then gave no answer; a run that recovered is graded.
        tools = ", ".join(sorted({str(d) for d in s.denied}))
        return f"the harness denied a tool call and no answer followed ({tools})"
    servers = {m.get("name"): m.get("status") for m in s.mcp_servers}
    if servers and any(status != "connected" for status in servers.values()):
        # A session that started before its MCP server connected has no tools; a model may
        # then write tool calls as plain text and invent the results.
        pending = ", ".join(f"{n}={st}" for n, st in servers.items() if st != "connected")
        return f"MCP server not connected at session start ({pending})"
    if s.result_subtype is None and s.tool_calls == 0 and not s.last_assistant_text:
        return f"no output from the CLI (exit code {outcome.get('exit_code')})"
    if s.is_error and not s.total_tokens and (s.api_error_status or s.api_errors):
        return f"API error before any token was used: {s.api_error_status or s.api_errors[-1]}"
    return None


# ----------------------------------------------------------------------------------------
# contamination
# ----------------------------------------------------------------------------------------
#: Markers of the benchmark's own files in a session's tool traffic (matched on lower-case
#: text with backslashes turned into slashes).
BENCH_MARKERS = ("benchmarks/composition", "composition/tasks", "task.schema.json")
#: A frozen answer mapping as the task files write it (``expected: {key: value, ...}``).
_EXPECTED_BLOCK = re.compile(r"(?<![\w\"'])expected:\s*\{\s*[A-Za-z_]\w*:")
#: The library name, except as part of a longer name such as the code environment's
#: ``worldparts-bench`` directory.
_LIBRARY = re.compile(r"(?<![\w-])worldparts(?![\w-])")


def _path_forms(root: Any) -> list[str]:
    """Spellings of a directory path as a tool might print it (lower case, slashes)."""
    p = str(root).replace("\\", "/").rstrip("/").lower()
    forms = {p}
    if len(p) > 2 and p[1] == ":":  # c:/x -> /c/x (Git Bash) and /mnt/c/x (WSL)
        forms.add(f"/{p[0]}{p[2:]}")
        forms.add(f"/mnt/{p[0]}{p[2:]}")
    return sorted(forms)


def _normalise(text: str) -> str:
    """Lower case, with JSON-escaped and plain backslashes turned into slashes."""
    return re.sub(r"\\+", "/", text).lower()


def contamination(
    s: StreamSummary,
    condition: str,
    task_id: str | None = None,
    repo_root: Any = REPO_ROOT,
    extra_texts: Iterable[str] = (),
) -> list[str]:
    """Why a session is contaminated (it looked at the benchmark itself), or ``[]``.

    The code condition runs Python with read access to the whole disk, so an agent could
    open the task files and copy the frozen answers. Every tool input and tool result
    (errors included) is scanned for the benchmark's task directory and schema, the task's
    own file name and a frozen ``expected: {...}`` mapping. In the code condition the
    repository path and any mention of the worldparts library count too: the agent works
    in a temporary directory outside the repository and its Python has no worldparts, so
    only a look into the repository or its virtual environment can produce them. The MCP
    condition legitimately talks to worldparts (whose messages may carry source paths), so
    those two markers are not applied there.
    """
    inputs = list(s.tool_input_texts)
    results = [*s.tool_result_texts, *s.tool_error_texts, *extra_texts]
    roots = _path_forms(repo_root) if repo_root and condition == "code" else []
    task_file = f"{task_id.lower()}.yaml" if task_id else None
    reasons: list[str] = []
    for where, texts in (("tool input", inputs), ("tool result", results)):
        for raw in texts:
            norm = _normalise(raw)
            found = [f"the repository path {r}" for r in roots if r in norm]
            found += [f"'{m}'" for m in BENCH_MARKERS if m in norm]
            if task_file and task_file in norm:
                found.append(f"the task file {task_file}")
            if _EXPECTED_BLOCK.search(raw):
                found.append("a frozen 'expected:' answer mapping")
            if condition == "code" and _LIBRARY.search(norm):
                found.append("the worldparts library")
            for f in found:
                reason = f"{where} mentions {f}"
                if reason not in reasons:
                    reasons.append(reason)
    return reasons
