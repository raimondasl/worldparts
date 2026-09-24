"""Units: parsing, SI conversion, display conversion and gauge-pressure handling.

Inside worldparts everything is SI (pressure in Pa **absolute**, temperature in K, flow in
kg/s or m3/s). At the API boundary values are in the unit a manifest declares. Plain numbers
are interpreted in that unit; strings with units (``"3 bar"``, ``"12 L/min"``, ``"55 degC"``)
are parsed with pint and converted. Pressures are gauge (relative to :data:`P_ATM`) unless a
variable declares ``pressure_reference: absolute`` or ``difference``. Temperatures are absolute
unless a variable declares ``quantity: temperature_difference`` (for example a temperature
rise in K): a difference converts by scale only, so a 35 K rise is a 35 degC rise and a
63 degF rise, never -238 degC.

Conversions between a declared unit and SI are affine (``si = value * scale + offset``); the
factors are computed once per unit with pint and cached, so runtime conversion is cheap.

SI conventions worth knowing: ``%`` and ``1`` are fractions in SI (50 % is 0.5), ``rpm`` is
rad/s in SI, ``degC`` is K, ``L/min`` and ``m3/h`` are m3/s, ``mW/cm2`` is W/m2 and
``mJ/cm2`` is J/m2.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pint

from worldparts.errors import InvalidValueError, UnitError

__all__ = [
    "MANIFEST_UNITS",
    "PRESSURE_REFERENCES",
    "P_ATM",
    "QUANTITIES",
    "TEMPERATURE_REFERENCES",
    "UnitConverter",
    "convert",
    "converter",
    "is_pressure_unit",
    "is_temperature_unit",
    "normalize_unit",
    "parse_duration",
    "parse_quantity",
    "parse_value",
    "same_dimension",
    "split_pressure_reference",
    "ureg",
]

#: Standard atmospheric pressure in Pa; the reference for gauge pressures.
P_ATM: float = 101325.0

#: The unit vocabulary manifests may use (design section 3.1).
MANIFEST_UNITS: tuple[str, ...] = (
    "1",
    "%",
    "Pa",
    "kPa",
    "bar",
    "m",
    "mm",
    "m/s",
    "m2",
    "m3",
    "L",
    "kg/s",
    "L/s",
    "L/min",
    "m3/h",
    "degC",
    "K",
    "W",
    "kW",
    "s",
    "min",
    "h",
    "rpm",
    "mW/cm2",
    "mJ/cm2",
    "kg/m3",
    "kWh/m3",
)

#: Allowed values of ``pressure_reference``.
PRESSURE_REFERENCES: tuple[str, ...] = ("gauge", "absolute", "difference")

#: References a temperature may have: an absolute temperature or a temperature difference.
TEMPERATURE_REFERENCES: tuple[str, ...] = ("absolute", "difference")

#: Allowed values of a manifest variable's ``quantity`` field, with the reference each implies.
QUANTITIES: dict[str, str] = {"temperature_difference": "difference"}

#: The shared pint registry. Offset units (degC) auto-convert to kelvin in arithmetic.
ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)

_POWER_RE = re.compile(r"(?<=[A-Za-z])([23])(?![0-9A-Za-z])")
_NUMBER_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$")
_REF_WORD_RE = re.compile(
    r"^(.*?)\s*(?:\(\s*(absolute|abs|a|gauge|g)\s*\)|\s(absolute|abs|gauge))\s*$", re.IGNORECASE
)
_REF_UNIT_RE = re.compile(r"^(.*?\b)(bar|psi)(a|g)\s*$", re.IGNORECASE)
_ATM_UNITS = ("atm", "atmosphere", "atmospheres")
#: Example quantities for messages, by pint dimensionality string.
_EXAMPLES = {
    "[mass] / [length] / [time] ** 2": "a pressure such as '3 bar' or '250 kPa'",
    "[length] ** 3 / [time]": "a volume flow such as '12 L/min' or '2 m3/h'",
    "[mass] / [time]": "a mass flow such as '0.2 kg/s'",
    "[temperature]": "a temperature such as '55 degC' or '300 K'",
    "[length]": "a length such as '16 mm' or '5 m'",
    "[time]": "a time such as '30 s' or '5 min'",
    "[length] ** 2 * [mass] / [time] ** 3": "a power such as '18 kW'",
    "[length] ** 3": "a volume such as '15 L'",
    "[length] / [time]": "a velocity such as '1.5 m/s'",
    "dimensionless": "a plain number such as 0.5 (or '50 %')",
}


def normalize_unit(unit: str) -> str:
    """Normalise a manifest unit string for pint.

    ``m3`` becomes ``m**3``, ``cm2`` becomes ``cm**2``, ``%`` becomes ``percent`` and ``1``
    (or an empty string) becomes ``dimensionless``.

    Args:
        unit: A unit string such as ``"m3/h"`` or ``"mW/cm2"``.

    Returns:
        A string pint can parse.
    """
    text = str(unit).strip()
    if text in ("", "1", "-"):
        return "dimensionless"
    text = text.replace("%", "percent")
    return _POWER_RE.sub(r"**\1", text)


@lru_cache(maxsize=512)
def _pint_unit(unit: str) -> pint.Unit:
    try:
        return ureg.Unit(normalize_unit(unit))
    except Exception as exc:  # pint raises several exception types
        raise UnitError(
            f"Cannot parse unit '{unit}'. Use a unit such as " + ", ".join(MANIFEST_UNITS) + "."
        ) from exc


def _pressure_dimensionality() -> Any:
    return ureg.Unit("Pa").dimensionality


def _energy_dimensionality() -> Any:
    return ureg.Unit("J").dimensionality


def same_dimension(unit_a: str, unit_b: str) -> bool:
    """Return True when two unit strings have the same physical dimension."""
    return _pint_unit(unit_a).dimensionality == _pint_unit(unit_b).dimensionality


@lru_cache(maxsize=512)
def is_pressure_unit(unit: str) -> bool:
    """Return True when ``unit`` is a pressure unit (Pa, kPa, bar, ...).

    An energy per volume (``kWh/m3``, ``J/m3``) has the dimension of a pressure but is not
    one: it has no gauge reference, so it is not treated as a pressure.
    """
    pu = _pint_unit(unit)
    if pu.dimensionality != _pressure_dimensionality():
        return False
    energy = _energy_dimensionality()
    return not any(ureg.Unit(name).dimensionality == energy for name in pu._units)


def is_temperature_unit(unit: str) -> bool:
    """Return True when ``unit`` is a temperature unit (K, degC, degF, delta_degC, ...)."""
    return str(_pint_unit(unit).dimensionality) == "[temperature]"


def _is_delta_unit(unit: str) -> bool:
    """True for pint's temperature-difference units such as ``delta_degC``."""
    return any(str(name).startswith("delta_") for name in _pint_unit(unit)._units)


