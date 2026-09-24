"""The outcome rule of PREREGISTRATION.md section 9 (CLOSE (harm), CONTINUE, PIVOT-skill,
PIVOT-small, CLOSE), evaluated in that order, for ``analysis/analyze.py`` (freeze-1).

Inputs (:class:`GateData`): for every (model, arm), the per-task scores over the gate tasks
(a task's score is its mean pass over its repeats; NaN when the task has no valid session
in that arm), the confident-wrong rate CW (the mean over F3 tasks of the share of the
task's sessions whose verdict counts as confident wrong) and the cost per pass (the total
cost of the arm's gate sessions divided by its number of passing sessions; infinite
without a pass). P(m, arm) is the mean of the task scores.

**Unknown costs.** The CLI reports a session's cost only in its result message, so a
session the harness stopped at the timeout, or one that was killed, has none (and no
final reply, so it never passes). Such a session is counted at its (model, arm) cell's
cost per wall-clock second (the reported costs over those sessions' wall-clock seconds)
times its own wall-clock time, or at the cell's largest reported cost when its wall-clock
time is unknown (:func:`arm_cost`); the number of estimated costs is reported with the
cost per pass. An unknown cost is never a veto: only a cell in which no session reported
a cost has no cost per pass, and condition 7 then fails saying so.

Contrasts are paired by task: Δ(m) = mean over tasks of [score(m, a) - score(m, b)]
(tasks with a NaN on either side are left out of that contrast). The lower bound is the
2.5th percentile (numpy's linear interpolation) of the mean per-task difference over
10,000 bootstrap resamples of the tasks, stratified by family (each family's tasks are
resampled within the family, keeping its size), with the fixed seed :data:`BOOT_SEED`.
The same resamples serve every contrast on the same task set.

Thresholds are in points of pass rate (fractions here: +15 points is 0.15). Every
comparison has a 1e-9 guard against floating-point rounding: "at least" is ``x >= t -
1e-9``, "at most" is ``x <= t + 1e-9`` and "exceeds" is ``x > t + 1e-9``.

Two readings are fixed here because section 9 leaves them open:

- "within 10 points of Sonnet 5 code-hint" (PIVOT-small) is read as P(Haiku, mcp-hybrid)
  >= P(Sonnet 5, code-hint) - 10 points (being higher is never a reason to fail);
- "every arm without worldparts, on both models" (condition 5 for Opus 5.5) is code+,
  code-hint and code-skill on Sonnet 5 and Opus 5.5.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

SONNET, OPUS, HAIKU = "Sonnet 5", "Opus 5.5", "Haiku 4.5"
FRONTIER = (SONNET, OPUS)
CODE_PLUS, CODE_HINT, CODE_SKILL = "code+", "code-hint", "code-skill"
LIB_DIRECTED, LIB, MCP_HYBRID = "lib-directed", "lib", "mcp-hybrid"
NO_WORLDPARTS_ARMS = (CODE_PLUS, CODE_HINT, CODE_SKILL)
GENERATORS = ("G-ind", "G-epa")

BOOT_RESAMPLES = 10_000
BOOT_SEED = 20260924
EPS = 1e-9

CLOSE_HARM = "CLOSE (harm)"
CONTINUE = "CONTINUE"
PIVOT_SKILL = "PIVOT-skill"
PIVOT_SMALL = "PIVOT-small"
CLOSE = "CLOSE"


def _ge(x: float, t: float) -> bool:
    return x >= t - EPS


def _le(x: float, t: float) -> bool:
    return x <= t + EPS


def _gt(x: float, t: float) -> bool:
    return x > t + EPS


# ----------------------------------------------------------------------------------------
# data and bootstrap
# ----------------------------------------------------------------------------------------
@dataclass
class GateData:
    """Everything the rule reads. Arrays in ``score`` are aligned with ``tasks``."""

    tasks: tuple[str, ...]
    family: tuple[str, ...]
    generator: tuple[str, ...]
    score: dict[tuple[str, str], np.ndarray]
    cw: dict[tuple[str, str], float | None] = field(default_factory=dict)
    cost_per_pass: dict[tuple[str, str], float | None] = field(default_factory=dict)
    #: Per (model, arm): how many session costs were estimated (see "Unknown costs").
    cost_estimated: dict[tuple[str, str], int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(self.tasks)
        if len(self.family) != n or len(self.generator) != n:
            raise ValueError("family and generator must have one entry per task")
        for key, v in self.score.items():
            if np.shape(v) != (n,):
                raise ValueError(f"score of {key} must have one value per task")

    def P(self, model: str, arm: str) -> float:
        return float(np.nanmean(self.score[(model, arm)]))

    def has(self, model: str, arm: str) -> bool:
        return (model, arm) in self.score


@lru_cache(maxsize=64)
def stratified_indices(
    family: tuple[str, ...], n_boot: int = BOOT_RESAMPLES, seed: int = BOOT_SEED
) -> np.ndarray:
    """Bootstrap resamples of task positions, ``(n_boot, n)``: within each family (in
    sorted family order) its positions are drawn with replacement, keeping its size."""
    fam = np.asarray(family)
    rng = np.random.default_rng(seed)
    idx = np.empty((n_boot, len(fam)), dtype=np.int64)
    for f in sorted(set(family)):
        pos = np.flatnonzero(fam == f)
        idx[:, pos] = pos[rng.integers(0, len(pos), size=(n_boot, len(pos)))]
    return idx


def bootstrap_lower_bound(
    diff: np.ndarray,
    family: Iterable[str],
    n_boot: int = BOOT_RESAMPLES,
    seed: int = BOOT_SEED,
    q: float = 2.5,
) -> float:
    """The ``q``-th percentile of the stratified-bootstrap mean of ``diff`` (NaN left out)."""
    d = np.asarray(diff, dtype=float)
    fam = np.asarray(list(family))
    ok = ~np.isnan(d)
    if not ok.any():
        return math.nan
    idx = stratified_indices(tuple(fam[ok].tolist()), n_boot, seed)
    return float(np.percentile(d[ok][idx].mean(axis=1), q))


def paired_mean(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    d = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    if mask is not None:
        d = d[mask]
    d = d[~np.isnan(d)]
    return float(d.mean()) if d.size else math.nan


# ----------------------------------------------------------------------------------------
# the decision
# ----------------------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Decision:
    outcome: str
    model: str | None
    harm: dict[str, float]
    continue_checks: dict[str, list[Check]]
    pivot_skill: list[Check]
    pivot_small: list[Check]
    numbers: dict[str, Any]

    def lines(self) -> list[str]:
        out = [f"Outcome: {self.outcome}" + (f" (m = {self.model})" if self.model else "")]
        out.append(
            "CLOSE (harm) check: "
            + ", ".join(f"Δ_hint({m}) = {100 * v:+.1f} points" for m, v in self.harm.items())
        )
        for m, checks in self.continue_checks.items():
            out.append(f"CONTINUE with m = {m}:")
            out += [f"  [{'x' if c.ok else ' '}] {c.name}: {c.detail}" for c in checks]
        if self.pivot_skill:
            out.append("PIVOT-skill (Sonnet 5):")
            out += [f"  [{'x' if c.ok else ' '}] {c.name}: {c.detail}" for c in self.pivot_skill]
        if self.pivot_small:
            out.append("PIVOT-small (Haiku 4.5):")
            out += [f"  [{'x' if c.ok else ' '}] {c.name}: {c.detail}" for c in self.pivot_small]
        return out


def _pts(x: float | None) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{100 * x:+.1f}"


def _cpp(data: GateData, key: tuple[str, str]) -> str:
    """A cost per pass as printed, with the number of estimated session costs."""
    v = data.cost_per_pass.get(key)
    n = data.cost_estimated.get(key, 0)
    if v is None:
        return f"n/a ({key[1]}: no session reported a cost)"
    return f"${v:.3f}" + (f" ({n} session cost(s) estimated)" if n else "")


def _require(data: GateData, pairs: Iterable[tuple[str, str]]) -> None:
    missing = [p for p in pairs if not data.has(*p)]
    if missing:
        raise ValueError("missing (model, arm) scores: " + ", ".join(map(str, missing)))


def decide(
    data: GateData,
    small_model_named: bool = False,
    n_boot: int = BOOT_RESAMPLES,
    seed: int = BOOT_SEED,
) -> Decision:
    """Evaluate section 9 on ``data``.

    ``small_model_named`` says whether the owner named a small-model deployment before
    freeze-1 (PIVOT-small is only available then).
    """
    _require(
        data,
        [(m, a) for m in FRONTIER for a in (CODE_PLUS, CODE_HINT, CODE_SKILL, LIB_DIRECTED)],
    )
    fam = np.asarray(data.family)
    gen = np.asarray(data.generator)
    num: dict[str, Any] = {"P": {}, "delta_hint": {}, "lb_hint": {}, "delta_skill": {}}
    d_hint: dict[str, np.ndarray] = {}
    for m in FRONTIER:
        for a in (CODE_PLUS, CODE_HINT, CODE_SKILL, LIB_DIRECTED):
            num["P"][f"{m}/{a}"] = data.P(m, a)
        d_hint[m] = data.score[(m, LIB_DIRECTED)] - data.score[(m, CODE_HINT)]
        num["delta_hint"][m] = paired_mean(
            data.score[(m, LIB_DIRECTED)], data.score[(m, CODE_HINT)]
        )
        num["delta_skill"][m] = paired_mean(
            data.score[(m, LIB_DIRECTED)], data.score[(m, CODE_SKILL)]
        )
    harm = {m: num["delta_hint"][m] for m in FRONTIER}

    # Condition 6 does not depend on m: no family harmed on both frontier models.
    fam_delta = {
        f: {m: paired_mean(data.score[(m, LIB_DIRECTED)], data.score[(m, CODE_HINT)], fam == f)
            for m in FRONTIER}
        for f in sorted(set(data.family))
    }  # fmt: skip
    harmed = [f for f, v in fam_delta.items() if all(_le(v[m], -0.10) for m in FRONTIER)]
    num["family_delta_hint"] = fam_delta

    no_wp = [(mm, a) for mm in FRONTIER for a in NO_WORLDPARTS_ARMS]
    checks: dict[str, list[Check]] = {}
    for m in FRONTIER:
        other = OPUS if m == SONNET else SONNET
        dh, dk = num["delta_hint"][m], num["delta_skill"][m]
        lb = bootstrap_lower_bound(d_hint[m], data.family, n_boot, seed)
        num["lb_hint"][m] = lb
        gen_d = {
            g: paired_mean(data.score[(m, LIB_DIRECTED)], data.score[(m, CODE_HINT)], gen == g)
            for g in GENERATORS
        }
        cw_ml, cw_mh = data.cw.get((m, LIB_DIRECTED)), data.cw.get((m, CODE_HINT))
        cw_ol, cw_oh = data.cw.get((other, LIB_DIRECTED)), data.cw.get((other, CODE_HINT))
        cw_other = None if cw_ol is None or cw_oh is None else cw_ol - cw_oh
        c4 = (
            cw_ml is not None
            and cw_mh is not None
            and cw_other is not None
            and _le(cw_ml, cw_mh + 0.05)
            and _le(cw_other, 0.10)
            and _ge(num["delta_hint"][other], -0.05)
        )
        c4_detail = (
            f"CW({m}) lib-directed {_pts(cw_ml)} vs code-hint {_pts(cw_mh)} (+5 allowed); "
            f"CW({other}) difference {_pts(cw_other)} (<= +10); "
            f"Δ_hint({other}) {_pts(num['delta_hint'][other])} (>= -5)"
        )
        if m == SONNET:
            c5 = _ge(data.P(SONNET, LIB_DIRECTED), data.P(OPUS, CODE_HINT))
            c5_detail = (
                f"P(Sonnet 5, lib-directed) {100 * data.P(SONNET, LIB_DIRECTED):.1f} % "
                f">= P(Opus 5.5, code-hint) {100 * data.P(OPUS, CODE_HINT):.1f} %"
            )
        else:
            best = max(no_wp, key=lambda p: data.P(*p))
            c5 = _gt(data.P(OPUS, LIB_DIRECTED), data.P(*best))
            c5_detail = (
                f"P(Opus 5.5, lib-directed) {100 * data.P(OPUS, LIB_DIRECTED):.1f} % > best "
                f"arm without worldparts ({best[0]} {best[1]}) {100 * data.P(*best):.1f} %"
            )
        cpp_l = data.cost_per_pass.get((m, LIB_DIRECTED))
        cpp_h = data.cost_per_pass.get((m, CODE_HINT))
        c7 = cpp_l is not None and cpp_h is not None and _le(cpp_l, 3 * cpp_h)
        checks[m] = [
            Check(
                "1 Δ_hint >= +15 points, lower bound >= +5",
                _ge(dh, 0.15) and _ge(lb, 0.05),
                f"Δ_hint {_pts(dh)}, 95 % lower bound {_pts(lb)}",
            ),
            Check("2 Δ_skill >= +10 points", _ge(dk, 0.10), f"Δ_skill {_pts(dk)}"),
            Check(
                "3 Δ_hint >= 0 on G-ind and on G-epa",
                all(not math.isnan(v) and _ge(v, 0.0) for v in gen_d.values()),
                ", ".join(f"{g} {_pts(v)}" for g, v in gen_d.items()),
            ),
            Check("4 CW and the other model", bool(c4), c4_detail),
            Check("5 against the other arms", c5, c5_detail),
            Check(
                "6 no family harmed on both models",
                not harmed,
                "harmed: " + (", ".join(harmed) if harmed else "none"),
            ),
            Check(
                "7 cost/pass(lib-directed) <= 3 x cost/pass(code-hint)",
                bool(c7),
                f"{_cpp(data, (m, LIB_DIRECTED))} vs {_cpp(data, (m, CODE_HINT))}",
            ),
        ]
    num["cw"] = {f"{m}/{a}": v for (m, a), v in data.cw.items()}
    num["cost_per_pass"] = {f"{m}/{a}": v for (m, a), v in data.cost_per_pass.items()}
    num["cost_estimated"] = {f"{m}/{a}": v for (m, a), v in data.cost_estimated.items()}

    # PIVOT-skill: Sonnet 5, code-skill against code+.
    ds = data.score[(SONNET, CODE_SKILL)] - data.score[(SONNET, CODE_PLUS)]
    d_ps = paired_mean(data.score[(SONNET, CODE_SKILL)], data.score[(SONNET, CODE_PLUS)])
    lb_ps = bootstrap_lower_bound(ds, data.family, n_boot, seed)
    num["pivot_skill"] = {"delta": d_ps, "lb": lb_ps}
    pivot_skill = [
        Check(
            "P(code-skill) - P(code+) >= +15 points, lower bound >= +5",
            _ge(d_ps, 0.15) and _ge(lb_ps, 0.05),
            f"{_pts(d_ps)}, 95 % lower bound {_pts(lb_ps)}",
        )
    ]

    pivot_small: list[Check] = []
    if small_model_named:
        _require(data, [(HAIKU, MCP_HYBRID), (HAIKU, CODE_HINT)])
        dh_small = data.score[(HAIKU, MCP_HYBRID)] - data.score[(HAIKU, CODE_HINT)]
        d_small = paired_mean(data.score[(HAIKU, MCP_HYBRID)], data.score[(HAIKU, CODE_HINT)])
        lb_small = bootstrap_lower_bound(dh_small, data.family, n_boot, seed)
        p_mcp, p_sonnet = data.P(HAIKU, MCP_HYBRID), data.P(SONNET, CODE_HINT)
        cpp_mcp = data.cost_per_pass.get((HAIKU, MCP_HYBRID))
        cpp_son = data.cost_per_pass.get((SONNET, CODE_HINT))
        num["pivot_small"] = {"delta": d_small, "lb": lb_small, "P": p_mcp}
        pivot_small = [
            Check(
                "mcp-hybrid - code-hint (Haiku) >= +20 points, lower bound >= +5",
                _ge(d_small, 0.20) and _ge(lb_small, 0.05),
                f"{_pts(d_small)}, 95 % lower bound {_pts(lb_small)}",
            ),
            Check("Haiku mcp-hybrid passes >= 70 %", _ge(p_mcp, 0.70), f"{100 * p_mcp:.1f} %"),
            Check(
                "within 10 points of Sonnet 5 code-hint, at a lower cost per pass",
                _ge(p_mcp, p_sonnet - 0.10)
                and cpp_mcp is not None
                and cpp_son is not None
                and cpp_mcp < cpp_son,
                f"{100 * p_mcp:.1f} % vs {100 * p_sonnet:.1f} %; cost/pass "
                f"{_cpp(data, (HAIKU, MCP_HYBRID))} vs {_cpp(data, (SONNET, CODE_HINT))}",
            ),
        ]

    if any(_le(v, -0.10) for v in harm.values()):
        outcome, model = CLOSE_HARM, None
    elif any(all(c.ok for c in checks[m]) for m in FRONTIER):
        outcome = CONTINUE
        model = next(m for m in FRONTIER if all(c.ok for c in checks[m]))
    elif all(c.ok for c in pivot_skill):
        outcome, model = PIVOT_SKILL, None
    elif small_model_named and all(c.ok for c in pivot_small):
        outcome, model = PIVOT_SMALL, None
    else:
        outcome, model = CLOSE, None
    return Decision(outcome, model, harm, checks, pivot_skill, pivot_small, num)


# ----------------------------------------------------------------------------------------
# from session records
# ----------------------------------------------------------------------------------------
def _known(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def arm_cost(sessions: Iterable[Mapping[str, Any]]) -> tuple[float | None, int]:
    """(total cost of one (model, arm) cell's sessions, how many of the costs are estimates).

    Sessions carry ``cost_usd`` (None when the CLI reported none) and ``wall_s``. A session
    without a cost is counted at the cell's reported cost per wall-clock second times its
    wall-clock time, or at the cell's largest reported cost when its wall-clock time is
    unknown (see "Unknown costs" in the module docstring). The total is None only when no
    session of the cell reported a cost. The report uses the same rule.
    """
    rows = [(_known(s.get("cost_usd")), _known(s.get("wall_s"))) for s in sessions]
    known = [c for c, _ in rows if c is not None]
    unknown = [w for c, w in rows if c is None]
    if not unknown:
        return float(sum(known)), 0
    if not known:
        return None, len(unknown)
    timed = [(c, w) for c, w in rows if c is not None and w is not None and w > 0]
    rate = sum(c for c, _ in timed) / sum(w for _, w in timed) if timed else None
    total = float(sum(known))
    for w in unknown:
        total += rate * w if rate is not None and w is not None else max(known)
    return total, len(unknown)


def gate_data_from_sessions(
    sessions: Iterable[Mapping[str, Any]], tasks: Mapping[str, tuple[str, str]]
) -> GateData:
    """Aggregate session records into :class:`GateData`.

    ``sessions``: mappings with ``model`` (a label such as "Sonnet 5"), ``arm``, ``task``,
    ``passed``, ``cw_category`` (F3), ``cost_usd`` and ``wall_s``; contaminated sessions
    must already be left out. ``tasks``: task id -> (family, generator), the gate tasks in
    order.
    """
    ids = tuple(tasks)
    pos = {t: i for i, t in enumerate(ids)}
    passes: dict[tuple[str, str], list[list[bool]]] = {}
    cws: dict[tuple[str, str], dict[str, list[bool]]] = {}
    cells: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for s in sessions:
        key = (str(s["model"]), str(s["arm"]))
        if s["task"] not in pos:
            raise ValueError(f"session on unknown task {s['task']!r}")
        passes.setdefault(key, [[] for _ in ids])[pos[s["task"]]].append(bool(s["passed"]))
        if tasks[s["task"]][0] == "F3":
            cws.setdefault(key, {}).setdefault(s["task"], []).append(
                s.get("cw_category") == "confident_wrong"
            )
        cells.setdefault(key, []).append(s)
    score = {
        k: np.array([sum(v) / len(v) if v else np.nan for v in per_task])
        for k, per_task in passes.items()
    }
    cw = {k: float(np.mean([np.mean(v) for v in d.values()])) for k, d in cws.items()}
    cpp: dict[tuple[str, str], float | None] = {}
    estimated: dict[tuple[str, str], int] = {}
    for k, rows in cells.items():
        n_pass = sum(sum(v) for v in passes[k])
        total, estimated[k] = arm_cost(rows)
        cpp[k] = None if total is None else (total / n_pass if n_pass else math.inf)
    return GateData(
        tasks=ids,
        family=tuple(tasks[t][0] for t in ids),
        generator=tuple(tasks[t][1] for t in ids),
        score=score,
        cw=cw,
        cost_per_pass=cpp,
        cost_estimated=estimated,
    )
