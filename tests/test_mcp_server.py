"""MCP server tests: every tool through the SDK's in-process client, plus one stdio run.

The in-process tests connect ``mcp.Client`` to the server object (the SDK's in-memory
transport; ``mode="legacy"`` runs the JSON-RPC stream loop over memory streams). Every
successful call's ``structuredContent`` is validated against the tool's ``outputSchema``.
Only the v0.1 core components (supply, drain, pipe, valve, check_valve) are used.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest
import yaml
from mcp import Client
from mcp.client.stdio import StdioServerParameters

import worldparts as wp
from worldparts.adapters import wntr_available
from worldparts.mcp_server import INSTRUCTIONS, RESOURCE_PREFIX, create_server

REPO = Path(__file__).resolve().parents[1]

TOOLS = {
    "list_components",
    "describe_component",
    "create_system",
    "add_component",
    "remove_component",
    "set_values",
    "connect",
    "disconnect",
    "check_system",
    "solve",
    "simulate",
    "list_variables",
    "get_system",
    "load_system",
    "run_contracts",
}


class Session:
    """A connected client plus the tools' output schemas."""

    def __init__(self, client: Client, schemas: dict[str, dict[str, Any]]) -> None:
        self.client = client
        self.schemas = schemas

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        """Call a tool that must succeed; validate its structured content."""
        result = await self.client.call_tool(tool, arguments)
        text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
        assert not result.is_error, text
        data = result.structured_content
        assert isinstance(data, dict)
        jsonschema.validate(data, self.schemas[tool])
        return data

    async def fail(self, tool: str, **arguments: Any) -> str:
        """Call a tool that must fail; return the error text."""
        result = await self.client.call_tool(tool, arguments)
        assert result.is_error, result.structured_content
        assert result.content
        return result.content[0].text  # type: ignore[union-attr]


def run(body: Callable[[Session], Awaitable[None]], mode: str = "auto") -> None:
    """Run ``body`` against a fresh server through the in-process client."""

    async def main() -> None:
        async with Client(create_server(), mode=mode) as client:  # type: ignore[arg-type]
            tools = await client.list_tools()
            schemas = {t.name: t.output_schema for t in tools.tools}
            await body(Session(client, schemas))  # type: ignore[arg-type]

    anyio.run(main)


async def build_line(s: Session, opening: float = 0.5) -> str:
    """supply -> pipe -> valve -> drain; returns the system id."""
    sid = (await s.call("create_system", name="line", description="test line"))["system_id"]
    await s.call("add_component", system_id=sid, name="mains", component="supply",
                 parameters={"pressure": "3 bar"})  # fmt: skip
    await s.call("add_component", system_id=sid, name="p", component="pipe",
                 parameters={"length": 10})  # fmt: skip
    await s.call("add_component", system_id=sid, name="v", component="valve",
                 inputs={"opening": opening})  # fmt: skip
    await s.call("add_component", system_id=sid, name="out", component="drain")
    await s.call("connect", system_id=sid, a="mains.port", b="p.port_a")
    await s.call("connect", system_id=sid, a="p.port_b", b="v.port_a")
    await s.call("connect", system_id=sid, a="v.port_b", b="out.port")
    return sid


