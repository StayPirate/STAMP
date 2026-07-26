# Testing Strategy

## Purpose

Define the testing methodology, infrastructure, and policies for the
Sentinel backend. This is the single canonical source for how tests are
structured, executed, and enforced. `docs/conventions.md` (Testing
Conventions) contains brief style rules and references this document for
the full strategy.

Scope boundary: this spec covers the backend test suite
(`backend/tests/`). CI pipeline configuration and local automation
(pre-commit hooks) are specified here at the requirements level; their
implementation is owned by `.github/workflows/ci.yml` and
`.githooks/` respectively.

---

## Test Pyramid

Sentinel uses a three-tier test pyramid. Each tier has distinct
characteristics and isolation rules.

### Tier 1 — Unit Tests

Fast, isolated tests for pure logic. No external dependencies.

| Property | Value |
|----------|-------|
| Marker | `@pytest.mark.unit` |
| Isolation | In-process only. No database, no Redis, no network I/O |
| Speed target | Full unit suite < 10 seconds |
| Typical subjects | Resolution cascades, parsers, validators, status evaluators, enum logic, Pydantic schema validation, utility functions |

A test that requires a database session, Redis connection, or HTTP
client is NOT a unit test — even if it tests a single function. Use
the `integration` marker instead.

### Tier 2 — Integration Tests

Tests that exercise service-layer functions with real database state.

| Property | Value |
|----------|-------|
| Marker | `@pytest.mark.integration` |
| Isolation | Real PostgreSQL (per-test transaction rollback). Redis available through the shared fixture when needed. No external network I/O |
| Speed target | Full integration suite < 120 seconds |
| Typical subjects | Service functions, CRUD operations, audit event creation, status gate evaluation, query builders, authorization checks |

Integration tests are the **primary verification layer** for
business logic. They verify that service functions interact correctly
with the database, create the right audit events, enforce constraints,
and produce the expected state transitions.

### Tier 3 — End-to-End Tests

Tests that exercise the full HTTP request/response cycle through
FastAPI's test client.

| Property | Value |
|----------|-------|
| Marker | `@pytest.mark.e2e` |
| Isolation | Real PostgreSQL + FastAPI test client (ASGI transport). Redis available via the shared fixture when exercised by the request path. No external network I/O |
| Speed target | Full e2e suite < 60 seconds |
| Typical subjects | API endpoint handlers, request validation, response schemas, authentication/authorization enforcement, error envelope format, pagination |

E2e tests verify the API contract — correct status codes, response
shapes, error codes, and permission enforcement. Business logic
correctness is covered by integration tests; e2e tests focus on the
HTTP layer.

### Default Marker

Tests without an explicit marker are treated as **integration** tests.
This is the safe default — a test that accidentally omits its marker
gets the full database fixture rather than failing mysteriously.

This is a classification convention, not a pytest enforcement
mechanism — `pytest -m integration` selects only tests explicitly
marked `@pytest.mark.integration`, not all unmarked tests. To run
integration tests plus unmarked tests, use `pytest` without a marker
filter (the full suite includes them).

The `asyncio_mode = "auto"` setting in `pyproject.toml` applies to all
tiers (all async tests run without manual `@pytest.mark.asyncio`
decoration). The `asyncio_default_fixture_loop_scope = "session"` and
`asyncio_default_test_loop_scope = "session"` settings ensure all async
fixtures and tests share a single session-scoped event loop, which is
required for the session-scoped engine fixture to work correctly with
function-scoped test functions.

### Marker Registration

All custom markers (`unit`, `integration`, `e2e`) MUST be registered in
`pyproject.toml` under `[tool.pytest.ini_options]` to avoid
`PytestUnknownMarkWarning`:

```ini
markers = [
    "unit: Fast, isolated tests (no DB, no Redis, no network)",
    "integration: Tests with real PostgreSQL",
    "e2e: Full HTTP request/response cycle tests",
]
```

---

## Database Strategy

### Engine: PostgreSQL Only

All tests run against a real PostgreSQL instance. SQLite is not used —
the data model relies on PostgreSQL-specific features (JSONB,
TIMESTAMPTZ, ARRAY types, partial indexes, `FOR UPDATE` locking) that
SQLite does not support or emulates differently. Testing against SQLite
would hide bugs.

