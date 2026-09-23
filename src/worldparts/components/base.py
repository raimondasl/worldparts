"""Component base class, NetworkBuilder and NetworkView (design section 6.1).

A component implementation turns a manifest into primitive network elements. Its life cycle
inside a :class:`worldparts.System`:

1. ``__init__(name, parameters, inputs)``: values arrive in SI, already validated (tables as
   :class:`worldparts.manifest.Table` with SI columns); ``init_states()`` is called.
2. ``build(nb)``: create internal nodes and branches and keep the handles. Called again
   whenever the system topology or a parameter changes, so it must not depend on having been
   called before.
3. Before every network solve the system calls ``update_laws()`` (refresh law coefficients,
   fixed-node pressures and temperatures, gate directions from parameters, inputs and states).
   ``solve()`` calls ``settle()`` first. ``simulate()`` calls ``start_simulation()`` once,
   then at each sample ``update_fast_states(dt)`` (the interval just elapsed, old commands),
   applies the events due, and calls ``update_fast_states(0)``.
4. After the solve: ``observables(view)`` returns SI values for every declared observable;
   ``extra_warnings(view)`` returns code-emitted warnings; in simulations
   ``integrate(dt, view)`` advances storage states with explicit Euler.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from worldparts.errors import ContractError, UnknownPortError, format_choices
from worldparts.laws import Law
from worldparts.manifest import Manifest
from worldparts.media import WATER, Medium, get_medium
from worldparts.network import Branch, Injection, Network, NetworkSolution, Node, ThermalMap
from worldparts.results import ComponentWarning

__all__ = ["Component", "NetworkBuilder", "NetworkView", "first_order"]


def first_order(x: float, target: float, tau: float, dt: float) -> float:
    """Exact first-order lag update: ``x + (target - x) * (1 - exp(-dt / tau))``.

    ``tau <= 0`` means instantaneous (returns ``target``).
    """
    if tau <= 0.0:
        return target
    if dt <= 0.0:
        return x
    return x + (target - x) * (1.0 - math.exp(-dt / tau))


class NetworkBuilder:
    """Creates a component's nodes and branches inside a system network.

    Obtained by :meth:`Component.build`; do not construct it yourself.
    """

    def __init__(
        self,
        network: Network,
        component: str,
        port_nodes: Mapping[str, Node],
        connected: Iterable[str] | None = None,
    ) -> None:
        self._net = network
        self.component = component
        self.port_nodes = dict(port_nodes)
        self.connected = frozenset(self.port_nodes if connected is None else connected)
        self.nodes: list[Node] = []
        self.branches: list[Branch] = []
        self.injections: list[Injection] = []

    def port(self, name: str) -> Node:
        """The (possibly shared junction) node of port ``name``.

        Raises:
            UnknownPortError: Listing the component's ports.
        """
        try:
            return self.port_nodes[name]
        except KeyError:
            raise UnknownPortError(
                f"{self.component} has no port '{name}'. " + format_choices(name, self.port_nodes)
            ) from None

    def is_connected(self, port: str) -> bool:
        """True when ``port`` is connected to another component (False when capped)."""
        self.port(port)
        return port in self.connected

    def node(self, label: str) -> Node:
        """A new internal free node (unknown pressure)."""
        n = self._net.add_node(Node(f"{self.component}.{label}"))
        self.nodes.append(n)
        return n

    def fixed_node(self, label: str, p_abs: float, T: float) -> Node:
        """A new internal fixed-pressure node.

        Args:
            label: Local label.
            p_abs: Absolute pressure in Pa. May be changed later via ``node.p``.
            T: Temperature in K of water leaving the node into the network (``node.T``).
        """
        n = self._net.add_node(Node(f"{self.component}.{label}", fixed=True, p=p_abs, T=T))
        self.nodes.append(n)
        return n

    def branch(
        self,
        a: Node,
        b: Node,
        law: Law,
        thermal: ThermalMap | None = None,
        label: str | None = None,
    ) -> Branch:
        """A new branch from ``a`` to ``b`` (forward flow direction) with ``law``.

        Args:
            a: Upstream node in the forward direction.
            b: Downstream node.
            law: A monotone law from :mod:`worldparts.laws`.
            thermal: Optional ``(T_upstream_K, m) -> T_downstream_K`` map.
            label: Local label for messages.
        """
        lbl = f"{self.component}.{label or f'branch{len(self.branches)}'}"
        br = self._net.add_branch(Branch(a, b, law, thermal, lbl))
        self.branches.append(br)
        return br

    def injection(self, node: Node, m: float, T: float | None = None, label: str = "") -> Injection:
        """A fixed mass inflow ``m`` (kg/s) into ``node`` with temperature ``T`` (K)."""
        inj = self._net.add_injection(Injection(node, m, T, f"{self.component}.{label}"))
        self.injections.append(inj)
        return inj


class NetworkView:
    """Read access to a solved network for one component (all values SI)."""

    def __init__(self, solution: NetworkSolution, builder: NetworkBuilder) -> None:
        self.solution = solution
        self._b = builder

    def p(self, node: Node) -> float | None:
        """Absolute pressure of ``node`` in Pa (None if undetermined)."""
        v = float(self.solution.p[node.index])
        return v if math.isfinite(v) else None

    def T(self, node: Node) -> float | None:
        """Temperature of ``node`` in K (None when no water flows into it)."""
        return self.solution.T[node.index]

    def m(self, branch: Branch) -> float:
        """Mass flow of ``branch`` in kg/s (positive from ``a`` to ``b``)."""
        return float(self.solution.m[branch.index])

    def dp(self, branch: Branch) -> float:
        """``p_a - p_b`` of ``branch`` in Pa."""
        return float(self.solution.p[branch.a.index] - self.solution.p[branch.b.index])

    def port_connected(self, port: str) -> bool:
        """True when ``port`` is connected to another component (False when capped)."""
        return self._b.is_connected(port)

    def port_p(self, port: str) -> float | None:
        """Absolute pressure at a port in Pa."""
        return self.p(self._b.port(port))

    def port_T(self, port: str) -> float | None:
        """Temperature at a port node in K (None without inflow)."""
        return self.T(self._b.port(port))

    def port_m_flow(self, port: str) -> float:
        """Mass flow into the component through ``port`` in kg/s (Modelica sign)."""
        node = self._b.port(port)
        total = 0.0
        for br in self._b.branches:
            if br.a is node:
                total += float(self.solution.m[br.index])
            if br.b is node:
                total -= float(self.solution.m[br.index])
        for inj in self._b.injections:
            if inj.node is node:
                total -= inj.m
        return total


class Component:
    """Base class of component implementations.

    Attributes:
        manifest: The manifest, bound by the catalogue when the class is resolved.
        name: Instance name.
        parameters: Parameter values in SI (tables as :class:`worldparts.manifest.Table`,
            strings and booleans as is).
        inputs: Input values in SI.
        states: State values in SI.
    """

    manifest: ClassVar[Manifest]
    #: Length in s of the simulation step over which the current solution will be integrated
    #: (set by ``System.simulate`` before ``update_laws()``; at the last sample, the length of
    #: the step that led to it). None in a steady ``solve()``. Storage components use it to
    #: keep an explicit step from taking more than they hold.
    time_step: float | None = None

    def __init__(self, name: str, parameters: dict[str, Any], inputs: dict[str, float]) -> None:
        self.name = name
        self.parameters: dict[str, Any] = dict(parameters)
        self.inputs: dict[str, float] = dict(inputs)
        self.states: dict[str, float] = {}
        self.init_states()

    # -- properties -------------------------------------------------------------------------
    @property
    def medium(self) -> Medium:
        """The medium of the component's ports (water in v0.1)."""
        ports = list(self.manifest.ports.values())
        return get_medium(ports[0].medium) if ports else WATER

    @property
    def rho(self) -> float:
        """Density in kg/m3."""
        return self.medium.rho

    @property
    def cp(self) -> float:
        """Specific heat in J/(kg K)."""
        return self.medium.cp

    @property
    def mu(self) -> float:
        """Dynamic viscosity in Pa s."""
        return self.medium.mu

    # -- hooks ------------------------------------------------------------------------------
    @classmethod
    def check_parameters(cls, parameters: Mapping[str, Any]) -> list[str]:
        """Cross-parameter validation on SI values; return problem messages (empty if OK).

        Called by the system before a parameter change is accepted. Single-parameter limits
        are already enforced from the manifest.
        """
        return []

    @classmethod
    def check_states(cls, parameters: Mapping[str, Any], states: Mapping[str, Any]) -> list[str]:
        """Cross-checks of state values against parameters (SI); return problem messages.

        Called by the system before a batch that sets states or parameters is accepted, with
        the trial values (after any re-initialisation), and by ``check()``. For example a
        tank level must not exceed the tank height. Single-state limits are already enforced
        from the manifest.
        """
        return []

    def init_states(self) -> None:
        """Set states to their initial values (default: manifest ``default`` or 0).

        Called at construction, by ``System.reset_states()`` and, when a parameter batch
        changes the initial value of a state, to re-initialise that state (outside
        simulations). Only write ``self.states``: the system also calls it to find the
        initial values.
        """
        for name, spec in self.manifest.states.items():
            self.states[name] = spec.to_si(spec.default) if spec.default is not None else 0.0

    def build(self, nb: NetworkBuilder) -> None:
        """Create internal nodes and branches; keep the returned handles."""
        raise NotImplementedError(f"{type(self).__name__}.build() is not implemented.")

    def update_laws(self) -> None:
        """Refresh law coefficients, fixed-node pressures and temperatures from the current
        parameters, inputs and states. Called before every network solve."""

    def settle(self) -> None:
        """Set ``steady: settle`` states to their equilibrium (steady solve)."""

    def start_simulation(self) -> None:
        """Called once at the start of every ``simulate()`` run, before the first sample.

        Reset per-run accumulators here (for example a spill or energy counter). States are
        not reset: a simulation continues from the current state.
        """

    def update_fast_states(self, dt: float) -> None:
        """Advance fast states (actuator lags) by ``dt`` seconds.

        In a simulation step from ``t0`` to ``t`` this is called twice: first with
        ``dt = t - t0``, before the events due at ``t`` are applied, so the lag acts on the
        command that held during ``(t0, t]``; then with ``dt = 0`` after the events, which
        must leave lagged states unchanged and apply only instantaneous responses (for
        :func:`first_order` with ``tau = 0`` that is the new target). The default settles
        instantly (calls :meth:`settle`).
        """
        self.settle()

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """SI values for every observable declared in the manifest."""
        raise NotImplementedError(f"{type(self).__name__}.observables() is not implemented.")

    def integrate(self, dt: float, sol: NetworkView) -> None:
        """Advance storage states by ``dt`` with explicit Euler (simulation only)."""

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """Code-emitted warnings (declared in the manifest ``warnings`` list)."""
        return []

    # -- helpers ----------------------------------------------------------------------------
    def warning(self, code: str, message: str | None = None) -> ComponentWarning:
        """Create a warning for a code declared in the manifest.

        Raises:
            ContractError: When ``code`` is not declared in the manifest.
        """
        spec = self.manifest.warnings.get(code)
        if spec is None:
            raise ContractError(
                f"{self.manifest.id}: component code emitted undeclared warning '{code}'. "
                "Declare it in the manifest 'warnings' list. "
                + format_choices(code, self.manifest.warnings)
            )
        return ComponentWarning(self.name, code, spec.severity, message or spec.description)

    def __repr__(self) -> str:
        mid = getattr(self, "manifest", None)
        return f"<{type(self).__name__} {self.name!r} ({mid.id if mid else '?'})>"
