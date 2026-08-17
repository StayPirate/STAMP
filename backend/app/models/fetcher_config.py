"""FetcherConfig model — per-fetcher operational configuration.

See `docs/data-model.md` (FetcherConfig) and
`docs/features/platform/fetcher-infrastructure.md` (Data Model —
FetcherConfig) for the full specification, including the
`bootstrap_fetcher_configs()` auto-creation routine
(`backend/app/services/fetcher_bootstrap.py`) — this model only
defines the persistence root.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.fetcher_audit_event import FetcherAuditEvent
    from app.models.fetcher_run import FetcherRun


class FetcherConfig(Base):
    """Per-fetcher configuration, managed by admins.

    Uses `fetcher_name` (matching `BaseFetcher.name`) as a natural
    string primary key — an explicit, documented exception to the
    project's UUID primary key convention (see `docs/data-model.md`,
    Notes) — rather than a surrogate UUID, since fetcher names are
    unique identifiers defined in code. Has no `created_at`: the row's
    creation time is not tracked, only its last modification
    (`docs/data-model.md`, Notes). A record is created automatically
    at process startup by `bootstrap_fetcher_configs()`
    (`backend/app/services/fetcher_bootstrap.py`); this model only
    defines the persistence root.
    """

    __tablename__ = "fetcher_config"

    fetcher_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule_override: Mapped[str | None] = mapped_column(String(50), nullable=True)
    run_timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    request_delay: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    custom_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # No cascade: the FK on the dependent tables uses ON DELETE RESTRICT
    # (docs/features/platform/fetcher-infrastructure.md, Deregistered
    # Fetcher Lifecycle — historical run and audit data must survive a
    # deregistered fetcher). `passive_deletes="all"` makes this
    # explicit: SQLAlchemy must not attempt to null out or otherwise
    # touch dependent rows on a hypothetical `delete(config)` — the
    # database FK constraint is the sole enforcement mechanism, and it
    # must fail loudly with an IntegrityError instead.
    runs: Mapped[list[FetcherRun]] = relationship(
        "FetcherRun",
        back_populates="config",
        passive_deletes="all",
    )
    audit_events: Mapped[list[FetcherAuditEvent]] = relationship(
        "FetcherAuditEvent",
        back_populates="config",
        passive_deletes="all",
    )
