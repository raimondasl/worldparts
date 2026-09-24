# Composition benchmark: lib-haiku45-v1

Models: claude-haiku-4-5-20251001. Runs: 40 over 40 task(s).

**Decision gate: no level-1 runs in condition mcp; the gate is not evaluated.**

**Library versus code: level-1 pass rate lib 82 % (9/11, 95 % CI 52 % to 95 %) versus code: no level-1 runs; no level-1 task has valid runs in both, so not compared.**

## Pass rate by condition

| Condition | Passed | Runs | Pass rate | 95 % CI |
|---|---:|---:|---:|---|
| lib | 23 | 40 | 57 % | 42 % to 71 % |

## Pass rate by level

| Level | lib |
|---|---:|
| 1 (2-4 components) | 82 % (9/11) |
| 2 (5-7 components) | 69 % (9/13) |
| 3 (8-19 components) | 50 % (4/8) |
| 4 (20+ components) | 12 % (1/8) |

## Pass rate by category

| Category | lib |
|---|---:|
| diagnosis | 75 % (3/4) |
| judgement | 75 % (6/8) |
| operating_point | 75 % (6/8) |
| sizing | 75 % (3/4) |
| transient | 0 % (0/10) |
| what_if | 83 % (5/6) |

## Pass rate by domain

| Domain | lib |
|---|---:|
| distribution | 29 % (2/7) |
| pumping | 93 % (13/14) |
| storage | 17 % (1/6) |
| treatment | 54 % (7/13) |

## Answers and traceability

A numeric answer is traceable when its value is within 0.5 % of a number in some tool result of the session (it came from a computation, not from thin air). This is a diagnostic: an answer equal to a prompt constant (column 'In prompt') or a closed-form result of prompt constants can be right without any tool result, so an untraceable correct answer is not by itself evidence of fabrication.

| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable | In prompt | Correct, untraceable, not in prompt |
|---|---:|---:|---:|---:|---:|---:|---:|
| lib | 65 % (93/144) | 119 | 71 | 118 (99 %) | 71 | 6 | 0 |

## Effort and cost

| Condition | Median tool calls | Median turns | Median tokens | Median cost | Total cost | Median duration | Timeouts | CLI errors | Format issues |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lib | 9.5 | 10.5 | 299,696 | $0.192 | $9.64 | 201 s | 0 | 0 | 0 |

## Effort and cost by level

Larger systems take more turns and time; timeouts and turn-limit hits count as failures. 'Limits' are the session limits the runs used (older runs did not record them).

| Level | Condition | Pass rate | Median turns | Median tool calls | Median tokens | Median cost | Median duration | Timeouts | Turn-limit hits | Limits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 (2-4 components) | lib | 82 % (9/11) | 5.0 | 4.0 | 139,273 | $0.142 | 175 s | 0 | 0 | 80 turns, 1800 s |
| 2 (5-7 components) | lib | 69 % (9/13) | 11.0 | 10.0 | 231,428 | $0.179 | 195 s | 0 | 0 | 80 turns, 1800 s |
| 3 (8-19 components) | lib | 50 % (4/8) | 15.0 | 14.0 | 402,734 | $0.214 | 208 s | 0 | 0 | 80 turns, 1800 s |
| 4 (20+ components) | lib | 12 % (1/8) | 31.0 | 30.0 | 1,397,048 | $0.501 | 553 s | 0 | 0 | 120 turns, 2400 s |

## Per task

