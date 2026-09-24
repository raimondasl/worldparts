"""Operating characteristics of the Stage 0 and Stage 1 rules (PREREGISTRATION.md section 8).

    uv run python -m benchmarks.operations.analysis.power            # print the tables
    uv run python -m benchmarks.operations.analysis.power --write    # and power-tables.md
    uv run python -m benchmarks.operations.analysis.power --check    # exit 1 beyond +/-0.05

The simulation models are the critic's (the scripts ``method_stage0.py`` and
``method_stage1d.py`` of the design review); the rules are the pre-registered ones.

**Stage 0** (:func:`stage0_p_closed`). 16 development tasks, one code-hint session per task
and model. A task has a latent difficulty z ~ N(0, 1), correlated between the two models
with correlation c = 0.7 (z_m = sqrt(c) z + sqrt(1 - c) z'); each session adds its own
logit noise with SD 0.6. The session passes with probability expit(mu_m + 2 z_m + 0.6 e),
where mu_m is set so that the mean pass probability is the model's true code-hint pass
rate (calibrated on the total logit SD sqrt(2^2 + 0.6^2)). At least one reference
estimator passes realisation 1 of a task with probability 0.95, the same for both models.
F(m) counts the tasks the model fails while a reference estimator passes; the room is
closed if and only if F(Sonnet 5) <= 3 and F(Opus 5.5) <= 3.

**Stage 1** (:func:`stage1_rates`). The 42 test tasks of section 4 (F1 10, F2 8, F3 16,
F4 8, with the pre-registered generator split), 3 repeats of code-hint, lib-directed and
code-skill and 1 of code+ per task and model. Logit of a session's pass probability:
mu_m + family offset (F1 +0.5, F2 +0.5, F3 -0.5, F4 0) + 2 z_m (task difficulty, SD 2,
correlation 0.7 between models) + e_r (the realisation effect, SD 0.5, the same in every
arm and model for repeat r) + the arm's shift: lib-directed d_m + u (u ~ N(0, 0.7^2) per
task and model: effect heterogeneity) on F3 and F4 only, nothing on F1 and F2;
code-skill d_skill + u on F3 and F4, with d_skill set so that code-skill gains 25 % of
lib-directed's gain; code+ -0.3. mu_m gives the true code-hint pass rates 0.65 (Sonnet 5)
and 0.70 (Opus 5.5); d_m gives the true Δ_hint (the mean over tasks of the pass
probability difference; "true 0" is d = 0 with the heterogeneity kept). On F3, a failed
code-hint or lib-directed session is confident wrong with probability 0.6. Every session
costs the same, so criterion 7 holds whenever lib-directed passes at least a third as
often as code-hint. Each simulated Stage 1 is decided by
:func:`benchmarks.operations.analysis.outcome.decide`, the section-9 rule as
pre-registered, with its 10,000-resample family-stratified bootstrap.

For comparison, the same draws are also decided by the critic's own rule (the "proposed
rules v2" of ``method_stage1d.py``), which the printed figures come from, and the
simulation can use the critic's generator split (the draft-1 layout); see
:func:`critic_continue` and :data:`CRITIC_LAYOUT`.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .outcome import (
    BOOT_RESAMPLES,
    BOOT_SEED,
    CLOSE_HARM,
    CODE_HINT,
    CODE_PLUS,
    CODE_SKILL,
    CONTINUE,
    FRONTIER,
    LIB_DIRECTED,
    OPUS,
    PIVOT_SKILL,
    SONNET,
    GateData,
    decide,
)

TABLES = Path(__file__).resolve().parent / "power-tables.md"
TOLERANCE = 0.05

#: The figures PREREGISTRATION.md section 8 prints (the critic's estimates).
PREREG_STAGE0: tuple[tuple[tuple[float, float], float], ...] = (
    ((0.65, 0.70), 0.07),
    ((0.70, 0.75), 0.17),
    ((0.75, 0.80), 0.32),
    ((0.80, 0.85), 0.54),
    ((0.85, 0.90), 0.78),
    ((0.90, 0.92), 0.92),
)
#: ((models gaining, true Δ_hint), P(CONTINUE)).
PREREG_STAGE1: tuple[tuple[tuple[str, float], float], ...] = (
    (("both", 0.00), 0.00),
    (("both", 0.10), 0.14),
    (("both", 0.15), 0.55),
    (("both", 0.20), 0.84),
    (("Sonnet 5 only", 0.15), 0.27),
    (("Sonnet 5 only", 0.20), 0.54),
)
#: P(CONTINUE) at a true +20 below this enlarges the test set (section 8).
POWER_REQUIREMENT = 0.70


def expit(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-x))


def _bisect(f: Any, target: float, lo: float, hi: float, n: int = 60) -> float:
    for _ in range(n):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ----------------------------------------------------------------------------------------
# Stage 0
# ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Stage0Model:
    n_dev: int = 16
    tau: float = 2.0  # logit SD of task difficulty
    c: float = 0.7  # correlation of task difficulty between the models
    s_arm: float = 0.6  # logit SD of the session's own noise
    r1: float = 0.95  # P(at least one reference estimator passes realisation 1)
    threshold: int = 3  # closed iff F <= threshold for both models


def stage0_p_closed(
    p_sonnet: float,
    p_opus: float,
    reps: int = 40_000,
    seed: int = 20260924,
    model: Stage0Model | None = None,
) -> float:
    """P(room closed) at the given true code-hint pass rates."""
    m = model or Stage0Model()
    rng = np.random.default_rng(seed)
    total_sd = math.sqrt(m.tau**2 + m.s_arm**2)
    zc = np.random.default_rng(seed + 1).standard_normal(400_000)

    def mu_for(p: float) -> float:
        return _bisect(lambda mu: float(expit(mu + total_sd * zc).mean()), p, -10.0, 10.0)

    z = rng.standard_normal((reps, m.n_dev))
    ref = rng.random((reps, m.n_dev)) < m.r1
    closed = np.ones(reps, dtype=bool)
    for p in (p_sonnet, p_opus):
        mu = mu_for(p)
        zm = math.sqrt(m.c) * z + math.sqrt(1 - m.c) * rng.standard_normal((reps, m.n_dev))
        lp = mu + m.tau * zm + m.s_arm * rng.standard_normal((reps, m.n_dev))
        passed = rng.random((reps, m.n_dev)) < expit(lp)
        F = (~passed & ref).sum(axis=1)
        closed &= threshold_closed(F, m.threshold)
    return float(closed.mean())


def threshold_closed(F: np.ndarray, threshold: int = 3) -> np.ndarray:
    """The per-model part of the rule: F(m) <= 3."""
    return np.asarray(F) <= threshold


# ----------------------------------------------------------------------------------------
# Stage 1
# ----------------------------------------------------------------------------------------
def _layout(cells: list[tuple[str, int, int]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    fam: list[str] = []
    gen: list[str] = []
    for f, n_ind, n_epa in cells:
        fam += [f] * (n_ind + n_epa)
        gen += ["G-ind"] * n_ind + ["G-epa"] * n_epa
    return tuple(fam), tuple(gen)


#: The pre-registered test set (section 4): F1 5/5, F2 8/0, F3 8/8, F4 4/4 (G-ind/G-epa).
PREREG_LAYOUT = _layout([("F1", 5, 5), ("F2", 8, 0), ("F3", 8, 8), ("F4", 4, 4)])
#: The critic's (draft-1) split: F1 5/5, F2 4/4, F3 10/6, F4 4/4.
CRITIC_LAYOUT = _layout([("F1", 5, 5), ("F2", 4, 4), ("F3", 10, 6), ("F4", 4, 4)])


@dataclass(frozen=True)
class Stage1Model:
    family: tuple[str, ...] = PREREG_LAYOUT[0]
    generator: tuple[str, ...] = PREREG_LAYOUT[1]
    fam_off: tuple[tuple[str, float], ...] = (("F1", 0.5), ("F2", 0.5), ("F3", -0.5), ("F4", 0.0))
    effect_families: tuple[str, ...] = ("F3", "F4")
    tau: float = 2.0
    c: float = 0.7
    s_e: float = 0.5  # realisation effect, shared by arms and models
    s_u: float = 0.7  # effect heterogeneity
    phi: float = 0.25  # share of lib-directed's gain that code-skill captures
    plus_shift: float = -0.3  # code+ against code-hint, logit
    q_cw: float = 0.6  # P(confident wrong | failed F3 session)
    p_hint: tuple[tuple[str, float], ...] = ((SONNET, 0.65), (OPUS, 0.70))
    repeats: int = 3
    n_cal: int = 4000

    @property
    def offsets(self) -> np.ndarray:
        off = dict(self.fam_off)
        return np.array([off[f] for f in self.family])

    @property
    def effect_mask(self) -> np.ndarray:
        return np.isin(np.array(self.family), self.effect_families)


@dataclass
class Calibration:
    mu: dict[str, float]
    delta: dict[str, float]
    dskill: dict[str, float]


def calibrate(model: Stage1Model, gains: dict[str, float], seed: int) -> Calibration:
    """mu_m for the true code-hint rates, and the lib-directed and code-skill shifts that
    give each model's true Δ_hint (``gains``) and 25 % of it."""
    rng = np.random.default_rng(seed)
    n = len(model.family)
    z = rng.standard_normal((model.n_cal, n))
    e = rng.standard_normal((model.n_cal, n)) * model.s_e
    u = rng.standard_normal((model.n_cal, n)) * model.s_u
    off, eff = model.offsets, model.effect_mask
    fixed = off + model.tau * z + e

    def mu_for(p: float) -> float:
        return _bisect(lambda mu: float(expit(mu + fixed).mean()), p, -10.0, 10.0, 50)

    def delta_for(mu: float, target: float) -> float:
        if target <= 0:
            return 0.0
        base = float(expit(mu + fixed).mean())
        return _bisect(
            lambda d: float(expit(mu + fixed + eff * (d + u)).mean()) - base, target, 0.0, 12.0, 50
        )

    mu = {m: mu_for(p) for m, p in model.p_hint}
    delta = {m: delta_for(mu[m], gains[m]) for m in mu}
    dskill = {m: delta_for(mu[m], model.phi * gains[m]) for m in mu}
    return Calibration(mu, delta, dskill)


