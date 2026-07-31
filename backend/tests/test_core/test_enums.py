"""Tests for static enumerations (backend/app/core/enums.py).

See docs/features/identity/rbac.md (Authorization Model) and
docs/data-model.md (Role Enum) for the contract these enums implement.
"""

from __future__ import annotations

import pytest

from app.core.enums import Capability, Role, Scope


@pytest.mark.unit
class TestRoleEnum:
    """Role must have exactly the three members defined in rbac.md."""

    def test_exact_members(self) -> None:
        assert {member.name for member in Role} == {
            "ADMIN",
            "VULNERABILITY_ANALYST",
            "RESTRICTED_ANALYST",
        }

    def test_db_values(self) -> None:
        """DB storage values match the Role Enum table in data-model.md."""
        assert Role.ADMIN.value == "Admin"
        assert Role.VULNERABILITY_ANALYST.value == "Vulnerability Analyst"
        assert Role.RESTRICTED_ANALYST.value == "Restricted Analyst"


@pytest.mark.unit
class TestCapabilityEnum:
    """Capability must have exactly the 11 members defined in rbac.md."""

    def test_exact_members(self) -> None:
        assert {member.value for member in Capability} == {
            "create_ticket",
            "triage_ticket",
            "manage_packages",
            "manage_cvss",
            "manage_references",
            "manage_confidentiality",
            "manage_users",
            "manage_role_mappings",
            "manage_settings",
            "manage_fetchers",
            "admin_ticket_ops",
        }

    def test_count(self) -> None:
        assert len(list(Capability)) == 11


@pytest.mark.unit
class TestScopeEnum:
    """Scope must have exactly the two members defined in rbac.md."""

    def test_exact_members(self) -> None:
        assert {member.value for member in Scope} == {"all", "non_confidential"}
