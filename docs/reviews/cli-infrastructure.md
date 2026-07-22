# Review: cli-infrastructure

**Spec**: `docs/features/platform/cli-infrastructure.md`
**Last reviewed**: 2026-07-22
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CLII-GAP-01 — Exit code 2→1 remapping cannot distinguish Click-originated from command-originated SystemExit(2) (Medium)

**Category**: Error handling
**Status**: OPEN

In "Error Handling & Exit Code Mapping" (~line 245), the spec states the root Click group "intercepts `SystemExit(2)` raised by Click's internal `UsageError` handling and re-raise as `SystemExit(1)`" and that "this remapping applies only to Click-originated usage errors." However, the described mechanism (generically intercepting `SystemExit(2)`) cannot actually distinguish a Click-originated `UsageError` exit from a command's own error handler raising `SystemExit(2)` for a genuine system error (e.g., database unreachable). Both produce identical `SystemExit(2)` at the interception point, so the remapping would incorrectly convert a real system error to a user error (exit 1).

**Recommended fix**: Specify that the remapping mechanism catches `click.UsageError` directly (before Click converts it to `SystemExit`), rather than intercepting `SystemExit(2)` generically after the fact. This achieves the stated intent precisely and avoids misclassifying non-Click system errors.

### CLII-GAP-02 — click.Abort message at the shared error handler is unspecified (Medium)

**Category**: Error handling
**Status**: OPEN

In "Error Handling & Exit Code Mapping" (~line 248), `click.Abort` is "treated identically to an explicit decline of the prompt," which per the Database Session Management section means printing a short confirmation of inaction to stdout. But when `click.Abort` propagates from an EOF during a prompt (Ctrl+D) all the way to the shared error handler (bypassing the command's own code, which never gets a chance to print its own message), it's unclear what the shared handler itself should print: nothing (silent exit 0), a generic message like "Aborted.", or Click's own default "Aborted!" written to stderr (which would violate the stdout/stderr channel separation contract in conventions.md, since this is a cancellation, not an error).

**Recommended fix**: Specify the exact message the shared error handler prints when it catches `click.Abort` (e.g., a generic "Aborted." to stdout), so behavior is consistent regardless of whether the command's own code or the shared handler catches the exception.

### CLII-GAP-03 — OSError/ConnectionError catch clause is broader than the described "database unreachable" scenario (Low)

**Category**: Error handling
**Status**: OPEN

In "Error Handling & Exit Code Mapping" (~line 246), the exception classes listed for exit code 2 include `OSError`/`ConnectionError`/SQLAlchemy connection-level exceptions "(database unreachable)". These base classes also match `BrokenPipeError` (e.g., when stdout is piped to a command that exits early, such as `sentinel manage-user list | head -1`), `FileNotFoundError`, `PermissionError`, and other unrelated OS-level errors. Since the catch-all row also exits 2, the practical exit-code impact is limited, but the printed error message ("Error: [Errno 32] Broken pipe") would misleadingly describe a database problem when the actual cause is unrelated.

**Recommended fix**: Either narrow the exception classes to database/Redis-specific types (letting broader `OSError` subclasses fall through to the catch-all with a generic message), or explicitly acknowledge that this category intentionally covers broader OS-level connection failures beyond just the database.

---

## Coherence

### CLII-COH-01 — Warning prefix casing inconsistency (`WARNING:` vs `Warning:`) (Low)

**Category**: Cross-spec consistency
**Status**: OPEN

`docs/conventions.md` (CLI Conventions, Error Output) and `docs/features/platform/cli-infrastructure.md` both define the warning prefix as `Warning:` (capitalized first letter only). However, `docs/features/identity/user-management.md` uses `WARNING:` (all-uppercase) for the last-admin warning in `manage-user deactivate` (two occurrences), while the same file correctly uses `Warning:` for the `manage-user unlock` command. This is an internal inconsistency within `user-management.md` and a contradiction with the authoritative format in `conventions.md`.

**Recommended fix**: Update `docs/features/identity/user-management.md` to use `Warning:` (matching conventions.md) instead of `WARNING:` for the last-admin deactivation warning, for consistency with the rest of the codebase and to pass automated output-contract verification tests.

---

## Design

### CLII-DES-01 — Two-session deactivate_flow example is misleading (TOCTOU) (High)

**Category**: Concurrency / example clarity
**Status**: OPEN

The `deactivate_flow` code example in the "Database Session Management" section (lines ~141-151) opens one session for pre-mutation reads (looking up the user, counting deactivation impact), closes it, shows an interactive confirmation prompt, then opens a NEW session for the mutation itself. The example implies these pre-mutation reads (user lookup, impact counts) are load-bearing inputs to the mutation decision. Between the two sessions, the user's state could change via a concurrent API call (role change, deletion, another deactivation). The service layer (`user_service.deactivate_user()`) likely re-validates all preconditions inside its own locked transaction (per `docs/conventions.md`, Transaction and Locking), making the mutation itself safe — but the spec's example does not make this explicit, and an implementer could reasonably conclude the pre-read counts must remain accurate/authoritative for correctness.

**Recommended fix**: Add an explicit note below the `deactivate_flow` example stating that pre-mutation reads are informational/advisory for the human operator's confirmation decision only; the service function independently validates all preconditions (guards) inside its own locked transaction, so staleness between the display and the mutation does not cause incorrect behavior.

---

## Security

_Reviewed. No open findings._

---

## API Conventions

_Reviewed. No open findings._
