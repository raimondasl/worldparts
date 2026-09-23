"""Adapters that carry worldparts systems to other simulation hosts (design section 11).

Adapters depend on optional packages, so this package imports none of them. Import the
adapter module you need, e.g. :mod:`worldparts.adapters.wntr_adapter` (the ``wntr``
package: ``uv add wntr``, or the extra,
``uv add "worldparts[wntr] @ git+https://github.com/raimondasl/worldparts"``); its functions
raise :class:`~worldparts.adapters.MissingDependencyError` with the install command when the
optional package is missing.
"""

from __future__ import annotations

import importlib.util

from worldparts.errors import WorldpartsError

__all__ = ["MissingDependencyError", "wntr_available"]


class MissingDependencyError(WorldpartsError):
    """An optional dependency an adapter needs is not installed."""

    code = "missing_dependency"


def wntr_available() -> bool:
    """True when the optional ``wntr`` package can be imported (without importing it)."""
    return importlib.util.find_spec("wntr") is not None
