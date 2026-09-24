"""Control loops (design section 13.1) and ramp events in the core (13.2).

Every number is checked against a hand calculation. The pump in these tests has an exactly
quadratic curve, ``H = 30 - 0.025 Q**2`` (m, Q in m3/h), so the fitted affinity law is
``H = 30 s**2 - 0.025 Q**2`` and the speed for a given head and flow is
``s = sqrt((H + 0.025 Q**2) / 30)``. Kv relations: ``dp = (rho / 1000) (Q / Kv)**2`` bar, a
head of ``100 / g (Q / Kv)**2`` m.
"""

from __future__ import annotations

import copy
import json
import math
from importlib import resources
from itertools import pairwise
from typing import Any

import jsonschema
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq

import worldparts as wp
import worldparts.system
from worldparts.components.pump import LAW_EPS
from worldparts.media import RHO, G

CURVE = [[0, 30], [10, 27.5], [20, 20], [30, 7.5]]
A_CURVE, C_CURVE = 30.0, -0.025  # m, m/(m3/h)**2
SG = RHO / 1000
K_HEAD = 100 / G  # m of head per (Q / Kv)**2 with Q in m3/h
PI = {"gain": 0.1, "integral_time": "1 s", "output_min": 0.3, "output_max": 1.2}


def speed_for(head: float, q_h: float) -> float:
    """Pump speed that gives ``head`` (m) at ``q_h`` (m3/h) on the test curve."""
    return math.sqrt((head - C_CURVE * q_h**2) / A_CURVE)


def pump_head(p_out: float, p_in: float, q_h: float) -> float:
    """Pump head (m) between gauge pressures in bar, with the law's tiny linear term."""
    m = RHO * q_h / 3600
    return ((p_out - p_in) * 1e5 + LAW_EPS * m) / (RHO * G)


def booster(kv: float = 10.0, speed: float = 0.5, **control: Any) -> wp.System:
    """mains (0.5 bar) -> pump -> valve (kv) -> drain, pump speed on a PI pressure loop."""
    s = wp.System("booster")
    s.add("mains", "supply", pressure=0.5)
    s.add("pump", "centrifugal_pump", head_curve=CURVE, speed=speed)
    s.add("v", "valve", kv=kv)
    s.add("out", "drain")
    s.connect("mains.port", "pump.inlet")
    s.connect("pump.outlet", "v.port_a")
    s.connect("v.port_b", "out.port")
    fields = {"setpoint": "3 bar", **PI, **control}
    s.add_control("duty", "pi", measure="pump.outlet.p", actuate="pump.speed", **fields)
    return s


# ----------------------------------------------------------------------------------------
# PI: steady solve
# ----------------------------------------------------------------------------------------
def test_pi_steady_solve_reaches_the_setpoint_by_hand() -> None:
    s = booster()
    r = s.solve()
    # The valve discharges to atmosphere, so its drop is the 3 bar setpoint.
    q = 10 * math.sqrt(3 / SG)  # 17.34 m3/h
    assert r["pump.outlet.p"] == pytest.approx(3.0, abs=1e-8)
    assert r["pump.volume_flow"] == pytest.approx(q, rel=1e-8)
    speed = speed_for(pump_head(3.0, 0.5, q), q)
    assert speed == pytest.approx(1.04965, abs=1e-5)
    assert r["pump.speed"] == pytest.approx(speed, rel=1e-8)
    c = r.controls["duty"]
    assert (c.type, c.measure, c.actuate) == ("pi", "pump.outlet.p", "pump.speed")
    assert c.output == pytest.approx(speed, rel=1e-8) and c.setpoint == 3.0
    assert abs(c.error) < 1e-8 and not c.saturated
    assert r["control.duty.output"] == c.output and r["control.duty.measure"] == c.measured
    assert r.units["control.duty.measure"] == "bar"
    assert r.references["control.duty.measure"] == "gauge"
    assert not any(w.component.startswith("control.") for w in r.warnings)
    # The actuator is left at the value found (like solve_for).
    assert s.get("pump.speed") == pytest.approx(speed, rel=1e-8)
    assert r.to_dict()["controls"]["duty"]["saturated"] is False


def test_pi_steady_solve_matches_an_independent_goal_seek() -> None:
    """Brent's method on the speed of the same system without the control."""
    plain = booster()
    plain.remove_control("duty")

    def excess(speed: float) -> float:
        plain.set("pump.speed", speed)
        return plain.solve()["pump.outlet.p"] - 3.0

    root = brentq(excess, 0.3, 1.2, xtol=1e-13)
    assert booster().solve()["pump.speed"] == pytest.approx(root, rel=1e-9)


