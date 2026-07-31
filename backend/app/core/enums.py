"""Static classification and state-machine enums shared across the app.

See `docs/conventions.md` (Enum Storage Strategy) for the two-category
classification (state-machine vs classification) and
`docs/features/identity/rbac.md` for the full RBAC authorization model.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Role(StrEnum):
    """Sentinel platform roles.

    Category A — state-machine (`VARCHAR` + CHECK constraint). Values
    match the stored values in `user_role.role` and `role_mapping.role`.
    See `docs/data-model.md` (Role Enum).
    """

    ADMIN = "Admin"
    VULNERABILITY_ANALYST = "Vulnerability Analyst"
    RESTRICTED_ANALYST = "Restricted Analyst"


class Capability(StrEnum):
    """Static RBAC capabilities.

    Category B — classification (Python enum only, no DB column).
    Capabilities are granted to roles via `ROLE_CAPABILITIES`. See
    `docs/features/identity/rbac.md` (Capabilities).
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

    Category B — classification (Python enum only, no DB column). Scope
    is orthogonal to capabilities: it controls what a user can see, not
    what they can do. See `docs/features/identity/rbac.md` (Scope).
    """

    ALL = "all"
    NON_CONFIDENTIAL = "non_confidential"


# Static role -> capability set mapping.
# See docs/features/identity/rbac.md (Predefined Roles).
ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.ADMIN: frozenset(
        {
            Capability.MANAGE_USERS,
            Capability.MANAGE_ROLE_MAPPINGS,
            Capability.MANAGE_SETTINGS,
            Capability.MANAGE_FETCHERS,
            Capability.ADMIN_TICKET_OPS,
        }
    ),
    Role.VULNERABILITY_ANALYST: frozenset(
        {
            Capability.CREATE_TICKET,
            Capability.TRIAGE_TICKET,
            Capability.MANAGE_PACKAGES,
            Capability.MANAGE_CVSS,
            Capability.MANAGE_REFERENCES,
            Capability.MANAGE_CONFIDENTIALITY,
        }
    ),
    Role.RESTRICTED_ANALYST: frozenset(
        {
            Capability.CREATE_TICKET,
            Capability.TRIAGE_TICKET,
            Capability.MANAGE_PACKAGES,
            Capability.MANAGE_CVSS,
            Capability.MANAGE_REFERENCES,
        }
    ),
}

# Static role -> scope mapping.
# See docs/features/identity/rbac.md (Predefined Roles, Scope).
ROLE_SCOPES: dict[Role, Scope] = {
    Role.ADMIN: Scope.ALL,
    Role.VULNERABILITY_ANALYST: Scope.ALL,
    Role.RESTRICTED_ANALYST: Scope.NON_CONFIDENTIAL,
}

# DB <-> wire format mapping for roles. The database (and this StrEnum)
# stores the display value (e.g. "Vulnerability Analyst"); API requests,
# responses, and the CLI use lowercase-with-underscores (e.g.
# "vulnerability_analyst"). See docs/features/identity/rbac.md (Role
# Wire Format).
ROLE_WIRE_VALUES: dict[Role, str] = {
    Role.ADMIN: "admin",
    Role.VULNERABILITY_ANALYST: "vulnerability_analyst",
    Role.RESTRICTED_ANALYST: "restricted_analyst",
}

_WIRE_TO_ROLE: dict[str, Role] = {
    wire_value: role for role, wire_value in ROLE_WIRE_VALUES.items()
}


def role_to_wire(role: Role) -> str:
    """Convert a `Role` to its lowercase-with-underscores wire format."""
    return ROLE_WIRE_VALUES[role]


def role_from_wire(value: str) -> Role:
    """Convert a lowercase-with-underscores wire value to a `Role`.

    Raises:
        ValueError: if `value` does not match any known role wire value.
    """
    try:
        return _WIRE_TO_ROLE[value]
    except KeyError:
        msg = f"Unknown role wire value: {value!r}"
        raise ValueError(msg) from None


def resolve_capabilities(roles: Iterable[Role]) -> frozenset[Capability]:
    """Return the union of capabilities granted by the given roles.

    A user with no roles (empty `roles`) resolves to an empty set of
    capabilities.
    """
    result: set[Capability] = set()
    for role in roles:
        result.update(ROLE_CAPABILITIES[role])
    return frozenset(result)


def resolve_scope(roles: Iterable[Role]) -> Scope:
    """Return the least-restrictive scope granted by the given roles.

    If any role has scope `all`, the effective scope is `all`. Otherwise
    (including when `roles` is empty), the effective scope is
    `non_confidential`. See docs/features/identity/rbac.md (Scope
    resolution) — this is the resolution for an authenticated user's
    role list; unauthenticated callers have no scope at all (`None`),
    which is handled by the caller, not this function.
    """
    for role in roles:
        if ROLE_SCOPES[role] == Scope.ALL:
            return Scope.ALL
    return Scope.NON_CONFIDENTIAL
