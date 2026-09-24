"""System: compose components, check, solve and simulate (design section 6.2).

Example::

    import worldparts as wp

    s = wp.System("demo")
    s.add("mains", "supply", pressure="3 bar")
    s.add("v", "valve", kv=2.5)
    s.add("out", "drain")
    s.connect("mains.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    r = s.solve()
    r["v.volume_flow"]            # L/min
"""

from __future__ import annotations

import contextlib
import copy
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from scipy.optimize import brentq

from worldparts.catalog import Catalog, default_catalog
from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.controls import (
    MAX_ROUNDS,
    ROUND_TOL,
    Control,
    HysteresisRuntime,
    InvalidControlError,
    PIRuntime,
    Resolved,
    ResolvedHysteresis,
    ResolvedPI,
    control_warning,
    raise_issue,
    resolve_control,
)
from worldparts.errors import (
    ContractError,
    IncompatiblePortsError,
    InvalidValueError,
    SelfConnectionError,
    SystemCheckError,
    UnknownComponentError,
    UnknownPortError,
    UnknownVariableError,
    WorldpartsError,
    format_choices,
)
from worldparts.expressions import compile_expression, is_true
from worldparts.manifest import PORT_VARIABLES, Manifest, VariableSpec, _schema_errors
from worldparts.network import Network, NetworkSolution, Node
from worldparts.results import (
    ComponentWarning,
    ControlReport,
    Issue,
    ModeChange,
    SimulationResult,
    SolveResult,
    VariableInfo,
)
from worldparts.units import convert, converter, parse_duration

__all__ = ["System"]

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BAR_GAUGE = converter("bar", "gauge")
_DEGC = converter("degC")


#: Expected shapes of the parts of a system document, appended to schema errors.
SYSTEM_DOCUMENT_HINTS: dict[str, str] = {
    "(root)": (
        "a system document is {'worldparts_system': '0.1', 'name': ..., 'description'?: ..., "
        "'components': [...], 'connections'?: [...], 'simulation'?: {...}}"
    ),
    "worldparts_system": "the format version, the string '0.1'",
    "components": (
        "components is a list of {'name', 'type', 'parameters'?, 'inputs'?, 'states'?} "
        "objects, e.g. [{'name': 'mains', 'type': 'supply', 'parameters': {'pressure': "
        "'3 bar'}}, {'name': 'out', 'type': 'drain'}]; names are identifiers, parameter, "
        "input and state values are numbers in the declared unit, strings with units, "
        "booleans or table rows"
    ),
    "connections": (
        "connections is a list of [port_path, port_path] pairs, e.g. [['mains.port', 'out.port']]"
    ),
    "simulation": (
        "simulation is {'duration': '10 min', 'step'?: '1 s', 'events'?: [{'at': '60 s', "
        "'set': {'valve.opening': 0}}, {'at': '2 min', 'ramp': {'valve.opening': [0, 1]}, "
        "'over': '1 min'}]}"
    ),
    "controls": (
        "controls is a list of {'name', 'type': 'pi', 'measure', 'setpoint', 'actuate', "
        "'gain', 'integral_time', 'output_min'?, 'output_max'?, 'direction'?} or {'name', "
        "'type': 'hysteresis', 'measure', 'actuate', 'on_below', 'off_above', 'on_value', "
        "'off_value', 'initial'?, 'max_switches_per_hour'?} objects, e.g. [{'name': 'duty', "
        "'type': 'pi', 'measure': 'pump.outlet.p', 'setpoint': '4 bar', 'actuate': "
        "'pump.speed', 'gain': 0.2, 'integral_time': '10 s', 'output_min': 0.3, "
        "'output_max': 1.2}]"
    ),
}


@dataclass
class _Instance:
    name: str
    type: str
    manifest: Manifest
    component: Component
    explicit: dict[str, Any] = field(default_factory=dict)
    bad: dict[str, tuple[Any, Issue]] = field(default_factory=dict)
    builder: NetworkBuilder | None = None
    #: Document group ("parameters", "inputs" or "states") of each entry of ``bad``.
    bad_groups: dict[str, str] = field(default_factory=dict)


@dataclass
class _Broken:
    name: str
    raw: dict[str, Any]
    issue: Issue


@dataclass
class _Event:
    time: float
    values: list[tuple[str, Any]]


