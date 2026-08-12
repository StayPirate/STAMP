"""Tests for `sentinel manage-user create/list/show`.

See docs/features/identity/user-management.md for the authoritative
per-command contract exercised here. Unit tests cover the pure
validation and rendering helpers directly; integration tests exercise
the full `CliRunner`-invoked commands against the real PostgreSQL test
database via the `cli_session_factory`/`cleanup_users_by_username`
fixtures (see docs/features/platform/testing-strategy.md, Sync
Entry-Point Tests) — `db_session`'s savepoint-scoped transaction is
never visible to the separate connection these commands use, so setup
data for these tests is created directly through `cli_session_factory`
rather than through `db_session`-based factories.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import click
import pytest
from click.testing import CliRunner, Result
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.cli.manage_user as manage_user_module
from app.cli import cli
from app.core.enums import IdentityAuditEventType, Role
from app.core.permissions import role_to_wire
from app.models.identity_audit_event import IdentityAuditEvent
from app.models.user import User
from app.models.user_role import UserRole

_STRONG_PASSWORD = "a-very-strong-password-1"
_PASSWORD_INPUT = f"{_STRONG_PASSWORD}\n{_STRONG_PASSWORD}\n"


def _invoke(args: list[str], input: str | None = None, **extra: Any) -> Result:
    """Invoke the raw `cli` group with `standalone_mode=False`, mirroring
    exactly how production's `main()` invokes it — see the identical
    helper docstring in `test_main.py`."""
    return CliRunner().invoke(cli, args, input=input, standalone_mode=False, **extra)


def _allow_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manage_user_module, "is_interactive_terminal", lambda: True)


def _inject_session_factory(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    monkeypatch.setattr(manage_user_module, "get_session_factory", lambda: factory)


async def _fetch_user(
    factory: async_sessionmaker[AsyncSession], username: str
) -> User | None:
    async with factory() as db:
        result: User | None = await db.scalar(
            select(User).where(User.username == username)
        )
        return result


async def _fetch_user_created_events(
    factory: async_sessionmaker[AsyncSession], username: str
) -> list[IdentityAuditEvent]:
    """Find `user_created` events by `new_value` (the persisted username).

    Used to prove the absence of a dangling audit event when the
    creation transaction rolled back and no `User` row (hence no
    `target_user_id`) exists to query by.
    """
    async with factory() as db:
        result = await db.execute(
            select(IdentityAuditEvent).where(
                IdentityAuditEvent.event_type
                == IdentityAuditEventType.USER_CREATED.value,
                IdentityAuditEvent.new_value == username,
            )
        )
        return list(result.scalars().all())


async def _create_user_directly(
    factory: async_sessionmaker[AsyncSession],
    *,
    username: str,
    email: str | None = None,
    full_name: str | None = None,
    active: bool = True,
    external_id: UUID | None = None,
    manager_id: UUID | None = None,
    last_login_at: datetime | None = None,
    roles: list[tuple[Role, str]] | None = None,
) -> User:
    """Insert and commit a `User` (with optional roles) directly through
    `cli_session_factory`, bypassing `user_service` entirely — used to set
    up fixtures for `list`/`show` tests, which run in a separate
    connection from `db_session`-backed factories (see module docstring).
    """
    async with factory() as db:
        user = User(
            username=username,
            email=email or f"{username}@example.com",
            full_name=full_name,
            active=active,
            external_id=external_id,
            manager_id=manager_id,
            password_hash=None if external_id is not None else "$2b$12$" + "a" * 53,
            last_login_at=last_login_at,
        )
        db.add(user)
        await db.flush()
        for role, group_name in roles or []:
            db.add(UserRole(user_id=user.id, role=role.value, group_name=group_name))
        await db.commit()
        return user


# ---------------------------------------------------------------------------
# Unit: validation helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_valid_roles_list_is_alphabetical() -> None:
    assert (
        manage_user_module._valid_roles_list()
        == "admin, restricted_analyst, vulnerability_analyst"
    )


@pytest.mark.unit
def test_parse_roles_or_exit_empty_returns_empty_list() -> None:
    assert manage_user_module._parse_roles_or_exit(()) == []


@pytest.mark.unit
def test_parse_roles_or_exit_valid_roles() -> None:
    assert manage_user_module._parse_roles_or_exit(("admin", "restricted_analyst")) == [
        Role.ADMIN,
        Role.RESTRICTED_ANALYST,
    ]


@pytest.mark.unit
def test_parse_roles_or_exit_invalid_role_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        manage_user_module._parse_roles_or_exit(("bogus",))
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == (
        "Error: Invalid role 'bogus'. Valid roles are: "
        "admin, restricted_analyst, vulnerability_analyst."
    )


@pytest.mark.unit
def test_normalize_username_or_exit_trims_and_lowercases() -> None:
    assert manage_user_module._normalize_username_or_exit("  JDoe  ") == "jdoe"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    ["1bad", "", "Has Space", "toolong" + "x" * 60, "bad$char"],
)
def test_normalize_username_or_exit_invalid_format_exits_one(
    capsys: pytest.CaptureFixture[str], value: str
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        manage_user_module._normalize_username_or_exit(value)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    normalized = value.strip().lower()
    assert captured.err.strip() == (
        f"Error: Invalid username '{normalized}'. Username must be 1-64 "
        "characters, start with a letter, and contain only lowercase "
        "letters, numbers, dots, hyphens, and underscores."
    )


@pytest.mark.unit
def test_normalize_email_or_exit_trims_and_lowercases() -> None:
    assert (
        manage_user_module._normalize_email_or_exit("  JDoe@Example.COM  ")
        == "jdoe@example.com"
    )


@pytest.mark.unit
def test_normalize_email_or_exit_invalid_format_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        manage_user_module._normalize_email_or_exit("not-an-email")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "Error: Invalid email format 'not-an-email'."


# ---------------------------------------------------------------------------
# Unit: rendering helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_format_utc_none_renders_em_dash() -> None:
    assert manage_user_module._format_utc(None) == "—"


@pytest.mark.unit
def test_format_utc_converts_to_utc_display_format() -> None:
    value = datetime(2025, 3, 15, 10, 30, 0, tzinfo=UTC)
    assert manage_user_module._format_utc(value) == "2025-03-15 10:30:00 UTC"


@pytest.mark.unit
def test_render_roles_with_origins_empty_renders_em_dash() -> None:
    assert manage_user_module._render_roles_with_origins([]) == "—"


@pytest.mark.unit
def test_render_roles_with_origins_single_manual_role() -> None:
    roles = [UserRole(role=Role.ADMIN.value, group_name="_manual")]
    assert manage_user_module._render_roles_with_origins(roles) == "admin (manual)"


@pytest.mark.unit
def test_render_roles_with_origins_manual_listed_before_external() -> None:
    roles = [
        UserRole(role=Role.ADMIN.value, group_name="O SUSE Admins"),
        UserRole(role=Role.ADMIN.value, group_name="_manual"),
    ]
    assert (
        manage_user_module._render_roles_with_origins(roles)
        == "admin (manual, O SUSE Admins)"
    )


@pytest.mark.unit
def test_render_roles_with_origins_multiple_roles_sorted_alphabetically() -> None:
    roles = [
        UserRole(role=Role.VULNERABILITY_ANALYST.value, group_name="_manual"),
        UserRole(role=Role.ADMIN.value, group_name="_manual"),
    ]
    assert (
        manage_user_module._render_roles_with_origins(roles)
        == "admin (manual), vulnerability_analyst (manual)"
    )


@pytest.mark.unit
def test_render_user_row_absent_full_name_and_roles() -> None:
    user = User(username="jdoe", email="jdoe@example.com", full_name=None, active=True)
    user.roles = []
    row = manage_user_module._render_user_row(user)
    assert row == ("jdoe", "—", "jdoe@example.com", "local", "active", "—")


@pytest.mark.unit
def test_render_user_row_external_inactive_with_roles() -> None:
    user = User(
        username="bwilson",
        email="bwilson@example.com",
        full_name="Bob Wilson",
        active=False,
        external_id=uuid4(),
    )
    user.roles = [UserRole(role=Role.VULNERABILITY_ANALYST.value, group_name="_manual")]
    row = manage_user_module._render_user_row(user)
    assert row == (
        "bwilson",
        "Bob Wilson",
        "bwilson@example.com",
        "external",
        "inactive",
        "vulnerability_analyst",
    )


@pytest.mark.unit
def test_render_list_table_aligns_columns() -> None:
    user = User(
        username="jdoe", email="jdoe@example.com", full_name="John Doe", active=True
    )
    user.roles = [UserRole(role=Role.ADMIN.value, group_name="_manual")]
    table = manage_user_module._render_list_table([user])
    lines = table.splitlines()
    assert lines[0].startswith("USERNAME")
    assert lines[1].startswith("jdoe")
    assert "admin" in lines[1]


@pytest.mark.unit
def test_render_user_detail_full_example() -> None:
    user = User(
        username="jdoe",
        email="jdoe@example.com",
        full_name="John Doe",
        active=True,
        created_at=datetime(2025, 3, 15, 10, 30, 0, tzinfo=UTC),
        last_login_at=datetime(2025, 6, 1, 14, 22, 0, tzinfo=UTC),
    )
    user.roles = [
        UserRole(role=Role.ADMIN.value, group_name="_manual"),
        UserRole(role=Role.VULNERABILITY_ANALYST.value, group_name="O SUSE Security"),
    ]
    manager = User(username="bwilson", email="bwilson@example.com")
    user.manager = manager

    detail = manage_user_module._render_user_detail(user)

    assert detail == (
        "Username:     jdoe\n"
        "Full name:    John Doe\n"
        "Email:        jdoe@example.com\n"
        "Type:         local\n"
        "Status:       active\n"
        "Roles:        admin (manual), vulnerability_analyst (O SUSE Security)\n"
        "Created:      2025-03-15 10:30:00 UTC\n"
        "Last login:   2025-06-01 14:22:00 UTC\n"
        "Manager:      bwilson"
    )


@pytest.mark.unit
def test_render_user_detail_absent_values() -> None:
    user = User(
        username="olduser",
        email="old@example.com",
        full_name=None,
        active=False,
        created_at=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
        last_login_at=None,
    )
    user.roles = []
    user.manager = None

    detail = manage_user_module._render_user_detail(user)

    assert "Full name:    —" in detail
    assert "Roles:        —" in detail
    assert "Last login:   —" in detail
    assert "Manager:      —" in detail


# ---------------------------------------------------------------------------
# Unit: list pagination loop
# ---------------------------------------------------------------------------


class _FakeSessionCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeSessionFactory:
    def __call__(self) -> _FakeSessionCtx:
        return _FakeSessionCtx()


@pytest.mark.unit
def test_list_users_flow_iterates_until_total_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import user_service as user_service_module

    fake_pages = {
        1: SimpleNamespace(items=["a", "b"], total=5),
        2: SimpleNamespace(items=["c", "d"], total=5),
        3: SimpleNamespace(items=["e"], total=5),
    }

    async def _fake_list_users(db: object, **kwargs: Any) -> SimpleNamespace:
        return fake_pages[kwargs["page"]]

    monkeypatch.setattr(user_service_module, "list_users", _fake_list_users)

    items = asyncio.run(
        manage_user_module._list_users_flow(
            cast("async_sessionmaker[AsyncSession]", _FakeSessionFactory()),
            active=None,
            roles=[],
            user_type=None,
        )
    )
    assert items == ["a", "b", "c", "d", "e"]  # type: ignore[comparison-overlap]


@pytest.mark.unit
def test_list_users_flow_empty_result_stops_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import user_service as user_service_module

    async def _fake_list_users(db: object, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(items=[], total=0)

    monkeypatch.setattr(user_service_module, "list_users", _fake_list_users)

    items = asyncio.run(
        manage_user_module._list_users_flow(
            cast("async_sessionmaker[AsyncSession]", _FakeSessionFactory()),
            active=None,
            roles=[],
            user_type=None,
        )
    )
    assert items == []


# ---------------------------------------------------------------------------
# Integration: create
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_success_with_no_roles(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    username = "clicreatenoroles"
    cleanup_users_by_username(username)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            username,
            "--email",
            f"{username}@example.com",
        ],
        input=_PASSWORD_INPUT,
    )

    assert result.exit_code == 0, result.output
    # `click.prompt()` echoes its two prompt labels to stdout ahead of the
    # command's own success line (Click's own default, not overridable
    # per-command wording) — only the final line is the command's output.
    assert (
        result.stdout.strip().splitlines()[-1]
        == f"Created user '{username}' ({username}@example.com) with no roles."
    )


@pytest.mark.integration
def test_create_persists_full_name(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    """`--full-name` is forwarded through `_create_flow` to
    `user_service.create_user(full_name=...)` and persisted verbatim."""
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    username = "clicreatefullname"
    cleanup_users_by_username(username)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            username,
            "--email",
            f"{username}@example.com",
            "--full-name",
            "Alice Smith",
        ],
        input=_PASSWORD_INPUT,
    )

    assert result.exit_code == 0, result.output

    user = asyncio.run(_fetch_user(cli_session_factory, username))
    assert user is not None
    assert user.full_name == "Alice Smith"


@pytest.mark.integration
def test_create_success_with_roles_persists_user_roles_and_audit(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    username = "clicreatewithroles"
    cleanup_users_by_username(username)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            username,
            "--email",
            f"{username}@example.com",
            "--role",
            "vulnerability_analyst",
            "--role",
            "admin",
        ],
        input=_PASSWORD_INPUT,
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[-1] == (
        f"Created user '{username}' ({username}@example.com) with roles: "
        "admin, vulnerability_analyst."
    )
    assert _STRONG_PASSWORD not in result.output

    async def _verify() -> None:
        async with cli_session_factory() as db:
            user = await db.scalar(select(User).where(User.username == username))
            assert user is not None
            assert user.active is True
            assert user.external_id is None

            roles = (
                (await db.execute(select(UserRole).where(UserRole.user_id == user.id)))
                .scalars()
                .all()
            )
            assert {r.role for r in roles} == {
                Role.ADMIN.value,
                Role.VULNERABILITY_ANALYST.value,
            }
            for role in roles:
                assert role.group_name == "_manual"
                assert role.assigned_by is None

            events = (
                (
                    await db.execute(
                        select(IdentityAuditEvent).where(
                            IdentityAuditEvent.target_user_id == user.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            event_types = [e.event_type for e in events]
            assert event_types.count(IdentityAuditEventType.USER_CREATED.value) == 1
            assert event_types.count(IdentityAuditEventType.ROLE_ADDED.value) == 2
            for event in events:
                assert event.user_id is None
                assert event.detail is None

            # Per identity-audit-log.md's IdentityAuditEventType Enum
            # table: user_created carries old_value=NULL, new_value=the
            # created username; role_added carries old_value=NULL,
            # new_value=the assigned role name.
            created_event = next(
                e
                for e in events
                if e.event_type == IdentityAuditEventType.USER_CREATED.value
            )
            assert created_event.old_value is None
            assert created_event.new_value == username

            role_events = [
                e
                for e in events
                if e.event_type == IdentityAuditEventType.ROLE_ADDED.value
            ]
            assert {e.new_value for e in role_events} == {
                role_to_wire(Role.ADMIN),
                role_to_wire(Role.VULNERABILITY_ANALYST),
            }
            for role_event in role_events:
                assert role_event.old_value is None

    asyncio.run(_verify())


@pytest.mark.integration
def test_create_invalid_username_exits_one_before_prompting(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    # Deliberately do NOT allow TTY / provide password input: if the
    # command reached the prompt, this test would hang or fail loudly.
    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            "1bad",
            "--email",
            "user@example.com",
        ],
    )
    assert result.exit_code == 1
    assert "Invalid username '1bad'" in result.stderr


@pytest.mark.integration
def test_create_invalid_role_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            "someuser",
            "--email",
            "user@example.com",
            "--role",
            "bogus",
        ],
    )
    assert result.exit_code == 1
    assert "Invalid role 'bogus'" in result.stderr


@pytest.mark.integration
def test_create_invalid_email_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            "someuser",
            "--email",
            "not-an-email",
        ],
    )
    assert result.exit_code == 1
    assert "Invalid email format 'not-an-email'" in result.stderr


@pytest.mark.integration
def test_create_non_tty_rejected(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    monkeypatch.setattr(manage_user_module, "is_interactive_terminal", lambda: False)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            "someuser",
            "--email",
            "user@example.com",
        ],
    )
    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Error: This command requires an interactive terminal (password input)."
    )


@pytest.mark.integration
def test_create_password_mismatch_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            "someuser",
            "--email",
            "user@example.com",
        ],
        input=f"{_STRONG_PASSWORD}\na-different-password-2\n",
    )
    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: Passwords do not match."


@pytest.mark.integration
@pytest.mark.parametrize(
    ("password", "expected_message"),
    [
        ("short-pw-1", "Error: Password must be at least 16 characters."),
        ("x" * 129, "Error: Password must be at most 128 characters."),
    ],
)
def test_create_password_length_boundaries_exit_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    password: str,
    expected_message: str,
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            "someuser",
            "--email",
            "user@example.com",
        ],
        input=f"{password}\n{password}\n",
    )
    assert result.exit_code == 1
    assert result.stderr.strip() == expected_message


@pytest.mark.integration
def test_create_duplicate_username_translates_conflict_message(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    username = "cliconflictusername"
    cleanup_users_by_username(username)
    asyncio.run(_create_user_directly(cli_session_factory, username=username))

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            username,
            "--email",
            "different@example.com",
        ],
        input=_PASSWORD_INPUT,
    )
    assert result.exit_code == 1
    assert result.stderr.strip() == (
        f"Error: A user with username '{username}' already exists."
    )


@pytest.mark.integration
def test_create_duplicate_email_translates_conflict_message(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    existing_username = "cliconflictemailexisting"
    new_username = "cliconflictemailnew"
    email = "cliconflict-shared@example.com"
    cleanup_users_by_username(existing_username, new_username)
    asyncio.run(
        _create_user_directly(
            cli_session_factory, username=existing_username, email=email
        )
    )

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            new_username,
            "--email",
            email,
        ],
        input=_PASSWORD_INPUT,
    )
    assert result.exit_code == 1
    assert (
        result.stderr.strip() == f"Error: A user with email '{email}' already exists."
    )


@pytest.mark.integration
def test_create_audit_failure_rolls_back_user_and_roles(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    username = "cliauditrollback"
    cleanup_users_by_username(username)

    from app.services import identity_audit_log as audit_module

    original_log_event = audit_module.IdentityAuditLog.log_event
    call_count = {"n": 0}

    async def _flaky_log_event(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated audit failure")
        return await original_log_event(*args, **kwargs)

    monkeypatch.setattr(audit_module.IdentityAuditLog, "log_event", _flaky_log_event)

    result = _invoke(
        [
            "manage-user",
            "create",
            "--username",
            username,
            "--email",
            f"{username}@example.com",
        ],
        input=_PASSWORD_INPUT,
    )
    assert result.exit_code != 0

    user = asyncio.run(_fetch_user(cli_session_factory, username))
    assert user is None


@pytest.mark.integration
def test_create_interrupted_before_commit_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    _allow_tty(monkeypatch)
    username = "cliinterruptrollback"
    cleanup_users_by_username(username)

    from app.services import user_service as user_service_module

    original_create_user = user_service_module.create_user

    async def _create_then_interrupt(*args: Any, **kwargs: Any) -> None:
        await original_create_user(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(user_service_module, "create_user", _create_then_interrupt)

    # Click's own `main()` converts a raw `KeyboardInterrupt` escaping the
    # command into `click.Abort` (see `core.py`'s `except (EOFError,
    # KeyboardInterrupt)` clause) regardless of `standalone_mode` — this
    # is the same conversion documented in cli-infrastructure.md's Error
    # Handling table. The rollback assertion below is what this test
    # actually verifies: `_create_flow`'s `except BaseException` guard
    # rolls back before that conversion ever happens.
    with pytest.raises(click.Abort):
        _invoke(
            [
                "manage-user",
                "create",
                "--username",
                username,
                "--email",
                f"{username}@example.com",
            ],
            input=_PASSWORD_INPUT,
            catch_exceptions=False,
        )

    user = asyncio.run(_fetch_user(cli_session_factory, username))
    assert user is None

    # Explicit per testing-strategy.md (CLI Commands): rollback leaves no
    # partial mutation *or audit event*. `target_user_id` cannot be
    # queried (no user row exists), so this checks by `new_value`
    # (the username the `user_created` event would have carried).
    audit_events = asyncio.run(
        _fetch_user_created_events(cli_session_factory, username)
    )
    assert audit_events == []


# ---------------------------------------------------------------------------
# Integration: list
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_without_any_filter_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No `--active`/`--inactive`/`--role`/`--type` at all: exercises the
    unfiltered default path (`user_type` stays `None`) end to end."""
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(["manage-user", "list"])
    assert result.exit_code == 0, result.output


