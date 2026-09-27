"""The firewall audit of builder transcripts (PREREGISTRATION.md section 7, FREEZES.md
"Build phase after Stage 0").

``python -m benchmarks.operations.harness firewall-audit TRANSCRIPT.jsonl [...]`` reads
Claude Code transcripts (a session's ``.jsonl`` and its subagents' ``.jsonl`` files, or a
``stream.jsonl`` of ``claude -p``) and scans the input of every tool call an assistant
message makes: the command of ``Bash`` and ``PowerShell``, the file path of ``Read``,
``Write``, ``Edit``, ``MultiEdit``, ``NotebookEdit`` and ``NotebookRead`` and the text
that the writing tools write (:func:`written_texts`), the path and glob of ``Grep``, the
pattern and path of ``Glob``, the path of ``LS``, the URL of ``WebFetch``, and the
path-like fields (:data:`PATH_FIELDS`) of any other tool. Messages, subagent prompts and
tool results are not scanned. Text is compared in lower case with backslashes as slashes.

**Violations** (a builder session must not do them):

- :data:`FORBIDDEN`: the private folder (``worldparts-opsbench``) and the generator
  (``opsim``), truth files and folders (``*.truth.json``, a path component ``truth``),
  grader records (``record.json``), run summaries (``summary.md``, ``summary.json``),
  headroom results (``headroom*.json``), the owner's scores (``score.json``), pass/fail
  logs (``pass-fail.log``), the owner's ``reports/`` (not
  ``benchmarks/composition/reports``), ``research_notes``, the Claude memory folder
  (``.claude/projects``, ``MEMORY.md``), the owner's benchmark folder under
  ``%LOCALAPPDATA%`` (``worldparts-bench``: environments and the pass/fail log) and the
  owner's variables (``WPBENCH_OPS_TRUTH``, ``WPBENCH_OPS_PASSFAIL_*``);
- wildcard spellings of those names (``*opsbench*``, ``tru*``, ``rec*.json``) and a change
  of directory into one of them (``cd reports``, ``cd ~/.claude``):
  :func:`wildcard_findings`, :func:`cd_findings`;
- the truth folder by path (``--truth``, else ``$WPBENCH_OPS_TRUTH`` or
  ``$WPBENCH_OPS_PASSFAIL_TRUTH`` of the auditing shell) and any ``--forbid`` path, in its
  Windows, Git Bash and WSL spellings;
- the draft history of PREREGISTRATION.md (:func:`git_findings`, :func:`web_findings`):
  a ``git`` command of :data:`GIT_HISTORY` (``log``, ``show``, ``diff``, ``blame``, ...)
  that names it, or that shows old content of a folder holding it (``git log -p --
  benchmarks/operations``, ``git show REV -- .``, ``git checkout REV -- benchmarks``); a
  command or URL that fetches its history from GitHub (``commits?path=``, a commit id,
  ``raw.githubusercontent.com/<commit>/...``);
- a recursive search, copy or archive (``grep -r``, ``rg --no-ignore``, ``find -exec``,
  ``xargs``, ``cp -r``, ``robocopy``, ``tar``, ``zip -r``, ``Copy-Item -Recurse``, ...)
  or a wildcard read over a run directory, or over a folder above one (the repository
  root, ``.``, ``benchmarks``, a checkout such as the Stage 0 worktree), unless it is
  restricted to transcript and code files (``--include``, ``-g``, ``-name``, a ``Grep``
  glob or type): it reads ``record.json`` without naming it (:func:`run_tree_findings`,
  :func:`recursive_findings`). ``rg``, the ``Grep`` tool and ``git grep`` skip the
  git-ignored run folders, so they are violations only over a folder above the
  repository (``..``, the home folder, a drive), where nothing is ignored; so is any
  recursive search or listing there, however restricted;
- the owner's commands: ``harness score`` without ``--pass-fail``, ``grade`` without
  it, ``headroom`` and ``report``; the pass/fail mode with ``--truth``,
  ``--truth-manifest``, ``--bundles`` or ``--out``; the harness's truth functions
  (:data:`TRUTH_API`) called from Python; setting ``WPBENCH_OPS_SESSION`` (which resets the
  evaluation count): :func:`harness_findings`, :func:`env_findings`;
- a change to the benchmark harness, its committed lists, the evaluation wrapper or the
  benchmark's tests (a write tool, or a shell redirect, ``sed -i``, ``cp``, ``rm``, ...
  to them; the quick reference, which build track A writes, excepted):
  :func:`protected_write_findings`;
- other sessions' transcripts (the session tools of :data:`TRANSCRIPT_TOOLS`,
  ``claude --resume`` or ``--continue``) and the owner's terminal;
- more than :data:`.score.MAX_EVALUATIONS` pass/fail evaluations in one session.

A script written by a write tool (``.py``, ``.sh``, ``.ps1``, ..., or no suffix:
:data:`SCRIPT_SUFFIXES`) is scanned like a shell command, since running it does what it
says; any other text a write tool writes (notes, docs) gives warnings.

**Warnings** (the owner reviews them): a forbidden path named by a tool that writes a file
(``Write``, ``Edit``, ...: :data:`WRITE_TOOLS`) without reading it, or in a written text
that is not a script; ``git log`` with patches, or ``git show`` of a commit, without a
path; ``git diff``, ``checkout``, ``switch``, ``worktree add`` or ``reset --hard`` with an
old revision and no path (either can show PREREGISTRATION.md's history); setting
``WPBENCH_OPS_BUNDLES`` (the pass/fail mode checks the bundles against the committed
manifest, so this is only reviewed); an evaluation inside a loop, ``xargs`` or a
background job (it may run more often than it is counted), or written into a script;
several session names given to the evaluation wrapper within one session; unreadable
lines.

**Evaluations.** A shell command segment that runs ``tools/ops_passfail.py`` (by any
launcher: ``uv run``, ``python``, ``py``, ``$PY``, ``./tools/ops_passfail.py``,
``runpy``) or the harness's ``score``/``grade`` with ``--pass-fail`` is one evaluation
(a segment whose command only reads the file, and ``--help``, are not). Evaluations are
counted per session: the ``sessionId`` of the transcript's lines (a subagent's transcript
carries its parent's), else the file name. The wrapper's own log, kept by the owner and
keyed by the session name the owner sets, is the primary count (:mod:`.score`); this
count is the second.

The verdict is ``CLEAN``, ``CLEAN, with N warning(s) to review`` or ``VIOLATIONS``. Like
the contamination markers, the audit is by markers: a deliberately disguised read would
pass it, so the transcripts are kept.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.composition.harness.grading import _normalise, _path_forms, _path_pattern

from .bundles import REPO_ROOT
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
    ("the owner's scores ('score.json')", re.compile(r"(?<![\w-])score\.json")),
    ("a pass/fail log ('pass-fail.log')", re.compile(r"pass-fail\.log")),
    ("the reports folder ('reports/')",
     re.compile(r"(?<!composition)/reports(?![\w.-])|(?<!composition/)(?<![\w.-])reports/")),
    ("the research notes ('research_notes')", re.compile(r"research_notes")),
    ("the Claude memory folder ('.claude/projects')", re.compile(r"\.claude/projects")),
    ("the Claude memory file ('MEMORY.md')", re.compile(r"(?<![\w-])memory\.md")),
    ("the truth folder's variable ('WPBENCH_OPS_TRUTH')", re.compile(r"wpbench_ops_truth")),
    ("the pass/fail mode's variables ('WPBENCH_OPS_PASSFAIL_*')",
     re.compile(r"wpbench_ops_passfail")),
    ("the owner's benchmark folder under LOCALAPPDATA ('worldparts-bench')",
     re.compile(r"(?:appdata/local|localappdata[%)}]?)/worldparts-bench")),
)  # fmt: skip
#: The forbidden names, for their wildcard spellings (:func:`wildcard_findings`).
FORBIDDEN_NAMES = (
    "worldparts-opsbench", "opsim", "truth", "ops-f1-001.truth.json", "record.json",
    "summary.md", "summary.json", "headroom.json", "score.json", "pass-fail.log", "reports",
    "research_notes", ".claude", "memory.md",
)  # fmt: skip
#: Folders a builder never changes into.
FORBIDDEN_DIRS = frozenset({"truth", "reports", "research_notes", ".claude"})
#: git subcommands that read history; naming PREREGISTRATION.md with one is a violation.
GIT_HISTORY = frozenset(
    {"log", "show", "diff", "blame", "annotate", "whatchanged", "format-patch", "cat-file",
     "checkout", "restore", "reflog", "difftool", "archive", "grep", "rev-list", "range-diff"}
)  # fmt: skip
PREREG_PATH = "benchmarks/operations/preregistration.md"
#: Tool-input fields scanned for every tool (besides a shell's ``command``).
PATH_FIELDS = ("file_path", "notebook_path", "path", "paths", "glob", "url")
SHELL_TOOLS = ("Bash", "PowerShell")
#: Tools that write a file without showing its content; a forbidden path in their input is
#: a warning.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
#: Tools whose inputs hold no path or command (messages, plans, subagent prompts).
UNSCANNED_TOOLS = frozenset(
    {"Task", "Agent", "TodoWrite", "WebSearch", "SendMessage", "ToolSearch",
     "ExitPlanMode", "AskUserQuestion", "Skill", "SlashCommand", "KillShell", "BashOutput",
     "TaskOutput", "TaskStop", "Monitor", "StructuredOutput"}
)  # fmt: skip
#: Tools that read other sessions' transcripts or the owner's terminal (what the
#: orchestrating session has seen): a violation whatever their input.
TRANSCRIPT_TOOLS = re.compile(
    r"transcript|(?:^|__)get_session$|(?:^|__)list_events$|read_terminal", re.I
)
#: A written file that runs (scanned like a shell command); other written text gives
#: warnings.
SCRIPT_SUFFIXES = (".py", ".pyw", ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd",
                   ".ipynb", ".js", ".mjs", ".ts", ".r", ".jl", ".pl", ".rb")  # fmt: skip
#: The harness's functions that read the truth or grade against it.
TRUTH_API = re.compile(
    r"(?<![\w])(?:load_truth|truth_root|truth_path|truth_listed|passfail_truth_root|"
    r"score_answers|grade_answer|grade_file|grade_session)(?![\w])"
)
#: What a builder never changes (the quick reference, written by build track A, aside).
PROTECTED = re.compile(
    r"benchmarks/operations/(?!preambles/quick-reference\.txt(?![\w.-]))|"
    r"(?<![\w-])tools/ops_passfail|tests/test_ops_bench|tests/fixtures/opsbench"
)

_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_GIT = re.compile(
    r"(?<![\w-])git(?:\.exe)?((?:\s+(?:-c\s+\S+|-C\s+\S+|--[\w-]+(?:=\S+)?|-p|-P))*)\s+"
    r"([a-z][\w-]*)(.*)"
)
_WRAPPER = re.compile(r"(?<![\w])ops_passfail(?![\w])")
_HARNESS_REF = re.compile(
    r"benchmarks[./]operations(?:[./]harness|\s+import\s)|harness[./]__main__"
)
_PASS_FAIL = re.compile(r"pass[-_]fail")
_SESSION_ARG = re.compile(r"--session(?:\s+|=)(\S+)")
_HELP = re.compile(r"(?:^|\s)(?:-h|--help)(?:\s|$)")
_PATCH_FLAGS = ("-p", "-u", "--patch", "--word-diff", "--cc", "--full-diff")
_NO_PATCH_FLAGS = ("--stat", "--name-only", "--name-status", "--no-patch", "-s", "--shortstat",
                   "--numstat", "--summary", "--oneline")  # fmt: skip
#: Commands that only read, list, search or edit text: a segment led by one never runs an
#: evaluation or the harness.
_NOT_RUNS = frozenset(
    {"cat", "type", "head", "tail", "less", "more", "bat", "grep", "egrep", "fgrep", "zgrep",
     "rg", "ag", "ack", "sed", "awk", "wc", "ls", "dir", "gci", "get-childitem",
     "get-content", "gc", "select-string", "sls", "findstr", "find", "file", "stat", "diff",
     "git", "code", "vim", "vi", "nano", "notepad", "echo", "printf", "cp", "copy", "mv",
     "move", "rm", "del", "touch", "jq", "sort", "uniq", "cut", "strings", "xxd", "od",
     "which", "where", "get-command", "test", "[", "ruff", "pytest", "mypy", "write-output",
     "write-host", "tee", "chmod", "copy-item", "move-item", "remove-item", "set-content",
     "out-file", "add-content", "new-item"}
)  # fmt: skip
#: Commands that list names only (a wildcard in their arguments reads no file).
_LISTERS = frozenset({"ls", "dir", "gci", "get-childitem", "tree", "du", "stat", "find",
                      "test", "[", "cd", "pushd", "set-location", "sl", "chdir", "mkdir",
                      "echo", "printf"})  # fmt: skip
_PREFIX_WORDS = frozenset({"sudo", "time", "nohup", "env", "command", "exec", "then", "do",
                           "else", "elif", "if", "while", "until", "!", "&", "call", "{",
                           "(", "builtin", "noglob"})  # fmt: skip
_LOOP = re.compile(
    r"(?<![\w-])(?:for|while|until|foreach|foreach-object|xargs|parallel|watch|start-job|"
    r"start-process)(?![\w-])|%\s*\{|[^\s&|;>(]\s*&(?![&>\d])"
)
#: Readers (besides :data:`_NOT_RUNS`) that turn a recursive listing into a read.
_CONTENT_READERS = frozenset({"cat", "type", "get-content", "gc", "select-string", "sls",
                              "grep", "egrep", "fgrep", "findstr", "copy-item", "cp", "xargs",
                              "head", "tail", "less", "more", "jq", "awk", "sed"})  # fmt: skip


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
    """The input texts of a tool call that are scanned for paths (None: the tool is not
    scanned). The text a write tool writes is :func:`written_texts`."""
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


def written_texts(tool: str, inp: dict[str, Any]) -> list[str]:
    """The text a write tool writes: ``content`` (Write), ``new_string`` (Edit), each
    edit's ``new_string`` (MultiEdit) or ``new_source`` (NotebookEdit)."""
    out: list[str] = []
    for key in ("content", "new_string", "new_source"):
        if isinstance(inp.get(key), str):
            out.append(inp[key])
    for edit in inp.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            out.append(edit["new_string"])
    return out if tool in WRITE_TOOLS else []


