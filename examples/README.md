# worldparts examples

Each example is a **system document** (YAML, validated by `system.schema.json`) and, for the
pumping examples, a short Python script that loads the document and asks the questions an
engineer or an agent would ask of it. The scripts use only the public API (`import worldparts as wp`)
and print fewer than 60 lines. `tests/test_examples.py` runs them and checks their numbers
against hand calculations (and, for the calibration example, against the wear its synthetic
readings were made with).

The examples are in the repository, not in the installed package. Clone it and run
everything from the repository root:

```
git clone https://github.com/raimondasl/worldparts
cd worldparts
uv sync
```

In your own project, write or save a system document instead (see "System documents and
the CLI" in the [README](../README.md#system-documents-and-the-cli)).

## Purification skid (start here)

`purification_skid.yaml`, `purification_skid.py`

A raw-water tank (topped up by a gravity intake) feeds a centrifugal pump through a short
suction pipe; the pump pushes water through a throttling valve, a pressure sand filter and a
UV reactor, up 16 m to the free-discharge inlet of a clearwell.

```
uv run python examples/purification_skid.py
```

The script:

1. runs the structural pre-flight `check()` and prints the design point: about 21.9 m3/h at
   91 % of the pump's best-efficiency flow, 62 % efficiency, 0.12 kWh/m3, 0.56 bar across the
   clean filter and a UV dose of 49 mJ/cm2 against 40 required, with no warnings;
2. simulates a 48 h filter run with the media clogging linearly, and prints how flow, filter
   pressure drop and UV dose respond and when each warning first appears (the filter reaches
   its 1.5 bar backwash limit after about 47 h; the dose rises as the flow falls);
3. diagnoses a fault by hypothesis simulation: flow is down 20 % and the filter pressure drop
   has doubled. Each candidate fault (clogged filter, valve closed further, worn pump modelled
   as a lower speed) is sized to reproduce the flow drop, and the candidates are ranked by
   how well they predict the filter pressure drop. Only the clogged filter does (1.95x);
4. goal-seeks the valve opening that restores the UV dose after the lamp ages to 75 %
   output (0.545 open gives 42 mJ/cm2, the 40 required plus 5 %).

The goal seek is Brent's method (`scipy.optimize.brentq`) over `System.set` and
`System.solve`, the Python counterpart of the MCP server's `solve_for` tool.

## Rooftop lift

`pump_lift.yaml`, `pump_lift.py`

A pump lifts water from a ground tank to a rooftop tank 25 m higher through 60 m of 50 mm
steel pipe.

```
uv run python examples/pump_lift.py
```

The script prints the full-speed operating point (18.7 m3/h, 34.3 m, 0.143 kWh/m3) with a
hand check of the head balance (static lift, Darcy-Weisbach friction with the Swamee-Jain
factor, Kv losses), finds the speed that delivers 15 m3/h (0.917, 2659 rpm), compares it with
throttling the discharge valve to the same flow (speed control uses about 20 % less energy
per m3), and estimates the time to empty the ground tank, by hand and by simulation (about
20 min, after which the tank and pump warn about drawing air and cavitation).

The tanks are filled and emptied in batches, so `check()` reports the ground tank's inlet and
the rooftop tank's outlet as unconnected (capped) ports. That is intended.

## Pump wear from field readings (calibration)

`calibrate_pump_wear.py`, `pump_wear_readings.yaml`

The rooftop lift's pump after some years in service. `pump_wear_readings.yaml` is a
measurement set (design section 14.1): outlet pressure, flow and shaft power at four
positions of the discharge valve, each with its instrument uncertainty. The readings are
synthetic (the lift with 10 % head wear and 12 % efficiency wear, plus instrument noise), so
the answer is known.

```
uv run python examples/calibrate_pump_wear.py
```

The script:

1. asks `wp.identifiability` which wear a pressure gauge and a flow meter could determine
   before any data exists: head wear (to about 0.004), but not efficiency wear, which only
   changes the shaft power; it recommends a power-related reading as the extra sensor;
2. calibrates both wear inputs to the survey with `wp.calibrate`: 0.097 +/- 0.003 head wear
   and 0.109 +/- 0.007 efficiency wear, both identifiable, reduced chi-square 0.65;
3. fits the survey again without the power readings: efficiency wear is then reported as
   not identifiable, with no standard error, instead of a made-up number;
4. applies the fit and compares the pumps with the valve open: the worn pump delivers about
   9 % less water and needs about 7 % more energy per m3.

## Domestic hot water (secondary)

`domestic_hot_water.yaml`

Mains water feeds an 18 kW instantaneous heater and two mixers (shower and basin). With both
open, the hot draw exceeds what 18 kW can heat to 55 degC: the heater is `saturated`, its
outlet reaches only about 39 degC and it raises `setpoint_not_met`. There is no script; solve
the document with the CLI.

## Booster station (a control loop)

`booster_station.yaml`

A variable-speed booster pump takes water from a 1 bar mains and holds the pressure at the
entrance of a supply zone, 120 m of 65 mm main away, at 2.5 bar. The document's `controls`
block holds a PI loop (`zone_pressure`) that reads `main.port_b.p` and writes `pump.speed`.
`solve` finds the steady speed that meets the setpoint (0.728 at half demand, 0.868 with the
zone valve fully open, where its Kv of 12 passes about 19.0 m3/h at 2.5 bar). The
document's `simulation` block ramps the zone demand from 50 % to fully open between 2 and 5 min; the loop speeds the
pump up, the zone pressure dips by less than 2 % and is back at 2.5 bar by the end. There is
no script; run it with the CLI, which prints a `Controls` section:

```
uv run worldparts solve examples/booster_station.yaml
uv run worldparts simulate examples/booster_station.yaml --var main.port_b.p,pump.speed
```

## Running the system documents with the CLI

Every document works with the `worldparts` command:

```
uv run worldparts validate examples/purification_skid.yaml
uv run worldparts solve examples/purification_skid.yaml
uv run worldparts solve examples/pump_lift.yaml --var pump,riser.velocity
uv run worldparts solve examples/domestic_hot_water.yaml --json
uv run worldparts simulate examples/purification_skid.yaml --var pump.volume_flow,filter.pressure_drop,uv.dose
uv run worldparts simulate examples/pump_lift.yaml --var ground.level,roof.level,pump.volume_flow
```

`simulate` without `--duration` uses the document's own `simulation` block: the skid runs a
48 h filter run with the clogging raised in 6 h steps, and the lift runs at full speed for
20 min (the ground tank empties after about 16 min).

The skid document and the script do not report the same filter run. The document raises
the clogging in 0.1125 steps to 0.9, and its last step falls on the final sample, so the CLI
shows `filter.change_required` only at 48 h (t=172800 s), with a 1.79 bar drop and 10.0 m3/h.
The script's linear ramp to 0.85 reaches the 1.5 bar backwash limit at about 47 h.

Flows are in L/min for pipes, valves, supplies and drains and in m3/h for pumps, tanks,
filters and UV reactors. To see them all in one unit, add `--units '*.volume_flow=m3/h'`
(repeatable; `PATH=UNIT` converts one variable):

```
uv run worldparts solve examples/purification_skid.yaml --units '*.volume_flow=m3/h'
```

An agent can do the same through the MCP server (`uv run worldparts mcp`): `load_system`
accepts these documents as YAML text, and `solve`, `simulate` and `solve_for` answer the same
questions as the scripts.
