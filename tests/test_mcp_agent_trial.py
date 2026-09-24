"""Fixes from the agent trial (a pump lift and a purification skid through a real client).

Every friction item that was in the tool surface is tested through the SDK's in-process
client, with each successful call's ``structuredContent`` validated against the tool's
(compacted) ``outputSchema``. Physics fixes are tested with hand calculations.
"""

from __future__ import annotations

import ast
import copy
import json
import math
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import jsonschema
import pytest
from mcp import Client

import worldparts as wp
from worldparts.mcp_server import INSTRUCTIONS, MAX_DESCRIBE, compact_schema, create_server
from worldparts.media import RHO, G
from worldparts.units import P_ATM


class Session:
    """A connected client plus the tools' output schemas."""

    def __init__(self, client: Client, schemas: dict[str, dict[str, Any]]) -> None:
        self.client = client
        self.schemas = schemas

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = await self.client.call_tool(tool, arguments)
        text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
        assert not result.is_error, text
        data = result.structured_content
        assert isinstance(data, dict)
        jsonschema.validate(data, self.schemas[tool])
        return data

    async def fail(self, tool: str, **arguments: Any) -> str:
        result = await self.client.call_tool(tool, arguments)
        assert result.is_error, result.structured_content
        return result.content[0].text  # type: ignore[union-attr]


def run(body: Callable[[Session], Awaitable[None]]) -> None:
    async def main() -> None:
        async with Client(create_server()) as client:  # type: ignore[arg-type]
            tools = await client.list_tools()
            schemas = {t.name: t.output_schema for t in tools.tools}
            await body(Session(client, schemas))  # type: ignore[arg-type]

    anyio.run(main)


LIFT = {
    "worldparts_system": "0.1",
    "name": "rooftop lift",
    "components": [
        {"name": "tank", "type": "tank", "parameters": {"diameter": 2, "initial_level": 1.5}},
        {"name": "pump", "type": "centrifugal_pump"},
        {"name": "v", "type": "valve", "parameters": {"kv": 150}},
        {
            "name": "riser",
            "type": "pipe",
            "parameters": {
                "length": 60,
                "diameter": 50,
                "roughness": 0.045,
                "height_difference": 25,
            },
        },
        {"name": "roof", "type": "drain"},
    ],
    "connections": [
        ["tank.outlet", "pump.inlet"],
        ["pump.outlet", "v.port_a"],
        ["v.port_b", "riser.port_a"],
        ["riser.port_b", "roof.port"],
    ],
}

SKID = {
    "worldparts_system": "0.1",
    "name": "purification skid",
    "components": [
        {"name": "raw", "type": "tank", "parameters": {"diameter": 5}},
        {
            "name": "suction",
            "type": "pipe",
            "parameters": {"length": 2, "diameter": 50, "roughness": 0.045},
        },
        {"name": "pump", "type": "centrifugal_pump"},
        {"name": "filter", "type": "media_filter"},
        {"name": "uv", "type": "uv_reactor"},
        {"name": "treated", "type": "tank", "parameters": {"diameter": 5}},
    ],
    "connections": [
        ["raw.outlet", "suction.port_a"],
        ["suction.port_b", "pump.inlet"],
        ["pump.outlet", "filter.inlet"],
        ["filter.outlet", "uv.inlet"],
        ["uv.outlet", "treated.inlet"],
    ],
}


# ----------------------------------------------------------------------------------------
# major: tools/list size
# ----------------------------------------------------------------------------------------
def _keys(schema: Any, key: str) -> int:
    """Occurrences of ``key`` as a schema keyword (property names do not count)."""
    if isinstance(schema, list):
        return sum(_keys(s, key) for s in schema)
    if not isinstance(schema, dict):
        return 0
    n = 0
    for k, v in schema.items():
        if k in ("properties", "$defs") and isinstance(v, dict):
            n += sum(_keys(s, key) for s in v.values())
        else:
            n += (k == key and isinstance(v, str)) + _keys(v, key)
    return n


