# Composition benchmark v0.2: results

Run on 23 and 24 September 2026. Raw summaries are in
[benchmarks/composition/reports/v0.2/](../benchmarks/composition/reports/v0.2/). The method is described in
[benchmarks/README.md](../benchmarks/README.md).

## Summary

- **Both frontier models passed 97 to 100 percent of the 32 tasks under both conditions.**
- **The decision gate is met.** Agents using worldparts passed all 11 level-1 tasks, against a threshold of 80 percent.
- **The comparative thesis is not supported for these models on these tasks.** The thesis was that composing pre-built components beats writing the physics from scratch. An agent writing Python from scratch was just as accurate, including on the judgement tasks. It used about one eighth of the tokens and cost four to five times less.

This result changes what worldparts should claim and what it should measure next. See [What this means](#what-this-means).

## Update: scale tasks (level 4)

Eight further tasks test scale. They have 22 to 57 components, looped networks with two supplies, towers on level switches, a three-pump booster staged by header pressure, three-train treatment plants, filters clogging under a PI flow loop, and 24-hour simulations. Independent auditors solved every task without worldparts, using their own Newton solvers or WNTR/EPANET, and landed within 0.7 percent of every reference value. All three models ran every task in both conditions, on Claude Code 2.1.280.

| Model | Condition | Passed | Median tool calls | Median tokens | Median cost | Median time |
|---|---|---:|---:|---:|---:|---:|
| Sonnet 5 | code | 8/8 | 2 | 33,812 | $0.080 | 52 s |
| Sonnet 5 | mcp | 8/8 | 9.5 | 340,658 | $0.316 | 84 s |
| Opus 5.5 | code | 8/8 | 1 | 20,714 | $0.108 | 50 s |
| Opus 5.5 | mcp | 8/8 | 6 | 190,882 | $0.410 | 53 s |
| Haiku 4.5 | code | 0/8 | 24.5 | 1,203,528 | $0.479 | 572 s |
| Haiku 4.5 | mcp | 1/8 | 15.5 | 505,290 | $0.347 | 213 s |

- **The scale hypothesis is not supported for frontier models.** Sonnet 5 and Opus 5.5 built correct 57-component networks and 24-hour control simulations from scratch, mostly with WNTR, in one or two tool calls, at a quarter of the MCP condition's cost.
- **Haiku 4.5 fails at scale in both conditions.** Its answers were present but wrong, for example 288 m³/h from the west supply of the looped network against the correct 177. The component library did not rescue a model that mis-builds a large system. The one pass was the irrigation network with the tools.
- **Two runs were interrupted.** All three harness processes were killed at the same moment during one night without any log output, and no sleep or power event was recorded. The runs resumed from their saved records and redid only the unfinished sessions.

## Update: Claude Haiku 4.5

A follow-up run put Claude Haiku 4.5 through the same 32 tasks, using the same product and harness versions as the Sonnet and Opus runs. This is the first result where the tools make a clear difference.

| Model | Condition | Passed | 95 % CI | Level 1 | Answers correct | Median tool calls | Median tokens | Median cost | Total cost |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| Haiku 4.5 | code | 19/32 (59 %) | 42 to 74 % | 7/11 | 67/98 (68 %) | 4 | 115,110 | $0.098 | $4.27 |
| Haiku 4.5 | mcp | 27/32 (84 %) | 68 to 93 % | 11/11 | 92/98 (94 %) | 13 | 241,883 | $0.130 | $4.50 |

- **The tools add 25 points of pass rate for the smaller model.** The gains are largest on what-if tasks (100 against 33 percent), diagnosis (75 against 25 percent), operating points (100 against 60 percent) and level-1 tasks (100 against 64 percent).
- **Code did better on transient tasks** (4 of 5 against 2 of 5) and on level 3 (5 of 8 against 4 of 8).
- **The five MCP failures are the agent's.** One is a unit slip: the simulation found the right time, but the answer was given in hours instead of minutes. One read two event times about 4 minutes early. Three are modelling misses of 3 to 6 percent.
- **The run was clean:** no infrastructure errors, no denied tool calls and no contamination.
- **This is not an argument that the tools save money.** Sonnet 5 writing Python from scratch scored 100 percent at a median of $0.027 per task. Haiku with worldparts scored 84 percent at $0.130, because it took more turns. The finding is narrower: packaged, tested components substantially raise the reliability of a smaller model. That matters where a small model is required, for example for latency, deployment or policy reasons.

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

- **Scale.** Not supported. Frontier models solved 20- to 57-component networks and 24-hour control simulations from scratch; Haiku failed them in both conditions (see the scale update above). Repeated what-if studies on one persistent system remain untested.
- **Operations.** Calibration and diagnosis against plant data, and a persistent model queried many times across sessions.
- **Data.** Real product curves and ratings with provenance, which a from-scratch agent does not have. This is milestone v0.4.
- **Weaker or cheaper models.** Confirmed for Claude Haiku 4.5: +25 points of pass rate (see the update above).
- **Auditability.** A system document with contracts and provenance can be reviewed and rerun; an ad-hoc script usually cannot.
- **Cost.** The tool list (about 35 KB) and the component descriptions dominate the MCP condition's tokens. They can be cut.

## Limits

- One session per task, model and condition, so the confidence intervals are wide.
- Tasks are fully specified by design. That is fair to the code condition, but it removes the knowledge advantage a library could have.
- The largest system has 11 components.
- The tasks were written by the same project. Independent audits reduced this risk but did not remove it.
- Three models were tested, and all runs were on one machine.