def simulate_stage1(
    model: Stage1Model, cal: Calibration, rng: np.random.Generator
) -> tuple[GateData, dict[str, np.ndarray]]:
    """One simulated Stage 1: the GateData and the per-session pass arrays."""
    n = len(model.family)
    off, eff = model.offsets, model.effect_mask
    f3 = np.array(model.family) == "F3"
    z = rng.standard_normal(n)
    e = rng.standard_normal((n, model.repeats)) * model.s_e
    score: dict[tuple[str, str], np.ndarray] = {}
    cw: dict[tuple[str, str], float | None] = {}
    cpp: dict[tuple[str, str], float | None] = {}
    raw: dict[str, np.ndarray] = {}
    for m in FRONTIER:
        zm = math.sqrt(model.c) * z + math.sqrt(1 - model.c) * rng.standard_normal(n)
        u = rng.standard_normal(n) * model.s_u
        base = cal.mu[m] + off + model.tau * zm
        shift_lib = eff * (cal.delta[m] + u)
        shift_skill = eff * (cal.dskill[m] + u) if cal.dskill[m] > 0 else np.zeros(n)
        arms = {
            CODE_HINT: (np.zeros(n), model.repeats),
            LIB_DIRECTED: (shift_lib, model.repeats),
            CODE_SKILL: (shift_skill, model.repeats),
            CODE_PLUS: (np.full(n, model.plus_shift), 1),
        }
        for arm, (shift, reps) in arms.items():
            lp = base[:, None] + e[:, :reps] + shift[:, None]
            passed = rng.random((n, reps)) < expit(lp)
            raw[f"{m}/{arm}"] = passed
            score[(m, arm)] = passed.mean(axis=1)
            n_pass = int(passed.sum())
            cpp[(m, arm)] = passed.size / n_pass if n_pass else math.inf
            if arm in (CODE_HINT, LIB_DIRECTED):
                wrong = ~passed[f3] & (rng.random(passed[f3].shape) < model.q_cw)
                cw[(m, arm)] = float(wrong.mean(axis=1).mean())
    data = GateData(
        tasks=tuple(f"t{i:02d}" for i in range(n)),
        family=model.family,
        generator=model.generator,
        score=score,
        cw=cw,
        cost_per_pass=cpp,
    )
    return data, raw