def is_script(path: str) -> bool:
    """Whether a written file runs (see :data:`SCRIPT_SUFFIXES`)."""
    name = _normalise(path).rstrip("/").rsplit("/", 1)[-1]
    return "." not in name.lstrip(".") or name.endswith(SCRIPT_SUFFIXES)


# ----------------------------------------------------------------------------------------
# shell words
# ----------------------------------------------------------------------------------------
def _words(segment: str) -> list[str]:
    return [w.strip("'\"`") for w in segment.split()]


def _command_index(words: list[str]) -> int:
    for i, w in enumerate(words):
        w = w.lstrip("({")
        if not w or w in _PREFIX_WORDS or re.fullmatch(r"[a-z_][\w]*=\S*", w, re.I):
            continue
        return i
    return len(words)


def command_word(segment: str) -> str:
    """The command a shell segment runs (after ``VAR=x``, ``sudo``, ``time``, ``do``, the
    PowerShell call operator ...), lower case, without its folder and ``.exe``."""
    words = _words(segment.lower())
    i = _command_index(words)
    if i >= len(words):
        return ""
    w = words[i].lstrip("({").rsplit("/", 1)[-1]
    return re.sub(r"\.exe$", "", w)


def _args(segment: str) -> list[str]:
    """The words after the segment's command (quotes stripped)."""
    words = _words(segment)
    return words[_command_index([w.lower() for w in words]) + 1 :]


