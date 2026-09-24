"""Pump wear from field readings: can the sensors tell, how worn is it, and how sure are we?

The rooftop lift of ``pump_lift.yaml`` has run for some years. ``pump_wear_readings.yaml``
holds a survey at four positions of the discharge valve: outlet pressure, flow and shaft
power, each with its instrument uncertainty. The script:

1. asks, before any data exists, which wear the planned sensors can determine
   (``wp.identifiability``): a pressure gauge and a flow meter see head wear but not
   efficiency wear, and the analysis names the extra sensor that would (a power reading);
2. calibrates head and efficiency wear to the survey (``wp.calibrate``) and prints each
   estimate with its standard error and verdict, the reduced chi-square and the worst
   residual;
3. fits the same survey without the power readings: efficiency wear is then reported as
   not identifiable, not as a number;
4. applies the fit and compares the worn pump's energy per m3 with the new pump's.

Run it with ``uv run python examples/calibrate_pump_wear.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import worldparts as wp

HERE = Path(__file__).parent
WEAR = {"pump.wear_head": [0, 0.5], "pump.wear_efficiency": [0, 0.5]}
OPENINGS = [{"valve.opening": y} for y in (1.0, 0.15, 0.1, 0.07)]


def load_system() -> wp.System:
    """The rooftop lift from its system document."""
    doc = yaml.safe_load((HERE / "pump_lift.yaml").read_text(encoding="utf-8"))
    return wp.System.from_dict(doc)


def show_fit(res: wp.CalibrationResult) -> None:
    """Estimates with standard errors and verdicts, and the goodness of fit."""
    for path, e in res.parameters.items():
        se = "no standard error" if e.standard_error is None else f"+/- {e.standard_error:.3f}"
        print(f"  {path:<22} {e.value:6.3f} {se:<18} {e.verdict}")
    worst = max(res.residuals, key=lambda r: abs(r.normalised or 0.0))
    print(
        f"  reduced chi-square {res.reduced_chi_square:.2f} (p = {res.p_value:.2f}); "
        f"largest residual {worst.normalised:+.1f} sigma ({worst.path} at '{worst.point}')"
    )


def main() -> dict[str, Any]:
    """Run the four steps and return their results."""
    s = load_system()

    # 1. Before any data: what can a pressure gauge and a flow meter determine?
    plan = wp.identifiability(s, ["pump.outlet.p", "pump.volume_flow"], WEAR, points=OPENINGS)
    print("1. Identifiability with outlet pressure and flow at four valve openings")
    for path, e in plan.parameters.items():
        print(f"  {path:<22} {e.verdict}: {e.reason}")
    rec = plan.recommendation
    assert rec is not None
    print(
        f"  best extra sensor: {rec.sensor} ({rec.sensor_unit}), {rec.parameter} then "
        f"{rec.verdict} with a standard error of {rec.standard_error:.3f}"
    )

    # 2. The survey, with a power reading at every point.
    survey = wp.load_measurements(HERE / "pump_wear_readings.yaml")
    fit = wp.calibrate(s, survey, WEAR)
    print(f"\n2. Calibration to '{survey.name}' ({survey.n_values} readings)")
    show_fit(fit)

    # 3. The same survey without the power readings.
    no_power = survey.to_dict()
    for point in no_power["points"]:
        del point["measured"]["pump.shaft_power"]
    partial = wp.calibrate(s, no_power, WEAR)
    print("\n3. Without the power readings")
    show_fit(partial)
    print(f"  {partial.notes[-1]}")

    # 4. What the wear costs: energy per m3 at full opening, new against worn.
    new = s.solve()
    wp.calibrate(s, survey, WEAR, apply=True)
    worn = s.solve()
    extra = worn["pump.specific_energy"] / new["pump.specific_energy"] - 1
    print("\n4. Valve open: new pump against the fitted, worn pump")
    for label, r in (("new", new), ("worn", worn)):
        print(
            f"  {label:<5} {r['pump.volume_flow']:5.2f} m3/h at {r['pump.head']:5.2f} m, "
            f"{r['pump.efficiency']:4.1f} % efficient, {r['pump.specific_energy']:.4f} kWh/m3"
        )
    print(
        f"  the worn pump delivers {1 - worn['pump.volume_flow'] / new['pump.volume_flow']:.0%}"
        f" less water and needs {extra:.0%} more energy per m3"
    )
    return {"plan": plan, "fit": fit, "partial": partial, "new": new, "worn": worn}


if __name__ == "__main__":
    main()
