"""Behaviour of the core components: supply, drain, pipe, valve and check valve."""

from __future__ import annotations

import itertools
import math

import pytest
from fluids.friction import Churchill_1977

import worldparts as wp
from worldparts.components.base import first_order
from worldparts.components.valves import characteristic
from worldparts.media import MU, RHO, G


def kv_flow_lmin(kv: float, dp_bar: float) -> float:
    return math.copysign(kv * math.sqrt(abs(dp_bar) * 1000 / RHO) * 1000 / 60, dp_bar)


def line(component: str, pressure: float = 1.0, **values: object) -> wp.System:
    s = wp.System(component)
    s.add("src", "supply", pressure=pressure)
    s.add("dut", component, **values)
    s.add("sink", "drain")
    first, second = wp.default_catalog().get(component).ports
    s.connect("src.port", f"dut.{first}")
    s.connect(f"dut.{second}", "sink.port")
    return s


# -- boundaries -----------------------------------------------------------------------------


def test_supply_is_an_ideal_pressure_source() -> None:
    for p in (-0.5, 0.0, 2.0, 50.0):
        r = line("valve", pressure=p).solve()
        assert r["src.port.p"] == pytest.approx(p, abs=1e-5)
        assert r["src.volume_flow"] == pytest.approx(kv_flow_lmin(2.5, p), rel=1e-5, abs=1e-6)
        assert r["sink.volume_flow"] == pytest.approx(r["src.volume_flow"], rel=1e-9, abs=1e-12)


def test_drain_backflow_and_temperatures() -> None:
    s = wp.System()
    s.add("src", "supply", pressure=-0.3, temperature=10)
    s.add("v", "valve")
    s.add("sink", "drain", temperature=30)
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "sink.port")
    r = s.solve()
    assert r["sink.volume_flow"] < 0
    assert r.modes == {"src": "absorbing", "v": "open", "sink": "backflow"}
    assert [w.path for w in r.warnings] == ["sink.backflow"]
    assert r["src.port.T"] == pytest.approx(30.0)  # the supply port receives drain water


def test_boundaries_joined_directly_are_a_short_circuit() -> None:
    """Two fixed pressures joined with no resistance would drive an unbounded flow."""
    s = wp.System()
    s.add("a", "supply", pressure=3)
    s.add("b", "supply", pressure=1)
    s.connect("a.port", "b.port")
    codes = {(i.severity, i.code) for i in s.check()}
    assert ("error", "boundary_short_circuit") in codes
    with pytest.raises(wp.SystemCheckError, match="boundary_short_circuit"):
        s.solve()
    # Equal pressures are harmless (no flow), and a resistance between them is fine.
    s.set("b.pressure", 3)
    assert s.solve()["a.volume_flow"] == pytest.approx(0.0, abs=1e-9)
    t = wp.System()
    t.add("a", "supply", pressure=3)
    t.add("d", "drain")
    t.add("v", "valve")
    t.connect("a.port", "v.port_a")
    t.connect("v.port_b", "d.port")
    assert not [i for i in t.check() if i.code == "boundary_short_circuit"]


def test_component_bypassed_on_itself_is_a_warning() -> None:
    s = line("valve", pressure=1)
    s.connect("dut.port_a", "dut.port_b")
    issues = [i for i in s.check() if i.code == "self_connection"]
    assert issues and issues[0].severity == "warning" and "bypassed" in issues[0].message


def test_supply_port_mass_flow_sign() -> None:
    r = line("valve", pressure=2).solve()
    # m_flow is positive into the component: the supply delivers, so it is negative
    assert r["src.port.m_flow"] < 0
    assert r["dut.port_a.m_flow"] > 0 and r["dut.port_b.m_flow"] < 0
    assert r["src.port.m_flow"] == pytest.approx(-r["dut.port_a.m_flow"])


# -- pipe -----------------------------------------------------------------------------------


@pytest.mark.parametrize("pressure", [0.0005, 0.02, 0.3, 2.0])
def test_pipe_against_fluids(pressure: float) -> None:
    r = line("pipe", pressure=pressure, length=8, diameter=20, roughness=0.01).solve()
    q = r["dut.volume_flow"] / 60000
    d = 0.02
    v = q / (math.pi * d * d / 4)
    re = RHO * v * d / MU
    f = Churchill_1977(re, 0.01e-3 / d)
    dp = f * 8 / d * RHO * v * v / 2
    assert dp / 1e5 == pytest.approx(r["dut.pressure_drop"], rel=1e-6, abs=1e-9)
    assert dp / 1e5 == pytest.approx(pressure, rel=1e-6, abs=1e-8)
    assert r["dut.velocity"] == pytest.approx(v, rel=1e-12)
    assert r["dut.reynolds"] == pytest.approx(re, rel=1e-12)
    assert r["dut.friction_factor"] == pytest.approx(f, rel=1e-10)
    assert r["dut.pressure_drop"] == pytest.approx(pressure, abs=1e-6)