def _segments(text: str) -> list[str]:
    return _SEGMENT.split(text)


def _runs_python(norm: str) -> bool:
    return bool(
        re.search(r"(?<![\w.-])(?:python[\d.]*|pythonw|py|ipython)(?:\.exe)?(?![\w-])", norm)
        or re.search(r"(?<![\w-])uv\s+run(?:\s+--?[\w-]+(?:[= ]\S+)?)*\s+(?:-m|--module)\s", norm)
    )


# ----------------------------------------------------------------------------------------
# git and web history of PREREGISTRATION.md
# ----------------------------------------------------------------------------------------
_OLD_REV = re.compile(
    r"^(?:[0-9a-f]{7,40}|[\w./-]*[~^]\d*|[\w./-]*@\{[^}]*\}|[\w./-]+\.\.\.?[\w./~^-]+)$"
)


def _is_old_rev(arg: str) -> bool:
    """A revision other than the current branch (a commit id, ``HEAD~3``, ``A..B``)."""
    return bool(_OLD_REV.match(arg)) and not arg.startswith(("./", "../", "/"))


def _has_pathspec(args: list[str]) -> bool:
    if "--" in args:
        return True
    return any(
        not a.startswith("-") and ("/" in a or re.search(r"\.[a-z0-9]{1,5}$", a)) for a in args
    )


def _pathspecs(args: list[str]) -> list[str]:
    if "--" in args:
        return args[args.index("--") + 1 :]
    return [
        a
        for a in args
        if not a.startswith("-")
        and not _is_old_rev(a)
        and ("/" in a or a in (".", "*", ":/", "benchmarks") or re.search(r"\.[a-z0-9]{1,5}$", a))
    ]