def reference_line(opening: float = 0.5) -> wp.System:
    """The same system through the Python API."""
    s = wp.System("line")
    s.add("mains", "supply", pressure="3 bar")
    s.add("p", "pipe", length=10)
    s.add("v", "valve", opening=opening)
    s.add("out", "drain")
    s.connect("mains.port", "p.port_a")
    s.connect("p.port_b", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


# ----------------------------------------------------------------------------------------
# server surface
# ----------------------------------------------------------------------------------------
def test_tools_have_output_schemas_and_instructions() -> None:
    async def body(s: Session) -> None:
        tools = (await s.client.list_tools()).tools
        names = {t.name for t in tools}
        assert names >= TOOLS
        # Design 9: export_system is registered only when an exporter exists (the WNTR
        # adapter, which needs the optional wntr package).
        assert ("export_system" in names) == wntr_available()
        assert ("compare_with_wntr" in names) == wntr_available()
        for t in tools:
            assert t.output_schema and t.output_schema.get("type") == "object", t.name
            assert t.description, t.name
            jsonschema.Draft202012Validator.check_schema(t.output_schema)
        assert s.client.instructions == INSTRUCTIONS
        for word in ("list_components", "describe_component", "check_system", "gauge"):
            assert word in INSTRUCTIONS

    run(body)


def test_list_components() -> None:
    async def body(s: Session) -> None:
        everything = await s.call("list_components")
        aliases = {c["alias"] for c in everything["components"]}
        assert {"supply", "drain", "pipe", "valve", "check_valve"} <= aliases
        assert everything["count"] == len(everything["components"])
        assert "note" not in everything
        valve = next(c for c in everything["components"] if c["alias"] == "valve")
        assert valve["id"] == "worldparts.hydraulic.valve"
        assert valve["ports"] == ["port_a", "port_b"]
        assert {"name": "kv", "unit": "m3/h", "default": 2.5} in valve["key_parameters"]
        assert valve["fidelity"] == "lumped_quasi_steady"

        found = await s.call("list_components", query="check valve")
        assert "worldparts.hydraulic.check_valve" in [c["id"] for c in found["components"]]

        none = await s.call("list_components", query="flux capacitor")
        assert none["count"] == 0 and "valve" in none["note"]

    run(body)


def test_describe_component() -> None:
    async def body(s: Session) -> None:
        d = await s.call("describe_component", component="valve", detail="full")
        assert d["id"] == "worldparts.hydraulic.valve" and d["alias"] == "valve"
        assert d["resource_uri"] == RESOURCE_PREFIX + "worldparts.hydraulic.valve"
        assert [p["name"] for p in d["ports"]] == ["port_a", "port_b"]
        kv = next(p for p in d["parameters"] if p["name"] == "kv")
        assert kv == {
            "name": "kv",
            "description": kv["description"],
            "type": "number",
            "unit": "m3/h",
            "default": 2.5,
            "minimum": 0.0001,
            "maximum": 100000.0,
        }
        char = next(p for p in d["parameters"] if p["name"] == "characteristic")
        assert char["enum"] == ["linear", "equal_percentage", "quick_opening"]
        assert "unit" not in char
        assert d["inputs"][0]["name"] == "opening"
        assert d["states"][0]["steady"] == "settle"
        drop = next(o for o in d["observables"] if o["name"] == "pressure_drop")
        assert drop["reference"] == "difference"
        assert [m["name"] for m in d["modes"]] == ["closed", "throttling", "open"]
        hpd = next(w for w in d["warnings"] if w["code"] == "high_pressure_drop")
        assert hpd["source"] == "envelope" and hpd["condition"] == "pressure_drop > 3"
        assert d["scenarios"] and d["contracts"]
        assert any(c["check"] == "equal" for c in d["contracts"])
        assert d["implementations"]["reference"]["python"].endswith(":TwoWayValve")
        assert d["provenance"]["data"]["acquisition"] == "generic"

        supply = await s.call("describe_component", component="worldparts.hydraulic.supply")
        pressure = next(p for p in supply["parameters"] if p["name"] == "pressure")
        assert pressure["reference"] == "gauge"

        pipe = await s.call("describe_component", component="pipe")
        codes = {w["code"]: w["source"] for w in pipe["warnings"]}
        assert codes["below_vapour_pressure"] == "component"

    run(body)


def test_run_contracts() -> None:
    async def body(s: Session) -> None:
        r = await s.call("run_contracts", component="check_valve")
        assert r["component"] == "worldparts.hydraulic.check_valve"
        assert r["passed"] is True
        assert len(r["scenarios"]) >= 2 and len(r["contracts"]) >= 3
        assert all(o["passed"] and o["failures"] == [] for o in r["scenarios"] + r["contracts"])

    run(body)


# ----------------------------------------------------------------------------------------
# composition and solving
# ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_workflow_solve_matches_python_api(mode: str) -> None:
    ref = reference_line().solve()

    async def body(s: Session) -> None:
        sid = await build_line(s)
        check = await s.call("check_system", system_id=sid)
        assert check == {"system_id": sid, "ok": True, "errors": 0, "warnings": 0, "issues": []}

        r = await s.call("solve", system_id=sid)
        assert r["converged"] is True
        values = r["values"]
        # Default selection: observables, states and port pressures (no parameters).
        assert "v.volume_flow" in values and "v.position" in values and "v.port_a.p" in values
        assert "v.kv" not in values and "v.port_a.m_flow" not in values
        assert values["v.volume_flow"]["unit"] == "L/min"
        assert values["v.volume_flow"]["value"] == pytest.approx(ref["v.volume_flow"], rel=1e-5)
        assert values["mains.port.p"] == {"value": 3.0, "unit": "bar", "reference": "gauge"}
        assert values["v.pressure_drop"]["reference"] == "difference"
        assert "reference" not in values["v.volume_flow"]
        assert r["modes"] == {
            "mains": "supplying",
            "p": "flowing",
            "v": "throttling",
            "out": "receiving",
        }
        assert r["warnings"] == [] and r["issues"] == []

    run(body, mode)


def test_add_component_returns_resolved_values_and_issues() -> None:
    async def body(s: Session) -> None:
        sid = (await s.call("create_system", name="x"))["system_id"]
        v = await s.call("add_component", system_id=sid, name="v", component="valve",
                         parameters={"kv": "3 m3/h", "characteristic": "equal_percentage"},
                         inputs={"opening": 0.25})  # fmt: skip
        assert v["type"] == "worldparts.hydraulic.valve"
        assert v["ports"] == ["v.port_a", "v.port_b"]
        assert v["parameters"]["kv"] == {"value": 3.0, "unit": "m3/h"}
        assert v["parameters"]["characteristic"] == {"value": "equal_percentage"}
        assert v["inputs"]["opening"] == {"value": 0.25, "unit": "1"}
        assert v["states"]["position"]["value"] == 0.25
        codes = {(i["code"], i["where"]) for i in v["issues"]}
        assert codes == {
            ("unconnected_port", "v.port_a"),
            ("unconnected_port", "v.port_b"),
            ("no_pressure_reference", "v.port_a, v.port_b"),
        }

        m = await s.call("add_component", system_id=sid, name="m", component="supply",
                         parameters={"pressure": "2 bar absolute"})  # fmt: skip
        gauge = m["parameters"]["pressure"]
        assert gauge["unit"] == "bar" and gauge["reference"] == "gauge"
        assert gauge["value"] == pytest.approx(2.0 - wp.P_ATM / 1e5)

    run(body)


def test_units_argument_converts_values() -> None:
    ref = reference_line().solve()

    async def body(s: Session) -> None:
        sid = await build_line(s)
        r = await s.call(
            "solve",
            system_id=sid,
            variables=["v.volume_flow"],
            units={"mains.port.p": "bar absolute", "v.pressure_drop": "kPa"},
        )
        assert list(r["values"]) == ["v.volume_flow", "mains.port.p", "v.pressure_drop"]
        assert r["values"]["mains.port.p"] == {
            "value": pytest.approx(4.01325),
            "unit": "bar",
            "reference": "absolute",
        }
        drop = r["values"]["v.pressure_drop"]
        assert drop["unit"] == "kPa" and drop["reference"] == "difference"
        assert drop["value"] == pytest.approx(ref["v.pressure_drop"] * 100, rel=1e-5)

        everything = await s.call("solve", system_id=sid, variables=["*"])
        assert "v.kv" in everything["values"] and "p.port_a.T" in everything["values"]
        instance = await s.call("solve", system_id=sid, variables=["out"])
        assert set(instance["values"]) == {
            "out.temperature",
            "out.volume_flow",
            "out.port.p",
            "out.port.m_flow",
            "out.port.T",
        }

    run(body)


def test_set_values_changes_the_operating_point() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s, opening=1.0)
        before = (await s.call("solve", system_id=sid, variables=["v.volume_flow"]))["values"]
        out = await s.call(
            "set_values", system_id=sid, values={"v.opening": 0.3, "mains.pressure": "2 bar"}
        )
        assert out["values"]["v.opening"] == {"value": 0.3, "unit": "1"}
        assert out["values"]["mains.pressure"] == {
            "value": 2.0,
            "unit": "bar",
            "reference": "gauge",
        }
        after = (await s.call("solve", system_id=sid, variables=["v.volume_flow"]))["values"]
        assert after["v.volume_flow"]["value"] < before["v.volume_flow"]["value"]

        ref = reference_line(0.3)
        ref.set("mains.pressure", "2 bar")
        expected = ref.solve()["v.volume_flow"]
        assert after["v.volume_flow"]["value"] == pytest.approx(expected, rel=1e-5)

    run(body)


