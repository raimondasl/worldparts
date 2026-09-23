# worldparts design (v0.1)

This document is the architecture contract for worldparts v0.1. It fixes the concepts, conventions, file layout, public APIs and the exact interface of every v0.1 component. Code, manifests and docs must agree with it; when they cannot, change this document first.

The motivation and evidence live in the research report, [World model libraries for AI agents](research/world-model-libraries-for-ai-agents.md). The one-line thesis: component physics for pumps, valves and faucets already exists, but nothing packages it so an AI agent can **discover, parameterize, compose, query and simulate** it and know when a result is wrong. worldparts is that packaging layer.

## 1. Goals and non-goals

Goals for v0.1:

1. A **component manifest** format (YAML, validated by JSON Schema) that describes a physical component for an agent: ports, parameters with units and limits, inputs, observables, discrete modes, operating envelope, behavioural contracts, canonical scenarios, implementation bindings and provenance.
2. A **catalogue** of 11 water/hydraulic components whose contracts and scenarios run in CI, so every manifest is a tested claim.
3. A **reference runtime**: a small, dependency-light, quasi-steady hydraulic and thermal network solver with explicit time stepping for storage states. It is the lightweight tier (fast, embeddable, deterministic) and the test oracle for adapters.
4. A **composition API** (`System`) with typed connection checks and a structural pre-flight, serialisable to a language-neutral system document.
5. An **MCP server** that exposes the catalogue and the composition API as a small typed tool surface, so an agent selects, parameterizes and wires components instead of writing physics code.
6. A **WNTR adapter** that exports a composed water network to WNTR/EPANET and reports divergence against the reference runtime.

Non-goals for v0.1: a general acausal equation engine (use Modelica for that, see the roadmap); compressible flow, water hammer and pipe heat loss; temperature-dependent fluid properties; node elevations beyond the two mechanisms in section 5.4.

Why a reference solver at all, when the report says "wrap the physics, do not build an engine"? That advice is aimed at general equation engines. v0.1 needs (a) a runtime that works with zero heavyweight installs, so any agent can use the catalogue in seconds, (b) a lumped middle tier that is cheap enough for agent loops, diagnosis by hypothesis simulation and controller-in-the-loop tests, and small enough to embed elsewhere later, and (c) an oracle to measure divergence against WNTR now and Modelica later. The solver is deliberately narrow: monotone branch laws on a graph, nothing more. High-fidelity work goes through adapters.

## 2. Repository layout

```
pyproject.toml                  uv-managed project (uv_build backend)
src/worldparts/
  __init__.py                   public API re-exports, __version__
  units.py                      pint registry, parsing, SI and display conversion, gauge handling
  media.py                      water properties (constants) and vapour pressure
  expressions.py                safe expression evaluator for modes, envelopes and contracts
  manifest.py                   manifest model, loading, JSON Schema validation
  catalog.py                    discovery and search of manifests (package catalog + extra dirs)
  laws.py                       branch laws (quadratic resistance, pipe, pump, check valve, gate, linear+quadratic)
  network.py                    primitive graph (nodes, branches) and the steady solver
  components/                   one module per family; classes referenced from manifests
    base.py                     Component base class and the NetworkBuilder API
    boundaries.py               supply, drain
    pipe.py                     pipe
    valves.py                   valve, check_valve
    faucet.py                   mixing_faucet
    heater.py                   instantaneous_water_heater
    pump.py                     centrifugal_pump
    tank.py                     tank
    treatment.py                media_filter, uv_reactor
  system.py                     System: add, connect, set, check, solve, simulate, to_dict, from_dict
  results.py                    SolveResult, SimulationResult, Issue, ComponentWarning
  contracts.py                  run manifest scenarios and contracts, catalogue self-test
  mcp_server.py                 MCP server (MCP Python SDK v2, `from mcp.server import MCPServer`)
  cli.py                        `worldparts` command-line interface (argparse)
  adapters/wntr_adapter.py      export to WNTR and cross-validate (optional extra `wntr`)
  schemas/component-manifest.schema.json
  schemas/system.schema.json
  catalog/hydraulic/*.yaml      the 11 v0.1 manifests
tests/                          pytest; one test module per source module or component family
examples/                       runnable scripts and system documents
spec/component-manifest.md      prose specification of the manifest format
docs/                           design (this file), research report, roadmap
```

Components are bound to manifests by import path (`implementations.reference.python: "worldparts.components.pump:CentrifugalPump"`), so there is no shared registry file to edit.

## 3. Conventions

### 3.1 Units

- **Inside components and the solver everything is SI**: pressure in Pa **absolute**, mass flow in kg/s, volume flow in m³/s, temperature in K, length in m, power in W, time in s.
- **At the API boundary everything is in declared units.** Every parameter, input, state and observable in a manifest declares a `unit`. Plain numbers passed to the API are interpreted in that declared unit. Strings with units (`"3 bar"`, `"12 L/min"`, `"55 degC"`) are parsed with pint and converted; a dimension mismatch is an error that names the expected unit.
- **Pressures at the API are gauge**, relative to `P_ATM = 101325 Pa`, unless a parameter declares `pressure_reference: absolute` or `difference`. Port pressures are reported in bar gauge. NPSH and vapour-pressure calculations use absolute pressure internally.
- Results are reported in the declared display unit of each variable, and every reported value carries its unit string.
- Unit strings in manifests use this vocabulary, which `units.py` must parse (normalising `m3` to `m**3`, `cm2` to `cm**2`, `%` to percent, `1` to dimensionless): `1`, `%`, `Pa`, `kPa`, `bar`, `m`, `mm`, `m/s`, `m2`, `m3`, `L`, `kg/s`, `L/s`, `L/min`, `m3/h`, `degC`, `K`, `W`, `kW`, `s`, `min`, `h`, `rpm`, `mW/cm2`, `mJ/cm2`, `kg/m3`.
- Temperature offsets: use pint with `autoconvert_offset_to_baseunit=True` so `"55 degC"` parses. Tolerances in scenarios are plain numbers in the variable's display unit (a tolerance of `1` on a `degC` variable means 1 K).
- A Kv value converts to SI like any flow (m³/h to m³/s); laws turn it into a mass-flow coefficient (section 5.2).
- Temperatures are absolute unless a variable declares `quantity: temperature_difference` (allowed only with unit `K` or `degC`). Differences convert by scale only (35.1 K is 35.1 degC, 63.18 degF) and results carry `reference: difference`. The loader rejects a temperature variable whose name looks like a difference (rise, drop, delta, difference and similar) without that declaration.
- `kWh/m3` (specific energy) is in the unit vocabulary. It has the dimension of a pressure but takes no gauge reference.

