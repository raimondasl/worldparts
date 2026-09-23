"""MCP server: the catalogue and the composition API as a typed tool surface (design 9).

Built on the MCP Python SDK v2 (``from mcp.server import MCPServer``). Every tool returns a
pydantic model, so clients receive ``structuredContent`` validated against the tool's
``outputSchema`` (plus the same JSON as a text block for clients that only read text).
Failures are raised as :class:`mcp.server.mcpserver.exceptions.ToolError`, which the SDK
returns as an ``isError`` result whose text is the worldparts error message (prefixed with
the error code), so the agent sees the offending path and the valid alternatives.

Systems live in an in-process :class:`SystemStore` keyed by a short ``system_id``
(``s1``, ``s2``, ...). Manifests are exposed as resources at
``worldparts://components/{id}`` (full id or short alias).

Run it with ``worldparts mcp`` (stdio). Tools: ``list_components``, ``describe_component``,
``create_system``, ``add_component``, ``remove_component``, ``set_values``, ``connect``,
``disconnect``, ``check_system``, ``solve``, ``solve_for``, ``simulate``,
``list_variables``, ``get_system``, ``load_system`` and ``run_contracts``.

The tool list is kept small for the agent's context: the generated JSON schemas are
compacted after registration (:func:`compact_schema`: no ``title`` keys, no ``null`` branch
for output fields that are left out when empty) and tool descriptions are dedented.
``describe_component`` is brief by default; ``detail='full'`` adds the scenario systems,
contract rules, implementation notes and provenance.

Extension point: a registrar is a function ``(server, store) -> None`` that declares tools
with ``server.tool()`` and reads systems with ``store.get(system_id)``; append it to
:data:`EXTRA_TOOL_REGISTRARS` (or pass it to :func:`create_server`) and it runs after the
built-in tools are registered. The WNTR adapter uses it: when the optional ``wntr`` package
is installed, :func:`register_wntr_tools` adds ``export_system(system_id, target)`` (design
9, target ``wntr_inp``) and ``compare_with_wntr(system_id)``. ``wntr`` itself is imported
only when one of them is called.
"""

from __future__ import annotations

import functools
import inspect
import itertools
import math
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Annotated, Any, Literal, ParamSpec, TypeVar

import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.server.mcpserver.resources import TextResource
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from scipy.optimize import brentq

import worldparts as wp
from worldparts.adapters import wntr_available
from worldparts.catalog import Catalog, default_catalog
from worldparts.cli import select_paths_with_units as _selection
from worldparts.cli import unit_targets as _convert_request
from worldparts.errors import UnknownComponentError, WorldpartsError, format_choices
from worldparts.manifest import Manifest, VariableSpec, load_yaml
from worldparts.units import parse_duration, parse_value

__all__ = [
    "EXTRA_TOOL_REGISTRARS",
    "INSTRUCTIONS",
    "RESOURCE_PREFIX",
    "SystemStore",
    "UnknownSystemError",
    "create_server",
    "main",
    "register_wntr_tools",
]

RESOURCE_PREFIX = "worldparts://components/"

INSTRUCTIONS = """\
worldparts gives you tested physical component models (supplies, drains, pipes, valves, \
pumps, tanks, ...) that you compose into a system and solve, instead of writing physics code.
Workflow: list_components (search the catalogue) -> describe_component (ports, parameters \
with units and limits, observables, modes, warning codes) -> create_system (returns a \
system_id) -> add_component (one instance per part) -> connect (port paths \
'<instance>.<port>'; several ports on one node form a junction) -> check_system (fix every \
error) -> solve (steady state) or simulate (time series with events). Use set_values to \
change inputs or parameters, list_variables to see every path, get_system/load_system to \
save and restore the system document.
Values: a plain number is in the variable's declared unit (see describe_component); a \
string may carry its own unit ('3.5 bar', '12 L/min', '55 degC'). Pressures are gauge \
(relative to atmosphere) unless marked otherwise ('2 bar absolute' is accepted); port \
pressures are reported in bar gauge. Results carry their unit and, for pressures, their \
reference (gauge, absolute or difference); a temperature difference such as a rise in K \
carries reference 'difference' and converts by scale only (35 K = 35 degC). Read the \
warnings and modes in every result: \
they flag operation outside a model's validity envelope.
Flow units differ by part (pipes, valves, drains, supplies: L/min; pumps, tanks, filters, \
UV reactors: m3/h); pass units={'*.volume_flow': 'm3/h'} to solve or simulate to report \
every matching path in one unit. solve_for finds the value of one input or parameter that \
gives a target result (e.g. the pump speed for 15 m3/h). describe_component is brief by \
default; detail='full' adds scenario systems, contract rules and provenance.
Systems live only as long as this server process: keep a system with get_system and \
restore it in a new session with load_system.\
"""

_P = ParamSpec("_P")
_R = TypeVar("_R")


class UnknownSystemError(WorldpartsError):
    """A ``system_id`` does not name a system in the store."""

    code = "unknown_system"


# ----------------------------------------------------------------------------------------
# output models
# ----------------------------------------------------------------------------------------
def _is_none(value: Any) -> bool:
    return value is None


def _opt(description: str) -> Any:
    """An optional field that is left out of the output when it is None."""
    return Field(None, description=description, exclude_if=_is_none)


PressureReference = Literal["gauge", "absolute", "difference"]
_REF_DOC = (
    "Reference: gauge, absolute or difference for pressures; difference for a temperature "
    "difference (converts by scale only). Absent otherwise."
)


class Quantity(BaseModel):
    """A numeric result with its unit (and pressure reference for pressures)."""

    value: float | None = Field(description="Value in `unit`; null where undefined.")
    unit: str = Field(description="Unit of the value.")
    reference: PressureReference | None = _opt(_REF_DOC)


class Value(BaseModel):
    """A parameter, input or state value in its declared unit."""

    value: Any = Field(description="Number, string, boolean or table rows.")
    unit: str | None = _opt("Declared unit (absent for strings, booleans and tables).")
    reference: PressureReference | None = _opt(_REF_DOC)


class IssueOut(BaseModel):
    """A structural issue found by the pre-flight check."""

    severity: Literal["error", "warning"]
    code: str = Field(description="Stable identifier, e.g. 'no_pressure_reference'.")
    where: str = Field(description="The offending component, port, variable or connection.")
    message: str


class WarningOut(BaseModel):
    """A component warning (validity envelope or component code)."""

    component: str
    code: str
    severity: Literal["info", "warning"]
    message: str
    time: float | None = _opt("Simulation time in s of the first occurrence.")
    last_time: float | None = _opt("Simulation time in s of the last occurrence.")
    active_at_end: bool | None = _opt("Simulation: still raised at the end time.")


class KeyParameter(BaseModel):
    """A parameter shown in listings."""

    name: str
    unit: str | None = _opt("Declared unit.")
    columns: list[str] | None = _opt("Table columns as 'name [unit]'; rows follow this order.")
    default: Any = Field(description="Default value in the declared unit.")