| Task | Level | Category | Domain | lib |
|---|---:|---|---|---|
| judge-01 | 1 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-03 | 1 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-07 | 1 | judgement | distribution | 1/1 runs, 2/2 answers |
| pump-booster-01 | 1 | sizing | pumping | 1/1 runs, 3/3 answers |
| pump-diag-01 | 1 | diagnosis | pumping | 1/1 runs, 2/2 answers |
| pump-drain-01 | 1 | transient | storage | 0/1 runs, 0/3 answers |
| pump-lift-01 | 1 | operating_point | pumping | 1/1 runs, 3/3 answers |
| treat-check-backflow-01 | 1 | what_if | distribution | 0/1 runs, 2/3 answers |
| treat-filter-clog-01 | 1 | what_if | treatment | 1/1 runs, 3/3 answers |
| treat-uv-dose-01 | 1 | operating_point | treatment | 1/1 runs, 3/3 answers |
| treat-uv-throttle-01 | 1 | sizing | treatment | 1/1 runs, 3/3 answers |
| judge-02 | 2 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-04 | 2 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-05 | 2 | judgement | treatment | 0/1 runs, 1/2 answers |
| pump-npsh-01 | 2 | what_if | pumping | 1/1 runs, 5/5 answers |
| pump-parallel-01 | 2 | what_if | pumping | 1/1 runs, 3/3 answers |
| pump-series-01 | 2 | sizing | pumping | 1/1 runs, 3/3 answers |
| pump-throttle-01 | 2 | what_if | pumping | 1/1 runs, 4/4 answers |
| pump-transfer-01 | 2 | transient | storage | 0/1 runs, 1/3 answers |
| treat-clearwell-fill-01 | 2 | transient | treatment | 0/1 runs, 0/4 answers |
| treat-diagnose-filter-01 | 2 | diagnosis | treatment | 0/1 runs, 2/3 answers |
| treat-skid-duty-01 | 2 | operating_point | treatment | 1/1 runs, 4/4 answers |
| treat-two-source-01 | 2 | operating_point | distribution | 1/1 runs, 4/4 answers |
| treat-uv-lamp-01 | 2 | what_if | treatment | 1/1 runs, 4/4 answers |
| judge-06 | 3 | judgement | storage | 1/1 runs, 2/2 answers |
| judge-08 | 3 | judgement | treatment | 0/1 runs, 1/2 answers |
| pump-leak-01 | 3 | diagnosis | pumping | 1/1 runs, 3/3 answers |
| pump-station-01 | 3 | operating_point | pumping | 1/1 runs, 4/4 answers |
| pump-tower-01 | 3 | transient | storage | 0/1 runs, 1/3 answers |
| treat-diagnose-train-01 | 3 | diagnosis | treatment | 1/1 runs, 3/3 answers |
| treat-parallel-uv-01 | 3 | sizing | treatment | 0/1 runs, 4/5 answers |
| treat-tower-drain-01 | 3 | transient | distribution | 0/1 runs, 2/4 answers |
| scale-net-booster-01 | 4 | transient | pumping | 0/1 runs, 1/5 answers |
| scale-net-irrigation-01 | 4 | operating_point | distribution | 0/1 runs, 1/5 answers |
| scale-net-loop-01 | 4 | operating_point | distribution | 0/1 runs, 0/5 answers |
| scale-net-tower-01 | 4 | transient | storage | 0/1 runs, 1/5 answers |
| scale-plant-clog-01 | 4 | transient | treatment | 0/1 runs, 1/6 answers |
| scale-plant-steady-01 | 4 | operating_point | treatment | 1/1 runs, 8/8 answers |
| scale-plant-tower-01 | 4 | transient | storage | 0/1 runs, 1/7 answers |
| scale-plant-transfer-01 | 4 | transient | distribution | 0/1 runs, 0/5 answers |

## Per answer

