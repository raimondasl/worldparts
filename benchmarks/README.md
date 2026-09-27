# worldparts benchmarks

## Composition benchmark (milestone v0.2)

### What it measures and why

The project's thesis is that an AI agent gets physical systems right more often when it
**composes pre-built, tested components** through typed tools than when it writes the
physics itself. The research report behind worldparts
([docs/research/world-model-libraries-for-ai-agents.md](../docs/research/world-model-libraries-for-ai-agents.md))
found that LLM-written physical models usually run but are rarely right: GPT-4o's Modelica
loads 95.56 % of the time and is functionally correct only 24.44 % of the time (ModiGen;
36.90 % at best with fine-tuning, retrieval and feedback), and ten LLMs scored 0.0 on
simulation fidelity in a 2026 fluid-systems benchmark. It also found that **no benchmark
measures an agent composing pre-built components**. This benchmark does.

Each task is a water-engineering question (an operating point, a sizing, a what-if, a
transient, a fault diagnosis or an open design review, see [Judgement tasks](#judgement-tasks))
stated completely in numbers. The same prompt runs in two conditions (three with the
optional [lib condition](#the-lib-condition)), and the answers are graded against a
reference computed with worldparts.

**Decision gate** (report, section "What to do next"): if tool-based composition does not
clear **80 %** on level-1 tasks with a frontier model, the packaging thesis needs rethinking.
Every report prints this line: the level-1 pass rate of condition `mcp` versus 80 %.

### The conditions

| Condition | The agent has | The agent does not have |
|---|---|---|
| `mcp` | Only the worldparts MCP server's tools (list, describe, create, add, connect, solve, solve_for, simulate, ...) | Code execution, files, web, any other tool |
| `code` | A shell (Bash, and Read, Write, Edit in its working directory) whose `python` is an environment with numpy, scipy, fluids and wntr | worldparts, pip, the network (by instruction and configuration; see limitations) |
| `lib` (opt-in) | The same tools and permissions as `code`; the environment also has **worldparts installed as a package** (and its `worldparts` command on PATH), and the preamble carries a short quick reference of it | The repository, pip, the network |

`run` runs `mcp` and `code` unless `--conditions` says otherwise; `lib` is described in
[The lib condition](#the-lib-condition). All conditions get the same task prompt, the same
appended answer-format instruction, the default Claude Code system prompt plus a short
description of the condition's tools, and the same turn and time limits (which may differ
by level, see
[Scale tasks](#scale-tasks-level-4)). Because the `code` agent must be able to reach
the same answer by hand or in plain Python, every prompt gives every number with units and
states every convention that is not textbook-standard (for example how a filter's pressure
drop splits into a linear and a quadratic part, or that UV dose is fluence rate times
volume over flow).

### The lib condition

Benchmark v0.2 found that frontier agents writing Python from scratch matched agents using
the worldparts MCP tools on accuracy at a quarter to an eighth of the tokens, while Claude
Haiku gained 25 points of pass rate from the MCP tools. Coding agents prefer writing one
script to making many tool calls. The `lib` condition asks whether worldparts helps when it
is delivered as a **Python library** the agent imports in its script: code-level token cost
with tested components. It differs from `code` only in its environment and preamble, so
`lib` against `code` isolates the library. `lib` against `mcp` measures the delivery plus
some API differences: the Python API has no `solve_for` (the MCP server's search for the
input that gives a target result) and no per-call `units` mapping (the agent converts a
result with `r.get(path, unit=...)` instead).

- **Environment.** `lib-env` builds a cached environment in
  `%LOCALAPPDATA%/worldparts-bench/lib-env-<key>` (`--lib-env DIR` overrides it for `run`,
  `smoke` and `lib-env`): Python 3.12, numpy, scipy, fluids and wntr at the code
  environment's versions, worldparts' own runtime dependencies (pint, pyyaml, jsonschema,
  mcp, pydantic and theirs; the code environment does not have them), every package at
  the version the repository's environment has (a constraints file), and worldparts from
  a **wheel built from the repository the harness runs from** (`uv build --wheel` into
  `<env>/dist`), installed non-editable, so the agent's Python sees an ordinary installed
  package and nothing points back to the repository. A probe checks the imports, that
  worldparts is imported from the environment's site-packages, that it is not editable and
  that no `.pth` file puts its sources on the path; `worldparts --version` checks the
  command. `stamp.json` records the versions and a SHA-256 of the package sources
  (`src/worldparts`, `pyproject.toml`, `README.md`, `LICENSE`), and `<key>` is a hash of
  that stamp: a change of the sources or versions, or another checkout with other
  sources, gets a new directory, so building one never clears an environment that a
  running benchmark uses (old `lib-env-*` directories can be deleted when no run uses
  them). The stamp is removed before a rebuild and written last, so a half-built
  environment is never taken as current. The session environment is set up as for
  `code`: the environment's scripts directory first on PATH (so `python` and `worldparts`
  are its own), `VIRTUAL_ENV`, `PIP_NO_INDEX=1`, `UV_OFFLINE=1`.
- **Which worldparts a run measured.** `run.json` records the lib environment (`lib_env`:
  directory, key, worldparts version, source hash), and so does each lib session's
  `outcome.json` (and its record). Resuming a run (`--run-id`) with lib sessions refuses
  when the sources or versions have changed since the run started; the report flags lib
  runs from more than one lib environment.
- **Tools.** Exactly those of `code`: `--tools Bash,Read,Write,Edit` and the same
  `--allowedTools`.
- **Preamble.** The code paragraph, which also names the installed package worldparts and
  its command, followed by a 23-line quick reference (`LIB_CHEAT_SHEET_*` in
  `harness/runner.py`): `worldparts list`, `worldparts describe <component>`, and the
  Python API (`wp.System`, `add`, `connect`, `check`, `solve` and reading results,
  `set`, `simulate` with events, `add_control`, `to_dict`/`System.from_dict`,
  `variables`), with the unit conventions the MCP instructions also give (plain numbers
  in declared units, strings with units, gauge pressures, and flow units that differ by
  part: L/min for pipes, valves, drains and supplies, m3/h for pumps, tanks, filters and
  UV reactors). It describes the package and never tells the agent to use it. Its example
  system (a pump transferring water from tank `a` through pipe `link` and valve `v` into
  tank `b`) matches no task's layout or instance names, so it is no template for a task;
  a test checks this against every task's reference system.
  `tests/test_benchmark_harness_lib.py` runs every line of it against the package (the
  shell lines through `worldparts.cli.main`, the Python lines as one script), so the
  reference cannot drift from the API; an opt-in test (`WPBENCH_LIB_ENV_TEST=1`) builds a
  real lib environment and runs them with its own `python` and `worldparts`. The dry run
  prints the preamble of each session.
- **Contamination.** In `lib`, the word worldparts and reads of the installed package's
  own files (its site-packages directory, manifests, schemas, the metadata and README
  installed with it) are allowed. A look into the repository is still flagged, as in
  `code`: its path (absolute, or relative to the home directory, which catches `~/...`,
  `$HOME/...`, `%USERPROFILE%/...` and `../..` spellings; each as a whole name, so a
  sibling such as `worldparts-bench` does not count), `src/worldparts/`, `uv.lock`,
  `tests/fixtures/benchmark`, and a tool input naming the repository's GitHub address;
  so are the task directory and schema, the task's file name and frozen `expected:`
  answers. A test builds the wheel and checks that none of its files trips a marker.
- **Report.** Every table has a row or column per condition, in the order mcp, code, lib.
  The decision gate stays the level-1 pass rate of `mcp`; a run with `lib` sessions adds
  the line `Library versus code: level-1 pass rate lib X % (k/n, 95 % CI ...) versus code
  Y % (...): +Z points for lib; median tokens A (lib) versus B (code); on N task(s) with
  valid runs in both conditions (M left out: ...).` (`lib_vs_code` in `summary.json`).
  The rates are paired by task: contamination and infrastructure errors can remove a
  task from one condition only, and such a task is left out rather than compared with a
  different task set (on each task, runs with the same repeat number are paired first,
  then the rest, as many as the smaller side has; all valid runs are kept under
  `all_runs`).

### Task format

One YAML file per task in `composition/tasks/<id>.yaml`, validated against
[composition/task.schema.json](composition/task.schema.json):

```yaml
id: pump-lift-01              # kebab-case, equals the file name
title: Rooftop lift operating point
level: 1                      # 1: 2-4 components, 2: 5-7, 3: 8-19, 4: 20 or more (reference system)
category: operating_point     # operating_point | sizing | what_if | transient | diagnosis | judgement
domain: pumping               # pumping | storage | treatment | distribution
prompt: |
  Fully specified task text: every number with units, every non-standard convention.
  No worldparts names, component, parameter or warning identifiers, no answer format.
answers:
  - {key: flow, description: Pump flow, unit: m3/h, rel_tol: 0.03}
  - {key: fault, description: Which fault, kind: choice, choices: [clogged_filter, worn_pump]}
  - {key: meets_dose, description: Dose met, kind: boolean}
reference:
  system: {worldparts_system: "0.1", components: [...], connections: [...]}
  steps:
    - {op: solve, read: {flow: pump.volume_flow}}
    - {op: set, values: {pump.speed: 0.9}}
    - {op: solve_for, target: pump.volume_flow, value: "15 m3/h", vary: pump.speed,
       lower: 0.3, upper: 1.2, read: {speed: pump.speed}}
    - {op: simulate, duration: 2 h, step: 10 s, events: [],
       read_final: {level: tank.level},
       read_first_warning: {empty_time: {warning: tank.tank_empty, unit: min}}}
    - {op: answer, values: {fault: clogged_filter, meets_dose: true}}
  expected: {flow: 18.65, fault: clogged_filter, meets_dose: true}   # written by regen
notes: Hand calculation and the reason for any loose tolerance.
```

- Ops run in order on one system. `solve_for` has exactly the MCP tool's semantics
  (Brent's method; the varied value stays at the root). `simulate` also takes ramp events
  (`{at, ramp: {path: [start, end]}, over}`), `restore`, `read_min`, `read_max` and
  `read_total`: `{pumped: pipe.volume_flow}` or `{energy: [p1.shaft_power,
  p2.shaft_power]}` is the time integral of a variable (or the sum of several) over the
  run, `sum(v[k] * (t[k+1] - t[k]))`: each sample's value holds over the step it drives,
  as in the explicit-Euler tank update, so a flow into a tank totals exactly the volume the
  tank receives. The answer unit must be the variable's unit times a time (m3/h to m3, kW
  to kWh); pressures and temperatures are rejected. No agent tool returns a total: the MCP
  `simulate` tool gives the minimum, maximum and final value of each series and the series
  itself, downsampled to `max_points` (up to 10,000, so a 24 h run at 60 s can come back
  whole). An MCP agent reaches a total from tank balances (final depths) and switch times,
  or from the returned series; tasks that ask for totals give them a 5 % tolerance, and
  their notes show that the balance route lands within about 1 %.
- Every read is converted to its answer's unit. Every answer key is produced by exactly one
  read or `answer` op; `expected` must equal what the steps produce (1e-6 relative).
- Tolerances: 3 % relative for steady flows, heads and pressures (friction-factor
  formulas differ by up to about 1 %, quadratic versus interpolated pump curves by a
  little more); 5 % for transient times and sensitive sizing results. A number passes when
  `|a - e| <= max(rel_tol * |e|, abs_tol)`.
- The loader also checks the level against the component count, choice and boolean values,
  and that the prompt names no answer format and that neither the prompt nor the appended
  answer-format line (answer keys, descriptions, choices) names a worldparts identifier. An
  answer key may not even contain one as a part (`npsh_available_20c` is rejected), so keys
  are plain engineering names such as `pump_power`, `pump_speed`, `npshr`.
- A `judgement` task has its own rules, listed in the next section; a level-4 task has
  its own prompt rules, listed in [Scale tasks](#scale-tasks-level-4).

### Judgement tasks

A pilot run showed that on fully specified calculations a frontier model writing Python
from scratch is as accurate as an agent using worldparts, and cheaper. What worldparts
claims to add is judgement: every component carries its validity envelope and warnings, so
an agent composing with it is told about cavitation, motor overload, operation far from
the best-efficiency point or UV under-dose, while an agent writing its own physics must
remember to check. The `judgement` category measures that. The prompt gives datasheet-level
data (curves, motor rating, water temperature and vapour pressure, pipe data, tank
dimensions and duty period, UV fluence rate, volume and required dose, filter change-out
drop) and ends with an open question ("Review this design. Is it acceptable for continuous
duty as specified, and what is its primary problem, if any?"). It never names the problem
or lists checks to perform, and its magnitudes make the answer clear-cut: exactly one
problem, well past any reasonable threshold, with every other candidate clearly fine (or
none at all, sometimes with a tempting false alarm such as a pump at 110 % of its
best-efficiency flow).

The loader enforces, for `category: judgement`:

- exactly two answers, in this order: `acceptable` (boolean: is the design acceptable for
  continuous duty as specified) and `primary_problem` (choice) whose choices are exactly,
  in this order, `none, cavitation, motor_overload, pump_far_from_best_efficiency,
  pump_beyond_end_of_curve, pump_below_minimum_flow, insufficient_uv_dose,
  filter_needs_cleaning, excessive_pipe_velocity, tank_runs_dry, tank_overflows,
  insufficient_delivery_pressure` (`JUDGEMENT_CHOICES` in `harness/tasks.py`). The list is
  the same for every judgement task, so it never reveals which problem a task has; it is
  exempt from the identifier rule (`motor_overload` is also a warning code);
- the last reference step is an `answer` op giving both answers, and `acceptable` is true
  exactly when `primary_problem` is `none` (in that op and in `expected`);
- the prompt has 100-250 words and contains none of the choices (underscores read as
  spaces) and none of a list of hint phrases (`cavitat`, `overload`, `best-efficiency`,
  `run dry`, `velocity`, `margin`, `verify`, ... : `JUDGEMENT_HINTS`).

The reference system reproduces the situation in worldparts; the verdict itself is stated
by the `answer` op (a simulate step without reads may precede it for a duty-period
transient). `tests/test_benchmark_tasks_judge.py` solves every judgement reference (or
simulates it, for a tank that runs dry within the duty period) and checks that it raises
the warning of the primary problem (`cavitation`, `motor_overload`,
`outside_preferred_region`, `underdose`, `high_velocity`, `drawing_air`/`tank_empty`, ...)
and no warning-level result that belongs to another choice; a `none` task raises no
warning and not the continuous-duty info `outside_preferred_region`. Each task's notes
give the independent hand check of the problem and of why the other candidates are fine.

| Task | Level | Domain | Primary problem | Key numbers |
|---|---|---|---|---|
| judge-01 | 1 | pumping | none | 44.7 m3/h at 111 % of best efficiency (40.3 m3/h); NPSHa 12.2 m vs 3.3 m; 4.57 of 5.5 kW; 1.51 m/s |
| judge-02 | 2 | pumping | cavitation | 7.0 m suction lift, 30 degC: NPSHa 2.47 m vs NPSHr 4.17 m at 39.7 m3/h (105 % BEP) |
| judge-03 | 1 | pumping | motor_overload | 9.01 kW shaft power on a 5.5 kW motor (164 %) at 52.0 m3/h (97 % BEP) |
| judge-04 | 2 | pumping | pump_far_from_best_efficiency | throttled to 12.7 m3/h = 38 % of the 33.5 m3/h best-efficiency flow (efficiency 50 % vs 72 %), about half the lower end of the datasheet's 24-40 m3/h recommended operating range; minimum flow 5 m3/h |
| judge-05 | 2 | treatment | insufficient_uv_dose | 20 mW/cm2 x 12 L at 36.2 m3/h = 23.9 mJ/cm2 vs 40 required |
| judge-06 | 3 | storage | tank_runs_dry | pump 39.3 m3/h, feed 26.9 m3/h, 32.7 m3 stored: drawn empty after about 3.5 h of an 8 h shift |
| judge-07 | 1 | distribution | excessive_pipe_velocity | 37.4 m3/h in a 52.5 mm riser: 4.8 m/s |
| judge-08 | 3 | treatment | none | 34.9 m3/h at 98 % BEP; dose 46.5 vs 40 mJ/cm2 (fluence rate at end of lamp life); part-loaded filter 0.51 vs 1.0 bar |

Authoring loop:

```sh
uv run python -m benchmarks.composition.harness validate          # schema and rules
uv run python -m benchmarks.composition.harness.regen             # freeze expected
uv run python -m benchmarks.composition.harness.regen --check     # CI: fail on drift
uv run python -m benchmarks.composition.harness oracle            # must be 100 %
uv run python -m benchmarks.composition.harness oracle --corrupt  # must be 0 %
```

`regen` rewrites only the `expected:` line of each file, so comments survive.
`tests/test_benchmark_harness.py` runs the same checks on every task file.

### Scale tasks (level 4)

Benchmark v0.2 found that on small, fully specified tasks (at most 11 components) a
frontier model writing Python from scratch was as accurate as an agent using worldparts and
several times cheaper ([results](../docs/benchmark-results-v0.2.md)). Level 4 tests the
**scale** hypothesis: with 20 to 80 components, looped networks, tanks, controls and
24-hour simulations, an agent writing its own model (in plain Python or with WNTR, which
the code condition also has) has much more to build and debug, while a worldparts agent
loads or builds one system document. The tasks stay fully specified and fair: a careful
engineer with Python and WNTR must be able to reach every answer within tolerance.

A task is level 4 when its reference system has **20 or more components** (controls are
not components). The loader enforces, for `level: 4`:

- the prompt has at most **1,200 words** (`SCALE_PROMPT_WORDS` in `harness/tasks.py`).
  Table markup is not counted: delimiter rows are skipped and `|` only separates cells,
  so a table counts the words in its cells (`prompt_word_count`);
- the prompt may give data in **markdown tables** (nodes, pipes, outlets, pumps,
  schedules). Every table must be well formed: a header row, a delimiter row such as
  `|---|---:|` and at least one body row; every row starts and ends with `|` and has as
  many cells as the header; no cell is empty, because an empty cell is an unstated value
  (write `0` or `none`);
- every other prompt rule is unchanged: no worldparts identifier (in prose or in a table
  cell), no JSON or fenced code, no answer-format text;
- a level-4 `judgement` task keeps every judgement rule except the length: its prompt has
  100-1,200 words instead of 100-250.

Prompts of levels 1-3 keep their v0.2 rules (judgement prompts 100-250 words; the pumping
and treatment task tests also hold their prompts to 80-250 words) and contain no table.

**Session limits.** The defaults stay `--max-turns 80` and `--timeout 1800` for every
level. Building and simulating a large network takes more turns and time, so `run` can set
the limits per level:

- `--level-max-turns LEVEL=N` and `--level-timeout LEVEL=SECONDS` (several values, comma
  or space separated, e.g. `--level-timeout 4=2400 3=2000`) override `--max-turns` and
  `--timeout` for tasks of that level;
- `--recommended-level-limits` applies the table `RECOMMENDED_LEVEL_LIMITS` in
  `harness/runner.py`:

| Level | Max turns | Timeout | Why |
|---|---:|---:|---|
| 1-3 | 80 (default) | 1800 s (default) | v0.2 sessions took a median of 2-5 turns and 15-19 s, with no timeouts |
| 4 | **120** | **2400 s** | 20-80 components and 24-hour simulations: a from-scratch agent builds, runs and debugs a network model; a worldparts agent adds many components one call at a time |

Precedence per limit: a `--level-*` value, then the recommended table (only with the
flag), then `--max-turns`/`--timeout`. Both conditions always get the same limits. The
dry run prints each session's limits; `run.json` records the limits per level and each
run's `outcome.json` the limits it ran with. Timeouts and turn limits count as failures, so
a level-4 run should use the recommended limits (and a `--max-budget-usd` cap) and the
report's per-level table shows how many sessions hit them.

`tests/test_benchmark_harness_scale.py` tests these rules, the limits and the report on a
20-component fixture built in the test (the lift fixture with its riser split into 17
segments given as a table), which also runs through the reference executor, regen and the
oracle.

**Scale tasks.** Each prompt gives every number, the time step and the exact control
semantics (when a switch or a PI loop samples, and that its command takes effect at that
step), and says that outlets are orifices discharging to atmosphere. Answers avoid
switch thresholds and switch counts: steady flows and pressures (3 %), totals over the day,
tank-level extremes that fall between thresholds and first crossings with a clear margin
(5 %). Each task's notes give an independent check without worldparts: an own Python
network model (Colebrook and Swamee-Jain friction, time steps from 15 s to 300 s) and,
where EPANET can represent the plant, WNTR's EpanetSimulator; every task was also audited
by a second, independent model built from the prompt alone.
`tests/test_benchmark_tasks_scale_scale-plant.py` reruns the scale-plant references, solves
the steady task independently and checks the tanks' volume balances;
`tests/test_benchmark_tasks_scale_scale-net.py` reruns the scale-net references and checks
the zone answer, the mass balances and the switching sequences and their margins.

| Task | Components | Category | Answers | Tolerance | Largest independent difference |
|---|---:|---|---|---|---:|
| scale-plant-steady-01 | 22 | operating_point | three train flows, three UV doses, total flow, collector pressure | 3 % | 0.02 % |
| scale-plant-clog-01 | 27 | transient | trim-pump speed at 0 h; first filter to reach its backwash drop and when; total flow, train-B flow and train-A dose at 24 h | 3 %, time 5 %, choice exact | 0.1 % |
| scale-plant-tower-01 | 22 | transient | pump flow and node pressure at 0 h; lowest and highest clearwell depth; pumped and delivered volume; pump energy | 3 % (0 h), 5 % | 0.7 % (1.3 % at a 300 s step) |
| scale-plant-transfer-01 | 48 | transient | intake pump flow at 0 h; volume pumped by each of three stages; lowest clearwell depth | 3 % (0 h), 5 % | 0.3 % (0.8 % at a 300 s step) |
| scale-net-irrigation-01 | 57 | operating_point | pump flow; mainline pressure; one zone's flow; lowest sprinkler pressure; the zone that holds it | 3 %, choice exact | 0.17 % |
| scale-net-loop-01 | 40 | operating_point | flow from each of two reservoirs; two node pressures; one pipe flow | 3 % | 0.2 % |
| scale-net-tower-01 | 26 | transient | lowest tower level; tower and reservoir levels at 24 h; lowest outlet pressure; highest pump flow | 5 % (tower), 0.2 m (reservoir), 3 % | 0.6 % (7.7 % at a 300 s step, not the stated 60 s) |
| scale-net-booster-01 | 30 | transient | highest and lowest header pressure; lowest pressure at one node; highest station flow; reservoir level at 24 h | 3 %, 0.1 m (reservoir) | 0.24 % |

### How to run

All commands run from the repository root. Real runs call the Claude Code CLI and consume
paid usage; start with a dry run.

```sh
# Print the exact claude command lines, environment changes and prompts; run nothing.
uv run python -m benchmarks.composition.harness run --dry-run --model opus --tasks pump-lift-01

# One-time: build the code condition's Python environment (outside the repository).
uv run python -m benchmarks.composition.harness code-env

# The lib condition's environment (worldparts from a wheel of this repository); `run`
# also builds or refreshes it when a selected condition is lib.
uv run python -m benchmarks.composition.harness lib-env

# lib against code and mcp: dry run (prints each session's preamble), then level 1.
uv run python -m benchmarks.composition.harness run --dry-run --model opus \
    --tasks pump-lift-01 --conditions mcp code lib
uv run python -m benchmarks.composition.harness run --model opus --levels 1 \
    --conditions mcp code lib --repeats 3 --max-budget-usd 2

# A real run: level-1 tasks, both conditions, three repeats each.
uv run python -m benchmarks.composition.harness run --model opus --levels 1 --repeats 3

# Selected tasks (ids or glob patterns), one condition, a spending cap per session.
uv run python -m benchmarks.composition.harness run --model sonnet --tasks "pump-*" \
    --conditions mcp --repeats 1 --max-budget-usd 2 --max-turns 80 --timeout 1800

# Scale tasks (level 4) with the recommended limits (120 turns, 2400 s per session).
uv run python -m benchmarks.composition.harness run --dry-run --model opus --levels 4 \
    --recommended-level-limits
uv run python -m benchmarks.composition.harness run --model opus --levels 4 --repeats 3 \
    --recommended-level-limits --max-budget-usd 10

# All levels; only level-4 sessions get the longer limits (explicit values win).
uv run python -m benchmarks.composition.harness run --model opus --repeats 3 \
    --level-max-turns 4=120 --level-timeout 4=2400

# Re-grade saved streams (after a tolerance fix) and rebuild the report.
uv run python -m benchmarks.composition.harness grade <run-id>
uv run python -m benchmarks.composition.harness report <run-id> --print

# Pilot: three tasks (one per level) in both conditions, one repeat, capped spend.
uv run python -m benchmarks.composition.harness run --dry-run --model opus \
    --tasks pump-lift-01 treat-skid-duty-01 pump-station-01 --conditions mcp code
uv run python -m benchmarks.composition.harness run --model opus --run-id pilot-1 \
    --tasks pump-lift-01 treat-skid-duty-01 pump-station-01 --conditions mcp code \
    --repeats 1 --max-turns 60 --max-budget-usd 2 --timeout 1800
uv run python -m benchmarks.composition.harness report pilot-1 --print

# Plumbing check with a raw prompt (no task).
uv run python -m benchmarks.composition.harness smoke --condition mcp --model haiku \
    --max-turns 4 --prompt 'Call list_components once and reply with the number of components as JSON {"n": N}.'
```

The CLI must be logged in (`claude` then `/login`) or `ANTHROPIC_API_KEY` must be set. A run
stops at the first authentication failure; rerunning with the same `--run-id` keeps the
graded sessions and redoes the rest. `--jobs N` runs sessions in parallel.

Each session runs in a fresh temporary directory outside the repository and is saved under
`composition/results/<run-id>/<task>/<condition>-<repeat>/`: `prompt.txt`,
`command.json` (argv, environment changes, MCP config), `stream.jsonl` (the raw
`stream-json` transcript), `stderr.txt`, `final.txt`, `usage.json` (tokens, cost, turns,
duration, tool calls), `outcome.json` (exit code, timeout, wall time), `grade.json`,
`record.json`, `systems/` (every system the MCP server autosaved) and `workdir/` (the
files the agent wrote). `summary.md` and `summary.json` sit in the run directory.

### How the command isolates a session

```text
claude -p --output-format stream-json --verbose --model M --max-turns N
  --setting-sources "" --strict-mcp-config --mcp-config <tmp>/mcp.json
  --settings <tmp>/settings.json --permission-mode dontAsk --disable-slash-commands
  --no-session-persistence --no-chrome [--max-budget-usd X]
  --append-system-prompt <condition paragraph>
  mcp:  --tools "" --allowedTools mcp__worldparts
  code: --tools Bash,Read,Write,Edit --allowedTools Bash Read Write Edit
  lib:  --tools Bash,Read,Write,Edit --allowedTools Bash Read Write Edit
```

(A `Bash(python *)` pattern denied ordinary commands such as `sed ...; python s.py` and
made agents give up, which measured the harness rather than the agent, so the code and lib
sessions are unrestricted within the session; grading's contamination check catches reads
of the benchmark.)

The prompt goes to stdin. `--setting-sources ""` loads no user, project or local settings
(so no hooks, permission rules or plugins); `settings.json` only sets `disableAllHooks`;
`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` keep CLAUDE.md
files and memory out; `--disable-slash-commands` disables skills; `--strict-mcp-config`
loads only the worldparts server (`uv run --directory <repo> worldparts mcp` with
`WORLDPARTS_AUTOSAVE_DIR=<tmp>/systems`) or, for `code` and `lib`, no server; `dontAsk` denies every
tool call that the allow rules do not cover; `ENABLE_TOOL_SEARCH=false` loads all tool
schemas up front. The parent's `CLAUDE*`/`ANTHROPIC_*` variables (except credentials) and
the repository's virtual environment are removed from the CLI's environment; for `code`,
`python` on PATH is a separate environment (`%LOCALAPPDATA%/worldparts-bench/code-env`)
with the same numpy, scipy, fluids and wntr versions as the repository and no worldparts,
no pip, `PIP_NO_INDEX=1` and `UV_OFFLINE=1`; for `lib`, `python` and `worldparts` come from
`%LOCALAPPDATA%/worldparts-bench/lib-env-<key>`, the same set-up plus worldparts installed
from a wheel (see [The lib condition](#the-lib-condition)). A multi-line preamble (lib)
cannot pass through a `.cmd` or `.bat` wrapper of the CLI; the harness refuses one before
any session starts (set `WPBENCH_CLAUDE` to the executable).

### How results are graded

- The final reply must end with one JSON object in a fenced `json` block with exactly the
  requested keys. The parser tolerates, and records as format issues, an untagged block, a
  bare object, trailing commas, comments, Python literals, numbers given as strings (with
  or without a unit), keys in another case or with spaces, and extra keys. Missing or
  unparseable answers fail.
- Numbers pass within the task's tolerance, choices on an exact match (case and spaces
  normalised), booleans on the same value. **A task passes only if every answer passes.**
- **Traceability**: a numeric answer is traceable when its value is within 0.5 % of a
  number in some tool result of the session (an MCP result, or the output of the agent's
  own Python). Reported per condition, next to accuracy, as a diagnostic only: some
  correct answers need no tool at all (a prompt constant such as a required dose the design
  just meets, or a one-line closed form of prompt numbers). Answers equal to a prompt number
  are marked `in_prompt` and counted separately; the column "correct, untraceable, not in
  prompt" is the one worth reading.
- **Contamination**: every tool input and result is scanned for the benchmark itself: the
  task directory or schema (`benchmarks/composition`, `composition/tasks`,
  `task.schema.json`), the task's own file name, a frozen `expected: {...}` mapping, in
  the code and lib conditions the repository (its path, also home-relative, its
  `src/worldparts/`, `uv.lock` and test fixtures, and its GitHub address in a tool input),
  and in the code condition only the word worldparts (the code environment does not
  contain it; its `worldparts-bench` directory name does not count). In the lib condition
  the word and the installed package's own files (site-packages, manifests, the metadata
  and docs installed with it) are allowed.
  A contaminated run is left out of every rate and listed with its reasons at the end of
  the report; the run command prints `CONTAMINATED` for it.
- Runs that fail for infrastructure reasons (CLI not logged in, CLI could not start, API
  errors before any token) are excluded from rates and listed separately. Timeouts and
  turn limits count as failures.
- The report gives pass rates by condition, level, category and domain (with 95 % Wilson
  intervals), per-answer accuracy, traceability, median tool calls, turns, tokens, cost
  and duration, total cost, a per-task table and the decision-gate line. Levels are shown
  with their component ranges (`4 (20+ components)`), and an "Effort and cost by level"
  table gives, per level and condition, the pass rate, median turns, tool calls, tokens,
  cost and duration, timeouts, turn-limit hits (`error_max_turns`) and the session limits
  the runs used, so level-4 cost and failures can be read against levels 1-3. The decision
  gate stays the level-1 pass rate of condition `mcp`. Conditions appear in the order mcp,
  code, lib; a run with lib sessions also prints the level-1 lib-versus-code line.

### Known limitations

- **The code and lib conditions are restricted, not sandboxed.** The agent works in a
  temporary directory, but its shell and Python can read any file the user can (including
  this repository and the frozen answers) and open network connections. Claude Code's own
  sandbox is not available on Windows. The code prompt never names worldparts (the lib
  prompt names only the installed package); runs whose tool traffic touches the benchmark
  are detected and excluded (see Contamination), and the saved transcripts show what each
  run did. Detection is by markers, so a deliberately disguised read would pass.
- **The lib environment is the repository's current package.** A new one is built from
  the working tree whenever the package sources change, so a lib run measures the
  worldparts that the harness's repository holds when the run starts (`run.json` and each
  lib session's outcome record the source hash; compare runs only across the same hash).
- **The lib package's metadata talks about this benchmark.** The wheel's METADATA carries
  the repository README, which states the v0.2 result (agents writing Python were as
  accurate as agents using worldparts, and cheaper) and the GitHub address, where the
  tasks and frozen answers are public if the repository is. A lib agent that opens the
  metadata (`pip show`, `importlib.metadata`) reads a statement about the choice it is
  making. This is accepted: the preamble already names the package, and a fetch of the
  repository needs the network, which the preamble says is unavailable; a tool input
  naming the GitHub address is flagged as contamination.
- **Tank-connection defaults (MCP condition).** A worldparts tank's nozzle defaults to
  Kv 200 m3/h, which is restrictive for DN150 and larger connections. The task prompts
  state that tank connections are hydrostatic, lossless or already inside the stated K,
  and the reference systems set Kv 1e6 there. An MCP agent that leaves the default in
  place fails `treat-check-backflow-01` (fill flow -5 %), `treat-parallel-uv-01` (power
  +3.7 %) and `treat-tower-drain-01` (the tower never reaches its low level), and only just
  passes `treat-diagnose-train-01`. This tests reading the specification into parameters,
  and it is fair to the code condition; read MCP failures on these tasks with it in mind.
- **Same-project tasks.** The tasks, the reference answers and the component models come
  from the same project. The reference answers are worldparts' own; a `code` agent that
  uses a different but sound method is protected only by the tolerances, which are set
  from the known differences (friction-factor formulas, curve fits, time steps). Each
  task's notes give a hand calculation.
- **Single-run variance.** Agent sessions are stochastic. Use `--repeats` of 3 or more for
  any claim and read the confidence intervals; one run per task is a smoke test.
- **Traceability is a heuristic.** Tool results echo inputs, so a value copied from the
  prompt can look traceable; a value converted by hand from a tool result (L/min to m3/h)
  looks untraceable.
- The MCP condition also has the optional `export_system` and `compare_with_wntr` tools
  when wntr is installed (it is, as a development dependency).
- Cost and token figures come from the CLI's `result` message; they are what the CLI
  reports, not an invoice.

## Operations benchmark (v0.3)

The protocol is [operations/PREREGISTRATION.md](operations/PREREGISTRATION.md); the bundle,
task and truth formats are in [operations/INTERFACE.md](operations/INTERFACE.md). The
harness is `benchmarks/operations/harness/`. It reuses the v0.2 session isolation,
environment scrubbing, stream and final-JSON parsers and contamination markers. The
section-9 outcome rule and the operating characteristics are in
`benchmarks/operations/analysis/` (see the [power tables](operations/analysis/power-tables.md)).

### Locations

- **Bundles**: `$WPBENCH_OPS_BUNDLES/<set>/<task>/r<k>/`, by default
  `C:/Users/raimo/world-model/opsbench-bundles`. A run checks them against the committed
  `operations/bundles-<set>.sha256` first.
- **Truth**: `$WPBENCH_OPS_TRUTH/<set>/<task>.truth.json`. Only `grade` and `headroom` read
  it, and no `WPBENCH*` variable reaches a session.
- **Runs**: `operations/results/<run>/`, which is not in git. It holds:
  - `run.json`;
  - `index.json`, which maps each session id to its model, task, arm and realisation;
  - `sessions/<id>/attempt-<n>/`: the prompt, command, stream, stderr, outcome and the
    files the agent wrote;
  - `sessions/<id>/record.json`, `audit.json` and `summary.md`.

### Stage 0

```sh
# Print the exact command lines, environment and prompts; run nothing.
uv run python -m benchmarks.operations.harness run --set dev --arms code+ code-hint \
    --realisation 1 --dry-run
# Build the code-plus environment (numpy, scipy, pandas, statsmodels, scikit-learn, lmfit,
# fluids, wntr, matplotlib; no worldparts), keyed by a hash of its versions.
uv run python -m benchmarks.operations.harness code-env
# The 64 sessions (paid: the owner approves the projected cost first), one at a time, with
# the pinned CLI (WPBENCH_CLAUDE) and the models' explicit ids (the defaults); FREEZES.md
# records every setting. Tasks whose realisation 1 is replaced or moves re-run in a new run.
uv run python -m benchmarks.operations.harness run --set dev --jobs 1 \
    --out benchmarks/operations/results/stage0
# The blind audit (opaque session ids; no arms, no grades), re-runs of the flagged
# sessions (same realisation, at most twice), then grading and the headroom rule.
uv run python -m benchmarks.operations.harness audit benchmarks/operations/results/stage0
uv run python -m benchmarks.operations.harness run --out benchmarks/operations/results/stage0 \
    --rerun-flagged
uv run python -m benchmarks.operations.harness grade benchmarks/operations/results/stage0
# Every Stage 0 run directory (replacements and moved realisations run in new ones);
# --final once Stage 0 has ended (a pending verdict then counts as open).
uv run python -m benchmarks.operations.harness headroom benchmarks/operations/results/stage0*
# The bundle manifest (freeze-0a and freeze-0b), and its check.
uv run python -m benchmarks.operations.harness manifest --set dev
uv run python -m benchmarks.operations.harness manifest --set dev --check
```

- **Arms.** Stage 0 runs `code+` and `code-hint`:
  - tools Bash, Read, Write and Edit, with `--max-turns 120` and 2,400 s per session;
  - the prompt is `task.md`, then the arm's preamble, then the answer-format instruction;
  - the preamble is `operations/preambles/environment.txt`, and `code-hint` adds the
    verbatim methods checklist, `checklist.txt`.

  `code-skill`, `lib`, `lib-directed` and `mcp-hybrid` are registered in `harness/arms.py`,
  but they refuse to run before freeze-1, except in a readiness run (`run --readiness`,
  see [Build phase](#build-phase-after-stage-0)); `mcp-hybrid` has no preamble yet.
- **Shell.** Sessions run with a private copy of Git Bash whose `/tmp` is a benchmark-only
  folder, emptied around every session (`harness/session_bash.py`), so they run one at a
  time (`--jobs 1`).
- **Sessions.** Sessions of the same task never run at the same time (`--jobs` runs
  different tasks in parallel), so no session can read another arm's work on its task. A
  tool input that names another session's temporary directory, or a tool result that
  shows a path inside one, counts as contamination. The session environment has no
  `WPBENCH*`, `PWD`, `OLDPWD` or `INIT_CWD` variable, and no variable whose value names
  the repository, the bundle folder or the truth folder.
- **Infrastructure errors** (`harness/infra.py`). The pre-registered signatures are
  `harness_interrupted`, `harness_start`, `authentication`, `mcp_not_connected`,
  `no_model_turn`, `api_or_cli_error`, `permission_denied` and `killed`. They are read from
  `stream.jsonl`, `stderr.txt` and `outcome.json` only.
  - These count as failures instead: a timeout after a model turn, the turn limit, the
    budget cap, a context overflow and an exception in the agent's code. An earlier 401
    retry or permission denial that the session got past does not change that.
  - A flagged session is re-run at most twice and never excluded.
  - `run` stops launching sessions after a harness start failure, an authentication
    failure, or an API error before any model turn (a usage limit, a rate limit or an
    outage). Resume with the same `--out` once the cause is fixed or the limit has reset.
- **Grading** (`harness/grading.py`) covers every answer kind and the diagnosis table of
  section 6.6. `run` never grades, and `grade` runs the audit first.
  - The final reply is the `result` of a success result message. A session that ended
    without one (a timeout or a kill before the result, the turn limit, the budget cap, an
    error) fails, whatever it wrote earlier.
  - The answer is the last `json` block of the final reply (without one, the last untagged
    block, then the last block with another tag, then the last bare object). If that block
    is not a JSON object, the reply has no answer; an earlier draft is never graded.
- **Records are for the owner only.** A record holds no truth value, but the truth of a
  numeric key can be worked out from the answer, its error and its interval score. A
  firewalled session (section 7) gets only pass or fail: `grade RUN --pass-fail`, which
  prints pass or fail per session, writes no record and logs each evaluation in
  `<run>/pass-fail.log`, or, in the build phase, the wrapper of `score DIR --pass-fail`
  (see [Build phase](#build-phase-after-stage-0)).
- **Cost per pass.** The CLI reports a cost only in its result message. A timed-out or
  killed session is counted at its arm's cost per wall-clock second times its wall-clock
  time, and the number of such estimates is reported.
- **Headroom** (`harness/headroom.py`) prints the rule verbatim with F(Sonnet 5) and
  F(Opus 5.5). It refuses to compute while any of these holds:
  - a re-run is pending;
  - a code-hint session is missing;
  - more than 5 % of an arm's sessions are contaminated;
  - a task's truth has no realisation-1 reference result. The loader also refuses a truth
    file whose realisations are not the bundle's r1 to rK, each with `oracle_pass` true.
- **Power**: `uv run python -m benchmarks.operations.analysis.power --write` reproduces the
  operating characteristics of section 8 and writes `analysis/power-tables.md`.

### Build phase (after Stage 0)

The room is open, so section 7's build phase runs under the protocol of
[FREEZES.md](operations/FREEZES.md) ("Build phase after Stage 0"): track A finishes
worldparts and writes the quick reference, track B writes the `code-skill` toolkit, and the
orchestrating session writes neither. The harness supports it with these commands.

```sh
# The owner: score scripted answers (every realisation of every validated task); writes
# every grade to FILE, outside DIR. Never show its output to a builder.
uv run python -m benchmarks.operations.harness score DIR --set dev --out FILE
# A builder: pass or fail only, at most 3 logged evaluations per session. The owner sets
# WPBENCH_OPS_PASSFAIL_TRUTH and WPBENCH_OPS_SESSION when launching the session (and not
# WPBENCH_OPS_TRUTH); the wrapper reads them itself.
uv run python tools/ops_passfail.py DIR
# The owner: audit builder transcripts (a session's .jsonl and its subagents' .jsonl).
uv run python -m benchmarks.operations.harness firewall-audit SESSION.jsonl \
    SESSION/subagents/*.jsonl [--truth T] [--forbid PATH ...] [--json OUT]
# The lib environment (code-plus plus a worldparts wheel), then the readiness runs of
# section 3 (paid: the owner approves them first; dry run first).
uv run python -m benchmarks.operations.harness lib-env
uv run python -m benchmarks.operations.harness run --set dev --readiness --dry-run
uv run python -m benchmarks.operations.harness run --set dev --readiness --jobs 1 \
    --out benchmarks/operations/results/readiness
```

- **Scripted answers** (`harness/score.py`, INTERFACE.md "Scripted answers"). `DIR` holds
  `<task_id>/r<k>.json`, one answer object per task realisation in the answer format of the
  prompt. Each file is graded exactly as a session's final reply: its text is the body of
  a fenced `json` block, parsed and graded by `grading.grade_answer`. A missing file, one
  that is not a JSON object, and one the grader cannot grade fail. Every realisation of the
  bundle (r1 to r3 for a validated task) is scored, for the tasks whose truth file is listed
  in `truth-dev.sha256`, and only on the committed bundles (`bundles-dev.sha256`); the
  output says how many realisations were scored. Nothing is written to `DIR`.
  - *Full mode* (the owner only) prints each task's realisations with their failing keys,
    the realisation pass rate (one decimal, never rounded up) and whether section 7's
    readiness threshold is met ("MET" or "NOT MET", with the passes needed), and writes
    every grade to `--out FILE` outside `DIR`. Like a session record, it lets a numeric
    key's truth be worked out.
  - *Pass/fail mode* (`--pass-fail`) prints only PASS or FAIL per task realisation and the
    number of passes. The truth folder (`WPBENCH_OPS_PASSFAIL_TRUTH`, which the owner's
    commands never read), the session's name (`WPBENCH_OPS_SESSION`) and the log
    (`WPBENCH_OPS_PASSFAIL_LOG`, by default `%LOCALAPPDATA%/worldparts-bench/ops-build/
    pass-fail.log`) come from the owner; `--truth`, `--truth-manifest`, `--bundles`,
    `--out` and `--no-manifest-check` are refused. The scored bundles are checked against
    the committed manifest before any truth file is opened. Each evaluation is logged under
    a file lock before anything is graded, so a failed one counts too, and a fourth is
    refused. Its errors never name the truth folder or a truth value.
- **Builder wrapper** (`tools/ops_passfail.py DIR [--session NAME]`) runs `score DIR --set
  dev --pass-fail` and takes no truth location. Builders never read, set or print the
  `WPBENCH_OPS_*` variables (the bundle folder aside), the truth folder, the private
  folder, `reports/`, `research_notes/`, the Claude memory folder, the owner's
  `%LOCALAPPDATA%/worldparts-bench`, any `record.json`, `summary.*`, `headroom*.json`,
  `score.json` or `pass-fail.log`, or the draft history of PREREGISTRATION.md; they never
  run the owner's commands or change the harness. `rg`, the `Grep` tool and `git grep`
  skip the git-ignored run folders; `grep -r` over the repository root reads them.
- **Firewall audit** (`harness/firewall.py`; its module docstring lists every rule). It
  scans the input of every tool call in the transcripts: shell commands (`Bash`,
  `PowerShell`), the paths of `Read`, `Write`, `Edit`, `Grep`, `Glob` and `LS`, the text
  the writing tools write (a script is scanned like a shell command), the URL of
  `WebFetch`, and path fields of other tools. Violations:
  - those forbidden paths and names, also in wildcard spellings (`*opsbench*`, `tru*`) and
    after `cd`, the truth folder by path (from `--truth`, `WPBENCH_OPS_TRUTH` or
    `WPBENCH_OPS_PASSFAIL_TRUTH`) and any `--forbid` path, in the Windows, Git Bash and WSL
    spellings;
  - the draft history of PREREGISTRATION.md: a `git log`, `show`, `diff`, `blame`, ... that
    names it or shows old content of a folder that holds it, or a fetch of its history
    from GitHub (`gh api .../commits?path=`, a commit URL);
  - a recursive search, copy or archive, or a wildcard read, over a run directory or a
    folder above one (`.`, the repository, a checkout, `..`, the home folder) that is not
    restricted to transcript and code files (`--include='*.jsonl'`, a `Grep` glob), since
    it reads `record.json` without naming it; above the repository, however restricted;
  - the owner's commands (`score` without `--pass-fail`, `grade`, `headroom`, `report`), the
    harness's truth functions from Python, the pass/fail mode with the owner's options,
    setting `WPBENCH_OPS_SESSION`;
  - a change to the harness, its committed lists, the wrapper or the benchmark's tests;
  - other sessions' transcripts (session-transcript tools, `claude --resume`);
  - more than 3 pass/fail evaluations (runs of `ops_passfail` by any launcher, `score ...
    --pass-fail` or `grade ... --pass-fail`) in one session, counted over the session's
    own and its subagents' transcripts (their `sessionId`).

  Warnings: a forbidden path named only by a write, or in written text that is not a
  script; `git log -p` or `git show` of whole commits without a path, and `git diff`,
  `checkout`, `worktree add` or `reset --hard` of an old revision; setting
  `WPBENCH_OPS_BUNDLES`; an evaluation in a loop, `xargs`, a background job or a written
  script (the wrapper's log, kept by the owner, has the count); several session names;
  unreadable lines. It prints each finding with its transcript line number and the verdict
  `CLEAN`, `CLEAN, with N warning(s) to review` or `VIOLATIONS (N)` (exit code 1).
  Detection is by markers, so the transcripts are kept.
- **Readiness arms** (`run --readiness`, development set only; default arms
  `lib-directed` and `code-skill`, default model `claude-sonnet-5`; `lib` on request).
  Without the flag they stay unavailable until freeze-1; `mcp-hybrid` stays unavailable.
  Every arm has the tools, limits and settings of Stage 0.
  - `lib-directed`: `environment-lib.txt` (the code+ text with worldparts added to the
    package list), `checklist.txt`, `quick-reference.txt` and `lib-directed.txt` (the
    directive, verbatim). `lib`: `environment-lib.txt` and `quick-reference.txt`.
    `code-skill`: `environment.txt`, `checklist.txt` and `code-skill.txt` (the sentence,
    verbatim).
  - **Preambles.** `run.json` records the SHA-256 of each preamble file (`preambles`); a
    resumed run or a re-run refuses other texts, and a readiness run refuses preamble
    files that differ from the commit.
  - **Quick reference** (`harness/quickref.py`). The lib arms refuse to run while
    `preambles/quick-reference.txt` is missing or breaks section 3: at most 45 lines of at
    most 120 characters, at most 20 for section 14 (the lines between the annotations
    `#! section 14` and `#! end`, which the agent never sees), no number that is not the
    checklist's (1 and 90; `.95` and `1_000` are numbers), no prose word outside the
    checklist and a fixed list of reference words (string literals, hyphen-joined backtick
    phrases and long underscore names are prose), no line with a procedure word (prefer,
    then, if, above, ...), not even a checklist line, and no section-14 name (read from
    worldparts' section-14 modules) used as code outside its part. Identifiers go in
    backticks or call syntax. `tests/test_ops_bench_build.py` runs the same test on the
    committed file.
  - **Lib environment** (`lib-env`, `harness/env.py`): `%LOCALAPPDATA%/worldparts-bench/ops-lib-<key>`
    holds the code-plus packages at the code-plus environment's versions and worldparts
    from a wheel built from this repository (not editable). The key hashes the versions,
    the wheel's source SHA-256 and the last commit that changed those sources (not HEAD,
    so a commit that leaves the wheel alone keeps the environment). `run.json` records
    `lib_env` (directory, key, commit, HEAD at build time, wheel file and SHA-256,
    versions) and `lib_key`. A run refuses a lib environment whose worldparts sources
    differ from the commit (git-ignored files the wheel would pack included), and a wheel
    whose files would trip a contamination marker. A resumed run or a re-run uses the
    run's environment directory and never rebuilds it in place.
  - **Toolkit** (`harness/toolkit.py`): `$WPBENCH_OPS_TOOLKIT` (or `--toolkit`) is the
    folder that every `code-skill` session gets as `reference/` in its working directory
    (without `.git` and caches). A run refuses without it, and refuses a toolkit with a
    file that is not UTF-8 text, with more lines than section 3's cap (800 or worldparts'
    section-14 line count, whichever is larger), or whose text would trip a contamination
    marker of the code arms (it must never name worldparts). `run.json` records its
    SHA-256 manifest (`toolkit`, `toolkit_digest`); each copy is checked against it
    (`extras.json` in the attempt folder), and a resumed run or a re-run with another
    toolkit is refused.
  - **Contamination.** `code-skill` sessions are marked as `code+` and `code-hint` sessions
    are: any mention of worldparts contaminates them. The lib arms may use worldparts, and
    the repository and benchmark markers still apply to them.
