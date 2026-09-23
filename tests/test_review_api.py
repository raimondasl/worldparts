"""Adversarial review: API and spec conformance, and usability by an AI agent.

Every test here demonstrates a defect found in review and is expected to FAIL until the
defect is fixed. Each docstring names the section of docs/design.md it enforces.
"""

from __future__ import annotations

import copy
import subprocess
import sys
import textwrap

import pytest

import worldparts as wp
from worldparts.components.valves import TwoWayValve
from worldparts.contracts import run_contract, run_scenario
from worldparts.manifest import Manifest, _schema_errors


def _heated_tank_system(catalog: wp.Catalog) -> wp.System:
    s = wp.System("tank", catalog=catalog)
    s.add("src", "supply", pressure=1)
    s.add("tk", "heated_tank")
    s.add("out", "drain")
    s.connect("src.port", "tk.inlet")
    s.connect("tk.outlet", "out.port")
    return s


# -- scenarios and contracts must not pass vacuously -----------------------------------------


def test_warning_expectation_with_undeclared_code_does_not_pass() -> None:
    """Design sections 4 and 7: every warning code is declared in the manifest, and scenarios
    are tested claims. An expectation on a misspelled (undeclared) warning code can never
    fail, so it must be reported as a failure (or rejected), not silently pass."""
    m = wp.default_catalog().get("valve")
    scen = copy.deepcopy(m.scenario("kv-at-1-bar"))
    scen["expect"] = [{"warning": "dut.high_presure_drop", "present": False}]
    outcome = run_scenario(m, scen)
    assert not outcome.passed, "a typo in a warning code made the expectation pass vacuously"


def test_warning_iff_with_undeclared_code_does_not_pass() -> None:
    """Design section 7 (`warning_iff`: `code` is `<instance>.<code>`) with section 4 (codes
    are declared): a contract on an undeclared code must fail instead of passing whenever the
    condition happens to be false at every sweep point."""
    m = wp.default_catalog().get("valve")
    contract = {
        "id": "typo",
        "description": "Misspelled code.",
        "scenario": "kv-at-1-bar",
        "sweep": {"variable": "dut.opening", "from": 0, "to": 1, "steps": 3},
        "check": {
            "type": "warning_iff",
            "code": "dut.high_presure_drop",
            "condition": "dut.pressure_drop > 30",
        },
    }
    assert not run_contract(m, contract).passed


def test_run_contract_reports_unknown_name_in_check_expression() -> None:
    """Design section 7: contracts run through `check-catalog`, which reports pass or fail per
    contract. A misspelled name in an `equal` expression must be reported as a failed
    contract (naming the name), not escape as an exception that aborts the whole run."""
    m = wp.default_catalog().get("valve")
    contract = {
        "id": "typo",
        "description": "Misspelled variable.",
        "scenario": "kv-at-1-bar",
        "sweep": {"variable": "dut.opening", "from": 0, "to": 1, "steps": 3},
        "check": {
            "type": "equal",
            "left": "dut.flow",
            "right": "dut.volume_flow",
            "abs_tol": 1e-9,
        },
    }
    outcome = run_contract(m, contract)  # currently raises UnknownVariableError
    assert not outcome.passed
    assert any("dut.flow" in f for f in outcome.failures)


# -- set_values ----------------------------------------------------------------------------


def test_set_values_accepts_consistent_batch_in_any_order(test_catalog: wp.Catalog) -> None:
    """Design section 9 (`set_values(system_id, values)`) and 8.9 (`initial_level` must not
    exceed `height`): a batch that is consistent as a whole must be accepted whatever the
    key order; cross-parameter rules must be checked against the batch result."""
    s = _heated_tank_system(test_catalog)
    s.set_values({"tk.initial_level": 2.5, "tk.height": 3})
    assert s.get("tk.height") == 3
    assert s.get("tk.initial_level") == 2.5


def test_set_values_is_atomic_when_a_cross_parameter_rule_fails(
    test_catalog: wp.Catalog,
) -> None:
    """Design section 9 (`set_values`) and the System.set_values contract ("values are
    validated before any is applied"): a rejected batch must leave the system unchanged."""
    s = _heated_tank_system(test_catalog)
    with pytest.raises(wp.InvalidValueError):
        s.set_values({"tk.initial_level": 1.5, "tk.height": 1.0})
    assert s.get("tk.initial_level") == 0.5, "the first value of a rejected batch was applied"
    assert s.get("tk.height") == 2.0


