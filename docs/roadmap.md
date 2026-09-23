# Roadmap

worldparts follows the recommendations of the [research report](research/world-model-libraries-for-ai-agents.md): wrap existing physics, build the agent-facing packaging, measure whether agents actually compose components correctly, and partner for real product data. Each milestone ends with something measurable.

Progress is tracked in [GitHub milestones](https://github.com/raimondasl/worldparts/milestones).

## v0.1: Hydraulic starter kit

Goal: an agent can build and query the two motivating systems (a bathroom with mixing faucets, a small water-purification skid) through typed tools, without writing physics code.

- Component manifest format 0.1 with a JSON Schema.
- A catalogue of 11 water components, each with scenarios and behavioural contracts that run in CI.
- A reference runtime: a quasi-steady hydraulic and thermal network solver with time stepping for tanks and actuators.
- The `System` composition API and a language-neutral system document.
- An MCP server and a CLI.
- A WNTR/EPANET adapter that measures divergence from the reference runtime.

## v0.2: Composition benchmark

Goal: test the project's central thesis. The report found that no published study measures an agent composing several pre-built components. This milestone builds that measurement.

- A task suite of natural-language composition tasks (bathrooms, purification skids, irrigation, hydronic loops), each with a machine checker based on reference operating points and contracts.
- A harness that runs an agent through the MCP server and records functional correctness, contract violations, tool calls, tokens and wall-clock time.
- A from-scratch baseline where the agent writes WNTR or plain Python code for the same tasks.
- Decision gate from the report: if tool-based composition does not clear 80 percent on simple systems with a frontier model, revisit the packaging thesis before investing further.

## v0.3: Modelica backend

Goal: connect the catalogue to high-fidelity, industry-standard physics.

- Export composed systems to Modelica using Modelica Buildings Library classes, run them in OpenModelica (Docker), and compare with the reference runtime.
- Generate manifests automatically from Modelica annotations through the OpenModelica compiler and from FMU `modelDescription.xml` files.
- FMU-backed components that step inside the reference runtime.

## v0.4: Real product data with provenance

Goal: parameterize components from real products with traceable, licence-cleared data.

- Parameter packs: product-specific parameter sets carrying source document, revision, acquisition method and data licence.
- A pilot corpus of pump curves published under EU Regulation 547/2012, and a proposal for a pump performance representation in the style of ASHRAE Standard 205.
- IFC and buildingSMART Data Dictionary mappings for catalogue components.

## v0.5: Scene runtime

Goal: the bathroom-view case. A faucet in a 3D scene behaves like a faucet.

- A TypeScript or WebAssembly port of the reference runtime.
- A browser demo with a behaving faucet and shower on a shared hot-water line.
- An experiment binding component ports to glTF `KHR_interactivity` or OpenUSD attributes.

## Later

- A qualitative tier: statechart manifests for discrete behaviour that LLMs can write and check.
- More domains: hydronic heating, simple thermal zones, electrical loads.
- A public component registry.