def test_tools_list_is_compact() -> None:
    """No titles, output schemas without per-field prose, dedented descriptions.

    The trial measured about 62 KB (15 tools) pretty-printed. The compact form of all 20
    tools (18 core, 2 WNTR) is about 35.6 KB; the 36 KB budget is kept on purpose, so a
    new tool or a longer description has to be paid for by trimming elsewhere. No output
    schema exceeds 4.5 KB.
    """

    async def body(s: Session) -> None:
        tools = (await s.client.list_tools()).tools
        total = 0
        for t in tools:
            dumped = t.model_dump(by_alias=True, exclude_none=True, mode="json")
            total += len(json.dumps(dumped))
            assert _keys(t.input_schema, "title") == 0, t.name
            assert _keys(t.output_schema, "title") == 0, t.name
            assert _keys(t.output_schema, "description") == 0, t.name
            assert len(json.dumps(t.output_schema)) < 4500, t.name
            assert t.description and not t.description.startswith(" "), t.name
            assert "\n        " not in t.description, t.name  # dedented
            # Input fields keep their documentation.
            for name, prop in t.input_schema.get("properties", {}).items():
                if name not in ("system_id",):
                    assert "description" in prop or "$ref" in prop, (t.name, name)
        assert total < 36_000, total

    run(body)


def test_compact_schema_keeps_structure() -> None:
    """Property names 'title', 'description' and 'default' survive; keywords go."""
    schema = {
        "title": "X",
        "type": "object",
        "description": "model doc",
        "required": ["description"],
        "properties": {
            "title": {"title": "Title", "type": "string"},
            "description": {"title": "Description", "type": "string", "description": "d"},
            "default": {"default": None, "description": "any"},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        },
    }
    out = compact_schema(schema, output=True)
    assert out == {
        "type": "object",
        "required": ["description"],
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "default": {},
            "note": {"type": "string"},
            "value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        },
    }
    inputs = compact_schema(schema)
    assert inputs["properties"]["note"]["anyOf"][1] == {"type": "null"}  # inputs accept null
    assert inputs["properties"]["description"]["description"] == "d"


def test_compact_schema_drops_additional_properties_true() -> None:
    """``additionalProperties: true`` is the JSON Schema default: dropping it keeps the
    validation and saves about 30 bytes per free-form object in tools/list."""
    schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "additionalProperties": {"type": "object", "additionalProperties": True},
            "names": {"type": "object", "additionalProperties": {"type": "string"}},
            "closed": {"type": "object", "additionalProperties": False},
        },
    }
    expected = {
        "type": "object",
        "properties": {
            "additionalProperties": {"type": "object"},
            "names": {"type": "object", "additionalProperties": {"type": "string"}},
            "closed": {"type": "object", "additionalProperties": False},
        },
    }
    for output in (False, True):
        out = compact_schema(schema, output)
        assert out == expected
        for sample, ok in (
            ({"extra": 1, "additionalProperties": {"x": 1}, "names": {"a": "b"}}, True),
            ({"names": {"a": 1}}, False),
            ({"closed": {"a": 1}}, False),
        ):
            for s in (schema, out):
                assert jsonschema.Draft202012Validator(s).is_valid(sample) is ok, (s, sample)


# ----------------------------------------------------------------------------------------
# major: describe_component detail
# ----------------------------------------------------------------------------------------
def test_describe_component_is_brief_by_default() -> None:
    async def body(s: Session) -> None:
        brief = await s.call("describe_component", component="centrifugal_pump")
        full = await s.call("describe_component", component="centrifugal_pump", detail="full")
        assert brief["detail"] == "brief" and full["detail"] == "full"
        # Everything needed to use the part is in both.
        for key in ("description", "ports", "parameters", "inputs", "observables", "modes",
                    "warnings", "assumptions"):  # fmt: skip
            assert brief[key] == full[key], key
        # Scenarios and contracts by id only; bindings by name only; no provenance.
        assert [x["id"] for x in brief["scenarios"]] == [x["id"] for x in full["scenarios"]]
        assert all(set(x) == {"id", "simulated"} for x in brief["scenarios"])
        assert all("rule" not in x and "description" not in x for x in brief["contracts"])
        assert all(x["check"] and x["scenario"] for x in brief["contracts"])
        assert brief["implementations"]["reference"] == "worldparts.components.pump:CentrifugalPump"
        assert brief["implementations"]["wntr"] == "HeadPump"
        assert "provenance" not in brief and full["provenance"]["license"] == "Apache-2.0"
        assert all("system" in x and "expect" in x for x in full["scenarios"])
        small, big = len(json.dumps(brief)), len(json.dumps(full))
        assert small < 15_000 and small < 0.5 * big, (small, big)

    run(body)


# ----------------------------------------------------------------------------------------
# benchmark pilot: the short path (describe every part in one call, build in one call)
# ----------------------------------------------------------------------------------------
PARTS = ["centrifugal_pump", "tank", "valve", "pipe", "drain"]


