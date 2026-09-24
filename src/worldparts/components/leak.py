"""Leak: an orifice discharging to atmosphere from one port (design 13.4).

The leak is a sharp-edged orifice of equivalent diameter ``d`` and discharge coefficient
``Cd`` between its port and the atmosphere:

    Q = Cd * A * opening * sqrt(2 * dp / rho),   A = pi * d**2 / 4,

with ``dp`` the gauge pressure at the port. In mass-flow form ``m = k * sqrt(dp)`` with
``k = Cd * A * opening * sqrt(2 * rho)``, which is exactly the
:class:`~worldparts.laws.QuadraticResistance` with that coefficient, regularised at zero
flow like every other law (exactly quadratic above 0.2 % of the flow at 1 bar, that is above
about 0.4 Pa). A negative gauge pressure draws air or dirty water back into the network
through the same orifice (``backflow``); the model treats it as water at 20 degC.
"""

from __future__ import annotations

import math

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import QuadraticResistance
from worldparts.network import Branch, Node
from worldparts.units import P_ATM

__all__ = ["Leak", "orifice_coefficient"]

#: Temperature (K) of water drawn back into the network through the leak (backflow).
BACKFLOW_T = 293.15
#: Smallest area fraction used for the law: a closed leak (``opening = 0``) keeps 1e-6 of
#: its area, so the law stays strictly increasing (as a closed valve keeps its leakage).
CLOSED_FRACTION = 1e-6


def orifice_coefficient(
    diameter: float, discharge_coefficient: float, opening: float, rho: float
) -> float:
    """Mass-flow coefficient ``k`` (kg/(s Pa^0.5)) of an orifice: ``m = k * sqrt(dp)``.

    ``k = Cd * A * opening * sqrt(2 * rho)`` with ``A = pi * diameter**2 / 4`` (SI), which is
    ``Q = Cd * A * opening * sqrt(2 * dp / rho)`` in mass-flow form.
    """
    area = math.pi * diameter * diameter / 4.0
    return discharge_coefficient * area * opening * math.sqrt(2.0 * rho)


class Leak(Component):
    """Orifice from ``port`` to the atmosphere: burst or background leakage."""

    atm: Node
    law: QuadraticResistance
    branch: Branch

    def build(self, nb: NetworkBuilder) -> None:
        """Fixed atmospheric node and one orifice branch ``port -> atmosphere``."""
        self.atm = nb.fixed_node("atmosphere", P_ATM, BACKFLOW_T)
        self.law = QuadraticResistance(1.0)
        self.branch = nb.branch(nb.port("port"), self.atm, self.law, label="orifice")

    def area_fraction(self) -> float:
        """The open fraction of the orifice area used by the law (``opening``, at least
        :data:`CLOSED_FRACTION`)."""
        return max(float(self.inputs["opening"]), CLOSED_FRACTION)

    def update_laws(self) -> None:
        """Orifice coefficient from the diameter, ``Cd`` and the opening."""
        p = self.parameters
        self.law.k = orifice_coefficient(
            float(p["diameter"]),
            float(p["discharge_coefficient"]),
            self.area_fraction(),
            self.rho,
        )

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """``volume_flow`` out of the network (m3/s) and the port ``pressure`` (Pa absolute,
        reported in bar gauge)."""
        return {
            "volume_flow": sol.m(self.branch) / self.rho,
            "pressure": sol.port_p("port"),
        }
