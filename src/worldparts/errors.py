"""Exception hierarchy.

Every error raised by worldparts derives from :class:`WorldpartsError`. Messages are
written for an AI agent: they name the offending path and list valid alternatives.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from worldparts.results import Issue

__all__ = [
    "CalibrationError",
    "ContractError",
    "ExpressionError",
    "IncompatiblePortsError",
    "InvalidValueError",
    "ManifestError",
    "MeasurementError",
    "OutOfRangeError",
    "SelfConnectionError",
    "SolverError",
    "SystemCheckError",
    "UnitError",
    "UnknownComponentError",
    "UnknownPortError",
    "UnknownVariableError",
    "WorldpartsError",
    "format_choices",
]


def format_choices(
    name: str, choices: Iterable[str], limit: int = 40, *, list_valid: bool = True
) -> str:
    """Return a sentence listing valid choices, with close matches to ``name`` first.

    Args:
        name: The value that was not recognised.
        choices: The valid alternatives.
        limit: Maximum number of alternatives to list.
        list_valid: False keeps only the close matches (``""`` when there are none), for
            a message that names several unknown values and lists the valid ones once.

    Returns:
        A string such as ``"Did you mean 'valve'? Valid: a, b, c."``.
    """
    options = sorted(dict.fromkeys(str(c) for c in choices))
    if not options:
        return "There are no valid alternatives." if list_valid else ""
    close = difflib.get_close_matches(str(name), options, n=3, cutoff=0.6)
    parts = []
    if close:
        parts.append("Did you mean " + " or ".join(f"'{c}'" for c in close) + "?")
    if not list_valid:
        return " ".join(parts)
    shown = options[:limit]
    more = f" (and {len(options) - limit} more)" if len(options) > limit else ""
    parts.append("Valid: " + ", ".join(shown) + more + ".")
    return " ".join(parts)


class WorldpartsError(Exception):
    """Base class for all worldparts errors."""

    code: str = "error"


class UnknownComponentError(WorldpartsError):
    """A component type or instance name is not known."""

    code = "unknown_component"


class UnknownPortError(WorldpartsError):
    """A port path does not name a port of a component."""

    code = "unknown_port"


class UnknownVariableError(WorldpartsError):
    """A variable path does not name a known variable."""

    code = "unknown_variable"


class InvalidValueError(WorldpartsError, ValueError):
    """A value has the wrong type, unit or form."""

    code = "invalid_value"


class UnitError(InvalidValueError):
    """A unit string cannot be parsed or has the wrong dimension."""

    code = "invalid_value"


class OutOfRangeError(InvalidValueError):
    """A value lies outside the hard limits declared in the manifest."""

    code = "parameter_out_of_range"


class SelfConnectionError(InvalidValueError):
    """A port was connected to itself."""

    code = "self_connection"


class IncompatiblePortsError(InvalidValueError):
    """Two ports of different type or medium were connected."""

    code = "incompatible_ports"


class ExpressionError(WorldpartsError):
    """An expression is not valid in the worldparts expression language."""

    code = "invalid_expression"


class ManifestError(WorldpartsError):
    """A component manifest is invalid.

    Attributes:
        problems: Individual problems found, one per entry.
        path: The manifest file, when known.
    """

    code = "invalid_manifest"

    def __init__(self, message: str, problems: Sequence[str] = (), path: Any = None) -> None:
        self.problems = list(problems)
        self.path = path
        if self.problems:
            message = message + "\n" + "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(message)


class MeasurementError(InvalidValueError):
    """A measurement set is malformed, or does not fit the system it is used with.

    Attributes:
        problems: Individual problems, each naming the point, the path or the file row.
    """

    code = "invalid_measurements"

    #: At most this many problems are written into the message (all are in ``problems``).
    MAX_LISTED = 25

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        self.problems = list(problems)
        if self.problems:
            shown = self.problems[: self.MAX_LISTED]
            more = len(self.problems) - len(shown)
            message = message + "\n" + "\n".join(f"  - {p}" for p in shown)
            if more:
                message += f"\n  ... and {more} more."
        super().__init__(message)


class CalibrationError(WorldpartsError):
    """Calibration or an identifiability analysis could not run, for example because the
    model fails at the starting values."""

    code = "calibration_failed"


class ContractError(WorldpartsError):
    """A component implementation broke its manifest contract (for example it did not
    return a declared observable or emitted an undeclared warning code)."""

    code = "contract_violation"


class SolverError(WorldpartsError):
    """The network solver did not converge.

    Attributes:
        residual: Final scaled maximum residual.
        worst: Description of the worst equation.
    """

    code = "solver_failed"

    def __init__(self, message: str, residual: float = float("nan"), worst: str = "") -> None:
        self.residual = residual
        self.worst = worst
        super().__init__(message)


class SystemCheckError(WorldpartsError):
    """A system has error-level issues and cannot be solved.

    Attributes:
        issues: All issues found by :meth:`worldparts.System.check`.
    """

    code = "system_check_failed"

    def __init__(self, issues: Sequence[Issue]) -> None:
        self.issues = list(issues)
        errors = [i for i in self.issues if i.severity == "error"]
        lines = [f"  - [{i.code}] {i.where}: {i.message}" for i in errors]
        super().__init__(
            f"The system has {len(errors)} error(s) and cannot be solved:\n" + "\n".join(lines)
        )