The `aiosqlite` dependency MUST NOT be included in dev dependencies —
it would allow writing tests against SQLite, creating a divergence from
the PostgreSQL-only policy.

### Database Provisioning

Two provisioning modes, selected automatically:

1. **CI environment**: when the `TEST_DATABASE_URL` environment variable
   is set (the CI workflow sets it to the PostgreSQL service container),
   the test engine connects directly. No container management needed.

2. **Local development**: when `TEST_DATABASE_URL` is not set, the
   `conftest.py` fixture uses `testcontainers` to start an ephemeral
   PostgreSQL 16 container automatically. The container is created once
   per test session and destroyed at the end. Zero manual setup required
   — the developer just runs `pytest`.

   Prerequisite: Docker or Podman must be available locally (already
   required by `dev-env.sh`).

### Schema Setup

At the start of each test session, the test engine runs
`Base.metadata.create_all()` to create all tables from SQLAlchemy model
definitions. This is faster than running Alembic migrations and
sufficient for correctness — the Alembic drift check (see Execution
Model, CI) separately verifies that migrations stay in sync with models.

### Per-Test Isolation: Transaction Rollback

Each test runs inside a **nested transaction** (savepoint) that is
rolled back after the test completes. This provides:

- **Speed**: no table drops/recreates between tests.
- **Isolation**: each test sees a clean database state.
- **Correctness**: tests cannot pollute each other.

Pattern (in `conftest.py`):

1. A session-scoped async fixture (using `pytest_asyncio.fixture` with
   `loop_scope="session"`) creates the async engine and runs
   `create_all`. The global `asyncio_default_fixture_loop_scope` and
   `asyncio_default_test_loop_scope` settings in `pyproject.toml` ensure
   all fixtures and tests share this session-scoped event loop.
2. A function-scoped `db_session` fixture begins a transaction, creates
   a savepoint, yields the session, then rolls back to the savepoint and
   closes the transaction.
3. The `client` fixture overrides `get_db` to use the same
   `db_session`, so HTTP requests in e2e tests share the test
   transaction.

### Alembic Drift Check

A CI step verifies that no model changes exist without a corresponding
Alembic migration. The check runs `alembic check` (or equivalent
autogenerate dry-run) and fails the build if differences are detected.
This ensures that developers who add or modify models also create the
matching migration.

### Concurrency Testing

The standard `db_session` fixture provides a single connection with a
wrapping transaction that is rolled back after each test. Within a
single transaction, `SELECT ... FOR UPDATE` is a no-op — the lock is
already held by the same transaction, so it never blocks. This means
the per-test rollback pattern **cannot** verify that `FOR UPDATE` locks
serialize concurrent mutations correctly (see `docs/conventions.md`,
Transaction and Locking).

Tests that need to verify lock serialization MUST use the
`db_session_factory` fixture, which creates independent sessions with
independent connections and transactions. The canonical two-session
pattern is:

1. Create **session A** and **session B** from `db_session_factory`.
2. In **A**: insert test data, flush, then acquire the `FOR UPDATE`
   lock on the target row.
3. Launch **B** as an `asyncio.Task` that attempts `SELECT ... FOR
   UPDATE` on the same row.
4. Verify that **B** blocks by using `asyncio.wait_for(task_B,
   timeout=0.5)` and catching `asyncio.TimeoutError`. A timeout
   confirms that B is blocked on the lock held by A.
5. Release the lock in **A** (rollback or commit).
6. Await **B**'s completion — it should now acquire the lock
   successfully.
7. Both sessions are tracked by the factory; fixture teardown rolls
   back open transactions and closes all sessions and connections.

When a test requires **commit** (e.g., to verify post-commit
visibility), the committed data is not rolled back by the fixture.
The test MUST perform explicit `DELETE` cleanup before the factory
teardown runs. Session and connection closure is still handled by the
fixture.

---

## Redis Strategy

Tier 2 tests and Tier 3 tests whose exercised request path uses Redis
access Redis through the shared `redis_client` fixture. Tests MUST NOT
create ad-hoc connections to application-configured Redis instances.

### Provisioning

Redis 7 is provisioned at session scope beneath the function-scoped
fixture. The test harness selects one of two modes:

1. **Configured test server**: when `TEST_REDIS_URL` is set (notably in
   CI), the harness connects to that designated Redis 7 test server and
   database range.
