# Composition benchmark: scale-opus55-v1

Models: claude-opus-5-5. Runs: 16 over 8 task(s).

**Decision gate: no level-1 runs in condition mcp; the gate is not evaluated.**

## Pass rate by condition

| Condition | Passed | Runs | Pass rate | 95 % CI |
|---|---:|---:|---:|---|
| code | 8 | 8 | 100 % | 68 % to 100 % |
| mcp | 8 | 8 | 100 % | 68 % to 100 % |

## Pass rate by level

| Level | code | mcp |
|---|---:|---:|
| 4 (20+ components) | 100 % (8/8) | 100 % (8/8) |

## Pass rate by category

| Category | code | mcp |
|---|---:|---:|
| operating_point | 100 % (3/3) | 100 % (3/3) |
| transient | 100 % (5/5) | 100 % (5/5) |

## Pass rate by domain

| Domain | code | mcp |
|---|---:|---:|
| distribution | 100 % (3/3) | 100 % (3/3) |
| pumping | 100 % (1/1) | 100 % (1/1) |
| storage | 100 % (2/2) | 100 % (2/2) |
| treatment | 100 % (2/2) | 100 % (2/2) |

## Answers and traceability

A numeric answer is traceable when its value is within 0.5 % of a number in some tool result of the session (it came from a computation, not from thin air). This is a diagnostic: an answer equal to a prompt constant (column 'In prompt') or a closed-form result of prompt constants can be right without any tool result, so an untraceable correct answer is not by itself evidence of fabrication.

| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable | In prompt | Correct, untraceable, not in prompt |
|---|---:|---:|---:|---:|---:|---:|---:|
| code | 100 % (46/46) | 44 | 44 | 44 (100 %) | 44 | 2 | 0 |
| mcp | 100 % (46/46) | 44 | 44 | 37 (84 %) | 37 | 2 | 7 |

## Effort and cost

| Condition | Median tool calls | Median turns | Median tokens | Median cost | Total cost | Median duration | Timeouts | CLI errors | Format issues |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| code | 1.0 | 2.0 | 20,714 | $0.108 | $0.91 | 50 s | 0 | 0 | 0 |
| mcp | 6.0 | 7.0 | 190,882 | $0.410 | $3.58 | 53 s | 0 | 0 | 0 |

## Effort and cost by level

Larger systems take more turns and time; timeouts and turn-limit hits count as failures. 'Limits' are the session limits the runs used (older runs did not record them).

| Level | Condition | Pass rate | Median turns | Median tool calls | Median tokens | Median cost | Median duration | Timeouts | Turn-limit hits | Limits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 4 (20+ components) | code | 100 % (8/8) | 2.0 | 1.0 | 20,714 | $0.108 | 50 s | 0 | 0 | 120 turns, 2400 s |
| 4 (20+ components) | mcp | 100 % (8/8) | 7.0 | 6.0 | 190,882 | $0.410 | 53 s | 0 | 0 | 120 turns, 2400 s |

## Per task

| Task | Level | Category | Domain | code | mcp |
|---|---:|---|---|---|---|
| scale-net-booster-01 | 4 | transient | pumping | 1/1 runs, 5/5 answers | 1/1 runs, 5/5 answers |
| scale-net-irrigation-01 | 4 | operating_point | distribution | 1/1 runs, 5/5 answers | 1/1 runs, 5/5 answers |
| scale-net-loop-01 | 4 | operating_point | distribution | 1/1 runs, 5/5 answers | 1/1 runs, 5/5 answers |
| scale-net-tower-01 | 4 | transient | storage | 1/1 runs, 5/5 answers | 1/1 runs, 5/5 answers |
| scale-plant-clog-01 | 4 | transient | treatment | 1/1 runs, 6/6 answers | 1/1 runs, 6/6 answers |
| scale-plant-steady-01 | 4 | operating_point | treatment | 1/1 runs, 8/8 answers | 1/1 runs, 8/8 answers |
| scale-plant-tower-01 | 4 | transient | storage | 1/1 runs, 7/7 answers | 1/1 runs, 7/7 answers |
| scale-plant-transfer-01 | 4 | transient | distribution | 1/1 runs, 5/5 answers | 1/1 runs, 5/5 answers |

