# Operations benchmark: operating characteristics

Reproduced by `uv run python -m benchmarks.operations.analysis.power --write` (`benchmarks/operations/analysis/power.py`; fixed seeds, so the numbers are reproducible). These tables replace the ones printed in PREREGISTRATION.md section 8. The models and assumptions are in the module docstring of `power.py`; in short: logit per-task SD 2, correlation 0.7 of task difficulty between the models, effect-heterogeneity SD 0.7, realisation effect SD 0.5 shared by all arms, gains on F3 and F4 only, code-skill capturing 25 % of the gain, code+ 0.3 logit below code-hint, true code-hint pass rates 0.65 (Sonnet 5) and 0.70 (Opus 5.5) in Stage 1, and at least one reference estimator passing 95 % of development realisations.

## Stage 0: P(room closed) (40,000 simulated Stage 0 runs per row)

Rule: the room is closed if and only if F(Sonnet 5) ≤ 3 and F(Opus 5.5) ≤ 3.

| True code-hint pass rate (Sonnet / Opus) | Printed (critic) | Reproduced | Difference | Within ±0.05 |
|---|---:|---:|---:|---|
| 0.65 / 0.70 | 0.07 | 0.07 | +0.002 | yes |
| 0.70 / 0.75 | 0.17 | 0.17 | -0.004 | yes |
| 0.75 / 0.80 | 0.32 | 0.33 | +0.007 | yes |
| 0.80 / 0.85 | 0.54 | 0.55 | +0.009 | yes |
| 0.85 / 0.90 | 0.78 | 0.78 | -0.004 | yes |
| 0.90 / 0.92 | 0.92 | 0.92 | -0.004 | yes |

Monte Carlo standard error: at most 0.0025 per row.

## Stage 1: P(CONTINUE) (5,000 simulated Stage 1 runs per row)

Each simulated run is decided by the section-9 rule as pre-registered (`analysis/outcome.py`: harm check first, then the seven CONTINUE conditions for either frontier model, with the family-stratified 10,000-resample bootstrap). The last column decides the same simulated runs by the critic's own rule ("proposed rules v2" in `method_stage1d.py`), which the printed figures come from.

| True Δ_hint | Models gaining | Printed (critic) | Reproduced (rule as pre-registered) | Difference | Within ±0.05 | Critic's rule, same runs |
|---:|---|---:|---:|---:|---|---:|
| +0 | both | 0.00 | 0.00 | +0.000 | yes | 0.00 |
| +10 | both | 0.14 | 0.15 | +0.015 | yes | 0.14 |
| +15 | both | 0.55 | 0.60 | +0.046 | yes | 0.56 |
| +20 | both | 0.84 | 0.88 | +0.041 | yes | 0.86 |
| +15 | Sonnet 5 only | 0.27 | 0.28 | +0.015 | yes | 0.27 |
| +20 | Sonnet 5 only | 0.54 | 0.56 | +0.023 | yes | 0.53 |

Monte Carlo standard error: at most 0.007 per row.

Where the power goes (share of runs in which some frontier model m meets the CONTINUE conditions 1 to k; condition 7, cost, holds whenever lib-directed passes at least a third as often as code-hint, since every session costs the same here):

| True Δ_hint | Models gaining | 1 | 1-2 | 1-3 | 1-4 | 1-5 | 1-6 | 1-7 | P(CLOSE (harm)) | P(PIVOT-skill) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| +0 | both | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.04 | 0.02 |
| +10 | both | 0.27 | 0.16 | 0.16 | 0.16 | 0.16 | 0.15 | 0.15 | 0.00 | 0.04 |
| +15 | both | 0.74 | 0.61 | 0.61 | 0.61 | 0.61 | 0.60 | 0.60 | 0.00 | 0.03 |
| +20 | both | 0.96 | 0.92 | 0.92 | 0.92 | 0.92 | 0.88 | 0.88 | 0.00 | 0.02 |
| +15 | Sonnet 5 only | 0.50 | 0.38 | 0.38 | 0.29 | 0.29 | 0.28 | 0.28 | 0.02 | 0.06 |
| +20 | Sonnet 5 only | 0.85 | 0.76 | 0.76 | 0.58 | 0.58 | 0.56 | 0.56 | 0.02 | 0.05 |

