# Composition benchmark: lib-opus55-v1

Models: claude-opus-5-5. Runs: 40 over 40 task(s).

**Decision gate: no level-1 runs in condition mcp; the gate is not evaluated.**

**Library versus code: level-1 pass rate lib 100 % (11/11, 95 % CI 74 % to 100 %) versus code: no level-1 runs; no level-1 task has valid runs in both, so not compared.**

## Pass rate by condition

| Condition | Passed | Runs | Pass rate | 95 % CI |
|---|---:|---:|---:|---|
| lib | 40 | 40 | 100 % | 91 % to 100 % |

## Pass rate by level

| Level | lib |
|---|---:|
| 1 (2-4 components) | 100 % (11/11) |
| 2 (5-7 components) | 100 % (13/13) |
| 3 (8-19 components) | 100 % (8/8) |
| 4 (20+ components) | 100 % (8/8) |

## Pass rate by category

| Category | lib |
|---|---:|
| diagnosis | 100 % (4/4) |
| judgement | 100 % (8/8) |
| operating_point | 100 % (8/8) |
| sizing | 100 % (4/4) |
| transient | 100 % (10/10) |
| what_if | 100 % (6/6) |

## Pass rate by domain

| Domain | lib |
|---|---:|
| distribution | 100 % (7/7) |
| pumping | 100 % (14/14) |
| storage | 100 % (6/6) |
| treatment | 100 % (13/13) |

## Answers and traceability

A numeric answer is traceable when its value is within 0.5 % of a number in some tool result of the session (it came from a computation, not from thin air). This is a diagnostic: an answer equal to a prompt constant (column 'In prompt') or a closed-form result of prompt constants can be right without any tool result, so an untraceable correct answer is not by itself evidence of fabrication.

| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable | In prompt | Correct, untraceable, not in prompt |
|---|---:|---:|---:|---:|---:|---:|---:|
| lib | 100 % (144/144) | 119 | 119 | 112 (94 %) | 112 | 4 | 7 |

## Effort and cost

| Condition | Median tool calls | Median turns | Median tokens | Median cost | Total cost | Median duration | Timeouts | CLI errors | Format issues |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lib | 1.0 | 2.0 | 15,942 | $0.050 | $2.53 | 17 s | 0 | 0 | 0 |

## Effort and cost by level

Larger systems take more turns and time; timeouts and turn-limit hits count as failures. 'Limits' are the session limits the runs used (older runs did not record them).

| Level | Condition | Pass rate | Median turns | Median tool calls | Median tokens | Median cost | Median duration | Timeouts | Turn-limit hits | Limits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 (2-4 components) | lib | 100 % (11/11) | 2.0 | 1.0 | 15,355 | $0.040 | 14 s | 0 | 0 | 80 turns, 1800 s |
| 2 (5-7 components) | lib | 100 % (13/13) | 2.0 | 1.0 | 15,781 | $0.048 | 16 s | 0 | 0 | 80 turns, 1800 s |
| 3 (8-19 components) | lib | 100 % (8/8) | 2.0 | 1.0 | 16,614 | $0.058 | 19 s | 0 | 0 | 80 turns, 1800 s |
| 4 (20+ components) | lib | 100 % (8/8) | 2.0 | 1.0 | 22,640 | $0.114 | 41 s | 0 | 0 | 120 turns, 2400 s |

## Per task

