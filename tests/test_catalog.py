"""Catalogue self-test, parametrized over every manifest shipped in the package.

New manifests dropped into ``src/worldparts/catalog/**`` are picked up automatically: each is
schema-validated, its implementation imported, and every scenario and contract run.
"""

from __future__ import annotations

from typing import Any

import pytest

import worldparts as wp
from worldparts.catalog import package_manifest_paths
from worldparts.components.base import Component
from worldparts.manifest import Manifest, _schema_errors, load_yaml

RAW: dict[str, dict[str, Any]] = {}
for _trav in package_manifest_paths():
    _data = load_yaml(_trav.read_text(encoding="utf-8"))
    RAW[str(_data.get("id", _trav.name))] = _data

IDS = sorted(RAW)
SCENARIOS = [(mid, s["id"]) for mid in IDS for s in RAW[mid].get("scenarios", [])]
CONTRACTS = [(mid, c["id"]) for mid in IDS for c in RAW[mid].get("contracts", [])]


def manifest(mid: str) -> Manifest:
    return wp.default_catalog().get(mid)


def test_catalogue_is_not_empty() -> None:
    assert {"supply", "drain", "pipe", "valve", "check_valve"} <= {
        m.rsplit(".", 1)[-1] for m in IDS
    }
    assert len(wp.default_catalog()) == len(IDS)


@pytest.mark.parametrize("mid", IDS)
def test_manifest_is_schema_valid(mid: str) -> None:
    assert _schema_errors("component-manifest", RAW[mid]) == []
    Manifest.from_dict(RAW[mid])  # semantic checks


@pytest.mark.parametrize("mid", IDS)
def test_manifest_basics(mid: str) -> None:
    m = manifest(mid)
    assert m.id.startswith("worldparts.")
    assert len(m.scenarios) >= 2
    assert len(m.contracts) >= 3
    assert any(c["check"]["type"] == "equal" for c in m.contracts), "needs a conservation check"
    prov = m.data["provenance"]
    assert prov["sources"] and prov["data"]["acquisition"] in (
        "generic", "estimate", "datasheet", "digitized", "measured", "standard"
    )  # fmt: skip
    assert m.data["fidelity"]["assumptions"]


@pytest.mark.parametrize("mid", IDS)
def test_implementation_is_importable(mid: str) -> None:
    cls = wp.default_catalog().implementation(mid)
    assert issubclass(cls, Component)
    assert cls.manifest.id == mid


@pytest.mark.parametrize("mid", IDS)
def test_defaults_instantiate_and_describe(mid: str) -> None:
    m = manifest(mid)
    s = wp.System("defaults")
    s.add("dut", mid)
    paths = {v.path for v in s.variables()}
    for name in m.observables:
        assert f"dut.{name}" in paths
    for port in m.ports:
        assert f"dut.{port}.p" in paths


@pytest.mark.parametrize(("mid", "scenario"), SCENARIOS, ids=[f"{m}:{s}" for m, s in SCENARIOS])
def test_scenario_passes(mid: str, scenario: str) -> None:
    m = manifest(mid)
    out = wp.run_scenario(m, scenario)
    assert out.passed, "\n".join(out.failures)
    _assert_declared(out.warnings)


@pytest.mark.parametrize(("mid", "contract"), CONTRACTS, ids=[f"{m}:{c}" for m, c in CONTRACTS])
def test_contract_passes(mid: str, contract: str) -> None:
    m = manifest(mid)
    out = wp.run_contract(m, contract)
    assert out.passed, "\n".join(out.failures)
    assert out.points >= 1
    _assert_declared(out.warnings)


def _assert_declared(seen: set[tuple[str, str]]) -> None:
    """Every warning code emitted during a run is declared in its component's manifest."""
    cat = wp.default_catalog()
    for component_id, code in seen:
        assert code in cat.get(component_id).warnings, f"{component_id} emitted {code}"