2. **Local development**: when `TEST_REDIS_URL` is absent, the harness
   starts an ephemeral Redis 7 container with `testcontainers`. The
   container is shared for the test session and destroyed afterward.

`TEST_REDIS_URL` is test-harness-only configuration, not Sentinel
runtime configuration. Tests MUST never use `REDIS_URL` or
`CELERY_BROKER_URL` as fixture storage, even when either variable points
to a locally available server.

### Worker and Test Isolation

Each pytest worker receives a dedicated Redis logical database. The
database encoded in `TEST_REDIS_URL` is the start of the test harness's
designated range; additional workers use consecutive logical databases.
The harness MUST verify that each worker maps to a distinct database and
that the Redis server provides enough logical databases. It MUST fail
explicitly if the range is insufficient or if the allocation could
collide with another owner; it must not continue with unsafe sharing.

All Redis clients and application processes created within one test MUST
use that test's worker database. This intentionally preserves realistic
contention, locking, and key-collision behavior within the test. Pytest
workers are suite executors, not substitutes for application replicas;
concurrency tests create multiple clients or processes inside one test
against the same logical database.

The fixture runs `FLUSHDB` before and after every test. It MUST never run
`FLUSHALL`, because other workers use other logical databases. An
unreachable server, an unsafe database allocation, or failed cleanup is
a test failure rather than a skip.

Scenarios whose behavior is server-global and cannot safely share a
server use a Redis 7 container dedicated to that test. This includes
memory exhaustion or eviction, server restart or unavailability, and
Pub/Sub scenarios where server lifecycle affects the result. Tests of
application outage handling do not stop a shared Redis service; they
inject a client that raises `RedisError` instead.

---

## Fixture Catalog

### Available Fixtures

| Fixture | Scope | Tier | Description |
|---------|-------|------|-------------|
| `db_session` | function | integration, e2e | Async SQLAlchemy session with per-test rollback |
| `client` | function | e2e | `httpx.AsyncClient` with ASGI transport, `get_db` overridden to use `db_session` |

### Planned Fixtures (added with their features)

The following fixtures will be created when the corresponding features
are implemented. They are documented here so implementers know the
expected patterns:

| Fixture | Scope | Tier | Created with |
|---------|-------|------|--------------|
| `authenticated_client` | function | e2e | Authentication feature (`local-authentication.md`) |
| `admin_client` | function | e2e | RBAC feature (`rbac.md`) |
| `user_factory` | function | integration, e2e | User model implementation (`user-management.md`) |
| `ticket_factory` | function | integration, e2e | Ticket model implementation (`tickets.md`) |
| `cve_factory` | function | integration, e2e | CVE model implementation (`cve-tracking.md`) |
| `db_session_factory` | function | integration, e2e | First concurrency/locking test (pessimistic locking pattern) |
| `redis_client` | function | integration, e2e | First Redis-dependent feature; follows Redis Strategy |

Factory fixtures use `factory-boy` (already in dev dependencies) and
follow the pattern: `<model>_factory` returns a callable that creates a
model instance with sensible defaults, accepting keyword overrides.

The planned `redis_client` fixture yields an asynchronous
`redis.asyncio.Redis` client configured with decoded string responses.
Its session-scoped provisioning layer selects the worker database and
verifies connectivity with `PING`. Before yielding, it runs `FLUSHDB`
and overrides applicable application Redis dependencies so all code in
the test uses the same client. Teardown restores those overrides, runs
`FLUSHDB`, and closes the client. Provisioning, connectivity, isolation,
or cleanup failures fail the test suite; they are never converted to
skips.

The planned `db_session_factory` fixture is an async callable
(`async def () -> AsyncSession`) that creates a new `AsyncSession` with
an independent connection and transaction on each call. Internally, the
factory uses the session-scoped `_engine` fixture (tests MUST NOT
interact with the engine directly). The factory tracks all sessions it
creates. During teardown, it rolls back all open transactions and closes
all sessions and connections — even if the test did not clean up
explicitly. If the database is unreachable or cleanup fails, the test
fails (never skips). This fixture is used exclusively for concurrency
and locking tests; all other integration tests continue using
`db_session`.

### Fixture Location

All shared fixtures live in `backend/tests/conftest.py`. Domain-specific
fixtures that are only useful within a single test directory (e.g.,
`tests/test_services/conftest.py`) may be defined locally, but shared
fixtures must not be duplicated.

---

## Coverage Policy

