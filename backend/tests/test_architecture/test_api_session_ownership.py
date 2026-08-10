"""Structural test enforcing that API route handlers acquire their
database session exclusively through the `DatabaseSession` dependency.

See `docs/conventions.md` (API Transaction Dependency Scope): every API
transaction session must go through `get_db()` declared with
`scope="function"` (the `DatabaseSession` alias in `app/database.py`),
so its commit/rollback exit phase completes before the response is
transmitted. `backend/tests/test_api_conventions.py`
(`TestTransactionDependencyScope`) already guards every *registered
route dependency* that uses `get_db`. This module guards the narrower,
complementary bypass a route handler could otherwise use to sidestep
that dependency entirely: opening an independent session directly via
`async_session_factory`, or completing a transaction itself via
`.commit()`/`.rollback()` — both would escape FastAPI's dependency
injection graph and are therefore invisible to a route-dependant walk.

Scope: this test detects direct references to `async_session_factory` and
direct `.commit()`/`.rollback()` calls in `app/api/` source text via AST
— it does not attempt full type inference to confirm a `.commit()`
receiver is actually a database session, since no legitimate use of
either pattern exists in the API layer today (see `AGENTS.md`,
Guardrail 26 — Reviewer Proportionality). Two modules are exempt, each
for a documented, specification-mandated reason:

- `app/api/health.py`: the readiness probe (`GET /ready`) never uses the
  `get_db` yield-dependency at all — it opens a read-only, no-commit
  session directly via `get_readiness_session_factory()` for its
  `SELECT 1` check, so the `scope="function"` rule (which governs
  transaction completion ordering for `get_db`) does not apply to it in
  the first place.
- `app/api/dependencies.py`: the API key `last_used_at` debounce touch
  (`LastUsedDebouncer.touch()`) is an orchestration boundary per
  `docs/conventions.md` (Transaction and Locking, Caller-Owned Service
  Transactions: "a component that explicitly owns its sessions ...
  keeps the transaction contract defined by its owning specification").
  `docs/features/identity/authentication.md` (API key validation, step
  5) requires this best-effort operational write to commit or roll back
  in its own dedicated transaction, independently of the request's main
  `DatabaseSession` transaction (which may still be open and must not be
  affected by this write's outcome).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_API_ROOT = Path(__file__).resolve().parents[2] / "app" / "api"

# `app/api/health.py` is the one exception: the readiness probe never
# uses the `get_db` yield-dependency — it opens its own read-only,
# no-commit session directly via `get_readiness_session_factory()`, so
# the `scope="function"` transaction-completion rule does not apply to
# it. `app/api/dependencies.py` is the second exception: its debounced
# API key `last_used_at` touch owns a dedicated transaction per
# `authentication.md` (see module docstring above for the full
# rationale). Matched by path relative to `APP_API_ROOT` (not by
# basename alone), so a differently-located future module that happens
# to share either name is not accidentally exempted.
_EXEMPT_RELATIVE_PATHS = {Path("health.py"), Path("dependencies.py")}

_FORBIDDEN_NAMES = {"async_session_factory"}
_FORBIDDEN_METHODS = {"commit", "rollback"}


def find_forbidden_session_usage(source: str) -> list[str]:
    """Every direct session-acquisition or transaction-completion call
    in `source` that bypasses the `DatabaseSession` dependency.

    Pure function: parses `source` as Python and inspects the AST only.
    Performs no I/O and imports no application code, so it is directly
    unit-testable with synthetic source strings.
    """
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            violations.append(
                f"line {node.lineno}: references '{node.id}' directly "
                "(use the 'DatabaseSession' dependency instead of "
                "opening an independent session)"
            )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _FORBIDDEN_NAMES:
                    violations.append(
                        f"line {node.lineno}: imports '{alias.name}' "
                        "directly (use the 'DatabaseSession' dependency "
                        "instead of opening an independent session)"
                    )
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            violations.append(
                f"line {node.lineno}: references '{node.attr}' directly "
                "via attribute access (use the 'DatabaseSession' "
                "dependency instead of opening an independent session)"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and (node.func.attr in _FORBIDDEN_METHODS)
        ):
            violations.append(
                f"line {node.lineno}: calls '.{node.func.attr}()' "
                "directly (transaction completion is owned by the "
                "'get_db' API dependency, not by route handlers)"
            )
    return violations


@pytest.mark.unit
class TestFindForbiddenSessionUsageDetector:
    """Unit tests for the pure AST detector, using synthetic sources."""

    def test_detects_direct_factory_reference(self) -> None:
        source = "session = async_session_factory()"
        assert len(find_forbidden_session_usage(source)) == 1

    def test_detects_direct_factory_import(self) -> None:
        source = "from app.database import async_session_factory"
        assert len(find_forbidden_session_usage(source)) == 1

    def test_detects_direct_commit_call(self) -> None:
        source = "await db.commit()"
        assert len(find_forbidden_session_usage(source)) == 1

    def test_detects_direct_rollback_call(self) -> None:
        source = "await db.rollback()"
        assert len(find_forbidden_session_usage(source)) == 1

    def test_ignores_unrelated_names_and_methods(self) -> None:
        source = "db.add(widget)\nawait db.flush()\nresult = await db.execute(stmt)"
        assert find_forbidden_session_usage(source) == []

    def test_ignores_database_session_dependency_usage(self) -> None:
        source = "async def endpoint(db: DatabaseSession) -> None: ..."
        assert find_forbidden_session_usage(source) == []

    def test_detects_factory_reference_via_module_attribute_access(self) -> None:
        source = "session = app.database.async_session_factory()"
        assert len(find_forbidden_session_usage(source)) == 1


@pytest.mark.unit
class TestNoDirectSessionUsageInApiLayer:
    """Scans every real module under `app/api/` (except the two
    documented exemptions in `_EXEMPT_RELATIVE_PATHS`) for the
    forbidden patterns.
    """

    def test_no_api_module_bypasses_the_database_session_dependency(self) -> None:
        violations: list[str] = []
        for path in sorted(APP_API_ROOT.rglob("*.py")):
            if path.relative_to(APP_API_ROOT) in _EXEMPT_RELATIVE_PATHS:
                continue
            source = path.read_text(encoding="utf-8")
            for violation in find_forbidden_session_usage(source):
                violations.append(f"{path}: {violation}")
        assert not violations, "\n".join(violations)
