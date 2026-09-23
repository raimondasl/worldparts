"""Centrifugal pump with fitted curves and affinity-law speed scaling (design 8.8).

The pump curves are given as tables at rated speed. The head curve is fitted with a
bounded quadratic ``H0(Q) = a + b*Q + c*Q**2`` (``a > 0``, ``b <= 0``, ``c < 0``) so the
branch law stays strictly monotone; shaft power and NPSH required are plain quadratic
least-squares fits. The affinity laws scale all three with the relative speed ``s``:

- head: ``H = a*s**2 + b*s*Q + c*Q*|Q|`` (the branch law, :class:`~worldparts.laws.PumpLaw`),
- shaft power: ``P = p0*s**3 + p1*s**2*Q + p2*s*Q**2``,
- NPSH required: ``n0*s**2 + n1*s*Q + n2*Q**2``,

each of which is ``s**k * F0(Q / s)`` for the rated-speed fit ``F0``.

A stopped pump (speed below ``OFF_SPEED``) is a resistance. The affinity law alone makes it
the resistance ``-c``, which is almost nothing for a head curve that falls linearly (``c`` at
its bound ``C_MAX``); the law therefore uses at least :data:`STOP_RESISTANCE_FACTOR` times
``a / Q_max**2`` there (blended out linearly as the speed rises to ``OFF_SPEED``), so a stopped
pump never passes more than about 1.4 times its largest curve flow under its shut-off head.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import lsq_linear, minimize_scalar

from worldparts.components.base import Component, NetworkBuilder, NetworkView
from worldparts.laws import PumpLaw
from worldparts.manifest import Table
from worldparts.media import RHO, G, vapour_pressure
from worldparts.network import Branch
from worldparts.results import ComponentWarning

__all__ = ["CentrifugalPump", "PumpCurveFit", "fit_pump_curves"]

#: Speed (relative) at or below which the pump counts as off (design 8.8 mode ``off``).
OFF_SPEED = 0.01
#: Volume-flow threshold (m3/s) for forward and reverse flow: 0.01 m3/h.
FLOW_THRESHOLD = 0.01 / 3600.0
#: Upper bound of the fitted quadratic coefficient ``c`` in SI (design 8.8).
C_MAX = -1e-9
#: Temperature (K) assumed for the vapour pressure where no water reaches the pump.
DEFAULT_T = 293.15
#: Linear term of the pump law in Pa/(kg/s); keeps the law strictly increasing at speed 0.
LAW_EPS = 1.0
#: Minimum resistance of a stopped pump as a fraction of ``a / Q_max**2`` (m/(m3/s)**2): at its
#: shut-off head a stopped pump passes at most ``Q_max / sqrt(0.5)``, about 1.41 ``Q_max``.
#: The default curve (``|c|`` = 1.06 ``a / Q_max**2``) is not affected.
STOP_RESISTANCE_FACTOR = 0.5


@dataclass(frozen=True)
class PumpCurveFit:
    """Rated-speed curve fits of a centrifugal pump, all in SI (Q in m3/s).

    Attributes:
        head: ``(a, b, c)`` of ``H0(Q) = a + b*Q + c*Q**2`` in m.
        head_rms: RMS residual of the head fit over the head-curve points, in m.
        power: ``(p0, p1, p2)`` of ``P0(Q)`` in W.
        npsh: ``(n0, n1, n2)`` of NPSH required at rated speed, in m.
        max_flow: Largest flow of the head curve (m3/s).
        bep_flow: Rated-speed flow (m3/s) of the maximum of ``rho*g*Q*H0/P0``.
        bep_efficiency: Efficiency at the best-efficiency point (fraction).
    """

    head: tuple[float, float, float]
    head_rms: float
    power: tuple[float, float, float]
    npsh: tuple[float, float, float]
    max_flow: float
    bep_flow: float
    bep_efficiency: float

    @property
    def stop_c(self) -> float:
        """Quadratic coefficient (m/(m3/s)**2, negative) of the stopped pump's resistance.

        ``min(c, -STOP_RESISTANCE_FACTOR * a / max_flow**2)``: the fitted ``c`` unless the
        head curve is so flat that ``-c`` would let a stopped pump short-circuit the network.
        """
        a, _, c = self.head
        q_max = self.max_flow if self.max_flow > 0.0 else 1.0
        return min(c, -STOP_RESISTANCE_FACTOR * a / (q_max * q_max))

    def law_c(self, s: float) -> float:
        """Quadratic coefficient of the branch law at relative speed ``s``.

        The fitted ``c`` at and above ``OFF_SPEED`` (exact affinity laws); :attr:`stop_c` at
        ``s = 0``, blended linearly in between so the law is continuous in the speed.
        """
        c = self.head[2]
        w = max(0.0, 1.0 - max(s, 0.0) / OFF_SPEED)
        return c + (self.stop_c - c) * w

    def head_at(self, q: float, s: float = 1.0) -> float:
        """Head in m at volume flow ``q`` (m3/s) and relative speed ``s``.

        The affinity-law head, with the stopped-pump resistance :meth:`law_c` below
        ``OFF_SPEED``; this is the head of the branch law without its 1 Pa per kg/s term.
        """
        a, b, _ = self.head
        c = self.law_c(s)
        return a * s * s + b * s * q + c * q * abs(q)

    def power_at(self, q: float, s: float = 1.0) -> float:
        """Shaft power in W (not clamped) at ``q`` and ``s``: ``s**3 * P0(q / s)``."""
        p0, p1, p2 = self.power
        return p0 * s**3 + p1 * s * s * q + p2 * s * q * q

    def npsh_required_at(self, q: float, s: float = 1.0) -> float:
        """NPSH required in m (not clamped) at ``q`` and ``s``: ``s**2 * N0(q / s)``."""
        n0, n1, n2 = self.npsh
        return n0 * s * s + n1 * s * q + n2 * q * q


def _columns(table: Table, value: str) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(table["flow"], dtype=float), np.asarray(table[value], dtype=float)


def _quadratic_fit(q: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-squares ``y = k0 + k1*q + k2*q**2`` (scaled for conditioning)."""
    scale = float(np.max(np.abs(q))) or 1.0
    x = q / scale
    design = np.vstack([np.ones_like(x), x, x * x]).T
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(coef[0]), float(coef[1] / scale), float(coef[2] / scale**2)


