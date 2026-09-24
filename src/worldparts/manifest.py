"""Component manifests: model, loading and validation (design section 4).

A manifest is a YAML file validated against ``schemas/component-manifest.schema.json`` and
then checked semantically (unique names, defaults inside limits, table shapes, parseable
expressions that only reference known names, declared warning codes, scenario references,
fault modes that vary a number parameter or input within its limits).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache, cached_property
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import yaml

from worldparts.errors import (
    ExpressionError,
    InvalidValueError,
    ManifestError,
    OutOfRangeError,
    UnknownVariableError,
    format_choices,
)
from worldparts.expressions import compile_expression
from worldparts.units import (
    QUANTITIES,
    UnitConverter,
    converter,
    is_pressure_unit,
    is_temperature_unit,
    parse_value,
)

__all__ = [
    "PORT_VARIABLES",
    "ColumnSpec",
    "FaultSpec",
    "Manifest",
    "ModeSpec",
    "PortSpec",
    "Table",
    "VariableSpec",
    "WarningSpec",
    "load_manifest",
    "load_schema",
    "load_yaml",
    "validate_manifest_data",
]

_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(text: str) -> Any:
    """Parse YAML safely (with libyaml when available)."""
    return yaml.load(text, Loader=_YAML_LOADER)


#: Port variables with their display units and pressure reference.
PORT_VARIABLES: dict[str, tuple[str, str | None, str]] = {
    "p": ("bar", "gauge", "Pressure at the port (gauge)."),
    "m_flow": ("kg/s", None, "Mass flow into the component through the port."),
    "T": ("degC", None, "Temperature of the water at the port node."),
}


@cache
def load_schema(name: str) -> dict[str, Any]:
    """Load a packaged JSON Schema: ``"component-manifest"`` or ``"system"``."""
    text = resources.files("worldparts").joinpath(f"schemas/{name}.schema.json").read_text("utf-8")
    return json.loads(text)


@cache
def _validator(name: str) -> jsonschema.protocols.Validator:
    schema = load_schema(name)
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema, format_checker=cls.FORMAT_CHECKER)


#: Longest excerpt of an offending value quoted in a schema error message.
_MAX_EXCERPT = 60


def _json_type(value: Any) -> str:
    """The JSON type name of a Python value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def _excerpt(value: Any) -> str:
    """A short description of an offending value: its repr when short, else its type."""
    text = repr(value)
    if len(text) <= _MAX_EXCERPT:
        return text
    if isinstance(value, Mapping):
        keys = ", ".join(repr(k) for k in list(value)[:5])
        more = ", ..." if len(value) > 5 else ""
        return f"an object with keys {keys}{more}"
    if isinstance(value, (list, tuple)):
        return f"an array of {len(value)} items"
    return text[: _MAX_EXCERPT - 3] + "..."


def _schema_message(e: jsonschema.ValidationError) -> str:
    """A schema error message that never echoes a large offending value."""
    if e.validator == "type":
        expected = e.validator_value
        if isinstance(expected, list):
            expected = " or ".join(str(x) for x in expected)
        got = _excerpt(e.instance)
        if not got.startswith("an "):
            got = f"{_json_type(e.instance)} {got}"
        return f"must be of type {expected}, got {got}"
    if e.validator in ("minItems", "maxItems") and isinstance(e.instance, (list, tuple)):
        which = "at least" if e.validator == "minItems" else "at most"
        return f"must have {which} {e.validator_value} items, got {len(e.instance)}"
    msg = e.message
    if e.validator == "oneOf" and e.context:
        best = min(e.context, key=lambda c: len(list(c.absolute_path)))
        return f"{_excerpt(e.instance)} matches none of the allowed forms (closest: " + (
            f"{_schema_message(best)})"
        )
    if len(msg) > 300:
        msg = msg[:297] + "..."
    return msg


