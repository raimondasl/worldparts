# Operations benchmark (v0.3): pre-registration

Status: **draft 2**, 2026-09-24. This file becomes binding at the **freeze-0** commit ([section 7](#7-freezes-sealing-and-firewalls)), before any agent session. After that, changes are allowed only as dated entries in the [change log](#change-log), each with its reason, and never because of agent results.

## 1. Why this benchmark exists

Benchmark v0.2 ([results](../../docs/benchmark-results-v0.2.md)) found that frontier models (Claude Sonnet 5 and Opus 5.5) solve fully specified pumping and treatment calculations from scratch in Python as accurately as with worldparts, and 4 to 10 times more cheaply. On 2026-09-24 the owner chose the "data and operations" direction, with a hard constraint:

> if worldparts does not provide clear value, we should either pivot or close the project.

Research done the same day ([summary](../../docs/research/data-and-operations-2026-09.md)) narrowed the direction.

- **Product data is not a moat.** Manufacturers' terms forbid redistributing their curves. The only clean public source gives one best-efficiency point per pump. EU law makes curves free to read, so a from-scratch agent can read them too.
- **Most of the operations stack exists in pieces.** The pieces are calibration statistics, identifiability, leak hypothesis ranking and water MCP servers.
- **One open gap was found:** plant-scale diagnosis that ranks hypotheses across component types and says honestly when the data cannot decide.

The question this benchmark answers:

**Given an operating plant's documents and its SCADA data, does worldparts make a frontier agent measurably more right, and more honest about what the data cannot decide, than the same agent working from scratch with the same data, the same method checklist and a reference script?**

The benchmark is **headroom-first**. A cheap Stage 0 runs only arms without worldparts on 16 development tasks. If frontier agents already pass them, worldparts cannot add clear value for those models, and the operations direction stops.

## 2. Scope

**Measured.** This is accuracy and honesty on offline, advisory operations questions answered from plant data:

- calibration of degradation;
- trend forecasting;
- fault diagnosis among hypotheses, including ambiguous, no-fault and sensor-fault cases;
- identifiability and sensor choice.

Cost, tokens, turns and wall-clock time are recorded.

**Not measured:**

- real-time control;
- models maintained across sessions;
- auditability;
- product-data retrieval (dropped because redistribution rights are missing);
- network-scale leak localisation (BattLeDIM, dropped: see the research note);
- real plants. The truth is realistic but synthetic, so a positive result is a claim about realistic synthetic plants only.

**Known worldparts gaps**, recorded now so that a negative result is read correctly:

- worldparts cannot import an EPANET `.inp`;
- water properties are constant;
- there is no motor or drive efficiency model;
- filter clogging is an input, not a dynamic;
- the fault catalogue of design section 14.3 has no sensor faults.

The [allowed additions](#7-freezes-sealing-and-firewalls) may close some of these before Stage 1.

## 3. Arms

Every arm gets the same task bundle ([section 5](#5-what-every-arm-receives)), the same answer format, the same limits and the same model settings. Arms differ only in what this table lists.

| Arm | Tools | Python environment | Preamble adds | Stage |
|---|---|---|---|---|
| `code+` | Bash, Read, Write, Edit | numpy, scipy, pandas, statsmodels, scikit-learn, lmfit, fluids, wntr, matplotlib (Agg) | The package list | 0, 1 |
| `code-hint` | as `code+` | as `code+` | The [methods checklist](#31-methods-checklist-verbatim) | 0, 1 |
| `code-skill` | as `code+` | as `code+` | The checklist, plus a reference toolkit in `reference/` in the working directory, and the sentence "Adapt and use the tools in reference/ for this plant." | 1 |
| `lib-directed` | as `code+` | `code+` plus the frozen worldparts wheel | The checklist, the worldparts quick reference, and the directive: "Use worldparts for the plant model, calibration, identifiability and diagnosis where it applies; use any other package for data handling, forecasting and anything worldparts does not model." | 1 (primary) |
| `lib` | as `code+` | as `lib-directed` | The quick reference only; never told to use it | 1 (adoption, secondary) |
| `mcp-hybrid` | Bash, Read, Write, Edit plus the worldparts MCP server (section-14 tools take file paths) | as `code+` | A hybrid preamble that states the shell, file and MCP tools accurately | 1, only for PIVOT-small |

### Why these contrasts

- `lib-directed` against `code-hint` isolates the software. The method text is identical; only the wheel, the quick reference and the directive differ.
- `lib-directed` against `code-skill` asks whether a library beats a toolkit shipped as a skill, which is the cheapest competing product.
- `lib` measures whether agents adopt worldparts unprompted.

### The quick reference

- It has at most 45 lines in all, with at most 20 lines for section 14.
- It contains call signatures and returned field names only: no thresholds and no procedure.
- A test fails if it contains a number, or a method statement, that is not in the checklist.

### The code-skill toolkit

- **Who writes it.** A separate session that has not seen worldparts' section-14 code writes it after Stage 0, from the same inputs and under the same limits as the sessions that finish worldparts:
  - the development bundles, with black-box grading only;
  - the Stage 0 transcripts;
  - the checklist;
  - the same working-time budget.
- **Its instruction** is to maximise the development-set pass rate.
- **Scope.** It covers what design section 14 covers:
  - component models from datasheet tables: pump fit and affinity laws, filter, valve characteristic, pipe friction, UV dose;
  - weighted least squares with Jacobian standard errors;
  - an SVD identifiability check;
  - single-fault and pair fits over the fault vocabulary, with the parsimony rule;
  - a long-format SCADA loader.
- **Size cap.** 800 lines or the line count of worldparts' section-14 code, whichever is larger.
- **Readiness.** Before the test seed is drawn, `lib-directed` and `code-skill` each run once on the development set with Sonnet 5, and both pass rates are committed.

### Models

- Claude Sonnet 5 (`claude-sonnet-5`) and Claude Opus 5.5 (`claude-opus-5-5`), called by these explicit model ids.
- Claude Haiku 4.5 only if the owner names a real deployment that needs a small or local model before freeze-1. In v0.2, Sonnet 5 writing code from scratch beat Haiku with the MCP tools on both accuracy and cost.

### Pinned settings

- The same Claude Code CLI version for all arms (currently 2.1.280, set with `WPBENCH_CLAUDE`).
- The same effort setting per model.
- `--max-turns 120` and 2,400 s per session.
- All recorded in `run.json`.

### 3.1 Methods checklist (verbatim)

This text is frozen at freeze-0. A test checks that it is 20 to 30 lines and names neither worldparts nor its functions as code. The English word "identifiability" in item 7 is allowed.

```text
Method checklist for questions answered from plant data.
1. Read plant.md, tables/ and events.csv first. Note each instrument's stated
   accuracy and mounting height, and the motor and drive nameplate data.
2. Inspect the data before fitting: gaps, spikes, repeated (frozen) values,
   time shifts, and offsets after recalibration events. Remove or flag data
   artefacts; they are not plant faults.
3. Aggregate the record into steady operating points (for example hourly or
   per-setting means) before fitting; use dynamics only when a question needs them.
4. Build the plant model from the datasheet (pump curves with affinity laws,
   motor slip and efficiency, pipe and valve losses, filter and reactor data)
   and check that it reproduces healthy operation within instrument accuracy.
5. Treat sensor biases and datasheet tolerances as unknowns within their bounds.
6. Fit unknowns by weighted least squares, weighting each residual by its
   measurement uncertainty; take standard errors from the Jacobian at the
   optimum, scaled by the reduced chi-square when it exceeds 1.
7. Check identifiability with the singular values of the scaled Jacobian and
   the parameter correlations; a parameter the data do not determine must not
   be reported as if it were.
8. For diagnosis, consider every fault in the list, including none and sensor
   faults. A fault counts only at or above its stated minimum magnitude.
9. Prefer none, then single faults; fit pairs of faults only when no single
   fault explains the data. Compare fits by chi-square, AIC or BIC.
10. If several candidates explain the data comparably, the answer is ambiguous:
    list them and name a measurement that would separate them.
11. Fit trends against their physical driver (for example filter loss against
    solids load since the last backwash), and use the stated future inputs.
12. Give 90 % intervals. If the data cannot determine a quantity, answer
    cannot_determine instead of guessing. Check answers against physics.
```

## 4. Task families and cells

G, the gate set, is F1 to F4. The development set (16 tasks, public seed) and the test set (42 tasks, beacon seed) are drawn from the same pre-registered distributions, with these exact cell counts:

| Family | Dev G-ind | Dev G-epa | Test G-ind | Test G-epa |
|---|---:|---:|---:|---:|
| F1 Pump wear calibration | 2 | 2 | 5 | 5 |
| F2 Filter clogging and forecast | 3 | 0 | 8 | 0 |
| F3 Fault diagnosis | 4 | 2 | 8 | 8 |
| F4 Identifiability and sensor placement | 2 | 1 | 4 | 4 |
| **Total** | **11** | **5** | **25** | **17** |

**F3 strata:**

| Set | Single faults | Double faults | Ambiguous | No fault | Sensor fault |
|---|---:|---:|---:|---:|---:|
| Test | 6 | 2 | 4 | 2 | 2 |
| Dev | 2 | 1 | 1 | 1 | 1 |

Generator assignment within F3 is part of the sealed specification.

### Answer keys

Every answer key has a fixed unit and a plausible range [L, U], stated in `task.md` and fixed per key in the generator specification (not per task).

- **F1: pump wear calibration**, at a raw-water or transfer pump station.
  - `head_deficit_bep_pp`: the head deficit versus the catalogue curve at the catalogue best-efficiency flow (stated in `plant.md`) and catalogue speed, in percentage points of catalogue head. Given as an estimate or `cannot_determine`.
  - `wire_to_water_efficiency_pct`: the mean over the last 24 h of running. Given as an estimate or `cannot_determine`.
  - `extra_energy_mwh_per_yr`: the extra energy against a new pump at the same delivered volume. The counterfactual is defined in `task.md`: the new pump follows the catalogue curve in the same system, the annual volume is the mean daily volume of the record times 365, fixed-speed pumps run fewer hours, and VFD pumps follow the same flow schedule at lower speed.
  - `deterioration_real`: a boolean, true if the head deficit rose by at least 2 pp over the record.
- **F2: filter clogging and forecast**, at two to four parallel pressure filters.
  - `hours_to_trigger_<filter>`: under the stated future flow, turbidity, temperature and backwash schedule.
  - `clean_bed_dp_bar`: after the latest backwash, at a stated reference flow and 15 °C.
  - `fouling_real`: a boolean, true if the clean-bed loss at reference conditions rose by at least 5 % over the record.
  - `first_to_trigger`: a choice among the filters.
- **F3: fault diagnosis**, on the pump station, the filtration stage or the treatment train.
  - `diagnosis`: a verdict and a fault set. When identified, it also carries each fault's magnitude, in that fault's stated unit, as an estimate. When ambiguous, it also carries a `resolving_measurement`, chosen from a list.
  - The vocabulary, the magnitude units and the minimum magnitudes are printed in every F3 `task.md`.
- **F4: identifiability and sensor placement**, with no data or a short record.
  - `determinable_set`: the set of listed quantities determinable to a 90 % half-width of at most X % from the stated test with the existing sensors.
  - `best_added_sensor`: a choice.
  - `two_point_test_helps`: a boolean.
  - `task.md` lists the unknown parameter vector and each nuisance bound; stated accuracies are uniform bounds.

### F3 fault vocabulary

The same 16 faults appear in every F3 task, each with its minimum magnitude m_min as stated in `task.md`:

`none`, `worn_pump`, `pump_running_slow`, `wrong_impeller_or_trim`, `reverse_rotation`, `blocked_suction_strainer`, `low_suction_level`, `air_entrainment`, `throttled_valve`, `check_valve_passing`, `clogged_filter`, `pipe_scaling`, `leak`, `uv_lamp_degraded`, `pressure_sensor_fault`, `flow_sensor_fault`.

- `reverse_rotation` and `air_entrainment` are distractors only, never truths.
- Hypotheses the plant cannot host are left out of that task's candidate list, with the reason stated. Examples: `check_valve_passing` without a standby pump, `uv_lamp_degraded` without a UV reactor.
- `task.md` states: "A sensor fault is a persistent offset or drift larger than twice the stated accuracy. Isolated spikes, dropouts and frozen runs are data-quality artefacts, not faults."

### Catalogue independence

The reference catalogue is design section 14.3 as written on 2026-09-24: `worn_impeller`, `efficiency_loss`, `running_slow`, `clogged`, `partly_closed`, `lamp_degraded`, `scaled` and `leak_at`. In vocabulary terms these cover `worn_pump`, `pump_running_slow`, `clogged_filter`, `throttled_valve`, `uv_lamp_degraded`, `pipe_scaling` and `leak`.

At least 7 of the 16 F3 test tasks have a truth fault, or a member of the ambiguous truth set, outside this list. Distractors do not count.

This criterion is computed against the list above even if worldparts later adds hypotheses as allowed additions.

## 5. What every arm receives

Each task realisation is a directory copied into the session's working directory. No arm receives a worldparts system document, a fitted model, or anything about the generator.

The bundles are too large for git, so they are kept outside it:

- They are stored in a shared bundle folder.
- A manifest of SHA-256 hashes for them is committed.
- They are published with the benchmark after the gate decision.

| File | Content |
|---|---|
| `task.md` | The operator's question as a ticket, the answer keys with units and plausible ranges, the answer format, and for F3 the vocabulary with m_min |
| `plant.md` | Layout; pipe table; equipment data as published, which includes: 6 to 8 pump-curve points at a stated impeller and speed, the catalogue best-efficiency flow, the acceptance grade and the certificate curve when one exists, motor synchronous and full-load speed, motor and drive efficiency at 25, 50, 75 and 100 % load, filter clean drop at rated flow and temperature, valve Kv and characteristic, and UV rating; the instrument list with accuracy and mounting height; the control description; the maintenance and event log |
| `plant.inp` | EPANET 2.2 model of the nominal plant where it can be represented. Filters and UV reactors are TCVs at their rated clean drop, with a comment saying so. |
| `tables/*.csv` | The same equipment data as CSV |
| `data/*.csv` | Historian export, long format (`timestamp, tag, value, quality`). Records of 7 days or less are at 1-minute resolution. Longer records are time-weighted 5-minute averages (at most 7 tags for 60 days) or 15-minute averages (at most 14 tags). At most 5 MB per bundle. |
| `events.csv` | Backwashes, valve operations, pump swaps, instrument recalibrations |

## 6. Truth, validity and grading

### 6.1 Generators

- **G-ind** is an independent plant simulator ("opsim"). Its component forms are taken from the literature and deliberately differ from worldparts' forms and from EPANET's defaults. The forms, the realised sensor-error model and the historian artefacts are specified in the **sealed appendix** ([section 7](#7-freezes-sealing-and-firewalls)).
- **G-epa** runs EPANET 2.2 through WNTR on the plant's `.inp`, perturbed to the truth state. Wear curves are tabulated from G-ind's wear model at 15 or more points, never as a uniform head fraction. Each period is a separate run.
- **Split.** G-epa is used for F1, F3 and F4; F2 is G-ind only.
- No task truth is generated with worldparts.
- **Solver check.** With slip and temperature effects switched off, opsim's elements, exported to EPANET with 41 or more curve points and matched resistances, agree with EPANET steady flows and heads to within 0.2 % on every nominal plant.

### 6.2 What the oracle knows

The oracle knows the generator's functional forms and prior distributions, **not the realised values**.

- Sensor biases, the as-built pump deviation, minor-loss multipliers and roughness are nuisance parameters with their generating priors, and they are profiled out.
- The oracle receives the artefact mask.
- χ² is computed on the aggregated steady bins defined in the sealed specification.
- **Validity.** Across the realisations, the median reduced χ² of the truth hypothesis must lie in [0.8, 1.25]. Otherwise the noise model is mis-specified and the draw is rejected.

### 6.3 Hypotheses and labels (F3)

**Hypotheses.**

- A single-fault hypothesis h is fitted with its magnitude in [m_min(h), max(h)].
- A pair hypothesis is two component-disjoint faults, both at or above m_min.
- `none` has no fault parameters. So no hypothesis contains another as a special case.

**Parsimony.** The candidate order is `none`, then single faults, then pairs. Pairs are candidates only when every single fault and `none` are excluded.

**Asimov screen.** For each draw, the oracle computes the noise-free (Asimov) Δχ² between the truth hypothesis and every candidate:

- a candidate is **consistent** if Δχ² < 1;
- it is **excluded** if Δχ² > 60;
- if any candidate falls in [1, 60], the draw is rejected.

**Truth label.**

- `no_fault` if the consistent set is {none};
- `identified` if it is one single fault or one pair;
- `ambiguous` otherwise.

The draw must match its stratum.

**Resolving measurements** (ambiguous tasks). A listed measurement resolves the task if, once it is added at its stated accuracy, every pair of members of the consistent set has an Asimov Δχ² above 60. Any resolving measurement passes. Each list must contain at least one measurement that resolves and at least two that do not.

**Monte Carlo confirmation.** The oracle is run on 200 realisations. On each, its verdict is the consistent set within Δχ² ≤ 10 of the best candidate, under the parsimony order. The oracle must reproduce the truth label in at least 95 % of realisations.

### 6.4 Numeric keys, tolerances and determinability

- **Per-realisation fit.** On each realisation, the oracle computes a point estimate and a 95 % profile-likelihood interval, with the parameter bounded to [L, U].
- **Spread and width.** SD is the standard deviation of the point estimates across realisations. W is the median interval width divided by (U − L).
- **Determinability.** A key is determinable if W ≤ 0.20 and not determinable if W ≥ 0.50. Anything in between is rejected.
- **Tolerance.** tol = max(3·SD, floor). A determinable key is kept only if 2·tol ≤ 0.35·(U − L).
- **Floors**, in each answer's own unit:

| Quantity | Floor |
|---|---|
| Head deficit | 1 pp |
| Efficiency | 2 pp |
| Energy | 10 % |
| Time to trigger | 10 % or 3 h, whichever is larger |
| Clean-bed Δp | 0.01 bar or 3 %, whichever is larger |
| Valve Kv | 5 % |
| Leak flow | 20 % |
| Sensor offset | 0.5 × the stated accuracy |
| UV lamp output | 3 pp |
| Speed | 1 % |

- **Booleans.** A boolean key is kept only if the true state lies at least one tolerance away from its threshold, and the oracle's decision matches the truth in at least 95 % of realisations.
- **F4 labels.** The oracle predicts each quantity's 90 % half-width with the true forms, confirmed by a 200-realisation Monte Carlo.
  - A quantity is **determinable** if its half-width is at most 0.5X.
  - It is **not determinable** if its half-width is at least 2X.
  - A draw with any listed quantity in between is rejected.
  - Exactly one candidate sensor may bring the target quantity Q to 0.5X or below, and every other candidate must leave it at 2X or above.

### 6.5 Reference estimators and task validity

No reference estimator is worldparts or an LLM. They share one estimation engine with pluggable forward models:

| Estimator | Pump form | Filter form | Valve form | Selection rule |
|---|---|---|---|---|
| **R-a** | Quadratic | Power law, Δp = a·Q^n | Equal percentage from the datasheet | Δχ² ≥ 9 |
| **R-b** | PCHIP | Linear plus quadratic | Linear Kv | AIC, 2-unit rule |
| **R2** | EPANET from the supplied `.inp` (F1, F3 and F4 only) | Consistent set within 2 AIC of the best in at least 50 % of realisations | | |

- **Shared behaviour.** Every reference estimator:
  - cleans artefacts (a Hampel filter and frozen-run removal);
  - models slip from the nameplate;
  - normalises viscosity by temperature;
  - corrects for mounting heights;
  - treats biases as bounded nuisance parameters;
  - answers `cannot_determine` when its 90 % interval exceeds half the plausible range;
  - forecasts F2 with a quadratic in cumulative solids load.
- **Naive estimator.** It takes datasheet values at face value and does no cleaning. It tries hypotheses in vocabulary order, and a hypothesis "fits" when every residual is within the stated accuracy. In F4 it treats a quantity as determinable if and only if it is directly measured.

**A task is valid only if all of these hold:**

- the oracle passes at least 95 % of realisations;
- at least two of the applicable reference estimators (R-a, R-b, and R2 where it applies) pass at least 90 %;
- the naive estimator fails at least 70 %;
- the bundle is at most 5 MB.

**Redraws.**

- Invalid draws are redrawn within their cell, using that cell's own seed sequence.
- Every rejection is logged with the rule and the estimator responsible.
- Before freeze-0, each cell's acceptance rate is measured on 200 public-seed draws. The Asimov screen runs on all 200. Full validation runs on a random subsample of at least 20 draws that passed the screen, with at least 50 realisations each. A cell below 10 % is redesigned on the development distribution.
- After the beacon, a cell not filled within 100 draws is dropped, and the drop is reported.

**Trivial policies.** Each policy is defined for every answer kind:

| Answer kind | Policies |
|---|---|
| Numbers | The datasheet value; the middle of the plausible range |
| `estimate_or_undetermined` | `cannot_determine` |
| Booleans | Always true; always false |
| Choices | The most common option; the first listed option |
| Sets | Empty; full; the most common set |
| Diagnosis | Always `none`; always ambiguous with every candidate; always the most common fault, with magnitude at the middle of its range |

Checked on the gate set:

- Per key, each policy passes at most max(35 %, 1/k + 15 %) of instances, where k is the number of options.
- Always-`cannot_determine` passes at most 20 % of each `estimate_or_undetermined` key.
- Every boolean key has a minority class of at least 35 %.
- At task level, the best per-key combination of these policies passes at most 15 % of tasks.

**Reports.** Before freeze-1, the rejection log is summarised by rule, estimator, family and generator. The gate claim then applies to "tasks where datasheet-level reasoning fails and reference estimators succeed". An exploratory estimate, reweighted to the distribution as drawn, is reported next to it.

### 6.6 Answer format and grading

The final reply ends with one fenced `json` object. Value kinds:

- `estimate`: `{"value": x, "lo90": a, "hi90": b}`
- `estimate_or_undetermined`: an estimate, or `"cannot_determine"`
- `diagnosis`: `{"verdict": "identified" | "ambiguous" | "no_fault", "faults": [...], "magnitudes": {"<fault>": <estimate>}, "resolving_measurement": "<choice>"}`. Only `identified` needs `magnitudes`, and only `ambiguous` needs `resolving_measurement`.
- `set`, `choice`, `boolean`: as in v0.2

**Numbers.**

- A point passes when |value − truth| ≤ tol. A bare number counts as a point with no interval.
- For `estimate_or_undetermined`:
  - on a determinable key, only a point within tol passes;
  - on a non-determinable key, `cannot_determine` passes, and so does an interval that contains the truth and covers at least 50 % of [L, U].

**Diagnosis.**

| Truth | Answer | Pass | Counted as |
|---|---|---|---|
| Single X | `identified` [X], magnitude within tol | yes | |
| Single X | `identified` [X], magnitude outside tol | no | wrong magnitude |
| Single X | `ambiguous` with a set containing X | no | under-commitment |
| Single X | `identified` [Y ≠ X], or a pair, or `no_fault` | no | confident wrong |
| Pair {X, Y} | `identified` [X, Y], both magnitudes within tol | yes | |
| Pair {X, Y} | `identified` [X] or [Y] | no | incomplete |
| Pair {X, Y} | any other `identified`, or `no_fault` | no | confident wrong |
| Pair {X, Y} | `ambiguous` | no | under-commitment |
| Ambiguous S | `ambiguous` with a set ⊇ S of size ≤ \|S\| + 1, and a resolving measurement | yes | |
| Ambiguous S | `ambiguous` with a wrong set or measurement | no | wrong set |
| Ambiguous S | `identified` (anything) or `no_fault` | no | confident wrong |
| None | `no_fault` | yes | |
| None | `ambiguous` | no | under-commitment |
| None | `identified` (anything) | no | confident wrong |

**Task pass.** A task passes only if every answer passes.

**Confident-wrong rate.** CW(m, arm) is the mean, over F3 tasks, of the share of a task's sessions whose verdict counts as confident wrong.

**Secondary metrics** (not gated):

- the Gneiting-Raftery interval score at α = 0.1, normalised by the oracle's;
- the coverage of 90 % intervals;
- abstention precision and recall;
- the utility score (+1 correct, +1 justified abstention, +0.25 unjustified abstention, −1 confident wrong).

**Infrastructure errors.**

- Only these count: a CLI, API, transport, harness or permission failure, a session with no model turn, or an external kill. They are detected by rules committed at freeze-0.
- Such sessions are re-run, at most twice, with the same realisation. They are never excluded.
- Exceptions raised by worldparts or by the agent's code, timeouts and turn-limit hits are failures.

**Contamination.** Traceability and contamination checks are kept from v0.2. New markers cover the private benchmark folder, truth files, seeds and the sealed appendix. If more than 5 % of an arm's sessions are contaminated, the gate is not computed until the cause is fixed and that arm is re-run.

## 7. Freezes, sealing and firewalls

### Private benchmark folder

opsim, the generators, the validation suite, the sealed appendix and all truth files live in a private folder outside the public repository. Each freeze commit in the public repository records the SHA-256 of a `git archive` of that folder. The folder is published after the gate decision.

### freeze-0 (before the harness pilot and Stage 0)

**Public repository:**

- this file;
- the grader;
- the harness support for the `code+` and `code-hint` arms;
- the headroom-rule script;
- `analysis/power.py` with its tables;
- the checklist;
- the preambles;
- the infrastructure-error rules;
- the hash manifest of the 16 development bundles (realisations 1 to 3).

**Private folder, by hash:**

- the sealed appendix (opsim forms, realised error model, historian artefacts, fault forward models, m_min, the tag lists and aggregation);
- opsim;
- the generators;
- the validation suite;
- the plausible ranges and tolerance floors;
- the development truth and validity reports;
- the cell acceptance rates.

After freeze-0, the files above change only through logged bug fixes. Any change that alters a development bundle or its truth re-runs Stage 0.

### freeze-1 (only if the room is open; before the beacon round)

- The worldparts wheel, tagged.
- The quick reference.
- The `lib`, `lib-directed` and `mcp-hybrid` preambles.
- The `code-skill` toolkit.
- `analysis/analyze.py`, implementing section 8 as written; only bug fixes against this text are allowed.
- The sealing code.
- A hash check that every freeze-0 file is byte-identical, or has only logged fixes.

The commit hash and the drand round number are posted in a public GitHub issue before the round.

### Test seed

The test seed is the randomness of the first drand mainnet round published at least 24 hours after the freeze-1 commit. The round number is fixed in that commit. Test bundles are generated after the round is published. Test truth stays in the private folder, encrypted at rest, and the key is never in a session's environment.

### Firewall

These sessions must not read the private folder, the research drafts, or the draft history of this file:

- sessions that finish or tune worldparts' section-14 features;
- the session that writes the quick reference;
- the session that writes the `code-skill` toolkit.

They may:

- read the public development bundles;
- run them as an agent would;
- receive pass or fail from the grader, at most 3 logged evaluations per session.

Their transcripts are kept and audited for reads of forbidden paths.

The orchestrating session, which has seen both sides, writes neither worldparts code nor the toolkit.

### Allowed worldparts additions after freeze-0

- The section-14 APIs: measurements with a long-format SCADA reader, `calibrate`, `identifiability` and `diagnose`.
- File-path measurements for MCP.
- Minimum fault magnitudes and the parsimony rule in `diagnose`: the fix for nested hypotheses found in review.
- Generic hypotheses such as a sensor offset, or a passing check valve.
- EPANET `.inp` import.
- Temperature-dependent water properties from a standard correlation.
- Motor and drive efficiency from nameplate tables.
- Performance work.

Not allowed:

- component laws, defaults or data-cleaning rules chosen to match anything in the sealed appendix.

Before the seed, a reviewer diffs `src/worldparts` between freeze-0 and freeze-1 and lists every new constant, default and cleaning rule.

**Readiness.** Before freeze-1, a scripted worldparts pipeline with no LLM must pass at least 90 % of development realisations, and `calibrate` and `diagnose` must finish within 300 s on every development bundle when used as documented.

### Publication

Test tasks and truth are published after the gate decision, with a canary string.

## 8. Stages and statistics

### Stage 0: headroom (no worldparts)

**What runs.**

- The 16 development tasks, once each, in `code+` and `code-hint`, on Sonnet 5 and Opus 5.5: 64 sessions.
- Each task uses **realisation 1**: the first realisation in its sequence on which the oracle passes.

**Cost.** Before Stage 0:

- A pilot of at most 6 sessions checks the harness. It is not scored.
- The cost per session is re-estimated from the pilot.
- The owner approves the projected Stage 0 cost. The owner re-approves if a later projection exceeds the approved figure by more than 1.5 times.

**Rule.** For each frontier model m:

- F(m) is the number of development tasks on which `code-hint` fails while at least one valid reference estimator passes the same realisation.
- **The room is closed if and only if F(Sonnet 5) ≤ 3 and F(Opus 5.5) ≤ 3.**
- `code+` is recorded but does not enter the rule.

**Operating characteristics.** `power.py` gives the chance that the room is closed at each true code-hint pass rate. It reproduced a critic's figures within ±0.01. The assumptions and the full tables are in [analysis/power-tables.md](analysis/power-tables.md).

| True code-hint pass rate (Sonnet / Opus) | P(closed) |
|---|---:|
| 0.65 / 0.70 | 0.07 |
| 0.70 / 0.75 | 0.17 |
| 0.75 / 0.80 | 0.33 |
| 0.80 / 0.85 | 0.55 |
| 0.85 / 0.90 | 0.78 |
| 0.90 / 0.92 | 0.92 |

**If the room is closed,** the recommendation to the owner is to close the operations direction. The report classifies each failure.

**If the room is open:**

- finish worldparts' section 14 under the firewall;
- write the `code-skill` toolkit;
- pass the readiness checks;
- make the freeze-1 commit;
- run Stage 1.

### Stage 1: the decision run

**Sessions.** The 42 test tasks, on both frontier models:

- `lib-directed`, `code-hint` and `code-skill`, with 3 repeats each;
- `code+` and `lib`, with 1 repeat each.

That is 924 sessions. If PIVOT-small is in play, Haiku adds `code-hint` and `mcp-hybrid`, with 3 repeats each.

- **Realisations.** Repeat r uses the r-th realisation on which the oracle passes, in every arm. Arms are paired on data noise; agent randomness is not paired.
- **Cost.** The owner approves the projected cost before the run.

**Analysis** (`analysis/analyze.py`):

- The unit is the task. A task's score is its mean pass over its repeats. P(m, arm) is the mean over the 42 gate tasks.
- Intervals come from a paired bootstrap:
  - resampling tasks, stratified by family;
  - 10,000 resamples with a fixed seed;
  - percentile limits.

  The lower bound is the 2.5th percentile of the resampled mean per-task difference.
- The primary contrast per model is Δ_hint(m) = P(m, lib-directed) − P(m, code-hint). Its companion is Δ_skill(m) = P(m, lib-directed) − P(m, code-skill).
- The family and generator terms in the CONTINUE rule are gate components. Every other breakdown is exploratory.

**Cost per pass.** cost/pass(m, arm) is the total cost of the arm's gate sessions divided by its number of passing sessions.

**Power.** `power.py` simulates P(CONTINUE) under the predicted pattern: gains concentrated on F3 and F4, about 0 on F1 and F2, and `code-skill` capturing a quarter of the gain.

- Under the rule as pre-registered, P(CONTINUE) is 0.00 at a true 0, 0.15 at +10, 0.60 at +15 and 0.88 at +20. When only Sonnet 5 gains, it is 0.28 at +15 and 0.56 at +20. These reproduce a critic's estimates within ±0.05; see [analysis/power-tables.md](analysis/power-tables.md).
- The requirement is met: P(CONTINUE) at a true +20 is at least 0.70. Had it fallen short, the test set would have been enlarged before the seed was drawn, or the owner's acceptance of the lower power recorded in the change log.
- The primary thresholds (+15 with a lower bound of +5) never change.

### Blind defect audit and re-runs

Before any grade is computed, the harness checks every session against the pre-registered infrastructure signatures, with arm labels hidden. A defect found this way, or found later without using grades, leads to re-running exactly the affected sessions in every arm with the same realisations. The gate is then computed once on the merged data, whatever the outcome. A whole stage is never re-run.

## 9. Outcomes

These are evaluated in order.

**CLOSE (harm).** If Δ_hint ≤ −10 points for either frontier model, the outcome is CLOSE, whatever else holds.

**CONTINUE.** worldparts shows clear value for operations if there is a frontier model m for which all of these hold:

1. Δ_hint(m) ≥ +15 points, with a lower bound ≥ +5.
2. Δ_skill(m) ≥ +10 points.
3. Δ_hint(m) ≥ 0 on both the G-ind subset and the G-epa subset.
4. CW(m, lib-directed) ≤ CW(m, code-hint) + 5 points. For the other model m′, CW(m′, lib-directed) − CW(m′, code-hint) ≤ +10 points, and Δ_hint(m′) ≥ −5 points.
5. If m is Sonnet 5, P(Sonnet 5, lib-directed) ≥ P(Opus 5.5, code-hint). If m is Opus 5.5, P(Opus 5.5, lib-directed) exceeds P of every arm without worldparts, on both models.
6. No family f has Δ_hint(f) ≤ −10 points on both frontier models.
7. cost/pass(m, lib-directed) ≤ 3 × cost/pass(m, code-hint).

Cost savings alone never justify CONTINUE.

**PIVOT-skill.** The method is the product. This holds if CONTINUE fails and, for Sonnet 5 (the pre-specified model), P(code-skill) − P(code+) ≥ +15 points with a lower bound ≥ +5. The project then publishes the checklist and the toolkit as an open agent skill, and archives the library.

**PIVOT-small.** This holds only if the owner named a small-model deployment before freeze-1. The pre-specified arm is `mcp-hybrid`, and all of these must hold:

- Haiku `mcp-hybrid` − Haiku `code-hint` ≥ +20 points, with a lower bound ≥ +5;
- Haiku `mcp-hybrid` passes at least 70 %;
- it is within 10 points of Sonnet 5 `code-hint`, at a lower cost per pass.

**PIVOT-data** is not available until manufacturers grant redistribution rights in writing. It would then need its own pre-registered contrast.

**CLOSE.** None of the outcomes above holds.

**Adoption.** If `lib` sessions use worldparts in fewer than 20 % of sessions, the report says so. Adoption is not gated.

## 10. Fairness threats and mitigations

| # | Threat | Favours | Mitigation |
|---|---|---|---|
| 1 | Truth model form equals worldparts' | worldparts | No worldparts truth; the forms are sealed and differ; reference estimators with several forms must pass |
| 2 | Truth form equals EPANET's, and the code agent has WNTR | code | Reported per generator; CONTINUE needs Δ ≥ 0 on G-epa |
| 3 | worldparts tuned to the generator | worldparts | Sealed appendix and private folder; firewall with audited transcripts; allowed-additions list; freeze-diff review |
| 4 | Validity filter selects tasks where worldparts' forms or rules work | worldparts | Two of three differently specified reference estimators must pass; rejection log; reweighted exploratory estimate |
| 5 | Nested hypotheses make no-fault labels impossible | either | Minimum magnitudes; parsimony order; Asimov screen |
| 6 | Oracle knowing the realised values shrinks tolerances | either | Oracle knows priors only; nuisance parameters profiled |
| 7 | Quick reference smuggles in method advice | worldparts | Line and content test; identical checklist in all method arms |
| 8 | Weak competing script | worldparts | Separate author with the same inputs and budget; scope matched to section 14; told to use it; size cap |
| 9 | Fault vocabulary mirrors worldparts' catalogue | worldparts | At least 7 of 16 F3 truths outside the frozen reference catalogue |
| 10 | Directed arm pushes worldparts where it does not apply | code | Directive limited to "where it applies"; known gaps recorded |
| 11 | Gate conjunction trips on noise | code | Per-model conditions; family harm only when both models show it; power requirement |
| 12 | One-sided re-runs | worldparts | Blind defect audit; affected sessions re-run in every arm, whatever the outcome |
| 13 | Infrastructure errors hide library failures | worldparts | Library exceptions and timeouts count as failures |
| 14 | Development truth readable by Stage 0 agents | code | Truth in the private folder; contamination markers |
| 15 | Turn and time limits | either | Equal limits; aggregation line in the checklist; readiness time check |
| 16 | Synthetic plants | external validity | Realistic artefacts; the claim is limited to realistic synthetic plants |
| 17 | Forking paths | whichever is hoped for | One primary contrast per model; freezes with hashes; public issue before the beacon |
| 18 | Model or CLI drift | either | Stage 1 runs every arm afresh; no Stage 0 session is reused |

## Change log

- 2026-09-24, draft 1: written from three independent designs and two judges' critiques.
- 2026-09-24, draft 2: rewritten after three critics (a methodologist, a skeptic and the builder) reviewed draft 1. Fixes:
  - the Stage 0 rule;
  - nested hypotheses and minimum magnitudes;
  - oracle knowledge;
  - determinability and tolerance margins;
  - trivial policies per key;
  - a per-model CONTINUE rule with a power requirement;
  - symmetric re-runs;
  - freezes and firewalls with a sealed appendix;
  - the competing toolkit's author and scope;
  - the quick-reference limits;
  - data-size rules;
  - F2 generated by G-ind only;
  - opsim simplified.

  Draft 1 printed some generator forms; they moved to the sealed appendix. The section-14 implementation that was running while this was written was started before the benchmark existed; its transcripts are audited for reads of `benchmarks/operations/`.
- 2026-09-24, freeze-0 review: logged bug fixes to the grader, the infrastructure rules, the harness and the outcome analysis. No rule of sections 6 to 9 changes. These readings are now fixed:
  - **Final reply.** It is the `result` of the session's success result message. A session that ended without one (a timeout or a kill before the result, the turn limit, the budget cap, an error) has no final reply and fails, whatever an earlier message held. A success result written before the harness stopped a process that did not exit is graded. A missing diagnosis is then `missing`, not confident wrong.
  - **Answer block.** It is the last `json` block of the final reply. Without one, the grader reads the last untagged block, then the last block with another tag, then the last bare object. Only that one block is read. If it is not a JSON object, the reply has no answer; an earlier block such as a draft is never graded.
  - **Numbers given as strings.** They count only when the whole string is one number, with at most a unit after it. A range, a hedge, an approximation or a decimal comma ("8 to 12", "8.4-9.0", "~8.4", "8,4") is not an estimate.
  - **Diagnosis.** The verdict governs. `identified` on an ambiguous or no-fault truth is confident wrong whatever `faults` holds. A `faults` that is missing, null or not a list of names names no fault, under every verdict and truth, so `identified` with such a list is confident wrong on a single fault or a pair as well. `malformed` is left for an answer that is not an object or has no valid verdict.
  - **Key names.** An exact key wins over a normalised variant: at the top level, in estimate and diagnosis fields, and in `magnitudes`. An ignored variant is recorded.
  - **Infrastructure signatures.** The CLI's own `<synthetic>` messages, which report an API error, are not model turns. A 401 retry counts only when the model took no turn after it. A permission denial counts only when the session then ended with a success result without an answer. A timeout, the turn limit or the budget cap after a recovered 401 or a denial is the agent's failure. `run` stops launching sessions after an API error before any model turn (a usage or rate limit), as it does after an authentication failure. A session still has at most three attempts.
  - **Unknown cost.** The CLI reports a session's cost only in its result message. A session without one (a timeout or a kill) is counted at its (model, arm) cell's cost per wall-clock second times its wall-clock time. The count of such estimates is reported with cost/pass. An unknown cost never vetoes condition 7; only a cell in which no session reported a cost fails it, and the check says so.
  - **Truth files.** `realisations` must hold the bundle's r1 to rK, each with `oracle_pass` true and a non-empty `reference_pass`. The headroom rule is not computed for a task without a realisation-1 reference result, instead of counting it as "no estimator passes".
  - **Isolation.** Sessions of one task never run at the same time. A tool input that names another session's temporary directory, or a tool result that shows a path inside one, is contamination. Sessions get no `PWD`, `OLDPWD` or `INIT_CWD`, and no variable whose value names the repository, the bundle folder or the truth folder.
  - **Firewall.** Grader records hold no truth value, but a numeric key's truth can be worked out from them, so records and summaries go to the owner only. A firewalled session gets only `grade RUN --pass-fail`, which prints pass or fail per session, writes no record and logs each evaluation.