### 3.2 Signs and names

- Port mass flow `m_flow` is **positive into the component** (Modelica convention).
- Two-port components define their forward direction as from the first port to the second (`port_a` to `port_b`, or `inlet` to `outlet`). Their `volume_flow` observable is positive in that direction.
- Variable paths: `<instance>.<name>` for inputs, parameters, states and observables; `<instance>.<port>.p|m_flow|T` for port variables. Port `p` is in bar gauge, `m_flow` in kg/s, `T` in degC.
- Undefined values (for example the outlet temperature of a closed faucet) are reported as `null`/`None`. Checks skip `None`.

### 3.3 Medium

v0.1 supports one medium, `water`, with constant properties: density 998.2 kg/m³, dynamic viscosity 1.002e-3 Pa·s, specific heat 4182 J/(kg·K). `media.py` also provides water vapour pressure as a function of temperature (Antoine or an IAPWS-quality correlation, valid 1 to 100 °C) for NPSH. Constant properties are a stated simplification recorded in every manifest's `fidelity.assumptions`.

### 3.4 Expressions

Mode conditions, envelope conditions and contract checks use a small, safe expression language evaluated by `expressions.py` over values **in display units**:

- literals (int, float, `true`, `false`), names with dots (`opening`, `port_a.p`, `hot.temperature`), `+ - * / **`, unary minus, comparisons (`< <= > >= == !=`, chained allowed), `and`, `or`, `not`, parentheses, and the functions `abs`, `min`, `max`, `sqrt`.
- Implemented by parsing with `ast` and whitelisting node types. No attribute access on objects, no calls other than the four functions, no subscripts.
- Inside a manifest's `modes` and `envelope`, names are the component's own local names (`opening`, `port_a.p`). Inside scenarios and contracts, names are system paths (`dut.flow`, `hot.temperature`).
- Any `None` operand makes the whole expression `None`; a `None` condition counts as false.

## 4. Component manifest (format 0.1)

A manifest is a YAML file validated against `schemas/component-manifest.schema.json` (JSON Schema draft 2020-12). The schema is the normative definition; `spec/component-manifest.md` is the prose explanation. Top-level fields:

```yaml
manifest_version: "0.1"                  # required, const "0.1"
id: worldparts.hydraulic.valve           # required, dotted lowercase namespace; last segment is the short alias
version: "0.1.0"                         # required, semver of this component definition
name: Two-way control valve              # required
summary: Throttling valve with Kv sizing, inherent characteristic and leakage.   # required, one line
description: |                           # required, markdown; what it models and what it does not
  ...
tags: [valve, throttling, kv]
classification:                          # optional cross-walk to existing vocabularies
  ifc: {entity: IfcValve, predefined_type: REGULATING}
  brick: Valve
  other: {}                              # free-form, e.g. eclass, etim, saref4watr
fidelity:
  level: lumped_quasi_steady             # qualitative | lumped_quasi_steady | lumped_dynamic | distributed
  assumptions: ["Incompressible water with constant properties."]
ports:
  - {name: port_a, type: fluid, medium: water, description: Inlet in the forward direction.}
parameters:
  - name: kv
    description: Flow coefficient at full opening (m3/h of water at 1 bar pressure drop).
    type: number                         # number | integer | string | boolean | table
    unit: m3/h                           # required for number; "1" for dimensionless
    default: 2.5                         # required
    minimum: 0.0001                      # hard limits: values outside are rejected
    maximum: 100000
    # pressure_reference: gauge          # only for pressures: gauge (default) | absolute | difference
    # enum: [...]                        # for type string
    # columns: [{name: flow, unit: m3/h}, {name: head, unit: m}]   # for type table
    # min_rows: 3                        # for type table
inputs:                                  # operational set-points that may change during a simulation
  - {name: opening, description: ..., unit: "1", default: 1.0, minimum: 0, maximum: 1}
states:
  - name: position
    unit: "1"
    description: Actual valve position after the actuator lag.
    steady: settle                       # settle: solve() sets it to equilibrium; hold: solve() keeps the current value
observables:
  - {name: volume_flow, unit: L/min, description: Volume flow from port_a to port_b.}
modes:                                   # ordered; the first matching condition is the mode
  - {name: closed, condition: "position <= 0.001", description: ...}
  - {name: open, condition: "true", description: ...}
envelope:                                # soft validity limits; produce warnings, never errors
  - code: high_pressure_drop
    severity: warning                    # info | warning
    condition: "pressure_drop > 3"
    message: Pressure drop above 3 bar; cavitation and noise are likely in a real valve.
warnings:                                # warnings emitted by component code (not expressible as envelope)
  - {code: cavitation, severity: warning, description: ...}
scenarios: [...]                         # section 7
contracts: [...]                         # section 7
implementations:
  reference: {python: "worldparts.components.valves:TwoWayValve"}    # required
  modelica: {class: Buildings.Fluid.Actuators.Valves.TwoWayLinear, library: "Buildings 13.0.0", notes: ...}  # optional
  wntr: {element: TCV, notes: ...}                                   # optional
provenance:
  authors: [worldparts contributors]
  sources: [{title: ..., url: ...}]      # equations, standards, datasheets used
  data:
    acquisition: generic                 # generic | estimate | datasheet | digitized | measured | standard
    notes: Default parameters are generic illustrative values, not a specific product.
  license: Apache-2.0                    # license of this manifest
  data_license: CC0-1.0                  # license of the parameter data
```

