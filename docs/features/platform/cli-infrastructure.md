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
| `docs/features/platform/testing-strategy.md` (Mandatory Test Scenarios → CLI Commands, Sync Entry-Point Tests) | Defines the mandatory CLI test scenarios and the synchronous test function requirement; this spec defines the harness those tests run against. |
| `docs/features/identity/user-management.md` | Consumer: `manage-user` command group delegates to async services via the pattern defined here. |
| `docs/features/identity/authentication.md` | Consumer: `api-key` command group delegates to async services via the pattern defined here. |
| `docs/features/platform/fetcher-operations.md` | Consumer: `fetcher` command group uses the `asyncio.run()` session mechanism defined here for its read-only queries. |
| `docs/features/identity/user-service.md`, `docs/features/identity/api-key-service.md` | Define the async service contracts invoked from within the `asyncio.run()` mechanism defined here. |
| `docs/features/platform/system-settings.md` | Defines the system settings mechanism consumed by the Configuration Guard decorator. |
| `docs/cli-reference.md` | Catalog: index table of all CLI commands, cross-referencing this spec for the shared mechanism. |

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

Sentinel is an async-only project (see `docs/conventions.md`, SQLAlchemy
Conventions): no synchronous database driver or engine exists anywhere
in the codebase (`backend/app/database.py` defines only
`create_async_engine` and `async_sessionmaker`; `backend/pyproject.toml`
declares only `asyncpg`, no `psycopg`/`psycopg2`). Every CLI command,
whether read-only or mutating, follows the same mechanism — there is no
per-command path selection.