def test_simulate_downsamples_and_summarises() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)
        sim = await s.call(
            "simulate",
            system_id=sid,
            duration="60 s",
            step="1 s",
            events=[{"at": "30 s", "set": {"v.opening": 0.0}}],
            variables=["v.volume_flow", "v.position"],
            max_points=11,
        )
        assert sim["samples"] == 61 and sim["duration"] == 60.0
        assert len(sim["time"]) == 11 and sim["time"][0] == 0.0 and sim["time"][-1] == 60.0
        flow = sim["variables"]["v.volume_flow"]
        assert flow["unit"] == "L/min" and len(flow["values"]) == 11
        assert flow["max"] == pytest.approx(reference_line().solve()["v.volume_flow"], rel=1e-5)
        assert flow["min"] == flow["final"] and flow["final"] < 0.01
        assert sim["variables"]["v.position"]["final"] == 0.0
        changes = [(c["time"], c["component"], c["mode"]) for c in sim["mode_changes"]]
        assert (0.0, "v", "throttling") in changes and (30.0, "v", "closed") in changes
        assert sim["final_modes"]["v"] == "closed"

        # Events persist: the system keeps the closed valve.
        doc = (await s.call("get_system", system_id=sid))["document"]
        valve = next(c for c in doc["components"] if c["name"] == "v")
        assert valve["inputs"]["opening"] == 0

    run(body)


