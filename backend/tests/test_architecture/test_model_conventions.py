"""Structural tests over SQLAlchemy model conventions.

Verifies invariants declared in `docs/conventions.md` (SQLAlchemy
Conventions, Enum Storage Strategy) and
`docs/features/platform/testing-strategy.md` (Structural Tests) across
every table registered in `Base.metadata`. These invariants apply
universally — with no per-table exceptions — so this module
intentionally does not maintain any allowlist. See the Structural
Tests section of the testing strategy for the invariants deliberately
kept out of this module's scope (CHECK constraint naming,
`created_at`/`updated_at` presence).
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
        for table in _mapped_tables():
            pk_columns = list(table.primary_key.columns)
            assert pk_columns, f"Table '{table.name}' has no primary key"
            for column in pk_columns:
                assert isinstance(column.type, UUID), (
                    f"Table '{table.name}' primary key column "
                    f"'{column.name}' is not a UUID type "
                    f"(got {type(column.type).__name__})"
                )


@pytest.mark.unit
class TestDateTimeColumnsAreTimezoneAware:
    """Every DateTime column is timezone-aware.

    See `docs/conventions.md` (Timestamps & Timezones): "Never use bare
    TIMESTAMP (without time zone) — naive timestamps are ambiguous and
    a source of bugs in multi-timezone environments."
    """

    def test_every_datetime_column_declares_timezone_true(self) -> None:
        for table in _mapped_tables():
            for column in table.columns:
                if isinstance(column.type, DateTime):
                    assert column.type.timezone is True, (
                        f"Table '{table.name}' column '{column.name}' "
                        "uses DateTime without timezone=True "
                        "(produces a naive TIMESTAMP column)"
                    )


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
        for table in _mapped_tables():
            for column in table.columns:
                assert not isinstance(column.type, SAEnum), (
                    f"Table '{table.name}' column '{column.name}' uses "
                    "a native ENUM type (forbidden — use VARCHAR(N) "
                    "with a CHECK constraint or a Python StrEnum instead, "
                    "per the Enum Storage Strategy)"
                )