class ComponentSummary(BaseModel):
    """A catalogue entry."""

    id: str = Field(description="Full component id.")
    alias: str = Field(description="Short alias accepted wherever an id is.")
    name: str
    summary: str
    ports: list[str]
    key_parameters: list[KeyParameter]
    fidelity: str
    tags: list[str]


class ComponentList(BaseModel):
    """Result of list_components."""

    count: int
    components: list[ComponentSummary]
    note: str | None = _opt("Hint when nothing matched.")


class PortDoc(BaseModel):
    """A port of a component type."""

    name: str
    type: str
    medium: str
    description: str


class ColumnDoc(BaseModel):
    """A column of a table parameter."""

    name: str
    unit: str
    description: str | None = _opt("What the column holds.")
    reference: PressureReference | None = _opt(_REF_DOC)
    minimum: float | None = _opt("Lower limit.")
    maximum: float | None = _opt("Upper limit.")


class VariableDoc(BaseModel):
    """A parameter, input, state or observable of a component type."""

    name: str
    description: str
    type: str = Field(description="number, integer, string, boolean or table.")
    unit: str | None = _opt("Declared unit; plain numbers are interpreted in it.")
    reference: PressureReference | None = _opt(_REF_DOC + " Pressures default to gauge.")
    quantity: str | None = _opt(
        "'temperature_difference' for a temperature difference (e.g. a rise in K)."
    )
    default: Any = _opt("Default in the declared unit.")
    minimum: float | None = _opt("Hard lower limit (values below are rejected).")
    maximum: float | None = _opt("Hard upper limit.")
    enum: list[str] | None = _opt("Allowed strings.")
    columns: list[ColumnDoc] | None = _opt("Table columns.")
    min_rows: int | None = _opt("Minimum table rows.")
    steady: str | None = _opt("For states: 'settle' (solve sets equilibrium) or 'hold'.")


class ModeDoc(BaseModel):
    """A discrete mode; the first mode whose condition holds is reported."""

    name: str
    condition: str
    description: str


class WarningDoc(BaseModel):
    """A warning code a component can emit."""

    code: str
    severity: str
    source: Literal["envelope", "component"] = Field(
        description="'envelope': raised when `condition` holds; 'component': raised by code."
    )
    condition: str | None = _opt("Envelope condition over display-unit values.")
    message: str


class ScenarioDoc(BaseModel):
    """A canonical scenario shipped with the manifest."""

    id: str
    description: str | None = _opt("detail='full': what the scenario checks and why.")
    simulated: bool
    system: dict[str, Any] | None = _opt(
        "detail='full': the test system (components and connections); a usable template."
    )
    simulate: dict[str, Any] | None = _opt("Simulation settings (duration, step, events).")
    expect: list[dict[str, Any]] | None = _opt(
        "detail='full': expected results (values with tolerances or ranges, modes, warnings)."
    )


class ContractDoc(BaseModel):
    """A behavioural contract shipped with the manifest."""

    id: str
    description: str | None = _opt("detail='full': what the contract asserts.")
    scenario: str
    check: str = Field(description="monotonic, bounds, equal or warning_iff.")
    sweep: str | None = _opt("Swept variable.")
    sweep_spec: dict[str, Any] | None = _opt(
        "detail='full': the full sweep (variable and range or values)."
    )
    rule: dict[str, Any] | None = _opt(
        "detail='full': the check's details (variable, direction, left/right, min/max, "
        "condition, tolerances)."
    )


class ComponentDescription(BaseModel):
    """Result of describe_component."""

    id: str
    alias: str
    version: str
    name: str
    summary: str
    description: str
    tags: list[str]
    fidelity: str
    assumptions: list[str]
    ports: list[PortDoc]
    parameters: list[VariableDoc]
    inputs: list[VariableDoc]
    states: list[VariableDoc]
    observables: list[VariableDoc]
    modes: list[ModeDoc]
    warnings: list[WarningDoc]
    scenarios: list[ScenarioDoc]
    contracts: list[ContractDoc]
    implementations: dict[str, Any] = Field(
        description="Bindings per host; brief: the class or element name only."
    )
    provenance: dict[str, Any] | None = _opt("detail='full': sources, data and licences.")
    classification: dict[str, Any] | None = _opt("Cross-walk to IFC, Brick, ...")
    detail: Literal["brief", "full"]
    resource_uri: str = Field(description="URI of the full manifest resource.")


class SystemSummary(BaseModel):
    """A system's composition."""

    system_id: str
    name: str
    description: str | None = _opt("Free text.")
    components: dict[str, str] = Field(description="Instance name to component id.")
    connections: list[list[str]] = Field(description="Connected port pairs.")
    unconnected_ports: list[str] = Field(description="Ports not yet connected (capped).")
    issues: list[IssueOut] | None = _opt("Pre-flight issues (load_system only).")


class ComponentInstance(BaseModel):
    """Result of add_component: the resolved values of the new instance."""

    system_id: str
    name: str
    type: str = Field(description="Full component id.")
    ports: list[str] = Field(description="Port paths of the instance.")
    parameters: dict[str, Value]
    inputs: dict[str, Value]
    states: dict[str, Value]
    issues: list[IssueOut] = Field(description="Pre-flight issues about this instance.")
    hint: str | None = _opt("How the issues of a new, unwired instance clear.")


class SetValuesResult(BaseModel):
    """Result of set_values: the values now in effect."""

    system_id: str
    values: dict[str, Value]


class CheckResult(BaseModel):
    """Result of check_system."""

    system_id: str
    ok: bool = Field(description="True when there is no error (solve will run).")
    errors: int
    warnings: int
    issues: list[IssueOut]


class SolveOutput(BaseModel):
    """Result of solve: a steady operating point."""

    system_id: str
    converged: bool
    iterations: int
    max_residual: float
    values: dict[str, Quantity] = Field(description="Selected variables with units.")
    modes: dict[str, str | None] = Field(description="Instance name to mode.")
    warnings: list[WarningOut]
    issues: list[IssueOut] = Field(description="Non-fatal pre-flight issues.")


class SolveForOutput(SolveOutput):
    """Result of solve_for: the value found and the operating point there."""

    vary: str = Field(description="The varied path; it is left at the value found.")
    found: Value = Field(description="Value of `vary` that meets the target.")
    target: str = Field(description="The target path.")
    target_value: Quantity = Field(description="The requested target value.")
    achieved: Quantity = Field(description="The target path's value at the solution.")
    evaluations: int = Field(description="Number of steady solves used.")


class SeriesOut(BaseModel):
    """One recorded variable of a simulation."""

    unit: str
    reference: PressureReference | None = _opt(_REF_DOC)
    min: float | None = Field(description="Minimum over the full series.")
    max: float | None = Field(description="Maximum over the full series.")
    final: float | None = Field(description="Value at the end time.")
    values: list[float | None] = Field(description="Downsampled values, aligned with `time`.")


class ModeChangeOut(BaseModel):
    """A component entering a mode."""

    time: float
    component: str
    mode: str | None


