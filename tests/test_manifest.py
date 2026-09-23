"""Tests for manifest loading, schema validation and semantic checks."""

from __future__ import annotations

import copy
import functools
import json
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest

from worldparts.catalog import package_manifest_paths
from worldparts.errors import InvalidValueError, ManifestError, OutOfRangeError
from worldparts.manifest import (
    Manifest,
    Table,
    load_manifest,
    load_schema,
    load_yaml,
    validate_manifest_data,
)

FIXTURE = Path(__file__).parent / "fixtures" / "catalog" / "heated_tank.yaml"


@functools.cache
def _raw(path: str) -> str:
    if path == "valve":
        trav = next(p for p in package_manifest_paths() if p.name == "valve.yaml")
        return trav.read_text(encoding="utf-8")
    return FIXTURE.read_text(encoding="utf-8")


def valve_data() -> dict[str, Any]:
    return load_yaml(_raw("valve"))


def tank_data() -> dict[str, Any]:
    return load_yaml(_raw("tank"))


def test_schemas_are_valid_draft_2020_12() -> None:
    for name in ("component-manifest", "system"):
        schema = load_schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        jsonschema.Draft202012Validator.check_schema(schema)


def test_load_package_manifest() -> None:
    m = Manifest.from_dict(valve_data())
    assert m.id == "worldparts.hydraulic.valve"
    assert m.alias == "valve"
    assert list(m.ports) == ["port_a", "port_b"]
    assert m.parameters["kv"].unit == "m3/h"
    assert m.parameters["characteristic"].enum == ("linear", "equal_percentage", "quick_opening")
    assert m.states["position"].steady == "settle"
    assert m.observables["pressure_drop"].reference == "difference"
    assert m.warnings["high_pressure_drop"].is_envelope
    assert m.implementation == "worldparts.components.valves:TwoWayValve"
    assert "opening" in m.local_names() and "port_a.p" in m.local_names()
    assert m.describe()["alias"] == "valve"


def test_load_manifest_from_file() -> None:
    m = load_manifest(FIXTURE)
    assert m.path == FIXTURE
    assert m.parameters["heater_curve"].type == "table"
    assert m.warnings["overheat"].is_envelope is False


def test_load_manifest_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="mapping"):
        load_manifest(bad)
    with pytest.raises(ManifestError, match="Cannot read"):
        load_manifest(tmp_path / "missing.yaml")


def _mutate(path: list[Any], value: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    data = copy.deepcopy(base if base is not None else valve_data())
    target: Any = data
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        (["manifest_version"], "0.2", "manifest_version"),
        (["id"], "Worldparts.Valve", "id"),
        (["id"], "valve", "id"),
        (["version"], "1.0", "version"),
        (["summary"], _DELETE, "summary"),
        (["provenance"], _DELETE, "provenance"),
        (["extra_field"], 1, "extra_field"),
        (["fidelity", "level"], "exact", "fidelity/level"),
        (["ports", 0, "medium"], "oil", "ports/0/medium"),
        (["parameters", 0, "unit"], "furlong", "parameters/0"),
        (["parameters", 0, "unit"], _DELETE, "parameters/0"),
        (["parameters", 0, "colour"], "red", "parameters/0"),
        (["parameters", 1, "enum"], _DELETE, "parameters/1"),
        (["parameters", 1, "unit"], "m", "parameters/1"),
        (["inputs", 0, "default"], "open", "inputs/0/default"),
        (["states", 0, "steady"], "sometimes", "states/0/steady"),
        (["envelope", 0, "severity"], "fatal", "envelope/0/severity"),
        (["scenarios"], [], "scenarios"),
        (["contracts", 0, "check", "direction"], "sideways", "contracts/0/check"),
        (["provenance", "data", "acquisition"], "guessed", "provenance/data/acquisition"),
        (["implementations", "reference", "python"], "not a path", "implementations"),
    ],
)
def test_schema_rejects(path: list[Any], value: Any, fragment: str) -> None:
    problems = validate_manifest_data(_mutate(path, value))
    assert problems, "expected a schema error"
    assert any(fragment in p for p in problems), problems


