# Draft: Introduce CLI Infrastructure Specification

**Status**: Draft — pending review before application to `docs/`.
**Author**: Spec agent session, 2026-07-21.
**Type**: New specification + corrections to existing specs. No code exists
yet (spec-first project) — this is a documentation-only change. No
migrations, no implementation.

## 1. Background

During review of `docs/features/platform/logging.md`, a gap was identified:
`logging.md` §"Scope of this pipeline" (lines 294–312) explicitly assumes
the existence of CLI-process-level infrastructure (a Click group
initialization point where a minimal structlog configuration is applied)
but no specification currently owns that substrate.

Investigation confirmed that Sentinel's CLI documentation currently exists
at three levels, all of which are legitimate and should be preserved:

1. **Contract/conventions** — `docs/conventions.md` §"CLI Conventions"
   (Framework, Command Design, Database Access, Output Contract, Naming).
2. **Individual command specs** — 11 commands fully specified across 3
   feature specs (`user-management.md`, `authentication.md`,
   `fetcher-operations.md`).
3. **Catalog** — `docs/cli-reference.md`, an index table.

What is missing is a fourth level: **the shared CLI infrastructure** — the
mechanisms every command relies on (entry point, root group bootstrap,
database session acquisition, error→exit-code mapping, signal handling,
etc.) — analogous to how `fetcher-infrastructure.md` owns `BaseFetcher` for
every fetcher, or `audit-trail-infrastructure.md` owns `BaseAuditLog` for
every audit trail.

A secondary, independent finding: `docs/cli-reference.md` is out of date —
it lists 7 of the 11 currently specified commands.

A third finding: `docs/conventions.md` §"Database Access" currently states
CLI commands use synchronous sessions unconditionally, which contradicts
`user-service.md` and `api-key-service.md` (both require `AsyncSession` via
`asyncio.run()`) and does not match `fetcher-operations.md` (which uses a
synchronous session for direct queries). This is a real, pre-existing
contradiction between specs, independent of the new spec being introduced.

## 2. Decisions Made (confirmed with user)

| # | Decision point | Resolution |
|---|-----------------|------------|
| D1 | Where should CLI infrastructure live? | New dedicated spec: `docs/features/platform/cli-infrastructure.md`, following the `platform/*-infrastructure.md` pattern. |
| D2 | How to resolve the sync/async session contradiction? | **Hybrid model**: synchronous session for direct read-only queries (`fetcher list`, `fetcher config`); `AsyncSession` + `asyncio.run()` for commands that delegate to async services (`manage-user *`, `api-key *`). `conventions.md` is corrected to describe this hybrid model instead of "sync only". |
| D3 | Should secondary documentation gaps be fixed in the same change? | Yes — `cli-reference.md` completeness, `AGENTS.md` file-placement map, `platform/README.md` index. |
| D4 | Should `--json` output be specified as shared infrastructure? | **No.** No command currently defines a `--json` flag; specifying an envelope now would be premature generalization (Guardrail 21-C: rule observed in zero real contexts). The new spec explicitly states this is out of scope for this phase. |
| D5 | What to do with `conventions.md:781` ("No JSON output unless a `--json` flag is explicitly added to a command")? | **Reword** to a generic constraint that does not name a specific flag: structured/machine-readable output is never the default; if a future command needs it, it must be an explicit per-command opt-in. This preserves the useful guardrail (JSON never silently becomes the default) while removing the forward reference to an unused, unspecified flag name. |

## 3. Files to Create

### 3.1 `docs/features/platform/cli-infrastructure.md` (NEW FILE)

Full content below. This is the authoritative text to write verbatim
(subject to review corrections) when the plan is applied.

