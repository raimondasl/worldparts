"""Tests for units.py and media.py."""

from __future__ import annotations

import math

import pytest

from worldparts.errors import InvalidValueError, UnitError
from worldparts.media import CP, MU, RHO, WATER, get_medium, vapour_pressure
from worldparts.units import (
    MANIFEST_UNITS,
    P_ATM,
    convert,
    converter,
    is_pressure_unit,
    normalize_unit,
    parse_duration,
    parse_quantity,
    parse_value,
    ureg,
)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("m3", "m**3"),
        ("m3/h", "m**3/h"),
        ("m2", "m**2"),
        ("cm2", "cm**2"),
        ("mW/cm2", "mW/cm**2"),
        ("mJ/cm2", "mJ/cm**2"),
        ("kg/m3", "kg/m**3"),
        ("%", "percent"),
        ("1", "dimensionless"),
        ("L/min", "L/min"),
    ],
)
def test_normalize_unit(unit: str, expected: str) -> None:
    assert normalize_unit(unit) == expected


@pytest.mark.parametrize("unit", MANIFEST_UNITS)
def test_every_manifest_unit_parses_and_round_trips(unit: str) -> None:
    conv = converter(unit)
    for x in (0.0, 1.0, 2.5, -3.0):
        assert conv.from_si(conv.to_si(x)) == pytest.approx(x, abs=1e-12)
    # pint agrees with the cached affine converter (excluding gauge offset for pressures)
    q = ureg.Quantity(2.5, normalize_unit(unit)).to_base_units().magnitude
    offset = P_ATM if is_pressure_unit(unit) else 0.0
    assert conv.to_si(2.5) == pytest.approx(q + offset, rel=1e-12)


@pytest.mark.parametrize(
    ("unit", "value", "si"),
    [
        ("degC", 55.0, 328.15),
        ("K", 300.0, 300.0),
        ("m3/h", 3.6, 0.001),
        ("L/min", 60.0, 0.001),
        ("L/s", 1.0, 0.001),
        ("L", 15.0, 0.015),
        ("%", 50.0, 0.5),
        ("1", 0.25, 0.25),
        ("rpm", 60.0, 2 * math.pi),
        ("mW/cm2", 20.0, 200.0),
        ("mJ/cm2", 40.0, 400.0),
        ("kW", 18.0, 18000.0),
        ("mm", 16.0, 0.016),
        ("min", 2.0, 120.0),
        ("h", 1.0, 3600.0),
    ],
)
def test_si_conversion(unit: str, value: float, si: float) -> None:
    assert converter(unit).to_si(value) == pytest.approx(si, rel=1e-12)


def test_pressure_references() -> None:
    assert converter("bar").to_si(3.0) == pytest.approx(P_ATM + 3e5)
    assert converter("bar", "gauge").reference == "gauge"
    assert converter("bar", "absolute").to_si(1.0) == pytest.approx(1e5)
    assert converter("bar", "difference").to_si(0.5) == pytest.approx(5e4)
    assert converter("kPa").from_si(P_ATM) == pytest.approx(0.0)
    assert converter("m").reference is None
    with pytest.raises(UnitError):
        converter("bar", "relative")


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (3.5, "bar", 3.5),
        ("3.5 bar", "bar", 3.5),
        ("350 kPa", "bar", 3.5),
        ("1e5 Pa", "bar", 1.0),
        ("55 degC", "degC", 55.0),
        ("328.15 K", "degC", 55.0),
        ("12 L/min", "m3/h", 0.72),
        ("0.5 m3/h", "L/min", 8.333333333333),
        ("50 %", "1", 0.5),
        ("50%", "1", 0.5),
        ("0.5", "1", 0.5),
        ("0.5 1", "%", 50.0),
        ("3000 rpm", "rpm", 3000.0),
        ("20 mW/cm2", "mW/cm2", 20.0),
        ("0.04 J/cm2", "mJ/cm2", 40.0),
        ("10 min", "s", 600.0),
        ("2 m3", "L", 2000.0),
        (7, "kW", 7.0),
    ],
)
def test_parse_value(value: object, unit: str, expected: float) -> None:
    assert parse_value(value, unit, "x") == pytest.approx(expected, rel=1e-9)


