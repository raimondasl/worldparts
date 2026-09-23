# Authoring a worldparts component

This guide is for the engineers writing the remaining v0.1 components (mixing faucet,
instantaneous water heater, centrifugal pump, tank, media filter, UV reactor). It explains
the base API, the conventions the core relies on, and how your scenarios and contracts are
tested. The contract is `docs/design.md`; this file shows how to meet it with the code that
exists.

A component is two files:

1. `src/worldparts/catalog/hydraulic/<alias>.yaml`: the manifest (design section 4),
   validated by `src/worldparts/schemas/component-manifest.schema.json`.
2. A class in `src/worldparts/components/<family>.py`, named by the manifest's
   `implementations.reference.python: "worldparts.components.<family>:<Class>"`.

There is no registry to edit. `tests/test_catalog.py` discovers every manifest in the package
and runs it, so your component is tested as soon as the YAML exists.

## 1. Conventions you must follow

**SI inside, declared units outside.** Your class only ever sees SI: `self.parameters`,
`self.inputs` and `self.states` hold SI values, and `observables()` returns SI. The system
converts to and from the declared unit of every variable. Some SI forms to remember:

| Declared unit | What your code sees |
|---|---|
| `bar` (gauge, the default for pressures) | Pa **absolute** (3 bar gauge is 401325.0) |
| `bar` with `pressure_reference: difference` | Pa difference (0.2 bar is 20000.0) |
| `degC` | K |
| `K` or `degC` with `quantity: temperature_difference` | K difference (a 43 K rise is 43.0) |
| `L/min`, `m3/h`, `L/s` | m3/s |
| `%` and `1` | a fraction (50 % is 0.5; return efficiency as 0.72, not 72) |
| `kW` | W |
| `rpm` | rad/s (so `speed * rated_speed` round-trips; do not convert yourself) |
| `L`, `m3` | m3 |
| `mW/cm2` | W/m2; `mJ/cm2` is J/m2, so `dose = fluence * t` needs no factor |
| `min`, `h` | s |
| `kWh/m3` | J/m3 (1 kWh/m3 is 3.6e6; `shaft_power / volume_flow` needs no factor) |

**Pressures default to gauge.** Any pressure-valued variable (unit `Pa`, `kPa` or `bar`) is
gauge unless you declare `pressure_reference: absolute` or `difference`. Pressure drops
(`pressure_drop`, `clean_pressure_drop`, `change_pressure_drop`, ...) must declare
`pressure_reference: difference`; the manifest loader rejects a pressure variable whose name
contains `drop`, `difference`, `delta`, `dp`, `rise` or `loss` and is still gauge. The loader
also rejects `pressure_reference` on a non-pressure unit (variables and table columns).

Users may name a reference in a value string: `"2 bar absolute"`, `"2 bar abs"`,
`"2 bar (a)"`, `"1.5 bara"`, `"3 bar gauge"`, `"3 barg"`; the value is converted to the
variable's declared reference. `"1 atm"` without a reference is rejected for gauge variables
(it almost always means atmospheric pressure, which is `0 bar` gauge). Results carry the
reference: `SolveResult.to_dict()` gives `{"value", "unit", "reference"}` for pressures, and
`result.get(path, unit="bar absolute")` converts a gauge value to absolute.

**Temperatures default to absolute.** A temperature difference (a rise, an approach, a
delta) declared in `K` would otherwise convert like an absolute temperature: 35 K would read
as -238.15 degC. Declare it with `quantity: temperature_difference`; it then converts by
scale only (35 K = 35 degC = 63 degF), strings such as `"9 degF"` parse as 5 K, results carry
`"reference": "difference"` (in `to_dict()`, `SolveResult.references` and the MCP output),
and `describe_component` shows the quantity. The loader rejects a temperature variable whose
name contains `rise`, `difference`, `delta`, `drop`, `increase`, `decrease`, `approach` or
`dt` without it, and the schema allows the quantity only with unit `K` or `degC`. Prefer
`K` for differences. Example: the heater's
`{name: temperature_rise, unit: K, quantity: temperature_difference, ...}`.

