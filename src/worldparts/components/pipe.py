"""Pipe: Darcy-Weisbach with the Churchill (1977) friction factor (design 8.3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import PipeLaw
from worldparts.media import vapour_pressure
from worldparts.network import Branch
from worldparts.results import ComponentWarning
from worldparts.units import P_ATM

__all__ = ["Pipe"]

#: Temperature (K) assumed for the vapour-pressure check where no water flows (capped end).
_DEFAULT_T = 293.15


class Pipe(Component):
    """Straight pipe with friction, minor losses and static head."""

    law: PipeLaw
    branch: Branch

    @classmethod
    def check_parameters(cls, parameters: Mapping[str, Any]) -> list[str]:
        """The wall roughness must be below half the diameter.

        The Churchill (1977) correlation is fitted up to a relative roughness of about 0.05
        (the ``high_relative_roughness`` envelope warning flags the extrapolation) and
        becomes singular near 3.7, where it would let a rougher pipe pass more water.
        """
        d, e = float(parameters["diameter"]), float(parameters["roughness"])
        if e >= d / 2.0:
            return [
                f"roughness ({e * 1000:g} mm) must be less than half the diameter "
                f"({d * 1000:g} mm); a relative roughness this large is not a pipe."
            ]
        return []

    def build(self, nb: NetworkBuilder) -> None:
        """One branch ``port_a -> port_b`` with a :class:`PipeLaw`."""
        self.law = PipeLaw(1.0, 0.01, rho=self.rho, mu=self.mu)
        self.branch = nb.branch(nb.port("port_a"), nb.port("port_b"), self.law, label="pipe")

    def update_laws(self) -> None:
        """Copy geometry from the parameters into the law."""
        p = self.parameters
        self.law.length = p["length"]
        self.law.diameter = p["diameter"]
        self.law.roughness = p["roughness"]
        self.law.minor_loss = p["minor_loss"]
        self.law.height_difference = p["height_difference"]

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow, velocity, pressure drop, Reynolds number and friction factor."""
        m = sol.m(self.branch)
        return {
            "volume_flow": m / self.rho,
            "velocity": self.law.velocity(m),
            "pressure_drop": sol.dp(self.branch),
            "reynolds": self.law.reynolds(m),
            "friction_factor": self.law.friction_factor(m),
        }

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``below_vapour_pressure`` when an end of the pipe is below the vapour pressure.

        Static head (a riser or a siphon) can take the modelled pressure below the vapour
        pressure, even below zero absolute. A real pipe would cavitate or the water column
        would separate there, so the result is not physical. The water temperature at the
        port is used, or the other end's temperature, or 20 degC where no water flows.
        """
        temps = [sol.port_T(port) for port in ("port_a", "port_b")]
        known = [t for t in temps if t is not None]
        out: list[ComponentWarning] = []
        for port, t in zip(("port_a", "port_b"), temps, strict=True):
            p_abs = sol.port_p(port)
            if p_abs is None:
                continue
            t_use = t if t is not None else (max(known) if known else _DEFAULT_T)
            p_v = vapour_pressure(min(max(t_use, 273.15), 647.0))
            if p_abs < p_v:
                out.append(
                    self.warning(
                        "below_vapour_pressure",
                        f"The pressure at {self.name}.{port} is {(p_abs - P_ATM) / 1e5:.3g} bar "
                        f"gauge ({p_abs / 1e5:.3g} bar absolute), below the vapour pressure of "
                        f"water at {t_use - 273.15:.3g} degC ({p_v / 1e5:.3g} bar absolute). A "
                        "real pipe would cavitate or its water column would separate; the "
                        "result is not physical.",
                    )
                )
                break
        return out