```markdown
# CLI Infrastructure

## Purpose & Scope

This specification defines the shared infrastructure underlying every
Sentinel CLI command: the package entry point, the root command group
and its bootstrap sequence, database session acquisition, error-to-exit-code
mapping, signal handling, the configuration-guard mechanism, and
interactive input helpers.

**This specification does not restate the CLI contract already defined in
`docs/conventions.md` (CLI Conventions)** — framework choice (Click),
command design principles, the Output Contract (channel separation, exit
code table, success/error message format, multi-step `✓`/`✗`/`—`
reporting, idempotency declarations), and naming conventions remain owned
by `docs/conventions.md`. This specification owns the shared *mechanism*
that implements that contract, so that individual command specs
(`user-management.md`, `authentication.md`, `fetcher-operations.md`) do
not each need to re-derive it.

**This specification does not define individual commands.** Each command's
parameters, behavior, and command-specific error messages remain owned by
its feature spec, per the classification rule in
`docs/conventions.md` (CLI Command Behaviors are documented via the CLI
Output Contract, not the Q1-Q6 completeness framework).

**Out of scope for this phase**: structured/machine-readable (`--json`)
output. No currently specified command defines such a flag. If a future
command requires it, the shared serialization envelope will be defined
here at that time — specifying it now would be speculative. See
`docs/conventions.md` (Human-Readable Format) for the current constraint
that plain text remains the default and any structured output must be an
explicit per-command opt-in.

## Related Specifications

| Spec | Relationship |
|------|--------------|
| `docs/conventions.md` (CLI Conventions) | Authoritative contract this spec implements: framework, Output Contract, exit codes, naming. |
| `docs/features/platform/logging.md` (Scope of this pipeline) | Defines the minimal structlog-to-stderr configuration this spec's bootstrap sequence applies at root group initialization. |
| `docs/features/platform/testing-strategy.md` (Mandatory Test Scenarios → CLI Commands) | Defines the mandatory CLI test scenarios; this spec defines the harness those tests run against. |
| `docs/features/identity/user-management.md` | Consumer: `manage-user` command group delegates to async services via the pattern defined here. |
| `docs/features/identity/authentication.md` | Consumer: `api-key` command group delegates to async services via the pattern defined here. |
| `docs/features/platform/fetcher-operations.md` | Consumer: `fetcher` command group uses the synchronous query path defined here. |
| `docs/features/identity/user-service.md`, `docs/features/identity/api-key-service.md` | Define the `asyncio.run()` invocation pattern this spec's async session path wraps. |

## Package Entry Point & Invocation

| Property | Value |
|---|---|
| Console script | `sentinel`, registered via `[project.scripts]` in `backend/pyproject.toml` (`sentinel = "app.cli:main"`) |
| Module invocation | `python -m sentinel ...`, backed by `backend/app/cli/__main__.py` delegating to the same `main()` entry point |
| Code location | `backend/app/cli/` (new top-level package under `backend/app/`) |
| Group assembly | `backend/app/cli/__init__.py` defines the root Click group (`main`) and registers each command group (`manage-user`, `fetcher`, `api-key`, and any future group) as a sub-group via `main.add_command(...)` |

Each command group (`manage-user`, `fetcher`, `api-key`) is implemented as
its own Click `Group` in a dedicated module under `backend/app/cli/`
(e.g., `backend/app/cli/manage_user.py`), imported and registered by the
root group at import time. This mirrors the fetcher registry's
import-time discovery pattern (`fetcher-infrastructure.md`, Registry)
without requiring dynamic discovery — the CLI's command surface is small
and enumerated explicitly.

**Q1 (inputs)**: N/A — this section describes packaging, not a function.

## Root Command Group & Bootstrap

The root group (`sentinel`) performs the following steps, in order, before
dispatching to the invoked subcommand:

1. Parse global options: `--version` (prints the value from
   `importlib.metadata.version("sentinel")` and exits 0, per the existing
   pattern in `backend/app/main.py`) and `--help` (Click's default
   behavior).
2. Load `Settings` (`backend/app/config.py`). If `Settings` initialization
   raises (invalid configuration), the process fails fast: a plain-text
   `Error: ...` message is printed to stderr and the process exits with
   code 2 (system error), consistent with the fail-fast precedents in
   `docs/conventions.md` (Runtime Version) and `logging.md` (Startup
   Validation).
3. Apply the minimal structlog-to-stderr configuration required by
   `docs/features/platform/logging.md` (§"Scope of this pipeline"): route
   structlog output through stdlib `logging` to stderr, plain-text format,
   level `WARNING` or above. This ensures DEBUG/INFO messages from shared
   service code invoked by CLI commands do not pollute stdout, which
   remains reserved exclusively for the CLI Output Contract. No
   correlation IDs (`request_id`, `celery_task_id`, `fetcher_run_id`) are
   bound in this context.
4. Dispatch to the invoked subcommand.

**Q1 (inputs)**: global CLI arguments (`--version`, `--help`, and the
subcommand path) — standard Click argument parsing, no custom semantics
beyond what is described above.

**Q2 (guards)**: `Settings` validation failure aborts before any subcommand
executes (exit 2). No other root-level guard exists — per-command guards
(e.g., configuration guards, see below) are evaluated by each subcommand.

**Q3 (behavior)**: as enumerated in the four steps above; no other root
group behavior exists.

**Q6 (exceptions)**: `Settings` validation exceptions are caught at this
level and converted to the exit-2 path described in step 2. All other
exceptions propagate to the per-command error handling described in
"Error Handling & Exit Code Mapping" below.

## Database Session Management

CLI commands acquire a database session through one of two paths,
selected per-command based on whether the command delegates to an async
service module or performs a direct read-only query. **A command MUST use
exactly one of the two paths — never both within the same invocation.**

### Path A — Synchronous session (direct queries)

Used by commands that query the database directly without delegating to
an async service module. Currently: `sentinel fetcher list`,
`sentinel fetcher config <name>` (per `fetcher-operations.md`, "Data
source: queries the database directly (synchronous session)").

- A synchronous SQLAlchemy engine and `sessionmaker` are constructed from
  the same `DATABASE_URL` value used by the async engine
  (`backend/app/database.py`), with the driver component of the URL
  substituted for a synchronous driver (`postgresql+asyncpg://...` →
  `postgresql+psycopg://...`). No separate configuration variable is
  introduced — `DATABASE_URL` remains the single source of the connection
  string, per `docs/conventions.md` (Configuration Management).