def test_parse_value_pint_quantity() -> None:
    assert parse_value(ureg.Quantity(2, "bar"), "kPa") == pytest.approx(200.0)


def test_parse_value_dimension_mismatch_names_expected_unit() -> None:
    with pytest.raises(UnitError) as exc:
        parse_value("3 bar", "L/min", "faucet.flow")
    msg = str(exc.value)
    assert "faucet.flow" in msg and "L/min" in msg


@pytest.mark.parametrize("bad", ["abc", "bar 3", True, None, [1], float("nan")])
def test_parse_value_rejects_non_numbers(bad: object) -> None:
    with pytest.raises(InvalidValueError):
        parse_value(bad, "bar", "src.pressure")


def test_parse_value_unknown_unit() -> None:
    with pytest.raises(UnitError):
        parse_value("3 furlongs_per_fortnight_x", "m/s", "v")


def test_parse_quantity() -> None:
    assert parse_quantity(" 3.5 bar ") == (3.5, "bar")
    assert parse_quantity("-1.5e-3") == (-1.5e-3, None)
    with pytest.raises(InvalidValueError):
        parse_quantity("bar")


def test_convert_display_units() -> None:
    assert convert(3.0, "bar", "kPa", "gauge") == pytest.approx(300.0)
    assert convert(60.0, "L/min", "L/s") == pytest.approx(1.0)
    assert convert(20.0, "degC", "K") == pytest.approx(293.15)
    assert convert(None, "bar", "kPa") is None
    with pytest.raises(UnitError):
        convert(1.0, "bar", "m")


def test_parse_duration() -> None:
    assert parse_duration("10 min") == pytest.approx(600.0)
    assert parse_duration(5) == 5.0
    assert parse_duration("0.5 h") == pytest.approx(1800.0)
    with pytest.raises(InvalidValueError):
        parse_duration("-1 s")
    with pytest.raises(UnitError):
        parse_duration("3 bar")


def test_water_properties() -> None:
    assert (RHO, MU, CP) == (998.2, 1.002e-3, 4182.0)
    assert get_medium("water") is WATER
    with pytest.raises(InvalidValueError, match="water"):
        get_medium("oil")


@pytest.mark.parametrize(
    ("t_c", "p_pa"),
    [
        # IAPWS-IF97 verification value and steam-table values.
        (100.0, 101418.0),
        (20.0, 2339.2),
        (50.0, 12352.0),
        (80.0, 47414.0),
        (1.0, 656.7),
    ],
)
def test_vapour_pressure(t_c: float, p_pa: float) -> None:
    assert vapour_pressure(273.15 + t_c) == pytest.approx(p_pa, rel=2e-3)


def test_vapour_pressure_iapws_verification_point() -> None:
    # IAPWS-IF97 table 35: T = 300 K -> ps = 0.353658941e-2 MPa.
    assert vapour_pressure(300.0) == pytest.approx(3536.58941, rel=1e-8)
    with pytest.raises(InvalidValueError):
        vapour_pressure(200.0)


# -- explicit pressure references (review usability) -----------------------------------------


@pytest.mark.parametrize(
    ("text", "reference", "expected"),
    [
        ("2 bar absolute", "gauge", 0.98675),
        ("2 bar abs", "gauge", 0.98675),
        ("2 bar (a)", "gauge", 0.98675),
        ("1.5 bara", "gauge", 0.48675),
        ("1 bar gauge", "absolute", 2.01325),
        ("1 barg", "absolute", 2.01325),
        ("1 atm absolute", "gauge", 0.0),
        ("1 atm", "absolute", 1.01325),
        ("3 bar gauge", "gauge", 3.0),
    ],
)
def test_parse_value_pressure_reference(text: str, reference: str, expected: float) -> None:
    assert parse_value(text, "bar", "x", reference) == pytest.approx(expected, abs=1e-12)