Every warning code a component can emit must be listed in the manifest, either in `envelope` or in `warnings`.
Revised after verification: parameters (numbers), inputs, states and observables accept an optional `quantity` field; its only value in 0.1 is `temperature_difference` (section 3.1).

## 5. Reference runtime

### 5.1 Primitive network

`network.py` defines:

- `Node`: `free` (unknown pressure) or `fixed` (known absolute pressure `p` and the temperature `T` of water leaving it into the network).
- `Branch(a, b, law, thermal=None)`: mass flow `m` positive from node `a` to node `b`; the law gives `p_a - p_b = law.dp(m)` and its derivative. `thermal`, if given, maps the upstream temperature and flow to the downstream temperature (identity by default).
- `Injection(node, m, T)`: fixed mass inflow at a node (optional, for demand-style components later).

**Monotonicity is the core invariant**: every law's `dp(m)` must be continuous and strictly increasing in `m`, with a derivative bounded below by a small positive number. That makes the network problem well posed (a unique solution exists for every connected sub-network that has a fixed-pressure node) and makes Newton robust. Laws achieve this with regularisation near zero flow, as Modelica's `regSquare` and `regRoot` do.

### 5.2 Laws (`laws.py`)

All laws work in SI and return `(dp, ddp_dm)`.

- `QuadraticResistance(k, m_eps)`: `dp = m * sqrt(m**2 + m_eps**2) / k**2`, with `k` the mass-flow coefficient in kg/(s·Pa^0.5). From a Kv value in SI (m³/s): `k = Kv_SI * sqrt(rho * 1000 / 1e5)`, which reproduces "Kv m³/h of water at 1 bar". `m_eps` defaults to 1e-3 of the flow at 1 bar through the fully open element. The regularisation is a C1 cubic, `m * (m_eps + m**2 / (4 * m_eps))`, inside `|m| < 2 * m_eps` and exactly `m * |m|` outside it, so Kv relations hold exactly above 0.2% of nominal flow.
- `LinearQuadraticResistance(r_lin, r_quad)`: `dp = r_lin * Q + r_quad * Q * |Q|` with `Q = m / rho`; used by the media filter.
- `PipeLaw(length, diameter, roughness, minor_loss, height_difference)`: Darcy-Weisbach with the Churchill (1977) friction factor, continuous across laminar, transitional and turbulent flow. Written as `dp = G(Re) * L * mu * v / (2 D**2) + K * rho * v * |v| / 2 + rho * g * dz` with `G = f * Re`, so `G -> 64` as `Re -> 0` and the law stays strictly increasing at zero flow. Tests cross-check `f` against `fluids.friction.Churchill_1977`.
- `PumpLaw(a, b, c, speed, eps)`: head `H(Q, s) = a*s**2 + b*s*Q + c*Q*|Q|` with `Q = m / rho`, fitted so that `a > 0`, `b <= 0`, `c < 0`; `dp = -rho * g * H + eps * m` (the tiny linear term keeps strict monotonicity at `s = 0`, where the pump acts as a resistance).
- `CheckValveLaw(k, leakage)`: quadratic resistance with coefficient `k` for forward flow and `k * leakage` for reverse flow, joined continuously at zero.
- `GateLaw(k, blocked_direction)`: a quadratic resistance that can switch one flow direction to leakage-only; used by tank ports when the tank is empty.
- Revised after verification: `GateLaw(k, blocked_direction, leakage, limit=None)`. With a numeric `limit`, flow in the blocked direction follows the open law up to the limit and then closes steeply (`GATE_STIFFNESS` = 1e10 Pa per kg/s). Tanks use it to cap each step's outflow at the water available.

### 5.3 Steady solver

Unknowns are the pressures of free nodes and the mass flows of branches. Equations: one per branch (`p_a - p_b - dp(m) = 0`) and one mass balance per free node. Newton-Raphson on the scaled system (pressures scaled by 1e5 Pa, flows by 1 kg/s) with backtracking line search on the residual norm; start from zero flows and the mean fixed pressure; converge when the scaled max residual is below 1e-9. If Newton fails after 100 iterations, a node-pressure Newton runs (each law inverted exactly, convex line search) and its result is polished by the branch-flow Newton; if that also fails, fall back to `scipy.optimize.root(method="hybr")`; if that fails, raise `SolverError` with the residual and the worst equation. Dense numpy linear algebra is fine for v0.1 sizes (up to a few hundred unknowns).

Before solving, the graph is split into connected sub-networks; any sub-network without a fixed-pressure node is a structural error (`no_pressure_reference`).

After the hydraulic solve, temperatures are computed by mixing: each free node's temperature is the flow-weighted mean of the temperatures of branches flowing into it, where a branch's outflow temperature is its `thermal` map applied to its upstream node temperature. When the flow graph is acyclic this is one exact sweep in flow order. When flow circulates in a loop, the mixing equations are solved directly (thermal maps linearised and Newton-iterated to 1e-9 K). A node that no temperature source reaches has temperature `None`.

### 5.4 Elevation

v0.1 has no global node elevations. Two mechanisms carry static head: the pipe's `height_difference` (outlet minus inlet elevation) and the tank's level (pressure at the tank ports is `P_ATM + rho * g * level`). All other ports are at the same datum.

