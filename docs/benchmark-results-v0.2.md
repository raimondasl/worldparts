# Composition benchmark v0.2: results

Run on 23 and 24 September 2026. Raw summaries are in
[benchmarks/composition/reports/v0.2/](../benchmarks/composition/reports/v0.2/). The method is described in
[benchmarks/README.md](../benchmarks/README.md).

## Summary

- **Both frontier models passed 97 to 100 percent of the 32 tasks under both conditions.**
- **The decision gate is met.** Agents using worldparts passed all 11 level-1 tasks, against a threshold of 80 percent.
- **The comparative thesis is not supported for these models on these tasks.** The thesis was that composing pre-built components beats writing the physics from scratch. An agent writing Python from scratch was just as accurate, including on the judgement tasks. It used about one eighth of the tokens and cost four to five times less.

This result changes what worldparts should claim and what it should measure next. See [What this means](#what-this-means).

## Setup

- **Tasks.** 24 calculation tasks: operating point, sizing, what-if, transient and diagnosis, covering pumping, storage, treatment and distribution. Also 8 judgement tasks, each asking whether a design is acceptable and naming its primary problem from a fixed list. Every task is fully specified with numbers, so an engineer could solve it by hand. Independent auditors solved all 32 without worldparts to confirm this.
- **Conditions.** The same prompt runs in two conditions:
  - `mcp`: the agent has only the worldparts MCP server.
  - `code`: the agent has a shell and files in a scratch folder, with numpy, scipy, fluids and wntr installed, and no worldparts.
- **Models and runs.** Claude Sonnet 5 and Claude Opus 5.5 ran through headless Claude Code 2.1.280, isolated from user settings. Each task ran once per model and condition, 128 sessions in all.
- **Grading.**
  - Numbers must fall within the task's tolerance, usually 3 percent, or 5 percent for transients.
  - Choices must match exactly.
  - A task passes only if every answer passes.
  - Traceability checks that each number appears in some tool result.

## Results

| Model | Condition | Passed | 95 % CI | Answers correct | Median tool calls | Median tokens | Median cost | Total cost |
|---|---|---:|---|---:|---:|---:|---:|---:|
| Sonnet 5 | code | 32/32 (100 %) | 89 to 100 % | 98/98 | 1 | 13,962 | $0.027 | $0.99 |
| Sonnet 5 | mcp | 31/32 (97 %) | 84 to 99 % | 96/98 | 4 | 111,148 | $0.123 | $3.81 |
| Opus 5.5 | code | 31/32 (97 %) | 84 to 99 % | 96/98 | 1 | 13,495 | $0.048 | $1.53 |
| Opus 5.5 | mcp | 31/32 (97 %) | 84 to 99 % | 96/98 | 4 | 102,314 | $0.210 | $5.70 |

Every category passed 100 percent under every condition, with three exceptions:

- MCP what-if passed 5 of 6 for both models.
- Opus code sizing passed 3 of 4.
- Both models passed all 8 judgement tasks in both conditions. Neither needed to be told to check for cavitation, motor overload, operation far from best efficiency, UV under-dose or a tank running dry.

Median wall-clock time was 15 to 19 seconds in both conditions. Costs are API-equivalent estimates reported by the CLI; the runs used a Claude Max plan. The whole benchmark cost about $18 API-equivalent, including the pilot and the reruns described below.

## The three failures

1. **pump-npsh-01, mcp, both models.** Both agents read the pump's NPSH available correctly: it already equals the suction energy balance. Both then added the velocity head a second time. The pump's description gave NPSH available as `(p_inlet_abs - p_vapour) / (rho g)`. It did not say that worldparts port pressures neglect the velocity head, as EPANET does. This was a documentation defect, not a physics defect, and it is fixed in commit 07bc2b4. The same wording misled two different models, which shows that a component's description is part of its correctness.
2. **pump-series-01, code, Opus 5.5.** When sizing a booster in series, the agent computed the head it needed without crediting the 0.84 bar already present at the booster's inlet. As a result, its speed and power were too high. A composed model does this bookkeeping itself. This was the only engineering error in 64 from-scratch sessions.

## Harness problems found and fixed

Each of these would have biased the result if the pilot and a careful look at the transcripts had not caught it:

- **MCP server not connected at the first turn.** A desktop parent session sets `MCP_CONNECTION_NONBLOCKING`, and the child sessions inherited it. The first turn therefore ran with no tools. One model then wrote tool calls as plain text and invented an answer: 23 components where there are 11. The harness now strips the variable, marks the server `alwaysLoad`, and classifies any such session as an infrastructure error.
- **CLI too old for Opus 5.5.** Version 2.1.198 was on PATH, and Opus 5.5 needs 2.1.280. The harness can now select the executable. The full runs used 2.1.280 for both models.
- **Code condition shell restricted to `python ...`.** Ordinary commands such as `sed ...; python s.py` were denied, and agents stopped without an answer. In the first full run this affected 8 of 32 Sonnet code sessions, 6 of which failed. The restriction was removed, a denial followed by no answer is now an infrastructure error, and all 64 code sessions were re-run. The table above uses the re-run.

## What this means

The report behind worldparts cited 24 to 37 percent functional correctness for LLM-generated Modelica components. That is a much harder task than a small, fully specified hydraulic calculation in Python. On this benchmark, current frontier models do the calculation reliably and cheaply. They also bring the engineering judgement that the manifests encode.

So the value of worldparts, if any, is not accuracy on small, fully specified calculations. The candidates below are hypotheses that the next benchmark round should test, not conclusions:

- **Scale.** Large networks, long simulations with controls and events, and repeated what-if studies on the same system, where a from-scratch agent must build and debug a solver each time.
- **Operations.** Calibration and diagnosis against plant data, and a persistent model queried many times across sessions.
- **Data.** Real product curves and ratings with provenance, which a from-scratch agent does not have. This is milestone v0.4.
- **Weaker or cheaper models.** Tools may help small models more. Claude Haiku was not tested.
- **Auditability.** A system document with contracts and provenance can be reviewed and rerun; an ad-hoc script usually cannot.
- **Cost.** The tool list (about 35 KB) and the component descriptions dominate the MCP condition's tokens. They can be cut.

## Limits

- One session per task, model and condition, so the confidence intervals are wide.
- Tasks are fully specified by design. That is fair to the code condition, but it removes the knowledge advantage a library could have.
- The largest system has 11 components.
- The tasks were written by the same project. Independent audits reduced this risk but did not remove it.
- Only two models were tested, both frontier models, and all runs were on one machine.