def _holds_prereg(spec: str) -> bool:
    """Whether a git pathspec names PREREGISTRATION.md or a folder that holds it."""
    s = spec.strip("'\"")
    s = re.sub(r"^:\([^)]*\)", "", s)  # pathspec magic
    if s in ("", ".", "./", "*", ":/", ":/*", ":"):
        return True
    s = re.sub(r"^(?:\./|:/)", "", s).rstrip("/")
    for target in (PREREG_PATH, "benchmarks/operations", "benchmarks"):
        if s == target or target.startswith(s + "/") or fnmatch.fnmatchcase(target, s):
            return True
    absolute = bool(re.match(r"^(?:[a-z]:|/|~)", s))
    return absolute and bool(re.search(r"/worldparts[\w.-]*(?:/benchmarks(?:/operations)?)?$", s))


def _shows_content(sub: str, args: list[str]) -> bool:
    """Whether a git command shows (or restores) old file content."""
    if sub in ("log", "whatchanged"):
        return any(a.startswith(_PATCH_FLAGS) for a in args)
    if sub == "show":
        return not any(a in _NO_PATCH_FLAGS or a.startswith("--format") for a in args)
    if sub in ("diff", "difftool", "checkout", "restore", "grep", "archive"):
        return any(_is_old_rev(a) for a in args) or any(
            a.startswith(("--source", "-s")) for a in args if sub == "restore"
        )
    return sub in ("format-patch", "range-diff")


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
        elif (
            sub in GIT_HISTORY
            and _shows_content(sub, args)
            and any(_holds_prereg(p) for p in _pathspecs(args))
        ):
            out.append(("violation", f"the draft history of PREREGISTRATION.md (git {sub} of a "
                                     "folder that holds it)"))  # fmt: skip
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
        elif (
            sub in ("diff", "checkout", "switch", "worktree", "reset")
            and any(_is_old_rev(a) for a in args)
            and (sub not in ("diff", "checkout") or not _pathspecs(args))
            and (sub != "reset" or "--hard" in args)
            and not (sub == "diff" and any(a in _NO_PATCH_FLAGS for a in args))
        ):
            out.append(("warning", f"git {sub} of an old revision of the whole repository (it "
                                   "can show PREREGISTRATION.md's history)"))  # fmt: skip
    return out


_GITHUB = re.compile(r"github|(?<![\w-])gh\s")
_WEB_REV = re.compile(
    r"commits|/commit/|/compare/|/blame/|/history|[?&]ref=|(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])"
)


def web_findings(text: str) -> list[str]:
    """Why a command or URL fetches the history of PREREGISTRATION.md from GitHub."""
    norm = _normalise(text)
    if "preregistration" in norm and _GITHUB.search(norm) and _WEB_REV.search(norm):
        return ["the draft history of PREREGISTRATION.md (fetched from GitHub)"]
    return []


# ----------------------------------------------------------------------------------------
# run directories, and the folders above them
# ----------------------------------------------------------------------------------------
_RECURSIVE = re.compile(
    r"\b[efz]?grep\b[^\n]*?\s-[a-z]*r[a-z]*\b|--recursive|(?<![\w-])(?:rg|ag|ack)(?![\w-])|"
    r"\bfindstr\b[^\n]*?\s/s\b|\bselect-string\b|(?<![\w-])sls(?![\w-])|-recurse\b|"
    r"\bdir\b[^\n]*?\s/s\b|\bxargs\b|\bfind\b[^\n]*?-exec|\brglob\b|os\.walk|copytree|"
    r"make_archive|\bcp\s+-[a-z]*[ra]|\brobocopy\b|\bxcopy\b|\btar\b|\bzip\s+-[a-z]*r|"
    r"\brsync\b|compress-archive|\b7z\b"
)
_INCLUDE = re.compile(
    r"(?:--include|--glob|(?<![\w-])-g|-include|-name|-iname|-filter)(?:=|\s+)['\"]?([^\s'\"]+)"
)
#: The files of a run directory a builder may read by a search or a wildcard: transcripts
#: (``stream.jsonl``), prompts and stderr (``.txt``) and the files agents wrote.
ALLOWED_RUN_FILES = (".jsonl", ".txt", ".py", ".csv", ".inp")
_WILD = re.compile(r"[*?\[]")
_TOKEN_SPLIT = re.compile(r"[\s'\"=,;()|&<>]+")


def run_tree_paths(norm: str) -> list[str]:
    """Paths in ``norm`` (normalised text) into a run directory (``.../operations/results/``,
    a relative ``results/``, or a ``sessions/`` folder) that can reach its grader files:
    the run, its ``sessions`` or a session folder, or a wildcard at those levels. A path
    inside an attempt folder (``sessions/<id>/attempt-<n>``), a named file, and a pattern
    for allowed file types are not among them."""
    out: list[str] = []
    for token in _TOKEN_SPLIT.split(norm):
        if "operations/results" in token:
            rest = token.split("operations/results", 1)[1]
        elif token.startswith(("results/", "./results/")) or token in ("results", "./results"):
            rest = token.split("results", 1)[1]
        elif re.match(r"(?:\./)?sessions/", token):
            rest = "/<run>/sessions" + token.split("sessions", 1)[1]
        else:
            continue
        segs = [s for s in rest.split("/") if s]
        if len(segs) >= 4 and segs[1] == "sessions" and segs[3].startswith("attempt-"):
            continue
        last = segs[-1] if segs else ""
        if last.endswith(ALLOWED_RUN_FILES):
            continue
        if "." in last and not _WILD.search(last):
            continue  # one named file: forbidden names are markers of their own
        out.append(token)
    return out


def _restricted(includes: list[str]) -> bool:
    return bool(includes) and all(p.endswith(ALLOWED_RUN_FILES) for p in includes)


RUN_TREE_REASON = (
    "a search, copy or wildcard read over a run directory (it reads record.json, summary.* "
    "and pass-fail.log; restrict it to the transcripts, e.g. --include='*.jsonl')"
)


