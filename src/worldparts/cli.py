"""The ``worldparts`` command-line interface (design section 10).

Commands::

    worldparts list [QUERY...] [--json]
    worldparts describe COMPONENT [--json]
    worldparts validate [PATHS...] [--json]
    worldparts check-catalog [--component ID] [--verbose] [--json]
    worldparts solve SYSTEM.yaml [--var PATH ...] [--json]
    worldparts simulate SYSTEM.yaml [--duration D] [--step S] [--var PATH ...]
                                    [--max-points N] [--json]
    worldparts mcp

Human-readable tables are the default; ``--json`` prints machine-readable JSON. Commands
exit with 0 on success, 1 when a validation or catalogue check fails and 2 on usage or
worldparts errors (the message, which names the offending path and lists valid
alternatives, goes to stderr).

This module deliberately does not import the MCP SDK; ``worldparts mcp`` imports
:mod:`worldparts.mcp_server` lazily.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import worldparts as wp
from worldparts.catalog import package_manifest_paths
from worldparts.errors import InvalidValueError, UnknownVariableError, format_choices
from worldparts.manifest import Manifest, VariableSpec, load_yaml, validate_manifest_data

__all__ = ["build_parser", "default_paths", "load_document", "main", "select_paths"]

# Exit codes.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2


# ----------------------------------------------------------------------------------------
# helpers shared with the MCP server
# ----------------------------------------------------------------------------------------
def default_paths(system: wp.System) -> list[str]:
    """The default result selection: observables, states and port pressures.

    Parameters and inputs are what the caller set, and port mass flows and temperatures
    are usually visible through observables, so they are left out unless asked for.
    """
    out: list[str] = []
    for v in system.variables():
        if not v.reported:
            continue
        if v.kind in ("observable", "state") or (v.kind == "port" and v.path.endswith(".p")):
            out.append(v.path)
    return out


def select_paths(system: wp.System, requested: Iterable[str] | None) -> list[str]:
    """Resolve a variable selection against a system.

    Each requested item is a variable path (``valve.volume_flow``, ``valve.port_a.p``), an
    instance name (every reported variable of that instance) or ``*`` (everything).
    Without a selection the :func:`default_paths` are returned.

    Raises:
        UnknownVariableError: For an item that matches nothing, listing valid choices.
    """
    items = [str(i).strip() for i in (requested or []) if str(i).strip()]
    if not items:
        return default_paths(system)
    infos = system.variables()
    reported = [v.path for v in infos if v.reported]
    known = set(reported)
    unreported = {v.path for v in infos if not v.reported}
    out: list[str] = []
    for item in items:
        if item in ("*", "all"):
            out.extend(reported)
        elif item in known:
            out.append(item)
        elif "." not in item and item in system.components:
            out.extend(p for p in reported if p.startswith(item + "."))
        elif item in unreported:
            raise UnknownVariableError(
                f"'{item}' is a string or table parameter and is not part of results; read "
                "it from the system document (get_system) or with System.get."
            )
        else:
            raise UnknownVariableError(
                f"Unknown variable '{item}'. Use a path such as 'valve.volume_flow', an "
                "instance name for all of its variables, or '*' for everything. "
                + format_choices(item, reported + list(system.components))
            )
    return list(dict.fromkeys(out))


def load_document(path: str | Path) -> dict[str, Any]:
    """Read a YAML or JSON file that must contain a mapping.

    Raises:
        InvalidValueError: When the file cannot be read or is not a mapping.
    """
    p = Path(path)
    try:
        data = load_yaml(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvalidValueError(f"Cannot read {p}: {exc}") from exc
    except Exception as exc:  # yaml.YAMLError and friends
        raise InvalidValueError(f"{p} is not valid YAML or JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidValueError(f"{p} must contain a YAML mapping, got {type(data).__name__}.")
    return data


# ----------------------------------------------------------------------------------------
# formatting
# ----------------------------------------------------------------------------------------
def _fmt(x: Any) -> str:
    """A compact human form of a value."""
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        if not math.isfinite(x):
            return str(x)
        return f"{x:.6g}"
    if isinstance(x, list):
        return json.dumps(x)
    return str(x)


def _unit(unit: str | None, reference: str | None = None) -> str:
    if unit is None:
        return ""
    return f"{unit} ({reference})" if reference else unit


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]], indent: str = "") -> str:
    """Left-aligned plain-text table; the last column is not padded."""
    body = [[_fmt(c) if not isinstance(c, str) else c for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: Sequence[str]) -> str:
        padded = [c.ljust(widths[i]) for i, c in enumerate(cells[:-1])]
        return (indent + "  ".join([*padded, cells[-1]])).rstrip()

    out = [line(list(headers)), line(["-" * w for w in widths])]
    out.extend(line(r) for r in body)
    return "\n".join(out)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _section(title: str) -> None:
    print()
    print(title)


# ----------------------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------------------
def _cmd_list(args: argparse.Namespace) -> int:
    query = " ".join(args.query) or None
    found = wp.default_catalog().search(query)
    if args.json:
        _print_json([m.describe() | {"tags": list(m.data.get("tags", []))} for m in found])
        return EXIT_OK
    if not found:
        print(f"No component matches '{query}'. Run 'worldparts list' to see all components.")
        return EXIT_OK
    rows = [(m.alias, m.id, m.name, ", ".join(m.ports)) for m in found]
    print(_table(["alias", "id", "name", "ports"], rows))
    return EXIT_OK


def _limits(spec: VariableSpec) -> str:
    if spec.enum:
        return " | ".join(spec.enum)
    if spec.minimum is None and spec.maximum is None:
        return ""
    lo = "-inf" if spec.minimum is None else f"{spec.minimum:g}"
    hi = "inf" if spec.maximum is None else f"{spec.maximum:g}"
    return f"[{lo}, {hi}]"


def _variable_rows(group: dict[str, VariableSpec], with_default: bool) -> list[list[Any]]:
    rows = []
    for spec in group.values():
        unit = _unit(spec.unit, spec.reference) if spec.type != "table" else "table"
        if spec.columns:
            unit = "table: " + ", ".join(f"{c.name} [{c.unit}]" for c in spec.columns)
        row: list[Any] = [spec.name, unit]
        if with_default:
            row += [spec.default, _limits(spec)]
        row.append(spec.description)
        rows.append(row)
    return rows


def _describe_text(m: Manifest) -> None:
    d = m.data
    print(f"{m.id}  (alias '{m.alias}', version {m.version})")
    print(f"{m.name}: {m.summary}")
    print(f"Fidelity: {d['fidelity']['level']}")
    _section("Description")
    for line in str(d.get("description", "")).strip().splitlines():
        print(f"  {line}")
    _section("Ports")
    print(
        _table(
            ["name", "type", "medium", "description"],
            [(p.name, p.type, p.medium, p.description) for p in m.ports.values()],
            "  ",
        )
    )
    for title, group, with_default in (
        ("Parameters", m.parameters, True),
        ("Inputs", m.inputs, True),
        ("States", m.states, False),
        ("Observables", m.observables, False),
    ):
        if not group:
            continue
        _section(title)
        headers = ["name", "unit"] + (["default", "limits"] if with_default else [])
        print(_table([*headers, "description"], _variable_rows(group, with_default), "  "))
    if m.modes:
        _section("Modes (first match wins)")
        print(
            _table(
                ["name", "condition", "description"],
                [(x.name, x.condition, x.description) for x in m.modes],
                "  ",
            )
        )
    if m.warnings:
        _section("Warnings")
        print(
            _table(
                ["code", "severity", "condition", "message"],
                [
                    (w.code, w.severity, w.condition or "(component code)", w.description)
                    for w in m.warnings.values()
                ],
                "  ",
            )
        )
    _section("Scenarios")
    for s in m.scenarios:
        print(f"  {s['id']}: {s.get('description', '')}")
    _section("Contracts")
    for c in m.contracts:
        print(f"  {c['id']} ({c['check']['type']}): {c.get('description', '')}")
    _section("Implementations")
    for key, value in d.get("implementations", {}).items():
        print(f"  {key}: {json.dumps(value)}")
    assumptions = d["fidelity"].get("assumptions", [])
    if assumptions:
        _section("Assumptions")
        for a in assumptions:
            print(f"  - {a}")


def _cmd_describe(args: argparse.Namespace) -> int:
    m = wp.default_catalog().get(args.component)
    if args.json:
        _print_json(m.to_dict())
    else:
        _describe_text(m)
    return EXIT_OK


def _document_files(paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(
                sorted(f for f in p.rglob("*") if f.suffix.lower() in (".yaml", ".yml", ".json"))
            )
        else:
            files.append(p)
    return files


def _validate_file(path: Path) -> dict[str, Any]:
    """Validate one manifest or system document; returns a JSON-ready report."""
    report: dict[str, Any] = {"path": str(path), "kind": "unknown", "valid": False, "problems": []}
    try:
        data = load_document(path)
    except InvalidValueError as exc:
        report["problems"] = [str(exc)]
        return report
    if "manifest_version" in data:
        report["kind"] = "manifest"
        report["id"] = data.get("id")
        report["problems"] = validate_manifest_data(data)
    elif "worldparts_system" in data:
        report["kind"] = "system"
        report["name"] = data.get("name")
        try:
            system = wp.System.from_dict(data)
        except wp.WorldpartsError as exc:
            report["problems"] = [str(exc)]
        else:
            issues = system.check()
            report["problems"] = [
                f"[{i.code}] {i.where}: {i.message}" for i in issues if i.severity == "error"
            ]
            report["warnings"] = [
                f"[{i.code}] {i.where}: {i.message}" for i in issues if i.severity != "error"
            ]
    else:
        report["problems"] = [
            "Not a component manifest (no 'manifest_version') or a system document (no "
            "'worldparts_system')."
        ]
    report["valid"] = not report["problems"]
    return report


def _cmd_validate(args: argparse.Namespace) -> int:
    if args.paths:
        files = _document_files(args.paths)
    else:
        files = [Path(str(t)) for t in package_manifest_paths()]
    reports = [_validate_file(f) for f in files]
    ok = all(r["valid"] for r in reports)
    if args.json:
        _print_json({"valid": ok, "files": reports})
    else:
        for r in reports:
            label = r.get("id") or r.get("name") or ""
            status = "OK  " if r["valid"] else "FAIL"
            print(f"{status} {r['kind']:<8} {r['path']}" + (f"  ({label})" if label else ""))
            for p in r["problems"]:
                print(f"       {p}")
            for w in r.get("warnings", []):
                print(f"       warning: {w}")
        print(f"\n{sum(r['valid'] for r in reports)} of {len(reports)} file(s) valid.")
    return EXIT_OK if ok else EXIT_FAILED


def _cmd_check_catalog(args: argparse.Namespace) -> int:
    reports = wp.check_catalog(component=args.component)
    ok = all(r.passed for r in reports)
    if args.json:
        _print_json({"passed": ok, "components": [r.to_dict() for r in reports]})
        return EXIT_OK if ok else EXIT_FAILED
    for r in reports:
        if args.verbose or not r.passed:
            print(r.summary())
        else:
            n_s = sum(o.kind == "scenario" for o in r.outcomes)
            n_c = sum(o.kind == "contract" for o in r.outcomes)
            print(f"PASS {r.component} ({n_s} scenarios, {n_c} contracts)")
    failed = [r.component for r in reports if not r.passed]
    print(
        f"\n{len(reports) - len(failed)} of {len(reports)} component(s) passed."
        + (f" Failed: {', '.join(failed)}." if failed else "")
    )
    return EXIT_OK if ok else EXIT_FAILED


def _load_system(path: str) -> wp.System:
    return wp.System.from_dict(load_document(path))


def _var_args(values: Sequence[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out.extend(x for x in v.split(",") if x.strip())
    return out


def _print_warnings_and_issues(
    warnings: Sequence[wp.ComponentWarning], issues: Sequence[wp.Issue]
) -> None:
    _section("Warnings")
    if warnings:
        rows = []
        for w in warnings:
            when = f"t={w.time:g} s" if w.time is not None else ""
            rows.append((f"{w.component}.{w.code}", w.severity, when, w.message))
        print(_table(["warning", "severity", "time", "message"], rows, "  "))
    else:
        print("  (none)")
    if issues:
        _section("Check issues")
        print(
            _table(
                ["code", "severity", "where", "message"],
                [(i.code, i.severity, i.where, i.message) for i in issues],
                "  ",
            )
        )


def _cmd_solve(args: argparse.Namespace) -> int:
    system = _load_system(args.system)
    requested = _var_args(args.var)
    result = system.solve()
    if args.json:
        paths = select_paths(system, requested) if requested else None
        _print_json(result.to_dict(paths))
        return EXIT_OK
    paths = select_paths(system, requested)
    print(
        f"System '{system.name}': {'converged' if result.converged else 'NOT converged'} in "
        f"{result.iterations} iterations (max residual {result.max_residual:.2g})."
    )
    _section("Values (pressures in bar are gauge unless marked)")
    rows = [(p, result[p], _unit(result.units[p], result.references.get(p))) for p in paths]
    print(_table(["variable", "value", "unit"], rows, "  "))
    _section("Modes")
    print(_table(["instance", "mode"], list(result.modes.items()), "  "))
    _print_warnings_and_issues(result.warnings, result.issues)
    return EXIT_OK


def _cmd_simulate(args: argparse.Namespace) -> int:
    system = _load_system(args.system)
    block = system.simulation or {}
    duration = args.duration if args.duration is not None else block.get("duration")
    step = args.step if args.step is not None else block.get("step")
    requested = _var_args(args.var)
    record = select_paths(system, requested) if (requested or not args.json) else None
    sim = system.simulate(duration, step, block.get("events"), variables=record)
    if args.json:
        _print_json(sim.to_dict(max_points=args.max_points, variables=record))
        return EXIT_OK
    t_end = sim.time[-1] if sim.time else 0.0
    print(f"System '{system.name}': simulated {t_end:g} s in {len(sim.time)} samples.")
    _section("Variables (pressures in bar are gauge unless marked)")
    rows = []
    for p in sim.series:
        s = sim.summary(p)
        rows.append((p, s["min"], s["max"], s["final"], _unit(s["unit"], sim.references.get(p))))
    print(_table(["variable", "min", "max", "final", "unit"], rows, "  "))
    _section("Mode changes")
    print(
        _table(
            ["time [s]", "instance", "mode"],
            [(f"{c.time:g}", c.component, c.mode or "-") for c in sim.mode_changes],
            "  ",
        )
    )
    _print_warnings_and_issues(sim.warnings, sim.final.issues)
    return EXIT_OK


def _cmd_mcp(args: argparse.Namespace) -> int:
    from worldparts.mcp_server import main as mcp_main

    mcp_main()
    return EXIT_OK


# ----------------------------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """The argument parser of the ``worldparts`` command."""
    parser = argparse.ArgumentParser(
        prog="worldparts",
        description="Agent-ready physical component models: browse the catalogue, validate "
        "manifests and system documents, solve and simulate systems, run the MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"worldparts {wp.__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, func: Callable[[argparse.Namespace], int], help_: str) -> Any:
        p = sub.add_parser(name, help=help_, description=help_)
        p.set_defaults(func=func)
        return p

    def json_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    p = add("list", _cmd_list, "List or search the component catalogue.")
    p.add_argument("query", nargs="*", help="Words that must all appear (id, name, tags, ...).")
    json_flag(p)

    p = add("describe", _cmd_describe, "Describe a component (ports, parameters, modes, ...).")
    p.add_argument("component", help="Full id or short alias, e.g. 'valve'.")
    json_flag(p)

    p = add(
        "validate",
        _cmd_validate,
        "Validate component manifests and system documents (default: the package catalogue).",
    )
    p.add_argument("paths", nargs="*", help="Files or directories (*.yaml, *.yml, *.json).")
    json_flag(p)

    p = add(
        "check-catalog",
        _cmd_check_catalog,
        "Run every scenario and contract of the catalogue; exits 1 on any failure.",
    )
    p.add_argument("--component", help="Only this component (full id or alias).")
    p.add_argument("--verbose", "-v", action="store_true", help="List every check.")
    json_flag(p)

    var_help = (
        "Variable to report: a path (valve.volume_flow), an instance name or '*'. Repeatable "
        "or comma-separated. Default: observables, states and port pressures (with --json: "
        "everything)."
    )
    p = add("solve", _cmd_solve, "Solve a system document for its steady operating point.")
    p.add_argument("system", help="System document (YAML or JSON).")
    p.add_argument("--var", action="append", metavar="PATH", help=var_help)
    json_flag(p)

    p = add("simulate", _cmd_simulate, "Simulate a system document over time.")
    p.add_argument("system", help="System document (YAML or JSON).")
    p.add_argument(
        "--duration",
        help="e.g. '10 min' (default: the document's simulation block, whose events always apply).",
    )
    p.add_argument("--step", help="e.g. '1 s' (default: the document's, else 1 s).")
    p.add_argument("--var", action="append", metavar="PATH", help=var_help)
    p.add_argument(
        "--max-points",
        type=int,
        default=200,
        help="Downsample JSON series to at most this many samples (default 200; 0 keeps all).",
    )
    json_flag(p)

    add("mcp", _cmd_mcp, "Run the MCP server over stdio.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``worldparts`` command; returns the exit code."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except wp.WorldpartsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
