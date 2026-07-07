# Code Conventions

## General

- All code, comments, docstrings, and documentation MUST be in English
- Follow the principle of least surprise: code should do what a reader expects
- Prefer explicit over implicit
- Keep functions short and focused on a single responsibility
- **API-first**: the REST API is the primary interface of the platform. The
  web UI is a consumer of the API. Every operation available through the UI
  must be achievable through the API alone, with equivalent filtering,
  pagination, and sorting capabilities. The API may expose additional
  capabilities not present in the UI, but the reverse is a defect
- **HTTP APIs over CLIs**: when integrating with external services (IBS/OBS,
  SMELT, AIMAAS, Bugzilla, etc.), Sentinel MUST use their HTTP/REST APIs
  directly. Command-line tools (`osc`, `secbox`, etc.) are available on the
  development machine for ad-hoc exploratory testing only (e.g., verifying an
  API response format) and MUST NOT be used in application code or background
  tasks

### Example Data in Documentation

All examples, API response samples, test fixtures, and documentation
snippets MUST use fictional placeholder data. Never copy real personal
identifiers — names, email addresses, usernames, Distinguished Names, or
any other PII — from external systems (IBS, SMELT, AD, Bugzilla, NVD,
etc.) into the repository.

Approved placeholder patterns:

| Type          | Examples                                             |
|---------------|------------------------------------------------------|
| Person names  | `John Doe`, `Alice Smith`, `Bob Wilson`              |
| Usernames     | `jdoe`, `asmith`, `bwilson`                          |
| Emails        | `john.doe@suse.com`, `alice.smith@example.com`       |
| Groups        | `pkg-maintainers`, `kernel-team`                     |
| Group emails  | `pkg-maintainers@suse.de`                            |
| LDAP DNs      | `CN=John Doe,OU=User accounts,DC=corp,DC=suse,DC=com` |

When documenting API response formats from external services, first
sanitize the response by replacing all real identifiers with fictional
equivalents before inserting it into a specification or documentation file.

### Username Format

Usernames must be 1–64 characters, start with a letter, and contain only
lowercase letters, numbers, dots, hyphens, and underscores
(`[a-z0-9._-]`). Usernames are stored exclusively as lowercase in the
database. Any entry point that accepts a username for user creation MUST
normalize it (trim whitespace, lowercase) and validate the format before
storage.

### Active Directory / LDAP / SSO Terminology

These three terms have distinct meanings in the Sentinel codebase and
documentation. They MUST NOT be used interchangeably:

| Term | Scope | Usage |
|------|-------|-------|
| **AD** (Active Directory) | Data origin | Prefix for columns, error classes, error codes, and CLI/API values that identify data originating from Active Directory. Examples: `ad_object_guid`, `ad_synced_at`, `ADUserStatusReadOnlyError`, `USER_AD_STATUS_READONLY`, `--type ad`, `"source": "ad"` |
| **LDAP** | Protocol / transport | Used only for the network protocol and infrastructure: environment variables (`LDAP_URI`), the fetcher name (`sync_ldap_directory`), and prose describing the connection layer ("LDAP sync", "LDAPS port 636"). Never as a user type or data-origin label |
| **SSO** | Authentication method | Used only for the browser-based single sign-on flow (OIDC/OAuth2). Never as a user type — AD users authenticate via SSO, but their identity source is AD |
| **directory** | Generic category only | Never used alone as a synonym for Active Directory. Acceptable only in generic phrasing like "directory sync" (shorthand for `sync_ldap_directory`) where context is unambiguous |

