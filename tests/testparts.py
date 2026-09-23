"""A test-only component that exercises the core features later components rely on.

``HeatedTank`` is an open tank with an electric heating element in its inlet line:

- a **table parameter** (``heater_curve``: heating power as a function of level),
- a **string enum parameter** (``heater``: on or off),
- **hold states** (``level`` and ``temperature``) integrated over time with explicit Euler,
- an **internal fixed node** whose pressure changes with the level between steps,
- a **thermal map** on the inlet branch (the heating element),
- a **gate law** whose blocked direction switches when the tank runs empty,
- a **code-emitted warning** (``overheat``) and an envelope warning (``tank_empty``).
"""

from __future__ import annotations

import math

import numpy as np

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import GateLaw, QuadraticResistance, kv_to_k
from worldparts.media import G
from worldparts.network import Branch, Node
from worldparts.results import ComponentWarning
from worldparts.units import P_ATM

OVERHEAT_K = 273.15 + 60.0
EMPTY_LEVEL = 0.001


class HeatedTank(Component):
    """Open tank fed through a heating element; outlet through a gate."""

    tank: Node
    inlet_branch: Branch
    outlet_branch: Branch
    inlet_law: QuadraticResistance
    gate: GateLaw

    @classmethod
    def check_parameters(cls, parameters: dict) -> list[str]:
        """The initial level must not exceed the tank height."""
        if parameters["initial_level"] > parameters["height"]:
            return [
                f"initial_level ({parameters['initial_level']:g} m) must not exceed height "
                f"({parameters['height']:g} m)."
            ]
        return []

    def init_states(self) -> None:
        """Level and temperature start from their parameters."""
        self.states["level"] = float(self.parameters["initial_level"])
        self.states["temperature"] = float(self.parameters["initial_temperature"])

    # -- network ---------------------------------------------------------------------------
    def build(self, nb: NetworkBuilder) -> None:
        """Tank node (fixed), heated inlet branch and gated outlet branch."""
        self.tank = nb.fixed_node("tank", P_ATM, self.states["temperature"])
        self.inlet_law = QuadraticResistance(1.0)
        self.gate = GateLaw(1.0)
        self.inlet_branch = nb.branch(
            nb.port("inlet"), self.tank, self.inlet_law, thermal=self._heat, label="heater"
        )
        self.outlet_branch = nb.branch(self.tank, nb.port("outlet"), self.gate, label="outlet")

    def heater_power(self) -> float:
        """Heating power in W from the table at the current level (0 when off)."""
        if self.parameters["heater"] != "on":
            return 0.0
        table = self.parameters["heater_curve"]
        return float(np.interp(self.states["level"], table["level"], table["power"]))

    def _heat(self, t_in: float, m: float) -> float:
        """Thermal map of the heating element (forward flow only)."""
        if m <= 1e-9:
            return t_in
        return min(t_in + self.heater_power() / (m * self.cp), 373.15)

    def update_laws(self) -> None:
        """Tank pressure from the level; gate blocks outflow when empty."""
        level = self.states["level"]
        self.tank.p = P_ATM + self.rho * G * level
        self.tank.T = self.states["temperature"]
        k = kv_to_k(self.parameters["port_kv"], self.rho)
        self.inlet_law.k = k
        self.gate.k = k
        self.gate.blocked_direction = "forward" if level <= EMPTY_LEVEL else None

    # -- results ---------------------------------------------------------------------------
    def _inflow_temperature(self, sol: NetworkView) -> float | None:
        m = sol.m(self.inlet_branch)
        t_port = sol.port_T("inlet")
        if m <= 1e-9 or t_port is None:
            return None
        return self._heat(t_port, m)

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Net inflow, heating power and heated inflow temperature."""
        m_in = sol.m(self.inlet_branch)
        m_out = sol.m(self.outlet_branch)
        return {
            "net_inflow": (m_in - m_out) / self.rho,
            "heater_power": self.heater_power(),
            "inlet_temperature": self._inflow_temperature(sol),
        }

    def integrate(self, dt: float, sol: NetworkView) -> None:
        """Explicit Euler for the level and the well-mixed temperature."""
        area = float(self.parameters["area"])
        m_in = sol.m(self.inlet_branch)
        m_out = sol.m(self.outlet_branch)
        inflows: list[tuple[float, float]] = []  # (volume flow m3/s, temperature K)
        t_in = self._inflow_temperature(sol)
        if m_in > 0 and t_in is not None:
            inflows.append((m_in / self.rho, t_in))
        t_back = sol.port_T("outlet")
        if m_out < 0 and t_back is not None:
            inflows.append((-m_out / self.rho, t_back))
        outflow = (max(-m_in, 0.0) + max(m_out, 0.0)) / self.rho
        v_old = area * self.states["level"]
        v_new = v_old + (sum(q for q, _ in inflows) - outflow) * dt
        temp = self.states["temperature"]
        if inflows and v_new > 1e-12:
            energy = (v_old - outflow * dt) * temp + sum(q * dt * t for q, t in inflows)
            temp = energy / v_new
        self.states["level"] = max(0.0, v_new / area)
        self.states["temperature"] = temp

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``overheat`` when the heated inflow exceeds 60 degC."""
        t_in = self._inflow_temperature(sol)
        if t_in is not None and t_in > OVERHEAT_K and math.isfinite(t_in):
            return [self.warning("overheat")]
        return []
