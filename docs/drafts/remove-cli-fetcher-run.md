# Remove `sentinel fetcher run` CLI Command

## Status

**Draft** — not yet implemented.

## Origin

The draft `docs/drafts/cli-fetcher-run-lifecycle-delegation.md` proposed
refactoring the CLI `sentinel fetcher run` command to delegate lifecycle
management to `BaseFetcher.run()`. Review of that draft (5 independent
reviewers: design, coherence ×2, gap analysis ×2) revealed 6
high-severity issues, 12 medium-severity issues, and 6 low-severity
issues — all arising from the inherent complexity of making the CLI a
full-fidelity entry point for fetcher execution.

Subsequent analysis determined that the command's primary justification
— the bootstrap chicken-and-egg problem (no users → no auth → cannot
call API to trigger LDAP sync) — can be solved more simply by creating
a local admin user via `sentinel manage-user create` first, then using
the API to trigger the fetcher. This eliminates the only use case that
had no alternative.

## Problem Statement

`sentinel fetcher run <name>` introduces significant specification and
implementation complexity:

- Lifecycle management duplication (reimplements `BaseFetcher.run()`)
- Signal handling through `asyncio.run()` (unreliable `KeyboardInterrupt`
  propagation)
- `FetcherConfig` FK constraint violation during bootstrap (the exact
  scenario the command targets)
- Concurrency check TOCTOU window
- Enabled check bypass without audit trail
- Session management ambiguity (sync CLI vs async `run()`)

Meanwhile, the bootstrap use case — the only one with no API
alternative — is solvable by reversing the sequence: create a local
admin first, then use the authenticated API to trigger fetchers.

## Alternative Bootstrap Sequence

**Current** (requires `sentinel fetcher run`):

```
1. sentinel fetcher run sync_ldap_directory          # populate Users
2. sentinel manage-user update --username admin1 --add-role admin
```

**Proposed** (no `sentinel fetcher run` needed):

```
1. Start all services (API, Celery worker, Celery Beat, Redis, PostgreSQL)
2. sentinel manage-user create --username bootstrap-admin \
     --email bootstrap@localhost --role admin          # local admin
3. Authenticate to API as bootstrap-admin (local login — see local-authentication.md)
4. POST /api/v1/fetchers/sync_ldap_directory/trigger  # LDAP sync via Celery
5. Poll GET /api/v1/fetchers/sync_ldap_directory/runs/{run_id} until completion
6. sentinel manage-user update --username <ad-user> --add-role admin  # promote real admin
7. sentinel manage-user deactivate --username bootstrap-admin         # recommended cleanup
```

**Why this works**:

- `manage-user create --role admin` requires only the DB schema — no
  prior data (confirmed by `deployment.md` line 136-144, already
  documented as the standard local dev bootstrap)
- The `admin` role includes `manage_fetchers` capability (`rbac.md`
  line 83)
- Celery workers auto-create `FetcherConfig` records at startup
  (`fetcher-infrastructure.md` line 822) — by the time step 4 runs,
  records exist
- The LDAP sync ignores local users (`ad_object_guid = NULL`) entirely
  (`user-management.md` lines 906-917) — no conflict with the bootstrap
  user
- If the bootstrap username collides with an AD `sAMAccountName`, the
  sync skips that AD entry with a warning — the local user is never
  modified (`ad-integration.md` lines 328-347)

## Proposed Changes

### Change 1: Remove `sentinel fetcher run <name>` from `fetcher-operations.md`

**File**: `docs/features/platform/fetcher-operations.md`

Remove the entire `### sentinel fetcher run <name>` section (lines
908-1040), including:

- Execution model (lines 927-953)
- Concurrency check (lines 954-991)
- Enabled check bypass (lines 992-1003)
- Signal handling (lines 1005-1024)
- Exit codes table (lines 1026-1040)

The commands `sentinel fetcher list` and `sentinel fetcher config`
remain unchanged — they are read-only commands with no lifecycle
complexity.

### Change 2: Update deregistered fetcher behavior in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md` (line 983)

Replace:

```markdown
- The `sentinel fetcher run` CLI command returns an error for
  deregistered fetchers. `sentinel fetcher config` displays a
  read-only snapshot of the stored configuration without schema context
```

With:

```markdown
- `sentinel fetcher config` displays a read-only snapshot of the stored
  configuration without schema context
```

### Change 2b: Clean up remaining CLI references in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`

