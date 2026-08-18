"""UserRole model — junction table linking users to roles.

See `docs/data-model.md` (UserRole) and `docs/features/identity/rbac.md`
(Role Origins and Coexistence) for the full specification.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Role
from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class UserRole(Base):
    """A single role grant to a user, tagged with its origin.

    `group_name` tracks the origin of the grant: the sentinel value
    `_manual` for admin/CLI assignments, or an external group name for
    grants derived from external sync. A user may hold the same role
    from multiple origins simultaneously — each origin is a distinct row
    (see `docs/features/identity/rbac.md`, Coexistence Rules).
    """

    __tablename__ = "user_role"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "role", "group_name", name="uq_user_role_user_role_group"
        ),
        CheckConstraint(
            f"role IN ({', '.join(repr(r.value) for r in Role)})",
            name="chk_user_role_role_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid7,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    group_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="_manual", server_default="_manual"
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Deliberately no cascade: user deletion is not supported
    # (docs/features/identity/user-service.md, User Deletion). A
    # hypothetical `delete(user)` must fail loudly with an IntegrityError
    # instead of silently destroying role grant records.
    user: Mapped[User] = relationship(
        "User",
        back_populates="roles",
        foreign_keys=[user_id],
    )
    # The admin who assigned this role (nullable for system actions).
    # Deliberately NOT cascading: deleting the assigning admin must never
    # delete or alter the role grant itself.
    assigning_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[assigned_by],
    )
