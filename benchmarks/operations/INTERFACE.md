# Operations benchmark: bundle, task and truth formats

This is the contract between the private generator and the public harness and grader. It reveals nothing about how the truth is generated.

## Locations

| What | Where | In git |
|---|---|---|
| Pre-registration, harness, grader, analysis | `benchmarks/operations/` in this repository | yes |
| Task bundles (what agents see) | `$WPBENCH_OPS_BUNDLES/<set>/<task_id>/r<k>/`, default `C:/Users/raimo/world-model/opsbench-bundles` | no; `bundles-<set>.sha256` manifest is in git |
| Truth | `$WPBENCH_OPS_TRUTH/<set>/<task_id>.truth.json`, in the private benchmark folder | no; published after the gate decision |

## Bundle layout (one realisation)

```text
<task_id>/r1/
  task.md          operator ticket, answer keys with units and plausible ranges, answer format
  task.json        machine-readable answer-key metadata (below); no truth values
  plant.md
  plant.inp        optional
  tables/*.csv
  data/*.csv       long format: timestamp, tag, value, quality
  events.csv
```

The harness copies everything in `r<k>/` except `task.json` into the session's working directory. The session prompt is the text of `task.md` plus the arm's preamble. The realisations `r1`, `r2` and `r3` differ only in data noise and artefacts.

**What each session saw.** Each session attempt records the digest of the realisation it saw in `bundle.json`. The digest is a SHA-256 over the path and SHA-256 of every file in `r<k>/`, `task.json` included.

Grading writes two fields into `record.json`:

- `bundle_digest`: the recorded digest;
- `bundle_current`: whether that digest still matches the bundle.

A session whose bundle has been replaced since it ran is `superseded`. That happens when validation redraws a task, or when a task's realisation 1 moves. The report and the headroom rule never score a superseded session (PREREGISTRATION.md section 8, Stage 0).

A rejected task's bundle moves to `dev-rejected/<task_id>-a<attempt>/`, and its replacement keeps the task id.

**Committed files for scoring.** `headroom` reads three committed files next to this one:

- `bundles-dev.sha256`: a session counts only if its digest matches realisation 1 there;
- `truth-dev.sha256`: a task counts as validated only if its truth file's SHA-256 is listed there;
- `stage0-settings.json`: a run with other settings is refused.

## `task.json`

```json
{
  "task_id": "ops-f1-003",
  "set": "dev",
  "family": "F1",
  "generator": "G-ind",
  "cell": "F1/G-ind",
  "stratum": null,
  "keys": {
    "head_deficit_bep_pp": {"kind": "estimate_or_undetermined", "unit": "pp", "range": [0, 30]},
    "wire_to_water_efficiency_pct": {"kind": "estimate_or_undetermined", "unit": "%", "range": [20, 90]},
    "extra_energy_mwh_per_yr": {"kind": "estimate", "unit": "MWh/yr", "range": [0, 500]},
    "deterioration_real": {"kind": "boolean"},
    "first_to_trigger": {"kind": "choice", "options": ["F-1", "F-2", "F-3"]},
    "determinable_set": {"kind": "set", "options": ["pump_head_deficit", "valve_kv", "filter_clean_dp"]},
    "diagnosis": {
      "kind": "diagnosis",
      "vocabulary": {
        "worn_pump": {"unit": "pp", "m_min": 5, "range": [0, 40]},
        "...": {}
      },
      "excluded_from_candidates": {"uv_lamp_degraded": "no UV reactor in this plant"},
      "resolving_options": ["suction_pressure", "sump_level", "motor_power", "downstream_flow"]
    }
  }
}
```

`stratum` is null except in F3, where it is one of `single`, `double`, `ambiguous`, `no_fault` or `sensor`.

## Answer format

The session's final reply ends with one fenced `json` block. It holds one entry per key:

- `estimate`: `{"value": x, "lo90": a, "hi90": b}`. A bare number is accepted as the point value, with no interval.
- `estimate_or_undetermined`: an estimate, or the string `"cannot_determine"`.
- `boolean`: `true` or `false`.
- `choice`: one of the options.
- `set`: a list of options.
- `diagnosis`: an object:

  ```json
  {"verdict": "identified",
   "faults": ["worn_pump"],
   "magnitudes": {"worn_pump": {"value": 8.2, "lo90": 6.9, "hi90": 9.4}},
   "resolving_measurement": null}
  ```

  - `verdict` is one of `identified`, `ambiguous` or `no_fault`.
  - `magnitudes` is required for `identified`.
  - `resolving_measurement` is required for `ambiguous`.

Grading follows `PREREGISTRATION.md`, section 6.6.

## `<task_id>.truth.json`

```json
{
  "task_id": "ops-f1-003",
  "keys": {
    "head_deficit_bep_pp": {"kind": "estimate_or_undetermined", "determinable": true, "value": 8.4, "tol": 1.0, "range": [0, 30]},
    "wire_to_water_efficiency_pct": {"kind": "estimate_or_undetermined", "determinable": false, "value": 61.2, "range": [20, 90]},
    "extra_energy_mwh_per_yr": {"kind": "estimate", "value": 37.5, "tol": 3.75},
    "deterioration_real": {"kind": "boolean", "value": true},
    "first_to_trigger": {"kind": "choice", "value": "F-2"},
    "determinable_set": {"kind": "set", "value": ["valve_kv"]},
    "diagnosis": {
      "kind": "diagnosis",
      "label": "ambiguous",
      "faults": ["low_suction_level", "worn_pump", "pump_running_slow"],
      "magnitudes": {},
      "resolving": ["suction_pressure", "sump_level"]
    }
  },
  "realisations": {
    "r1": {"oracle_pass": true, "reference_pass": {"R-a": true, "R-b": true, "R2": null}, "naive_pass": false},
    "r2": {"...": "..."},
    "r3": {"...": "..."}
  }
}
```

Field notes:

- **Identified diagnoses.** When `label` is `identified`, `magnitudes` maps each true fault to `{"value": v, "tol": t}`.
- **Estimator results.**
  - `reference_pass` records whether each reference estimator passes that realisation.
  - `null` means the estimator does not apply, or, for R2, that it was not needed because R-a or R-b passes (PREREGISTRATION 6.5).
  - **R2 where R-a and R-b both fail.** R2 must have a result there. The exception is a truth whose top level says `"r2_applies": false`, as on filtration and train plants.
  - The Stage 0 headroom rule uses these values for realisation `r1`.
- **Realisation order.** The harness uses `r1`, `r2` and `r3` in order.
  - **Before validation:** a task's bundle holds only `r1`, written from the first realisation of its sequence.
  - **If the oracle fails it:** `r1` is rewritten from the first realisation the oracle passes, and sessions on the earlier `r1` are superseded (PREREGISTRATION.md section 8).
  - **After validation:** `r1`, `r2` and `r3` are the first three realisations on which the oracle passes.
- **Required entries.** `realisations` has exactly one entry for each realisation directory of the bundle (`r1` to `rK`). Each entry has `oracle_pass: true` and a `reference_pass` with at least one estimator. The grader refuses a truth file that breaks this.
