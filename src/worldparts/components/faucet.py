"""Single-lever mixing faucet (design 8.6), the bathroom example.

Network inside the component::

    hot  --[kv_hot  * phi_h]--\\
                               C --[kv_spout]--> ATM (fixed node at P_ATM, water leaves)
    cold --[kv_cold * phi_c]--/

with ``phi_h = l + (1 - l) * lift * mix`` and ``phi_c = l + (1 - l) * lift * (1 - mix)``
(``l`` is the cartridge leakage). The mixed outlet temperature is the temperature of the
mixing node ``C``, which the network's mixing solver computes as the flow-weighted mean of
the water entering it (energy balance with constant ``cp``).
"""

from __future__ import annotations

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import QuadraticResistance, kv_to_k
from worldparts.network import Branch, Node
from worldparts.results import ComponentWarning
from worldparts.units import P_ATM

__all__ = ["CLOSED_LIFT", "MIN_DISCHARGE", "MixingFaucet"]

#: Lever lift (fraction) at or below which the faucet counts as closed (the ``closed`` mode).
CLOSED_LIFT = 0.001

#: Discharge in m3/s (0.06 L/min) below which the outlet temperature is undefined (None).
MIN_DISCHARGE = 1e-6

#: Nominal temperature (K) of the spout's atmospheric node. Water never flows from it into
#: the network in normal use (it would only on reverse flow into the spout, which needs a
#: sub-atmospheric supply, flagged by the manifest's back_siphonage warning), so the value only
#: matters in that pathological case.
_SPOUT_T = 293.15

_INLETS = ("hot", "cold")


class MixingFaucet(Component):
    """Single-lever basin or shower mixer discharging to atmosphere.

    Two cartridge branches (hot and cold inlets) meet at an internal mixing node, which
    discharges through the spout and aerator resistance to an internal fixed node at
    atmospheric pressure. Water leaving through the spout leaves the network.
    """

    hot_law: QuadraticResistance
    cold_law: QuadraticResistance
    spout_law: QuadraticResistance
    hot_branch: Branch
    cold_branch: Branch
    spout_branch: Branch
    mix_node: Node
    atm: Node

    # -- characteristic ---------------------------------------------------------------------
    def cartridge_kv(self) -> tuple[float, float]:
        """Effective Kv (SI, m3/s) of the hot and cold cartridge branches.

        ``kv_hot * (l + (1 - l) * lift * mix)`` and ``kv_cold * (l + (1 - l) * lift *
        (1 - mix))``; the leakage ``l`` keeps both strictly positive.
        """
        p, u = self.parameters, self.inputs
        leak = float(p["leakage"])
        lift = min(1.0, max(0.0, float(u["lift"])))
        mix = min(1.0, max(0.0, float(u["mix"])))
        kv_h = p["kv_hot"] * (leak + (1.0 - leak) * lift * mix)
        kv_c = p["kv_cold"] * (leak + (1.0 - leak) * lift * (1.0 - mix))
        return kv_h, kv_c

    # -- network ----------------------------------------------------------------------------
    def build(self, nb: NetworkBuilder) -> None:
        """Mixing node ``C``, two cartridge branches into it and the spout to atmosphere."""
        self.hot_law = QuadraticResistance(1.0)
        self.cold_law = QuadraticResistance(1.0)
        self.spout_law = QuadraticResistance(1.0)
        self.mix_node = nb.node("mix")
        self.atm = nb.fixed_node("spout", P_ATM, _SPOUT_T)
        self.hot_branch = nb.branch(nb.port("hot"), self.mix_node, self.hot_law, label="hot")
        self.cold_branch = nb.branch(nb.port("cold"), self.mix_node, self.cold_law, label="cold")
        self.spout_branch = nb.branch(self.mix_node, self.atm, self.spout_law, label="spout")

    def update_laws(self) -> None:
        """Cartridge coefficients from lift and mix; spout coefficient from ``kv_spout``."""
        kv_h, kv_c = self.cartridge_kv()
        self.hot_law.k = kv_to_k(kv_h, self.rho)
        self.cold_law.k = kv_to_k(kv_c, self.rho)
        self.spout_law.k = kv_to_k(self.parameters["kv_spout"], self.rho)
        self.atm.p = P_ATM
        self.atm.T = _SPOUT_T

    # -- results ----------------------------------------------------------------------------
    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Discharge, mixed outlet temperature and the flow through each inlet (SI)."""
        q = sol.m(self.spout_branch) / self.rho
        temperature = sol.T(self.mix_node) if q >= MIN_DISCHARGE else None
        return {
            "flow": q,
            "temperature": temperature,
            "hot_flow": sol.m(self.hot_branch) / self.rho,
            "cold_flow": sol.m(self.cold_branch) / self.rho,
        }

    def is_open(self) -> bool:
        """True when the lever is lifted beyond the ``closed`` threshold."""
        return float(self.inputs["lift"]) > CLOSED_LIFT

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``low_supply_pressure`` for connected inlets below ``min_flow_pressure``.

        Capped (unconnected) inlets are ignored: their node sits at the mixing-node
        pressure, which says nothing about a supply.
        """
        if not self.is_open():
            return []
        limit = float(self.parameters["min_flow_pressure"])  # Pa absolute
        low: list[str] = []
        for port in _INLETS:
            if not sol.port_connected(port):
                continue
            p = sol.port_p(port)
            if p is not None and p < limit:
                low.append(f"{port} {(p - P_ATM) / 1e5:.3g} bar")
        if not low:
            return []
        message = (
            f"Supply pressure below min_flow_pressure ({(limit - P_ATM) / 1e5:.3g} bar gauge) "
            f"at {', '.join(low)} (gauge); the faucet will deliver less than its rated flow and "
            "a real cartridge or aerator may not work properly."
        )
        return [self.warning("low_supply_pressure", message)]
