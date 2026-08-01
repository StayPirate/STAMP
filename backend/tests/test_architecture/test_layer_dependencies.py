"""Structural tests enforcing the Backend Layer Architecture dependency
direction defined in `docs/architecture.md`.

Each layer may only import from the layers listed in its "May depend
on" column of the Backend Layer Architecture table. Imports of shared
foundational modules that are not part of the seven-layer table
(`app.config`, `app.database`, the `app.main` entry point) are always
allowed — they are leaf infrastructure, not one of the layers whose
direction this test enforces.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# Directory name (relative to APP_ROOT) -> dotted module prefix, for
# each layer in the Backend Layer Architecture table.
LAYER_DIRS: dict[str, str] = {
    "api": "app.api",
    "cli": "app.cli",
    "services": "app.services",
    "models": "app.models",
    "schemas": "app.schemas",
    "core": "app.core",
    "tasks": "app.tasks",
}

# The "May depend on" column of the Backend Layer Architecture table.
# A layer may always import from itself (intra-layer imports); that is
# enforced separately and not repeated here.
ALLOWED_DEPENDENCIES: dict[str, set[str]] = {
    "app.api": {"app.services", "app.schemas", "app.models", "app.core"},
    "app.cli": {"app.services", "app.models", "app.core"},
    "app.services": {"app.models", "app.core"},
    "app.models": {"app.core"},
    "app.schemas": {"app.models", "app.core"},
    "app.core": set(),
    "app.tasks": {"app.services", "app.core"},
}


def _iter_python_files(directory: Path) -> list[Path]:
    """Every `.py` file under `directory`, or an empty list if the
    layer directory does not exist yet (e.g. `app/cli/` has not been
    created yet — the test then simply has nothing to check for it).
    """
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.py"))


def _containing_package(file_path: Path) -> str:
    """The dotted package path containing `file_path` — its `__package__`
    value if imported as part of the `app` package. Used to resolve
    relative imports (`from . import x`, `from .. import x`) to an
    absolute `app.*` module path. This is the dotted path of the file's
    parent directory, which is correct both for a regular module and
    for a package's `__init__.py`.
    """
    relative_dir = file_path.relative_to(APP_ROOT.parent).parent
    return ".".join(relative_dir.parts)


def _resolve_relative_import(package: str, node: ast.ImportFrom) -> str | None:
    """Resolve a relative `ast.ImportFrom` (`node.level > 0`) to an
    absolute dotted module path, following the same algorithm Python's
    import system uses (see `importlib._bootstrap._resolve_name`).
    Returns `None` if the relative import climbs above the top-level
    package (invalid, would fail at runtime — not this test's concern).
    """
    bits = package.rsplit(".", node.level - 1)
    if len(bits) < node.level:
        return None
    base = bits[0]
    return f"{base}.{node.module}" if node.module else base


def _imported_app_modules(source: str, package: str) -> set[str]:
    """Every `app.*` module path imported anywhere in `source`.

    Uses a full AST walk (`ast.walk`, not just top-level statements) so
    that imports guarded by `if TYPE_CHECKING:` or nested inside
    functions are still caught — a cross-layer coupling is a violation
    whether it is a runtime dependency or a type-only one. Relative
    imports (`from . import x`, `from .. import x`) are resolved to
    their absolute form using `package` (the dotted path of the
    importing file's own package) before the `app.` prefix check, so a
    cross-layer relative import cannot silently bypass this test.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = (
                _resolve_relative_import(package, node)
                if node.level > 0
                else node.module
            )
            if module and module.startswith("app."):
                modules.add(module)
    return modules


def _layer_of(module: str) -> str | None:
    """The layer prefix (e.g. `app.services`) `module` belongs to, or
    `None` if `module` is outside the seven-layer table — shared
    infrastructure such as `app.config` or `app.database`, which is
    always allowed regardless of the importing layer.
    """
    for prefix in LAYER_DIRS.values():
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _iter_layer_violations() -> list[str]:
    violations: list[str] = []
    for dir_name, layer_prefix in LAYER_DIRS.items():
        for path in _iter_python_files(APP_ROOT / dir_name):
            source = path.read_text(encoding="utf-8")
            package = _containing_package(path)
            for module in _imported_app_modules(source, package):
                imported_layer = _layer_of(module)
                if imported_layer is None:
                    continue  # shared infrastructure, always allowed
                if imported_layer == layer_prefix:
                    continue  # intra-layer import, always allowed
                if imported_layer not in ALLOWED_DEPENDENCIES[layer_prefix]:
                    violations.append(
                        f"{path.relative_to(APP_ROOT.parent)}: layer "
                        f"'{layer_prefix}' must not import from layer "
                        f"'{imported_layer}' (imports '{module}')"
                    )
    return violations


@pytest.mark.unit
class TestLayerDependencyDirection:
    """Enforce the dependency direction of the Backend Layer
    Architecture table (`docs/architecture.md`).
    """

    def test_no_layer_imports_a_disallowed_layer(self) -> None:
        violations = _iter_layer_violations()
        assert not violations, "Layer dependency violations found:\n" + "\n".join(
            violations
        )
