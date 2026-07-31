"""Static enumerations shared across the application.

Every enumerated column in the schema is validated against a `StrEnum`
defined in this module — both Category A (state-machine, additionally
protected by a database CHECK constraint) and Category B (classification,
validated only in Python). See `docs/conventions.md` (Enum Storage
Strategy) for the classification criterion.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Sentinel platform roles.

    Category A — state-machine (VARCHAR + CHECK constraints:
    `chk_user_role_role_valid` on `user_role`,
    `chk_role_mapping_role_valid` on `role_mapping`). Adding a value
    requires an Alembic migration. See `docs/features/identity/rbac.md`
    (Predefined Roles) and `docs/data-model.md` (Role Enum).
    """

    ADMIN = "Admin"
    VULNERABILITY_ANALYST = "Vulnerability Analyst"
    RESTRICTED_ANALYST = "Restricted Analyst"


class Capability(StrEnum):
    """Static capabilities granted by roles.

    Category B — classification (Python Enum only, no CHECK constraint;
    capabilities are never stored in the database). See
    `docs/features/identity/rbac.md` (Capabilities) for the full
    description of the operations each capability covers.
    """

    # Vulnerability Analyst capabilities
    CREATE_TICKET = "create_ticket"
    TRIAGE_TICKET = "triage_ticket"
    MANAGE_PACKAGES = "manage_packages"
    MANAGE_CVSS = "manage_cvss"
    MANAGE_REFERENCES = "manage_references"
    MANAGE_CONFIDENTIALITY = "manage_confidentiality"

    # Admin capabilities
    MANAGE_USERS = "manage_users"
    MANAGE_ROLE_MAPPINGS = "manage_role_mappings"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_FETCHERS = "manage_fetchers"
    ADMIN_TICKET_OPS = "admin_ticket_ops"


class Scope(StrEnum):
    """Default visibility scope for confidential tickets.

    Category B — classification (Python Enum only, never stored in the
    database; scope is a static, code-resolved property of a role). See
    `docs/features/identity/rbac.md` (Scope).
    """

    ALL = "all"
    NON_CONFIDENTIAL = "non_confidential"
