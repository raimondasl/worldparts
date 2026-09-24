# worldparts

**Tested water components an AI agent can wire together instead of writing physics code.**

worldparts gives an AI agent parts to build water systems from: pumps, valves, pipes, tanks, media filters and UV
reactors that it selects, sets and connects through typed tools. Every part declares its
units, hard limits, operating envelope and warning codes. Every part ships scenarios and
behavioural contracts that run in CI. The agent composes the system and the library does
the physics. When a result falls outside a model's validity, the result carries a warning.

The focus is **pumping, water treatment and distribution**: design checks, what-if analysis,
and calibration and diagnosis against plant data.

Our own benchmark keeps the claims honest. On small, fully specified calculations, frontier
models writing Python from scratch were as accurate as agents using worldparts, and several
times cheaper ([results](docs/benchmark-results-v0.2.md)). worldparts therefore aims at
what a one-off script does not give you: a reusable system model that can be reviewed and
rerun, calibration and diagnosis against measurements, real product data with provenance,
and systems too large or long-running to rebuild each time.

> Status: v0.1.0, alpha. The interfaces may still change. See [Status](#status-and-roadmap).

## What an agent can do

The call sequence below builds a small purification skid over MCP (tank, pump, media
filter, throttling valve, UV reactor, discharge), finds a problem and fixes it in five
calls. The arguments are abbreviated; each call is one typed MCP tool call with its result.

```text
list_components(query="uv")                          -> uv_reactor
describe_component(["tank", "centrifugal_pump", "media_filter", "valve", "uv_reactor",
                    "drain"])                        -> ports, parameters, units, limits
load_system({"worldparts_system": "0.1", "name": "skid",
             "components": [{"name": "raw", "type": "tank"}, ...,
               {"name": "valve", "type": "valve", "parameters": {"kv": 40}}, ...],
             "connections": [["raw.outlet", "pump.inlet"], ... down the line to out.port]})
                                   -> system_id "s1", issues: 0 errors (raw.inlet is capped)
solve(s1)                                            -> pump 36.8 m3/h, UV dose 29.3 mJ/cm2
    warnings: pump.beyond_curve, filter.over_rated_flow, uv.underdose
solve_for(s1, target="uv.dose", value="45 mJ/cm2", vary="valve.opening", lower=0.05, upper=1)
                                                     -> opening 0.393, 24.0 m3/h, no warnings
```

The agent writes no equations. The warnings tell it that the pump is running past its
curve, the filter is overloaded and the water is under-disinfected. The fix is a hand
check away: dose = 20 mW/cm² × 15 L / (24 m³/h) = 20 × 2.25 s = 45 mJ/cm².
`tests/test_docs.py` replays this sequence against the server.

## Install

worldparts needs Python 3.11 or later. The commands below use [uv](https://docs.astral.sh/uv/).

Try it without installing:

```sh
uvx --from git+https://github.com/raimondasl/worldparts worldparts list
```

Add it to a uv project:

```sh
uv add git+https://github.com/raimondasl/worldparts
```

With the optional WNTR/EPANET adapter:

```sh
uv add "worldparts[wntr] @ git+https://github.com/raimondasl/worldparts"
```

## Connect the MCP server

The server runs over stdio: `worldparts mcp`. Run by hand in a terminal, it prints nothing
and waits for an MCP client on stdin; you normally let the client start it (below). Stop
it with Ctrl+C.

**Claude Code:**

```sh
claude mcp add worldparts -- uvx --from git+https://github.com/raimondasl/worldparts worldparts mcp
```

**Claude Desktop or any other MCP client:** add the server to the client's configuration
(for Claude Desktop, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "worldparts": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/raimondasl/worldparts", "worldparts", "mcp"]
    }
  }
}
```

The server has 18 tools: `list_components`, `describe_component`, `run_contracts`,
`create_system`, `add_component`, `remove_component`, `set_values`, `connect`,
`disconnect`, `add_control`, `remove_control`, `check_system`, `list_variables`, `solve`,
`solve_for`, `simulate`, `get_system` and `load_system`. `add_control` and
`remove_control` manage control loops (a PI loop or a hysteresis switch that reads one
variable and writes one input); `solve`, `solve_for` and `simulate` report them under
`controls`, and `variables=["control"]` selects their results. When the `wntr` package is installed, the
[WNTR adapter](docs/wntr-adapter.md) adds `export_system` and `compare_with_wntr`. Systems
live as long as the server process; `get_system` and `load_system` save and restore them.

The server's instructions steer an agent to the short path for a new system, the one in
the sequence above: `list_components`, then `describe_component` with every part in one
call (it takes a list of up to 12 ids or aliases and returns `{components: [...]}` in that
order), then one `load_system` call with the complete document (components with parameters
and inputs, connections and controls). Its `issues` are the `check_system` report, so the
next call is `solve`, `solve_for` or `simulate`. The step-by-step tools (`create_system`,
`add_component`, `connect`, `set_values`) are for editing, with `check_system` after edits.

## Python in 20 lines

A pump lifts water from a ground tank through a DN50 riser to a free discharge 25 m up:

```python
import worldparts as wp