### Two Independent Gates

The CI pipeline enforces **two independent quality gates**. Both must
pass; neither substitutes for the other:

1. **Test pass/fail**: if ANY test fails, the build fails immediately.
   A single broken test blocks the build regardless of coverage
   percentage. This is the primary correctness control.

2. **Coverage threshold**: the build fails if line coverage drops below
   **85%** (`--cov-fail-under=85`). This prevents untested code from
   entering the codebase silently.

### Why 85%, Not 100%

- Coverage measures **quantity** (lines executed), not **quality**
  (correctness of assertions). 100% coverage with weak assertions
  provides false confidence.
- The last 10–15% typically consists of defensive error handlers,
  platform-specific branches, and boilerplate that is expensive to
  test and yields diminishing returns.
- An unrealistic target incentivizes low-value tests that game the
  metric (executing code without verifying behavior).
- 85% is a **floor, not a ceiling**. Teams should aim higher where
  practical; the threshold exists to catch regressions, not to define
  "good enough."

### Ratchet Mechanism

The coverage threshold only goes up, never down. When coverage
naturally exceeds 85% (e.g., reaches 90%), the threshold SHOULD be
raised to the new level (rounded down to nearest 5%) to prevent
regression. This is a manual adjustment made when updating the CI
configuration — not an automatic mechanism.

### Coverage Configuration

Coverage is measured by `pytest-cov` with the following settings (in
`pyproject.toml`):

- Source: `app` (the application package)
- Omissions: `*/tests/*`, `*/alembic/*`, `app/database.py` (infrastructure,
  tested via integration)
- Report format: `term-missing` in CI (shows uncovered lines)

---

## Audit Trail Testing

### General Rule

For every mutation covered by any audit trail registered in the Audit
Trail Index (`docs/features/platform/audit-trail-infrastructure.md`,
section "Audit Trail Index"), tests MUST verify that the corresponding
audit event is created in the same transaction with correct field
values.

The Audit Trail Index is the authoritative source for which audit
trails exist. As of this writing, four audit trails are registered:

| Audit Trail | Event Model | Owning Spec |
|---|---|---|
| Ticket | `TicketAuditEvent` | `docs/features/tickets/ticket-audit-log.md` |
| Identity | `IdentityAuditEvent` | `docs/features/identity/identity-audit-log.md` |
| Fetcher | `FetcherAuditEvent` | `docs/features/platform/fetcher-infrastructure.md` |
| Setting | `SettingAuditEvent` | `docs/features/platform/system-settings.md` |

This list is **not closed**. When a new audit trail is added to the
Index, the testing obligation extends automatically — no update to this
document is needed.

### What to Assert

For each audit-producing mutation, the test MUST assert:

1. **Existence**: exactly the expected number of audit events are
   created (no missing, no duplicates).
2. **Event type**: the `event_type` value matches the contract table in
   the owning spec.
3. **Actor**: `user_id` is set for user-initiated actions and `NULL` for
   system/automated actions.
4. **Payload**: `old_value`, `new_value`, and any domain-specific fields
   (`comment`, `detail`, `target_user_id`, etc.) match the contract.
5. **Atomicity**: the audit event and the mutation are visible within the
   same test transaction (i.e., no intermediate commit separates them).

### Immutability Testing

Audit event tables are append-only. No application-level UPDATE or
DELETE operations are permitted. Tests SHOULD verify the absence of
UPDATE/DELETE operations on audit event models in the service layer.
A structural test (inspecting service-layer code for prohibited
operations on audit event classes) is preferred over per-function
negative assertions.

---

## Test Structure and Naming

### Directory Structure

Test files mirror the `backend/app/` directory structure:

```
backend/tests/
├── conftest.py                 # Shared fixtures
├── test_api_conventions.py     # Structural API convention tests
├── test_api/                   # E2e tests for API endpoints
│   ├── conftest.py             # API-specific fixtures (authenticated clients)
│   └── test_<resource>.py      # One file per API resource/router
├── test_services/              # Integration tests for service layer
│   └── test_<service>.py       # One file per service module
├── test_models/                # Integration tests for model constraints
│   └── test_<model>.py         # One file per model (constraints, relationships)
└── test_tasks/                 # Integration tests for Celery tasks
    └── test_<task>.py          # One file per task module
```

### Naming Convention

