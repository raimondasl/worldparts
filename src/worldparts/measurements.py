"""Measurement sets: operating points with measured values and uncertainties (design 14.1).

A measurement set is a list of **operating points**. Each point has a name, optional
settings (parameters, inputs or states applied before solving), an optional time (for a
time series, compared against one simulation) and measured values with units and optional
uncertainty::

    points:
      - name: valve-open
        settings: {valve.opening: 1.0}
        measured:
          pump.volume_flow: {value: "31.8 m3/h", sigma: "0.5 m3/h"}
          pump.outlet.p: {value: "2.71 bar", sigma: "0.02 bar"}
      - name: valve-half
        settings: {valve.opening: 0.5}
        measured: {pump.volume_flow: "22.4 m3/h", pump.outlet.p: "3.35 bar"}

A measured value is a number in the variable's declared unit, a string with a unit
(``"31.8 m3/h"``, ``"2.71 bar"``, ``"3.8 bara"``) or a mapping ``{value, unit?, sigma?}``.
``sigma`` is the standard uncertainty (one standard deviation) as a positive difference: a
number in the value's unit or a string with any compatible unit (``"0.5 L/min"``; a
``"0.1 degC"`` sigma is 0.1 K, never an absolute temperature). Without ``sigma`` the default
of :func:`default_sigma` applies: 1 % of the value, floored at an absolute value for the kind
of quantity (:data:`SIGMA_FLOORS`).

:func:`load_measurements` reads this structure from YAML or JSON, or a long-format CSV with
one measured value per row::

    point,time,path,value,unit,sigma
    valve-open,,pump.volume_flow,31.8,m3/h,0.5
    valve-open,,pump.outlet.p,2.71,bar,0.02

``point`` may be blank when ``time`` is given (the point is then named after its time), and
an optional ``kind`` column marks a row as a ``setting`` instead of a ``measured`` value, so
a CSV time series can carry operator actions. :meth:`MeasurementSet.from_dict` parses the
same structure from plain Python data (for example an MCP tool argument).

:meth:`MeasurementSet.resolve` validates a set against a :class:`~worldparts.System`: every
measured path must be a reported numeric variable, every setting a settable one, every unit
compatible. Values and sigmas are converted to each variable's declared display unit (with
its pressure reference). All problems are reported together in one
:class:`~worldparts.errors.MeasurementError`.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from worldparts.errors import (
    InvalidValueError,
    MeasurementError,
    UnitError,
    WorldpartsError,
    format_choices,
)
from worldparts.media import RHO
from worldparts.units import (
    _pint_unit,
    convert,
    converter,
    is_pressure_unit,
    is_temperature_unit,
    normalize_unit,
    parse_duration,
    parse_quantity,
    parse_value,
    same_dimension,
    split_pressure_reference,
    ureg,
)

if TYPE_CHECKING:
    from worldparts.results import VariableInfo
    from worldparts.system import System

__all__ = [
    "CSV_COLUMNS",
    "MIN_SIGMA_RELATIVE",
    "RELATIVE_SIGMA",
    "SIGMA_FLOORS",
    "MeasuredValue",
    "MeasurementPoint",
    "MeasurementSet",
    "ResolvedMeasurements",
    "ResolvedPoint",
    "ResolvedValue",
    "default_sigma",
    "load_measurements",
    "quantity_kind",
]

#: Relative part of the default uncertainty: 1 % of the measured value (design 14.1).
RELATIVE_SIGMA: float = 0.01

#: Absolute floor of the default uncertainty by kind of quantity: ``(floor, unit)``. The
#: default sigma is ``max(RELATIVE_SIGMA * |value|, floor)``; the floor is converted to the
#: variable's unit as a difference. The kinds cover every unit dimension in the catalogue
#: and in the manifest unit vocabulary (design 3.1).
SIGMA_FLOORS: dict[str, tuple[float, str]] = {
    "pressure": (0.01, "bar"),  # gauge, absolute or difference; 1 kPa
    "volume_flow": (0.05, "m3/h"),
    "mass_flow": (0.05 / 3600 * RHO, "kg/s"),  # 0.05 m3/h of water
    "temperature": (0.1, "K"),  # relative part on the Celsius value (see default_sigma)
    "temperature_difference": (0.1, "K"),
    "power": (0.01, "kW"),
    "length": (0.01, "m"),  # level, head, NPSH
    "velocity": (0.01, "m/s"),
    "dimensionless": (0.001, "1"),  # fractions and % values: 0.1 percentage point
    "specific_energy": (0.001, "kWh/m3"),
    "dose": (0.1, "mJ/cm2"),
    "irradiance": (0.01, "mW/cm2"),
    "time": (0.1, "s"),
    "volume": (0.001, "m3"),  # 1 L
    "area": (1e-4, "m2"),  # 1 cm2
    "rotational_speed": (1.0, "rpm"),
    "density": (0.1, "kg/m3"),
}

#: A sigma below this fraction of ``max(|value|, floor)`` (the floor of the value's kind of
#: quantity, :data:`SIGMA_FLOORS`) is rejected: no instrument is that accurate, and the
#: normalised residuals would overflow.
MIN_SIGMA_RELATIVE: float = 1e-9

#: Columns of the long-format CSV. ``path`` and ``value`` are required, and ``point`` or
#: ``time``; ``kind`` is ``measured`` (default) or ``setting``.
CSV_COLUMNS: tuple[str, ...] = ("point", "time", "path", "value", "unit", "sigma", "kind")

_POINT_KEYS = ("name", "time", "settings", "measured")
_VALUE_KEYS = ("value", "unit", "sigma", "sigma_unit")
_SET_KEYS = ("name", "description", "points")
_KINDS_ORDER = [k for k in SIGMA_FLOORS if k not in ("pressure", "temperature")]


# ----------------------------------------------------------------------------------------
# default uncertainties
# ----------------------------------------------------------------------------------------
def _base_unit(unit: str) -> str:
    """``unit`` without an explicit pressure reference (``bara`` -> ``bar``)."""
    base, _ = split_pressure_reference(unit)
    return base


def quantity_kind(unit: str, reference: str | None = None) -> str:
    """The kind of quantity a unit measures, a key of :data:`SIGMA_FLOORS`.

    Args:
        unit: A unit string such as ``"m3/h"``, ``"bar"`` or ``"degC"``.
        reference: The variable's reference; ``"difference"`` makes a temperature unit a
            ``temperature_difference``.

    Raises:
        UnitError: When the unit cannot be parsed.
        InvalidValueError: When no kind has the unit's dimension.
    """
    base = _base_unit(unit)
    converter(base)  # raises UnitError for an unknown unit
    if is_pressure_unit(base):
        return "pressure"
    if is_temperature_unit(base):
        return "temperature_difference" if reference == "difference" else "temperature"
    dim = ureg.Unit(normalize_unit(base)).dimensionality
    for kind in _KINDS_ORDER:
        if ureg.Unit(normalize_unit(SIGMA_FLOORS[kind][1])).dimensionality == dim:
            return kind
    raise InvalidValueError(
        f"There is no default uncertainty for values in {unit} (dimension {dim}); give "
        "'sigma' for them explicitly. Default uncertainties exist for: "
        + ", ".join(SIGMA_FLOORS)
        + "."
    )


def _difference(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a difference (an uncertainty) between units: by scale only, no offset."""
    a, b = _base_unit(from_unit), _base_unit(to_unit)
    if not same_dimension(a, b):
        raise UnitError(
            f"Cannot express an uncertainty in {from_unit} in {to_unit}: the dimensions differ."
        )
    # As differences, so a temperature-difference unit such as delta_degC is valid here.
    return value * converter(a, "difference").scale / converter(b, "difference").scale