def _clean(x: float) -> float:
    """Round away pint's floating-point noise (e.g. 0.0010000000000000002)."""
    return float(f"{x:.15g}")


@dataclass(frozen=True)
class UnitConverter:
    """Affine converter between a declared unit and SI.

    Attributes:
        unit: The declared unit string.
        scale: SI value of one declared unit (for offset units, of one unit difference).
        offset: SI value of zero in the declared unit, including :data:`P_ATM` for gauge
            pressures.
        si_unit: The SI unit as a string (for information).
        reference: ``gauge``, ``absolute`` or ``difference`` for pressures, else None.
    """

    unit: str
    scale: float
    offset: float
    si_unit: str
    reference: str | None = None

    def to_si(self, value: float) -> float:
        """Convert a value in the declared unit to SI."""
        return value * self.scale + self.offset

    def from_si(self, value: float | None) -> float | None:
        """Convert an SI value to the declared unit (None stays None)."""
        if value is None:
            return None
        return (value - self.offset) / self.scale


@lru_cache(maxsize=512)
def converter(unit: str, pressure_reference: str | None = None) -> UnitConverter:
    """Return the cached converter for a declared unit.

    Args:
        unit: Declared unit string, e.g. ``"bar"``, ``"L/min"``, ``"degC"``.
        pressure_reference: The variable's reference. For pressure units: ``gauge`` (the
            default), ``absolute`` or ``difference``. For temperature units: ``absolute``
            (the default) or ``difference`` (a temperature difference, converted by scale
            only: 1 K = 1 degC = 1.8 degF, no offset). Ignored for other dimensions.

    Returns:
        A :class:`UnitConverter`.
    """
    pu = _pint_unit(unit)
    q0 = ureg.Quantity(0.0, pu).to_base_units()
    q1 = ureg.Quantity(1.0, pu).to_base_units()
    offset = _clean(float(q0.magnitude))
    scale = _clean(float(q1.magnitude) - float(q0.magnitude))
    if offset != 0.0 and is_temperature_unit(unit):
        # Exact scale of an offset unit from its delta unit (1 delta_degF = 5/9 K).
        delta = ureg.Quantity(1.0, f"delta_{pu}").to_base_units()
        scale = float(delta.magnitude)
    reference = None
    if is_pressure_unit(unit):
        reference = pressure_reference or "gauge"
        if reference not in PRESSURE_REFERENCES:
            raise UnitError(
                f"Unknown pressure_reference '{pressure_reference}'. "
                f"Valid: {', '.join(PRESSURE_REFERENCES)}."
            )
        if reference == "gauge":
            offset += P_ATM
    elif is_temperature_unit(unit):
        if pressure_reference not in (None, *TEMPERATURE_REFERENCES):
            raise UnitError(
                f"A temperature is 'absolute' or a 'difference', not '{pressure_reference}'."
            )
        if pressure_reference == "difference":
            reference, offset = "difference", 0.0
        elif _is_delta_unit(unit):
            raise UnitError(
                f"'{unit}' is a temperature-difference unit, but the value is an absolute "
                "temperature; use K, degC or degF."
            )
    si_unit = f"{q1.units:~}" if str(q1.units) != "dimensionless" else "1"
    return UnitConverter(unit, scale, offset, si_unit, reference)


