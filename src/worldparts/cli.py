"""The ``worldparts`` command-line interface (design section 10).

Commands::

    worldparts list [QUERY...] [--json]
    worldparts describe COMPONENT [--json]
    worldparts validate [PATHS...] [--json]
    worldparts check-catalog [--component ID] [--verbose] [--json]
    worldparts solve SYSTEM.yaml [--var PATH ...] [--units PATTERN=UNIT ...] [--json]
    worldparts simulate SYSTEM.yaml [--duration D] [--step S] [--var PATH ...]
                                    [--units PATTERN=UNIT ...] [--max-points N] [--json]
    worldparts export SYSTEM.yaml --target wntr_inp [-o FILE] [--compare] [--json]
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
import dataclasses
import json
import math
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import worldparts as wp
from worldparts.catalog import package_manifest_paths
from worldparts.errors import InvalidValueError, UnknownVariableError, format_choices
from worldparts.manifest import Manifest, VariableSpec, load_yaml, validate_manifest_data
from worldparts.units import split_pressure_reference

__all__ = [
    "build_parser",
    "default_paths",
    "is_wildcard",
    "load_document",
    "main",
    "select_paths",
    "select_paths_with_units",
    "unit_targets",
]

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


def is_wildcard(key: str) -> bool:
    """True for a ``units`` key of the form ``'*.<name>'`` (every selected ``*.<name>``)."""
    return key.startswith("*.") and len(key) > 2


def select_paths_with_units(
    system: wp.System, variables: Iterable[str] | None, units: Mapping[str, str] | None
) -> list[str]:
    """Requested paths (default selection when none) plus every path named in ``units``
    (wildcard keys ``'*.<name>'`` select nothing; they apply to the selected paths)."""
    paths = select_paths(system, variables)
    named = [k for k in units or {} if not is_wildcard(k)]
    if named:
        paths += [p for p in select_paths(system, named) if p not in paths]
    return paths


def unit_targets(
    result_units: Mapping[str, str],
    references: Mapping[str, str],
    units: Mapping[str, str] | None,
    paths: Iterable[str] = (),
) -> dict[str, tuple[str, str | None, str]]:
    """``(unit, reference, requested unit string)`` per path for unit conversions.

    A wildcard key ``'*.<name>'`` applies to every path in ``paths`` ending in ``.<name>``;
    an explicit path overrides it. Shared by the CLI ``--units`` option and the MCP
    ``units`` argument.

    Raises:
        UnknownVariableError: An unknown path, or a wildcard that matches no path.
    """
    out: dict[str, tuple[str, str | None, str]] = {}
    selected = list(paths)
    explicit = {k: v for k, v in (units or {}).items() if not is_wildcard(k)}
    for key, target in (units or {}).items():
        if not is_wildcard(key):
            continue
        matched = [p for p in selected if p.endswith(key[1:]) and p not in explicit]
        if not matched and not any(p.endswith(key[1:]) for p in explicit):
            names = sorted({"*." + p.rsplit(".", 1)[-1] for p in selected})
            raise UnknownVariableError(
                f"units: '{key}' matches no reported variable. " + format_choices(key, names)
            )
        for p in matched:
            base, ref = split_pressure_reference(target)
            out[p] = (base, ref or references.get(p), target)
    for path, target in explicit.items():
        if path not in result_units:
            raise UnknownVariableError(
                f"units: unknown variable '{path}'. " + format_choices(path, result_units)
            )
        base, ref = split_pressure_reference(target)
        out[path] = (base, ref or references.get(path), target)
    return out


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
    return _bounds(spec.minimum, spec.maximum)


def _bounds(minimum: float | None, maximum: float | None) -> str:
    lo = "-inf" if minimum is None else f"{minimum:g}"
    hi = "inf" if maximum is None else f"{maximum:g}"
    return f"[{lo}, {hi}]"


def _column_limits(spec: VariableSpec) -> str:
    """Limits of a table parameter's columns, e.g. ``flow [0, inf], head [0, inf]``."""
    return ", ".join(
        f"{c.name} {_bounds(c.minimum, c.maximum)}"
        for c in spec.columns
        if c.minimum is not None or c.maximum is not None
    )


def _variable_rows(group: dict[str, VariableSpec], with_default: bool) -> list[list[Any]]:
    rows = []
    for spec in group.values():
        unit = _unit(spec.unit, spec.reference) if spec.type != "table" else "table"
        if spec.columns:
            unit = "table: " + ", ".join(f"{c.name} [{c.unit}]" for c in spec.columns)
        row: list[Any] = [spec.name, unit]
        if with_default:
            row += [spec.default, _column_limits(spec) if spec.columns else _limits(spec)]
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


def _units_args(values: Sequence[str] | None) -> dict[str, str]:
    """``--units PATTERN=UNIT`` options as a mapping (later options win)."""
    out: dict[str, str] = {}
    for item in values or []:
        key, sep, unit = item.partition("=")
        if not sep or not key.strip() or not unit.strip():
            raise InvalidValueError(
                f"--units expects PATTERN=UNIT, e.g. --units '*.volume_flow=m3/h' or --units "
                f"'pump.outlet.p=bar absolute'; got '{item}'."
            )
        out[key.strip()] = unit.strip()
    return out


def _converted_solve(
    result: wp.SolveResult, targets: Mapping[str, tuple[str, str | None, str]]
) -> wp.SolveResult:
    """A copy of ``result`` with the ``targets`` paths converted to their requested units."""
    if not targets:
        return result
    values, units, refs = dict(result.values), dict(result.units), dict(result.references)
    for path, (unit, ref, target) in targets.items():
        values[path] = result.get(path, target)
        units[path] = unit
        if ref:
            refs[path] = ref
        else:
            refs.pop(path, None)
    return dataclasses.replace(result, values=values, units=units, references=refs)


