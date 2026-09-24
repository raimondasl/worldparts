"""Adversarial review of control loops (design 13.1) and ramp events (13.2).

Every test here demonstrates a defect found in review; each fails against the
implementation it was written for.
"""

from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from mcp import Client

import worldparts as wp
from worldparts import cli
from worldparts.mcp_server import create_server

CURVE = [[0, 30], [10, 27.5], [20, 20], [30, 7.5]]


# ----------------------------------------------------------------------------------------
# sampled-data timing: a command issued at t must act over the next step like an event
# ----------------------------------------------------------------------------------------
def tower(control: bool = True) -> wp.System:
    """Pump -> check valve -> Kv 2 valve -> 0.3 m tower tank -> Kv 3 valve -> drain."""
    s = wp.System("tower")
    s.add("src", "supply", pressure=0)
    s.add("pump", "centrifugal_pump", head_curve=CURVE, speed=1.0)
    s.add("cv", "check_valve", kv=100)
    s.add("fill", "valve", kv=2)
    s.add("tower", "tank", diameter=0.3, height=3, initial_level=1.5)
    s.add("use", "valve", kv=3)
    s.add("out", "drain")
    for a, b in [("src.port", "pump.inlet"), ("pump.outlet", "cv.port_a"),
                 ("cv.port_b", "fill.port_a"), ("fill.port_b", "tower.inlet"),
                 ("tower.outlet", "use.port_a"), ("use.port_b", "out.port")]:  # fmt: skip
        s.connect(a, b)
    if control:
        s.add_control("switch", "hysteresis", measure="tower.level", actuate="pump.speed",
                      on_below=1.0, off_above=2.0, on_value=1, off_value=0,
                      initial="on")  # fmt: skip
    return s


@pytest.mark.parametrize("step", [1.0, 10.0])
def test_control_command_acts_over_the_next_step_exactly_like_an_event(step: float) -> None:
    """Design 13.1: the output computed at t "is applied as an input change at t, taking
    effect over the next step exactly like an event". Replaying the switch commands as set
    events at the same times must therefore give the same tank trajectory. It does not:
    the storage integration over [t, t + h] uses the solution computed before the command,
    so the command reaches the tank one step later than an event would, and the overshoot
    beyond the band grows with the step (2.146 m against 2.070 m at h = 10 s)."""
    sim = tower().simulate("10 min", f"{step} s")
    out = sim["control.switch.output"]
    switches = [(sim.time[i], out[i]) for i in range(1, len(out)) if out[i] != out[i - 1]]
    assert len(switches) >= 2
    events = [{"at": t, "set": {"pump.speed": v}} for t, v in switches]
    replay = tower(control=False).simulate("10 min", f"{step} s", events=events)
    assert sim["tower.level"] == pytest.approx(replay["tower.level"], rel=1e-9, abs=1e-12)


# ----------------------------------------------------------------------------------------
# steady solve: undefined measures and failed solves
# ----------------------------------------------------------------------------------------
def shower(lift: float) -> wp.System:
    """A mixer with a thermostatic PI loop on its mix lever."""
    s = wp.System("shower")
    s.add("cold", "supply", pressure=3, temperature=12)
    s.add("hot", "supply", pressure=3, temperature=60)
    s.add("f", "mixing_faucet", lift=lift, mix=0.5)
    s.connect("cold.port", "f.cold")
    s.connect("hot.port", "f.hot")
    s.add_control("thermo", "pi", measure="f.temperature", setpoint="38 degC",
                  actuate="f.mix", gain=0.01, integral_time=2)  # fmt: skip
    return s


def test_failed_steady_solve_leaves_the_system_unchanged() -> None:
    """solve() that fails inside the PI goal seek must not leave the actuator at a trial
    value. With the faucet closed, the goal seek writes f.mix = 0 (output_min), then
    raises; the system keeps mix = 0 and the document records it as an explicit input."""
    s = shower(lift=0.0)
    before = s.to_dict()
    with contextlib.suppress(wp.WorldpartsError):
        s.solve()
    assert s.get("f.mix") == 0.5
    assert s.to_dict() == before


def test_steady_solve_with_an_undefined_measure_holds_the_output() -> None:
    """A thermostatic loop on a closed faucet: the outlet temperature is undefined (None,
    design 3.2). A simulation holds the PI output while the measure is None; the steady
    solve instead raises "Narrow output_min/output_max to where it is defined", which no
    limit can satisfy, so any system containing a closed controlled faucet cannot be
    solved at all."""
    s = shower(lift=0.0)
    r = s.solve()
    assert r["f.temperature"] is None
    c = r.controls["thermo"]
    assert c.measured is None and c.error is None and not c.saturated
    assert c.output == 0.5  # held, as in a simulation