class System:
    """A composed system of component instances and connections.

    Args:
        name: System name.
        description: Optional free text.
        catalog: Catalogue used to resolve component types (default: the package catalogue).
    """

    def __init__(
        self, name: str = "system", description: str | None = None, catalog: Catalog | None = None
    ) -> None:
        self.name = name
        self.description = description
        self.catalog = catalog if catalog is not None else default_catalog()
        self.simulation: dict[str, Any] | None = None
        self._instances: dict[str, _Instance] = {}
        self._broken: dict[str, _Broken] = {}
        self._order: list[str] = []  # instance names (valid and broken) in insertion order
        self._connections: list[tuple[str, str]] = []
        self._bad_connections: list[tuple[tuple[str, str], Issue | None]] = []
        self._net: Network | None = None
        self._port_nodes: dict[str, Node] = {}
        self._dirty = True
        self._x0: Any = None
        self._simulating = False
        self._controls: dict[str, Control] = {}

    def __repr__(self) -> str:
        controls = f", controls={list(self._controls)}" if self._controls else ""
        return (
            f"System({self.name!r}, components={list(self.components)}, "
            f"connections={len(self._connections)}{controls})"
        )

    # ------------------------------------------------------------------------------------
    # composition
    # ------------------------------------------------------------------------------------
    @property
    def components(self) -> dict[str, str]:
        """Instance name to component id (unknown types map to the type as written)."""
        out = {n: i.manifest.id for n, i in self._instances.items()}
        out.update({n: str(b.raw.get("type")) for n, b in self._broken.items()})
        return out

    @property
    def connections(self) -> list[tuple[str, str]]:
        """Valid connections as ``(port_path, port_path)`` pairs."""
        return list(self._connections)

    def component(self, name: str) -> Component:
        """The component implementation object of instance ``name``."""
        return self._instance(name).component

    def manifest(self, name: str) -> Manifest:
        """The manifest of instance ``name``."""
        return self._instance(name).manifest

    def add(
        self,
        name: str,
        component: str,
        parameters: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        **values: Any,
    ) -> Component:
        """Add a component instance.

        Args:
            name: Instance name (an identifier, unique in the system).
            component: Component type: full id or short alias.
            parameters: Parameter values (plain numbers are in the declared unit).
            inputs: Input values.
            **values: Parameters or inputs by name, e.g. ``pressure="3.5 bar"``.

        Returns:
            The component implementation object.

        Raises:
            UnknownComponentError: Unknown type (lists valid types).
            UnknownVariableError: Unknown parameter or input name (lists valid names).
            InvalidValueError: Invalid or out-of-range value, or invalid instance name.
        """
        self._check_new_name(name)
        manifest = self.catalog.get(component)
        merged: dict[str, Any] = {}
        for src in (parameters or {}, inputs or {}, values):
            for key, value in src.items():
                merged[key] = value
        explicit: dict[str, Any] = {}
        for key, value in merged.items():
            spec = self._init_spec(manifest, name, key)
            explicit[key] = spec.parse(value, f"{name}.{key}")
        inst = self._make_instance(name, component, manifest, explicit, strict=True)
        return inst.component

    def _check_new_name(self, name: str) -> None:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise InvalidValueError(
                f"Invalid instance name {name!r}: use letters, digits and underscores, starting "
                "with a letter or underscore (e.g. 'mains', 'valve_1')."
            )
        if name in self._instances or name in self._broken:
            raise InvalidValueError(
                f"An instance named '{name}' already exists. Existing: "
                + ", ".join(sorted(self.components))
                + "."
            )

    @staticmethod
    def _init_spec(manifest: Manifest, inst: str, key: str) -> VariableSpec:
        if key in manifest.parameters:
            return manifest.parameters[key]
        if key in manifest.inputs:
            return manifest.inputs[key]
        valid = list(manifest.parameters) + list(manifest.inputs)
        raise UnknownVariableError(
            f"{manifest.alias} has no parameter or input '{key}' (instance '{inst}'). "
            + format_choices(key, valid)
        )

    def _make_instance(
        self,
        name: str,
        type_: str,
        manifest: Manifest,
        explicit: dict[str, Any],
        strict: bool,
        bad: dict[str, tuple[Any, Issue]] | None = None,
    ) -> _Instance:
        params_si = {
            n: s.to_si(s.parse(s.default, f"{name}.{n}")) for n, s in manifest.parameters.items()
        }
        inputs_si = {n: s.to_si(s.default) for n, s in manifest.inputs.items()}
        for key, disp in explicit.items():
            if key in manifest.parameters:
                params_si[key] = manifest.parameters[key].to_si(disp)
            else:
                inputs_si[key] = manifest.inputs[key].to_si(disp)
        cls = self.catalog.implementation(manifest)
        if strict:
            problems = cls.check_parameters(params_si)
            if problems:
                raise InvalidValueError(f"{name}: " + " ".join(problems))
        comp = cls(name, params_si, inputs_si)
        inst = _Instance(name, type_, manifest, comp, dict(explicit), dict(bad or {}))
        self._instances[name] = inst
        if name not in self._order:
            self._order.append(name)
        self._dirty = True
        return inst

    def remove(self, name: str) -> None:
        """Remove an instance and every connection to it.

        Raises:
            UnknownComponentError: Listing the instances.
        """
        if name in self._instances:
            del self._instances[name]
        elif name in self._broken:
            del self._broken[name]
        else:
            raise UnknownComponentError(
                f"No instance named '{name}'. " + format_choices(name, self.components)
            )
        self._order = [n for n in self._order if n != name]
        self._connections = [
            c for c in self._connections if not any(p.split(".")[0] == name for p in c)
        ]
        self._bad_connections = [
            c for c in self._bad_connections if not any(p.split(".")[0] == name for p in c[0])
        ]
        self._dirty = True

    def _instance(self, name: str) -> _Instance:
        inst = self._instances.get(name)
        if inst is not None:
            return inst
        if name in self._broken:
            raise UnknownComponentError(
                f"Instance '{name}' has an unknown component type "
                f"'{self._broken[name].raw.get('type')}'; remove it or add it with a valid type."
            )
        raise UnknownComponentError(
            f"No instance named '{name}'. " + format_choices(name, self.components)
        )

    def _port_path(self, path: str) -> tuple[_Instance, str]:
        parts = str(path).split(".")
        if len(parts) != 2:
            raise UnknownPortError(
                f"'{path}' is not a port path; write '<instance>.<port>', e.g. 'mains.port'."
            )
        inst = self._instance(parts[0])
        if parts[1] not in inst.manifest.ports:
            raise UnknownPortError(
                f"{inst.name} ({inst.manifest.alias}) has no port '{parts[1]}'. "
                + format_choices(
                    f"{inst.name}.{parts[1]}", [f"{inst.name}.{p}" for p in inst.manifest.ports]
                )
            )
        return inst, parts[1]

    def connect(self, a: str, b: str) -> None:
        """Connect two ports. Connecting several ports to one node forms a junction.

        Raises:
            UnknownComponentError: Unknown instance.
            UnknownPortError: Unknown port (lists the instance's ports).
            SelfConnectionError: ``a`` and ``b`` are the same port.
            IncompatiblePortsError: Port types or media differ.
        """
        ia, pa = self._port_path(a)
        ib, pb = self._port_path(b)
        self._check_pair(a, b, ia, pa, ib, pb)
        if (a, b) in self._connections or (b, a) in self._connections:
            return
        self._connections.append((a, b))
        self._dirty = True

    @staticmethod
    def _check_pair(a: str, b: str, ia: _Instance, pa: str, ib: _Instance, pb: str) -> None:
        if a == b:
            raise SelfConnectionError(f"Cannot connect port '{a}' to itself.")
        sa, sb = ia.manifest.ports[pa], ib.manifest.ports[pb]
        if sa.type != sb.type or sa.medium != sb.medium:
            raise IncompatiblePortsError(
                f"Cannot connect '{a}' ({sa.type}, {sa.medium}) to '{b}' ({sb.type}, {sb.medium}): "
                "port type and medium must match."
            )

    def disconnect(self, a: str, b: str) -> None:
        """Remove the connection between ports ``a`` and ``b``.

        Raises:
            InvalidValueError: When the two ports are not directly connected.
        """
        for pair in ((a, b), (b, a)):
            if pair in self._connections:
                self._connections.remove(pair)
                self._dirty = True
                return
            for bad in list(self._bad_connections):
                if bad[0] == pair:
                    self._bad_connections.remove(bad)
                    return
        related = [f"{x} -- {y}" for x, y in self._connections if a in (x, y) or b in (x, y)]
        raise InvalidValueError(
            f"'{a}' and '{b}' are not connected. Connections involving them: "
            + (", ".join(related) if related else "none")
            + "."
        )

    # ------------------------------------------------------------------------------------
    # controls (design section 13.1)
    # ------------------------------------------------------------------------------------
    @property
    def controls(self) -> dict[str, Control]:
        """Control name to its definition, in the order the controls were added."""
        return dict(self._controls)

    def add_control(self, name: str, type: str, **fields: Any) -> Control:
        """Add a control loop that reads one variable and writes one component input.

        ``type="pi"`` takes ``measure`` (any reported numeric path), ``setpoint`` (in the
        measured variable's unit, or a string with units), ``actuate`` (a numeric input),
        ``gain`` (actuator units per measured unit, positive), ``integral_time`` (e.g.
        ``"10 s"``), optional ``output_min`` and ``output_max`` (default: the input's hard
        limits) and ``direction`` (``"reverse"``, the default, raises the output when the
        measure is below the setpoint; ``"direct"`` lowers it).

        ``type="hysteresis"`` takes ``measure``, ``actuate``, ``on_below``, ``off_above``
        (thresholds in the measured unit), ``on_value``, ``off_value`` (actuator values),
        optional ``initial`` (``"on"`` or ``"off"``, default ``"off"``) and
        ``max_switches_per_hour`` (default 6, the ``short_cycling`` limit).

        ``solve()`` goal-seeks every PI actuator to its setpoint and holds every hysteresis
        state; ``simulate()`` runs the loops as sampled-data controllers (design 13.1).

        Returns:
            The definition.

        Raises:
            UnknownVariableError: A ``measure`` or ``actuate`` path that does not exist.
            InvalidControlError: An inconsistent definition (code ``invalid_control``), e.g.
                ``on_below >= off_above`` or an ``actuate`` that is not a numeric input.
            ControlConflictError: Another control already writes the same input.
        """
        if name in self._controls:
            raise InvalidControlError(
                f"A control named '{name}' already exists. Existing controls: "
                + ", ".join(self._controls)
                + ". Remove it first to redefine it."
            )
        ctrl = Control.from_document({**fields, "name": name, "type": type})
        _, issues = resolve_control(ctrl, self._variable_map())
        issues += self._conflict_issues([*self._controls.values(), ctrl], only=name)
        issues += self._control_name_clash()
        for issue in issues:
            if issue.severity == "error":
                raise_issue(issue)
        self._controls[name] = ctrl
        return ctrl

    def remove_control(self, name: str) -> None:
        """Remove a control; the input it wrote keeps its last value.

        Raises:
            UnknownVariableError: Listing the controls.
        """
        if name not in self._controls:
            raise UnknownVariableError(
                f"No control named '{name}'. "
                + (format_choices(name, self._controls) if self._controls else "There are none.")
            )
        del self._controls[name]

    def _variable_map(self) -> dict[str, VariableInfo]:
        return {v.path: v for v in self._component_variables()}

    def _conflict_issues(self, controls: list[Control], only: str | None = None) -> list[Issue]:
        """``control_conflict`` for every input written by more than one control."""
        writers: dict[str, list[str]] = {}
        for c in controls:
            writers.setdefault(c.actuate, []).append(c.name)
        out: list[Issue] = []
        for path, names in writers.items():
            if len(names) < 2 or (only is not None and only not in names):
                continue
            out.append(
                Issue(
                    "error",
                    "control_conflict",
                    f"Controls {', '.join(names)} all write {path}; an input can have only "
                    "one control. Remove all but one of them.",
                    ", ".join(f"control.{n}" for n in names),
                )
            )
        return out

    def _control_name_clash(self) -> list[Issue]:
        if not self._controls and "control" not in self._instances:
            return []
        if "control" in self._instances or "control" in self._broken:
            return [
                Issue(
                    "error",
                    "invalid_control",
                    "An instance is named 'control', which clashes with the control result "
                    "paths 'control.<name>.output'; rename the instance.",
                    "control",
                )
            ]
        return []

    def _control_issues(self) -> tuple[dict[str, Resolved], list[Issue]]:
        """Validated controls and the check() issues of every control."""
        if not self._controls:
            return {}, []
        variables = self._variable_map()
        resolved: dict[str, Resolved] = {}
        issues: list[Issue] = []
        for ctrl in self._controls.values():
            r, found = resolve_control(ctrl, variables)
            issues += found
            if r is not None:
                resolved[ctrl.name] = r
        issues += self._conflict_issues(list(self._controls.values()))
        issues += self._control_name_clash()
        return resolved, issues

    def _write_input(self, path: str, value: float) -> None:
        """Set a (validated) input in its declared unit without the set_values checks."""
        inst, name = self._var_path(path)
        spec = inst.manifest.inputs[name]
        inst.component.inputs[name] = spec.to_si(value)
        inst.explicit[name] = value
        inst.bad.pop(name, None)
        inst.bad_groups.pop(name, None)

    def _report_controls(
        self,
        result: SolveResult,
        resolved: Mapping[str, Resolved],
        outputs: Mapping[str, float] | None = None,
        switches: Mapping[str, int] | None = None,
        measured_values: Mapping[str, float | None] | None = None,
        unresponsive: Iterable[str] = (),
    ) -> None:
        """Add control values, reports and ``control_saturated`` warnings to a result.

        ``outputs`` are the commands issued after the solve (simulations); without them the
        output is the actuator value the solve used. ``measured_values`` (control name to
        value) are the values the controls measured, when they differ from the result's
        (simulations, where the result is solved again after the commands). A loop in
        ``unresponsive`` (its measure does not depend on its actuator in a steady solve) is
        never reported as saturated.
        """
        skip = set(unresponsive)
        for name, r in resolved.items():
            measured = (
                measured_values[name]
                if measured_values is not None and name in measured_values
                else result.values.get(r.measure)
            )
            output = outputs[name] if outputs is not None else result.values.get(r.actuate)
            out_path, m_path = f"control.{name}.output", f"control.{name}.measure"
            result.values[out_path] = output
            result.units[out_path] = r.actuate_unit
            result.values[m_path] = measured
            result.units[m_path] = r.measure_unit
            if r.measure_reference:
                result.references[m_path] = r.measure_reference
            if isinstance(r, ResolvedPI):
                error = r.error(measured) if measured is not None else None
                saturated = bool(
                    name not in skip
                    and output is not None
                    and error is not None
                    and r.at_limit(output, error)
                )
                if saturated:
                    assert error is not None
                    result.warnings.append(
                        control_warning(
                            name,
                            "control_saturated",
                            f"{r.actuate} = {output:g} {r.actuate_unit} with {r.measure} = "
                            f"{measured:g} {r.measure_unit} against a setpoint of "
                            f"{r.setpoint:g}.",
                        )
                    )
                result.controls[name] = ControlReport(
                    name, "pi", r.measure, r.actuate, output, r.actuate_unit, measured,
                    r.measure_unit, setpoint=r.setpoint, error=error, saturated=saturated,
                )  # fmt: skip
            else:
                result.controls[name] = ControlReport(
                    name, "hysteresis", r.measure, r.actuate, output, r.actuate_unit,
                    measured, r.measure_unit, state=self._controls[name].state,
                    switches=None if switches is None else switches.get(name, 0),
                )  # fmt: skip

    def _settle_controls(
        self, resolved: Mapping[str, Resolved]
    ) -> tuple[list[ComponentWarning], set[str]]:
        """Steady solve with controls: hold hysteresis states, goal-seek PI actuators.

        Every PI actuator is goal-sought within its output limits with Brent's method so
        that the measure equals the setpoint; with several PI loops the goal seeks repeat
        loop by loop until no actuator moves by more than 1e-6 of its range in a round (at
        most 50 rounds). A loop that cannot reach its setpoint is left at the limit its
        integral would wind to. A loop whose measure is undefined (None) over its whole
        output range holds its output, as in a simulation; a loop whose measure does not
        respond to its actuator (a held storage state such as a tank level) holds its output
        and raises ``control_unresponsive``.

        Returns:
            The control warnings and the names of the unresponsive loops.
        """
        for r in resolved.values():
            if isinstance(r, ResolvedHysteresis):
                self._write_input(r.actuate, r.output(self._controls[r.name].state))
        loops = [r for r in resolved.values() if isinstance(r, ResolvedPI)]
        if not loops:
            return [], set()
        u = {r.name: r.clamp(float(self.get(r.actuate))) for r in loops}
        for r in loops:
            self._write_input(r.actuate, u[r.name])
        status: dict[str, str] = {}
        converged = False
        moved = 0.0
        for _ in range(MAX_ROUNDS):
            moved = 0.0
            for r in loops:
                new, status[r.name] = self._goal_seek(r, u[r.name])
                moved = max(moved, abs(new - u[r.name]) / (r.output_max - r.output_min))
                u[r.name] = new
                self._write_input(r.actuate, new)
            if len(loops) == 1 or moved < ROUND_TOL:
                converged = True
                break
        warnings = [
            control_warning(n, "control_direction")
            for n in sorted(n for n, st in status.items() if st == "backwards")
        ]
        unresponsive = {n for n, st in status.items() if st == "unresponsive"}
        for r in loops:
            if r.name in unresponsive:
                warnings.append(
                    control_warning(
                        r.name,
                        "control_unresponsive",
                        f"{r.measure} is the same at {r.actuate} = "
                        f"{_with_unit(r.output_min, r.actuate_unit)} and "
                        f"{_with_unit(r.output_max, r.actuate_unit)}; the output is held at "
                        f"{_with_unit(u[r.name], r.actuate_unit)}.",
                    )
                )
        if not converged:
            warnings += [
                control_warning(r.name, "control_not_converged", f"Last round moved {moved:.3g}.")
                for r in loops
            ]
        return warnings, unresponsive

    def _goal_seek(self, r: ResolvedPI, current: float) -> tuple[float, str]:
        """The steady output of one PI loop, the others fixed, and how it was found.

        The status is ``ok``, ``backwards`` (the loop acts the wrong way), ``undefined`` (the
        measure is None at both limits and at ``current``: the output is held at
        ``current``) or ``unresponsive`` (the measure is the same at both limits: held).

        Raises:
            InvalidValueError: The measure is undefined at some outputs but not at others.
        """

        def measure_at(x: float) -> float | None:
            self._write_input(r.actuate, x)
            for inst in self._instances.values():
                inst.component.settle()
            result, _ = self._snapshot(None)
            measured = result.values.get(r.measure)
            return None if measured is None else float(measured)

        def undefined(at: float, defined: float) -> InvalidValueError:
            return InvalidValueError(
                f"Control '{r.name}': {r.measure} is undefined (null) at {r.actuate} = "
                f"{_with_unit(at, r.actuate_unit)} but defined at "
                f"{_with_unit(defined, r.actuate_unit)}, so the steady solve cannot compare "
                "it with the setpoint over the whole output range. Narrow output_min/"
                "output_max to where it is defined."
            )

        def error_at(x: float) -> float:
            measured = measure_at(x)
            if measured is None:
                raise undefined(x, lo if m_lo is not None else hi)
            return r.error(measured)

        lo, hi = r.output_min, r.output_max
        m_lo, m_hi = measure_at(lo), measure_at(hi)
        if m_lo is None and m_hi is None:
            if measure_at(current) is None:
                return current, "undefined"
            raise undefined(lo, current)
        if m_lo is None or m_hi is None:
            raise undefined(lo if m_lo is None else hi, hi if m_lo is None else lo)
        e_lo, e_hi = r.error(m_lo), r.error(m_hi)
        if abs(e_hi - e_lo) <= r.error_tol():
            self._write_input(r.actuate, current)
            return current, "unresponsive"
        if e_lo == 0.0:
            return lo, "ok"
        if e_hi == 0.0:
            return hi, "ok"
        if e_lo > 0 and e_hi > 0:
            return hi, "ok"  # the integral winds up to the upper limit
        if e_lo < 0 and e_hi < 0:
            return lo, "ok"
        root = float(brentq(error_at, lo, hi, xtol=1e-10 * (hi - lo), maxiter=200))
        return root, "backwards" if e_lo < 0 < e_hi else "ok"

    # ------------------------------------------------------------------------------------
    # values
    # ------------------------------------------------------------------------------------
    def _var_path(self, path: str) -> tuple[_Instance, str]:
        parts = str(path).split(".")
        if len(parts) < 2:
            raise UnknownVariableError(
                f"'{path}' is not a variable path; write '<instance>.<name>', e.g. 'valve.opening'."
            )
        return self._instance(parts[0]), ".".join(parts[1:])

    def _settable(self, path: str) -> tuple[_Instance, str, VariableSpec]:
        inst, name = self._var_path(path)
        m = inst.manifest
        spec = m.settable().get(name)
        if spec is None:
            valid = [f"{inst.name}.{n}" for n in m.settable()]
            what = (
                "is read-only (an observable or port variable)"
                if (name in m.observables or "." in name)
                else "does not exist"
            )
            raise UnknownVariableError(
                f"'{path}' {what}; settable variables of {inst.name}: "
                + format_choices(path, valid)
            )
        return inst, name, spec

    def set(self, path: str, value: Any) -> None:
        """Set an input, parameter or state. Plain numbers are in the declared unit.

        Changing a parameter re-initialises (outside simulations) the states whose initial
        value it changes, e.g. a tank's ``initial_level`` resets its ``level``, while other
        parameter changes keep the current states. Use :meth:`reset_states` to restart every
        state.

        Raises:
            UnknownVariableError: Unknown or read-only path (lists the settable names).
            InvalidValueError: Invalid value; ``OutOfRangeError`` outside hard limits.
        """
        self.set_values({path: value})

    def set_values(self, values: Mapping[str, Any]) -> None:
        """Set several inputs, parameters and states at once (atomically).

        Every value is parsed and range-checked, and each component's cross-parameter rules
        (``check_parameters``) are applied to the batch as a whole, before anything is
        changed; a rejected batch leaves the system unchanged, and the result does not depend
        on the key order. Parameters are applied first, then inputs; then (outside
        simulations) every state whose initial value (``init_states()``) the batch changes is
        re-initialised, the other states keep their values, and finally the states given in
        the batch are applied, so they are never discarded. The resulting states are checked
        with the component's ``check_states`` before anything is changed.

        Raises:
            UnknownVariableError: Unknown or read-only path (lists the settable names).
            InvalidValueError: Invalid value or a failed cross-parameter rule;
                ``OutOfRangeError`` outside hard limits.
        """
        plan: list[tuple[_Instance, str, VariableSpec, Any, Any]] = []
        for path, value in values.items():
            inst, name, spec = self._settable(path)
            disp = spec.parse(value, path)
            plan.append((inst, name, spec, disp, spec.to_si(disp)))
        trials: dict[str, tuple[_Instance, dict[str, Any], list[str]]] = {}
        for inst, name, spec, _, si in plan:
            if spec.kind == "parameter":
                entry = trials.setdefault(inst.name, (inst, dict(inst.component.parameters), []))
                entry[1][name] = si
                entry[2].append(f"{inst.name}.{name}")
        for inst, trial, paths in trials.values():
            problems = type(inst.component).check_parameters(trial)
            if problems:
                raise InvalidValueError(f"{', '.join(paths)}: " + " ".join(problems))
        # Trial states: a parameter change re-initialises (outside simulations) only the
        # states whose initial value it changes, so a tank keeps its water when its port Kv
        # changes; states given in the batch are applied last. check_states sees the result.
        new_states: dict[str, tuple[_Instance, dict[str, Any], list[str]]] = {}
        for inst, name, spec, _, _ in plan:
            if spec.kind in ("parameter", "state"):
                entry = new_states.setdefault(inst.name, (inst, dict(inst.component.states), []))
                entry[2].append(f"{inst.name}.{name}")
        for inst, states, _ in new_states.values():
            comp = inst.component
            if inst.name in trials and not self._simulating:
                inputs = dict(comp.inputs)
                for other, name, spec, _, si in plan:
                    if other is inst and spec.kind == "input":
                        inputs[name] = si
                before = _initial_states(comp, comp.parameters, comp.inputs)
                after = _initial_states(comp, trials[inst.name][1], inputs)
                for name, value in after.items():
                    if name not in before or before[name] != value:
                        states[name] = value
            for other, name, spec, _, si in plan:
                if other is inst and spec.kind == "state":
                    states[name] = si
        for inst, states, paths in new_states.values():
            params = trials[inst.name][1] if inst.name in trials else inst.component.parameters
            problems = type(inst.component).check_states(params, states)
            if problems:
                raise InvalidValueError(f"{', '.join(paths)}: " + " ".join(problems))
        for kind in ("parameter", "input", "state"):
            if kind == "state":
                for inst, states, _ in new_states.values():
                    inst.component.states.update(states)
                    if inst.name in trials:
                        self._dirty = True
            for inst, name, spec, disp, si in plan:
                if spec.kind != kind:
                    continue
                comp = inst.component
                if kind == "parameter":
                    comp.parameters[name] = si
                elif kind == "input":
                    comp.inputs[name] = si
                if kind in ("parameter", "input"):
                    inst.explicit[name] = disp
                inst.bad.pop(name, None)
                inst.bad_groups.pop(name, None)

    def get(self, path: str, unit: str | None = None) -> Any:
        """Current value of a parameter, input or state in its declared unit (or ``unit``).

        Tables are returned as lists of rows. For observables, use :meth:`solve`.
        """
        inst, name = self._var_path(path)
        m = inst.manifest
        spec = m.settable().get(name)
        if spec is None:
            raise UnknownVariableError(
                f"'{path}' is not a parameter, input or state; observables and port variables "
                "come from solve(). "
                + format_choices(path, [f"{inst.name}.{n}" for n in m.settable()])
            )
        store = {"parameter": inst.component.parameters, "input": inst.component.inputs}.get(
            spec.kind, inst.component.states
        )
        value = spec.from_si(store[name])
        if unit is not None and spec.is_numeric:
            return convert(value, spec.unit or "1", unit, spec.reference)
        return value

    def reset_states(self) -> None:
        """Re-initialise every component's states from its parameters and inputs, and put
        every hysteresis control back to its ``initial`` state."""
        for inst in self._instances.values():
            inst.component.init_states()
        for ctrl in self._controls.values():
            ctrl.reset()

    def variables(self) -> list[VariableInfo]:
        """Every variable path with kind, unit and description.

        Besides component variables, every valid control contributes two results of kind
        ``control``: ``control.<name>.output`` (the command, in the actuated input's unit)
        and ``control.<name>.measure`` (the measured value).
        """
        out = self._component_variables()
        resolved, _ = self._control_issues()
        for name, r in resolved.items():
            out.append(
                VariableInfo(
                    f"control.{name}.output",
                    "control",
                    r.actuate_unit,
                    f"Output of {r.type} control '{name}': the command written to {r.actuate}.",
                )
            )
            out.append(
                VariableInfo(
                    f"control.{name}.measure",
                    "control",
                    r.measure_unit,
                    f"The value of {r.measure} measured by control '{name}'.",
                    r.measure_reference,
                )
            )
        return out

    def _component_variables(self) -> list[VariableInfo]:
        out: list[VariableInfo] = []
        for inst in self._instances.values():
            m = inst.manifest
            for kind, group in (
                ("parameter", m.parameters),
                ("input", m.inputs),
                ("state", m.states),
                ("observable", m.observables),
            ):
                for n, s in group.items():
                    out.append(
                        VariableInfo(
                            f"{inst.name}.{n}",
                            kind,
                            s.unit,
                            s.description,
                            s.reference,
                            s.type,
                            s.minimum,
                            s.maximum,
                            kind != "observable",
                            reported=s.type not in ("string", "table"),
                            quantity=s.quantity,
                        )
                    )
            for port in m.ports:
                for pv, (unit, ref, desc) in PORT_VARIABLES.items():
                    out.append(VariableInfo(f"{inst.name}.{port}.{pv}", "port", unit, desc, ref))
        return out

    def describe(self, name: str) -> dict[str, Any]:
        """Resolved values of an instance with units (for agents)."""
        inst = self._instance(name)
        m = inst.manifest

        def entries(group: Mapping[str, VariableSpec], store: Mapping[str, Any]) -> dict[str, Any]:
            return {n: {"value": s.from_si(store[n]), "unit": s.unit} for n, s in group.items()}

        c = inst.component
        return {
            "name": name,
            "type": m.id,
            "ports": list(m.ports),
            "parameters": entries(m.parameters, c.parameters),
            "inputs": entries(m.inputs, c.inputs),
            "states": entries(m.states, c.states),
        }

    # ------------------------------------------------------------------------------------
    # structure
    # ------------------------------------------------------------------------------------
    def _build(self) -> None:
        if not self._dirty and self._net is not None:
            return
        net = Network()
        paths = [f"{i.name}.{p}" for i in self._instances.values() for p in i.manifest.ports]
        parent = {p: p for p in paths}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in self._connections:
            if a in parent and b in parent:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
        groups: dict[str, list[str]] = {}
        for p in paths:
            groups.setdefault(find(p), []).append(p)
        port_nodes: dict[str, Node] = {}
        for members in groups.values():
            node = net.add_node(Node(" = ".join(sorted(members))))
            for p in members:
                port_nodes[p] = node
        connected = {p for c in self._connections for p in c}
        for inst in self._instances.values():
            nodes = {p: port_nodes[f"{inst.name}.{p}"] for p in inst.manifest.ports}
            linked = [p for p in inst.manifest.ports if f"{inst.name}.{p}" in connected]
            nb = NetworkBuilder(net, inst.name, nodes, linked)
            inst.component.build(nb)
            inst.builder = nb
        self._net = net
        self._port_nodes = port_nodes
        self._dirty = False
        self._x0 = None

    def check(self) -> list[Issue]:
        """Structural pre-flight.

        Returns:
            Issues with stable codes: ``unknown_component``, ``unknown_port``,
            ``incompatible_ports``, ``self_connection``, ``unconnected_port`` (warning),
            ``no_pressure_reference``, ``parameter_out_of_range`` and ``invalid_value``;
            for controls (``where`` is ``control.<name>``) ``unknown_variable``,
            ``invalid_control`` and ``control_conflict``.
        """
        issues: list[Issue] = [b.issue for b in self._broken.values()]
        issues.extend(i for _, i in self._bad_connections if i is not None)
        for inst in self._instances.values():
            issues.extend(issue for _, issue in inst.bad.values())
            cls = type(inst.component)
            problems = cls.check_parameters(inst.component.parameters)
            problems += cls.check_states(inst.component.parameters, inst.component.states)
            for p in problems:
                issues.append(Issue("error", "invalid_value", p, inst.name))
        connected = {p for c in self._connections for p in c}
        for inst in self._instances.values():
            for port in inst.manifest.ports:
                path = f"{inst.name}.{port}"
                if path not in connected:
                    issues.append(
                        Issue(
                            "warning",
                            "unconnected_port",
                            f"Port {path} is not connected; it is treated as capped (no flow).",
                            path,
                        )
                    )
        self._build()
        assert self._net is not None
        node_ports: dict[int, list[str]] = {}
        for path, node in self._port_nodes.items():
            node_ports.setdefault(node.index, []).append(path)
        for group in self._net.unreferenced_subnetworks():
            ports = sorted(p for n in group for p in node_ports.get(n.index, []))
            where = ", ".join(ports) if ports else ", ".join(n.label for n in group)
            issues.append(
                Issue(
                    "error",
                    "no_pressure_reference",
                    f"The sub-network containing {where} has no fixed-pressure node, so its "
                    "pressures are undetermined. Connect a supply, drain or tank to it.",
                    where,
                )
            )
        issues.extend(self._bypass_issues())
        issues.extend(self._short_circuit_issues(node_ports))
        issues.extend(self._control_issues()[1])
        return issues

    def _bypass_issues(self) -> list[Issue]:
        """Warn when two ports of one instance are joined to the same node."""
        out: list[Issue] = []
        for inst in self._instances.values():
            by_node: dict[int, list[str]] = {}
            for port in inst.manifest.ports:
                path = f"{inst.name}.{port}"
                by_node.setdefault(self._port_nodes[path].index, []).append(path)
            for paths in by_node.values():
                if len(paths) > 1:
                    joined = " and ".join(paths)
                    out.append(
                        Issue(
                            "warning",
                            "self_connection",
                            f"{joined} are connected to each other, so {inst.name} is bypassed "
                            "(there is no pressure difference across it). Connect its ports to "
                            "different parts of the network.",
                            joined,
                        )
                    )
        return out

    def _short_circuit_issues(self, node_ports: Mapping[int, list[str]]) -> list[Issue]:
        """Error when fixed-pressure nodes at different pressures meet through ideal joints.

        Boundaries (supply, drain) join their fixed node to their port with a near-ideal
        joint, so two of them at different pressures on one junction drive an unbounded flow.
        """
        assert self._net is not None
        net = self._net
        owner: dict[int, str] = {}
        for inst in self._instances.values():
            with contextlib.suppress(WorldpartsError, ValueError, ArithmeticError):
                inst.component.update_laws()  # refresh fixed-node pressures
            if inst.builder is not None:
                for nd in inst.builder.nodes:
                    owner[nd.index] = inst.name
        parent = list(range(len(net.nodes)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for br in net.branches:
            if getattr(br.law, "ideal", False):
                ra, rb = find(br.a.index), find(br.b.index)
                if ra != rb:
                    parent[ra] = rb
        groups: dict[int, list[Node]] = {}
        for nd in net.nodes:
            groups.setdefault(find(nd.index), []).append(nd)
        out: list[Issue] = []
        for group in groups.values():
            fixed = [nd for nd in group if nd.fixed and nd.p is not None]
            pressures = [float(nd.p) for nd in fixed if nd.p is not None]
            if len(fixed) < 2 or max(pressures) - min(pressures) <= 1e-6:
                continue
            names = sorted({owner.get(nd.index, nd.label) for nd in fixed})
            where = ", ".join(sorted(p for nd in group for p in node_ports.get(nd.index, [])))
            levels = ", ".join(
                f"{owner.get(nd.index, nd.label)} at {_BAR_GAUGE.from_si(float(nd.p or 0)):g} bar"
                for nd in fixed
            )
            out.append(
                Issue(
                    "error",
                    "boundary_short_circuit",
                    f"The fixed-pressure boundaries {', '.join(names)} are joined directly at "
                    f"{where} ({levels} gauge): the flow between two different fixed pressures "
                    "with no resistance between them is unbounded. Put a pipe or valve between "
                    "them, or give them the same pressure.",
                    where,
                )
            )
        return out

    # ------------------------------------------------------------------------------------
    # solving
    # ------------------------------------------------------------------------------------
    def _preflight(self) -> list[Issue]:
        issues = self.check()
        if any(i.severity == "error" for i in issues):
            raise SystemCheckError(issues)
        return [i for i in issues if i.severity != "error"]

    def solve(self) -> SolveResult:
        """Steady operating point.

        ``steady: settle`` states are set to equilibrium; ``steady: hold`` states keep their
        current values.

        With controls (design 13.1), a hysteresis control holds its current state (its
        actuator is set to the on or off value), and every PI actuator is goal-sought within
        its output limits so that the measure equals the setpoint (Brent's method; several
        loops are iterated loop by loop until they agree). The actuators are left at the
        values found. A loop that cannot reach its setpoint stays at a limit and raises
        ``control_saturated``; the result's ``controls`` report every loop.

        Raises:
            SystemCheckError: When :meth:`check` finds error-level issues.
            SolverError: When the solver does not converge.
        """
        return self._solve(self._preflight())

    def _solve(self, issues: list[Issue]) -> SolveResult:
        """:meth:`solve` after the pre-flight; ``issues`` are the non-fatal check issues to
        attach to the result. Calibration (design 14.2) checks the system once and then
        calls this for every trial, since values set through :meth:`set_values` are
        validated there."""
        resolved, _ = self._control_issues()
        for inst in self._instances.values():
            inst.component.time_step = None
            inst.component.settle()
        if not resolved:
            result, _ = self._snapshot(None)
            result.issues = issues
            return result
        # A failed goal seek must not leave an actuator at a trial value.
        with self.restore_on_error():
            control_warnings, unresponsive = self._settle_controls(resolved)
            for inst in self._instances.values():
                inst.component.settle()
            result, _ = self._snapshot(None)
        self._report_controls(result, resolved, unresponsive=unresponsive)
        result.warnings.extend(control_warnings)
        result.issues = issues
        return result

    def _snapshot(self, time: float | None) -> tuple[SolveResult, NetworkSolution]:
        self._build()
        assert self._net is not None
        for inst in self._instances.values():
            inst.component.update_laws()
        sol = self._net.solve(self._x0)
        self._x0 = sol.x
        return self._collect(sol, time), sol

    def _collect(self, sol: NetworkSolution, time: float | None) -> SolveResult:
        values: dict[str, Any] = {}
        units: dict[str, str] = {}
        refs: dict[str, str] = {}
        modes: dict[str, str | None] = {}
        warnings: list[ComponentWarning] = []
        for inst in self._instances.values():
            m = inst.manifest
            comp = inst.component
            assert inst.builder is not None
            view = NetworkView(sol, inst.builder)
            obs = comp.observables(view)
            missing = [n for n in m.observables if n not in obs]
            if missing:
                raise ContractError(
                    f"{m.id}: {type(comp).__name__}.observables() did not return {missing}."
                )
            local: dict[str, Any] = {}
            for group, store in (
                (m.parameters, comp.parameters),
                (m.inputs, comp.inputs),
                (m.states, comp.states),
                (m.observables, obs),
            ):
                for n, spec in group.items():
                    raw = store.get(n)
                    if spec.is_numeric:
                        v = None
                        if raw is not None:
                            fv = float(raw)
                            v = spec.converter.from_si(fv) if math.isfinite(fv) else None
                    elif spec.type == "boolean":
                        v = bool(raw)
                    else:
                        continue
                    local[n] = v
                    path = f"{inst.name}.{n}"
                    values[path] = v
                    units[path] = spec.unit or "1"
                    if spec.reference:
                        refs[path] = spec.reference
            for port in m.ports:
                p_abs = view.port_p(port)
                t_k = view.port_T(port)
                pv = {
                    "p": _BAR_GAUGE.from_si(p_abs),
                    "m_flow": view.port_m_flow(port),
                    "T": _DEGC.from_si(t_k),
                }
                for key, v in pv.items():
                    local[f"{port}.{key}"] = v
                    path = f"{inst.name}.{port}.{key}"
                    values[path] = v
                    units[path] = PORT_VARIABLES[key][0]
                    if key == "p":
                        refs[path] = "gauge"
            mode = None
            for spec_mode in m.modes:
                if is_true(compile_expression(spec_mode.condition).evaluate(local)):
                    mode = spec_mode.name
                    break
            modes[inst.name] = mode
            for rule in m.envelope:
                assert rule.condition is not None
                if is_true(compile_expression(rule.condition).evaluate(local)):
                    warnings.append(
                        ComponentWarning(inst.name, rule.code, rule.severity, rule.description)
                    )
            for w in comp.extra_warnings(view):
                if w.code not in m.warnings:
                    raise ContractError(
                        f"{m.id}: component emitted undeclared warning code '{w.code}'. "
                        + format_choices(w.code, m.warnings)
                    )
                warnings.append(w)
        return SolveResult(
            converged=sol.converged,
            iterations=sol.iterations,
            max_residual=sol.max_residual,
            values=values,
            units=units,
            modes=modes,
            warnings=warnings,
            references=refs,
            time=time,
        )

    def _parse_events(
        self, events: Iterable[Mapping[str, Any]] | None, total: float, dt: float, tol: float
    ) -> list[_Event]:
        """Validated events; each ramp becomes one set event per step (design 13.2)."""
        controlled = {c.actuate: c for c in self._controls.values()}
        out: list[_Event] = []
        for k, ev in enumerate(events or []):
            where = f"events[{k}]"
            if not isinstance(ev, Mapping) or "at" not in ev:
                raise InvalidValueError(
                    f"{where} must be a mapping with 'at' and 'set' (or 'ramp' and 'over'), "
                    "e.g. {'at': '60 s', 'set': {'valve.opening': 0}}; got " + repr(ev)
                )
            unknown = sorted(set(ev) - {"at", "set", "ramp", "over"})
            if unknown:
                raise InvalidValueError(
                    f"{where}: unknown key(s) {', '.join(map(repr, unknown))}; an event has "
                    "'at' and 'set', or 'at', 'ramp' and 'over'."
                )
            if (ev.get("set") is None) == (ev.get("ramp") is None):
                raise InvalidValueError(
                    f"{where} needs exactly one of 'set' (values at `at`) or 'ramp' (with "
                    "'over'), e.g. {'at': '0 s', 'ramp': {'filter.clogging': [0, 0.8]}, "
                    "'over': '30 min'}."
                )
            t = parse_duration(ev["at"], f"{where}.at")
            if t > total + tol:
                raise InvalidValueError(
                    f"{where}.at = {t:g} s is after the end of the simulation "
                    f"({total:g} s), so it would never be applied. Use a longer duration or "
                    "remove the event."
                )
            if ev.get("set") is not None:
                if ev.get("over") is not None:
                    raise InvalidValueError(f"{where}: 'over' is only used with 'ramp'.")
                sets = ev["set"]
                if not isinstance(sets, Mapping) or not sets:
                    raise InvalidValueError(f"{where}.set must be a non-empty mapping.")
                for path, value in sets.items():
                    inst, name = self._var_path(path)
                    spec = inst.manifest.settable().get(name)
                    if spec is None:
                        valid = [f"{inst.name}.{n}" for n in inst.manifest.settable()]
                        raise UnknownVariableError(
                            f"{where}: '{path}' cannot be set. " + format_choices(path, valid)
                        )
                    self._check_uncontrolled(where, path, controlled)
                    spec.parse(value, f"{where}.set.{path}")
                out.append(_Event(min(t, total), list(sets.items())))
                continue
            out.extend(self._ramp_events(where, ev, t, total, dt, tol, controlled))
        out.sort(key=lambda e: e.time)
        return out

    @staticmethod
    def _check_uncontrolled(where: str, path: str, controlled: Mapping[str, Control]) -> None:
        ctrl = controlled.get(path)
        if ctrl is not None:
            raise InvalidValueError(
                f"{where}: '{path}' is written by control '{ctrl.name}' ({ctrl.type}), which "
                "would overwrite the event at its next sample. Remove the control "
                "(remove_control) to drive the input with events."
            )

    def _ramp_events(
        self,
        where: str,
        ev: Mapping[str, Any],
        t0: float,
        total: float,
        dt: float,
        tol: float,
        controlled: Mapping[str, Control],
    ) -> list[_Event]:
        """A ramp ``{at, ramp: {path: [start, end]}, over}`` as one set event per step.

        The values change linearly from ``at`` to ``at + over``: one event at ``at`` and at
        every step after it, and the last one exactly at ``at + over`` with the end values.
        """
        ramp, over = ev["ramp"], ev.get("over")
        if over is None or not isinstance(ramp, Mapping) or not ramp:
            raise InvalidValueError(
                f"{where}: a ramp needs a non-empty 'ramp' mapping and a duration 'over'."
            )
        span = parse_duration(over, f"{where}.over")
        if span <= 0:
            raise InvalidValueError(f"{where}.over must be positive.")
        if t0 + span > total + tol:
            raise InvalidValueError(
                f"{where}: the ramp ends at {t0 + span:g} s, after the end of the "
                f"simulation ({total:g} s). Shorten 'over' or lengthen the duration."
            )
        ends: dict[str, tuple[float, float]] = {}
        for path, pair in ramp.items():
            self.get(path)  # validates the path (a parameter, input or state)
            inst, name = self._var_path(path)
            spec = inst.manifest.settable()[name]
            self._check_uncontrolled(where, path, controlled)
            if not spec.is_numeric or not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise InvalidValueError(
                    f"{where}.ramp.{path} must be [start, end] of a numeric variable."
                )
            a = float(spec.parse(pair[0], f"{where}.ramp.{path}[0]"))
            b = float(spec.parse(pair[1], f"{where}.ramp.{path}[1]"))
            ends[path] = (a, b)
        n = max(math.ceil(span / dt - 1e-9), 1)
        out: list[_Event] = []
        for i in range(n + 1):
            t = t0 + span if i == n else t0 + i * dt
            frac = min((t - t0) / span, 1.0)
            values = [(p, a + (b - a) * frac) for p, (a, b) in ends.items()]
            out.append(_Event(min(t, total), values))
        return out

    @staticmethod
    def _time_grid(total: float, dt: float, events: list[_Event], tol: float) -> list[float]:
        """Sample times: multiples of ``dt``, the end time, and every event time."""
        n = math.floor(total / dt + 1e-9)
        grid = [k * dt for k in range(n + 1)]
        if total - grid[-1] > tol:
            grid.append(total)  # a final, shorter step ends exactly at the duration
        extra = []
        for ev in events:
            k = round(ev.time / dt)
            if abs(ev.time - k * dt) <= tol and k <= n:
                ev.time = k * dt  # on the grid (within round-off)
            elif abs(ev.time - grid[-1]) <= tol:
                ev.time = grid[-1]
            else:
                extra.append(ev.time)
        return sorted(set(grid) | set(extra))

    def simulate(
        self,
        duration: Any = None,
        step: Any = None,
        events: Iterable[Mapping[str, Any]] | None = None,
        variables: Iterable[str] | None = None,
        restore: bool = False,
    ) -> SimulationResult:
        """Time simulation with a fixed step (design section 5.5).

        Samples are taken at every multiple of ``step``, at the end time ``duration`` (the
        last step is shortened when ``duration`` is not a multiple of ``step``) and at every
        event time (the step is split there, so an event takes effect exactly when it is due).

        At each sample time ``t``, reached from the previous sample ``t0``: fast states
        advance over ``(t0, t]`` with the commands that held during that interval
        (``update_fast_states(t - t0)``); events due at ``t`` are applied; instantaneous
        responses to them are applied (``update_fast_states(0)``, so an actuator with a time
        constant still reads its old position at ``t``); hydraulics and temperatures are
        solved and recorded; storage states are integrated to the next sample. The system is
        left at the final state (inputs changed by events stay changed; use
        :meth:`reset_states` to restart), unless ``restore`` is true.

        Controls (design 13.1) are sampled-data controllers: after the solve at ``t`` each
        control computes its output from the value it measured at ``t``, and the output is
        applied as an input change at ``t`` exactly like an event due at ``t``: the system is
        solved again with it, and that solution is recorded at ``t`` and drives the storage
        integration over the next step (so ``control.<name>.measure`` at ``t`` is the value
        before the command, and the measured variable's own series the value after it). A
        PI loop starts from its actuator's current value
        (clamped to its output limits) and uses the incremental form ``u += gain * (e -
        e_prev) + gain * dt / integral_time * e`` (``dt`` the time since its previous
        sample), clamped to its limits, so the integral cannot wind up. A hysteresis control
        starts from its current state, and its actuator is set to the matching value before
        the first solve. The series carry ``control.<name>.output`` (the command issued at
        each sample) and ``control.<name>.measure``; ``control_saturated`` and
        ``short_cycling`` warnings are raised for ``control.<name>``. Events may not set an
        input that a control writes.

        Each warning is reported once, with the message and time of its first occurrence,
        the time of its last occurrence (``last_time``) and whether it is still raised at
        the end (``active_at_end``).

        Args:
            duration: Total time, e.g. ``"10 min"`` or seconds. When omitted, the system
                document's ``simulation`` block (duration, step and events) is used.
            step: Time step, e.g. ``"1 s"`` (default 1 s, or the ``simulation`` block's).
            events: ``[{"at": "60 s", "set": {"valve.opening": 0}}]``; values may carry
                units. A ramp ``{"at": "0 s", "ramp": {"filter.clogging": [0, 0.8]},
                "over": "30 min"}`` changes values linearly from ``at`` over ``over``, as one
                set event at ``at`` and at every step after it (design 13.2). Events after
                ``duration`` are rejected.
            variables: Paths to record (default: all). Control series are always recorded.
            restore: When true, the parameters, inputs, states and control switch states are
                put back to their values before the run (also when the run fails), so the
                same system can be simulated again from the same start.

        Raises:
            InvalidValueError: Invalid duration, step or events (including events after the
                end), or no duration and no ``simulation`` block.
            SystemCheckError: When :meth:`check` finds error-level issues.
            SolverError: When the solver fails at some step (the message names the time).
        """
        if duration is None:
            if not self.simulation:
                raise InvalidValueError(
                    "simulate() needs a duration (e.g. '10 min'), or a 'simulation' block in "
                    "the system document such as {'duration': '5 min', 'step': '1 s'}."
                )
            duration = self.simulation["duration"]
            if step is None:
                step = self.simulation.get("step")
            if events is None:
                events = self.simulation.get("events")
        if step is None:
            step = "1 s"
        total = parse_duration(duration, "duration")
        dt = parse_duration(step, "step")
        if dt <= 0:
            raise InvalidValueError(f"step must be positive, got {step!r}.")
        n = math.floor(total / dt + 1e-9)
        tol = 1e-9 * dt
        parsed = self._parse_events(events, total, dt, tol)
        if n + len(parsed) > 1_000_000:
            raise InvalidValueError(
                f"duration/step gives {n} steps; the limit is 1,000,000. Use a larger step."
            )
        grid = self._time_grid(total, dt, parsed, tol)
        issues = self._preflight()
        resolved, _ = self._control_issues()
        wanted = list(variables) if variables is not None else None
        if wanted is not None:
            for name in resolved:
                for key in ("output", "measure"):
                    if f"control.{name}.{key}" not in wanted:
                        wanted.append(f"control.{name}.{key}")
        times: list[float] = []
        series: dict[str, list[Any]] = {}
        first_warn: dict[tuple[str, str], ComponentWarning] = {}
        last_warn: dict[tuple[str, str], float] = {}
        mode_changes: list[ModeChange] = []
        last_modes: dict[str, str | None] = {}
        result: SolveResult | None = None
        ev_i = 0
        saved = self._save_values() if restore else None
        saved_controls = {n: c.state for n, c in self._controls.items()}
        self._simulating = True
        try:
            for inst in self._instances.values():
                inst.component.start_simulation()
            runtimes = self._start_controls(resolved)
            for k, t in enumerate(grid):
                if k > 0:
                    for inst in self._instances.values():
                        inst.component.update_fast_states(t - grid[k - 1])
                while ev_i < len(parsed) and parsed[ev_i].time <= t + tol:
                    self.set_values(dict(parsed[ev_i].values))
                    ev_i += 1
                step_ahead = (
                    grid[k + 1] - t if k + 1 < len(grid) else t - grid[k - 1] if k else None
                )
                for inst in self._instances.values():
                    inst.component.update_fast_states(0.0)
                    inst.component.time_step = step_ahead
                result, sol = self._snapshot_at(t)
                if runtimes:
                    result, sol = self._step_controls(runtimes, resolved, result, sol, t)
                if wanted is None:
                    wanted = list(result.values)
                    for p in wanted:
                        series[p] = []
                elif not series:
                    for p in wanted:
                        if p not in result.values:
                            raise UnknownVariableError(
                                f"Cannot record unknown variable '{p}'. "
                                + format_choices(p, result.values)
                            )
                        series[p] = []
                times.append(t)
                for p in wanted:
                    series[p].append(result.values[p])
                for w in result.warnings:
                    key = (w.component, w.code)
                    if key not in first_warn:
                        first_warn[key] = ComponentWarning(
                            w.component, w.code, w.severity, w.message, t
                        )
                    last_warn[key] = t
                for inst_name, mode in result.modes.items():
                    if k == 0 or last_modes.get(inst_name) != mode:
                        mode_changes.append(ModeChange(t, inst_name, mode))
                    last_modes[inst_name] = mode
                if k + 1 < len(grid):
                    h = grid[k + 1] - t
                    for inst in self._instances.values():
                        assert inst.builder is not None
                        inst.component.integrate(h, NetworkView(sol, inst.builder))
        finally:
            self._simulating = False
            for inst in self._instances.values():
                inst.component.time_step = None
            if saved is not None:
                self._restore_values(saved)
                for name, state in saved_controls.items():
                    if name in self._controls:
                        self._controls[name].state = state
        assert result is not None
        result.issues = issues
        active = {(w.component, w.code) for w in result.warnings}
        warnings = [
            replace(w, last_time=last_warn[key], active_at_end=key in active)
            for key, w in first_warn.items()
        ]
        units = {p: result.units[p] for p in series}
        refs = {p: result.references[p] for p in series if p in result.references}
        return SimulationResult(
            time=times,
            series=series,
            units=units,
            warnings=warnings,
            mode_changes=mode_changes,
            final=result,
            references=refs,
            controls=dict(result.controls),
        )

    def _start_controls(
        self, resolved: Mapping[str, Resolved]
    ) -> dict[str, PIRuntime | HysteresisRuntime]:
        """Runtimes of the controls; their actuators are set before the first solve."""
        runtimes: dict[str, PIRuntime | HysteresisRuntime] = {}
        start: dict[str, float] = {}
        for name, r in resolved.items():
            current = float(self.get(r.actuate))
            if isinstance(r, ResolvedPI):
                pi = PIRuntime(r, r.clamp(current))
                runtimes[name] = pi
                value = pi.output
            else:
                runtimes[name] = HysteresisRuntime(r, self._controls[name])
                value = r.output(self._controls[name].state)
            if value != current:
                start[r.actuate] = value
        if start:
            self.set_values(start)
        return runtimes

    def _snapshot_at(self, t: float) -> tuple[SolveResult, NetworkSolution]:
        """:meth:`_snapshot` at simulation time ``t``; errors name the time."""
        try:
            return self._snapshot(t)
        except WorldpartsError as exc:
            exc.args = (f"At t = {t:g} s: {exc}", *exc.args[1:])
            raise

    def _step_controls(
        self,
        runtimes: Mapping[str, PIRuntime | HysteresisRuntime],
        resolved: Mapping[str, Resolved],
        result: SolveResult,
        sol: NetworkSolution,
        t: float,
    ) -> tuple[SolveResult, NetworkSolution]:
        """Run every control on the solution at ``t`` and apply its commands at ``t``.

        The commands are applied exactly like events due at ``t`` (``set_values``, then the
        instantaneous responses ``update_fast_states(0)``) and the system is solved again,
        so the recorded sample and the storage integration over the next step both see
        them (design 13.1). Returns the solution to record and integrate: the re-solved one
        when a command changed an input, else the one given. The control reports carry the
        values measured before the commands.
        """
        commands: dict[str, float] = {}
        outputs: dict[str, float] = {}
        switches: dict[str, int] = {}
        cycling: list[ComponentWarning] = []
        measured = {name: result.values.get(r.measure) for name, r in resolved.items()}
        for name, rt in runtimes.items():
            value = measured.get(name)
            if isinstance(rt, PIRuntime):
                previous = rt.output
                outputs[name], _ = rt.update(t, value)
                if outputs[name] != previous:
                    commands[rt.spec.actuate] = outputs[name]
                continue
            if rt.update(t, value):
                commands[rt.spec.actuate] = rt.spec.output(rt.control.state)
            outputs[name] = rt.spec.output(rt.control.state)
            switches[name] = len(rt.switch_times)
            count = rt.switches_in_window(t)
            if count > rt.spec.max_switches_per_hour:
                cycling.append(
                    control_warning(
                        name,
                        "short_cycling",
                        f"{count} switches in the hour up to t = {t:g} s (limit "
                        f"{rt.spec.max_switches_per_hour:g} per hour).",
                    )
                )
        if commands:
            self.set_values(commands)
            for inst in self._instances.values():
                inst.component.update_fast_states(0.0)
            result, sol = self._snapshot_at(t)
        self._report_controls(result, resolved, outputs, switches, measured)
        result.warnings.extend(cycling)
        return result, sol

    @contextlib.contextmanager
    def restore_on_error(self) -> Iterator[None]:
        """Context manager: when the block raises, put back every parameter, input and
        state (with which values are explicit) and every control switch state as they were
        on entry, then re-raise. Used by goal seeks, so a failed search leaves the system
        unchanged."""
        saved = self._save_values()
        switch_states = {n: c.state for n, c in self._controls.items()}
        try:
            yield
        except BaseException:
            self._restore_values(saved)
            for name, state in switch_states.items():
                if name in self._controls:
                    self._controls[name].state = state
            raise

    def _save_values(self) -> dict[str, tuple[dict[str, Any], ...]]:
        """Copies of every instance's parameters, inputs, states and explicit values."""
        return {
            name: (
                dict(inst.component.parameters),
                dict(inst.component.inputs),
                dict(inst.component.states),
                dict(inst.explicit),
            )
            for name, inst in self._instances.items()
        }

    def _restore_values(self, saved: Mapping[str, tuple[dict[str, Any], ...]]) -> None:
        """Put back values saved by :meth:`_save_values`."""
        for name, (params, inputs, states, explicit) in saved.items():
            inst = self._instances.get(name)
            if inst is None:
                continue
            comp = inst.component
            comp.parameters.clear()
            comp.parameters.update(params)
            comp.inputs.clear()
            comp.inputs.update(inputs)
            comp.states.clear()
            comp.states.update(states)
            inst.explicit.clear()
            inst.explicit.update(explicit)
        self._dirty = True

    # ------------------------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """The system document (design section 6.3), valid against ``system.schema.json``.

        Components are written in the order they were added. Explicitly set parameters and
        inputs are written as numbers in their declared units (tables as rows). States whose
        current value differs from the value ``init_states()`` would give (a tank level
        reached in a simulation, a valve position part-way through its lag, a value set with
        :meth:`set`) are written under ``states``, so the document reproduces the system.
        Unknown components and invalid values are preserved as written. Controls are
        written as given, with a hysteresis control's ``state`` when it differs from its
        ``initial`` state.
        """
        comps: list[dict[str, Any]] = []
        for name in self._order:
            if name in self._broken:
                comps.append(copy.deepcopy(self._broken[name].raw))
                continue
            inst = self._instances[name]
            entry: dict[str, Any] = {"name": name, "type": inst.manifest.id}
            groups: dict[str, dict[str, Any]] = {
                "parameters": {
                    k: v for k, v in inst.explicit.items() if k in inst.manifest.parameters
                },
                "inputs": {k: v for k, v in inst.explicit.items() if k in inst.manifest.inputs},
                "states": _changed_states(inst),
            }
            for key, (raw, _) in inst.bad.items():
                group = inst.bad_groups.get(
                    key, "inputs" if key in inst.manifest.inputs else "parameters"
                )
                groups[group][key] = raw
            for group, values in groups.items():
                if values:
                    entry[group] = _plain(values)
            comps.append(entry)
        doc: dict[str, Any] = {"worldparts_system": "0.1", "name": self.name}
        if self.description:
            doc["description"] = self.description
        doc["components"] = comps
        conns = [list(c) for c in self._connections] + [list(c) for c, _ in self._bad_connections]
        doc["connections"] = conns
        if self._controls:
            doc["controls"] = [c.to_dict() for c in self._controls.values()]
        if self.simulation:
            doc["simulation"] = copy.deepcopy(self.simulation)
        return doc

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any], catalog: Catalog | None = None) -> System:
        """Build a system from a document (design section 6.3).

        The document is validated against ``system.schema.json``. Unknown component types,
        unknown ports and invalid values do not raise here: they are kept and reported by
        :meth:`check` with stable codes, so an agent can see every problem at once. A
        component's optional ``states`` are applied after its parameters and inputs.
        Controls are kept as written; :meth:`check` reports their problems.

        Raises:
            InvalidValueError: When the document does not match the schema or repeats an
                instance name.
        """
        problems = _schema_errors("system", dict(doc), SYSTEM_DOCUMENT_HINTS)
        if problems:
            raise InvalidValueError(
                "The system document does not match system.schema.json:\n"
                + "\n".join(f"  - {p}" for p in problems)
            )
        s = cls(str(doc["name"]), doc.get("description"), catalog)
        if doc.get("simulation"):
            s.simulation = copy.deepcopy(dict(doc["simulation"]))
        for entry in doc.get("components", []):
            s._add_lenient(dict(entry))
        for a, b in doc.get("connections", []):
            s._connect_lenient(a, b)
        for entry in doc.get("controls", []):
            ctrl = Control.from_document(entry)
            if ctrl.name in s._controls:
                raise InvalidValueError(
                    f"The system document defines the control '{ctrl.name}' twice; control "
                    "names must be unique."
                )
            s._controls[ctrl.name] = ctrl
        return s

    def _add_lenient(self, entry: dict[str, Any]) -> None:
        name = entry["name"]
        self._check_new_name(name)
        try:
            manifest = self.catalog.get(entry["type"])
        except UnknownComponentError as exc:
            self._broken[name] = _Broken(
                name, copy.deepcopy(entry), Issue("error", "unknown_component", str(exc), name)
            )
            self._order.append(name)
            return
        explicit: dict[str, Any] = {}
        states: dict[str, Any] = {}
        bad: dict[str, tuple[Any, Issue]] = {}
        bad_groups: dict[str, str] = {}
        for group in ("parameters", "inputs", "states"):
            for key, value in (entry.get(group) or {}).items():
                path = f"{name}.{key}"
                try:
                    if group == "states":
                        spec = manifest.states.get(key)
                        if spec is None:
                            raise UnknownVariableError(
                                f"{manifest.alias} has no state '{key}' (instance '{name}'). "
                                + format_choices(key, manifest.states)
                            )
                        states[key] = spec.to_si(spec.parse(value, path))
                    else:
                        spec = self._init_spec(manifest, name, key)
                        explicit[key] = spec.parse(value, path)
                except UnknownVariableError as exc:
                    bad[key] = (value, Issue("error", "invalid_value", str(exc), path))
                    bad_groups[key] = group
                except InvalidValueError as exc:
                    bad[key] = (value, Issue("error", exc.code, str(exc), path))
                    bad_groups[key] = group
        inst = self._make_instance(name, entry["type"], manifest, explicit, strict=False, bad=bad)
        inst.bad_groups.update(bad_groups)
        inst.component.states.update(states)

    def _connect_lenient(self, a: str, b: str) -> None:
        try:
            self.connect(a, b)
        except UnknownComponentError as exc:
            inst_names = {a.split(".")[0], b.split(".")[0]}
            if inst_names & set(self._broken):
                self._bad_connections.append(((a, b), None))
            else:
                self._bad_connections.append(
                    ((a, b), Issue("error", "unknown_component", str(exc), f"{a} -- {b}"))
                )
        except UnknownPortError as exc:
            self._bad_connections.append(
                ((a, b), Issue("error", "unknown_port", str(exc), f"{a} -- {b}"))
            )
        except InvalidValueError as exc:
            self._bad_connections.append(
                ((a, b), Issue("error", exc.code, str(exc), f"{a} -- {b}"))
            )


def _initial_states(
    comp: Component, parameters: Mapping[str, Any], inputs: Mapping[str, Any]
) -> dict[str, Any]:
    """The states ``init_states()`` gives for these parameters and inputs (SI).

    The component is left exactly as it was (``init_states`` only writes ``states``).
    """
    saved = (comp.parameters, comp.inputs, dict(comp.states))
    try:
        comp.parameters, comp.inputs = dict(parameters), dict(inputs)
        comp.init_states()
        return dict(comp.states)
    finally:
        comp.parameters, comp.inputs = saved[0], saved[1]
        comp.states.clear()
        comp.states.update(saved[2])


def _changed_states(inst: _Instance) -> dict[str, Any]:
    """States (display units) whose value differs from what ``init_states()`` gives."""
    comp = inst.component
    current = dict(comp.states)
    try:
        comp.init_states()
        initial = dict(comp.states)
    finally:
        comp.states.clear()
        comp.states.update(current)
    out: dict[str, Any] = {}
    for name, spec in inst.manifest.states.items():
        value, start = current.get(name), initial.get(name)
        if value is None or not math.isfinite(float(value)):
            continue
        if start is None or not math.isclose(value, start, rel_tol=1e-12, abs_tol=1e-15):
            out[name] = spec.from_si(value)
    return out


def _plain(values: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in values.items():
        if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
            out[k] = int(v) if not isinstance(v, bool) else v
        else:
            out[k] = v
    return out


def _with_unit(value: float, unit: str) -> str:
    """``value`` with its unit for messages; a dimensionless value (unit ``1``) bare."""
    return f"{value:g}" if unit in ("", "1") else f"{value:g} {unit}"