def parse_quantity(text: str) -> tuple[float, str | None]:
    """Split a string such as ``"3.5 bar"`` into a number and a unit string.

    Args:
        text: A number optionally followed by a unit.

    Returns:
        ``(number, unit)`` where ``unit`` is None when the string holds a bare number.

    Raises:
        InvalidValueError: When the string does not start with a number.
    """
    m = _NUMBER_RE.match(str(text))
    if not m:
        raise InvalidValueError(
            f"Cannot parse '{text}' as a quantity. Write a number optionally followed by a "
            "unit, for example '3 bar', '12 L/min' or '55 degC'."
        )
    number = float(m.group(1))
    unit = m.group(2) or None
    return number, unit


def split_pressure_reference(text: str) -> tuple[str, str | None]:
    """Split an explicit pressure reference off a quantity or unit string.

    Recognised forms: ``"2 bar absolute"``, ``"2 bar abs"``, ``"2 bar (a)"``,
    ``"2 bar gauge"``, ``"2 bar (g)"``, ``"2 bara"``, ``"2 barg"`` (and ``psia``/``psig``).

    Returns:
        ``(text without the marker, "absolute" | "gauge" | None)``.
    """
    t = str(text).strip()
    m = _REF_UNIT_RE.match(t)
    if m:
        ref = "absolute" if m.group(3).lower() == "a" else "gauge"
        return f"{m.group(1)}{m.group(2)}".strip(), ref
    m = _REF_WORD_RE.match(t)
    if m and m.group(1).strip():
        word = (m.group(2) or m.group(3)).lower()
        return m.group(1).strip(), "absolute" if word.startswith("a") else "gauge"
    return t, None