An energy per volume (`kWh/m3`) has the dimension of a pressure but is not one: it has no
gauge offset and takes no `pressure_reference`.

**One namespace per instance.** Parameters, inputs, states, observables and ports share the
path namespace `<instance>.<name>`, so every name must be unique across them. States are
reported in results automatically (`tank.level`, `valve.position`): **do not redeclare a state
as an observable.** (Design 8.4 and 8.9 list the valve `position` and the tank `level` and
`temperature` as observables too; declare them once, as states. `list_variables` then shows
their kind as `state`. This deviation still has to be recorded in design.md.)

String and table parameters are not part of solve or simulation results (read them with
`System.get`); `System.variables()` marks them `reported=False`. They also cannot appear in
mode or envelope conditions: the expression language has no string or table values, and the
loader rejects such a condition with a message saying so.

**Signs.** Port `m_flow` is positive into the component. A two-port component's forward
direction is from its first port to its second; create its main branch in that direction so
`volume_flow = m / rho` is positive forward.

**Medium.** Use `self.rho`, `self.cp`, `self.mu` (water, constant properties) and
`worldparts.media.vapour_pressure(T_kelvin)`, and `worldparts.media.G` for gravity.

**Units vocabulary.** Manifest units must come from the list in design 3.1 (the schema
enforces it), plus `kWh/m3` (specific energy), added at integration.

## 2. The base API

```python
from worldparts.components.base import Component, NetworkBuilder, NetworkView, first_order
```

The system drives your component through these hooks (all optional except `build` and
`observables`):

| Hook | When | What to do |
|---|---|---|
| `__init__(name, parameters, inputs)` | once | Inherited. Values arrive in SI and validated. Calls `init_states()`. Override only if you must, and call `super().__init__`. |
| `check_parameters(cls, parameters) -> list[str]` | before a parameter change is accepted | Cross-parameter rules on SI values, e.g. tank `initial_level <= height`. Return messages; the system raises `InvalidValueError` and `check()` reports `invalid_value`. It receives the whole trial parameter set of a `set_values` batch, so rules see consistent values whatever the key order. |
| `check_states(cls, parameters, states) -> list[str]` | before a batch that sets parameters or states is accepted, and in `check()` | Cross-checks of states against parameters on SI values, e.g. tank `level <= height`. It receives the trial parameters and the trial states (after re-initialisation and the batch's own state values); a problem rejects the whole batch (`InvalidValueError`) and `check()` reports `invalid_value` (for example a system document with `states: {level: 5}` on a 3 m tank). |
| `init_states()` | at construction, in `reset_states()`, and when a parameter batch changes a state's initial value (outside simulations) | Set `self.states[...]` from parameters or inputs. Default: the manifest state `default`, else 0. **Only write `self.states`**: the system calls it with trial parameters to find which initial values a batch changes, and `to_dict()` calls it to find which states differ from their initial values. A batch re-initialises only the states whose initial value it changes (a tank's `initial_level` resets `level`; its `port_kv` keeps the water). |
| `build(nb)` | whenever the topology or a parameter changes | Create nodes and branches with the `NetworkBuilder`; store the handles on `self`. Must be re-runnable. |
| `update_laws()` | before **every** network solve, and in `check()` | Push parameters, inputs and states into the law objects, fixed-node pressures (`node.p`) and temperatures (`node.T`), and gate directions. Must not fail on the defaults. |
| `settle()` | at the start of `solve()` | Set `steady: settle` states to equilibrium (e.g. `position = opening`). |
| `start_simulation()` | once at the start of every `simulate()` | Reset per-run accumulators (e.g. a spilled-volume counter). Do not reset states: a simulation continues from the current state (`simulate(restore=True)` puts parameters, inputs and states back afterwards; the component needs no code for it). |
| `update_fast_states(dt)` | twice per simulation sample (see below) | First-order lags with `first_order(x, target, tau, dt)`. Default calls `settle()`. |
| `observables(view) -> dict` | after each solve | SI value (or `None`) for **every** observable in the manifest. Missing keys raise `ContractError`. |
| `extra_warnings(view) -> list` | after each solve | Code-emitted warnings via `self.warning(code, message=None)`; the code must be declared in the manifest `warnings` list. |
| `integrate(dt, view)` | after recording each simulation sample (not after the last) | Explicit Euler for storage states (tank level and temperature) over the step to the next sample. |