def run_tree_findings(command: str) -> list[str]:
    """Why a shell command reads a run directory's grader files wholesale, or ``[]``."""
    norm = _normalise(command)
    if not run_tree_paths(norm) or _restricted(_INCLUDE.findall(norm)):
        return []
    if _RECURSIVE.search(norm):
        return [RUN_TREE_REASON]
    for segment in _segments(norm):
        wild = [p for p in run_tree_paths(segment) if _WILD.search(p)]
        if wild and command_word(segment) not in _LISTERS:
            return [RUN_TREE_REASON]
    return []


def _drive_form(path: Path | str) -> str:
    """A path in lower case with slashes, as the audit compares it (``c:/users/...``)."""
    return str(path).replace("\\", "/").rstrip("/").lower()


def _absolute(token: str) -> str | None:
    """``token`` as a lower-case absolute path ``c:/...`` (home and Git Bash or WSL drive
    spellings resolved), or None for a relative path."""
    t = token.strip("'\"`").replace("\\", "/").lower()
    home = _drive_form(Path.home())
    t = re.sub(r"^(?:~|\$home|\$\{home\}|%userprofile%|\$env:userprofile|\$userprofile)"
               r"(?=/|$)", home, t)  # fmt: skip
    m = re.match(r"^(?:/mnt)?/([a-z])(?=/|$)", t)
    if m and not re.match(r"^/(?:tmp|usr|etc|dev|proc|home|var|opt|bin)(?:/|$)", t):
        t = f"{m.group(1)}:" + t[m.end() :]
    if re.match(r"^[a-z]:(?:/|$)", t):
        return t.rstrip("/") or t
    return None


def _sensitive_roots(extra_roots: Iterable[str]) -> list[str]:
    """What only a folder above the repository reaches: the folder holding the repository
    and the private folder (world-model), the home folder (the Claude memory), and the
    truth and ``--forbid`` paths."""
    roots = [REPO_ROOT.parent, Path.home(), *extra_roots]
    return [_absolute(str(r)) or _drive_form(r) for r in roots]


_CHECKOUT = re.compile(r"(?:^|/)worldparts(?:-[\w.-]+)?$")


def ancestor_kind(
    token: str, extra_roots: Iterable[str] = (), cwd: str | None = None
) -> str | None:
    """``"private"`` for a folder above the repository that holds the private folder, the
    owner's notes or the Claude memory (``..``, the home folder, world-model, a drive);
    ``"runs"`` for a folder that holds a run directory (``.``, the repository or a checkout
    such as the Stage 0 worktree, ``benchmarks``, ``benchmarks/operations``); else None.
    ``cwd`` is the kind of the folder ``.`` stands for after a ``cd`` (``"sub"``: a
    subfolder, so ``.`` is no ancestor); by default the repository ("runs")."""
    t = token.strip("'\"`").replace("\\", "/").lower()
    if not t or t.startswith("-"):
        return None
    if re.fullmatch(r"\.(?:/\.)*/?", t):
        return None if cwd == "sub" else (cwd or "runs")
    if re.fullmatch(r"\.\.(?:/\.\.)*/?", t):
        return "private"
    rel = re.sub(r"^\./", "", t).rstrip("/")
    if rel in ("benchmarks", "benchmarks/operations"):
        return "runs"
    m = re.fullmatch(r"\.\./(.+)", rel)
    if m:
        up = m.group(1)
        if re.fullmatch(r"(?:\.\./)*\.\.", up):
            return "private"
        if _CHECKOUT.search(up) and "/" not in up:
            return "runs"
        return None
    path = _absolute(t)
    if path is None:
        return None
    if re.fullmatch(r"[a-z]:/?", path):
        return "private"
    for root in _sensitive_roots(extra_roots):
        if path == root or root.startswith(path + "/"):
            return "private"
    if _CHECKOUT.search(path) or re.search(r"/benchmarks(?:/operations)?$", path):
        return "runs"
    return None


#: Flags of grep/rg that take a value (the pattern is then not the first positional).
_VALUE_FLAGS = frozenset({"-e", "-f", "-m", "-A", "-B", "-C", "-g", "-t", "-T", "-d", "-j",
                          "-M", "--regexp", "--file", "--max-count", "--glob", "--iglob",
                          "--type", "--type-not", "--context", "--after-context",
                          "--before-context", "--directories", "--threads",
                          "--max-columns", "--include", "--exclude", "--exclude-dir",
                          "-path", "-destination", "-filter", "-include", "-exclude",
                          "-maxdepth", "-mindepth", "-name", "-iname", "-type",
                          "--max-depth"})  # fmt: skip


#: PowerShell parameters that take a value (case-insensitive).
_PS_VALUE_FLAGS = frozenset({"-path", "-literalpath", "-destination", "-filter", "-include",
                             "-exclude", "-pattern", "-depth"})  # fmt: skip


def _positionals(args: list[str], slash_flags: bool = False) -> list[str]:
    """The arguments that are not flags or flag values (``slash_flags``: cmd.exe's
    ``/s``, ``/e`` are flags too)."""
    out: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _VALUE_FLAGS or a.lower() in _PS_VALUE_FLAGS:
            skip = True
            continue
        if a.startswith("-") or (slash_flags and re.fullmatch(r"/[a-z]{1,3}", a, re.I)):
            continue
        out.append(a)
    return out


def _option(args: list[str], *names: str) -> list[str]:
    """The values of the (PowerShell) options ``names`` (case-insensitive)."""
    low = [a.lower() for a in args]
    return [args[i + 1] for i, a in enumerate(low[:-1]) if a in names]