def test_simulate_default_selection_and_units() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)
        sim = await s.call("simulate", system_id=sid, duration=3, units={"v.volume_flow": "L/s"})
        assert sim["samples"] == 4
        assert "v.position" in sim["variables"] and "mains.port.p" in sim["variables"]
        assert sim["variables"]["mains.port.p"]["reference"] == "gauge"
        assert sim["variables"]["v.volume_flow"]["unit"] == "L/s"

    run(body)


def test_list_variables() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)
        allvars = (await s.call("list_variables", system_id=sid))["variables"]
        by_path = {v["path"]: v for v in allvars}
        assert by_path["v.opening"]["kind"] == "input" and by_path["v.opening"]["settable"]
        assert by_path["v.position"]["kind"] == "state"
        assert by_path["v.volume_flow"]["kind"] == "observable"
        assert not by_path["v.volume_flow"]["settable"]
        assert by_path["v.port_a.p"]["kind"] == "port"
        assert by_path["v.port_a.p"]["reference"] == "gauge"
        assert by_path["v.characteristic"]["reported"] is False
        assert by_path["mains.pressure"]["minimum"] == -0.9

        only = (await s.call("list_variables", system_id=sid, component="out"))["variables"]
        assert only and all(v["path"].startswith("out.") for v in only)
        err = await s.fail("list_variables", system_id=sid, component="outlet")
        assert "outlet" in err and "Did you mean 'out'" in err

    run(body)


def test_get_and_load_system_round_trip() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)
        solved = await s.call("solve", system_id=sid, variables=["v.volume_flow"])
        doc = (await s.call("get_system", system_id=sid))["document"]
        assert doc["worldparts_system"] == "0.1" and doc["name"] == "line"
        assert doc["description"] == "test line"
        assert ["v.port_b", "out.port"] in doc["connections"]

        loaded = await s.call("load_system", document=doc)
        assert loaded["system_id"] != sid
        assert loaded["issues"] == [] and loaded["unconnected_ports"] == []
        again = await s.call("solve", system_id=loaded["system_id"], variables=["v.volume_flow"])
        assert again["values"] == solved["values"]

        # YAML text works too, and problems are reported rather than raised.
        text = yaml.safe_dump(
            {
                "worldparts_system": "0.1",
                "name": "broken",
                "components": [
                    {"name": "a", "type": "supply"},
                    {"name": "b", "type": "valv"},
                ],
                "connections": [["a.port", "b.port_a"]],
            }
        )
        broken = await s.call("load_system", document=text)
        codes = {i["code"] for i in broken["issues"]}
        assert "unknown_component" in codes
        assert broken["components"]["b"] == "valv"

    run(body)


