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

Field notes:

- **Key names** are lower-case snake_case, `[a-z][a-z0-9_]*`, in task.json, task.md and the truth file alike. An F2 forecast key is `hours_to_trigger_f_1` for filter F-1. The loader refuses any other key name.
- **Vocabulary bounds.** A fault's `m_min` and `range` are both numbers in the fault's unit, or both null. Both are null for a fault with no task-level magnitude bounds:
  - a fault with no magnitude, such as `reverse_rotation`;
  - a fault whose bounds depend on the faulted instrument, such as `pressure_sensor_fault`. Its bounds may be stated as text in an extra field, `bounds`.
- **Signed faults.** A fault whose unit is marked `(signed)` has a signed truth magnitude. When its bounds are given, `range` is signed and `m_min` bounds the absolute magnitude. `pressure_sensor_fault` is signed, and its bounds are null.

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

## Scripted answers (`score DIR`)

A program's answers, such as those of the scripted worldparts pipeline of the readiness check (PREREGISTRATION.md section 7) or a builder's, are scored without an agent session:

```text
DIR/
  <task_id>/r<k>.json   one JSON object: the answer object of the answer format above
```

Nothing else is written to `DIR`.

- **One file per task realisation.** `r<k>.json` answers realisation `r<k>` of the task's bundle, with one entry per key of `task.json`. It is graded exactly as a session's final reply: its text is the body of a fenced `json` block, read with the same lenient parse of one JSON object, then graded by section 6.6. A missing file fails, and so does a file that is not a JSON object or that the grader cannot grade (an integer too large for a float, an array nested thousands deep); the other files are still scored.
- **What is scored.** Every realisation `r1` to `rK` of every task whose truth file is listed in the committed `truth-<set>.sha256`. Other tasks are not validated yet and are not scored, and files for them are ignored. The output says how many realisations were scored. The bundles of the scored tasks must be the committed ones (`bundles-<set>.sha256`); the full mode skips this check only with `--no-manifest-check`.
- **Full mode** (`score DIR --out FILE`, the owner only) writes `FILE`, which must be outside `DIR`. It holds, per realisation, the task, the realisation, whether it passed, whether its file was missing, the bundle digest and the grade (the fields of a session record's grade), and the pass rate with `readiness_needed` and `readiness_met` (section 7's 90 %, computed exactly). Like a record, it is for the owner only.
- **Pass/fail mode** (`score DIR --pass-fail`) takes everything from the owner, who sets it when launching the builder session:
  - `WPBENCH_OPS_PASSFAIL_TRUTH`: the truth folder. `WPBENCH_OPS_TRUTH`, which the owner's commands read, is not set in a builder session.
  - `WPBENCH_OPS_SESSION`: the session's name. `--session`, if given, must be that name.
  - `WPBENCH_OPS_PASSFAIL_LOG` (optional): the log, by default `pass-fail.log` in `%LOCALAPPDATA%/worldparts-bench/ops-build/`.

  `--truth`, `--truth-manifest`, `--bundles`, `--out` and `--no-manifest-check` are refused. Before any truth file is opened, the scored tasks' bundles are checked against the committed manifest; a refusal before that point logs nothing.
- **The pass/fail log** gets, per evaluation and under a file lock, a line `<time> session=<NAME> evaluation=<n> started` before anything is graded. Then it gets a line `<time> session=<NAME> <task_id>/r<k> PASS|FAIL` per realisation and `<time> session=<NAME> evaluation=<n> realisations=<N> passed=<P>`, or `<time> session=<NAME> evaluation=<n> failed`. An evaluation counts once started. A fourth one for the session is refused and logged as `<time> session=<NAME> refused: ...`, as is a `--session` other than the owner's.

## Build-phase inputs of the Stage 1 arms

- **The `code-skill` toolkit** is a folder, `$WPBENCH_OPS_TOOLKIT`. Every `code-skill` session gets a copy of its files, without `.git`, caches and `*.pyc`, as `reference/` in its working directory. No file, and no file name, may mention worldparts or anything else a contamination marker of the code arms flags. Every file is UTF-8 text, and the files have at most 800 lines in all, or the line count of worldparts' section-14 modules (`measurements`, `calibration`, `diagnosis`, and `scada` or `identifiability` if they exist) if that is larger. `run.json` records its manifest: the SHA-256 of each file and a digest over them, in the form of the bundle digest.
- **The quick reference** is `preambles/quick-reference.txt`, plain text. A line that starts with `#!` is a harness annotation and is never shown to the agent: `#! section 14` opens the part that documents design section 14 (at most 20 lines), and `#! end` closes it. The file has exactly one such part. A shown line has at most 120 characters. Section 3's limits are checked by `harness/quickref.py`. The section-14 names are the public names `worldparts/__init__.py` imports from the section-14 modules.
- **Preambles in `run.json`.** A run records the SHA-256 of every preamble file its arms use (`preambles`). A resumed run or a re-run refuses other texts. A readiness run refuses preamble files that differ from the commit, as it refuses a wheel built from changed sources.