s = wp.System("rooftop lift")
s.add("tank", "tank", initial_level="1.5 m")  # ground tank, 2 m diameter by default
s.add("pump", "centrifugal_pump")  # default curve: about 25 m3/h at 26 m, 2900 rpm
s.add("valve", "valve", kv="40 m3/h")  # roughly a DN50 globe valve
s.add("riser", "pipe", length="60 m", diameter="50 mm", roughness="0.045 mm",
      height_difference="25 m", minor_loss=1)  # 1 = exit loss of the free discharge
s.add("roof", "drain")
for a, b in [("tank.outlet", "pump.inlet"), ("pump.outlet", "valve.port_a"),
             ("valve.port_b", "riser.port_a"), ("riser.port_b", "roof.port")]:
    s.connect(a, b)

r = s.solve()
print(f"{r['pump.volume_flow']:.1f} m3/h, {r['pump.head']:.1f} m, {r['pump.efficiency']:.0f} %")
print([w.path for w in r.warnings])
sim = s.simulate(duration="30 min", step="10 s")
print([(w.time, w.path) for w in sim.warnings])
```

Output:

```text
14.7 m3/h, 31.0 m, 54 %
['pump.outside_preferred_region']
[(0.0, 'pump.outside_preferred_region'), (960.0, 'tank.low_level'), (1190.0, 'tank.drawing_air'), (1200.0, 'tank.tank_empty'), (1200.0, 'pump.low_flow')]
```

The pump runs at 61 % of its best-efficiency flow, which is below the preferred operating
region (an `info`). The simulation drains the ground tank. As the lift grows, the flow
falls from 14.7 to 13.6 m³/h, so a hand estimate is 4.71 m³ at a mean of 14.2 m³/h, or 20
minutes. The simulated tank empties at 1200 s. The tank then reports that the pump is
drawing air, and the pump reports low flow.

Plain numbers are in each variable's declared unit. Strings may carry their own unit
(`"1.5 m"`, `"40 m3/h"`). Pressures are gauge unless stated otherwise.

### System documents and the CLI

The CLI works on **system documents**: YAML (or JSON) files that list the components with
their parameters, the connections and, optionally, a `simulation` block. This is the lift
above as a document:

```yaml
worldparts_system: "0.1"
name: rooftop lift
components:
  - {name: tank, type: tank, parameters: {initial_level: 1.5}}
  - {name: pump, type: centrifugal_pump}
  - {name: valve, type: valve, parameters: {kv: 40}}
  - name: riser
    type: pipe
    parameters: {length: 60, diameter: 50, roughness: 0.045, height_difference: 25, minor_loss: 1}
  - {name: roof, type: drain}
connections:
  - [tank.outlet, pump.inlet]
  - [pump.outlet, valve.port_a]
  - [valve.port_b, riser.port_a]
  - [riser.port_b, roof.port]
simulation: {duration: 30 min, step: 10 s}
```

Plain numbers are in the declared units (`diameter: 50` is 50 mm); strings with units work
too (`diameter: 50 mm`). You do not have to write documents by hand: `System.to_dict()`
returns one (with full component ids and every value in declared units). Continuing the
Python example:

```python
import yaml