### 5.5 Time stepping

`System.simulate(duration, step, events)` samples at every multiple of `step`, at every event time and exactly at `duration`; events after `duration` are rejected, and without a `duration` the system document's `simulation` block is used. Going from sample `t0` to `t`: fast states advance over `t - t0` with the previous command (actuator lags, exact first-order response `x += (x_cmd - x) * (1 - exp(-dt / tau))`); the events due at `t` are applied; instantaneous responses are applied; hydraulics and temperatures are solved; outputs and warnings are recorded; storage states are integrated with explicit Euler (tank level and temperature). A `start_simulation()` hook runs once per call for per-run accumulators. `solve()` is a steady snapshot: states marked `steady: settle` are set to their equilibrium, states marked `steady: hold` keep their current values.

## 6. Component and System APIs

### 6.1 Component implementation (`components/base.py`)

```python
class Component:
    manifest: Manifest                      # bound by the catalogue when the class is resolved

    def __init__(self, name: str, parameters: dict[str, Any], inputs: dict[str, float]): ...
        # parameters and inputs arrive in SI (tables as numpy arrays in SI columns), already validated
    def build(self, nb: NetworkBuilder) -> None: ...
        # create internal nodes and branches; nb.port(name) returns the node for a port
    def update_laws(self) -> None: ...      # refresh law coefficients after inputs, parameters or states change
    def observables(self, sol: NetworkView) -> dict[str, float | None]: ...
        # SI values for every observable declared in the manifest
    def init_states(self) -> None: ...
    def settle(self) -> None: ...           # set 'settle' states to equilibrium (steady solve)
    def update_fast_states(self, dt: float) -> None: ...
    def integrate(self, dt: float, sol: NetworkView) -> None: ...   # storage states
    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]: ...
    def start_simulation(self) -> None: ...  # once per simulate() call, for per-run accumulators
    @classmethod
    def check_parameters(cls, params) -> list[str]: ...  # cross-parameter rules on the whole trial batch
```

The full, current authoring guide is [authoring-components.md](authoring-components.md).

`NetworkBuilder` offers `port(name)`, `node(label)`, `fixed_node(label, p_abs, T)`, `branch(a, b, law, thermal=None, label=...)` and returns handles the component keeps. `NetworkView` gives the solved `p` and `T` of nodes and `m` of branches by handle. The system computes port variables itself: a port's `m_flow` is the sum of this component's branch flows at the port node, signed into the component.
Revised after verification: `Component.time_step` is the length of the step that follows the current solve (`None` in `solve()`), and `check_states(parameters, states)` returns problems with a trial batch of states (for example a tank level above its height).

### 6.2 System (`system.py`)

```python
import worldparts as wp

s = wp.System("bathroom")
s.add("mains", "supply", pressure="3.5 bar", temperature="12 degC")     # type by full id or short alias
s.add("faucet", "worldparts.hydraulic.mixing_faucet", inputs={"lift": 1.0, "mix": 0.5})
s.connect("mains.port", "faucet.cold")    # connecting several ports to one node forms a junction (tee)
s.set("faucet.lift", 0.5)                 # inputs or parameters; plain numbers are in the declared unit
issues = s.check()                        # list[Issue]: severity error|warning, code, message, where
r = s.solve()                             # SolveResult
r["faucet.flow"]                          # float in the declared display unit (L/min)
r.get("faucet.flow", unit="L/s")
sim = s.simulate(duration="10 min", step="1 s", events=[{"at": "60 s", "set": {"faucet.lift": 0}}])
doc = s.to_dict(); s2 = wp.System.from_dict(doc)
```

`check()` codes (stable identifiers agents can rely on): `unknown_component`, `unknown_port`, `incompatible_ports`, `self_connection` (error for a port connected to itself; warning when two ports of one instance share a node, which bypasses the component), `unconnected_port` (warning; the port is treated as capped), `no_pressure_reference` (error), `boundary_short_circuit` (error: two fixed-pressure boundaries at different pressures joined without any resistance), `parameter_out_of_range` (error), `invalid_value` (error). `solve()` raises `SystemCheckError` (carrying the issues) if any error-level issue exists. Invalid calls (`add` with an unknown type, `connect` with an unknown port, a value outside hard limits) raise `WorldpartsError` subclasses immediately with a message that lists valid alternatives.

`SolveResult`: `converged`, `iterations`, `max_residual`, `values` (path to display-unit value), `units` (path to unit string), `modes` (instance to mode), `warnings` (list of `ComponentWarning(component, code, severity, message)`), `to_dict()` (pressure values carry `reference: gauge|absolute|difference`). `get(path, unit="bar absolute")` converts between references. `System.variables()` lists every path; string and table parameters are not part of results (`reported: false`) and are read with `System.get`. Pressure strings may state their reference explicitly (`"2 bar absolute"`, `"1.5 bara"`, `"3 barg"`); `"1 atm"` without a reference is rejected for gauge variables. `SimulationResult`: `time` (s), `series` (path to list), `units`, `warnings` (first occurrence time for each component and code), `mode_changes` (time, instance, mode), `final` (a `SolveResult` at the end), `to_dict(max_points=None)` with downsampling.
Revised after verification: `set_values` re-initialises only the states whose initial value the batch changes, then runs `check_states` on the trial values before applying anything; `check()` reports `check_states` problems as `invalid_value`. `VariableInfo` carries `quantity`, and temperature differences report the reference `difference`.

### 6.3 System document

Language-neutral YAML/JSON validated by `schemas/system.schema.json`:

