"""Generate docs/catalog.md from the component manifests.

Usage (from the repository root)::

    uv run python tools/gen_catalog_docs.py            # write docs/catalog.md
    uv run python tools/gen_catalog_docs.py --check    # exit 1 if docs/catalog.md is stale

The page is a pure function of the package catalogue, so ``tests/test_docs.py`` regenerates
it in memory and compares. Edit the manifests (or this script), never the generated page.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from worldparts.catalog import Catalog, default_catalog
from worldparts.manifest import Manifest, VariableSpec

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "catalog.md"

#: Sections of the page, in order. Components not listed here go under "Other".
GROUPS: list[tuple[str, list[str]]] = [
    ("Pumping and storage", ["centrifugal_pump", "tank"]),
    ("Treatment", ["media_filter", "uv_reactor"]),
    ("Distribution", ["pipe", "valve", "check_valve"]),
    ("Boundaries", ["supply", "drain"]),
    ("Building services (secondary)", ["mixing_faucet", "instantaneous_water_heater"]),
]


# ------------------------------------------------------------------------------------------
# formatting helpers
# ------------------------------------------------------------------------------------------
def cell(text: Any) -> str:
    """Text safe for one Markdown table cell (no newlines, escaped pipes)."""
    s = " ".join(str(text).split())
    return s.replace("|", "\\|")


def num(x: Any) -> str:
    """A number without float noise: 100000 rather than 1e+05, 0.0001 rather than 1e-04."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if x.is_integer() and abs(x) < 1e15:
            return str(int(x))
        return f"{x:.10g}"
    return str(x)


def value(x: Any) -> str:
    """A default value: numbers, strings, booleans or table rows."""
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(value(v) for v in x) + "]"
    return num(x)


def unit_text(spec: VariableSpec) -> str:
    """The declared unit with its reference, e.g. ``bar (difference)``."""
    if spec.unit is None:
        return ""
    ref = spec.reference
    return f"{spec.unit} ({ref})" if ref else spec.unit


def limits(spec: VariableSpec) -> str:
    """``[min, max]`` in the declared unit, ``enum`` choices, or per-column table limits."""
    if spec.enum:
        return ", ".join(spec.enum)
    if spec.columns:
        parts = []
        for c in spec.columns:
            if c.minimum is not None or c.maximum is not None:
                lo = "-inf" if c.minimum is None else num(c.minimum)
                hi = "inf" if c.maximum is None else num(c.maximum)
                parts.append(f"{c.name} [{lo}, {hi}]")
        return ", ".join(parts)
    if spec.minimum is None and spec.maximum is None:
        return ""
    lo = "-inf" if spec.minimum is None else num(spec.minimum)
    hi = "inf" if spec.maximum is None else num(spec.maximum)
    return f"[{lo}, {hi}]"


def table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    """A Markdown table as lines."""
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out.extend("| " + " | ".join(cell(c) for c in row) + " |" for row in rows)
    return out


def parameter_rows(specs: Iterable[VariableSpec]) -> list[list[str]]:
    """Rows for parameter, input or state tables."""
    rows = []
    for s in specs:
        desc = s.description
        unit = unit_text(s)
        if s.type == "table" and s.columns:
            unit = "table: " + ", ".join(f"{c.name} [{c.unit}]" for c in s.columns)
            if "row" not in desc:
                desc = f"{desc} At least {s.min_rows or 1} rows."
        elif s.type in ("string", "boolean"):
            unit = s.type
        if s.steady:
            desc = f"{desc} (steady: {s.steady})"
        rows.append([f"`{s.name}`", unit, value(s.default), limits(s), desc])
    return rows


# ------------------------------------------------------------------------------------------
# page
# ------------------------------------------------------------------------------------------
def grouped(catalog: Catalog) -> list[tuple[str, list[Manifest]]]:
    """The catalogue split into the page's sections."""
    by_alias = {m.alias: m for m in catalog}
    used: set[str] = set()
    out = []
    for title, aliases in GROUPS:
        ms = [by_alias[a] for a in aliases if a in by_alias]
        used.update(m.alias for m in ms)
        if ms:
            out.append((title, ms))
    rest = [m for m in catalog if m.alias not in used]
    if rest:
        out.append(("Other", rest))
    return out


def key_warnings(m: Manifest) -> str:
    """Warning codes of a component, envelope first, as inline code."""
    return ", ".join(f"`{code}`" for code in m.warnings)


