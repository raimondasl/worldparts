"""Rooftop lift: operating point, speed control against throttling, and tank drain-down.

A pump lifts water from a ground tank to a rooftop tank 25 m higher through 60 m of 50 mm
steel pipe (the system document ``pump_lift.yaml`` next to this script). The script:

1. solves the operating point at full speed and checks it by hand (static lift plus
   Darcy-Weisbach friction equals the pump head);
2. finds the pump speed that delivers 15 m3/h (goal seek on ``pump.speed``);
3. compares that with throttling the discharge valve to the same flow at full speed;
4. estimates how long the ground tank lasts at 15 m3/h, by hand and by simulation.

Run it with ``uv run python examples/pump_lift.py``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml
from scipy.optimize import brentq

import worldparts as wp

DOCUMENT = Path(__file__).with_name("pump_lift.yaml")
DUTY_FLOW = 15.0  # m3/h
G = 9.80665  # m/s2


def load_system() -> wp.System:
    """The rooftop lift from its system document."""
    return wp.System.from_dict(yaml.safe_load(DOCUMENT.read_text(encoding="utf-8")))


def goal_seek(
    s: wp.System, target: str, value: float, vary: str, lower: float, upper: float
) -> tuple[float, wp.SolveResult]:
    """Set ``vary`` so that ``target`` equals ``value`` (Brent's method on steady solves).

    The Python counterpart of the MCP tool ``solve_for``. ``vary`` is left at the root.
    """

    def miss(x: float) -> float:
        s.set(vary, x)
        return s.solve()[target] - value

    x = brentq(miss, lower, upper, xtol=1e-9)
    s.set(vary, x)
    return x, s.solve()


def names(result: wp.SolveResult | wp.SimulationResult) -> str:
    """Warning paths, or 'none'."""
    return ", ".join(w.path for w in result.warnings) or "none"


def hand_check(s: wp.System, flow: float) -> dict[str, float]:
    """System head in m at ``flow`` (m3/h) from textbook formulas, independent of the solver.

    Static lift from the tank levels, Darcy-Weisbach with the Swamee-Jain friction factor,
    minor losses in velocity heads, and Kv losses ``SG * (Q / Kv)**2`` bar for the valve and
    the two tank nozzles.
    """
    rho, nu = 998.2, 1.002e-3 / 998.2
    q = flow / 3600
    d = s.get("riser.diameter", "m")
    v = q / (math.pi * d * d / 4)
    re = v * d / nu
    rel = s.get("riser.roughness", "m") / d
    f = 0.25 / math.log10(rel / 3.7 + 5.74 / re**0.9) ** 2
    hv = v * v / (2 * G)
    pipe = (f * s.get("riser.length") / d + s.get("riser.minor_loss")) * hv
    kv_bar = (flow / s.get("valve.kv")) ** 2 + (flow / s.get("ground.port_kv")) ** 2
    kv_bar += (flow / s.get("roof.port_kv")) ** 2
    fittings = rho / 1000 * kv_bar * 1e5 / (rho * G)
    static = s.get("riser.height_difference") + s.get("roof.level") - s.get("ground.level")
    total = static + pipe + fittings
    return {"static": static, "pipe": pipe, "v": v, "f": f, "fittings": fittings, "total": total}


def duty(label: str, r: wp.SolveResult) -> None:
    """One line of the energy comparison."""
    print(
        f"  {label:<24}{r['pump.volume_flow']:6.1f}  {r['pump.head']:6.1f}"
        f"  {r['pump.efficiency']:6.1f}  {r['pump.shaft_power']:6.2f}"
        f"  {r['pump.specific_energy']:8.4f}  {names(r)}"
    )


def main() -> dict[str, Any]:
    """Run the four studies on the rooftop lift and return their results."""
    s = load_system()
    for issue in s.check():
        print(f"check(): {issue.severity} {issue.code} at {issue.where} (capped on purpose)")

    # 1. Full speed, with an independent hand check of the head balance at the solved flow.
    full = s.solve()
    print("\n1. Full speed (2900 rpm)")
    print(
        f"  flow {full['pump.volume_flow']:.2f} m3/h, head {full['pump.head']:.2f} m, "
        f"efficiency {full['pump.efficiency']:.1f} % (BEP {full['pump.bep_flow']:.1f} m3/h)"
    )
    print(
        f"  shaft power {full['pump.shaft_power']:.2f} kW, specific energy "
        f"{full['pump.specific_energy']:.4f} kWh/m3, warnings: {names(full)}"
    )
    hand = hand_check(s, full["pump.volume_flow"])
    print(
        f"  hand check: static {hand['static']:.2f} m + pipe {hand['pipe']:.2f} m "
        f"(v {hand['v']:.2f} m/s, Swamee-Jain f {hand['f']:.4f})"
    )
    print(
        f"    + valve and tank nozzles {hand['fittings']:.2f} m = {hand['total']:.2f} m "
        f"(solver: {full['pump.head']:.2f} m)"
    )

    # 2. Speed control and 3. throttling to the same duty flow.
    speed, by_speed = goal_seek(s, "pump.volume_flow", DUTY_FLOW, "pump.speed", 0.5, 1.0)
    s.set("pump.speed", 1.0)
    opening, by_valve = goal_seek(s, "pump.volume_flow", DUTY_FLOW, "valve.opening", 0.02, 1.0)
    s.set("valve.opening", 1.0)
    print(f"\n2-3. Delivering {DUTY_FLOW:g} m3/h")
    print(
        f"  {'control':<24}{'m3/h':>6}  {'head m':>6}  {'eff %':>6}  {'kW':>6}  {'kWh/m3':>8}"
        "  warnings"
    )
    duty("full speed (no control)", full)
    duty(f"speed {speed:.3f} ({by_speed['pump.speed_rpm']:.0f} rpm)", by_speed)
    duty(f"valve opening {opening:.3f}", by_valve)
    saving = 1 - by_speed["pump.specific_energy"] / by_valve["pump.specific_energy"]
    print(f"  throttling burns {by_valve['valve.pressure_drop']:.2f} bar in the valve;")
    print(
        f"  speed control needs {saving:.0%} less energy per m3 "
        f"({1000 * by_speed['pump.specific_energy']:.0f} vs "
        f"{1000 * by_valve['pump.specific_energy']:.0f} kWh per 1000 m3)"
    )

    # 4. Drain-down of the ground tank at the speed-controlled duty.
    s.set("pump.speed", speed)
    ground_area = math.pi * s.get("ground.diameter") ** 2 / 4
    roof_area = math.pi * s.get("roof.diameter") ** 2 / 4
    usable = ground_area * s.get("ground.level")
    estimate = usable / DUTY_FLOW * 60
    # Refined estimate: the flow with both tanks half-way through the transfer.
    half_way = {
        "ground.level": s.get("ground.level") / 2,
        "roof.level": s.get("roof.level") + usable / 2 / roof_area,
    }
    s.set_values(half_way)
    mid_flow = s.solve()["pump.volume_flow"]
    s.reset_states()  # back to the initial levels
    refined = usable / mid_flow * 60
    sim = s.simulate(duration="30 min", step="10 s", restore=True)
    first = {w.path: w.time for w in sim.warnings}
    empty = first["ground.tank_empty"] / 60
    print(f"\n4. Ground tank drain-down at speed {speed:.3f}")
    print(f"  hand estimate: {usable:.2f} m3 / {DUTY_FLOW:g} m3/h = {estimate:.1f} min")
    print(
        f"  the lift grows as the levels change; at the half-way levels the flow is "
        f"{mid_flow:.2f} m3/h: {refined:.1f} min"
    )
    print(f"  simulated: ground.tank_empty first at {empty:.1f} min")
    for w in sim.warnings:
        if w.path != "ground.tank_empty":
            print(f"    {w.path} ({w.severity}) from {w.time / 60:.1f} min")
    print(
        f"  rooftop tank level {sim['roof.level'][0]:.2f} -> {sim.final['roof.level']:.2f} m; "
        "stop the pump on a low-level switch before the tank runs dry"
    )
    return {
        "full": full,
        "speed": speed,
        "by_speed": by_speed,
        "opening": opening,
        "by_valve": by_valve,
        "estimate_min": estimate,
        "refined_min": refined,
        "hand": hand,
        "empty_min": empty,
        "sim": sim,
    }


if __name__ == "__main__":
    main()
