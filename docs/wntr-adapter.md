# WNTR adapter

`worldparts.adapters.wntr_adapter` carries a composed water network to
[WNTR](https://github.com/USEPA/WNTR) and EPANET 2.2, and measures how far EPANET's answer is
from the worldparts reference runtime. It is the report's "load the same component into
several hosts and measure divergence" experiment (design section 11), applied to pumping,
treatment and distribution networks.

It needs the optional `wntr` package: `uv add wntr` (or `pip install wntr`), or install
worldparts with the extra, `uv add "worldparts[wntr] @ git+https://github.com/raimondasl/worldparts"`
(quote the extra: an unquoted `[wntr]` fails in zsh). WNTR is imported only
when an adapter function runs, so the rest of worldparts is unaffected when it is missing;
the adapter functions then raise `MissingDependencyError` with the install command.

## Running an export or a comparison

Python:

```python
import worldparts as wp
from worldparts.adapters.wntr_adapter import compare_with_wntr, export_inp, to_wntr

s = wp.System.from_dict(doc)            # or build it with s.add / s.connect
wn = to_wntr(s)                         # wntr.network.WaterNetworkModel
text = export_inp(s, "plant.inp")       # EPANET .inp text (also written to the file)
report = compare_with_wntr(s)           # solve both, compare
print(report.summary())                 # readable tables and explanations
report.max_flow_rel_diff, report.max_pressure_abs_diff
report.link("pump").rel_diff            # one component's flow
report.node("pump.outlet").abs_diff     # one node's pressure (bar)
report.to_dict()                        # JSON-ready
```

`translate(system)` returns the model together with the name mapping (`nodes`, `links`,
`boundaries`), the pump-curve choice (`pumps`), the leaks' emitters (`emitters`) and the list
of approximations made for this particular system.

Command line:

```
worldparts export plant.yaml --target wntr_inp -o plant.inp
worldparts export plant.yaml --target wntr_inp -o plant.inp --compare   # report on stdout
worldparts export plant.yaml --target wntr_inp --compare          # .inp on stdout, report on stderr
worldparts export plant.yaml --target wntr_inp --compare --json   # {"inp": ..., "comparison": ...}
```

MCP (registered only when `wntr` is installed): `export_system(system_id, target="wntr_inp")`
returns the `.inp` text and the approximation notes; `compare_with_wntr(system_id)` returns
the maxima and the full report.

## What maps to what

| worldparts | WNTR / EPANET | Exact? |
|---|---|---|
| connection node | junction `J1`, `J2`, ... (zero demand, assigned elevation) | yes |
| `supply` | reservoir named after the instance, head = elevation + gauge pressure / (ρg), joined to its port junction by a TCV with loss coefficient 0 (the "joint", named after the instance) | yes (EPANET's open-valve resistance is negligible) |
| `drain` | reservoir at head = elevation (gauge 0), joined the same way | yes |
| `pipe` | Darcy-Weisbach pipe: length, diameter, roughness (m in WNTR, mm in the `.inp`), minor loss; `height_difference` becomes the elevation difference of its end junctions | friction-factor formula differs (below) |
| `valve` | TCV whose loss coefficient reproduces the effective Kv at the settled position (`kv * φ(opening)`), with the diameter of an adjacent pipe (50 mm if there is none) | yes |
| `check_valve` | 1 mm pipe with a check valve (CV), minor loss reproducing the Kv, adjacent pipe diameter | forward yes; reverse leakage is 0 in EPANET |
| `centrifugal_pump` | HEAD pump with the fitted quadratic, times `1 - wear_head`, as a three-point power curve (when that is exact) or a multi-point curve (41 points to the run-out flow, continued with negative heads to 3 times it), speed setting = relative speed; a stopped pump is exported CLOSED | yes / within 4 mm of head up to 3 times the run-out flow; shaft power and `wear_efficiency` are not exported or compared |
| `tank` | tank at the elevation of its port nodes, initial level = current level (0 when the level is at most 1 mm, which worldparts counts as empty), max level = height, diameter, overflow allowed; each connected port is a TCV to the tank for `port_kv`; a top inlet (`inlet_height > 0`) is exported at the bottom | yes (steady), including a full tank that overflows and an empty tank; a top inlet only while its mouth is submerged (listed as an approximation) |
| `leak` | EPANET emitter at the port's junction, exponent 0.5, coefficient `Cd A opening sqrt(2 g)` (several leaks at one junction add their coefficients) | yes (the emitter is the orifice equation), including backflow at negative pressure |
| `uv_reactor` | TCV through the rated point | yes (its law is quadratic) |
| `media_filter` | TCV matching the filter's pressure drop at a reference flow: the worldparts operating point (default, `reference="operating"`) or the rated flow (`reference="rated"`) | only at the reference flow |
| `mixing_faucet`, `instantaneous_water_heater`, anything else | `UnsupportedComponentError` listing the supported components | - |

Conventions:

- **Loss coefficients.** A quadratic resistance `dp = r Q²` becomes a TCV setting
  `K = 2 g_E A² r / (ρ G)`, with `r = 100 ρ / Kv²` for a Kv in m³/s. `g_E = 9.8156 m/s²` is
  the gravity implied by EPANET's minor-loss constant 0.02517; using it makes EPANET's head
  loss equal to worldparts' pressure drop divided by `ρ G` (standard gravity), so the
  equivalent elements are exact to EPANET's output precision (tested).
- **Elevations.** worldparts has no global elevations (design 5.4). The adapter walks the
  graph adding each pipe's `height_difference` (every other component keeps its two ports
  level), starting at elevation 0 from the first drain of each connected part (else the
  first supply, else a tank), so a drain sits at head 0. Height differences around a loop
  that do not add up to zero raise `WntrExportError`.
- **Units and options.** The `.inp` uses flow units CMH (m³/h), Darcy-Weisbach head loss, a
  relative viscosity that reproduces worldparts' 1.0038e-6 m²/s, ACCURACY 1e-6 (EPANET's
  default is 1e-3) and a single period (duration 0). Pressures are compared as
  `(head - elevation) ρ g` with worldparts' density and standard gravity.
