"""Valves: two-way control valve and check valve (design 8.4 and 8.5)."""

from __future__ import annotations

import math

from worldparts.components.base import Component, NetworkBuilder, NetworkView, first_order
from worldparts.laws import CheckValveLaw, QuadraticResistance, kv_to_k
from worldparts.network import Branch

__all__ = ["CheckValve", "TwoWayValve", "characteristic"]


def characteristic(kind: str, y: float, leakage: float, rangeability: float) -> float:
    """Relative flow coefficient phi(y) of a valve at position ``y`` in [0, 1].

    Args:
        kind: ``linear``, ``equal_percentage`` or ``quick_opening``.
        y: Valve position.
        leakage: Relative Kv when closed.
        rangeability: Ratio R of the equal-percentage characteristic.
    """
    y = min(1.0, max(0.0, y))
    if kind == "linear":
        shape = y
    elif kind == "equal_percentage":
        r = rangeability
        shape = (r ** (y - 1.0) - 1.0 / r) / (1.0 - 1.0 / r)
    elif kind == "quick_opening":
        shape = math.sqrt(y)
    else:
        raise ValueError(f"Unknown valve characteristic '{kind}'.")
    return leakage + (1.0 - leakage) * shape


class TwoWayValve(Component):
    """Throttling valve with Kv sizing, inherent characteristic, leakage and actuator lag."""

    law: QuadraticResistance
    branch: Branch

    def init_states(self) -> None:
        """The valve starts at its commanded opening."""
        self.states["position"] = float(self.inputs["opening"])

    def settle(self) -> None:
        """Steady state: the position equals the command."""
        self.states["position"] = float(self.inputs["opening"])

    def update_fast_states(self, dt: float) -> None:
        """First-order actuator lag with time constant ``actuator_time``."""
        tau = float(self.parameters["actuator_time"])
        self.states["position"] = first_order(
            self.states["position"], float(self.inputs["opening"]), tau, dt
        )

    def effective_kv(self) -> float:
        """Effective Kv in SI (m3/s) at the current position."""
        p = self.parameters
        phi = characteristic(
            p["characteristic"], self.states["position"], p["leakage"], p["rangeability"]
        )
        return p["kv"] * phi

    def build(self, nb: NetworkBuilder) -> None:
        """One quadratic-resistance branch ``port_a -> port_b``."""
        self.law = QuadraticResistance(1.0)
        self.branch = nb.branch(nb.port("port_a"), nb.port("port_b"), self.law, label="valve")

    def update_laws(self) -> None:
        """Mass-flow coefficient from the effective Kv (regularisation scales with it)."""
        self.law.k = kv_to_k(self.effective_kv(), self.rho)

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow, pressure drop, effective Kv and position."""
        return {
            "volume_flow": sol.m(self.branch) / self.rho,
            "pressure_drop": sol.dp(self.branch),
            "effective_kv": self.effective_kv(),
            "position": self.states["position"],
        }


class CheckValve(Component):
    """Non-return valve: Kv forward, Kv times leakage in reverse."""

    law: CheckValveLaw
    branch: Branch

    def build(self, nb: NetworkBuilder) -> None:
        """One check-valve branch ``port_a -> port_b``."""
        self.law = CheckValveLaw(1.0, 1e-6)
        self.branch = nb.branch(nb.port("port_a"), nb.port("port_b"), self.law, label="check")

    def update_laws(self) -> None:
        """Coefficients from ``kv`` and ``leakage``."""
        self.law.k = kv_to_k(self.parameters["kv"], self.rho)
        self.law.leakage = self.parameters["leakage"]

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow and pressure drop."""
        return {
            "volume_flow": sol.m(self.branch) / self.rho,
            "pressure_drop": sol.dp(self.branch),
        }
