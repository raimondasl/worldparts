"""The worldparts quick reference of the lib arms (PREREGISTRATION.md section 3).

``preambles/quick-reference.txt`` is written after Stage 0 (build track A) and frozen at
freeze-1. The ``lib`` and ``lib-directed`` arms refuse to run when it is missing or when
:func:`quick_reference_problems` finds a problem. Section 3 sets the limits:

- at most 45 lines in all, with at most 20 lines for section 14;
- call signatures and returned field names only: no thresholds and no procedure;
- "A test fails if it contains a number, or a method statement, that is not in the
  checklist."

**Format.** A line whose first characters are ``#!`` is a harness annotation: it is never
shown to the agent and is not counted. ``#! section 14`` opens the part that documents
design section 14 (measurements and the long-format SCADA reader, ``calibrate``,
``identifiability``, ``diagnose``), and ``#! end`` closes it (otherwise it runs to the end
of the file). The file has exactly one such part, and a section-14 name used as code
(``calibrate(``, ``.diagnose``, ```identifiability```) outside it is refused, so the
section-14 lines cannot hide elsewhere. The section-14 names are read from worldparts'
public API (:func:`section14_names`): every name ``worldparts/__init__.py`` imports from a
section-14 module (:data:`SECTION14_MODULES`), so a name added by build track A (a SCADA
reader, say) counts at once. Every other line counts, blank lines included; a leading
``#`` marks a heading. A shown line has at most :data:`MAX_LINE_CHARS` characters.

**Code and prose.** Code is an identifier or an expression: a backtick span without
spaces; a call ``name(...)`` whose arguments are single tokens (``x``, ``x=None``,
``x: float``, ``*``); a token holding an underscore, a digit, one of
``= < > [ ] { } ( ) \\ | * @ # $ %``, or a dot between letters (``wp.System``). Every
other word is prose, and so are:

- a backtick span or an argument with spaces in it;
- the text of a string literal (``"..."`` or ``'...'``), in a call or a backtick span;
- a backtick span of three or more hyphen-joined words (``fit-hourly-means``), and an
  identifier with more than four underscore-joined words: prose in code clothing;
- the parts of a prose token joined by ``/``, ``,``, ``;``, ``+`` or other punctuation
  (``Hampel/median`` is two words).

Put identifiers in backticks or in call syntax.

**The test** (what :func:`quick_reference_problems` checks):

1. *Numbers.* Every number that is not part of a name (``lo90`` is a name; ``0.95``,
   ``.95``, ``1_000``, ``1e-6``, ``3h`` and ``2x`` are numbers) must be a number of the
   checklist. Leading list labels (``3.`` or ``3)`` at the start of a line) are not
   numbers, in either text. The checklist's numbers are 1 and 90.
2. *Words.* Every prose word must be a word of the checklist or of
   :data:`REFERENCE_WORDS`, a fixed list of words that describe signatures, types and
   results, and only ASCII letters. A method named in prose (a Hampel filter, a bootstrap)
   is refused unless the checklist names it.
3. *Procedure.* A line whose prose holds a word of :data:`PROCEDURE_CUES` (prefer, first,
   then, before, if, when, above, at least, ...) is refused, even when it copies the
   checklist: section 3 allows "no thresholds and no procedure", and the ``lib`` arm gets
   the quick reference without the checklist.

Detection is mechanical, so the quick reference is also read by a reviewer before
freeze-1 (fairness threat 7). :data:`REFERENCE_WORDS` and :data:`PROCEDURE_CUES` are
frozen with the quick reference; a change is logged in FREEZES.md.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .bundles import REPO_ROOT

QUICK_REFERENCE = "quick-reference.txt"
MAX_LINES = 45
MAX_SECTION14_LINES = 20
#: The longest shown line (so that the line limits cannot be met with a few long lines).
MAX_LINE_CHARS = 120
ANNOTATION = "#!"
SECTION14_OPEN = "section 14"
SECTION14_CLOSE = "end"
#: The section-14 API names PREREGISTRATION.md section 7 names ("Allowed worldparts
#: additions"); :func:`section14_names` adds the rest of worldparts' section-14 API.
SECTION14_BASE = ("load_measurements", "calibrate", "identifiability", "diagnose")
#: The worldparts modules of design section 14: measurements and the long-format SCADA
#: reader, calibration and identifiability, diagnosis. Their public names are section-14
#: names, and their line count is section 14's (the toolkit's size cap, section 3).
SECTION14_MODULES = ("measurements", "scada", "calibration", "identifiability", "diagnosis")
WORLDPARTS_SRC = REPO_ROOT / "src" / "worldparts"


def section14_sources(src: Path | None = None) -> list[Path]:
    """The source files of the section-14 modules (``<module>.py`` or a package's files)."""
    src = src or WORLDPARTS_SRC
    out: list[Path] = []
    for name in SECTION14_MODULES:
        if (src / f"{name}.py").is_file():
            out.append(src / f"{name}.py")
        elif (src / name).is_dir():
            out += sorted(p for p in (src / name).rglob("*.py") if "__pycache__" not in p.parts)
    return out


def section14_lines(src: Path | None = None) -> int:
    """The line count of worldparts' section-14 code (:func:`section14_sources`)."""
    return sum(
        len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        for p in section14_sources(src)
    )


def section14_names(src: Path | None = None) -> tuple[str, ...]:
    """:data:`SECTION14_BASE`, the module names, and every public name that
    ``worldparts/__init__.py`` imports from a section-14 module."""
    names = set(SECTION14_BASE) | set(SECTION14_MODULES)
    init = (src or WORLDPARTS_SRC) / "__init__.py"
    try:
        tree = ast.parse(init.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return tuple(sorted(names))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            local = node.level > 0 or parts[0] == "worldparts"
            if local and parts[-1] in SECTION14_MODULES:
                names.update(a.asname or a.name for a in node.names if a.name != "*")
    return tuple(sorted(n for n in names if not n.startswith("_")))


def _wordset(text: str) -> frozenset[str]:
    return frozenset(text.split())


#: Words that describe signatures, types and results; allowed in prose besides the words of
#: the checklist.
REFERENCE_WORDS = _wordset(
    """
    a an the and or of to in on for from with by as at into per via
    it its this that these those each every all any one ones is are be
    worldparts wp python package module import imports installed version command commands
    cli shell api quick reference
    function functions class classes method methods call calls signature signatures
    argument arguments keyword keywords option options returns return returned result
    results output outputs input inputs
    field fields attribute attributes key keys item items entry entries column columns
    row rows name names path paths file files folder directory text format long wide
    csv json yaml str string strings float floats int integer integers bool boolean
    booleans number numbers list lists tuple tuples dict dicts dictionary mapping
    mappings map array arrays series dataframe dataframes table tables object objects
    type types none true false null optional default defaults value values unit units
    same see also etc new get set read reads write writes load loads save saves
    """
)
#: Words that signal a procedure or a threshold. A line whose prose holds one is refused.
PROCEDURE_CUES = _wordset(
    """
    prefer first then before after always never should must only unless instead avoid
    try recommend recommended best better consider remember ensure if when whenever
    until above below exceed exceeds exceeding threshold thresholds least most within
    larger smaller greater less than
    """
)

_LIST_LABEL = re.compile(r"^\s*\d+[.)](?=\s|$)")
#: A number not preceded by a letter, digit, underscore or dot (so ``lo90``, ``m3`` and the
#: ``12`` of ``python3.12`` are names, while ``3h`` and ``2x`` are numbers with a unit); a
#: leading dot (``.95``) and digit groups (``1_000``) belong to the number.
_NUMBER = re.compile(r"(?<![\w.])(?:\d+(?:_\d+)*(?:\.\d+(?:_\d+)*)?|\.\d+)(?:[eE][-+]?\d+)?")
_CALL = re.compile(r"[A-Za-z_][\w.]*\((?:[^()]|\([^()]*\))*\)")
_BACKTICK = re.compile(r"`([^`]*)`")
_STRING = re.compile(r"\"([^\"]*)\"|'([^']*)'")
#: One argument of a signature (string literals replaced by ``""`` first).
_ARG = re.compile(
    r"\*{0,2}[A-Za-z_][\w.]*(?:\s*:\s*[\w.\[\]|]+)?(?:\s*=\s*[^\s,]+)?|\*|/|\.\.\.|\"\""
)
_WORD = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
_LETTERS = re.compile(r"[^\W\d_]+")
_CODE_CHARS = set("_0123456789=<>[]{}()\\|*@#$%")
#: Three or more hyphen-joined words (after leading dashes): prose, not an option name.
_HYPHEN_PHRASE = re.compile(r"-{0,2}[A-Za-z]+(?:-[A-Za-z]+){2,}")
#: More underscore-joined words than this in one name read as prose.
MAX_NAME_WORDS = 4


def _section14_code(names: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(?:(?<![\w])(?:{alt})\s*\(|\.(?:{alt})(?![\w])|`(?:{alt})`)")


@dataclass
class QuickReference:
    """A quick-reference file split into what the agent sees and its section-14 part."""

    shown: list[str]
    section14: list[int] = field(default_factory=list)  # indices into ``shown``
    problems: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.shown)


def parse(text: str) -> QuickReference:
    """Split ``text`` at its annotations (``#! section 14`` ... ``#! end``)."""
    ref = QuickReference(shown=[])
    inside = False
    opened = 0
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(ANNOTATION):
            directive = " ".join(line.strip()[len(ANNOTATION) :].split()).lower()
            if directive == SECTION14_OPEN:
                if inside or opened:
                    ref.problems.append(f"line {n}: a second '#! section 14' part")
                inside = True
                opened += 1
            elif directive == SECTION14_CLOSE:
                if not inside:
                    ref.problems.append(f"line {n}: '#! end' without '#! section 14'")
                inside = False
            else:
                ref.problems.append(f"line {n}: unknown annotation {line.strip()!r}")
            continue
        ref.shown.append(line)
        if inside:
            ref.section14.append(len(ref.shown) - 1)
    while ref.shown and not ref.shown[-1].strip():  # the file's trailing blank lines
        ref.shown.pop()
    ref.section14 = [i for i in ref.section14 if i < len(ref.shown)]
    if not opened:
        ref.problems.append(
            "no '#! section 14' part: mark the lines that document section 14 (section 3 "
            "allows at most 20 of them)"
        )
    return ref


def shown_text(text: str) -> str:
    """What the agent sees of a quick-reference file: its lines without annotations."""
    return parse(text).text


def _strip_label(line: str) -> str:
    return _LIST_LABEL.sub("", line, count=1)


def numbers_in(text: str) -> list[str]:
    """The numbers of ``text`` (see the module docstring), list labels left out."""
    out: list[str] = []
    for line in text.splitlines():
        out += _NUMBER.findall(_strip_label(line))
    return out


def _value(number: str) -> float:
    return float(number.replace("_", ""))


def _long_name_words(code: str) -> list[str]:
    """The words of each name in ``code`` that joins more than :data:`MAX_NAME_WORDS`
    words with underscores (prose in code clothing), else ``[]``."""
    out: list[str] = []
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code):
        parts = [p for p in name.split("_") if p]
        if len(parts) > MAX_NAME_WORDS:
            out += parts
    return out


def _strings_prose(code: str) -> tuple[str, list[str]]:
    """``code`` with its string literals replaced by ``""``, and their texts (prose)."""
    texts = [a or b for a, b in _STRING.findall(code)]
    return _STRING.sub('""', code), texts


def _code_span(inner: str) -> str:
    """The prose of a backtick span: all of it when it has spaces, else its string
    literals, a hyphen-joined phrase and over-long names (see the module docstring)."""
    stripped = inner.strip()
    if not stripped:
        return " "
    if re.search(r"\s", stripped):
        return inner
    rest, texts = _strings_prose(stripped)
    prose = list(texts)
    if _HYPHEN_PHRASE.fullmatch(rest):
        prose.append(rest.replace("-", " "))
    else:
        prose += _long_name_words(rest)
    return " " + " ".join(prose) + " "


def _call(m: re.Match[str]) -> str:
    span = m.group(0)
    cut = span.index("(")
    prose = _long_name_words(span[:cut])
    for arg in (a.strip() for a in span[cut + 1 : -1].split(",")):
        if not arg:
            continue
        rest, texts = _strings_prose(arg)
        if _ARG.fullmatch(rest):
            prose += texts + _long_name_words(rest)
        else:
            prose.append(arg)
    return " " + " ".join(prose) + " "


def _is_code_token(token: str) -> bool:
    """An identifier or an expression (see the module docstring)."""
    return any(c in _CODE_CHARS for c in token) or bool(re.search(r"[A-Za-z]\.[A-Za-z_]", token))


def _token_words(token: str) -> list[str]:
    token = token.strip(".,;:!?\"'()[]{}")
    if not token or token in ("->", "=>"):
        return []
    if _is_code_token(token):
        return [w.lower() for w in _long_name_words(token)]
    words: list[str] = []
    for run in _LETTERS.findall(token):  # "Hampel/median", "a,b,c", "chi-square", "it's"
        words.append(run.lower())
    return words


def prose_words(line: str) -> list[str]:
    """The prose words of one shown line (lower case), code left out. A word with
    letters that are not ASCII is kept as it is (the test refuses it)."""
    text = _strip_label(line).lstrip()
    text = re.sub(r"^#+\s*", "", text)
    text = _BACKTICK.sub(lambda m: _code_span(m.group(1)), text)
    while True:
        new = _CALL.sub(_call, text)
        if new == text:
            break
        text = new
    words: list[str] = []
    for token in text.split():
        words += _token_words(token)
    return words


def _vocabulary(text: str) -> set[str]:
    words: set[str] = set()
    for w in _WORD.findall(text):
        words.update(p.lower() for p in re.split(r"['-]", w) if p)
    return words


def _known(word: str, vocab: set[str]) -> bool:
    forms = {word, word + "s", word + "es"}
    if word.endswith("s"):
        forms.add(word[:-1])
    if word.endswith("es"):
        forms.add(word[:-2])
    if word.endswith("ies"):
        forms.add(word[:-3] + "y")
    return bool(forms & vocab)


def _shorten(word: str, limit: int = 40) -> str:
    return word if len(word) <= limit else word[: limit - 3] + "..."


def quick_reference_problems(
    text: str, checklist: str, section14: tuple[str, ...] | None = None
) -> list[str]:
    """Why ``text`` (quick-reference.txt) breaks section 3's limits, or ``[]``.
    ``section14`` is the list of section-14 names (default :func:`section14_names`)."""
    ref = parse(text)
    problems = list(ref.problems)
    if len(ref.shown) > MAX_LINES:
        problems.append(f"{len(ref.shown)} lines; section 3 allows at most {MAX_LINES}")
    if len(ref.section14) > MAX_SECTION14_LINES:
        problems.append(
            f"{len(ref.section14)} lines for section 14; section 3 allows at most "
            f"{MAX_SECTION14_LINES}"
        )
    allowed = {_value(x) for x in numbers_in(checklist)}
    vocab = _vocabulary(checklist) | set(REFERENCE_WORDS)
    in_section14 = set(ref.section14)
    section14_code = _section14_code(section14 or section14_names())
    for i, line in enumerate(ref.shown):
        where = f"shown line {i + 1}"
        if len(line) > MAX_LINE_CHARS:
            problems.append(f"{where}: {len(line)} characters; at most {MAX_LINE_CHARS}")
        for x in numbers_in(line):
            if _value(x) not in allowed:
                problems.append(f"{where}: the number {x} is not in the checklist")
        words = prose_words(line)
        foreign = sorted({w for w in words if not w.isascii()})
        if foreign:
            problems.append(
                f"{where}: word(s) with letters that are not ASCII: "
                + ", ".join(_shorten(w) for w in foreign)
            )
        unknown = sorted({w for w in words if w.isascii() and not _known(w, vocab)})
        if unknown:
            problems.append(
                f"{where}: prose word(s) not in the checklist or the reference words: "
                + ", ".join(_shorten(w) for w in unknown)
            )
        cues = sorted({w for w in words if w in PROCEDURE_CUES})
        if cues:
            problems.append(
                f"{where}: a method statement ({', '.join(cues)}); section 3 allows no "
                "procedure or threshold in the quick reference, not even the checklist's"
            )
        if i not in in_section14 and section14_code.search(line):
            problems.append(f"{where}: a section-14 name outside the '#! section 14' part")
    return problems