def _recursive_roots(segment: str, later: list[str]) -> tuple[str, list[str]] | None:
    """(``"search"``, ``"ignoring"``, ``"copy"`` or ``"list"``, the folders) of a
    recursive command in ``segment``; ``later`` are the segments after it (for a listing
    piped into a reader). ``"ignoring"`` skips git-ignored files (``rg``, ``ag``)."""
    word = command_word(segment)
    args = _args(segment)
    low = [a.lower() for a in args]
    pos = _positionals(args)

    def flag(*names: str) -> bool:
        return any(
            a in names
            or (
                re.fullmatch(r"-[a-z]+", a, re.I)
                and any(n.startswith("-") and len(n) == 2 and n[1] in a[1:] for n in names)
            )
            for a in args
        )

    has_pattern = not any(a in ("-e", "-f", "--regexp", "--file") for a in args)
    if word in ("grep", "egrep", "fgrep", "zgrep") and (
        flag("-r", "-R", "--recursive") or "--directories=recurse" in low
    ):
        return "search", (pos[1:] if has_pattern else pos) or ["."]
    if word in ("rg", "ripgrep", "ag", "ack"):
        roots = (pos[1:] if has_pattern else pos) or ["."]
        unrestricted = word == "ack" or any(
            a.startswith(("--no-ignore", "-u", "--unrestricted")) for a in low
        )
        return ("search" if unrestricted else "ignoring"), roots
    if word == "findstr" and "/s" in low:
        return "search", ["."]  # it searches the current folder and those below
    if word == "find":
        roots = [
            a
            for a in args[
                : next((i for i, a in enumerate(args) if a.startswith(("-", "(", "!"))), len(args))
            ]
        ] or ["."]
        reads = any(a in ("-exec", "-execdir", "-ok") for a in low) or any(
            command_word(s) in ("xargs",) for s in later
        )
        return ("search" if reads else "list"), roots
    if word in ("gci", "get-childitem", "ls", "dir") and (
        "-recurse" in low or ("-r" in low and word != "ls") or "/s" in low or "-R" in args
    ):
        roots = _option(args, "-path", "-literalpath") or _positionals(args, True) or ["."]
        reads = any(command_word(s) in _CONTENT_READERS for s in later)
        return ("search" if reads else "list"), roots
    if word == "tree":
        return "list", pos or ["."]
    if word in ("cp", "copy", "scp") and flag("-r", "-R", "-a", "--recursive", "--archive"):
        return "copy", pos[:-1]
    if word == "rsync" and flag("-r", "-a", "--recursive", "--archive"):
        return "copy", pos[:-1]
    if word in ("copy-item", "cpi") and "-recurse" in low:
        return "copy", _option(args, "-path", "-literalpath") or pos[:1]
    if word in ("robocopy", "xcopy"):
        return "copy", _positionals(args, True)[:1]
    if word in ("tar", "bsdtar") and (
        "--create" in low or any(re.fullmatch(r"-?[a-z]*c[a-z]*", a) for a in low[:1])
    ):
        return "copy", [a for a in pos if not re.search(r"\.(?:tar|tgz|gz|bz2|xz|zip)$", a)]
    if word == "zip" and flag("-r", "--recurse-paths"):
        return "copy", pos[1:]
    if word in ("7z", "7za") and pos[:1] == ["a"]:
        return "copy", pos[2:]
    if word == "compress-archive":
        return "copy", _option(args, "-path", "-literalpath") or pos[:1]
    return None


def recursive_findings(command: str, extra_roots: Iterable[str] = ()) -> list[str]:
    """Why a shell command searches, lists, copies or archives a folder above a run
    directory or above the repository (see the module docstring), or ``[]``."""
    norm = _normalise(command)
    extra = list(extra_roots)
    restricted = _restricted(_INCLUDE.findall(norm))
    segments = _segments(command.replace("\\", "/"))
    cwd: str | None = None
    out: list[str] = []
    for i, segment in enumerate(segments):
        if command_word(segment) in ("cd", "pushd", "set-location", "sl", "chdir"):
            target = (_positionals(_args(segment)) or ["~"])[0]
            if target != "-":
                cwd = ancestor_kind(target, extra, cwd) or "sub"
            continue
        found = _recursive_roots(segment, segments[i + 1 :])
        if found is None:
            continue
        kind, roots = found
        for root in roots:
            where = ancestor_kind(root, extra, cwd)
            if where == "private":
                out.append(
                    f"a recursive {kind} over {root!r}, a folder above the repository (the "
                    "private folder, the owner's notes, the Claude memory)"
                )
            elif where == "runs" and kind in ("search", "copy") and not restricted:
                out.append(
                    f"a recursive {kind} over {root!r}, a folder that holds run directories "
                    "(it reads record.json; search a subfolder, use rg or git grep, or "
                    "restrict it, e.g. --include='*.py')"
                )
    return list(dict.fromkeys(out))


def grep_tool_findings(
    inp: dict[str, Any], extra_roots: Iterable[str] = (), tool: str = "Grep"
) -> list[str]:
    """The same for the ``Grep`` tool (ripgrep: it skips git-ignored files, except in a
    run directory named as its path, and nothing above the repository) and the ``Glob``
    tool (a pattern over several levels lists the names of a folder above the
    repository)."""
    path = inp.get("path")
    path = path if isinstance(path, str) else ""
    out: list[str] = []
    if tool == "Grep" and path and run_tree_paths(_normalise(path)):
        glob = inp.get("glob")
        restricted = isinstance(glob, str) and _restricted([_normalise(glob)])
        if not restricted and inp.get("type") not in ("py", "txt", "csv"):
            out.append(RUN_TREE_REASON)
    where = path
    if tool == "Glob":
        pattern = _normalise(str(inp.get("pattern") or ""))
        literal = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
        if not path and "/" in literal:
            where = literal.rsplit("/", 1)[0] or "/"
        if "**" not in pattern and "/" not in pattern[len(literal) :]:
            return out  # one level: names only, as ls lists them
    if where and ancestor_kind(_normalise(where), extra_roots) == "private":
        what = "search" if tool == "Grep" else "listing"
        out.append(f"a recursive {what} over {where!r}, a folder above the repository (the "
                   "private folder, the owner's notes, the Claude memory)")  # fmt: skip
    return out