**Simulation sample order.** Samples are at every multiple of `step`, at every event time
(the step is split there) and at `duration` (a shorter last step when `duration` is not a
multiple of `step`; events after `duration` are rejected). Going from sample `t0` to `t`:

1. `update_fast_states(t - t0)`: lags advance over `(t0, t]` with the command that held then;
2. events due at `t` are applied (`set_values` per event);
3. `update_fast_states(0)`: instantaneous responses to the new commands. With `dt = 0`,
   `first_order` returns the target when `tau = 0` and leaves the state alone otherwise, so a
   valve with a 10 s actuator still reads its old position at the event time and then follows
   `1 - exp(-(t - t_event) / tau)` exactly, independent of the step size;
4. `update_laws()`, solve, record observables, modes and warnings;
5. `integrate(t_next - t, view)`.

Before step 4 the system sets `self.time_step` on every component to `t_next - t`, the step
over which this solution will be integrated (at the last sample, the step that led to it);
it is `None` in a steady `solve()`. A storage component uses it in `update_laws()` to keep an
explicit step from taking more than it holds (the tank caps its port outflow with it).

Because storage states are integrated after recording, anything `integrate` computes for
the step `[t, t_next]` (the tank's `overflow_rate`, and a mode or warning based on it) is
reported at `t_next`. That is the natural reading for explicit Euler (the rate that produced
the current level); keep it in mind when writing expectations.

`NetworkBuilder` (argument of `build`):

- `nb.port(name) -> Node`: the node of a port. Connected ports share one node (a junction).
- `nb.node(label) -> Node`: an internal free node (e.g. the faucet mixing node `C`).
- `nb.fixed_node(label, p_abs, T) -> Node`: an internal fixed-pressure node, e.g. the faucet
  spout at `P_ATM` or the tank bottom at `P_ATM + rho * g * level`. Change `node.p` and
  `node.T` in `update_laws()`; they are read at every solve.
- `nb.branch(a, b, law, thermal=None, label=None) -> Branch`: flow is positive from `a` to `b`.
- `nb.injection(node, m, T)`: a fixed mass inflow (rarely needed).
- `nb.is_connected(port)`: False when the port is capped (not connected). The faucet's
  `low_supply_pressure` warning uses this to ignore capped inlets.

`NetworkView` (argument of `observables`, `extra_warnings`, `integrate`), all SI:
`view.p(node)`, `view.T(node)` (K or `None` when no water flows in), `view.m(branch)`,
`view.dp(branch)` (`p_a - p_b`), `view.port_p(port)` (absolute Pa), `view.port_T(port)`,
`view.port_m_flow(port)` (into the component), `view.port_connected(port)`.

The system itself computes and reports `<instance>.<port>.p|m_flow|T` for every port, all
parameters, inputs and states, modes (from the manifest `modes` conditions) and envelope
warnings. You never format output.

## 3. Laws (`worldparts.laws`)

Every law's `dp(m)` is continuous and strictly increasing; use only these and the network
stays well posed. Coefficients are plain attributes: create the law in `build()`, set its
coefficients in `update_laws()`.

| Law | Use | Coefficients |
|---|---|---|
| `QuadraticResistance(k)` | Kv elements (faucet cartridges, spout, heater, UV reactor) | `k = kv_to_k(kv_si, rho)`; regularisation scales with `k` automatically: exactly quadratic above 0.2 % of the flow at 1 bar, a C1 cubic below (slope at zero `m_eps / k**2`, `m_eps` = 1e-3 of the flow at 1 bar). |
| `LinearQuadraticResistance(r_lin, r_quad)` | media filter | Pa/(m3/s) and Pa/(m3/s)^2 in terms of `Q = m / rho`. |
| `PipeLaw(length, diameter, roughness, minor_loss, height_difference)` | pipe | SI lengths. |
| `PumpLaw(a, b, c, speed, eps)` | pump | `H = a*s^2 + b*s*Q + c*Q*abs(Q)` in m with Q in m3/s; needs `a > 0`, `b <= 0`, `c < 0`. |
| `CheckValveLaw(k, leakage)` | check valve | reverse coefficient `k * leakage`. |
| `GateLaw(k, blocked_direction, leakage=1e-6, limit=None)` | tank ports | set `gate.blocked_direction = "forward"` (a to b blocked), `"reverse"` or `None` in `update_laws()`; it may change every step. With `limit=None` the blocked direction is leakage only; with `limit` (kg/s) it follows the open law up to the limit and closes beyond it with `GATE_STIFFNESS` (1e10 Pa per kg/s, about 1e-5 kg/s per bar past the cap); `limit=0` closes it at zero flow. |

Helpers: `kv_to_k(kv_si)`, `k_to_kv(k)`, `flow_at_1bar(k)`, `churchill_friction_factor(re, rr)`.
A rated point converts to a Kv as `kv_si = Q_rated / sqrt(dp_rated / 1e5 * 1000 / rho)`.

`ideal_connection()` is the near-ideal joint the supply and drain use between their fixed
node and their port: a linear resistance of 1e-6 Pa per kg/s (at most 1e-6 bar up to
100 m3/s). Do not use it inside a component to join two points that could be at different
fixed pressures: `check()` reports fixed nodes at different pressures joined only by ideal
joints as the error `boundary_short_circuit` (the flow would be unbounded). A tank's
internal fixed node should be joined to its ports with its `GateLaw(port_kv)`, as design 8.9
says, not with an ideal joint.

**Recirculation is supported.** Temperatures are solved exactly in loops (a pump circuit,
a tank recirculation line): an acyclic flow graph is swept once in flow order, and a graph
with a flow cycle is solved as a linear system, with thermal maps linearised and iterated
to 1e-9 K. Thermal maps must be continuous in `T_in` (piecewise affine such as
`min(setpoint, T_in + rise)` is fine). A node no temperature source reaches reports `None`.

## 4. Worked example: an actuated orifice

A complete (hypothetical, not in the catalogue) component: a Kv orifice whose opening follows
its command with a lag, which warns when the flow reverses. It uses a law, a `settle` state
with a fast-state update, an envelope rule and a code-emitted warning.

```yaml
# src/worldparts/catalog/hydraulic/orifice.yaml (abridged: add description, fidelity,
# classification, provenance and implementations as in valve.yaml)
id: worldparts.hydraulic.orifice
ports:
  - {name: inlet, type: fluid, medium: water, description: Forward inlet.}
  - {name: outlet, type: fluid, medium: water, description: Forward outlet.}
parameters:
  - {name: kv, description: Kv when fully open., type: number, unit: m3/h, default: 2.0, minimum: 0.001, maximum: 1000}
  - {name: lag, description: Actuator time constant., type: number, unit: s, default: 0, minimum: 0, maximum: 600}
inputs:
  - {name: command, description: "Commanded opening, 0 to 1.", unit: "1", default: 1, minimum: 0, maximum: 1}
states:
  - {name: opening, description: Actual opening., unit: "1", steady: settle}
observables:
  - {name: volume_flow, unit: L/min, description: Flow from inlet to outlet.}
  - {name: pressure_drop, unit: bar, pressure_reference: difference, description: Inlet minus outlet pressure.}
modes:
  - {name: closed, condition: "opening < 0.001", description: Closed.}
  - {name: open, condition: "true", description: Open.}
envelope:
  - {code: high_drop, severity: warning, condition: "pressure_drop > 3", message: Pressure drop above 3 bar.}
warnings:
  - {code: reverse_flow, severity: warning, description: Water flows from outlet to inlet.}
```

```python
# src/worldparts/components/orifice.py
from worldparts.components.base import Component, NetworkBuilder, NetworkView, first_order
from worldparts.laws import QuadraticResistance, kv_to_k
from worldparts.network import Branch
from worldparts.results import ComponentWarning


class Orifice(Component):
    """Kv orifice with a first-order actuator."""

    law: QuadraticResistance
    branch: Branch

    def init_states(self) -> None:
        self.states["opening"] = self.inputs["command"]

    def settle(self) -> None:                      # steady: settle
        self.states["opening"] = self.inputs["command"]

    def update_fast_states(self, dt: float) -> None:
        self.states["opening"] = first_order(
            self.states["opening"], self.inputs["command"], self.parameters["lag"], dt
        )

    def build(self, nb: NetworkBuilder) -> None:
        self.law = QuadraticResistance(1.0)        # placeholder; set in update_laws
        self.branch = nb.branch(nb.port("inlet"), nb.port("outlet"), self.law, label="orifice")

    def update_laws(self) -> None:
        kv = self.parameters["kv"] * max(self.states["opening"], 1e-6)   # never exactly 0
        self.law.k = kv_to_k(kv, self.rho)

    def observables(self, sol: NetworkView) -> dict[str, float | None]:
        return {
            "volume_flow": sol.m(self.branch) / self.rho,   # m3/s; shown in L/min
            "pressure_drop": sol.dp(self.branch),           # Pa difference; shown in bar
        }

    def extra_warnings(self, sol: NetworkView) -> list[ComponentWarning]:
        if sol.m(self.branch) / self.rho < -1e-6:
            return [self.warning("reverse_flow")]
        return []
```

Keep coefficients strictly positive (a Kv of exactly zero is not a valid law); model "closed"
as a leakage fraction as the valve and faucet do.

## 5. Feature recipes

**Thermal map (heater).** Pass `thermal=` to `nb.branch`. It is called as
`thermal(T_upstream_K, m)` with the signed branch flow and returns the downstream temperature.
Apply heating only for forward flow; return `T_in` otherwise:

```python
def _heat(self, t_in: float, m: float) -> float:
    p = self.parameters
    if m <= 0 or m / self.rho <= p["activation_flow"]:   # both m3/s
        return t_in
    rise = self.inputs["enabled"] * p["max_power"] / (m * self.cp)   # W / (kg/s * J/(kg K))
    return min(p["setpoint"], t_in + rise)                        # K
```

The mixing solver then propagates the heated temperature downstream. The outlet temperature
observable is `view.T(outlet_node)` or `_heat(view.port_T("inlet"), m)`; both give `None`
handling for you if you check `t_in is None`.

**Internal fixed node (faucet spout).** `self.atm = nb.fixed_node("spout", P_ATM, T=293.15)`
and a branch `C -> atm`. Water leaving through it leaves the network. The mixed outlet
temperature is `view.T(self.c_node)`; report `None` when the flow is below 1e-6 as design 8.6
requires.

**`steady: settle` versus `steady: hold`.** `solve()` calls `settle()`, so settle states jump
to equilibrium (valve position equals opening). Hold states (tank level and temperature) are
never touched by `solve()`: a steady solve is a snapshot at the current level. `simulate()`
never calls `settle()`; it calls `update_fast_states(dt)` (default: `settle()`), so a
component with no lag behaves the same in both.

**Storage integration (tank).** In `update_laws()` set `self.node.p = P_ATM + self.rho * G *
level`, `self.node.T = temperature` and each gate's `blocked_direction` (blocking outflow when
`level <= 0.001`). In a simulation also cap the outflow: explicit Euler must never take more
out over a step than the tank holds, or the clamp at 0 creates water (a pump drawing from a
nearly empty break tank did exactly that). The tank sets `blocked_direction = "reverse"` and
`limit = rho * A * (level - just under 1 mm) / (time_step * connected_ports)` on its gates, so
the level settles just above empty and a pump delivers what flows in. In
`integrate(dt, view)` compute the net inflow from `view.m(branch)` of your port branches,
advance the level with explicit Euler, spill anything above `height` (remember the spill for
the `overflow_rate` observable) and mix inflow temperatures (use `view.port_T(port)` for
water entering through a port). Keep the clamp at 0 as a guard. Reject a level above the
rim in `check_states`.
`tests/testparts.py` (`HeatedTank`) is a complete, tested example of all of this, including
the energy balance.