def _unit_text(unit: str) -> str:
    return "dimensionless" if unit in ("1", "") else unit


def _example(unit: str) -> str:
    dim = str(_pint_unit(unit).dimensionality)
    return _EXAMPLES.get(dim, f"a value such as '1.5 {unit}'")


def parse_value(
    value: Any, unit: str, what: str = "value", pressure_reference: str | None = None
) -> float:
    """Interpret ``value`` in the declared ``unit`` and return a number in that unit.

    Plain numbers are taken to be in ``unit``. Strings with units and pint quantities are
    converted to ``unit``; offsets such as degC are handled.

    For a temperature difference (``pressure_reference="difference"`` with a temperature
    unit) a string converts by scale only: ``"5 degC"`` is 5 K and ``"9 degF"`` is 5 K.

    For pressures, ``pressure_reference`` is the declared reference of the variable
    (``gauge``, ``absolute`` or ``difference``). A string may then name its own reference
    (``"2 bar absolute"``, ``"1.5 bara"``, ``"3 bar (g)"``) and is converted to the declared
    one. ``atm`` without a reference is rejected for gauge variables, because ``"1 atm"``
    almost always means atmospheric pressure (0 bar gauge), not 1 atm above it.

    Args:
        value: A number, a numeric string, a string with a unit, or a pint Quantity.
        unit: The declared unit.
        what: The variable path, used in error messages.
        pressure_reference: Declared reference of a pressure variable, if any.

    Returns:
        The number expressed in ``unit`` (not SI).

    Raises:
        InvalidValueError: For non-numeric values or dimension mismatches.
    """
    if isinstance(value, str) and pressure_reference is not None:
        text, given_ref = split_pressure_reference(value)
        if given_ref is not None and given_ref != pressure_reference:
            if pressure_reference == "difference":
                kind = "temperature" if is_temperature_unit(unit) else "pressure"
                raise InvalidValueError(
                    f"{what}: '{value}' names a {given_ref} value, but {what} is a {kind} "
                    f"difference; write it without a reference, e.g. '0.5 {unit}'."
                )
            number = parse_value(text, unit, what)
            si = converter(unit, given_ref).to_si(number)
            out = converter(unit, pressure_reference).from_si(si)
            assert out is not None
            return out
        if given_ref is None and pressure_reference == "gauge":
            _, given_unit = parse_quantity(text)
            if given_unit is not None and given_unit.strip().lower() in _ATM_UNITS:
                raise InvalidValueError(
                    f"{what}: '{value}' is ambiguous because {what} is a gauge pressure "
                    f"(relative to 1 atm). Write '0 {unit}' for atmospheric pressure, "
                    f"'{text} absolute' for an absolute pressure, or '{text} gauge' for "
                    "that much above atmospheric."
                )
        value = text
    if isinstance(value, bool):
        raise InvalidValueError(
            f"{what}: expected a number in {_unit_text(unit)}, got boolean {value}."
        )
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            raise InvalidValueError(f"{what}: NaN is not a valid value.")
        return number
    if isinstance(value, pint.Quantity):
        q = value
        given_unit = f"{value.units}"
    elif isinstance(value, str):
        try:
            magnitude, given = parse_quantity(value)
        except InvalidValueError as exc:
            raise InvalidValueError(
                f"{what}: {exc} The declared unit is {_unit_text(unit)}."
            ) from None
        if given is None:
            return magnitude
        given_unit = given
        try:
            q = ureg.Quantity(magnitude, normalize_unit(given))
        except Exception as exc:
            raise UnitError(
                f"{what}: cannot parse unit '{given}' in '{value}'. The declared unit is {unit}."
            ) from exc
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidValueError(
                f"{what}: expected {_example(unit)} (declared unit {_unit_text(unit)}), "
                f"got {value!r}."
            ) from exc
        if math.isnan(number):
            raise InvalidValueError(f"{what}: NaN is not a valid value.")
        return number
    target = _pint_unit(unit)
    if (
        pressure_reference == "difference"
        and q.dimensionality == target.dimensionality
        and is_temperature_unit(unit)
    ):
        # A temperature difference: scale only (5 degC of rise is 5 K, not 278.15 K).
        diff = converter(f"{q.units}", "difference").to_si(float(q.magnitude))
        out = converter(unit, "difference").from_si(diff)
        assert out is not None
        return out
    if q.dimensionality != target.dimensionality:
        hint = ""
        if str(target.dimensionality) == "[temperature]" and given_unit.strip() in ("C", "F"):
            name = {"C": "coulomb", "F": "farad"}[given_unit.strip()]
            hint = f" '{given_unit.strip()}' is read as {name}; write degC (or degF)."
        raise UnitError(
            f"{what}: '{value}' has unit {given_unit}, which is not compatible with the "
            f"declared unit {_unit_text(unit)}: expected {_example(unit)}.{hint}"
        )
    try:
        return float(q.to(target).magnitude)
    except pint.errors.PintError as exc:
        # delta_degC and the like into an absolute temperature (pint cannot add the offset).
        raise UnitError(
            f"{what}: '{value}' is a temperature difference, but the variable is an absolute "
            f"temperature; use K, degC or degF (declared unit {_unit_text(unit)})."
            if _is_delta_unit(given_unit)
            else f"{what}: cannot convert '{value}' to {_unit_text(unit)}: {exc}"
        ) from None