def test_pi_unreachable_setpoint_saturates_at_the_limit() -> None:
    """kv 30 at full speed 1.2: p = 0.5 + (43.2 - 0.025 Q^2) rho g / 1e5 with
    Q = 30 sqrt(p / sg), about 1.47 bar, below the 3 bar setpoint."""
    s = booster(kv=30)
    r = s.solve()
    assert r["pump.speed"] == 1.2
    p = r["pump.outlet.p"]
    q = 30 * math.sqrt(p / SG)
    assert r["pump.volume_flow"] == pytest.approx(q, rel=1e-8)
    assert pump_head(p, 0.5, q) == pytest.approx(A_CURVE * 1.44 + C_CURVE * q * q, rel=1e-8)
    assert p == pytest.approx(1.4747, abs=1e-4)
    c = r.controls["duty"]
    assert c.saturated and c.error == pytest.approx(3 - p)
    sat = [w for w in r.warnings if w.path == "control.duty.control_saturated"]
    assert len(sat) == 1 and sat[0].severity == "warning" and "pump.speed = 1.2" in sat[0].message
    # Mains above the setpoint: even the lowest speed overshoots, so the loop winds down to
    # its lower limit instead.
    low = booster()
    low.set("mains.pressure", 3.5)
    r = low.solve()
    assert r["pump.speed"] == 0.3 and r.controls["duty"].saturated
    assert r["pump.outlet.p"] > 3


def test_direct_action_and_wrong_direction() -> None:
    """A 'direct' loop on this plant acts backwards: solve still finds the setpoint but
    warns that a real loop would run away to a limit."""
    r = booster(direction="direct").solve()
    assert r["pump.outlet.p"] == pytest.approx(3.0, abs=1e-8)
    assert [w.code for w in r.warnings if w.component == "control.duty"] == ["control_direction"]
    assert r.controls["duty"].error == pytest.approx(0.0, abs=1e-8)


def test_two_interacting_pi_loops_converge_in_steady_solve() -> None:
    """Pump speed holds the header at 3 bar; a valve holds its branch at 100 L/min.

    Each actuator moves the other loop's measure (the valve changes the pump flow, the
    speed changes the valve's pressure drop), so the loops are iterated until they agree.
    By hand at 3 bar: branch B (Kv 5, open) passes 5 sqrt(3 / sg); branch A passes 6 m3/h,
    so its effective Kv is 6 / sqrt(3 / sg) and its linear opening (phi - l) / (1 - l).
    """
    s = wp.System("two loops")
    s.add("mains", "supply", pressure=0.5)
    s.add("pump", "centrifugal_pump", head_curve=CURVE, speed=0.8)
    s.add("a", "valve", kv=10, opening=0.5)
    s.add("b", "valve", kv=5)
    s.add("out_a", "drain")
    s.add("out_b", "drain")
    for x, y in [("mains.port", "pump.inlet"), ("pump.outlet", "a.port_a"),
                 ("pump.outlet", "b.port_a"), ("a.port_b", "out_a.port"),
                 ("b.port_b", "out_b.port")]:  # fmt: skip
        s.connect(x, y)
    s.add_control("header", "pi", measure="pump.outlet.p", setpoint=3, actuate="pump.speed",
                  **PI)  # fmt: skip
    s.add_control("branch", "pi", measure="a.volume_flow", setpoint="6 m3/h",
                  actuate="a.opening", gain=0.002, integral_time="2 s")  # fmt: skip
    r = s.solve()
    q_b = 5 * math.sqrt(3 / SG)
    phi = 6 / math.sqrt(3 / SG) / 10
    opening = (phi - 1e-4) / (1 - 1e-4)
    q = 6 + q_b
    assert r["pump.outlet.p"] == pytest.approx(3, abs=1e-6)
    assert r["a.volume_flow"] == pytest.approx(100, abs=1e-5)  # L/min
    assert r["b.volume_flow"] == pytest.approx(q_b * 1000 / 60, rel=1e-6)
    assert r["a.opening"] == pytest.approx(opening, rel=1e-5)
    assert r["pump.speed"] == pytest.approx(speed_for(pump_head(3, 0.5, q), q), rel=1e-6)
    assert not any(w.component.startswith("control.") for w in r.warnings)
    assert r.controls["branch"].setpoint == pytest.approx(100)  # '6 m3/h' in L/min
    # The same loops simulated as sampled controllers settle at the same point.
    sim = s.simulate("300 s", "1 s", restore=True)
    assert sim.final["pump.outlet.p"] == pytest.approx(3, abs=1e-4)
    assert sim.final["a.volume_flow"] == pytest.approx(100, abs=1e-2)


