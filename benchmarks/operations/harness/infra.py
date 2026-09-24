"""Infrastructure-error signatures and the blind defect audit (PREREGISTRATION.md 6.6, 8).

Only these count as infrastructure errors: a CLI, API, transport, harness or permission
failure, a session with no model turn, or an external kill. The rules below detect them
mechanically from three files of a session attempt, ``stream.jsonl``, ``stderr.txt`` and
``outcome.json``, none of which names the arm. The audit reads nothing else: not the
prompt, the command, the run index, the records or the grades.

These are failures of the agent, never infrastructure errors: exceptions raised by
worldparts or by the agent's own code (they are tool results), a timeout of a session in
which the model took at least one turn, the turn limit (``error_max_turns``), the budget
cap (``error_max_budget_usd``) and a context overflow ("prompt is too long").

A session with an infrastructure error is re-run with the same realisation, at most twice
(:data:`MAX_ATTEMPTS` attempts in all), and never excluded: the latest attempt is graded
whatever it holds.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.grading import parse_final_json

#: Attempts per session: the first run and at most two re-runs.
MAX_ATTEMPTS = 3
AUDIT_FILE = "audit.json"
#: The only files of an attempt that the audit reads.
AUDITED_FILES = ("stream.jsonl", "stderr.txt", "outcome.json")


@dataclass(frozen=True)
class Signature:
    name: str
    description: str


#: The pre-registered signatures, in the order they are checked.
SIGNATURES: tuple[Signature, ...] = (
    Signature(
        "harness_interrupted",
        "outcome.json is missing: the harness stopped before it recorded the session.",
    ),
    Signature(
        "harness_start",
        "outcome.json records a harness error: the CLI could not be started or the session "
        "could not be prepared.",
    ),
    Signature(
        "authentication",
        "The CLI was not logged in or its credentials were rejected: an API retry with status "
        "401 or an authentication error in a session that then has no result or an error "
        "result, or an error result beginning 'Not logged in', 'Invalid API key', "
        "'OAuth token' or 'Please run /login'.",
    ),
    Signature(
        "mcp_not_connected",
        "An MCP server of the session was not connected when the session started.",
    ),
    Signature(
        "no_model_turn",
        "The stream has no assistant message: the model never took a turn (whether or not "
        "the session then timed out).",
    ),
    Signature(
        "api_or_cli_error",
        "The result message is an error that is not the turn limit (error_max_turns), the "
        "budget cap (error_max_budget_usd) or a context overflow: an API, transport or CLI "
        "failure.",
    ),
    Signature(
        "permission_denied",
        "The CLI's permission system denied a tool call (the result's permission_denials, or "
        "a CLI denial message in a tool result) and the final reply has no JSON object.",
    ),
    Signature(
        "killed",
        "The model took at least one turn, the stream has no result message and the harness "
        "did not stop the session for its timeout: the process was killed or crashed.",
    ),
)
SIGNATURE_NAMES = tuple(s.name for s in SIGNATURES)

_LIMIT_SUBTYPES = ("error_max_turns", "error_max_budget_usd")
_CONTEXT_OVERFLOW = re.compile(
    r"prompt is too long|context (?:window|length)|input is too long|too many tokens", re.I
)
_AUTH_TEXT = ("not logged in", "invalid api key", "oauth token", "please run /login")
#: How the CLI words a permission denial in a tool result (not an OS "Permission denied").
_CLI_DENIAL = re.compile(
    r"requested permissions? to use|haven't granted it yet|permission to use \S+ (?:was|has "
    r"been) denied",
    re.I,
)


@dataclass
class StreamFacts:
    """What the audit needs from a stream-json transcript."""

    assistant_messages: int = 0
    result: dict[str, Any] | None = None
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    api_retries: list[tuple[Any, str]] = field(default_factory=list)
    denial_texts: list[str] = field(default_factory=list)
    last_assistant_text: str | None = None


def _items(msg: dict[str, Any]) -> list[dict[str, Any]]:
    content = (msg.get("message") or {}).get("content") or []
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def stream_facts(text: str) -> StreamFacts:
    f = StreamFacts()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        kind = msg.get("type")
        if kind == "assistant":
            f.assistant_messages += 1
            texts = [str(c.get("text", "")) for c in _items(msg) if c.get("type") == "text"]
            if texts:
                f.last_assistant_text = "\n".join(texts)
        elif kind == "system" and msg.get("subtype") == "init":
            f.mcp_servers = [m for m in msg.get("mcp_servers") or [] if isinstance(m, dict)]
        elif kind == "system" and msg.get("subtype") == "api_retry":
            f.api_retries.append((msg.get("error_status"), str(msg.get("error") or "")))
        elif kind == "user":
            for item in _items(msg):
                if item.get("type") == "tool_result" and item.get("is_error"):
                    body = item.get("content")
                    body_text = body if isinstance(body, str) else json.dumps(body)
                    if _CLI_DENIAL.search(body_text):
                        f.denial_texts.append(body_text[:300])
        elif kind == "result":
            f.result = msg
    return f


def audit_session(stream_text: str, stderr_text: str, outcome: dict[str, Any] | None) -> list[str]:
    """The names of the signatures an attempt matches (``[]``: not an infrastructure error).

    ``outcome`` is the parsed outcome.json, or None when the file is missing.
    ``stderr_text`` is read so that the rules may use it; none of the current rules does.
    """
    del stderr_text  # recorded for the audit trail; no rule depends on it
    found: list[str] = []
    if outcome is None:
        found.append("harness_interrupted")
        outcome = {}
    if outcome.get("error"):
        found.append("harness_start")
    f = stream_facts(stream_text)
    result = f.result or {}
    result_text = str(result.get("result") or "").strip()
    is_error = bool(result.get("is_error"))
    auth_retry = any(
        str(status) == "401" or "authentication" in err.lower() for status, err in f.api_retries
    )
    # A 401 retry counts only when the session did not then finish normally.
    auth = (auth_retry and (f.result is None or is_error)) or (
        is_error and result_text.lower().startswith(_AUTH_TEXT)
    )
    if auth:
        found.append("authentication")
    if any(m.get("status") != "connected" for m in f.mcp_servers):
        found.append("mcp_not_connected")
    if f.assistant_messages == 0:
        found.append("no_model_turn")
    if (
        f.result is not None
        and is_error
        and not auth
        and result.get("subtype") not in _LIMIT_SUBTYPES
        and not _CONTEXT_OVERFLOW.search(result_text)
    ):
        found.append("api_or_cli_error")
    denied = bool(result.get("permission_denials")) or bool(f.denial_texts)
    if denied:
        final = result.get("result") if f.result is not None else f.last_assistant_text
        if parse_final_json(final if isinstance(final, str) else None).data is None:
            found.append("permission_denied")
    if f.assistant_messages > 0 and f.result is None and not outcome.get("timed_out"):
        found.append("killed")
    return found


# ----------------------------------------------------------------------------------------
# the blind audit of a run directory
# ----------------------------------------------------------------------------------------
def attempt_dirs(session_dir: Path) -> list[Path]:
    """``attempt-<n>`` directories of a session, in attempt order."""
    out = []
    for p in session_dir.glob("attempt-*"):
        m = re.fullmatch(r"attempt-([1-9][0-9]*)", p.name)
        if m and p.is_dir():
            out.append((int(m.group(1)), p))
    return [p for _, p in sorted(out)]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def audit_attempt(attempt_dir: Path) -> list[str]:
    """Audit one attempt directory, reading only :data:`AUDITED_FILES`."""
    stream = _read(attempt_dir / "stream.jsonl")
    stderr = _read(attempt_dir / "stderr.txt")
    outcome: dict[str, Any] | None
    try:
        outcome = json.loads((attempt_dir / "outcome.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        outcome = None
    if outcome is not None and not isinstance(outcome, dict):
        outcome = None
    return audit_session(stream, stderr, outcome)


def _fingerprint(attempt_dir: Path) -> str:
    h = hashlib.sha256()
    for name in AUDITED_FILES:
        p = attempt_dir / name
        h.update(name.encode() + b"\0")
        h.update(p.read_bytes() if p.is_file() else b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def audit_run(run_dir: Path) -> dict[str, Any]:
    """Audit the latest attempt of every session under ``run_dir/sessions`` and write
    ``audit.json``. Sessions are named by their opaque ids only; the arm, model and task
    are never read. Returns the audit document."""
    sessions: dict[str, Any] = {}
    root = run_dir / "sessions"
    for sdir in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        attempts = attempt_dirs(sdir)
        if not attempts:
            continue
        latest = attempts[-1]
        sigs = audit_attempt(latest)
        sessions[sdir.name] = {
            "attempt": len(attempts),
            "signatures": sigs,
            "flagged": bool(sigs),
            "rerun_allowed": bool(sigs) and len(attempts) < MAX_ATTEMPTS,
            "fingerprint": _fingerprint(latest),
        }
    doc = {
        "rules": [{"name": s.name, "description": s.description} for s in SIGNATURES],
        "max_attempts": MAX_ATTEMPTS,
        "sessions": sessions,
        "flagged": sorted(k for k, v in sessions.items() if v["flagged"]),
        "pending_reruns": sorted(k for k, v in sessions.items() if v["rerun_allowed"]),
    }
    (run_dir / AUDIT_FILE).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def audit_lines(doc: dict[str, Any]) -> list[str]:
    """The audit as printable lines: opaque session ids and signature names only."""
    lines = []
    for sid in doc["flagged"]:
        s = doc["sessions"][sid]
        allowed = "re-run allowed" if s["rerun_allowed"] else "no re-run left: graded as is"
        lines.append(
            f"FLAGGED {sid} attempt {s['attempt']}/{doc['max_attempts']}: "
            f"{', '.join(s['signatures'])} ({allowed})"
        )
    n = len(doc["sessions"])
    lines.append(
        f"audited {n} session(s): {len(doc['flagged'])} flagged, "
        f"{len(doc['pending_reruns'])} to re-run"
    )
    return lines