```yaml
worldparts_system: "0.1"
name: bathroom
description: Two mixers on a shared hot line fed by an instantaneous heater.
components:
  - {name: mains, type: supply, parameters: {pressure: "3.5 bar", temperature: "12 degC"}}
  - {name: faucet, type: mixing_faucet, inputs: {lift: 1, mix: 0.5}}
connections:
  - [mains.port, faucet.cold]
    # optional per component: states: {level: 1.2}  (written by to_dict when states differ from their initial values)
    # table cells may be numbers (declared column unit) or strings with units
simulation:                               # optional default run for `worldparts simulate`
  duration: 5 min
  step: 1 s
  events: [{at: 60 s, set: {faucet.lift: 0}}]
```

## 7. Scenarios and contracts

Scenarios are small systems that exercise one component (named `dut` by convention) with expected results. Contracts are behavioural properties checked by sweeping a variable in a scenario. Both run in CI through `worldparts check-catalog`, which turns every manifest into a tested claim.

```yaml
scenarios:
  - id: basin-3-bar
    description: Fully open, half mixed, 3 bar gauge on both sides.
    system: {components: [...], connections: [...]}          # a system document body
    simulate: {duration: 60 s, step: 1 s, events: [...]}     # optional; expectations then refer to the final time
    expect:
      - {variable: dut.temperature, value: 33.5, abs_tol: 1.0}
      - {variable: dut.flow, min: 5, max: 15}
      - {warning: dut.scald_risk, present: false}
contracts:
  - id: flow-increases-with-lift
    description: More lever lift never reduces discharge.
    scenario: basin-3-bar
    sweep: {variable: dut.lift, from: 0, to: 1, steps: 11}  # or values: [...]
    check: {type: monotonic, variable: dut.flow, direction: increasing, strict: false}
```

Expectations may also test modes: `{mode: dut, is: open}`. Warning expectations and `warning_iff` checks must name a declared warning code; an undeclared code is a failure, not a vacuous pass.

Check types:

- `monotonic`: `variable`, `direction` (`increasing` | `decreasing`), `strict` (default false), optional `tolerance` (default 1e-9 relative).
- `bounds`: `variable`, `min` and/or `max` (expressions), holds at every sweep point.
- `equal`: `left`, `right` (expressions), `abs_tol` and/or `rel_tol`, holds at every sweep point.
- `warning_iff`: `code` (`<instance>.<code>`), `condition` (expression); the warning is present exactly when the condition is true, at every sweep point.

Every component must ship at least two scenarios and three contracts, including at least one mass-conservation `equal` check.

## 8. v0.1 component catalogue

All ids are `worldparts.hydraulic.<alias>`. Numbers are defaults; bracketed ranges are hard limits. All volume flows use water density 998.2 kg/m³. Default parameters are generic illustrative values (`acquisition: generic` or `estimate`), never presented as a specific product.

### 8.1 `supply` (ideal pressure source: mains, reservoir, pressurised line)

- Ports: `port`.
- Parameters: `pressure` bar gauge, 3.0, [-0.9, 100]; `temperature` degC, 12, [0.5, 99].
- Observables: `volume_flow` L/min, positive when the supply delivers water into the network (`-port.m_flow / rho`).
- Modes: `supplying` (`volume_flow > 0.01`), `absorbing` (`volume_flow < -0.01`), `idle`.
- Model: a fixed node at `P_ATM + pressure` with outflow temperature `temperature`, joined to the port.

### 8.2 `drain` (open discharge to atmosphere: basin, open channel, drain)

- Ports: `port`. Parameters: `temperature` degC, 20, [0.5, 99] (temperature of water drawn back from the drain, only used on backflow).
- Observables: `volume_flow` L/min, positive into the drain.
- Envelope: `backflow` warning when `volume_flow < -0.01` (the network is below atmospheric pressure and draws from the drain).
- Modes: `receiving`, `backflow`, `idle`.

### 8.3 `pipe`

- Ports: `port_a`, `port_b`.
- Parameters: `length` m, 5, [0.01, 100000]; `diameter` mm (inner), 16, [1, 5000]; `roughness` mm, 0.0015, [0, 10]; `minor_loss` 1 (sum of K), 0, [0, 1000]; `height_difference` m (elevation of `port_b` minus `port_a`), 0, [-1000, 1000].
- Observables: `volume_flow` L/min; `velocity` m/s; `pressure_drop` bar (`p_a - p_b`); `reynolds` 1; `friction_factor` 1.
- Envelope: `high_velocity` warning when `abs(velocity) > 3`; `high_relative_roughness` warning when roughness exceeds 5% of the diameter. Code-emitted: `below_vapour_pressure` when a port's absolute pressure falls below the vapour pressure (the quasi-steady model would otherwise report impossible negative absolute pressures). Parameter rule: `roughness < diameter / 2`.
- Modes: `stagnant` (`abs(volume_flow) < 0.001`), `flowing`.
- Law: `PipeLaw`.

### 8.4 `valve` (two-way control valve)

- Ports: `port_a`, `port_b`.
- Parameters: `kv` m3/h, 2.5, [0.0001, 100000]; `characteristic` string, `linear` | `equal_percentage` | `quick_opening`, default `linear`; `rangeability` 1, 50, [2, 500]; `leakage` 1, 1e-4, [1e-8, 0.1]; `actuator_time` s, 0, [0, 3600] (first-order time constant; 0 is instantaneous).
- Inputs: `opening` 1, 1.0, [0, 1]. States: `position` 1, `steady: settle` (states are reported in results, so `position` is a state only, not also an observable; all names under one instance share one namespace).
- Characteristic φ(y) with `l` the leakage and `R` the rangeability: linear `l + (1-l)*y`; equal percentage `l + (1-l)*(R**(y-1) - 1/R)/(1 - 1/R)`; quick opening `l + (1-l)*sqrt(y)`. Effective Kv is `kv * φ(position)`.
- Observables: `volume_flow` L/min; `pressure_drop` bar; `effective_kv` m3/h.
- Envelope: `high_pressure_drop` warning when `pressure_drop > 3`.
- Modes: `closed` (`position <= 0.001`), `throttling` (`position < 0.999`), `open`.