def test_set_values_keeps_state_given_in_the_same_batch(test_catalog: wp.Catalog) -> None:
    """Design sections 5.5 (`steady: hold` states keep their current values) and 9
    (`set_values`): a state value given explicitly in a batch must not be silently discarded
    because a parameter later in the same batch re-initialises the states."""
    s = _heated_tank_system(test_catalog)
    s.set_values({"tk.level": 1.7, "tk.port_kv": 2.0})
    assert s.get("tk.level") == pytest.approx(1.7)


# -- system documents ----------------------------------------------------------------------


def test_system_document_accepts_table_cells_with_units(test_catalog: wp.Catalog) -> None:
    """Design sections 3.1 (strings with units are parsed) and 6.3 (language-neutral system
    document): table cells with units are accepted by System.add/System.set, so the same
    values must be valid in a system document (system.schema.json) and in from_dict."""
    curve = [["0 m", "2000 W"], ["200 cm", "6 kW"]]
    s = wp.System("t", catalog=test_catalog)
    s.add("tk", "heated_tank", heater_curve=curve)  # the Python API accepts it
    doc = {
        "worldparts_system": "0.1",
        "name": "t",
        "components": [
            {"name": "tk", "type": "heated_tank", "parameters": {"heater_curve": curve}}
        ],
    }
    assert _schema_errors("system", doc) == []
    s2 = wp.System.from_dict(doc, catalog=test_catalog)
    assert s2.get("tk.heater_curve") == [[0.0, 2.0], [2.0, 6.0]]


def test_round_trip_preserves_component_order() -> None:
    """Design section 6.3 (`to_dict()`/`from_dict()` round-trip the system document):
    from_dict followed by to_dict must keep the components in document order, including
    components whose type is unknown (they are preserved as written)."""
    doc = {
        "worldparts_system": "0.1",
        "name": "x",
        "components": [
            {"name": "a", "type": "suply", "parameters": {"pressure": 3}},
            {"name": "v", "type": "worldparts.hydraulic.valve"},
            {"name": "d", "type": "worldparts.hydraulic.drain"},
        ],
        "connections": [],
    }
    out = wp.System.from_dict(doc).to_dict()
    assert [c["name"] for c in out["components"]] == ["a", "v", "d"]


# -- manifests and expressions -------------------------------------------------------------


def _valve_data() -> dict:
    return copy.deepcopy(wp.default_catalog().get("valve").data)


def test_mode_on_string_parameter_is_rejected_or_works() -> None:
    """Design sections 3.4 and 4: mode conditions use the component's local names. The
    manifest validator accepts a condition naming a string parameter, but solve() then raises
    UnknownVariableError. Either the loader must reject it or solve() must evaluate it."""
    d = _valve_data()
    d["id"] = "worldparts.testing.string_mode_valve"
    d["modes"].insert(0, {"name": "odd", "condition": "characteristic", "description": "x"})
    try:
        m = Manifest.from_dict(d)
    except wp.ManifestError:
        return  # rejected at load time: acceptable
    cat = wp.load_catalog()
    cat.add(m, TwoWayValve)
    s = wp.System(catalog=cat)
    s.add("src", "supply")
    s.add("v", "string_mode_valve")
    s.add("out", "drain")
    s.connect("src.port", "v.port_a")
    s.connect("v.port_b", "out.port")
    s.solve()  # currently raises UnknownVariableError


def test_pressure_reference_only_for_pressure_units() -> None:
    """Design section 4 (`pressure_reference` is "only for pressures"): declaring it on a
    non-pressure unit (a Kv in m3/h) is a manifest error an author should be told about."""
    d = _valve_data()
    d["id"] = "worldparts.testing.pref_valve"
    d["parameters"][0]["pressure_reference"] = "absolute"
    with pytest.raises(wp.ManifestError):
        Manifest.from_dict(d)


def test_expression_power_is_bounded() -> None:
    """Design section 3.4: expressions are evaluated by a *safe* evaluator. Integer `**`
    towers such as `9 ** 9 ** 9` must not hang the process (they should give None or an
    ExpressionError quickly)."""
    code = textwrap.dedent(
        """
        from worldparts.expressions import evaluate
        from worldparts.errors import ExpressionError
        try:
            evaluate("9 ** 9 ** 9 > 1", {})
        except ExpressionError:
            pass
        """
    )
    try:
        subprocess.run([sys.executable, "-c", code], timeout=10, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("evaluating '9 ** 9 ** 9 > 1' did not finish within 10 s")
