# Composition benchmark: full-sonnet5-v1

Models: claude-sonnet-5. Runs: 64 over 32 task(s).

**Decision gate: level-1 pass rate for condition mcp is 100 % (11/11, 95 % CI 74 % to 100 %) versus the 80 % threshold: MET.**

## Pass rate by condition

| Condition | Passed | Runs | Pass rate | 95 % CI |
|---|---:|---:|---:|---|
| code | 32 | 32 | 100 % | 89 % to 100 % |
| mcp | 31 | 32 | 97 % | 84 % to 99 % |

## Pass rate by level

| Level | code | mcp |
|---|---:|---:|
| 1 | 100 % (11/11) | 100 % (11/11) |
| 2 | 100 % (13/13) | 92 % (12/13) |
| 3 | 100 % (8/8) | 100 % (8/8) |

## Pass rate by category

| Category | code | mcp |
|---|---:|---:|
| diagnosis | 100 % (4/4) | 100 % (4/4) |
| judgement | 100 % (8/8) | 100 % (8/8) |
| operating_point | 100 % (5/5) | 100 % (5/5) |
| sizing | 100 % (4/4) | 100 % (4/4) |
| transient | 100 % (5/5) | 100 % (5/5) |
| what_if | 100 % (6/6) | 83 % (5/6) |

## Pass rate by domain

| Domain | code | mcp |
|---|---:|---:|
| distribution | 100 % (4/4) | 100 % (4/4) |
| pumping | 100 % (13/13) | 92 % (12/13) |
| storage | 100 % (4/4) | 100 % (4/4) |
| treatment | 100 % (11/11) | 100 % (11/11) |

## Answers and traceability

A numeric answer is traceable when its value is within 0.5 % of a number in some tool result of the session (it came from a computation, not from thin air). This is a diagnostic: an answer equal to a prompt constant (column 'In prompt') or a closed-form result of prompt constants can be right without any tool result, so an untraceable correct answer is not by itself evidence of fabrication.

| Condition | Answer accuracy | Numbers | Correct | Traceable | Correct and traceable | In prompt | Correct, untraceable, not in prompt |
|---|---:|---:|---:|---:|---:|---:|---:|
| code | 100 % (98/98) | 75 | 75 | 65 (87 %) | 65 | 2 | 10 |
| mcp | 98 % (96/98) | 75 | 73 | 65 (87 %) | 65 | 2 | 8 |

## Effort and cost

| Condition | Median tool calls | Median turns | Median tokens | Median cost | Total cost | Median duration | Timeouts | CLI errors | Format issues |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| code | 1.0 | 2.0 | 13,962 | $0.027 | $0.99 | 15 s | 0 | 0 | 0 |
| mcp | 4.0 | 5.0 | 111,148 | $0.123 | $3.81 | 15 s | 0 | 0 | 0 |

## Per task

| Task | Level | Category | Domain | code | mcp |
|---|---:|---|---|---|---|
| judge-01 | 1 | judgement | pumping | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| judge-03 | 1 | judgement | pumping | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| judge-07 | 1 | judgement | distribution | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| pump-booster-01 | 1 | sizing | pumping | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| pump-diag-01 | 1 | diagnosis | pumping | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| pump-drain-01 | 1 | transient | storage | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| pump-lift-01 | 1 | operating_point | pumping | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-check-backflow-01 | 1 | what_if | distribution | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-filter-clog-01 | 1 | what_if | treatment | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-uv-dose-01 | 1 | operating_point | treatment | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-uv-throttle-01 | 1 | sizing | treatment | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| judge-02 | 2 | judgement | pumping | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| judge-04 | 2 | judgement | pumping | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| judge-05 | 2 | judgement | treatment | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| pump-npsh-01 | 2 | what_if | pumping | 1/1 runs, 5/5 answers | 0/1 runs, 3/5 answers |
| pump-parallel-01 | 2 | what_if | pumping | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| pump-series-01 | 2 | sizing | pumping | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| pump-throttle-01 | 2 | what_if | pumping | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| pump-transfer-01 | 2 | transient | storage | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-clearwell-fill-01 | 2 | transient | treatment | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| treat-diagnose-filter-01 | 2 | diagnosis | treatment | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-skid-duty-01 | 2 | operating_point | treatment | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| treat-two-source-01 | 2 | operating_point | distribution | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| treat-uv-lamp-01 | 2 | what_if | treatment | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| judge-06 | 3 | judgement | storage | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| judge-08 | 3 | judgement | treatment | 1/1 runs, 2/2 answers | 1/1 runs, 2/2 answers |
| pump-leak-01 | 3 | diagnosis | pumping | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| pump-station-01 | 3 | operating_point | pumping | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |
| pump-tower-01 | 3 | transient | storage | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-diagnose-train-01 | 3 | diagnosis | treatment | 1/1 runs, 3/3 answers | 1/1 runs, 3/3 answers |
| treat-parallel-uv-01 | 3 | sizing | treatment | 1/1 runs, 5/5 answers | 1/1 runs, 5/5 answers |
| treat-tower-drain-01 | 3 | transient | distribution | 1/1 runs, 4/4 answers | 1/1 runs, 4/4 answers |