class SimulateOutput(BaseModel):
    """Result of simulate."""

    system_id: str
    duration: float = Field(description="End time in s.")
    samples: int = Field(description="Number of samples computed (before downsampling).")
    time: list[float] = Field(description="Downsampled sample times in s.")
    variables: dict[str, SeriesOut]
    warnings: list[WarningOut] = Field(
        description="Each warning once: first time and message, last time, active at end."
    )
    mode_changes: list[ModeChangeOut] = Field(description="Initial modes and every change.")
    final_modes: dict[str, str | None]
    issues: list[IssueOut] = Field(description="Non-fatal pre-flight issues.")


class VariableOut(BaseModel):
    """A variable path of a system."""

    path: str
    kind: Literal["parameter", "input", "state", "observable", "port"]
    unit: str | None = _opt("Display unit.")
    reference: PressureReference | None = _opt(_REF_DOC)
    quantity: str | None = _opt("'temperature_difference' for a temperature difference.")
    type: str
    minimum: float | None = _opt("Hard lower limit.")
    maximum: float | None = _opt("Hard upper limit.")
    settable: bool = Field(description="Accepted by set_values.")
    reported: bool = Field(description="Part of solve/simulate results.")
    description: str


class VariableList(BaseModel):
    """Result of list_variables."""

    system_id: str
    variables: list[VariableOut]


class SystemDocument(BaseModel):
    """Result of get_system."""

    system_id: str
    document: dict[str, Any] = Field(description="System document (design 6.3).")


class OutcomeOut(BaseModel):
    """One scenario or contract outcome."""

    id: str
    passed: bool
    points: int
    failures: list[str]


class ContractReport(BaseModel):
    """Result of run_contracts."""

    component: str
    passed: bool
    scenarios: list[OutcomeOut]
    contracts: list[OutcomeOut]


class Event(BaseModel):
    """A simulation event: set values at a time, or ramp them linearly from that time."""

    at: float | str = Field(description="Time: seconds or a string such as '60 s', '2 min'.")
    set: dict[str, Any] | None = Field(
        None, description="Paths to values, e.g. {'valve.opening': 0}."
    )
    ramp: dict[str, list[float | str]] | None = Field(
        None,
        description="Instead of set: paths to [start, end] values, changed linearly from "
        "`at` over `over` (applied at every step), e.g. {'filter.clogging': [0, 0.8]}.",
    )
    over: float | str | None = Field(None, description="Ramp duration, e.g. '30 min'.")


# Argument types shared by several tools (module level so the SDK can resolve them).
SystemId = Annotated[str, Field(description="Id returned by create_system or load_system.")]
Variables = Annotated[
    list[str] | None,
    Field(
        description="Paths to report ('valve.volume_flow', 'valve.port_a.p'), instance "
        "names (all their variables) or '*'. Default: observables, states and port "
        "pressures."
    ),
]
Units = Annotated[
    dict[str, str] | None,
    Field(
        description="Optional unit per path, e.g. {'valve.volume_flow': 'L/s', "
        "'mains.port.p': 'bar absolute'}. A key '*.<name>' applies to every reported path "
        "ending in '.<name>', e.g. {'*.volume_flow': 'm3/h'}; explicit paths win."
    ),
]


# ----------------------------------------------------------------------------------------
# store
# ----------------------------------------------------------------------------------------
class SystemStore:
    """In-process store of systems keyed by short ids (``s1``, ``s2``, ...).

    Tool calls run on worker threads; :attr:`lock` serialises them.
    """

    def __init__(self, catalog: Catalog | None = None) -> None:
        self.catalog = catalog if catalog is not None else default_catalog()
        self.lock = threading.RLock()
        self._systems: dict[str, wp.System] = {}
        self._ids = itertools.count(1)

    def __len__(self) -> int:
        return len(self._systems)

    def ids(self) -> list[str]:
        """Stored system ids in creation order."""
        return list(self._systems)

    def add(self, system: wp.System) -> str:
        """Store ``system`` and return its new id."""
        system_id = f"s{next(self._ids)}"
        self._systems[system_id] = system
        return system_id

    def create(self, name: str, description: str | None = None) -> str:
        """Create an empty system and return its id."""
        return self.add(wp.System(name, description, self.catalog))

    def get(self, system_id: str) -> wp.System:
        """The system with ``system_id``.

        Raises:
            UnknownSystemError: Listing the valid ids.
        """
        system = self._systems.get(system_id)
        if system is None:
            known = self.ids()
            hint = (
                format_choices(system_id, known)
                if known
                else "No systems exist yet; call create_system or load_system first."
            )
            raise UnknownSystemError(f"Unknown system_id '{system_id}'. {hint}")
        return system


#: Functions ``(server, store) -> None`` that register extra tools (e.g. export_system).
EXTRA_TOOL_REGISTRARS: list[Callable[[MCPServer, SystemStore], None]] = []


# ----------------------------------------------------------------------------------------
# conversion helpers
# ----------------------------------------------------------------------------------------
def _round(x: Any, digits: int = 6) -> float | None:
    """Round to ``digits`` significant digits; None for None and non-finite values."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return float(f"{x:.{digits}g}")


def _issue(i: wp.Issue) -> IssueOut:
    return IssueOut(severity=i.severity, code=i.code, where=i.where, message=i.message)  # type: ignore[arg-type]


def _concerns(issue: wp.Issue, instance: str) -> bool:
    """True when an issue's ``where`` names ``instance`` or one of its ports."""
    parts = issue.where.replace(" -- ", ", ").split(", ")
    return any(p == instance or p.startswith(instance + ".") for p in parts)


def _warning(w: wp.ComponentWarning) -> WarningOut:
    return WarningOut(
        component=w.component,
        code=w.code,
        severity=w.severity,  # type: ignore[arg-type]
        message=w.message,
        time=w.time,
        last_time=w.last_time,
        active_at_end=w.active_at_end,
    )


def _value(spec: VariableSpec, value: Any) -> Value:
    if spec.is_numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
        value = _round(value, 10)
    return Value(value=value, unit=spec.unit, reference=spec.reference)  # type: ignore[arg-type]


def _instance_values(system: wp.System, name: str, group: Mapping[str, VariableSpec]) -> dict:
    return {n: _value(s, system.get(f"{name}.{n}")) for n, s in group.items()}


def _summary(
    store_id: str, system: wp.System, issues: list[wp.Issue] | None = None
) -> SystemSummary:
    connected = {p for c in system.connections for p in c}
    unconnected = []
    for inst in system.components:
        try:
            ports = system.manifest(inst).ports
        except WorldpartsError:
            continue  # unknown component type; reported by check
        unconnected.extend(f"{inst}.{p}" for p in ports if f"{inst}.{p}" not in connected)
    return SystemSummary(
        system_id=store_id,
        name=system.name,
        description=system.description,
        components=system.components,
        connections=[list(c) for c in system.connections],
        unconnected_ports=unconnected,
        issues=[_issue(i) for i in issues] if issues is not None else None,
    )