def test_disconnect_and_remove_component() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)
        out = await s.call("disconnect", system_id=sid, a="out.port", b="v.port_b")
        assert ["v.port_b", "out.port"] not in out["connections"]
        assert set(out["unconnected_ports"]) == {"v.port_b", "out.port"}
        check = await s.call("check_system", system_id=sid)
        assert check["ok"] and check["warnings"] == 2
        assert {i["code"] for i in check["issues"]} == {"unconnected_port"}

        removed = await s.call("remove_component", system_id=sid, name="mains")
        assert "mains" not in removed["components"]
        assert ["mains.port", "p.port_a"] not in removed["connections"]
        await s.call("remove_component", system_id=sid, name="out")
        check = await s.call("check_system", system_id=sid)
        assert not check["ok"] and check["errors"] == 1
        assert [i["code"] for i in check["issues"] if i["severity"] == "error"] == [
            "no_pressure_reference"
        ]
        err = await s.fail("solve", system_id=sid)
        assert "system_check_failed" in err and "no_pressure_reference" in err

    run(body)


# ----------------------------------------------------------------------------------------
# errors
# ----------------------------------------------------------------------------------------
def test_error_messages_name_the_path_and_list_alternatives() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)

        err = await s.fail("add_component", system_id=sid, name="x", component="valv")
        assert "[unknown_component]" in err and "'valv'" in err
        assert "Did you mean 'valve'" in err and "check_valve" in err

        err = await s.fail("describe_component", component="pmup")
        assert "[unknown_component]" in err and "Valid:" in err

        err = await s.fail("connect", system_id=sid, a="v.port_c", b="out.port")
        assert "[unknown_port]" in err and "port_c" in err
        assert "v.port_a" in err and "v.port_b" in err

        err = await s.fail("set_values", system_id=sid, values={"v.opening": 2})
        assert "[parameter_out_of_range]" in err and "v.opening" in err and "[0, 1]" in err

        err = await s.fail("add_component", system_id=sid, name="y", component="valve",
                           inputs={"opening": 1.5})  # fmt: skip
        assert "[parameter_out_of_range]" in err and "y.opening" in err

        err = await s.fail("add_component", system_id=sid, name="z", component="pipe",
                           parameters={"length": "3 bar"})  # fmt: skip
        assert "z.length" in err

        err = await s.fail("set_values", system_id=sid, values={"v.volume_flow": 3})
        assert "read-only" in err and "v.opening" in err

        err = await s.fail("add_component", system_id=sid, name="v", component="valve")
        assert "already exists" in err

        err = await s.fail("solve", system_id="s999")
        assert "[unknown_system]" in err and "s999" in err and sid in err

        err = await s.fail("solve", system_id=sid, variables=["v.flow"])
        assert "v.flow" in err and "v.volume_flow" in err

        err = await s.fail("solve", system_id=sid, units={"v.volume_flow": "bar"})
        assert "L/min" in err and "bar" in err

        err = await s.fail("simulate", system_id=sid, duration="10 s",
                           events=[{"at": "20 s", "set": {"v.opening": 0}}])  # fmt: skip
        assert "events[0]" in err and "after the end" in err

        err = await s.fail("run_contracts", component="nope")
        assert "[unknown_component]" in err

        # Argument validation by the SDK (wrong type) is also an error result.
        err = await s.fail("simulate", system_id=sid, duration="1 s", max_points=1)
        assert "max_points" in err

    run(body)


# ----------------------------------------------------------------------------------------
# resources
# ----------------------------------------------------------------------------------------
def test_manifest_resources() -> None:
    async def body(s: Session) -> None:
        listed = await s.client.list_resources()
        uris = {str(r.uri) for r in listed.resources}
        for alias in ("supply", "drain", "pipe", "valve", "check_valve"):
            assert f"{RESOURCE_PREFIX}worldparts.hydraulic.{alias}" in uris
        valve = next(r for r in listed.resources if str(r.uri).endswith(".valve"))
        assert valve.mime_type == "application/yaml" and valve.name == "valve"

        templates = await s.client.list_resource_templates()
        assert [t.uri_template for t in templates.resource_templates] == [RESOURCE_PREFIX + "{id}"]

        full = await s.client.read_resource(RESOURCE_PREFIX + "worldparts.hydraulic.valve")
        by_alias = await s.client.read_resource(RESOURCE_PREFIX + "valve")
        text = full.contents[0].text  # type: ignore[union-attr]
        assert yaml.safe_load(text)["id"] == "worldparts.hydraulic.valve"
        assert by_alias.contents[0].text == text  # type: ignore[union-attr]

        with pytest.raises(Exception, match="valv"):
            await s.client.read_resource(RESOURCE_PREFIX + "valv")

    run(body)


