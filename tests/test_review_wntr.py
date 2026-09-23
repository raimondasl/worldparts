"""Independent review of the WNTR adapter: cases the adapter's own tests do not cover.

Each test builds a small pumping or distribution network, solves it in worldparts and in
EPANET 2.2 (through the adapter) and asserts the agreement the adapter documents for the
mapping involved (docs/wntr-adapter.md: tank "yes (steady)", pump curve "within 4 mm of
head", "an empty tank blocks outflow in both"). The whole module is skipped without the
optional ``wntr`` package.
"""

from __future__ import annotations

import pytest

import worldparts as wp

wntr = pytest.importorskip("wntr")

from worldparts.adapters.wntr_adapter import compare_with_wntr, translate  # noqa: E402

#: The adapter's own test head curve with a falling start (fitted linear term b < 0), which
#: is exported as a 41-point curve ending at the run-out flow (about 49.6 m3/h).
FALLING_CURVE = [[0, 40], [10, 36], [20, 30], [30, 22], [36, 16]]


# ----------------------------------------------------------------------------------------
# networks the adapter's tests do not cover (these pass: kept as regression checks)
# ----------------------------------------------------------------------------------------
def parallel_pumps() -> wp.System:
    """Tank -> falling suction (-2 m) -> two pumps in parallel (speeds 1 and 0.9) -> riser
    (+15 m) -> falling pipe (-4 m) -> drain."""
    s = wp.System("parallel")
    s.add("tank", "tank", initial_level=1.0, height=3, diameter=2)
    s.add("suction", "pipe", length=5, diameter="100 mm", roughness="0.05 mm",
          height_difference=-2)  # fmt: skip
    s.add("p1", "centrifugal_pump", speed=1.0)
    s.add("p2", "centrifugal_pump", speed=0.9)
    s.add("riser", "pipe", length=40, diameter="100 mm", roughness="0.05 mm",
          height_difference=15)  # fmt: skip
    s.add("down", "pipe", length=30, diameter="80 mm", roughness="0.05 mm",
          height_difference=-4)  # fmt: skip
    s.add("top", "drain")
    s.connect("tank.outlet", "suction.port_a")
    s.connect("suction.port_b", "p1.inlet")
    s.connect("suction.port_b", "p2.inlet")
    s.connect("p1.outlet", "riser.port_a")
    s.connect("p2.outlet", "riser.port_a")
    s.connect("riser.port_b", "down.port_a")
    s.connect("down.port_b", "top.port")
    return s


def test_parallel_pumps_with_negative_height_differences() -> None:
    """Measured 0.016 % flow and 3e-4 bar; elevations follow height_difference."""
    s = parallel_pumps()
    tr = translate(s)
    expected = {
        "top.port": 0.0,
        "riser.port_b": 4.0,
        "p1.outlet": -11.0,
        "p1.inlet": -11.0,
        "tank.outlet": -9.0,
    }
    for port, z in expected.items():
        node = tr.node_of_port(port)
        assert node is not None and node.elevation == pytest.approx(z)
    report = compare_with_wntr(s)
    assert report.max_flow_rel_diff < 5e-4
    assert report.max_pressure_abs_diff < 2e-3
    assert report.link("p1").wntr_value + report.link("p2").wntr_value == pytest.approx(
        report.link("riser").wntr_value, rel=1e-5
    )


def test_pipe_laid_against_the_flow_keeps_its_sign() -> None:
    """A gravity line whose pipe runs port_b -> port_a (rising 10 m from b to a)."""
    s = wp.System("gravity")
    s.add("src", "supply", pressure=0.0)
    s.add("p", "pipe", length=50, diameter="50 mm", roughness="0.05 mm", height_difference=10)
    s.add("o", "drain")
    s.connect("src.port", "p.port_b")
    s.connect("p.port_a", "o.port")
    report = compare_with_wntr(s)
    assert report.link("p").worldparts < 0.0 and report.link("p").wntr_value < 0.0
    assert report.max_flow_rel_diff < 1e-3