s.reset_states()  # back to the initial tank level after the simulation
s.simulation = {"duration": "30 min", "step": "10 s"}
with open("lift.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(s.to_dict(), f, sort_keys=False)
```

Then, from the same directory:

```sh
uv run worldparts validate lift.yaml
uv run worldparts solve lift.yaml --var pump
uv run worldparts simulate lift.yaml                        # uses the simulation block
uv run worldparts simulate lift.yaml --duration "5 min" --step "1 s" --var tank.level
uv run worldparts solve lift.yaml --units "*.volume_flow=m3/h"
```

Pipes, valves, supplies and drains report flow in L/min; pumps, tanks, filters and UV
reactors in m³/h. `--units PATTERN=UNIT` (repeatable) converts any reported variable, for
example `--units "*.volume_flow=m3/h"` or `--units "pump.inlet.p=bar absolute"`. The other
commands are `list`, `describe`, `check-catalog` and, with the WNTR adapter, `export`; every
command except `mcp` takes `--json` (`worldparts --help`).

More documents and scripts, including a purification skid and a pump lift to a rooftop
tank, are in [examples/](examples/README.md). They are in the repository, not in the
installed package: clone it (`git clone https://github.com/raimondasl/worldparts`) to run
them.

### Controls

A control reads one reported variable and writes one component input (design 13.1). A PI
loop holds a booster pump's discharge at 3 bar:

```python
b = wp.System("booster")
b.add("mains", "supply", pressure="0.5 bar")
b.add("pump", "centrifugal_pump", speed=0.5)
b.add("zone", "valve", kv="10 m3/h")
b.add("out", "drain")
for a, c in [("mains.port", "pump.inlet"), ("pump.outlet", "zone.port_a"),
             ("zone.port_b", "out.port")]:
    b.connect(a, c)
b.add_control("duty", "pi", measure="pump.outlet.p", setpoint="3 bar",
              actuate="pump.speed", gain=0.1, integral_time="2 s",
              output_min=0.3, output_max=1.2)

r = b.solve()  # goal-seeks the speed that holds 3 bar
print(f"speed {r['pump.speed']:.3f}, {r['pump.outlet.p']:.2f} bar")
sim = b.simulate("2 min", "1 s", events=[{"at": "60 s", "set": {"zone.opening": 0.5}}])
print(f"speed {sim.final['pump.speed']:.3f}, {sim.final['pump.outlet.p']:.2f} bar")
```

Output:

```text
speed 0.935, 3.00 bar
speed 0.885, 3.00 bar
```

`solve()` goal-seeks every PI actuator within its output limits and leaves it at the value
found; `simulate()` runs the loop as a sampled-data controller whose command acts from the
sample where it is computed, like an event. A `hysteresis` control switches an input
between two values (a level switch on a fill pump). Results carry `r.controls` and the
series `control.duty.output` and `control.duty.measure`. System documents list controls
under `controls`, and simulation events may ramp a value: `{at, ramp: {path: [start,
end]}, over}`. See [examples/booster_station.yaml](examples/booster_station.yaml).

## The catalogue

Eleven components, all with the id prefix `worldparts.hydraulic.`. The full reference
(ports, parameters with units, defaults and limits, modes, warnings, bindings and
provenance) is generated from the manifests: [docs/catalog.md](docs/catalog.md).

| Component | What it models | Key warnings |
|---|---|---|
| `centrifugal_pump` | Variable-speed pump from head, power and NPSH curves; affinity laws, efficiency, best-efficiency point, wear inputs (`wear_head`, `wear_efficiency`) | `cavitation`, `low_flow`, `beyond_curve`, `outside_preferred_region`, `motor_overload`, `reverse_flow` |
| `tank` | Open cylindrical tank with bottom ports or a top inlet (`inlet_height`), level-dependent head, overflow, mixed temperature | `tank_empty`, `low_level`, `tank_overflow`, `drawing_air` |
| `media_filter` | Sand or cartridge filter; linear media loss that grows with clogging plus housing loss | `change_required`, `over_rated_flow`, `reverse_flow` |
| `uv_reactor` | UV disinfection with a rated pressure drop and a plug-flow average dose | `underdose`, `lamp_off`, `reverse_flow` |
| `pipe` | Darcy-Weisbach pipe (Churchill friction factor), minor losses, static head | `high_velocity`, `high_relative_roughness`, `below_vapour_pressure` |
| `valve` | Control valve: Kv, linear, equal-percentage or quick-opening characteristic, leakage, actuator lag | `high_pressure_drop` |
| `check_valve` | Non-return valve: Kv forward, small leakage in reverse | none |
| `supply` | Fixed-pressure source: main, reservoir, pressurised line | none |
| `drain` | Open discharge to atmosphere | `backflow` |
| `leak` | Orifice to atmosphere for burst or background leakage, Q = Cd A sqrt(2 dp / rho); opening can change mid-run | `backflow` |
| `mixing_faucet` | Single-lever mixer with energy-balance mixing (secondary) | `scald_risk`, `crossflow`, `back_siphonage`, `low_supply_pressure` |
| `instantaneous_water_heater` | Flow-switched tankless heater with a power limit (secondary) | `setpoint_not_met`, `below_activation_flow` |

The runtime is a quasi-steady hydraulic and thermal network solver with time stepping for
tanks and actuators. It does not model water hammer, compressible flow, pipe heat loss or
temperature-dependent water properties. Each manifest lists its own assumptions and what it
leaves out.

## Manifests, contracts and provenance

Each component is a YAML manifest plus a Python class. The manifest is what the agent
reads (through `describe_component`) and what the runtime and the tests enforce. It
declares ports, parameters, inputs, states and observables with units and hard limits. It
also declares modes, an envelope of soft validity rules, and code-emitted warnings.
Scenarios are small systems with expected results, taken from hand calculations. Contracts
are properties checked over a sweep, such as "flow never falls as the valve opens" or
"mass in equals mass out". `worldparts check-catalog` runs every scenario and contract, so
each manifest is a tested claim. Provenance records the sources of the equations and where
the default data came from. The defaults are generic illustrative values, never a specific
product. The format is specified in
[spec/component-manifest.md](spec/component-manifest.md).

A trimmed excerpt of the valve manifest
([full file](src/worldparts/catalog/hydraulic/valve.yaml)):

```yaml
id: worldparts.hydraulic.valve
summary: Throttling valve with Kv sizing, inherent characteristic, leakage and first-order actuator lag.
ports:
  - {name: port_a, type: fluid, medium: water, description: Inlet in the forward direction.}
  - {name: port_b, type: fluid, medium: water, description: Outlet in the forward direction.}
parameters:
  - {name: kv, type: number, unit: m3/h, default: 2.5, minimum: 0.0001, maximum: 100000, description: ...}
inputs:
  - {name: opening, description: "Commanded valve opening: 0 closed, 1 fully open.", unit: "1", default: 1.0, minimum: 0, maximum: 1}
observables:
  - {name: pressure_drop, unit: bar, pressure_reference: difference, description: Pressure at port_a minus pressure at port_b.}
envelope:
  - {code: high_pressure_drop, severity: warning, condition: "pressure_drop > 3", message: ...}
scenarios:
  - id: kv-at-1-bar          # hand calculation: 2.5 m3/h * sqrt(1000 / 998.2) = 41.7042 L/min
    system: {components: [...], connections: [...]}
    expect:
      - {variable: dut.volume_flow, value: 41.7042, abs_tol: 0.01}
contracts:
  - id: mass-conservation
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check: {type: equal, left: dut.port_a.m_flow, right: -dut.port_b.m_flow, abs_tol: 1.0e-9}
implementations:
  reference: {python: "worldparts.components.valves:TwoWayValve"}
  modelica: {class: Buildings.Fluid.Actuators.Valves.TwoWayLinear, ...}
  wntr: {element: TCV, ...}
provenance:
  data: {acquisition: generic, notes: Default parameters are generic illustrative values ...}
  license: Apache-2.0
  data_license: CC0-1.0
```

## Evidence

**Our benchmark (v0.2).** 32 tasks (calculations and design-judgement reviews) ran under two
conditions: an agent with only the worldparts MCP tools, and an agent writing Python from
scratch with numpy, scipy, fluids and wntr. Claude Sonnet 5 and Claude Opus 5.5 each ran
every task in both conditions. Both passed 97 to 100 percent in both conditions. The
from-scratch agent used about one eighth of the tokens. The only worldparts failure traced to
an ambiguous component description, since fixed. Details, harness problems found along the
way, and limits: [docs/benchmark-results-v0.2.md](docs/benchmark-results-v0.2.md).

The design follows a research report,
[World model libraries for AI agents](docs/research/world-model-libraries-for-ai-agents.md).
Three of its findings drive the design:

- **LLM-written physical models compile but rarely simulate correctly.** In ModiGen,
  GPT-4o's Modelica loaded 95.56 % of the time but was functionally correct only 24.44 % of
  the time. In a 2026 fluid-systems benchmark, ten LLMs scored 0.0 on simulation fidelity.
- **Agents do well when they compose verified parts through typed tools.** EPANET-Agentic,
  which gives an agent function-call tools over WNTR, reported 100 % task success on three
  benchmark networks. An earlier framework that generated free-form EPANET code scored 56
  to 81 %.
- **No existing library packages components for agents.** The physics exists in Modelica
  Buildings, WNTR/EPANET and WaterTAP. None of them ships an agent-readable manifest,
  behavioural contracts or data provenance.

Before this release, one agent used the MCP server, with tools only, for two tasks: a pump
lift to a rooftop tank and a purification skid with a clogging filter. It made 43 tool
calls with one error. Its answers agreed with its hand calculations. It reported 16
friction items, and most were fixed before this release. See
[docs/agent-trial.md](docs/agent-trial.md). That was one informal trial, not a benchmark.

## Status and roadmap

v0.1.0 is the first public release: manifest format 0.1, 11 components, the reference
runtime, the `System` API, the MCP server and the CLI. The WNTR/EPANET adapter is described
in [docs/wntr-adapter.md](docs/wntr-adapter.md). The composition benchmark (v0.2) is done
([results](docs/benchmark-results-v0.2.md)). Next come pump systems and diagnostics (v0.3), real product data with provenance (v0.4) and a
Modelica backend (v0.5). See [docs/roadmap.md](docs/roadmap.md), the design contract
[docs/design.md](docs/design.md) and the [changelog](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, how to add a component and
how contracts are tested. The design contract changes first; code follows.

## License

Apache-2.0 for the code and manifests ([LICENSE](LICENSE)). The default parameter data in
the manifests is CC0-1.0.
