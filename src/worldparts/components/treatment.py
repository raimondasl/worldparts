"""Water treatment: media filter and UV reactor (design 8.10 and 8.11).

Both are two-port resistances in the forward direction ``inlet -> outlet``:

- :class:`MediaFilter`: a sand or cartridge filter whose pressure drop is the sum of a linear
  media loss (Darcy flow through the bed, which grows as the bed clogs) and a quadratic
  housing loss (inlet, outlet and distributor fittings).
- :class:`UVReactor`: a UV disinfection chamber with a quadratic pressure drop through its
  rated point and an idealised plug-flow average dose ``fluence_rate * residence_time``.

The design 8.10 and 8.11 envelope rules (``change_required``, ``over_rated_flow``,
``underdose``, ``lamp_off``) only cover forward flow. Both components therefore also emit a
``reverse_flow`` warning (declared in the manifest ``warnings``) when water flows backwards
faster than 0.01 m3/h, and its message states which of the forward-flow checks the reverse
flow would fail, so a mode such as ``underdosing`` is never left without a warning.
"""

from __future__ import annotations

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import LinearQuadraticResistance, QuadraticResistance
from worldparts.network import Branch
from worldparts.results import ComponentWarning

__all__ = ["MediaFilter", "UVReactor", "filter_coefficients"]

#: Regularisation volume flow of the filter law as a fraction of the rated flow. The law is
#: exactly linear plus quadratic above twice this fraction (0.02 % of the rated flow).
_FILTER_EPS_FRACTION = 1e-4

#: Reverse-flow warning threshold, 0.01 m3/h in m3/s (the ``idle`` mode threshold).
_REVERSE_FLOW = 0.01 / 3600.0
#: 1 bar in Pa (pressure differences).
_BAR = 1e5

#: Cap of the UV reactor residence time (s) and dose (mJ/cm2), design 8.11.
_UV_CAP = 1e6
#: 1 mJ/cm2 in SI (J/m2).
_MJ_PER_CM2 = 10.0


def filter_coefficients(
    rated_flow: float, clean_pressure_drop: float, housing_fraction: float, clogging: float
) -> tuple[float, float]:
    """Linear and quadratic coefficients of the media filter law (design 8.10).

    Args:
        rated_flow: Rated volume flow ``Q_rated`` in m3/s.
        clean_pressure_drop: Clean pressure drop ``dp_clean`` at the rated flow in Pa.
        housing_fraction: Share ``h`` of the clean drop that is quadratic housing loss.
        clogging: Clogging fraction ``c`` in [0, 0.99]; the media resistance scales with
            ``1 / (1 - c)``.

    Returns:
        ``(r_lin, r_quad)`` in Pa/(m3/s) and Pa/(m3/s)**2, with
        ``r_lin = (1 - h) * dp_clean / Q_rated / (1 - c)`` and
        ``r_quad = h * dp_clean / Q_rated**2``.
    """
    h = housing_fraction
    r_lin = (1.0 - h) * clean_pressure_drop / rated_flow / (1.0 - clogging)
    r_quad = h * clean_pressure_drop / rated_flow**2
    return r_lin, r_quad


class MediaFilter(Component):
    """Sand or cartridge filter: linear media loss plus quadratic housing loss.

    ``dp = r_lin * Q + r_quad * Q * |Q|`` (see :func:`filter_coefficients`). The media part
    grows with the ``clogging`` input; the housing part does not. ``dp_ratio`` compares the
    drop with that of the same filter when clean at the same flow.
    """

    law: LinearQuadraticResistance
    clean_law: LinearQuadraticResistance
    branch: Branch

    def build(self, nb: NetworkBuilder) -> None:
        """One linear-plus-quadratic branch ``inlet -> outlet``."""
        self.law = LinearQuadraticResistance(1.0, 1.0, rho=self.rho)
        self.clean_law = LinearQuadraticResistance(1.0, 1.0, rho=self.rho)
        self.branch = nb.branch(nb.port("inlet"), nb.port("outlet"), self.law, label="filter")

    def update_laws(self) -> None:
        """Coefficients of the current (clogged) and the clean filter."""
        p = self.parameters
        q_rated = p["rated_flow"]
        args = (q_rated, p["clean_pressure_drop"], p["housing_fraction"])
        q_eps = _FILTER_EPS_FRACTION * q_rated
        for law, clogging in ((self.law, self.inputs["clogging"]), (self.clean_law, 0.0)):
            law.r_lin, law.r_quad = filter_coefficients(*args, clogging)
            law.q_eps = q_eps
            law.rho = self.rho

    def dp_ratio(self, m: float) -> float:
        """Pressure drop divided by the clean pressure drop at mass flow ``m``.

        At (numerically) zero flow the ratio is the limit ``m -> 0``, the ratio of the law
        slopes, so the observable is continuous: ``1 / (1 - clogging)`` for a pure media
        filter and 1 for a pure housing loss.
        """
        dp, slope = self.law.dp(m)
        dp_clean, slope_clean = self.clean_law.dp(m)
        # Below 1e-9 of the rated flow the quotient loses precision; use the slope ratio.
        if abs(m) <= 1e-9 * self.parameters["rated_flow"] * self.rho:
            return slope / slope_clean
        return dp / dp_clean

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow, pressure drop and ratio to the clean pressure drop."""
        m = sol.m(self.branch)
        return {
            "volume_flow": m / self.rho,
            "pressure_drop": sol.dp(self.branch),
            "dp_ratio": self.dp_ratio(m),
        }

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``reverse_flow`` when ``volume_flow < -0.01 m3/h``.

        The message adds the forward-flow checks the reverse flow would fail: a drop beyond
        ``change_pressure_drop`` or a flow beyond 125 % of ``rated_flow`` in magnitude.
        """
        q = sol.m(self.branch) / self.rho
        if q >= -_REVERSE_FLOW:
            return []
        p = self.parameters
        dp = sol.dp(self.branch)
        notes = []
        if abs(dp) > p["change_pressure_drop"]:
            notes.append(
                f"the reverse pressure drop ({abs(dp) / _BAR:.3g} bar) exceeds the change "
                f"pressure drop ({p['change_pressure_drop'] / _BAR:.3g} bar)"
            )
        if abs(q) > 1.25 * p["rated_flow"]:
            notes.append(
                f"the flow exceeds 125 % of the rated flow ({1.25 * p['rated_flow'] * 3600:.3g}"
                " m3/h)"
            )
        message = (
            f"Water flows backwards through the filter (outlet to inlet) at {-q * 3600:.3g} m3/h;"
            " retained solids may be flushed to the raw-water side. change_required and"
            " over_rated_flow only check forward flow"
        )
        message += ("; " + " and ".join(notes) + ".") if notes else "."
        return [self.warning("reverse_flow", message)]