## Per answer

| Answer | code | mcp |
|---|---:|---:|
| judge-01/acceptable | 1/1 | 1/1 |
| judge-01/primary_problem | 1/1 | 1/1 |
| judge-02/acceptable | 1/1 | 1/1 |
| judge-02/primary_problem | 1/1 | 1/1 |
| judge-03/acceptable | 1/1 | 1/1 |
| judge-03/primary_problem | 1/1 | 1/1 |
| judge-04/acceptable | 1/1 | 1/1 |
| judge-04/primary_problem | 1/1 | 1/1 |
| judge-05/acceptable | 1/1 | 1/1 |
| judge-05/primary_problem | 1/1 | 1/1 |
| judge-06/acceptable | 1/1 | 1/1 |
| judge-06/primary_problem | 1/1 | 1/1 |
| judge-07/acceptable | 1/1 | 1/1 |
| judge-07/primary_problem | 1/1 | 1/1 |
| judge-08/acceptable | 1/1 | 1/1 |
| judge-08/primary_problem | 1/1 | 1/1 |
| pump-booster-01/head | 1/1 | 1/1 |
| pump-booster-01/pump_power | 1/1 | 1/1 |
| pump-booster-01/speed | 1/1 | 1/1 |
| pump-diag-01/fault | 1/1 | 1/1 |
| pump-diag-01/speed | 1/1 | 1/1 |
| pump-drain-01/initial_flow | 1/1 | 1/1 |
| pump-drain-01/level_20min | 1/1 | 1/1 |
| pump-drain-01/time_to_low | 1/1 | 1/1 |
| pump-leak-01/chamber_pressure | 1/1 | 1/1 |
| pump-leak-01/fault | 1/1 | 1/1 |
| pump-leak-01/lost_flow | 1/1 | 1/1 |
| pump-lift-01/flow | 1/1 | 1/1 |
| pump-lift-01/head | 1/1 | 1/1 |
| pump-lift-01/pump_power | 1/1 | 1/1 |
| pump-npsh-01/flow | 1/1 | 1/1 |
| pump-npsh-01/margin_met_50c | 1/1 | 1/1 |
| pump-npsh-01/npsha_20c | 1/1 | 0/1 |
| pump-npsh-01/npsha_50c | 1/1 | 0/1 |
| pump-npsh-01/npshr | 1/1 | 1/1 |
| pump-parallel-01/flow_one_pump | 1/1 | 1/1 |
| pump-parallel-01/flow_two_pumps | 1/1 | 1/1 |
| pump-parallel-01/head_two_pumps | 1/1 | 1/1 |
| pump-series-01/booster_inlet_pressure | 1/1 | 1/1 |
| pump-series-01/booster_power | 1/1 | 1/1 |
| pump-series-01/booster_speed | 1/1 | 1/1 |
| pump-station-01/flow_large | 1/1 | 1/1 |
| pump-station-01/flow_small | 1/1 | 1/1 |
| pump-station-01/header_pressure | 1/1 | 1/1 |
| pump-station-01/zone_pressure | 1/1 | 1/1 |
| pump-throttle-01/power_speed_control | 1/1 | 1/1 |
| pump-throttle-01/power_throttled | 1/1 | 1/1 |
| pump-throttle-01/speed | 1/1 | 1/1 |
| pump-throttle-01/valve_opening | 1/1 | 1/1 |
| pump-tower-01/ground_empty_time | 1/1 | 1/1 |
| pump-tower-01/max_tower_level | 1/1 | 1/1 |
| pump-tower-01/tower_level_3h | 1/1 | 1/1 |
| pump-transfer-01/empty_time | 1/1 | 1/1 |
| pump-transfer-01/initial_flow | 1/1 | 1/1 |
| pump-transfer-01/overflow_time | 1/1 | 1/1 |
| treat-check-backflow-01/check_outlet_pressure | 1/1 | 1/1 |
| treat-check-backflow-01/fill_flow | 1/1 | 1/1 |
| treat-check-backflow-01/flow_after_drop | 1/1 | 1/1 |
| treat-clearwell-fill-01/fill_time | 1/1 | 1/1 |
| treat-clearwell-fill-01/full_flow | 1/1 | 1/1 |
| treat-clearwell-fill-01/initial_dose | 1/1 | 1/1 |
| treat-clearwell-fill-01/initial_flow | 1/1 | 1/1 |
| treat-diagnose-filter-01/clogging | 1/1 | 1/1 |
| treat-diagnose-filter-01/corrected_flow | 1/1 | 1/1 |
| treat-diagnose-filter-01/fault | 1/1 | 1/1 |
| treat-diagnose-train-01/corrected_flow | 1/1 | 1/1 |
| treat-diagnose-train-01/fault | 1/1 | 1/1 |
| treat-diagnose-train-01/opening | 1/1 | 1/1 |
| treat-filter-clog-01/clean_flow | 1/1 | 1/1 |
| treat-filter-clog-01/clogged_filter_dp | 1/1 | 1/1 |
| treat-filter-clog-01/clogged_flow | 1/1 | 1/1 |
| treat-parallel-uv-01/dose_a | 1/1 | 1/1 |
| treat-parallel-uv-01/dose_b | 1/1 | 1/1 |
| treat-parallel-uv-01/plant_flow | 1/1 | 1/1 |
| treat-parallel-uv-01/pump_power | 1/1 | 1/1 |
| treat-parallel-uv-01/pump_speed | 1/1 | 1/1 |
| treat-skid-duty-01/dose | 1/1 | 1/1 |
| treat-skid-duty-01/flow | 1/1 | 1/1 |
| treat-skid-duty-01/head | 1/1 | 1/1 |
| treat-skid-duty-01/pump_power | 1/1 | 1/1 |
| treat-tower-drain-01/pressure_c | 1/1 | 1/1 |
| treat-tower-drain-01/pressure_c_end | 1/1 | 1/1 |
| treat-tower-drain-01/time_to_low | 1/1 | 1/1 |
| treat-tower-drain-01/tower_outflow | 1/1 | 1/1 |
| treat-two-source-01/interconnect_flow | 1/1 | 1/1 |
| treat-two-source-01/junction_pressure | 1/1 | 1/1 |
| treat-two-source-01/plant_flow | 1/1 | 1/1 |
| treat-two-source-01/village_pressure | 1/1 | 1/1 |
| treat-uv-dose-01/dose | 1/1 | 1/1 |
| treat-uv-dose-01/flow | 1/1 | 1/1 |
| treat-uv-dose-01/meets_dose | 1/1 | 1/1 |
| treat-uv-lamp-01/aged_dose | 1/1 | 1/1 |
| treat-uv-lamp-01/aged_meets_dose | 1/1 | 1/1 |
| treat-uv-lamp-01/flow | 1/1 | 1/1 |
| treat-uv-lamp-01/opening | 1/1 | 1/1 |
| treat-uv-throttle-01/dose_full_open | 1/1 | 1/1 |
| treat-uv-throttle-01/flow | 1/1 | 1/1 |
| treat-uv-throttle-01/opening | 1/1 | 1/1 |