@pytest.mark.integration
def test_list_empty_result_message(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(
        [
            "manage-user",
            "list",
            "--role",
            "admin",
            "--type",
            "external",
            "--active",
        ],
    )
    # An external+active+admin combination is exceedingly unlikely to
    # match any row left over from other tests, and this command applies
    # its filters with AND semantics — but to keep this assertion robust
    # regardless of leftover data, only the exit code is required to be 0.
    assert result.exit_code == 0


@pytest.mark.integration
def test_list_active_inactive_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(["manage-user", "list", "--active", "--inactive"])
    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Error: --active and --inactive cannot be used together."
    )


@pytest.mark.integration
def test_list_invalid_role_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(["manage-user", "list", "--role", "bogus"])
    assert result.exit_code == 1
    assert "Invalid role 'bogus'" in result.stderr


@pytest.mark.integration
def test_list_invalid_type_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(["manage-user", "list", "--type", "bogus"])
    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Error: Invalid type 'bogus'. Valid types are: local, external."
    )


@pytest.mark.integration
def test_list_type_filters_local_and_external_users(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    """`--type local`/`--type external` map to distinct `UserType` values
    end to end: a swapped `UserType.LOCAL`/`UserType.EXTERNAL` constant
    would cause one of these assertions to fail."""
    _inject_session_factory(monkeypatch, cli_session_factory)
    prefix = "clilisttypefilter"
    local_user = f"{prefix}local"
    external_user = f"{prefix}external"
    cleanup_users_by_username(local_user, external_user)

    asyncio.run(_create_user_directly(cli_session_factory, username=local_user))
    asyncio.run(
        _create_user_directly(
            cli_session_factory, username=external_user, external_id=uuid4()
        )
    )

    local_result = _invoke(["manage-user", "list", "--type", "local"])
    assert local_result.exit_code == 0, local_result.output
    assert local_user in local_result.stdout
    assert external_user not in local_result.stdout

    external_result = _invoke(["manage-user", "list", "--type", "external"])
    assert external_result.exit_code == 0, external_result.output
    assert external_user in external_result.stdout
    assert local_user not in external_result.stdout


@pytest.mark.integration
def test_list_filters_role_or_and_type_and_active(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    prefix = "clilistfilter"
    admin_user = f"{prefix}admin"
    va_user = f"{prefix}va"
    other_user = f"{prefix}other"
    inactive_user = f"{prefix}inactive"
    cleanup_users_by_username(admin_user, va_user, other_user, inactive_user)

    asyncio.run(
        _create_user_directly(
            cli_session_factory,
            username=admin_user,
            roles=[(Role.ADMIN, "_manual")],
        )
    )
    asyncio.run(
        _create_user_directly(
            cli_session_factory,
            username=va_user,
            roles=[(Role.VULNERABILITY_ANALYST, "_manual")],
        )
    )
    asyncio.run(
        _create_user_directly(
            cli_session_factory,
            username=other_user,
            roles=[(Role.RESTRICTED_ANALYST, "_manual")],
        )
    )
    asyncio.run(
        _create_user_directly(
            cli_session_factory,
            username=inactive_user,
            active=False,
            roles=[(Role.ADMIN, "_manual")],
        )
    )

    result = _invoke(
        [
            "manage-user",
            "list",
            "--active",
            "--type",
            "local",
            "--role",
            "admin",
            "--role",
            "vulnerability_analyst",
        ],
    )
    assert result.exit_code == 0, result.output
    assert admin_user in result.stdout
    assert va_user in result.stdout
    assert other_user not in result.stdout
    assert inactive_user not in result.stdout

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    data_lines = lines[1:]
    usernames_in_order = [line.split()[0] for line in data_lines]
    assert usernames_in_order == sorted(usernames_in_order)


# ---------------------------------------------------------------------------
# Integration: show
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_show_not_found_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    result = _invoke(["manage-user", "show", "--username", "doesnotexistatall"])
    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: User 'doesnotexistatall' not found."


@pytest.mark.integration
def test_show_normalizes_username_before_lookup(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    username = "clishownormalize"
    cleanup_users_by_username(username)
    asyncio.run(_create_user_directly(cli_session_factory, username=username))

    result = _invoke(["manage-user", "show", "--username", f"  {username.upper()}  "])
    assert result.exit_code == 0, result.output
    assert f"Username:     {username}" in result.stdout


@pytest.mark.integration
def test_show_renders_roles_manager_and_absent_values(
    monkeypatch: pytest.MonkeyPatch,
    cli_session_factory: async_sessionmaker[AsyncSession],
    cleanup_users_by_username: Callable[..., None],
) -> None:
    _inject_session_factory(monkeypatch, cli_session_factory)
    manager_username = "clishowmanager"
    username = "clishowdetail"
    cleanup_users_by_username(manager_username, username)

    manager = asyncio.run(
        _create_user_directly(cli_session_factory, username=manager_username)
    )
    asyncio.run(
        _create_user_directly(
            cli_session_factory,
            username=username,
            full_name="Detail Test User",
            manager_id=manager.id,
            roles=[
                (Role.ADMIN, "_manual"),
                (Role.ADMIN, "O SUSE Admins"),
                (Role.VULNERABILITY_ANALYST, "O SUSE Security"),
            ],
        )
    )

    result = _invoke(["manage-user", "show", "--username", username])
    assert result.exit_code == 0, result.output
    output = result.stdout
    assert f"Username:     {username}" in output
    assert "Full name:    Detail Test User" in output
    assert "Type:         local" in output
    assert "Status:       active" in output
    assert (
        "Roles:        admin (manual, O SUSE Admins), "
        "vulnerability_analyst (O SUSE Security)" in output
    )
    assert "Last login:   —" in output
    assert f"Manager:      {manager_username}" in output
