"""Media properties.

v0.1 supports one medium, water, with constant properties (design section 3.3). The vapour
pressure uses the IAPWS-IF97 saturation-pressure equation (region 4), which is valid from the
triple point to the critical point; worldparts uses it between 1 and 100 degC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from worldparts.errors import InvalidValueError, format_choices

__all__ = ["CP", "MU", "RHO", "WATER", "G", "Medium", "get_medium", "vapour_pressure"]

#: Standard gravity in m/s2.
G: float = 9.80665
#: Water density in kg/m3.
RHO: float = 998.2
#: Water dynamic viscosity in Pa s.
MU: float = 1.002e-3
#: Water specific heat in J/(kg K).
CP: float = 4182.0


@dataclass(frozen=True)
class Medium:
    """Constant fluid properties.

    Attributes:
        name: Medium name as used in manifests.
        rho: Density in kg/m3.
        mu: Dynamic viscosity in Pa s.
        cp: Specific heat in J/(kg K).
    """

    name: str
    rho: float
    mu: float
    cp: float

    def vapour_pressure(self, T: float) -> float:
        """Vapour pressure in Pa at absolute temperature ``T`` in K."""
        return vapour_pressure(T)


WATER = Medium("water", RHO, MU, CP)
_MEDIA = {"water": WATER}


def get_medium(name: str) -> Medium:
    """Return the medium called ``name``.

    Raises:
        InvalidValueError: When the medium is not supported.
    """
    try:
        return _MEDIA[name]
    except KeyError:
        raise InvalidValueError(
            f"Unknown medium '{name}'. {format_choices(name, _MEDIA)}"
        ) from None


_N = (
    0.11670521452767e4,
    -0.72421316703206e6,
    -0.17073846940092e2,
    0.12020824702470e5,
    -0.32325550322333e7,
    0.14915108613530e2,
    -0.48232657361591e4,
    0.40511340542057e6,
    -0.23855557567849,
    0.65017534844798e3,
)


def vapour_pressure(T: float) -> float:
    """Saturation (vapour) pressure of water in Pa (IAPWS-IF97, region 4).

    Args:
        T: Absolute temperature in K (273.15 K to 647.096 K).

    Returns:
        Vapour pressure in Pa.

    Raises:
        InvalidValueError: Outside the validity range.
    """
    if not 273.15 <= T <= 647.096:
        raise InvalidValueError(
            f"Water vapour pressure is defined between 273.15 K and 647.096 K, got {T} K."
        )
    n = _N
    theta = T + n[8] / (T - n[9])
    a = theta**2 + n[0] * theta + n[1]
    b = n[2] * theta**2 + n[3] * theta + n[4]
    c = n[5] * theta**2 + n[6] * theta + n[7]
    p_mpa = (2.0 * c / (-b + math.sqrt(b * b - 4.0 * a * c))) ** 4
    return p_mpa * 1e6
