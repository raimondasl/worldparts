# Roadmap

worldparts follows the recommendations of the [research report](research/world-model-libraries-for-ai-agents.md): wrap existing physics, build the agent-facing packaging, measure whether agents actually compose components correctly, and partner for real product data.

The focus is the use case with paying users and an open substrate: **pumping, water treatment and distribution**, for design checks, what-if analysis and fault diagnosis. Each milestone ends with something measurable.

Progress is tracked in [GitHub milestones](https://github.com/raimondasl/worldparts/milestones).

## v0.1: Hydraulic starter kit

Goal: an agent can build and query a small water-treatment skid (tank, pump, filter, UV reactor) through typed tools, without writing physics code.

- Component manifest format 0.1 with a JSON Schema.
- A catalogue of 11 water components, each with scenarios and behavioural contracts that run in CI.
- A reference runtime: a quasi-steady hydraulic and thermal network solver with time stepping for tanks and actuators.
- The `System` composition API and a language-neutral system document.
- An MCP server and a CLI.
- A WNTR/EPANET adapter that measures divergence from the reference runtime.

## v0.2: Composition benchmark

**Done (September 2026).** Both Claude Sonnet 5 and Claude Opus 5.5 passed 97 to 100 percent of 32 tasks, with and without worldparts. The decision gate is met, but from-scratch Python was just as accurate and several times cheaper on these small, fully specified tasks. See [the results](benchmark-results-v0.2.md). The next benchmark round tests where a component library could still add value: scale, operations against plant data, product data, weaker models, and cost.

Goal: test the project's central thesis. The report found that no published study measures an agent composing several pre-built components. This milestone builds that measurement on pumping, treatment and distribution tasks.

- Build, sizing, what-if and diagnosis tasks, each with a machine checker based on reference operating points and contracts.
- A harness that runs an agent through the MCP server and records functional correctness, contract violations, tool calls, tokens and wall-clock time.
- A from-scratch baseline where the agent writes WNTR or plain Python code for the same tasks.
- Decision gate from the report: if tool-based composition does not clear 80 percent on simple systems with a frontier model, revisit the packaging thesis before investing further.

## Direction after v0.2 (September 2026)

v0.2 showed that frontier models write correct small-system hydraulics from scratch. The owner then chose the "data and operations" direction, with a hard constraint: **if worldparts does not provide clear value, the project pivots or closes.**

Research into licences and prior art ([summary](research/data-and-operations-2026-09.md)) changed the plan:

- **Product data is on hold.** Manufacturers' terms forbid redistributing their pump curves, so the v0.4 corpus below cannot be built as planned without written permission.
- **Operations is where value is still open.** The open gap found is plant-scale diagnosis that ranks faults across component types and says honestly when the data cannot decide.

The [operations benchmark](../benchmarks/operations/PREREGISTRATION.md) decides whether that gap is worth a library. It is pre-registered and headroom-first. It first checks whether frontier agents working from scratch already pass realistic operations tasks. Only if they do not does it test worldparts against the same agent with the same method checklist and against a competing script toolkit.

Its outcome decides one of three paths:

- **Continue:** keep building worldparts.
- **Pivot:** publish the method as an agent skill, or serve small models.
- **Close:** close the project.

## v0.3: Pump systems and diagnostics

Goal: make worldparts useful for an operating plant, not just a design sketch. Calibration, identifiability and diagnosis (design section 14) are built and reviewed on a branch. Whether they are finished and released depends on the operations benchmark's Stage 0.

- Pump wear and degradation.
- Calibration from sensor data, with an identifiability report that says which parameters the available sensors actually determine.
- Fault diagnosis by hypothesis simulation: rank clogged filters, worn impellers, closed valves, cavitation and leaks by how well each explains the measurements.
- Pump stations: controllers, parallel pumps, VFD energy analysis and specific energy.
- A plant-model oracle for control logic, so generated control code is tested against the equipment it controls.
- More water components: pressure-reducing valve, dosing pump, RO membrane stage, leak, contact tank.

## v0.4: Real product data with provenance (on hold)

**On hold since September 2026.** Almost every major pump manufacturer's terms forbid redistributing their curves. The only clean public source, the US DOE Compliance Certification Database, gives one best-efficiency point per pump. This milestone resumes only if manufacturers grant redistribution rights in writing. A pack format could still let users build private packs from their own datasheets, but that is not planned unless the operations benchmark shows value.

Goal: parameterize components from real products with traceable, licence-cleared data.

- Parameter packs: product-specific parameter sets carrying source document, revision, acquisition method and data licence.
- A pilot corpus of pump curves published under EU Regulation 547/2012, and a proposal for a pump performance representation in the style of ASHRAE Standard 205.
- IFC and buildingSMART Data Dictionary mappings for catalogue components.

## v0.5: Modelica backend

Goal: connect the catalogue to high-fidelity, industry-standard physics.

- Export composed systems to Modelica using Modelica Buildings Library classes, run them in OpenModelica (Docker), and compare with the reference runtime.
- Generate manifests automatically from Modelica annotations through the OpenModelica compiler and from FMU `modelDescription.xml` files.
- FMU-backed components that step inside the reference runtime.

## Later

These are open for contributors but not on the current plan.

- A TypeScript or WebAssembly port of the runtime, and a browser demo (issues labelled `later`).
- A qualitative tier: statechart manifests for discrete behaviour.
- More domains: hydronic heating, simple thermal zones, electrical loads.
- A public component registry.

The catalogue also includes a mixing faucet and an instantaneous water heater. They exercise the thermal-mixing parts of the runtime, but building services are not a focus.