**Table parameters (pump curves).** Declare `type: table` with `columns` (name and unit each,
optionally `minimum` and `maximum` per column, e.g. `{name: flow, unit: m3/h, minimum: 0}`)
and optionally `min_rows` (default 1). Users pass rows in the declared column units; cells
may be strings with units, in the Python API and in system documents alike. Column limits
are enforced when a value is parsed (`OutOfRangeError` naming the cell, e.g.
`pump.head_curve[2].head`). Your code receives a `worldparts.manifest.Table`:

```python
curve = self.parameters["head_curve"]
q = curve["flow"]      # numpy array, m3/s
h = curve["head"]      # numpy array, m
```

Validate the rules column limits cannot express (strictly increasing flows, at least three
distinct points, a head that falls with flow) in `check_parameters`. Fit in `update_laws()` or cache the fit keyed on the
table object (`self.parameters[...]` is replaced, not mutated, when the user sets a new table).

**String enum parameters.** `type: string` with `enum: [...]`; the value arrives as the string
(`self.parameters["characteristic"] == "linear"`). Modes and envelope rules cannot test a
string; if a mode depends on one, expose a numeric or boolean observable (e.g.
`heater_on: 1 or 0`) and use that. Inputs are numeric only (the schema has no `type` on
inputs); an on/off input is a number in [0, 1].