| Task | Level | Category | Domain | lib |
|---|---:|---|---|---|
| judge-01 | 1 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-03 | 1 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-07 | 1 | judgement | distribution | 1/1 runs, 2/2 answers |
| pump-booster-01 | 1 | sizing | pumping | 1/1 runs, 3/3 answers |
| pump-diag-01 | 1 | diagnosis | pumping | 1/1 runs, 2/2 answers |
| pump-drain-01 | 1 | transient | storage | 1/1 runs, 3/3 answers |
| pump-lift-01 | 1 | operating_point | pumping | 1/1 runs, 3/3 answers |
| treat-check-backflow-01 | 1 | what_if | distribution | 1/1 runs, 3/3 answers |
| treat-filter-clog-01 | 1 | what_if | treatment | 1/1 runs, 3/3 answers |
| treat-uv-dose-01 | 1 | operating_point | treatment | 1/1 runs, 3/3 answers |
| treat-uv-throttle-01 | 1 | sizing | treatment | 1/1 runs, 3/3 answers |
| judge-02 | 2 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-04 | 2 | judgement | pumping | 1/1 runs, 2/2 answers |
| judge-05 | 2 | judgement | treatment | 1/1 runs, 2/2 answers |
| pump-npsh-01 | 2 | what_if | pumping | 1/1 runs, 5/5 answers |
| pump-parallel-01 | 2 | what_if | pumping | 1/1 runs, 3/3 answers |
| pump-series-01 | 2 | sizing | pumping | 1/1 runs, 3/3 answers |
| pump-throttle-01 | 2 | what_if | pumping | 1/1 runs, 4/4 answers |
| pump-transfer-01 | 2 | transient | storage | 1/1 runs, 3/3 answers |
| treat-clearwell-fill-01 | 2 | transient | treatment | 1/1 runs, 4/4 answers |
| treat-diagnose-filter-01 | 2 | diagnosis | treatment | 1/1 runs, 3/3 answers |
| treat-skid-duty-01 | 2 | operating_point | treatment | 1/1 runs, 4/4 answers |
| treat-two-source-01 | 2 | operating_point | distribution | 1/1 runs, 4/4 answers |
| treat-uv-lamp-01 | 2 | what_if | treatment | 1/1 runs, 4/4 answers |
| judge-06 | 3 | judgement | storage | 1/1 runs, 2/2 answers |
| judge-08 | 3 | judgement | treatment | 1/1 runs, 2/2 answers |
| pump-leak-01 | 3 | diagnosis | pumping | 1/1 runs, 3/3 answers |
| pump-station-01 | 3 | operating_point | pumping | 1/1 runs, 4/4 answers |
| pump-tower-01 | 3 | transient | storage | 1/1 runs, 3/3 answers |
| treat-diagnose-train-01 | 3 | diagnosis | treatment | 1/1 runs, 3/3 answers |
| treat-parallel-uv-01 | 3 | sizing | treatment | 1/1 runs, 5/5 answers |
| treat-tower-drain-01 | 3 | transient | distribution | 1/1 runs, 4/4 answers |
| scale-net-booster-01 | 4 | transient | pumping | 1/1 runs, 5/5 answers |
| scale-net-irrigation-01 | 4 | operating_point | distribution | 1/1 runs, 5/5 answers |
| scale-net-loop-01 | 4 | operating_point | distribution | 1/1 runs, 5/5 answers |
| scale-net-tower-01 | 4 | transient | storage | 1/1 runs, 5/5 answers |
| scale-plant-clog-01 | 4 | transient | treatment | 1/1 runs, 6/6 answers |
| scale-plant-steady-01 | 4 | operating_point | treatment | 1/1 runs, 8/8 answers |
| scale-plant-tower-01 | 4 | transient | storage | 1/1 runs, 7/7 answers |
| scale-plant-transfer-01 | 4 | transient | distribution | 1/1 runs, 5/5 answers |

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
| judge-05/primary_problem | 1/1 |
| judge-06/acceptable | 1/1 |
| judge-06/primary_problem | 1/1 |
| judge-07/acceptable | 1/1 |
| judge-07/primary_problem | 1/1 |
| judge-08/acceptable | 1/1 |
| judge-08/primary_problem | 1/1 |
| pump-booster-01/head | 1/1 |
| pump-booster-01/pump_power | 1/1 |
| pump-booster-01/speed | 1/1 |
| pump-diag-01/fault | 1/1 |
| pump-diag-01/speed | 1/1 |
| pump-drain-01/initial_flow | 1/1 |
| pump-drain-01/level_20min | 1/1 |
| pump-drain-01/time_to_low | 1/1 |
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
| pump-tower-01/max_tower_level | 1/1 |
| pump-tower-01/tower_level_3h | 1/1 |
| pump-transfer-01/empty_time | 1/1 |
| pump-transfer-01/initial_flow | 1/1 |
| pump-transfer-01/overflow_time | 1/1 |
| scale-net-booster-01/max_header_pressure | 1/1 |
| scale-net-booster-01/max_station_flow | 1/1 |
| scale-net-booster-01/min_header_pressure | 1/1 |
| scale-net-booster-01/min_pressure_n9 | 1/1 |
| scale-net-booster-01/reservoir_level_24h | 1/1 |
| scale-net-irrigation-01/discharge_pressure | 1/1 |
| scale-net-irrigation-01/lowest_pressure_zone | 1/1 |
| scale-net-irrigation-01/lowest_sprinkler_pressure | 1/1 |
| scale-net-irrigation-01/pump_flow | 1/1 |
| scale-net-irrigation-01/zone_e_flow | 1/1 |
| scale-net-loop-01/east_supply_flow | 1/1 |
| scale-net-loop-01/flow_j11_j21 | 1/1 |
| scale-net-loop-01/pressure_j14 | 1/1 |
| scale-net-loop-01/pressure_j41 | 1/1 |
| scale-net-loop-01/west_supply_flow | 1/1 |
| scale-net-tower-01/max_pump_flow | 1/1 |
| scale-net-tower-01/min_outlet_pressure | 1/1 |
| scale-net-tower-01/min_tower_level | 1/1 |
| scale-net-tower-01/reservoir_level_24h | 1/1 |
| scale-net-tower-01/tower_level_24h | 1/1 |
| scale-plant-clog-01/change_time | 1/1 |
| scale-plant-clog-01/dose_a_end | 1/1 |
| scale-plant-clog-01/first_filter | 1/1 |
| scale-plant-clog-01/flow_b_end | 1/1 |
| scale-plant-clog-01/total_flow_end | 1/1 |
| scale-plant-clog-01/trim_speed_start | 1/1 |
| scale-plant-steady-01/dose_a | 1/1 |
| scale-plant-steady-01/dose_b | 1/1 |
| scale-plant-steady-01/dose_c | 1/1 |
| scale-plant-steady-01/flow_a | 1/1 |
| scale-plant-steady-01/flow_b | 1/1 |
| scale-plant-steady-01/flow_c | 1/1 |
| scale-plant-steady-01/header_pressure | 1/1 |
| scale-plant-steady-01/total_flow | 1/1 |
| scale-plant-tower-01/delivered_volume | 1/1 |
| scale-plant-tower-01/flow_start | 1/1 |
| scale-plant-tower-01/max_clearwell_level | 1/1 |
| scale-plant-tower-01/min_clearwell_level | 1/1 |
| scale-plant-tower-01/pressure_j3_start | 1/1 |
| scale-plant-tower-01/pump_energy | 1/1 |
| scale-plant-tower-01/pumped_volume | 1/1 |
| scale-plant-transfer-01/distributed_volume | 1/1 |
| scale-plant-transfer-01/flow_intake_start | 1/1 |
| scale-plant-transfer-01/intake_volume | 1/1 |
| scale-plant-transfer-01/min_clearwell_level | 1/1 |
| scale-plant-transfer-01/treated_volume | 1/1 |
| treat-check-backflow-01/check_outlet_pressure | 1/1 |
| treat-check-backflow-01/fill_flow | 1/1 |
| treat-check-backflow-01/flow_after_drop | 1/1 |
| treat-clearwell-fill-01/fill_time | 1/1 |
| treat-clearwell-fill-01/full_flow | 1/1 |
| treat-clearwell-fill-01/initial_dose | 1/1 |
| treat-clearwell-fill-01/initial_flow | 1/1 |
| treat-diagnose-filter-01/clogging | 1/1 |
| treat-diagnose-filter-01/corrected_flow | 1/1 |
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
| treat-parallel-uv-01/pump_power | 1/1 |
| treat-parallel-uv-01/pump_speed | 1/1 |
| treat-skid-duty-01/dose | 1/1 |
| treat-skid-duty-01/flow | 1/1 |
| treat-skid-duty-01/head | 1/1 |
| treat-skid-duty-01/pump_power | 1/1 |
| treat-tower-drain-01/pressure_c | 1/1 |
| treat-tower-drain-01/pressure_c_end | 1/1 |
| treat-tower-drain-01/time_to_low | 1/1 |
| treat-tower-drain-01/tower_outflow | 1/1 |
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
