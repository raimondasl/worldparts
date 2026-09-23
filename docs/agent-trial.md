# Agent usability trial (September 2026)

Before the v0.1.0 release, one AI agent was given two engineering tasks and the worldparts
MCP server, and nothing else. This page records what it did, what it found and what
changed as a result.

> **Scope.** This was a single informal trial, by one agent, on two tasks, run once. It is
> not a benchmark and supports no claim about success rates. It was a usability test of the
> tool surface. Measuring whether agents compose components correctly, against a
> from-scratch baseline and with machine-checked answers, is milestone v0.2 (see the
> [roadmap](roadmap.md)).

## Setup

- **Server.** The development MCP server (`uv run worldparts mcp`, stdio) as of commit
  `d094556`, which added the six remaining components, the MCP server and the CLI. That
  was before any of the changes described below.
- **Client.** A small Python MCP client (MCP Python SDK v2) held one stdio session open,
  so the in-process system store survived between calls. Every call was logged with its
  tool, arguments, error flag and duration.
- **Rules.** The agent built and ran every model through MCP tool calls. It wrote no
  physics code, and it checked results with its own hand calculations.
- **Tasks.** Paraphrased from the agent's answers; the prompts were not archived.
  - **Task A, pump lift.** A ground tank (2 m diameter, 3 m high, 1.5 m of water) feeds a
    centrifugal pump that lifts water through a valve and 60 m of DN50 steel pipe to a
    rooftop tank 25 m up. Find the operating point at full speed, the speed that gives
    15 m³/h, whether the pump stays inside its envelope, and how long the ground tank
    lasts.
  - **Task B, purification skid.** A raw-water tank feeds a pump, a media filter and a UV
    reactor into a treated-water tank. Find the operating point with a clean filter,
    follow the filter clogging from 0 to 0.8 over 30 minutes, and judge whether raising
    the pump speed to 1.2 compensates.

## How the agent worked

For Task A, the agent searched the catalogue, described the pump, tank, pipe and valve,
built the system part by part, checked it, solved it at several speeds and valve settings,
and simulated the drain-down. For Task B, it described the filter and the UV reactor and
loaded the whole skid as one system document. It then solved the skid, simulated the
clogging and re-solved at higher speed.

| | Task A | Task B | Total |
|---|---|---|---|
| Tool calls | 29 | 14 | 43 |
| Calls that returned an error | 0 | 1 | 1 |
| Server time for all calls | | | 4.1 s |

Calls by tool:

| Tool | Task A | Task B |
|---|---|---|
| `list_components` | 1 | 0 |
| `describe_component` | 4 | 2 |
| `create_system` | 1 | 0 |
| `add_component` | 5 | 0 |
| `connect` | 4 | 0 |
| `check_system` | 1 | 0 |
| `load_system` | 0 | 2 |
| `get_system` | 0 | 1 |
| `set_values` | 6 | 4 |
| `solve` | 5 | 4 |
| `simulate` | 2 | 1 |

The one error came from the agent's first `load_system` call. It wrote `components` as a
mapping from names to parts, but the system document needs a list of
`{name, type, parameters, ...}` items. The error said only that the value "is not of type
'array'" and echoed the whole input. The agent learned the item shape by calling
`get_system` on the Task A system, and then the load succeeded.

## Answers and hand checks

**Task A.** The task gave no valve Kv, so the agent answered for two guesses: a DN50 globe
valve (Kv 40) and a full-bore valve (Kv 150), both fully open.

| Case | Flow | Head | Efficiency | Specific energy |
|---|---|---|---|---|
| Kv 40, full speed | 14.88 m³/h | 30.90 m | 54.8 % | 0.1535 kWh/m³ |
| Kv 150, full speed | 15.94 m³/h | 30.45 m | 56.5 % | 0.1464 kWh/m³ |
| Kv 150, speed 0.9828 (15 m³/h) | 15.01 m³/h | 29.69 m | 55.4 % | 0.1456 kWh/m³ |
| Kv 150, full speed, valve throttled to 15 m³/h (opening 0.285) | 15 m³/h | | 55.0 % | 0.1525 kWh/m³ |

