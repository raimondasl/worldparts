"""Tests of the operations-benchmark analysis: the section-9 outcome rule on synthetic data
(benchmarks/operations/analysis/outcome.py) and the power simulations (power.py)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.operations.analysis import outcome as O  # noqa: E402
from benchmarks.operations.analysis import power as P  # noqa: E402

PREREG = (REPO / "benchmarks" / "operations" / "PREREGISTRATION.md").read_text(encoding="utf-8")
FAMILY, GENERATOR = P.PREREG_LAYOUT
N = len(FAMILY)
S, OP, H = O.SONNET, O.OPUS, O.HAIKU


# ----------------------------------------------------------------------------------------
# bootstrap
# ----------------------------------------------------------------------------------------
def test_stratified_resamples_keep_each_family() -> None:
    idx = O.stratified_indices(FAMILY, 500, 7)
    assert idx.shape == (500, N)
    fam = np.array(FAMILY)
    for f in ("F1", "F2", "F3", "F4"):
        pos = np.flatnonzero(fam == f)
        assert np.isin(idx[:, pos], pos).all()  # a family's slots draw only its own tasks
    assert (O.stratified_indices(FAMILY, 500, 7) == idx).all()  # fixed seed
    assert not (O.stratified_indices(FAMILY, 500, 8) == idx).all()


def test_bootstrap_lower_bound() -> None:
    assert O.bootstrap_lower_bound(np.full(N, 0.2), FAMILY) == pytest.approx(0.2)
    d = np.where(np.arange(N) % 2 == 0, 0.75, -0.45)  # mean 0.15, wide spread
    lb = O.bootstrap_lower_bound(d, FAMILY)
    assert lb < 0.05 < d.mean()
    d2 = d.copy()
    d2[0] = np.nan  # a task without sessions in one arm is left out
    assert np.isfinite(O.bootstrap_lower_bound(d2, FAMILY))
    assert np.isnan(O.bootstrap_lower_bound(np.full(N, np.nan), FAMILY))
    assert O.BOOT_RESAMPLES == 10_000


# ----------------------------------------------------------------------------------------
# the decision
# ----------------------------------------------------------------------------------------
def const(x: float) -> np.ndarray:
    return np.full(N, float(x))


def make(**over: Any) -> O.GateData:
    """Sonnet 5 meets every CONTINUE condition; Opus 5.5 gains nothing."""
    score = {
        (S, O.CODE_HINT): const(0.5),
        (S, O.LIB_DIRECTED): const(0.7),
        (S, O.CODE_SKILL): const(0.55),
        (S, O.CODE_PLUS): const(0.4),
        (OP, O.CODE_HINT): const(0.5),
        (OP, O.LIB_DIRECTED): const(0.5),
        (OP, O.CODE_SKILL): const(0.5),
        (OP, O.CODE_PLUS): const(0.45),
    }
    cw = {(S, O.LIB_DIRECTED): 0.10, (S, O.CODE_HINT): 0.20,
          (OP, O.LIB_DIRECTED): 0.20, (OP, O.CODE_HINT): 0.20}  # fmt: skip
    cpp = {(S, O.LIB_DIRECTED): 1.0, (S, O.CODE_HINT): 1.0,
           (OP, O.LIB_DIRECTED): 1.0, (OP, O.CODE_HINT): 1.0}  # fmt: skip
    score.update(over.pop("score", {}))
    cw.update(over.pop("cw", {}))
    cpp.update(over.pop("cpp", {}))
    assert not over
    return O.GateData(
        tasks=tuple(f"t{i}" for i in range(N)),
        family=FAMILY,
        generator=GENERATOR,
        score=score,
        cw=cw,
        cost_per_pass=cpp,
    )


def oks(d: O.Decision, m: str) -> list[bool]:
    return [c.ok for c in d.continue_checks[m]]


def test_continue_with_sonnet() -> None:
    d = O.decide(make())
    assert d.outcome == O.CONTINUE and d.model == S
    assert oks(d, S) == [True] * 7
    assert d.numbers["delta_hint"][S] == pytest.approx(0.2)
    assert d.numbers["lb_hint"][S] == pytest.approx(0.2)
    assert "Outcome: CONTINUE (m = Sonnet 5)" in d.lines()[0]


def _split_gain(hi: float, lo: float) -> np.ndarray:
    """lib-directed scores: +hi over code-hint on G-ind tasks, +lo on G-epa ones."""
    return np.where(np.array(GENERATOR) == "G-ind", 0.5 + hi, 0.5 + lo)


def _fam_gain(per_family: dict[str, float], base: float = 0.5) -> np.ndarray:
    return np.array([base + per_family[f] for f in FAMILY])


ALT = np.where(np.arange(N) % 2 == 0, 0.75, -0.45)


@pytest.mark.parametrize(
    ("name", "over", "failing"),
    [
        ("Δ_hint below +15", {"score": {(S, O.LIB_DIRECTED): const(0.64),
                                        (S, O.CODE_SKILL): const(0.5)}}, 1),
        ("lower bound below +5",
         {"score": {(S, O.CODE_HINT): np.where(ALT > 0, 0.25, 0.75),
                    (S, O.LIB_DIRECTED): np.where(ALT > 0, 1.0, 0.30),
                    (S, O.CODE_SKILL): np.where(ALT > 0, 0.85, 0.15)}}, 1),
        ("Δ_skill below +10", {"score": {(S, O.CODE_SKILL): const(0.61)}}, 2),
        ("G-epa below 0", {"score": {(S, O.LIB_DIRECTED): _split_gain(0.3, -0.01),
                                     (S, O.CODE_SKILL): _split_gain(0.15, -0.16)}}, 3),
        ("CW rises by more than 5", {"cw": {(S, O.LIB_DIRECTED): 0.2501}}, 4),
        ("other model's CW rises by more than 10", {"cw": {(OP, O.LIB_DIRECTED): 0.3001}}, 4),
        ("other model loses more than 5", {"score": {(OP, O.LIB_DIRECTED): const(0.44)}}, 4),
        ("Sonnet lib-directed below Opus code-hint",
         {"score": {(OP, O.CODE_HINT): const(0.75), (OP, O.LIB_DIRECTED): const(0.75),
                    (OP, O.CODE_SKILL): const(0.75)}}, 5),
        ("a family harmed on both models",
         {"score": {(S, O.LIB_DIRECTED): _fam_gain({"F1": -0.1, "F2": .25, "F3": .25, "F4": .25}),
                    (S, O.CODE_SKILL): _fam_gain({"F1": -0.25, "F2": .1, "F3": .1, "F4": .1}),
                    (OP, O.LIB_DIRECTED): _fam_gain({"F1": -0.1, "F2": 0, "F3": 0, "F4": 0})}},
         6),
        ("cost per pass above 3x", {"cpp": {(S, O.LIB_DIRECTED): 3.0001}}, 7),
        ("cost unknown", {"cpp": {(S, O.LIB_DIRECTED): None}}, 7),
        ("CW unknown", {"cw": {(S, O.CODE_HINT): None}}, 4),
    ],
)  # fmt: skip
def test_each_continue_condition_can_fail_alone(name: str, over: Any, failing: int) -> None:
    d = O.decide(make(**over))
    expected = [True] * 7
    expected[failing - 1] = False
    assert oks(d, S) == expected, (name, [(c.name, c.detail) for c in d.continue_checks[S]])
    assert d.outcome != O.CONTINUE


@pytest.mark.parametrize(
    "over",
    [
        {"cw": {(S, O.LIB_DIRECTED): 0.25}},  # CW up by exactly 5 points
        {"cw": {(OP, O.LIB_DIRECTED): 0.30}},  # the other model's CW up by exactly 10
        {"score": {(OP, O.LIB_DIRECTED): const(0.45)}},  # the other model -5 exactly
        {"cpp": {(S, O.LIB_DIRECTED): 3.0}},  # exactly 3x
        {"score": {(S, O.LIB_DIRECTED): const(0.65), (S, O.CODE_SKILL): const(0.55)}},  # +15, +10
        {"score": {(OP, O.CODE_HINT): const(0.7), (OP, O.LIB_DIRECTED): const(0.7),
                   (OP, O.CODE_SKILL): const(0.7)}},  # P(S, lib) = P(O, hint)
        {"score": {(OP, O.LIB_DIRECTED): _fam_gain({"F1": -0.1, "F2": 0, "F3": .1, "F4": .1})}},
    ],
)  # fmt: skip
def test_boundaries_that_still_pass(over: Any) -> None:
    d = O.decide(make(**over))
    assert oks(d, S) == [True] * 7, [(c.name, c.detail) for c in d.continue_checks[S]]
    assert d.outcome == O.CONTINUE


def test_harm_on_either_model_closes_whatever_else_holds() -> None:
    d = O.decide(make(score={(OP, O.LIB_DIRECTED): const(0.4)}))  # Opus -10 exactly
    assert d.outcome == O.CLOSE_HARM and d.model is None
    assert d.harm[OP] == pytest.approx(-0.1)
    d = O.decide(make(score={(OP, O.LIB_DIRECTED): const(0.41)}))  # -9: condition 4 fails
    assert d.outcome != O.CLOSE_HARM


def test_continue_with_opus_needs_to_beat_every_arm_without_worldparts() -> None:
    base = {
        (S, O.LIB_DIRECTED): const(0.5),
        (S, O.CODE_SKILL): const(0.5),
        (OP, O.LIB_DIRECTED): const(0.75),
        (OP, O.CODE_SKILL): const(0.6),
    }
    d = O.decide(make(score=base))
    assert d.outcome == O.CONTINUE and d.model == OP
    assert oks(d, S)[0] is False
    # Sonnet code-skill equal to Opus lib-directed: not exceeded
    d = O.decide(make(score={**base, (S, O.CODE_SKILL): const(0.75)}))
    assert oks(d, OP)[4] is False and d.outcome != O.CONTINUE
    # Opus code+ above it
    d = O.decide(make(score={**base, (OP, O.CODE_PLUS): const(0.76)}))
    assert oks(d, OP)[4] is False
    # the Sonnet form of condition 5 is not asked of Opus: P(Sonnet 5, lib-directed) 0.5 is
    # below P(Opus 5.5, code-hint) 0.55 here
    d = O.decide(
        make(score={**base, (OP, O.CODE_HINT): const(0.55), (OP, O.LIB_DIRECTED): const(0.8)})
    )
    assert oks(d, OP) == [True] * 7 and d.outcome == O.CONTINUE and d.model == OP


def test_pivot_skill() -> None:
    no_gain = {(S, O.LIB_DIRECTED): const(0.5)}
    d = O.decide(make(score={**no_gain, (S, O.CODE_SKILL): const(0.55)}))
    assert d.outcome == O.PIVOT_SKILL  # code-skill 0.55 - code+ 0.40 = +15
    d = O.decide(make(score={**no_gain, (S, O.CODE_SKILL): const(0.54)}))
    assert d.outcome == O.CLOSE
    skill = np.where(ALT > 0, 1.0, 0.1)  # mean 0.55 but a lower bound below +5
    d = O.decide(make(score={**no_gain, (S, O.CODE_SKILL): skill}))
    assert d.numbers["pivot_skill"]["delta"] == pytest.approx(0.15)
    assert d.outcome == O.CLOSE


def _small(**over: Any) -> O.GateData:
    # no CONTINUE (no gain) and no PIVOT-skill (code-skill 0.5 - code+ 0.4 = +10)
    data = make(score={(S, O.LIB_DIRECTED): const(0.5), (S, O.CODE_SKILL): const(0.5)})
    data.score[(H, O.CODE_HINT)] = const(0.5)
    data.score[(H, O.MCP_HYBRID)] = const(over.get("mcp", 0.75))
    data.cost_per_pass[(H, O.MCP_HYBRID)] = over.get("cpp", 0.5)
    return data


def test_pivot_small_only_when_named() -> None:
    assert O.decide(_small(), small_model_named=True).outcome == O.PIVOT_SMALL
    assert O.decide(_small(), small_model_named=False).outcome == O.CLOSE
    assert O.decide(_small(mcp=0.69), small_model_named=True).outcome == O.CLOSE  # +19, < 70 %
    assert O.decide(_small(cpp=1.0), small_model_named=True).outcome == O.CLOSE  # not cheaper
    d = O.decide(_small(mcp=0.70), small_model_named=True)
    assert d.outcome == O.PIVOT_SMALL and [c.ok for c in d.pivot_small] == [True] * 3
    with pytest.raises(ValueError, match="Haiku"):
        O.decide(make(), small_model_named=True)


def test_pivot_skill_comes_before_pivot_small() -> None:
    data = _small()
    data.score[(S, O.CODE_SKILL)] = const(0.6)
    assert O.decide(data, small_model_named=True).outcome == O.PIVOT_SKILL


def test_missing_arms_are_refused() -> None:
    data = make()
    del data.score[(OP, O.CODE_PLUS)]
    with pytest.raises(ValueError, match="code\\+"):
        O.decide(data)
    with pytest.raises(ValueError, match="one value per task"):
        O.GateData(("a",), ("F1",), ("G-ind",), {(S, O.CODE_HINT): np.zeros(2)})


def test_gate_data_from_sessions() -> None:
    tasks = {"a": ("F1", "G-ind"), "b": ("F3", "G-epa"), "c": ("F3", "G-ind")}
    sessions = [
        {"model": S, "arm": O.CODE_HINT, "task": "a", "passed": True, "cost_usd": 1.0},
        {"model": S, "arm": O.CODE_HINT, "task": "a", "passed": False, "cost_usd": 1.0},
        {"model": S, "arm": O.CODE_HINT, "task": "b", "passed": False, "cost_usd": 2.0,
         "cw_category": "confident_wrong"},
        {"model": S, "arm": O.CODE_HINT, "task": "b", "passed": True, "cost_usd": 2.0,
         "cw_category": "pass"},
        {"model": S, "arm": O.CODE_HINT, "task": "c", "passed": False, "cost_usd": 1.0,
         "cw_category": "under_commitment"},
        {"model": S, "arm": O.CODE_PLUS, "task": "a", "passed": False, "cost_usd": None},
    ]  # fmt: skip
    g = O.gate_data_from_sessions(sessions, tasks)
    assert g.family == ("F1", "F3", "F3")
    assert list(g.score[(S, O.CODE_HINT)]) == [0.5, 0.5, 0.0]
    assert g.cw[(S, O.CODE_HINT)] == pytest.approx((0.5 + 0.0) / 2)
    assert g.cost_per_pass[(S, O.CODE_HINT)] == pytest.approx(7.0 / 2)
    assert g.cost_per_pass[(S, O.CODE_PLUS)] is None
    assert np.isnan(g.score[(S, O.CODE_PLUS)][1])  # no session on b
    assert g.P(S, O.CODE_PLUS) == 0.0
    with pytest.raises(ValueError, match="unknown task"):
        O.gate_data_from_sessions([{**sessions[0], "task": "z"}], tasks)


def test_zero_passes_cost_infinite() -> None:
    g = O.gate_data_from_sessions(
        [{"model": S, "arm": O.LIB_DIRECTED, "task": "a", "passed": False, "cost_usd": 1.0}],
        {"a": ("F1", "G-ind")},
    )
    assert g.cost_per_pass[(S, O.LIB_DIRECTED)] == float("inf")


# ----------------------------------------------------------------------------------------
# power.py
# ----------------------------------------------------------------------------------------
def test_preregistered_figures_are_the_ones_printed() -> None:
    for (ps, po), want in P.PREREG_STAGE0:
        assert f"| {ps:.2f} / {po:.2f} | {want:.2f} |" in PREREG
    flat = re.sub(r"\s+", " ", PREREG)
    s1 = dict(P.PREREG_STAGE1)
    assert (
        f"about {s1[('both', 0.0)]:.2f} at a true 0, {s1[('both', 0.10)]:.2f} at +10, "
        f"{s1[('both', 0.15)]:.2f} at +15, and {s1[('both', 0.20)]:.2f} at +20" in flat
    )
    assert (
        f"fall to {s1[('Sonnet 5 only', 0.15)]:.2f} and {s1[('Sonnet 5 only', 0.20)]:.2f} "
        "when only one model gains" in flat
    )
    assert "If P(CONTINUE) at a true +20 is below 0.70" in flat and P.POWER_REQUIREMENT == 0.70


def test_layouts_match_the_preregistered_cells() -> None:
    from benchmarks.operations.harness.bundles import PREREGISTERED_CELLS

    fam, gen = P.PREREG_LAYOUT
    cells: dict[str, int] = {}
    for f, g in zip(fam, gen, strict=True):
        cells[f"{f}/{g}"] = cells.get(f"{f}/{g}", 0) + 1
    assert cells == PREREGISTERED_CELLS["test"] and len(fam) == 42


def test_stage0_reproduces_a_printed_row() -> None:
    got = P.stage0_p_closed(0.80, 0.85, reps=20_000)
    assert abs(got - 0.54) <= 0.05


def test_stage0_extremes() -> None:
    assert P.stage0_p_closed(0.3, 0.3, reps=2000) < 0.01
    assert P.stage0_p_closed(0.995, 0.995, reps=2000) > 0.95


def test_stage1_calibration_hits_the_targets() -> None:
    model = P.Stage1Model()
    cal = P.calibrate(model, {S: 0.15, OP: 0.0}, seed=3)
    assert cal.delta[OP] == 0.0 and cal.dskill[OP] == 0.0 and cal.delta[S] > cal.dskill[S] > 0
    rng = np.random.default_rng(5)
    n = len(model.family)
    z = rng.standard_normal((20_000, n))
    e = rng.standard_normal((20_000, n)) * model.s_e
    u = rng.standard_normal((20_000, n)) * model.s_u
    fixed = cal.mu[S] + model.offsets + model.tau * z + e
    p_hint = P.expit(fixed).mean()
    p_lib = P.expit(fixed + model.effect_mask * (cal.delta[S] + u)).mean()
    p_skill = P.expit(fixed + model.effect_mask * (cal.dskill[S] + u)).mean()
    assert p_hint == pytest.approx(0.65, abs=0.01)
    assert p_lib - p_hint == pytest.approx(0.15, abs=0.01)
    assert p_skill - p_hint == pytest.approx(0.25 * 0.15, abs=0.01)


def test_stage1_simulation_runs_and_is_decided_by_the_rule() -> None:
    r = P.stage1_rates({S: 0.2, OP: 0.2}, sims=40, seed=1, n_boot=500)
    assert 0 <= r.p_continue <= 1 and 0 <= r.p_continue_critic_rule <= 1
    assert len(r.cumulative) == 7
    assert all(a >= b for a, b in zip(r.cumulative, r.cumulative[1:], strict=False))
    # conditions 1 and 4 rule out harm, so meeting all seven is CONTINUE
    assert r.cumulative[-1] == pytest.approx(r.p_continue)
    null = P.stage1_rates({S: 0.0, OP: 0.0}, sims=40, seed=2, n_boot=500)
    assert null.p_continue == 0.0


def test_differences_flags_rows_beyond_the_tolerance() -> None:
    res = {
        "stage0": [{"p": (0.7, 0.75), "prereg": 0.17, "got": 0.23}],
        "stage1": [{"which": "both", "delta": 0.2, "prereg": 0.84,
                    "result": P.Stage1Result({}, 1, 0.84, 0.84, 0, 0)}],
    }  # fmt: skip
    assert P.differences(res) == ["Stage 0 (0.7, 0.75): 0.230 vs 0.17"]


def test_power_tables_are_committed_and_within_tolerance() -> None:
    text = P.TABLES.read_text(encoding="utf-8")
    assert "## Stage 0: P(room closed)" in text and "## Stage 1: P(CONTINUE)" in text
    cell = r" \| ([0-9.]+) \| ([0-9.]+) \| ([+-][0-9.]+) \| (yes|NO) \|"
    stage0 = re.findall(r"^\| [0-9.]+ / [0-9.]+" + cell, text, re.M)
    stage1 = re.findall(r"^\| [+-][0-9]+ \| [^|]+" + cell, text, re.M)
    assert len(stage0) == len(P.PREREG_STAGE0) and len(stage1) == len(P.PREREG_STAGE1)
    rows = stage0 + stage1
    for printed, got, diff, within in rows:
        assert abs(float(got) - float(printed) - float(diff)) < 0.006
        assert (within == "yes") is (abs(float(diff)) <= P.TOLERANCE)
    if any(w == "NO" for *_, w in rows):
        assert "explained by the split above" in text
    assert "## Power requirement (section 8)" in text
