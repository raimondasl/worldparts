"""Catalogue: discovery, lookup and search of component manifests.

The package catalogue lives in ``worldparts/catalog/**/*.yaml``. Extra directories can be
added (``load_catalog(extra_dirs=[...])``) and manifests can be registered programmatically
with an explicit implementation class, which is how tests add components that are not part
of the package.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Iterator
from functools import cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worldparts.errors import ManifestError, UnknownComponentError, format_choices
from worldparts.manifest import Manifest, load_yaml

if TYPE_CHECKING:
    from worldparts.components.base import Component

__all__ = ["Catalog", "default_catalog", "load_catalog", "package_manifest_paths"]


def package_manifest_paths() -> list[Any]:
    """Traversables of every manifest YAML shipped in the package, sorted by name."""
    root = resources.files("worldparts").joinpath("catalog")
    out: list[Any] = []

    def walk(node: Any) -> None:
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            if child.is_dir():
                walk(child)
            elif child.name.endswith((".yaml", ".yml")):
                out.append(child)

    if root.is_dir():
        walk(root)
    return out


class Catalog:
    """A set of manifests addressable by full id or short alias."""

    def __init__(self, manifests: Iterable[Manifest] = ()) -> None:
        self._by_id: dict[str, Manifest] = {}
        self._classes: dict[str, type[Component]] = {}
        for m in manifests:
            self.add(m)

    # -- building ---------------------------------------------------------------------------
    def add(self, manifest: Manifest, implementation: type[Component] | None = None) -> None:
        """Add a manifest, optionally with an explicit implementation class.

        Raises:
            ManifestError: When another manifest already uses the same id.
        """
        existing = self._by_id.get(manifest.id)
        if existing is not None and existing is not manifest:
            raise ManifestError(
                f"Duplicate component id '{manifest.id}' "
                f"({existing.path or 'registered'} and {manifest.path or 'registered'})."
            )
        self._by_id[manifest.id] = manifest
        self._classes.pop(manifest.id, None)
        if implementation is not None:
            self._classes[manifest.id] = self._bind(manifest, implementation)

    def add_directory(self, directory: str | Path) -> list[Manifest]:
        """Load every ``*.yaml`` manifest below ``directory`` and add it."""
        added = []
        for path in sorted(Path(directory).rglob("*.y*ml")):
            data = load_yaml(path.read_text(encoding="utf-8"))
            m = Manifest.from_dict(data, path)
            self.add(m)
            added.append(m)
        return added

    # -- lookup -----------------------------------------------------------------------------
    def __contains__(self, key: object) -> bool:
        try:
            self.get(str(key))
        except UnknownComponentError:
            return False
        return True

    def __iter__(self) -> Iterator[Manifest]:
        return iter(sorted(self._by_id.values(), key=lambda m: m.id))

    def __len__(self) -> int:
        return len(self._by_id)

    def ids(self) -> list[str]:
        """All full ids, sorted."""
        return sorted(self._by_id)

    def aliases(self) -> dict[str, list[str]]:
        """Alias to the list of ids that share it."""
        out: dict[str, list[str]] = {}
        for mid in sorted(self._by_id):
            out.setdefault(mid.rsplit(".", 1)[-1], []).append(mid)
        return out

    def get(self, key: str, *, list_valid: bool = True) -> Manifest:
        """Resolve a full id or a short alias.

        Raises:
            UnknownComponentError: Naming close matches and listing valid ids and aliases
                (only the close matches when ``list_valid`` is False; see
                :meth:`choices`).
        """
        if key in self._by_id:
            return self._by_id[key]
        matches = self.aliases().get(key, [])
        if len(matches) == 1:
            return self._by_id[matches[0]]
        if len(matches) > 1:
            raise UnknownComponentError(
                f"Component alias '{key}' is ambiguous; use a full id: {', '.join(matches)}."
            )
        hint = format_choices(key, self.choices(), list_valid=list_valid)
        raise UnknownComponentError(f"Unknown component type '{key}'. {hint}".rstrip())

    def choices(self) -> list[str]:
        """Every key :meth:`get` accepts: the aliases, then the full ids."""
        return list(self.aliases()) + self.ids()

    def search(self, query: str | None = None) -> list[Manifest]:
        """Manifests whose id, name, summary, description or tags contain every query word."""
        items = list(self)
        if not query:
            return items
        words = [w.lower() for w in query.split() if w.strip()]
        scored = []
        for m in items:
            d = m.data
            hay = " ".join(
                [m.id, m.name, m.summary, d.get("description", ""), " ".join(d.get("tags", []))]
            ).lower()
            if all(w in hay for w in words):
                head = f"{m.id} {m.name} {' '.join(d.get('tags', []))}".lower()
                score = sum(w in head for w in words)
                scored.append((-score, m.id, m))
        return [m for _, _, m in sorted(scored, key=lambda t: (t[0], t[1]))]

    def implementation(self, key: str | Manifest) -> type[Component]:
        """The implementation class for a component, with its manifest bound.

        The class named in ``implementations.reference.python`` is imported and a subclass
        with ``manifest`` set is returned (cached), so one Python class can serve several
        manifests without shared mutable state.

        Raises:
            ManifestError: When the class cannot be imported or is not a Component.
        """
        m = key if isinstance(key, Manifest) else self.get(key)
        cached = self._classes.get(m.id)
        if cached is not None:
            return cached
        module_name, _, cls_name = m.implementation.partition(":")
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, cls_name)
        except (ImportError, AttributeError) as exc:
            raise ManifestError(
                f"{m.id}: cannot import implementation '{m.implementation}': {exc}"
            ) from exc
        bound = self._bind(m, cls)
        self._classes[m.id] = bound
        return bound

    @staticmethod
    def _bind(manifest: Manifest, cls: type[Any]) -> type[Component]:
        from worldparts.components.base import Component

        if not (isinstance(cls, type) and issubclass(cls, Component)):
            raise ManifestError(
                f"{manifest.id}: implementation {cls!r} is not a subclass of "
                "worldparts.components.base.Component."
            )
        return type(cls.__name__, (cls,), {"manifest": manifest, "__module__": cls.__module__})


def load_catalog(extra_dirs: Iterable[str | Path] = (), include_package: bool = True) -> Catalog:
    """Build a catalogue from the package manifests and optional extra directories.

    Args:
        extra_dirs: Directories searched recursively for ``*.yaml`` manifests.
        include_package: Whether to include the packaged catalogue.

    Returns:
        A new :class:`Catalog`.
    """
    cat = Catalog()
    if include_package:
        for trav in package_manifest_paths():
            data = load_yaml(trav.read_text(encoding="utf-8"))
            path = Path(str(trav))
            cat.add(Manifest.from_dict(data, path))
    for d in extra_dirs:
        cat.add_directory(d)
    return cat


@cache
def default_catalog() -> Catalog:
    """The package catalogue (loaded once, then shared; treat it as read-only)."""
    return load_catalog()
