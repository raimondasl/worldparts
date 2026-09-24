# Composition benchmark: scale-haiku45-v1

Models: claude-haiku-4-5-20251001. Runs: 16 over 8 task(s).

**Decision gate: no level-1 runs in condition mcp; the gate is not evaluated.**

## Pass rate by condition

| Condition | Passed | Runs | Pass rate | 95 % CI |
|---|---:|---:|---:|---|
| code | 0 | 8 | 0 % | 0 % to 32 % |
| mcp | 1 | 8 | 12 % | 2 % to 47 % |

## Pass rate by level

| Level | code | mcp |
|---|---:|---:|
| 4 (20+ components) | 0 % (0/8) | 12 % (1/8) |

## Pass rate by category

| Category | code | mcp |
|---|---:|---:|
| operating_point | 0 % (0/3) | 33 % (1/3) |
| transient | 0 % (0/5) | 0 % (0/5) |

## Pass rate by domain

| Domain | code | mcp |
|---|---:|---:|
| distribution | 0 % (0/3) | 33 % (1/3) |
| pumping | 0 % (0/1) | 0 % (0/1) |
| storage | 0 % (0/2) | 0 % (0/2) |
| treatment | 0 % (0/2) | 0 % (0/2) |

## Answers and traceability

A numeric answer is traceable when its value is within 0.5 % of a number in some tool result of the session (it came from a computation, not from thin air). This is a diagnostic: an answer equal to a prompt constant (column 'In prompt') or a closed-form result of prompt constants can be right without any tool result, so an untraceable correct answer is not by itself evidence of fabrication.

| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable | In prompt | Correct, untraceable, not in prompt |
|---|---:|---:|---:|---:|---:|---:|---:|
| code | 9 % (4/46) | 44 | 3 | 44 (100 %) | 3 | 9 | 0 |
| mcp | 11 % (5/46) | 44 | 4 | 37 (84 %) | 4 | 8 | 0 |

## Effort and cost

| Condition | Median tool calls | Median turns | Median tokens | Median cost | Total cost | Median duration | Timeouts | CLI errors | Format issues |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| code | 24.5 | 25.5 | 1,203,528 | $0.479 | $4.15 | 572 s | 0 | 0 | 1 |
| mcp | 15.5 | 16.5 | 505,290 | $0.347 | $2.61 | 213 s | 0 | 0 | 1 |

## Effort and cost by level

Larger systems take more turns and time; timeouts and turn-limit hits count as failures. 'Limits' are the session limits the runs used (older runs did not record them).

| Level | Condition | Pass rate | Median turns | Median tool calls | Median tokens | Median cost | Median duration | Timeouts | Turn-limit hits | Limits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 4 (20+ components) | code | 0 % (0/8) | 25.5 | 24.5 | 1,203,528 | $0.479 | 572 s | 0 | 0 | 120 turns, 2400 s |
| 4 (20+ components) | mcp | 12 % (1/8) | 16.5 | 15.5 | 505,290 | $0.347 | 213 s | 0 | 0 | 120 turns, 2400 s |

## Per task

| Task | Level | Category | Domain | code | mcp |
|---|---:|---|---|---|---|
| scale-net-booster-01 | 4 | transient | pumping | 0/1 runs, 0/5 answers | 0/1 runs, 0/5 answers |
| scale-net-irrigation-01 | 4 | operating_point | distribution | 0/1 runs, 1/5 answers | 1/1 runs, 5/5 answers |
| scale-net-loop-01 | 4 | operating_point | distribution | 0/1 runs, 0/5 answers | 0/1 runs, 0/5 answers |
| scale-net-tower-01 | 4 | transient | storage | 0/1 runs, 1/5 answers | 0/1 runs, 0/5 answers |
| scale-plant-clog-01 | 4 | transient | treatment | 0/1 runs, 0/6 answers | 0/1 runs, 0/6 answers |
| scale-plant-steady-01 | 4 | operating_point | treatment | 0/1 runs, 0/8 answers | 0/1 runs, 0/8 answers |
| scale-plant-tower-01 | 4 | transient | storage | 0/1 runs, 2/7 answers | 0/1 runs, 0/7 answers |
| scale-plant-transfer-01 | 4 | transient | distribution | 0/1 runs, 0/5 answers | 0/1 runs, 0/5 answers |