- **Implementation note (non-normative, flagged for the implementation
  phase)**: this requires a synchronous PostgreSQL driver (e.g.,
  `psycopg`) as an additional dependency alongside `asyncpg`. This is a
  Guardrail 5 (CI/CD awareness) consideration to revisit when the CLI is
  implemented — not a spec-level requirement addressed further here.
- The session is opened at the start of the command, used for one or more
  reads, and closed (via context manager) before the command returns. No
  explicit transaction/commit is required for read-only commands (no
  writes occur on this path — per Guardrail 12/24, the `fetcher` CLI group
  is read-only by design, all mutations are API-only).
- Connection failure (database unreachable) is not caught locally — it
  propagates per "Error Handling & Exit Code Mapping" below, resulting in
  exit code 2.

### Path B — Async session via `asyncio.run()` (service delegation)

Used by commands that delegate to an async service module. Currently: all
`sentinel manage-user *` commands (delegate to `user_service`, per
`user-service.md`) and all `sentinel api-key *` commands (delegate to
`api_key_service`, per `api-key-service.md`).

- The command constructs an `AsyncSession` using the existing async
  `async_sessionmaker` (`backend/app/database.py`) and passes it into a
  single `asyncio.run(...)` call wrapping the entire service invocation,
  per the pattern already declared in `user-service.md` ("Async pattern")
  and `api-key-service.md` ("Async pattern"):

  ```python
  asyncio.run(user_service.create_user(session, ...))
  ```

- Session and transaction lifecycle (commit on success, rollback on
  exception) are the responsibility of the async service function being
  called, per the transaction conventions already defined in each service
  spec — this spec does not alter or restate those contracts.
- Exactly one `asyncio.run()` call occurs per command invocation. Nested
  or multiple `asyncio.run()` calls within a single command are not a
  supported pattern.
- Connection failure (database unreachable) propagates as an exception
  from the wrapped async call; see "Error Handling & Exit Code Mapping"
  below.

### Selecting the path

| Condition | Path |
|---|---|
| Command only reads data and does not call an async service function | A (sync) |
| Command calls any function in `app/services/*` (all of which are async, per `docs/conventions.md` and the FastAPI Conventions section) | B (async via `asyncio.run()`) |

This mirrors the existing, already-specified behavior of all 11 current
commands: it does not change any existing command's behavior, it
documents the pattern each already follows.

**Q4 (audit events)**: N/A — this section describes session acquisition
only. Whether a given command's operation creates audit events is
determined entirely by the delegated service function's own contract
(e.g., `user_service.create_user()` creates `IdentityAuditEvent` per
`user-service.md`); this spec introduces no additional audit trail
obligations.

**Q5 (re-invocation)**: N/A at this level — idempotency is a per-command
property already declared in each command's spec (per the CLI Output
Contract's Idempotency declaration), not a property of the session
mechanism itself.

## Error Handling & Exit Code Mapping

Every CLI command is wrapped by a shared error-handling mechanism that
maps exceptions to the exit codes defined in `docs/conventions.md` (CLI
Output Contract, Exit Codes table). This mechanism is implemented once
(e.g., as a Click command decorator or a shared invocation wrapper in
`backend/app/cli/`) and applied to every command, so individual command
specs do not need to restate this mapping.

| Exception / condition | Exit code | Handling |
|---|---|---|
| No exception; command completed (including idempotent no-op) | 0 | Success message printed to stdout per the command's own spec. |
| A `ServiceError` subclass (or any shared exception per `docs/conventions.md`, Service Exception Conventions) raised by a delegated service call | 1 | The exception's message is formatted as `Error: {message}` and printed to stderr. The specific message text is determined by the command's own spec (see each command spec's "Behavior" section for the exact error strings), not by this mechanism. |
| A validation failure raised directly by the CLI command's own input parsing (e.g., invalid username format, password length) — i.e., a guard documented in the command's own spec, not a service exception | 1 | Same formatting as above; message text owned by the command spec. |
| `OSError`/`ConnectionError`/SQLAlchemy connection-level exceptions (database unreachable), or `RedisError` (per `docs/conventions.md`, Redis Error Handling) surfacing from a command that touches Redis | 2 | Printed to stderr as `Error: {message}`. This is the exit code the "Automated Verification" mandatory test scenario in `docs/conventions.md` (CLI Conventions) requires to be simulatable. |
| Any other unhandled exception | 2 | Printed to stderr as `Error: {message}`. Reserved as the catch-all "system error" path per the Exit Codes table in `docs/conventions.md`. |
| `KeyboardInterrupt` (operator sends SIGINT, e.g., Ctrl+C) | 130 | See Signal Handling below. |
| `SIGTERM` received while a command is running | 143 | See Signal Handling below. |

**Q6 (exceptions)**: this mechanism is the terminal exception handler for
every CLI command — no exception propagates past it to the shell. It
propagates nothing; it converts every exception into the exit code above
and a stderr message.

**Q3 (behavior in every case)**: the table above is exhaustive for the
generic mechanism. Command-specific exception→message text mapping
remains the responsibility of each command's own spec (this mechanism
only determines the exit code and the `Error:`/`Warning:` formatting
convention, not the message content).