def default_sigma(value: float, unit: str, reference: str | None = None) -> float:
    """Default standard uncertainty of a measured value, in ``unit`` (design 14.1).

    The default is 1 % of the value (:data:`RELATIVE_SIGMA`), floored at an absolute value
    for the kind of quantity (:data:`SIGMA_FLOORS`): 0.01 bar for pressures (gauge, absolute
    or difference), 0.05 m3/h for volume flows (0.0139 kg/s of water for mass flows), 0.1 K
    for temperatures and temperature differences, 0.01 kW for powers, 0.01 m for levels,
    heads and other lengths, 0.01 m/s for velocities, 0.001 (0.1 percentage point) for
    dimensionless fractions and percentages, 0.001 kWh/m3 for specific energy, 0.1 mJ/cm2
    for UV dose, 0.01 mW/cm2 for irradiance, 0.1 s for times, 1 L for volumes, 1 cm2 for
    areas, 1 rpm for rotational speeds and 0.1 kg/m3 for densities.

    The relative part applies to the value as the variable reports it: a gauge pressure's
    gauge value, a pressure drop's difference. For an absolute temperature it applies to the
    value in degC (a sensor's error scales with the reading above 0 degC, never with the
    kelvin value), in whatever unit the temperature is given.

    Args:
        value: The measured (or predicted) value, in ``unit``.
        unit: Its unit (the variable's declared unit).
        reference: The variable's reference (``gauge``, ``absolute`` or ``difference``).

    Returns:
        The standard uncertainty in ``unit`` (as a difference).

    Raises:
        InvalidValueError: When the unit's kind of quantity has no floor; give sigma.
    """
    kind = quantity_kind(unit, reference)
    floor_value, floor_unit = SIGMA_FLOORS[kind]
    floor = _difference(floor_value, floor_unit, unit)
    if kind == "temperature":
        celsius = convert(value, _base_unit(unit), "degC")
        assert celsius is not None
        relative = _difference(RELATIVE_SIGMA * abs(celsius), "K", unit)
    else:
        relative = RELATIVE_SIGMA * abs(value)
    return max(relative, floor)


