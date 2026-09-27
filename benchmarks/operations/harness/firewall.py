"""The firewall audit of builder transcripts (PREREGISTRATION.md section 7, FREEZES.md
"Build phase after Stage 0").

``python -m benchmarks.operations.harness firewall-audit TRANSCRIPT.jsonl [...]`` reads
Claude Code transcripts (a session's ``.jsonl`` and its subagents' ``.jsonl`` files, or a
``stream.jsonl`` of ``claude -p``) and scans the input of every tool call an assistant
message makes: the command of ``Bash`` and ``PowerShell``, the file path of ``Read``,
``Write``, ``Edit``, ``MultiEdit``, ``NotebookEdit`` and ``NotebookRead``, the path and
glob of ``Grep``, the pattern and path of ``Glob``, the path of ``LS``, and the path-like
fields (:data:`PATH_FIELDS`) of any other tool. Messages, subagent prompts and tool results
are not scanned. Text is compared in lower case with backslashes as slashes.

**Violations** (a builder session must not read them):

- :data:`FORBIDDEN`: the private folder (``worldparts-opsbench``) and the generator
  (``opsim``), truth files and folders (``*.truth.json``, a path component ``truth``),
  grader records (``record.json``), run summaries (``summary.md``, ``summary.json``),
  headroom results (``headroom*.json``), pass/fail logs (``pass-fail.log``), the owner's
  ``reports/`` (not ``benchmarks/composition/reports``), ``research_notes``, the Claude
  memory folder (``.claude/projects``, ``MEMORY.md``) and the truth folder's variable
  (``WPBENCH_OPS_TRUTH``);
- the truth folder by path (``--truth``, else ``$WPBENCH_OPS_TRUTH`` of the auditing
  shell) and any ``--forbid`` path, in its Windows, Git Bash and WSL spellings;
- the draft history of PREREGISTRATION.md: a ``git`` command of :data:`GIT_HISTORY`
  (``log``, ``show``, ``diff``, ``blame``, ...) that names it;
- a recursive search (``grep -r``, ``rg``, ``Select-String``, ``find -exec``, ``xargs``,
  the ``Grep`` tool) or a wildcard read (``cat``, ``type``, ``cp``, ...) over a run
  directory at a level that holds its grader files (the run, ``sessions``, a session
  folder), unless it is restricted to transcript files (``--include``, ``-g``, ``-name``,
  a ``Grep`` glob or type): it reads ``record.json`` without naming it
  (:func:`run_tree_findings`);
- more than :data:`.score.MAX_EVALUATIONS` pass/fail evaluations in one session.

**Warnings** (the owner reviews them): a forbidden path named by a tool that writes a file
(``Write``, ``Edit``, ...: :data:`WRITE_TOOLS`) without reading it, ``git log`` with
patches, or ``git show`` of a commit, without a path (either can show PREREGISTRATION.md's
history), several session names given to the evaluation wrapper within one session, and
unreadable lines.

**Evaluations.** A command that runs ``tools/ops_passfail.py``, ``harness score ...
--pass-fail`` or ``harness grade ... --pass-fail`` is one evaluation (each run in a
command counts; ``--help`` does not). Evaluations are counted per session: the
``sessionId`` of the transcript's lines (a subagent's transcript carries its parent's),
else the file name. A run hidden in a script file is not seen here; the wrapper's own log
(``pass-fail.log``, at most 3 evaluations per session name) is the second count.

The verdict is ``CLEAN``, ``CLEAN, with N warning(s) to review`` or ``VIOLATIONS``. Like
the contamination markers, the audit is by markers: a deliberately disguised read would
pass it, so the transcripts are kept.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.grading import _normalise, _path_forms, _path_pattern

from .score import MAX_EVALUATIONS

#: (what, pattern on normalised text).
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("the private folder 'worldparts-opsbench'", re.compile(r"worldparts-opsbench")),
    ("the generator 'opsim'", re.compile(r"(?<![a-z0-9])opsim(?![a-z])")),
    ("a truth file ('*.truth.json')", re.compile(r"truth\.json")),
    ("a truth folder (a path component 'truth')",
     re.compile(r"/truth(?![\w.-])|(?<![\w.-])truth/")),
    ("a grader record ('record.json')", re.compile(r"(?<![\w-])record\.json")),
    ("a run summary ('summary.md' or 'summary.json')",
     re.compile(r"(?<![\w-])summary\.(?:md|json)(?![\w])")),
    ("a headroom result ('headroom*.json')",
     re.compile(r"(?<![\w-])headroom[^/\s'\"]*\.json")),
    ("a pass/fail log ('pass-fail.log')", re.compile(r"pass-fail\.log")),
    ("the reports folder ('reports/')",
     re.compile(r"(?<!composition)/reports(?![\w.-])|(?<!composition/)(?<![\w.-])reports/")),
    ("the research notes ('research_notes')", re.compile(r"research_notes")),
    ("the Claude memory folder ('.claude/projects')", re.compile(r"\.claude/projects")),
    ("the Claude memory file ('MEMORY.md')", re.compile(r"(?<![\w-])memory\.md")),
    ("the truth folder's variable ('WPBENCH_OPS_TRUTH')", re.compile(r"wpbench_ops_truth")),
)  # fmt: skip
#: git subcommands that read history; naming PREREGISTRATION.md with one is a violation.
GIT_HISTORY = frozenset(
    {"log", "show", "diff", "blame", "annotate", "whatchanged", "format-patch", "cat-file",
     "checkout", "restore", "reflog", "difftool", "archive", "grep", "rev-list", "range-diff"}
)  # fmt: skip
#: Tool-input fields scanned for every tool (besides a shell's ``command``).
PATH_FIELDS = ("file_path", "notebook_path", "path", "paths", "glob")
SHELL_TOOLS = ("Bash", "PowerShell")
#: Tools that write a file without showing its content; a forbidden path in their input is
#: a warning.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
#: Tools whose inputs hold no path or command (messages, plans, subagent prompts).
UNSCANNED_TOOLS = frozenset(
    {"Task", "Agent", "TodoWrite", "WebFetch", "WebSearch", "SendMessage", "ToolSearch",
     "ExitPlanMode", "AskUserQuestion", "Skill", "SlashCommand", "KillShell", "BashOutput",
     "TaskOutput", "TaskStop", "Monitor", "StructuredOutput"}
)  # fmt: skip

_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_GIT = re.compile(
    r"(?<![\w-])git(?:\.exe)?((?:\s+(?:-c\s+\S+|-C\s+\S+|--[\w-]+(?:=\S+)?|-p|-P))*)\s+"
    r"([a-z][\w-]*)(.*)"
)
_EVALUATIONS = (
    re.compile(r"(?:\bpython[\w.]*(?:\.exe)?|\buv\s+run)(?:\s+-{1,2}[\w-]+(?:[= ]\S+)?)*"
               r"\s+\S*ops_passfail(?:\.py)?(?![\w])", re.I),
    re.compile(r"\bpython[\w.]*(?:\.exe)?\s+(?:-\S+\s+)*-m\s+benchmarks\.operations\.harness\s+"
               r"(?:score|grade)\b.*--pass-fail", re.I),
)  # fmt: skip
_SESSION_ARG = re.compile(r"--session(?:\s+|=)(\S+)")
_HELP = re.compile(r"(?:^|\s)(?:-h|--help)(?:\s|$)")
_PATCH_FLAGS = ("-p", "-u", "--patch", "--word-diff", "--cc", "--full-diff")
_NO_PATCH_FLAGS = ("--stat", "--name-only", "--name-status", "--no-patch", "-s", "--shortstat",
                   "--numstat", "--summary", "--oneline")  # fmt: skip


@dataclass
class Finding:
    file: str
    line: int
    tool: str
    level: str  # "violation" or "warning"
    reason: str
    text: str


@dataclass
class TranscriptAudit:
    file: str
    session: str
    tool_calls: int = 0
    scanned: int = 0
    unscanned: dict[str, int] = field(default_factory=dict)
    evaluations: int = 0
    session_names: list[str] = field(default_factory=list)
    bad_lines: int = 0


@dataclass
class AuditResult:
    transcripts: list[TranscriptAudit]
    findings: list[Finding]
    evaluations: dict[str, int]
    session_names: dict[str, list[str]]

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "violation"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def verdict(self) -> str:
        if self.violations:
            return f"VIOLATIONS ({len(self.violations)})"
        if self.warnings:
            return f"CLEAN, with {len(self.warnings)} warning(s) to review"
        return "CLEAN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "clean": not self.violations,
            "transcripts": [asdict(t) for t in self.transcripts],
            "evaluations": self.evaluations,
            "session_names": self.session_names,
            "findings": [asdict(f) for f in self.findings],
        }


def tool_uses(path: Path) -> Iterator[tuple[int, str, dict[str, Any], str | None]]:
    """(line number, tool name, input, session id) of every tool call in a transcript;
    unreadable lines yield ``(n, "", {}, None)``."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                yield n, "", {}, None
                continue
            if not isinstance(msg, dict) or msg.get("type") != "assistant":
                continue
            sid = msg.get("sessionId") or msg.get("session_id")
            content = (msg.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    inp = item.get("input")
                    yield n, str(item.get("name") or "?"), inp if isinstance(inp, dict) else {}, sid


def session_of(path: Path) -> str:
    """The transcript's session id (a subagent's transcript carries its parent's), else
    the file name without its extension."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                sid = msg.get("sessionId") or msg.get("session_id")
                if sid:
                    return str(sid)
    return path.stem


def scanned_texts(tool: str, inp: dict[str, Any]) -> list[str] | None:
    """The input texts of a tool call that are scanned (None: the tool is not scanned)."""
    if tool in UNSCANNED_TOOLS:
        return None
    texts: list[str] = []
    cmd = inp.get("command")
    if isinstance(cmd, str):
        texts.append(cmd)
    for key in PATH_FIELDS:
        v = inp.get(key)
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, list):
            texts += [x for x in v if isinstance(x, str)]
    if tool == "Glob" and isinstance(inp.get("pattern"), str):
        texts.append(inp["pattern"])
    return texts


def _has_pathspec(args: list[str]) -> bool:
    if "--" in args:
        return True
    return any(
        not a.startswith("-") and ("/" in a or re.search(r"\.[a-z0-9]{1,5}$", a)) for a in args
    )


def git_findings(command: str) -> list[tuple[str, str]]:
    """(level, reason) for the git commands of a shell command (see the module docstring)."""
    out: list[tuple[str, str]] = []
    for segment in _SEGMENT.split(_normalise(command)):
        m = _GIT.search(segment)
        if not m:
            continue
        sub, rest = m.group(2), m.group(3)
        args = rest.split()
        if "prereg" in rest and sub in GIT_HISTORY:
            out.append(("violation", f"the draft history of PREREGISTRATION.md (git {sub})"))
        elif sub == "log" and any(a.startswith(_PATCH_FLAGS) for a in args):
            if not _has_pathspec(args):
                out.append(("warning", "git log with patches over the whole repository (it "
                                       "can show PREREGISTRATION.md's history)"))  # fmt: skip
        elif (
            sub in ("show", "whatchanged", "format-patch")
            and not _has_pathspec(args)
            and not any(a in _NO_PATCH_FLAGS or a.startswith("--format") for a in args)
        ):
            out.append(("warning", f"git {sub} of whole commits (it can show "
                                   "PREREGISTRATION.md's history)"))  # fmt: skip
    return out


#: A recursive search, or a command that reads many files (with a wildcard or ``xargs``).
_RECURSIVE = re.compile(
    r"\b[efz]?grep\b[^\n]*?\s-[a-z]*r[a-z]*\b|--recursive|(?<![\w-])(?:rg|ag|ack)(?![\w-])|"
    r"\bfindstr\b[^\n]*?\s/s\b|\bselect-string\b|(?<![\w-])sls(?![\w-])|-recurse\b|"
    r"\bdir\b[^\n]*?\s/s\b|\bxargs\b|\bfind\b[^\n]*?-exec"
)
_READ_MANY = re.compile(
    r"(?<![\w-])(?:cat|type|get-content|gc|head|tail|more|less|bat|zip|tar|cp|copy|"
    r"copy-item|robocopy|xcopy)(?![\w-])"
)
_INCLUDE = re.compile(
    r"(?:--include|--glob|(?<![\w-])-g|-include|-name|-iname|-filter)(?:=|\s+)['\"]?([^\s'\"]+)"
)
#: The files of a run directory a builder may read by a search or a wildcard: transcripts
#: (``stream.jsonl``), prompts and stderr (``.txt``) and the files agents wrote.
ALLOWED_RUN_FILES = (".jsonl", ".txt", ".py", ".csv", ".inp")


def run_tree_paths(norm: str) -> list[str]:
    """Paths in ``norm`` (normalised text) into a run directory (``.../operations/results/``
    or a relative ``results/``) that can reach its grader files: the run, its ``sessions``
    or a session folder, or a wildcard at those levels. A path inside an attempt folder
    (``sessions/<id>/attempt-<n>``), a named file, and a pattern for allowed file types
    are not among them."""
    out: list[str] = []
    for token in re.split(r"[\s'\"=,;()|&<>]+", norm):
        if "operations/results" in token:
            rest = token.split("operations/results", 1)[1]
        elif token.startswith(("results/", "./results/")) or token in ("results", "./results"):
            rest = token.split("results", 1)[1]
        else:
            continue
        segs = [s for s in rest.split("/") if s]
        if len(segs) >= 4 and segs[1] == "sessions" and segs[3].startswith("attempt-"):
            continue
        last = segs[-1] if segs else ""
        if last.endswith(ALLOWED_RUN_FILES):
            continue
        if "." in last and not any(c in last for c in "*?["):
            continue  # one named file: forbidden names are markers of their own
        out.append(token)
    return out


def _restricted(includes: list[str]) -> bool:
    return bool(includes) and all(p.endswith(ALLOWED_RUN_FILES) for p in includes)


RUN_TREE_REASON = (
    "a search or wildcard read over a run directory (it reads record.json, summary.* and "
    "pass-fail.log; restrict it to the transcripts, e.g. --include='*.jsonl')"
)


def run_tree_findings(command: str) -> list[str]:
    """Why a shell command reads a run directory's grader files wholesale, or ``[]``."""
    norm = _normalise(command)
    paths = run_tree_paths(norm)
    if not paths:
        return []
    wildcard = any(any(c in p for c in "*?[") for p in paths)
    if not (_RECURSIVE.search(norm) or (wildcard and _READ_MANY.search(norm))):
        return []
    if _restricted(_INCLUDE.findall(norm)):
        return []
    return [RUN_TREE_REASON]


def grep_tool_findings(inp: dict[str, Any]) -> list[str]:
    """The same for the ``Grep`` tool, which always searches recursively."""
    path = inp.get("path")
    if not isinstance(path, str) or not run_tree_paths(_normalise(path)):
        return []
    glob = inp.get("glob")
    if isinstance(glob, str) and _restricted([_normalise(glob)]):
        return []
    if inp.get("type") in ("py", "txt", "csv"):
        return []
    return [RUN_TREE_REASON]


def evaluations_in(command: str) -> tuple[int, list[str]]:
    """(number of pass/fail evaluations a shell command runs, the session names given)."""
    n = 0
    names: list[str] = []
    for segment in _SEGMENT.split(command.replace("\\\n", " ")):
        if _HELP.search(segment):
            continue
        hits = sum(len(p.findall(segment)) for p in _EVALUATIONS)
        if hits:
            n += hits
            names += _SESSION_ARG.findall(segment)
    return n, names


def forbidden_patterns(
    truth_roots: Iterable[Path | str] = (), extra: Iterable[Path | str] = ()
) -> list[tuple[str, re.Pattern[str]]]:
    """:data:`FORBIDDEN` plus the truth folders and the extra paths in every spelling."""
    out = list(FORBIDDEN)
    for label, roots in (("the truth folder", truth_roots), ("the forbidden path", extra)):
        for root in roots:
            out += [(f"{label} {form}", _path_pattern(form)) for form in _path_forms(root)]
    return out


def _snippet(text: str, limit: int = 160) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 3] + "..."