Test functions follow the pattern:
`test_<what>_<condition>_<expected_result>`

Examples:
- `test_get_cve_not_found_returns_404`
- `test_set_track_status_affected_creates_audit_event`
- `test_resolve_severity_no_scores_returns_none`
- `test_create_user_duplicate_username_raises_conflict`

### Test Independence

Every test MUST be independent — it must not rely on execution order,
shared mutable state, or side effects from other tests. The per-test
transaction rollback enforces database isolation; tests must also avoid
module-level mutable state (global caches, singletons) that could leak
between tests.

When testing code that uses module-level caches (e.g.,
`FETCHER_REGISTRY`, `_CVE_SOURCE_TYPE_MAP`), the test must clean up the
cache in a fixture or teardown. See
`docs/features/platform/cve-fetcher-infrastructure.md` for the test
helper extension rule.

### Sync Entry-Point Tests

Test functions that exercise code containing `asyncio.run()` — such as
CLI commands (invoked via `CliRunner.invoke()`) or Celery task
functions called directly — MUST be synchronous (`def`, not
`async def`). With `asyncio_mode = "auto"` (see Marker Registration
above), an async test function runs inside an event loop managed by
pytest-asyncio; `asyncio.run()` in the code under test then raises
`RuntimeError: asyncio.run() cannot be called when another event loop
is running`. This applies to any synchronous entry point that bridges
into the project's async-only database layer via a single
`asyncio.run()` call (see `docs/conventions.md`, Sync-to-Async
Bridging). Fixtures for
these tests provide the async session factory itself (for the code
under test to wrap in its own `asyncio.run()` call), not a live
`AsyncSession` via an async fixture.

---

## Execution Model

### Local Development

Developers (and OpenCode agents) run tests locally using:

```bash
# Full suite
cd backend && pytest

# Unit tests only (fast feedback)
cd backend && pytest -m unit

# Integration tests only
cd backend && pytest -m integration

# Specific file or test
cd backend && pytest tests/test_services/test_ticket_mutations.py
cd backend && pytest -k "test_set_track_status"
```

When `TEST_DATABASE_URL` or `TEST_REDIS_URL` is not set, the corresponding
shared fixture automatically starts a PostgreSQL 16 or Redis 7 container
via testcontainers. Containers are reused for the test session. Redis
tests require no application `REDIS_URL` or `CELERY_BROKER_URL`; leaving
`TEST_REDIS_URL` unset is the normal local setup.

### Pre-Commit Hooks (Local Automation)

Repository-level git hooks provide fast feedback before commits reach
CI. Configured as shell scripts in `.githooks/`, activated via
`git config core.hooksPath .githooks`:

- **pre-commit**: ruff check + ruff format check + `pytest -m unit`
  (fast gate, < 15 seconds)
- **pre-push**: full test suite (`pytest`) including integration and
  e2e tests

These hooks are a supplementary safety net. The CI pipeline is the
authoritative enforcer — hooks can be bypassed in extraordinary
circumstances but CI cannot.

To activate after cloning:

```bash
git config core.hooksPath .githooks
```

### CI Pipeline

The GitHub Actions CI workflow (`.github/workflows/ci.yml`) runs on
every push to `master` and every pull request targeting `master`. It
provides the **non-bypassable enforcement layer**:

| Job | What it checks |
|-----|----------------|
| `backend-lint` | `ruff check` + `ruff format --check` |
| `backend-test` | Full test suite with coverage gate (`--cov-fail-under=85`) |
| `backend-security` | `bandit` static analysis + `pip-audit` dependency scan |

The `backend-test` job uses PostgreSQL 16 and Redis 7 as GitHub Actions
service containers, matching the production stack. The
`TEST_DATABASE_URL` and `TEST_REDIS_URL` test-harness variables point to
their respective service containers. The Redis service provides enough
dedicated logical databases for all configured pytest workers.

An additional CI step verifies Alembic migration drift — the build
fails if model definitions and migration scripts are out of sync.

---

## Mandatory Test Scenarios

Beyond the general obligation to test every implemented function (see
Guardrail 6 in `AGENTS.md`), the following scenarios are always
required when their feature area is affected:

### API Endpoints

Every new or modified API endpoint MUST be tested for:

