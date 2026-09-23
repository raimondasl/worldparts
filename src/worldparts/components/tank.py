"""Open atmospheric tank with bottom ports (design 8.9).

The tank is an internal fixed-pressure node at ``P_ATM + rho * g * level`` whose outflow
temperature is the (perfectly mixed) tank temperature. Each port joins it through a
:class:`~worldparts.laws.GateLaw` with the port Kv; the gates block outflow from the tank
(leakage only) when it is empty. The level and temperature are storage states integrated
with explicit Euler; water above the rim is spilled and reported as ``overflow_rate``.

Explicit Euler must not take more water out of the tank over a step than it holds, or the
level clamp at 0 would create water (a pump drawing from a nearly empty break tank). In a
simulation the gates therefore cap the outflow of each connected port at its share of the
water above the empty level divided by the step (``GateLaw.limit``); the tank then settles
just above the empty level with the pump delivering what flows in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import GateLaw, kv_to_k
from worldparts.media import G
from worldparts.network import Branch, Node
from worldparts.units import P_ATM

__all__ = ["Tank"]

#: Level (m) at or below which the tank counts as empty and outflow is blocked.
EMPTY_LEVEL = 0.001
#: Level (m) down to which a simulation step may draw the tank: just below EMPTY_LEVEL, so a
#: step that takes all the water it may leaves the tank empty (level <= EMPTY_LEVEL) whatever
#: the rounding.
DRAW_LEVEL = EMPTY_LEVEL * (1.0 - 1e-9)
#: Volume flows (m3/s) below this magnitude do not count as overflow (solver noise).
OVERFLOW_TOL = 1e-9
PORTS = ("inlet", "outlet")


class Tank(Component):
    """Open tank: level-dependent pressure at the bottom ports, spill at the rim, mixing."""

    node: Node
    gates: dict[str, GateLaw]
    branches: dict[str, Branch]
    #: Spill rate (m3/s) over the last integrated step.
    _overflow_rate: float = 0.0
    #: Ports connected in the current network (they share the outflow cap).
    _connected: list[str]

    @classmethod
    def check_parameters(cls, parameters: Mapping[str, Any]) -> list[str]:
        """The initial level must not exceed the tank height."""
        level, height = float(parameters["initial_level"]), float(parameters["height"])
        if level > height * (1.0 + 1e-12):
            return [f"initial_level ({level:g} m) must not exceed height ({height:g} m)."]
        return []

    @classmethod
    def check_states(cls, parameters: Mapping[str, Any], states: Mapping[str, Any]) -> list[str]:
        """The level must not exceed the tank height (water above the rim spills)."""
        level, height = float(states["level"]), float(parameters["height"])
        if level > height * (1.0 + 1e-12):
            return [
                f"level ({level:g} m) must not exceed height ({height:g} m): water above the "
                "rim spills. Lower the level, or raise the height in the same set_values batch."
            ]
        return []

    # -- states ----------------------------------------------------------------------------
    def init_states(self) -> None:
        """Level and temperature start from ``initial_level`` and ``initial_temperature``."""
        self.states["level"] = float(self.parameters["initial_level"])
        self.states["temperature"] = float(self.parameters["initial_temperature"])

    def settle(self) -> None:
        """Nothing settles (both states are ``hold``)."""

    def start_simulation(self) -> None:
        """Reset the spill rate at the start of every simulation."""
        self._overflow_rate = 0.0

    def update_fast_states(self, dt: float) -> None:
        """The tank has no fast states (level and temperature are storage states)."""

    @property
    def area(self) -> float:
        """Cross-section in m2."""
        return math.pi * float(self.parameters["diameter"]) ** 2 / 4.0

    # -- network ---------------------------------------------------------------------------
    def build(self, nb: NetworkBuilder) -> None:
        """Fixed tank node and one gate branch ``port -> tank`` per port (positive inflow)."""
        self.node = nb.fixed_node("tank", P_ATM, float(self.states["temperature"]))
        self.gates = {}
        self.branches = {}
        self._connected = [port for port in PORTS if nb.is_connected(port)]
        for port in PORTS:
            gate = GateLaw(1.0)
            self.gates[port] = gate
            self.branches[port] = nb.branch(nb.port(port), self.node, gate, label=f"{port}_gate")

    def update_laws(self) -> None:
        """Bottom pressure from the level; outflow blocked when empty, capped in a simulation.

        Steady solve: outflow blocked (leakage only) when ``level <= EMPTY_LEVEL``, else open.
        Simulation (``time_step`` set): the outflow of each connected port is capped at its
        share of the water above ``DRAW_LEVEL`` divided by the step, so no step can draw more
        than the tank holds; the cap binds only when the step would nearly empty the tank,
        and it is 0 (gate closed) once the tank is empty.
        """
        level = float(self.states["level"])
        self.node.p = P_ATM + self.rho * G * level
        self.node.T = float(self.states["temperature"])
        k = kv_to_k(float(self.parameters["port_kv"]), self.rho)
        blocked: str | None = None
        limit: float | None = None
        if self.time_step is not None and self.time_step > 0.0:
            blocked = "reverse"
            ports = max(len(getattr(self, "_connected", PORTS)), 1)
            drawable = self.area * max(level - DRAW_LEVEL, 0.0) * self.rho  # kg
            limit = drawable / (self.time_step * ports)
        elif level <= EMPTY_LEVEL:
            blocked = "reverse"
        for gate in self.gates.values():
            gate.k = k
            gate.blocked_direction = blocked
            gate.limit = limit

    # -- results ---------------------------------------------------------------------------
    def _net_inflow(self, sol: NetworkView) -> float:
        """Net volume inflow through the ports in m3/s."""
        return sum(sol.m(self.branches[port]) for port in PORTS) / self.rho

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Volume, fill fraction, net inflow and overflow rate.

        In a simulation ``overflow_rate`` is the spill over the step that led to the current
        sample (explicit Euler). In a steady solve it is the net inflow when the tank is full
        and filling (the excess spills over the rim), else 0.
        """
        level = float(self.states["level"])
        height = float(self.parameters["height"])
        net = self._net_inflow(sol)
        if self.time_step is not None:  # in a simulation
            overflow = self._overflow_rate
        else:
            full = level >= height * (1.0 - 1e-12)
            overflow = net if full and net > OVERFLOW_TOL else 0.0
        return {
            "volume": self.area * level,
            "fill_fraction": level / height,
            "net_inflow": net,
            "overflow_rate": overflow,
        }

    def integrate(self, dt: float, sol: NetworkView) -> None:
        """Explicit Euler for the level, spill above the rim and perfect mixing."""
        area = self.area
        temp = float(self.states["temperature"])
        inflows: list[tuple[float, float]] = []  # (volume flow m3/s, temperature K)
        outflow = 0.0
        for port in PORTS:
            q = sol.m(self.branches[port]) / self.rho
            if q > 0.0:
                t_in = sol.port_T(port)
                inflows.append((q, t_in if t_in is not None else temp))
            else:
                outflow -= q
        q_in = sum(q for q, _ in inflows)
        v_old = area * float(self.states["level"])
        v_new = v_old + (q_in - outflow) * dt
        remaining = max(v_old - outflow * dt, 0.0)
        mixed = remaining + q_in * dt
        if inflows and mixed > 1e-15:
            temp = (remaining * temp + sum(q * dt * t for q, t in inflows)) / mixed
        v_max = area * float(self.parameters["height"])
        spill = max(v_new - v_max, 0.0)
        self._overflow_rate = spill / dt if dt > 0 and spill / dt > OVERFLOW_TOL else 0.0
        v_new = min(v_new, v_max)
        self.states["level"] = max(v_new / area, 0.0)
        self.states["temperature"] = temp