- The command constructs an `AsyncSession` via the existing
  `async_session_factory` (`backend/app/database.py`) and passes it
  into a single `asyncio.run(...)` call wrapping the **entire command
  workflow** — not just a service invocation. For read-only commands
  (`fetcher list`, `fetcher config`, `manage-user list`, `manage-user
  show`, `api-key list`), the wrapped function performs the read(s)
  directly. For commands that delegate to an async service module
  (`manage-user create`, `update`, `deactivate`, `set-password`,
  `unlock`, and `api-key revoke`), the wrapped function calls the
  service function, per the async pattern already declared in
  `user-service.md` ("Async pattern") and `api-key-service.md` ("Async
  pattern").
- For commands with pre-mutation reads or interactive prompts (e.g.,
  `manage-user deactivate` reads impact counts, prompts for
  confirmation, then deactivates), all of these steps run inside the
  same `asyncio.run()` call. Blocking terminal I/O (e.g.,
  `click.confirm()`, hidden password prompts) inside the wrapped async
  function is acceptable for one-shot CLI processes — the event loop has
  no other work scheduled while waiting for operator input, unlike in a
  server context where blocking would stall concurrent requests, e.g.:

  ```python
  async def deactivate_flow(session_factory, username):
      async with session_factory() as db:
          user = await lookup_user(db, username)      # pre-mutation reads
          impact = await count_deactivation_impact(db, user)
      if not click.confirm("Proceed?"):                # blocking prompt
          print("Aborted.")                            # printed to stdout
          return                                        # exit 0, no mutation
      async with session_factory() as db:
          await user_service.deactivate_user(db, user.id, ...)  # mutation

  asyncio.run(deactivate_flow(async_session_factory, username))
  ```

  When the operator declines a confirmation prompt, the command prints a
  short confirmation of inaction (e.g., `"Aborted."`) to stdout and exits
  0 — declining is a valid, non-erroneous outcome (the desired state,
  "do not proceed," was honored), not a user error. The exact wording of
  the abort message is owned by the calling command's own spec; this
  section only fixes the exit code (0) and the fact that no mutation
  occurs. If the confirmation prompt receives EOF instead of an explicit
  answer (e.g., piped/closed stdin, Ctrl+D), Click raises `click.Abort`;
  this is treated identically to an explicit decline (stdout message,
  exit 0) — TTY detection (see Interactive Input Helpers) is expected to
  reject non-interactive invocations before reaching the prompt in the
  first place, so `click.Abort` from an already-detected TTY session
  represents an explicit operator-initiated cancellation, not a system
  error.

  For commands with no pre-mutation reads or prompts, the simpler
  single-call form applies directly. Mutation example:

  ```python
  async def revoke_flow(session_factory, key_id):
      async with session_factory() as db:
          await api_key_service.revoke_key(db, key_id, ...)

  asyncio.run(revoke_flow(async_session_factory, key_id))
  ```

  Read-only example:

  ```python
  async def fetcher_list(session_factory):
      async with session_factory() as db:
          fetchers = (await db.execute(select(FetcherConfig))).scalars().all()
      print_table(fetchers)

  asyncio.run(fetcher_list(async_session_factory))
  ```

- Session and transaction lifecycle (commit on success, rollback on
  exception) for mutation commands are the responsibility of the async
  service function being called, per the transaction conventions
  already defined in each service spec — this spec does not alter or
  restate those contracts. Read-only commands require no explicit
  commit (no writes occur — per `fetcher-operations.md` (CLI Commands),
  the `fetcher` CLI group is read-only by design; all mutations are done
  exclusively through the API).
- Exactly one `asyncio.run()` call occurs per command invocation. Nested
  or multiple `asyncio.run()` calls within a single command are not a
  supported pattern.
- Connection failure (database unreachable) propagates as an exception
  from the wrapped async call; see "Error Handling & Exit Code Mapping"
  below.

As of this writing, of the 11 currently specified commands, 5 are
read-only (`fetcher list`, `fetcher config`, `manage-user list`,
`manage-user show`, `api-key list`) and 6 delegate to an async service
module for mutation (`manage-user create`, `manage-user update`,
`manage-user deactivate`, `manage-user set-password`, `manage-user
unlock`, `api-key revoke`). This documents the pattern each command
already follows in its owning spec (`user-management.md`,
`authentication.md`, `fetcher-operations.md`) — this section does not
change any existing command's behavior, it only defines the shared
mechanism underlying all of them. This inventory is illustrative, not
an exhaustive registry that must be updated per new command (the
authoritative, current list lives in `docs/cli-reference.md`).

**Q4 (audit events)**: N/A — this section describes session acquisition
only. Whether a given command's operation creates audit events is
determined entirely by the delegated service function's own contract
(e.g., `user_service.create_user()` creates `IdentityAuditEvent` per
`user-service.md`); this spec introduces no additional audit trail
obligations. Read-only commands create no audit events.

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
| `click.UsageError` / `click.BadParameter` (missing required option, invalid option type, mutually exclusive option violation — i.e., malformed invocation caught by Click itself before the command callback executes) | 1 | Click's built-in argument parsing and validation are preserved unchanged (error formatting, type coercion, `--help` hints). Click's default behavior exits with code 2 for these exceptions; the root group remaps this to exit code 1, consistent with `docs/conventions.md` classifying "bad input" as a user error (exit 1), not a system error (exit 2). Implementation: the root Click group overrides exit handling to intercept `SystemExit(2)` raised by Click's internal `UsageError` handling and re-raise as `SystemExit(1)`. This remapping applies only to Click-originated usage errors — it does not alter the meaning of exit code 2 for any other condition in this table. |
| `OSError`/`ConnectionError`/SQLAlchemy connection-level exceptions (database unreachable), or `RedisError` (per `docs/conventions.md`, Redis Error Handling) surfacing from a command that touches Redis | 2 | Printed to stderr as `Error: {message}`. This is the exit code the "Automated Verification" mandatory test scenario in `docs/conventions.md` (CLI Conventions) requires to be simulatable. |
| Any other unhandled exception | 2 | Printed to stderr as `Error: {message}`. Reserved as the catch-all "system error" path per the Exit Codes table in `docs/conventions.md`. |
| `click.Abort` (EOF/Ctrl+D received by an interactive prompt, e.g. `click.confirm()` or a hidden password prompt) | 0 | Treated identically to an explicit decline of the prompt (see Database Session Management, "Interactive prompts" discussion) — an aborted interactive prompt is an operator-initiated cancellation, not an error. TTY detection (Interactive Input Helpers) is expected to reject non-interactive invocations before a prompt is reached in the first place; `click.Abort` reaching this handler means an already-detected TTY session was cancelled mid-prompt. |
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
| `SIGTERM` | Process manager requests shutdown | 143 | A handler is registered that raises a `SystemExit(143)`-equivalent signal at the next Python bytecode boundary. No mid-transaction partial commit is attempted — an in-flight database transaction is left to roll back via the session's own `__aexit__`/`finally` handling, consistent with normal exception-driven rollback. |

Commands are not expected to perform custom cleanup beyond what their
database session context manager already guarantees. No command in the
current catalog holds a lock or performs a multi-step operation where
partial interruption would leave inconsistent state beyond what a single
transaction rollback already resolves (read-only commands perform no
writes; mutation commands delegate transaction atomicity entirely to the
called service function).

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

The guard's setting read (step 1) executes **inside** the same
`asyncio.run()` call that wraps the command's own workflow (see
Database Session Management, "exactly one `asyncio.run()` call per
command invocation") — the decorator wraps the command's async
workflow function itself and performs its check as the first step of
that function, before any command-specific logic runs. It does not
open a second `asyncio.run()` call. If the settings read itself fails
(e.g., database unreachable), that failure propagates through the same
exit-2 "connection failure" path defined in Error Handling & Exit Code
Mapping — the guard introduces no separate error-handling path.

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
set-password`, and `manage-user deactivate`. Any command requiring
interactive terminal input SHOULD use these helpers rather than
reimplementing the pattern. The "Example consumers" column lists
representative usages, not an exhaustive registry — each command's own
spec remains authoritative for which helpers it uses, so this table
does not need to be updated every time a new command adopts one of
these helpers:

| Helper | Behavior | Example consumers |
|---|---|---|
| Hidden password prompt with confirmation | Prompts twice via a hidden (non-echoed) input (Click's `hide_input=True`), compares the two entries. If they differ, the calling command receives a mismatch signal and is responsible for its own error message and exit code (per that command's own spec — this helper does not print the error itself, to preserve each command's exact wording). | `manage-user create`, `manage-user set-password` |
| TTY detection | Checks `sys.stdin.isatty()` before invoking a prompt (password entry or confirmation). If no TTY is detected, returns a signal the calling command uses to print its own "requires an interactive terminal" error (exact wording owned by the command spec) and exit 1. | `manage-user create`, `manage-user set-password` (before password prompt), `manage-user deactivate` (before confirmation prompt) |
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
- **Test function type**: CLI tests MUST be synchronous (`def`, not
  `async def`) — see `docs/features/platform/testing-strategy.md`
  (Sync Entry-Point Tests) for the cross-cutting rationale. Click's
  `CliRunner.invoke()` is itself synchronous; the commands under test
  internally call `asyncio.run()`, which would raise `RuntimeError` if
  invoked from within an already-running event loop.
- **Database fixture**: the async session factory is passed into the
  command under test (not injected via an async fixture in the test
  function itself, since the test function is synchronous). The
  factory points at the same test database used by the rest of the
  suite (`docs/features/platform/testing-strategy.md`, Database
  Strategy) — no separate CLI-only database is provisioned, for both
  read-only and mutation commands.
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
