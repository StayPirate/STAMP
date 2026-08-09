"""Session model — active user session tracking.

See `docs/data-model.md` (Session) and
`docs/features/identity/authentication.md` (Session Management) for the
full specification. Session creation at login, sliding-session
refresh, and deactivation on logout are implemented in
`app/services/session_service.py` and `app/core/jwt.py` — this module
implements only the persistence root (the `Session` table itself).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Session(Base):
    """A single login session, referenced by the JWT `session_id` claim.

    `expires_at` is the immutable maximum session lifetime, calculated
    once at login as `now() + SESSION_MAX_LIFETIME_DAYS * 86400`. It is
    never recomputed from the current setting — a later change to
    `SESSION_MAX_LIFETIME_DAYS` only affects sessions created by
    subsequent logins (see `authentication.md`, Session Management).
    """

    __tablename__ = "session"
    __table_args__ = (Index("ix_session_user_id_is_active", "user_id", "is_active"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Deliberately no cascade: user deletion is not supported
    # (docs/features/identity/user-service.md, User Deletion). A
    # hypothetical `delete(user)` must fail loudly with an IntegrityError
    # instead of silently destroying session records.
    user: Mapped[User] = relationship("User", back_populates="sessions")