def _converted_simulation(
    sim: wp.SimulationResult, targets: Mapping[str, tuple[str, str | None, str]]
) -> wp.SimulationResult:
    """A copy of ``sim`` (and its final result) with ``targets`` converted."""
    if not targets:
        return sim
    series, units, refs = dict(sim.series), dict(sim.units), dict(sim.references)
    for path, (unit, ref, target) in targets.items():
        series[path] = sim.get(path, target)
        units[path] = unit
        if ref:
            refs[path] = ref
        else:
            refs.pop(path, None)
    final = _converted_solve(sim.final, {p: t for p, t in targets.items() if p in sim.final})
    return dataclasses.replace(sim, series=series, units=units, references=refs, final=final)


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
    units = _units_args(args.units)
    result = system.solve()
    if args.json and not requested and not units:
        _print_json(result.to_dict())
        return EXIT_OK
    if args.json and not requested:
        paths = list(result.values)  # --json reports everything; --units converts some
    else:
        paths = select_paths_with_units(system, requested, units)
    result = _converted_solve(result, unit_targets(result.units, result.references, units, paths))
    if args.json:
        _print_json(result.to_dict(paths))
        return EXIT_OK
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
    if duration is None:
        raise InvalidValueError(
            f"{args.system} has no 'simulation' block; pass --duration, e.g. worldparts "
            f"simulate {args.system} --duration '10 min' --step '1 s', or add "
            "'simulation: {duration: 10 min, step: 1 s}' to the document."
        )
    step = args.step if args.step is not None else block.get("step")
    requested = _var_args(args.var)
    units = _units_args(args.units)
    record = None  # --json without --var records everything
    if requested or not args.json:
        record = select_paths_with_units(system, requested, units)
    sim = system.simulate(duration, step, block.get("events"), variables=record)
    selected = record if record is not None else list(sim.series)
    sim = _converted_simulation(sim, unit_targets(sim.units, sim.references, units, selected))
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


def _cmd_export(args: argparse.Namespace) -> int:
    """Export a system document to another host (``wntr_inp``: an EPANET .inp via WNTR).

    The adapter (and ``wntr``) is imported lazily, so the other commands never pay for it.
    """
    from worldparts.adapters import MissingDependencyError, wntr_available

    if not wntr_available():
        raise MissingDependencyError(
            "Target 'wntr_inp' needs the optional 'wntr' package: uv add wntr (or pip "
            "install wntr), or reinstall with the extra: "
            'uv add "worldparts[wntr] @ git+https://github.com/raimondasl/worldparts"'
        )
    from worldparts.adapters.wntr_adapter import compare_with_wntr, export_inp

    system = _load_system(args.system)
    text = export_inp(system, args.output)
    report = compare_with_wntr(system) if args.compare else None
    if args.json:
        data: dict[str, Any] = {
            "system": system.name,
            "target": args.target,
            "file": str(args.output) if args.output else None,
            "inp": text,
        }
        if report is not None:
            data["comparison"] = report.to_dict()
        _print_json(data)
        return EXIT_OK
    if args.output:
        print(
            f"Wrote {args.output}: EPANET .inp of system '{system.name}' "
            f"({len(text.splitlines())} lines, flow units CMH, Darcy-Weisbach head loss)."
        )
    else:
        sys.stdout.write(text)
        sys.stdout.flush()
    if report is not None:
        # With the .inp on stdout, the comparison goes to stderr so the file stays clean.
        stream = sys.stdout if args.output else sys.stderr
        print(report.summary(), file=stream)
        if not args.output:
            print(
                "\n(The .inp text is on stdout, above this report; use -o FILE to save it "
                "and keep only the report on screen.)",
                file=sys.stderr,
            )
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
    units_help = (
        "Report a variable in another unit: PATH=UNIT, or *.NAME=UNIT for every selected "
        "variable called NAME, e.g. --units '*.volume_flow=m3/h' or --units "
        "'pump.outlet.p=bar absolute'. Repeatable; explicit paths win over wildcards, and a "
        "named path is added to the selection."
    )
    p = add("solve", _cmd_solve, "Solve a system document for its steady operating point.")
    p.add_argument("system", help="System document (YAML or JSON).")
    p.add_argument("--var", action="append", metavar="PATH", help=var_help)
    p.add_argument("--units", action="append", metavar="PATTERN=UNIT", help=units_help)
    json_flag(p)

    p = add("simulate", _cmd_simulate, "Simulate a system document over time.")
    p.add_argument("system", help="System document (YAML or JSON).")
    p.add_argument(
        "--duration",
        help="e.g. '10 min' (default: the document's simulation block, whose events always apply).",
    )
    p.add_argument("--step", help="e.g. '1 s' (default: the document's, else 1 s).")
    p.add_argument("--var", action="append", metavar="PATH", help=var_help)
    p.add_argument("--units", action="append", metavar="PATTERN=UNIT", help=units_help)
    p.add_argument(
        "--max-points",
        type=int,
        default=200,
        help="Downsample JSON series to at most this many samples (default 200; 0 keeps all).",
    )
    json_flag(p)

    p = add(
        "export",
        _cmd_export,
        "Export a system document to another simulation host (needs the 'wntr' extra).",
    )
    p.add_argument("system", help="System document (YAML or JSON).")
    p.add_argument(
        "--target",
        required=True,
        choices=["wntr_inp"],
        help="wntr_inp: an EPANET .inp file written through WNTR (flow units CMH).",
    )
    p.add_argument("-o", "--output", help="Write to this file (default: print the text).")
    p.add_argument(
        "--compare",
        action="store_true",
        help="Also solve the system in EPANET and report flow and pressure differences "
        "from the worldparts solution.",
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