def _schema_errors(name: str, data: Any, hints: Mapping[str, str] | None = None) -> list[str]:
    """Schema errors as ``'<location>: <message>'`` lines.

    Messages name the expected type and describe (not echo) large offending values. With
    ``hints``, a hint keyed by the first path element (or ``"(root)"``) is appended to the
    first error at that place, e.g. the expected shape of a list item.
    """
    errors = sorted(_validator(name).iter_errors(data), key=lambda e: list(e.absolute_path))
    out = []
    hinted: set[str] = set()
    for e in errors:
        path = list(e.absolute_path)
        loc = "/".join(str(p) for p in path) or "(root)"
        line = f"{loc}: {_schema_message(e)}"
        key = str(path[0]) if path else "(root)"
        if hints and key in hints and key not in hinted:
            hinted.add(key)
            line += f" ({hints[key]})"
        out.append(line)
    return out


class Table:
    """A table parameter: rows of numbers with one SI numpy array per column.

    Attributes:
        columns: Column names in order.
        array: 2-D array (rows x columns) in SI.
    """

    def __init__(self, columns: Iterable[str], array: np.ndarray) -> None:
        self.columns = tuple(columns)
        self.array = np.asarray(array, dtype=float)
        self.array.setflags(write=False)

    def __getitem__(self, column: str) -> np.ndarray:
        try:
            return self.array[:, self.columns.index(column)]
        except ValueError:
            raise KeyError(
                f"Table has no column '{column}'. Valid: {', '.join(self.columns)}."
            ) from None

    def __len__(self) -> int:
        return int(self.array.shape[0])

    @property
    def rows(self) -> int:
        """Number of rows."""
        return len(self)

    def __repr__(self) -> str:
        return f"Table(columns={self.columns}, rows={self.rows})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Table)
            and self.columns == other.columns
            and self.array.shape == other.array.shape
            and bool(np.allclose(self.array, other.array))
        )

    __hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PortSpec:
    """A port declared in a manifest."""

    name: str
    type: str
    medium: str
    description: str


@dataclass(frozen=True)
class ColumnSpec:
    """A column of a table parameter."""

    name: str
    unit: str
    description: str = ""
    pressure_reference: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    @property
    def converter(self) -> UnitConverter:
        """Converter between the column unit and SI."""
        return converter(self.unit, self.pressure_reference)