def test_steady_loops_that_do_not_settle_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    s = booster()
    s.add("v2", "valve", kv=5)
    s.add("out2", "drain")
    s.connect("pump.outlet", "v2.port_a")
    s.connect("v2.port_b", "out2.port")
    s.set("v.opening", 0.5)
    s.add_control("flow", "pi", measure="v.volume_flow", setpoint=100, actuate="v.opening",
                  gain=0.002, integral_time="2 s")  # fmt: skip
    monkeypatch.setattr(worldparts.system, "MAX_ROUNDS", 1)
    r = s.solve()
    codes = {(w.component, w.code) for w in r.warnings}
    assert ("control.duty", "control_not_converged") in codes
    assert ("control.flow", "control_not_converged") in codes


# ----------------------------------------------------------------------------------------
# PI: simulation
# ----------------------------------------------------------------------------------------
def test_pi_simulation_follows_the_incremental_form_and_converges() -> None:
    s = booster(speed=0.5)
    steady = booster().solve()["pump.speed"]
    sim = s.simulate("60 s", "1 s")
    u, m = sim["control.duty.output"], sim["control.duty.measure"]
    speed = sim["pump.speed"]
    # t = 0: bumpless start (no previous error or time), so the output stays at 0.5.
    assert u[0] == 0.5
    # The command issued at t is applied at t like an event, so the sample recorded at t
    # (solved again after the command) and the next step both use it.
    assert speed == pytest.approx(u, rel=1e-12)
    # By hand: u1 = u0 + K (e1 - e0) + K dt / Ti e1 with e = 3 - m.
    e = [3 - x for x in m]
    assert u[1] == pytest.approx(0.5 + 0.1 * (e[1] - e[0]) + 0.1 * e[1], rel=1e-12)
    assert u[2] == pytest.approx(u[1] + 0.1 * (e[2] - e[1]) + 0.1 * e[2], rel=1e-12)
    # Converges to the steady goal seek without overshooting the setpoint or the limits.
    assert max(m) <= 3 + 1e-6
    assert all(0.3 <= x <= 1.2 for x in u)
    assert all(b >= a - 1e-12 for a, b in pairwise(u))  # monotone rise
    assert m[-1] == pytest.approx(3, abs=1e-6)
    assert u[-1] == pytest.approx(steady, rel=1e-6)
    assert not any(w.component == "control.duty" for w in sim.warnings)
    assert sim.controls["duty"].output == u[-1] and not sim.controls["duty"].saturated
    # The command at the end is applied, so a new run continues from it.
    assert s.get("pump.speed") == u[-1]


def test_pi_anti_windup_recovers_immediately() -> None:
    """Unreachable for 60 s (the loop sits at 1.2), then the demand valve closes to 20 %
    and the setpoint becomes reachable: the output leaves the limit at the first sample."""
    s = booster(kv=30, speed=1.0)
    sim = s.simulate("120 s", "1 s", events=[{"at": "60 s", "set": {"v.opening": 0.2}}])
    u, m = sim["control.duty.output"], sim["control.duty.measure"]
    i = sim.time.index(60.0)
    assert u[10:i] == [1.2] * (i - 10)  # saturated before the change
    assert m[i - 1] == pytest.approx(1.4747, abs=1e-4)
    # At 60 s the valve is at 20 %: 4.35 bar at full speed, so the error is negative and
    # the incremental form lowers the output at once (a wound-up integral would hold it).
    assert m[i] == pytest.approx(4.345, abs=1e-3)
    assert u[i] == pytest.approx(1.2 + 0.1 * (m[i - 1] - m[i]) + 0.1 * (3 - m[i]), rel=1e-12)
    assert u[i] < 1.2
    sat = next(w for w in sim.warnings if w.code == "control_saturated")
    assert sat.component == "control.duty" and sat.time <= 10
    assert sat.last_time == 59 and sat.active_at_end is False
    assert m[-1] == pytest.approx(3, abs=1e-4)


def test_simulation_records_control_series_with_variables_and_restore() -> None:
    s = booster(speed=0.5)
    sim = s.simulate("5 s", "1 s", variables=["pump.volume_flow"], restore=True)
    assert list(sim.series) == [
        "pump.volume_flow", "control.duty.output", "control.duty.measure"
    ]  # fmt: skip
    assert sim.units["control.duty.output"] == "1"
    assert sim.references["control.duty.measure"] == "gauge"
    assert s.get("pump.speed") == 0.5  # restored
    d = sim.to_dict()
    assert d["controls"]["duty"]["type"] == "pi"


def test_events_cannot_write_a_controlled_input() -> None:
    s = booster()
    with pytest.raises(wp.InvalidValueError, match="written by control 'duty'"):
        s.simulate("10 s", events=[{"at": 5, "set": {"pump.speed": 1}}])
    with pytest.raises(wp.InvalidValueError, match="written by control 'duty'"):
        s.simulate("10 s", events=[{"at": 0, "ramp": {"pump.speed": [0.5, 1]}, "over": 5}])


