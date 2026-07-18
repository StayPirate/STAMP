# Testing Infrastructure Rollout

Prescriptive runbook for applying the testing infrastructure defined in
`docs/features/platform/testing-strategy.md`. Each step specifies
**exactly** what to change, where, and how. Execute in order.

**Prerequisite**: Track A (the spec itself, registrations in README/tracking,
and `conventions.md` trimming) is already applied.

**Acceptance criteria**: `cd backend && pytest -v` and
`cd backend && ruff check . && ruff format --check .` both pass green.

---

## Step 1 — `backend/pyproject.toml`

### 1a. Add `testcontainers[postgres]` to dev dependencies

Add to the `[project.optional-dependencies] dev` list:

```diff
 dev = [
     "pytest>=8.3.0",
     "pytest-asyncio>=0.24.0",
     "pytest-cov>=6.0.0",
     "httpx>=0.28.0",
     "ruff>=0.8.0",
     "factory-boy>=3.3.0",
-    "aiosqlite>=0.20.0",
+    "testcontainers[postgres]>=4.9.0",
 ]
```

Changes:
- **Remove** `aiosqlite>=0.20.0` (SQLite is forbidden per testing-strategy.md)
- **Add** `testcontainers[postgres]>=4.9.0` (auto-provisioning of
  ephemeral Postgres for local tests)

### 1b. Register custom pytest markers

Add the `markers` key to `[tool.pytest.ini_options]`:

```diff
 [tool.pytest.ini_options]
 asyncio_mode = "auto"
 testpaths = ["tests"]
+markers = [
+    "unit: Fast, isolated tests (no DB, no Redis, no network)",
+    "integration: Tests with real PostgreSQL",
+    "e2e: Full HTTP request/response cycle tests",
+]
```

### 1c. Add coverage source configuration

Add a `[tool.coverage.run]` section after `[tool.pytest.ini_options]`:

```ini
[tool.coverage.run]
source = ["app"]
omit = [
    "*/tests/*",
    "*/alembic/*",
    "app/config.py",
    "app/database.py",
]
```

### Risks / Verification

- After this step, run `pip install -e ".[dev]"` to verify dependency
  resolution succeeds.
- Verify that `pytest --markers` lists the three custom markers.

---

## Step 2 — `backend/tests/conftest.py`

Replace the entire file with the following content:

```python
"""Shared test fixtures for the Sentinel backend.

See docs/features/platform/testing-strategy.md for the full testing strategy.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

from app.database import Base, get_db
from app.main import app


def _database_url() -> str:
    """Resolve the test database URL.

    Priority:
    1. TEST_DATABASE_URL env var (set by CI or developer override)
    2. Auto-provisioned PostgreSQL via testcontainers (local dev)
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url

    # Lazy import — testcontainers is only needed when no URL is provided
    from testcontainers.postgres import PostgresContainer

    # Module-level container — started once per session, reused across tests
    if not hasattr(_database_url, "_container"):
        container = PostgresContainer("postgres:16")
        container.start()
        # Build asyncpg URL from the container's connection params
        url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        _database_url._container = container
        _database_url._url = url
    return _database_url._url


@pytest.fixture(scope="session", loop_scope="session")
async def _engine():
    """Create the async engine and tables once per session."""
    engine = create_async_engine(_database_url(), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async DB session with per-test transaction rollback.

    Each test runs inside a savepoint. After the test, the savepoint is
    rolled back, ensuring complete isolation between tests.
    """
    async with _engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        # Create a nested savepoint for per-test rollback
        nested = await conn.begin_nested()

        try:
            yield session
        finally:
            if nested.is_active:
                await nested.rollback()
            await transaction.rollback()
            await session.close()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """Provide an async HTTP test client with DB session override.

    The FastAPI app's get_db dependency is overridden to use the test
    session, ensuring e2e tests share the test transaction.
    """

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)
```

### Design decisions

- **`_database_url()` with lazy container**: the testcontainers instance
  is created only once per process via a function attribute cache. This
  avoids the session-scoped fixture dance for a non-async resource.
- **`db_session` binds to a specific connection**: the session is created
  with `AsyncSession(bind=conn)` (not via `async_sessionmaker`) to ensure
  it shares the connection's transaction. `begin_nested()` creates a
  savepoint; rollback after each test restores a clean state.
- **`client` overrides `get_db`**: ensures that HTTP requests via the
  FastAPI test client use the same session and transaction as the test.

### Risks / Verification