@dataclass(frozen=True)
class VariableSpec:
    """A parameter, input, state or observable declared in a manifest.

    Attributes:
        name: Local name.
        kind: ``parameter``, ``input``, ``state`` or ``observable``.
        description: Text for agents.
        unit: Declared (display) unit; None for string, boolean and table parameters.
        type: ``number``, ``integer``, ``string``, ``boolean`` or ``table``.
        default: Default in the declared unit (tables: list of rows).
        minimum: Hard lower limit in the declared unit.
        maximum: Hard upper limit in the declared unit.
        enum: Allowed strings for string parameters.
        columns: Table columns.
        min_rows: Minimum table rows.
        pressure_reference: For pressure units.
        steady: For states, ``settle`` or ``hold``.
        quantity: ``temperature_difference`` for a temperature difference (e.g. a rise in K),
            which converts between units by scale only; None otherwise.
        measurable: False for an observable that is a model quantity rather than a physical
            state of the plant (for example a property of a fitted curve), which is never
            proposed as a sensor.
    """

    name: str
    kind: str
    description: str
    unit: str | None = None
    type: str = "number"
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[str, ...] | None = None
    columns: tuple[ColumnSpec, ...] | None = None
    min_rows: int | None = None
    pressure_reference: str | None = None
    steady: str | None = None
    quantity: str | None = None
    measurable: bool = True

    @property
    def is_numeric(self) -> bool:
        """True for number and integer variables."""
        return self.type in ("number", "integer")

    @cached_property
    def converter(self) -> UnitConverter:
        """Converter between the declared unit and SI (numeric variables only; computed
        once, since solves convert every value)."""
        assert self.unit is not None
        return converter(self.unit, self.reference)

    @cached_property
    def reference(self) -> str | None:
        """Effective reference used for unit conversion (computed once).

        Pressures: ``gauge`` (default), ``absolute`` or ``difference``. Temperature
        differences (``quantity: temperature_difference``): ``difference``. Otherwise None.
        """
        if self.unit is None or not self.is_numeric:
            return None
        if is_pressure_unit(self.unit):
            return self.pressure_reference or "gauge"
        if self.quantity is not None and is_temperature_unit(self.unit):
            return QUANTITIES.get(self.quantity)
        return None

    def limits_text(self) -> str:
        """Human-readable allowed range (dimensionless ranges carry no unit)."""
        lo = "-inf" if self.minimum is None else f"{self.minimum:g}"
        hi = "inf" if self.maximum is None else f"{self.maximum:g}"
        return f"[{lo}, {hi}]{_unit_suffix(self.unit, self.reference)}"

    def parse(self, value: Any, path: str) -> Any:
        """Validate ``value`` and return it in the declared unit (canonical display form).

        Numbers are returned as floats in the declared unit, strings as strings, booleans as
        booleans and tables as lists of rows in the declared column units.

        Raises:
            InvalidValueError: Wrong type, unit or shape.
            OutOfRangeError: Outside the hard limits.
        """
        if self.type == "string":
            if not isinstance(value, str) or (self.enum and value not in self.enum):
                raise InvalidValueError(
                    f"{path}: {value!r} is not a valid choice. "
                    + format_choices(str(value), self.enum or ())
                )
            return value
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise InvalidValueError(f"{path}: expected true or false, got {value!r}.")
        if self.type == "table":
            return self._parse_table(value, path)
        assert self.unit is not None
        number = parse_value(value, self.unit, path, self.reference)
        if not math.isfinite(number):
            raise InvalidValueError(f"{path}: {value!r} is not a finite number.")
        if self.type == "integer":
            if abs(number - round(number)) > 1e-9:
                raise InvalidValueError(f"{path}: expected an integer, got {value!r}.")
            number = float(round(number))
        self.check_range(number, path)
        return number

    def check_range(self, number: float, path: str) -> None:
        """Raise :class:`OutOfRangeError` when ``number`` (declared unit) is out of limits."""
        tol = 1e-12 * max(1.0, abs(number))
        if (self.minimum is not None and number < self.minimum - tol) or (
            self.maximum is not None and number > self.maximum + tol
        ):
            raise OutOfRangeError(
                f"{path} = {number:g}{_unit_suffix(self.unit)} is outside the allowed range "
                f"{self.limits_text()}."
            )

    def _parse_table(self, value: Any, path: str) -> list[list[float]]:
        assert self.columns is not None
        ncol = len(self.columns)
        names = ", ".join(f"{c.name} [{c.unit}]" for c in self.columns)
        if isinstance(value, Table):
            value = self.table_to_rows(value)
        if isinstance(value, np.ndarray):
            value = value.tolist()
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(r, (list, tuple)) for r in value
        ):
            raise InvalidValueError(
                f"{path}: expected a table as a list of rows [{names}], got {value!r}."
            )
        rows: list[list[float]] = []
        for i, row in enumerate(value):
            if len(row) != ncol:
                raise InvalidValueError(
                    f"{path}: row {i} has {len(row)} values; each row needs {ncol} ({names})."
                )
            out_row = []
            for c, v in zip(self.columns, row, strict=True):
                cell = f"{path}[{i}].{c.name}"
                x = parse_value(v, c.unit, cell, c.converter.reference)
                if not math.isfinite(x):
                    raise InvalidValueError(f"{cell}: not a finite number.")
                tol = 1e-12 * max(1.0, abs(x))
                if (c.minimum is not None and x < c.minimum - tol) or (
                    c.maximum is not None and x > c.maximum + tol
                ):
                    lo = "-inf" if c.minimum is None else f"{c.minimum:g}"
                    hi = "inf" if c.maximum is None else f"{c.maximum:g}"
                    raise OutOfRangeError(
                        f"{cell} = {x:g}{_unit_suffix(c.unit)} is outside the allowed range "
                        f"[{lo}, {hi}]{_unit_suffix(c.unit)}."
                    )
                out_row.append(x)
            rows.append(out_row)
        if len(rows) < (self.min_rows or 1):
            raise InvalidValueError(
                f"{path}: the table needs at least {self.min_rows or 1} rows ({names}), "
                f"got {len(rows)}."
            )
        return rows

    def to_si(self, display: Any) -> Any:
        """Convert a canonical display value (from :meth:`parse`) to SI.

        Tables become :class:`Table` objects with SI columns.
        """
        if self.type == "table":
            assert self.columns is not None
            arr = np.array(
                [
                    [c.converter.to_si(x) for c, x in zip(self.columns, row, strict=True)]
                    for row in display
                ],
                dtype=float,
            )
            return Table((c.name for c in self.columns), arr)
        if self.is_numeric:
            return self.converter.to_si(float(display))
        return display

    def from_si(self, value: Any) -> Any:
        """Convert an SI value to its canonical display form."""
        if self.type == "table":
            return self.table_to_rows(value)
        if self.is_numeric:
            return None if value is None else self.converter.from_si(float(value))
        return value

    def table_to_rows(self, table: Table) -> list[list[float]]:
        """Rows of a :class:`Table` in the declared column units."""
        assert self.columns is not None
        return [
            [
                float(c.converter.from_si(float(x)) or 0.0)
                for c, x in zip(self.columns, row, strict=True)
            ]
            for row in table.array
        ]


