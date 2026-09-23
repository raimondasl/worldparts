"""Boundary components: supply (ideal pressure source) and drain (open discharge)."""

from __future__ import annotations

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import ideal_connection
from worldparts.network import Branch, Node
from worldparts.units import P_ATM

__all__ = ["Drain", "Supply"]


class _Boundary(Component):
    """A fixed node joined to the single port ``port`` by a near-ideal branch."""

    source: Node
    joint: Branch

    def _pressure(self) -> float:
        raise NotImplementedError

    def build(self, nb: NetworkBuilder) -> None:
        """Internal fixed node ``source`` joined to ``port``."""
        self.source = nb.fixed_node("source", self._pressure(), self.parameters["temperature"])
        self.joint = nb.branch(self.source, nb.port("port"), ideal_connection(), label="joint")

    def update_laws(self) -> None:
        """Refresh the fixed pressure and temperature."""
        self.source.p = self._pressure()
        self.source.T = self.parameters["temperature"]

    def _outflow(self, sol: NetworkView) -> float:
        """Volume flow in m3/s from the boundary into the network."""
        return -sol.port_m_flow("port") / self.rho


class Supply(_Boundary):
    """Ideal pressure source: mains, reservoir or pressurised line (design 8.1)."""

    def _pressure(self) -> float:
        return float(self.parameters["pressure"])

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """``volume_flow``: positive when the supply delivers water into the network."""
        return {"volume_flow": self._outflow(sol)}


class Drain(_Boundary):
    """Open discharge to atmosphere: basin, open channel or drain (design 8.2)."""

    def _pressure(self) -> float:
        return P_ATM

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """``volume_flow``: positive into the drain."""
        return {"volume_flow": -self._outflow(sol)}
