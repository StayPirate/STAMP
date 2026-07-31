"""SQLAlchemy ORM models.

All models must be imported here so Alembic's autogenerate can discover
them via `Base.metadata` (see `alembic/env.py`).
"""

from __future__ import annotations

from app.models.user import User
from app.models.user_role import UserRole

__all__ = ["User", "UserRole"]