- If the developer does not have Docker/Podman, testcontainers will
  fail with a clear error message. This is acceptable — Docker/Podman
  is already required by `dev-env.sh`.
- Fixture verification is deferred to Step 3b — the smoke test
  (`test_infrastructure_smoke.py`) validates the fixtures work.

---

## Step 3 — Test baseline adjustments

### 3a. Mark `test_health.py` as `xfail(strict=True)`

Replace the content of `backend/tests/test_health.py` with:

```python
"""Test the health check endpoint.

Marked xfail(strict=True) until /health is implemented per
docs/features/platform/health-endpoints.md. When the endpoint is
implemented, this test will pass — strict mode will then cause CI
to fail, signaling that the xfail marker should be removed.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.e2e
@pytest.mark.xfail(strict=True, reason="Health endpoint not yet implemented")
async def test_health_check_returns_ok(client: AsyncClient) -> None:
    """Health check endpoint should return status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

### 3b. Add infrastructure smoke test

Create `backend/tests/test_infrastructure_smoke.py`:

```python
"""Smoke tests for the test infrastructure itself.

These tests verify that the database fixture, transaction rollback,
and test client work correctly. They serve as the green baseline
before any feature code is implemented.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
async def test_db_session_executes_query(db_session: AsyncSession) -> None:
    """The db_session fixture should provide a working DB connection."""
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.integration
async def test_db_session_rollback_isolation(db_session: AsyncSession) -> None:
    """Each test should see a clean database (no state from other tests).

    This test creates a temporary table and inserts a row. The next test
    (test_db_session_no_leaked_state) verifies the table does not exist.
    """
    await db_session.execute(
        text("CREATE TABLE IF NOT EXISTS _test_isolation (id INT)")
    )
    await db_session.execute(text("INSERT INTO _test_isolation VALUES (42)"))
    result = await db_session.execute(text("SELECT COUNT(*) FROM _test_isolation"))
    assert result.scalar() == 1


@pytest.mark.integration
async def test_db_session_no_leaked_state(db_session: AsyncSession) -> None:
    """Verify that the previous test's table was rolled back."""
    result = await db_session.execute(
        text(
            "SELECT EXISTS ("
            "SELECT FROM information_schema.tables "
            "WHERE table_name = '_test_isolation'"
            ")"
        )
    )
    assert result.scalar() is False


@pytest.mark.e2e
async def test_client_connects_to_app(client: AsyncClient) -> None:
    """The test client should be able to make requests to the app."""
    # Any request works — we're testing the client fixture, not an endpoint.
    # A 404 from an unknown path is a valid response (proves the app is running).
    response = await client.get("/nonexistent-path-for-smoke-test")
    assert response.status_code in (404, 405)
```

### Risks / Verification

- `test_db_session_rollback_isolation` and `test_db_session_no_leaked_state`
  depend on execution order. To enforce this, they test for a DDL
  operation (CREATE TABLE) that is rolled back by the savepoint. If
  savepoint rollback does NOT roll back DDL (unlikely in Postgres, but
  worth knowing), the isolation test will fail — revealing a real
  problem with the fixture.
- Run `pytest tests/test_infrastructure_smoke.py -v` — all 4 should pass.

---

## Step 4 — CI pipeline updates

**Delegate to `@cicd`** — provide these requirements:

### 4a. Coverage gate

In `.github/workflows/ci.yml`, modify the `backend-test` job's pytest
command:

```diff
-      - run: pytest -v --cov=app --cov-report=term-missing
+      - run: pytest -v --cov=app --cov-report=term-missing --cov-fail-under=85
```

### 4b. `TEST_DATABASE_URL` environment variable

Add to the `env` section of the `backend-test` job (may already be set
via the existing `DATABASE_URL` — verify and use `TEST_DATABASE_URL`
explicitly for clarity):

```yaml
env:
  TEST_DATABASE_URL: postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_test
  DATABASE_URL: postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_test
  REDIS_URL: redis://localhost:6379/0
  CELERY_BROKER_URL: redis://localhost:6379/1
```

The `conftest.py` from Step 2 checks `TEST_DATABASE_URL` first, falling
back to testcontainers only when absent.

### 4c. Alembic drift check

Add a new step to the `backend-test` job, **after** the pytest step:

```yaml
      - name: Check Alembic migration drift
        run: |
          alembic upgrade head
          alembic check
