# Review: cli-infrastructure

**Spec**: `docs/features/platform/cli-infrastructure.md`
**Last reviewed**: 2026-07-22
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CLII-GAP-01 — Exit code 2→1 remapping cannot distinguish Click-originated from command-originated SystemExit(2) (Medium)

**Status**: RESOLVED — Replaced the `SystemExit(2)` interception hack with a unified exception mapper: the root group now invokes Click with `standalone_mode=False` and catches `click.ClickException` directly (before Click ever converts it to a process exit), always mapping to exit 1 via `e.show()` + explicit exit, eliminating any ambiguity with command-originated system errors (2026-07-22)

### CLII-GAP-02 — click.Abort message at the shared error handler is unspecified (Medium)

**Status**: RESOLVED — Specified that the mapper prints an explicit `Aborted.` to stdout and exits 0 when it catches `click.Abort` in non-standalone mode (where Click itself does not print anything), covering both the EOF-bypasses-command-code case and the explicit-decline case (2026-07-22)

### CLII-GAP-03 — OSError/ConnectionError catch clause is broader than the described "database unreachable" scenario (Low)

**Status**: RESOLVED — Narrowed the exit-2 database/Redis row to SQLAlchemy `OperationalError`/`DBAPIError` and `RedisError` specifically; generic `OSError`/`ConnectionError` are no longer caught there — broken pipes are handled by Click's own EPIPE handling, and other OSError subclasses fall through to the generic catch-all (2026-07-22)

---

## Coherence

### CLII-COH-01 — Warning prefix casing inconsistency (`WARNING:` vs `Warning:`) (Low)

**Status**: RESOLVED — Changed `WARNING:` to `Warning:` in `docs/features/identity/user-management.md` (example output block and "Output channels" description for `manage-user deactivate`) to match the authoritative format in `conventions.md` (2026-07-22)

---

## Design

### CLII-DES-01 — Two-session deactivate_flow example is misleading (TOCTOU) (High)

**Status**: RESOLVED — Added a note after the `deactivate_flow` example in "Database Session Management" clarifying that pre-mutation reads are advisory only for operator confirmation, while `user_service.deactivate_user()` independently re-validates preconditions under `SELECT ... FOR UPDATE` locking, so staleness between sessions is a deliberate, non-defective tradeoff (2026-07-22)

---

## Security

_Reviewed. No open findings._

---

## API Conventions

_Reviewed. No open findings._
