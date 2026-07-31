"""Tests for static RBAC enums and resolution (backend/app/core/enums.py).

See docs/features/identity/rbac.md for the authoritative RBAC contract
this module implements.
"""

from __future__ import annotations

import pytest

from app.core.enums import (
    ROLE_CAPABILITIES,
    ROLE_SCOPES,
    Capability,
    Role,
    Scope,
    resolve_capabilities,
    resolve_scope,
    role_from_wire,
    role_to_wire,
)


@pytest.mark.unit
class TestRoleEnum:
    """Role stores the exact DB display values."""

    def test_role_values_match_data_model(self):
        assert Role.ADMIN.value == "Admin"
        assert Role.VULNERABILITY_ANALYST.value == "Vulnerability Analyst"
        assert Role.RESTRICTED_ANALYST.value == "Restricted Analyst"

    def test_role_has_exactly_three_members(self):
        assert len(Role) == 3


@pytest.mark.unit
class TestCapabilityEnum:
    """Capability enumerates exactly the 11 static capabilities."""

    def test_capability_has_exactly_eleven_members(self):
        assert len(Capability) == 11

    def test_va_capability_values(self):
        assert Capability.CREATE_TICKET.value == "create_ticket"
        assert Capability.TRIAGE_TICKET.value == "triage_ticket"
        assert Capability.MANAGE_PACKAGES.value == "manage_packages"
        assert Capability.MANAGE_CVSS.value == "manage_cvss"
        assert Capability.MANAGE_REFERENCES.value == "manage_references"
        assert Capability.MANAGE_CONFIDENTIALITY.value == "manage_confidentiality"

    def test_admin_capability_values(self):
        assert Capability.MANAGE_USERS.value == "manage_users"
        assert Capability.MANAGE_ROLE_MAPPINGS.value == "manage_role_mappings"
        assert Capability.MANAGE_SETTINGS.value == "manage_settings"
        assert Capability.MANAGE_FETCHERS.value == "manage_fetchers"
        assert Capability.ADMIN_TICKET_OPS.value == "admin_ticket_ops"


@pytest.mark.unit
class TestScopeEnum:
    def test_scope_values(self):
        assert Scope.ALL.value == "all"
        assert Scope.NON_CONFIDENTIAL.value == "non_confidential"

    def test_scope_has_exactly_two_members(self):
        assert len(Scope) == 2


@pytest.mark.unit
class TestRoleCapabilityMapping:
    """ROLE_CAPABILITIES exactly matches rbac.md (Predefined Roles)."""

    def test_admin_capabilities(self):
        assert ROLE_CAPABILITIES[Role.ADMIN] == frozenset(
            {
                Capability.MANAGE_USERS,
                Capability.MANAGE_ROLE_MAPPINGS,
                Capability.MANAGE_SETTINGS,
                Capability.MANAGE_FETCHERS,
                Capability.ADMIN_TICKET_OPS,
            }
        )

    def test_vulnerability_analyst_capabilities(self):
        assert ROLE_CAPABILITIES[Role.VULNERABILITY_ANALYST] == frozenset(
            {
                Capability.CREATE_TICKET,
                Capability.TRIAGE_TICKET,
                Capability.MANAGE_PACKAGES,
                Capability.MANAGE_CVSS,
                Capability.MANAGE_REFERENCES,
                Capability.MANAGE_CONFIDENTIALITY,
            }
        )

    def test_restricted_analyst_capabilities(self):
        assert ROLE_CAPABILITIES[Role.RESTRICTED_ANALYST] == frozenset(
            {
                Capability.CREATE_TICKET,
                Capability.TRIAGE_TICKET,
                Capability.MANAGE_PACKAGES,
                Capability.MANAGE_CVSS,
                Capability.MANAGE_REFERENCES,
            }
        )

    def test_restricted_analyst_lacks_manage_confidentiality(self):
        assert (
            Capability.MANAGE_CONFIDENTIALITY
            not in ROLE_CAPABILITIES[Role.RESTRICTED_ANALYST]
        )

    def test_admin_does_not_inherit_va_capabilities(self):
        """Design note: admin does NOT inherit VA capabilities."""
        assert ROLE_CAPABILITIES[Role.ADMIN].isdisjoint(
            ROLE_CAPABILITIES[Role.VULNERABILITY_ANALYST]
        )

    def test_every_role_has_a_capability_mapping(self):
        assert set(ROLE_CAPABILITIES.keys()) == set(Role)