def test_pipe_static_head_and_minor_loss() -> None:
    s = line("pipe", pressure=2, length=10, diameter=25, minor_loss=5, height_difference=5)
    r = s.solve()
    q = r["dut.volume_flow"] / 60000
    v = q / (math.pi * 0.025**2 / 4)
    re = RHO * v * 0.025 / MU
    f = Churchill_1977(re, 0.0015e-3 / 0.025)
    dp = f * 10 / 0.025 * RHO * v * v / 2 + 5 * RHO * v * v / 2 + RHO * G * 5
    assert dp / 1e5 == pytest.approx(r["dut.pressure_drop"], rel=1e-6, abs=1e-9)
    assert dp / 1e5 == pytest.approx(2.0, rel=1e-6)


def test_pipe_flows_backwards_up_a_hill() -> None:
    r = line("pipe", pressure=0.5, height_difference=10).solve()  # 0.5 bar < 0.98 bar of head
    assert r["dut.volume_flow"] < 0
    assert r["sink.volume_flow"] < 0


def test_pipe_high_velocity_warning() -> None:
    r = line("pipe", pressure=1).solve()
    assert abs(r["dut.velocity"]) > 3
    assert r.has_warning("dut.high_velocity")
    assert not line("pipe", pressure=0.1).solve().has_warning("dut.high_velocity")


def test_pipe_stagnant_mode_and_undefined_friction_factor() -> None:
    s = wp.System()
    s.add("src", "supply", pressure=1)
    s.add("dut", "pipe")
    s.connect("src.port", "dut.port_a")
    r = s.solve()
    assert r.modes["dut"] == "stagnant"
    assert r["dut.friction_factor"] is None


# -- valve ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "y", "expected"),
    [
        ("linear", 0.0, 1e-4),
        ("linear", 0.5, 1e-4 + (1 - 1e-4) * 0.5),
        ("linear", 1.0, 1.0),
        ("equal_percentage", 0.0, 1e-4),
        ("equal_percentage", 1.0, 1.0),
        ("equal_percentage", 0.5, 1e-4 + (1 - 1e-4) * (50**-0.5 - 1 / 50) / (1 - 1 / 50)),
        ("quick_opening", 0.25, 1e-4 + (1 - 1e-4) * 0.5),
        ("quick_opening", 1.0, 1.0),
    ],
)
def test_valve_characteristics(kind: str, y: float, expected: float) -> None:
    assert characteristic(kind, y, 1e-4, 50) == pytest.approx(expected, rel=1e-12)


def test_unknown_characteristic() -> None:
    with pytest.raises(ValueError):
        characteristic("parabolic", 0.5, 1e-4, 50)


@pytest.mark.parametrize("kind", ["linear", "equal_percentage", "quick_opening"])
def test_valve_flow_follows_effective_kv(kind: str) -> None:
    for y in (0.0, 0.1, 0.5, 0.9, 1.0):
        r = line("valve", pressure=2, characteristic=kind, opening=y).solve()
        kv = 2.5 * characteristic(kind, y, 1e-4, 50)
        assert r["dut.effective_kv"] == pytest.approx(kv, rel=1e-12)
        assert r["dut.volume_flow"] == pytest.approx(kv_flow_lmin(kv, 2.0), rel=1e-5)


def test_valve_is_symmetric() -> None:
    s = wp.System()
    s.add("src", "supply", pressure=1.5)
    s.add("dut", "valve", opening=0.7)
    s.add("sink", "drain")
    s.connect("src.port", "dut.port_b")
    s.connect("dut.port_a", "sink.port")
    r = s.solve()
    kv = 2.5 * characteristic("linear", 0.7, 1e-4, 50)
    assert r["dut.volume_flow"] == pytest.approx(-kv_flow_lmin(kv, 1.5), rel=1e-5)
    assert r["dut.pressure_drop"] == pytest.approx(-1.5, abs=1e-6)


def test_closed_valve_modes() -> None:
    r = line("valve", pressure=3, opening=0).solve()
    assert r.modes["dut"] == "closed"
    assert r["dut.volume_flow"] == pytest.approx(kv_flow_lmin(2.5e-4, 3.0), rel=1e-4)
    assert line("valve", opening=1).solve().modes["dut"] == "open"


