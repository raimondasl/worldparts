# Composition benchmark: pilot-sonnet5

Models: claude-sonnet-5. Runs: 6 over 3 task(s).

**Decision gate: level-1 pass rate for condition mcp is 100 % (1/1, 95 % CI 21 % to 100 %) versus the 80 % threshold: MET.**

## Pass rate by condition

| Condition | Passed | Runs | Pass rate | 95 % CI |
|---|---:|---:|---:|---|
| code | 3 | 3 | 100 % | 44 % to 100 % |
| mcp | 3 | 3 | 100 % | 44 % to 100 % |

## Pass rate by level

| Level | code | mcp |
|---|---:|---:|
| 1 | 100 % (1/1) | 100 % (1/1) |
| 2 | 100 % (1/1) | 100 % (1/1) |
| 3 | 100 % (1/1) | 100 % (1/1) |

## Pass rate by category

| Category | code | mcp |
|---|---:|---:|
| operating_point | 100 % (3/3) | 100 % (3/3) |

## Pass rate by domain

| Domain | code | mcp |
|---|---:|---:|
| pumping | 100 % (2/2) | 100 % (2/2) |
| treatment | 100 % (1/1) | 100 % (1/1) |

## Answers and traceability

A numeric answer is traceable when its value is within 0.5 % of a number in some tool result of the session (it came from a computation, not from thin air). This is a diagnostic: an answer equal to a prompt constant (column 'In prompt') or a closed-form result of prompt constants can be right without any tool result, so an untraceable correct answer is not by itself evidence of fabrication.

| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable | In prompt | Correct, untraceable, not in prompt |
|---|---:|---:|---:|---:|---:|---:|---:|
| code | 100 % (11/11) | 11 | 11 | 11 (100 %) | 11 | 0 | 0 |
| mcp | 100 % (11/11) | 11 | 11 | 11 (100 %) | 11 | 0 | 0 |

## Effort and cost

| Condition | Median tool calls | Median turns | Median tokens | Median cost | Total cost | Median duration | Timeouts | CLI errors | Format issues |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| code | 4.0 | 5.0 | 79,293 | $0.197 | $0.62 | 59 s | 0 | 0 | 0 |
| mcp | 25.0 | 26.0 | 329,724 | $0.428 | $1.23 | 42 s | 0 | 0 | 0 |

## Per task

| Task | Level | Category | Domain | code | mcp |
|---|---:|---|---|---|---|
| pump-lift-01 | 1 | operating_point | pumping | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-skid-duty-01 | 2 | operating_point | treatment | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| pump-station-01 | 3 | operating_point | pumping | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |

## Per answer

| Answer | code | mcp |
|---|---:|---:|
| pump-lift-01/flow | 1/1 | 1/1 |
| pump-lift-01/head | 1/1 | 1/1 |
| pump-lift-01/pump_power | 1/1 | 1/1 |
| pump-station-01/flow_large | 1/1 | 1/1 |
| pump-station-01/flow_small | 1/1 | 1/1 |
| pump-station-01/header_pressure | 1/1 | 1/1 |
| pump-station-01/zone_pressure | 1/1 | 1/1 |
| treat-skid-duty-01/dose | 1/1 | 1/1 |
| treat-skid-duty-01/flow | 1/1 | 1/1 |
| treat-skid-duty-01/head | 1/1 | 1/1 |
| treat-skid-duty-01/pump_power | 1/1 | 1/1 |