# ----------------------------------------------------------------------------------------
# defects
# ----------------------------------------------------------------------------------------
def fill_full_roof_tank() -> wp.System:
    """A lift pump filling a rooftop tank that is at its rim (it overflows)."""
    s = wp.System("full roof")
    s.add("ground", "tank", initial_level=1.5, height=3, diameter=2)
    s.add("pump", "centrifugal_pump")
    s.add("riser", "pipe", length=40, diameter="80 mm", roughness="0.05 mm",
          height_difference=12)  # fmt: skip
    s.add("roof", "tank", initial_level=2.0, height=2.0, diameter=2)
    s.connect("ground.outlet", "pump.inlet")
    s.connect("pump.outlet", "riser.port_a")
    s.connect("riser.port_b", "roof.inlet")
    return s


def test_full_tank_keeps_receiving_water_as_in_worldparts() -> None:
    """worldparts' steady tank is a fixed-level node that spills what exceeds the rim
    (``tank_overflow``); the export gives EPANET a tank with the overflow option off, and
    EPANET closes every link that would fill a full tank. The pump flow falls from 36 m3/h
    to 0 in EPANET (100 % difference), and neither the approximations nor the divergence
    sources mention it, while docs/wntr-adapter.md lists the tank as exact in steady state.
    Setting ``overflow=True`` on the WNTR tank (EPANET 2.2 supports it) reproduces
    worldparts to 0.01 %."""
    s = fill_full_roof_tank()
    r = s.solve()
    assert float(r.get("pump.volume_flow", unit="m3/h")) > 30.0  # worldparts pumps (and spills)
    report = compare_with_wntr(s)
    assert report.link("pump").wntr_value == pytest.approx(report.link("pump").worldparts, rel=5e-4)
    assert report.max_flow_rel_diff < 5e-4


def test_empty_tank_blocks_outflow_in_both() -> None:
    """KNOWN_DIVERGENCE_SOURCES: "an empty tank blocks outflow in both". worldparts counts a
    tank as empty at levels up to 1 mm (tank.EMPTY_LEVEL) and blocks its outflow; the export
    passes the level (0.5 mm) to EPANET with min_level 0, so EPANET drains the tank through
    the pipe at 33 m3/h. Exporting a level <= EMPTY_LEVEL as 0 makes EPANET close the outlet
    too (measured 0 m3/h)."""
    s = wp.System("empty")
    s.add("t", "tank", initial_level=0.0005, height=3.0, diameter=2)
    s.add("p", "pipe", length=10, diameter="50 mm", roughness="0.05 mm", height_difference=-5)
    s.add("o", "drain")
    s.connect("t.outlet", "p.port_a")
    s.connect("p.port_b", "o.port")
    report = compare_with_wntr(s)
    assert abs(report.link("p").worldparts) < 1e-2
    assert report.max_flow_abs_diff < 1e-2


def test_pump_beyond_run_out_follows_the_quadratic() -> None:
    """A booster on a 2 bar main with a short discharge runs past its run-out flow (the
    fitted head turns negative; worldparts warns ``beyond_curve``). The 41-point curve stops
    at the run-out flow and EPANET extrapolates its last segment linearly, while worldparts
    follows the quadratic: the flow differs by 2.3 % (11 % at 6 bar), yet the approximation
    note claims the curve is within 5 mm of the quadratic. Extending the multi-point curve
    past the run-out flow (negative heads, which EPANET accepts) restores agreement
    (measured 0.002 % at 6 bar with the curve sampled to three times the run-out flow)."""
    s = wp.System("booster")
    s.add("m", "supply", pressure="2 bar")
    s.add("pu", "centrifugal_pump", head_curve=FALLING_CURVE)
    s.add("p", "pipe", length=10, diameter="80 mm", roughness="0.05 mm")
    s.add("o", "drain")
    s.connect("m.port", "pu.inlet")
    s.connect("pu.outlet", "p.port_a")
    s.connect("p.port_b", "o.port")
    tr = translate(s)
    assert tr.pumps["pu"].form == "multi_point"
    q = float(s.solve().get("pu.volume_flow", unit="m3/h"))
    assert q > tr.pumps["pu"].runout_flow * 3600.0  # operating beyond the run-out flow
    report = compare_with_wntr(s)
    assert report.max_flow_rel_diff < 5e-4