With Kv 40, 15 m³/h needs speed 1.0025, just above rated. With Kv 150, reducing the speed
used about 4.5 % less energy than throttling. The simulated ground tank emptied in 1106 s
(Kv 150) and 1185 s (Kv 40). The agent's hand estimate for the first case was 4.71 m³ at
about 15.3 m³/h, or 18.5 minutes. At empty, the server raised `tank_empty` and the pump's
`low_flow`.

The agent checked the Kv 40 case by hand:

- velocity 2.105 m/s, Re 1.05e5, Churchill friction factor 0.0219 (Colebrook 0.0217);
- losses: friction 5.94 m, static lift 23.5 m, valve 1.41 m and tank port 0.06 m, a total
  of 30.90 m;
- pump head from the fitted curve: 33.97 − 0.013855 × 14.88² = 30.90 m;
- hydraulic power 1.250 kW, and specific energy 2.2834 kW / 14.88 m³/h = 0.1535 kWh/m³.

**Task B.** The agent added its own guess of 2 m of DN50 suction pipe. Everything else used
the catalogue defaults, with both tanks at a 2 m level.

- Clean filter, full speed: 40.16 m³/h, pump head 11.63 m, 38.5 % efficiency, filter drop
  0.523 bar, UV drop 0.403 bar, UV dose 26.9 mJ/cm². Outside the envelope:
  `beyond_curve` (pump), `over_rated_flow` (filter), `underdose` (UV) and `high_velocity`
  (suction pipe).
- Hand checks: filter 0.2 × (0.7 × 2.008 + 0.3 × 2.008²) = 0.523 bar; UV
  0.1 × (40.16 / 20)² = 0.403 bar; dose 20 mW/cm² × 15 L / (40.16 m³/h) = 20 × 1.345 s =
  26.9 mJ/cm²; pump 33.97 − 0.013855 × 40.16² = 11.63 m, which matches the 1.14 bar sum of
  the losses.
- Clogging ramp 0 to 0.8 over 30 minutes: the flow fell from 40.2 to 32.5 m³/h and the
  filter drop rose from 0.52 to 1.30 bar. The filter entered the `loaded` mode at 690 s and
  raised `change_required` at 1590 s. The UV dose rose from 26.9 to 33.2 mJ/cm², still
  under-dosed. The agent enlarged both tanks to 5 m diameter, because the default 2 m
  tanks overflowed or emptied within 5 to 9 minutes.
- Speed 1.2 at clogging 0.8 (steady, both levels at 2 m): the flow rose from 33.7 to
  41.9 m³/h, the shaft power from 3.08 to 5.40 kW, and the dose fell from 32.0 to
  25.7 mJ/cm². The agent concluded that raising the speed defeats both disinfection and
  filtration and probably overloads the motor. Its advice was to backwash and throttle to
  27 m³/h or less.

The agent judged both answers physically plausible. Re-running the trial's systems on
v0.1.0 gives the same operating points (the flows, heads, efficiencies and doses above).
The new warnings now also fire: `outside_preferred_region` in Task A, and
`motor_overload` at speed 1.2 in Task B.

## Friction found

The agent reported 4 major and 12 minor friction items, and 6 physics concerns. The
concerns were checked by hand calculation before any change was made.

