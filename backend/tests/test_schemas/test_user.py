"""Unit tests for user directory/profile request/response/query schemas
(`backend/app/schemas/user.py`).

See `docs/features/identity/user-management.md` (List Users, Get User)
for the authoritative contract under test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.enums import SortOrder, UserSortField
from app.schemas.common import PaginationMeta
from app.schemas.user import (
    UserData,
    UserListQuery,
    UserListResponse,
    UserManagerData,
    UserResponse,
    UserRoleAssignmentData,
)


@pytest.mark.unit
class TestUserListQuery:
    def test_defaults(self) -> None:
        query = UserListQuery()
        assert query.search is None
        assert query.type is None
        assert query.active is None
        assert query.role == []
        assert query.has_role is None
        assert query.page == 1
        assert query.per_page == 20
        assert query.sort_by is UserSortField.USERNAME
        assert query.sort_order is SortOrder.ASC

    def test_search_absent_is_valid(self) -> None:
        assert UserListQuery().search is None

    def test_search_at_minimum_length_is_accepted(self) -> None:
        assert UserListQuery(search="jd").search == "jd"

    def test_search_below_minimum_length_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(search="j")

    def test_search_empty_string_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(search="")

    def test_type_accepts_an_invalid_value_without_raising(self) -> None:
        """`type` is intentionally untyped at the schema level — an
        invalid value must reach the route handler so it can render an
        empty page (docs/api-spec.md, Enum Filter Validation)."""
        assert UserListQuery(type="not-a-real-type").type == "not-a-real-type"

    def test_role_accepts_a_list_of_raw_strings(self) -> None:
        query = UserListQuery(role=["admin", "not-a-real-role"])
        assert query.role == ["admin", "not-a-real-role"]

    def test_page_below_one_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(page=0)

    def test_page_at_int32_max_is_accepted(self) -> None:
        assert UserListQuery(page=2_147_483_647).page == 2_147_483_647

    def test_page_above_int32_max_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(page=2_147_483_648)

    def test_per_page_above_100_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(per_page=101)

    def test_per_page_at_100_is_accepted(self) -> None:
        assert UserListQuery(per_page=100).per_page == 100

    @pytest.mark.parametrize(
        "sort_by", ["username", "full_name", "email", "created_at"]
    )
    def test_valid_sort_by_is_accepted(self, sort_by: str) -> None:
        assert UserListQuery(sort_by=sort_by).sort_by == sort_by

    def test_invalid_sort_by_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(sort_by="active")

    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_valid_sort_order_is_accepted(self, sort_order: str) -> None:
        assert UserListQuery(sort_order=sort_order).sort_order == sort_order

    def test_invalid_sort_order_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserListQuery(sort_order="ascending")


def _make_manager_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "username": "bwilson",
        "full_name": "Bob Wilson",
        "active": True,
        "email": "bwilson@example.com",
    }
    defaults.update(overrides)
    return defaults


def _make_role_assignment_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "role": "admin",
        "group_name": "_manual",
        "assigned_by": uuid4(),
        "created_at": datetime(2026, 5, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return defaults


def _make_user_data_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "username": "jdoe",
        "email": "jdoe@example.com",
        "full_name": "John Doe",
        "active": True,
        "source": "local",
        "external_id": None,
        "manager": None,
        "roles": [],
        "created_at": datetime(2026, 5, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.unit
class TestUserManagerData:
    def test_full_name_accepts_null(self) -> None:
        manager = UserManagerData(**_make_manager_kwargs(full_name=None))
        assert manager.full_name is None

    def test_carries_email_unlike_the_common_user_reference(self) -> None:
        manager = UserManagerData(**_make_manager_kwargs())
        assert manager.email == "bwilson@example.com"


@pytest.mark.unit
class TestUserRoleAssignmentData:
    def test_assigned_by_accepts_null_for_system_assignment(self) -> None:
        assignment = UserRoleAssignmentData(
            **_make_role_assignment_kwargs(assigned_by=None)
        )
        assert assignment.assigned_by is None

    def test_group_name_carries_external_origin(self) -> None:
        assignment = UserRoleAssignmentData(
            **_make_role_assignment_kwargs(group_name="O SUSE Security")
        )
        assert assignment.group_name == "O SUSE Security"


@pytest.mark.unit
class TestUserData:
    def test_full_name_accepts_null_with_no_fallback(self) -> None:
        """docs/features/identity/user-management.md (Get User, Field
        notes): the API returns `null` verbatim — no `username`
        substitution happens at the schema/backend layer."""
        data = UserData(**_make_user_data_kwargs(full_name=None))
        assert data.full_name is None

    def test_source_external_pairs_with_external_id(self) -> None:
        external_id = uuid4()
        data = UserData(
            **_make_user_data_kwargs(source="external", external_id=external_id)
        )
        assert data.source == "external"
        assert data.external_id == external_id

    def test_manager_accepts_null(self) -> None:
        data = UserData(**_make_user_data_kwargs(manager=None))
        assert data.manager is None

    def test_manager_accepts_a_manager_object(self) -> None:
        manager = UserManagerData(**_make_manager_kwargs())
        data = UserData(**_make_user_data_kwargs(manager=manager))
        assert data.manager is not None
        assert data.manager.username == "bwilson"

    def test_roles_accepts_multiple_assignments(self) -> None:
        roles = [
            UserRoleAssignmentData(**_make_role_assignment_kwargs()),
            UserRoleAssignmentData(
                **_make_role_assignment_kwargs(
                    role="vulnerability_analyst", group_name="O SUSE Security"
                )
            ),
        ]
        data = UserData(**_make_user_data_kwargs(roles=roles))
        assert len(data.roles) == 2

    def test_has_no_last_login_at_field(self) -> None:
        """Structural guarantee: the public full profile must never
        expose the operational `last_login_at` field — CLI
        `manage-user show` remains its sole owner."""
        assert "last_login_at" not in UserData.model_fields

    def test_has_no_password_hash_field(self) -> None:
        assert "password_hash" not in UserData.model_fields

    def test_has_no_synced_at_field(self) -> None:
        assert "synced_at" not in UserData.model_fields


@pytest.mark.unit
class TestResponseEnvelopes:
    def test_user_response_wraps_a_single_object(self) -> None:
        response = UserResponse(data=UserData(**_make_user_data_kwargs()))
        assert response.data.username == "jdoe"

    def test_user_list_response_has_data_and_meta(self) -> None:
        response = UserListResponse(
            data=[UserData(**_make_user_data_kwargs())],
            meta=PaginationMeta(total=1, page=1, per_page=20),
        )
        assert len(response.data) == 1
        assert response.meta.total == 1
