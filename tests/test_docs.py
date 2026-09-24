"""The user documentation stays true: generated pages are current and published code runs.

- ``docs/catalog.md`` equals what ``tools/gen_catalog_docs.py`` renders from the manifests.
- Every fenced ``python`` block in README.md runs, and when an ``Output:`` block follows it,
  the printed output matches it exactly. Blocks run in order in one namespace (a later
  block may continue an earlier one), in a temporary working directory.
- The README's system document is the Python example's ``System.to_dict()``, and the CLI
  commands shown for it work.
- The README's MCP call sequence gives the numbers the README quotes.
- The README catalogue table lists every component, and local links in the docs resolve.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import re
import shlex
from pathlib import Path
from types import ModuleType
from typing import Any

import anyio
import pytest
import yaml
from mcp import Client

import worldparts as wp
from worldparts import cli
from worldparts.mcp_server import create_server

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

#: Documents whose relative links must resolve.
LINKED_DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "docs/catalog.md",
    "docs/agent-trial.md",
    "spec/component-manifest.md",
]

_BLOCK = re.compile(
    r"```python\n(?P<code>.*?)```[ \t]*\n(?:\s*Output:\s*\n\s*```text\n(?P<out>.*?)```)?",
    re.DOTALL,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _load_generator() -> ModuleType:
    path = ROOT / "tools" / "gen_catalog_docs.py"
    spec = importlib.util.spec_from_file_location("gen_catalog_docs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_page_is_up_to_date() -> None:
    expected = _load_generator().render()
    current = _read(ROOT / "docs" / "catalog.md")
    assert current == expected, (
        "docs/catalog.md is stale; run `uv run python tools/gen_catalog_docs.py`."
    )


def test_catalog_page_covers_every_component() -> None:
    text = _read(ROOT / "docs" / "catalog.md")
    for m in wp.default_catalog():
        assert f"### `{m.alias}`: {m.name}" in text
        for name in m.variables():
            assert f"`{name}`" in text, f"{m.alias}.{name} missing from docs/catalog.md"
        for code in m.warnings:
            assert f"`{code}`" in text


def _python_blocks() -> list[tuple[str, str | None]]:
    return [(m["code"], m["out"]) for m in _BLOCK.finditer(_read(README))]


def test_readme_has_python_examples() -> None:
    assert _python_blocks(), "README.md has no python code block"


def _run_readme_blocks(upto: int) -> tuple[dict[str, Any], str]:
    """Run README python blocks 0..upto in one namespace; returns it and block upto's output.

    Call inside a temporary working directory: a block may write files (``lift.yaml``).
    """
    namespace: dict[str, Any] = {"__name__": "readme"}
    output = ""
    for index, (code, _) in enumerate(_python_blocks()[: upto + 1]):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exec(compile(code, f"README.md python block {index}", "exec"), namespace)
        output = buffer.getvalue()
    return namespace, output


@pytest.mark.parametrize("index", range(len(_python_blocks())))
def test_readme_python_block_runs(
    index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _, output = _run_readme_blocks(index)
    expected = _python_blocks()[index][1]
    if expected is not None:
        assert output == expected


def test_readme_system_document_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The YAML document in "System documents and the CLI" is what the Python example
    saves, and the CLI commands shown for it run."""
    text = _read(README)
    section = text[text.index("### System documents and the CLI") :]
    document = re.search(r"```yaml\n(.*?)```", section, re.DOTALL)
    assert document is not None
    shown = yaml.safe_load(document[1])
    monkeypatch.chdir(tmp_path)
    _run_readme_blocks(len(_python_blocks()) - 1)
    saved = yaml.safe_load((tmp_path / "lift.yaml").read_text(encoding="utf-8"))
    assert wp.System.from_dict(shown).to_dict() == saved
    commands = re.search(
        r"```sh\n(uv run worldparts validate lift\.yaml.*?)```", section, re.DOTALL
    )
    assert commands is not None
    lines = commands[1].splitlines()
    assert len(lines) >= 3
    capsys.readouterr()
    for line in lines:
        argv = shlex.split(line.split("#")[0])
        assert argv[:3] == ["uv", "run", "worldparts"], line
        assert cli.main(argv[3:]) == 0, (line, capsys.readouterr().err)


def test_readme_catalogue_table_lists_every_component() -> None:
    text = _read(README)
    for m in wp.default_catalog():
        assert f"| `{m.alias}` |" in text, f"{m.alias} missing from the README catalogue table"


