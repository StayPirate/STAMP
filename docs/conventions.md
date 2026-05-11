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
| **AD** (Active Directory) | Data origin | Prefix for columns, error classes, error codes, and CLI/API values that identify data originating from Active Directory. Examples: `ad_object_guid`, `ad_dn`, `ad_synced_at`, `ADUserStatusReadOnlyError`, `USER_AD_STATUS_READONLY`, `--type ad`, `"source": "ad"` |
| **LDAP** | Protocol / transport | Used only for the network protocol and infrastructure: environment variables (`LDAP_URI`, `LDAP_CA_CERT_PATH`), the fetcher name (`sync_ldap_directory`), and prose describing the connection layer ("LDAP sync", "LDAPS port 636"). Never as a user type or data-origin label |
| **SSO** | Authentication method | Used only for the browser-based single sign-on flow (OIDC/OAuth2). Never as a user type — AD users authenticate via SSO, but their identity source is AD |
| **directory** | Generic category only | Never used alone as a synonym for Active Directory. Acceptable only in generic phrasing like "directory sync" (shorthand for `sync_ldap_directory`) where context is unambiguous |

Rules:
- A user whose `ad_object_guid IS NOT NULL` is an "AD user" (not "LDAP
  user", "SSO user", or "directory user")
- A user whose `ad_object_guid IS NULL` is a "local user"
- The `source` field in API responses returns `"ad"` or `"local"` (never
  `"ldap"` or `"sso"`)

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
  (e.g., `fetcher run` has concurrency check → execute → record, but
  the user cares about the fetcher result, not the internal phases)

#### Idempotency

Commands MUST be idempotent where practical. Specifically:

- If the desired state is already reached (e.g., deactivating an already
  inactive user, unlocking a user that is not locked), the command prints
  an informational message to stdout and exits with code 0 — never with
  an error
- Commands that require interactive input for security (e.g., password
  prompts) are exempt from idempotency — each invocation inherently
  changes state
- Commands that execute external work (e.g., `fetcher run`) are exempt —
  they produce side effects by design

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
  (e.g., `sentinel manage-user create`, `sentinel fetcher run`)

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
their Cross-references section. Endpoint-specific error tables document only
errors unique to the endpoint logic; errors handled globally (Pydantic
validation, authentication, authorization, etc.) are defined in `api-spec.md`
and must not be repeated per-endpoint.