## Per answer

| Answer | code | mcp |
|---|---:|---:|
| scale-net-booster-01/max_header_pressure | 0/1 | 0/1 |
| scale-net-booster-01/max_station_flow | 0/1 | 0/1 |
| scale-net-booster-01/min_header_pressure | 0/1 | 0/1 |
| scale-net-booster-01/min_pressure_n9 | 0/1 | 0/1 |
| scale-net-booster-01/reservoir_level_24h | 0/1 | 0/1 |
| scale-net-irrigation-01/discharge_pressure | 0/1 | 1/1 |
| scale-net-irrigation-01/lowest_pressure_zone | 1/1 | 1/1 |
| scale-net-irrigation-01/lowest_sprinkler_pressure | 0/1 | 1/1 |
| scale-net-irrigation-01/pump_flow | 0/1 | 1/1 |
| scale-net-irrigation-01/zone_e_flow | 0/1 | 1/1 |
| scale-net-loop-01/east_supply_flow | 0/1 | 0/1 |
| scale-net-loop-01/flow_j11_j21 | 0/1 | 0/1 |
| scale-net-loop-01/pressure_j14 | 0/1 | 0/1 |
| scale-net-loop-01/pressure_j41 | 0/1 | 0/1 |
| scale-net-loop-01/west_supply_flow | 0/1 | 0/1 |
| scale-net-tower-01/max_pump_flow | 0/1 | 0/1 |
| scale-net-tower-01/min_outlet_pressure | 0/1 | 0/1 |
| scale-net-tower-01/min_tower_level | 0/1 | 0/1 |
| scale-net-tower-01/reservoir_level_24h | 1/1 | 0/1 |
| scale-net-tower-01/tower_level_24h | 0/1 | 0/1 |
| scale-plant-clog-01/change_time | 0/1 | 0/1 |
| scale-plant-clog-01/dose_a_end | 0/1 | 0/1 |
| scale-plant-clog-01/first_filter | 0/1 | 0/1 |
| scale-plant-clog-01/flow_b_end | 0/1 | 0/1 |
| scale-plant-clog-01/total_flow_end | 0/1 | 0/1 |
| scale-plant-clog-01/trim_speed_start | 0/1 | 0/1 |
| scale-plant-steady-01/dose_a | 0/1 | 0/1 |
| scale-plant-steady-01/dose_b | 0/1 | 0/1 |
| scale-plant-steady-01/dose_c | 0/1 | 0/1 |
| scale-plant-steady-01/flow_a | 0/1 | 0/1 |
| scale-plant-steady-01/flow_b | 0/1 | 0/1 |
| scale-plant-steady-01/flow_c | 0/1 | 0/1 |
| scale-plant-steady-01/header_pressure | 0/1 | 0/1 |
| scale-plant-steady-01/total_flow | 0/1 | 0/1 |
| scale-plant-tower-01/delivered_volume | 0/1 | 0/1 |
| scale-plant-tower-01/flow_start | 1/1 | 0/1 |
| scale-plant-tower-01/max_clearwell_level | 1/1 | 0/1 |
| scale-plant-tower-01/min_clearwell_level | 0/1 | 0/1 |
| scale-plant-tower-01/pressure_j3_start | 0/1 | 0/1 |
| scale-plant-tower-01/pump_energy | 0/1 | 0/1 |
| scale-plant-tower-01/pumped_volume | 0/1 | 0/1 |
| scale-plant-transfer-01/distributed_volume | 0/1 | 0/1 |
| scale-plant-transfer-01/flow_intake_start | 0/1 | 0/1 |
| scale-plant-transfer-01/intake_volume | 0/1 | 0/1 |
| scale-plant-transfer-01/min_clearwell_level | 0/1 | 0/1 |
| scale-plant-transfer-01/treated_volume | 0/1 | 0/1 |