**Warnings**: a command may emit zero or more `Warning: {message}` lines
to stderr without affecting the exit code (per `docs/conventions.md`,
Error Output). This mechanism does not alter or intercept warnings — they
are emitted directly by command code at the point of detection.

## Signal Handling

The root Click group installs signal handlers for `SIGINT` and `SIGTERM`
that guarantee the exit codes mandated by `docs/conventions.md` (CLI
Output Contract, Exit Codes table):

| Signal | Trigger | Exit code | Behavior |
|---|---|---|---|
| `SIGINT` | Operator presses Ctrl+C | 130 | Click's default `KeyboardInterrupt` handling is allowed to propagate to the shared error-handling mechanism above, which recognizes `KeyboardInterrupt` specifically and exits 130 (not the generic 2) — no cleanup beyond what the interrupted database session's own `__exit__`/`finally` already performs. |
| `SIGTERM` | Process manager requests shutdown | 143 | A handler is registered that raises a `SystemExit(143)`-equivalent signal at the next Python bytecode boundary. No mid-transaction partial commit is attempted — an in-flight database transaction (Path A or Path B) is left to roll back via the session's own `__exit__`/`finally` handling, consistent with normal exception-driven rollback. |

Commands are not expected to perform custom cleanup beyond what their
database session context manager already guarantees. No command in the
current catalog holds a lock or performs a multi-step operation where
partial interruption would leave inconsistent state beyond what a single
transaction rollback already resolves (Path A is read-only; Path B
delegates transaction atomicity entirely to the called service function).

**Q6 (exceptions)**: `KeyboardInterrupt` and the SIGTERM-derived exit are
both terminal — they do not propagate beyond the root group.

## Configuration Guard

`docs/conventions.md` (Command Design) states: "Commands that modify data
MAY check a configuration guard before executing. If a guard is defined
and not enabled, the command MUST exit with a clear error message
explaining which setting to enable." This section defines the shared
mechanism implementing that MAY clause.

A configuration guard is implemented as a decorator applied to a specific
command's Click callback (e.g.,
`@requires_setting("some_setting_name")`), which:

1. Reads the named setting via the system settings mechanism
   (`docs/features/platform/system-settings.md`).
2. If the setting is falsy/disabled, prints
   `Error: This command requires the '{setting_name}' setting to be enabled.`
   to stderr and exits 1 (a user/configuration error, not a system error)
   — before the command's own body executes.
3. If the setting is enabled, proceeds to execute the command body
   normally.

**No currently specified command uses this mechanism** (none of the 11
commands declares a configuration guard). This section exists so that a
future command needing one has a defined shape to follow, per the MAY
clause already present in `docs/conventions.md`. This is not new
scope — it documents the mechanism for an already-declared but
previously unspecified convention.

**Q1 (inputs)**: the setting name (string) to check, provided by the
command author when applying the decorator.

**Q2 (guards)**: the guard itself IS the early-rejection condition — if
disabled, the command body never executes (exit 1).

**Q6 (exceptions)**: none beyond the exit-1 path above; this mechanism
does not raise, it exits directly.

## Interactive Input Helpers

Three shared helpers back the interactive behaviors already declared in
`user-management.md` for `manage-user create`, `manage-user
set-password`, and `manage-user deactivate`:

