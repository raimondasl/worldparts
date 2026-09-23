"""Branch laws (design section 5.2).

A law maps the mass flow ``m`` (kg/s, positive from node ``a`` to node ``b``) to the pressure
difference ``p_a - p_b`` in Pa. Every law is continuous and strictly increasing in ``m`` with
a derivative bounded below by a small positive number; this is the invariant that makes the
network problem well posed. Laws achieve it with regularisation near zero flow, as Modelica's
``regSquare`` does.

All laws work in SI and return ``(dp, ddp_dm)`` from :meth:`Law.dp`. Coefficients are plain
attributes, so a component can change them between solves (``update_laws``).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from worldparts.errors import InvalidValueError
from worldparts.media import MU, RHO, G

__all__ = [
    "IDEAL_RESISTANCE",
    "CheckValveLaw",
    "GateLaw",
    "Law",
    "LinearQuadraticResistance",
    "PipeLaw",
    "PumpLaw",
    "QuadraticResistance",
    "churchill_friction_factor",
    "flow_at_1bar",
    "ideal_connection",
    "k_to_kv",
    "kv_to_k",
]

#: Linear resistance in Pa/(m3/s) of an "ideal" joint (see :func:`ideal_connection`): about
#: 1e-6 Pa per kg/s, so 1e-6 bar at 1e5 kg/s (100 m3/s).
IDEAL_RESISTANCE: float = 1e-6 * RHO

_EPS_FRACTION = 1e-3  # regularisation flow as a fraction of the flow at 1 bar


def kv_to_k(kv_si: float, rho: float = RHO) -> float:
    """Convert a Kv value in SI (m3/s) to a mass-flow coefficient in kg/(s Pa^0.5).

    ``k = Kv_SI * sqrt(rho * 1000 / 1e5)`` reproduces the Kv definition (m3/h of water at
    1 bar, with the specific gravity relative to 1000 kg/m3), so ``m = k * sqrt(dp)``.
    """
    return kv_si * math.sqrt(rho * 1000.0 / 1e5)


def k_to_kv(k: float, rho: float = RHO) -> float:
    """Inverse of :func:`kv_to_k`: Kv in SI (m3/s) from a mass-flow coefficient."""
    return k / math.sqrt(rho * 1000.0 / 1e5)


def flow_at_1bar(k: float) -> float:
    """Mass flow in kg/s through a quadratic resistance ``k`` at 1 bar pressure drop."""
    return k * math.sqrt(1e5)


class Law(ABC):
    """Base class of branch laws.

    Attributes:
        ideal: True for the near-ideal joints of :func:`ideal_connection` (used by the
            structural check to find boundaries short-circuited through a junction).
    """

    ideal: bool = False

    @abstractmethod
    def dp(self, m: float) -> tuple[float, float]:
        """Return ``(p_a - p_b, d(p_a - p_b)/dm)`` in Pa and Pa/(kg/s) at mass flow ``m``."""

    def __call__(self, m: float) -> tuple[float, float]:
        return self.dp(m)

    def flow(self, dp: float, tol: float = 1e-12) -> float:
        """Invert the law: the mass flow at pressure difference ``dp`` (safeguarded Newton).

        Useful for tests and initial guesses; the solver does not need it.
        """
        lo, hi = -1.0, 1.0
        while self.dp(lo)[0] > dp:
            lo *= 2.0
            if lo < -1e12:
                raise InvalidValueError("Law.flow: no bracket found.")
        while self.dp(hi)[0] < dp:
            hi *= 2.0
            if hi > 1e12:
                raise InvalidValueError("Law.flow: no bracket found.")
        m = 0.5 * (lo + hi)
        for _ in range(200):
            f, d = self.dp(m)
            f -= dp
            if f > 0:
                hi = m
            else:
                lo = m
            step = m - f / d if d > 0 else 0.5 * (lo + hi)
            m = step if lo < step < hi else 0.5 * (lo + hi)
            if hi - lo < tol * max(1.0, abs(m)):
                break
        return m


def _reg_square(m: float, eps: float) -> tuple[float, float]:
    """Regularised ``m * |m|`` and its derivative.

    Exactly ``m * |m|`` for ``|m| >= 2 * eps``; inside that band the odd cubic
    ``m * (eps + m**2 / (4 * eps))``, which joins the square law with a continuous first
    derivative (like Modelica's ``regSquare2``). The slope at zero is ``eps``, the same as
    that of ``m * sqrt(m**2 + eps**2)``, but the law is undistorted outside the band.
    """
    if eps <= 0.0:
        return m * abs(m), 2.0 * abs(m)
    if abs(m) >= 2.0 * eps:
        return m * abs(m), 2.0 * abs(m)
    return m * (eps + m * m / (4.0 * eps)), eps + 0.75 * m * m / eps


class QuadraticResistance(Law):
    """``dp = m * |m| / k**2``, regularised near zero flow.

    Within ``|m| < 2 * m_eps`` the square law is replaced by a C1 cubic whose slope at zero,
    ``m_eps / k**2``, equals that of ``m * sqrt(m**2 + m_eps**2) / k**2`` (design 5.2). Outside
    that band the law is exactly quadratic, so the Kv law is undistorted above 0.2 % of the
    flow at 1 bar.

    Args:
        k: Mass-flow coefficient in kg/(s Pa^0.5); see :func:`kv_to_k`.
        m_eps: Regularisation flow in kg/s. When None (the default) it is 1e-3 of the flow at
            1 bar through the element, recomputed whenever ``k`` changes.
    """

    def __init__(self, k: float, m_eps: float | None = None) -> None:
        self.k = k
        self._m_eps = m_eps

    @classmethod
    def from_kv(
        cls, kv_si: float, m_eps: float | None = None, rho: float = RHO
    ) -> QuadraticResistance:
        """Build from a Kv value in SI (m3/s)."""
        return cls(kv_to_k(kv_si, rho), m_eps)

    @property
    def m_eps(self) -> float:
        """Regularisation flow in kg/s."""
        if self._m_eps is not None:
            return self._m_eps
        return _EPS_FRACTION * flow_at_1bar(self.k)

    @m_eps.setter
    def m_eps(self, value: float | None) -> None:
        self._m_eps = value

    def dp(self, m: float) -> tuple[float, float]:
        """See :meth:`Law.dp`."""
        if self.k <= 0:
            raise InvalidValueError(f"QuadraticResistance: k must be positive, got {self.k}.")
        f, d = _reg_square(m, self.m_eps)
        k2 = self.k * self.k
        return f / k2, d / k2


class LinearQuadraticResistance(Law):
    """``dp = r_lin * Q + r_quad * Q * |Q|`` with ``Q = m / rho`` (media filter).

    Args:
        r_lin: Linear coefficient in Pa/(m3/s).
        r_quad: Quadratic coefficient in Pa/(m3/s)**2.
        q_eps: Regularisation volume flow (m3/s) for the quadratic term; it keeps the law
            strictly increasing at zero flow when ``r_lin`` is zero.
        rho: Density in kg/m3.
    """

    def __init__(self, r_lin: float, r_quad: float, q_eps: float = 1e-7, rho: float = RHO) -> None:
        self.r_lin = r_lin
        self.r_quad = r_quad
        self.q_eps = q_eps
        self.rho = rho

    def dp(self, m: float) -> tuple[float, float]:
        """See :meth:`Law.dp`."""
        q = m / self.rho
        f, d = _reg_square(q, self.q_eps)
        dp = self.r_lin * q + self.r_quad * f
        ddp = (self.r_lin + self.r_quad * d) / self.rho
        return dp, ddp


# --- Churchill (1977) friction factor, written as G = f * Re -------------------------------

_LN8_12 = 12.0 * math.log(8.0)
_C_A = 2.457
_LN_C_A = math.log(_C_A)


def _churchill_G(re: float, rel_rough: float) -> tuple[float, float]:
    """Return ``G = f * Re`` and ``Re * dG/dRe`` for the Churchill (1977) correlation.

    ``f = 8 * ((8/Re)**12 + (A + B)**-1.5)**(1/12)`` so
    ``G = 8 * (8**12 + Re**12 * (A + B)**-1.5)**(1/12)``, which tends to 64 as Re -> 0.
    Everything is computed in log space to avoid overflow.
    """
    if re < 1e-12:
        return 64.0, 0.0
    ln_re = math.log(re)
    w7 = (7.0 / re) ** 0.9
    u = w7 + 0.27 * rel_rough
    l1 = -math.log(u)  # ln(1/u)
    ln_b = 16.0 * (math.log(37530.0) - ln_re)
    if l1 != 0.0:
        ln_a = 16.0 * (_LN_C_A + math.log(abs(l1)))
        ln_ab = float(_logaddexp(ln_a, ln_b))
    else:
        ln_a = -math.inf
        ln_ab = ln_b
    ln_t = 12.0 * ln_re - 1.5 * ln_ab  # T = Re^12 (A+B)^-1.5
    ln_s = float(_logaddexp(_LN8_12, ln_t))  # S = 8^12 + T
    g = 8.0 * math.exp(ln_s / 12.0)
    # Re * dA/dRe = 16 * C_A^16 * l1^15 * (0.9 * w7 / u);  Re * dB/dRe = -16 B
    # (Re*A' + Re*B') / (A + B), in a numerically safe form.
    ratio_a = 0.0
    if l1 != 0.0:
        ratio_a = 16.0 * (0.9 * w7 / u) / l1 * math.exp(ln_a - ln_ab)
    ratio_b = -16.0 * math.exp(ln_b - ln_ab)
    re_ds_over_t = 12.0 - 1.5 * (ratio_a + ratio_b)  # Re * dS/dRe / T
    # Re * dG/dRe = (8/12) * S^(-11/12) * T * (Re dS/dRe / T)
    re_dg = (8.0 / 12.0) * math.exp(ln_t - (11.0 / 12.0) * ln_s) * re_ds_over_t
    return g, re_dg


def _logaddexp(a: float, b: float) -> float:
    if a == -math.inf:
        return b
    if b == -math.inf:
        return a
    hi, lo = (a, b) if a > b else (b, a)
    return hi + math.log1p(math.exp(lo - hi))


def churchill_friction_factor(re: float, rel_roughness: float = 0.0) -> float:
    """Darcy friction factor from Churchill (1977), valid in all flow regimes.

    Args:
        re: Reynolds number (> 0).
        rel_roughness: Relative roughness ``roughness / diameter``.

    Returns:
        The Darcy friction factor ``f``.
    """
    if re <= 0:
        raise InvalidValueError("churchill_friction_factor: Re must be positive.")
    g, _ = _churchill_G(re, rel_roughness)
    return g / re


class PipeLaw(Law):
    """Darcy-Weisbach pipe with the Churchill (1977) friction factor, minor losses and static
    head.

    ``dp = G(Re) * L * mu * v / (2 D**2) + K * rho * v * |v| / 2 + rho * g * dz`` with
    ``G = f * Re``; ``G -> 64`` as ``Re -> 0`` so the law is strictly increasing at zero flow.

    Args:
        length: Pipe length in m.
        diameter: Inner diameter in m.
        roughness: Absolute roughness in m.
        minor_loss: Sum of minor-loss coefficients K.
        height_difference: Elevation of ``b`` minus ``a`` in m.
        rho: Density in kg/m3.
        mu: Dynamic viscosity in Pa s.
    """

    def __init__(
        self,
        length: float,
        diameter: float,
        roughness: float = 0.0,
        minor_loss: float = 0.0,
        height_difference: float = 0.0,
        rho: float = RHO,
        mu: float = MU,
    ) -> None:
        self.length = length
        self.diameter = diameter
        self.roughness = roughness
        self.minor_loss = minor_loss
        self.height_difference = height_difference
        self.rho = rho
        self.mu = mu

    @property
    def area(self) -> float:
        """Flow cross-section in m2."""
        return math.pi * self.diameter**2 / 4.0

    def velocity(self, m: float) -> float:
        """Mean velocity in m/s at mass flow ``m``."""
        return m / (self.rho * self.area)

    def reynolds(self, m: float) -> float:
        """Reynolds number (non-negative) at mass flow ``m``."""
        return self.rho * abs(self.velocity(m)) * self.diameter / self.mu

    def friction_factor(self, m: float) -> float | None:
        """Darcy friction factor at mass flow ``m`` (None at zero flow, where it diverges)."""
        re = self.reynolds(m)
        if re < 1e-9:
            return None
        return churchill_friction_factor(re, self.roughness / self.diameter)

    def dp(self, m: float) -> tuple[float, float]:
        """See :meth:`Law.dp`."""
        rho, mu, d = self.rho, self.mu, self.diameter
        area = self.area
        v = m / (rho * area)
        re = rho * abs(v) * d / mu
        g, re_dg = _churchill_G(re, self.roughness / d)
        c_f = self.length * mu / (2.0 * d * d)
        dp_f = g * c_f * v
        ddp_f_dv = c_f * (g + re_dg)
        dp_k = self.minor_loss * rho * v * abs(v) / 2.0
        ddp_k_dv = self.minor_loss * rho * abs(v)
        dp_z = rho * G * self.height_difference
        dv_dm = 1.0 / (rho * area)
        return dp_f + dp_k + dp_z, (ddp_f_dv + ddp_k_dv) * dv_dm


class PumpLaw(Law):
    """Centrifugal pump: ``dp = -rho * g * H + eps * m`` with
    ``H = a*s**2 + b*s*Q + c*Q*|Q|`` and ``Q = m / rho``.

    Args:
        a: Shut-off head coefficient in m (> 0).
        b: Linear coefficient in m/(m3/s) (<= 0).
        c: Quadratic coefficient in m/(m3/s)**2 (< 0).
        speed: Relative speed ``s`` (0 means stopped; the pump then acts as a resistance).
        eps: Small linear term in Pa/(kg/s) that keeps the law strictly increasing.
        rho: Density in kg/m3.
    """

    def __init__(
        self, a: float, b: float, c: float, speed: float = 1.0, eps: float = 1.0, rho: float = RHO
    ) -> None:
        self.a = a
        self.b = b
        self.c = c
        self.speed = speed
        self.eps = eps
        self.rho = rho

    def head(self, m: float) -> float:
        """Pump head in m at mass flow ``m``."""
        q = m / self.rho
        s = self.speed
        return self.a * s * s + self.b * s * q + self.c * q * abs(q)

    def dp(self, m: float) -> tuple[float, float]:
        """See :meth:`Law.dp`."""
        if self.a <= 0 or self.b > 0 or self.c >= 0 or self.eps <= 0:
            raise InvalidValueError(
                "PumpLaw needs a > 0, b <= 0, c < 0 and eps > 0; got "
                f"a={self.a}, b={self.b}, c={self.c}, eps={self.eps}."
            )
        q = m / self.rho
        s = self.speed
        dh_dq = self.b * s + 2.0 * self.c * abs(q)
        dp = -self.rho * G * self.head(m) + self.eps * m
        ddp = -G * dh_dq + self.eps
        return dp, ddp


class _AsymmetricQuadratic(Law):
    """Quadratic resistance with a different coefficient per flow direction.

    Forward flow (``m > 0``) uses ``k_forward``; reverse flow uses ``k_reverse``. Each side is
    the regularised square law of :class:`QuadraticResistance` with its own ``eps``. The open
    (larger-k) side uses ``eps`` = 1e-3 of its flow at 1 bar; the other side's ``eps`` is
    scaled by ``(k_side / k_open)**2`` so both sides have the same slope at zero flow. The law
    is therefore continuous, continuously differentiable and strictly increasing, and exactly
    quadratic on each side outside its regularisation band.

    With a very small leakage (1e-9) the reverse side's band is tiny and its slope grows very
    quickly just below zero flow; the solver copes (the node-pressure fallback handles such
    kinks), but it is the stiffest law in the catalogue.
    """

    def _k_pair(self) -> tuple[float, float]:
        raise NotImplementedError

    def dp(self, m: float) -> tuple[float, float]:
        """See :meth:`Law.dp`."""
        kf, kr = self._k_pair()
        if kf <= 0 or kr <= 0:
            raise InvalidValueError(f"{type(self).__name__}: coefficients must be positive.")
        k_open = max(kf, kr)
        k = kf if m >= 0 else kr
        eps = _EPS_FRACTION * flow_at_1bar(k_open) * (k / k_open) ** 2
        f, d = _reg_square(m, eps)
        return f / (k * k), d / (k * k)


class CheckValveLaw(_AsymmetricQuadratic):
    """Check valve: coefficient ``k`` forward, ``k * leakage`` in reverse.

    Args:
        k: Forward mass-flow coefficient in kg/(s Pa^0.5).
        leakage: Reverse coefficient as a fraction of ``k`` (0 < leakage <= 1).
    """

    def __init__(self, k: float, leakage: float = 1e-6) -> None:
        self.k = k
        self.leakage = leakage

    def _k_pair(self) -> tuple[float, float]:
        return self.k, self.k * self.leakage


class GateLaw(_AsymmetricQuadratic):
    """Quadratic resistance whose flow in one direction can be switched to leakage only.

    Used by tank ports: when the tank is empty, outflow from the tank is blocked.

    Args:
        k: Mass-flow coefficient in kg/(s Pa^0.5).
        blocked_direction: None (open both ways), ``"forward"`` (a to b blocked) or
            ``"reverse"`` (b to a blocked). May be changed between solves.
        leakage: Coefficient fraction in the blocked direction.
    """

    DIRECTIONS = (None, "forward", "reverse")

    def __init__(
        self, k: float, blocked_direction: str | None = None, leakage: float = 1e-6
    ) -> None:
        self.k = k
        self.leakage = leakage
        self.blocked_direction = blocked_direction

    @property
    def blocked_direction(self) -> str | None:
        """The blocked flow direction, or None."""
        return self._blocked

    @blocked_direction.setter
    def blocked_direction(self, value: str | None) -> None:
        if value not in self.DIRECTIONS:
            raise InvalidValueError(
                f"GateLaw.blocked_direction must be None, 'forward' or 'reverse', got {value!r}."
            )
        self._blocked = value

    def _k_pair(self) -> tuple[float, float]:
        kf = self.k * self.leakage if self._blocked == "forward" else self.k
        kr = self.k * self.leakage if self._blocked == "reverse" else self.k
        return kf, kr


def ideal_connection() -> LinearQuadraticResistance:
    """A near-ideal joint: a linear resistance of about 1e-6 Pa per kg/s.

    Boundaries join their internal fixed node to their port with it. Its pressure drop is
    below 1e-6 bar up to 1e5 kg/s (100 m3/s), far beyond any flow the catalogue's hard limits
    produce through a real element, and being linear it adds no nonlinearity to the solve.
    Two ideal joints between fixed nodes at different pressures form a short circuit, which
    :meth:`worldparts.System.check` reports as ``boundary_short_circuit``.
    """
    law = LinearQuadraticResistance(IDEAL_RESISTANCE, 0.0)
    law.ideal = True
    return law
