# Changelog

All notable changes to worldparts are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). Before 1.0, minor versions may change
interfaces. Each such change is listed here and in [docs/design.md](docs/design.md).

## [Unreleased]

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