# ----------------------------------------------------------------------------------------
# hysteresis
# ----------------------------------------------------------------------------------------
def tower(diameter: float = 1.0, **control: Any) -> wp.System:
    """A pump fills a tower tank through a check valve and a Kv 2 valve; a Kv 3 valve
    drains it continuously. A level switch runs the pump between 1 m and 2 m."""
    s = wp.System("tower")
    s.add("src", "supply", pressure=0)
    s.add("pump", "centrifugal_pump", head_curve=CURVE)
    s.add("cv", "check_valve", kv=100)
    s.add("fill", "valve", kv=2)
    s.add("tower", "tank", diameter=diameter, height=3, initial_level=1.5)
    s.add("use", "valve", kv=3)
    s.add("out", "drain")
    for a, b in [("src.port", "pump.inlet"), ("pump.outlet", "cv.port_a"),
                 ("cv.port_b", "fill.port_a"), ("fill.port_b", "tower.inlet"),
                 ("tower.outlet", "use.port_a"), ("use.port_b", "out.port")]:  # fmt: skip
        s.connect(a, b)
    s.add_control("switch", "hysteresis", measure="tower.level", actuate="pump.speed",
                  on_below=1.0, off_above=2.0, on_value=1, off_value=0, initial="on",
                  **control)  # fmt: skip
    return s


def q_in(level: float) -> float:
    """Pump delivery (m3/h) into the tower at ``level``: 30 - 0.025 Q^2 = level + losses
    in the check valve (Kv 100), the fill valve (Kv 2) and the tank port (Kv 200)."""
    return math.sqrt((30 - level) / (-C_CURVE + K_HEAD * (1 / 100**2 + 1 / 2**2 + 1 / 200**2)))


def q_out(level: float) -> float:
    """Draw-off (m3/h) through the tank port (Kv 200) and the Kv 3 valve to atmosphere."""
    return math.sqrt(level / (K_HEAD * (1 / 3**2 + 1 / 200**2)))


def switch_times(sim: wp.SimulationResult) -> list[tuple[float, float]]:
    out = sim["control.switch.output"]
    return [(sim.time[i], out[i]) for i in range(1, len(out)) if out[i] != out[i - 1]]


def test_level_switch_cycle_times_by_hand() -> None:
    """Fill and drain times from the tank area and the net flows, plus the sampling delay.

    By hand: F = int_1^2 A dh / (q_in - q_out) and D = int_1^2 A dh / q_out. A switch
    command is issued at the first sample past a threshold (a delay d in [0, h)) and acts
    over the next step like an event at that time (design 13.1), so the level overshoots
    by d times the rate there, which takes d times the rate ratio to undo. Between switch
    commands: drain = d1 r_fill(2) / r_drain(2) + D + d2 and
    fill = d2 r_drain(1) / r_fill(1) + F + d3.
    """
    h = 5.0
    area = math.pi / 4
    fill = quad(lambda x: 3600 * area / (q_in(x) - q_out(x)), 1, 2)[0]
    drain = quad(lambda x: 3600 * area / q_out(x), 1, 2)[0]
    assert fill == pytest.approx(1300.7, abs=0.1) and drain == pytest.approx(2493.5, abs=0.1)
    s = tower()
    r = s.solve()  # the switch holds its state: on, so the pump runs at speed 1
    assert r["pump.speed"] == 1 and r.controls["switch"].state == "on"
    assert r["fill.volume_flow"] * 60 / 1000 == pytest.approx(q_in(1.5), rel=1e-5)
    assert r["use.volume_flow"] * 60 / 1000 == pytest.approx(q_out(1.5), rel=1e-5)
    sim = s.simulate("4 h", f"{h} s")
    changes = switch_times(sim)
    assert [v for _, v in changes] == [0, 1, 0, 1, 0, 1, 0]
    up = (q_in(2) - q_out(2)) / q_out(2)
    down = q_out(1) / (q_in(1) - q_out(1))
    for (t0, v), (t1, _) in pairwise(changes):
        if v == 0:  # drain phase
            lo, hi = drain, h * up + drain + h
        else:
            lo, hi = fill, h * down + fill + h
        assert lo - 1 <= t1 - t0 <= hi + 1, (t0, v, t1 - t0, lo, hi)
    # The switch never leaves the band by more than the delay allows.
    level = sim["tower.level"]
    assert 1 - h * q_out(1) / 3600 / area < min(level[10:])
    assert max(level) < 2 + h * (q_in(2) - q_out(2)) / 3600 / area
    report = sim.controls["switch"]
    assert report.switches == 7 and report.state == "off" and report.output == 0
    assert not any(w.code == "short_cycling" for w in sim.warnings)  # about 2 per hour