**Warnings.** Envelope rules (`envelope`) are evaluated by the system over display-unit values
and need no code. Anything an expression cannot say (it depends on port connectivity, on a
curve lookup or on a quantity you do not expose) goes in `extra_warnings`, and its code must
be listed under `warnings`. `self.warning(code)` raises `ContractError` for an undeclared code,
and the catalogue test checks every emitted code against the manifest. A simulation reports
each warning once: `time` and `message` of its first occurrence, `last_time` of its last and
`active_at_end`; the component does not track this itself. Prefer one warning per condition:
the pump's `outside_preferred_region` info is suppressed while the more specific `low_flow`
or `beyond_curve` applies. Use severity `info` for advice (continuous-duty sizing, a nearly
empty tank) and `warning` when a result is outside the model's validity or the equipment is
at risk.

**Observables near zero flow.** A ratio over the flow (energy per m3, residence time) is
undefined without flow; return `None` below the 0.01 m3/h threshold the modes use, not only at
exactly zero, or a leakage flow (1e-5 m3/h behind a closed gate) gives a huge meaningless
value that swamps the minimum and maximum of a simulated series (the pump's
`specific_energy`).

**Boundaries that cannot hold a suction.** A quasi-steady network keeps every line full of
water, so a pump can pull the outlet of an empty tank far below atmospheric pressure without
the pump itself seeing anything wrong (at zero flow a starved suction and a closed discharge
valve look the same to it). Flag such states where they are unambiguous: the tank raises
`drawing_air` when it is empty and a port is under suction.