def test_readme_agent_sequence() -> None:
    """Replay the README's MCP call sequence and check the numbers it quotes."""
    text = _read(README)
    parts = [
        ("raw", "tank", {}),
        ("pump", "centrifugal_pump", {}),
        ("filter", "media_filter", {}),
        ("valve", "valve", {"kv": 40}),
        ("uv", "uv_reactor", {}),
        ("out", "drain", {}),
    ]
    links = [
        ("raw.outlet", "pump.inlet"),
        ("pump.outlet", "filter.inlet"),
        ("filter.outlet", "valve.port_a"),
        ("valve.port_b", "uv.inlet"),
        ("uv.outlet", "out.port"),
    ]
    # The README's describe_component call names every part type, in order.
    listed = re.search(r"describe_component\((\[.*?\])\)", text, re.DOTALL)
    assert listed, "README has no describe_component([...]) call"
    types = ast.literal_eval(listed.group(1))
    assert types == [component for _, component, _ in parts]
    assert '{"worldparts_system": "0.1", "name": "skid"' in text
    assert '"parameters": {"kv": 40}' in text and '["raw.outlet", "pump.inlet"]' in text
    seen: dict[str, Any] = {}

    async def call(client: Client, tool: str, **args: Any) -> dict[str, Any]:
        result = await client.call_tool(tool, args)
        assert not result.is_error, result.content
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    async def main() -> None:
        async with Client(create_server()) as client:  # type: ignore[arg-type]
            found = await call(client, "list_components", query="uv")
            seen["found"] = [c["alias"] for c in found["components"]]
            described = await call(client, "describe_component", component=types)
            seen["described"] = [d["alias"] for d in described["components"]]
            document = {
                "worldparts_system": "0.1",
                "name": "skid",
                "components": [
                    {"name": name, "type": component} | ({"parameters": p} if p else {})
                    for name, component, p in parts
                ],
                "connections": [list(link) for link in links],
            }
            loaded = await call(client, "load_system", document=document)
            sid = loaded["system_id"]
            seen["sid"] = sid
            seen["loaded"] = loaded
            seen["check"] = await call(client, "check_system", system_id=sid)
            seen["solve"] = await call(client, "solve", system_id=sid)
            seen["solve_for"] = await call(
                client,
                "solve_for",
                system_id=sid,
                target="uv.dose",
                value="45 mJ/cm2",
                vary="valve.opening",
                lower=0.05,
                upper=1,
            )

    anyio.run(main)

    assert seen["found"] == ["uv_reactor"]
    assert seen["described"] == types
    assert seen["sid"] == "s1"
    # load_system's issues are the check_system report: no errors, raw.inlet is capped.
    issues = seen["loaded"]["issues"]
    assert [i["where"] for i in issues] == ["raw.inlet"]
    assert all(i["severity"] != "error" for i in issues)
    check = seen["check"]
    assert check["ok"] and check["errors"] == 0 and check["issues"] == issues

    solved = seen["solve"]
    flow = solved["values"]["pump.volume_flow"]["value"]
    dose = solved["values"]["uv.dose"]["value"]
    codes = sorted(f"{w['component']}.{w['code']}" for w in solved["warnings"])
    assert f"pump {flow:.1f} m3/h, UV dose {dose:.1f} mJ/cm2" in text
    assert codes == ["filter.over_rated_flow", "pump.beyond_curve", "uv.underdose"]
    assert "warnings: pump.beyond_curve, filter.over_rated_flow, uv.underdose" in text

    goal = seen["solve_for"]
    opening = goal["found"]["value"]
    flow = goal["values"]["pump.volume_flow"]["value"]
    assert goal["achieved"]["value"] == pytest.approx(45.0, abs=1e-6)
    assert goal["warnings"] == []
    assert f"opening {opening:.3f}, {flow:.1f} m3/h, no warnings" in text
    # the README's hand check: dose = fluence rate * volume / flow
    assert 20 * 15e-3 / (flow / 3600) == pytest.approx(45.0, rel=1e-3)


def test_spec_appendix_is_the_shipped_valve_manifest() -> None:
    spec = _read(ROOT / "spec" / "component-manifest.md")
    appendix = spec.split("## Appendix: the valve manifest", 1)[1]
    block = re.search(r"```yaml\n(.*?)```", appendix, re.DOTALL)
    assert block is not None
    valve = _read(ROOT / "src" / "worldparts" / "catalog" / "hydraulic" / "valve.yaml")
    assert block[1] == valve, "spec appendix differs from valve.yaml; copy the file again"


_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("doc", LINKED_DOCS)
def test_local_links_resolve(doc: str) -> None:
    path = ROOT / doc
    if not path.exists():
        pytest.fail(f"{doc} is missing")
    broken = []
    for target in _LINK.findall(_read(path)):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        local = target.split("#", 1)[0]
        if not (path.parent / local).exists():
            broken.append(target)
    assert not broken, f"{doc}: broken links {broken}"