def test_describe_component_takes_a_list() -> None:
    """A list returns {components: [...]} in the order given; one id keeps the flat shape."""

    async def body(s: Session) -> None:
        batch = await s.call("describe_component", component=PARTS)
        assert list(batch) == ["components"]
        assert [d["alias"] for d in batch["components"]] == PARTS
        for alias, d in zip(PARTS, batch["components"], strict=True):
            assert d == await s.call("describe_component", component=alias)
        assert all(d["detail"] == "brief" and "provenance" not in d for d in batch["components"])
        # Full ids and aliases mix; the order is kept, a repeated entry is described twice.
        mixed = ["worldparts.hydraulic.drain", "valve", "worldparts.hydraulic.valve", "drain"]
        got = await s.call("describe_component", component=mixed)
        assert [d["id"] for d in got["components"]] == [
            "worldparts.hydraulic.drain",
            "worldparts.hydraulic.valve",
            "worldparts.hydraulic.valve",
            "worldparts.hydraulic.drain",
        ]
        # detail applies to every entry.
        full = await s.call("describe_component", component=["pipe", "tank"], detail="full")
        assert [d["detail"] for d in full["components"]] == ["full", "full"]
        assert full["components"][1] == await s.call(
            "describe_component", component="tank", detail="full"
        )
        assert all("provenance" in d for d in full["components"])
        # A one-element list is still a list; a single id is one description as before.
        one = await s.call("describe_component", component=["pipe"])
        assert [d["alias"] for d in one["components"]] == ["pipe"]
        single = await s.call("describe_component", component="pipe")
        assert "components" not in single and single["alias"] == "pipe"
        assert single == one["components"][0]
        # Up to MAX_DESCRIBE entries.
        many = await s.call("describe_component", component=["pipe"] * MAX_DESCRIBE)
        assert len(many["components"]) == MAX_DESCRIBE

    run(body)


def test_describe_component_list_errors_name_every_unknown_entry() -> None:
    async def body(s: Session) -> None:
        err = await s.fail(
            "describe_component", component=["tank", "pmup", "pipe", "centrifugal_pmp"]
        )
        assert "[unknown_component]" in err
        assert "component[1]: Unknown component type 'pmup'." in err
        assert (
            "component[3]: Unknown component type 'centrifugal_pmp'. "
            "Did you mean 'centrifugal_pump'?" in err
        )
        assert "component[0]" not in err and "component[2]" not in err
        # The valid choices (aliases and full ids) are listed once.
        assert err.count("Valid:") == 1
        assert "Valid: centrifugal_pump, check_valve, drain" in err
        assert "worldparts.hydraulic.uv_reactor" in err
        err = await s.fail("describe_component", component=["valve", "vlave"])
        assert "component[1]: Unknown component type 'vlave'. Did you mean 'valve'?" in err
        assert err.count("Valid:") == 1
        # A comma-separated string is one unknown id; the error says to pass a list.
        err = await s.fail("describe_component", component="tank, pipe")
        assert "Unknown component type 'tank, pipe'." in err and err.count("Valid:") == 1
        assert "pass a list, e.g. ['tank', 'pipe']" in err
        err = await s.fail("describe_component", component="pmup")
        assert "pass a list" not in err and err.count("Valid:") == 1
        # An empty list and a list over the limit are rejected by the argument schema.
        await s.fail("describe_component", component=[])
        err = await s.fail("describe_component", component=["pipe"] * (MAX_DESCRIBE + 1))
        assert f"at most {MAX_DESCRIBE} items" in err

    run(body)


