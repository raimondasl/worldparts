"""Instantaneous (tankless, flow-switched) water heater (design 8.7).

One quadratic-resistance branch ``inlet -> outlet`` (Kv ``kv``) carries a thermal map: for
forward flow above ``activation_flow`` the heater raises the water towards ``setpoint`` with
at most ``enabled * max_power``::

    T_out = max(T_in, min(setpoint, T_in + enabled * max_power / (m * cp)))

Otherwise (reverse flow, or flow at or below ``activation_flow``) ``T_out = T_in``. The outer
``max`` is a physical guard: a heater cannot cool water that already arrives above the
setpoint (design 8.7 writes the map without it).
"""

from __future__ import annotations

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import QuadraticResistance, kv_to_k
from worldparts.network import Branch

__all__ = ["InstantaneousWaterHeater"]


class InstantaneousWaterHeater(Component):
    """Flow-switched tankless heater with a power limit and an outlet setpoint."""

    law: QuadraticResistance
    branch: Branch

    def build(self, nb: NetworkBuilder) -> None:
        """One heated branch ``inlet -> outlet``."""
        self.law = QuadraticResistance(1.0)
        self.branch = nb.branch(
            nb.port("inlet"), nb.port("outlet"), self.law, thermal=self.heat, label="heater"
        )

    def update_laws(self) -> None:
        """Mass-flow coefficient from ``kv``."""
        self.law.k = kv_to_k(self.parameters["kv"], self.rho)

    def firing(self, m: float) -> bool:
        """True when the flow switch closes: forward flow above ``activation_flow``."""
        return m > 0.0 and m / self.rho > float(self.parameters["activation_flow"])

    def heat(self, t_in: float, m: float) -> float:
        """Thermal map ``(T_in K, m kg/s) -> T_out K`` of the heating element."""
        if not self.firing(m):
            return t_in
        p = self.parameters
        rise = float(self.inputs["enabled"]) * float(p["max_power"]) / (m * self.cp)
        return max(t_in, min(float(p["setpoint"]), t_in + rise))

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow, outlet temperature, temperature rise and heat rate (SI).

        For forward flow the outlet temperature is the thermal map applied to the inlet
        temperature. For reverse flow nothing is heated and the water leaving through the
        inlet has the temperature arriving at the outlet port. Without any defined upstream
        temperature the outlet temperature and rise are None and the heat rate is 0.
        """
        m = sol.m(self.branch)
        t_in = sol.port_T("inlet") if m > 0.0 else sol.port_T("outlet")
        if t_in is None:
            return {
                "volume_flow": m / self.rho,
                "outlet_temperature": None,
                "temperature_rise": None,
                "heat_rate": 0.0,
            }
        t_out = self.heat(t_in, m)
        return {
            "volume_flow": m / self.rho,
            "outlet_temperature": t_out,
            "temperature_rise": t_out - t_in,
            "heat_rate": max(m, 0.0) * self.cp * (t_out - t_in),
        }