def test_atm_is_ambiguous_for_gauge_pressures() -> None:
    with pytest.raises(InvalidValueError, match="ambiguous"):
        parse_value("1 atm", "bar", "src.pressure", "gauge")
    with pytest.raises(InvalidValueError, match="difference"):
        parse_value("1 bar absolute", "bar", "v.pressure_drop", "difference")


def test_convert_to_explicit_reference() -> None:
    assert convert(3.0, "bar", "bar absolute", "gauge") == pytest.approx(4.01325)
    assert convert(4.01325, "bar", "kPa (g)", "absolute") == pytest.approx(300.0)
    with pytest.raises(UnitError):
        convert(0.5, "bar", "bar absolute", "difference")


def test_unit_messages_are_agent_friendly() -> None:
    with pytest.raises(UnitError, match="write degC"):
        parse_value("12 C", "degC", "src.temperature")
    with pytest.raises(UnitError, match="a pressure such as"):
        parse_value("3 L/min", "bar", "src.pressure")


# -- temperature differences -------------------------------------------------------------
def test_temperature_difference_converts_by_scale_only() -> None:
    """A 35.1 K rise is a 35.1 degC rise and a 63.18 degF rise, not -238.05 degC."""
    assert convert(35.1, "K", "degC", "difference") == pytest.approx(35.1, abs=1e-12)
    assert convert(35.1, "K", "degF", "difference") == pytest.approx(63.18, abs=1e-12)
    assert convert(20.0, "degC", "K", "difference") == pytest.approx(20.0, abs=1e-12)
    assert convert(35.1, "K", "delta_degC", "difference") == pytest.approx(35.1, abs=1e-12)
    conv = converter("degC", "difference")
    assert conv.reference == "difference" and conv.offset == 0.0 and conv.to_si(5.0) == 5.0
    # absolute temperatures keep their offsets
    assert convert(300.0, "K", "degC") == pytest.approx(26.85, abs=1e-12)
    assert convert(55.0, "degC", "degF") == pytest.approx(131.0, abs=1e-12)
    assert converter("degC").reference is None


def test_temperature_difference_parsing() -> None:
    assert parse_value("5 degC", "K", "x", "difference") == pytest.approx(5.0, abs=1e-12)
    assert parse_value("9 degF", "K", "x", "difference") == pytest.approx(5.0, abs=1e-12)
    assert parse_value("5 K", "degC", "x", "difference") == pytest.approx(5.0, abs=1e-12)
    assert parse_value(7, "K", "x", "difference") == 7.0
    # an absolute temperature string still converts with the offset
    assert parse_value("55 degC", "K", "x") == pytest.approx(328.15, abs=1e-12)
    with pytest.raises(InvalidValueError, match="temperature difference"):
        parse_value("5 K absolute", "K", "x", "difference")


def test_temperature_reference_errors() -> None:
    with pytest.raises(UnitError, match="temperature-difference unit"):
        convert(300.0, "K", "delta_degC")
    with pytest.raises(UnitError, match="absolute"):
        converter("K", "gauge")
    with pytest.raises(UnitError, match="not a gauge or absolute pressure"):
        convert(35.0, "K", "degC absolute", "difference")


def test_energy_per_volume_is_not_a_pressure() -> None:
    """kWh/m3 has the dimension of a pressure but no gauge offset (specific energy)."""
    assert not is_pressure_unit("kWh/m3")
    assert is_pressure_unit("bar") and is_pressure_unit("kPa")
    conv = converter("kWh/m3")
    assert conv.reference is None and conv.offset == 0.0
    assert conv.to_si(1.0) == pytest.approx(3.6e6)
    assert parse_value("3.6 MJ/m3", "kWh/m3") == pytest.approx(1.0)