def test_instructions_and_descriptions_recommend_the_short_path() -> None:
    """list -> describe (all parts, one call) -> load_system (whole document, one call; its
    issues are the check report) -> solve; the incremental tools are named as the way to
    edit, with check_system after edits."""
    workflow = INSTRUCTIONS[INSTRUCTIONS.index("Recommended workflow") :]
    steps = ["list_components", "describe_component", "load_system", "solve"]
    at = [workflow.index(step) for step in steps]
    assert at == sorted(at), at
    # No separate check_system step between load_system and solve: load_system's issues
    # are the check_system report; check_system is named for use after edits.
    between = workflow[workflow.index("load_system") : workflow.index("-> solve")]
    assert "its `issues` are the check_system report" in between
    assert "fix every error" in between
    assert "check_system after edits" in workflow
    assert len(INSTRUCTIONS) < 2_700, len(INSTRUCTIONS)  # sent to every session
    assert "EVERY component you need in ONE call" in INSTRUCTIONS
    assert "COMPLETE system document in ONE call" in INSTRUCTIONS
    for word in ("parameters and inputs", "connections", "controls", "solve_for", "simulate"):
        assert word in workflow, word
    assert "Edit a system with set_values, add_component, connect or add_control" in INSTRUCTIONS

    async def body(s: Session) -> None:
        tools = {t.name: t for t in (await s.client.list_tools()).tools}
        describe = tools["describe_component"]
        assert "as a list in one call" in (describe.description or "")
        component = describe.input_schema["properties"]["component"]
        assert {b["type"] for b in component["anyOf"]} == {"string", "array"}
        assert component["anyOf"][1]["maxItems"] == MAX_DESCRIBE
        assert "one call" in (tools["list_components"].description or "")
        assert "load_system" in (tools["create_system"].description or "")
        load = tools["load_system"]
        assert (load.description or "").startswith("Build a whole system in one call")
        assert "then solve" in (load.description or "")
        assert "check_system report, no need to call it" in (load.description or "")
        # The example document in load_system's argument help loads and solves as it is.
        doc_help = load.input_schema["properties"]["document"]["description"]
        example = ast.literal_eval(doc_help[doc_help.index("{") : doc_help.index(". Components")])
        assert example["components"][1]["inputs"] == {"opening": 0.5}
        loaded = await s.call("load_system", document=example)
        assert loaded["issues"] == []
        solved = await s.call("solve", system_id=loaded["system_id"])
        assert solved["converged"] and solved["values"]["v.volume_flow"]["value"] > 0

    run(body)


def test_short_path_matches_the_step_by_step_build() -> None:
    """The four-call path gives the numbers of the thirty-call path."""
    doc = copy.deepcopy(LIFT)
    doc["components"][2]["inputs"] = {"opening": 0.8}
    fill = {"name": "fill", "type": "hysteresis", "measure": "tank.level",
            "actuate": "pump.speed", "on_below": "0.5 m", "off_above": "1.8 m",
            "on_value": 1, "off_value": 0, "initial": "on"}  # fmt: skip
    doc["controls"] = [fill]

    async def body(s: Session) -> None:
        # Short path: list, describe all parts at once, load the whole document, solve.
        await s.call("list_components")
        described = await s.call("describe_component", component=PARTS)
        assert len(described["components"]) == len(PARTS)
        loaded = await s.call("load_system", document=doc)
        assert loaded["controls"] and "fill" in loaded["controls"]
        short = loaded["system_id"]
        assert [i["where"] for i in loaded["issues"]] == ["tank.inlet"]
        assert all(i["severity"] == "warning" for i in loaded["issues"])
        a = await s.call("solve", system_id=short)
        # load_system's issues are exactly what check_system reports (so the path skips it).
        check = await s.call("check_system", system_id=short)
        assert check["ok"] and check["issues"] == loaded["issues"]

        # Step by step: create, add each part, connect each pair, add the control.
        long = (await s.call("create_system", name="rooftop lift"))["system_id"]
        for c in doc["components"]:
            await s.call("add_component", system_id=long, name=c["name"], component=c["type"],
                         parameters=c.get("parameters"), inputs=c.get("inputs"))  # fmt: skip
        for x, y in doc["connections"]:
            await s.call("connect", system_id=long, a=x, b=y)
        settings = {
            k: v for k, v in fill.items() if k not in ("name", "type", "measure", "actuate")
        }
        await s.call("add_control", system_id=long, name="fill", type="hysteresis",
                     measure="tank.level", actuate="pump.speed", settings=settings)  # fmt: skip
        b = await s.call("solve", system_id=long)

        assert a["values"] == b["values"] and a["controls"] == b["controls"]
        assert a["values"]["pump.volume_flow"]["value"] > 0
        assert a["controls"]["fill"]["state"] == "on"
        # Both documents are the same system.
        doc_a = (await s.call("get_system", system_id=short))["document"]
        doc_b = (await s.call("get_system", system_id=long))["document"]
        assert doc_a["components"] == doc_b["components"]
        assert doc_a["connections"] == doc_b["connections"]
        assert doc_a["controls"] == doc_b["controls"]

    run(body)


def test_list_components_names_table_columns() -> None:
    async def body(s: Session) -> None:
        found = await s.call("list_components", query="pump")
        pump = next(c for c in found["components"] if c["alias"] == "centrifugal_pump")
        params = {k["name"]: k for k in pump["key_parameters"]}
        assert params["head_curve"]["columns"] == ["flow [m3/h]", "head [m]"]
        assert params["power_curve"]["columns"] == ["flow [m3/h]", "power [kW]"]
        numeric = [k for k in pump["key_parameters"] if not isinstance(k["default"], list)]
        assert all("columns" not in k for k in numeric)

    run(body)


