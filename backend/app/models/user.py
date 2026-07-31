"""User model — the identity root for authentication and RBAC.

See `docs/data-model.md` (User) and
`docs/features/identity/rbac.md` for the full authorization model.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user_role import UserRole


class User(Base):
    """Platform user with role-based access.

    Users are populated from an external identity provider (see
    `docs/features/identity/identity-provisioning.md`) or created locally
    via CLI. A user is either local (`external_id IS NULL`, has a
    `password_hash`) or external (`external_id IS NOT NULL`, no
    `password_hash`) — enforced by `chk_user_auth_exclusive`.
    """

    __tablename__ = "user"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    password_hash: Mapped[str | None] = mapped_column(String(72), nullable=True)
    external_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True, nullable=True)
    manager_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    manager: Mapped[User | None] = relationship(
        "User",
        remote_side=[id],
        back_populates="direct_reports",
        foreign_keys=[manager_id],
    )
    # passive_deletes=True: let the database enforce ON DELETE RESTRICT
    # (docs/api-spec.md, "User References in Responses") instead of the
    # ORM proactively nulling out the child FK before the DELETE.
    direct_reports: Mapped[list[User]] = relationship(
        "User",
        back_populates="manager",
        foreign_keys=[manager_id],
        passive_deletes=True,
    )
    roles: Mapped[list[UserRole]] = relationship(
        "UserRole",
        back_populates="user",
        foreign_keys="[UserRole.user_id]",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(external_id IS NOT NULL AND password_hash IS NULL) OR "
            "(external_id IS NULL AND password_hash IS NOT NULL)",
            name="chk_user_auth_exclusive",
        ),
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, username={self.username!r})"