# ----------------------------------------------------------------------------------------
# the measurement model
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MeasuredValue:
    """One measured value as written.

    Attributes:
        value: The number, in ``unit``.
        unit: The unit as written, possibly with a pressure reference (``"bara"``,
            ``"bar absolute"``); None means the variable's declared display unit.
        sigma: Standard uncertainty (one standard deviation), a positive difference in
            ``sigma_unit``; None means the default uncertainty (:func:`default_sigma`).
        sigma_unit: Unit of ``sigma``; None means the same unit as the value.
    """

    value: float
    unit: str | None = None
    sigma: float | None = None
    sigma_unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form ``{value, unit?, sigma?, sigma_unit?}``."""
        out: dict[str, Any] = {"value": self.value}
        if self.unit is not None:
            out["unit"] = self.unit
        if self.sigma is not None:
            out["sigma"] = self.sigma
            if self.sigma_unit is not None and self.sigma_unit != self.unit:
                out["sigma_unit"] = self.sigma_unit
        return out


@dataclass
class MeasurementPoint:
    """An operating point: settings, an optional time and measured values.

    Attributes:
        name: Unique name within the set.
        measured: Variable path to measured value.
        settings: Parameter, input or state path to the value applied before solving (as
            written: a number in the declared unit, a string with a unit, or a boolean,
            string or table for such parameters). In a time series the settings are applied
            as events at ``time`` and persist afterwards.
        time: Time in s from the start of the simulation, or None for a steady point.
    """

    name: str
    measured: dict[str, MeasuredValue] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    time: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form (the YAML/JSON structure; time in s)."""
        out: dict[str, Any] = {"name": self.name}
        if self.time is not None:
            out["time"] = self.time
        if self.settings:
            out["settings"] = dict(self.settings)
        out["measured"] = {p: v.to_dict() for p, v in self.measured.items()}
        return out


@dataclass(frozen=True)
class ResolvedPoint:
    """A point validated against a system.

    Attributes:
        name: Point name.
        time: Time in s, or None for a steady point.
        settings: Path to value in the declared unit (parsed and range-checked).
    """

    name: str
    time: float | None
    settings: dict[str, Any]


@dataclass(frozen=True)
class ResolvedValue:
    """A measured value validated against a system, in the variable's display unit.

    Attributes:
        point: Index of the point in the set.
        path: Variable path.
        value: Measured value in ``unit``.
        sigma: Standard uncertainty in ``unit``.
        unit: The variable's declared display unit.
        reference: The variable's reference (``gauge``, ``absolute``, ``difference``) or None.
        sigma_default: True when ``sigma`` is the default uncertainty.
    """

    point: int
    path: str
    value: float
    sigma: float
    unit: str
    reference: str | None
    sigma_default: bool


@dataclass(frozen=True)
class ResolvedMeasurements:
    """A measurement set validated against a system (:meth:`MeasurementSet.resolve`).

    Attributes:
        points: The points in order, with parsed settings.
        values: Every measured value in the variables' display units, point by point.
    """

    points: tuple[ResolvedPoint, ...]
    values: tuple[ResolvedValue, ...]

    @property
    def timed(self) -> bool:
        """True when any point has a time (the set needs a simulation)."""
        return any(p.time is not None for p in self.points)