def _head_fit(q: np.ndarray, h: np.ndarray) -> tuple[tuple[float, float, float], float]:
    """Bounded least squares for the head curve; returns ``((a, b, c), rms)``."""
    scale = float(np.max(q)) or 1.0
    x = q / scale
    design = np.vstack([np.ones_like(x), x, x * x]).T
    # In scaled variables c_scaled = c * scale**2, so the SI bound c <= C_MAX becomes
    # c_scaled <= C_MAX * scale**2. a > 0 is imposed as a tiny positive lower bound.
    lower = [1e-9, -np.inf, -np.inf]
    upper = [np.inf, 0.0, C_MAX * scale**2]
    res = lsq_linear(design, h, bounds=(lower, upper), method="bvls")
    a, bs, cs = (float(v) for v in res.x)
    coef = (a, min(bs / scale, 0.0), min(cs / scale**2, C_MAX))
    fitted = coef[0] + coef[1] * q + coef[2] * q * q
    rms = float(np.sqrt(np.mean((fitted - h) ** 2)))
    return coef, rms


def fit_pump_curves(
    head_curve: Table, power_curve: Table, npsh_curve: Table, rho: float
) -> PumpCurveFit:
    """Fit the rated-speed curves and locate the best-efficiency point.

    Args:
        head_curve: Table with columns ``flow`` (m3/s) and ``head`` (m).
        power_curve: Table with columns ``flow`` (m3/s) and ``power`` (W).
        npsh_curve: Table with columns ``flow`` (m3/s) and ``npsh`` (m).
        rho: Density in kg/m3.

    Returns:
        The fitted coefficients, the head RMS residual and the best-efficiency point, found
        as the maximum of ``rho*g*Q*H0(Q)/P0(Q)`` over the head-curve flow range.
    """
    qh, h = _columns(head_curve, "head")
    head, rms = _head_fit(qh, h)
    qp, p = _columns(power_curve, "power")
    power = _quadratic_fit(qp, p)
    qn, n = _columns(npsh_curve, "npsh")
    npsh = _quadratic_fit(qn, n)
    q_lo, q_hi = float(np.min(qh)), float(np.max(qh))
    fit = PumpCurveFit(head, rms, power, npsh, q_hi, 0.0, 0.0)

    def eff(q: float) -> float:
        pw = fit.power_at(q)
        hd = fit.head_at(q)
        if q <= 0.0 or pw <= 0.0 or hd <= 0.0:
            return 0.0
        return rho * G * q * hd / pw

    grid = np.linspace(q_lo, q_hi, 401)
    values = np.array([eff(float(q)) for q in grid])
    k = int(np.argmax(values))
    lo = float(grid[max(k - 1, 0)])
    hi = float(grid[min(k + 1, len(grid) - 1)])
    q_best, e_best = float(grid[k]), float(values[k])
    if hi > lo:
        res = minimize_scalar(
            lambda q: -eff(q),
            bounds=(lo, hi),
            method="bounded",
            options={"xatol": 1e-12 * max(q_hi, 1e-12)},
        )
        if res.success and -float(res.fun) >= e_best:
            q_best, e_best = float(res.x), -float(res.fun)
    return PumpCurveFit(head, rms, power, npsh, q_hi, q_best, e_best)