def _variable_doc(spec: VariableSpec) -> VariableDoc:
    return VariableDoc(
        name=spec.name,
        description=spec.description,
        type=spec.type,
        unit=spec.unit,
        reference=spec.reference,  # type: ignore[arg-type]
        quantity=spec.quantity,
        default=spec.default,
        minimum=spec.minimum,
        maximum=spec.maximum,
        enum=list(spec.enum) if spec.enum else None,
        columns=[
            ColumnDoc(
                name=c.name,
                unit=c.unit,
                description=c.description or None,
                reference=c.converter.reference,  # type: ignore[arg-type]
                minimum=c.minimum,
                maximum=c.maximum,
            )
            for c in spec.columns
        ]
        if spec.columns
        else None,
        min_rows=spec.min_rows,
        steady=spec.steady,
    )


def _binding_name(binding: Any) -> Any:
    """The class, element or import path of an implementation binding (no notes)."""
    if isinstance(binding, Mapping):
        for key in ("python", "class", "element", "entity"):
            if key in binding:
                return binding[key]
        return {k: v for k, v in binding.items() if k != "notes"}
    return binding


def describe_manifest(
    m: Manifest, detail: Literal["brief", "full"] = "full"
) -> ComponentDescription:
    """Everything an agent needs to use a component type.

    ``detail='brief'`` keeps what is needed to use the part (description, ports,
    parameters, inputs, states, observables, modes, warnings) and lists the scenarios and
    contracts by id only (with the contract's check type and swept variable), the
    implementation bindings by name only and no provenance; ``'full'`` adds the scenario
    and contract descriptions, scenario systems and expectations, contract rules and
    sweeps, binding notes and provenance.
    """
    full = detail == "full"
    d = m.data
    envelope_codes = {w.code for w in m.envelope}
    return ComponentDescription(
        id=m.id,
        alias=m.alias,
        version=m.version,
        name=m.name,
        summary=m.summary,
        description=str(d.get("description", "")).strip(),
        tags=list(d.get("tags", [])),
        fidelity=d["fidelity"]["level"],
        assumptions=list(d["fidelity"].get("assumptions", [])),
        ports=[
            PortDoc(name=p.name, type=p.type, medium=p.medium, description=p.description)
            for p in m.ports.values()
        ],
        parameters=[_variable_doc(s) for s in m.parameters.values()],
        inputs=[_variable_doc(s) for s in m.inputs.values()],
        states=[_variable_doc(s) for s in m.states.values()],
        observables=[_variable_doc(s) for s in m.observables.values()],
        modes=[
            ModeDoc(name=x.name, condition=x.condition, description=x.description) for x in m.modes
        ],
        warnings=[
            WarningDoc(
                code=w.code,
                severity=w.severity,
                source="envelope" if w.code in envelope_codes else "component",
                condition=w.condition,
                message=w.description,
            )
            for w in m.warnings.values()
        ],
        scenarios=[
            ScenarioDoc(
                id=str(s["id"]),
                description=str(s.get("description", "")) if full else None,
                simulated=bool(s.get("simulate")),
                system=dict(s.get("system", {})) if full else None,
                simulate=dict(s["simulate"]) if full and s.get("simulate") else None,
                expect=[dict(e) for e in s.get("expect", [])] if full else None,
            )
            for s in m.scenarios
        ],
        contracts=[
            ContractDoc(
                id=str(c["id"]),
                description=str(c.get("description", "")) if full else None,
                scenario=str(c.get("scenario", "")),
                check=str(c["check"]["type"]),
                sweep=(c.get("sweep") or {}).get("variable"),
                sweep_spec=dict(c["sweep"]) if full and c.get("sweep") else None,
                rule={k: v for k, v in c["check"].items() if k != "type"} if full else None,
            )
            for c in m.contracts
        ],
        implementations=dict(d.get("implementations", {}))
        if full
        else {k: _binding_name(v) for k, v in d.get("implementations", {}).items()},
        provenance=dict(d.get("provenance", {})) if full else None,
        classification=d.get("classification"),
        detail=detail,
        resource_uri=RESOURCE_PREFIX + m.id,
    )


def _key_parameters(m: Manifest) -> list[KeyParameter]:
    """The listing's key parameters, with 'name [unit]' columns for tables."""
    out = []
    for k in m.describe()["key_parameters"]:
        spec = m.parameters.get(k["name"])
        columns = (
            [f"{c.name} [{c.unit}]" for c in spec.columns]
            if spec is not None and spec.columns
            else None
        )
        out.append(KeyParameter(**k, columns=columns))
    return out


