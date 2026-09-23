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
``disconnect``, ``check_system``, ``solve``, ``simulate``, ``list_variables``,
``get_system``, ``load_system`` and ``run_contracts``.

Extension point: ``export_system(system_id, target)`` (design 9, target ``wntr_inp``) is
not registered yet; it arrives with the WNTR adapter. A registrar is a function
``(server, store) -> None`` that declares tools with ``server.tool()`` and reads systems
with ``store.get(system_id)``; append it to :data:`EXTRA_TOOL_REGISTRARS` (or pass it to
:func:`create_server`) and it runs after the built-in tools are registered.
"""

from __future__ import annotations

import functools
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

import worldparts as wp
from worldparts.catalog import Catalog, default_catalog
from worldparts.cli import select_paths
from worldparts.errors import UnknownComponentError, WorldpartsError, format_choices
from worldparts.manifest import Manifest, VariableSpec, load_yaml
from worldparts.units import split_pressure_reference

__all__ = [
    "EXTRA_TOOL_REGISTRARS",
    "INSTRUCTIONS",
    "RESOURCE_PREFIX",
    "SystemStore",
    "UnknownSystemError",
    "create_server",
    "main",
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
they flag operation outside a model's validity envelope.\
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


class KeyParameter(BaseModel):
    """A parameter shown in listings."""

    name: str
    unit: str | None = _opt("Declared unit.")
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
    description: str
    simulated: bool
    system: dict[str, Any] = Field(
        default_factory=dict,
        description="The test system (components and connections); a usable template.",
    )
    simulate: dict[str, Any] | None = _opt("Simulation settings (duration, step, events).")
    expect: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Expected results: values with tolerances or ranges, modes and warnings.",
    )


class ContractDoc(BaseModel):
    """A behavioural contract shipped with the manifest."""

    id: str
    description: str
    scenario: str
    check: str = Field(description="monotonic, bounds, equal or warning_iff.")
    sweep: str | None = _opt("Swept variable.")
    sweep_spec: dict[str, Any] | None = _opt("The full sweep: variable and range or values.")
    rule: dict[str, Any] = Field(
        default_factory=dict,
        description="The check's details (variable, direction, left/right, min/max, "
        "condition, tolerances).",
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
    implementations: dict[str, Any]
    provenance: dict[str, Any]
    classification: dict[str, Any] | None = _opt("Cross-walk to IFC, Brick, ...")
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
    warnings: list[WarningOut] = Field(description="First occurrence of each warning.")
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
    """A simulation event: set values at a time."""

    at: float | str = Field(description="Time: seconds or a string such as '60 s', '2 min'.")
    set: dict[str, Any] = Field(description="Paths to values, e.g. {'valve.opening': 0}.")


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
        "'mains.port.p': 'bar absolute'}."
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


def _selection(
    system: wp.System, variables: list[str] | None, units: Mapping[str, str] | None
) -> list[str]:
    """Requested paths (default selection when none) plus every path named in ``units``."""
    paths = select_paths(system, variables)
    if units:
        paths += [p for p in select_paths(system, list(units)) if p not in paths]
    return paths


def _convert_request(
    result_units: Mapping[str, str],
    references: Mapping[str, str],
    units: Mapping[str, str] | None,
) -> dict[str, tuple[str, str | None, str]]:
    """``(unit, reference, requested unit string)`` per path for unit conversions."""
    out: dict[str, tuple[str, str | None, str]] = {}
    for path, target in (units or {}).items():
        if path not in result_units:
            raise wp.UnknownVariableError(
                f"units: unknown variable '{path}'. " + format_choices(path, result_units)
            )
        base, ref = split_pressure_reference(target)
        out[path] = (base, ref or references.get(path), target)
    return out


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


def describe_manifest(m: Manifest) -> ComponentDescription:
    """Everything an agent needs to use a component type."""
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
                description=str(s.get("description", "")),
                simulated=bool(s.get("simulate")),
                system=dict(s.get("system", {})),
                simulate=dict(s["simulate"]) if s.get("simulate") else None,
                expect=[dict(e) for e in s.get("expect", [])],
            )
            for s in m.scenarios
        ],
        contracts=[
            ContractDoc(
                id=str(c["id"]),
                description=str(c.get("description", "")),
                scenario=str(c.get("scenario", "")),
                check=str(c["check"]["type"]),
                sweep=(c.get("sweep") or {}).get("variable"),
                sweep_spec=dict(c["sweep"]) if c.get("sweep") else None,
                rule={k: v for k, v in c["check"].items() if k != "type"},
            )
            for c in m.contracts
        ],
        implementations=dict(d.get("implementations", {})),
        provenance=dict(d.get("provenance", {})),
        classification=d.get("classification"),
        resource_uri=RESOURCE_PREFIX + m.id,
    )


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
                    key_parameters=[KeyParameter(**k) for k in info["key_parameters"]],
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
    ) -> ComponentDescription:
        """Describe a component type: everything needed to use it.

        Ports; parameters and inputs with units, defaults and hard limits; states and
        observables with units; modes with their conditions; warning codes (envelope rules
        with conditions, and component-emitted codes); scenarios and contracts; provenance
        and implementation bindings. Plain numbers you pass later are in these units;
        pressures are gauge unless the variable says otherwise.
        """
        return describe_manifest(cat.get(component))

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
        return ComponentInstance(
            system_id=system_id,
            name=name,
            type=m.id,
            ports=[f"{name}.{p}" for p in m.ports],
            parameters=_instance_values(system, name, m.parameters),
            inputs=_instance_values(system, name, m.inputs),
            states=_instance_values(system, name, m.states),
            issues=issues,
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
        targets = _convert_request(result.units, result.references, units)
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
        return SolveOutput(
            system_id=system_id,
            converged=result.converged,
            iterations=result.iterations,
            max_residual=float(f"{result.max_residual:.3g}"),
            values=values,
            modes=dict(result.modes),
            warnings=[_warning(w) for w in result.warnings],
            issues=[_issue(i) for i in result.issues],
        )

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
                "{'valve.opening': 0}}]; events after the duration are rejected."
            ),
        ] = None,
        variables: Variables = None,
        units: Units = None,
        max_points: Annotated[
            int, Field(ge=2, le=10000, description="Downsample series to this many samples.")
        ] = 200,
    ) -> SimulateOutput:
        """Simulate over time with a fixed step and timed events.

        Samples at every multiple of step, at each event time and at the end. Returns
        downsampled series (the first and last samples are always kept) with per-variable
        min, max and final values over the full run, the first occurrence of each warning,
        and every mode change. The system keeps its final state and the values set by
        events (use set_values to restore them).
        """
        system = store.get(system_id)
        paths = _selection(system, variables, units)
        sim = system.simulate(
            duration, step, [e.model_dump() for e in events or []], variables=paths
        )
        targets = _convert_request(sim.units, sim.references, units)
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
                description="A system document (object, or YAML/JSON text) with "
                "worldparts_system: '0.1', name, components and connections."
            ),
        ],
    ) -> SystemSummary:
        """Create a system from a system document and return its new system_id.

        Unknown component types, ports and invalid values do not fail the load: they are
        kept and listed in `issues` (the same codes as check_system) so you can fix them all.
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
    return server


def main() -> None:
    """Run the worldparts MCP server over stdio (``worldparts mcp``)."""
    create_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
