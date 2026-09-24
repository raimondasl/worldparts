"""Primitive network and the steady solver (design sections 5.1 and 5.3).

A network is a graph of :class:`Node` objects joined by :class:`Branch` objects. Free nodes
have unknown pressure; fixed nodes have a known absolute pressure ``p`` and the temperature
``T`` of water leaving them into the network. Each branch carries a mass flow ``m`` (positive
from ``a`` to ``b``) governed by a monotone :class:`~worldparts.laws.Law`.

The hydraulic problem is solved with Newton-Raphson on the scaled system (pressures scaled by
1e5 Pa, flows by 1 kg/s) with a backtracking line search. If that fails, a globally convergent
node-pressure Newton method is tried: branch flows are obtained by inverting each monotone law
exactly, which makes the mass balance the gradient of a convex potential, and the step length
is chosen by a line search on that potential's directional derivative. The last resort is
``scipy.optimize.root(method="hybr")``. Temperatures are then computed by flow-weighted mixing:
one sweep in flow order when the flows are acyclic, a direct solve of the mixing equations
when water recirculates.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from worldparts.errors import SolverError
from worldparts.laws import Law

__all__ = [
    "Branch",
    "Injection",
    "Network",
    "NetworkSolution",
    "Node",
    "ThermalMap",
]

#: ``thermal(T_upstream_K, m) -> T_downstream_K``; ``m`` is the signed branch flow.
ThermalMap = Callable[[float, float], float]

P_SCALE = 1e5
M_SCALE = 1.0
#: Flows below this magnitude (kg/s) do not carry temperature into a node.
T_FLOW_THRESHOLD = 1e-10


class Node:
    """A network node.

    Attributes:
        label: Human-readable name used in messages.
        fixed: True for a fixed-pressure node.
        p: Absolute pressure in Pa (fixed nodes; may be changed between solves).
        T: Temperature in K of water leaving a fixed node into the network.
    """

    __slots__ = ("T", "fixed", "index", "label", "p")

    def __init__(
        self, label: str, fixed: bool = False, p: float | None = None, T: float | None = None
    ) -> None:
        self.label = label
        self.fixed = fixed
        self.p = p
        self.T = T
        self.index = -1

    def __repr__(self) -> str:
        kind = f"fixed p={self.p}" if self.fixed else "free"
        return f"Node({self.label!r}, {kind})"


class Branch:
    """A two-terminal element between nodes ``a`` and ``b``.

    Attributes:
        a: Upstream node in the forward direction.
        b: Downstream node in the forward direction.
        law: The pressure-flow law, ``p_a - p_b = law.dp(m)``.
        thermal: Optional temperature map applied to water passing the branch.
        label: Human-readable name used in messages.
    """

    __slots__ = ("a", "b", "index", "label", "law", "thermal")

    def __init__(
        self,
        a: Node,
        b: Node,
        law: Law,
        thermal: ThermalMap | None = None,
        label: str = "",
    ) -> None:
        self.a = a
        self.b = b
        self.law = law
        self.thermal = thermal
        self.label = label or f"{a.label}->{b.label}"
        self.index = -1

    def __repr__(self) -> str:
        return f"Branch({self.label!r})"


@dataclass
class Injection:
    """A fixed mass inflow ``m`` (kg/s) at ``node`` with temperature ``T`` (K) when m > 0.

    Attributes may be changed between solves.
    """

    node: Node
    m: float
    T: float | None = None
    label: str = ""


@dataclass
class NetworkSolution:
    """Solved state of a network.

    Attributes:
        p: Absolute pressure per node index (Pa).
        m: Mass flow per branch index (kg/s).
        T: Temperature per node index (K), None where no water flows in.
        converged: Whether the solver met the tolerance.
        iterations: Newton iterations used (plus fallback evaluations, if any).
        max_residual: Final scaled maximum residual.
        method: ``"newton"``, ``"node-newton"`` or ``"hybr"``.
    """

    p: np.ndarray
    m: np.ndarray
    T: list[float | None]
    converged: bool = True
    iterations: int = 0
    max_residual: float = 0.0
    method: str = "newton"
    x: np.ndarray | None = field(default=None, repr=False)


class Network:
    """A graph of nodes, branches and injections with a steady solver."""

    def __init__(self) -> None:
        self.nodes: list[Node] = []
        self.branches: list[Branch] = []
        self.injections: list[Injection] = []
        # unreferenced_subnetworks() by (node count, branch count): the structure only grows
        # by the add_* methods, and a simulation solves the same structure at every step.
        self._unreferenced: tuple[tuple[int, int], list[list[Node]]] | None = None

    # -- construction -----------------------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        """Add a node and return it."""
        node.index = len(self.nodes)
        self.nodes.append(node)
        return node

    def add_branch(self, branch: Branch) -> Branch:
        """Add a branch (its nodes must already be in the network) and return it."""
        for n in (branch.a, branch.b):
            if n.index < 0 or n.index >= len(self.nodes) or self.nodes[n.index] is not n:
                raise ValueError(f"Node {n.label!r} of branch {branch.label!r} is not in network.")
        branch.index = len(self.branches)
        self.branches.append(branch)
        return branch

    def add_injection(self, injection: Injection) -> Injection:
        """Add a fixed mass injection and return it."""
        self.injections.append(injection)
        return injection

    # -- structure --------------------------------------------------------------------------
    def subnetworks(self) -> list[list[Node]]:
        """Connected sub-networks (by branches), each a list of nodes."""
        parent = list(range(len(self.nodes)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for br in self.branches:
            ra, rb = find(br.a.index), find(br.b.index)
            if ra != rb:
                parent[ra] = rb
        groups: dict[int, list[Node]] = {}
        for n in self.nodes:
            groups.setdefault(find(n.index), []).append(n)
        return list(groups.values())

    def unreferenced_subnetworks(self) -> list[list[Node]]:
        """Sub-networks that contain branches but no fixed-pressure node (computed once per
        structure)."""
        key = (len(self.nodes), len(self.branches))
        if self._unreferenced is None or self._unreferenced[0] != key:
            self._unreferenced = (key, self._find_unreferenced())
        return [list(group) for group in self._unreferenced[1]]

    def _find_unreferenced(self) -> list[list[Node]]:
        touched = {br.a.index for br in self.branches} | {br.b.index for br in self.branches}
        out = []
        for group in self.subnetworks():
            if any(n.fixed for n in group):
                continue
            if any(n.index in touched for n in group):
                out.append(group)
        return out

    # -- solver -----------------------------------------------------------------------------
    def solve(
        self,
        x0: np.ndarray | None = None,
        tol: float = 1e-9,
        max_iter: int = 100,
    ) -> NetworkSolution:
        """Solve pressures, flows and temperatures.

        Args:
            x0: Optional warm start (the ``x`` of a previous solution of the same network).
            tol: Convergence tolerance on the scaled maximum residual.
            max_iter: Newton iteration limit before the scipy fallback.

        Returns:
            The :class:`NetworkSolution`.

        Raises:
            SolverError: When neither Newton nor the fallback converges, or a sub-network has
                no fixed-pressure node.
        """
        for n in self.nodes:
            if n.fixed and (n.p is None or not math.isfinite(n.p)):
                raise SolverError(f"Fixed node {n.label!r} has no valid pressure (p={n.p}).")
        bad = self.unreferenced_subnetworks()
        if bad:
            labels = ", ".join(n.label for n in bad[0])
            raise SolverError(
                f"No pressure reference: the sub-network with nodes {labels} has no fixed-"
                "pressure node. Connect a supply, drain or tank to it."
            )
        problem = _Problem(self)
        x_init = problem.initial_guess() if x0 is None or len(x0) != problem.n else x0.copy()
        x, iters, res, ok = problem.newton(x_init, tol, max_iter)
        method = "newton"
        if not ok:
            x_best, res_best = x, res
            x, iters2, res, ok = problem.node_newton(x_init, tol)
            iters += iters2
            method = "node-newton"
            if not ok and res_best < res:
                x, res = x_best, res_best
        if not ok:
            x, iters2, res, ok = problem.fallback(x, x_init, tol)
            iters += iters2
            method = "hybr"
        if not ok:
            worst = problem.worst_equation(x)
            raise SolverError(
                f"The hydraulic solver did not converge (scaled max residual {res:.3g} after "
                f"{iters} iterations; worst equation: {worst}). Typical causes: a pump running "
                "against a closed path, parallel branches with extreme resistance ratios, or "
                "parameters far outside realistic ranges. Try opening a valve slightly, adding a "
                "pipe, or checking parameter units.",
                residual=res,
                worst=worst,
            )
        p, m = problem.unpack(x)
        T = self._temperatures(p, m)
        return NetworkSolution(p, m, T, True, iters, res, method, x)

    def _temperatures(self, p: np.ndarray, m: np.ndarray) -> list[float | None]:
        """Flow-weighted mixing temperatures (design section 5.3).

        Each free node's temperature is the flow-weighted mean of the temperatures of the
        water flowing into it (branch outflows after their ``thermal`` map, and injections).
        Nodes that no temperature source reaches along the flow get ``None``.

        When the flow graph is acyclic, one sweep in flow order gives the exact solution.
        With recirculation (a cycle of flows), the mixing equations are solved directly:
        each thermal map is linearised as ``T_out = a * T_in + b`` at the current estimate,
        the linear system is solved, and the linearisation is repeated (Newton) until the
        temperatures change by less than 1e-9 K. Identity and piecewise-affine maps converge
        in a few iterations.

        Raises:
            SolverError: When the recirculating mixing problem does not converge.
        """
        n = len(self.nodes)
        T: list[float | None] = [nd.T if nd.fixed else None for nd in self.nodes]
        # inflows[j]: list of (upstream node index, branch, |m|)
        inflows: list[list[tuple[int, Branch, float]]] = [[] for _ in range(n)]
        for br in self.branches:
            mi = float(m[br.index])
            if mi > T_FLOW_THRESHOLD:
                inflows[br.b.index].append((br.a.index, br, mi))
            elif mi < -T_FLOW_THRESHOLD:
                inflows[br.a.index].append((br.b.index, br, -mi))
        inj_in: list[list[Injection]] = [[] for _ in range(n)]
        for inj in self.injections:
            if inj.m > T_FLOW_THRESHOLD and inj.T is not None:
                inj_in[inj.node.index].append(inj)
        reach = _reached(self.nodes, inflows, inj_in)
        free = [nd.index for nd in self.nodes if not nd.fixed and reach[nd.index]]
        # Inflows from unreached nodes carry no temperature (they are negligible leaks).
        inflows = [[(u, br, w) for u, br, w in inflows[j] if reach[u]] for j in range(n)]
        order, acyclic = _flow_order(free, inflows)
        if acyclic:
            for j in order:
                T[j] = _mix(j, inflows, inj_in, m, T)
            return T
        return _solve_mixing(free, inflows, inj_in, m, T)


def _branch_out(br: Branch, t_up: float, m: np.ndarray) -> float:
    return br.thermal(t_up, float(m[br.index])) if br.thermal else t_up


def _mix(
    j: int,
    inflows: list[list[tuple[int, Branch, float]]],
    inj_in: list[list[Injection]],
    m: np.ndarray,
    T: list[float | None],
) -> float | None:
    """Flow-weighted mean temperature of the water flowing into node ``j``."""
    num = 0.0
    den = 0.0
    for u, br, w in inflows[j]:
        tu = T[u]
        if tu is None:
            continue
        num += w * _branch_out(br, tu, m)
        den += w
    for inj in inj_in[j]:
        assert inj.T is not None
        num += inj.m * inj.T
        den += inj.m
    return num / den if den > 0 else None


def _reached(
    nodes: Sequence[Node],
    inflows: list[list[tuple[int, Branch, float]]],
    inj_in: list[list[Injection]],
) -> list[bool]:
    """Nodes reached along the flow from a temperature source (fixed node or injection)."""
    n = len(nodes)
    downstream: list[list[int]] = [[] for _ in range(n)]
    for j in range(n):
        for u, _, _ in inflows[j]:
            downstream[u].append(j)
    reach = [False] * n
    stack = [nd.index for nd in nodes if nd.fixed and nd.T is not None]
    stack += [j for j in range(n) if inj_in[j]]
    while stack:
        j = stack.pop()
        if reach[j]:
            continue
        reach[j] = True
        stack.extend(k for k in downstream[j] if not reach[k])
    for nd in nodes:  # fixed nodes keep their own temperature (or None)
        if nd.fixed:
            reach[nd.index] = nd.T is not None
    return reach


def _solve_mixing(
    free: list[int],
    inflows: list[list[tuple[int, Branch, float]]],
    inj_in: list[list[Injection]],
    m: np.ndarray,
    T: list[float | None],
    tol: float = 1e-9,
    max_iter: int = 100,
) -> list[float | None]:
    """Solve the mixing equations of the reached free nodes directly (see _temperatures)."""
    pos = {j: i for i, j in enumerate(free)}
    nf = len(free)
    sources = [t for t in T if t is not None]
    sources += [inj.T for js in inj_in for inj in js if inj.T is not None]
    x = np.full(nf, float(np.mean(sources)) if sources else 293.15)
    for _ in range(max_iter):
        a_mat = np.zeros((nf, nf))
        rhs = np.zeros(nf)
        for j in free:
            i = pos[j]
            for u, br, w in inflows[j]:
                a_mat[i, i] += w
                pu = pos.get(u)
                tu = float(x[pu]) if pu is not None else T[u]
                assert tu is not None
                t_out = _branch_out(br, tu, m)
                slope = 1.0
                if br.thermal is not None:
                    h = 1e-4
                    slope = (_branch_out(br, tu + h, m) - t_out) / h
                if pu is not None:
                    rhs[i] += w * (t_out - slope * tu)
                    a_mat[i, pu] -= w * slope
                else:
                    rhs[i] += w * t_out
            for inj in inj_in[j]:
                assert inj.T is not None
                a_mat[i, i] += inj.m
                rhs[i] += inj.m * inj.T
        try:
            x_new = np.linalg.solve(a_mat, rhs)
        except np.linalg.LinAlgError:
            x_new = np.linalg.lstsq(a_mat, rhs, rcond=None)[0]
        if not np.all(np.isfinite(x_new)):
            break
        change = float(np.max(np.abs(x_new - x))) if nf else 0.0
        x = x_new
        if change < tol:
            out = list(T)
            for j in free:
                out[j] = float(x[pos[j]])
            return out
    raise SolverError(
        "The temperature mixing equations did not converge in a recirculating flow loop "
        f"after {max_iter} iterations. Check the thermal maps (heaters) inside the loop."
    )


def _flow_order(
    free: Sequence[int], inflows: list[list[tuple[int, Branch, float]]]
) -> tuple[list[int], bool]:
    """Order free nodes along the flow (Kahn's algorithm).

    Returns:
        ``(order, acyclic)``. When the flow graph has a cycle, the nodes on or behind it are
        appended at the end and ``acyclic`` is False.
    """
    free_set = set(free)
    pending = {j: sum(1 for u, _, _ in inflows[j] if u in free_set) for j in free}
    downstream: dict[int, list[int]] = {j: [] for j in free}
    for j in free:
        for u, _, _ in inflows[j]:
            if u in free_set:
                downstream[u].append(j)
    ready = [j for j in free if pending[j] == 0]
    order: list[int] = []
    seen: set[int] = set()
    while ready:
        j = ready.pop()
        if j in seen:
            continue
        seen.add(j)
        order.append(j)
        for k in downstream[j]:
            pending[k] -= 1
            if pending[k] <= 0:
                ready.append(k)
    acyclic = len(seen) == len(free_set)
    order.extend(j for j in free if j not in seen)
    return order, acyclic


class _Problem:
    """The scaled nonlinear system ``F(x) = 0`` for one network."""

    def __init__(self, net: Network) -> None:
        self.net = net
        touched = {br.a.index for br in net.branches} | {br.b.index for br in net.branches}
        self.free = [nd.index for nd in net.nodes if not nd.fixed and nd.index in touched]
        self.free_pos = {j: i for i, j in enumerate(self.free)}
        self.nf = len(self.free)
        self.nb = len(net.branches)
        self.n = self.nf + self.nb
        # Constant part of the Jacobian.
        jac = np.zeros((self.n, self.n))
        for br in net.branches:
            i = br.index
            ia = self.free_pos.get(br.a.index)
            ib = self.free_pos.get(br.b.index)
            if ia is not None:
                jac[i, ia] += 1.0
                jac[self.nb + ia, self.nf + i] -= M_SCALE
            if ib is not None:
                jac[i, ib] -= 1.0
                jac[self.nb + ib, self.nf + i] += M_SCALE
        self.jac0 = jac
        self.inj = np.zeros(self.nf)
        for inj in net.injections:
            pos = self.free_pos.get(inj.node.index)
            if pos is not None:
                self.inj[pos] += inj.m
        self.p_fixed = np.array(
            [nd.p if nd.fixed and nd.p is not None else np.nan for nd in net.nodes], dtype=float
        )
        self.a_idx = np.array([br.a.index for br in net.branches], dtype=int)
        self.b_idx = np.array([br.b.index for br in net.branches], dtype=int)

    def initial_guess(self) -> np.ndarray:
        """Zero flows; free pressures at the mean fixed pressure of their sub-network."""
        x = np.zeros(self.n)
        groups = self.net.subnetworks()
        for g in groups:
            fixed = [nd.p for nd in g if nd.fixed and nd.p is not None]
            p0 = float(np.mean(fixed)) if fixed else 101325.0
            for nd in g:
                pos = self.free_pos.get(nd.index)
                if pos is not None:
                    x[pos] = p0 / P_SCALE
        return x

    def unpack(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Absolute pressures per node (Pa) and flows per branch (kg/s) from ``x``."""
        p = self.p_fixed.copy()
        for pos, j in enumerate(self.free):
            p[j] = x[pos] * P_SCALE
        m = x[self.nf :] * M_SCALE
        return p, m

    def residual(
        self, x: np.ndarray, want_jac: bool = True
    ) -> tuple[np.ndarray, np.ndarray | None]:
        """Scaled residuals (branch laws in bar, balances in kg/s) and Jacobian."""
        p, m = self.unpack(x)
        r = np.empty(self.n)
        jac = self.jac0.copy() if want_jac else None
        for br in self.net.branches:
            i = br.index
            dp, ddp = br.law.dp(float(m[i]))
            r[i] = (p[br.a.index] - p[br.b.index] - dp) / P_SCALE
            if jac is not None:
                jac[i, self.nf + i] = -ddp * M_SCALE / P_SCALE
        bal = self.inj.copy()
        for br in self.net.branches:
            pa = self.free_pos.get(br.a.index)
            pb = self.free_pos.get(br.b.index)
            mi = m[br.index]
            if pa is not None:
                bal[pa] -= mi
            if pb is not None:
                bal[pb] += mi
        r[self.nb :] = bal / M_SCALE
        if not np.all(np.isfinite(r)):
            r = np.where(np.isfinite(r), r, 1e30)
        return r, jac

    def newton(
        self, x: np.ndarray, tol: float, max_iter: int
    ) -> tuple[np.ndarray, int, float, bool]:
        """Newton on the branch-flow system with a backtracking line search."""
        if self.n == 0:
            return x, 0, 0.0, True
        r, jac = self.residual(x)
        norm = float(np.linalg.norm(r))
        res = float(np.max(np.abs(r)))
        for it in range(1, max_iter + 1):
            if res < tol:
                return x, it - 1, res, True
            assert jac is not None
            try:
                dx = np.linalg.solve(jac, -r)
            except np.linalg.LinAlgError:
                dx = np.linalg.lstsq(jac, -r, rcond=None)[0]
            if not np.all(np.isfinite(dx)):
                return x, it, res, False
            alpha = 1.0
            accepted = False
            while alpha > 1e-12:
                xn = x + alpha * dx
                rn, jn = self.residual(xn)
                nn = float(np.linalg.norm(rn))
                if nn < (1.0 - 1e-4 * alpha) * norm or nn == 0.0:
                    accepted = True
                    break
                alpha *= 0.5
            if not accepted:
                return x, it, res, False
            x, r, jac, norm = xn, rn, jn, nn
            res = float(np.max(np.abs(r)))
        return x, max_iter, res, res < tol

    # -- node-pressure Newton on the convex potential -------------------------------------
    def _node_state(
        self, P: np.ndarray, m_guess: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Flows (inverse laws), conductances dm/dP (kg/s per bar) and nodal balances."""
        p = self.p_fixed.copy()
        for pos, j in enumerate(self.free):
            p[j] = P[pos] * P_SCALE
        m = np.empty(self.nb)
        g = np.empty(self.nb)
        for br in self.net.branches:
            i = br.index
            mi, d = _invert(br.law, float(p[br.a.index] - p[br.b.index]), float(m_guess[i]))
            m[i] = mi
            g[i] = P_SCALE / d
        bal = self.inj.copy()
        for br in self.net.branches:
            pa = self.free_pos.get(br.a.index)
            pb = self.free_pos.get(br.b.index)
            if pa is not None:
                bal[pa] -= m[br.index]
            if pb is not None:
                bal[pb] += m[br.index]
        return m, g, bal

    def node_newton(
        self, x0: np.ndarray, tol: float, max_iter: int = 200
    ) -> tuple[np.ndarray, int, float, bool]:
        """Newton on nodal pressures with exact branch flows and a convex line search.

        With ``m = g(dp)`` the inverse of each monotone law, the mass balance ``F(P)`` is minus
        the gradient of the convex potential ``sum(integral g) - inj . P``, and its Jacobian
        is ``-B^T diag(g') B``. The Newton direction is a descent direction of the potential
        and the line search keeps the directional derivative non-positive, so the iteration
        cannot stall on the kinks and steep regions that trip the branch-flow Newton.
        """
        if self.nf == 0:
            m = np.zeros(self.nb)
            m, _, _ = self._node_state(np.zeros(0), m)
            x = np.concatenate([np.zeros(0), m / M_SCALE])
            r, _ = self.residual(x, want_jac=False)
            res = float(np.max(np.abs(r))) if r.size else 0.0
            return x, 1, res, res < tol
        inc = np.zeros((self.nb, self.nf))
        for br in self.net.branches:
            pa = self.free_pos.get(br.a.index)
            pb = self.free_pos.get(br.b.index)
            if pa is not None:
                inc[br.index, pa] += 1.0
            if pb is not None:
                inc[br.index, pb] -= 1.0
        P = x0[: self.nf].copy()
        m, g, F = self._node_state(P, np.zeros(self.nb))
        res = math.inf
        history: list[float] = []
        for it in range(1, max_iter + 1):
            x = np.concatenate([P, m / M_SCALE])
            r, _ = self.residual(x, want_jac=False)
            res = float(np.max(np.abs(r)))
            history.append(res)
            if res < tol:
                return x, it - 1, res, True
            # Near the solution, hand over to the branch-flow Newton, which resolves flows
            # below the pressure round-off of very stiff branches (near-ideal joints).
            stalled = len(history) > 8 and res > 0.5 * history[-8]
            if res < 1e-6 or stalled:
                xp, itp, resp, okp = self.newton(x, tol, 50)
                if okp:
                    return xp, it + itp, resp, True
                if stalled:
                    return x, it + itp, res, False
            H = inc.T @ (g[:, None] * inc)
            try:
                d = np.linalg.solve(H, F)
            except np.linalg.LinAlgError:
                d = np.linalg.lstsq(H, F, rcond=None)[0]
            psi0 = float(F @ d)
            if not np.all(np.isfinite(d)) or psi0 <= 0.0:
                return x, it, res, False
            m1, g1, F1 = self._node_state(P + d, m)
            psi1 = float(F1 @ d)
            if psi1 >= 0.0:
                P, m, g, F = P + d, m1, g1, F1
                continue
            lo, hi, psi_lo, psi_hi = 0.0, 1.0, psi0, psi1
            best = None
            for _ in range(60):
                # Illinois-style regula falsi on the decreasing function psi(alpha)
                a = hi - psi_hi * (hi - lo) / (psi_hi - psi_lo)
                if not lo < a < hi:
                    a = 0.5 * (lo + hi)
                ma, ga, Fa = self._node_state(P + a * d, m)
                psi = float(Fa @ d)
                if 0.0 <= psi <= 0.5 * psi0:
                    best = (a, ma, ga, Fa)
                    break
                if psi < 0.0:
                    hi, psi_hi = a, psi
                    psi_lo *= 0.5
                else:
                    lo, psi_lo = a, psi
                    psi_hi *= 0.5
                    best = (a, ma, ga, Fa)
                if hi - lo < 1e-14:
                    break
            if best is None:
                return x, it, res, False
            a, m, g, F = best
            P = P + a * d
        x = np.concatenate([P, m / M_SCALE])
        r, _ = self.residual(x, want_jac=False)
        res = float(np.max(np.abs(r)))
        return x, max_iter, res, res < tol

    def fallback(
        self, x_best: np.ndarray, x_init: np.ndarray, tol: float
    ) -> tuple[np.ndarray, int, float, bool]:
        """scipy ``root(method='hybr')`` from the best point, then Newton polish."""
        from scipy.optimize import root

        def fun(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            r, j = self.residual(x)
            assert j is not None
            return r, j

        best_x, best_res, evals = x_best, math.inf, 0
        for start in (x_best, x_init):
            try:
                sol = root(fun, start, jac=True, method="hybr", options={"xtol": 1e-14})
            except Exception:  # scipy may raise on pathological input
                continue
            evals += int(getattr(sol, "nfev", 0))
            x = np.asarray(sol.x, dtype=float)
            # Polish with Newton from the fallback point.
            x, it, res, ok = self.newton(x, tol, 50)
            evals += it
            if ok:
                return x, evals, res, True
            if res < best_res:
                best_x, best_res = x, res
        return best_x, evals, best_res, False

    def worst_equation(self, x: np.ndarray) -> str:
        """Describe the equation with the largest residual."""
        r, _ = self.residual(x, want_jac=False)
        k = int(np.argmax(np.abs(r)))
        if k < self.nb:
            br = self.net.branches[k]
            return f"pressure-flow law of branch {br.label!r} (residual {r[k]:.3g} bar)"
        node = self.net.nodes[self.free[k - self.nb]]
        return f"mass balance at node {node.label!r} (residual {r[k]:.3g} kg/s)"


def _invert(law: Law, target: float, m0: float) -> tuple[float, float]:
    """Solve ``law.dp(m) = target`` for ``m`` (safeguarded Newton with bracketing).

    Returns ``(m, ddp_dm)`` at the solution.
    """
    lo, hi = -math.inf, math.inf
    m = m0 if math.isfinite(m0) else 0.0
    f, d = law.dp(m)
    for _ in range(200):
        r = f - target
        if abs(r) <= 1e-7 * max(1.0, 1e-9 * abs(target)):
            return m, d
        if r > 0:
            hi = m
        else:
            lo = m
        if math.isfinite(lo) and math.isfinite(hi) and hi - lo <= 1e-15 * max(1.0, abs(m)):
            return m, d
        step = m - r / d if d > 0 else math.nan
        if not (lo < step < hi):
            if math.isfinite(lo) and math.isfinite(hi):
                step = 0.5 * (lo + hi)
            elif math.isfinite(lo):
                step = lo + 2.0 * (abs(m - lo) + 1e-6)
            else:
                step = hi - 2.0 * (abs(hi - m) + 1e-6)
        m = step
        f, d = law.dp(m)
    return m, d