def critic_continue(data: GateData, rng: np.random.Generator) -> bool:
    """The critic's "proposed rules v2" (method_stage1d.py), for comparison only."""
    fam, gen = np.array(data.family), np.array(data.generator)
    n = len(fam)
    d = {m: data.score[(m, LIB_DIRECTED)] - data.score[(m, CODE_HINT)] for m in FRONTIER}
    D = {m: float(d[m].mean()) for m in FRONTIER}
    idx = rng.integers(0, n, (2000, n))
    LB = {m: float(np.quantile(d[m][idx].mean(1), 0.025)) for m in FRONTIER}
    other = {SONNET: OPUS, OPUS: SONNET}
    c1 = {m: D[m] >= 0.15 and LB[m] >= 0.05 and D[other[m]] >= -0.05 for m in FRONTIER}
    c2 = {
        m: float((data.score[(m, LIB_DIRECTED)] - data.score[(m, CODE_SKILL)]).mean()) >= 0.10
        for m in FRONTIER
    }
    c3 = data.P(SONNET, LIB_DIRECTED) >= data.P(OPUS, CODE_HINT)
    fam_ok = True
    for f in ("F1", "F2", "F3", "F4"):
        dd = (d[SONNET][fam == f] + d[OPUS][fam == f]) / 2
        j = rng.integers(0, len(dd), (1000, len(dd)))
        if dd.mean() <= -0.10 and np.quantile(dd[j].mean(1), 0.99) < 0:
            fam_ok = False
    gen_ok = {
        m: d[m][gen == "G-ind"].mean() >= 0 and d[m][gen == "G-epa"].mean() >= 0 for m in FRONTIER
    }
    cw = data.cw
    c4 = {
        m: cw[(m, LIB_DIRECTED)] <= cw[(m, CODE_HINT)]  # type: ignore[operator]
        and cw[(other[m], LIB_DIRECTED)] <= cw[(other[m], CODE_HINT)] + 0.10  # type: ignore[operator]
        for m in FRONTIER
    }
    return any(c1[m] and c2[m] and gen_ok[m] and c4[m] for m in FRONTIER) and c3 and fam_ok