- **Names.** Links carry the instance names; tank ports are `<tank>_<port>`, pump curves
  `<pump>_H`. Names are cut to EPANET's 31 characters and made unique.
- **Emitters.** A leak discharges `Q = Cd A opening sqrt(2 dp / ρ)`. With the pressure head
  `h = dp / (ρ G)` that is `Q = C h^0.5` with `C = Cd A opening sqrt(2 G)`, an EPANET emitter
  with exponent 0.5 (the `EMITTER EXPONENT` option is set explicitly). WNTR stores emitter
  coefficients in SI, m³/s per m^0.5 of pressure head: `HydParam.EmitterCoeff` in
  `wntr/epanet/util.py` converts only the flow unit (times 3600 for CMH) and adds a
  `sqrt(psi / ft)` factor for US units, so the `.inp` carries `3600 C` in m³/h per m^0.5
  (0.18783 for the default 5 mm, Cd 0.6 leak; tested, with a round trip through the
  `.inp`). EPANET reports the emitter's flow as the junction's demand; with several leaks
  at one junction each gets its coefficient's share. EPANET 2.2 lets an emitter take water
  in at negative pressure, as worldparts' `backflow` does.
- **Simulator.** Only WNTR's `EpanetSimulator` (EPANET 2.2) is used. WNTR 1.5's own
  `WNTRSimulator` rejects Darcy-Weisbach head loss (and PBV/GPV valves), so
  `compare_with_wntr(..., simulator="wntr")` raises a clear error instead of switching the
  pipes to Hazen-Williams.

### Pump curves

worldparts fits `H0(Q) = a + b Q + c Q²` with `b <= 0` and `c < 0` and scales it with the
affinity laws. EPANET offers three curve forms:

- one point: EPANET invents a shut-off head of 133 % and a maximum flow of 200 %; never used;
- three points (first at zero flow): EPANET fits `A - B Q^C`. Through `(0, a)`,
  `(Q_max/2, H)`, `(Q_max, H)` this is **exact when `b = 0`**, which is where the bounded fit
  lands for the default pump and for most real curves that are flat at shut-off
  (`C = 2`, deviation 1e-14 m);
- multi-point: linear interpolation. 41 points from zero to the run-out flow deviate at most
  `|c| h² / 8` below the parabola (h the spacing): 4 to 5 mm of head for the curves tested.
  The curve continues at the same spacing past the run-out flow with negative heads, to
  3 times the run-out flow (121 points), with the same bound. A pump pushed past its
  run-out flow (a booster on a pressurised main with a short discharge; worldparts warns
  `beyond_curve`) therefore follows the quadratic in EPANET too, instead of EPANET's
  straight-line extension of the last segment (which gave 2.3 % flow difference at 2 bar
  suction and 11 % at 6 bar). Beyond the end of the curve the approximations list says so.

A worn pump (`wear_head > 0`) has the head law `(1 - wear_head)` times the new pump's
at every flow and speed, so the exported curve is the fitted quadratic times
`1 - wear_head`, which keeps the three-point form exact when `b = 0`. `wear_efficiency`
changes only the shaft power; EPANET gets no efficiency curve, power is not compared, and
the approximations list says so.