| Answer | lib |
|---|---:|
| judge-01/acceptable | 1/1 |
| judge-01/primary_problem | 1/1 |
| judge-02/acceptable | 1/1 |
| judge-02/primary_problem | 1/1 |
| judge-03/acceptable | 1/1 |
| judge-03/primary_problem | 1/1 |
| judge-04/acceptable | 1/1 |
| judge-04/primary_problem | 1/1 |
| judge-05/acceptable | 1/1 |
| judge-05/primary_problem | 0/1 |
| judge-06/acceptable | 1/1 |
| judge-06/primary_problem | 1/1 |
| judge-07/acceptable | 1/1 |
| judge-07/primary_problem | 1/1 |
| judge-08/acceptable | 1/1 |
| judge-08/primary_problem | 0/1 |
| pump-booster-01/head | 1/1 |
| pump-booster-01/pump_power | 1/1 |
| pump-booster-01/speed | 1/1 |
| pump-diag-01/fault | 1/1 |
| pump-diag-01/speed | 1/1 |
| pump-drain-01/initial_flow | 0/1 |
| pump-drain-01/level_20min | 0/1 |
| pump-drain-01/time_to_low | 0/1 |
| pump-leak-01/chamber_pressure | 1/1 |
| pump-leak-01/fault | 1/1 |
| pump-leak-01/lost_flow | 1/1 |
| pump-lift-01/flow | 1/1 |
| pump-lift-01/head | 1/1 |
| pump-lift-01/pump_power | 1/1 |
| pump-npsh-01/flow | 1/1 |
| pump-npsh-01/margin_met_50c | 1/1 |
| pump-npsh-01/npsha_20c | 1/1 |
| pump-npsh-01/npsha_50c | 1/1 |
| pump-npsh-01/npshr | 1/1 |
| pump-parallel-01/flow_one_pump | 1/1 |
| pump-parallel-01/flow_two_pumps | 1/1 |
| pump-parallel-01/head_two_pumps | 1/1 |
| pump-series-01/booster_inlet_pressure | 1/1 |
| pump-series-01/booster_power | 1/1 |
| pump-series-01/booster_speed | 1/1 |
| pump-station-01/flow_large | 1/1 |
| pump-station-01/flow_small | 1/1 |
| pump-station-01/header_pressure | 1/1 |
| pump-station-01/zone_pressure | 1/1 |
| pump-throttle-01/power_speed_control | 1/1 |
| pump-throttle-01/power_throttled | 1/1 |
| pump-throttle-01/speed | 1/1 |
| pump-throttle-01/valve_opening | 1/1 |
| pump-tower-01/ground_empty_time | 1/1 |
| pump-tower-01/max_tower_level | 0/1 |
| pump-tower-01/tower_level_3h | 0/1 |
| pump-transfer-01/empty_time | 0/1 |
| pump-transfer-01/initial_flow | 1/1 |
| pump-transfer-01/overflow_time | 0/1 |
| scale-net-booster-01/max_header_pressure | 1/1 |
| scale-net-booster-01/max_station_flow | 0/1 |
| scale-net-booster-01/min_header_pressure | 0/1 |
| scale-net-booster-01/min_pressure_n9 | 0/1 |
| scale-net-booster-01/reservoir_level_24h | 0/1 |
| scale-net-irrigation-01/discharge_pressure | 0/1 |
| scale-net-irrigation-01/lowest_pressure_zone | 1/1 |
| scale-net-irrigation-01/lowest_sprinkler_pressure | 0/1 |
| scale-net-irrigation-01/pump_flow | 0/1 |
| scale-net-irrigation-01/zone_e_flow | 0/1 |
| scale-net-loop-01/east_supply_flow | 0/1 |
| scale-net-loop-01/flow_j11_j21 | 0/1 |
| scale-net-loop-01/pressure_j14 | 0/1 |
| scale-net-loop-01/pressure_j41 | 0/1 |
| scale-net-loop-01/west_supply_flow | 0/1 |
| scale-net-tower-01/max_pump_flow | 0/1 |
| scale-net-tower-01/min_outlet_pressure | 1/1 |
| scale-net-tower-01/min_tower_level | 0/1 |
| scale-net-tower-01/reservoir_level_24h | 0/1 |
| scale-net-tower-01/tower_level_24h | 0/1 |
| scale-plant-clog-01/change_time | 0/1 |
| scale-plant-clog-01/dose_a_end | 0/1 |
| scale-plant-clog-01/first_filter | 0/1 |
| scale-plant-clog-01/flow_b_end | 0/1 |
| scale-plant-clog-01/total_flow_end | 1/1 |
| scale-plant-clog-01/trim_speed_start | 0/1 |
| scale-plant-steady-01/dose_a | 1/1 |
| scale-plant-steady-01/dose_b | 1/1 |
| scale-plant-steady-01/dose_c | 1/1 |
| scale-plant-steady-01/flow_a | 1/1 |
| scale-plant-steady-01/flow_b | 1/1 |
| scale-plant-steady-01/flow_c | 1/1 |
| scale-plant-steady-01/header_pressure | 1/1 |
| scale-plant-steady-01/total_flow | 1/1 |
| scale-plant-tower-01/delivered_volume | 1/1 |
| scale-plant-tower-01/flow_start | 0/1 |
| scale-plant-tower-01/max_clearwell_level | 0/1 |
| scale-plant-tower-01/min_clearwell_level | 0/1 |
| scale-plant-tower-01/pressure_j3_start | 0/1 |
| scale-plant-tower-01/pump_energy | 0/1 |
| scale-plant-tower-01/pumped_volume | 0/1 |
| scale-plant-transfer-01/distributed_volume | 0/1 |
| scale-plant-transfer-01/flow_intake_start | 0/1 |
| scale-plant-transfer-01/intake_volume | 0/1 |
| scale-plant-transfer-01/min_clearwell_level | 0/1 |
| scale-plant-transfer-01/treated_volume | 0/1 |
| treat-check-backflow-01/check_outlet_pressure | 0/1 |
| treat-check-backflow-01/fill_flow | 1/1 |
| treat-check-backflow-01/flow_after_drop | 1/1 |
| treat-clearwell-fill-01/fill_time | 0/1 |
| treat-clearwell-fill-01/full_flow | 0/1 |
| treat-clearwell-fill-01/initial_dose | 0/1 |
| treat-clearwell-fill-01/initial_flow | 0/1 |
| treat-diagnose-filter-01/clogging | 1/1 |
| treat-diagnose-filter-01/corrected_flow | 0/1 |
| treat-diagnose-filter-01/fault | 1/1 |
| treat-diagnose-train-01/corrected_flow | 1/1 |
| treat-diagnose-train-01/fault | 1/1 |
| treat-diagnose-train-01/opening | 1/1 |
| treat-filter-clog-01/clean_flow | 1/1 |
| treat-filter-clog-01/clogged_filter_dp | 1/1 |
| treat-filter-clog-01/clogged_flow | 1/1 |
| treat-parallel-uv-01/dose_a | 1/1 |
| treat-parallel-uv-01/dose_b | 1/1 |
| treat-parallel-uv-01/plant_flow | 1/1 |
| treat-parallel-uv-01/pump_power | 0/1 |
| treat-parallel-uv-01/pump_speed | 1/1 |
| treat-skid-duty-01/dose | 1/1 |
| treat-skid-duty-01/flow | 1/1 |
| treat-skid-duty-01/head | 1/1 |
| treat-skid-duty-01/pump_power | 1/1 |
| treat-tower-drain-01/pressure_c | 1/1 |
| treat-tower-drain-01/pressure_c_end | 1/1 |
| treat-tower-drain-01/time_to_low | 0/1 |
| treat-tower-drain-01/tower_outflow | 0/1 |
| treat-two-source-01/interconnect_flow | 1/1 |
| treat-two-source-01/junction_pressure | 1/1 |
| treat-two-source-01/plant_flow | 1/1 |
| treat-two-source-01/village_pressure | 1/1 |
| treat-uv-dose-01/dose | 1/1 |
| treat-uv-dose-01/flow | 1/1 |
| treat-uv-dose-01/meets_dose | 1/1 |
| treat-uv-lamp-01/aged_dose | 1/1 |
| treat-uv-lamp-01/aged_meets_dose | 1/1 |
| treat-uv-lamp-01/flow | 1/1 |
| treat-uv-lamp-01/opening | 1/1 |
| treat-uv-throttle-01/dose_full_open | 1/1 |
| treat-uv-throttle-01/flow | 1/1 |
| treat-uv-throttle-01/opening | 1/1 |