### 8.5 `check_valve`

- Ports: `port_a` (inlet), `port_b` (outlet).
- Parameters: `kv` m3/h, 3.0, [0.0001, 100000]; `leakage` 1, 1e-6, [1e-9, 0.1].
- Observables: `volume_flow` L/min; `pressure_drop` bar.
- Modes: `open` (`volume_flow > 0.001`), `closed`.
- Law: `CheckValveLaw`.

### 8.6 `mixing_faucet` (single-lever basin or shower mixer; the bathroom example)

- Ports: `hot`, `cold`. The spout discharges to atmosphere inside the component (water leaves the network).
- Parameters: `kv_hot` m3/h, 0.6; `kv_cold` m3/h, 0.6; `kv_spout` m3/h, 0.8 (spout and aerator), all [0.001, 100]; `leakage` 1, 1e-6, [1e-9, 0.01]; `min_flow_pressure` bar gauge, 0.5, [0, 10]; `scald_temperature` degC, 49, [30, 90].
- Inputs: `lift` 1, 0.0, [0, 1] (lever lift, 0 closed); `mix` 1, 0.5, [0, 1] (0 full cold, 1 full hot).
- Model: internal mixing node `C`; branch `hot -> C` with Kv `kv_hot * (l + (1-l)*lift*mix)`; branch `cold -> C` with Kv `kv_cold * (l + (1-l)*lift*(1-mix))`; branch `C -> ATM` (internal fixed node at `P_ATM`) with Kv `kv_spout`.
- Observables: `flow` L/min (discharge); `temperature` degC (mixed outlet temperature, `None` when `flow < 1e-6`); `hot_flow` L/min and `cold_flow` L/min (into the faucet through each port; negative means crossflow).
- Envelope: `scald_risk` warning when `temperature > scald_temperature`; `crossflow` warning when `hot_flow < -0.01 or cold_flow < -0.01` (one supply pushes water into the other through the cartridge); `low_supply_pressure` warning when the lever is open and the pressure at a connected inlet is below `min_flow_pressure` (code-emitted, because it must ignore capped ports).
- Modes: `closed` (`lift <= 0.001`), `cold_only` (`mix <= 0.02`), `hot_only` (`mix >= 0.98`), `mixing`.
- Defaults give about 14 L/min at 3 bar fully open and half mixed.
- Revised after verification: `crossflow` is `(hot_flow < -0.01 and cold_flow > 0.01) or (cold_flow < -0.01 and hot_flow > 0.01)`; a new `back_siphonage` warning fires when `flow < -0.01` (water drawn back through the spout). The 1e-6 threshold for an undefined outlet temperature is in m³/s.

### 8.7 `instantaneous_water_heater`

- Ports: `inlet`, `outlet`.
- Parameters: `setpoint` degC, 55, [20, 95]; `max_power` kW, 18, [0.1, 1000]; `activation_flow` L/min, 2.0, [0, 100] (below it the heater does not fire, a real behaviour of flow-switched heaters); `kv` m3/h, 1.5, [0.001, 1000].
- Inputs: `enabled` 1, 1.0, [0, 1] (fraction of `max_power` available).
- Thermal map on the `inlet -> outlet` branch: for forward flow above `activation_flow`, `T_out = min(setpoint, T_in + enabled * max_power / (m * cp))`; otherwise `T_out = T_in`.
- Observables: `volume_flow` L/min; `outlet_temperature` degC; `temperature_rise` K; `heat_rate` kW.
- Envelope: `setpoint_not_met` warning when `volume_flow > activation_flow and outlet_temperature < setpoint - 1`; `below_activation_flow` info when `volume_flow > 0.01 and volume_flow < activation_flow`.
- Modes: `idle` (`volume_flow < activation_flow`), `saturated` (`outlet_temperature < setpoint - 0.01`), `heating`.
- Revised after verification: the thermal map is `max(T_in, min(setpoint, T_in + ...))`, so the heater never cools; `idle` is `heat_rate <= 0` (a heater with `enabled = 0` is idle); `below_activation_flow` is `volume_flow > 0.01 and volume_flow <= activation_flow and heat_rate <= 0`; `temperature_rise` declares `quantity: temperature_difference`.

### 8.8 `centrifugal_pump` (the purification-plant example)