# ----------------------------------------------------------------------------------------
# major: load_system errors
# ----------------------------------------------------------------------------------------
def test_load_system_error_names_the_expected_shape() -> None:
    """The trial passed components as a mapping; the error echoed the whole input."""

    async def body(s: Session) -> None:
        big = {f"part_{i}": {"type": "pipe", "parameters": {"length": i}} for i in range(40)}
        doc = {"worldparts_system": "0.1", "name": "x", "components": big}
        err = await s.fail("load_system", document=doc)
        assert "components: must be of type array, got an object with keys" in err
        assert "{'name', 'type', 'parameters'?, 'inputs'?, 'states'?}" in err
        assert "part_39" not in err and len(err) < 1000  # not echoed
        err = await s.fail(
            "load_system",
            document={
                "worldparts_system": "0.1",
                "name": "x",
                "components": [],
                "connections": [["a.port"]],
            },
        )
        assert "connections/0: must have at least 2 items, got 1" in err
        assert "[port_path, port_path]" in err
        tools = {t.name: t for t in (await s.client.list_tools()).tools}
        doc_help = tools["load_system"].input_schema["properties"]["document"]["description"]
        assert "'worldparts_system': '0.1'" in doc_help and "'connections'" in doc_help
        loaded = await s.call("load_system", document=LIFT)
        assert [i["code"] for i in loaded["issues"]] == ["unconnected_port"]  # tank.inlet

    run(body)


def test_system_document_errors_do_not_echo_values() -> None:
    part = {"name": "a", "type": "drain", "parameters": {"t": {"deep": list(range(100))}}}
    doc = {"worldparts_system": "0.1", "name": "x", "components": [part]}
    with pytest.raises(wp.InvalidValueError) as info:
        wp.System.from_dict(doc)
    text = str(info.value)
    assert "components/0/parameters/t" in text and "matches none of the allowed forms" in text
    assert "99" not in text


# ----------------------------------------------------------------------------------------
# major: pump operating region and motor rating
# ----------------------------------------------------------------------------------------
def test_pump_operating_region_and_motor_overload_through_the_server() -> None:
    """Task A runs at 66 % of BEP (info); Task B at speed 1.2 overloads a 4 kW motor."""

    async def body(s: Session) -> None:
        sid = (await s.call("load_system", document=LIFT))["system_id"]
        r = await s.call("solve", system_id=sid, variables=["pump"])
        q = r["values"]["pump.volume_flow"]["value"]
        assert q == pytest.approx(15.94, abs=0.01)
        codes = {(w["code"], w["severity"]) for w in r["warnings"]}
        assert ("outside_preferred_region", "info") in codes
        assert "low_flow" not in {c for c, _ in codes}

        sid = (await s.call("load_system", document=SKID))["system_id"]
        await s.call("set_values", system_id=sid,
                     values={"filter.clogging": 0.8, "pump.speed": 1.2})  # fmt: skip
        r = await s.call("solve", system_id=sid, variables=["pump.shaft_power"])
        shaft = r["values"]["pump.shaft_power"]["value"]
        assert shaft > 5.0  # about 5.4 kW
        overload = [w for w in r["warnings"] if w["code"] == "motor_overload"]
        assert overload and "4 kW" in overload[0]["message"]
        await s.call("set_values", system_id=sid, values={"pump.motor_power": "7.5 kW"})
        r = await s.call("solve", system_id=sid, variables=["pump.shaft_power"])
        assert "motor_overload" not in {w["code"] for w in r["warnings"]}

    run(body)


def test_pump_preferred_region_bounds_and_parameter_rule() -> None:
    s = wp.System.from_dict(LIFT)
    r = s.solve()
    q, bep = r["pump.volume_flow"], r["pump.bep_flow"]
    assert q / bep == pytest.approx(0.661, abs=0.002)
    assert r.has_warning("pump.outside_preferred_region")
    s.set("pump.preferred_min_fraction", 0.6)  # widen the region below the point
    assert not s.solve().has_warning("pump.outside_preferred_region")
    with pytest.raises(wp.InvalidValueError, match="preferred_min_fraction"):
        s.set_values({"pump.preferred_min_fraction": 1.0, "pump.preferred_max_fraction": 1.0})