@dataclass
class MeasurementSet:
    """A list of operating points with measured values (design 14.1).

    Build one with :meth:`from_dict` (plain data, e.g. from YAML, JSON or an MCP call) or
    :func:`load_measurements` (a file), or directly from :class:`MeasurementPoint` objects.

    Attributes:
        points: The operating points, in order.
        name: Optional name of the set.
        description: Optional free text.
    """

    points: list[MeasurementPoint]
    name: str | None = None
    description: str | None = None

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self) -> Iterator[MeasurementPoint]:
        return iter(self.points)

    @property
    def n_values(self) -> int:
        """Number of measured values over all points."""
        return sum(len(p.measured) for p in self.points)

    @property
    def paths(self) -> list[str]:
        """Measured paths, in order of first appearance."""
        return list(dict.fromkeys(path for p in self.points for path in p.measured))

    @classmethod
    def from_dict(cls, data: Any) -> MeasurementSet:
        """Parse the YAML/JSON structure: ``{points: [...], name?, description?}`` or a bare
        list of points.

        Raises:
            MeasurementError: Listing every problem found (bad structure, non-numeric
                values, unknown units, non-positive sigmas, duplicate point names).
        """
        return _parse_set(data)

    def to_dict(self) -> dict[str, Any]:
        """The YAML/JSON structure; :meth:`from_dict` reads it back."""
        out: dict[str, Any] = {}
        if self.name:
            out["name"] = self.name
        if self.description:
            out["description"] = self.description
        out["points"] = [p.to_dict() for p in self.points]
        return out

    def resolve(self, system: System) -> ResolvedMeasurements:
        """Validate against ``system`` and convert to its variables' display units.

        Measured paths must be reported numeric variables (observables, states, parameters,
        inputs, port variables or control results); settings must be settable paths with
        valid values that no control writes (and, at a steady point, not a state that a
        steady solve settles). The parser's checks are repeated (unique names, finite times
        and values, positive sigmas no smaller than :data:`MIN_SIGMA_RELATIVE` of the value
        or its kind's floor), so a set built from :class:`MeasurementPoint` objects is
        checked too. Measured values are converted to the declared unit and reference
        (``"3.8 bara"`` becomes 2.7867 bar gauge for a port pressure); sigmas convert by
        scale only; missing sigmas get :func:`default_sigma` of the measured value.

        Raises:
            MeasurementError: Listing every problem, each naming its point and path, with
                close matches for unknown paths.
        """
        return _resolve(self, system)

    def validate(self, system: System) -> None:
        """Raise :class:`~worldparts.errors.MeasurementError` if the set does not fit
        ``system`` (see :meth:`resolve`)."""
        self.resolve(system)


# ----------------------------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------------------------
def _number_and_unit(raw: Any, where: str) -> tuple[float, str | None]:
    """A number with an optional unit from a number or a string such as ``"2.71 bara"``."""
    if isinstance(raw, bool) or raw is None:
        raise InvalidValueError(f"{where}: expected a number or a string with a unit, got {raw!r}.")
    if isinstance(raw, (int, float)):
        number, unit = float(raw), None
    elif isinstance(raw, str):
        try:
            number, unit = parse_quantity(raw)
        except InvalidValueError as exc:
            raise InvalidValueError(f"{where}: {exc}") from None
    else:
        raise InvalidValueError(
            f"{where}: expected a number or a string with a unit such as '31.8 m3/h', got "
            f"{type(raw).__name__} {raw!r}."
        )
    if not math.isfinite(number):
        raise InvalidValueError(f"{where}: {raw!r} is not a finite number.")
    if unit is not None:
        _check_unit(unit, where)
    return number, unit


def _check_unit(unit: str, where: str) -> None:
    """Raise :class:`UnitError` when ``unit`` cannot be parsed. Whether it fits the variable
    (its dimension, absolute or difference) is decided against the system in
    :func:`resolve_value`, so a temperature-difference unit such as ``delta_degC`` passes
    here: it is right for a sigma and for a temperature-difference variable."""
    try:
        _pint_unit(_base_unit(unit))
    except UnitError as exc:
        raise UnitError(f"{where}: {exc}") from None


def _parse_time(raw: Any, where: str) -> float:
    """A point's time in s: non-negative and finite."""
    time = parse_duration(raw, where)
    if not math.isfinite(time):
        raise InvalidValueError(f"{where}: a time must be finite, got {raw!r}.")
    return time


def _seconds(t: float) -> str:
    """A time in s written without losing digits: ``60``, ``12345.25``, ``1000001``."""
    return str(int(t)) if float(t).is_integer() and abs(t) < 1e15 else repr(float(t))


def _time_name(t: float) -> str:
    """The default name of a timed point, ``t=<time> s``; distinct times give distinct
    names."""
    return f"t={_seconds(t)} s"


