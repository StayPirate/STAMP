"""SystemSetting model — key-value store for system-wide configuration.

See `docs/data-model.md` (SystemSetting) and
`docs/features/platform/system-settings.md` for the full specification,
including the bootstrap and lifespan self-healing behavior that keeps
the `default_cvss_version` row present.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemSetting(Base):
    """A single system-wide configuration key-value pair.

    Uses a natural string key as its primary key (an explicit,
    documented exception to the project's UUID primary key convention
    — see `docs/data-model.md`, Notes) rather than a surrogate UUID,
    since settings are identified and referenced by their key
    throughout the codebase (e.g. `default_cvss_version`). Has no
    `created_at`: the row's creation time is not tracked, only its last
    modification (`docs/data-model.md`, Notes).
    """

    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