# ----------------------------------------------------------------------------------------
# minor: goal seek
# ----------------------------------------------------------------------------------------
def test_solve_for_finds_the_pump_speed() -> None:
    """Task A: the speed for 15 m3/h through the Kv 150 valve.

    Hand check with the affinity laws: the system curve is 23.5 m static plus a quadratic
    loss k Q**2; at full speed 30.448 m at 15.943 m3/h gives k = 6.948 / 15.943**2. At
    15 m3/h the system needs 23.5 + k * 225 = 29.650 m, and the pump gives
    33.970527 s**2 - 0.0138548 * 225, so s = sqrt((29.650 + 3.1173) / 33.970527) = 0.9820
    (the solver's 0.9828 is 0.1 % higher: the friction factor rises as the Reynolds number
    falls, so the loss at 15 m3/h is a little above the quadratic scaling).
    """

    async def body(s: Session) -> None:
        sid = (await s.call("load_system", document=LIFT))["system_id"]
        r = await s.call("solve_for", system_id=sid, target="pump.volume_flow",
                         value="15 m3/h", vary="pump.speed", lower=0.5, upper=1.2,
                         variables=["pump"])  # fmt: skip
        assert r["found"]["unit"] == "1"
        assert r["found"]["value"] == pytest.approx(0.9828, abs=5e-4)
        assert r["found"]["value"] == pytest.approx(0.9820, rel=2e-3)
        assert r["achieved"]["value"] == pytest.approx(15.0, abs=1e-6)
        assert r["target_value"] == {"value": 15.0, "unit": "m3/h"}
        assert r["values"]["pump.volume_flow"]["value"] == pytest.approx(15.0, abs=1e-4)
        assert r["evaluations"] < 30
        # vary stays at the value found.
        doc = (await s.call("get_system", system_id=sid))["document"]
        pump = next(c for c in doc["components"] if c["name"] == "pump")
        assert pump["inputs"]["speed"] == pytest.approx(r["found"]["value"], rel=1e-9)

        # A target out of reach names both ends and changes nothing.
        before = (await s.call("get_system", system_id=sid))["document"]
        err = await s.fail("solve_for", system_id=sid, target="pump.volume_flow",
                           value=40, vary="pump.speed", lower=0.5, upper=1.2)  # fmt: skip
        assert "not between them" in err and "pump.speed = 1.2" in err
        assert (await s.call("get_system", system_id=sid))["document"] == before
        err = await s.fail("solve_for", system_id=sid, target="pump.volume_flow",
                           value=15, vary="pump.head", lower=0, upper=1)  # fmt: skip
        assert "not a parameter, input or state" in err
        err = await s.fail("solve_for", system_id=sid, target="pump.volume_flow",
                           value=15, vary="pump.speed", lower=1, upper=0.5)  # fmt: skip
        assert "must be below" in err

    run(body)


# ----------------------------------------------------------------------------------------
# minor: ramps, restore, warning intervals, unit wildcards
# ----------------------------------------------------------------------------------------
def test_simulate_ramp_restore_and_warning_intervals() -> None:
    """Task B: clogging ramped 0 -> 0.8 over 30 min in one event instead of 60."""

    async def body(s: Session) -> None:
        sid = (await s.call("load_system", document=SKID))["system_id"]
        ramp = [{"at": "0 s", "ramp": {"filter.clogging": [0, 0.8]}, "over": "30 min"}]
        sim = await s.call("simulate", system_id=sid, duration="30 min", step="10 s",
                           events=ramp, variables=["filter.pressure_drop", "pump.volume_flow",
                                                   "raw.level"],
                           restore=True)  # fmt: skip
        assert sim["samples"] == 181
        dp = sim["variables"]["filter.pressure_drop"]
        assert dp["values"][0] == pytest.approx(0.523, abs=0.002)
        assert all(b > a for a, b in zip(dp["values"], dp["values"][1:], strict=False))
        assert sim["variables"]["pump.volume_flow"]["final"] < 34
        warnings = {w["code"]: w for w in sim["warnings"]}
        beyond = warnings["beyond_curve"]
        assert beyond["time"] == 0 and beyond["active_at_end"] is False
        assert 0 < beyond["last_time"] < 1800
        change = warnings["change_required"]
        assert change["active_at_end"] is True and change["last_time"] == 1800
        # restore: clogging, levels and every other value are back where they started.
        doc = (await s.call("get_system", system_id=sid))["document"]
        assert doc == {**SKID, "components": doc["components"]}
        assert all("states" not in c for c in doc["components"])
        assert all("clogging" not in c.get("inputs", {}) for c in doc["components"])

        # Without restore the ramp's end value and the final levels stay.
        await s.call("simulate", system_id=sid, duration="60 s", step="10 s",
                     events=[{"at": 0, "ramp": {"filter.clogging": ["0", 0.5]}, "over": 60}],
                     variables=["raw.level"])  # fmt: skip
        values = await s.call("set_values", system_id=sid, values={"pump.speed": 1})
        assert values["values"]["pump.speed"]["value"] == 1
        doc = (await s.call("get_system", system_id=sid))["document"]
        filt = next(c for c in doc["components"] if c["name"] == "filter")
        assert filt["inputs"]["clogging"] == pytest.approx(0.5)

        for bad, text in [
            ({"at": 0, "set": {"pump.speed": 1}, "ramp": {"pump.speed": [0, 1]}, "over": 1},
             "exactly one of"),
            ({"at": 0, "ramp": {"pump.speed": [0, 1]}}, "needs a non-empty"),
            ({"at": "50 s", "ramp": {"pump.speed": [0, 1]}, "over": "20 s"}, "after the end"),
            ({"at": 0, "ramp": {"pump.speed": [0]}, "over": 10}, "[start, end]"),
            ({"at": 0, "ramp": {"pump.head": [0, 1]}, "over": 10}, "not a parameter"),
        ]:  # fmt: skip
            err = await s.fail("simulate", system_id=sid, duration="60 s", events=[bad])
            assert text in err, err

    run(body)