- Happy path with valid input
- Validation errors (invalid/missing fields) → correct error code
- Authentication enforcement (unauthenticated request → 401)
- Authorization enforcement (insufficient permissions → 403)
- Resource not found → 404
- Edge cases: empty results, boundary values, concurrent modifications
  (for endpoints backed by `FOR UPDATE` locking: verify lock
  serialization using `db_session_factory` and the two-session pattern
  described in Database Strategy — Concurrency Testing)

### User Identifier Resolution

Every endpoint that accepts a user identifier MUST be tested with both
UUID and username inputs. At minimum:

- Valid UUID → returns expected result
- Valid username → returns same expected result
- Non-existent UUID → 404
- Non-existent username → 404

### Service Functions

Every new or modified service function MUST be tested for:

- Expected behavior on valid input
- All guard conditions (Q2) and their error responses
- Audit event creation with correct field values (see Audit Trail
  Testing)
- Re-invocation behavior (Q5 — idempotency characteristics)
- Exception propagation to callers (Q6)
- Lock serialization: every service function that acquires `FOR UPDATE`
  (as documented in `docs/features/tickets/ticket-mutations.md`,
  `docs/features/tickets/ticket-service.md`,
  `docs/features/packages/package-service.md`) MUST have at least one
  test verifying lock serialization using `db_session_factory` and the
  two-session pattern described in Database Strategy — Concurrency
  Testing

### Model Constraints

New models MUST be tested for:

- Creation with valid data
- Unique constraints (duplicate insert → IntegrityError)
- NOT NULL constraints (missing required field → IntegrityError)
- Foreign key relationships (cascade behavior)
- Enum constraints (invalid value → rejected)

### CLI Commands

CLI commands MUST be tested per the automated verification contract
in `docs/conventions.md` (CLI Conventions, Automated Verification):

- Exit code 0 on success and idempotent no-ops
- Exit code 1 on user errors
- Exit code 2 on system errors
- Error messages on stderr, success messages on stdout
- Multi-step commands produce `✓`/`✗`/`—` prefixed lines

---

## Checklist: Adding Tests for a New Feature

When implementing a new feature, follow this checklist to ensure
comprehensive test coverage:

1. **Identify test tiers**: which functions need unit tests (pure
   logic), integration tests (DB interaction), and e2e tests (API
   endpoints)?

2. **Create test files**: mirror the `app/` structure. Service tests in
   `test_services/`, API tests in `test_api/`, model tests in
   `test_models/`.

3. **Mark tests**: apply `@pytest.mark.unit`, `@pytest.mark.integration`,
   or `@pytest.mark.e2e` to each test function or class.

4. **Use fixtures**: use `db_session` for integration tests, `client`
   for e2e tests, `redis_client` for integration or e2e paths that
   use Redis, and `db_session_factory` ONLY for concurrency/locking
   tests (all other integration tests continue using `db_session`).
   Create test data using factories (when available) or
   direct model instantiation. Do not connect test fixtures through
   application `REDIS_URL` or `CELERY_BROKER_URL`.

5. **Test audit events**: for every mutation covered by an audit trail,
   assert event creation with correct fields (see Audit Trail Testing).

6. **Test error paths**: every guard condition in the service spec
   (Q2) should have a corresponding test that triggers the guard and
   verifies the exception.

7. **Test edge cases**: empty collections, boundary values, concurrent
   access (if the function under test acquires `FOR UPDATE`, a
   concurrency test using `db_session_factory` and the two-session
   pattern in Database Strategy — Concurrency Testing is mandatory),
   re-invocation behavior.

8. **Run the suite**: `cd backend && pytest` — all tests must pass
   before declaring the task complete.

9. **Review**: invoke `@test-reviewer` for new features or modules (see
   Guardrail 6).

---

## Cross-references

- `docs/conventions.md` — Testing Conventions (style rules, naming),
  Transaction and Locking (pessimistic locking pattern)
- `docs/features/platform/audit-trail-infrastructure.md` — Audit Trail
  Index, `BaseAuditLog`, atomicity rules, immutability
- `docs/features/tickets/ticket-audit-log.md` — TicketAuditEvent
  contract
- `docs/features/identity/identity-audit-log.md` — IdentityAuditEvent
  contract
- `docs/features/platform/fetcher-infrastructure.md` — FetcherAuditEvent
  contract, test helper extension rule
- `docs/features/platform/system-settings.md` — SettingAuditEvent
  contract
- `AGENTS.md` — Guardrail 6 (Mandatory testing)