- Ports: `inlet`, `outlet`.
- Parameters (tables at rated speed): `head_curve` columns flow m3/h, head m, default `[[0, 34], [10, 32.5], [20, 28.5], [30, 21.5], [36, 16]]`, at least 3 rows; `power_curve` flow m3/h, shaft power kW, default `[[0, 1.5], [10, 2.0], [20, 2.55], [30, 2.95], [36, 3.15]]`; `npsh_curve` flow m3/h, NPSH required m, default `[[10, 1.4], [20, 2.1], [30, 3.4], [36, 4.6]]`; `rated_speed` rpm, 2900, [100, 20000]; `npsh_margin` m, 0.5, [0, 10]; `min_flow_fraction` 1, 0.15, [0, 1].
- Inputs: `speed` 1, 1.0, [0, 1.2] (relative to rated speed).
- Fitting: head `H0(Q) = a + b*Q + c*Q**2` by bounded least squares (`scipy.optimize.lsq_linear`, `a > 0`, `b <= 0`, `c <= -1e-9` in SI); report the RMS residual. Affinity laws: `H = a*s**2 + b*s*Q + c*Q*|Q|`; shaft power from a quadratic fit `P0(Q) = p0 + p1*Q + p2*Q**2` scaled as `p0*s**3 + p1*s**2*Q + p2*s*Q**2`; NPSH required from a quadratic fit scaled by `s**2` in the same way. Best-efficiency flow is the maximum of `rho*g*Q*H0/P0` over the curve range, scaled by `s`.
- Observables: `volume_flow` m3/h; `head` m; `shaft_power` kW; `hydraulic_power` kW; `efficiency` % (0 when flow or power is not positive); `npsh_available` m (`(p_inlet_abs - p_vapour(T_inlet)) / (rho*g)`); `npsh_required` m; `speed_rpm` rpm; `bep_flow` m3/h; `curve_fit_rms` m.
- Warnings (code-emitted, listed in the manifest `warnings`): `cavitation` when `npsh_available < npsh_required + npsh_margin` with forward flow; `low_flow` when running and `volume_flow < min_flow_fraction * bep_flow`; `beyond_curve` when `volume_flow` exceeds the largest curve flow times `speed`; `reverse_flow` when `volume_flow < -0.01`.
- Modes: `off` (`speed <= 0.01`), `reverse_flow`, `cavitating`, `low_flow`, `running` (the implementation may compute mode conditions from observables it exposes, for example `npsh_available < npsh_required`).
- Revised after verification:
  - New observable `specific_energy` in kWh/m3: shaft power divided by volume flow, `None` when the flow is not positive.
  - `cavitation` is also raised whenever NPSH available is below zero; `beyond_curve` and `low_flow` apply only while running (speed above 0.01).
  - `check_parameters` rejects curves whose best efficiency exceeds 100 %.
  - A stopped pump is a resistance whose quadratic coefficient is at least `0.5 * a / Q_max**2` (`STOP_RESISTANCE_FACTOR`), blended linearly into the fitted `c` as the speed rises to 0.01, so a stopped pump with a flat curve cannot short-circuit the network.

### 8.9 `tank` (open atmospheric tank, ports at the bottom)

