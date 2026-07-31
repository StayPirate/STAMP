"""Regression tests for database engine construction (app/database.py).

`app/database.py` is excluded from the coverage gate (module-level
side-effecting engine construction — see pyproject.toml), but these
structural assertions guard two real regressions:

1. `DEBUG` must never re-couple to SQLAlchemy's `echo` parameter (see
   docs/features/platform/logging.md, "Relationship with DEBUG").
2. `hide_parameters=True` must remain set so bound SQL parameters
   (which may include credential material) never leak into an
   unhandled `StatementError`'s traceback/message, independent of
   `LOG_LEVEL` (see docs/conventions.md, Secrets and PII Discipline).
"""

from __future__ import annotations

import pytest

from app.database import engine


@pytest.mark.unit
class TestEngineConstruction:
    """Structural regression guards on the module-level `engine`."""

    def test_hide_parameters_is_enabled(self):
        """Bound SQL parameters must never leak via exception messages,
        regardless of LOG_LEVEL — see security discipline in
        docs/conventions.md."""
        assert engine.sync_engine.hide_parameters is True

    def test_echo_is_not_enabled_by_default(self):
        """DEBUG must not control SQLAlchemy echo; echo must remain
        unset (falsy) regardless of the DEBUG setting."""
        assert not engine.sync_engine.echo
