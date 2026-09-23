"""Shared fixtures for the worldparts test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import worldparts as wp

TESTS_DIR = Path(__file__).parent
FIXTURE_CATALOG = TESTS_DIR / "fixtures" / "catalog"

# The test-only component module (testparts.py) is imported by path from its manifest.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


@pytest.fixture(scope="session")
def catalog() -> wp.Catalog:
    """The package catalogue."""
    return wp.default_catalog()


@pytest.fixture(scope="session")
def test_catalog() -> wp.Catalog:
    """The package catalogue plus the test-only components in tests/fixtures/catalog."""
    return wp.load_catalog(extra_dirs=[FIXTURE_CATALOG])