## Per answer

| Answer | code | mcp |
|---|---:|---:|
| scale-net-booster-01/max_header_pressure | 1/1 | 1/1 |
| scale-net-booster-01/max_station_flow | 1/1 | 1/1 |
| scale-net-booster-01/min_header_pressure | 1/1 | 1/1 |
| scale-net-booster-01/min_pressure_n9 | 1/1 | 1/1 |
| scale-net-booster-01/reservoir_level_24h | 1/1 | 1/1 |
| scale-net-irrigation-01/discharge_pressure | 1/1 | 1/1 |
| scale-net-irrigation-01/lowest_pressure_zone | 1/1 | 1/1 |
| scale-net-irrigation-01/lowest_sprinkler_pressure | 1/1 | 1/1 |
| scale-net-irrigation-01/pump_flow | 1/1 | 1/1 |
| scale-net-irrigation-01/zone_e_flow | 1/1 | 1/1 |
| scale-net-loop-01/east_supply_flow | 1/1 | 1/1 |
| scale-net-loop-01/flow_j11_j21 | 1/1 | 1/1 |
| scale-net-loop-01/pressure_j14 | 1/1 | 1/1 |
| scale-net-loop-01/pressure_j41 | 1/1 | 1/1 |
| scale-net-loop-01/west_supply_flow | 1/1 | 1/1 |
| scale-net-tower-01/max_pump_flow | 1/1 | 1/1 |
| scale-net-tower-01/min_outlet_pressure | 1/1 | 1/1 |
| scale-net-tower-01/min_tower_level | 1/1 | 1/1 |
| scale-net-tower-01/reservoir_level_24h | 1/1 | 1/1 |
| scale-net-tower-01/tower_level_24h | 1/1 | 1/1 |
| scale-plant-clog-01/change_time | 1/1 | 1/1 |
| scale-plant-clog-01/dose_a_end | 1/1 | 1/1 |
| scale-plant-clog-01/first_filter | 1/1 | 1/1 |
| scale-plant-clog-01/flow_b_end | 1/1 | 1/1 |
| scale-plant-clog-01/total_flow_end | 1/1 | 1/1 |
| scale-plant-clog-01/trim_speed_start | 1/1 | 1/1 |
| scale-plant-steady-01/dose_a | 1/1 | 1/1 |
| scale-plant-steady-01/dose_b | 1/1 | 1/1 |
| scale-plant-steady-01/dose_c | 1/1 | 1/1 |
| scale-plant-steady-01/flow_a | 1/1 | 1/1 |
| scale-plant-steady-01/flow_b | 1/1 | 1/1 |
| scale-plant-steady-01/flow_c | 1/1 | 1/1 |
| scale-plant-steady-01/header_pressure | 1/1 | 1/1 |
| scale-plant-steady-01/total_flow | 1/1 | 1/1 |
| scale-plant-tower-01/delivered_volume | 1/1 | 1/1 |
| scale-plant-tower-01/flow_start | 1/1 | 1/1 |
| scale-plant-tower-01/max_clearwell_level | 1/1 | 1/1 |
| scale-plant-tower-01/min_clearwell_level | 1/1 | 1/1 |
| scale-plant-tower-01/pressure_j3_start | 1/1 | 1/1 |
| scale-plant-tower-01/pump_energy | 1/1 | 1/1 |
| scale-plant-tower-01/pumped_volume | 1/1 | 1/1 |
| scale-plant-transfer-01/distributed_volume | 1/1 | 1/1 |
| scale-plant-transfer-01/flow_intake_start | 1/1 | 1/1 |
| scale-plant-transfer-01/intake_volume | 1/1 | 1/1 |
| scale-plant-transfer-01/min_clearwell_level | 1/1 | 1/1 |
| scale-plant-transfer-01/treated_volume | 1/1 | 1/1 |
