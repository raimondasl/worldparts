# Changelog

All notable changes to worldparts are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). Before 1.0, minor versions may change
interfaces. Each such change is listed here and in [docs/design.md](docs/design.md).

## [Unreleased]

Milestone v0.3, part 1 (design section 13): control loops, ramp events in documents, pump
wear, a leak component and top-fed tanks.

### Added

- **Control loops** (design 13.1, `worldparts.controls`). A system may carry `controls`: a
  `pi` loop or a `hysteresis` switch that reads one reported numeric variable and writes
  one numeric component input. `System.add_control`, `remove_control` and `controls`;
  controls round-trip in system documents (`controls` list, validated by the schema).
  `solve()` goal-seeks every PI actuator within its output limits (Brent's method, loop by
  loop for interacting loops) and leaves it at the value found; a hysteresis control holds
  its state. `simulate()` runs the loops as sampled-data controllers: the command computed
  from the solution at `t` is applied at `t` exactly like an event (the system is solved
  again with it, and that solution is recorded and integrated), PI in incremental form with
  clamping as anti-windup. `SolveResult.controls` and `SimulationResult.controls` report
  each loop; `control.<name>.output` and `control.<name>.measure` are results. `check()`
  codes `invalid_control` and `control_conflict`; warnings `control_saturated`,
  `short_cycling`, `control_direction`, `control_unresponsive` and
  `control_not_converged`. `System.restore_on_error()` puts every value back when a block
  raises.
- **MCP tools `add_control` and `remove_control`** (18 tools). `solve`, `solve_for` and
  `simulate` return a `controls` block, `list_variables(component="control")` lists the
  control results, and `variables=["control"]` (or `"control.<name>"`) selects them, as
  does the CLI `--var control`. The CLI prints a `Controls` table.
- **Ramp events in system documents and `System.simulate`** (design 13.2):
  `{at, ramp: {path: [start, end]}, over}` becomes one set event per step in the core, so
  documents, the Python API, the CLI and MCP share it.
- **Pump wear** (design 13.3): `centrifugal_pump` inputs `wear_head` and `wear_efficiency`
  (0 to 0.5). Head wear scales the running pump's head law (the linear term included); the
  stopped pump's resistance is not worn. Shaft power is `P_new (1 - wear_head) / (1 -
  wear_efficiency)`. The WNTR adapter scales a worn pump's head curve.
- **`leak` component** (design 13.4): an orifice to atmosphere, `Q = Cd A opening
  sqrt(2 dp / rho)`, with backflow at negative gauge pressure (`backflow` warning). The
  WNTR adapter exports it as an EPANET emitter.
- **Top-fed tanks** (design 13.5): `tank.inlet_height`. The inlet discharges freely at that
  height; backflow through a dry mouth is blocked and a simulation step draws through the
  inlet at most the water above the mouth. The WNTR adapter exports the inlet bottom-fed
  and lists it as an approximation.
- **Example** `examples/booster_station.yaml`: a PI booster pump with a demand ramp.
- **Batched `describe_component`**: `component` also takes a list of up to 12 ids or
  aliases; the result is then `{components: [...]}` in the order given (brief by default,
  `detail="full"` for every entry). A single id returns one description as before. Unknown
  entries are all named by index, with the valid choices listed once.
- **Ramp events in component-manifest scenarios**: `scenarios[].simulate.events` accept
  `{at, ramp: {path: [start, end]}, over}` (the schema now matches `system.schema.json`; the
  scenario runner already called `System.simulate`).

### Changed

- **MCP: the short path is the recommended one** (benchmark pilot: one model built a pump
  station in 5 calls, another in 30). The server instructions and the tool descriptions of
  `list_components`, `describe_component`, `create_system` and `load_system` recommend
  `list_components` -> `describe_component` (all parts, one call) -> `load_system` (the
  complete document, one call; its `issues` are the `check_system` report, so no separate
  check is needed) -> `solve`/`solve_for`/`simulate`, with `add_component`, `connect` and
  `set_values` for edits and `check_system` after edits. `load_system`'s example document
  shows inputs as well as parameters. The README's headline MCP example now uses this
  path. A comma-separated string passed to `describe_component` gets a hint to pass a
  list. To keep `tools/list` within its budget, tool
  descriptions are unwrapped (one line per paragraph) and `additionalProperties: true` (the
  JSON Schema default) is left out of the tool schemas.
- The benchmark reference executor hands ramp events straight to `System.simulate`
  instead of expanding them itself (the frozen expected answers are unchanged).

### Fixed (review of the part 1 work)

- A control command now acts over the step that follows the sample at which it is
  computed, exactly like an event at that time; it used to reach storage one step later.
- A steady solve whose PI measure is undefined (None) at every output holds the output,
  as a simulation does, instead of raising; one undefined only at some outputs raises
  with both points named. A failed steady solve, or a failed MCP `solve_for`, leaves every
  value as it was (actuators and switch states included).
- A PI loop whose measure does not respond to its actuator in a steady solve (a tank level,
  which `solve()` holds) holds its output and warns `control_unresponsive` instead of
  reporting a draining tank as settled or saturated.
- Setpoints, thresholds, output limits and `integral_time` must be finite, and `state` on a
  PI control is reported as an unknown field (`invalid_control`).
- `worldparts simulate --json --var ...` keeps the control series.
- `drawing_air` on a dry top inlet fires only when the port is more than 100 Pa below
  atmospheric (design 8.9), not whenever it is below the mouth's head: a supply too weak to
  reach the mouth, or a stopped fill pump, is no longer reported as not physical.

## [0.1.0] - 2026-09-23

The first public release: a hydraulic starter kit for pumping, water treatment and
distribution.

### Added

- **Component manifest format 0.1.** YAML manifests validated by a JSON Schema
  (`src/worldparts/schemas/component-manifest.schema.json`) plus semantic checks. A
  manifest declares ports, parameters with units and hard limits, inputs, states,
  observables, modes, envelope rules, code-emitted warnings, scenarios, behavioural
  contracts, implementation bindings (reference Python, Modelica, WNTR) and provenance.
  Specified in [spec/component-manifest.md](spec/component-manifest.md).
- **Catalogue of 11 water components**, each with at least two scenarios and three
  contracts that run in CI: `centrifugal_pump`, `tank`, `media_filter`, `uv_reactor`,
  `pipe`, `valve`, `check_valve`, `supply`, `drain`, `mixing_faucet` and
  `instantaneous_water_heater`. Reference: [docs/catalog.md](docs/catalog.md), generated
  from the manifests by `tools/gen_catalog_docs.py`.
- **Reference runtime.** A quasi-steady hydraulic network solver built on monotone branch
  laws (Kv resistance, Darcy-Weisbach with the Churchill friction factor, pump curves with
  affinity laws, check valve, gate, linear-plus-quadratic). It uses Newton-Raphson with
  fallbacks. Thermal mixing is exact in loops. Explicit time stepping covers tanks and
  actuator lags.
- **`System` composition API.** `add`, `connect`, `set`, `check` (stable issue codes),
  `solve`, `simulate` (events, and `restore=True`), `variables`, `to_dict` and
  `from_dict`. Values carry units, and pressures carry a gauge, absolute or difference
  reference. The language-neutral system document has its own JSON Schema.
- **Scenario and contract runner.** `run_scenario`, `run_contract`, `run_component` and
  `check_catalog`, with `monotonic`, `bounds`, `equal` and `warning_iff` checks. A check on
  an undeclared warning code or a misspelled name fails instead of passing vacuously.
- **MCP server** (`worldparts mcp`, stdio) with 16 typed tools: `list_components`,
  `describe_component` (brief or full), `run_contracts`, `create_system`, `add_component`,
  `remove_component`, `set_values`, `connect`, `disconnect`, `check_system`,
  `list_variables`, `solve`, `solve_for` (goal seek), `simulate` (step and ramp events,
  unit wildcards, warning intervals), `get_system` and `load_system`. Manifests are also
  exposed as resources at `worldparts://components/{id}`.
