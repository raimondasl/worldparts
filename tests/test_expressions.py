"""Tests for the safe expression language."""

from __future__ import annotations

import math

import pytest

from worldparts.errors import ExpressionError, UnknownVariableError
from worldparts.expressions import compile_expression, evaluate, is_true

ENV = {
    "opening": 0.5,
    "port_a.p": 3.0,
    "dut.hot.temperature": 55.0,
    "flow": 12.0,
    "zero": 0.0,
    "neg": -4.0,
    "undef": None,
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("2 ** 3", 8),
        ("-opening", -0.5),
        ("+opening", 0.5),
        ("(1 + 2) * 3", 9),
        ("flow / 4", 3.0),
        ("port_a.p - 1", 2.0),
        ("dut.hot.temperature > 49", True),
        ("0 < opening < 1", True),
        ("0 < opening < 0.2", False),
        ("opening <= 0.5 and flow >= 12", True),
        ("opening > 0.9 or flow == 12", True),
        ("not opening > 0.9", True),
        ("flow != 12", False),
        ("abs(neg)", 4.0),
        ("min(flow, 3, opening)", 0.5),
        ("max(flow, 3)", 12.0),
        ("sqrt(flow * 3)", 6.0),
        ("true", True),
        ("false", False),
        ("1e-3 * 1000", 1.0),
    ],
)
def test_evaluate(text: str, expected: object) -> None:
    assert evaluate(text, ENV) == pytest.approx(expected)


def test_names_are_collected() -> None:
    expr = compile_expression("abs(dut.flow) > 3 and port_a.p < max(x, 1) or true")
    assert expr.names == {"dut.flow", "port_a.p", "x"}


@pytest.mark.parametrize(
    "text",
    [
        "undef > 3",
        "undef + 1",
        "abs(undef)",
        "undef > 3 or true",  # any None operand makes the whole expression None
        "flow / zero",
        "sqrt(neg)",
        "neg ** 0.5",
    ],
)
def test_undefined_results_are_none(text: str) -> None:
    assert evaluate(text, ENV) is None
    assert is_true(evaluate(text, ENV)) is False


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "open('x')",
        "flow.__class__",
        "x[0]",
        "lambda: 1",
        "'text'",
        "flow if true else 0",
        "[1, 2]",
        "flow % 2",
        "flow // 2",
        "abs(x=1)",
        "f(flow)",
        "(flow).real()",
        "a = 1",
        "1 in x",
    ],
)
def test_forbidden_constructs(text: str) -> None:
    with pytest.raises(ExpressionError):
        compile_expression(text)


def test_syntax_error() -> None:
    with pytest.raises(ExpressionError, match="Invalid expression"):
        compile_expression("flow >")


def test_unknown_name_lists_alternatives() -> None:
    with pytest.raises(UnknownVariableError) as exc:
        evaluate("flwo > 1", ENV)
    assert "flow" in str(exc.value)


def test_is_true() -> None:
    assert is_true(True) and is_true(1.0)
    assert not is_true(False) and not is_true(0) and not is_true(None)


def test_power_is_evaluated_in_floating_point() -> None:
    assert evaluate("9 ** 9 ** 9 > 1", {}) is None  # overflow, not a hang
    assert evaluate("2 ** 0.5", {}) == pytest.approx(math.sqrt(2))
    assert evaluate("(-8) ** (1 / 3)", {}) is None  # complex result