def test_level_loop_steady_solve_does_not_report_a_draining_tank_as_settled() -> None:
    """A PI loop on a tank level: solve() holds the level (steady: hold), so the measure
    does not depend on the actuator. With the level at the setpoint, the goal seek sees
    zero error at output_min and returns it: the pump is stopped, the tank drains at
    about 1.15 m3/h, and the result reports a satisfied loop with no warning. Either the
    loop should settle the storage (net inflow 0) or the result should say the measure
    does not respond to the actuator."""
    s = tower(control=False)
    s.set("tower.diameter", 1.0)
    s.add_control("lvl", "pi", measure="tower.level", setpoint=1.5, actuate="pump.speed",
                  gain=0.5, integral_time="60 s", output_min=0, output_max=1.2)  # fmt: skip
    r = s.solve()
    warned = any(w.component == "control.lvl" for w in r.warnings)
    assert warned or abs(r["tower.net_inflow"]) < 1e-3, r.controls["lvl"]


# ----------------------------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------------------------
def booster() -> wp.System:
    s = wp.System("booster")
    s.add("mains", "supply", pressure=0.5)
    s.add("pump", "centrifugal_pump", head_curve=CURVE, speed=0.5)
    s.add("v", "valve", kv=10)
    s.add("out", "drain")
    s.connect("mains.port", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


PI: dict[str, Any] = {"measure": "pump.outlet.p", "actuate": "pump.speed", "gain": 0.1,
                      "integral_time": 1, "output_min": 0.3, "output_max": 1.2}  # fmt: skip


def test_infinite_setpoint_is_an_invalid_control() -> None:
    """setpoint=inf is accepted; solve() then drives the pump to 1.2 with error = inf and
    reports saturated=False and no control_saturated warning (the error tolerance is
    1e-6 * |setpoint| = inf), and to_dict() writes a non-finite number that is not JSON."""
    s = booster()
    with pytest.raises(wp.InvalidControlError):
        s.add_control("c", "pi", setpoint=float("inf"), **PI)


def test_state_field_on_a_pi_control_is_reported() -> None:
    """'state' belongs to hysteresis controls. On a PI control it is silently dropped by
    Control.from_document (popped before validation), while every other foreign field,
    such as 'initial', is reported as invalid_control."""
    s = booster()
    with pytest.raises(wp.InvalidControlError, match="state"):
        s.add_control("c", "pi", setpoint=3, state="on", **PI)


# ----------------------------------------------------------------------------------------
# MCP and CLI surfaces
# ----------------------------------------------------------------------------------------
BOOSTER_DOC: dict[str, Any] = {
    "worldparts_system": "0.1",
    "name": "booster",
    "components": [
        {"name": "mains", "type": "supply", "parameters": {"pressure": 0.5}},
        {"name": "pump", "type": "centrifugal_pump", "parameters": {"head_curve": CURVE},
         "inputs": {"speed": 0.5}},
        {"name": "v", "type": "valve", "parameters": {"kv": 10}},
        {"name": "out", "type": "drain"},
    ],
    "connections": [["mains.port", "pump.inlet"], ["pump.outlet", "v.port_a"],
                    ["v.port_b", "out.port"]],
    "controls": [
        {"name": "duty", "type": "pi", "measure": "pump.outlet.p", "setpoint": "3 bar",
         "actuate": "pump.speed", "gain": 0.1, "integral_time": "1 s", "output_min": 0.3,
         "output_max": 1.2},
    ],
}  # fmt: skip


def test_failed_solve_for_leaves_controlled_inputs_unchanged() -> None:
    """solve_for promises that an unreachable target "reports the target at both bounds
    and leaves the system unchanged" (design 9). With a PI loop present it restores only
    `vary`: every trial solve moved the PI actuator, so pump.speed ends at the value of
    the last trial (1.2 at kv = 20) instead of where it was."""

    async def main() -> None:
        async with Client(create_server()) as client:  # type: ignore[arg-type]
            loaded = await client.call_tool("load_system", {"document": BOOSTER_DOC})
            sid = loaded.structured_content["system_id"]  # type: ignore[index]
            before = await client.call_tool("get_system", {"system_id": sid})
            failed = await client.call_tool(
                "solve_for",
                {"system_id": sid, "target": "pump.volume_flow", "value": 500,
                 "vary": "v.kv", "lower": 5, "upper": 20},
            )  # fmt: skip
            assert failed.is_error
            after = await client.call_tool("get_system", {"system_id": sid})
            assert after.structured_content == before.structured_content

    anyio.run(main)


def test_cli_simulate_json_with_var_keeps_control_series(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Design 13.1: control output and measure series "are recorded in simulations"; the
    library always records them, the MCP simulate always returns them and the CLI text
    table lists them, but `worldparts simulate --json --var PATH` drops them from the
    series and summary."""
    doc = {**BOOSTER_DOC, "simulation": {"duration": "10 s", "step": "1 s"}}
    path = tmp_path / "booster.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    code = cli.main(["simulate", str(path), "--json", "--var", "pump.volume_flow"])
    out, _ = capsys.readouterr()
    assert code == 0
    data = json.loads(out)
    assert "control.duty.output" in data["series"]
    assert "control.duty.measure" in data["series"]
    assert math.isfinite(data["summary"]["control.duty.output"]["final"])