- **CLI.** `worldparts list`, `describe`, `validate`, `check-catalog`, `solve`, `simulate`,
  `export` and `mcp`; the commands other than `mcp` take `--json`. `solve` and `simulate`
  take `--units PATTERN=UNIT` (repeatable, with `*.name` wildcards, as the MCP `units`
  argument).
- **WNTR/EPANET adapter.** Exports a composed network to WNTR and reports its divergence
  from the reference runtime: the `worldparts export` command, and the MCP tools
  `export_system` and `compare_with_wntr`, which are registered when `wntr` is installed.
  It needs the optional `wntr` extra. A full tank is exported with EPANET's overflow
  option, and a pump curve continues past the run-out flow, so both hosts agree there too.
  See [docs/wntr-adapter.md](docs/wntr-adapter.md).
- **Examples** of a purification skid, a pump lift and a domestic hot-water system: see
  [examples/](examples/README.md).
- **Documentation.** [README](README.md), [manifest specification](spec/component-manifest.md),
  [catalogue reference](docs/catalog.md), [authoring guide](docs/authoring-components.md),
  [design contract](docs/design.md), [roadmap](docs/roadmap.md), the
  [research report](docs/research/world-model-libraries-for-ai-agents.md) and the
  [agent usability trial](docs/agent-trial.md).

### Changed before release, after the agent usability trial

- The MCP `tools/list` payload is smaller (structure-only output schemas), and
  `describe_component` is brief by default.
- System-document errors name the expected shape instead of echoing the input.
- Pump: `motor_power` with a `motor_overload` warning; a preferred operating region with an
  `outside_preferred_region` info; `specific_energy` is undefined at leakage flows.
- Tank: `drawing_air` warning when a pump pulls on an empty tank.
- The drain, tank and pipe descriptions give guidance on exit losses, and the valve `kv`
  description lists typical Kv by nominal size.

### Known limitations

- One medium, water with constant properties. No water hammer, compressible flow, pipe
  heat loss or cavitation dynamics (cavitation is flagged, not modelled).
- No global node elevations: static head comes from pipe `height_difference` and tank
  levels.
- The tank has bottom ports only; a free-discharge top inlet is not modelled.
- Default parameters are generic illustrative values, not specific products.
- The Modelica bindings in the manifests are documentation only; there is no Modelica
  backend yet (roadmap v0.5).

[Unreleased]: https://github.com/raimondasl/worldparts/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/raimondasl/worldparts/releases/tag/v0.1.0