Rules:
- A user whose `ad_object_guid IS NOT NULL` is an "AD user" (not "LDAP
  user", "SSO user", or "directory user")
- A user whose `ad_object_guid IS NULL` is a "local user"
- The `source` field in API responses returns `"ad"` or `"local"` (never
  `"ldap"` or `"sso"`)

### Cascade / Chain / Flattening Terminology

These three terms have distinct meanings in the Sentinel codebase and
documentation. They MUST NOT be used interchangeably:

| Term | Concept | Usage |
|------|---------|-------|
| **cascade** | Resolution strategy | Prioritized fallback sequence that tries sources in order until a result is found. Examples: "Severity Resolution Cascade", "package match cascade" |
| **chain** | Propagation of side effects | Sequence of derived mutations triggered by a primary change. Examples: "Recalculation Chain", "Deactivation chain", "orphan chain" |
| **flattening** | Linked-list resolution | Resolution and update of pointer chains. Examples: "duplicate flattening", `execute_duplicate_flattening()` |

Rules:

- Do not use "cascade" for propagation/side-effect sequences
- The term "chain" in this convention refers exclusively to mutation
  propagation. Pre-existing domain-specific uses of "chain" in other
  contexts are unrelated and unaffected: "duplicate chain" (the
  `duplicate_of_id` linked-list data structure), "submission chain"
  (IBS SR/incident/RR pipeline in `maintainer.md`), "manager chain"
  (reporting hierarchy in `ad-integration.md`), "certificate chain"
  (TLS)

### Ticket Status Category Terminology

Ticket statuses are grouped into two categories. Use the following
canonical terms consistently across all documentation and code:

| Category | Statuses | Canonical term |
|----------|----------|----------------|
| Active | `New`, `Analysis`, `Analyzed` | **active status** / **active ticket** |
| Inactive | `Resolved`, `Ignored`, `Duplicated` | **inactive status** / **inactive ticket** |

The authoritative definition of which statuses compose each set is in
`docs/features/tickets/tickets.md` (Status Categories).

Forbidden alternatives (MUST NOT be used for ticket status categories):

| Forbidden term | Why |
|----------------|-----|
| "terminal status" / "terminal state" | Semantically inaccurate — all three inactive statuses admit reverse transitions |
| "final status" (for tickets) | Reserved for `TicketPackageTrack` statuses `{NOT_AFFECTED, FIXED, WONT_FIX}` per `package-model.md` |
| "non-final" (for tickets) | Inverse of "final status" — same ambiguity |
| "non-active" | Non-standard variant; use "inactive" |
| "open tickets" / "open status" | Conflicts with IBS request states and the "Reopen" transition verb |
| "closed" / "closure" / "auto-closed" / "manually-closed" | Informal and inconsistent; no ticket status is named "Closed" |

**Disambiguation scope**: this convention applies exclusively to
**ticket** status categories. The following unrelated uses are NOT
affected:

- User lifecycle: `User.active` field, "inactive user", "active status"
  as a boolean attribute (identity domain)
- Product lifecycle: `Product.active` field (product domain)
- Assignee state: "inactive assignee" = user whose `active` field is
  `false` (ticket-mutations domain)
- IBS request states: "open", "accepted", "declined" (IBS domain)
- IBS incident lifecycle: "incident closed" (IBS domain)
- Status transition verbs: "Reopen" (action, not state category)

### Timestamps & Timezones

Sentinel follows the **"UTC everywhere, local display"** convention:

- **Database**: all timestamp columns use PostgreSQL `TIMESTAMPTZ` (which
  normalizes values to UTC internally). Never use bare `TIMESTAMP`
  (without time zone) — naive timestamps are ambiguous and a source of
  bugs in multi-timezone environments
- **Backend**: all datetime operations (comparisons, arithmetic,
  scheduling, TTL checks) operate in UTC. Use `datetime.now(UTC)` (never
  `datetime.utcnow()`, which returns a naive datetime). Celery Beat
  schedules are expressed in UTC. The Celery application MUST be
  configured with `timezone = "UTC"` and `enable_utc = True` (the
  Celery 4+ defaults). These settings MUST NOT be overridden in any
  environment — the Celery app factory validates them at module import
  time and refuses to start any process if they are incorrect (see
  `docs/configuration.md`, Celery Worker Configuration)
- **API responses**: all datetime values are serialized in UTC with the
  `Z` suffix (e.g., `2025-03-15T10:30:00Z`)
- **API inputs**: datetime filter parameters (e.g., `from_date`,
  `to_date`) interpret naive values (without timezone offset) as UTC.
  Values with an explicit offset (e.g., `+02:00`) are accepted and
  converted to UTC before comparison. See `docs/api-spec.md` (Date
  Range Interpretation) for the detailed parsing rules
- **Frontend**: the UI converts UTC timestamps from the API to the
  user's local timezone at display time (using `Intl.DateTimeFormat` or
  equivalent). When submitting datetime values to the API, the frontend
  converts local time to UTC before sending
- **CLI**: timestamps in CLI output are displayed in UTC with an
  explicit "UTC" suffix (e.g., `2025-03-15 10:30:00 UTC`)

## Python (Backend)

### Style

- **Formatter**: ruff format (black-compatible)
- **Linter**: ruff check
- **Line length**: 88 characters (ruff default)
- **Quotes**: double quotes for strings
- **Imports**: sorted by ruff (isort-compatible), grouped as:
  1. Standard library
  2. Third-party
  3. Local application

### Type Hints

- All function signatures MUST have type hints for parameters and return values
- Use `from __future__ import annotations` for modern annotation syntax
- Use `Optional[X]` or `X | None` for nullable types

### Naming

- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions/methods**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private**: prefix with single underscore `_`
- **Fetchers**: `BaseFetcher` subclass naming follows the
  `<verb>_<source>_<noun>` convention — see
  `docs/features/platform/fetcher-infrastructure.md` (Naming Convention)