```

This verifies that running all migrations brings the DB to a state that
matches the SQLAlchemy models — no pending autogenerate differences.

Note: `alembic check` requires Alembic >= 1.13.0 (already satisfied by
the `alembic>=1.14.0` dependency). If `alembic check` is not available,
use `alembic revision --autogenerate -m "drift_check" --check` as an
alternative (the `--check` flag causes a non-zero exit if differences
are detected, without creating a migration file).

### Risks / Verification

- The coverage gate may cause CI to fail if existing code has < 85%
  coverage. Currently, the backend has minimal code (~11 app files, all
  stubs) and minimal tests, so the threshold should be easily met. If
  the initial run shows < 85%, adjust the threshold to match the current
  level and ratchet upward.
- The Alembic drift check requires that at least one migration exists.
  If `alembic/versions/` is empty, the step will trivially pass (no
  models, no drift).

---

## Step 5 — Local automation (pre-commit hooks)

**Delegate to `@cicd`** — provide these requirements:

### 5a. Create `.pre-commit-config.yaml`

At the repository root:

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: ruff check backend/app/ backend/tests/
        language: system
        types: [python]
        pass_filenames: false

      - id: ruff-format
        name: ruff format check
        entry: ruff format --check backend/app/ backend/tests/
        language: system
        types: [python]
        pass_filenames: false

      - id: unit-tests
        name: unit tests
        entry: bash -c "cd backend && pytest -m unit --no-header -q"
        language: system
        types: [python]
        pass_filenames: false
        stages: [pre-commit]

  - repo: local
    hooks:
      - id: full-test-suite
        name: full test suite
        entry: bash -c "cd backend && pytest --no-header -q"
        language: system
        types: [python]
        pass_filenames: false
        stages: [pre-push]
```

### 5b. Configure `core.hooksPath`

The hooks must be repo-level, not global. Two options (the `@cicd` agent
should choose the most appropriate):

**Option A** — Use the `pre-commit` framework's install command:
```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type pre-push
```

**Option B** — Set `core.hooksPath` to a tracked `.githooks/` directory:
```bash
git config core.hooksPath .githooks
```
with corresponding shell scripts in `.githooks/pre-commit` and
`.githooks/pre-push` that invoke the checks.

**Note**: document whichever approach is chosen in `docs/deployment.md`
or a project README section, so new developers know how to activate the
hooks after cloning.

### Risks / Verification

- If `pre-commit` is not installed, hooks don't run — this is
  acceptable since CI is the authoritative enforcer.
- The `unit-tests` hook runs only tests marked `@pytest.mark.unit`.
  Initially, there may be zero unit tests (all current tests are
  integration/e2e), so the hook passes trivially. This is fine —
  it activates automatically as unit tests are added.
- The `pre-push` hook runs the full suite, which requires a running
  PostgreSQL (via testcontainers or `dev-env.sh`). If the developer
  doesn't have Docker/Podman, the push hook will fail — this is
  intentional (catches the problem before CI).

---

## Step 6 — Guardrail 6 update (`AGENTS.md`)

Replace the current Guardrail 6 section (lines 215–241 of `AGENTS.md`)
with:

```markdown
### 6. Mandatory testing

CRITICAL: Every code change (new feature or modification) MUST include tests.

Before considering any implementation task complete:

1. Write tests that cover the new/modified functionality
   - Backend: pytest tests in `backend/tests/` mirroring the `app/` structure
   - Apply markers: `@pytest.mark.unit`, `@pytest.mark.integration`, or
     `@pytest.mark.e2e` per `docs/features/platform/testing-strategy.md`
2. Run the test suite and verify all tests pass
   - Backend: `cd backend && pytest`
3. If tests fail, fix the code or tests until they pass
4. After all tests pass, evaluate whether a test quality review is needed:
   - New feature or new module: invoke `@test-reviewer`
   - Bug fix with regression test: invoke `@test-reviewer`
   - Minor refactor or small change to existing tested code: skip review
   - When in doubt, invoke `@test-reviewer`
5. Only THEN inform the user that the task is complete

Test requirements:

- New API endpoints: test happy path, validation errors, auth/permissions
- New models: test creation, constraints, relationships
- New services: test business logic, edge cases, error handling
- Bug fixes: add a regression test that reproduces the bug
- **Audit trail coverage**: for every mutation covered by any audit trail
  registered in the Audit Trail Index
  (`docs/features/platform/audit-trail-infrastructure.md`), tests MUST
  verify that the corresponding audit event is created with correct
  field values (`event_type`, `old_value`/`new_value`, `user_id`) in
  the same transaction. See
  `docs/features/platform/testing-strategy.md` (Audit Trail Testing)
  for the full assertion checklist

NEVER skip tests. If the user asks to skip tests, remind them that the project
requires tests for all changes and suggest writing them.
```