def test_first_order_lag_is_exact() -> None:
    x = 0.0
    for _ in range(10):
        x = first_order(x, 1.0, 5.0, 1.0)
    assert x == pytest.approx(1 - math.exp(-2.0), rel=1e-12)
    assert first_order(0.3, 1.0, 0.0, 1.0) == 1.0
    assert first_order(0.3, 1.0, 5.0, 0.0) == 0.3


def test_valve_actuator_lag_in_simulation() -> None:
    s = line("valve", actuator_time=10, opening=0)
    s.set("dut.opening", 1)
    sim = s.simulate("30 s", "1 s", variables=["dut.position", "dut.volume_flow"])
    for k, t in enumerate(sim.time):
        assert sim["dut.position"][k] == pytest.approx(1 - math.exp(-t / 10), abs=1e-12)
    flows = sim["dut.volume_flow"]
    assert all(b > a for a, b in itertools.pairwise(flows))


def test_instant_actuator_in_simulation() -> None:
    s = line("valve", opening=0.3)
    sim = s.simulate(3, events=[{"at": 2, "set": {"dut.opening": 0.8}}])
    assert sim["dut.position"] == pytest.approx([0.3, 0.3, 0.8, 0.8])


# -- check valve ----------------------------------------------------------------------------


def test_check_valve_forward_and_reverse() -> None:
    fwd = line("check_valve", pressure=1).solve()
    assert fwd["dut.volume_flow"] == pytest.approx(kv_flow_lmin(3.0, 1.0), rel=1e-5)
    assert fwd.modes["dut"] == "open"
    rev = line("check_valve", pressure=-0.5).solve()
    assert rev["dut.volume_flow"] == pytest.approx(kv_flow_lmin(3e-6, -0.5), rel=1e-3)
    assert rev.modes["dut"] == "closed"


def test_check_valve_prevents_backflow_between_supplies() -> None:
    """A check valve protects a low-pressure line from a high-pressure one."""
    s = wp.System()
    s.add("low", "supply", pressure=1, temperature=10)
    s.add("high", "supply", pressure=4, temperature=60)
    s.add("cv", "check_valve")
    s.add("tap", "valve", kv=1.0)
    s.add("out", "drain")
    s.connect("low.port", "cv.port_a")
    s.connect("cv.port_b", "tap.port_a")
    s.connect("high.port", "tap.port_a")  # junction
    s.connect("tap.port_b", "out.port")
    r = s.solve()
    assert r["cv.volume_flow"] <= 0 and abs(r["cv.volume_flow"]) < 1e-3
    assert r["tap.volume_flow"] == pytest.approx(kv_flow_lmin(1.0, 4.0), rel=1e-4)
    assert r["out.port.T"] == pytest.approx(60.0, abs=0.01)


# -- pipe validity and vapour pressure (review regressions) ----------------------------------


def test_pipe_rejects_roughness_of_half_the_diameter() -> None:
    s = wp.System()
    with pytest.raises(wp.InvalidValueError, match="half the diameter"):
        s.add("p", "pipe", diameter=10, roughness=5)
    s.add("p", "pipe", diameter=10, roughness=1)
    with pytest.raises(wp.InvalidValueError, match="half the diameter"):
        s.set("p.diameter", 2)
    assert s.get("p.diameter") == 10


def test_pipe_warns_about_relative_roughness_beyond_churchill_range() -> None:
    r = line("pipe", pressure=1, diameter=10, roughness=0.6).solve()
    assert r.has_warning("dut.high_relative_roughness")
    r = line("pipe", pressure=1, diameter=10, roughness=0.4).solve()
    assert not r.has_warning("dut.high_relative_roughness")


def test_pipe_warns_below_vapour_pressure_of_hot_water() -> None:
    """A siphon over an 8 m crest: the crest is near 0.2 bar absolute.

    That is above the vapour pressure of water at 20 degC (0.023 bar) but below that at
    95 degC (0.845 bar, IAPWS-IF97), so only the hot siphon is flagged.
    """
    s = wp.System()
    s.add("src", "supply", pressure=0, temperature=95)
    s.add("up", "pipe", length=10, diameter=25, height_difference=8)
    s.add("down", "pipe", length=12, diameter=25, height_difference=-10)
    s.add("sink", "drain")
    s.connect("src.port", "up.port_a")
    s.connect("up.port_b", "down.port_a")
    s.connect("down.port_b", "sink.port")
    r = s.solve()
    assert r["up.volume_flow"] > 0
    assert -1.01325 + 0.02339 < r["up.port_b.p"] < -1.01325 + 0.845
    assert r.has_warning("up.below_vapour_pressure")
    assert r.has_warning("down.below_vapour_pressure")
    s.set("src.temperature", 20)
    r = s.solve()
    assert not r.has_warning("up.below_vapour_pressure")
