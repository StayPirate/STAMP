"""ApiKey model — programmatic access credentials.

See `docs/data-model.md` (ApiKey) and
`docs/features/identity/api-key-management.md` (API Key Contract) for
the full specification. This model intentionally implements only the
persistence root: key generation, hashing, and lifecycle management
(revocation, last-used tracking) are out of scope for this piece (see
`docs/drafts/implementation-plan.md`, P2-01).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class ApiKey(Base):
    """An API key for non-interactive authentication (bots, AI agents,
    CI pipelines). Only the SHA-256 hash of the full key is stored; the
    full value is shown once at creation and never persisted.

    Has no `updated_at` column — every mutable field (`last_used_at`,
    `revoked_at`) is independently nullable and updated in place; a
    generic "last modified" timestamp would be redundant (see
    `docs/data-model.md`, ApiKey).
    """

    __tablename__ = "api_key"
    __table_args__ = (
        # Restricts key_hash to a 64-character lowercase hexadecimal string
        # (the shape of a SHA-256 digest). Defense in depth: makes a
        # plaintext key or an uppercase digest structurally unrepresentable.
        # See docs/data-model.md (ApiKey, Check constraint).
        CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name="chk_api_key_hash_is_sha256_hex",
        ),
        Index("ix_api_key_user_id_revoked_at", "user_id", "revoked_at"),
        Index(
            "uq_api_key_user_id_name_active",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=True
    )

    # Deliberately no cascade: user deletion is not supported
    # (docs/features/identity/user-service.md, User Deletion). A
    # hypothetical `delete(user)` must fail loudly with an IntegrityError
    # instead of silently destroying API key records.
    user: Mapped[User] = relationship(
        "User", back_populates="api_keys", foreign_keys=[user_id]
    )
    # The user who revoked this key (nullable for system/CLI
    # revocations). Deliberately NOT cascading and NOT back-populated:
    # deleting the revoking user must never delete or alter the key
    # record itself. Mirrors UserRole.assigning_user.
    revoking_user: Mapped[User | None] = relationship("User", foreign_keys=[revoked_by])