def _parse_measured(raw: Any, where: str) -> MeasuredValue:
    if not isinstance(raw, Mapping):
        value, unit = _number_and_unit(raw, where)
        return MeasuredValue(value, unit)
    unknown = sorted(str(k) for k in raw if k not in _VALUE_KEYS)
    if unknown:
        raise InvalidValueError(
            f"{where}: unknown key(s) {', '.join(map(repr, unknown))}; a measured value is "
            "{value, unit?, sigma?}, e.g. {value: '31.8 m3/h', sigma: '0.5 m3/h'}."
        )
    if raw.get("value") is None:
        raise InvalidValueError(f"{where}: 'value' is missing, e.g. {{value: '31.8 m3/h'}}.")
    value, unit = _number_and_unit(raw["value"], f"{where}.value")
    if raw.get("unit") not in (None, ""):
        if unit is not None:
            raise InvalidValueError(
                f"{where}: the value {raw['value']!r} already has a unit; drop 'unit' or give "
                "the value as a plain number."
            )
        unit = str(raw["unit"]).strip()
        _check_unit(unit, f"{where}.unit")
    sigma: float | None = None
    sigma_unit: str | None = None
    if raw.get("sigma") not in (None, ""):
        sigma, sigma_unit = _number_and_unit(raw["sigma"], f"{where}.sigma")
        if raw.get("sigma_unit") not in (None, ""):
            if sigma_unit is not None:
                raise InvalidValueError(f"{where}: the sigma already has a unit.")
            sigma_unit = str(raw["sigma_unit"]).strip()
            _check_unit(sigma_unit, f"{where}.sigma_unit")
        if not sigma > 0:
            raise InvalidValueError(
                f"{where}.sigma must be positive (a standard uncertainty), got {raw['sigma']!r}."
            )
        if (
            sigma_unit is not None
            and unit is not None
            and _base_unit(sigma_unit) == _base_unit(unit)
        ):
            sigma_unit = None
    return MeasuredValue(value, unit, sigma, sigma_unit)


def _parse_point(raw: Any, index: int, problems: list[str]) -> MeasurementPoint | None:
    where = f"points[{index}]"
    if not isinstance(raw, Mapping):
        problems.append(
            f"{where}: a point is a mapping {{name?, time?, settings?, measured}}, got "
            f"{type(raw).__name__}."
        )
        return None
    unknown = sorted(str(k) for k in raw if k not in _POINT_KEYS)
    if unknown:
        problems.append(
            f"{where}: unknown key(s) {', '.join(map(repr, unknown))}. "
            f"A point has: {', '.join(_POINT_KEYS)}."
        )
    name = raw.get("name")
    time: float | None = None
    if raw.get("time") is not None:
        try:
            time = _parse_time(raw["time"], f"{where}.time")
        except InvalidValueError as exc:
            problems.append(str(exc))
    if name is None:
        name = f"point_{index + 1}" if time is None else _time_name(time)
    where = f"{where} ('{name}')"
    settings = raw.get("settings") or {}
    if not isinstance(settings, Mapping):
        problems.append(f"{where}.settings must be a mapping of path to value.")
        settings = {}
    measured_raw = raw.get("measured") or {}
    if not isinstance(measured_raw, Mapping):
        problems.append(f"{where}.measured must be a mapping of path to value.")
        measured_raw = {}
    measured: dict[str, MeasuredValue] = {}
    for path, value in measured_raw.items():
        try:
            measured[str(path)] = _parse_measured(value, f"{where}.measured['{path}']")
        except InvalidValueError as exc:
            problems.append(str(exc))
    return MeasurementPoint(str(name), measured, {str(k): v for k, v in settings.items()}, time)


def _parse_set(data: Any) -> MeasurementSet:
    problems: list[str] = []
    name = description = None
    if isinstance(data, MeasurementSet):
        return data
    if isinstance(data, Mapping):
        unknown = sorted(str(k) for k in data if k not in _SET_KEYS)
        if unknown:
            problems.append(
                f"unknown top-level key(s) {', '.join(map(repr, unknown))}; a measurement set "
                "is {points: [...], name?, description?}."
            )
        name, description = data.get("name"), data.get("description")
        raw_points = data.get("points")
    else:
        raw_points = data
    if isinstance(raw_points, (str, bytes)) or not isinstance(raw_points, Sequence):
        raise MeasurementError(
            "A measurement set is {points: [{name, settings?, time?, measured: {path: value}}, "
            "...]} or a list of such points; got "
            + (type(raw_points).__name__ if raw_points is not None else "no 'points'")
            + ".",
            problems,
        )
    points: list[MeasurementPoint] = []
    for i, raw in enumerate(raw_points):
        point = _parse_point(raw, i, problems)
        if point is not None:
            points.append(point)
    seen: dict[str, int] = {}
    for p in points:
        seen[p.name] = seen.get(p.name, 0) + 1
    for n, count in seen.items():
        if count > 1:
            problems.append(f"The point name '{n}' is used {count} times; names must be unique.")
    if problems:
        raise MeasurementError("The measurement set is not valid:", problems)
    return MeasurementSet(
        points,
        None if name is None else str(name),
        None if description is None else str(description),
    )