- **CVE fetchers**: inherit from `BaseCVEFetcher`
  (`backend/app/services/base_cve_fetcher.py`). Declare
  `cve_source_type` and implement `fetch_single()` (unless
  `supports_fetch_single = False`). See
  `docs/features/platform/cve-fetcher-infrastructure.md`
- **Git-based CVE fetchers (delta-flow)**: inherit from `BaseGitFetcher`
  (`backend/app/services/base_git_fetcher.py`). Only implement
  `process_item()`, `_construct_candidate_paths()`, and optionally
  `filter_delta_files()` / `deduplicate_items()`. Do NOT override
  `execute()`. See `docs/features/platform/git-fetcher-infrastructure.md`

### FastAPI Conventions

- Endpoint handlers should be thin: validate, call service, return response
- Use dependency injection (`Depends()`) for database sessions, auth, etc.
- All endpoints must have OpenAPI documentation (summary, description)
- Use appropriate HTTP status codes and response models
- **User identifier resolution**: all parameters that identify a user accept
  either a UUID or a username (see `docs/api-spec.md`, "User Identifier
  Resolution"). Use the shared `resolve_user_identifier` dependency:

  ```python
  from uuid import UUID
  from sqlalchemy import select
  from sqlalchemy.ext.asyncio import AsyncSession
  from fastapi import HTTPException

  async def resolve_user_identifier(
      identifier: str, db: AsyncSession
  ) -> User:
      """Resolve a user by UUID or username.

      If the identifier is a valid UUID, lookup is by primary key.
      Otherwise, lookup is by the username field (exact match).
      Raises 404 if no user is found.
      """
      try:
          user_uuid = UUID(identifier)
          user = await db.get(User, user_uuid)
      except ValueError:
          user = await db.scalar(
              select(User).where(User.username == identifier)
          )
      if not user:
          raise HTTPException(status_code=404, detail="User not found")
      return user
  ```

  Location: `backend/app/core/dependencies.py` (or equivalent shared module)

- **Capability-based authorization**: use `require_capability()` as the
  standard authorization dependency for capability-protected endpoints.
  Example:

  ```python
  @router.post("/tickets")
  async def create_ticket(
      ...,
      _: User = Depends(require_capability(Capability.CREATE_TICKET)),
  ):
  ```

  Scope filtering (confidential ticket visibility) is handled by shared
  query utilities (`confidential_ticket_filter()`,
  `require_accessible_ticket`), not per-endpoint logic. See
  `docs/features/identity/rbac.md` for the full authorization model

- **Cross-cutting query parameter constraints**: enforce global constraints
  (such as the 500-character string parameter length limit defined in
  `docs/api-spec.md`) via a shared dependency injected at the app or router
  level, rather than repeating validation logic in individual endpoint
  handlers. This ensures consistent enforcement across all endpoints and
  reduces the risk of omission

### SQLAlchemy Conventions

- Use SQLAlchemy 2.0 style (mapped_column, declarative base)
- All models inherit from a common `Base` class
- Use UUID primary keys
- Always include `created_at` and `updated_at` timestamps
- Define relationships explicitly with `back_populates`

### Pydantic Conventions

- Separate schemas for Create, Update, and Response
- Use `model_config = ConfigDict(from_attributes=True)` for ORM integration
- Validate at the schema level, not in endpoints or services

### Audit Trail

Every audit event SQLAlchemy model MUST inherit from `AuditEventMixin`
(`backend/app/models/mixins.py`). Every audit trail MUST be implemented
as a `BaseAuditLog` subclass
(`backend/app/services/base_audit_log.py`).

See `docs/features/platform/audit-trail-infrastructure.md` for the
full specification: base class interface, mixin columns, naming
conventions, atomicity rules, and the Audit Trail Index.

### Transaction and Locking

When a service module centralizes all mutations on an entity (e.g.,
`ticket_mutations` for tickets), concurrent transactions can produce
lost updates or stale audit trail values. To prevent this, apply
pessimistic locking at the module boundary.

#### Pessimistic Locking Pattern

Every public function in a centralized mutation module MUST acquire a
row-level lock on the root entity as the first database operation in
the transaction:

```python
ticket = await db.execute(
    select(Ticket).where(Ticket.id == ticket_id).with_for_update()
)
```

This serializes all concurrent mutations on the same entity at the
database level. The lock is released automatically when the transaction
commits or rolls back.

#### Transaction Hygiene Rules

The transaction that holds a `FOR UPDATE` lock MUST be kept as short
as possible. Two categories of work are forbidden inside it:

1. **No external service calls**: HTTP requests to external services
   (IBS, SMELT, NVD, AIMAAS, AD, or any network I/O) MUST happen
   **before** the transaction that acquires the lock. The correct
   pattern is:

   ```
   1. Fetch data from external service (no lock held)
   2. Open transaction → SELECT ... FOR UPDATE on root entity
   3. Apply mutations + create audit events
   4. Commit (lock released)
   ```

   Holding a row lock while waiting for an external service response
   (which may take seconds or time out entirely) blocks all other
   mutations on the same entity for the duration.

2. **No expensive queries**: analytical queries, aggregations over
   large tables, or computationally intensive operations MUST be
   executed before acquiring the lock. The locked transaction should
   contain only fast reads (single-row lookups for validation) and
   writes.

**I/O-then-Lock corollary**: in modules that contain both orchestration
functions (with external I/O) and mutation functions (with `FOR UPDATE`
locks), the two concerns MUST be separated into distinct functions —
orchestration functions MUST NOT acquire locks. See
`docs/features/packages/package-service.md` (Module Invariant) for the
canonical application of this rule.

### Testing Conventions

- Test files mirror the `app/` directory structure
- Use `pytest` with async support (`pytest-asyncio`)
- Use fixtures for database sessions, test data, authenticated clients
- Test naming: `test_<what>_<condition>_<expected_result>`
- Example: `test_get_cve_not_found_returns_404`
- **User identifier resolution**: every endpoint that accepts a user
  identifier MUST be tested with both UUID and username inputs. At minimum:
  - Valid UUID → returns expected result
  - Valid username → returns same expected result
  - Non-existent UUID → 404
  - Non-existent username → 404

### Redis Key Conventions

Redis keys in Sentinel fall into two categories with different
documentation rules:

**Application-owned keys**: keys whose format is defined by Sentinel
(e.g., `login_attempts:{username}`, `session_liveness:{session_id}`,
`fetch_pending:{cve_id}:{source}`). These are accessed via the Redis
client directly. The spec that owns the key MUST document the exact
format, TTL, and value contract — the format IS the specification.

**Library-managed keys**: keys whose format is defined by a third-party
library (e.g., `celery-redbeat` schedule entries). Sentinel code MUST
interact with these exclusively via the library's public API — never by
constructing Redis keys directly. Specifications MUST describe behavior
in terms of the library API (e.g., "create an entry via
`RedBeatSchedulerEntry`"), not in terms of internal key formats (e.g.,
"write to `redbeat:{name}`"). Internal key patterns may be documented
as informational notes for operational debugging, clearly marked as
library-internal.

### Redis Error Handling

All application-owned Redis operations (operations that access
`REDIS_URL` directly, as opposed to library-managed broker operations)
MUST catch **`RedisError`** (the base class from `redis.exceptions`),
not narrower subclasses like `ConnectionError` or `TimeoutError`.

**Rationale**: under `noeviction` memory policy, when Redis reaches
`maxmemory`, write commands return an OOM error. The Python client
raises `redis.exceptions.ResponseError` — a subclass of `RedisError`
but NOT of `ConnectionError`. Catching only `ConnectionError` would
leave OOM errors unhandled (resulting in HTTP 500 responses).

By catching `RedisError`, all Redis failure modes (connection loss,
timeout, OOM rejection, protocol errors) trigger the same graceful
degradation path already specified per feature:

| Feature | Degradation on `RedisError` |
|---------|----------------------------|
| Session liveness (`session_liveness:*`) | Fall back to direct PostgreSQL query |
| Login lockout (`login_attempts:*`) | Fail-open (login proceeds without rate limiting) |
| Fetch deduplication (`fetch_pending:*`) | Unconditional enqueue (idempotent) |
| CVSS recalculation lock (`cvss_recalc_active`) | Return 503 `REDIS_UNAVAILABLE` |
| IBS consumer heartbeat | Log WARNING, continue operating |

**Scope**: this convention applies to application code that directly
calls the Redis client. It does NOT apply to:

- Celery broker operations (managed by the Celery framework; errors
  surface as task publish failures with Celery's own retry logic)
- Redbeat operations (managed by the library; errors in `tick()` are
  handled by the Beat fail-fast mechanism — see
  `docs/features/platform/fetcher-infrastructure.md`, "Runtime: Redis
  Data Loss")

**Pattern**:

```python
try:
    redis.set(f"fetch_pending:{cve_id}:{source}", "1", nx=True, ex=600)
except RedisError:
    # Degrade gracefully per feature spec
    logger.warning("Redis unavailable for dedup lock: %s", exc)
    # proceed without deduplication (idempotent downstream)
```

## TypeScript (Frontend)

### Style

- **Formatter**: Prettier (via ESLint)
- **Linter**: ESLint with TypeScript plugin
- **Line length**: 80 characters
- **Quotes**: double quotes
- **Semicolons**: required

### Naming

- **Files**: `PascalCase.tsx` for components, `camelCase.ts` for utilities
- **Components**: `PascalCase`
- **Functions/hooks**: `camelCase`
- **Types/interfaces**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`

### React Conventions

- Functional components only (no class components)
- Use custom hooks for reusable logic
- Keep components focused: one component, one file
- Props interfaces defined in the same file as the component
- Use React Query for server state management

### Component Structure

```typescript
// 1. Imports
import { useState } from "react";
import { Button } from "@/components/ui/button";

// 2. Types
interface MyComponentProps {
  title: string;
  onAction: () => void;
}

// 3. Component
export function MyComponent({ title, onAction }: MyComponentProps) {
  // hooks first
  const [state, setState] = useState(false);

  // handlers
  const handleClick = () => { ... };

  // render
  return ( ... );
}
```

### Testing Conventions

- Use Vitest as test runner
- Use React Testing Library for component tests
- Test user behavior, not implementation details
- Co-locate tests with components when practical

## CLI Conventions

### Framework

- **Library**: Click
- **Entry point**: `sentinel` (registered as a console script in `pyproject.toml`)
- **Architecture**: command groups for related commands (e.g.,
  `sentinel manage-user create`, `sentinel manage-user update`)

### Command Design

- Commands that modify data MAY check a configuration guard before
  executing. If a guard is defined and not enabled, the command MUST exit
  with a clear error message explaining which setting to enable
- Commands MUST be idempotent where practical — running the same command
  twice should not produce errors or duplicate data
- Use `--flag` for boolean options and `--option VALUE` for parameterized
  options
- Repeatable options use multiple `--option` flags (e.g.,
  `--role admin --role vulnerability_analyst`)
- **Username normalization**: all CLI commands that accept a username
  argument MUST normalize it (trim whitespace, lowercase) before lookup.
  See Username Format (above) for the full format specification

### Database Access

- CLI commands use synchronous database sessions (not async). They are
  one-shot processes, not long-running servers — async provides no benefit
  and adds complexity

### Output Contract

All CLI commands MUST follow this output contract for consistency and
scriptability.

#### Channel Separation

- **stdout**: success messages, results, tables, structured step reports
- **stderr**: error messages (`Error: ...`), warnings (`Warning: ...`)

This separation allows callers to redirect stdout for parsing while
still receiving diagnostics on stderr.

#### Exit Codes

| Code | Meaning | Examples |
|------|---------|----------|
| 0    | Success (includes idempotent no-ops) | Command completed, or state already reached |
| 1    | User error | Bad input, validation failure, concurrency conflict, unknown resource |
| 2    | System error | Database unreachable, Redis unreachable, unhandled exception |
| 130  | Interrupted by SIGINT (Ctrl+C) | Operator cancelled a long-running command |
| 143  | Interrupted by SIGTERM | Process manager requested shutdown |

Every command specification MUST document which exit codes it can produce.

#### Success Output

Single-operation commands print a concise confirmation to stdout:

```
Created user 'jdoe' (jdoe@example.com) with roles: admin.
```

Read-only commands print structured output (tables, lists) to stdout.

#### Error Output

All errors go to stderr with the prefix `Error:`:

```
Error: User 'jdoe' not found.
```

Warnings go to stderr with the prefix `Warning:` and do NOT cause a
non-zero exit code:

```
Warning: User 'jdoe' is inactive. Unlock has no practical effect until the user is reactivated.
```

#### Multi-Step Reporting (Fail-Fast)

Commands that perform multiple sequential mutations with fail-fast
semantics MUST use structured step reporting on stdout:

| Prefix | Meaning |
|--------|---------|
| `✓`    | Step completed successfully |
| `✗`    | Step failed (include reason) |
| `—`    | Step not attempted (aborted due to previous failure) |

Example:

```
✓ Email updated to new@example.com
✗ Role update failed: role 'nonexistent' does not exist
— Reactivation not attempted (aborted due to previous error)
```

This pattern applies when a command executes >1 independent mutation in
sequence where partial success is possible. It does NOT apply to:

- Atomic single-operation commands (use simple success/error messages)
- Commands with internal phases that are not user-visible mutations
  (the user cares about the final result, not the internal phases)

#### Idempotency

Commands MUST be idempotent where practical. Specifically:

- If the desired state is already reached (e.g., deactivating an already
  inactive user, unlocking a user that is not locked), the command prints
  an informational message to stdout and exits with code 0 — never with
  an error
- Commands that require interactive input for security (e.g., password
  prompts) are exempt from idempotency — each invocation inherently
  changes state
- Commands that execute external work are exempt — they produce side
  effects by design

Each command specification MUST explicitly declare its idempotency:

- **Idempotent**: safe to re-run; no-op if state is already reached
- **Not idempotent (interactive)**: requires interactive input that
  changes state on every invocation
- **Not idempotent (by design)**: produces side effects intentionally

#### Human-Readable Format

- Output is human-readable plain text by default
- No JSON output unless a `--json` flag is explicitly added to a command
- Tables use fixed-width columns aligned with spaces (no box-drawing
  characters)

#### Automated Verification

When CLI commands are implemented, a parametrized test suite MUST verify
the output contract mechanically. At minimum, the test suite should
cover:

- Exit code 0 on success and idempotent no-ops
- Exit code 1 on known user errors (bad input, unknown resource)
- Exit code 2 on simulated system errors (unreachable database/Redis)
- Error messages written to stderr (not stdout)
- Success messages written to stdout (not stderr)
- Multi-step commands produce `✓`/`✗`/`—` prefixed lines on partial
  failure

This is preferred over a manual review agent because the rules are
deterministic and mechanically verifiable.

### Naming

- Command groups: `noun` with `verb` subcommands for related operations
  (e.g., `sentinel manage-user create`, `sentinel fetcher list`)

## Git Conventions

### Branch Naming

- `feature/<short-description>` — new features
- `fix/<short-description>` — bug fixes
- `docs/<short-description>` — documentation changes
- `refactor/<short-description>` — code refactoring

### Commit Messages

- Use conventional commits format: `type: description`
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`
- Keep the first line under 72 characters
- Use imperative mood: "add feature" not "added feature"
- Examples:
  - `feat: add CVE severity filtering to dashboard`
  - `fix: correct CVSS score parsing for NVD API v2`
  - `docs: update data model with Product table`
  - `test: add integration tests for CVE sync service`

## Feature Specifications

### API Cross-references

Feature specs that define API endpoints MUST include `docs/api-spec.md` in
their Cross-references section. The rules governing what appears in
per-endpoint error tables (global responses, scoped responses, Pydantic
validation) are defined in `docs/api-spec.md` (section "What belongs in
an endpoint error table") and are not restated here.

### Fetcher Documentation

Every `BaseFetcher` subclass MUST have its specification documented
following the fetcher documentation requirements defined in
`docs/features/platform/fetcher-infrastructure.md` (section "Fetcher
Documentation Requirements"). This includes the classification rule
(dedicated spec vs. embedded section), the minimum documentation
template, and the Fetcher Registry maintenance obligation.

### Function Specification Completeness

Every function documented in a feature specification MUST provide enough
information that an implementer can write a complete, correct
implementation without making autonomous design decisions. If an
implementer must choose between two plausible behaviors because the spec
does not specify which, the spec is incomplete.

#### Required Information (by function category)

Functions are classified into two categories based on their observable
effects:

**Category A — Functions with side effects**: any function that mutates
database state, creates audit events, enqueues tasks, acquires locks, or
calls external services.

The spec MUST answer ALL of the following questions. The format and
location of each answer is unrestricted — answers may appear as
dedicated sections, inline in numbered steps, in tables, in prose, or in
any combination the author finds clearest for the specific function.

| # | Question | What the implementer needs to know |
|---|----------|------------------------------------|
| Q1 | What are all inputs and their types? | Function signature: parameter names, types, and semantic meaning of any parameter whose purpose is not obvious from name+type alone |
| Q2 | When does the function refuse to execute? | All guard conditions that cause early rejection (raises/returns before the main mutation begins), and what exception or error each guard produces |
| Q3 | What does it do in every possible case? | Complete behavioral specification covering all execution paths — the implementer must never encounter a case the spec does not address |
| Q4 | What audit events does it create, and under what conditions? | Event type, which fields are populated, and whether creation is conditional. If the function creates no audit events, state "None" explicitly (unless derivable per the Decision rule) |
| Q5 | What happens on re-invocation with the same inputs? | Whether the function is idempotent, conditionally idempotent, or creates new effects on every call. Critical for Celery retry safety and API consumer retry logic |
| Q6 | What exceptions does it propagate to callers? | All exceptions that escape the function boundary, including shared exceptions from other modules |

**Category B — Pure/stateless functions**: functions that perform
computation without side effects (no DB writes, no audit events, no
external calls). Includes query builders, resolution cascades, parsers,
validators, and read-only lookups.

The spec MUST answer:

| # | Question |
|---|----------|
| Q1 | What are all inputs and their types? |
| Q3 | What does it do in every possible case? |
| Q6 | What exceptions does it propagate? (or "None" if infallible) |

Q2, Q4, Q5 are not applicable to stateless functions and SHOULD be
omitted (their absence is not a gap).

#### Structural freedom

The convention prescribes WHAT information must be present, not HOW it
is organized. All of the following are valid structures for answering
the required questions:

- Dedicated labeled sections (e.g., "Preconditions", "Audit events")
- Numbered behavioral steps where guards, audit events, and
  re-invocation are naturally embedded in the flow
- Sub-sections organized by independent concerns (e.g., "Merge
  Strategy", "Transaction Boundaries") where each concern answers a
  subset of the questions
- Consolidated tables for groups of 2+ functions sharing an identical
  structural pattern
- Narrative prose with code blocks, flow diagrams, or pseudo-code
- Any combination of the above

The author SHOULD choose the structure that maximizes clarity for the
specific function. A complex orchestrator function benefits from
concern-based sub-sections; a trivial guard benefits from a one-line
signature description. Forcing either into the other's format degrades
readability.

#### Consolidated groups

When 2+ functions share an identical structural pattern (e.g., all are
HTTP client wrappers, all are single-statement metric helpers, all are
CRUD delegations with the same shape), they MAY be documented as a
single table with columns adapted to the group's nature. The table must
still answer Q1, Q3, and Q6 for each function in the group.

#### Module-level defaults

A spec MAY declare default answers to Q4, Q5, or Q6 that apply to all
functions in a section. Per-function documentation then only states
deviations from the default. This eliminates repetitive "None"
statements across homogeneous function groups.

Example:

> All functions in this module propagate only the exceptions listed in
> the Service Exceptions table. No function creates audit events unless
> stated per-function. Read-only functions are infallible.

Example (delegate propagation):

> All public functions in this module propagate exceptions from
> delegated services (`ticket_mutations`, `user_service`) unless
> explicitly caught inline. Callers must handle both this module's
> Service Exceptions and those of the delegated modules.

A module-level default MUST be placed at the beginning of the functions
section (before the first function) so readers encounter it before any
individual specification. Per-function overrides take precedence over
the default.

#### Decision rule

**Insufficiency test**: if an implementer reading the spec must make a
design decision (choose between two plausible behaviors), the spec fails
the completeness requirement.

**Excess test**: if the spec repeats information already obvious from
the function's name and type signature alone, the documentation is
excessive. Remove redundancy.

**Derivability rule**: a question's answer MAY be omitted when it is
unambiguously derivable from:

1. **The function's category** — Category B functions have no side
   effects by definition; Q4 is inherently "None" and Q5 is inherently
   "N/A" without per-function repetition
2. **Other answers already present** — if Q3 (behavior) or Q2 (guards)
   already make the answer logically certain, restating it is redundant.
   Examples: if Q2 shows a guard that rejects on a post-mutation state,
   Q5 (re-invocation fails on that guard) is derivable; if Q3 shows
   only deterministic in-memory operations with no failure paths, Q6
   ("None") is derivable
3. **A module-level or section-level default** — see "Module-level
   defaults" above

The Insufficiency test takes unconditional precedence: if there is *any*
reasonable ambiguity about the answer, the spec MUST state it
explicitly. When in doubt, state it — the cost of one redundant
sentence is lower than the cost of one ambiguous omission.

#### Scope and Exclusions

This convention applies to **service-layer functions, private helpers,
and utility functions** documented in feature specifications. The
following categories use their own documentation patterns and are NOT
subject to these completeness questions:

1. **API endpoint handlers**: documented using the endpoint format
   (Request body/Query parameters + Behavior + Response + Error
   responses). See `docs/api-spec.md` for endpoint documentation
   standards. This includes FastAPI dependencies that produce HTTP
   responses directly (authentication middleware, authorization guards
   injected via `Depends()`).

2. **Fetcher `execute()` algorithms**: documented using the fetcher
   documentation template (Properties table + Algorithm + Error handling
   + Metrics) defined in
   `docs/features/platform/fetcher-infrastructure.md`. Named helper
   functions documented exclusively as sub-steps within an excluded
   algorithm's section inherit the exclusion. If the helper is extracted
   into its own top-level section or referenced from multiple unrelated
   algorithms, this convention applies.

3. **Interface and abstract contracts**: abstract methods, protocol
   definitions, and hook method contracts that define what implementors
   must provide. These use: Signature + Contract semantics + Signaling
   convention (if applicable).

4. **Event-processing pipelines**: message handlers and long-running
   consumer pipelines whose "parameters" are message payload fields.
   These use numbered pipeline steps without the Q1-Q6 framework.

5. **CLI command behaviors**: documented using the CLI Output Contract
   (Parameters + Behavior + Idempotency + Exit codes + Output channels)
   defined in the CLI Conventions section. The "Idempotency" declaration
   satisfies Q5.

**Precedence rule**: when a more specific documentation template exists
for a function category, the specific template takes precedence. This
convention applies to all functions NOT covered by a more specific
template.

**Cross-reference overviews**: when a function's authoritative
specification lives in another document, a lighter behavioral overview
in the referencing document is acceptable. It MUST NOT be elevated to
the function's full completeness level — that would create
contradictory sources of truth.

#### Algorithm-reference pattern

When a function's complete algorithm is already specified in a dedicated
section of the same document (e.g., a resolution cascade, a match
procedure), the function's own entry MAY reference that section instead
of re-specifying the steps. The reference must be unambiguous (section
name or anchor). The referenced section must itself answer Q3 for all
execution paths.

### Service Exception Conventions

Every service module that raises exceptions propagated to API callers
MUST follow the standard exception documentation pattern established in
`docs/features/packages/package-service.md`.

#### Base class requirement

All exceptions in a service module inherit from a common
`<Module>ServiceError` base class (e.g., `TicketServiceError`,
`UserServiceError`). All module base classes inherit from a common
`ServiceError` root class. The spec MUST include the declaration:

> All exceptions in this module inherit from `<Module>ServiceError`.
> API endpoint handlers catch `<Module>ServiceError` subclasses and map
> them to the corresponding HTTP status code and error code per
> `api-spec.md`.

#### API-facing exception table format

All modules use a 4-column table for exceptions that reach API handlers:

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `ExampleError` | 409 | `DOMAIN_ERROR_CODE` | Brief semantic condition |

#### System-internal exception sub-table

For exceptions that never reach an API handler (CLI-only, fetcher-only,
or caught internally), a separate sub-table is used:

| Exception | Raised when | Handling |
|-----------|-------------|----------|
| `InternalError` | Semantic condition | How callers handle it |

#### "Raised when" column rules

- Describes the **semantic condition** that triggers the exception in
  one brief sentence
- Does NOT list function names or code paths (avoids drift when
  implementations change)
- The condition should be stable — tied to the exception's meaning,
  not to implementation details

#### 1:1 mapping rule

Every API-facing exception MUST map to exactly one HTTP status code and
one error code. If an exception currently maps to multiple codes based
on an attribute (e.g., `reason`), it MUST be split into separate
exception classes — one per distinct error code.

#### Domain-specific validation codes

Domain-specific validation exceptions (e.g., `PasswordValidationError`,
`ApiKeyNameValidationError`) use domain-specific error codes (e.g.,
`USER_PASSWORD_POLICY_VIOLATION`, `AUTH_API_KEY_NAME_INVALID`) — NOT the
generic `VALIDATION_ERROR` code. The `errors` array in the response body
remains exclusive to `VALIDATION_ERROR` responses (Pydantic schema
validation). Domain validation errors use the standard `code` + `detail`
envelope format.

#### Mapping authority chain

The three-tier authority relationship for error information is:

1. **`api-spec.md`** — authoritative registry of all valid error codes
   (prefix, domain, enumeration)
2. **Service spec** — authoritative source for each exception's HTTP
   status code and error code mapping
3. **Endpoint error tables** (in feature specs like `tickets.md`,
   `user-management.md`) — reference the service spec's exceptions for
   traceability; not authoritative for the HTTP/code mapping

The HTTP/code mapping for each exception MUST be documented in the
service module spec itself — not deferred to endpoint-level error tables
in other specs.

Every error code appearing in a service exception table MUST also be
registered in the Error Code Categories table in `api-spec.md`.

#### Shared exceptions

Exceptions used by multiple service modules (e.g., `TicketNotFoundError`
used by `ticket-service`, `ticket-mutations`, and `package-service`) are
defined once in a common exceptions module and imported by each service.
Each service spec MUST still list the exception in its own table (the
reader should not need to consult another spec). Shared exceptions are
marked with `†` in exception tables.

**Inheritance**: shared exceptions inherit from `ServiceError` directly —
they are NOT subclasses of any individual module's base class.

**Handler requirements**: API handlers MUST catch each exception class
individually. Using `except ModuleServiceError` as a catch-all is
insufficient when the table includes shared exceptions (marked `†`),
since those do not inherit from the module's base class. A
`except ServiceError` fallback MAY be added for defense-in-depth but
MUST log a warning (indicates a missing specific handler).

#### Endpoint error tables (post-standardization)

Endpoint-level error tables in feature specs document errors specific to
the endpoint's logic. They reference the error code and condition for
traceability — the authoritative HTTP status mapping for service
exceptions lives in the owning service spec's exception table.

Global and scoped responses (defined in `api-spec.md`) are never
included as table rows — they are covered by a reference line.