def convert(
    value: float | None,
    from_unit: str,
    to_unit: str,
    pressure_reference: str | None = None,
) -> float | None:
    """Convert a number between two units of the same dimension.

    Both units share ``pressure_reference`` (for example gauge bar to gauge kPa), unless
    ``to_unit`` names its own reference (``"bar absolute"``, ``"bara"``, ``"kPa (g)"``) and the
    value is a gauge or absolute pressure. A temperature difference (reference
    ``difference``) converts by scale only: 35 K is 35 degC and 63 degF.

    Args:
        value: The number in ``from_unit`` (None passes through).
        from_unit: Source unit.
        to_unit: Target unit.
        pressure_reference: Pressure reference of both values, when they are pressures.

    Returns:
        The number in ``to_unit``.

    Raises:
        UnitError: When the dimensions differ.
    """
    if value is None:
        return None
    to_unit, to_ref = split_pressure_reference(to_unit)
    if to_ref is not None and to_ref != pressure_reference:
        if pressure_reference not in ("gauge", "absolute") or not is_pressure_unit(to_unit):
            raise UnitError(
                f"Cannot convert to '{to_unit} {to_ref}': the value is not a gauge or absolute "
                f"pressure (reference: {pressure_reference})."
            )
        if not same_dimension(from_unit, to_unit):
            raise UnitError(f"Cannot convert from {from_unit} to {to_unit}.")
        si = converter(from_unit, pressure_reference).to_si(value)
        out = converter(to_unit, to_ref).from_si(si)
        assert out is not None
        return out
    if not same_dimension(from_unit, to_unit):
        raise UnitError(
            f"Cannot convert from {from_unit} to {to_unit}: the dimensions differ "
            f"({_pint_unit(from_unit).dimensionality} vs {_pint_unit(to_unit).dimensionality})."
        )
    si = converter(from_unit, pressure_reference).to_si(value)
    out = converter(to_unit, pressure_reference).from_si(si)
    assert out is not None
    return out


def parse_duration(value: Any, what: str = "duration") -> float:
    """Parse a time span such as ``"10 min"`` or ``60`` (seconds) into seconds.

    Raises:
        InvalidValueError: For negative or non-time values.
    """
    seconds = parse_value(value, "s", what)
    if seconds < 0:
        raise InvalidValueError(f"{what}: must not be negative, got {value!r}.")
    return seconds