def _curve_problems(name: str, table: Table, min_rows: int) -> list[str]:
    q = np.asarray(table["flow"], dtype=float)
    out: list[str] = []
    if len(q) < min_rows:
        out.append(f"{name} needs at least {min_rows} rows, got {len(q)}.")
    if len(q) > 1 and not np.all(np.diff(q) > 0):
        out.append(f"{name}: flows must be strictly increasing.")
    if len(np.unique(q)) < 3:
        out.append(f"{name}: at least three distinct flows are needed for a quadratic fit.")
    return out


class CentrifugalPump(Component):
    """Centrifugal pump: fitted head, power and NPSH curves with affinity-law speed scaling."""

    law: PumpLaw
    branch: Branch
    _fit: PumpCurveFit
    _fit_key: tuple[Any, Any, Any] | None = None

    @classmethod
    def check_parameters(cls, parameters: Mapping[str, Any]) -> list[str]:
        """Curve tables must be fittable and physically sensible.

        Flows strictly increasing with at least three distinct points in every curve; the
        head must fall from the first to the last point of the head curve; the fitted shaft
        power must stay positive over the head-curve flow range; and the best efficiency the
        head and power curves imply, ``max(rho*g*Q*H0/P0)``, must not exceed 100 % (a power
        curve in the wrong unit or for another pump).
        """
        head, power, npsh = (
            parameters["head_curve"],
            parameters["power_curve"],
            parameters["npsh_curve"],
        )
        out = _curve_problems("head_curve", head, 3)
        out += _curve_problems("power_curve", power, 3)
        out += _curve_problems("npsh_curve", npsh, 3)
        if out:
            return out
        qh, h = _columns(head, "head")
        if not h[-1] < h[0]:
            out.append(
                "head_curve: the head must fall with flow (the head at the largest flow must "
                "be below the head at the smallest flow)."
            )
        p0, p1, p2 = _quadratic_fit(*_columns(power, "power"))
        grid = np.linspace(float(qh[0]), float(qh[-1]), 101)
        if np.any(p0 + p1 * grid + p2 * grid * grid <= 0.0):
            out.append(
                "power_curve: the fitted shaft power is not positive over the head-curve flow "
                "range; check the power values and units (kW)."
            )
            return out
        fit = fit_pump_curves(head, power, npsh, rho=RHO)
        if fit.bep_efficiency > 1.0:
            out.append(
                f"power_curve: with this head curve the best efficiency would be "
                f"{fit.bep_efficiency * 100:.4g} % at {fit.bep_flow * 3600:.4g} m3/h, above "
                "100 %; the shaft power must exceed the hydraulic power rho*g*Q*H. Check the "
                "power values and units (kW) and that both curves are for the same pump."
            )
        return out

    # -- fitting ----------------------------------------------------------------------------
    @property
    def fit(self) -> PumpCurveFit:
        """Curve fits for the current tables (cached on the table objects)."""
        p = self.parameters
        key = (p["head_curve"], p["power_curve"], p["npsh_curve"])
        if self._fit_key is None or any(
            x is not y for x, y in zip(key, self._fit_key, strict=True)
        ):
            self._fit = fit_pump_curves(*key, rho=self.rho)
            self._fit_key = key
        return self._fit

    # -- network ---------------------------------------------------------------------------
    def build(self, nb: NetworkBuilder) -> None:
        """One pump branch ``inlet -> outlet``."""
        self.law = PumpLaw(1.0, 0.0, -1.0, 1.0, eps=LAW_EPS, rho=self.rho)
        self.branch = nb.branch(nb.port("inlet"), nb.port("outlet"), self.law, label="pump")

    def update_laws(self) -> None:
        """Fitted head coefficients and the relative speed (stopped-pump resistance below
        ``OFF_SPEED``, see :meth:`PumpCurveFit.law_c`)."""
        fit = self.fit
        speed = float(self.inputs["speed"])
        a, b, _ = fit.head
        self.law.a, self.law.b, self.law.c = a, b, fit.law_c(speed)
        self.law.speed = speed

    # -- results ---------------------------------------------------------------------------
    def _inlet_temperature(self, sol: NetworkView) -> float:
        for port in ("inlet", "outlet"):
            t = sol.port_T(port)
            if t is not None and math.isfinite(t):
                return t
        return DEFAULT_T

    def _npsh_available(self, sol: NetworkView) -> float | None:
        p_in = sol.port_p("inlet")
        if p_in is None:
            return None
        t = min(max(self._inlet_temperature(sol), 273.16), 647.0)
        return (p_in - vapour_pressure(t)) / (self.rho * G)

    def _state(self, sol: NetworkView) -> dict[str, float | None]:
        s = float(self.inputs["speed"])
        fit = self.fit
        q = sol.m(self.branch) / self.rho
        head = -sol.dp(self.branch) / (self.rho * G)
        shaft = max(fit.power_at(q, s), 0.0)
        hydraulic = self.rho * G * q * head
        eff = hydraulic / shaft if q > 0.0 and shaft > 0.0 and hydraulic > 0.0 else 0.0
        return {
            "volume_flow": q,
            "head": head,
            "shaft_power": shaft,
            "hydraulic_power": hydraulic,
            "efficiency": eff,
            "specific_energy": shaft / q if q > 0.0 else None,
            "npsh_available": self._npsh_available(sol),
            "npsh_required": max(fit.npsh_required_at(q, s), 0.0),
            "speed_rpm": s * float(self.parameters["rated_speed"]),
            "bep_flow": s * fit.bep_flow,
            "curve_fit_rms": fit.head_rms,
        }

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        """Flow, head, powers, efficiency, specific energy, NPSH, speed, best-efficiency
        flow and fit RMS.

        ``specific_energy`` is the shaft energy per pumped volume, ``shaft_power /
        volume_flow`` (J/m3 in SI, reported in kWh/m3); it is None when the flow is not
        positive (no water is delivered, so there is no energy per volume).

        ``head`` is the pressure rise ``(p_outlet - p_inlet) / (rho * g)``; it differs from the
        fitted curve only by the law's tiny linear term (1 Pa per kg/s).
        """
        return self._state(sol)

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        """``cavitation``, ``low_flow``, ``beyond_curve`` and ``reverse_flow``.

        The conditions are the ones the manifest modes use, on the same numbers:

        - cavitation: forward flow (above 0.01 m3/h) and
          ``npsh_available < npsh_required + npsh_margin``, or ``npsh_available < 0`` at any
          flow (the inlet is below the vapour pressure: running dry or a blocked suction);
        - low_flow: running (speed above 0.01) and ``volume_flow < min_flow_fraction *
          bep_flow``;
        - beyond_curve: running and ``volume_flow > largest curve flow * speed``;
        - reverse_flow: ``volume_flow < -0.01 m3/h``.
        """
        st = self._state(sol)
        p = self.parameters
        s = float(self.inputs["speed"])
        q = float(st["volume_flow"] or 0.0)
        running = s > OFF_SPEED
        out: list[ComponentWarning] = []
        npsh_a, npsh_r = st["npsh_available"], st["npsh_required"]
        if npsh_a is not None and npsh_a < 0.0:
            out.append(
                self.warning(
                    "cavitation",
                    f"The inlet pressure is below the vapour pressure (NPSH available "
                    f"{npsh_a:.3g} m): the suction side cannot supply the pump (running dry, a "
                    "blocked suction or an empty tank). The quasi-steady result is not "
                    "physical there.",
                )
            )
        elif (
            q > FLOW_THRESHOLD
            and npsh_a is not None
            and npsh_r is not None
            and npsh_a < npsh_r + float(p["npsh_margin"])
        ):
            out.append(
                self.warning(
                    "cavitation",
                    f"NPSH available {npsh_a:.3g} m is below NPSH required {npsh_r:.3g} m plus "
                    f"the margin {float(p['npsh_margin']):.3g} m at {q * 3600:.3g} m3/h; the "
                    "pump cavitates (head and flow would drop and the impeller erodes).",
                )
            )
        bep = float(st["bep_flow"] or 0.0)
        if running and q < float(p["min_flow_fraction"]) * bep:
            out.append(
                self.warning(
                    "low_flow",
                    f"Flow {q * 3600:.3g} m3/h is below {float(p['min_flow_fraction']):.3g} "
                    f"times the best-efficiency flow ({bep * 3600:.3g} m3/h); expect heating, "
                    "recirculation and vibration.",
                )
            )
        if running and q > self.fit.max_flow * s:
            out.append(
                self.warning(
                    "beyond_curve",
                    f"Flow {q * 3600:.3g} m3/h exceeds the largest curve flow scaled to this "
                    f"speed ({self.fit.max_flow * s * 3600:.3g} m3/h); the fitted curves are "
                    "extrapolated.",
                )
            )
        if q < -FLOW_THRESHOLD:
            out.append(self.warning("reverse_flow"))
        return out
