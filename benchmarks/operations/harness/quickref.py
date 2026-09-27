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
section-14 lines cannot hide elsewhere. Every other line counts, blank lines included;
a leading ``#`` marks a heading.

**Code and prose.** Code is: a backtick span without spaces; a call ``name(...)`` whose
arguments are single tokens (``x``, ``x=None``, ``x: float``, ``*``); a token holding an
underscore, a dot, a digit or any of ``= < > [ ] { } ( ) / \\ | * @ # $ %``. Every other
word is prose, and a backtick span or an argument with spaces in it is read as prose too.
Put identifiers in backticks or in call syntax.

**The test** (what :func:`quick_reference_problems` checks):

1. *Numbers.* Every number that is not part of a name (``lo90`` is a name; ``0.95``,
   ``1e-6``, ``3h`` and ``2x`` are numbers) must be a number of the checklist. Leading
   list labels (``3.`` or ``3)`` at the start of a line) are not numbers, in either text.
   The checklist's numbers are 1 and 90.
2. *Words.* Every prose word must be a word of the checklist or of
   :data:`REFERENCE_WORDS`, a fixed list of words that describe signatures, types and
   results. A method named in prose (a Hampel filter, a bootstrap) is refused unless the
   checklist names it.
3. *Procedure.* A line whose prose holds a word of :data:`PROCEDURE_CUES` (prefer, first,
   then, before, if, when, above, at least, ...) must be, as prose, a verbatim part of the
   checklist: a method statement is allowed only when the checklist already makes it.

Detection is mechanical, so the quick reference is also read by a reviewer before
freeze-1 (fairness threat 7). :data:`REFERENCE_WORDS` and :data:`PROCEDURE_CUES` are
frozen with the quick reference; a change is logged in FREEZES.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

QUICK_REFERENCE = "quick-reference.txt"
MAX_LINES = 45
MAX_SECTION14_LINES = 20
ANNOTATION = "#!"
SECTION14_OPEN = "section 14"
SECTION14_CLOSE = "end"
#: The section-14 API names (PREREGISTRATION.md section 7, "Allowed worldparts additions").
SECTION14_NAMES = ("load_measurements", "calibrate", "identifiability", "diagnose")


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
#: Words that signal a procedure or a threshold. A line whose prose holds one must be a
#: verbatim part of the checklist.
PROCEDURE_CUES = _wordset(
    """
    prefer first then before after always never should must only unless instead avoid
    try recommend recommended best better consider remember ensure if when whenever
    until above below exceed exceeds exceeding threshold thresholds least most within
    larger smaller greater less than
    """
)

_LIST_LABEL = re.compile(r"^\s*\d+[.)](?=\s|$)")
#: A number not preceded by a letter, digit, underscore or dot (so ``lo90`` and ``m3`` are
#: names, while ``3h`` and ``2x`` are numbers with a unit).
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_CALL = re.compile(r"[A-Za-z_][\w.]*\((?:[^()]|\([^()]*\))*\)")
_BACKTICK = re.compile(r"`([^`]*)`")
_ARG = re.compile(
    r"\*{0,2}[A-Za-z_][\w.]*(?:\s*:\s*[\w.\[\]|]+)?(?:\s*=\s*[^\s,]+)?|\*|/|\.\.\.|"
    r"\"[^\"]*\"|'[^']*'"
)
_WORD = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
_CODE_CHARS = set("_.0123456789=<>[]{}()/\\|*@#$%")
_SECTION14_CODE = re.compile(
    r"(?:(?<![\w])(?:"
    + "|".join(SECTION14_NAMES)
    + r")\s*\(|\.(?:"
    + "|".join(SECTION14_NAMES)
    + r")(?![\w])|`(?:"
    + "|".join(SECTION14_NAMES)
    + r")`)"
)


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
    return float(number)


def _is_code_token(token: str) -> bool:
    return any(c in _CODE_CHARS for c in token)


def prose_words(line: str) -> list[str]:
    """The prose words of one shown line (lower case), code left out."""
    text = _strip_label(line).lstrip()
    text = re.sub(r"^#+\s*", "", text)

    def backtick(m: re.Match[str]) -> str:
        inner = m.group(1)
        return " " if not inner.strip() or not re.search(r"\s", inner.strip()) else inner

    text = _BACKTICK.sub(backtick, text)

    def call(m: re.Match[str]) -> str:
        span = m.group(0)
        inner = span[span.index("(") + 1 : -1]
        prose = [a.strip() for a in inner.split(",") if a.strip()]
        return " " + " ".join(a for a in prose if not _ARG.fullmatch(a)) + " "

    while True:
        new = _CALL.sub(call, text)
        if new == text:
            break
        text = new
    words: list[str] = []
    for token in text.split():
        token = token.strip(".,;:!?\"'()[]{}")
        if not token or _is_code_token(token) or token == "->":
            continue
        if _WORD.fullmatch(token):
            words += [w.lower() for w in re.split(r"['-]", token) if w]
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


def quick_reference_problems(text: str, checklist: str) -> list[str]:
    """Why ``text`` (quick-reference.txt) breaks section 3's limits, or ``[]``."""
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
    checklist_words = [
        p.lower() for w in _WORD.findall(checklist) for p in re.split(r"['-]", w) if p
    ]
    checklist_prose = " " + " ".join(checklist_words) + " "
    section14 = set(ref.section14)
    for i, line in enumerate(ref.shown):
        where = f"shown line {i + 1}"
        for x in numbers_in(line):
            if _value(x) not in allowed:
                problems.append(f"{where}: the number {x} is not in the checklist")
        words = prose_words(line)
        unknown = sorted({w for w in words if not _known(w, vocab)})
        if unknown:
            problems.append(
                f"{where}: prose word(s) not in the checklist or the reference words: "
                + ", ".join(unknown)
            )
        cues = sorted({w for w in words if w in PROCEDURE_CUES})
        if cues and f" {' '.join(words)} " not in checklist_prose:
            problems.append(
                f"{where}: a method statement ({', '.join(cues)}) that the checklist does not make"
            )
        if i not in section14 and _SECTION14_CODE.search(line):
            problems.append(f"{where}: a section-14 name outside the '#! section 14' part")
    return problems