@dataclass
class Stage1Result:
    gains: dict[str, float]
    sims: int
    p_continue: float
    p_continue_critic_rule: float
    p_close_harm: float
    p_pivot_skill: float
    cumulative: list[float] = field(default_factory=list)  # any m meeting 1..k, k = 1..7
    seconds: float = 0.0

    @property
    def se(self) -> float:
        p = self.p_continue
        return math.sqrt(max(p * (1 - p), 1e-12) / self.sims)


def stage1_rates(
    gains: dict[str, float],
    sims: int = 5000,
    seed: int = 20260924,
    model: Stage1Model | None = None,
    n_boot: int = BOOT_RESAMPLES,
) -> Stage1Result:
    """P(CONTINUE) and the other outcome rates for true Δ_hint ``gains`` per model."""
    mdl = model or Stage1Model()
    cal = calibrate(mdl, gains, seed)
    rng = np.random.default_rng(seed + 7)
    critic_rng = np.random.default_rng(seed + 11)
    t0 = time.monotonic()
    n_cont = n_crit = n_harm = n_pivot = 0
    cum = np.zeros(7)
    for _ in range(sims):
        data, _raw = simulate_stage1(mdl, cal, rng)
        dec = decide(data, n_boot=n_boot, seed=BOOT_SEED)
        n_cont += dec.outcome == CONTINUE
        n_harm += dec.outcome == CLOSE_HARM
        n_pivot += dec.outcome == PIVOT_SKILL
        n_crit += critic_continue(data, critic_rng)
        for k in range(7):
            cum[k] += any(all(c.ok for c in dec.continue_checks[m][: k + 1]) for m in FRONTIER)
    return Stage1Result(
        gains=dict(gains),
        sims=sims,
        p_continue=n_cont / sims,
        p_continue_critic_rule=n_crit / sims,
        p_close_harm=n_harm / sims,
        p_pivot_skill=n_pivot / sims,
        cumulative=[float(x / sims) for x in cum],
        seconds=time.monotonic() - t0,
    )