| Helper | Behavior | Used by |
|---|---|---|
| Hidden password prompt with confirmation | Prompts twice via a hidden (non-echoed) input (Click's `hide_input=True`), compares the two entries. If they differ, the calling command receives a mismatch signal and is responsible for its own error message and exit code (per that command's own spec — this helper does not print the error itself, to preserve each command's exact wording). | `manage-user create`, `manage-user set-password` |
| TTY detection | Checks `sys.stdin.isatty()` before invoking the password prompt. If no TTY is detected, returns a signal the calling command uses to print its own "requires an interactive terminal" error (exact wording owned by the command spec) and exit 1. | `manage-user create`, `manage-user set-password` |
| Confirmation prompt | A yes/no prompt (Click's `confirm()`) for destructiveish operations. Exact prompt text and default answer are owned by the calling command's own spec. | `manage-user deactivate` |

These are implementation-shared utility functions (e.g.,
`backend/app/cli/_prompts.py`), not new behavioral contracts — the
behaviors themselves are already fully specified in
`user-management.md`. This section exists so the shared implementation
has one canonical home instead of being duplicated per command.

**Q1/Q3/Q6**: N/A beyond the table above — these are Category B (no side
effects beyond terminal I/O) helper functions whose complete behavior is
the table itself; they raise no exceptions of their own (a non-matching
password or non-TTY condition is communicated via return value, not by
raising, so each calling command retains full control over its own exact
error message per its own spec).

## Testing

This spec defines the harness; the mandatory test scenarios themselves are
owned by `docs/features/platform/testing-strategy.md` (Mandatory Test
Scenarios → CLI Commands) and are not restated here.

- **Test runner**: Click's `CliRunner` (`click.testing.CliRunner`),
  invoked against the root `sentinel` group.
- **Database fixture**: CLI tests requiring database access use a
  synchronous test session fixture (mirroring Path A) for commands on
  the synchronous path, and the existing async test session fixture
  wrapped in `asyncio.run()` (mirroring Path B) for commands that
  delegate to services. Both fixtures point at the same test database
  used by the rest of the suite (`docs/features/platform/testing-strategy.md`,
  Database Strategy) — no separate CLI-only database is provisioned.
- **Exit code and channel assertions**: the "Automated Verification"
  scenarios required by `docs/conventions.md` (CLI Conventions) are
  implemented against this harness: exit 0 on success/idempotent no-op,
  exit 1 on simulated user errors, exit 2 on simulated system errors
  (achieved by monkeypatching the session factory to raise a connection
  error), stderr/stdout channel separation, and multi-step
  `✓`/`✗`/`—` output for commands with fail-fast multi-step behavior.
- **Signal handling**: not mechanically tested via `CliRunner` (which
  does not simulate OS signals); verified by manual/integration testing
  when the CLI is implemented. This is a documented testing limitation,
  not a gap in this specification.

## Cross-references

- `docs/conventions.md` (CLI Conventions) — the authoritative contract
  this specification implements the mechanism for.
- `docs/features/platform/logging.md` (Scope of this pipeline) — the
  structlog bootstrap configuration applied at root group initialization.
- `docs/features/platform/testing-strategy.md` (Mandatory Test Scenarios
  → CLI Commands) — the test scenarios this spec's harness supports.
- `docs/features/platform/system-settings.md` — the settings mechanism
  read by the Configuration Guard.
- `docs/features/identity/user-management.md`,
  `docs/features/identity/authentication.md`,
  `docs/features/platform/fetcher-operations.md` — command groups
  consuming this infrastructure.
- `docs/features/identity/user-service.md`,
  `docs/features/identity/api-key-service.md` — the async service
  contracts wrapped via the Path B session mechanism.
- `docs/cli-reference.md` — the command catalog.
```

## 4. Files to Modify

### 4.1 `docs/conventions.md`

**Change 1 — §"Database Access" (currently lines 675-679, exact text
below), replace to describe the hybrid model:**

Current text (verify exact line numbers before editing, content may have
shifted):

```
### Database Access

- CLI commands use synchronous database sessions (not async). They are
  one-shot processes, not long-running servers — async provides no benefit
  and adds complexity
```

New text:

```
### Database Access

- CLI commands acquire a database session through one of two paths,
  selected per-command: a synchronous session for commands that query the
  database directly, or an `AsyncSession` wrapped in a single
  `asyncio.run()` call for commands that delegate to an async service
  module (services are async-only — see FastAPI Conventions above). See
  `docs/features/platform/cli-infrastructure.md` (Database Session
  Management) for the full mechanism and the criteria for selecting a
  path.
```

**Change 2 — §"Human-Readable Format" (currently line 781), reword to
remove the `--json` forward reference:**

Current text:

```
#### Human-Readable Format

- Output is human-readable plain text by default
- No JSON output unless a `--json` flag is explicitly added to a command
- Tables use fixed-width columns aligned with spaces (no box-drawing
  characters)
```

New text:

```
#### Human-Readable Format

- Output is human-readable plain text by default
- Structured/machine-readable output is never the default; if a command
  needs it, it MUST be an explicit per-command opt-in, never silently
  produced. As of this writing, no command defines such an option — see
  `docs/features/platform/cli-infrastructure.md` (Purpose & Scope) for the
  rationale
- Tables use fixed-width columns aligned with spaces (no box-drawing
  characters)
```

**Change 3 — add a cross-reference.** In the `### Framework` subsection
(around line 651-658), no change needed (framework choice is unaffected).
Optionally add one sentence at the top of `## CLI Conventions` pointing to
the new spec:

```
## CLI Conventions

See `docs/features/platform/cli-infrastructure.md` for the shared
implementation mechanism (entry point, session management, error
handling, signal handling) backing the contract defined in this section.
```

Insert this immediately after the `## CLI Conventions` heading, before
`### Framework`.

### 4.2 `docs/cli-reference.md`

Replace the entire "Commands" table (currently lines 7-17) with a complete
11-row table, and add a reference to the new infrastructure spec. Full
replacement content:

```markdown
# CLI Reference

Sentinel provides a command-line interface via the `sentinel` entry point. See
`docs/conventions.md` (CLI Conventions) for framework choices, design
guidelines, and the CLI Output Contract, and
`docs/features/platform/cli-infrastructure.md` for the shared
implementation mechanism (entry point, session management, error
handling).

## Commands

| Command                            | Description                              | Idempotent | Spec                                         |
|------------------------------------|------------------------------------------|------------|----------------------------------------------|
| `sentinel manage-user create`      | Create a local user account              | No (interactive) | [user-management](features/identity/user-management.md) |
| `sentinel manage-user update`      | Update an existing user account          | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user deactivate`  | Deactivate a user account                | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user set-password`| Set or reset password for a local user   | No (interactive) | [user-management](features/identity/user-management.md) |
| `sentinel manage-user unlock`      | Clear login lockout counter for a user   | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user list`        | List users with filters                  | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user show`        | Show detailed info for a single user     | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel fetcher list`            | List all fetchers with current state     | Yes        | [fetcher-operations](features/platform/fetcher-operations.md) |
| `sentinel fetcher config <name>`   | Display fetcher configuration            | Yes        | [fetcher-operations](features/platform/fetcher-operations.md) |
| `sentinel api-key list`            | List API keys for a user                 | Yes        | [authentication](features/identity/authentication.md) |
| `sentinel api-key revoke`          | Revoke a specific API key                | Yes        | [authentication](features/identity/authentication.md) |
```

**Verification step before applying**: re-check each command's exact
"Idempotent" declaration against its owning spec at application time (the
values above are taken from the exploration performed in this session);
if any command's spec has changed since, use the current value instead.

### 4.3 `AGENTS.md`

In the file-placement table (currently around line 154-176), add one row.
Insert after the "Backend tests" row (line 174), before "Draft documents"
(line 175):

```
| CLI commands                | `backend/app/cli/`                |
```

Resulting fragment (for context, do not duplicate other rows):

```
| Backend tests              | `backend/tests/`                  |
| CLI commands                | `backend/app/cli/`                |
| Draft documents            | `docs/drafts/`                    |
```

### 4.4 `docs/features/platform/README.md`

**Change 1** — add one line to the `## Specs` code block (currently lines
7-20). Insert `cli-infrastructure.md` in a position consistent with
alphabetical-ish grouping already used (it is not strictly alphabetical;
insert near other cross-cutting mechanism specs, e.g., after
`audit-trail-infrastructure.md` and before `system-settings.md`):

```
fetcher-infrastructure.md       BaseFetcher base class, registry, execution tracking
cve-fetcher-infrastructure.md   BaseCVEFetcher base class, CVE fetcher conventions
git-fetcher-infrastructure.md   BaseGitFetcher base class, git_operations module
networking.md                   HTTP client (httpx), TLS configuration, SUSE CA
fetcher-operations.md           Monitoring, API, and CLI diagnostics for fetchers
audit-trail-infrastructure.md   BaseAuditLog base class, AuditEventMixin
cli-infrastructure.md           Shared CLI mechanism: entry point, session management, error handling
system-settings.md              System settings (default CVSS version, etc.)
health-endpoints.md             Liveness (/health) and readiness (/ready) probes
logging.md                       Operational/diagnostic logging model, correlation IDs
cve-record-parser.md            Shared CVE record parser for all CVE fetchers
cve-source-failure-retry.md     Retry policy for per-source CVE fetch failures
testing-strategy.md             Testing methodology, fixtures, coverage policy
```

**Change 2** — add one bullet to `## Relationships` (currently lines
22-43), after the `fetcher-operations.md` bullet and before the
`system-settings.md` bullet:

```
- `cli-infrastructure.md` defines the shared mechanism backing every CLI
  command (entry point, session management, error handling, signal
  handling); individual command groups
  (`user-management.md`, `authentication.md`, `fetcher-operations.md`)
  consume it. It implements the contract declared in `docs/conventions.md`
  (CLI Conventions) and consumes the CLI bootstrap requirement declared in
  `logging.md` (Scope of this pipeline).
```

### 4.5 `docs/reviews/.tracking.json`

Insert a new entry in the `specs` object, **alphabetically between
`authentication` and `cpe-package-mapping`**, registering
`cli-infrastructure` as an **enabled, not-yet-reviewed** spec. `cache` is
`null` — this is a purely mechanical registration; **no reviewer is
executed to populate it** (the formal 5-reviewer pipeline that would
populate `cache` and open findings is run by the user at a later time, not
as part of this change):

```json
    "cli-infrastructure": {
      "enabled": true,
      "abbr": "CLII",
      "cache": null
    },
```

No `path` field is needed — the spec lives at the default-inferred
location `docs/features/platform/cli-infrastructure.md`, consistent with
`logging`, `health-endpoints`, and other platform specs that omit `path`.

**Verification step before applying**: confirm `CLII` does not collide
with any existing `abbr` value in the file (checked at draft time: no
collision found).

### 4.6 `docs/reviews/README.md`

Insert one row (plus its blank spacer row, matching the table's existing
two-row-per-spec pattern) in the Summary Table, **alphabetically between
the `authentication` and `cpe-package-mapping` rows** (currently lines
16-19). All five reviewer columns show `—` (never executed), `Open` is
`0/0`, `Last Review` is blank, no stale marker (there is nothing to be
stale relative to):

```
| [cli-infrastructure](cli-infrastructure.md) | — | — | — | — | — | 0/0 | — |  |
|  |  |  |  |  |  |  |  |  |
```

No other row in the table changes as a result of this insertion. The
**Total** row (currently `2/786`, unchanged) requires no numeric edit,
since the new spec contributes `0/0`.

This edit, like §4.5, is a **mechanical registration only** — it does not
involve running any reviewer, and does not add any finding to
`docs/reviews/` beyond the empty placeholder row itself.

## 5. Explicit Non-Changes

To avoid ambiguity during application, the following are confirmed **out
of scope** and must NOT be touched by this change:

- No changes to `user-management.md`, `authentication.md`, or
  `fetcher-operations.md` command definitions — their behavior is
  unchanged; the new spec only documents the mechanism they already rely
  on.
- No changes to `docs/data-model.md` — no new tables/columns.
- No changes to `docs/api-spec.md` — no new API endpoints.
- No changes to `docs/configuration.md` — no new environment variables
  are introduced (the synchronous engine reuses `DATABASE_URL`).
- No implementation code is written (`backend/app/cli/` etc. are
  referenced as future locations only, per the specs-first principle).
- `--json` output infrastructure is explicitly NOT specified now, per
  decision D4.
- `docs/drafts/ideas.md` line 7 ("Propose Sentinel command-line commands
  that could be useful") is a distinct, unrelated idea (proposing *new*
  commands) and is not resolved or removed by this change.
- Steps 6 (§4.5/§4.6, review-tracking registration) and 8 (verification
  reviewers) below are independent concerns and must not be conflated:
  step 6 is a mechanical file edit with no reviewer execution; step 8
  runs reviewers but does not write to `docs/reviews/` or to the
  `cache` field registered in step 6. The formal 5-reviewer pass that
  populates real findings for `cli-infrastructure` in `/reviews/` is
  explicitly deferred to the user, outside this change.

## 6. Action Plan (execute in this exact order)

1. **Create** `docs/features/platform/cli-infrastructure.md` with the
   exact content from §3.1 above.
2. **Edit** `docs/conventions.md`:
   a. Apply Change 1 (§"Database Access" rewrite).
   b. Apply Change 2 (§"Human-Readable Format" reword).
   c. Apply Change 3 (cross-reference sentence under `## CLI
      Conventions`).
3. **Edit** `docs/cli-reference.md`: replace the Commands table per §4.2,
   after re-verifying each command's current idempotency declaration
   against its owning spec.
4. **Edit** `AGENTS.md`: add the file-placement row per §4.3.
5. **Edit** `docs/features/platform/README.md`: apply both Change 1
   (Specs list) and Change 2 (Relationships bullet) per §4.4.
6. **Register the new spec in the review tracking system** — apply §4.5
   (`docs/reviews/.tracking.json`: add the `cli-infrastructure` entry,
   `enabled: true`, `cache: null`) and §4.6 (`docs/reviews/README.md`:
   add the `—`/`0/0` row). **This step is purely mechanical — do not
   invoke any reviewer to perform or validate it.** It only marks the
   spec as eligible for the formal review pipeline the user will run
   later.
7. **Self-check for internal coherence** before invoking reviewers:
   - Confirm `cli-infrastructure.md`'s "Related Specifications" table
     lists every spec that now references it back (bidirectional
     consistency).
   - Confirm no other spec still contains the old, contradictory
     "CLI commands use synchronous database sessions (not async)"
     wording (search the whole `docs/` tree for this exact phrase).
   - Confirm `docs/cli-reference.md` now lists exactly 11 commands and
     the count matches the "11 distinct CLI commands" figure used in
     `cli-infrastructure.md`'s own text (if referenced) — this
     specification intentionally does not hardcode the count, so no
     mismatch is expected, but verify no stray count references exist
     elsewhere (e.g., `docs/reviews/user-management.md`, which is
     historical and must NOT be edited).
   - Confirm `docs/reviews/.tracking.json` is still valid JSON after the
     insertion, the new `abbr` (`CLII`) does not collide with any
     existing one, and the entry is positioned alphabetically.
   - Confirm `docs/reviews/README.md`'s Summary Table row for
     `cli-infrastructure` was inserted alphabetically and the **Total**
     row still correctly reflects the sum of all specs (unchanged, since
     the new row contributes `0/0`).
8. **Invoke reviewers** against the changed/created specs to verify the
   plan was applied correctly and without introducing new problems. This
   step verifies the *application of this change* — it is distinct from,
   and does not substitute for, the formal review pipeline the user will
   run later to populate real findings in `docs/reviews/` for
   `cli-infrastructure`. Findings produced here are reported back to the
   user and are **not** written into `docs/reviews/` or into the `cache`
   field registered in step 6:
   - `@spec-gap-analyzer` on `docs/features/platform/cli-infrastructure.md`
     (new spec — Guardrail 17).
   - `@spec-coherence-reviewer` on `docs/features/platform/cli-infrastructure.md`
     (checks against `docs/conventions.md`, `logging.md`,
     `testing-strategy.md`, and the three command-group specs it
     references — Guardrail 15).
   - `@docs-placement-reviewer` — verify the CLI mechanism content placed
     in the new spec is not misplaced relative to `docs/conventions.md`,
     and that the `--json` exclusion and Database Access correction are
     placed correctly (Guardrail 21).
   - `@docs-reviewer` on the full set of changed files (`conventions.md`,
     `cli-reference.md`, `AGENTS.md`, `platform/README.md`,
     `cli-infrastructure.md`) for overall completeness/coherence
     (Guardrail 9).
   - Address any "Needs revision" finding from the above before
     considering the change complete; minor issues should be fixed in the
     same pass.
9. **Delete this draft file**
   (`docs/drafts/cli-infrastructure-change.md`) once all reviewer findings
   from step 8 have been resolved and the change is considered complete.

## 7. Internal Coherence Check (performed on this draft)

- D1↔§3.1: the new spec's own "Purpose & Scope" explicitly delimits
  itself from `conventions.md` and from individual command specs,
  consistent with D1's placement decision and Guardrail 21.
- D2↔§3.1 "Database Session Management": the hybrid model is fully
  specified with an explicit path-selection table; §4.1 Change 1 updates
  `conventions.md` to match, removing the contradiction identified in §1.
- D3↔§4.2/§4.3/§4.4: all three secondary gaps (cli-reference.md,
  AGENTS.md, platform/README.md) have concrete, complete edits specified.
- D4↔§3.1 "Purpose & Scope" (Out of scope bullet) and §5 (Explicit
  Non-Changes): both consistently state `--json` is excluded; no other
  section of the new spec introduces `--json` handling.
- D5↔§4.1 Change 2: the reworded text drops the `--json` flag name while
  preserving the "never default" constraint, matching the decision
  exactly.
- Cross-check: `cli-infrastructure.md` §"Related Specifications" lists
  `user-management.md`, `authentication.md`, `fetcher-operations.md`,
  `user-service.md`, `api-key-service.md`, `logging.md`,
  `testing-strategy.md`, `system-settings.md` — every one of these is
  either edited in this plan (none are — confirmed correct, since their
  command definitions are unchanged per §5) or already contains the
  content being referenced (verified during exploration: `logging.md`
  lines 294-312 for the bootstrap requirement; `testing-strategy.md`
  Mandatory Test Scenarios → CLI Commands; `user-service.md`/
  `api-key-service.md` "Async pattern" sections).
- No section of the new spec duplicates content already owned elsewhere
  without a cross-reference (checked against Guardrail 21-A "Duplication"
  test): Output Contract details (exit codes, channel separation,
  idempotency declaration format) are referenced, not restated; only the
  exit-code *mapping mechanism* (which exception maps to which code) is
  newly specified, since that mechanism did not exist anywhere before.
- The plan does not touch `docs/data-model.md`, `docs/api-spec.md`, or
  any file outside the seven files enumerated in §3 and §4 (the new spec
  itself, plus `conventions.md`, `cli-reference.md`, `AGENTS.md`,
  `platform/README.md`, `docs/reviews/.tracking.json`, and
  `docs/reviews/README.md` — the last two added in §4.5/§4.6) —
  consistent with the "no implementation, no migrations" framing
  requested by the user.
- Action plan ordering (§6) creates the new spec first, then edits
  cross-referencing files, then registers the spec in the review
  tracking system (step 6), then self-checks (step 7), then runs
  verification reviewers (step 8), then deletes the draft (step 9) —
  matching the user's explicit requests: review-tracking registration
  added as its own step (not folded into the reviewer step), reviewer
  execution and draft deletion remain the last two steps.
- Step 6 (§4.5/§4.6) and step 8 (verification reviewers) are kept
  strictly separate, per the user's explicit clarification: step 6 is a
  mechanical registration with **no reviewer execution**; step 8 runs
  reviewers to verify correct application of this change but does
  **not** write findings into `docs/reviews/` or into the `cache` field
  registered in step 6. The formal review pipeline that will populate
  real findings for `cli-infrastructure` is deferred to the user, to be
  run at a later time, entirely outside this change.
- `docs/reviews/.tracking.json` and `docs/reviews/README.md` are edited
  consistently with each other (§4.5/§4.6): both register
  `cli-infrastructure` in the same "enabled, never reviewed" state
  (`cache: null` ↔ all-`—` row), at the same alphabetical position
  (between `authentication`/`cpe-package-mapping`), with no discrepancy
  between the two files.

**Result of coherence check**: no internal contradictions found. The
draft is ready for review.