The Stale Run Detection section and data model descriptions contain
several references to CLI-based stale resolution and triggering that
no longer apply after removing `sentinel fetcher run`.

**2b-i** — Replace lines 732-733:

```markdown
When a stale run is detected (by the Celery task, the API trigger
endpoint, or the CLI), it is resolved by updating the stale `FetcherRun`
record:
```

With:

```markdown
When a stale run is detected (by the Celery task or the API trigger
endpoint), it is resolved by updating the stale `FetcherRun` record:
```

**2b-ii** — Replace lines 736-741:

```markdown
**Operational risk of `timeout_seconds=0`**: disabling stale detection
means a fetcher that gets stuck will block all future executions
indefinitely, requiring manual intervention. When `timeout_seconds` is
set to 0 via the API or CLI, a warning is surfaced to the operator (see
`docs/features/platform/fetcher-operations.md`, "Update Fetcher Config"
for the API warning field and CLI warning message).
```

With:

```markdown
**Operational risk of `timeout_seconds=0`**: disabling stale detection
means a fetcher that gets stuck will block all future executions
indefinitely, requiring manual intervention. When `timeout_seconds` is
set to 0 via the API, a warning is surfaced to the operator (see
`docs/features/platform/fetcher-operations.md`, "Update Fetcher Config"
for the API warning field).
```

**2b-iii** — Replace lines 743-746:

```markdown
- `error_message` → `"Marked as stale (running for {elapsed}, timeout
  {timeout}s)"` for automatic resolution (Celery/API), or `"Marked as
  stale by operator via CLI"` for CLI resolution
```

With:

```markdown
- `error_message` → `"Marked as stale (running for {elapsed}, timeout
  {timeout}s)"`