The adapter computes both deviations and exports the smaller; `translate(s).pumps[name]`
records the choice. For a curve with `b < 0` (`[[0, 40], [10, 36], [20, 30], [30, 22],
[36, 16]]`) the three-point power curve would be 1.6 m off, the multi-point curve 4 mm. EPANET
applies the same affinity scaling to both forms (`H = s² H0(Q / s)`), so speed settings
carry over exactly.

## Measured divergence

EPANET 2.2 through WNTR 1.5.0 against the reference runtime (the systems are in
`tests/test_wntr_adapter.py`). Flow differences are relative to the worldparts flow of the
same link, pressure differences are absolute in bar gauge.

| Case | Largest flow difference | Largest pressure difference | Test tolerance | Main source |
|---|---|---|---|---|
| 1. supply → pipe (20 m, DN25) → valve (Kv 4, 60 %) → drain | 0.0062 % (3.80 m³/h) | 3.4e-4 bar (0.014 %) | 0.05 %, 2 mbar | pipe friction factor |
| 2. tank → default pump → riser (40 m, DN80, +12 m) → elevated drain | 0.0068 % (38.5 m³/h) | 1.7e-4 bar (0.012 %) | 0.05 %, 2 mbar | riser friction factor; pump curve exact |
| 2b. same, pump curve with `b < 0`, speed 1 / 0.8 | 0.0051 % / 0.0065 % | 1.8e-4 / 9.7e-5 bar | 0.05 %, 2 mbar | multi-point curve (4 mm) and pipe |
| 2c. booster on a 0.5 to 6 bar main, same curve, 10 m DN80 to a drain (past the run-out flow, 52.6 to 84.4 m³/h) | 0.0022 % at most | - | 0.05 % (2 bar case) | multi-point curve past the run-out flow |
| 2d. lift pump into a full rooftop tank that overflows (36.7 m³/h) | 0.0065 % | 1.4e-4 bar | 0.05 % | riser friction factor |
| 3. two supplies (3 and 2.5 bar), five-pipe loop with height differences, valve → drain | 0.091 % (p3, 1.5 m³/h; 0.002 m³/h absolute) | 3.9e-4 bar (0.017 %) | 0.5 %, 0.01 m³/h, 2 mbar | friction differences redistribute flow in the loop |
| 4. supply → pipe → media filter → UV reactor → pipe → drain at the rated 20 m³/h | 0.015 % | 4.6e-5 bar | 0.05 %, 1 mbar | pipes; filter and UV exact at the reference |
| 4b. same train at 1.5 bar (44 m³/h), filter matched at the rated flow | 10.7 % | 0.139 bar | 5 % to 20 % (characterisation) | filter's linear term |
| 4c. same, filter matched at the operating point | 0.014 % | 1.6e-4 bar | 0.05 %, 1 mbar | pipes |
| 2e. case 2 with a worn pump: `wear_head` 0.2 (35.75 m³/h), 0.3 at speed 0.9, and 0.2 on the `b < 0` curve | 0.0086 % / 0.011 % / 0.004 % | 1.6e-4 bar | 0.05 %, 2 mbar | riser friction factor; scaled curve exact |
| 5. supply 3 bar → valve Kv 5 → 5 mm leak and valve Kv 3 → drain | 6.6e-6 (leak 3.4e-7 at 0.84 m³/h) | 1.5e-6 bar | 1e-5 | none: the emitter is the orifice equation; EPANET's output precision |
| 5b. same at -0.3 bar (backflow into the network) | 6.7e-6 (leak 3.6e-7 at -0.27 m³/h) | 1.3e-7 bar | 1e-5 | none |
| 5d. two leaks (5 mm Cd 0.6, 10 mm Cd 0.8) at one junction of case 5 | 2.2e-6 each | - | 1e-5 | none: the shares of one emitter |
| 5c. supply 3 bar → pipe (50 m, DN32) → 5 mm / 20 mm leak → pipe → Kv 3 → drain | 0.016 % / 0.036 % (leak 0.008 % / 0.025 %) | 6.4e-4 bar | 0.5 %, 2 mbar | pipe friction factor |
| 6. top-fed tank (mouth 2.5 m), level 2.5 or 2.8 m (mouth submerged) | 6e-6 | < 1e-5 bar | 1e-5 | none: bottom-fed export is exact |
| 6b. same, level 1 m (mouth 1.5 m above the water) | 25.5 % (inlet 1.264 vs 1.587 m³/h) | - | 20 % to 30 % (characterisation) | EPANET's bottom inlet feels the level, not the mouth |
| equivalent elements only (tank port, check valve, equal-percentage valve, clogged filter, UV) | < 1e-5 | < 1e-5 bar | 1e-5 | EPANET's single-precision output |