@dataclass(frozen=True)
class ModeSpec:
    """A discrete mode; the first mode whose condition holds is the component's mode."""

    name: str
    condition: str
    description: str


@dataclass(frozen=True)
class WarningSpec:
    """A warning code: an envelope rule (with condition and message) or a code-emitted one."""

    code: str
    severity: str
    description: str
    condition: str | None = None

    @property
    def is_envelope(self) -> bool:
        """True for envelope rules."""
        return self.condition is not None


@dataclass(frozen=True)
class FaultSpec:
    """A fault mode declared in a manifest (design 14.3): a failure that shows as one
    numeric parameter or input moving within a plausible range.

    Attributes:
        name: Fault name, unique within the manifest (``worn_impeller``); a system refers
            to it as ``<instance>.<name>``.
        description: What the failure is and how it shows.
        path: Local name of the number parameter or input the fault varies.
        lower: Lower end of the range, in the variable's declared unit (a multiple of its
            current value when ``relative``).
        upper: Upper end of the range, likewise.
        healthy: The value without the fault, inside ``[lower, upper]``, likewise.
        relative: When true, ``lower``, ``upper`` and ``healthy`` are non-negative multiples
            of the value the variable has in the system being diagnosed (for a commanded
            input such as a valve opening, or a value that varies by installation such as a
            pipe's roughness).
    """

    name: str
    description: str
    path: str
    lower: float
    upper: float
    healthy: float
    relative: bool = False

    def resolve(self, current: float) -> tuple[float, float, float]:
        """``(lower, upper, healthy)`` in the variable's unit for a variable whose value in
        the system is ``current`` (unchanged unless the fault is relative)."""
        if not self.relative:
            return self.lower, self.upper, self.healthy
        return self.lower * current, self.upper * current, self.healthy * current

    def to_dict(self) -> dict[str, Any]:
        """The manifest form ``{name, description, vary: {path, lower, upper, relative?},
        healthy}``."""
        vary: dict[str, Any] = {"path": self.path, "lower": self.lower, "upper": self.upper}
        if self.relative:
            vary["relative"] = True
        return {
            "name": self.name,
            "description": self.description,
            "vary": vary,
            "healthy": self.healthy,
        }


