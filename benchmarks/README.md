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
stated completely in numbers. The same prompt runs in two conditions, and the answers are
graded against a reference computed with worldparts.

**Decision gate** (report, section "What to do next"): if tool-based composition does not
clear **80 %** on level-1 tasks with a frontier model, the packaging thesis needs rethinking.
Every report prints this line: the level-1 pass rate of condition `mcp` versus 80 %.

### The two conditions

| Condition | The agent has | The agent does not have |
|---|---|---|
| `mcp` | Only the worldparts MCP server's tools (list, describe, create, add, connect, solve, solve_for, simulate, ...) | Code execution, files, web, any other tool |
| `code` | A shell restricted to running Python (`python script.py`, `python -c`) in an environment with numpy, scipy, fluids and wntr; reading and writing files in its working directory | worldparts, pip, the network (by instruction and configuration; see limitations) |

Both conditions get the same task prompt, the same appended answer-format instruction, the
default Claude Code system prompt plus a one-paragraph description of the condition's
tools, and the same turn and time limits (which may differ by level, see
[Scale tasks](#scale-tasks-level-4)). Because the `code` agent must be able to reach
the same answer by hand or in plain Python, every prompt gives every number with units and
states every convention that is not textbook-standard (for example how a filter's pressure
drop splits into a linear and a quadratic part, or that UV dose is fluence rate times
volume over flow).

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
  code: --tools Bash,Read,Write,Edit --allowedTools "Bash(python *)" "Read(./**)" "Write(./**)" "Edit(./**)"
```

The prompt goes to stdin. `--setting-sources ""` loads no user, project or local settings
(so no hooks, permission rules or plugins); `settings.json` only sets `disableAllHooks`;
`CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` keep CLAUDE.md
files and memory out; `--disable-slash-commands` disables skills; `--strict-mcp-config`
loads only the worldparts server (`uv run --directory <repo> worldparts mcp` with
`WORLDPARTS_AUTOSAVE_DIR=<tmp>/systems`) or, for `code`, no server; `dontAsk` denies every
tool call that the allow rules do not cover; `ENABLE_TOOL_SEARCH=false` loads all tool
schemas up front. The parent's `CLAUDE*`/`ANTHROPIC_*` variables (except credentials) and
the repository's virtual environment are removed from the CLI's environment; for `code`,
`python` on PATH is a separate environment (`%LOCALAPPDATA%/worldparts-bench/code-env`)
with the same numpy, scipy, fluids and wntr versions as the repository and no worldparts,
no pip, `PIP_NO_INDEX=1` and `UV_OFFLINE=1`.

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
  `task.schema.json`), the task's own file name, a frozen `expected: {...}` mapping, and,
  in the code condition only, the repository path and the word worldparts (the code
  environment does not contain it; its `worldparts-bench` directory name does not count).
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
  gate stays the level-1 pass rate of condition `mcp`.

### Known limitations

- **The code condition is restricted, not sandboxed.** The agent can only run `python`,
  but Python can read any file the user can (including this repository and the frozen
  answers) and open network connections. Claude Code's own sandbox is not available on
  Windows. The prompt never names worldparts; runs whose tool traffic touches the
  benchmark are detected and excluded (see Contamination), and the saved transcripts show
  what each run did. Detection is by markers, so a deliberately disguised read would pass.
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