### The same with the critic's generator split

The critic simulated the draft-1 generator split (G-ind/G-epa: F1 5/5, F2 4/4, F3 10/6, F4 4/4). Draft 2 pre-registers F1 5/5, F2 8/0, F3 8/8, F4 4/4 (F2 is G-ind only). Only condition 3 (Δ_hint ≥ 0 on each generator subset) reads the split.

| True Δ_hint | Models gaining | Printed (critic) | Rule as pre-registered | Critic's rule |
|---:|---|---:|---:|---:|
| +0 | both | 0.00 | 0.00 | 0.00 |
| +10 | both | 0.14 | 0.15 | 0.14 |
| +15 | both | 0.55 | 0.60 | 0.56 |
| +20 | both | 0.84 | 0.88 | 0.86 |
| +15 | Sonnet 5 only | 0.27 | 0.28 | 0.27 |
| +20 | Sonnet 5 only | 0.54 | 0.56 | 0.53 |

## Power requirement (section 8)

P(CONTINUE) at a true +20 (both models): 0.88; the requirement P ≥ 0.70 is met.

## Where the differences from the printed figures come from

The printed Stage 1 figures are the critic's "proposed rules v2" (`method_stage1d.py`, 1,200 simulated runs per row). Section 9 as pre-registered differs from that rule in four places; the reproduced column applies all four, the critic's-rule column none:

- condition 4 allows CW(m, lib-directed) up to CW(m, code-hint) + 5 points (the critic: no increase at all);
- condition 5 is per model: for m = Sonnet 5, P(Sonnet 5, lib-directed) ≥ P(Opus 5.5, code-hint); for m = Opus 5.5, P(Opus 5.5, lib-directed) exceeds every arm without worldparts on both models (the critic: always the Sonnet condition);
- condition 6 flags a family only when its Δ_hint ≤ −10 on both models (the critic: the mean over both models ≤ −10 with a 99 % bootstrap upper bound below 0);
- the lower bound of condition 1 comes from 10,000 resamples stratified by family (the critic: 2,000 unstratified resamples).

Each difference splits exactly into three parts: the printed figure's own Monte Carlo error (the critic's script rerun with 5,000 runs, outside this repository, minus the printed figure), the difference between two simulations of the same model (the critic's rule on the runs simulated here minus the critic's script at 5,000 runs), and the rule differences above (the pre-registered rule minus the critic's rule, on the same runs).

| True Δ_hint | Models gaining | Printed | Critic's script, 5,000 runs | Critic's rule, runs here | Rule as pre-registered, runs here | Printed's MC error | Simulation | Rule differences | Total |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| +10 | both | 0.14 | 0.144 | 0.141 | 0.155 | +0.004 | -0.003 | +0.014 | +0.015 |
| +15 | both | 0.55 | 0.570 | 0.560 | 0.596 | +0.020 | -0.010 | +0.037 | +0.046 |
| +20 | both | 0.84 | 0.854 | 0.861 | 0.881 | +0.014 | +0.007 | +0.020 | +0.041 |
| +15 | Sonnet 5 only | 0.27 | 0.275 | 0.269 | 0.285 | +0.005 | -0.006 | +0.016 | +0.015 |
| +20 | Sonnet 5 only | 0.54 | 0.551 | 0.534 | 0.563 | +0.011 | -0.017 | +0.029 | +0.023 |

Every reproduced figure is within ±0.05 of the printed one.

The Stage 0 model is the critic's `method_stage0.py`, whose t = 4 column gives the printed rows from 0.70/0.75 on; the printed 0.65/0.70 row matches the critic's `method_pipeline.py` (P(open) 0.93 there), which adds family offsets and a realisation effect. The Stage 0 model here reproduces every printed row.