def wildcard_findings(norm: str) -> list[str]:
    """Wildcard spellings of the forbidden names (``*opsbench*``, ``tru*/dev``,
    ``rec*.json``, ``~/.cl*/proj*``): a pattern whose literal letters are part of the name."""
    out: list[str] = []
    for token in _TOKEN_SPLIT.split(norm):
        if not _WILD.search(token):
            continue
        comps = [c for c in token.split("/") if c]
        for i, comp in enumerate(comps):
            if not _WILD.search(comp) or comp.startswith("-"):
                continue
            runs = [r for r in re.split(r"[*?\[\]./!-]+", comp) if len(r) >= 3]
            for name in FORBIDDEN_NAMES:
                if not fnmatch.fnmatchcase(name, comp):
                    continue
                if name == "reports" and i > 0 and comps[i - 1] == "composition":
                    continue
                stem = name if name.startswith(".") else name.rsplit(".", 1)[0]
                dotted = name.startswith(".") and comp.startswith(".") and len(
                    re.sub(r"[^a-z]", "", comp)) >= 2  # fmt: skip
                if dotted or any(r in stem for r in runs):
                    out.append(f"a wildcard spelling of {name!r}")
                    break
    return list(dict.fromkeys(out))


def cd_findings(command: str) -> list[str]:
    """Why a shell command changes into a forbidden folder (``cd reports``)."""
    out: list[str] = []
    for segment in _segments(_normalise(command)):
        if command_word(segment) not in ("cd", "pushd", "set-location", "sl", "chdir"):
            continue
        for target in _positionals(_args(segment))[:1]:
            comps = [c for c in target.split("/") if c]
            for i, c in enumerate(comps):
                if c in FORBIDDEN_DIRS and not (c == "reports" and i and comps[i - 1] ==
                                                "composition"):  # fmt: skip
                    out.append(f"a change of directory into {c!r}")
    return out


# ----------------------------------------------------------------------------------------
# the owner's commands, variables and files
# ----------------------------------------------------------------------------------------
_HARNESS_SUB = re.compile(r"(?<![\w./-])(score|grade|headroom|report)(?![\w./-])")
_OWNER_OPTIONS = re.compile(r"--(?:truth|truth-manifest|bundles|out|no-manifest-check)(?![\w-])")


def harness_findings(command: str, python_file: bool = False) -> list[str]:
    """Why a command (or a Python file, ``python_file``) runs the owner's side of the
    harness: ``score``/``grade`` without ``--pass-fail``, ``headroom``, ``report``, the
    pass/fail mode with the owner's options, or the truth functions (:data:`TRUTH_API`)."""
    norm = _normalise(command)
    runs = python_file or _runs_python(norm)
    out: list[str] = []
    harness = bool(_HARNESS_REF.search(norm))
    for segment in _segments(norm):
        if command_word(segment) in _NOT_RUNS and not python_file:
            continue
        if runs and harness and TRUTH_API.search(segment):
            out.append(f"the harness's truth function {TRUTH_API.search(segment).group(0)!r} "
                       "(it reads the truth)")  # fmt: skip
        if not (harness and runs) or _HELP.search(segment):
            continue
        m = _HARNESS_SUB.search(segment)
        if not m:
            continue
        if m.group(1) in ("score", "grade") and _PASS_FAIL.search(segment):
            if _OWNER_OPTIONS.search(segment):
                out.append("the pass/fail mode given the owner's options (--truth, "
                           "--truth-manifest, --bundles, --out)")  # fmt: skip
            continue
        out.append(f"the owner's command 'harness {m.group(1)}' (it reads the truth or the "
                   "grades)")  # fmt: skip
    return list(dict.fromkeys(out))


_ENV_SET = re.compile(
    r"wpbench_ops_(session|bundles)['\"\]]*\s*=(?!=)|"
    r"(?:setx|set-item|setenvironmentvariable|putenv|setenv|environ\.update|"
    r"(?<![\w-])set\s)[^\n;|&]*?wpbench_ops_(session|bundles)"
)


def env_findings(command: str) -> list[tuple[str, str]]:
    """(level, reason) for a command that sets ``WPBENCH_OPS_SESSION`` (a violation: it
    would reset the evaluation count) or ``WPBENCH_OPS_BUNDLES`` (a warning)."""
    out: list[tuple[str, str]] = []
    for m in _ENV_SET.finditer(_normalise(command)):
        var = m.group(1) or m.group(2)
        if var == "session":
            out.append(("violation", "setting WPBENCH_OPS_SESSION (the owner's session name, "
                                     "which the evaluations are counted by)"))  # fmt: skip
        else:
            out.append(("warning", "setting WPBENCH_OPS_BUNDLES (the pass/fail mode checks the "
                                   "bundles against the committed manifest)"))  # fmt: skip
    return list(dict.fromkeys(out))


_WRITE_COMMANDS = frozenset({"rm", "del", "erase", "rmdir", "rd", "unlink", "truncate", "touch",
                             "chmod", "set-content", "add-content", "out-file", "clear-content",
                             "new-item", "remove-item", "ni", "ri", "patch"})  # fmt: skip
_COPY_COMMANDS = frozenset({"cp", "copy", "mv", "move", "copy-item", "move-item", "cpi",
                            "mi", "rename-item", "ren", "rename", "install", "ln", "rsync",
                            "robocopy", "xcopy"})  # fmt: skip