def component_section(m: Manifest) -> list[str]:
    """The Markdown section of one component."""
    d = m.data
    out = [f"### `{m.alias}`: {m.name}", ""]
    tags = ", ".join(d.get("tags", []))
    out.append(
        f"`{m.id}` version {m.version}. Fidelity: {d['fidelity']['level']}."
        + (f" Tags: {tags}." if tags else "")
    )
    out += ["", f"**{m.summary.strip()}**", ""]
    out += [str(d["description"]).strip(), ""]

    out += ["**Ports**", ""]
    out += table(
        ["Port", "Medium", "Description"],
        [[f"`{p.name}`", p.medium, p.description] for p in m.ports.values()],
    )
    header = ["Name", "Unit", "Default", "Limits", "Description"]
    for title, group in (
        ("Parameters", m.parameters),
        ("Inputs", m.inputs),
        ("States", m.states),
    ):
        if group:
            out += ["", f"**{title}**", ""]
            out += table(header, parameter_rows(group.values()))
    out += ["", "**Observables**", ""]
    out += table(
        ["Name", "Unit", "Description"],
        [[f"`{s.name}`", unit_text(s), s.description] for s in m.observables.values()],
    )
    if m.modes:
        out += ["", "**Modes** (the first condition that holds is the mode)", ""]
        out += table(
            ["Mode", "Condition", "Description"],
            [[f"`{x.name}`", f"`{x.condition}`", x.description] for x in m.modes],
        )
    if m.warnings:
        envelope = {w.code for w in m.envelope}
        out += ["", "**Warnings**", ""]
        out += table(
            ["Code", "Severity", "Source", "Condition or description"],
            [
                [
                    f"`{w.code}`",
                    w.severity,
                    "envelope" if w.code in envelope else "component",
                    f"`{w.condition}`: {w.description}" if w.condition else w.description,
                ]
                for w in m.warnings.values()
            ],
        )

    checks = Counter(str(c["check"]["type"]) for c in m.contracts)
    simulated = sum(1 for s in m.scenarios if s.get("simulate"))
    kinds = ", ".join(f"{n} {k}" for k, n in sorted(checks.items()))
    out += ["", "**Tests**", ""]
    out.append(
        f"{len(m.scenarios)} scenarios ({simulated} simulated) and {len(m.contracts)} "
        f"contracts ({kinds}), run by `worldparts check-catalog`."
    )
    out.append("")
    out.append("Scenarios: " + ", ".join(f"`{s['id']}`" for s in m.scenarios) + ".")
    out.append("")
    out.append("Contracts: " + ", ".join(f"`{c['id']}`" for c in m.contracts) + ".")

    out += ["", "**Fidelity and assumptions**", ""]
    out.append(f"Level: {d['fidelity']['level']}.")
    out.append("")
    out += [f"- {cell(a)}" for a in d["fidelity"]["assumptions"]]

    out += ["", "**Bindings**", ""]
    out += table(["Target", "Binding", "Notes"], binding_rows(d["implementations"]))

    prov = d["provenance"]
    out += ["", "**Provenance**", ""]
    out.append(
        f"Authors: {', '.join(prov['authors'])}. Manifest license: {prov['license']}. "
        f"Data license: {prov['data_license']}."
    )
    out.append("")
    out.append(f"Data acquisition: {prov['data']['acquisition']}. {cell(prov['data']['notes'])}")
    out += ["", "Sources:", ""]
    for src in prov["sources"]:
        title = cell(src["title"])
        line = f"- [{title}]({src['url']})" if src.get("url") else f"- {title}"
        for key in ("citation", "notes"):
            if src.get(key):
                line += ("" if line.endswith(".") else ".") + " " + cell(src[key])
        out.append(line)
    out.append("")
    return out


def binding_rows(implementations: Mapping[str, Any]) -> list[list[str]]:
    """Rows of the bindings table: the reference implementation first."""
    rows = []
    for target, binding in implementations.items():
        if not isinstance(binding, Mapping):
            rows.append([target, str(binding), ""])
            continue
        name = next(
            (binding[k] for k in ("python", "class", "element") if k in binding),
            ", ".join(f"{k}: {v}" for k, v in binding.items() if k != "notes"),
        )
        label = {"reference": "Reference (Python)", "modelica": "Modelica", "wntr": "WNTR"}
        lib = f" ({binding['library']})" if binding.get("library") else ""
        rows.append([label.get(target, target), f"`{name}`{lib}", binding.get("notes", "")])
    return rows


def render(catalog: Catalog | None = None) -> str:
    """The full text of docs/catalog.md."""
    cat = catalog or default_catalog()
    groups = grouped(cat)
    out = [
        "# Component catalogue",
        "",
        "<!-- Generated by tools/gen_catalog_docs.py from the manifests in "
        "src/worldparts/catalog. Do not edit by hand. -->",
        "",
        f"worldparts {_version()} ships {len(cat)} water components. Every number below comes "
        "from the component's manifest, the file the runtime, the MCP server and the tests "
        "read. Units are the declared display units; plain numbers passed to the API are in "
        "these units. Pressures are gauge unless marked `absolute` or `difference`. Limits are "
        "hard: a value outside them is rejected. Warnings are soft: they flag results outside "
        "the model's validity or equipment at risk. The format is specified in "
        "[spec/component-manifest.md](../spec/component-manifest.md).",
        "",
        "Regenerate this page with `uv run python tools/gen_catalog_docs.py`.",
        "",
        "## Overview",
        "",
    ]
    rows = []
    for _, ms in groups:
        for m in ms:
            rows.append(
                [
                    f"[`{m.alias}`](#{anchor(m)})",
                    m.summary,
                    f"{len(m.scenarios)} / {len(m.contracts)}",
                    key_warnings(m),
                ]
            )
    out += table(["Component", "What it models", "Scenarios / contracts", "Warnings"], rows)
    out.append("")
    for title, ms in groups:
        out += [f"## {title}", ""]
        for m in ms:
            out += component_section(m)
    return "\n".join(out).rstrip() + "\n"


def anchor(m: Manifest) -> str:
    """GitHub's anchor for the heading ``### `alias`: Name``."""
    text = f"{m.alias}: {m.name}".lower()
    kept = "".join(ch for ch in text if ch.isalnum() or ch in " -_")
    return kept.replace(" ", "-")


def _version() -> str:
    import worldparts

    return worldparts.__version__


def main(argv: Sequence[str] | None = None) -> int:
    """Write docs/catalog.md, or with ``--check`` report whether it is up to date."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit 1 if the page is stale.")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Output path.")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{args.output} is out of date; run tools/gen_catalog_docs.py.", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date.")
        return 0
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