def test_store_is_per_server() -> None:
    a, b = create_server(), create_server()
    assert a.store is not b.store  # type: ignore[attr-defined]

    async def body(s: Session) -> None:
        first = await s.call("create_system", name="one")
        second = await s.call("create_system", name="two")
        assert (first["system_id"], second["system_id"]) == ("s1", "s2")

    run(body)


def test_extra_tool_registrars_extension_point() -> None:
    seen: list[Any] = []

    def register(server: Any, store: Any) -> None:
        seen.append(store)

        @server.tool()
        def export_system(system_id: str, target: str) -> dict[str, str]:
            """Placeholder export."""
            return {"system_id": system_id, "target": target, "components": ""}

    server = create_server(registrars=[register])
    assert seen == [server.store]  # type: ignore[attr-defined]

    async def main() -> None:
        async with Client(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert "export_system" in names

    anyio.run(main)


# ----------------------------------------------------------------------------------------
# end to end over stdio
# ----------------------------------------------------------------------------------------
UV = shutil.which("uv")


@pytest.mark.skipif(
    UV is None or bool(os.environ.get("WORLDPARTS_SKIP_SPAWN")),
    reason="needs 'uv' on PATH to spawn 'uv run worldparts mcp' (unset WORLDPARTS_SKIP_SPAWN)",
)
def test_stdio_end_to_end() -> None:
    params = StdioServerParameters(
        command=str(UV),
        args=["run", "--no-sync", "worldparts", "mcp"],
        cwd=str(REPO),
        env=dict(os.environ),
    )

    async def main() -> None:
        with anyio.fail_after(120):
            async with Client(params) as client:
                tools = await client.list_tools()
                schemas = {t.name: t.output_schema for t in tools.tools}
                assert set(schemas) >= TOOLS
                s = Session(client, schemas)  # type: ignore[arg-type]
                sid = (await s.call("create_system", name="e2e"))["system_id"]
                await s.call("add_component", system_id=sid, name="src", component="supply",
                             parameters={"pressure": 1.0})  # fmt: skip
                await s.call("add_component", system_id=sid, name="pipe", component="pipe",
                             parameters={"length": 0.01, "diameter": "100 mm"})  # fmt: skip
                await s.call("add_component", system_id=sid, name="v", component="valve")
                await s.call("add_component", system_id=sid, name="sink", component="drain")
                await s.call("connect", system_id=sid, a="src.port", b="pipe.port_a")
                await s.call("connect", system_id=sid, a="pipe.port_b", b="v.port_a")
                await s.call("connect", system_id=sid, a="v.port_b", b="sink.port")
                r = await s.call("solve", system_id=sid, variables=["v.volume_flow"])
                # A short, wide pipe loses almost nothing, so the fully open Kv 2.5 valve at
                # 1 bar passes Kv * sqrt(1000 / 998.2) m3/h (the Kv definition).
                expected = 2.5 * (1000 / 998.2) ** 0.5 * 1000 / 60
                assert r["values"]["v.volume_flow"]["value"] == pytest.approx(expected, rel=1e-3)
                err = await s.fail("connect", system_id=sid, a="v.port_x", b="sink.port")
                assert "v.port_a" in err

    anyio.run(main)


def test_temperature_difference_through_the_server() -> None:
    """temperature_rise is declared a difference: describe, list and convert it as one."""

    async def body(s: Session) -> None:
        d = await s.call("describe_component", component="instantaneous_water_heater")
        rise = next(o for o in d["observables"] if o["name"] == "temperature_rise")
        assert rise["unit"] == "K" and rise["reference"] == "difference"
        assert rise["quantity"] == "temperature_difference"
        outlet = next(o for o in d["observables"] if o["name"] == "outlet_temperature")
        assert "reference" not in outlet and "quantity" not in outlet

        sid = (await s.call("create_system", name="shower"))["system_id"]
        await s.call("add_component", system_id=sid, name="mains", component="supply",
                     parameters={"pressure": 3, "temperature": 12})  # fmt: skip
        await s.call("add_component", system_id=sid, name="h",
                     component="instantaneous_water_heater")  # fmt: skip
        await s.call("add_component", system_id=sid, name="v", component="valve",
                     parameters={"kv": 0.2})  # fmt: skip
        await s.call("add_component", system_id=sid, name="out", component="drain")
        await s.call("connect", system_id=sid, a="mains.port", b="h.inlet")
        await s.call("connect", system_id=sid, a="h.outlet", b="v.port_a")
        await s.call("connect", system_id=sid, a="v.port_b", b="out.port")
        r = await s.call(
            "solve",
            system_id=sid,
            variables=["h.temperature_rise"],
            units={"h.outlet_temperature": "degF"},
        )
        assert r["values"]["h.temperature_rise"] == {
            "value": pytest.approx(43.0, abs=1e-4),
            "unit": "K",
            "reference": "difference",
        }
        assert r["values"]["h.outlet_temperature"]["value"] == pytest.approx(131.0, abs=1e-4)
        r = await s.call("solve", system_id=sid, units={"h.temperature_rise": "degF"})
        assert r["values"]["h.temperature_rise"]["value"] == pytest.approx(77.4, abs=1e-4)
        infos = await s.call("list_variables", system_id=sid, component="h")
        rise_info = next(v for v in infos["variables"] if v["path"] == "h.temperature_rise")
        assert rise_info["reference"] == "difference"
        assert rise_info["quantity"] == "temperature_difference"

    run(body)


def test_describe_component_shows_what_the_manifest_declares() -> None:
    """Scenario systems and expectations, contract rules and sweeps, table columns."""

    async def body(s: Session) -> None:
        d = await s.call("describe_component", component="valve", detail="full")
        m = wp.default_catalog().get("valve")
        assert [c["rule"] for c in d["contracts"]] == [
            {k: v for k, v in c["check"].items() if k != "type"} for c in m.contracts
        ]
        assert all(c.get("sweep_spec") == c_m.get("sweep") for c, c_m in
                   zip(d["contracts"], m.contracts, strict=True))  # fmt: skip
        for doc, raw in zip(d["scenarios"], m.scenarios, strict=True):
            assert doc["system"] == raw["system"] and doc["expect"] == raw["expect"]
        pump = await s.call("describe_component", component="centrifugal_pump")
        head = next(p for p in pump["parameters"] if p["name"] == "head_curve")
        assert [c["name"] for c in head["columns"]] == ["flow", "head"]
        assert all("description" in c for c in head["columns"])

    run(body)


# ----------------------------------------------------------------------------------------
# WNTR adapter tools (registered only when the optional wntr package is installed)
# ----------------------------------------------------------------------------------------
@pytest.mark.skipif(not wntr_available(), reason="needs the optional wntr package")
def test_export_system_and_compare_with_wntr() -> None:
    async def body(s: Session) -> None:
        sid = await build_line(s)
        out = await s.call("export_system", system_id=sid, target="wntr_inp")
        assert out["system_id"] == sid and out["target"] == "wntr_inp"
        assert "[PIPES]" in out["text"] and "D-W" in out["text"]
        assert "notes" not in out  # nothing is approximated in a pipe-and-valve line

        cmp = await s.call("compare_with_wntr", system_id=sid)
        report = cmp["report"]
        flow = reference_line().solve().get("v.volume_flow", unit="m3/h")
        v = next(c for c in report["links"] if c["path"] == "v.volume_flow")
        assert v["worldparts"] == pytest.approx(flow, rel=1e-9)
        # Default pipes are smooth: Churchill vs Swamee-Jain, well below 0.2 %.
        assert cmp["max_flow_rel_diff"] < 2e-3
        assert cmp["max_pressure_abs_diff"] < 5e-3
        assert report["divergence_sources"]

        err = await s.fail("export_system", system_id="s99")
        assert "unknown_system" in err
        tap = (await s.call("create_system", name="tap"))["system_id"]
        await s.call("add_component", system_id=tap, name="mains", component="supply")
        await s.call("add_component", system_id=tap, name="f", component="mixing_faucet")
        await s.call("connect", system_id=tap, a="mains.port", b="f.cold")
        err = await s.fail("export_system", system_id=tap)
        assert "[unsupported_component]" in err and "f (mixing_faucet)" in err

    run(body)