**Modes.** Conditions are evaluated in order over the local display-unit names (parameters,
inputs, states, observables, `port.p|m_flow|T`); the first true one wins, so end with a
`"true"` catch-all. A `None` operand makes a condition false, which is how a mode that depends
on an undefined temperature falls through. If a mode needs a quantity (pump
`npsh_available < npsh_required`), expose it as an observable and reference it. **Modes cannot
reference code-emitted warnings**: a pump whose `cavitating` and `low_flow` modes mirror its
`cavitation` and `low_flow` warnings must compute the same condition from observables in the
mode (e.g. `npsh_available < npsh_required + npsh_margin and volume_flow > 0`), and its
`extra_warnings` should use the same numbers so the two never disagree.

**System checks you may meet.** Besides the design 6.2 codes, `check()` reports
`boundary_short_circuit` (error: two fixed-pressure boundaries at different pressures joined
directly at one junction) and `self_connection` as a warning when two ports of one instance
are joined to the same node (the component is bypassed). The pipe emits
`below_vapour_pressure` when an end drops below the vapour pressure (static head in a riser
or siphon); the pump's own `cavitation` warning covers its suction side.

**System documents.** `to_dict()` writes components in insertion order, explicit parameters
and inputs, and under `states` every state that differs from its `init_states()` value, so a
tank level reached in a simulation survives `to_dict`/`from_dict`. Scenario systems may also
set `states`.

