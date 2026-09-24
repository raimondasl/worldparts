"""Open atmospheric tank with bottom ports and an optional top inlet (design 8.9, 13.5).

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

Top-fed inlet (design 13.5): with ``inlet_height > 0`` the ``inlet`` port discharges freely
at that height above the bottom. Its gate then ends at a second fixed node, the pipe mouth,
at ``P_ATM + rho * g * max(level, inlet_height)``: below the mouth the inflow sees a constant
back-pressure and does not depend on the level; once the mouth is submerged the tank acts
as with a bottom inlet. Backflow out through the inlet is blocked while the level is below
the mouth (leakage only in a steady solve, closed in a simulation, where the inlet may draw
at most the water above the mouth over a step). With ``inlet_height = 0`` both ports end at
the tank node, as in v0.1.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import GateLaw, kv_to_k
from worldparts.media import G
from worldparts.network import Branch, Node
from worldparts.results import ComponentWarning
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
#: Suction (Pa below atmospheric, about 1 cm of water) at the port of an empty tank above
#: which the ``drawing_air`` warning is raised: an empty open tank cannot hold its outlet
#: below atmospheric pressure, air enters instead.
SUCTION_TOL = 100.0
#: Fraction of the simulation outflow cap at which the cap counts as binding (the tank is
#: being drawn empty within one step).
CAP_BINDING = 0.999
PORTS = ("inlet", "outlet")


class Tank(Component):
    """Open tank: level-dependent pressure at the bottom ports, spill at the rim, mixing."""

    node: Node
    #: Fixed node at the inlet pipe's mouth; the tank node itself when ``inlet_height`` is 0.
    inlet_node: Node
    gates: dict[str, GateLaw]
    branches: dict[str, Branch]
    #: Spill rate (m3/s) over the last integrated step.
    _overflow_rate: float = 0.0
    #: Ports connected in the current network (they share the outflow cap).
    _connected: list[str]

    @classmethod
    def check_parameters(cls, parameters: Mapping[str, Any]) -> list[str]:
        """The initial level and the inlet height must not exceed the tank height."""
        level, height = float(parameters["initial_level"]), float(parameters["height"])
        out = []
        if level > height * (1.0 + 1e-12):
            out.append(f"initial_level ({level:g} m) must not exceed height ({height:g} m).")
        inlet = float(parameters.get("inlet_height", 0.0))
        if inlet > height * (1.0 + 1e-12):
            out.append(
                f"inlet_height ({inlet:g} m) must not exceed height ({height:g} m): the top "
                "inlet discharges inside the tank, at or below the rim."
            )
        return out

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

    @property
    def inlet_height(self) -> float:
        """Height (m) of the inlet pipe's mouth above the bottom; 0 for a bottom inlet."""
        return float(self.parameters.get("inlet_height", 0.0))

    def mouth_height(self, port: str) -> float:
        """Height (m) above the bottom at which ``port`` meets the water (0 at the bottom)."""
        return self.inlet_height if port == "inlet" else 0.0

    def dry(self, port: str) -> bool:
        """True when ``port`` cannot draw water: its mouth is above the water surface (or,
        for a bottom port, the tank is empty)."""
        level = float(self.states["level"])
        mouth = self.mouth_height(port)
        return level <= EMPTY_LEVEL if mouth <= 0.0 else level < mouth

    # -- network ---------------------------------------------------------------------------
    def build(self, nb: NetworkBuilder) -> None:
        """Fixed tank node and one gate branch ``port -> tank`` per port (positive inflow).

        With ``inlet_height > 0`` the inlet's gate ends at a second fixed node, the inlet
        pipe's mouth (:attr:`inlet_node`).
        """
        temp = float(self.states["temperature"])
        self.node = nb.fixed_node("tank", P_ATM, temp)
        self.inlet_node = self.node
        if self.inlet_height > 0.0:
            self.inlet_node = nb.fixed_node("inlet_mouth", P_ATM, temp)
        self.gates = {}
        self.branches = {}
        self._connected = [port for port in PORTS if nb.is_connected(port)]
        for port in PORTS:
            gate = GateLaw(1.0)
            self.gates[port] = gate
            target = self.inlet_node if port == "inlet" else self.node
            self.branches[port] = nb.branch(nb.port(port), target, gate, label=f"{port}_gate")

    def update_laws(self) -> None:
        """Bottom pressure from the level; outflow blocked when empty, capped in a simulation.

        Steady solve: outflow blocked (leakage only) through a dry port (the tank empty, or
        a top inlet's mouth above the water), else open. Simulation (``time_step`` set): the
        outflow of each connected port is capped at its share of the water above its mouth
        (above ``DRAW_LEVEL`` for a bottom port) divided by the step, so no step can draw
        more than the tank holds; the cap binds only when the step would nearly empty the
        tank (or uncover the mouth), and it is 0 (gate closed) through a dry port. Dry ports
        take no share of the cap.

        A top inlet's mouth node is at ``P_ATM + rho * g * max(level, inlet_height)``.
        """
        level = float(self.states["level"])
        temp = float(self.states["temperature"])
        self.node.p = P_ATM + self.rho * G * level
        self.node.T = temp
        if self.inlet_node is not self.node:
            self.inlet_node.p = P_ATM + self.rho * G * max(level, self.inlet_height)
            self.inlet_node.T = temp
        k = kv_to_k(float(self.parameters["port_kv"]), self.rho)
        simulating = self.time_step is not None and self.time_step > 0.0
        connected = getattr(self, "_connected", list(PORTS))
        drawing = max(len([port for port in connected if not self.dry(port)]), 1)
        for port, gate in self.gates.items():
            gate.k = k
            blocked: str | None = None
            limit: float | None = None
            if simulating:
                assert self.time_step is not None
                blocked = "reverse"
                floor = max(DRAW_LEVEL, self.mouth_height(port))
                drawable = 0.0
                if not self.dry(port):
                    drawable = self.area * max(level - floor, 0.0) * self.rho  # kg
                limit = drawable / (self.time_step * drawing)
            elif self.dry(port):
                blocked = "reverse"
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

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``drawing_air`` when the tank cannot supply what a port draws.

        Raised for each connected port held more than :data:`SUCTION_TOL` below atmospheric
        pressure (design 8.9, the signature of a pump losing prime) while the port is dry
        (the tank empty, ``level <= EMPTY_LEVEL``, or a top inlet's mouth above the water)
        or, in a simulation, while the port's outflow is held at the cap that stops a step
        drawing more water than there is above its mouth. A port with no water above it is
        open to the air, so a real pump or siphon on it would draw air (lose prime, run dry,
        break the siphon); the model keeps the line full of water and lets the port pressure
        fall instead, so the results downstream are not physical.

        A dry top inlet at or above atmospheric pressure is not flagged: the supply cannot
        lift water to the mouth (or a stopped fill pump holds nothing), nothing flows either
        way, and that result is exact.
        """
        level = float(self.states["level"])
        out: list[ComponentWarning] = []
        for port in PORTS:
            p = sol.port_p(port)
            mouth = self.mouth_height(port)
            if p is None or not sol.port_connected(port) or p >= P_ATM - SUCTION_TOL:
                continue
            gate = self.gates[port]
            outflow = -sol.m(self.branches[port])  # kg/s out of the tank
            capped = gate.limit is not None and outflow >= CAP_BINDING * gate.limit
            if not self.dry(port) and not capped:
                continue
            if mouth > 0.0:
                state = (
                    f"filled to {level:.4g} m, below the top inlet's mouth at {mouth:.4g} m"
                    if level < mouth
                    else f"filled to {level:.4g} m, just above the top inlet's mouth"
                )
                pipe = "the inlet pipe"
                suction = f"{(p - P_ATM) / 1e5:.3g} bar gauge at the tank bottom"
            else:
                state = "empty" if level <= EMPTY_LEVEL else f"nearly empty ({level * 1000:.3g} mm)"
                pipe = "the outlet"
                suction = f"{(p - P_ATM) / 1e5:.3g} bar gauge"
            out.append(
                self.warning(
                    "drawing_air",
                    f"The tank is {state} and port '{port}' is under suction "
                    f"({suction}): air would "
                    f"enter {pipe}, so a pump drawing from it loses prime and runs dry and a "
                    "siphon breaks. The model keeps the line full of water, so the results "
                    "downstream of this port are not physical.",
                )
            )
        return out

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
