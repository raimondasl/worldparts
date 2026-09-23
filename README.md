# worldparts

**Agent-ready physical component models.** worldparts packages small physical components (pumps, valves, faucets, pipes, tanks, filters) so an AI agent can discover them, set their parameters with units, wire them into a system, run it, and know when a result is outside the model's validity.

> Status: early development (v0.1 in progress). The design is in [docs/design.md](docs/design.md).

## Why

The physics for everyday components already exists in mature open libraries such as the Modelica Buildings Library, WNTR/EPANET and WaterTAP. What is missing is the layer between those libraries and an AI agent. The [research report](docs/research/world-model-libraries-for-ai-agents.md) behind this project found three things:

- LLMs write physical models that compile but rarely simulate correctly. Published functional-correctness rates for Modelica generation sit around 24 to 37 percent.
- Agents that select, parameterize and wire **pre-built, verified components** through typed tools succeed far more often, up to 100 percent in a water-network study.
- No existing library packages components that way. None ships an agent-readable manifest, a lightweight runtime, behavioural contracts, and provenance for parameter data.

worldparts is that layer.

## What v0.1 contains

- **A component manifest format.** Each component has a YAML manifest validated by JSON Schema. It declares ports, parameters with units and hard limits, inputs, observables, modes, operating envelope, behavioural contracts, test scenarios, implementation bindings and data provenance.
- **A tested catalogue** of 11 water components: supply, drain, pipe, valve, check valve, mixing faucet, instantaneous water heater, centrifugal pump, tank, media filter and UV reactor. Every manifest's contracts run in CI.
- **A reference runtime.** It is a small quasi-steady hydraulic and thermal network solver with time stepping for tanks and actuators.
- **An MCP server** so agents in Claude Code, Claude Desktop or any MCP client can build and query systems through typed tools.
- **A WNTR adapter** that exports water networks to EPANET and measures divergence from the reference runtime.

## License

Apache-2.0. See [LICENSE](LICENSE).
