# worldparts component manifest, format 0.1

A component manifest describes one physical component so that an AI agent can find it, set
its parameters with units, connect it, run it and tell when a result is outside the
model's validity. It is also a test specification: its scenarios and contracts run in CI.

This document explains the format in prose. The normative definition is the JSON Schema
[`src/worldparts/schemas/component-manifest.schema.json`](../src/worldparts/schemas/component-manifest.schema.json)
(draft 2020-12). If this text and the schema disagree, the schema wins. The design
decisions behind the format are in [docs/design.md](../docs/design.md), sections 3, 4 and 7.
The packaged manifests in
[`src/worldparts/catalog/hydraulic/`](../src/worldparts/catalog/hydraulic/) are the
reference examples, and [docs/catalog.md](../docs/catalog.md) is generated from them.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119. A manifest is valid
when it passes the schema **and** the loader's semantic checks (section 13). The
`worldparts` loader rejects anything else with a `ManifestError` that lists every problem.

Contents:

1. [File and top-level fields](#1-file-and-top-level-fields)
2. [Identity and description](#2-identity-and-description)
3. [Units, pressure references and quantities](#3-units-pressure-references-and-quantities)
4. [Ports](#4-ports)
5. [Parameters, inputs, states and observables](#5-parameters-inputs-states-and-observables)
6. [The expression language](#6-the-expression-language)
7. [Modes](#7-modes)
8. [Envelope and warnings](#8-envelope-and-warnings)
9. [Scenarios](#9-scenarios)
10. [Contracts and check types](#10-contracts-and-check-types)
11. [Implementations](#11-implementations)
12. [Provenance](#12-provenance)
13. [Rules the loader enforces beyond the schema](#13-rules-the-loader-enforces-beyond-the-schema)
14. [Rules checked when the manifest runs](#14-rules-checked-when-the-manifest-runs)
15. [Appendix: the valve manifest](#appendix-the-valve-manifest)

## 1. File and top-level fields

A manifest is one YAML file (parsed with a safe loader) whose root is a mapping. No other
top-level keys are allowed.

| Field | Required | Type | Meaning |
|---|---|---|---|
| `manifest_version` | yes | `"0.1"` | Format version. The only valid value is the string `"0.1"`. |
| `id` | yes | string | Globally unique dotted id (section 2). |
| `version` | yes | string | Semantic version of this component definition. |
| `name` | yes | string | Human-readable name. |
| `summary` | yes | string | One line (no newline). |
| `description` | yes | string | Markdown: what the component models and what it does not. |
| `tags` | no | list of strings | Search keywords. |
| `classification` | no | mapping | Cross-walk to other vocabularies. |
| `fidelity` | yes | mapping | Fidelity level and assumptions. |
| `ports` | yes | list, at least 1 | Connection points (section 4). |
| `parameters` | yes | list, may be empty | Configuration values (section 5). |
| `inputs` | no | list | Operational set-points (section 5). |
| `states` | no | list | Dynamic states (section 5). |
| `observables` | yes | list, at least 1 | Computed outputs (section 5). |
| `modes` | no | list | Named discrete modes (section 7). |
| `envelope` | no | list | Soft validity rules (section 8). |
| `warnings` | no | list | Warning codes emitted by the implementation (section 8). |
| `scenarios` | yes | list, at least 2 | Test systems with expected results (section 9). |
| `contracts` | yes | list, at least 3 | Behavioural properties (section 10). |
| `implementations` | yes | mapping | Bindings to code (section 11). |
| `provenance` | yes | mapping | Authors, sources, data origin, licences (section 12). |

## 2. Identity and description

**`id`** is a dotted lowercase namespace with at least two segments. Each segment matches
`[a-z][a-z0-9_]*`. The last segment is the component's **alias**, for example `valve` in
`worldparts.hydraulic.valve`. APIs accept the full id or the alias. An alias shared by
two manifests is ambiguous, and then only the full id resolves. Two manifests with the same
id MUST NOT be loaded into one catalogue.

**`version`** is a semantic version (`MAJOR.MINOR.PATCH`, with optional pre-release and
build suffixes) of this component definition. It is separate from `manifest_version`, which
versions the format. Format 0.1 does not define when to bump it. We recommend a major bump
when a port, variable name, unit or meaning changes incompatibly.

**`summary`** is one line for listings. **`description`** is Markdown. It SHOULD state the
equations, the sign conventions and what the model leaves out. Agents read it through
`describe_component`, so write it for someone who has to decide whether the model fits
their question.

**`tags`** are unique strings matching `[a-z0-9][a-z0-9_-]*`. Catalogue search matches query
words against the id, name, summary, description and tags.

**`classification`** is optional and has three keys, all optional:

- `ifc`: `{entity, predefined_type}`, where `entity` matches `Ifc[A-Za-z]+`, for example
  `{entity: IfcValve, predefined_type: REGULATING}`;
- `brick`: a Brick class name such as `Valve`;
- `other`: a free-form mapping for other vocabularies (eCl@ss, ETIM, SAREF4WATR, ...).

**`fidelity`** has two required keys:

- `level`: one of `qualitative`, `lumped_quasi_steady` (algebraic at each instant),
  `lumped_dynamic` (has storage states integrated in time, like the tank) or `distributed`;
- `assumptions`: a non-empty list of non-empty strings. Every modelling simplification that
  affects a result belongs here, for example constant water properties.

## 3. Units, pressure references and quantities

**Units.** Every numeric parameter, input, state, observable and table column declares a
`unit` from this vocabulary:

```text
1  %  Pa  kPa  bar  m  mm  m/s  m2  m3  L  kg/s  L/s  L/min  m3/h
degC  K  W  kW  s  min  h  rpm  mW/cm2  mJ/cm2  kg/m3  kWh/m3
```

`1` means dimensionless. The declared unit is the **display unit**. A plain number given for
a variable (in a system document, a scenario, `System.set` or an MCP call) is read in that
unit. A string may carry its own unit (`"3.5 bar"`, `"12 L/min"`, `"55 degC"`). It is
converted with pint, and a dimension mismatch is an error that names the expected unit.
Results are reported in the display unit and carry the unit string. Internally,
implementations see SI only (Pa absolute for gauge and absolute pressures, Pa for
differences, m³/s, K, W, s and so on). `%` is converted to a fraction, so an efficiency
of 72 % is 0.72 inside the implementation.

**Pressure reference.** A variable whose unit is a pressure (`Pa`, `kPa` or `bar`) has a
reference, set by `pressure_reference`:

| `pressure_reference` | Meaning |
|---|---|
| `gauge` (default) | Relative to standard atmosphere, `P_ATM = 101325 Pa`. `3 bar` gauge is 401325 Pa absolute. |
| `absolute` | Absolute pressure. |
| `difference` | A pressure difference (a drop or a rise). It converts by scale only. |

Port pressures (`<port>.p`) are always in bar gauge. User strings may name a reference
(`"2 bar absolute"`, `"2 bar abs"`, `"2 bar (a)"`, `"1.5 bara"`, `"3 bar gauge"`,
`"3 barg"`), and the value is converted to the variable's declared reference. `"1 atm"`
without a reference is rejected for gauge variables, because it almost always means
atmospheric pressure (0 bar gauge). Reported pressures carry their reference.
`pressure_reference` is allowed only on pressure units (section 13). `kWh/m3` (specific
energy) has the dimension of a pressure but is not one. It has no reference.

**Quantity.** A temperature is absolute unless the variable declares
`quantity: temperature_difference`. That is the only value of `quantity` in format 0.1, and
it is allowed only with unit `K` or `degC`. A temperature difference converts by scale only
(35 K is 35 degC and 63 degF, never -238.15 degC), and its results carry the reference
`difference`. `quantity` is accepted on number parameters, inputs, states and observables.

**Tolerances** in scenarios and contracts are plain numbers in the display unit of the
variable they apply to. A tolerance of `1` on a `degC` variable means 1 K.

## 4. Ports

Each port is `{name, type, medium, description}`, and all four are required.

- `name` matches `[a-z][a-z0-9_]*`.
- `type` is `fluid`, the only port type in format 0.1.
- `medium` is `water`, the only medium in format 0.1. Water has constant properties:
  density 998.2 kg/m³, dynamic viscosity 1.002e-3 Pa·s and specific heat 4182 J/(kg·K).

Every port exposes three **port variables**, readable in expressions and results as
`<port>.p` (bar gauge), `<port>.m_flow` (kg/s, **positive into the component**) and
`<port>.T` (degC). A two-port component's forward direction runs from its first port to its
second, and its `volume_flow` observable is positive in that direction. Connecting several
ports to one node forms a junction. An unconnected port is capped (no flow), and the system
check reports it as the warning `unconnected_port`.

## 5. Parameters, inputs, states and observables

These four lists declare the component's variables. Together with the ports they share
**one namespace per component**: every name MUST be unique across ports, parameters,
inputs, states and observables. A variable's path in a system is `<instance>.<name>`.

### Parameters

Parameters configure the component (sizes, curves, ratings). Every parameter has `name`,
`description`, `type` and `default`. The other allowed keys depend on `type`:

| `type` | Required | Optional | Value |
|---|---|---|---|
| `number` | `unit` | `minimum`, `maximum`, `pressure_reference`, `quantity` | A real number in `unit`. |
| `integer` | `unit` | `minimum`, `maximum` | An integer. A value within 1e-9 of an integer is accepted. |
| `string` | `enum` | (none) | One of the non-empty, unique strings in `enum`. |
| `boolean` | (none) | (none) | `true` or `false`. `0`, `1`, `"true"` and `"false"` are also accepted. |
| `table` | `columns` | `min_rows` | A list of rows, each with one number per column. |

`minimum` and `maximum` are **hard limits** in the declared unit. A value outside them is
rejected with `OutOfRangeError` at the API, and a system document that contains one fails
the check (`parameter_out_of_range`). Values that are not finite are rejected. The
`default` MUST lie within the limits (section 13).

A **table** parameter declares its `columns`. Each column is
`{name, unit, description?, pressure_reference?, minimum?, maximum?}`, and the column limits
apply to every cell. `min_rows` (default 1) is the minimum number of rows. The default is a
list of rows of numbers in the column units. Users may also write cells as strings with
units. A pump's head curve is the typical case:

```yaml
- name: head_curve
  description: Head against flow at rated speed (at least 3 rows, flows strictly increasing, head falling with flow).
  type: table
  columns:
    - {name: flow, unit: m3/h, minimum: 0, description: Volume flow.}
    - {name: head, unit: m, minimum: 0, description: Pump head.}
  min_rows: 3
  default: [[0, 34], [10, 32.5], [20, 28.5], [30, 21.5], [36, 16]]
```

Rules that limits cannot express, such as strictly increasing flows, belong in the
implementation's `check_parameters` hook. A violation there is reported as `invalid_value`.

String and table parameters are not part of solve and simulation results. They cannot be
used in mode or envelope conditions, because the expression language has no string or
table values.

### Inputs

Inputs are operational set-points that may change during a simulation, through events, for
example a valve `opening` or a pump `speed`. Inputs are numeric only. Each input has
`name`, `description`, `unit` and `default`, and MAY have `minimum`, `maximum`,
`pressure_reference` and `quantity`. An on/off input is a number in [0, 1].

### States

States are the component's dynamic memory. Each state has `name`, `description`, `unit`
and `steady`, and MAY have `default`, `minimum`, `maximum`, `pressure_reference` and
`quantity`.

- `steady: settle`: a steady `solve()` sets the state to its equilibrium, for example
  valve `position = opening`. In a simulation it follows the implementation's fast-state
  update, for example a first-order actuator lag.
- `steady: hold`: `solve()` keeps the current value, for example a tank's `level`. A steady
  solve is then a snapshot at the current state. A simulation integrates the state in
  time.
- `default` is the initial value when the implementation does not set one. If present, it
  MUST lie within the limits.

States are reported in results automatically. A state MUST NOT be declared again as an
observable. States can be set through the API and in system documents
(`states: {level: 1.2}`).

### Observables

Observables are values the component computes after every solve. Each observable has
`name`, `description` and `unit`, and MAY have `pressure_reference` and `quantity`. The
implementation MUST return a value for every declared observable. `None` (`null`) means
undefined, for example the outlet temperature when nothing flows. Checks skip undefined
values.

## 6. The expression language

Mode conditions, envelope conditions and contract checks are written in a small, safe
expression language. In YAML an expression is a string, a number or a boolean. `true` and
`false` are read as the literals `true` and `false`.

Allowed syntax:

- literals: integers, floats, `true`, `false`;
- names, possibly dotted: `opening`, `port_a.p`, `dut.volume_flow`, `dut.port_b.m_flow`;
- arithmetic: `+ - * / **`, unary `-` and `+`, parentheses;
- comparisons: `< <= > >= == !=`, chainable (`0 < x <= 1`);
- logic: `and`, `or`, `not`;
- functions: `abs`, `min`, `max` and `sqrt`, and no others.

Nothing else is accepted. There is no attribute access on objects, no subscripts, no
strings, no other calls and no names that start with `__`. Expressions are parsed with
Python's `ast` against a whitelist and never passed to `eval`.

Evaluation:

- Names take their values **in display units**. A pressure is in its declared unit and
  reference, a port pressure is in bar gauge and a temperature is in degC.
- A `None` operand makes the whole expression `None`. Division by zero, the square root of
  a negative number and overflow also give `None`. `**` is evaluated in floating point, so
  `9 ** 9 ** 9` overflows to `None` instead of hanging.
- A `None` condition counts as **false**.

Scope:

- In `modes` and `envelope`, names are the component's **local** names: its numeric and
  boolean parameters, inputs, states and observables, plus `<port>.p`, `<port>.m_flow` and
  `<port>.T` for each port.
- In scenarios and contracts, names are **system paths**, `<instance>.<name>` and
  `<instance>.<port>.p|m_flow|T`, over the scenario's instances.

## 7. Modes

`modes` is an ordered list of `{name, condition, description}`. After each solve, the mode
is the **first** entry whose condition is true. If none is true, the mode is `null`. The
last mode SHOULD have the condition `"true"` as a catch-all. Mode names match
`[a-z][a-z0-9_]*` and MUST be unique.

Modes are part of every result (`SolveResult.modes`) and of a simulation's mode changes.
They exist so an agent can ask "is the valve closed?" or "is the pump cavitating?" without
re-deriving the thresholds.

A mode condition cannot refer to a warning. When a mode mirrors a code-emitted warning (the
pump's `cavitating` mode and its `cavitation` warning), the condition MUST compute the same
test from observables. The implementation SHOULD use the same numbers so that the two never
disagree.

## 8. Envelope and warnings

There are two ways to declare that a result is outside the model's validity, or that the
equipment is at risk. Both produce **warnings**. A warning never raises an error and never
stops a solve. A warning has a `code` (matching `[a-z][a-z0-9_]*`) and a `severity`:
`info` for advice (continuous-duty sizing, a nearly empty tank) or `warning` for a result
outside the model's validity or equipment at risk.

**Envelope rules** (`envelope`) are declarative. Each is `{code, severity, condition,
message}`. The system evaluates `condition` over the component's local display-unit
values after every solve and raises the warning with `message` when the condition is true.
No code is needed.

```yaml
envelope:
  - code: high_pressure_drop
    severity: warning
    condition: "pressure_drop > 3"
    message: Pressure drop above 3 bar; cavitation and noise are likely in a real valve.
```

**Code-emitted warnings** (`warnings`) are raised by the implementation, for conditions an
expression cannot state: they depend on whether a port is connected, on a curve lookup or
on a quantity the component does not expose. Each is `{code, severity, description}`. The
implementation raises them with `self.warning(code)`, and an undeclared code raises
`ContractError`.

Every warning code a component can emit MUST be declared exactly once, in `envelope` or in
`warnings`. Warnings differ from the other two kinds of problem:

| Kind | Where declared | Effect |
|---|---|---|
| Hard limit | `minimum`/`maximum` of a variable or column | The value is rejected (`OutOfRangeError`); a document with it fails the check. |
| System check issue | Fixed codes of `System.check()`: `unknown_component`, `unknown_port`, `incompatible_ports`, `self_connection`, `unconnected_port`, `no_pressure_reference`, `boundary_short_circuit`, `parameter_out_of_range`, `invalid_value` | Error-level issues stop `solve()` (`SystemCheckError`); warning-level issues do not. |
| Warning | `envelope` or `warnings` | Reported with the result; the result is still returned. |

In a simulation each warning is reported once per component and code, with the time of its
first occurrence, the time of its last occurrence (`last_time`) and whether it is still
raised at the end (`active_at_end`).

## 9. Scenarios

A scenario is a small system that exercises the component, which is named `dut` (device
under test) by convention, together with expected results. Each scenario has:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Matches `[a-z0-9][a-z0-9_-]*`; unique within the manifest. |
| `description` | yes | What the scenario shows and **where the expected numbers come from**. |
| `system` | yes | A system document body: `components` (at least one) and optional `connections` and `description`. |
| `simulate` | no | `{duration, step?, events?}`. Without it, the scenario is a steady solve. |
| `expect` | yes | At least one expectation. |

The `system` body uses the language-neutral system document format (design section 6.3)
without its `worldparts_system` and `name` header. Each component is
`{name, type, parameters?, inputs?, states?}`. `type` is a full id or an alias. Values are
numbers in the declared unit, strings with units, booleans, strings for string parameters,
or rows for tables. Each connection is a pair of `<instance>.<port>` paths.

`simulate` takes a `duration` and an optional `step` (default `1 s`). Both are numbers in
seconds or strings with a time unit. `events` is a list of `{at, set}`, where `set` maps
paths to values. Samples fall at every multiple of the step, at every event time and at the
duration. Events after the duration are rejected. With `simulate`, expectations refer to
the **final sample**, at exactly `duration`: its values, modes and the warnings raised at
that sample.

Each expectation takes one of four forms:

| Form | Passes when |
|---|---|
| `{variable, value, abs_tol?, rel_tol?}` | The value is within `max(abs_tol, rel_tol * abs(value))` of `value`. Without either tolerance, within `1e-9 * max(1, abs(value))`. `value: null` expects an undefined result. |
| `{variable, min?, max?}` | At least one bound is given, the value is defined, and it lies within the bounds. |
| `{warning: <instance>.<code>, present: true\|false}` | The warning is present, or absent, in the result. The code MUST be declared by that instance's manifest (section 14). |
| `{mode: <instance>, is: <mode>\|null}` | The instance's mode equals `is`. This form is a worldparts extension. |

Expected numbers SHOULD come from a hand calculation or an independent library, never from
running the model itself, and the description SHOULD show the calculation. For example, the
valve's `kv-at-1-bar` scenario expects `2.5 m3/h * sqrt(1000 / 998.2) = 41.7042 L/min`.

## 10. Contracts and check types

A contract is a behavioural property checked by sweeping one variable of a scenario. Each
contract has:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Matches `[a-z0-9][a-z0-9_-]*`; unique within the manifest. |
| `description` | yes | The property in words. |
| `scenario` | yes | The id of a scenario in the same manifest. |
| `sweep` | no | The swept variable. Without it, the check runs at one point. |
| `check` | yes | One of the four check types below. |

**Sweeps** take one of two forms:

- `{variable, from, to, steps}`: `steps` (at least 2) evenly spaced values from `from` to
  `to`, both ends included, as plain numbers in the variable's declared unit;
- `{variable, values: [...]}`: an explicit list. Values may be numbers, strings with units
  (`"50 kPa"`), booleans or table rows.

The swept variable is any settable path in the scenario: a parameter, input or state of any
instance, not only the `dut`. For a steady scenario, one system is built and reused across
the sweep points, so held states carry over. For a simulated scenario the system is rebuilt
and simulated at each point, and the check reads the final sample.

**Check types:**

| `type` | Fields | Holds when |
|---|---|---|
| `monotonic` | `variable`, `direction` (`increasing` or `decreasing`), `strict` (default false), `tolerance` (default 1e-9, relative) | Across the sweep points in order, skipping undefined values, each step moves in `direction`. Non-strict allows a step back of at most `tolerance * max(abs(y0), abs(y1))`. Strict requires a step forward larger than that. At least two defined points are needed. |
| `bounds` | `variable`, `min` and/or `max` (expressions) | At every point where the variable is defined, it lies within the bounds, which are evaluated at that point (relative slack 1e-12). |
| `equal` | `left`, `right` (expressions), `abs_tol` and/or `rel_tol` (at least one) | At every point where both sides are defined, `abs(left - right) <= max(abs_tol, rel_tol * max(abs(left), abs(right)))`. |
| `warning_iff` | `code` (`<instance>.<code>`), `condition` (expression) | At every point, the warning is present exactly when the condition is true. The code MUST be declared. |

A `bounds`, `equal` or `warning_iff` check fails if no point can be evaluated because every
value is undefined. Nothing passes vacuously.

Every manifest MUST ship at least two scenarios and at least three contracts, and at least
one contract MUST be an `equal` check. By convention that check is mass conservation, for
example `left: dut.port_a.m_flow`, `right: -dut.port_b.m_flow`, `abs_tol: 1.0e-9`.

Examples from the catalogue:

```yaml
contracts:
  - id: flow-increases-with-opening        # monotonic
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check: {type: monotonic, variable: dut.volume_flow, direction: increasing, strict: true}
  - id: kv-law                             # equal, with a hand-derived right side
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check:
      type: equal
      left: dut.volume_flow
      right: "dut.effective_kv * sqrt(dut.pressure_drop * 1000 / 998.2) * 1000 / 60"
      rel_tol: 1.0e-5
  - id: high-pressure-drop-warning         # warning_iff
    scenario: kv-at-1-bar
    sweep: {variable: src.pressure, from: 0, to: 6, steps: 13}
    check: {type: warning_iff, code: dut.high_pressure_drop, condition: "dut.pressure_drop > 3"}
```

A contract SHOULD be able to fail. Check this by breaking the implementation on purpose
(flip a sign, drop a term) and confirming that the contract catches it. A contract that
passes whatever the implementation does is a bug.

## 11. Implementations

`implementations` maps binding targets to bindings. `reference` is required. Other keys are
optional, and any further key MUST map to a mapping.

- `reference`: `{python, notes?}`. `python` is the import path `module:Class` of a subclass
  of `worldparts.components.base.Component`, for example
  `"worldparts.components.valves:TwoWayValve"`. Manifests are bound to classes by this path
  alone. There is no registry.
- `modelica`: `{class, library?, notes?}`. The equivalent Modelica class, for example
  `Buildings.Fluid.Actuators.Valves.TwoWayLinear` from the Modelica Buildings Library.
- `wntr`: `{element, notes?}`. The equivalent WNTR/EPANET element, for example `TCV`,
  `HeadPump` or `Reservoir`. `notes` explains how parameters map and where results will
  diverge.

In format 0.1 the Modelica and WNTR bindings are documentation for adapters. Unless a note
says otherwise they have not been cross-validated.

## 12. Provenance

`provenance` is required and has five required keys:

| Key | Meaning |
|---|---|
| `authors` | Non-empty list of authors of the manifest. |
| `sources` | Non-empty list of `{title, url?, citation?, notes?}`: the equations, standards, datasheets and libraries used. `url` must start with `http://` or `https://`. |
| `data` | `{acquisition, notes}`: where the default parameter values come from. |
| `license` | Licence of the manifest (the catalogue uses `Apache-2.0`). |
| `data_license` | Licence of the parameter data (the catalogue uses `CC0-1.0`). |

`data.acquisition` is one of:

| Value | Intended meaning |
|---|---|
| `generic` | Illustrative values for a typical component, not a specific product. |
| `estimate` | An engineering estimate. |
| `datasheet` | Taken from a manufacturer's published data. |
| `digitized` | Read off a published curve or figure. |
| `measured` | Measured on a real unit. |
| `standard` | Taken from a standard. |

`data.notes` MUST say what the values represent. Default parameters MUST NOT be presented as
a specific product unless they are one. Every v0.1 manifest uses `generic`.

## 13. Rules the loader enforces beyond the schema

After schema validation, `Manifest.from_dict` (and so `load_manifest`, the catalogue,
`worldparts validate` and the tests) checks the following. Each violation is reported with
the offending name.

1. **One namespace.** Names are unique across ports, parameters, inputs, states and
   observables. Port names are unique and mode names are unique.
2. **Warning codes once.** A code appears at most once across `envelope` and `warnings`.
3. **Limits.** `minimum <= maximum` for every variable and table column.
4. **Defaults.** Every parameter and input default, and every state default that is
   present, parses in its declared unit and lies within its limits. For tables this
   includes the row width, the column limits and `min_rows`.
5. **Pressure differences.** A pressure variable (unit `Pa`, `kPa` or `bar`) whose name
   contains `drop`, `difference`, `delta`, `dp`, `rise` or `loss` MUST NOT be gauge. It
   declares `pressure_reference: difference` (or `absolute`).
6. **Temperature differences.** A temperature variable whose name contains `rise`,
   `difference`, `delta`, `drop`, `increase`, `decrease`, `approach` or `_dt`, or ends in
   `dt`, MUST declare `quantity: temperature_difference`.
7. **Quantity unit.** `quantity: temperature_difference` requires the unit `K` or `degC`.
8. **Pressure reference only on pressures.** `pressure_reference` on a variable or table
   column whose unit is not `Pa`, `kPa` or `bar` is rejected. This includes `kWh/m3`.
9. **Table columns.** Column names within a table are unique.
10. **Conditions.** Every mode and envelope condition parses and uses only local names
    (section 6). A reference to a string or table parameter is rejected with a message
    saying so, and an unknown name is rejected with a list of close matches.
11. **Scenario and contract ids.** Scenario ids are unique, contract ids are unique, and
    each contract's `scenario` names an existing scenario.
12. **Check expressions.** The `left`, `right`, `min`, `max` and `condition` expressions of
    every check parse.
13. **Mass conservation.** At least one contract uses an `equal` check.

## 14. Rules checked when the manifest runs

Some rules can only be checked by running the manifest. `worldparts check-catalog` and
`tests/test_catalog.py` check them for every packaged manifest:

- the `reference` implementation imports and is a `Component` subclass;
- the component instantiates with its defaults and exposes every declared observable and
  port variable;
- `observables()` returns a value for every declared observable (a missing one raises
  `ContractError`);
- every warning the implementation emits is declared;
- every scenario passes and every contract holds;
- a warning expectation or `warning_iff` check that names an unknown instance or an
  undeclared code **fails**. It does not pass vacuously. A misspelled name in a check
  expression also fails the contract, and the message names it.

## Appendix: the valve manifest

The complete manifest of the two-way control valve, as shipped in
[`src/worldparts/catalog/hydraulic/valve.yaml`](../src/worldparts/catalog/hydraulic/valve.yaml):

```yaml
manifest_version: "0.1"
id: worldparts.hydraulic.valve
version: "0.1.0"
name: Two-way control valve
summary: Throttling valve with Kv sizing, inherent characteristic, leakage and first-order actuator lag.
description: |
  A two-way throttling valve between `port_a` and `port_b`. The pressure-flow relation is the
  Kv (flow coefficient) law: at full opening, `kv` m3/h of water flows at a pressure drop of
  1 bar. The effective Kv is `kv * phi(position)` where `phi` is the inherent characteristic:

  - linear: `l + (1 - l) * y`
  - equal_percentage: `l + (1 - l) * (R**(y - 1) - 1/R) / (1 - 1/R)`
  - quick_opening: `l + (1 - l) * sqrt(y)`

  with `y` the valve position, `l` the leakage (relative Kv when closed) and `R` the
  rangeability. The position follows the commanded `opening` through a first-order lag with
  time constant `actuator_time` in simulations; a steady solve sets it equal to the opening.
  The law is regularised near zero flow so it stays strictly monotone; the valve is
  symmetric (flow may go either way).

  Not modelled: choked or cavitating flow (the `high_pressure_drop` warning flags the region
  where it becomes likely), installed characteristic distortion beyond what the network
  itself produces, hysteresis, dead band and actuator force limits.
tags: [valve, throttling, kv, control-valve, actuator]
classification:
  ifc: {entity: IfcValve, predefined_type: REGULATING}
  brick: Valve
fidelity:
  level: lumped_quasi_steady
  assumptions:
    - Incompressible water with constant properties (998.2 kg/m3, 1.002e-3 Pa s).
    - Turbulent Kv law dp proportional to Q**2, exact above 0.2 % of the flow at 1 bar and regularised (C1 cubic) below it.
    - No choked flow, cavitation or flashing; no pressure recovery effects.
    - Inherent characteristic only; actuator modelled as an exact first-order lag.
ports:
  - {name: port_a, type: fluid, medium: water, description: Inlet in the forward direction.}
  - {name: port_b, type: fluid, medium: water, description: Outlet in the forward direction.}
parameters:
  - name: kv
    description: "Flow coefficient at full opening (m3/h of water at 1 bar pressure drop). Size it to the line: the default 2.5 m3/h is a small DN15 control valve. Typical globe control valves are about Kv 4 (DN15), 10 (DN25), 25 (DN40), 40 (DN50), 63 (DN65), 100 (DN80) and 160 (DN100); full-bore ball and butterfly valves pass several times more (DN50 about 150 to 300)."
    type: number
    unit: m3/h
    default: 2.5
    minimum: 0.0001
    maximum: 100000
  - name: characteristic
    description: Inherent flow characteristic.
    type: string
    enum: [linear, equal_percentage, quick_opening]
    default: linear
  - name: rangeability
    description: Rangeability R of the equal-percentage characteristic (ratio of largest to smallest controllable Kv).
    type: number
    unit: "1"
    default: 50
    minimum: 2
    maximum: 500
  - name: leakage
    description: Relative Kv when fully closed (seat leakage as a fraction of kv).
    type: number
    unit: "1"
    default: 0.0001
    minimum: 1.0e-8
    maximum: 0.1
  - name: actuator_time
    description: First-order time constant of the actuator; 0 is instantaneous.
    type: number
    unit: s
    default: 0
    minimum: 0
    maximum: 3600
inputs:
  - {name: opening, description: "Commanded valve opening: 0 closed, 1 fully open.", unit: "1", default: 1.0, minimum: 0, maximum: 1}
states:
  - name: position
    description: Actual valve position after the actuator lag (reported as the variable `position`).
    unit: "1"
    steady: settle
    minimum: 0
    maximum: 1
observables:
  - {name: volume_flow, unit: L/min, description: Volume flow from port_a to port_b.}
  - {name: pressure_drop, unit: bar, pressure_reference: difference, description: Pressure at port_a minus pressure at port_b.}
  - {name: effective_kv, unit: m3/h, description: Kv at the current position (kv times the characteristic).}
modes:
  - {name: closed, condition: "position <= 0.001", description: Valve closed; only seat leakage passes.}
  - {name: throttling, condition: "position < 0.999", description: Partly open.}
  - {name: open, condition: "true", description: Fully open.}
envelope:
  - code: high_pressure_drop
    severity: warning
    condition: "pressure_drop > 3"
    message: Pressure drop above 3 bar; cavitation and noise are likely in a real valve and the Kv law may overpredict flow (choked flow is not modelled).
scenarios:
  - id: kv-at-1-bar
    description: Fully open linear valve between 1 bar gauge and atmosphere; the flow reproduces the Kv definition.
    system:
      components:
        - {name: src, type: supply, parameters: {pressure: 1.0}}
        - {name: dut, type: valve}
        - {name: sink, type: drain}
      connections:
        - [src.port, dut.port_a]
        - [dut.port_b, sink.port]
    expect:
      - {variable: dut.volume_flow, value: 41.7042, abs_tol: 0.01}
      - {variable: dut.effective_kv, value: 2.5, abs_tol: 1.0e-9}
      - {variable: dut.pressure_drop, value: 1.0, abs_tol: 1.0e-5}
      - {mode: dut, is: open}
      - {warning: dut.high_pressure_drop, present: false}
  - id: closed-at-3-bar
    description: Closed valve at 3 bar; only seat leakage (kv * leakage) passes.
    system:
      components:
        - {name: src, type: supply, parameters: {pressure: 3.0}}
        - {name: dut, type: valve, inputs: {opening: 0}}
        - {name: sink, type: drain}
      connections:
        - [src.port, dut.port_a]
        - [dut.port_b, sink.port]
    expect:
      - {variable: dut.effective_kv, value: 0.00025, rel_tol: 1.0e-9}
      - {variable: dut.volume_flow, value: 0.0072230, rel_tol: 0.001}
      - {variable: dut.pressure_drop, value: 3.0, abs_tol: 1.0e-5}
      - {mode: dut, is: closed}
  - id: equal-percentage-half-open
    description: Equal-percentage valve at half opening, 1 bar; effective Kv follows the characteristic.
    system:
      components:
        - {name: src, type: supply, parameters: {pressure: 1.0}}
        - {name: dut, type: valve, parameters: {characteristic: equal_percentage}, inputs: {opening: 0.5}}
        - {name: sink, type: drain}
      connections:
        - [src.port, dut.port_a]
        - [dut.port_b, sink.port]
    expect:
      - {variable: dut.effective_kv, value: 0.309968, rel_tol: 1.0e-5}
      - {variable: dut.volume_flow, value: 5.17084, rel_tol: 1.0e-4}
      - {mode: dut, is: throttling}
  - id: actuator-lag
    description: Valve with a 10 s actuator starts closed and is commanded fully open at t = 0; after 30 s the position is 1 - exp(-3).
    system:
      components:
        - {name: src, type: supply, parameters: {pressure: 1.0}}
        - {name: dut, type: valve, parameters: {actuator_time: 10 s}, inputs: {opening: 0}}
        - {name: sink, type: drain}
      connections:
        - [src.port, dut.port_a]
        - [dut.port_b, sink.port]
    simulate:
      duration: 30 s
      step: 1 s
      events:
        - {at: 0 s, set: {dut.opening: 1}}
    expect:
      - {variable: dut.position, value: 0.950213, abs_tol: 1.0e-5}
      - {variable: dut.opening, value: 1.0}
      - {mode: dut, is: throttling}
contracts:
  - id: flow-increases-with-opening
    description: Opening the valve never reduces the flow at a fixed pressure difference.
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check: {type: monotonic, variable: dut.volume_flow, direction: increasing, strict: true}
  - id: mass-conservation
    description: Mass entering port_a leaves through port_b at every opening.
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check: {type: equal, left: dut.port_a.m_flow, right: -dut.port_b.m_flow, abs_tol: 1.0e-9}
  - id: kv-law
    description: The flow equals effective Kv times sqrt(dp / specific gravity) at every opening.
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check:
      type: equal
      left: dut.volume_flow
      right: "dut.effective_kv * sqrt(dut.pressure_drop * 1000 / 998.2) * 1000 / 60"
      rel_tol: 1.0e-5
  - id: linear-characteristic
    description: For the linear characteristic the effective Kv is kv * (l + (1 - l) * position).
    scenario: kv-at-1-bar
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 6}
    check:
      type: equal
      left: dut.effective_kv
      right: "dut.kv * (dut.leakage + (1 - dut.leakage) * dut.position)"
      rel_tol: 1.0e-12
  - id: equal-percentage-monotonic
    description: The equal-percentage valve also passes more flow as it opens.
    scenario: equal-percentage-half-open
    sweep: {variable: dut.opening, from: 0, to: 1, steps: 11}
    check: {type: monotonic, variable: dut.volume_flow, direction: increasing, strict: true}
  - id: high-pressure-drop-warning
    description: The high_pressure_drop warning is raised exactly when the drop exceeds 3 bar.
    scenario: kv-at-1-bar
    sweep: {variable: src.pressure, from: 0, to: 6, steps: 13}
    check: {type: warning_iff, code: dut.high_pressure_drop, condition: "dut.pressure_drop > 3"}
implementations:
  reference: {python: "worldparts.components.valves:TwoWayValve"}
  modelica:
    class: Buildings.Fluid.Actuators.Valves.TwoWayLinear
    library: Modelica Buildings Library
    notes: Use TwoWayEqualPercentage or TwoWayQuickOpening for the other characteristics; Buildings parameterises by Kv or nominal flow and pressure drop. Binding not yet cross-validated in v0.1.
  wntr:
    element: TCV
    notes: Throttle control valve whose minor-loss coefficient reproduces the effective Kv at a nominal diameter.
provenance:
  authors: [worldparts contributors]
  sources:
    - title: "IEC 60534-2-1: Industrial-process control valves - Part 2-1: Flow capacity - Sizing equations for fluid flow under installed conditions"
      notes: Definition of the flow coefficient Kv (m3/h of water at 1 bar with specific gravity relative to water at 1000 kg/m3).
    - title: Modelica Buildings Library
      url: https://simulationresearch.lbl.gov/modelica/
      notes: Valve characteristics (linear, equal percentage, quick opening) with leakage, and regularised flow functions.
    - title: Modelica Standard Library (Modelica.Fluid.Utilities.regSquare)
      url: https://github.com/modelica/ModelicaStandardLibrary
      notes: Regularisation of the square law near zero flow.
  data:
    acquisition: generic
    notes: Default parameters (Kv 2.5 m3/h, rangeability 50, leakage 1e-4) are generic illustrative values for a small DN15 to DN20 valve, not a specific product.
  license: Apache-2.0
  data_license: CC0-1.0
```