## 6. Scenarios and contracts (how you are tested)

Every manifest needs at least two scenarios and three contracts, including one `equal` check
of mass conservation (the schema and loader enforce the counts and the `equal` check). Name
the component under test `dut`.

```yaml
scenarios:
  - id: rated-point
    description: Rated flow through the dut between a supply and a drain.
    system:
      components:
        - {name: src, type: supply, parameters: {pressure: 1.2}}
        - {name: dut, type: media_filter}
        - {name: sink, type: drain}
      connections: [[src.port, dut.inlet], [dut.outlet, sink.port]]
    # simulate: {duration: 60 s, step: 1 s, events: [{at: 10 s, set: {dut.lamp_output: 0}}]}
    expect:
      - {variable: dut.volume_flow, value: 20.0, rel_tol: 0.01}
      - {variable: dut.pressure_drop, min: 0.1, max: 0.3}
      - {warning: dut.change_required, present: false}
      - {mode: dut, is: clean}
contracts:
  - id: mass-conservation
    description: Mass in equals mass out.
    scenario: rated-point
    sweep: {variable: src.pressure, from: 0.1, to: 3, steps: 8}
    check: {type: equal, left: dut.inlet.m_flow, right: -dut.outlet.m_flow, abs_tol: 1.0e-9}
```

Expectation forms: `{variable, value, abs_tol|rel_tol}` (`value: null` expects an undefined
result), `{variable, min, max}`, `{warning: inst.code, present}` and `{mode: inst, is: name}`
(the last is a worldparts extension). With `simulate`, expectations refer to the final time,
which is exactly `duration`. Contract sweeps take `{variable, from, to, steps}` or
`{variable, values: [...]}` (values may carry units, `"50 kPa"`); checks are `monotonic`,
`bounds` (min/max may be expressions), `equal` and `warning_iff`. Steady contracts reuse one
system across sweep points; simulated ones rebuild it per point.

Nothing passes vacuously: a `warning` expectation or `warning_iff` check whose instance does
not exist or whose code is not declared by that instance's manifest fails with a message
listing the declared codes, and a misspelled name in a check expression fails the contract
(naming the name) instead of aborting the catalogue run.

Numbers in expectations should come from a hand calculation or an independent library, not
from running your own model; say where they came from in the scenario description.

Run your component alone while developing:

```
uv run python -c "import worldparts as wp; print(wp.run_component('media_filter').summary())"
uv run pytest tests/test_catalog.py -k media_filter
```

`tests/test_catalog.py` then checks, for every manifest: schema validity and semantic checks,
importable implementation, instantiation with defaults, every scenario, every contract, and
that every emitted warning code is declared. Add family-specific physics tests (hand
calculations such as the pump operating point) in `tests/test_<family>.py`.

## 7. Checklist

- [ ] Manifest validates; ids, names and codes are lowercase with underscores.
- [ ] Every parameter has a default inside its hard limits; drops declare `difference`.
- [ ] States are not duplicated as observables; `observables()` returns every observable.
- [ ] Laws are created in `build()` and updated in `update_laws()`; no coefficient is zero.
- [ ] Code-emitted warning codes are listed under `warnings`.
- [ ] Two or more scenarios, three or more contracts, one mass-conservation `equal` check.
- [ ] Provenance names real sources; default data is marked `generic` or `estimate`.
- [ ] `uv run ruff check src tests`, `uv run ruff format --check src tests` and
      `uv run pytest` pass.