def test_table_parameter_schema_rules() -> None:
    data = tank_data()
    assert validate_manifest_data(data) == []
    idx = next(i for i, p in enumerate(data["parameters"]) if p["type"] == "table")
    problems = validate_manifest_data(_mutate(["parameters", idx, "columns"], _DELETE, data))
    assert any(f"parameters/{idx}" in p for p in problems)
    # min_rows is optional (design 4); it defaults to 1
    assert validate_manifest_data(_mutate(["parameters", idx, "min_rows"], _DELETE, data)) == []
    problems = validate_manifest_data(_mutate(["parameters", idx, "unit"], "m", data))
    assert problems


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        (["parameters", 0, "default"], 1e6, "outside the allowed range"),
        (["parameters", 0, "minimum"], 1e7, "exceeds maximum"),
        (["parameters", 1, "default"], "butterfly", "not a valid choice"),
        (["observables", 0, "name"], "kv", "already used"),
        (["observables", 0, "name"], "port_a", "already used"),
        (["modes", 0, "condition"], "positon <= 0.001", "unknown name 'positon'"),
        (["modes", 0, "condition"], "position <=", "Invalid expression"),
        (["envelope", 0, "condition"], "__import__('os')", "unsupported"),
        (["contracts", 0, "scenario"], "nope", "unknown scenario"),
        (["contracts", 1, "check", "left"], "dut.flow +", "Invalid expression"),
        (["observables", 1, "pressure_reference"], _DELETE, "pressure difference"),
    ],
)
def test_semantic_problems(path: list[Any], value: Any, fragment: str) -> None:
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict(_mutate(path, value))
    assert any(fragment in p for p in exc.value.problems), exc.value.problems


def test_duplicate_warning_codes_and_missing_equal_contract() -> None:
    data = valve_data()
    data["warnings"] = [{"code": "high_pressure_drop", "severity": "info", "description": "dup"}]
    for c in data["contracts"]:
        if c["check"]["type"] == "equal":
            c["check"] = {"type": "bounds", "variable": "dut.volume_flow", "min": 0}
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict(data)
    text = " ".join(exc.value.problems)
    assert "declared twice" in text
    assert "equal" in text


def test_table_values() -> None:
    m = Manifest.from_dict(tank_data())
    spec = m.parameters["heater_curve"]
    rows = spec.parse([[0, "1500 W"], ["1 m", 4], [2, 6]], "dut.heater_curve")
    assert rows == [[0.0, 1.5], [1.0, 4.0], [2.0, 6.0]]
    table = spec.to_si(rows)
    assert isinstance(table, Table)
    assert table.columns == ("level", "power")
    np.testing.assert_allclose(table["power"], [1500.0, 4000.0, 6000.0])
    np.testing.assert_allclose(table["level"], [0.0, 1.0, 2.0])
    assert table.rows == 3 and len(table) == 3
    assert spec.from_si(table) == rows
    assert spec.parse(table, "x") == rows
    assert spec.parse(np.array(rows), "x") == rows
    with pytest.raises(KeyError, match="level, power"):
        table["flow"]
    with pytest.raises(ValueError):
        table.array[0, 0] = 1.0  # read-only


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ([[0, 2]], "at least 2 rows"),
        ([[0, 2, 3], [1, 2, 3]], "each row needs 2"),
        ("0 2 1 3", "list of rows"),
        ([[0, "2 bar"], [1, 3]], "not compatible"),
        ([[0, float("inf")], [1, 3]], "finite"),
    ],
)
def test_table_validation(value: Any, fragment: str) -> None:
    spec = Manifest.from_dict(tank_data()).parameters["heater_curve"]
    with pytest.raises(InvalidValueError, match=fragment):
        spec.parse(value, "dut.heater_curve")


def test_scalar_parsing_and_limits() -> None:
    m = Manifest.from_dict(valve_data())
    kv = m.parameters["kv"]
    assert kv.parse("600 L/h", "v.kv") == pytest.approx(0.6)
    with pytest.raises(OutOfRangeError) as exc:
        kv.parse(2e5, "v.kv")
    assert "v.kv" in str(exc.value) and "[0.0001, 100000] m3/h" in str(exc.value)
    ch = m.parameters["characteristic"]
    assert ch.parse("equal_percentage", "v.characteristic") == "equal_percentage"
    with pytest.raises(InvalidValueError, match="equal_percentage"):
        ch.parse("equal_percent", "v.characteristic")
    assert m.inputs["opening"].parse("50 %", "v.opening") == pytest.approx(0.5)