def compact_schema(schema: Any, output: bool = False) -> Any:
    """A JSON schema without the noise pydantic adds, for a smaller tools/list.

    Drops every ``title`` keyword (property names are kept). With ``output`` true, an
    optional property whose default is null and whose type is ``anyOf [X, null]`` becomes
    plain ``X``: the output models leave such fields out when they are empty (:func:`_opt`),
    so null never appears in the output. Output schemas also lose their ``description`` and
    ``default`` keywords: they describe structure only, and what the results mean is in the
    tool descriptions (each tool's output schema repeated the same field documentation,
    which made tools/list several times larger). Validation of what the tools return is
    unchanged.
    """
    if isinstance(schema, list):
        return [compact_schema(s, output) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    required = set(schema.get("required", []))
    for key, value in schema.items():
        if key == "title" and isinstance(value, str):
            continue
        if output and key == "description" and isinstance(value, str):
            continue
        if output and key == "default":
            continue
        if key in ("properties", "$defs") and isinstance(value, dict):
            props = {}
            for name, sub in value.items():
                if key == "properties" and output and name not in required:
                    sub = _drop_null_branch(sub)
                props[name] = compact_schema(sub, output)
            out[key] = props
        else:
            out[key] = compact_schema(value, output)
    return out


def _drop_null_branch(sub: Any) -> Any:
    if not isinstance(sub, dict) or "default" not in sub or sub["default"] is not None:
        return sub
    branches = sub.get("anyOf")
    if not isinstance(branches, list):
        return sub
    kept = [b for b in branches if b != {"type": "null"}]
    if len(kept) == len(branches) or not kept:
        return sub
    rest = {k: v for k, v in sub.items() if k not in ("anyOf", "default")}
    if len(kept) == 1 and isinstance(kept[0], dict):
        return {**kept[0], **rest}
    return {"anyOf": kept, **rest}


def _compact_tools(server: MCPServer) -> None:
    """Compact every registered tool's schemas and dedent its description."""
    for tool in server._tool_manager.list_tools():
        tool.description = inspect.cleandoc(tool.description or "")
        tool.parameters = compact_schema(tool.parameters)
        meta = tool.fn_metadata
        if meta.output_schema is not None:
            meta.output_schema = compact_schema(meta.output_schema, output=True)


def manifest_text(m: Manifest) -> str:
    """The manifest as YAML: the source file when there is one, else a dump."""
    if m.path is not None:
        try:
            return m.path.read_text(encoding="utf-8")
        except OSError:
            pass
    return yaml.safe_dump(m.data, sort_keys=False, allow_unicode=True)


# ----------------------------------------------------------------------------------------
# server
# ----------------------------------------------------------------------------------------
def create_server(
    catalog: Catalog | None = None,
    registrars: Iterable[Callable[[MCPServer, SystemStore], None]] | None = None,
) -> MCPServer:
    """Build the worldparts MCP server with a fresh, empty system store.

    Args:
        catalog: Catalogue to serve (default: the package catalogue).
        registrars: Extra tool registrars (default: :data:`EXTRA_TOOL_REGISTRARS`), e.g.
            the future ``export_system`` tool of the WNTR adapter.

    Returns:
        The server; its store is available as ``server.store``.
    """
    store = SystemStore(catalog)
    cat = store.catalog
    server = MCPServer(
        name="worldparts",
        title="worldparts",
        description="Agent-ready physical component models: discover, compose, solve.",
        instructions=INSTRUCTIONS,
        version=wp.__version__,
        log_level="WARNING",
    )
    server.store = store  # type: ignore[attr-defined]  # for tests and extensions

    def tool_call(fn: Callable[_P, _R]) -> Callable[_P, _R]:
        """Serialise calls on the store and turn worldparts errors into tool errors."""

        @functools.wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            try:
                with store.lock:
                    return fn(*args, **kwargs)
            except WorldpartsError as exc:
                raise ToolError(f"[{exc.code}] {exc}") from exc

        return wrapper

    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    editing = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
    removing = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

    # -- catalogue ------------------------------------------------------------------------
    @server.tool(annotations=read_only)
    @tool_call
    def list_components(
        query: Annotated[
            str | None,
            Field(description="Words that must all appear (id, name, summary, tags)."),
        ] = None,
    ) -> ComponentList:
        """Search the component catalogue.

        Returns id, alias, name, summary, ports, key parameters (with units and defaults),
        fidelity level and tags of every matching component. Call without a query to list
        everything, then describe_component for the details of one.
        """
        found = cat.search(query)
        items = []
        for m in found:
            info = m.describe()
            items.append(
                ComponentSummary(
                    id=m.id,
                    alias=m.alias,
                    name=m.name,
                    summary=m.summary,
                    ports=list(m.ports),
                    key_parameters=_key_parameters(m),
                    fidelity=info["fidelity"],
                    tags=list(m.data.get("tags", [])),
                )
            )
        note = None
        if not items and query:
            note = (
                f"No component matches '{query}' (every word must appear). Call "
                f"list_components without a query to see all {len(cat)} components: "
                + ", ".join(sorted(cat.aliases()))
                + "."
            )
        return ComponentList(count=len(items), components=items, note=note)

    @server.tool(annotations=read_only)
    @tool_call
    def describe_component(
        component: Annotated[str, Field(description="Full id or short alias, e.g. 'valve'.")],
        detail: Annotated[
            Literal["brief", "full"],
            Field(
                description="'brief' (default): what is needed to use the part. 'full' adds "
                "scenario systems and expectations (templates), contract rules and sweeps, "
                "binding notes and provenance (several times larger)."
            ),
        ] = "brief",
    ) -> ComponentDescription:
        """Describe a component type: everything needed to use it.

        Ports; parameters and inputs with units, defaults and hard limits (a table
        parameter lists its columns in row order); states and observables with units;
        modes (the first whose condition holds is reported); warning codes; scenario and
        contract ids; implementation bindings. Plain numbers you pass later are in these
        units; pressures are gauge unless the variable says otherwise, and a variable with
        quantity 'temperature_difference' converts by scale only. A state's `steady` is
        'settle' (solve sets its equilibrium, e.g. a valve position) or 'hold' (solve keeps
        it, e.g. a tank level). A warning's `source` is 'envelope' (raised when its
        `condition`, over display-unit values, holds) or 'component' (raised by the model's
        code; `message` says when).
        """
        return describe_manifest(cat.get(component), detail)

    @server.tool(annotations=read_only)
    @tool_call
    def run_contracts(
        component: Annotated[str, Field(description="Full id or short alias.")],
    ) -> ContractReport:
        """Run a component's scenarios and contracts and report pass or fail.

        Every manifest ships canonical scenarios with expected results and behavioural
        contracts (monotonicity, bounds, conservation, warning conditions); this runs them
        against the reference implementation.
        """
        report = wp.run_component(cat.get(component), cat)

        def out(kind: str) -> list[OutcomeOut]:
            return [
                OutcomeOut(id=o.id, passed=o.passed, points=o.points, failures=list(o.failures))
                for o in report.outcomes
                if o.kind == kind
            ]

        return ContractReport(
            component=report.component,
            passed=report.passed,
            scenarios=out("scenario"),
            contracts=out("contract"),
        )

    # -- composition ----------------------------------------------------------------------
    @server.tool(annotations=editing)
    @tool_call
    def create_system(
        name: Annotated[str, Field(description="System name, e.g. 'bathroom'.")],
        description: Annotated[str | None, Field(description="Optional free text.")] = None,
    ) -> SystemSummary:
        """Create a new empty system and return its system_id.

        Next: add_component for each part, then connect their ports.
        """
        system_id = store.create(name, description)
        return _summary(system_id, store.get(system_id))

    @server.tool(annotations=editing)
    @tool_call
    def add_component(
        system_id: SystemId,
        name: Annotated[
            str, Field(description="Instance name: letters, digits, underscores ('valve_1').")
        ],
        component: Annotated[str, Field(description="Component type: full id or alias.")],
        parameters: Annotated[
            dict[str, Any] | None,
            Field(
                description="Parameter values; plain numbers are in the declared unit, "
                "strings may carry units ('3.5 bar', '16 mm'). Omitted ones take defaults."
            ),
        ] = None,
        inputs: Annotated[
            dict[str, Any] | None,
            Field(description="Input (set-point) values, e.g. {'opening': 0.5}."),
        ] = None,
    ) -> ComponentInstance:
        """Add a component instance to a system.

        Returns the resolved parameters, inputs and states with units, the instance's port
        paths, and pre-flight issues about it (unconnected ports until you connect them).
        """
        system = store.get(system_id)
        system.add(name, component, parameters=parameters or {}, inputs=inputs or {})
        m = system.manifest(name)
        issues = [_issue(i) for i in system.check() if _concerns(i, name)]
        wiring = {"unconnected_port", "no_pressure_reference"}
        hint = (
            "Expected for a part that is not wired yet: unconnected_port and "
            "no_pressure_reference clear once its ports are connected to a network that "
            "contains a supply, drain or tank."
            if any(i.code in wiring for i in issues)
            else None
        )
        return ComponentInstance(
            system_id=system_id,
            name=name,
            type=m.id,
            ports=[f"{name}.{p}" for p in m.ports],
            parameters=_instance_values(system, name, m.parameters),
            inputs=_instance_values(system, name, m.inputs),
            states=_instance_values(system, name, m.states),
            issues=issues,
            hint=hint,
        )

    @server.tool(annotations=removing)
    @tool_call
    def remove_component(
        system_id: SystemId,
        name: Annotated[str, Field(description="Instance name to remove.")],
    ) -> SystemSummary:
        """Remove a component instance and every connection to it."""
        system = store.get(system_id)
        system.remove(name)
        return _summary(system_id, system)

    @server.tool(annotations=editing)
    @tool_call
    def set_values(
        system_id: SystemId,
        values: Annotated[
            dict[str, Any],
            Field(
                description="Paths to values, e.g. {'faucet.lift': 0.5, 'mains.pressure': "
                "'2 bar'}. Inputs, parameters and states are settable."
            ),
        ],
    ) -> SetValuesResult:
        """Set inputs, parameters or states by path (atomically: all or nothing).

        Plain numbers are in the declared unit; strings may carry units. Returns the values
        now in effect, in declared units.
        """
        system = store.get(system_id)
        system.set_values(values)
        out: dict[str, Value] = {}
        for path in values:
            inst, _, local = path.partition(".")
            spec = system.manifest(inst).variable(local)
            out[path] = _value(spec, system.get(path))
        return SetValuesResult(system_id=system_id, values=out)

    @server.tool(annotations=editing)
    @tool_call
    def connect(
        system_id: SystemId,
        a: Annotated[str, Field(description="Port path '<instance>.<port>', e.g. 'mains.port'.")],
        b: Annotated[str, Field(description="Port path, e.g. 'valve.port_a'.")],
    ) -> SystemSummary:
        """Connect two ports. Connecting several ports to one node forms a junction (tee).

        Errors name the offending port and list the instance's valid ports. Returns the
        connections and the ports still unconnected (unconnected ports are capped).
        """
        system = store.get(system_id)
        system.connect(a, b)
        return _summary(system_id, system)

    @server.tool(annotations=removing)
    @tool_call
    def disconnect(
        system_id: SystemId,
        a: Annotated[str, Field(description="Port path.")],
        b: Annotated[str, Field(description="Port path.")],
    ) -> SystemSummary:
        """Remove the direct connection between two ports."""
        system = store.get(system_id)
        system.disconnect(a, b)
        return _summary(system_id, system)

    @server.tool(annotations=read_only)
    @tool_call
    def check_system(system_id: SystemId) -> CheckResult:
        """Structural pre-flight with stable issue codes.

        Codes: unknown_component, unknown_port, incompatible_ports, self_connection,
        unconnected_port (warning), no_pressure_reference, boundary_short_circuit,
        parameter_out_of_range, invalid_value. solve and simulate refuse to run while any
        error remains.
        """
        issues = store.get(system_id).check()
        errors = sum(i.severity == "error" for i in issues)
        return CheckResult(
            system_id=system_id,
            ok=errors == 0,
            errors=errors,
            warnings=len(issues) - errors,
            issues=[_issue(i) for i in issues],
        )

    @server.tool(annotations=read_only)
    @tool_call
    def list_variables(
        system_id: SystemId,
        component: Annotated[
            str | None, Field(description="Only this instance's variables.")
        ] = None,
    ) -> VariableList:
        """Every variable path with kind, unit, limits and description.

        Kinds: parameter, input, state (settable), observable and port (results only; port
        variables are '<instance>.<port>.p' in bar gauge, '.m_flow' in kg/s into the
        component, '.T' in degC).
        """
        system = store.get(system_id)
        if component is not None and component not in system.components:
            raise UnknownComponentError(
                f"No instance named '{component}'. " + format_choices(component, system.components)
            )
        infos = [
            v
            for v in system.variables()
            if component is None or v.path.split(".", 1)[0] == component
        ]
        return VariableList(
            system_id=system_id,
            variables=[
                VariableOut(
                    path=v.path,
                    kind=v.kind,  # type: ignore[arg-type]
                    unit=v.unit,
                    reference=v.pressure_reference,  # type: ignore[arg-type]
                    quantity=v.quantity,
                    type=v.type,
                    minimum=v.minimum,
                    maximum=v.maximum,
                    settable=v.settable,
                    reported=v.reported,
                    description=v.description,
                )
                for v in infos
            ],
        )

    # -- solving --------------------------------------------------------------------------
    def solve_values(
        system: wp.System,
        result: wp.SolveResult,
        paths: list[str],
        units: Mapping[str, str] | None,
    ) -> dict[str, Quantity]:
        targets = _convert_request(result.units, result.references, units, paths)
        values: dict[str, Quantity] = {}
        for p in paths:
            if p in targets:
                unit, ref, target = targets[p]
                converted = result.get(p, target)
                values[p] = Quantity(value=_round(converted), unit=unit, reference=ref)  # type: ignore[arg-type]
            else:
                values[p] = Quantity(
                    value=_round(result[p]),
                    unit=result.units[p],
                    reference=result.references.get(p),  # type: ignore[arg-type]
                )
        return values

    @server.tool(annotations=read_only)
    @tool_call
    def solve(system_id: SystemId, variables: Variables = None, units: Units = None) -> SolveOutput:
        """Solve the steady operating point.

        Returns the selected values with units (6 significant digits; pressures carry their
        reference, port pressures are bar gauge), the mode of every instance, component
        warnings and non-fatal pre-flight issues. Fails with the list of errors when
        check_system reports any.
        """
        system = store.get(system_id)
        paths = _selection(system, variables, units)
        result = system.solve()
        return SolveOutput(
            system_id=system_id,
            converged=result.converged,
            iterations=result.iterations,
            max_residual=float(f"{result.max_residual:.3g}"),
            values=solve_values(system, result, paths, units),
            modes=dict(result.modes),
            warnings=[_warning(w) for w in result.warnings],
            issues=[_issue(i) for i in result.issues],
        )

    @server.tool(annotations=editing)
    @tool_call
    def solve_for(
        system_id: SystemId,
        target: Annotated[str, Field(description="Result path to reach, e.g. 'pump.volume_flow'.")],
        value: Annotated[
            float | str,
            Field(
                description="Target value: a number in the target's reported unit, or a "
                "string with a unit such as '15 m3/h'."
            ),
        ],
        vary: Annotated[
            str,
            Field(description="Numeric input, parameter or state to adjust, e.g. 'pump.speed'."),
        ],
        lower: Annotated[
            float | str, Field(description="Lower bound of `vary` (declared unit or with unit).")
        ],
        upper: Annotated[float | str, Field(description="Upper bound of `vary`.")],
        variables: Variables = None,
        units: Units = None,
    ) -> SolveForOutput:
        """Goal seek: find the value of one input or parameter that gives a target result.

        Solves the steady state repeatedly (Brent's method on `vary` between `lower` and
        `upper`) until `target` equals `value`, e.g. the pump speed that delivers 15 m3/h
        or the valve opening that gives 2 bar downstream. The target must cross the value
        inside the bounds; otherwise the error reports the target at both bounds and
        nothing is changed. On success `vary` stays at the value found and the result is
        the steady operating point there, as from solve.
        """
        system = store.get(system_id)
        paths = _selection(system, variables, units)
        original = system.get(vary)  # validates the path (settable)
        inst, _, local = vary.partition(".")
        spec = system.manifest(inst).variable(local)
        if not spec.is_numeric:
            raise wp.InvalidValueError(f"vary: '{vary}' is not numeric; solve_for varies a number.")
        lo = float(spec.parse(lower, "lower"))
        hi = float(spec.parse(upper, "upper"))
        if not lo < hi:
            raise wp.InvalidValueError(f"lower ({lo:g}) must be below upper ({hi:g}).")
        count = 0
        goal: list[float] = []  # the target value in the target's reported unit
        unit: list[str] = []

        def reached(x: float) -> float:
            nonlocal count
            system.set_values({vary: x})
            result = system.solve()
            count += 1
            got = result[target]
            if not goal:
                unit.append(result.units[target])
                goal.append(
                    parse_value(value, result.units[target], "value", result.references.get(target))
                    if isinstance(value, str)
                    else float(value)
                )
            if got is None:
                raise wp.InvalidValueError(
                    f"'{target}' is undefined (null) at {vary} = {x:g}; choose bounds where "
                    "it is defined."
                )
            return float(got) - goal[0]

        try:
            f_lo = reached(lo)
            f_hi = reached(hi)
            if f_lo * f_hi > 0.0:
                u = unit[0]
                raise wp.InvalidValueError(
                    f"'{target}' is {f_lo + goal[0]:.6g} {u} at {vary} = {lo:g} and "
                    f"{f_hi + goal[0]:.6g} {u} at {vary} = {hi:g}; the target "
                    f"{goal[0]:.6g} {u} is not between them. Widen the bounds (within the "
                    "variable's limits) or change another part of the system."
                )
            if f_lo == 0.0:
                root = lo
            elif f_hi == 0.0:
                root = hi
            else:
                root = float(
                    brentq(reached, lo, hi, xtol=1e-12 * max(abs(lo), abs(hi), 1e-12), rtol=1e-12)
                )
            system.set_values({vary: root})
            result = system.solve()
            count += 1
        except BaseException:
            system.set_values({vary: original})
            raise
        values = solve_values(system, result, paths, units)
        achieved = result[target]
        return SolveForOutput(
            system_id=system_id,
            converged=result.converged,
            iterations=result.iterations,
            max_residual=float(f"{result.max_residual:.3g}"),
            values=values,
            modes=dict(result.modes),
            warnings=[_warning(w) for w in result.warnings],
            issues=[_issue(i) for i in result.issues],
            vary=vary,
            found=_value(spec, system.get(vary)),
            target=target,
            target_value=Quantity(
                value=_round(goal[0], 10),
                unit=result.units[target],
                reference=result.references.get(target),  # type: ignore[arg-type]
            ),
            achieved=Quantity(
                value=_round(achieved, 10),
                unit=result.units[target],
                reference=result.references.get(target),  # type: ignore[arg-type]
            ),
            evaluations=count,
        )

    def expand_events(
        system: wp.System, events: list[Event] | None, duration: float | str, step: float | str
    ) -> list[dict[str, Any]]:
        """Plain set events, with every ramp turned into one set event per step."""
        total = parse_duration(duration, "duration")
        dt = parse_duration(step, "step")
        out: list[dict[str, Any]] = []
        for k, ev in enumerate(events or []):
            where = f"events[{k}]"
            if (ev.set is None) == (ev.ramp is None):
                raise wp.InvalidValueError(
                    f"{where} needs exactly one of 'set' (values at `at`) or 'ramp' (with "
                    "'over'), e.g. {'at': '0 s', 'ramp': {'filter.clogging': [0, 0.8]}, "
                    "'over': '30 min'}."
                )
            if ev.set is not None:
                if ev.over is not None:
                    raise wp.InvalidValueError(f"{where}: 'over' is only used with 'ramp'.")
                out.append({"at": ev.at, "set": ev.set})
                continue
            if ev.over is None or not ev.ramp:
                raise wp.InvalidValueError(
                    f"{where}: a ramp needs a non-empty 'ramp' mapping and a duration 'over'."
                )
            t0 = parse_duration(ev.at, f"{where}.at")
            span = parse_duration(ev.over, f"{where}.over")
            if span <= 0 or dt <= 0:
                raise wp.InvalidValueError(f"{where}.over must be positive.")
            if t0 + span > total + 1e-9 * dt:
                raise wp.InvalidValueError(
                    f"{where}: the ramp ends at {t0 + span:g} s, after the end of the "
                    f"simulation ({total:g} s). Shorten 'over' or lengthen the duration."
                )
            ends: dict[str, tuple[float, float]] = {}
            for path, pair in ev.ramp.items():
                system.get(path)  # validates the path (settable)
                inst, _, local = path.partition(".")
                spec = system.manifest(inst).variable(local)
                if not spec.is_numeric or len(pair) != 2:
                    raise wp.InvalidValueError(
                        f"{where}.ramp.{path} must be [start, end] of a numeric variable."
                    )
                a = float(spec.parse(pair[0], f"{where}.ramp.{path}[0]"))
                b = float(spec.parse(pair[1], f"{where}.ramp.{path}[1]"))
                ends[path] = (a, b)
            n = max(math.ceil(span / dt - 1e-9), 1)
            for i in range(n + 1):
                t = t0 + span if i == n else t0 + i * dt
                frac = min((t - t0) / span, 1.0)
                out.append({"at": t, "set": {p: a + (b - a) * frac for p, (a, b) in ends.items()}})
        return out

    @server.tool(annotations=editing)
    @tool_call
    def simulate(
        system_id: SystemId,
        duration: Annotated[
            float | str, Field(description="Total time: seconds or e.g. '10 min'.")
        ],
        step: Annotated[float | str, Field(description="Time step, e.g. '1 s'.")] = "1 s",
        events: Annotated[
            list[Event] | None,
            Field(
                description="Timed set-point changes, e.g. [{'at': '60 s', 'set': "
                "{'valve.opening': 0}}], or linear ramps, e.g. [{'at': '0 s', 'ramp': "
                "{'filter.clogging': [0, 0.8]}, 'over': '30 min'}]; events after the "
                "duration are rejected."
            ),
        ] = None,
        variables: Variables = None,
        units: Units = None,
        max_points: Annotated[
            int, Field(ge=2, le=10000, description="Downsample series to this many samples.")
        ] = 200,
        restore: Annotated[
            bool,
            Field(
                description="Put parameters, inputs and states (tank levels, positions) back "
                "to their values before the run, so the next solve or simulate starts from "
                "the same point."
            ),
        ] = False,
    ) -> SimulateOutput:
        """Simulate over time with a fixed step and timed events.

        Samples at every multiple of step, at each event time and at the end. Returns
        downsampled series (the first and last samples are always kept) with per-variable
        min, max and final values over the full run; each warning once with its first
        time, last time and whether it is still active at the end; and every mode change.
        Unless restore is true, the system keeps its final state and the values set by
        events.
        """
        system = store.get(system_id)
        paths = _selection(system, variables, units)
        sim = system.simulate(
            duration,
            step,
            expand_events(system, events, duration, step),
            variables=paths,
            restore=restore,
        )
        targets = _convert_request(sim.units, sim.references, units, paths)
        n = len(sim.time)
        idx = list(range(n))
        if n > max_points:
            stride = (n - 1) / (max_points - 1)
            idx = sorted({round(i * stride) for i in range(max_points)})
        series: dict[str, SeriesOut] = {}
        for p in paths:
            if p in targets:
                unit, ref, target = targets[p]
                full = sim.get(p, target)
            else:
                unit, ref = sim.units[p], sim.references.get(p)
                full = sim[p]
            defined = [v for v in full if v is not None]
            series[p] = SeriesOut(
                unit=unit,
                reference=ref,  # type: ignore[arg-type]
                min=_round(min(defined)) if defined else None,
                max=_round(max(defined)) if defined else None,
                final=_round(full[-1]) if full else None,
                values=[_round(full[i]) for i in idx],
            )
        return SimulateOutput(
            system_id=system_id,
            duration=sim.time[-1] if sim.time else 0.0,
            samples=n,
            time=[_round(sim.time[i], 9) or 0.0 for i in idx],
            variables=series,
            warnings=[_warning(w) for w in sim.warnings],
            mode_changes=[
                ModeChangeOut(time=c.time, component=c.component, mode=c.mode)
                for c in sim.mode_changes
            ],
            final_modes=dict(sim.final.modes),
            issues=[_issue(i) for i in sim.final.issues],
        )

    # -- documents ------------------------------------------------------------------------
    @server.tool(annotations=read_only)
    @tool_call
    def get_system(system_id: SystemId) -> SystemDocument:
        """The system document (design 6.3): components with explicit values, connections,
        changed states and the optional simulation block. Pass it to load_system later."""
        return SystemDocument(system_id=system_id, document=store.get(system_id).to_dict())

    @server.tool(annotations=editing)
    @tool_call
    def load_system(
        document: Annotated[
            dict[str, Any] | str,
            Field(
                description="A system document (object, or YAML/JSON text). Minimal "
                "example: {'worldparts_system': '0.1', 'name': 'line', 'components': "
                "[{'name': 'mains', 'type': 'supply', 'parameters': {'pressure': '3 bar'}}, "
                "{'name': 'v', 'type': 'valve', 'inputs': {'opening': 0.5}}, {'name': 'out', "
                "'type': 'drain'}], 'connections': [['mains.port', 'v.port_a'], ['v.port_b', "
                "'out.port']]}. Component items are {name, type, parameters?, inputs?, "
                "states?}; an optional 'simulation' is {duration, step?, events?}."
            ),
        ],
    ) -> SystemSummary:
        """Create a system from a system document and return its new system_id.

        get_system returns such a document. A document of the wrong shape fails with every
        schema error and the expected shape of the offending part. Unknown component types,
        ports and invalid values do not fail the load: they are kept and listed in `issues`
        (the same codes as check_system) so you can fix them all.
        """
        doc = document
        if isinstance(doc, str):
            try:
                doc = load_yaml(doc)
            except yaml.YAMLError as exc:
                raise wp.InvalidValueError(f"document is not valid YAML or JSON: {exc}") from exc
            if not isinstance(doc, dict):
                raise wp.InvalidValueError("document must be a mapping (a system document).")
        system = wp.System.from_dict(doc, cat)
        system_id = store.add(system)
        return _summary(system_id, system, system.check())

    # -- resources --------------------------------------------------------------------------
    for m in cat:
        server.add_resource(
            TextResource(
                uri=RESOURCE_PREFIX + m.id,
                name=m.alias,
                title=m.name,
                description=m.summary,
                mime_type="application/yaml",
                text=manifest_text(m),
            )
        )

    @server.resource(
        RESOURCE_PREFIX + "{id}",
        name="component-manifest",
        title="Component manifest",
        description="The YAML manifest of a component, by full id or short alias.",
        mime_type="application/yaml",
    )
    def component_manifest(id: str) -> str:
        try:
            return manifest_text(cat.get(id))
        except UnknownComponentError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    for register in EXTRA_TOOL_REGISTRARS if registrars is None else registrars:
        register(server, store)
    _compact_tools(server)
    return server


# ----------------------------------------------------------------------------------------
# WNTR adapter tools (registered only when the optional wntr package is installed)
# ----------------------------------------------------------------------------------------
class ExportOutput(BaseModel):
    system_id: str
    target: str
    text: str
    notes: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)


