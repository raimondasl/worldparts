"""Command-line interface tests (``worldparts.cli.main`` and one subprocess run)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import worldparts as wp
from worldparts import cli
from worldparts.contracts import CheckOutcome, ComponentReport

REPO = Path(__file__).resolve().parents[1]

LINE: dict[str, Any] = {
    "worldparts_system": "0.1",
    "name": "line",
    "components": [
        {"name": "mains", "type": "supply", "parameters": {"pressure": "3 bar"}},
        {"name": "p", "type": "pipe", "parameters": {"length": 10}},
        {"name": "v", "type": "valve", "inputs": {"opening": 0.5}},
        {"name": "out", "type": "drain"},
    ],
    "connections": [["mains.port", "p.port_a"], ["p.port_b", "v.port_a"], ["v.port_b", "out.port"]],
    "simulation": {
        "duration": "20 s",
        "step": "1 s",
        "events": [{"at": "10 s", "set": {"v.opening": 0}}],
    },
}


@pytest.fixture
def line_file(tmp_path: Path) -> Path:
    path = tmp_path / "line.yaml"
    path.write_text(yaml.safe_dump(LINE, sort_keys=False), encoding="utf-8")
    return path


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def expected_flow() -> float:
    return wp.System.from_dict(LINE).solve()["v.volume_flow"]  # type: ignore[return-value]


# ----------------------------------------------------------------------------------------
# catalogue commands
# ----------------------------------------------------------------------------------------
def test_list(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "list")
    assert code == 0
    assert out.splitlines()[0].split() == ["alias", "id", "name", "ports"]
    for alias in ("supply", "drain", "pipe", "valve", "check_valve"):
        assert f"worldparts.hydraulic.{alias}" in out

    code, out, _ = run_cli(capsys, "list", "check", "valve", "--json")
    assert code == 0
    ids = [c["id"] for c in json.loads(out)]
    assert "worldparts.hydraulic.check_valve" in ids

    code, out, _ = run_cli(capsys, "list", "flux-capacitor")
    assert code == 0 and "No component matches" in out


def test_describe(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "describe", "valve")
    assert code == 0
    assert out.startswith("worldparts.hydraulic.valve")
    for section in ("Ports", "Parameters", "Inputs", "States", "Observables", "Modes"):
        assert f"\n{section}" in out
    assert "high_pressure_drop" in out and "[0.0001, 100000]" in out
    assert "bar (difference)" in out

    code, out, _ = run_cli(capsys, "describe", "check_valve", "--json")
    assert code == 0 and json.loads(out)["id"] == "worldparts.hydraulic.check_valve"


def test_unknown_component_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run_cli(capsys, "describe", "valv")
    assert code == 2 and out == ""
    assert "Unknown component type 'valv'" in err and "Did you mean 'valve'" in err


def test_check_catalog_one_component(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "check-catalog", "--component", "valve")
    assert code == 0
    assert "PASS worldparts.hydraulic.valve" in out and "1 of 1 component(s) passed" in out

    code, out, _ = run_cli(capsys, "check-catalog", "--component", "drain", "--json", "-v")
    data = json.loads(out)
    assert code == 0 and data["passed"] is True
    assert data["components"][0]["component"] == "worldparts.hydraulic.drain"

    code, out, _ = run_cli(capsys, "check-catalog", "--component", "pipe", "--verbose")
    assert code == 0 and "ok   scenario" in out


def test_check_catalog_fails_nonzero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = ComponentReport(
        "worldparts.hydraulic.valve",
        [CheckOutcome("scenario", "kv-at-1-bar", False, ["dut.volume_flow: expected 41.7"])],
    )
    monkeypatch.setattr(wp, "check_catalog", lambda **_: [failing])
    code, out, _ = run_cli(capsys, "check-catalog")
    assert code == 1
    assert "FAIL worldparts.hydraulic.valve" in out and "expected 41.7" in out
    assert "Failed: worldparts.hydraulic.valve" in out
    code, out, _ = run_cli(capsys, "check-catalog", "--json")
    assert code == 1 and json.loads(out)["passed"] is False


# ----------------------------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------------------------
def test_validate_package_catalogue(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "validate", "--json")
    data = json.loads(out)
    core = [f for f in data["files"] if Path(f["path"]).stem in ("supply", "valve", "pipe")]
    assert len(core) == 3 and all(f["valid"] and f["kind"] == "manifest" for f in core)
    # Other manifests may be work in progress; the exit code reflects all of them.
    assert code == (0 if data["valid"] else 1)


def test_validate_files(
    capsys: pytest.CaptureFixture[str], line_file: Path, tmp_path: Path
) -> None:
    code, out, _ = run_cli(capsys, "validate", str(line_file))
    assert code == 0 and "OK   system" in out and "1 of 1 file(s) valid" in out

    bad_system = dict(LINE, components=[*LINE["components"], {"name": "x", "type": "valv"}])
    (tmp_path / "bad_system.yaml").write_text(yaml.safe_dump(bad_system), encoding="utf-8")
    manifest = yaml.safe_load(
        (REPO / "src/worldparts/catalog/hydraulic/valve.yaml").read_text(encoding="utf-8")
    )
    manifest["parameters"][0]["default"] = -1  # below its minimum
    (tmp_path / "bad_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (tmp_path / "other.json").write_text('{"hello": 1}', encoding="utf-8")

    code, out, _ = run_cli(capsys, "validate", str(tmp_path), "--json")
    assert code == 1
    reports = {Path(r["path"]).name: r for r in json.loads(out)["files"]}
    assert reports["line.yaml"]["valid"]
    assert not reports["bad_system.yaml"]["valid"]
    assert "unknown_component" in reports["bad_system.yaml"]["problems"][0]
    assert not reports["bad_manifest.yaml"]["valid"]
    assert reports["bad_manifest.yaml"]["kind"] == "manifest"
    assert any("kv" in p for p in reports["bad_manifest.yaml"]["problems"])
    assert reports["other.json"]["kind"] == "unknown" and not reports["other.json"]["valid"]

    code, out, _ = run_cli(capsys, "validate", str(tmp_path / "missing.yaml"))
    assert code == 1 and "Cannot read" in out


# ----------------------------------------------------------------------------------------
# solve and simulate
# ----------------------------------------------------------------------------------------
def test_solve_table(capsys: pytest.CaptureFixture[str], line_file: Path) -> None:
    code, out, _ = run_cli(capsys, "solve", str(line_file))
    assert code == 0
    assert "converged" in out
    row = next(line for line in out.splitlines() if line.strip().startswith("v.volume_flow"))
    assert row.split()[1] == f"{expected_flow():.6g}" and row.split()[2] == "L/min"
    assert "mains.port.p       3" in out and "bar (gauge)" in out
    assert "v.kv" not in out  # parameters are not in the default selection
    assert "throttling" in out


def test_solve_json_and_var(capsys: pytest.CaptureFixture[str], line_file: Path) -> None:
    code, out, _ = run_cli(capsys, "solve", str(line_file), "--json")
    data = json.loads(out)
    assert code == 0 and data["converged"] is True
    assert "v.kv" in data["values"]  # --json without --var gives everything
    assert data["values"]["v.volume_flow"]["value"] == pytest.approx(expected_flow())

    code, out, _ = run_cli(
        capsys, "solve", str(line_file), "--json", "--var", "v.volume_flow,mains.port.p"
    )
    data = json.loads(out)
    assert list(data["values"]) == ["v.volume_flow", "mains.port.p"]
    assert data["values"]["mains.port.p"]["reference"] == "gauge"

    code, out, err = run_cli(capsys, "solve", str(line_file), "--var", "v.flow")
    assert code == 2 and "Unknown variable 'v.flow'" in err and "v.volume_flow" in err


def test_solve_reports_check_errors(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    doc = dict(LINE, components=LINE["components"][1:3], connections=[["p.port_b", "v.port_a"]])
    path = tmp_path / "floating.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    code, out, err = run_cli(capsys, "solve", str(path))
    assert code == 2 and out == ""
    assert "no_pressure_reference" in err


def test_simulate(capsys: pytest.CaptureFixture[str], line_file: Path) -> None:
    code, out, _ = run_cli(capsys, "simulate", str(line_file), "--var", "v")
    assert code == 0
    assert "simulated 20 s in 21 samples" in out
    row = next(line for line in out.splitlines() if line.strip().startswith("v.opening"))
    assert row.split()[1:4] == ["0", "0.5", "0"]  # min, max, final: the event closed it
    assert "closed" in out  # mode change at 10 s

    code, out, _ = run_cli(
        capsys,
        "simulate",
        str(line_file),
        "--duration",
        "15 s",
        "--step",
        "0.5 s",
        "--var",
        "v.volume_flow",
        "--json",
        "--max-points",
        "4",
    )
    data = json.loads(out)
    assert code == 0
    assert data["time"][-1] == 15.0 and len(data["time"]) == 4
    assert list(data["series"]) == ["v.volume_flow"]
    summary = data["summary"]["v.volume_flow"]
    assert summary["max"] == pytest.approx(expected_flow()) and summary["final"] < 0.01

    # The document's events still apply, so a duration that ends before them is rejected.
    code, _, err = run_cli(capsys, "simulate", str(line_file), "--duration", "5 s")
    assert code == 2 and "events[0]" in err


def test_simulate_needs_a_duration(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    doc = {k: v for k, v in LINE.items() if k != "simulation"}
    path = tmp_path / "nosim.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, _, err = run_cli(capsys, "simulate", str(path))
    assert code == 2 and "duration" in err


def test_select_paths_helpers() -> None:
    s = wp.System.from_dict(LINE)
    default = cli.select_paths(s, None)
    assert "v.volume_flow" in default and "v.position" in default and "out.port.p" in default
    assert "v.kv" not in default and "v.port_a.T" not in default
    assert cli.select_paths(s, ["v.kv", "v.kv"]) == ["v.kv"]
    assert "v.characteristic" not in cli.select_paths(s, ["*"])
    with pytest.raises(wp.UnknownVariableError, match="string or table parameter"):
        cli.select_paths(s, ["v.characteristic"])


def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert wp.__version__ in capsys.readouterr().out


# ----------------------------------------------------------------------------------------
# subprocess
# ----------------------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("uv") is None, reason="needs 'uv' on PATH")
def test_uv_run_worldparts_list() -> None:
    proc = subprocess.run(
        [str(shutil.which("uv")), "run", "--no-sync", "worldparts", "list"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "worldparts.hydraulic.valve" in proc.stdout
