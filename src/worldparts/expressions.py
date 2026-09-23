"""A small, safe expression language for modes, envelopes and contracts (design section 3.4).

Supported: int and float literals, ``true``/``false``, dotted names (``port_a.p``,
``dut.flow``), ``+ - * / **``, unary minus and plus, comparisons (chained allowed), ``and``,
``or``, ``not``, parentheses and the functions ``abs``, ``min``, ``max`` and ``sqrt``.

Expressions are parsed with :mod:`ast` and only whitelisted node types are accepted. Values
are evaluated over a mapping from names to numbers (display units). Any ``None`` operand makes
the whole expression ``None``; callers treat a ``None`` condition as false. Division by zero,
invalid square roots and overflow also give ``None``. ``**`` is evaluated in floating point,
so integer power towers such as ``9 ** 9 ** 9`` overflow to ``None`` instead of hanging.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

from worldparts.errors import ExpressionError, UnknownVariableError, format_choices

__all__ = ["Expression", "compile_expression", "evaluate", "is_true"]

Value = float | bool | None


class _Undefined(Exception):
    """Raised internally when an operand is None."""


_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_CMPOPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


def _sqrt(x: float) -> float:
    if x < 0:
        raise _Undefined
    return math.sqrt(x)


_FUNCS: dict[str, Callable[..., Any]] = {"abs": abs, "min": min, "max": max, "sqrt": _sqrt}
_CONSTS = {"true": True, "false": False, "True": True, "False": False}


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


class Expression:
    """A compiled expression.

    Attributes:
        text: The source text.
        names: Every variable name the expression references.
    """

    def __init__(self, text: str) -> None:
        self.text = str(text)
        try:
            tree = ast.parse(self.text.strip(), mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"Invalid expression '{self.text}': {exc.msg}.") from None
        names: set[str] = set()
        self._validate(tree.body, names)
        self._tree = tree.body
        self.names: frozenset[str] = frozenset(names)

    def _validate(self, node: ast.AST, names: set[str]) -> None:
        bad = f"Expression '{self.text}' uses an unsupported construct"
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (bool, int, float)):
                return
            raise ExpressionError(f"{bad}: literal {node.value!r}. Only numbers are allowed.")
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = _dotted(node)
            if name is None:
                raise ExpressionError(f"{bad}: attribute access on a non-name.")
            if any(part.startswith("__") for part in name.split(".")):
                raise ExpressionError(f"{bad}: dunder name '{name}'.")
            if name not in _CONSTS:
                names.add(name)
            return
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS:
                raise ExpressionError(f"{bad}: operator {type(node.op).__name__}.")
            self._validate(node.left, names)
            self._validate(node.right, names)
            return
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
                raise ExpressionError(f"{bad}: unary {type(node.op).__name__}.")
            self._validate(node.operand, names)
            return
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self._validate(v, names)
            return
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if type(op) not in _CMPOPS:
                    raise ExpressionError(f"{bad}: comparison {type(op).__name__}.")
            self._validate(node.left, names)
            for c in node.comparators:
                self._validate(c, names)
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ExpressionError(
                    f"{bad}: only the functions {', '.join(sorted(_FUNCS))} may be called."
                )
            if node.keywords or not node.args:
                raise ExpressionError(f"{bad}: {node.func.id}() takes positional arguments.")
            for a in node.args:
                self._validate(a, names)
            return
        raise ExpressionError(
            f"{bad}: {type(node).__name__}. Allowed: numbers, names, + - * / **, comparisons, "
            "and, or, not, parentheses, abs(), min(), max(), sqrt()."
        )

    def __repr__(self) -> str:
        return f"Expression({self.text!r})"

    def evaluate(self, env: Mapping[str, Any]) -> Value:
        """Evaluate over ``env`` (name to value).

        Returns:
            A number, a boolean, or None when any operand is None or the result is undefined.

        Raises:
            UnknownVariableError: When a name is missing from ``env``.
        """
        try:
            return self._eval(self._tree, env)
        except _Undefined:
            return None
        except ZeroDivisionError:
            return None
        except OverflowError:
            return None

    def _eval(self, node: ast.AST, env: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = _dotted(node)
            assert name is not None
            if name in _CONSTS:
                return _CONSTS[name]
            if name not in env:
                raise UnknownVariableError(
                    f"Expression '{self.text}' references unknown name '{name}'. "
                    + format_choices(name, env.keys())
                )
            value = env[name]
            if value is None:
                raise _Undefined
            if not isinstance(value, (bool, int, float)):
                raise _Undefined
            return value
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, env)
            right = self._eval(node.right, env)
            if isinstance(node.op, ast.Pow):
                # Floating point keeps power towers bounded (overflow gives None) instead of
                # computing huge exact integers.
                left, right = float(left), float(right)
            result = _BINOPS[type(node.op)](left, right)
            if isinstance(result, complex):
                raise _Undefined
            return result
        if isinstance(node, ast.UnaryOp):
            v = self._eval(node.operand, env)
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return +v
            return not v
        if isinstance(node, ast.BoolOp):
            # Evaluate every operand so that a None anywhere makes the result None.
            values = [self._eval(v, env) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(bool(v) for v in values)
            return any(bool(v) for v in values)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            rights = [self._eval(c, env) for c in node.comparators]
            ok = True
            for op, right in zip(node.ops, rights, strict=True):
                ok = ok and _CMPOPS[type(op)](left, right)
                left = right
            return ok
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            args = [self._eval(a, env) for a in node.args]
            return _FUNCS[node.func.id](*args)
        raise ExpressionError(f"Cannot evaluate {type(node).__name__} in '{self.text}'.")


@lru_cache(maxsize=4096)
def compile_expression(text: str) -> Expression:
    """Parse and validate an expression (cached).

    Raises:
        ExpressionError: For syntax errors or unsupported constructs.
    """
    return Expression(str(text))


def evaluate(text: str, env: Mapping[str, Any]) -> Value:
    """Compile (cached) and evaluate ``text`` over ``env``."""
    return compile_expression(text).evaluate(env)


def is_true(value: Value) -> bool:
    """Return the truth of a condition result; None counts as false."""
    return value is not None and bool(value)
