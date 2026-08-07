"""User model — identity root.

See `docs/data-model.md` (User) and `docs/features/identity/rbac.md` for
the full specification. This model intentionally implements only the
persistence root: authentication, session management, and user lifecycle
services are out of scope for this piece (see `docs/drafts/implementation-plan.md`,
P1-04).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.session import Session
    from app.models.user_role import UserRole


class User(Base):
    """Platform user, populated from an external identity provider or
    created locally via CLI.
    """

    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint(
            "(external_id IS NOT NULL AND password_hash IS NULL) "
            "OR (external_id IS NULL AND password_hash IS NOT NULL)",
            name="chk_user_auth_exclusive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_hash: Mapped[str | None] = mapped_column(String(72), nullable=True)
    external_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    manager: Mapped[User | None] = relationship(
        "User",
        remote_side=[id],
        back_populates="direct_reports",
        foreign_keys=[manager_id],
    )
    direct_reports: Mapped[list[User]] = relationship(
        "User",
        back_populates="manager",
        foreign_keys=[manager_id],
    )
    roles: Mapped[list[UserRole]] = relationship(
        "UserRole",
        back_populates="user",
        foreign_keys="UserRole.user_id",
        cascade="all, delete-orphan",
    )
    # Deliberately no cascade: user deletion is not supported
    # (docs/features/identity/user-service.md, User Deletion). A
    # hypothetical `delete(user)` must fail loudly with an
    # IntegrityError instead of silently destroying session or API key
    # records. See issue #149 for aligning `roles` to this same
    # behavior.
    sessions: Mapped[list[Session]] = relationship("Session", back_populates="user")
    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey", back_populates="user", foreign_keys="ApiKey.user_id"
    )
