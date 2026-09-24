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
transient or a fault diagnosis) stated completely in numbers. The same prompt runs in two
conditions, and the answers are graded against a reference computed with worldparts.

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
tools, and the same turn and time limits. Because the `code` agent must be able to reach
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
level: 1                      # 1: 2-4 components, 2: 5-7, 3: 8 or more (reference system)
category: operating_point     # operating_point | sizing | what_if | transient | diagnosis
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
  (`{at, ramp: {path: [start, end]}, over}`), `restore`, `read_min` and `read_max`.
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
  and duration, total cost, a per-task table and the decision-gate line.

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