Pipe-only lines, by flow regime:

| Regime | Reynolds number | Flow difference | Why |
|---|---|---|---|
| laminar | 155 | 0.080 % | both use 64/Re; EPANET's g = 32.2 ft/s² in the friction term |
| transitional | 3100 | 8.3 % | EPANET interpolates f with a cubic between Re 2000 and 4000; Churchill blends smoothly |
| fully rough turbulent (e/D = 0.01) | 7e5 | 0.013 % | Swamee-Jain vs Churchill |

Why the tolerances are what they are: in turbulent flow the only formula difference left is
the friction factor (Churchill against Swamee-Jain, a few tenths of a percent of `f`, which
moves a flow by half that where the pipe dominates) plus EPANET's gravity constant in the
friction term (0.08 % of the pipe head). The tests allow about ten times the measured value
so that a real mapping error (a wrong unit, a lost minor loss, a wrong elevation) fails
while EPANET's convergence noise does not. In a loop, branch flows are differences of
larger flows, so the relative tolerance is ten times wider.

## Known limitations

- **Filter linearisation.** EPANET has no linear-plus-quadratic loss element. The exported
  TCV matches the filter only at its reference flow. The default reference is the worldparts
  operating point, so an export reproduces the current operating point; a what-if in EPANET
  at another flow diverges (case 4b: 11 % of flow at 2.2 times the reference, 23 % at 0.4
  times). A GPV with a head-loss curve could represent it exactly but is not supported by
  WNTR's own simulator and was left out.
- **Transitional pipe flow.** Between Re 2000 and 4000 EPANET's friction factor differs from
  Churchill's by up to about 10 %.
- **Stopped pumps and reverse flow.** A stopped pump is exported CLOSED; worldparts lets
  water through it as a resistance. EPANET never lets a pump run backwards; worldparts does
  (and warns `reverse_flow`).
- **Leakage.** EPANET's check valve blocks reverse flow completely, worldparts leaks
  `leakage × Kv`. Relative differences of flows below 1e-3 m³/h (closed-valve leakage) are
  not reported.
- **Steady state only.** The export is one steady snapshot: valve positions are the settled
  positions (`opening`), the tank level is the current level, and time-dependent events,
  actuator lags and tank filling are not carried over. EPANET extended-period runs of the
  exported model are possible but not compared.
- **Tank details.** A full tank keeps receiving water in both hosts (worldparts spills the
  excess and warns `tank_overflow`; the tank is exported with EPANET's overflow option,
  the `Overflow` column of `[TANKS]`). worldparts counts a level up to 1 mm as empty and
  blocks the outflow; such a level is exported as 0 so EPANET blocks it too. The
  `drawing_air` diagnostic and worldparts' outflow cap in simulations have no EPANET
  counterpart; tank ports are modelled as TCVs for `port_kv`.
- **Top-fed tanks.** EPANET tanks are bottom-fed. A tank with `inlet_height > 0` is
  exported with the inlet at the bottom: exact while the mouth is under water, but while it
  is above the water EPANET's inflow meets the head of the level instead of the mouth
  height (case 6b: 25 % more inflow with the mouth 1.5 m above the water) and EPANET does
  not block backflow through the inlet. The export lists the tank as an approximation with
  the height of the mouth above the water, as design 13.5 asks. A reservoir at the mouth's
  head would imitate the free discharge in a single period but would not conserve the
  tank's water in an extended-period run. An exact route for the dry mouth exists: a PSV
  with setting `inlet_height` (m) between the inlet TCV and a very short pipe into the tank
  holds the inlet at the mouth's head while the level is below it and opens fully once the
  mouth is submerged, so the water still goes into the tank; in review it matched the
  worldparts inflow to 6.4e-6 at levels of 1.0, 2.49 and 2.8 m. It also blocks backflow
  through a dry mouth, as worldparts does, but it would wrongly block a siphon through a
  submerged mouth (which the current export gets exactly). Adopting it needs a revision
  of design 13.5 and is left for a later version.
- **Pump power.** Shaft power, efficiency and `wear_efficiency` are not exported (no
  efficiency or energy curves) and not compared; the head curve carries `wear_head`.
- **Unsupported components.** The mixing faucet and the water heater (thermal mixing, heat
  input) raise `UnsupportedComponentError`; EPANET has no thermal model.
- **Precision.** EPANET reports results in single precision and stops at ACCURACY 1e-6, so
  differences below about 1e-6 relative are noise.