def test_ramp_matches_step_events() -> None:
    """A ramp is the same as one set event per step with linearly spaced values."""

    async def body(s: Session) -> None:
        a = (await s.call("load_system", document=SKID))["system_id"]
        b = (await s.call("load_system", document=SKID))["system_id"]
        ramp = [{"at": "20 s", "ramp": {"filter.clogging": [0.1, 0.5]}, "over": "40 s"}]
        steps = [{"at": 20 + 10 * i, "set": {"filter.clogging": 0.1 + 0.1 * i}} for i in range(5)]
        kw = {"duration": "90 s", "step": "10 s", "variables": ["filter.pressure_drop"]}
        ra = await s.call("simulate", system_id=a, events=ramp, **kw)
        rb = await s.call("simulate", system_id=b, events=steps, **kw)
        assert ra["variables"] == rb["variables"]

    run(body)


def test_units_wildcard_reports_one_flow_unit() -> None:
    async def body(s: Session) -> None:
        sid = (await s.call("load_system", document=LIFT))["system_id"]
        r = await s.call("solve", system_id=sid, units={"*.volume_flow": "m3/h"})
        flows = {p: v for p, v in r["values"].items() if p.endswith(".volume_flow")}
        assert set(flows) == {"pump.volume_flow", "v.volume_flow", "riser.volume_flow",
                              "roof.volume_flow"}  # fmt: skip
        assert all(v["unit"] == "m3/h" for v in flows.values())
        q = flows["pump.volume_flow"]["value"]
        assert flows["v.volume_flow"]["value"] == pytest.approx(q, rel=1e-5)
        assert flows["roof.volume_flow"]["value"] == pytest.approx(q, rel=1e-5)
        # An explicit path wins over the wildcard; tank.net_inflow is not a volume_flow.
        r = await s.call("solve", system_id=sid, variables=["v", "pump.volume_flow"],
                         units={"*.volume_flow": "m3/h", "v.volume_flow": "L/s"})  # fmt: skip
        assert r["values"]["v.volume_flow"]["unit"] == "L/s"
        assert r["values"]["pump.volume_flow"]["unit"] == "m3/h"
        sim = await s.call("simulate", system_id=sid, duration="20 s", step="10 s",
                           variables=["v.volume_flow"], units={"*.volume_flow": "m3/h"},
                           restore=True)  # fmt: skip
        assert sim["variables"]["v.volume_flow"]["unit"] == "m3/h"
        err = await s.fail("solve", system_id=sid, units={"*.volume_flw": "m3/h"})
        assert "matches no reported variable" in err and "*.volume_flow" in err
        err = await s.fail("solve", system_id=sid, units={"*.volume_flow": "bar"})
        assert "bar" in err

    run(body)


# ----------------------------------------------------------------------------------------
# minor: hints and instructions
# ----------------------------------------------------------------------------------------
def test_add_component_hint_and_instructions() -> None:
    async def body(s: Session) -> None:
        sid = (await s.call("create_system", name="x"))["system_id"]
        v = await s.call("add_component", system_id=sid, name="v", component="valve")
        assert {i["code"] for i in v["issues"]} == {"unconnected_port", "no_pressure_reference"}
        assert "clear once its ports are connected" in v["hint"]

    run(body)
    assert "get_system" in INSTRUCTIONS and "server process" in INSTRUCTIONS
    assert "'*.volume_flow'" in INSTRUCTIONS and "solve_for" in INSTRUCTIONS


def test_valve_kv_description_gives_sizes() -> None:
    kv = wp.default_catalog().get("valve").parameters["kv"]
    assert "DN50" in kv.description and "40" in kv.description