@pytest.mark.unit
class TestRoleScopeMapping:
    def test_admin_scope_is_all(self):
        assert ROLE_SCOPES[Role.ADMIN] == Scope.ALL

    def test_vulnerability_analyst_scope_is_all(self):
        assert ROLE_SCOPES[Role.VULNERABILITY_ANALYST] == Scope.ALL

    def test_restricted_analyst_scope_is_non_confidential(self):
        assert ROLE_SCOPES[Role.RESTRICTED_ANALYST] == Scope.NON_CONFIDENTIAL

    def test_every_role_has_a_scope_mapping(self):
        assert set(ROLE_SCOPES.keys()) == set(Role)


@pytest.mark.unit
class TestResolveCapabilities:
    """resolve_capabilities() returns the union across all held roles."""

    def test_single_role(self):
        result = resolve_capabilities([Role.RESTRICTED_ANALYST])
        assert result == ROLE_CAPABILITIES[Role.RESTRICTED_ANALYST]

    def test_multiple_roles_union(self):
        result = resolve_capabilities([Role.ADMIN, Role.VULNERABILITY_ANALYST])
        assert result == (
            ROLE_CAPABILITIES[Role.ADMIN]
            | ROLE_CAPABILITIES[Role.VULNERABILITY_ANALYST]
        )

    def test_duplicate_roles_do_not_duplicate_capabilities(self):
        result = resolve_capabilities(
            [Role.VULNERABILITY_ANALYST, Role.VULNERABILITY_ANALYST]
        )
        assert result == ROLE_CAPABILITIES[Role.VULNERABILITY_ANALYST]

    def test_no_roles_yields_no_capabilities(self):
        assert resolve_capabilities([]) == frozenset()

    def test_result_is_frozenset(self):
        assert isinstance(resolve_capabilities([Role.ADMIN]), frozenset)


@pytest.mark.unit
class TestResolveScope:
    """resolve_scope() returns the least-restrictive scope."""

    def test_single_all_scope_role(self):
        assert resolve_scope([Role.ADMIN]) == Scope.ALL

    def test_single_non_confidential_scope_role(self):
        assert resolve_scope([Role.RESTRICTED_ANALYST]) == Scope.NON_CONFIDENTIAL

    def test_mixed_roles_yield_all(self):
        """If any role has scope `all`, the effective scope is `all`."""
        assert resolve_scope([Role.RESTRICTED_ANALYST, Role.ADMIN]) == Scope.ALL

    def test_mixed_roles_order_independent(self):
        assert resolve_scope([Role.ADMIN, Role.RESTRICTED_ANALYST]) == Scope.ALL

    def test_no_roles_yields_non_confidential(self):
        """Authenticated users with no roles have scope non_confidential."""
        assert resolve_scope([]) == Scope.NON_CONFIDENTIAL

    def test_multiple_restricted_roles_yield_non_confidential(self):
        assert (
            resolve_scope([Role.RESTRICTED_ANALYST, Role.RESTRICTED_ANALYST])
            == Scope.NON_CONFIDENTIAL
        )


@pytest.mark.unit
class TestRoleWireFormat:
    """DB <-> wire format conversion per rbac.md (Role Wire Format)."""

    @pytest.mark.parametrize(
        ("role", "wire_value"),
        [
            (Role.ADMIN, "admin"),
            (Role.VULNERABILITY_ANALYST, "vulnerability_analyst"),
            (Role.RESTRICTED_ANALYST, "restricted_analyst"),
        ],
    )
    def test_role_to_wire(self, role: Role, wire_value: str):
        assert role_to_wire(role) == wire_value

    @pytest.mark.parametrize(
        ("wire_value", "role"),
        [
            ("admin", Role.ADMIN),
            ("vulnerability_analyst", Role.VULNERABILITY_ANALYST),
            ("restricted_analyst", Role.RESTRICTED_ANALYST),
        ],
    )
    def test_role_from_wire(self, wire_value: str, role: Role):
        assert role_from_wire(wire_value) == role

    def test_role_from_wire_unknown_value_raises(self):
        with pytest.raises(ValueError, match="Unknown role wire value"):
            role_from_wire("nonexistent_role")

    def test_role_from_wire_rejects_db_display_value(self):
        """The wire format is lowercase_with_underscores, not the DB value."""
        with pytest.raises(ValueError, match="Unknown role wire value"):
            role_from_wire("Admin")

    def test_roundtrip_every_role(self):
        for role in Role:
            assert role_from_wire(role_to_wire(role)) == role