def test_boolean_and_integer_parameters() -> None:
    data = valve_data()
    data["parameters"].append(
        {"name": "flag", "description": "A flag.", "type": "boolean", "default": False}
    )
    data["parameters"].append(
        {"name": "turns", "description": "Turns.", "type": "integer", "unit": "1", "default": 3,
         "minimum": 1, "maximum": 10}
    )  # fmt: skip
    m = Manifest.from_dict(data)
    flag = m.parameters["flag"]
    assert flag.parse(True, "f") is True
    assert flag.parse("false", "f") is False
    assert flag.parse(1, "f") is True
    with pytest.raises(InvalidValueError):
        flag.parse("maybe", "f")
    turns = m.parameters["turns"]
    assert turns.parse(4, "t") == 4.0
    with pytest.raises(InvalidValueError, match="integer"):
        turns.parse(2.5, "t")
    with pytest.raises(OutOfRangeError):
        turns.parse(11, "t")


def test_scenario_lookup() -> None:
    m = Manifest.from_dict(valve_data())
    assert m.scenario("kv-at-1-bar")["id"] == "kv-at-1-bar"
    with pytest.raises(Exception, match="kv-at-1-bar"):
        m.scenario("kv-at-2-bar")


# -- review regressions ----------------------------------------------------------------------


def test_table_column_limits() -> None:
    data = tank_data()
    idx = next(i for i, p in enumerate(data["parameters"]) if p["type"] == "table")
    data["parameters"][idx]["columns"][0]["minimum"] = 0
    m = Manifest.from_dict(data)
    spec = m.parameters["heater_curve"]
    assert spec.parse([[0, 1], [1, 2]], "x") == [[0.0, 1.0], [1.0, 2.0]]
    with pytest.raises(OutOfRangeError, match=r"x\[0\]\.level"):
        spec.parse([[-1, 1], [1, 2]], "x")


def test_pressure_reference_on_table_column_needs_a_pressure_unit() -> None:
    data = tank_data()
    idx = next(i for i, p in enumerate(data["parameters"]) if p["type"] == "table")
    data["parameters"][idx]["columns"][1]["pressure_reference"] = "gauge"
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict(data)
    assert any("only for pressures" in p for p in exc.value.problems)


def test_condition_on_string_parameter_explains_the_problem() -> None:
    data = valve_data()
    data["modes"][0]["condition"] = "characteristic"
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict(data)
    assert any("string parameter 'characteristic'" in p for p in exc.value.problems)


def test_dimensionless_range_message_has_no_unit() -> None:
    m = Manifest.from_dict(valve_data())
    with pytest.raises(OutOfRangeError) as exc:
        m.inputs["opening"].parse(1.5, "v.opening")
    assert str(exc.value) == "v.opening = 1.5 is outside the allowed range [0, 1]."


def _with_rise(**extra: Any) -> dict[str, Any]:
    data = valve_data()
    data["observables"].append(
        {"name": "temperature_rise", "unit": "K", "description": "A rise.", **extra}
    )
    return data


def test_temperature_difference_must_be_declared() -> None:
    """A temperature named like a difference must say so, or it converts as absolute."""
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict(_with_rise())
    assert any("quantity: temperature_difference" in p for p in exc.value.problems)
    m = Manifest.from_dict(_with_rise(quantity="temperature_difference"))
    spec = m.observables["temperature_rise"]
    assert spec.quantity == "temperature_difference" and spec.reference == "difference"
    assert spec.converter.offset == 0.0
    assert m.observables["volume_flow"].reference is None


def test_quantity_schema_rules() -> None:
    problems = validate_manifest_data(_with_rise(quantity="temperature_difference", unit="bar"))
    assert any("observables/" in p for p in problems), problems
    problems = validate_manifest_data(_with_rise(quantity="speed"))
    assert any("observables/" in p for p in problems), problems
    data = valve_data()
    data["parameters"].append(
        {
            "name": "approach",
            "description": "Design temperature approach.",
            "type": "number",
            "unit": "degC",
            "quantity": "temperature_difference",
            "default": 5,
            "minimum": 0,
            "maximum": 50,
        }
    )
    m = Manifest.from_dict(data)
    spec = m.parameters["approach"]
    assert spec.parse("9 degF", "v.approach") == pytest.approx(5.0)
    assert spec.to_si(5.0) == pytest.approx(5.0)


def test_schemas_have_no_duplicate_keys() -> None:
    """A duplicate key silently replaces the first definition (last one wins in JSON)."""

    def no_dups(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        dups = {k for k in keys if keys.count(k) > 1}
        assert not dups, f"duplicate keys {sorted(dups)}"
        return dict(pairs)

    for name in ("component-manifest", "system"):
        text = (
            resources.files("worldparts").joinpath(f"schemas/{name}.schema.json").read_text("utf-8")
        )
        json.loads(text, object_pairs_hook=no_dups)