### Changes from current version

- Added marker guidance with reference to testing-strategy.md
- Added audit trail coverage requirement generalized to the Audit Trail
  Index (not hardcoded to TicketAuditEvent)
- Reference to testing-strategy.md for the full assertion checklist
- All other content preserved

---

## Step 7 — `@test-reviewer` update (`.opencode/agents/test-reviewer.md`)

Replace the entire file with:

```markdown
---
description: >
  Reviews test quality and coverage. Use this agent after writing tests
  to verify they are comprehensive and follow project testing conventions.
  Read-only: does not modify files.
mode: subagent
permission:
  edit: deny
  bash:
    "cd backend && pytest*": allow
    "*": deny
---

## Role

You review tests for completeness and quality. You do NOT write or modify code.

## Before reviewing

1. Read `docs/features/platform/testing-strategy.md` for the full testing
   strategy (test pyramid, fixtures, coverage policy, audit trail testing,
   mandatory test scenarios)
2. Read the implementation code that is being tested
3. Read the corresponding feature specification in `docs/features/**/`
4. Read `docs/conventions.md` for testing style conventions

## What to check

### Test structure and markers

- Are test files placed in the correct directory (mirroring `app/`)?
- Do tests use the correct marker (`@pytest.mark.unit`,
  `@pytest.mark.integration`, or `@pytest.mark.e2e`)?
- Do unit tests avoid database, Redis, and network I/O?
- Are fixtures used correctly (`db_session` for integration,
  `client` for e2e)?

### Coverage and completeness

- Are all new/modified functions covered by tests?
- Do tests cover happy path, edge cases, and error scenarios?
- Are tests independent and not relying on execution order?
- Are fixtures and mocks used correctly?
- Do test names follow the `test_<what>_<condition>_<expected_result>` pattern?
- Is there a regression test for bug fixes?
- Backend: are API endpoints tested for auth, validation, and permissions?
- Backend: are database constraints and relationships tested?

### Audit trail testing

For every mutation covered by any audit trail registered in the Audit Trail
Index (`docs/features/platform/audit-trail-infrastructure.md`), verify that
tests assert:

- The correct number of audit events are created (no missing, no duplicates)
- The `event_type` matches the contract table in the owning spec
- `user_id` is set for user-initiated actions, `NULL` for system/automated actions
- Domain-specific fields (`old_value`, `new_value`, `comment`, `detail`,
  `target_user_id`, etc.) match the contract
- The event and mutation are in the same transaction (no intermediate commit)

This applies to ALL registered audit trails: Ticket (`TicketAuditEvent`),
Identity (`IdentityAuditEvent`), Fetcher (`FetcherAuditEvent`), and
Setting (`SettingAuditEvent`). See the Audit Trail Index for the
authoritative list — this enumeration is informational, not exhaustive.

### Audit event immutability

Verify that no test performs UPDATE or DELETE operations on audit event
model instances. Audit event tables are append-only.

## Output

Provide a structured summary of:

1. **Well tested**: what is adequately covered
2. **Missing coverage**: specific gaps in test coverage
3. **Weak tests**: tests that exist but are insufficient
4. **Audit gaps**: mutations that create audit events but lack assertions
   for correct event creation
5. **Suggestions**: specific additional test cases to write
```

### Changes from current version

- Fixed permission glob: `"cd backend && pytest*"` (matches both
  `pytest` and `pytest -v`, `pytest tests/...`, etc.) — previously
  `"cd backend && pytest *"` which required an argument
- Added `testing-strategy.md` as the first document to read
- Generalized audit trail checking from TicketAuditEvent-only to ALL
  registered audit trails via the Audit Trail Index
- Added `detail`, `target_user_id` to the assertion checklist (for
  Identity trail compatibility)
- Added audit event immutability check
- Added "Audit gaps" output section
- Added test structure/marker verification
- Added fixture usage verification

---

## Step 8 — Skill updates

### 8a. `new-feature` (`.opencode/skills/new-feature/SKILL.md`)

Replace Step 4 (Write tests) with:

```markdown
### Step 4: Write tests

1. Backend tests in `backend/tests/` mirroring the `app/` structure
2. Apply markers per `docs/features/platform/testing-strategy.md`:
   - `@pytest.mark.unit` for pure logic (no DB/Redis/network)
   - `@pytest.mark.integration` for service-layer tests (with DB)
   - `@pytest.mark.e2e` for API endpoint tests (with HTTP client)
3. Use the shared fixtures: `db_session` for integration, `client` for e2e
4. For every mutation covered by an audit trail, assert correct event creation
   (see testing-strategy.md, Audit Trail Testing)
5. Run all tests and verify they pass: `cd backend && pytest`
```

### 8b. `new-api-endpoint` (`.opencode/skills/new-api-endpoint/SKILL.md`)

Replace Step 6 (Write tests) with:

```markdown
### Step 6: Write tests

1. Create tests in `backend/tests/test_api/`
2. Mark all endpoint tests with `@pytest.mark.e2e`
3. Use the `client` fixture (provides HTTP test client with DB override)
4. Test cases required:
   - Happy path with valid data
   - Validation errors (invalid input)
   - Authentication (unauthenticated request → 401)
   - Authorization (insufficient permissions → 403)
   - Edge cases (empty results, not found, etc.)
5. For mutations, assert audit event creation with correct field values
   (see `docs/features/platform/testing-strategy.md`, Audit Trail Testing)
6. Run `cd backend && pytest` and verify all tests pass
```

---

## Step 9 — `.opencode/README.md` update

Update the `@test-reviewer` row in the Subagents table:

```diff
-| `@test-reviewer` | Reviewer | Guardrail 6 | Reviews test quality, coverage, and adherence to testing conventions |
+| `@test-reviewer` | Reviewer | Guardrail 6 | Reviews test quality, coverage, audit trail assertions, and adherence to testing conventions |
```

No other rows change.

---

## Step 10 — Final verification

### 10a. Run the test suite

```bash
cd backend && pip install -e ".[dev]" && pytest -v
```

Expected result: all tests pass (smoke tests green, health test xfail,
API convention tests skipped).

### 10b. Run linting

```bash
cd backend && ruff check . && ruff format --check .
```

Expected result: no issues.

### 10c. Run reviewers on all modified artifacts

Invoke the following reviewers on the artifacts changed by this rollout:

| Reviewer | Target | Why |
|----------|--------|-----|
| `@docs-reviewer` | `AGENTS.md` (Guardrail 6), `testing-strategy.md` | Verify documentation coherence after guardrail update |
| `@docs-placement-reviewer` | `AGENTS.md` (Guardrail 6) | Verify the audit trail generalization rule is correctly placed |
| `@test-reviewer` | `backend/tests/conftest.py`, `test_infrastructure_smoke.py`, `test_health.py` | Verify the test infrastructure itself follows conventions |
| `@spec-coherence-reviewer` | `testing-strategy.md` | Re-verify coherence after all changes are applied (catch any drift introduced by guardrail/agent updates) |

Fix any "Needs revision" findings before declaring complete.

### 10d. Delete this draft

Once all verification passes and all reviewer findings are resolved:

```bash
rm docs/drafts/testing-infrastructure-rollout.md
```

This draft is a transient runbook. All durable rules live in
`testing-strategy.md`, `conventions.md`, `AGENTS.md`, and the agent
definitions.

---

## Summary of files modified

| File | Action |
|------|--------|
| `backend/pyproject.toml` | Edit (deps, markers, coverage) |
| `backend/tests/conftest.py` | Rewrite (Postgres fixtures) |
| `backend/tests/test_health.py` | Edit (xfail marker) |
| `backend/tests/test_infrastructure_smoke.py` | New (smoke tests) |
| `.github/workflows/ci.yml` | Edit (coverage gate, Alembic drift, TEST_DATABASE_URL) — `@cicd` |
| `.pre-commit-config.yaml` | New (repo-level hooks) — `@cicd` |
| `.githooks/pre-commit`, `.githooks/pre-push` | New (only if `@cicd` chooses Option B) |
| `AGENTS.md` | Edit (Guardrail 6) |
| `.opencode/agents/test-reviewer.md` | Rewrite (generalized) |
| `.opencode/skills/new-feature/SKILL.md` | Edit (Step 4) |
| `.opencode/skills/new-api-endpoint/SKILL.md` | Edit (Step 6) |
| `.opencode/README.md` | Edit (test-reviewer row) |
| `docs/drafts/testing-infrastructure-rollout.md` | Delete (self-consuming) |