# ----------------------------------------------------------------------------------------
# physics concerns
# ----------------------------------------------------------------------------------------
def test_specific_energy_is_none_at_leakage_flow() -> None:
    """Behind an empty tank the pump passes only gate leakage (about 3e-5 m3/h): 1.5 kW
    over that flow would be about 50,000 kWh/m3, so the observable is None there."""
    s = wp.System.from_dict(LIFT)
    s.set("tank.initial_level", 0)
    r = s.solve()
    assert 0 < r["pump.volume_flow"] < 0.01
    assert r["pump.shaft_power"] > 1.0
    assert r["pump.specific_energy"] is None
    s.set("tank.initial_level", 1.5)
    r = s.solve()
    assert r["pump.specific_energy"] == pytest.approx(
        r["pump.shaft_power"] / r["pump.volume_flow"], rel=1e-12
    )


def test_drawing_air_when_the_lift_empties_the_tank() -> None:
    """Task A drain-down: the pump keeps a 25 m column, so after the tank empties its
    inlet sits at 25 - 33.97 = -8.97 m (-0.878 bar gauge) with NPSH available about
    10.35 - 8.97 - 0.17 = 1.2 m: no cavitation at zero flow, only low_flow. The tank now
    says that it is drawing air, from the step that empties it to the end."""
    s = wp.System.from_dict(LIFT)
    r = s.solve()
    assert not r.has_warning("tank.drawing_air")
    sim = s.simulate("25 min", "5 s", variables=["tank.level", "tank.outlet.p"])
    w = {x.code: x for x in sim.warnings if x.component == "tank"}
    empty, air = w["tank_empty"], w["drawing_air"]
    assert air.time is not None and empty.time is not None
    assert empty.time - 5 <= air.time <= empty.time  # the emptying step, or the next
    assert air.active_at_end and air.last_time == pytest.approx(1500)
    assert 1100 <= empty.time <= 1115  # hand estimate 4.71 m3 / about 15.3 m3/h = 1108 s
    p_hand = (25 - 33.970527) * RHO * G / 1e5
    assert sim["tank.outlet.p"][-1] == pytest.approx(p_hand, abs=0.002)
    assert "drawing_air" in {x.code for x in sim.final.warnings}
    msg = next(x.message for x in sim.final.warnings if x.code == "drawing_air")
    assert "empty" in msg and "outlet" in msg


def test_no_drawing_air_when_an_empty_tank_is_not_under_suction() -> None:
    """An empty tank draining by gravity to a drain at the same level: the outlet is at
    atmospheric pressure, nothing is drawn, no warning."""
    s = wp.System("gravity")
    s.add("t", "tank", initial_level=0)
    s.add("p", "pipe", length=5, diameter=25)
    s.add("d", "drain")
    s.connect("t.outlet", "p.port_a")
    s.connect("p.port_b", "d.port")
    r = s.solve()
    assert r.has_warning("t.tank_empty") and not r.has_warning("t.drawing_air")
    assert abs(r["t.outlet.p"]) < 1e-3
    s.set("p.height_difference", -3)  # a siphon leg below the tank pulls suction
    r = s.solve()
    assert r["t.outlet.p"] == pytest.approx(-3 * RHO * G / 1e5, abs=1e-3)
    assert r.has_warning("t.drawing_air")
    assert P_ATM > 0


def test_exit_loss_recommendation_is_one_velocity_head() -> None:
    """The documented fix for the drain's missing exit loss: minor_loss = 1 on the
    discharging pipe adds exactly rho v**2 / 2 (0.26 m at 2.26 m/s)."""

    s = wp.System("exit")
    s.add("src", "supply", pressure=1)
    s.add("p", "pipe", length=10, diameter=50, roughness=0.045)
    s.add("d", "drain")
    s.connect("src.port", "p.port_a")
    s.connect("p.port_b", "d.port")
    base = s.solve()
    q = base["p.volume_flow"]  # L/min
    v = q / 60000 / (math.pi * 0.05**2 / 4)
    assert base["p.velocity"] == pytest.approx(v, rel=1e-9)
    # With minor_loss = 1 the same flow needs exactly rho v**2 / 2 more supply pressure.
    s.set_values({"p.minor_loss": 1, "src.pressure": f"{1e5 + RHO * v * v / 2} Pa"})
    with_loss = s.solve()
    assert with_loss["p.volume_flow"] == pytest.approx(q, rel=1e-6)
    desc = wp.default_catalog().get("drain").data["description"]
    assert "exit loss" in desc and "minor_loss" in desc