def _cond_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@dataclass
class Manifest:
    """A validated component manifest.

    Attributes:
        data: The raw manifest dict.
        path: Source file, when loaded from disk.
    """

    data: dict[str, Any]
    path: Path | None = None
    ports: dict[str, PortSpec] = field(init=False)
    parameters: dict[str, VariableSpec] = field(init=False)
    inputs: dict[str, VariableSpec] = field(init=False)
    states: dict[str, VariableSpec] = field(init=False)
    observables: dict[str, VariableSpec] = field(init=False)
    modes: list[ModeSpec] = field(init=False)
    envelope: list[WarningSpec] = field(init=False)
    warnings: dict[str, WarningSpec] = field(init=False)
    faults: dict[str, FaultSpec] = field(init=False)

    def __post_init__(self) -> None:
        d = self.data
        self.ports = {
            p["name"]: PortSpec(p["name"], p["type"], p["medium"], p["description"])
            for p in d.get("ports", [])
        }
        self.parameters = {p["name"]: _var(p, "parameter") for p in d.get("parameters", [])}
        self.inputs = {p["name"]: _var(p, "input") for p in d.get("inputs", [])}
        self.states = {p["name"]: _var(p, "state") for p in d.get("states", [])}
        self.observables = {p["name"]: _var(p, "observable") for p in d.get("observables", [])}
        self.modes = [
            ModeSpec(m["name"], _cond_text(m["condition"]), m["description"])
            for m in d.get("modes", [])
        ]
        self.envelope = [
            WarningSpec(e["code"], e["severity"], e["message"], _cond_text(e["condition"]))
            for e in d.get("envelope", [])
        ]
        self.warnings = {w.code: w for w in self.envelope}
        for w in d.get("warnings", []):
            self.warnings.setdefault(
                w["code"], WarningSpec(w["code"], w["severity"], w["description"])
            )
        self.faults = {}
        for f in d.get("faults", []):
            vary = f["vary"]
            self.faults.setdefault(
                f["name"],
                FaultSpec(
                    f["name"],
                    f["description"],
                    vary["path"],
                    float(vary["lower"]),
                    float(vary["upper"]),
                    float(f["healthy"]),
                    bool(vary.get("relative", False)),
                ),
            )

    # -- convenience ------------------------------------------------------------------------
    @property
    def id(self) -> str:
        """Full component id, e.g. ``worldparts.hydraulic.valve``."""
        return str(self.data["id"])

    @property
    def alias(self) -> str:
        """Short alias: the last segment of the id."""
        return self.id.rsplit(".", 1)[-1]

    @property
    def name(self) -> str:
        """Display name."""
        return str(self.data["name"])

    @property
    def summary(self) -> str:
        """One-line summary."""
        return str(self.data["summary"])

    @property
    def version(self) -> str:
        """Component definition version."""
        return str(self.data["version"])

    @property
    def implementation(self) -> str:
        """Reference implementation import path ``module:Class``."""
        return str(self.data["implementations"]["reference"]["python"])

    @property
    def scenarios(self) -> list[dict[str, Any]]:
        """Scenario definitions (raw dicts)."""
        return list(self.data.get("scenarios", []))

    @property
    def contracts(self) -> list[dict[str, Any]]:
        """Contract definitions (raw dicts)."""
        return list(self.data.get("contracts", []))

    def scenario(self, scenario_id: str) -> dict[str, Any]:
        """The scenario with ``scenario_id``.

        Raises:
            UnknownVariableError: When there is no such scenario.
        """
        for s in self.scenarios:
            if s["id"] == scenario_id:
                return s
        raise UnknownVariableError(
            f"{self.id} has no scenario '{scenario_id}'. "
            + format_choices(scenario_id, [s["id"] for s in self.scenarios])
        )

    def variables(self) -> dict[str, VariableSpec]:
        """All parameters, inputs, states and observables by local name."""
        out: dict[str, VariableSpec] = {}
        for group in (self.parameters, self.inputs, self.states, self.observables):
            out.update(group)
        return out

    def variable(self, name: str) -> VariableSpec:
        """Look up any declared variable by local name.

        Raises:
            UnknownVariableError: Listing the valid names.
        """
        allvars = self.variables()
        if name in allvars:
            return allvars[name]
        raise UnknownVariableError(
            f"{self.alias} has no variable '{name}'. " + format_choices(name, allvars)
        )

    def settable(self) -> dict[str, VariableSpec]:
        """Variables accepted by ``System.set``: parameters, inputs and states."""
        out: dict[str, VariableSpec] = {}
        for group in (self.parameters, self.inputs, self.states):
            out.update(group)
        return out

    def local_names(self) -> list[str]:
        """Names usable in this manifest's mode and envelope expressions.

        Numeric and boolean variables and port variables; string and table parameters are
        excluded because the expression language has no string or table values.
        """
        names = [n for n, v in self.variables().items() if v.type not in ("table", "string")]
        for port in self.ports:
            names.extend(f"{port}.{pv}" for pv in PORT_VARIABLES)
        return names

    def defaults(self) -> dict[str, Any]:
        """Canonical default values (declared units) for parameters and inputs."""
        out = {n: v.default for n, v in self.parameters.items()}
        out.update({n: v.default for n, v in self.inputs.items()})
        return out

    def to_dict(self) -> dict[str, Any]:
        """The raw manifest dict (a deep copy is not made)."""
        return self.data

    def describe(self) -> dict[str, Any]:
        """A compact description for listings."""
        key = [
            {"name": v.name, "unit": v.unit, "default": v.default}
            for v in list(self.parameters.values())[:6]
        ]
        return {
            "id": self.id,
            "alias": self.alias,
            "name": self.name,
            "summary": self.summary,
            "ports": list(self.ports),
            "key_parameters": key,
            "fidelity": self.data["fidelity"]["level"],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], path: Path | None = None) -> Manifest:
        """Validate ``data`` (schema and semantics) and build a manifest.

        Raises:
            ManifestError: Listing every problem found.
        """
        data = dict(data)
        where = f" ({path})" if path else ""
        problems = _schema_errors("component-manifest", data)
        if problems:
            raise ManifestError(
                f"Manifest {data.get('id', '?')}{where} does not match the schema.",
                problems,
                path,
            )
        m = cls(data, path)
        problems = m._semantic_problems()
        if problems:
            raise ManifestError(f"Manifest {m.id}{where} is inconsistent.", problems, path)
        return m

    # -- semantic validation ----------------------------------------------------------------
    def _semantic_problems(self) -> list[str]:
        out: list[str] = []
        d = self.data
        seen: dict[str, str] = {}
        for port in self.ports:
            seen[port] = "port"
        for group, kind in (
            ("parameters", "parameter"),
            ("inputs", "input"),
            ("states", "state"),
            ("observables", "observable"),
        ):
            for item in d.get(group, []):
                n = item["name"]
                if n in seen:
                    out.append(f"{group}: name '{n}' is already used by a {seen[n]}.")
                seen[n] = kind
        for group in ("ports", "modes"):
            names = [x["name"] for x in d.get(group, [])]
            dups = {n for n in names if names.count(n) > 1}
            if dups:
                out.append(f"{group}: duplicate names {sorted(dups)}.")
        codes = [e["code"] for e in d.get("envelope", [])] + [
            w["code"] for w in d.get("warnings", [])
        ]
        dups = {c for c in codes if codes.count(c) > 1}
        if dups:
            out.append(f"warning codes declared twice (envelope/warnings): {sorted(dups)}.")
        # defaults and limits
        for spec in self.variables().values():
            label = f"{spec.kind} '{spec.name}'"
            if (
                spec.minimum is not None
                and spec.maximum is not None
                and spec.minimum > spec.maximum
            ):
                out.append(f"{label}: minimum {spec.minimum} exceeds maximum {spec.maximum}.")
            if spec.kind in ("parameter", "input") or (
                spec.kind == "state" and spec.default is not None
            ):
                try:
                    spec.parse(spec.default, f"{label} default")
                except (InvalidValueError, OutOfRangeError) as exc:
                    out.append(str(exc))
            if spec.unit and spec.is_numeric and is_pressure_unit(spec.unit):
                looks_diff = re.search(r"(drop|difference|delta|dp|rise|loss)", spec.name)
                if looks_diff and spec.reference == "gauge":
                    out.append(
                        f"{label}: its name suggests a pressure difference; declare "
                        "'pressure_reference: difference' (pressures default to gauge)."
                    )
            if spec.unit and spec.is_numeric and is_temperature_unit(spec.unit):
                looks_diff = re.search(
                    r"(rise|difference|delta|drop|increase|decrease|approach|dt$|_dt)", spec.name
                )
                if looks_diff and spec.quantity != "temperature_difference":
                    out.append(
                        f"{label}: its name suggests a temperature difference; declare "
                        "'quantity: temperature_difference' (temperatures default to absolute, "
                        "so 35 K would otherwise convert to -238.15 degC)."
                    )
            if spec.quantity == "temperature_difference" and not (
                spec.unit and spec.is_numeric and is_temperature_unit(spec.unit)
            ):
                out.append(
                    f"{label}: quantity 'temperature_difference' needs a temperature unit "
                    f"(K or degC), but the unit is {spec.unit!r}."
                )
            if spec.pressure_reference is not None and not (
                spec.unit and spec.is_numeric and is_pressure_unit(spec.unit)
            ):
                out.append(
                    f"{label}: pressure_reference is only for pressures (unit Pa, kPa or bar), "
                    f"but the unit is {spec.unit!r}; remove it."
                )
            if spec.type == "table" and spec.columns:
                cols = [c.name for c in spec.columns]
                if len(set(cols)) != len(cols):
                    out.append(f"{label}: duplicate column names {cols}.")
                for c in spec.columns:
                    if c.pressure_reference is not None and not is_pressure_unit(c.unit):
                        out.append(
                            f"{label} column '{c.name}': pressure_reference is only for "
                            f"pressures, but the unit is {c.unit!r}; remove it."
                        )
                    if c.minimum is not None and c.maximum is not None and c.minimum > c.maximum:
                        out.append(
                            f"{label} column '{c.name}': minimum {c.minimum} exceeds maximum "
                            f"{c.maximum}."
                        )
        # expressions
        local = set(self.local_names())
        non_numeric = {
            n: v.type for n, v in self.variables().items() if v.type in ("string", "table")
        }
        for m in self.modes:
            out.extend(_expr_problems(f"mode '{m.name}'", m.condition, local, non_numeric))
        for e in self.envelope:
            out.extend(
                _expr_problems(f"envelope '{e.code}'", e.condition or "", local, non_numeric)
            )
        # scenarios and contracts
        scen_ids = [s["id"] for s in self.scenarios]
        dups = {s for s in scen_ids if scen_ids.count(s) > 1}
        if dups:
            out.append(f"scenarios: duplicate ids {sorted(dups)}.")
        cids = [c["id"] for c in self.contracts]
        dups = {c for c in cids if cids.count(c) > 1}
        if dups:
            out.append(f"contracts: duplicate ids {sorted(dups)}.")
        has_equal = False
        for c in self.contracts:
            if c["scenario"] not in scen_ids:
                out.append(
                    f"contract '{c['id']}': unknown scenario '{c['scenario']}'. "
                    + format_choices(c["scenario"], scen_ids)
                )
            chk = c["check"]
            if chk["type"] == "equal":
                has_equal = True
            for key in ("left", "right", "min", "max", "condition"):
                if key in chk:
                    try:
                        compile_expression(_cond_text(chk[key]))
                    except ExpressionError as exc:
                        out.append(f"contract '{c['id']}' {key}: {exc}")
        if self.contracts and not has_equal:
            out.append("contracts: at least one 'equal' check (mass conservation) is required.")
        out.extend(self._fault_problems(seen))
        return out

    def _fault_problems(self, names: Mapping[str, str]) -> list[str]:
        """Problems of the ``faults`` list (design 14.3): unique names that do not shadow a
        variable or port, a path that is a number parameter or input, ``lower < upper``,
        absolute bounds within the variable's hard limits, relative bounds not negative,
        and ``healthy`` within the bounds."""
        out: list[str] = []
        raw = [f["name"] for f in self.data.get("faults", [])]
        dups = sorted({n for n in raw if raw.count(n) > 1})
        if dups:
            out.append(f"faults: duplicate names {dups}.")
        numeric = [
            n
            for group in (self.parameters, self.inputs)
            for n, s in group.items()
            if s.type == "number"
        ]
        for f in self.faults.values():
            label = f"fault '{f.name}'"
            if f.name in names:
                kind = names[f.name]
                article = "an" if kind[0] in "aeiou" else "a"
                out.append(
                    f"{label}: the name is already used by {article} {kind}; a fault is "
                    f"referred to as '<instance>.{f.name}', so give it a name of its own."
                )
            spec = self.parameters.get(f.path) or self.inputs.get(f.path)
            if spec is None or spec.type != "number":
                if spec is not None:
                    article = "an" if spec.type[0] in "aeiou" else "a"
                    what = f"{article} {spec.type} {spec.kind}"
                elif f.path in self.states:
                    what = "a state"
                elif f.path in self.observables:
                    what = "an observable"
                else:
                    what = "not a variable of this component"
                out.append(
                    f"{label}: vary.path '{f.path}' is {what}; a fault varies a number "
                    "parameter or input. " + format_choices(f.path, numeric)
                )
                continue
            infinite = [
                key
                for key, value in (
                    ("vary.lower", f.lower),
                    ("vary.upper", f.upper),
                    ("healthy", f.healthy),
                )
                if not math.isfinite(value)
            ]
            if infinite:
                what = "a finite number" if len(infinite) == 1 else "finite numbers"
                out.append(
                    f"{label}: {' and '.join(infinite)} must be {what}, not infinity or NaN (a "
                    f"fault's range is fitted, so it needs finite ends within the hard limits "
                    f"of '{f.path}', {spec.limits_text()})."
                )
                continue
            if not f.lower < f.upper:
                out.append(
                    f"{label}: vary.lower ({f.lower:g}) must be below vary.upper ({f.upper:g})."
                )
                continue
            if f.relative:
                if f.lower < 0:
                    out.append(
                        f"{label}: relative bounds are non-negative multiples of the current "
                        f"value of '{f.path}'; vary.lower is {f.lower:g}."
                    )
            else:
                tol = 1e-12 * max(1.0, abs(f.lower), abs(f.upper))
                if (spec.minimum is not None and f.lower < spec.minimum - tol) or (
                    spec.maximum is not None and f.upper > spec.maximum + tol
                ):
                    out.append(
                        f"{label}: the range [{f.lower:g}, {f.upper:g}]"
                        f"{_unit_suffix(spec.unit, spec.reference)} must lie within the hard "
                        f"limits of '{f.path}', {spec.limits_text()}."
                    )
            if not f.lower <= f.healthy <= f.upper:
                kind = "multiple of the current value" if f.relative else "value"
                out.append(
                    f"{label}: healthy ({f.healthy:g}) must lie within the range "
                    f"[{f.lower:g}, {f.upper:g}] (a {kind})."
                )
        return out