def protected_write_findings(command: str) -> list[str]:
    """Why a shell command changes the harness, its committed lists, the wrapper or the
    benchmark's tests (:data:`PROTECTED`)."""
    norm = _normalise(command)
    if not PROTECTED.search(norm):
        return []
    reason = "a change to the benchmark harness, its committed lists, the wrapper or its tests"
    for segment in _segments(norm):
        if not PROTECTED.search(segment):
            continue
        word = command_word(segment)
        args = _args(segment)
        targets: list[str] = re.findall(r"(?<![<\d&])\d?>{1,2}\s*([^\s;&|<>]+)", segment)
        if word == "tee":
            targets += _positionals(args)
        if word in ("sed", "perl") and any(re.fullmatch(r"-[a-z]*i\S*|--in-place\S*", a)
                                           for a in args):  # fmt: skip
            targets += args
        if word in _WRITE_COMMANDS:
            targets += args
        if word in _COPY_COMMANDS:
            pos = _positionals(args)
            moves = word in ("mv", "move", "move-item", "mi", "rename-item", "ren", "rename")
            targets += pos[-1:] + (pos if moves else [])
        if word == "git" and re.search(r"(?<![\w-])(?:checkout|restore|apply|am|rm|mv|stash)"
                                       r"(?![\w-])", segment):  # fmt: skip
            targets += args
        if re.search(r"write_text|write_bytes|open\([^)]*['\"][wax]|\.unlink\(|os\.remove|"
                     r"shutil\.(?:copy|move|rmtree)|os\.replace|\.rename\(", segment):  # fmt: skip
            targets.append(segment)
        if any(PROTECTED.search(t) for t in targets):
            return [reason]
    return []


_CLAUDE_RESUME = re.compile(r"(?<![\w-])(?:--resume|-r|--continue|-c|--fork-session)(?![\w-])")


def claude_findings(command: str) -> list[str]:
    """Why a command resumes or continues another Claude Code session (its transcript)."""
    for segment in _segments(_normalise(command)):
        if command_word(segment) == "claude" and _CLAUDE_RESUME.search(segment):
            return ["another session's transcript (claude --resume or --continue)"]
    return []


def evaluations_in(command: str) -> tuple[int, list[str]]:
    """(number of pass/fail evaluations a shell command runs, the session names given)."""
    n = 0
    names: list[str] = []
    text = command.replace("\\\n", " ")
    harness = bool(_HARNESS_REF.search(_normalise(text)))
    for segment in _SEGMENT.split(text):
        low = _normalise(segment)
        if _HELP.search(segment) or command_word(low) in _NOT_RUNS:
            continue
        if _WRAPPER.search(low) or (
            harness and _PASS_FAIL.search(low) and (_HARNESS_SUB.search(low) or "main(" in low)
        ):
            n += 1
            names += _SESSION_ARG.findall(segment)
    return n, names


def looped(command: str) -> bool:
    """Whether a command runs something in a loop, through ``xargs`` or in the background."""
    return bool(_LOOP.search(_normalise(command)))


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


def shell_findings(
    command: str, extra_roots: Iterable[str] = (), python_file: bool = False
) -> list[tuple[str, str]]:
    """(level, reason) of every shell rule on ``command`` (or on a written script)."""
    out: list[tuple[str, str]] = list(git_findings(command))
    out += [("violation", r) for r in web_findings(command)]
    out += [("violation", r) for r in run_tree_findings(command)]
    out += [("violation", r) for r in recursive_findings(command, extra_roots)]
    out += [("violation", r) for r in wildcard_findings(_normalise(command))]
    out += [("violation", r) for r in cd_findings(command)]
    out += [("violation", r) for r in harness_findings(command, python_file)]
    out += env_findings(command)
    out += [("violation", r) for r in protected_write_findings(command)]
    out += [("violation", r) for r in claude_findings(command)]
    return list(dict.fromkeys(out))


def audit_transcripts(
    paths: list[Path],
    truth_roots: Iterable[Path | str] = (),
    extra: Iterable[Path | str] = (),
) -> AuditResult:
    """Audit the transcripts (see the module docstring)."""
    truth_roots, extra = list(truth_roots), list(extra)
    patterns = forbidden_patterns(truth_roots, extra)
    roots = [str(r) for r in (*truth_roots, *extra)]
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

            def add(
                level: str, reason: str, text: str, n: int = n, tool: str = tool, f: str = str(path)
            ) -> None:
                findings.append(Finding(f, n, tool, level, reason, _snippet(text)))

            if TRANSCRIPT_TOOLS.search(tool):
                add("violation", "another session's transcript or the owner's terminal "
                                 "(it holds what the orchestrating session has seen)",
                    json.dumps(inp))  # fmt: skip
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
                if tool not in SHELL_TOOLS and not isinstance(inp.get("command"), str):
                    reasons += wildcard_findings(norm)
                    reasons += web_findings(text)
                for what in dict.fromkeys(reasons):
                    add(level, what, text)
            if tool in WRITE_TOOLS:
                target = str(inp.get("file_path") or inp.get("notebook_path") or "")
                if PROTECTED.search(_normalise(target)):
                    add("violation", "a change to the benchmark harness, its committed lists, "
                                     "the wrapper or its tests", target)  # fmt: skip
                script = is_script(target)
                for body in written_texts(tool, inp):
                    wlevel = "violation" if script else "warning"
                    norm = _normalise(body)
                    for what in dict.fromkeys(w for w, pat in patterns if pat.search(norm)):
                        add(wlevel, f"{what}, in the text written to {target or 'a file'}", body)
                    if not script:
                        continue
                    python = target.lower().endswith((".py", ".pyw", ".ipynb"))
                    for lvl, reason in shell_findings(body, roots, python_file=python):
                        add(lvl, f"{reason}, in the script {target or 'written'}", body)
                    if evaluations_in(body)[0]:
                        add("warning", f"a script that runs the evaluation wrapper ({target}): "
                                       "each run of it is an evaluation", body)  # fmt: skip
            if tool in ("Grep", "Glob"):
                for reason in grep_tool_findings(inp, roots, tool):
                    add("violation", reason, json.dumps(inp))
            if tool in SHELL_TOOLS or isinstance(inp.get("command"), str):
                cmd = inp.get("command") or ""
                for lvl, reason in shell_findings(cmd, roots):
                    add(lvl, reason, cmd)
                k, given = evaluations_in(cmd)
                if k and looped(cmd):
                    add("warning", f"{k} pass/fail evaluation(s) inside a loop, xargs or a "
                                   "background job: it may run more often than counted here; "
                                   "the wrapper's log has the count", cmd)  # fmt: skip
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