class WntrComparisonOutput(BaseModel):
    system_id: str
    max_flow_rel_diff: float
    max_pressure_abs_diff: float
    report: dict[str, Any]


def register_wntr_tools(server: MCPServer, store: SystemStore) -> None:
    """Register ``export_system`` and ``compare_with_wntr`` (the WNTR adapter, design 11)."""

    def call(fn: Callable[[], _R]) -> _R:
        try:
            with store.lock:
                return fn()
        except WorldpartsError as exc:
            raise ToolError(f"[{exc.code}] {exc}") from exc

    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(annotations=read_only)
    def export_system(
        system_id: SystemId,
        target: Annotated[
            Literal["wntr_inp"],
            Field(description="'wntr_inp' (EPANET .inp via WNTR)."),
        ] = "wntr_inp",
    ) -> ExportOutput:
        """Export a system as EPANET .inp text (units CMH, Darcy-Weisbach).

        Faucets and heaters are unsupported. `notes`: what the export approximates.
        """

        def run() -> ExportOutput:
            from worldparts.adapters.wntr_adapter import model_to_inp, translate

            tr = translate(store.get(system_id))
            return ExportOutput(
                system_id=system_id,
                target=target,
                text=model_to_inp(tr.model),
                notes=tr.approximations,
            )

        return call(run)

    @server.tool(annotations=read_only)
    def compare_with_wntr(system_id: SystemId) -> WntrComparisonOutput:
        """Solve the system here and in EPANET (via WNTR) and compare: per-link flows
        (m3/h) and node pressures (bar gauge), differences and why they diverge."""

        def run() -> WntrComparisonOutput:
            from worldparts.adapters.wntr_adapter import compare_with_wntr as compare

            report = compare(store.get(system_id))
            return WntrComparisonOutput(
                system_id=system_id,
                max_flow_rel_diff=report.max_flow_rel_diff,
                max_pressure_abs_diff=report.max_pressure_abs_diff,
                report=report.to_dict(),
            )

        return call(run)


if wntr_available():
    EXTRA_TOOL_REGISTRARS.append(register_wntr_tools)


def main() -> None:
    """Run the worldparts MCP server over stdio (``worldparts mcp``)."""
    create_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