def audit_transcripts(
    paths: list[Path],
    truth_roots: Iterable[Path | str] = (),
    extra: Iterable[Path | str] = (),
) -> AuditResult:
    """Audit the transcripts (see the module docstring)."""
    patterns = forbidden_patterns(truth_roots, extra)
    findings: list[Finding] = []
    audits: list[TranscriptAudit] = []
    evaluations: dict[str, int] = {}
    names: dict[str, list[str]] = {}
    for path in paths:
        ta = TranscriptAudit(str(path), session_of(path))
        audits.append(ta)
        for n, tool, inp, _sid in tool_uses(path):
            if not tool:
                ta.bad_lines += 1
                continue
            ta.tool_calls += 1
            texts = scanned_texts(tool, inp)
            if texts is None:
                ta.unscanned[tool] = ta.unscanned.get(tool, 0) + 1
                continue
            ta.scanned += 1
            # A write names a path without reading it (an edit of a file needs a read of
            # it first, which is scanned on its own): a warning, not a violation.
            level = "warning" if tool in WRITE_TOOLS else "violation"
            for text in texts:
                norm = _normalise(text)
                reasons = [what for what, pat in patterns if pat.search(norm)]
                for what in dict.fromkeys(reasons):
                    findings.append(Finding(str(path), n, tool, level, what, _snippet(text)))
            if tool == "Grep":
                for reason in grep_tool_findings(inp):
                    findings.append(Finding(str(path), n, tool, "violation", reason,
                                            _snippet(json.dumps(inp))))  # fmt: skip
            if tool in SHELL_TOOLS or isinstance(inp.get("command"), str):
                cmd = inp.get("command") or ""
                for level, reason in git_findings(cmd):
                    findings.append(Finding(str(path), n, tool, level, reason, _snippet(cmd)))
                for reason in run_tree_findings(cmd):
                    findings.append(Finding(str(path), n, tool, "violation", reason,
                                            _snippet(cmd)))  # fmt: skip
                k, given = evaluations_in(cmd)
                ta.evaluations += k
                ta.session_names += [g for g in given if g not in ta.session_names]
        if ta.bad_lines:
            findings.append(Finding(str(path), 0, "", "warning",
                                    f"{ta.bad_lines} unreadable line(s)", ""))  # fmt: skip
        evaluations[ta.session] = evaluations.get(ta.session, 0) + ta.evaluations
        known = names.setdefault(ta.session, [])
        known += [g for g in ta.session_names if g not in known]
    for session, k in evaluations.items():
        where = ", ".join(t.file for t in audits if t.session == session)
        if k > MAX_EVALUATIONS:
            findings.append(Finding(where, 0, "", "violation",
                                    f"session {session}: {k} pass/fail evaluations (at most "
                                    f"{MAX_EVALUATIONS} per session)", ""))  # fmt: skip
        if len(names.get(session, [])) > 1:
            given = ", ".join(names[session])
            findings.append(Finding(where, 0, "", "warning",
                                    f"session {session} gave several session names to the "
                                    f"evaluation wrapper: {given}", ""))  # fmt: skip
    return AuditResult(audits, findings, evaluations, names)


def audit_lines(res: AuditResult) -> list[str]:
    lines = [f"firewall audit of {len(res.transcripts)} transcript(s)"]
    for t in res.transcripts:
        unscanned = ", ".join(f"{k} {v}" for k, v in sorted(t.unscanned.items()))
        lines.append(
            f"{t.file}: session {t.session}; {t.tool_calls} tool call(s), {t.scanned} scanned"
            + (f" (not scanned: {unscanned})" if unscanned else "")
            + f"; {t.evaluations} pass/fail evaluation(s)"
        )
    for f in res.findings:
        where = f"{f.file} line {f.line}" if f.line else f.file
        tool = f" {f.tool}" if f.tool else ""
        text = f": {f.text}" if f.text else ""
        lines.append(f"  {f.level.upper()} {where}{tool}: {f.reason}{text}")
    lines.append("pass/fail evaluations per session:")
    for session, k in sorted(res.evaluations.items()):
        given = res.session_names.get(session) or []
        lines.append(
            f"  {session}: {k} of at most {MAX_EVALUATIONS}"
            + (f" (session names: {', '.join(given)})" if given else "")
        )
    lines.append(f"verdict: {res.verdict}")
    return lines
