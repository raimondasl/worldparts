"""Result objects: issues, component warnings, steady results and time series."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from worldparts.errors import UnknownVariableError, format_choices
from worldparts.units import convert

__all__ = [
    "ComponentWarning",
    "Issue",
    "ModeChange",
    "SimulationResult",
    "SolveResult",
    "VariableInfo",
]


@dataclass(frozen=True)
class Issue:
    """A structural problem found by :meth:`worldparts.System.check`.

    Attributes:
        severity: ``"error"`` or ``"warning"``.
        code: Stable identifier, e.g. ``no_pressure_reference``.
        message: Explanation written for an agent.
        where: The offending path (component, port, variable or connection).
    """

    severity: str
    code: str
    message: str
    where: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form."""
        return asdict(self)


@dataclass(frozen=True)
class ComponentWarning:
    """A warning raised by a component (envelope rule or component code).

    Attributes:
        component: Instance name.
        code: Warning code declared in the manifest.
        severity: ``"info"`` or ``"warning"``.
        message: Explanation.
        time: Simulation time in s of the first occurrence (None for steady solves).
        last_time: Simulation time in s of the last sample at which the warning was raised
            (None for steady solves).
        active_at_end: Whether the warning is still raised at the final sample (None for
            steady solves).
    """

    component: str
    code: str
    severity: str
    message: str
    time: float | None = None
    last_time: float | None = None
    active_at_end: bool | None = None

    @property
    def path(self) -> str:
        """``<component>.<code>``, the form used in scenarios and contracts."""
        return f"{self.component}.{self.code}"

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form."""
        d = asdict(self)
        for key in ("time", "last_time", "active_at_end"):
            if d[key] is None:
                del d[key]
        return d


@dataclass(frozen=True)
class VariableInfo:
    """Description of a system variable path.

    Attributes:
        path: ``<instance>.<name>`` or ``<instance>.<port>.<p|m_flow|T>``.
        kind: ``parameter``, ``input``, ``state``, ``observable`` or ``port``.
        unit: Display unit.
        description: Text from the manifest.
        pressure_reference: The reference used for unit conversion: for pressures
            ``gauge``, ``absolute`` or ``difference``; ``difference`` for a temperature
            difference (``quantity: temperature_difference``); None otherwise.
        type: Value type (``number``, ``integer``, ``boolean``, ``string``, ``table``).
        minimum: Hard lower limit (parameters and inputs).
        maximum: Hard upper limit.
        settable: Whether :meth:`worldparts.System.set` accepts the path.
        reported: Whether solve and simulation results include the path (False for string
            and table parameters; read those with :meth:`worldparts.System.get`).
        quantity: ``temperature_difference`` for a temperature difference, else None.
    """

    path: str
    kind: str
    unit: str | None
    description: str = ""
    pressure_reference: str | None = None
    type: str = "number"
    minimum: float | None = None
    maximum: float | None = None
    settable: bool = False
    reported: bool = True
    quantity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form without empty fields."""
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass(frozen=True)
class ModeChange:
    """A component entering a mode at ``time`` (s)."""

    time: float
    component: str
    mode: str | None

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form."""
        return asdict(self)


def _lookup(mapping: Mapping[str, Any], path: str, what: str = "variable") -> Any:
    try:
        return mapping[path]
    except KeyError:
        raise UnknownVariableError(
            f"Unknown {what} '{path}'. " + format_choices(path, mapping.keys())
        ) from None


@dataclass
class SolveResult:
    """A steady operating point.

    Attributes:
        converged: Whether the solver converged.
        iterations: Solver iterations.
        max_residual: Final scaled maximum residual.
        values: Path to value in the display unit (None where undefined).
        units: Path to display unit string.
        modes: Instance name to mode name.
        warnings: Component warnings.
        issues: Non-fatal issues from the structural check (warnings only).
        time: Simulation time in s when the result is part of a simulation.
    """

    converged: bool
    iterations: int
    max_residual: float
    values: dict[str, float | None]
    units: dict[str, str]
    modes: dict[str, str | None] = field(default_factory=dict)
    warnings: list[ComponentWarning] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    references: dict[str, str] = field(default_factory=dict, repr=False)
    time: float | None = None

    def __getitem__(self, path: str) -> float | None:
        return _lookup(self.values, path)

    def __contains__(self, path: object) -> bool:
        return path in self.values

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def get(self, path: str, unit: str | None = None) -> float | None:
        """Value at ``path``, optionally converted to ``unit`` (same reference).

        Pressures keep their reference unless ``unit`` names one (``"bar absolute"``);
        temperature differences convert by scale only (a 35 K rise is 35 degC).

        Raises:
            UnknownVariableError: For unknown paths.
            UnitError: When ``unit`` has the wrong dimension.
        """
        value = _lookup(self.values, path)
        if unit is None or value is None:
            return value
        return convert(value, self.units[path], unit, self.references.get(path))

    def unit(self, path: str) -> str:
        """Display unit of ``path``."""
        return _lookup(self.units, path)

    def has_warning(self, code: str) -> bool:
        """True when a warning ``<component>.<code>`` is present."""
        return any(w.path == code for w in self.warnings)

    def _entry(self, path: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"value": _jsonable(self[path]), "unit": self.units[path]}
        ref = self.references.get(path)
        if ref:
            entry["reference"] = ref
        return entry

    def to_dict(self, variables: list[str] | None = None) -> dict[str, Any]:
        """JSON-ready dict: values with units, modes, warnings and solver statistics.

        Each value is {"value", "unit"}, plus "reference" (gauge, absolute
        or difference) for pressures and "difference" for temperature differences, so a
        reader can tell a gauge port pressure from a pressure drop, and a temperature rise
        in K from an absolute temperature.
        """
        paths = variables if variables is not None else list(self.values)
        out: dict[str, Any] = {
            "converged": self.converged,
            "iterations": self.iterations,
            "max_residual": self.max_residual,
            "values": {p: self._entry(p) for p in paths},
            "modes": dict(self.modes),
            "warnings": [w.to_dict() for w in self.warnings],
        }
        if self.issues:
            out["issues"] = [i.to_dict() for i in self.issues]
        if self.time is not None:
            out["time"] = self.time
        return out


def _jsonable(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


@dataclass
class SimulationResult:
    """A time series from :meth:`worldparts.System.simulate`.

    Attributes:
        time: Sample times in s.
        series: Path to list of values in display units (None where undefined).
        units: Path to display unit.
        warnings: Each (component, code) raised during the run, with the message and time
            of its first occurrence, the time of its last occurrence and whether it is still
            raised at the end.
        mode_changes: Initial mode of each component at t=0 and every later change.
        final: The steady result at the final time.
    """

    time: list[float]
    series: dict[str, list[float | None]]
    units: dict[str, str]
    warnings: list[ComponentWarning]
    mode_changes: list[ModeChange]
    final: SolveResult
    references: dict[str, str] = field(default_factory=dict, repr=False)

    def __getitem__(self, path: str) -> list[float | None]:
        return _lookup(self.series, path)

    def __contains__(self, path: object) -> bool:
        return path in self.series

    def get(self, path: str, unit: str | None = None) -> list[float | None]:
        """Series at ``path``, optionally converted to ``unit``."""
        values = _lookup(self.series, path)
        if unit is None:
            return list(values)
        ref = self.references.get(path)
        return [convert(v, self.units[path], unit, ref) for v in values]

    def summary(self, path: str) -> dict[str, Any]:
        """Minimum, maximum and final value of a series (None values ignored)."""
        values = [v for v in _lookup(self.series, path) if v is not None]
        return {
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "final": self.series[path][-1] if self.series[path] else None,
            "unit": self.units[path],
        }

    def to_dict(
        self, max_points: int | None = None, variables: list[str] | None = None
    ) -> dict[str, Any]:
        """JSON-ready dict, optionally downsampled to at most ``max_points`` samples.

        Downsampling keeps the first and last samples and evenly spaced samples between.
        Summaries (min, max, final) are always computed over the full series.
        """
        n = len(self.time)
        idx = list(range(n))
        if max_points is not None and max_points > 0 and n > max_points:
            if max_points == 1:
                idx = [n - 1]
            else:
                step = (n - 1) / (max_points - 1)
                idx = sorted({round(i * step) for i in range(max_points)})
        paths = variables if variables is not None else list(self.series)
        return {
            "time": [self.time[i] for i in idx],
            "series": {p: [_jsonable(self.series[p][i]) for i in idx] for p in paths},
            "units": {p: self.units[p] for p in paths},
            "references": {p: self.references[p] for p in paths if p in self.references},
            "summary": {p: self.summary(p) for p in paths},
            "warnings": [w.to_dict() for w in self.warnings],
            "mode_changes": [c.to_dict() for c in self.mode_changes],
            "final": self.final.to_dict(paths if variables is not None else None),
        }