| Item | Severity | Outcome in v0.1.0 |
|---|---|---|
| `tools/list` was large (the agent measured about 62 KB; output schemas repeated shared definitions) | major | Output schemas are structure-only. The compact `tools/list` went from 46.1 KB for 15 tools to 34.1 KB for 16. |
| `describe_component` returned 13 to 29 KB per component, mostly scenarios, contracts and provenance | major | New `detail` argument, `"brief"` by default (pump 13.6 KB instead of 30.3 KB); `detail="full"` returns everything. |
| The `load_system` error did not say what a component item looks like and echoed the whole input | major | Schema errors name the expected type and shape and never echo large values; the tool description includes a minimal document. |
| The pump had no motor rating and no preferred operating region: 5.4 kW at speed 1.2 and 62 to 66 % of best-efficiency flow passed without a warning | major | New pump parameters `motor_power`, `preferred_min_fraction` and `preferred_max_fraction`; new warnings `motor_overload` and `outside_preferred_region` (info, after ANSI/HI 9.6.3). |
| No goal seek; the speed for 15 m³/h was found by hand with the affinity laws | minor | New tool `solve_for` (Brent's method). For Task A it finds speed 0.9828; the hand affinity estimate was 0.9820. |
| A clogging ramp needed 60 step events | minor | `simulate` accepts ramp events `{at, ramp: {path: [start, end]}, over}`. |
| Flow units differ by part (L/min for pipes and valves, m³/h for pumps and treatment) | minor | Kept (the units are fixed by the design contract); `units={"*.volume_flow": "m3/h"}` reports every flow in one unit. |
| Simulation warnings gave only the first occurrence | minor | Warnings also carry `last_time` and `active_at_end`. |
| `specific_energy` reached 46,900 kWh/m³ at a leakage flow after the tank emptied | minor | It is `None` unless the flow is above 0.01 m³/h. |
| `list_components` showed pump curves as bare rows | minor | Table parameters list their columns as `name [unit]`. |
| The tank has only bottom ports, so a rooftop tank fed from above cannot be modelled | minor | Not done. It needs a second level-dependent node in the tank; an `inlet_height` parameter is proposed for a later release. |
| The default valve Kv 2.5 m³/h is far too small for a DN50 line and the task gave none | minor | The `kv` description lists typical Kv by nominal size. |
| `add_component` reported an error-level `no_pressure_reference` for every new part | minor | Kept as an error to match `check_system`; a `hint` now says that wiring issues clear once the part is connected. |
| The system keeps its final states after `simulate`, so the tank level had to be reset by hand | minor | `simulate(restore=True)` puts parameters, inputs and states back. |
| `export_system` was in the design's tool table but not in the server | minor | The design now says that it is registered by the WNTR adapter ([docs/wntr-adapter.md](wntr-adapter.md)). |
| Systems are lost when the server process ends | minor | The server instructions say so and point to `get_system` and `load_system`. |

Physics concerns:

- **A drain has no exit loss** (0.23 to 0.26 m, about 0.8 % of the head, at 2.1 to
  2.3 m/s). Confirmed and documented rather than modelled, because the drain does not
  know the pipe diameter. The drain, tank and pipe descriptions now say to add
  `minor_loss = 1` to a pipe that ends in a free discharge or a tank entry. A test shows
  that this adds exactly ρv²/2.
- **A pump drawing from an empty tank under a standing column reported only `low_flow`.**
  Confirmed: the inlet sits at −0.878 bar gauge, leaving about 1.2 m of NPSH available.
  At zero flow the pump cannot tell a starved suction from a closed discharge valve, so
  the check went on the tank instead: a new `drawing_air` warning fires when the tank is
  empty (or a step empties it) and a port is held more than 100 Pa below atmospheric.
- **`specific_energy` diverged at leakage flows.** Fixed, as in the table.
- **Motor overload at speed 1.2 was not diagnosed.** Fixed by `motor_overload`.
- **The Churchill friction factor differs from Colebrook by about 1 %.** No change needed.
- **The Task B skid is a mismatched design** (the pump runs beyond its curve). This is how
  the agent set up the skid, not a model defect; `beyond_curve` flagged it.

All of these changes are in commit `e9d0e6d`, with regression tests in
`tests/test_mcp_agent_trial.py`, and are recorded in [docs/design.md](design.md)
(sections 6.2, 8.8, 8.9 and 9).

## What the trial does and does not show

It shows that one agent could build, solve and simulate two small pumping and treatment
systems through the tools alone, with one recoverable error. Its answers agreed with its
own hand calculations. It also shows that the warnings pointed the agent at the real
problems: a pump beyond its curve, an overloaded filter and an under-dosed UV reactor.

It does not show how often agents succeed, how they compare with writing WNTR or Python
code from scratch, or how different models behave. Those questions belong to the v0.2
composition benchmark.