def test_short_cycling_fires_for_an_undersized_tank() -> None:
    """A 0.3 m tower holds 0.071 m3 between the thresholds: about 2 min to fill and 4 min
    to drain, 20 switches an hour against the default limit of 6."""
    s = tower(diameter=0.3)
    sim = s.simulate("30 min", "1 s")
    times = [t for t, _ in switch_times(sim)]
    assert len(times) == 11
    cycling = [w for w in sim.warnings if w.code == "short_cycling"]
    assert len(cycling) == 1
    w = cycling[0]
    assert w.component == "control.switch" and w.severity == "warning"
    assert w.time == times[6]  # the seventh switch within an hour
    assert w.active_at_end is True and "7 switches" in w.message
    # A higher limit silences it.
    calm = tower(diameter=0.3, max_switches_per_hour=30).simulate("30 min", "1 s")
    assert not any(x.code == "short_cycling" for x in calm.warnings)


def test_hysteresis_state_persists_and_restores() -> None:
    s = tower(diameter=0.3)
    s.simulate("70 s", "1 s", restore=True)
    assert s.controls["switch"].state == "on"
    s.simulate("70 s", "1 s")  # the level passes 2 m at about 62 s
    assert s.controls["switch"].state == "off"
    assert s.get("pump.speed") == 0
    doc = s.to_dict()
    entry = doc["controls"][0]
    assert entry["state"] == "off" and entry["initial"] == "on"
    again = wp.System.from_dict(doc)
    assert again.controls["switch"].state == "off"
    assert again.solve()["pump.speed"] == 0  # holds the off state
    again.reset_states()
    assert again.controls["switch"].state == "on"


# ----------------------------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------------------------
def base() -> wp.System:
    s = booster()
    s.remove_control("duty")
    return s


PI_OK = {"measure": "pump.outlet.p", "actuate": "pump.speed", "setpoint": 3, "gain": 0.1,
         "integral_time": 1}  # fmt: skip
HYST_OK = {"measure": "pump.outlet.p", "actuate": "v.opening", "on_below": 1,
           "off_above": 2, "on_value": 1, "off_value": 0}  # fmt: skip


@pytest.mark.parametrize(
    ("type_", "fields", "code", "text"),
    [
        ("pi", {"measure": "pump.outlet.q"}, "unknown_variable", "unknown measure"),
        ("pi", {"actuate": "pump.sped"}, "unknown_variable", "pump.speed"),
        ("pi", {"actuate": "pump.rated_speed"}, "invalid_control", "is a parameter"),
        ("pi", {"actuate": "pump.head"}, "invalid_control", "is a observable"),
        ("pi", {"output_min": 1.2, "output_max": 0.3}, "invalid_control", "must be below"),
        ("pi", {"output_max": 2}, "invalid_control", "outside the limits"),
        ("pi", {"gain": -0.1}, "invalid_control", "gain must be positive"),
        ("pi", {"integral_time": 0}, "invalid_control", "integral_time must be positive"),
        ("pi", {"direction": "up"}, "invalid_control", "direction must be"),  # schema too
        ("pi", {"setpoint": "3 m3/h"}, "invalid_control", "setpoint"),
        ("pi", {"setpoint": None}, "invalid_control", "missing setpoint"),
        ("pi", {"on_below": 1}, "invalid_control", "unknown field 'on_below'"),
        ("hysteresis", {"on_below": 2, "off_above": 1}, "invalid_control", "must be below"),
        ("hysteresis", {"on_value": 0}, "invalid_control", "equal"),
        ("hysteresis", {"initial": "maybe"}, "invalid_control", "initial must be"),
        ("hysteresis", {"max_switches_per_hour": 0}, "invalid_control", "must be positive"),
        ("pid", {}, "invalid_control", "unknown type"),
        # non-finite numbers and a hysteresis-only field on a PI control (review findings)
        ("pi", {"setpoint": math.inf}, "invalid_control", "setpoint must be a finite"),
        ("pi", {"integral_time": math.inf}, "invalid_control", "positive and finite"),
        ("hysteresis", {"off_above": math.inf}, "invalid_control", "off_above must be a fin"),
        ("pi", {"state": "on"}, "invalid_control", "unknown field 'state'"),
    ],
)
def test_invalid_controls(type_: str, fields: dict[str, Any], code: str, text: str) -> None:
    ok = HYST_OK if type_ == "hysteresis" else PI_OK
    merged = {k: v for k, v in {**ok, **fields}.items() if v is not None}
    s = base()
    with pytest.raises(wp.WorldpartsError, match=text) as info:
        s.add_control("c", type_, **merged)
    assert info.value.code == code
    assert s.controls == {}  # nothing added
    # A document keeps the definition and check() reports it with the same code.
    doc = base().to_dict()
    doc["controls"] = [{"name": "c", "type": type_, **merged}]
    if {"direction", "initial"} & set(fields) or type_ == "pid":
        # Enumerated fields are also checked by the schema, which names the part.
        with pytest.raises(wp.InvalidValueError, match="controls/0"):
            wp.System.from_dict(doc)
        return
    loaded = wp.System.from_dict(doc)
    issues = [i for i in loaded.check() if i.where == "control.c"]
    assert [i.code for i in issues if i.severity == "error"][:1] == [code]
    with pytest.raises(wp.SystemCheckError):
        loaded.solve()


