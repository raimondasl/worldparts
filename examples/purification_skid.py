"""Purification skid: design check, filter clogging, fault diagnosis and goal seek.

A raw-water tank feeds a centrifugal pump through a short suction pipe; the pump pushes the
water through a throttling valve, a pressure sand filter and a UV reactor up to a clearwell.
The system is the document ``purification_skid.yaml`` next to this script.

The script answers four questions an operator or a design reviewer would ask:

1. Is the design point sensible? (pump near its best-efficiency point, UV dose met)
2. How does the plant respond as the filter clogs over a 48 h run, and when do the first
   warnings appear?
3. Flow has dropped 20 % and the filter pressure drop has doubled. Is that a clogged filter,
   a partly closed valve or a worn pump?
4. The UV lamp has aged to 75 % output. Which valve opening restores the required dose?

Run it with ``uv run python examples/purification_skid.py``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml
from scipy.optimize import brentq

import worldparts as wp

DOCUMENT = Path(__file__).with_name("purification_skid.yaml")
RUN_HOURS = 48  # length of the filter run
FINAL_CLOGGING = 0.85  # media clogging at the end of the run (linear ramp from 0)
LAMP_OUTPUT = 0.75  # aged UV lamp, fraction of full output
DOSE_MARGIN = 1.05  # goal-seek target: required dose plus 5 %


def load_system() -> wp.System:
    """The purification skid from its system document."""
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


def design_point(s: wp.System) -> wp.SolveResult:
    """Section 1: pre-flight check and the operating-point table."""
    issues = s.check()
    print(f"check(): {len(issues)} issue(s)" + "".join(f"\n  {i.code}: {i.where}" for i in issues))
    r = s.solve()
    bep = r["pump.bep_flow"]
    rows = [
        ("flow", f"{r['pump.volume_flow']:.1f} m3/h", f"{r['pump.volume_flow'] / bep:.0%} of BEP"),
        ("pump head", f"{r['pump.head']:.1f} m", ""),
        ("pump efficiency", f"{r['pump.efficiency']:.1f} %", ""),
        ("shaft power", f"{r['pump.shaft_power']:.2f} kW", ""),
        ("specific energy", f"{r['pump.specific_energy']:.3f} kWh/m3", ""),
        (
            "NPSH available",
            f"{r['pump.npsh_available']:.1f} m",
            f"required {r['pump.npsh_required']:.1f} m",
        ),
        ("valve dp", f"{r['valve.pressure_drop']:.3f} bar", f"opening {s.get('valve.opening')}"),
        (
            "filter dp",
            f"{r['filter.pressure_drop']:.3f} bar",
            f"backwash at {s.get('filter.change_pressure_drop'):g} bar",
        ),
        ("UV dose", f"{r['uv.dose']:.1f} mJ/cm2", f"required {s.get('uv.required_dose'):g}"),
    ]
    print("\n1. Design point (clean filter)")
    for label, value, note in rows:
        print(f"  {label:<17}{value:<15}{note}".rstrip())
    print(f"  warnings: {names(r)}")
    return r


def clogging_run(s: wp.System) -> wp.SimulationResult:
    """Section 2: ramp the filter clogging over a filter run and watch the plant respond."""
    step = 1800.0  # s
    n = round(RUN_HOURS * 3600 / step)
    events = [
        {"at": k * step, "set": {"filter.clogging": FINAL_CLOGGING * k / n}}
        for k in range(1, n + 1)
    ]
    sim = s.simulate(duration=f"{RUN_HOURS} h", step=step, events=events, restore=True)
    print(f"\n2. Filter run: clogging ramps 0 -> {FINAL_CLOGGING} over {RUN_HOURS} h")
    print("  time h  clogging  flow m3/h  filter dp bar  UV dose mJ/cm2  pump eff %  raw level m")
    for k, t in enumerate(sim.time):
        if t % (6 * 3600) == 0:
            print(
                f"  {t / 3600:6.0f}  {FINAL_CLOGGING * t / (RUN_HOURS * 3600):8.3f}"
                f"  {sim['pump.volume_flow'][k]:9.1f}  {sim['filter.pressure_drop'][k]:13.2f}"
                f"  {sim['uv.dose'][k]:14.1f}  {sim['pump.efficiency'][k]:10.1f}"
                f"  {sim['raw.level'][k]:11.2f}"
            )
    for w in sim.warnings:
        end = "still active at the end" if w.active_at_end else "cleared"
        print(
            f"  {w.path} ({w.severity}): first at {w.time / 3600:.1f} h, last at "
            f"{w.last_time / 3600:.1f} h, {end}"
        )
    print("  the UV dose rises as the flow falls: clogging costs throughput and efficiency")
    return sim


def diagnose(s: wp.System, base: wp.SolveResult) -> list[dict[str, Any]]:
    """Section 3: which single fault explains a 20 % flow drop with a doubled filter dp?

    Each hypothesis is sized (goal seek) to reproduce the observed flow; the hypotheses are
    then ranked by how well they predict the second measurement, the filter pressure drop.
    """
    observed_flow = 0.8 * base["pump.volume_flow"]
    observed_ratio = 2.0  # filter dp now / filter dp at the design point
    print(f"\n3. Diagnosis: flow fell 20 % to {observed_flow:.1f} m3/h, filter dp doubled")
    hypotheses = [
        ("(a) clogged filter", "filter.clogging", 0.0, 0.99),
        ("(b) valve closed further", "valve.opening", 0.05, s.get("valve.opening")),
        ("(c) worn pump (lower speed)", "pump.speed", 0.3, 1.0),
    ]
    ranked = []
    for label, vary, lower, upper in hypotheses:
        before = s.get(vary)
        x, r = goal_seek(s, "pump.volume_flow", observed_flow, vary, lower, upper)
        ratio = r["filter.pressure_drop"] / base["filter.pressure_drop"]
        miss = abs(math.log(ratio / observed_ratio))
        ranked.append({"label": label, "vary": vary, "value": x, "ratio": ratio, "miss": miss})
        s.set(vary, before)
    ranked.sort(key=lambda h: h["miss"])
    print("  hypothesis (sized to the flow)  fault                     filter dp ratio")
    for h in ranked:
        fault = f"{h['vary']} = {h['value']:.3f}"
        print(f"  {h['label']:<30}  {fault:<24}  {h['ratio']:>14.2f}x")
    best = ranked[0]
    others = " and ".join(f"{h['ratio']:.2f}x" for h in ranked[1:])
    print(f"  best explanation: {best['label']} ({best['ratio']:.2f}x; the others give {others})")
    if best["vary"] == "filter.clogging":
        hours = best["value"] / FINAL_CLOGGING * RUN_HOURS
        print(f"  (clogging {best['value']:.2f} is reached after about {hours:.0f} h of the run)")
    return ranked


def restore_dose(s: wp.System) -> tuple[float, wp.SolveResult]:
    """Section 4: goal seek the valve opening that restores the UV dose with an aged lamp."""
    s.set("uv.lamp_output", LAMP_OUTPUT)
    r = s.solve()
    target = DOSE_MARGIN * s.get("uv.required_dose")
    print(f"\n4. UV lamp aged to {LAMP_OUTPUT:.0%} output")
    print(
        f"  at valve opening {s.get('valve.opening'):.2f}: dose {r['uv.dose']:.1f} mJ/cm2, "
        f"flow {r['pump.volume_flow']:.1f} m3/h, warnings: {names(r)}"
    )
    x, r = goal_seek(s, "uv.dose", target, "valve.opening", 0.05, 1.0)
    print(f"  valve opening for {target:.0f} mJ/cm2 (required + 5 %): {x:.3f}")
    print(
        f"  -> dose {r['uv.dose']:.1f} mJ/cm2, flow {r['pump.volume_flow']:.1f} m3/h, "
        f"specific energy {r['pump.specific_energy']:.3f} kWh/m3, warnings: {names(r)}"
    )
    return x, r


def main() -> dict[str, Any]:
    """Run the four studies on the purification skid and return their results."""
    s = load_system()
    print(f"{s.name}: {' -> '.join(s.components)}")
    design = design_point(s)
    run = clogging_run(s)
    ranking = diagnose(s, design)
    opening, restored = restore_dose(s)
    return {
        "design": design,
        "run": run,
        "ranking": ranking,
        "opening": opening,
        "restored": restored,
    }


if __name__ == "__main__":
    main()
