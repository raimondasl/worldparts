"""Export worldparts water networks to WNTR/EPANET and measure divergence (design section 11).

This is the "load the same component into several hosts and measure divergence" experiment
of the research report, in code: :func:`to_wntr` builds a
:class:`wntr.network.WaterNetworkModel` from a :class:`worldparts.System`,
:func:`export_inp` writes it as an EPANET ``.inp`` file, and :func:`compare_with_wntr`
solves both and reports per-link flow and per-node pressure differences.

Mapping (see ``docs/wntr-adapter.md`` for the measured divergence)::

    supply            reservoir, head = elevation + gauge pressure / (rho g), joined to its
                      port junction by a lossless TCV (setting 0) named after the supply
    drain             reservoir, head = elevation (gauge 0), joined the same way
    pipe              Darcy-Weisbach pipe (length, diameter, roughness, minor loss);
                      height_difference becomes junction elevations
    valve             TCV whose loss coefficient reproduces the effective Kv (at the settled
                      position) at the diameter of an adjacent pipe
    check_valve       pipe with a check valve (CV), 1 mm long, minor loss from the Kv
    centrifugal_pump  HEAD pump, speed setting = relative speed; EPANET's three-point power
                      curve when it reproduces the fitted quadratic exactly (linear term 0),
                      else a multi-point curve sampled from the quadratic, continued past the
                      run-out flow with negative heads
    tank              tank with the overflow option (elevation of its port nodes, level, height,
                      diameter; a level up to 1 mm is exported as 0, empty) and one TCV per
                      connected port for ``port_kv``
    media_filter      TCV matching the pressure drop at a reference flow (the linear media
                      term cannot be represented; see :data:`KNOWN_DIVERGENCE_SOURCES`)
    uv_reactor        TCV through the rated point (exact: the UV law is quadratic)

Every worldparts connection node becomes a junction (``J1``, ``J2``, ...). A supply or drain
is a reservoir joined to its port's junction by a TCV with loss coefficient 0, which EPANET
treats as an open valve with a negligible linear resistance: the counterpart of the ideal
joint inside the worldparts boundary, and the link that carries the boundary's flow.
worldparts has no global elevations (design 5.4): the
adapter assigns them by walking the graph, adding each pipe's ``height_difference`` and
keeping every other component level, with the first drain (else supply, else tank) of each
connected part at elevation 0, so a drain sits at head 0.

Units: WNTR works in SI (m, m3/s, head in m); the ``.inp`` is written in ``CMH`` (m3/h,
lengths in m, diameters and Darcy-Weisbach roughness in mm). Heads convert to pressures with
the worldparts water density and standard gravity. Only EPANET (``EpanetSimulator``) runs the
comparison: the ``WNTRSimulator`` of WNTR 1.5 rejects Darcy-Weisbach head loss.

``wntr`` is an optional dependency (``uv add wntr``, or the extra:
``uv add "worldparts[wntr] @ git+https://github.com/raimondasl/worldparts"``); it is imported on
first use, so this module imports without it and raises
:class:`~worldparts.adapters.MissingDependencyError` when a function needs it.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import worldparts as wp
from worldparts.adapters import MissingDependencyError
from worldparts.components.tank import EMPTY_LEVEL
from worldparts.components.treatment import filter_coefficients
from worldparts.components.valves import characteristic
from worldparts.errors import SolverError, SystemCheckError, WorldpartsError
from worldparts.media import MU, RHO, G
from worldparts.units import P_ATM

if TYPE_CHECKING:  # pragma: no cover
    from wntr.network import WaterNetworkModel

__all__ = [
    "EPANET_G_FRICTION",
    "EPANET_G_MINOR",
    "INP_UNITS",
    "KNOWN_DIVERGENCE_SOURCES",
    "SUPPORTED_COMPONENTS",
    "ComparisonReport",
    "LinkComparison",
    "LinkMap",
    "NodeComparison",
    "NodeMap",
    "PumpCurveMap",
    "UnsupportedComponentError",
    "WntrExportError",
    "WntrTranslation",
    "compare_with_wntr",
    "export_inp",
    "model_to_inp",
    "to_wntr",
    "translate",
]

# ----------------------------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------------------------
#: Gravity (m/s2) implied by EPANET's minor-loss constant 0.02517 = 8 / (g * pi**2) in US
#: units (about 9.8156 m/s2). TCV settings and check-valve losses computed by the adapter are
#: scaled by ``EPANET_G_MINOR / G`` so EPANET reproduces the worldparts pressure drop.
EPANET_G_MINOR: float = 8.0 / (0.02517 * math.pi**2) * 0.3048
#: Gravity (m/s2) EPANET uses in its Darcy-Weisbach pipe resistance (32.2 ft/s2). Pipe
#: geometry is exported as is, so EPANET's pipe friction head is about 0.08 % lower.
EPANET_G_FRICTION: float = 32.2 * 0.3048
#: EPANET's reference kinematic viscosity (1.1e-5 ft2/s) in m2/s; the ``VISCOSITY`` option is
#: relative to it.
EPANET_VISCOSITY: float = 1.1e-5 * 0.3048**2
#: Flow units of the exported ``.inp`` (m3/h; lengths in m, diameters and roughness in mm).
INP_UNITS: str = "CMH"
#: EPANET ``ACCURACY`` option (relative flow change at convergence; EPANET's default is 1e-3).
ACCURACY: float = 1e-6
#: Diameter (m) of a TCV or check-valve pipe that has no adjacent pipe to take it from. The
#: loss coefficient is computed for this diameter, so the head loss is exact whatever it is.
DEFAULT_DIAMETER: float = 0.05
#: Length (m) of the pipe that carries a check valve; its friction is negligible.
CV_PIPE_LENGTH: float = 0.001
#: Smallest Darcy-Weisbach roughness (m): WNTR rejects 0, worldparts allows it (smooth pipe).
MIN_ROUGHNESS: float = 1e-9
#: Number of points of the multi-point pump curve from zero flow to the run-out flow, sampled
#: from the fitted quadratic.
PUMP_CURVE_POINTS: int = 41
#: The multi-point curve continues at the same spacing, with negative heads, up to this multiple
#: of the run-out flow, so a pump driven past its run-out flow (a booster on a pressurised
#: main) follows the quadratic in EPANET too instead of EPANET's linear extrapolation.
PUMP_CURVE_EXTENT: float = 3.0
#: Longest EPANET ID (characters).
MAX_ID: int = 31
#: Relative speed at or below which a pump is off (worldparts ``OFF_SPEED``).
PUMP_OFF_SPEED: float = 0.01
#: Flows below this (m3/h) or gauge pressures below this (bar) get no relative difference.
FLOW_FLOOR: float = 1e-3
PRESSURE_FLOOR: float = 1e-4

_H = "worldparts.hydraulic."

#: Supported component ids and what each becomes in WNTR.
SUPPORTED_COMPONENTS: dict[str, str] = {
    _H + "supply": "reservoir (head = elevation + gauge pressure / (rho g)) and joint TCV",
    _H + "drain": "reservoir (head = elevation, gauge 0) and joint TCV",
    _H + "pipe": "Darcy-Weisbach pipe; height_difference becomes junction elevations",
    _H + "valve": "TCV whose loss coefficient reproduces the effective Kv",
    _H + "check_valve": "pipe with a check valve (CV); minor loss reproduces the Kv",
    _H + "centrifugal_pump": "HEAD pump, curve from the fitted quadratic, speed setting",
    _H + "tank": "tank (overflow allowed) plus one TCV per connected port for port_kv",
    _H + "media_filter": "TCV matching the pressure drop at a reference flow (approximate)",
    _H + "uv_reactor": "TCV through the rated point (exact quadratic)",
}

#: Why EPANET results differ from the reference runtime (measured sizes in
#: ``docs/wntr-adapter.md``).
KNOWN_DIVERGENCE_SOURCES: tuple[str, ...] = (
    "Friction factor: worldparts uses Churchill (1977) in every regime; EPANET 2.2 uses "
    "64/Re below Re 2000, Swamee-Jain above Re 4000 and a cubic interpolation in between. "
    "In turbulent flow pipe flows agree within about 0.1 %; in the transition region "
    "(Re 2000 to 4000) they differ by several percent (8 % measured at Re 3100).",
    "Gravity constants: EPANET's pipe resistance uses g = 32.2 ft/s2 (9.8146 m/s2), so its "
    "friction head is about 0.08 % below worldparts' (9.80665 m/s2); its minor-loss "
    "constant 0.02517 implies g = 9.8156 m/s2, which the adapter compensates in the TCV "
    "and check-valve coefficients it computes but not in pipe minor losses (0.09 %).",
    "Media filter: its linear media loss cannot be represented in EPANET; the TCV matches "
    "the filter's pressure drop at one reference flow (the worldparts operating point by "
    "default, or the rated flow) and is purely quadratic elsewhere, so a model exported at "
    "one flow diverges at another (11 % of flow at 2.2 times the reference flow).",
    "Pump curve: EPANET's three-point power curve A - B*Q**C is exact when the fitted "
    "linear term is 0; otherwise a multi-point curve is interpolated linearly (a few mm of "
    "head below the quadratic from zero flow to three times the run-out flow, with "
    "negative heads past the run-out flow; beyond its end EPANET extrapolates the last "
    "segment linearly, which the export notes when the operating point is there). EPANET "
    "closes a pump against reverse flow and a stopped "
    "pump (speed <= 0.01) is exported closed, while worldparts lets water through a "
    "stopped pump as a resistance.",
    "Leakage: a closed worldparts check valve passes its leakage fraction in reverse, "
    "EPANET's check valve nothing; EPANET linearises very small valve flows, so relative "
    "differences below 1e-3 m3/h are not reported.",
    "Tank: EPANET holds the tank level fixed in a single-period run, as worldparts' steady "
    "solve does. A full tank keeps receiving water in both (worldparts spills the excess "
    "and warns tank_overflow; the tank is exported with EPANET's overflow option). An "
    "empty tank blocks outflow in both, through different mechanisms: worldparts counts a "
    "level up to 1 mm as empty, so such a level is exported as 0.",
    "Convergence: EPANET stops at ACCURACY = 1e-6 (relative flow change) and reports in "
    "single precision (about 1e-7 relative); worldparts converges to 1e-9 scaled residual. "
    "Viscosity is set to the worldparts value (1.0038e-6 m2/s).",
)

FilterReference = Literal["operating", "rated"]


class WntrExportError(WorldpartsError):
    """A system cannot be carried to WNTR (for example inconsistent pipe elevations)."""

    code = "export_failed"


class UnsupportedComponentError(WntrExportError):
    """The system contains components the WNTR adapter cannot represent.

    Attributes:
        components: ``{instance: component id}`` of the unsupported instances.
    """

    code = "unsupported_component"

    def __init__(self, components: Mapping[str, str]) -> None:
        self.components = dict(components)
        listed = ", ".join(f"{n} ({cid.rsplit('.', 1)[-1]})" for n, cid in components.items())
        supported = ", ".join(c.rsplit(".", 1)[-1] for c in SUPPORTED_COMPONENTS)
        super().__init__(
            f"The WNTR adapter cannot represent {listed}: it has no WNTR mapping for them "
            "(EPANET has no thermal model, so a faucet's mixing or a heater's heat input "
            "cannot be carried over) and does not approximate them silently. "
            f"Supported components: {supported}. Remove or replace the unsupported "
            "instances (for example a faucet by a valve and a drain) to export the network."
        )


def _require_wntr() -> Any:
    try:
        import wntr
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise MissingDependencyError(
            "The WNTR adapter needs the optional 'wntr' package: uv add wntr (or pip "
            "install wntr), or reinstall with the extra: "
            'uv add "worldparts[wntr] @ git+https://github.com/raimondasl/worldparts"'
        ) from exc
    return wntr


# ----------------------------------------------------------------------------------------
# translation records
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeMap:
    """A WNTR node and the worldparts connection node it represents.

    Attributes:
        name: WNTR node name.
        kind: ``junction``, ``reservoir`` or ``tank``.
        ports: worldparts port paths at this node (empty for a tank's storage node).
        elevation: Assigned elevation in m (a tank's bottom for a tank).
        label: worldparts node label (member ports joined by ``" = "``), or the tank name.
    """

    name: str
    kind: str
    ports: tuple[str, ...]
    elevation: float
    label: str


@dataclass(frozen=True)
class LinkMap:
    """A WNTR link and the worldparts flow it carries.

    Attributes:
        name: WNTR link name.
        kind: ``pipe``, ``tcv``, ``cv_pipe``, ``pump`` or ``joint`` (the lossless TCV
            between a supply's or drain's reservoir and its port junction).
        component: worldparts instance.
        start: WNTR start node.
        end: WNTR end node.
        flow_path: worldparts result path of the same flow (``p.volume_flow``, or a tank
            port's ``tank.inlet.m_flow``).
        setting: TCV loss coefficient, pump speed or None.
        diameter: Link diameter in m (None for a pump).
    """

    name: str
    kind: str
    component: str
    start: str
    end: str
    flow_path: str
    setting: float | None = None
    diameter: float | None = None


@dataclass(frozen=True)
class PumpCurveMap:
    """How a pump's fitted quadratic was carried to EPANET.

    EPANET offers a three-point power curve ``A - B*Q**C`` (exact when the fitted linear
    term ``b`` is 0, as for the default pump) and a multi-point curve interpolated linearly.
    The adapter exports the one that deviates less from the quadratic over 0 to
    :data:`PUMP_CURVE_EXTENT` times the run-out flow.

    Attributes:
        component: worldparts instance.
        curve: WNTR curve name.
        form: ``three_point`` or ``multi_point``.
        points: ``(flow m3/s, head m)`` points of the exported curve (rated speed).
        coefficients: ``(a, b, c)`` of the rated-speed fit ``H = a + b*Q + c*Q**2`` (SI).
        runout_flow: Flow (m3/s) at which the fitted head reaches 0 (rated speed).
        multi_point_deviation: Largest head difference (m) of the multi-point curve from the
            quadratic, over 0 to :data:`PUMP_CURVE_EXTENT` times the run-out flow (the
            curve continues past the run-out flow with negative heads).
        three_point_deviation: The same, over the same range, for the three-point power
            curve through 0, half and all of the largest catalogue flow; None when EPANET
            could not fit it.
        fit_rms: RMS residual (m) of the worldparts quadratic fit to the catalogue points.
    """

    component: str
    curve: str
    form: str
    points: tuple[tuple[float, float], ...]
    coefficients: tuple[float, float, float]
    runout_flow: float
    multi_point_deviation: float
    three_point_deviation: float | None
    fit_rms: float

    @property
    def max_deviation(self) -> float:
        """Largest head difference (m) of the exported curve from the quadratic."""
        if self.form == "three_point" and self.three_point_deviation is not None:
            return self.three_point_deviation
        return self.multi_point_deviation


@dataclass
class WntrTranslation:
    """A WNTR model built from a worldparts system, with the name mapping.

    Attributes:
        model: The :class:`wntr.network.WaterNetworkModel`.
        system: Name of the worldparts system.
        nodes: WNTR node name to :class:`NodeMap`.
        links: WNTR link name to :class:`LinkMap`.
        boundaries: Supply or drain instance to its reservoir name (its joint link has the
            instance's name, possibly shortened).
        pumps: Pump instance to :class:`PumpCurveMap`.
        approximations: What this particular export approximates, with magnitudes.
    """

    model: WaterNetworkModel
    system: str
    nodes: dict[str, NodeMap] = field(default_factory=dict)
    links: dict[str, LinkMap] = field(default_factory=dict)
    boundaries: dict[str, str] = field(default_factory=dict)
    pumps: dict[str, PumpCurveMap] = field(default_factory=dict)
    approximations: list[str] = field(default_factory=list)

    def node_of_port(self, port: str) -> NodeMap | None:
        """The node that carries worldparts port ``port`` (``'p.port_a'``), if any."""
        for node in self.nodes.values():
            if port in node.ports:
                return node
        return None


# ----------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------
def _broken(system: wp.System) -> set[str]:
    """Instances whose component type is unknown (``check()`` reports them)."""
    out = set()
    for name in system.components:
        try:
            system.manifest(name)
        except wp.UnknownComponentError:
            out.add(name)
    return out


def _alias(cid: str) -> str:
    return cid.rsplit(".", 1)[-1]


class _Names:
    """Unique EPANET IDs (at most 31 characters, no spaces or semicolons)."""

    def __init__(self) -> None:
        self.taken: set[str] = set()

    def reserve(self, desired: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_.\-]", "_", desired)[:MAX_ID] or "x"
        name, k = base, 2
        while name in self.taken:
            suffix = f"_{k}"
            name = base[: MAX_ID - len(suffix)] + suffix
            k += 1
        self.taken.add(name)
        return name


def _area(diameter: float) -> float:
    return math.pi * diameter * diameter / 4.0


def _loss_coefficient(r_quad: float, diameter: float) -> float:
    """EPANET loss coefficient K of a link with ``dp = r_quad * Q**2`` (Pa, m3/s).

    EPANET's head loss is ``K * v**2 / (2 * g_E)`` with ``g_E =`` :data:`EPANET_G_MINOR`;
    worldparts' head is ``dp / (rho * G)``. Equating them at every flow gives
    ``K = 2 * g_E * A**2 * r_quad / (rho * G)``.
    """
    return 2.0 * EPANET_G_MINOR * _area(diameter) ** 2 * r_quad / (RHO * G)


def _kv_resistance(kv_si: float) -> float:
    """Quadratic resistance (Pa per (m3/s)**2) of a Kv in m3/s: ``dp = 1 bar at Q = Kv``
    for water of 1000 kg/m3, i.e. ``r = 100 * rho / Kv**2`` (the worldparts ``kv_to_k``)."""
    return 1e5 * RHO / (1000.0 * kv_si * kv_si)


def _runout_flow(a: float, b: float, c: float) -> float:
    """Positive root of ``a + b*Q + c*Q**2`` (``a > 0``, ``c < 0``)."""
    return (-b - math.sqrt(b * b - 4.0 * a * c)) / (2.0 * c)


def _power_fit(
    pts: list[tuple[float, float]],
) -> tuple[float, float, float] | None:
    """EPANET's fit of ``A - B*Q**C`` through a three-point curve, or None (as EPANET 2.2).

    ``A = h0``, ``C = ln((h0 - h2) / (h0 - h1)) / ln(q2 / q1)``, ``B = (h0 - h1) / q1**C``;
    EPANET falls back to a multi-point curve when the heads do not fall or ``C`` is outside
    (0, 20].
    """
    (_, h0), (q1, h1), (q2, h2) = pts
    tiny = 1e-6
    if h0 < tiny or h0 - h1 < tiny or h1 - h2 < tiny or q1 < tiny or q2 - q1 < tiny:
        return None
    cc = math.log((h0 - h2) / (h0 - h1)) / math.log(q2 / q1)
    if not 0.0 < cc <= 20.0:
        return None
    return h0, (h0 - h1) / q1**cc, cc


def _pump_curves(
    a: float, b: float, c: float, q_max: float
) -> tuple[list[tuple[float, float]], float, list[tuple[float, float]] | None, float | None]:
    """Multi-point and three-point curves of ``H = a + b*Q + c*Q**2`` with their deviations.

    The multi-point curve has :data:`PUMP_CURVE_POINTS` points from 0 to the run-out flow
    (head 0) and continues at the same spacing, with negative heads, to
    :data:`PUMP_CURVE_EXTENT` times the run-out flow. Returns
    ``(multi_points, multi_dev, three_points, three_dev)``; the deviations are the largest
    head differences (m) from the quadratic on 0 to the end of the multi-point curve.
    """
    q_run = _runout_flow(a, b, c)

    def head(q: float) -> float:
        return a + b * q + c * q * q

    n = PUMP_CURVE_POINTS
    dq = q_run / (n - 1)
    total = round((n - 1) * PUMP_CURVE_EXTENT) + 1
    multi = [(dq * k, head(dq * k)) for k in range(total)]
    multi[n - 1] = (q_run, 0.0)
    q_end = multi[-1][0]
    grid = [q_end * k / 2400.0 for k in range(2401)]

    def interp(q: float) -> float:
        k = min(int(q / dq), total - 2)
        (x0, y0), (x1, y1) = multi[k], multi[k + 1]
        return y0 + (y1 - y0) * (q - x0) / (x1 - x0)

    multi_dev = max(abs(interp(q) - head(q)) for q in grid)
    three = [(0.0, head(0.0)), (0.5 * q_max, head(0.5 * q_max)), (q_max, head(q_max))]
    fit = _power_fit(three)
    if fit is None:
        return multi, multi_dev, None, None
    aa, bb, cc = fit
    three_dev = max(abs(aa - bb * q**cc - head(q)) for q in grid)
    return multi, multi_dev, three, three_dev


def _port_groups(system: wp.System) -> tuple[list[list[str]], dict[str, int], set[str]]:
    """Connection nodes as lists of port paths (the same union as ``System._build``)."""
    paths = [f"{n}.{p}" for n in system.components for p in system.manifest(n).ports]
    parent = {p: p for p in paths}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in system.connections:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    order: dict[str, int] = {}
    groups: list[list[str]] = []
    for p in paths:
        root = find(p)
        if root not in order:
            order[root] = len(groups)
            groups.append([])
        groups[order[root]].append(p)
    index = {p: order[find(p)] for p in paths}
    connected = {p for c in system.connections for p in c}
    return groups, index, connected


# ----------------------------------------------------------------------------------------
# translation
# ----------------------------------------------------------------------------------------
def translate(
    system: wp.System,
    reference: FilterReference = "operating",
    solution: wp.SolveResult | None = None,
) -> WntrTranslation:
    """Build a WNTR model of ``system`` and record how every element maps back.

    Args:
        system: A worldparts system without check errors, made of the components in
            :data:`SUPPORTED_COMPONENTS`.
        reference: Flow at which a media filter's linear-plus-quadratic loss is matched by
            the quadratic TCV: ``"operating"`` (the worldparts steady solution; the rated
            flow when the filter is idle or the system does not solve) or ``"rated"``.
        solution: A worldparts :class:`~worldparts.SolveResult` of ``system`` to take the
            operating point from (solved here when needed and not given).

    Returns:
        The model and the node, link and pump-curve mapping.

    Raises:
        UnsupportedComponentError: The system contains a component WNTR cannot represent.
        SystemCheckError: The system has check errors.
        WntrExportError: The pipe height differences around a loop do not add up to zero.
        MissingDependencyError: ``wntr`` is not installed.
    """
    if reference not in ("operating", "rated"):
        raise wp.InvalidValueError(f"reference must be 'operating' or 'rated', got {reference!r}.")
    comps = system.components
    known = {n for n in comps if n not in _broken(system)}
    unsupported = {n: comps[n] for n in known if comps[n] not in SUPPORTED_COMPONENTS}
    if unsupported:
        raise UnsupportedComponentError(unsupported)
    issues = system.check()
    if any(i.severity == "error" for i in issues):
        raise SystemCheckError(issues)
    wntr = _require_wntr()

    kinds = {n: _alias(cid) for n, cid in comps.items()}
    groups, node_index, connected = _port_groups(system)

    # -- elevations ----------------------------------------------------------------------
    edges: dict[int, list[tuple[int, float, str]]] = {i: [] for i in range(len(groups))}
    for name, kind in kinds.items():
        comp = system.component(name)
        if kind in ("supply", "drain"):
            continue
        if kind == "tank":
            ports = [f"{name}.inlet", f"{name}.outlet"]
            if all(p in connected for p in ports):
                i, j = node_index[ports[0]], node_index[ports[1]]
                edges[i].append((j, 0.0, name))
                edges[j].append((i, 0.0, name))
            continue
        pa, pb = (f"{name}.{p}" for p in system.manifest(name).ports)
        dz = float(comp.parameters["height_difference"]) if kind == "pipe" else 0.0
        i, j = node_index[pa], node_index[pb]
        edges[i].append((j, dz, name))
        edges[j].append((i, -dz, name))

    def rank(i: int) -> int:
        order = {"drain": 0, "supply": 1, "tank": 2}
        best = 3
        for p in groups[i]:
            if p in connected:
                best = min(best, order.get(kinds[p.split(".")[0]], 3))
        return best

    elevation: dict[int, float] = {}
    for root in sorted(range(len(groups)), key=lambda i: (rank(i), i)):
        if root in elevation:
            continue
        elevation[root] = 0.0
        stack = [root]
        while stack:
            i = stack.pop()
            for j, dz, via in edges[i]:
                z = elevation[i] + dz
                if j not in elevation:
                    elevation[j] = z
                    stack.append(j)
                elif abs(elevation[j] - z) > 1e-6:
                    raise WntrExportError(
                        "The pipe height differences around a loop do not add up to zero "
                        f"(at {via}: {elevation[j]:.6g} m versus {z:.6g} m), so no set of "
                        "node elevations reproduces them. Correct the height_difference "
                        "values of the pipes in the loop."
                    )

    # -- model ---------------------------------------------------------------------------
    wn = wntr.network.WaterNetworkModel()
    opts = wn.options
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "changing the headloss formula" notice
        opts.hydraulic.headloss = "D-W"
    opts.hydraulic.inpfile_units = INP_UNITS
    opts.hydraulic.viscosity = (MU / RHO) / EPANET_VISCOSITY
    opts.hydraulic.accuracy = ACCURACY
    opts.time.duration = 0
    wn.title = [
        f"worldparts system '{system.name}' (worldparts {wp.__version__})",
        "Exported by worldparts.adapters.wntr_adapter; see docs/wntr-adapter.md.",
    ]
    tr = WntrTranslation(wn, system.name)
    node_names, link_names, curve_names = _Names(), _Names(), _Names()

    # Reservoirs and tanks are named after their instances; junctions are J1, J2, ...
    reservoirs = {
        n: node_names.reserve(n)
        for n, k in kinds.items()
        if k in ("supply", "drain") and f"{n}.port" in connected
    }
    tank_nodes = {n: node_names.reserve(n) for n, k in kinds.items() if k == "tank"}
    wn_node: dict[int, str] = {}
    for i, members in enumerate(groups):
        needs_node = any(
            p in connected or kinds[p.split(".")[0]] not in ("tank", "supply", "drain")
            for p in members
        )
        if not needs_node:
            continue  # only an unconnected tank or boundary port: nothing flows there
        name = node_names.reserve(f"J{len(wn_node) + 1}")
        wn_node[i] = name
        wn.add_junction(name, base_demand=0.0, elevation=elevation[i])
        tr.nodes[name] = NodeMap(
            name, "junction", tuple(members), elevation[i], " = ".join(sorted(members))
        )

    def adjacent_diameter(*ports: str) -> float:
        for port in ports:
            for other in groups[node_index[port]]:
                inst = other.split(".")[0]
                if kinds[inst] == "pipe" and other in connected:
                    return float(system.component(inst).parameters["diameter"])
        return DEFAULT_DIAMETER

    def add_tcv(name: str, inst: str, a: str, b: str, r_quad: float, diameter: float) -> None:
        k = _loss_coefficient(r_quad, diameter)
        wn.add_valve(name, a, b, diameter=diameter, valve_type="TCV", initial_setting=k)
        path = f"{inst}.volume_flow"
        tr.links[name] = LinkMap(name, "tcv", inst, a, b, path, k, diameter)

    operating: list[wp.SolveResult | None] = [solution]
    solved = [solution is not None]

    def operating_flow(inst: str) -> float | None:
        """worldparts steady flow (m3/s) of ``inst``, or None when the system does not solve."""
        if not solved[0]:
            solved[0] = True
            try:
                operating[0] = system.solve()
            except WorldpartsError:
                operating[0] = None
        result = operating[0]
        if result is None:
            return None
        q = result.get(f"{inst}.volume_flow", unit="m3/s")
        return None if q is None else float(q)

    for inst, kind in kinds.items():
        comp = system.component(inst)
        p = comp.parameters
        if kind in ("supply", "drain"):
            if inst not in reservoirs:
                continue
            port = f"{inst}.port"
            z = elevation[node_index[port]]
            gauge = float(p["pressure"]) - P_ATM if kind == "supply" else 0.0
            reservoir, junction = reservoirs[inst], wn_node[node_index[port]]
            wn.add_reservoir(reservoir, base_head=z + gauge / (RHO * G))
            tr.nodes[reservoir] = NodeMap(reservoir, "reservoir", (), z, inst)
            tr.boundaries[inst] = reservoir
            # Oriented so the link flow is the boundary's volume_flow (into the network for
            # a supply, into the drain for a drain).
            a, b = (reservoir, junction) if kind == "supply" else (junction, reservoir)
            name = link_names.reserve(inst)
            d = adjacent_diameter(port)
            wn.add_valve(name, a, b, diameter=d, valve_type="TCV", initial_setting=0.0)
            tr.links[name] = LinkMap(name, "joint", inst, a, b, f"{inst}.volume_flow", 0.0, d)
            continue
        if kind == "tank":
            level = min(float(comp.states["level"]), float(p["height"]))
            if level <= EMPTY_LEVEL:
                # worldparts counts a tank as empty up to EMPTY_LEVEL and blocks its outflow;
                # EPANET does so only at min_level, so an "empty" tank is exported at 0.
                level = 0.0
            z = elevation[node_index[f"{inst}.inlet"]]
            for port in ("inlet", "outlet"):
                if f"{inst}.{port}" in connected:
                    z = elevation[node_index[f"{inst}.{port}"]]
                    break
            wn.add_tank(
                tank_nodes[inst],
                elevation=z,
                init_level=level,
                min_level=0.0,
                max_level=float(p["height"]),
                diameter=float(p["diameter"]),
                # A full steady worldparts tank spills what exceeds its rim (tank_overflow)
                # and keeps receiving water; without the overflow option EPANET would close
                # every link that fills a full tank.
                overflow=True,
            )
            tr.nodes[tank_nodes[inst]] = NodeMap(tank_nodes[inst], "tank", (), z, inst)
            for port in ("inlet", "outlet"):
                path = f"{inst}.{port}"
                if path not in connected:
                    continue
                name = link_names.reserve(f"{inst}_{port}")
                d = adjacent_diameter(path)
                r = _kv_resistance(float(p["port_kv"]))
                k = _loss_coefficient(r, d)
                a = wn_node[node_index[path]]
                wn.add_valve(name, a, tank_nodes[inst], diameter=d, valve_type="TCV",
                             initial_setting=k)  # fmt: skip
                tr.links[name] = LinkMap(
                    name, "tcv", inst, a, tank_nodes[inst], f"{path}.m_flow", k, d
                )
            continue
        pa, pb = (f"{inst}.{q}" for q in system.manifest(inst).ports)
        a, b = wn_node[node_index[pa]], wn_node[node_index[pb]]
        name = link_names.reserve(inst)
        if kind == "pipe":
            d = float(p["diameter"])
            wn.add_pipe(
                name,
                a,
                b,
                length=float(p["length"]),
                diameter=d,
                roughness=max(float(p["roughness"]), MIN_ROUGHNESS),
                minor_loss=float(p["minor_loss"]),
            )
            tr.links[name] = LinkMap(name, "pipe", inst, a, b, f"{inst}.volume_flow", None, d)
        elif kind == "valve":
            opening = float(comp.inputs["opening"])  # solve() settles the position here
            phi = characteristic(
                p["characteristic"], opening, float(p["leakage"]), float(p["rangeability"])
            )
            add_tcv(name, inst, a, b, _kv_resistance(float(p["kv"]) * phi),
                    adjacent_diameter(pa, pb))  # fmt: skip
        elif kind == "check_valve":
            d = adjacent_diameter(pa, pb)
            k = _loss_coefficient(_kv_resistance(float(p["kv"])), d)
            wn.add_pipe(name, a, b, length=CV_PIPE_LENGTH, diameter=d, roughness=MIN_ROUGHNESS,
                        minor_loss=k, check_valve=True)  # fmt: skip
            tr.links[name] = LinkMap(name, "cv_pipe", inst, a, b, f"{inst}.volume_flow", k, d)
        elif kind == "uv_reactor":
            r = float(p["rated_pressure_drop"]) / float(p["rated_flow"]) ** 2
            add_tcv(name, inst, a, b, r, adjacent_diameter(pa, pb))
        elif kind == "media_filter":
            q_rated = float(p["rated_flow"])
            r_lin, r_quad = filter_coefficients(
                q_rated,
                float(p["clean_pressure_drop"]),
                float(p["housing_fraction"]),
                float(comp.inputs["clogging"]),
            )
            q_ref, source = q_rated, "rated flow"
            if reference == "operating":
                q_op = operating_flow(inst)
                if q_op is not None and abs(q_op) > 1e-3 * q_rated:
                    q_ref, source = abs(q_op), "worldparts operating point"
            r_eq = r_lin / q_ref + r_quad
            add_tcv(name, inst, a, b, r_eq, adjacent_diameter(pa, pb))
            tr.approximations.append(
                f"{inst} (media_filter): quadratic TCV matching the pressure drop at "
                f"{q_ref * 3600:.4g} m3/h ({source}); the linear media term "
                f"({(1 - float(p['housing_fraction'])) * 100:.3g} % of the clean drop at "
                "rated flow) is not represented, so the drop is too low below and too high "
                "above that flow."
            )
        elif kind == "centrifugal_pump":
            fit = comp.fit
            ca, cb, cc = fit.head
            multi, multi_dev, three, three_dev = _pump_curves(ca, cb, cc, fit.max_flow)
            use_three = three is not None and three_dev is not None and three_dev <= multi_dev
            pts = three if use_three and three is not None else multi
            form = "three_point" if use_three else "multi_point"
            curve = curve_names.reserve(f"{inst}_H")
            wn.add_curve(curve, "HEAD", pts)
            speed = float(comp.inputs["speed"])
            status = "OPEN" if speed > PUMP_OFF_SPEED else "CLOSED"
            wn.add_pump(name, a, b, pump_type="HEAD", pump_parameter=curve,
                        speed=max(speed, 0.0), initial_status=status)  # fmt: skip
            tr.links[name] = LinkMap(name, "pump", inst, a, b, f"{inst}.volume_flow", speed)
            pump_map = PumpCurveMap(
                inst,
                curve,
                form,
                tuple(pts),
                (ca, cb, cc),
                multi[PUMP_CURVE_POINTS - 1][0],
                multi_dev,
                three_dev,
                fit.head_rms,
            )
            tr.pumps[inst] = pump_map
            other = (
                f"a multi-point curve would deviate up to {multi_dev:.3g} m"
                if use_three
                else (
                    f"a three-point power curve would deviate up to {three_dev:.3g} m"
                    if three_dev is not None
                    else "EPANET cannot fit a three-point power curve to it"
                )
            )
            what = (
                "three-point power curve (exact when the fitted linear term is 0)"
                if use_three
                else (
                    f"{len(pts)}-point curve sampled from the fitted quadratic "
                    f"({PUMP_CURVE_POINTS} points to the run-out flow, then negative heads to "
                    f"{PUMP_CURVE_EXTENT:g} times it)"
                )
            )
            tr.approximations.append(
                f"{inst} (centrifugal_pump): {what}, at most {pump_map.max_deviation:.3g} m "
                f"from the quadratic ({other}); the quadratic itself fits the catalogue "
                f"points with RMS {fit.head_rms:.3g} m."
            )
            q_end = pts[-1][0] * speed
            q_op = operating_flow(inst) if status == "OPEN" and not use_three else None
            if q_op is not None and q_op > q_end:
                tr.approximations.append(
                    f"{inst} (centrifugal_pump): the worldparts operating flow "
                    f"{q_op * 3600:.4g} m3/h is beyond the end of the exported curve "
                    f"({q_end * 3600:.4g} m3/h at speed {speed:g}), where EPANET extrapolates "
                    "the last segment linearly and worldparts follows the quadratic."
                )
            if status == "CLOSED":
                tr.approximations.append(
                    f"{inst} (centrifugal_pump): stopped (speed {speed:g}), exported CLOSED; "
                    "worldparts treats a stopped pump as a resistance."
                )
    return tr


def to_wntr(system: wp.System, reference: FilterReference = "operating") -> WaterNetworkModel:
    """A :class:`wntr.network.WaterNetworkModel` of ``system`` (see :func:`translate`)."""
    return translate(system, reference).model


def model_to_inp(model: WaterNetworkModel, path: str | os.PathLike[str] | None = None) -> str:
    """EPANET ``.inp`` text of a WNTR model (flow units :data:`INP_UNITS`).

    Args:
        model: A model from :func:`to_wntr` or :func:`translate`.
        path: Where to write the file as well (optional).
    """
    wntr = _require_wntr()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        target = Path(tmp) / "system.inp"
        wntr.network.io.write_inpfile(model, str(target), units=INP_UNITS)
        text = target.read_text(encoding="utf-8")
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


def export_inp(
    system: wp.System,
    path: str | os.PathLike[str] | None = None,
    reference: FilterReference = "operating",
) -> str:
    """EPANET ``.inp`` text of ``system`` (flow units CMH, Darcy-Weisbach), written by WNTR.

    Args:
        system: The worldparts system (see :func:`translate` for what is supported).
        path: Where to write the file as well (optional).
        reference: Media-filter reference flow (see :func:`translate`).

    Returns:
        The ``.inp`` text.
    """
    return model_to_inp(to_wntr(system, reference), path)


# ----------------------------------------------------------------------------------------
# comparison
# ----------------------------------------------------------------------------------------
def _diffs(ref: float, other: float, floor: float) -> tuple[float, float | None]:
    diff = other - ref
    return diff, (abs(diff) / abs(ref) if abs(ref) > floor else None)


@dataclass(frozen=True)
class LinkComparison:
    """One flow compared: a component's flow (m3/h) in worldparts and in WNTR.

    Attributes:
        path: worldparts result path (``p.volume_flow``, ``mains.volume_flow`` or a tank
            port's ``tank.inlet.m_flow``).
        wntr: WNTR link carrying the flow (a supply's or drain's joint for its flow).
        kind: WNTR link kind (``pipe``, ``tcv``, ``cv_pipe``, ``pump`` or ``joint``).
        worldparts: worldparts flow in m3/h, in the worldparts sign convention.
        wntr_value: WNTR flow in m3/h, in the same convention.
        abs_diff: ``wntr_value - worldparts`` in m3/h.
        rel_diff: ``|abs_diff| / |worldparts|`` (None below 1e-3 m3/h, where leakage flows
            through closed valves differ in relative terms).
    """

    path: str
    wntr: str
    kind: str
    worldparts: float
    wntr_value: float
    abs_diff: float
    rel_diff: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict."""
        return {
            "path": self.path,
            "wntr": self.wntr,
            "kind": self.kind,
            "worldparts": self.worldparts,
            "wntr_value": self.wntr_value,
            "abs_diff": self.abs_diff,
            "rel_diff": self.rel_diff,
        }


@dataclass(frozen=True)
class NodeComparison:
    """One node pressure compared, in bar gauge.

    Attributes:
        node: worldparts node label (member ports joined by ``" = "``).
        wntr: WNTR node name.
        elevation: Assigned elevation in m.
        worldparts: worldparts pressure (bar gauge) at the node's ports.
        wntr_value: WNTR pressure ``(head - elevation) * rho * g`` in bar gauge.
        abs_diff: ``wntr_value - worldparts`` in bar.
        rel_diff: ``|abs_diff| / |worldparts|`` (None below 1e-4 bar).
    """

    node: str
    wntr: str
    elevation: float
    worldparts: float
    wntr_value: float
    abs_diff: float
    rel_diff: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict."""
        return {
            "node": self.node,
            "wntr": self.wntr,
            "elevation": self.elevation,
            "worldparts": self.worldparts,
            "wntr_value": self.wntr_value,
            "abs_diff": self.abs_diff,
            "rel_diff": self.rel_diff,
        }


@dataclass
class ComparisonReport:
    """worldparts versus WNTR/EPANET for one steady operating point.

    Attributes:
        system: worldparts system name.
        simulator: ``epanet``.
        links: Flow comparisons (m3/h).
        nodes: Pressure comparisons (bar gauge).
        approximations: What this export approximates (per component, with magnitudes).
        divergence_sources: Known sources of divergence (:data:`KNOWN_DIVERGENCE_SOURCES`).
    """

    system: str
    simulator: str
    links: list[LinkComparison]
    nodes: list[NodeComparison]
    approximations: list[str]
    divergence_sources: list[str]

    @property
    def max_flow_abs_diff(self) -> float:
        """Largest absolute flow difference in m3/h."""
        return max((abs(c.abs_diff) for c in self.links), default=0.0)

    @property
    def max_flow_rel_diff(self) -> float:
        """Largest relative flow difference (flows above 1e-3 m3/h)."""
        return max((c.rel_diff for c in self.links if c.rel_diff is not None), default=0.0)

    @property
    def max_pressure_abs_diff(self) -> float:
        """Largest absolute pressure difference in bar."""
        return max((abs(c.abs_diff) for c in self.nodes), default=0.0)

    @property
    def max_pressure_rel_diff(self) -> float:
        """Largest relative pressure difference (gauge pressures above 1e-4 bar)."""
        return max((c.rel_diff for c in self.nodes if c.rel_diff is not None), default=0.0)

    def link(self, path: str) -> LinkComparison:
        """The flow comparison of worldparts path ``path`` (or instance name)."""
        for c in self.links:
            if c.path == path or c.path.split(".")[0] == path:
                return c
        raise KeyError(path)

    def node(self, port: str) -> NodeComparison:
        """The pressure comparison of the node carrying port ``port`` (``'p.port_b'``)."""
        for c in self.nodes:
            if port in c.node.split(" = "):
                return c
        raise KeyError(port)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict with units, maxima, every comparison and the explanations."""
        return {
            "system": self.system,
            "simulator": self.simulator,
            "flow_unit": "m3/h",
            "pressure_unit": "bar",
            "pressure_reference": "gauge",
            "max_flow_abs_diff": self.max_flow_abs_diff,
            "max_flow_rel_diff": self.max_flow_rel_diff,
            "max_pressure_abs_diff": self.max_pressure_abs_diff,
            "max_pressure_rel_diff": self.max_pressure_rel_diff,
            "links": [c.to_dict() for c in self.links],
            "nodes": [c.to_dict() for c in self.nodes],
            "approximations": list(self.approximations),
            "divergence_sources": list(self.divergence_sources),
        }

    def summary(self) -> str:
        """A readable table of the differences and the explanations.

        ``element`` and ``node id`` are the EPANET link and node names; the ``worldparts``
        and ``wntr`` columns are the two values.
        """

        def rel(x: float | None) -> str:
            return "-" if x is None else f"{x * 100:.3g} %"

        lines = [
            f"worldparts vs WNTR ({self.simulator}) for system '{self.system}'",
            f"  max flow difference     {self.max_flow_abs_diff:.4g} m3/h "
            f"({self.max_flow_rel_diff * 100:.3g} %)",
            f"  max pressure difference {self.max_pressure_abs_diff:.4g} bar "
            f"({self.max_pressure_rel_diff * 100:.3g} %)",
            "",
            "Flows [m3/h]",
        ]
        rows = [
            (
                c.path,
                c.wntr,
                f"{c.worldparts:.6g}",
                f"{c.wntr_value:.6g}",
                f"{c.abs_diff:+.3g}",
                rel(c.rel_diff),
            )
            for c in self.links
        ]
        lines += _format_table(["path", "element", "worldparts", "wntr", "diff", "rel"], rows)
        lines += ["", "Pressures [bar gauge]"]
        rows = [
            (
                c.node,
                c.wntr,
                f"{c.elevation:.4g}",
                f"{c.worldparts:.6g}",
                f"{c.wntr_value:.6g}",
                f"{c.abs_diff:+.3g}",
                rel(c.rel_diff),
            )
            for c in self.nodes
        ]
        lines += _format_table(
            ["node", "node id", "z [m]", "worldparts", "wntr", "diff", "rel"], rows
        )
        if self.approximations:
            lines += ["", "Approximations in this export"]
            lines += [f"  - {a}" for a in self.approximations]
        lines += ["", "Known divergence sources"]
        lines += [f"  - {s}" for s in self.divergence_sources]
        return "\n".join(lines)


def _format_table(headers: list[str], rows: list[tuple[str, ...]]) -> list[str]:
    widths = [len(h) for h in headers]
    for r in rows:
        widths = [max(w, len(str(x))) for w, x in zip(widths, r, strict=True)]

    def line(cells: tuple[str, ...] | list[str]) -> str:
        return "  " + "  ".join(str(x).ljust(w) for x, w in zip(cells, widths, strict=True))

    return [line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def _run_epanet(model: WaterNetworkModel) -> Any:
    wntr = _require_wntr()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        sim = wntr.sim.EpanetSimulator(model)
        try:
            return sim.run_sim(file_prefix=os.path.join(tmp, "worldparts"),
                               convergence_error=True)  # fmt: skip
        except Exception as exc:  # EPANET toolkit errors are plain exceptions
            raise SolverError(f"EPANET did not solve the exported network: {exc}") from exc


def compare_with_wntr(
    system: wp.System,
    simulator: str = "epanet",
    reference: FilterReference = "operating",
) -> ComparisonReport:
    """Solve ``system`` in worldparts and in EPANET (through WNTR) and compare.

    Args:
        system: The worldparts system (see :func:`translate` for what is supported).
        simulator: ``"epanet"`` (WNTR's ``EpanetSimulator``, EPANET 2.2). WNTR's own
            ``WNTRSimulator`` is not supported: it rejects Darcy-Weisbach head loss.
        reference: Media-filter reference flow (see :func:`translate`).

    Returns:
        Per-link flow and per-node pressure differences with explanations.

    Raises:
        WntrExportError: For an unknown simulator (and the errors of :func:`translate`).
        SolverError: When either solver fails.
    """
    if simulator != "epanet":
        raise WntrExportError(
            f"Unknown simulator {simulator!r}: use 'epanet'. WNTR's own WNTRSimulator "
            "(simulator 'wntr') does not support the Darcy-Weisbach head loss the pipes need."
        )
    solution = system.solve()
    tr = translate(system, reference, solution)
    res = _run_epanet(tr.model)
    flows = res.link["flowrate"].iloc[0]
    heads = res.node["head"].iloc[0]
    links: list[LinkComparison] = []
    for link in tr.links.values():
        if link.flow_path.endswith(".m_flow"):
            wp_q = float(solution[link.flow_path] or 0.0) / RHO * 3600.0
        else:
            wp_q = float(solution.get(link.flow_path, unit="m3/h") or 0.0)
        q = float(flows[link.name]) * 3600.0
        diff, rel = _diffs(wp_q, q, FLOW_FLOOR)
        links.append(LinkComparison(link.flow_path, link.name, link.kind, wp_q, q, diff, rel))
    nodes: list[NodeComparison] = []
    for node in tr.nodes.values():
        if not node.ports:
            continue
        wp_p = float(solution[f"{node.ports[0]}.p"] or 0.0)
        p = (float(heads[node.name]) - node.elevation) * RHO * G / 1e5
        diff, rel = _diffs(wp_p, p, PRESSURE_FLOOR)
        nodes.append(NodeComparison(node.label, node.name, node.elevation, wp_p, p, diff, rel))
    return ComparisonReport(
        system=system.name,
        simulator=simulator,
        links=links,
        nodes=nodes,
        approximations=list(tr.approximations),
        divergence_sources=list(KNOWN_DIVERGENCE_SOURCES),
    )