def test_control_conflict_and_name_rules() -> None:
    s = base()
    s.add_control("a", "pi", **PI_OK)
    with pytest.raises(wp.ControlConflictError, match=r"all write pump\.speed") as info:
        s.add_control("b", "hysteresis", **{**HYST_OK, "actuate": "pump.speed"})
    assert info.value.code == "control_conflict"
    with pytest.raises(wp.InvalidControlError, match="already exists"):
        s.add_control("a", "pi", **{**PI_OK, "actuate": "v.opening", "output_min": 0.1})
    with pytest.raises(wp.UnknownVariableError, match="No control named 'x'"):
        s.remove_control("x")
    doc = s.to_dict()
    doc["controls"].append({"name": "b", "type": "hysteresis", **HYST_OK, "actuate": "pump.speed"})
    loaded = wp.System.from_dict(doc)
    conflict = [i for i in loaded.check() if i.code == "control_conflict"]
    assert len(conflict) == 1 and conflict[0].where == "control.a, control.b"
    doc["controls"].append(doc["controls"][0])
    with pytest.raises(wp.InvalidValueError, match="twice"):
        wp.System.from_dict(doc)
    # Removing the measured component leaves the control, and check() names it.
    s.remove("pump")
    codes = {(i.code, i.where) for i in s.check()}
    assert ("unknown_variable", "control.a") in codes


def test_output_limits_default_to_the_input_limits() -> None:
    s = base()
    s.add_control("a", "pi", **PI_OK)
    r = s.solve()
    assert r.controls["a"].output == pytest.approx(1.04965, abs=1e-5)
    s.set("v.kv", 30)
    r = s.solve()
    assert r["pump.speed"] == 1.2  # the pump's own hard limit


# ----------------------------------------------------------------------------------------
# documents
# ----------------------------------------------------------------------------------------
def schema() -> dict[str, Any]:
    text = (resources.files("worldparts") / "schemas" / "system.schema.json").read_text("utf-8")
    return json.loads(text)


def test_controls_round_trip_and_validate() -> None:
    s = booster()
    s.add("t", "tank")
    s.add("tv", "valve")
    s.connect("pump.outlet", "tv.port_a")
    s.connect("tv.port_b", "t.inlet")
    s.add_control("lvl", "hysteresis", measure="t.level", actuate="tv.opening", on_below=1,
                  off_above="250 cm", on_value=1, off_value=0)  # fmt: skip
    doc = s.to_dict()
    jsonschema.Draft202012Validator(schema()).validate(doc)
    assert doc["controls"] == [
        {"name": "duty", "type": "pi", "measure": "pump.outlet.p", "actuate": "pump.speed",
         "setpoint": "3 bar", **PI},
        {"name": "lvl", "type": "hysteresis", "measure": "t.level", "actuate": "tv.opening",
         "on_below": 1, "off_above": "250 cm", "on_value": 1, "off_value": 0},
    ]  # fmt: skip
    again = wp.System.from_dict(copy.deepcopy(doc))
    assert again.to_dict() == doc
    assert again.solve().values == pytest.approx(s.solve().values, rel=1e-9)
    assert list(again.controls) == ["duty", "lvl"]
    assert again.controls["duty"].describe() == "pi: pump.outlet.p -> pump.speed"
    paths = {v.path: v for v in again.variables() if v.kind == "control"}
    assert set(paths) == {"control.duty.output", "control.duty.measure",
                          "control.lvl.output", "control.lvl.measure"}  # fmt: skip
    assert paths["control.lvl.measure"].unit == "m"


def test_yaml_on_and_off_are_accepted() -> None:
    """YAML 1.1 reads a bare 'initial: on' as true."""
    import yaml

    doc = base().to_dict()
    doc["controls"] = yaml.safe_load(
        "- {name: s, type: hysteresis, measure: pump.outlet.p, actuate: v.opening,\n"
        "   on_below: 1, off_above: 2, on_value: 1, off_value: 0, initial: on}"
    )
    assert doc["controls"][0]["initial"] is True
    jsonschema.Draft202012Validator(schema()).validate(doc)
    s = wp.System.from_dict(doc)
    assert s.check() == [] and s.controls["s"].state == "on"