- Ports: `inlet`, `outlet` (both at the bottom; names are conventional, flow may go either way).
- Parameters: `diameter` m, 2.0, [0.05, 100]; `height` m, 3.0, [0.1, 100]; `initial_level` m, 2.0, [0, 100] (must not exceed `height`); `initial_temperature` degC, 15, [0.5, 99]; `port_kv` m3/h, 200, [0.01, 1e6].
- States: `level` m (`steady: hold`), `temperature` degC (`steady: hold`).
- Model: internal fixed node at `P_ATM + rho*g*level` with outflow temperature `temperature`; each port connects through a `GateLaw(port_kv)`; when `level <= 0.001` m, outflow from the tank is blocked (leakage only). Integration: level from net volume inflow; above `height` the excess is spilled and reported as `overflow_rate`; temperature by perfect mixing of inflow.
- Observables: `volume` m3; `fill_fraction` %; `net_inflow` m3/h; `overflow_rate` m3/h (`level` and `temperature` are states and are reported as such).
- Envelope: `tank_empty` warning when `level <= 0.001`; `low_level` info when `fill_fraction < 10`; `tank_overflow` warning when `overflow_rate > 0`.
- Modes: `empty`, `overflowing`, `filling` (`net_inflow > 0.001`), `draining` (`net_inflow < -0.001`), `steady`.
- Revised after verification:
  - `level` and `temperature` are states only (as the valve's `position` in 8.4).
  - In a simulation each step's outflow is capped at the water above the 1 mm empty level, shared between the connected ports, so the water balance closes exactly.
  - The level must not exceed the height (`check_states`); only `initial_level` and `initial_temperature` reset the states.
  - In a steady solve, `overflow_rate` is the net inflow when the tank is full and filling.

### 8.10 `media_filter` (sand or cartridge filter; purification example)

- Ports: `inlet`, `outlet`.
- Parameters: `rated_flow` m3/h, 20, [0.01, 100000]; `clean_pressure_drop` bar at rated flow, 0.2, [0.001, 20]; `housing_fraction` 1, 0.3, [0, 1] (share of the clean drop that is quadratic housing loss; the rest is linear media loss); `change_pressure_drop` bar, 1.0, [0.01, 50].
- Inputs: `clogging` 1, 0.0, [0, 0.99].
- Law: `LinearQuadraticResistance` with `r_lin = (1 - h) * dp_clean / Q_rated / (1 - clogging)` and `r_quad = h * dp_clean / Q_rated**2`.
- Observables: `volume_flow` m3/h; `pressure_drop` bar; `dp_ratio` 1 (pressure drop divided by the clean pressure drop at the same flow).
- Envelope: `change_required` warning when `pressure_drop > change_pressure_drop`; `over_rated_flow` warning when `volume_flow > 1.25 * rated_flow`.
- Modes: `idle` (`abs(volume_flow) < 0.01`), `needs_change` (`pressure_drop > change_pressure_drop`), `loaded` (`clogging >= 0.3`), `clean`.
- Revised after verification: a code-emitted `reverse_flow` warning fires below -0.01 m³/h, because the forward-flow envelope checks do not apply to reverse flow.

### 8.11 `uv_reactor` (UV disinfection; purification example)

- Ports: `inlet`, `outlet`.
- Parameters: `rated_flow` m3/h, 20, [0.01, 100000]; `rated_pressure_drop` bar, 0.1, [0.001, 10]; `volume` L, 15, [0.1, 100000]; `fluence_rate` mW/cm2, 20, [0.1, 1000] (average at full lamp output); `required_dose` mJ/cm2, 40, [1, 1000].
- Inputs: `lamp_output` 1, 1.0, [0, 1].
- Law: quadratic resistance through the rated point. Dose is the idealised plug-flow average: `residence_time = volume / |Q|` and `dose = fluence_rate * lamp_output * residence_time` (mW/cm² × s = mJ/cm²), both capped at 1e6.
- Observables: `volume_flow` m3/h; `pressure_drop` bar; `residence_time` s; `dose` mJ/cm2.
- Envelope: `underdose` warning when `volume_flow > 0.01 and dose < required_dose`; `lamp_off` warning when `lamp_output < 0.01 and volume_flow > 0.01`.
- Modes: `idle` (`abs(volume_flow) < 0.01`), `underdosing` (`dose < required_dose`), `disinfecting`.
- The defaults give about 54 mJ/cm² at rated flow and fall below 40 mJ/cm² above about 27 m³/h, which is the diagnostic the purification example relies on.
- Revised after verification: a code-emitted `reverse_flow` warning fires below -0.01 m³/h; the 1e6 cap on `residence_time` and `dose` is a lower bound on the true value, and the `underdose` verdict is unaffected by it when `lamp_output >= 0.01`.

## 9. MCP server

The server (`worldparts mcp`, stdio) is built on MCP Python SDK v2 (`from mcp.server import MCPServer`). Tools return pydantic models so clients receive `structuredContent` with an `outputSchema`. Systems live in an in-process store keyed by `system_id`. Tools:

| Tool | Purpose |
|---|---|
| `list_components(query=None)` | Search the catalogue; returns id, alias, name, summary, ports, key parameters, fidelity. |
| `describe_component(component)` | Everything an agent needs to use a component: ports, parameters and inputs with units, defaults and limits, observables, modes, envelope and warning codes, contracts, provenance, bindings. |
| `create_system(name, description=None)` | New empty system; returns `system_id`. |
| `add_component(system_id, name, component, parameters={}, inputs={})` | Instantiate; returns resolved values with units and any issues. |
| `set_values(system_id, values)` | Set inputs or parameters by path, e.g. `{"faucet.lift": 0.5, "mains.pressure": "2 bar"}`. |
| `connect(system_id, a, b)` | Connect two ports; errors list the valid ports. |
| `disconnect(system_id, a, b)`, `remove_component(system_id, name)` | Editing. |
| `check_system(system_id)` | Structural pre-flight with stable issue codes. |
| `solve(system_id, variables=None, units=None)` | Steady operating point; values with units, modes, warnings. |
| `simulate(system_id, duration, step="1 s", events=[], variables=None, max_points=200)` | Time series, downsampled, with per-variable min, max and final summaries, warnings and mode changes. |
| `list_variables(system_id)` | Every path with kind (input, parameter, state, observable, port), unit and description. |
| `get_system(system_id)`, `load_system(document)` | Round-trip the system document. |
| `run_contracts(component)` | Run a component's scenarios and contracts and report pass or fail. |
| `export_system(system_id, target)` | `wntr_inp` in v0.1 (requires the `wntr` extra). |

Error messages are written for an agent: they name the offending path and list valid alternatives. Manifests are also exposed as resources at `worldparts://components/{id}`.
Revised after implementation:
- `solve` and `simulate` return observables, states and port pressures when no variables are given; `variables` also accepts an instance name (all its variables) or `"*"`; `simulate` takes `units`; `list_variables` takes an optional `component` filter; `load_system` also accepts YAML text. Values are rounded to 6 significant digits; system ids are `s1`, `s2` and so on.
- `add_component` reports the check issues that name the new instance, so a fresh part shows unconnected-port warnings until it is wired.
- `describe_component` also returns each variable's `quantity`, table-column descriptions and references, each scenario's `system`, `simulate` and `expect` (usable as templates), and each contract's rule and sweep.
- Extra tools such as `export_system` register through `create_server(registrars=[...])` or `EXTRA_TOOL_REGISTRARS`.

## 10. CLI

`worldparts list [QUERY]`, `worldparts describe COMPONENT`, `worldparts validate [PATHS...]` (schema-validate manifests and system documents), `worldparts check-catalog [--component ID]` (run all scenarios and contracts, non-zero exit on failure), `worldparts solve SYSTEM.yaml [--var PATH ...] [--json]`, `worldparts simulate SYSTEM.yaml [--duration] [--step] [--var PATH ...] [--json]`, `worldparts export SYSTEM.yaml --target wntr_inp`, `worldparts mcp`.
Revised after implementation: every command takes `--json`; exit code 0 on success, 1 when validation or a catalogue check fails, 2 for usage and worldparts errors. `validate` without paths checks the package catalogue and, for system documents, also runs the pre-flight check. `check-catalog` takes `--verbose`.

## 11. WNTR adapter

`to_wntr(system) -> wntr.network.WaterNetworkModel` and `compare_with_wntr(system) -> ComparisonReport`. Mapping: `supply` and `drain` to reservoirs (head from gauge pressure), `pipe` to a Darcy-Weisbach pipe, `valve` to a throttle control valve whose minor-loss coefficient reproduces the Kv at a nominal diameter, `centrifugal_pump` to a head pump with the fitted curve and speed setting, `tank` to a tank, `media_filter` and `uv_reactor` to equivalent resistances. Components WNTR cannot represent (the faucet's mixing, the heater's thermal map) are reported as unsupported rather than approximated silently. The comparison reports per-link flow and per-node pressure differences, and explains the expected sources of divergence (curve refitting, head-loss formula, units). This is the report's "load the same component into several hosts and measure divergence" experiment, in code.

## 12. Quality bar

- Type hints on public functions, docstrings on public classes and functions, `uv run ruff check` and `uv run ruff format --check` clean.
- `uv run pytest` passes on Linux and Windows. Physics tests compare against hand calculations (Kv at 1 bar, pump operating point from the intersection of two quadratics, Darcy-Weisbach against `fluids`, energy balance of mixing).
- `uv run worldparts check-catalog` passes.
- No network access at runtime; no global state except the MCP server's system store.
