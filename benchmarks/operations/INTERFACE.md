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
  - `null` means the estimator does not apply.
  - The Stage 0 headroom rule uses these values for realisation `r1`.
- **Realisation order.** The harness uses `r1`, `r2` and `r3` in order. They are already the first three realisations on which the oracle passes.
