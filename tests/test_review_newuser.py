"""Defects found by following the README as a new user (fresh uv project, local install).

Each test states the documented or expected behaviour and fails while the defect exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import worldparts.adapters as adapters
from worldparts import cli

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_readme_python_heading_matches_the_example_length() -> None:
    """The heading promises "Python in N lines"; the block under it must not be longer."""
    text = _readme()
    heading = re.search(r"^## Python(?: in (\d+) lines)?[^\n]*$", text, re.M)
    assert heading is not None, "README has no Python section"
    block = re.search(r"```python\n(.*?)```", text[heading.end() :], re.S)
    assert block is not None
    code_lines = [line for line in block[1].splitlines() if line.strip()]
    claimed = int(heading[1]) if heading[1] else len(code_lines)
    assert len(code_lines) <= claimed, (
        f"README heading says {heading[1]} lines but the example has {len(code_lines)} "
        "non-blank lines"
    )


def test_cli_simulate_without_duration_names_the_cli_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A document without a `simulation` block needs --duration; the CLI error should say so.

    Today the CLI prints the Python API message ("simulate() needs a duration ..."), which
    does not tell a command-line user which option to pass.
    """
    doc = {
        "worldparts_system": "0.1",
        "name": "line",
        "components": [
            {"name": "mains", "type": "supply", "parameters": {"pressure": "3 bar"}},
            {"name": "v", "type": "valve"},
            {"name": "out", "type": "drain"},
        ],
        "connections": [["mains.port", "v.port_a"], ["v.port_b", "out.port"]],
    }
    path = tmp_path / "line.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    code = cli.main(["simulate", str(path)])
    out, err = capsys.readouterr()
    assert code == 2
    assert "--duration" in out + err, (out + err).strip()


def test_missing_wntr_hint_is_a_command_that_works_as_documented(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install hint must be copy-pasteable in every common shell and match the README.

    `pip install worldparts[wntr]` without quotes fails in zsh ("no matches found"), and the
    README installs worldparts only from git, never from an index.
    """
    monkeypatch.setattr(adapters, "wntr_available", lambda: False)
    path = tmp_path / "line.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "worldparts_system": "0.1",
                "name": "line",
                "components": [{"name": "out", "type": "drain"}],
            }
        ),
        encoding="utf-8",
    )
    code = cli.main(["export", str(path), "--target", "wntr_inp"])
    out, err = capsys.readouterr()
    message = out + err
    assert code == 2
    assert "install worldparts[wntr]" not in message, message.strip()