def test_schema_rejects_malformed_controls() -> None:
    doc = base().to_dict()
    doc["controls"] = [{"name": "c", "type": "pid", "measure": "a.b", "actuate": "c.d"}]
    with pytest.raises(wp.InvalidValueError, match="controls"):
        wp.System.from_dict(doc)
    doc["controls"] = [{"name": "c", "type": "pi", "measure": "a.b"}]
    with pytest.raises(wp.InvalidValueError, match="actuate"):
        wp.System.from_dict(doc)


# ----------------------------------------------------------------------------------------
# ramps in the core (design 13.2)
# ----------------------------------------------------------------------------------------
def line() -> wp.System:
    s = wp.System("line")
    s.add("mains", "supply", pressure=2)
    s.add("v", "valve", kv=2.5, opening=0.1)
    s.add("out", "drain")
    s.connect("mains.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    return s


def test_ramp_is_one_set_event_per_step() -> None:
    ramp = [{"at": "2 s", "ramp": {"v.opening": [0.1, 0.9]}, "over": "4 s"}]
    steps = [{"at": 2 + i, "set": {"v.opening": 0.1 + 0.2 * i}} for i in range(5)]
    a = line().simulate("8 s", "1 s", events=ramp)
    b = line().simulate("8 s", "1 s", events=steps)
    assert a.series == b.series
    assert a["v.opening"] == pytest.approx([0.1, 0.1, 0.1, 0.3, 0.5, 0.7, 0.9, 0.9, 0.9])
    # A ramp shorter than a step, and one ending between samples.
    c = line().simulate("4 s", "1 s", events=[{"at": 1, "ramp": {"v.opening": ["10 %", 1]},
                                                "over": 1.5}])  # fmt: skip
    assert c.time == [0, 1, 2, 2.5, 3, 4]
    assert c["v.opening"] == pytest.approx([0.1, 0.1, 0.1 + 0.9 / 1.5, 1, 1, 1])


def test_document_simulation_block_accepts_ramps() -> None:
    doc = line().to_dict()
    doc["simulation"] = {
        "duration": "10 s",
        "step": "1 s",
        "events": [
            {"at": "1 s", "set": {"mains.pressure": 3}},
            {"at": "2 s", "ramp": {"v.opening": [0.1, 1]}, "over": "5 s"},
        ],
    }
    jsonschema.Draft202012Validator(schema()).validate(doc)
    s = wp.System.from_dict(doc)
    sim = s.simulate()
    assert sim["v.opening"][7] == 1 and sim["v.opening"][4] == pytest.approx(0.1 + 0.9 * 2 / 5)
    flow = sim["v.volume_flow"][-1]
    assert flow == pytest.approx(2.5 * math.sqrt(3 / SG) * 1000 / 60, rel=1e-6)
    bad = copy.deepcopy(doc)
    bad["simulation"]["events"][1].pop("over")
    with pytest.raises(wp.InvalidValueError, match="simulation"):
        wp.System.from_dict(bad)


@pytest.mark.parametrize(
    ("event", "text"),
    [
        ({"at": 0, "set": {"v.opening": 1}, "ramp": {"v.opening": [0, 1]}, "over": 1},
         "exactly one of"),
        ({"at": 0, "ramp": {"v.opening": [0, 1]}}, "needs a non-empty"),
        ({"at": "8 s", "ramp": {"v.opening": [0, 1]}, "over": "5 s"}, "after the end"),
        ({"at": 0, "ramp": {"v.opening": [0]}, "over": 1}, "[start, end]"),
        ({"at": 0, "ramp": {"v.volume_flow": [0, 1]}, "over": 1}, "not a parameter"),
        ({"at": 0, "ramp": {"v.opening": [0, 1]}, "over": 0}, "must be positive"),
        ({"at": 0, "set": {"v.opening": 1}, "over": 1}, "only used with 'ramp'"),
        ({"at": 0, "set": {"v.opening": 1}, "when": 1}, "unknown key"),
        ({"at": 0, "ramp": {"v.opening": [0, 2]}, "over": 1}, "v.opening"),
    ],
)  # fmt: skip
def test_ramp_errors(event: dict[str, Any], text: str) -> None:
    with pytest.raises(wp.WorldpartsError) as info:
        line().simulate("10 s", "1 s", events=[event])
    assert text in str(info.value)


# ----------------------------------------------------------------------------------------
# review fixes: sampled-data timing, steady solve edge cases, selections
# ----------------------------------------------------------------------------------------
def test_recorded_flows_integrate_to_the_level_with_a_controller() -> None:
    """The command issued at t is applied at t like an event: the sample recorded at t is
    solved with it and the storage integration over the next step uses that same solution,
    so the recorded net inflows add up to the level change exactly (explicit Euler)."""
    h = 10.0
    sim = tower(diameter=0.3).simulate("30 min", f"{h} s")
    assert len(switch_times(sim)) >= 4
    area = math.pi * 0.3**2 / 4
    gained = area * (sim["tower.level"][-1] - sim["tower.level"][0])
    inflow = sum(q / 3600 * h for q in sim["tower.net_inflow"][:-1])
    assert gained == pytest.approx(inflow, rel=1e-12, abs=1e-12)
    # The pump speed recorded at t is the command issued at t.
    assert sim["pump.speed"] == sim["control.switch.output"]


def shower(lift: float, **control: Any) -> wp.System:
    s = wp.System("shower")
    s.add("cold", "supply", pressure=3, temperature=12)
    s.add("hot", "supply", pressure=3, temperature=60)
    s.add("f", "mixing_faucet", lift=lift, mix=0.5)
    s.connect("cold.port", "f.cold")
    s.connect("hot.port", "f.hot")
    fields = {"measure": "f.temperature", "setpoint": "38 degC", "actuate": "f.mix",
              "gain": 0.01, "integral_time": 2, **control}  # fmt: skip
    s.add_control("thermo", "pi", **fields)
    return s


def test_undefined_measure_holds_in_steady_solve_and_simulation() -> None:
    """A closed faucet: the temperature is undefined at every mix, so both the steady solve
    and a simulation hold the output; nothing is saturated and nothing is written."""
    s = shower(lift=0.0)
    before = s.to_dict()
    r = s.solve()
    c = r.controls["thermo"]
    assert (c.output, c.measured, c.error, c.saturated) == (0.5, None, None, False)
    assert not any(w.component == "control.thermo" for w in r.warnings)
    sim = s.simulate("5 s", "1 s")
    assert set(sim["control.thermo.output"]) == {0.5}
    assert s.to_dict() == before


def test_measure_undefined_at_some_outputs_raises_and_restores() -> None:
    """The loop drives the faucet lift: at lift 0 the temperature is undefined, at lift 1
    it is defined. The goal seek cannot compare an undefined value with the setpoint, so
    solve() raises, names both points without a spurious unit '1', and changes nothing."""
    s = shower(lift=0.5, actuate="f.lift")
    before = s.to_dict()
    with pytest.raises(wp.InvalidValueError, match=r"f\.lift = 0 but defined at 1,") as info:
        s.solve()
    assert " 1 but" not in str(info.value) and "0 1" not in str(info.value)
    assert s.get("f.lift") == 0.5 and s.to_dict() == before


def test_a_measure_held_by_the_steady_solve_is_reported_unresponsive() -> None:
    """A PI loop on a tank level: solve() holds the level, so the measure does not respond
    to the pump speed. The loop holds its output and says so with control_unresponsive,
    whether the level is at the setpoint or not; it is never reported as saturated."""
    for level in (1.5, 1.2):
        s = tower()
        s.remove_control("switch")
        s.set_values({"tower.initial_level": level, "pump.speed": 0.8})
        s.add_control("lvl", "pi", measure="tower.level", setpoint=1.5, actuate="pump.speed",
                      gain=0.5, integral_time="60 s", output_min=0, output_max=1.2)  # fmt: skip
        r = s.solve()
        c = r.controls["lvl"]
        assert c.output == 0.8 and not c.saturated
        assert c.error == pytest.approx(1.5 - level, abs=1e-12)
        codes = [w.code for w in r.warnings if w.component == "control.lvl"]
        assert codes == ["control_unresponsive"]
        assert s.get("pump.speed") == 0.8


def test_control_results_can_be_selected_by_name() -> None:
    from worldparts.cli import select_paths

    s = booster()
    both = ["control.duty.output", "control.duty.measure"]
    assert sorted(select_paths(s, ["control"])) == sorted(both)
    assert sorted(select_paths(s, ["control.duty"])) == sorted(both)
    with pytest.raises(wp.UnknownVariableError, match="control"):
        select_paths(base(), ["control"])
    with pytest.raises(wp.UnknownVariableError):
        select_paths(s, ["control.nope"])


def test_restore_on_error_puts_back_values_and_switch_states() -> None:
    s = tower()
    before = s.to_dict()
    with pytest.raises(RuntimeError), s.restore_on_error():
        s.set_values({"pump.speed": 0.3, "tower.level": 0.2})
        s.controls["switch"].state = "off"
        raise RuntimeError("boom")
    assert s.to_dict() == before and s.controls["switch"].state == "on"
