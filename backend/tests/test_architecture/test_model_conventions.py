"""Structural tests over SQLAlchemy model conventions.

Verifies invariants declared in `docs/conventions.md` (SQLAlchemy
Conventions, Enum Storage Strategy) and
`docs/features/platform/testing-strategy.md` (Structural Tests) across
every table registered in `Base.metadata`.

Scope note: CHECK constraint naming and `created_at`/`updated_at`
presence are deliberately NOT verified here, even though both are
model-level invariants. `docs/data-model.md` (Notes) already documents
a growing, per-table exception list for `created_at`/`updated_at`
(append-only and auto-created tables); mirroring that list here would
require hand-keeping a second copy in sync, since this module must not
parse `data-model.md`. CHECK constraint naming has only two instances
project-wide, one of which (`chk_user_auth_exclusive`) is a legitimate
exception to the enum-check pattern — a case better served by human
review in each rare PR that adds one than by a hard-coded rule (see
issue #58 for the full rationale). The three invariants below apply
universally, with zero per-table exceptions, which is what makes them
good structural-test candidates.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import DateTime

import app.models  # noqa: F401 — import populates Base.metadata as a side effect
from app.database import Base


def _mapped_tables() -> Iterable[Table]:
    """Every table registered on the shared declarative Base."""
    return Base.metadata.tables.values()


@pytest.mark.unit
class TestPrimaryKeyType:
    """Every mapped table uses a UUID primary key.

    See `docs/conventions.md` (SQLAlchemy Conventions): "Use UUID
    primary keys."
    """

    def test_every_table_has_a_uuid_primary_key(self) -> None:
        violations: list[str] = []
        for table in _mapped_tables():
            pk_columns = list(table.primary_key.columns)
            if not pk_columns:
                violations.append(f"Table '{table.name}' has no primary key")
                continue
            for column in pk_columns:
                if not isinstance(column.type, UUID):
                    violations.append(
                        f"Table '{table.name}' primary key column "
                        f"'{column.name}' is not a UUID type "
                        f"(got {type(column.type).__name__})"
                    )
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestDateTimeColumnsAreTimezoneAware:
    """Every DateTime column is timezone-aware.

    See `docs/conventions.md` (Timestamps & Timezones): "Never use bare
    TIMESTAMP (without time zone) — naive timestamps are ambiguous and
    a source of bugs in multi-timezone environments."
    """

    def test_every_datetime_column_declares_timezone_true(self) -> None:
        violations: list[str] = []
        for table in _mapped_tables():
            for column in table.columns:
                if (
                    isinstance(column.type, DateTime)
                    and column.type.timezone is not True
                ):
                    violations.append(
                        f"Table '{table.name}' column '{column.name}' "
                        "uses DateTime without timezone=True "
                        "(produces a naive TIMESTAMP column)"
                    )
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestNoPostgresEnumTypes:
    """No column uses a native SQL ENUM type.

    See `docs/conventions.md` (Enum Storage Strategy): "Sentinel does
    not use PostgreSQL ENUM types (CREATE TYPE ... AS ENUM). All
    enumerated columns use VARCHAR(N)."

    `sqlalchemy.dialects.postgresql.ENUM` is a subclass of
    `sqlalchemy.Enum`, so a single `isinstance` check against the
    generic type catches both the emulated and the PostgreSQL-native
    variant.
    """

    def test_no_column_uses_a_native_enum_type(self) -> None:
        violations: list[str] = []
        for table in _mapped_tables():
            for column in table.columns:
                if isinstance(column.type, SAEnum):
                    violations.append(
                        f"Table '{table.name}' column '{column.name}' uses "
                        "a native ENUM type (forbidden — use VARCHAR(N) "
                        "with a CHECK constraint or a Python StrEnum instead, "
                        "per the Enum Storage Strategy)"
                    )
        assert not violations, "\n".join(violations)