class UVReactor(Component):
    """UV disinfection reactor: quadratic pressure drop and plug-flow average dose.

    The pressure drop passes through the rated point (``rated_pressure_drop`` at
    ``rated_flow``). ``residence_time = volume / |Q|`` and
    ``dose = fluence_rate * lamp_output * residence_time``, each capped at 1e6 in its display
    unit (s and mJ/cm2), so zero flow gives finite values.

    The cap is the valid range of ``residence_time``: a reading of 1e6 s means "at least
    1e6 s". It can bind at a legal flowing point (volume above about 2.8 m3 with the flow just
    above the 0.01 m3/h idle threshold, e.g. 100 m3 at 0.05 m3/h: true 7.2e6 s). The
    ``underdose`` verdict is then unchanged whenever ``lamp_output >= 0.01``: the capped dose
    is at least ``0.1 mW/cm2 * 0.01 * 1e6 s = 1000 mJ/cm2``, the largest legal
    ``required_dose``. Below 1 % lamp output ``lamp_off`` is raised anyway.
    """

    law: QuadraticResistance
    branch: Branch

    def build(self, nb: NetworkBuilder) -> None:
        """One quadratic-resistance branch ``inlet -> outlet``."""
        self.law = QuadraticResistance(1.0)
        self.branch = nb.branch(nb.port("inlet"), nb.port("outlet"), self.law, label="uv")

    def update_laws(self) -> None:
        """Mass-flow coefficient through the rated point: ``k = rho * Q_rated / sqrt(dp)``.

        This equals ``kv_to_k(kv)`` with ``kv = Q_rated / sqrt(dp_rated / 1 bar * 1000 / rho)``.
        """
        p = self.parameters
        self.law.k = self.rho * p["rated_flow"] / p["rated_pressure_drop"] ** 0.5

    def residence_time(self, q: float) -> float:
        """Plug-flow residence time in s at volume flow ``q`` (m3/s), capped at 1e6 s.

        The cap (design 8.11) keeps zero flow finite; above it the value is a lower bound.
        """
        volume = self.parameters["volume"]
        if abs(q) * _UV_CAP <= volume:
            return _UV_CAP
        return volume / abs(q)

    def dose(self, q: float) -> float:
        """Average UV dose in J/m2 at volume flow ``q`` (m3/s), capped at 1e6 mJ/cm2."""
        fluence = self.parameters["fluence_rate"] * self.inputs["lamp_output"]  # W/m2
        return min(fluence * self.residence_time(q), _UV_CAP * _MJ_PER_CM2)

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow, pressure drop, residence time and dose."""
        q = sol.m(self.branch) / self.rho
        return {
            "volume_flow": q,
            "pressure_drop": sol.dp(self.branch),
            "residence_time": self.residence_time(q),
            "dose": self.dose(q),
        }

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``reverse_flow`` when ``volume_flow < -0.01 m3/h``.

        The message adds the forward-flow checks the reverse flow would fail: a dose below
        ``required_dose`` (the mode then reads ``underdosing``) or the lamp off.
        """
        q = sol.m(self.branch) / self.rho
        if q >= -_REVERSE_FLOW:
            return []
        p = self.parameters
        dose = self.dose(q)
        notes = []
        if self.inputs["lamp_output"] < 0.01:
            notes.append("the lamp is off")
        if dose < p["required_dose"]:
            notes.append(
                f"the average dose ({dose / _MJ_PER_CM2:.3g} mJ/cm2) is below the required "
                f"dose ({p['required_dose'] / _MJ_PER_CM2:.3g} mJ/cm2)"
            )
        message = (
            f"Water flows backwards through the UV reactor (outlet to inlet) at "
            f"{-q * 3600:.3g} m3/h. underdose and lamp_off only check forward flow"
        )
        message += ("; " + " and ".join(notes) + ".") if notes else "."
        return [self.warning("reverse_flow", message)]