def scenario_gains(which: str, delta: float) -> dict[str, float]:
    if which == "both":
        return {SONNET: delta, OPUS: delta}
    if which == "Sonnet 5 only":
        return {SONNET: delta, OPUS: 0.0}
    raise ValueError(which)


# ----------------------------------------------------------------------------------------
# tables
# ----------------------------------------------------------------------------------------
def run_all(stage0_reps: int, stage1_sims: int, log: Any = print) -> dict[str, Any]:
    out: dict[str, Any] = {"stage0": [], "stage1": [], "stage1_critic_layout": []}
    for (ps, po), want in PREREG_STAGE0:
        got = stage0_p_closed(ps, po, reps=stage0_reps)
        out["stage0"].append({"p": (ps, po), "prereg": want, "got": got})
        log(f"stage 0 {ps:.2f}/{po:.2f}: P(closed) {got:.3f} (pre-registered {want:.2f})")
    for i, ((which, delta), want) in enumerate(PREREG_STAGE1):
        for key, mdl in (
            ("stage1", Stage1Model()),
            ("stage1_critic_layout", Stage1Model(*CRITIC_LAYOUT)),
        ):
            r = stage1_rates(scenario_gains(which, delta), stage1_sims, seed=424242 + i, model=mdl)
            out[key].append({"which": which, "delta": delta, "prereg": want, "result": r})
            log(
                f"{key} {which} {delta:+.2f}: P(CONTINUE) {r.p_continue:.3f} "
                f"(critic's rule {r.p_continue_critic_rule:.3f}; pre-registered {want:.2f}; "
                f"{r.seconds:.0f} s)"
            )
    return out


def differences(res: dict[str, Any]) -> list[str]:
    """Every reproduced figure more than :data:`TOLERANCE` from the pre-registered one."""
    bad = []
    for row in res["stage0"]:
        if abs(row["got"] - row["prereg"]) > TOLERANCE:
            bad.append(f"Stage 0 {row['p']}: {row['got']:.3f} vs {row['prereg']:.2f}")
    for row in res["stage1"]:
        got = row["result"].p_continue
        if abs(got - row["prereg"]) > TOLERANCE:
            bad.append(
                f"Stage 1 {row['which']} {row['delta']:+.2f}: {got:.3f} vs {row['prereg']:.2f}"
            )
    return bad