def _unit_suffix(unit: str | None, reference: str | None = None) -> str:
    """`` bar gauge`` style suffix for messages; empty for dimensionless units."""
    ref = f" {reference}" if reference else ""
    if unit in (None, "", "1"):
        return ref
    return f" {unit}{ref}"


def _expr_problems(
    label: str, text: str, local: set[str], non_numeric: Mapping[str, str] | None = None
) -> list[str]:
    try:
        expr = compile_expression(text)
    except ExpressionError as exc:
        return [f"{label}: {exc}"]
    kinds = non_numeric or {}
    out = []
    for n in sorted(n for n in expr.names if n not in local):
        if n in kinds:
            out.append(
                f"{label}: condition '{text}' references the {kinds[n]} parameter '{n}'; "
                "conditions can only use numeric and boolean names (the expression language "
                "has no strings or tables)."
            )
        else:
            out.append(
                f"{label}: condition '{text}' references unknown name '{n}'. "
                + format_choices(n, local)
            )
    return out


def _var(item: Mapping[str, Any], kind: str) -> VariableSpec:
    typ = item.get("type", "number")
    columns = None
    if typ == "table":
        columns = tuple(
            ColumnSpec(
                c["name"],
                c["unit"],
                c.get("description", ""),
                c.get("pressure_reference"),
                c.get("minimum"),
                c.get("maximum"),
            )
            for c in item.get("columns", [])
        )
    enum = tuple(item["enum"]) if "enum" in item else None
    return VariableSpec(
        name=item["name"],
        kind=kind,
        description=item.get("description", ""),
        unit=item.get("unit"),
        type=typ,
        default=item.get("default"),
        minimum=item.get("minimum"),
        maximum=item.get("maximum"),
        enum=enum,
        columns=columns,
        min_rows=item.get("min_rows"),
        pressure_reference=item.get("pressure_reference"),
        steady=item.get("steady"),
        quantity=item.get("quantity"),
        measurable=bool(item.get("measurable", True)),
    )


def validate_manifest_data(data: Mapping[str, Any]) -> list[str]:
    """Return every schema and semantic problem of a manifest dict (empty when valid)."""
    try:
        Manifest.from_dict(data)
    except ManifestError as exc:
        return exc.problems or [str(exc)]
    return []


def load_manifest(path: str | Path) -> Manifest:
    """Load and validate a manifest YAML file.

    Raises:
        ManifestError: When the file cannot be parsed or is invalid.
    """
    p = Path(path)
    try:
        data = load_yaml(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"Cannot read manifest {p}: {exc}", path=p) from exc
    if not isinstance(data, dict):
        raise ManifestError(f"Manifest {p} must be a YAML mapping.", path=p)
    return Manifest.from_dict(data, p)