```

**2b-iv** — Replace lines 757-761:

```markdown
Stale run detection is a recovery mechanism for unclean process
terminations (OOM-kill, node crash, `kill -9`). It is NOT a substitute
for proper signal handling — processes that can handle `SIGINT`/`SIGTERM`
must do so (see `docs/features/platform/fetcher-operations.md`, section "CLI
Commands", "Signal handling").
```

With:

```markdown
Stale run detection is a recovery mechanism for unclean process
terminations (OOM-kill, node crash, `kill -9`). Celery workers handle
`SIGTERM` via the Celery runtime's own signal handling — when a worker
shuts down gracefully, active tasks are revoked and their `FetcherRun`
records are finalized by the `run()` method's exception handler.
```

**2b-v** — Replace line 817:

```markdown
| `manual` | Triggered by an admin (via API or CLI) |
```

With:

```markdown
| `manual` | Triggered by an admin (via API) |
```

**2b-vi** — Replace lines 848-849:

```markdown
   2. **Stale run detection threshold**: when > 0, used by the Celery task,
      API trigger endpoint, and CLI to determine whether a `running`
```

With:

```markdown
   2. **Stale run detection threshold**: when > 0, used by the Celery task
      and API trigger endpoint to determine whether a `running`
```

**2b-vii** — Replace line 987:

```markdown
  database and is accessible through the API, CLI, and dashboard UI
```

With:

```markdown
  database and is accessible through the API and dashboard UI
```

### Change 2c: Update CLI Commands intro in `fetcher-operations.md`

**File**: `docs/features/platform/fetcher-operations.md`

**2c-i** — Replace lines 8-9:

```markdown
control (manual trigger, enable/disable, configuration) and CLI access
for bootstrap and troubleshooting.
```

With:

```markdown
control (manual trigger, enable/disable, configuration) and CLI
commands for diagnostics and troubleshooting.
```

**2c-ii** — Replace lines 831-836:

```markdown
The `sentinel fetcher` command group provides operational access to the
fetcher infrastructure from the command line. It is designed for
bootstrap, troubleshooting, and environments where the API/UI is not
yet available. It is NOT a replacement for the API — configuration
changes (schedule, timeout, rate limit, enable/disable) are done
exclusively through the API.
```

With:

```markdown
The `sentinel fetcher` command group provides read-only diagnostic
access to the fetcher infrastructure from the command line. It is
designed for troubleshooting and quick status checks. All mutations
(trigger, enable/disable, configuration changes) are done exclusively
through the API.
```

### Change 3: Update bootstrap sequence in `ad-integration.md`

**File**: `docs/features/identity/ad-integration.md` (lines 551-575)

Replace:

```markdown
## CLI Usage

The LDAP sync can be triggered from the command line using the generic
fetcher command:

```
sentinel fetcher run sync_ldap_directory
```

This runs the sync synchronously in the CLI process (no Celery
required). See `docs/features/platform/fetcher-operations.md` (section "CLI
Commands") for full details on the `sentinel fetcher` command group.

### Post-deployment bootstrap sequence

```
1. sentinel fetcher run sync_ldap_directory                        # populate User table (~3,200 records)
2. sentinel manage-user update --username admin1 --add-role admin  # assign Admin role to first admin
```

The `manage-user` command is documented in
`docs/features/identity/user-management.md`. In this bootstrap context, the
user already exists (created by the LDAP sync in step 1), and
`manage-user update` adds the Admin role with `ad_group_cn = '_manual'`
and `assigned_by = NULL` (CLI action).
```

With:

```markdown
## Triggering the LDAP Sync

The LDAP sync is triggered via the API:

```
POST /api/v1/fetchers/sync_ldap_directory/trigger
```

This enqueues the sync as a Celery task. The response includes a
`run_id` that can be polled via
`GET /api/v1/fetchers/sync_ldap_directory/runs/{run_id}` until
completion. See `docs/features/platform/fetcher-operations.md` (section
"Trigger Fetcher") for full details.

### Post-deployment bootstrap sequence

**Prerequisites**: all services must be running (API server, Celery
worker, Redis, PostgreSQL). Verify the Celery worker has started
successfully (check logs for fetcher registry population) before
proceeding to step 2.

```
1. sentinel manage-user create --username bootstrap-admin \
     --email bootstrap@localhost --role admin              # create local admin
2. Authenticate to API as bootstrap-admin                  # see local-authentication.md
3. POST /api/v1/fetchers/sync_ldap_directory/trigger       # trigger LDAP sync
4. Poll GET .../runs/{run_id} until status != running      # wait for completion
5. sentinel manage-user update --username <ad-user> --add-role admin  # promote real admin
6. sentinel manage-user deactivate --username bootstrap-admin         # recommended: deactivate bootstrap user
```

Step 1 creates a local admin account for initial API access. Step 2
authenticates using local login (see
`docs/features/identity/local-authentication.md`) to obtain a JWT
token for subsequent API calls. Step 3 triggers the LDAP sync which
populates the User table from Active Directory (~3,200 records).
Step 4 polls the run status until completion. Step 5 promotes an AD
user to the Admin role (with `ad_group_cn = '_manual'` and
`assigned_by = NULL`). Step 6 deactivates the bootstrap account to
prevent username collision with AD users (recommended).

**Failure recovery**:

- If step 3 returns 404 (`FETCHER_NOT_FOUND`): the Celery worker has
  not finished registering fetchers. Wait and retry.
- If step 3 returns 503 (`CELERY_ENQUEUE_FAILED`): the Celery worker
  is unreachable. Fix the worker configuration and retry.
- If step 4 shows `status = failure`: check `error_detail` in the run
  response (requires `manage_fetchers` capability, which the
  bootstrap-admin has), fix the underlying issue (e.g., AD
  unreachable, `LDAP_URI` misconfigured), and re-trigger.

The `manage-user` commands are documented in
`docs/features/identity/user-management.md`.
```

### Change 4: Update bootstrap reference in `rbac.md`

**File**: `docs/features/identity/rbac.md` (lines 589-592)

Replace:

```markdown
9. Admin bootstrap: run `sentinel fetcher run sync_ldap_directory` to
   populate users from AD, then
   `sentinel manage-user update --username <username> --add-role admin` to
   assign the first Admin role. See `docs/features/identity/ad-integration.md`.
   For bot accounts, see Business Rule 14
```

With:

```markdown
9. Admin bootstrap: create a local admin via
   `sentinel manage-user create --username bootstrap-admin --email bootstrap@localhost --role admin`,
   then use the API to trigger the LDAP sync
   (`POST /api/v1/fetchers/sync_ldap_directory/trigger`), then promote
   an AD user via
   `sentinel manage-user update --username <username> --add-role admin`.
   See `docs/features/identity/ad-integration.md`.
   For bot accounts, see Business Rule 14
```

### Change 5: Update `cli-reference.md`

**File**: `docs/cli-reference.md` (line 17)

Remove:

```markdown
| `sentinel fetcher run <name>`      | Execute a fetcher synchronously          | No (by design) | [fetcher-operations](features/platform/fetcher-operations.md) |
```

### Change 6: Update index file descriptions

After removing `sentinel fetcher run`, the CLI scope is reduced to
read-only diagnostics (`list`, `config`). Update the spec description in
index files to avoid implying operational/execution CLI capability.

**6-i** — `docs/system-map.md` (line 715)

Replace:

```markdown
| [fetcher-operations](features/platform/fetcher-operations.md) | Platform | Background task monitoring, API, and CLI |
```

With:

```markdown
| [fetcher-operations](features/platform/fetcher-operations.md) | Platform | Background task monitoring, API, and CLI diagnostics |
```

**6-ii** — `docs/features/README.md` (line 51)

Replace:

```markdown
- [fetcher-operations.md](platform/fetcher-operations.md) — Monitoring, API, and CLI for fetchers
```

With:

```markdown
- [fetcher-operations.md](platform/fetcher-operations.md) — Monitoring, API, and CLI diagnostics for fetchers
```

**6-iii** — `docs/features/platform/README.md` (line 9)

Replace:

```markdown
fetcher-operations.md           Monitoring, API, and CLI for fetchers
```

With:

```markdown
fetcher-operations.md           Monitoring, API, and CLI diagnostics for fetchers
```

**6-iv** — `docs/features/platform/fetcher-infrastructure.md` (lines 12-13)

Replace:

```markdown
For the monitoring dashboard (API endpoints, frontend pages, CLI
commands) that consumes this infrastructure, see
```

With:

```markdown
For the monitoring dashboard (API endpoints, frontend pages, CLI
diagnostics) that consumes this infrastructure, see
```

### Change 7: Update CLI convention examples in `conventions.md`

**File**: `docs/conventions.md`

**7a** — Replace lines 446-448:

```markdown
- Commands with internal phases that are not user-visible mutations
  (e.g., `fetcher run` has concurrency check → execute → record, but
  the user cares about the fetcher result, not the internal phases)
```

With:

```markdown
- Commands with internal phases that are not user-visible mutations
  (the user cares about the final result, not the internal phases)
```

**7b** — Replace lines 461-462:

```markdown
- Commands that execute external work (e.g., `fetcher run`) are exempt —
  they produce side effects by design
```

With:

```markdown
- Commands that execute external work are exempt — they produce side
  effects by design
```

**7c** — Replace line 498:

```markdown
  (e.g., `sentinel manage-user create`, `sentinel fetcher run`)
```

With:

```markdown
  (e.g., `sentinel manage-user create`, `sentinel fetcher list`)
```

### Change 8: Formalize `run()` signature in `fetcher-infrastructure.md`

**Origin**: old draft, Change 10b (adapted — no `FetcherRunResult`,
no CLI considerations).

**File**: `docs/features/platform/fetcher-infrastructure.md`

Add after line 33 ("that wraps the fetcher's `execute()` method with:"):

```markdown
   Signature:

   ```python
   async def run(
       self,
       *,
       triggered_by: str = "schedule",
       triggered_by_user_id: UUID | None = None,
       run_id: UUID | None = None,
   ) -> None:
   ```

   `run()` manages its own database sessions internally — callers do
   not pass a session. Each database operation (record creation,
   finalization) uses a short-lived session. The connection is not held
   open during `execute()`.
```

### Change 9: Document conditional lifecycle behavior

**Origin**: old draft, Change 10c.

**File**: `docs/features/platform/fetcher-infrastructure.md`

Replace line 34:

```markdown
   - Creation of a `FetcherRun` record with status `running`
```

With:

```markdown
   - **FetcherRun record acquisition**:
     - When `run_id` is `None` (scheduled runs): creates a new
       `FetcherRun` record with `status = running`, `triggered_by` and
       `triggered_by_user_id` set from the corresponding parameters
     - When `run_id` is provided (API trigger): retrieves the existing
       `FetcherRun` record (created synchronously by the API trigger
       endpoint). The record already has `status = running` and its
       `triggered_by`/`triggered_by_user_id` fields already set.
       `run()` continues its lifecycle without creating a new record
```

### Change 10: Add `run_id` to `run_fetcher` Celery task signature

**Origin**: old draft, Change 10d.

**File**: `docs/features/platform/fetcher-infrastructure.md` (lines
653-659)

Replace:

```python
@celery_app.task(bind=True)
def run_fetcher(self, fetcher_name: str, triggered_by: str = "schedule",
                user_id: str | None = None) -> None:
    """Run a fetcher by name."""
    ...
```

With:

```python
@celery_app.task(bind=True)
def run_fetcher(self, fetcher_name: str, triggered_by: str = "schedule",
                user_id: str | None = None,
                run_id: str | None = None) -> None:
    """Run a fetcher by name.

    Args:
        fetcher_name: registry key identifying the fetcher
        triggered_by: "schedule" (Beat) or "manual" (API)
        user_id: UUID of the user who triggered (None for scheduled
                 runs). Passed to run() as triggered_by_user_id after
                 conversion to UUID.
        run_id: UUID of a pre-created FetcherRun record (API trigger
                flow). When provided, run() updates this record instead
                of creating a new one. When None, run() creates a new
                record. Passed to run() after conversion to UUID.
    """
    ...
```

**File**: `docs/features/platform/fetcher-operations.md` (line 762)

Replace:

```
| Parameters | `fetcher_name` (str), `triggered_by` (str), `user_id` (str, optional) |
```

With:

```
| Parameters | `fetcher_name` (str), `triggered_by` (str), `user_id` (str, optional), `run_id` (str, optional) |
```

### Change 11: Explicit `run_id` transport in API trigger section

**Origin**: old draft, Change 10e.

**File**: `docs/features/platform/fetcher-operations.md` (lines 453-457)

Replace:

```markdown
- Creates a `FetcherRun` record **synchronously** (before enqueuing the
  Celery task) with `status = running` and `triggered_by = manual`. This
  ensures the `run_id` is available in the API response. The
  `BaseFetcher.run()` method detects the existing `FetcherRun` record
  (matched by `run_id`) and updates it rather than creating a new one
```

With:

```markdown
- Creates a `FetcherRun` record **synchronously** (before enqueuing the
  Celery task) with `status = running` and `triggered_by = manual`. This
  ensures the `run_id` is available in the API response
- Passes `run_id` to the Celery task via `run_fetcher.apply_async(kwargs=
  {"fetcher_name": name, "triggered_by": "manual", "user_id": str(user.id),
  "run_id": str(run.id)})`. The task forwards it to
  `fetcher.run(run_id=run_id, ...)`, which updates the existing record
  instead of creating a new one
```

### Change 12: Verify stale run recovery documentation (no action needed)

The current stale run recovery text (within the `sentinel fetcher run`
section, lines 1019-1024) references the CLI's interactive stale
resolution. After removing the CLI section (Change 1), that text is
deleted. Change 2b cleans up the Stale Run Detection section in
`fetcher-infrastructure.md` to remove all CLI-specific references.

The remaining documentation is adequate:

- `fetcher-infrastructure.md` "Stale Run Detection" section documents
  automatic resolution by the Celery task and API trigger endpoint
- `fetcher-operations.md` "Trigger Fetcher" endpoint (lines 446, 480-497)
  documents how the API handles stale runs on trigger
- `fetcher-operations.md` "sentinel fetcher list" shows the `(stale?)`
  indicator for diagnostic purposes

No additional changes needed beyond Changes 1 and 2b.

### Change 13: Resolve findings FEO-GAP-06, FEO-SEC-04, and FEI-SEC-004

Both findings refer to the `sentinel fetcher run` CLI command. With the
command removed from the specification, both findings become moot
(the code they reference will never be implemented).

Additionally, the FEI-SEC-004 resolution text in
`docs/reviews/fetcher-infrastructure.md` references "API/CLI response"
which is no longer accurate after Change 2b-ii removes CLI references
from the spec.

**File**: `docs/reviews/fetcher-operations.md`

Mark FEO-GAP-06 as RESOLVED:

```markdown
### FEO-GAP-06 — CLI fetcher run: unhandled exception exit code ambiguity (Low)

**Status**: RESOLVED — Moot: CLI `sentinel fetcher run` command removed from specification (2026-05-29)
```

Mark FEO-SEC-04 as RESOLVED:

```markdown
### FEO-SEC-04 — CLI bypasses enabled check without audit event (Low)

**Status**: RESOLVED — Moot: CLI `sentinel fetcher run` command removed from specification (2026-05-29)
```

**File**: `docs/reviews/fetcher-infrastructure.md`

Update FEI-SEC-004 resolution text:

Replace:

```markdown
**Status**: RESOLVED — Spec updated: added warning in API/CLI response when timeout_seconds=0 (2026-05-28)
```

With:

```markdown
**Status**: RESOLVED — Spec updated: added warning in API response when timeout_seconds=0 (2026-05-28)
```

**File**: `docs/reviews/.tracking.json`

Update cache: FEO GAP open L: 7→6, FEO SEC open L: 2→1, FEO
resolved: 13→15.

**File**: `docs/reviews/README.md`

Update fetcher-operations row counts accordingly.

### Change 14: Delete the old draft

**File**: `docs/drafts/cli-fetcher-run-lifecycle-delegation.md`

Delete this file. Its useful content (Changes 10b-10e) has been
migrated to this draft as Changes 8-11. The remaining changes (1-9,
11) were CLI-specific and are superseded by the removal of the command.

### Change 15: File new finding FEI-GAP-018 for FetcherRun retrieval failure

Change 9 introduces a new code path in `BaseFetcher.run()`: when
`run_id` is provided (API trigger flow), the method retrieves an
existing `FetcherRun` record instead of creating one. However, the
"FetcherRun creation failure" section in `fetcher-infrastructure.md`
(lines 70-82) documents only the creation failure case. The retrieval
failure modes (record not found, DB unreachable during retrieval) are
undocumented.

This is a completeness gap — not a contradiction — that should be
tracked as a new finding and addressed when implementing the feature.

**File**: `docs/reviews/fetcher-infrastructure.md`

Add after the last finding:

```markdown
### FEI-GAP-018 — FetcherRun retrieval failure undocumented for API-trigger flow (Low)

**Category**: Error handling
**Status**: OPEN

When `run_id` is provided to `BaseFetcher.run()` (API trigger flow),
the method retrieves an existing `FetcherRun` record. The spec
documents only FetcherRun creation failure (lines 70-82). Retrieval
failure modes are undocumented:
- DB unreachable during retrieval (same handling as creation failure?)
- Record not found (deleted between API trigger and task execution)
```

**File**: `docs/reviews/.tracking.json`

Update FEI cache: GAP open L: 0→1.

**File**: `docs/reviews/README.md`

Update fetcher-infrastructure row: GAP `🟢` → `1`, add severity
sub-row `1:🟡`, total 1→2.

---

## What Is NOT Changed

- `sentinel fetcher list` — read-only, remains unchanged
- `sentinel fetcher config <name>` — read-only, remains unchanged
- `BaseFetcher.run()` lifecycle — unchanged (enabled check stays in
  `run()` — without the CLI bypass use case, there is no reason to
  relocate it)
- `FetcherAuditLog.log_event()` `user_id` validation — unchanged (all
  `triggered` events now come exclusively from the API, which always has
  a user context)
- Signal handling in `run()` — unchanged (no `KeyboardInterrupt`/
  `SystemExit` handling needed — `run()` only runs inside Celery workers
  which have their own signal handling via the Celery runtime)

## Use Cases Resolved Without CLI

| Former use case | Resolution |
|---|---|
| Bootstrap (no users, no auth) | Create local admin via `manage-user create`, then API trigger |
| Celery down | Fix Celery — this is an infrastructure issue, not a fetcher issue |
| Run disabled fetcher | Enable via API → trigger → re-disable (or fix whatever caused the disable) |
| Stale run resolution | API trigger resolves stale runs automatically |
| Synchronous output | API trigger + polling (`GET .../runs/{run_id}`) |

## Verification Checklist

After all changes are applied, verify:

- [ ] `sentinel fetcher run` section removed from `fetcher-operations.md`
      (only `list` and `config` remain in CLI Commands)
- [ ] `fetcher-operations.md` Purpose section updated (no "bootstrap"
      reference for CLI)
- [ ] `fetcher-operations.md` CLI Commands intro describes read-only
      diagnostic access (not bootstrap or execution)
- [ ] `fetcher-infrastructure.md` deregistered fetcher section no longer
      mentions CLI `fetcher run`
- [ ] `fetcher-infrastructure.md` Stale Run Detection section no longer
      mentions CLI resolution (no "or the CLI", no CLI error message
      variant, no reference to CLI signal handling section)
- [ ] `fetcher-infrastructure.md` FetcherRunTriggeredBy enum `manual`
      description says "via API" (not "via API or CLI")
- [ ] `fetcher-infrastructure.md` `timeout_seconds` description no
      longer mentions CLI for stale detection
- [ ] `fetcher-infrastructure.md` deregistered fetcher observable effects
      — historical data accessible through "API and dashboard UI"
      (not "API, CLI, and dashboard UI")
- [ ] `fetcher-infrastructure.md` Purpose cross-reference says "CLI
      diagnostics" (not "CLI commands")
- [ ] `ad-integration.md` bootstrap sequence uses API trigger, not CLI
- [ ] `ad-integration.md` bootstrap includes failure recovery guidance
- [ ] `ad-integration.md` bootstrap references local-authentication.md
      for API auth
- [ ] `rbac.md` Business Rule 9 references the new bootstrap flow
- [ ] `cli-reference.md` no longer lists `sentinel fetcher run`
- [ ] `conventions.md` examples no longer reference `fetcher run`
- [ ] `system-map.md` fetcher-operations description says "CLI
      diagnostics" (not just "CLI")
- [ ] `docs/features/README.md` and `docs/features/platform/README.md`
      descriptions updated to "CLI diagnostics"
- [ ] `run()` has a formal signature with `triggered_by`,
      `triggered_by_user_id`, and `run_id` parameters (keyword-only)
- [ ] `run()` lifecycle documents conditional behavior: `run_id`
      provided → update existing record; `run_id` absent → create new
- [ ] `run_fetcher` Celery task signature includes `run_id` parameter
      (both `fetcher-infrastructure.md` and `fetcher-operations.md`)
- [ ] `run_fetcher` task docstring documents type conversion
      responsibility (`str` → `UUID`)
- [ ] API trigger section explicitly documents `run_id` transport
      (handler → apply_async → task → run())
- [ ] FEO-GAP-06 marked as RESOLVED (moot)
- [ ] FEO-SEC-04 marked as RESOLVED (moot)
- [ ] FEI-SEC-004 resolution text updated ("API response" not "API/CLI
      response")
- [ ] FEI-GAP-018 filed for FetcherRun retrieval failure gap
- [ ] `docs/reviews/README.md` and `.tracking.json` updated for all
      finding changes
- [ ] Old draft file deleted
- [ ] No remaining references to `sentinel fetcher run` in `docs/`
      (except this draft and the old draft being deleted)
- [ ] `deployment.md` bootstrap section is consistent with the new
      flow (already documents `manage-user create --role admin`)

## Impact on Other Findings

- **FEO-GAP-06**: moot (CLI command removed)
- **FEO-SEC-04**: moot (CLI command removed)
- **FEI-SEC-004**: resolution text updated (historical correction)
- **FEI-GAP-018**: new finding filed (FetcherRun retrieval failure gap)
- All other fetcher-operations findings: not impacted (they concern
  the API/dashboard, not the CLI)

## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher
  contract, `run_fetcher` task, Stale Run Detection, data model enums
- `docs/features/platform/fetcher-operations.md` — CLI section (to
  remove), CLI Commands intro (to update), API trigger section (to update)
- `docs/features/identity/ad-integration.md` — bootstrap sequence
- `docs/features/identity/rbac.md` — Business Rule 9
- `docs/features/identity/local-authentication.md` — bootstrap auth
- `docs/cli-reference.md` — command inventory
- `docs/conventions.md` — CLI convention examples
- `docs/deployment.md` — local dev bootstrap
- `docs/system-map.md` — spec description index
- `docs/features/README.md` — feature spec index
- `docs/features/platform/README.md` — platform spec index
- `docs/reviews/fetcher-operations.md` — FEO-GAP-06, FEO-SEC-04
- `docs/reviews/fetcher-infrastructure.md` — FEI-SEC-004, FEI-GAP-018
- `docs/drafts/cli-fetcher-run-lifecycle-delegation.md` — superseded

## Post-Application Steps

After all changes have been applied to the specification files:

1. **Run reviewers** to verify the changes are consistent and complete:
   - `@spec-coherence-reviewer` on `fetcher-operations.md` — verify
     CLI section removal leaves no dangling references
   - `@spec-coherence-reviewer` on `fetcher-infrastructure.md` — verify
     the `run()` signature and `run_id` transport are consistent
   - `@spec-coherence-reviewer` on `ad-integration.md` — verify the
     new bootstrap sequence is consistent with the rest of the spec
   - `@docs-reviewer` — verify documentation completeness
2. **Delete** `docs/drafts/cli-fetcher-run-lifecycle-delegation.md`
3. **Delete** this draft file
