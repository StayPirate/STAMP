"""UserRole model — junction table linking users to roles.

See `docs/data-model.md` (UserRole) and
`docs/features/identity/rbac.md` (Role Origins and Coexistence).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Role
from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User

_ROLE_VALUES_SQL = ", ".join(f"'{role.value}'" for role in Role)


class UserRole(Base):
    """A single role assignment held by a user from one origin.

    The `group_name` column tracks the origin of the assignment: the
    sentinel value `_manual` for admin/CLI-assigned roles, or an external
    group name for roles derived from that group's `RoleMapping`. A user
    can hold the same role from multiple origins simultaneously — see
    `docs/features/identity/rbac.md` (Coexistence Rules).
    """

    __tablename__ = "user_role"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    group_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="_manual", server_default="_manual"
    )
    assigned_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(
        "User",
        back_populates="roles",
        foreign_keys=[user_id],
    )
    assigned_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[assigned_by],
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "role", "group_name", name="uq_user_role_user_id_role_group_name"
        ),
        CheckConstraint(
            f"role IN ({_ROLE_VALUES_SQL})",
            name="chk_user_role_role_valid",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"UserRole(user_id={self.user_id!r}, role={self.role!r}, "
            f"group_name={self.group_name!r})"
        )