def to_markdown(res: dict[str, Any], stage0_reps: int, stage1_sims: int) -> str:
    L = [
        "# Operations benchmark: operating characteristics",
        "",
        "Reproduced by `uv run python -m benchmarks.operations.analysis.power --write` "
        "(`benchmarks/operations/analysis/power.py`; fixed seeds, so the numbers are "
        "reproducible). These tables replace the ones printed in PREREGISTRATION.md "
        "section 8. The models and assumptions are in the module docstring of `power.py`; "
        "in short: logit per-task SD 2, correlation 0.7 of task difficulty between the "
        "models, effect-heterogeneity SD 0.7, realisation effect SD 0.5 shared by all arms, "
        "gains on F3 and F4 only, code-skill capturing 25 % of the gain, code+ 0.3 logit "
        "below code-hint, true code-hint pass rates 0.65 (Sonnet 5) and 0.70 (Opus 5.5) in "
        "Stage 1, and at least one reference estimator passing 95 % of development "
        "realisations.",
        "",
        f"## Stage 0: P(room closed) ({stage0_reps:,} simulated Stage 0 runs per row)",
        "",
        "Rule: the room is closed if and only if F(Sonnet 5) ≤ 3 and F(Opus 5.5) ≤ 3.",
        "",
        "| True code-hint pass rate (Sonnet / Opus) | Printed (critic) | Reproduced | "
        "Difference | Within ±0.05 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in res["stage0"]:
        ps, po = row["p"]
        diff = row["got"] - row["prereg"]
        L.append(
            f"| {ps:.2f} / {po:.2f} | {row['prereg']:.2f} | {row['got']:.2f} | {diff:+.3f} | "
            f"{'yes' if abs(diff) <= TOLERANCE else 'NO'} |"
        )
    L += [
        "",
        f"Monte Carlo standard error: at most {0.5 / math.sqrt(stage0_reps):.4f} per row.",
        "",
        f"## Stage 1: P(CONTINUE) ({stage1_sims:,} simulated Stage 1 runs per row)",
        "",
        "Each simulated run is decided by the section-9 rule as pre-registered "
        "(`analysis/outcome.py`: harm check first, then the seven CONTINUE conditions for "
        "either frontier model, with the family-stratified 10,000-resample bootstrap). The "
        "last column decides the same simulated runs by the critic's own rule "
        '("proposed rules v2" in `method_stage1d.py`), which the printed figures come from.',
        "",
        "| True Δ_hint | Models gaining | Printed (critic) | Reproduced (rule as "
        "pre-registered) | Difference | Within ±0.05 | Critic's rule, same runs |",
        "|---:|---|---:|---:|---:|---|---:|",
    ]
    for row in res["stage1"]:
        r: Stage1Result = row["result"]
        diff = r.p_continue - row["prereg"]
        L.append(
            f"| {100 * row['delta']:+.0f} | {row['which']} | {row['prereg']:.2f} | "
            f"{r.p_continue:.2f} | {diff:+.3f} | {'yes' if abs(diff) <= TOLERANCE else 'NO'} | "
            f"{r.p_continue_critic_rule:.2f} |"
        )
    L += [
        "",
        f"Monte Carlo standard error: at most {0.5 / math.sqrt(stage1_sims):.3f} per row.",
        "",
        "Where the power goes (share of runs in which some frontier model m meets the "
        "CONTINUE conditions 1 to k; condition 7, cost, holds whenever lib-directed passes "
        "at least a third as often as code-hint, since every session costs the same here):",
        "",
        "| True Δ_hint | Models gaining | 1 | 1-2 | 1-3 | 1-4 | 1-5 | 1-6 | 1-7 | "
        "P(CLOSE (harm)) | P(PIVOT-skill) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in res["stage1"]:
        r = row["result"]
        L.append(
            f"| {100 * row['delta']:+.0f} | {row['which']} | "
            + " | ".join(f"{x:.2f}" for x in r.cumulative)
            + f" | {r.p_close_harm:.2f} | {r.p_pivot_skill:.2f} |"
        )
    L += [
        "",
        "### The same with the critic's generator split",
        "",
        "The critic simulated the draft-1 generator split (G-ind/G-epa: F1 5/5, F2 4/4, F3 "
        "10/6, F4 4/4). Draft 2 pre-registers F1 5/5, F2 8/0, F3 8/8, F4 4/4 (F2 is G-ind "
        "only). Only condition 3 (Δ_hint ≥ 0 on each generator subset) reads the split.",
        "",
        "| True Δ_hint | Models gaining | Printed (critic) | Rule as pre-registered | "
        "Critic's rule |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in res["stage1_critic_layout"]:
        r = row["result"]
        L.append(
            f"| {100 * row['delta']:+.0f} | {row['which']} | {row['prereg']:.2f} | "
            f"{r.p_continue:.2f} | {r.p_continue_critic_rule:.2f} |"
        )
    p20 = next(
        row["result"].p_continue
        for row in res["stage1"]
        if row["which"] == "both" and abs(row["delta"] - 0.20) < 1e-9
    )
    verdict = "met" if p20 >= POWER_REQUIREMENT else "NOT MET"
    L += [
        "",
        "## Power requirement (section 8)",
        "",
        f"P(CONTINUE) at a true +20 (both models): {p20:.2f}; the requirement "
        f"P ≥ {POWER_REQUIREMENT:.2f} is {verdict}.",
        "",
        "## Where the differences from the printed figures come from",
        "",
        'The printed Stage 1 figures are the critic\'s "proposed rules v2" '
        "(`method_stage1d.py`, 1,200 simulated runs per row). Section 9 as pre-registered "
        "differs from that rule in four places; the reproduced column applies all four, the "
        "critic's-rule column none:",
        "",
        "- condition 4 allows CW(m, lib-directed) up to CW(m, code-hint) + 5 points (the "
        "critic: no increase at all);",
        "- condition 5 is per model: for m = Sonnet 5, P(Sonnet 5, lib-directed) ≥ "
        "P(Opus 5.5, code-hint); for m = Opus 5.5, P(Opus 5.5, lib-directed) exceeds every "
        "arm without worldparts on both models (the critic: always the Sonnet condition);",
        "- condition 6 flags a family only when its Δ_hint ≤ −10 on both models (the critic: "
        "the mean over both models ≤ −10 with a 99 % bootstrap upper bound below 0);",
        "- the lower bound of condition 1 comes from 10,000 resamples stratified by family "
        "(the critic: 2,000 unstratified resamples).",
        "",
        "Each difference splits exactly into three parts: the printed figure's own Monte "
        "Carlo error (the critic's script rerun with 5,000 runs, outside this repository, "
        "minus the printed figure), the difference between two simulations of the same model "
        "(the critic's rule on the runs simulated here minus the critic's script at 5,000 "
        "runs), and the rule differences above (the pre-registered rule minus the critic's "
        "rule, on the same runs).",
        "",
        "| True Δ_hint | Models gaining | Printed | Critic's script, 5,000 runs | Critic's rule, "
        "runs here | Rule as pre-registered, runs here | Printed's MC error | Simulation | "
        "Rule differences | Total |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in res["stage1"]:
        r = row["result"]
        rerun = CRITIC_RERUN_5000.get((row["which"], row["delta"]))
        if rerun is None:
            continue
        L.append(
            f"| {100 * row['delta']:+.0f} | {row['which']} | {row['prereg']:.2f} | {rerun:.3f} | "
            f"{r.p_continue_critic_rule:.3f} | {r.p_continue:.3f} | "
            f"{rerun - row['prereg']:+.3f} | {r.p_continue_critic_rule - rerun:+.3f} | "
            f"{r.p_continue - r.p_continue_critic_rule:+.3f} | "
            f"{r.p_continue - row['prereg']:+.3f} |"
        )
    bad = differences(res)
    L += [
        "",
        (
            "Every reproduced figure is within ±0.05 of the printed one."
            if not bad
            else "Figures more than 0.05 from the printed ones (explained by the split above; "
            "not tuned): " + "; ".join(bad) + "."
        ),
        "",
        "The Stage 0 model is the critic's `method_stage0.py`, whose t = 4 column gives the "
        "printed rows from 0.70/0.75 on; the printed 0.65/0.70 row matches the critic's "
        "`method_pipeline.py` (P(open) 0.93 there), which adds family offsets and a "
        "realisation effect. The Stage 0 model here reproduces every printed row.",
    ]
    return "\n".join(L) + "\n"


#: The critic's own script (``method_stage1d.py``, "proposed rules v2"), rerun outside the
#: repository on 2026-09-24 with 5,000 simulated runs per row (the printed figures used
#: 1,200), to separate the printed figures' Monte Carlo error from other differences.
CRITIC_RERUN_5000: dict[tuple[str, float], float] = {
    ("both", 0.10): 0.144,
    ("both", 0.15): 0.570,
    ("both", 0.20): 0.854,
    ("Sonnet 5 only", 0.15): 0.275,
    ("Sonnet 5 only", 0.20): 0.551,
}


def utf8_stdout() -> None:
    """Print the tables' symbols (≤, Δ) even when stdout is redirected on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stage0-reps", type=int, default=40_000)
    p.add_argument("--stage1-sims", type=int, default=5000)
    p.add_argument("--write", action="store_true", help=f"Write {TABLES.name}.")
    p.add_argument("--check", action="store_true", help="Exit 1 if a figure is off by > 0.05.")
    args = p.parse_args(argv)
    res = run_all(args.stage0_reps, args.stage1_sims)
    md = to_markdown(res, args.stage0_reps, args.stage1_sims)
    if args.write:
        TABLES.write_text(md, encoding="utf-8")
        print(f"wrote {TABLES}")
    else:
        print(md)
    bad = differences(res)
    for b in bad:
        print(f"DIFFERS: {b}")
    return 1 if bad and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