def _read_csv(path: Path) -> MeasurementSet:
    text = path.read_text(encoding="utf-8-sig")
    numbered = [
        (n, row)
        for n, row in enumerate(csv.reader(text.splitlines()), start=1)
        if any(c.strip() for c in row)
    ]
    if not numbered:
        raise MeasurementError(f"{path}: the CSV file is empty.")
    header = [h.strip().lower() for h in numbered[0][1]]
    problems: list[str] = []
    unknown = [h for h in header if h not in CSV_COLUMNS]
    if unknown:
        raise MeasurementError(
            f"{path}: unknown CSV column(s) {', '.join(map(repr, unknown))}. Columns: "
            + ", ".join(CSV_COLUMNS)
            + " (path and value required, and point or time).",
        )
    missing = [c for c in ("path", "value") if c not in header]
    if missing or ("point" not in header and "time" not in header):
        raise MeasurementError(
            f"{path}: the CSV header must have 'path', 'value' and 'point' or 'time'; it has "
            + ", ".join(header)
            + "."
        )
    col = {name: header.index(name) for name in header}
    order: list[str] = []
    points: dict[str, dict[str, Any]] = {}

    def cell(row: list[str], name: str) -> str:
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    for n, row in numbered[1:]:
        where = f"{path.name} row {n}"
        time_text = cell(row, "time")
        time: float | None = None
        if time_text:
            try:
                time = _parse_time(
                    float(time_text) if _is_number(time_text) else time_text, f"{where}: time"
                )
            except InvalidValueError as exc:
                problems.append(str(exc))
                continue
        name = cell(row, "point") or (_time_name(time) if time is not None else "")
        if not name:
            problems.append(f"{where}: give a point name or a time.")
            continue
        entry = points.get(name)
        if entry is None:
            entry = {
                "name": name,
                "time": time,
                "settings": {},
                "measured": {},
                "row": n,
                "setting_rows": {},
            }
            points[name] = entry
            order.append(name)
        elif entry["time"] != time:
            problems.append(
                f"{where}: point '{name}' has time {_fmt_time(time)} here but "
                f"{_fmt_time(entry['time'])} in row {entry['row']}; a point has one time."
            )
            continue
        var = cell(row, "path")
        if not var:
            problems.append(f"{where}: 'path' is empty.")
            continue
        kind = (cell(row, "kind") or "measured").lower()
        value, unit, sigma = cell(row, "value"), cell(row, "unit"), cell(row, "sigma")
        if kind == "setting":
            if sigma:
                problems.append(f"{where}: a setting has no sigma.")
            if var in entry["settings"]:
                problems.append(
                    f"{where}: point '{name}' sets {var} twice (rows "
                    f"{entry['setting_rows'][var]} and {n}); give each setting once."
                )
                continue
            setting: Any = _setting_cell(value)
            if unit:
                setting = f"{value} {unit}"
            entry["settings"][var] = setting
            entry["setting_rows"][var] = n
        elif kind == "measured":
            if var in entry["measured"]:
                problems.append(f"{where}: point '{name}' measures {var} twice.")
                continue
            raw: dict[str, Any] = {"value": float(value) if _is_number(value) else value}
            if unit:
                raw["unit"] = unit
            if sigma:
                raw["sigma"] = float(sigma) if _is_number(sigma) else sigma
            entry["measured"][var] = raw
        else:
            problems.append(f"{where}: kind must be 'measured' or 'setting', got {kind!r}.")
    if problems:
        raise MeasurementError(f"{path}: the CSV file is not valid:", problems)
    data = [
        {
            k: v
            for k, v in points[n].items()
            if k not in ("row", "setting_rows") and v not in (None, {})
        }
        for n in order
    ]
    return _parse_set({"points": data})


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _setting_cell(text: str) -> Any:
    if _is_number(text):
        return float(text)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def _fmt_time(t: float | None) -> str:
    return "none" if t is None else f"{_seconds(t)} s"


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that records keys given twice in one mapping (the plain loader
    keeps the last value silently)."""

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self.duplicates: list[str] = []

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        if isinstance(node, yaml.MappingNode):
            first: dict[Any, int] = {}
            for key_node, _ in node.value:  # the mapping's own keys; a merge (<<) may override
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = self.construct_object(key_node, deep=True)
                line = key_node.start_mark.line + 1
                try:
                    seen = key in first
                except TypeError:  # an unhashable key; the base loader reports it
                    continue
                if seen:
                    self.duplicates.append(
                        f"line {line}: '{key}' is given twice in one mapping (first on line "
                        f"{first[key]}); give each key once."
                    )
                else:
                    first[key] = line
        return super().construct_mapping(node, deep=deep)


def _load_yaml(text: str) -> tuple[Any, list[str]]:
    """Parsed YAML and the keys given twice in one mapping."""
    loader = _UniqueKeyLoader(text)
    try:
        return loader.get_single_data(), loader.duplicates
    finally:
        loader.dispose()


def _load_json(text: str) -> tuple[Any, list[str]]:
    """Parsed JSON and the keys given twice in one object."""
    duplicates: list[str] = []

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                shown = [
                    repr(v) if len(repr(v)) <= 40 else repr(v)[:37] + "..."
                    for v in (out[key], value)
                ]
                duplicates.append(
                    f"'{key}' is given twice in one object (values {shown[0]} and {shown[1]}); "
                    "give each key once."
                )
            out[key] = value
        return out

    return json.loads(text, object_pairs_hook=pairs), duplicates


def load_measurements(path: str | os.PathLike[str]) -> MeasurementSet:
    """Read a measurement set from a YAML (``.yaml``, ``.yml``), JSON (``.json``) or
    long-format CSV (``.csv``) file (design 14.1).

    Raises:
        MeasurementError: When the file cannot be read or is not a valid measurement set;
            the message lists every problem.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".yaml", ".yml", ".json", ".csv"):
        raise MeasurementError(
            f"{p}: unknown measurement file type '{suffix}'. Use .yaml, .yml, .json or .csv."
        )
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise MeasurementError(f"Cannot read the measurement file {p}: {exc}.") from None
    if suffix == ".csv":
        return _read_csv(p)
    try:
        data, duplicates = _load_json(text) if suffix == ".json" else _load_yaml(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise MeasurementError(f"{p}: cannot parse the file: {exc}") from None
    if duplicates:
        raise MeasurementError(f"{p}: the measurement set is not valid:", duplicates)
    try:
        return _parse_set(data)
    except MeasurementError as exc:
        raise MeasurementError(f"{p}: the measurement set is not valid:", exc.problems) from None


# ----------------------------------------------------------------------------------------
# validation against a system
# ----------------------------------------------------------------------------------------
def _value_text(value: float, unit: str | None) -> Any:
    if unit is None:
        return value
    number = str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    return f"{number} {unit}"


def resolve_value(mv: MeasuredValue, info: VariableInfo, where: str) -> tuple[float, float, bool]:
    """``(value, sigma, sigma_default)`` of a measured value in the variable's display unit.

    Raises:
        InvalidValueError: For an incompatible unit or a variable without a default sigma.
    """
    unit = info.unit or "1"
    ref = info.pressure_reference
    value = parse_value(_value_text(mv.value, mv.unit), unit, where, ref)
    if not math.isfinite(value):
        raise InvalidValueError(f"{where}: the value must be a finite number, got {mv.value!r}.")
    if mv.sigma is None:
        return value, default_sigma(value, unit, ref), True
    if not (math.isfinite(mv.sigma) and mv.sigma > 0):
        raise InvalidValueError(
            f"{where}: sigma must be positive and finite (a standard uncertainty), got "
            f"{mv.sigma!r}."
        )
    sigma_unit = mv.sigma_unit or mv.unit
    if sigma_unit is None:
        return value, mv.sigma, False
    try:
        sigma = _difference(mv.sigma, sigma_unit, unit)
    except UnitError:
        raise UnitError(
            f"{where}: sigma {mv.sigma:g} {sigma_unit} is not compatible with {unit}."
        ) from None
    return value, sigma, False


def _sigma_problem(value: float, sigma: float, info: VariableInfo, where: str) -> str | None:
    """A problem when ``sigma`` is below :data:`MIN_SIGMA_RELATIVE` of ``max(|value|,
    floor)``, with the floor of the variable's kind of quantity (:data:`SIGMA_FLOORS`)."""
    unit = info.unit or "1"
    try:
        kind = quantity_kind(unit, info.pressure_reference)
        floor = _difference(*SIGMA_FLOORS[kind], unit)
    except InvalidValueError:
        floor = 0.0
    least = MIN_SIGMA_RELATIVE * max(abs(value), floor)
    if sigma >= least:
        return None
    return (
        f"{where}: sigma {sigma:.3g} {unit} is below {MIN_SIGMA_RELATIVE:g} of the value (or "
        f"of the default floor for its kind of quantity), {least:.3g} {unit}. No instrument "
        "is that accurate, and the normalised residuals would overflow; give the instrument's "
        "real uncertainty."
    )


def measurable_paths(system: System) -> dict[str, VariableInfo]:
    """Reported numeric variables of ``system`` (paths that can be measured)."""
    return {
        v.path: v
        for v in system.variables()
        if v.reported and v.type in ("number", "integer") and v.unit is not None
    }


def parse_settings(
    system: System,
    settings: Mapping[str, Any],
    where: str,
    problems: list[str],
    time: float | None = None,
) -> dict[str, Any]:
    """Settings parsed into declared units; problems are appended to ``problems``.

    Besides unknown paths and invalid values, a setting is a problem when a control writes
    its path (the control would override it: silently at a steady point, as a rejected
    event in a time series), and, at a steady point (``time`` None), when it sets a state
    that a steady solve puts to its equilibrium (``steady: settle``), which discards it.
    """
    controlled = {c.actuate: name for name, c in system.controls.items()}
    out: dict[str, Any] = {}
    for path, raw in settings.items():
        try:
            _, _, spec = system._settable(path)
            out[path] = spec.parse(raw, f"{where}.settings['{path}']")
        except WorldpartsError as exc:
            problems.append(f"{where}.settings: {exc}")
            continue
        if path in controlled:
            problems.append(
                f"{where} sets {path}, which control '{controlled[path]}' writes, so the "
                "control would override the setting. Remove the setting, or remove the "
                f"control (System.remove_control) and set {path} at every point."
            )
        elif time is None and spec.kind == "state" and spec.steady == "settle":
            problems.append(
                f"{where} sets {path}, a state that a steady solve puts to its equilibrium, "
                "so the setting would have no effect. Set the input that drives it, or give "
                "the point a time (a time series applies it as an event)."
            )
    return out


def _resolve(ms: MeasurementSet, system: System) -> ResolvedMeasurements:
    variables = measurable_paths(system)
    everything = {v.path: v for v in system.variables()}
    problems: list[str] = []
    unknown: dict[str, list[str]] = {}
    points: list[ResolvedPoint] = []
    values: list[ResolvedValue] = []
    # A set built directly from MeasurementPoint objects has not been through the parser:
    # repeat its checks on names and times here.
    counts: dict[str, int] = {}
    for pt in ms.points:
        counts[pt.name] = counts.get(pt.name, 0) + 1
    problems += [
        f"The point name '{n}' is used {c} times; names must be unique."
        for n, c in counts.items()
        if c > 1
    ]
    for i, pt in enumerate(ms.points):
        where = f"point '{pt.name}'"
        if pt.time is not None and not (math.isfinite(pt.time) and pt.time >= 0):
            problems.append(f"{where}: time must be finite and not negative, got {pt.time!r}.")
        settings = parse_settings(system, pt.settings, where, problems, pt.time)
        points.append(ResolvedPoint(pt.name, pt.time, settings))
        for path, mv in pt.measured.items():
            info = variables.get(path)
            if info is None:
                other = everything.get(path)
                if other is not None:
                    problems.append(
                        f"{where}: {path} is a {other.type} {other.kind}, which is not a "
                        "measurable number."
                    )
                else:
                    unknown.setdefault(path, []).append(pt.name)
                continue
            try:
                value, sigma, default = resolve_value(mv, info, f"{where}: {path}")
            except InvalidValueError as exc:
                problems.append(str(exc))
                continue
            problem = _sigma_problem(value, sigma, info, f"{where}: {path}")
            if problem:
                problems.append(problem)
                continue
            values.append(
                ResolvedValue(
                    i, path, value, sigma, info.unit or "1", info.pressure_reference, default
                )
            )
    for path, names in unknown.items():
        shown = ", ".join(f"'{n}'" for n in names[:5]) + (" ..." if len(names) > 5 else "")
        hint = format_choices(path, variables, list_valid=False)
        problems.append(
            f"'{path}' (in point(s) {shown}) is not a variable of system '{system.name}'."
            + (f" {hint}" if hint else "")
        )
    problems.extend(_time_conflicts(points))
    if problems:
        if unknown:
            names = sorted(variables)
            more = f" (and {len(names) - 60} more)" if len(names) > 60 else ""
            problems.append("Measurable paths: " + ", ".join(names[:60]) + more + ".")
        raise MeasurementError(
            f"The measurements do not fit system '{system.name}' ({len(problems)} problem(s)):",
            problems,
        )
    return ResolvedMeasurements(tuple(points), tuple(values))


def _time_conflicts(points: Iterable[ResolvedPoint]) -> list[str]:
    """Timed points at one time must not set one path to different values."""
    by_time: dict[float, dict[str, tuple[Any, str]]] = {}
    out: list[str] = []
    for p in points:
        if p.time is None:
            continue
        slot = by_time.setdefault(p.time, {})
        for path, value in p.settings.items():
            if path in slot and slot[path][0] != value:
                out.append(
                    f"Points '{slot[path][1]}' and '{p.name}' both at t = {_seconds(p.time)} "
                    f"s set {path} to different values ({slot[path][0]!r} and {value!r})."
                )
            else:
                slot[path] = (value, p.name)
    return out
