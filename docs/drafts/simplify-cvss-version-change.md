# Simplify CVSS Default Version Change Side Effects

## Status

**Draft** — pending review and application.

## Scope

This document is a **specification change plan**. The Sentinel project
is in the specification phase — no implementation code exists yet.
This draft describes modifications to existing feature specifications.
No code is to be written, tested, or deployed as part of this work.

The deliverable is a set of updated `.md` specification files that are
internally coherent and pass the project's automated reviewers.

## Summary

Replace the complex batch recalculation machinery triggered by a
`default_cvss_version` change (Redis distributed lock with heartbeat,
exponential backoff, stale-detection, read-after-lock pattern) with a
**simple one-shot batch task** that iterates active tickets and calls
the existing `recalculate_cvss_chain()` function per ticket in
independent transactions.

Additionally, introduce a **dedicated re-run endpoint** for manual
recovery and add a **lightweight Redis cooldown slot** (TTL-based) that
prevents flip-flop and doubles as a Redis liveness check.

The `SystemSetting` table and the `default_cvss_version` DB setting are
**retained** — the simplification targets only the side-effect machinery,
not the setting infrastructure.

## Motivation

The current specification describes an elaborate batch recalculation
system designed for an operation that will realistically occur **once in
the lifetime of the platform** (the industry transition from CVSS v3.1
to v4.0). The machinery includes:

- Singleton Redis distributed lock (`sentinel:batch_cvss_recalc`)
- Heartbeat updates every 60 seconds within the processing loop
- Exponential backoff with jitter (up to 10 retries) on lock contention
- Stale-lock detection (3-minute threshold) with differentiated logging
- Read-after-lock pattern to handle rapid version changes
- 1-hour TTL as crash safety net

This complexity is disproportionate to the operational reality. The
per-ticket `FOR UPDATE` lock + idempotency of `recalculate_cvss_chain()`
already serialize concurrent executions at the row level. The distributed
lock defends against a problem that PostgreSQL row-level locking and
function idempotency already solve.

### Why not pure-lazy (no batch at all)?

A pure-lazy approach (let future operations naturally adopt the new
version) was considered and rejected due to a **silent eligibility
correctness hole**:

- The Resolved gate requires `released_at IS NOT NULL` for all
  `eligible = true` products under FIXED tracks.
- If the version change makes a product newly eligible (score crosses
  threshold) but the ticket's `eligible` flag is never refreshed, the
  ticket resolves **without that product receiving the fix** — a
  permanent silent omission.
- The normal resolution path (release detection, `reconcile_ticket_status`)
  does NOT recalculate eligibility.
- Tickets not touched by any CVSS mutation or sync never converge (the
  `upsert_cvss_assessment` no-op short-circuit prevents refresh when
  vectors are unchanged).

The simple batch closes this hole at minimal cost by reusing existing
infrastructure.

## Design

### Core Principle

The batch is a **thin loop around `recalculate_cvss_chain()`** — a
function that must exist regardless (used by ticket reactivation, CVE
association, and per-mutation recalculation). No new business logic is
introduced.

### Shared Components

The trigger logic is factored into reusable helpers, defined once in
`system-settings.md` and used by both the PATCH endpoint and the re-run
endpoint:

| Component | Responsibility |
|---|---|
| `acquire_recalc_slot()` | `SET cvss_recalc_active <timestamp> NX EX 900`. Returns normally on success. Raises `RecalcSlotUnavailableError` (503) if Redis is unreachable. Raises `RecalcAlreadyInProgressError` (409) if key exists |
| `release_slot()` | `DEL cvss_recalc_active`. Called by the batch task on completion (success or failure) and by endpoint error paths after slot acquisition |
| `enqueue_recalc_batch(version)` | Enqueues the Celery task with the explicit version as argument. On failure: calls `release_slot()` and raises |
| Celery task `recalc_active_tickets(version)` | Iterates `active_tickets_with_cve()`, calls `recalculate_cvss_chain(t, default_cvss_version=version)` in independent transactions, logs failures per ticket, calls `release_slot()` on completion, logs metrics (total/success/failed). `time_limit=900` (hard timeout matching slot TTL — ensures the task cannot outlive its slot) |

### Slot Cooldown (flip-flop prevention)

The Redis key `cvss_recalc_active` serves three purposes simultaneously:

1. **Redis liveness probe** — if the SET fails due to unreachability,
   the PATCH returns 503 and nothing is committed (requirement 2b).
2. **Flip-flop guard** — if the key exists, a recalculation is in
   progress; the PATCH returns 409 preventing rapid version toggling.
3. **Crash recovery** — the fixed TTL (900 seconds) ensures the key
   self-heals if the worker crashes without calling `release_slot()`.

The effective block duration is `min(batch_duration, 900s)`. Since the
batch typically completes in seconds/minutes, the admin is unblocked as
soon as it finishes. The 900-second TTL is only a crash safety net
(internal constant, not configurable).

### PATCH Endpoint Flow (commit-first)

```
PATCH /api/v1/admin/settings {default_cvss_version: V}

1. Validate V                                → 422 if invalid
2. No-op check: if current value == V        → 200 {recalculation_scheduled: false}
3. acquire_recalc_slot()                     → 503 if Redis down | 409 if in progress
4. Commit: setting=V + SettingAuditEvent     → if fails: release_slot(); 500
5. enqueue_recalc_batch(V)                   → if fails: release_slot(); 200 + warning
6. Return 200 OK
```

**Commit-first rationale**: the `SettingAuditEvent` is always the first
durable record. No ticket mutation can occur without the setting change
being audited. This prevents "phantom mutations" (ticket audit events
without a recorded cause).

**Enqueue-failure after commit (step 5 failure)**: the setting is already
committed and audited. Returning 503 would be misleading since the
primary operation succeeded. Instead, return 200 with
`recalculation_scheduled: false` and a message directing the admin to use
the manual re-run endpoint.

### Re-run Endpoint Flow

```
POST /api/v1/admin/settings/default-cvss-version/recalculate

1. Read current V from DB
2. acquire_recalc_slot()                     → 503 if Redis down | 409 if in progress
3. enqueue_recalc_batch(V)                   → if fails: release_slot(); 503
4. Return 202 Accepted
```

No setting change, no audit event. Pure operational recovery/refresh.

### Batch Task Behavior

```python
def recalc_active_tickets(version: str):
    tickets = query_active_tickets_with_cve()  # New, Analysis, Analyzed + cve_id IS NOT NULL
    success = 0
    failed = 0
    for ticket_id in tickets:
        try:
            with new_transaction() as db:
                recalculate_cvss_chain(db, ticket_id, default_cvss_version=version)
                db.commit()
            success += 1
        except Exception as e:
            log.error(f"Recalc failed for ticket {ticket_id}: {e}")
            failed += 1
            continue
    release_slot()
    log.info(f"Batch complete: {success} succeeded, {failed} failed out of {success + failed}")
```

Properties:

- **Isolation**: each ticket in its own transaction; one failure does not
  affect others.
- **Idempotency**: `recalculate_cvss_chain()` is idempotent — re-running
  the batch (or running it concurrently with per-ticket mutations) is
  safe. Unchanged values produce no audit events.
- **Scope**: only active tickets with a CVE. CVE-less tickets derive
  severity from `severity_override` and eligibility from the 10.0
  fallback — both independent of the CVSS version.
- **Side effects**: may emit `severity_changed` and
  `product_eligibility_changed` audit events for tickets whose derived
  values actually change. May promote tickets (e.g., Analyzed → Resolved
  if a product becomes ineligible and resolution gates are now satisfied).
  Cannot de-resolve tickets (Resolved tickets are outside the active
  scope).
- **Duration**: seconds to minutes for typical active ticket counts
  (each call is a few queries + short transaction).
- **Hard timeout**: `time_limit=900` (Celery hard timeout). Matches the
  slot TTL — ensures the task is terminated before its slot can expire,
  preventing concurrent batches with conflicting version arguments.
- **No distributed lock**: per-ticket `FOR UPDATE` serializes concurrent
  mutations; idempotency makes concurrent batches a no-op.

### Residual Risk (accepted)

If Redis dies in the micro-window between `acquire_recalc_slot()` (step
3) and `enqueue_recalc_batch()` (step 5) — after the DB commit — the
setting is persisted and audited, but the batch never starts. This is:

- **Visible**: the admin sees `recalculation_scheduled: false` or can
  check the slot status.
- **Recoverable**: the re-run endpoint triggers a fresh batch.
- **Not a correctness violation**: no unrecorded mutations occur.

The only way to eliminate this residual entirely would be a transactional
outbox pattern, which is disproportionate for a once-in-a-lifetime
operation.

### What is removed

The following specification elements are **deleted**:

1. Redis distributed lock with key `sentinel:batch_cvss_recalc`
2. Heartbeat updates every 60 seconds (conditional SET)
3. Exponential backoff with jitter (up to 10 retries) on lock contention
4. Stale-lock detection (3-minute threshold with differentiated logging)
5. Read-after-lock pattern
6. 1-hour TTL safety net (replaced by simpler 900-second slot TTL)
7. Admin feedback: confirmation dialog showing active ticket count
   (unnecessary for a simple background operation)

### What is retained

1. `SystemSetting` table and `default_cvss_version` DB record
2. `SettingAuditEvent` and `SettingAuditLog`
3. `manage_settings` capability
4. `GET /api/v1/admin/settings` endpoint
5. `PATCH /api/v1/admin/settings` endpoint (simplified side-effect)
6. `GET /api/v1/admin/settings/audit-log` endpoint
7. Bootstrap mechanism (Alembic + FastAPI lifespan)
8. `get_default_cvss_version()` failure behavior invariant
9. `recalculate_cvss_chain()` function and all its callers (unchanged)
10. Per-ticket recalculation on CVSS mutations, CVE association, and
    ticket reactivation (unchanged — steady-state path untouched)

### What is added

1. `POST /api/v1/admin/settings/default-cvss-version/recalculate`
   endpoint (manual re-run)
2. Shared helper functions: `acquire_recalc_slot()`, `release_slot()`,
   `enqueue_recalc_batch(version)`
3. Celery task `recalc_active_tickets(version)` (simplified batch,
   `time_limit=900` matching slot TTL)
4. Redis key `cvss_recalc_active` (slot/cooldown, fixed 900-second TTL)

---

## Application Plan

This plan is fully prescriptive. The executor applies each step
mechanically without making autonomous design decisions. Each step
specifies exact text to remove and exact replacement text.

### Step 1 — `docs/features/platform/system-settings.md`

#### Step 1.1 — Replace "Impact of changing the default version"

**Location**: lines 30–51 (from `**Impact of changing the default
version**:` through `**Warning**: changing the default CVSS version is a
significant operation.`)

**Remove** the following text (inclusive):

```markdown
**Impact of changing the default version**:

When the Admin changes the default CVSS version, Sentinel MUST:

1. Recalculate severity for **all CVEs with active tickets** (status: New,
   Analysis, Analyzed — see `docs/data-model.md`) using
   `resolve_severity_score` (5-step severity cascade, multi-provider)
2. Re-evaluate product eligibility for all active tickets using
   `resolve_eligibility_score` (2-step SUSE-only cascade)
3. Apply the same recalculation chain as a CVSS score change (see
   `docs/features/tickets/cvss-scoring.md`, Recalculation Chain)
4. Create `TicketAuditEvent` records for every severity or eligibility change

This operation may take time for a large number of active tickets. It
is executed as a background task (Celery). The task calls
`ticket_mutations.recalculate_cvss_chain()` for each ticket in an
independent database transaction. When the default CVSS version changes,
the batch recalculation task uses a singleton Redis lock to serialize
concurrent executions. See `docs/features/tickets/cvss-scoring.md`
(Chain Execution Model) for details.

**Warning**: changing the default CVSS version is a significant operation.
```

**Replace with**:

```markdown
**Impact of changing the default version**:

When the Admin changes the default CVSS version, the PATCH endpoint
executes the following sequence:

1. **Validate** the new value against allowed values (`"3.1"`, `"4.0"`)
2. **No-op check**: if the current value equals the new value, return
   200 immediately with `recalculation_scheduled: false` (no audit
   event, no batch — consistent with the audit-trail-infrastructure
   cross-cutting rule for idempotent no-ops)
3. **Acquire recalculation slot**: `SET cvss_recalc_active <timestamp>
   NX EX 900` on Redis. This step serves as both a Redis liveness probe
   and a flip-flop guard:
   - If Redis is unreachable → return 503 `REDIS_UNAVAILABLE` (nothing
     committed)
   - If the key already exists (a recalculation is in progress) → return
     409 `RECALC_ALREADY_IN_PROGRESS` (nothing committed)
4. **Commit** the new setting value and a `SettingAuditEvent` record to
   the database. If the commit fails: release the slot (`DEL
   cvss_recalc_active`) and return 500
5. **Enqueue** the batch recalculation Celery task
   (`recalc_active_tickets`) with the new version as an explicit
   argument. If the enqueue fails: release the slot and return 200 with
   `recalculation_scheduled: false` (the primary operation — the setting
   change — succeeded; the admin can use the manual re-run endpoint to
   trigger the batch)
6. Return 200 OK with `recalculation_scheduled: true`

**Commit-first rationale**: the `SettingAuditEvent` is always the first
durable record. No ticket mutation can occur without the setting change
being audited. This prevents phantom mutations (ticket audit events
without a recorded cause).

The batch task (`recalc_active_tickets`) iterates all active tickets
with a CVE (status: New, Analysis, Analyzed; `cve_id IS NOT NULL`) and
calls `ticket_mutations.recalculate_cvss_chain()` for each ticket in an
independent database transaction. Failures on individual tickets are
logged and skipped. On completion (or failure), the task releases the
slot (`DEL cvss_recalc_active`) and logs metrics (total, succeeded,
failed). The task has a hard timeout (`time_limit=900`) matching the
slot TTL — this ensures the task is terminated before its slot can
expire, preventing concurrent batches with conflicting versions.

The slot key has a fixed TTL of 900 seconds (internal constant). In
normal operation the task completes in seconds/minutes and releases the
slot immediately. The TTL serves only as a crash-recovery safety net: if
the worker dies without releasing the slot, the key self-expires and the
admin can retry.

See `docs/features/tickets/cvss-scoring.md` (Chain Execution Model) for
additional details on the batch task behavior.
```

#### Step 1.2 — Update PATCH endpoint section

**Location**: the "Update System Settings" section (lines 124–159).

**Remove** lines 138–139 (the paragraph starting `Validates the value
against allowed values.`):

```markdown
Validates the value against allowed values. Triggers recalculation for all
active tickets as a background task.
```

**Replace with**:

```markdown
Validates the value against allowed values. On a value change, acquires
the recalculation slot, commits the setting and audit event, and
enqueues a batch recalculation task. See "Impact of changing the default
version" above for the full sequence.
```

**Remove** lines 141–147 (the "Note on PATCH with side effects"
paragraph):

```markdown
**Note on PATCH with side effects**: this endpoint uses PATCH because
semantically it is a configuration field update — the setting changes value
and the response is returned immediately. The recalculation chain is an
asynchronous side effect (Celery background task) that does not block the
response. The client experience is that of a simple field update with
instant confirmation. This is a documented deviation from the
`POST /resource/{id}/verb` convention for operations with side effects.
```

**Replace with**:

```markdown
**Note on PATCH with side effects**: this endpoint uses PATCH because
semantically it is a configuration field update — the setting changes
value and the response is returned immediately. The recalculation is an
asynchronous side effect (Celery background task) that does not block
the response. This is a documented deviation from the
`POST /resource/{id}/verb` convention for operations with side effects.
```

**Remove** lines 149–154 (error responses table):

```markdown
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 422 | `VALIDATION_ERROR` | Invalid setting value (e.g., unsupported CVSS version) |
```

**Replace with**:

```markdown
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (setting change blocked until current batch completes) |
| 422 | `VALIDATION_ERROR` | Invalid setting value (e.g., unsupported CVSS version) |
| 503 | `REDIS_UNAVAILABLE` | Redis broker is unreachable (setting change requires broker availability) |
```

**Remove** lines 156–157 (the response description):

```markdown
Response: the updated settings object in the standard `{"data": ...}`
envelope.
```

**Replace with**:

```markdown
Response (200 OK): the settings object in the standard
`{"data": ...}` envelope. The `recalculation_scheduled` boolean field
is **always present** in the response:

```json
{
  "data": {
    "default_cvss_version": "4.0",
    "recalculation_scheduled": true
  }
}
```

Values of `recalculation_scheduled`:

- `true` — value changed and batch task successfully enqueued
- `false` — either (a) no-op (value unchanged, no batch needed), or
  (b) value changed but enqueue failed (transient broker failure after
  slot acquisition — admin should use
  `POST /api/v1/admin/settings/default-cvss-version/recalculate` to
  trigger the batch manually)
```

#### Step 1.3 — Rename audit-log endpoint heading

**Location**: line 184 in `system-settings.md` (the `### API` heading
above the audit-log endpoint).

**Remove**:

```markdown
### API
```

**Replace with**:

```markdown
### List Settings Audit Events
```

This resolves ADM-API-01 (generic heading producing ambiguous anchor
`#api`). The new anchor is `#list-settings-audit-events`.

#### Step 1.4 — Add re-run endpoint section

**Location**: insert a new section **after** the "Update System
Settings" section (after `**`Capability: manage_settings`**` on line
159) and **before** `## Data Model` (line 161). This places the new
endpoint under `## API Endpoints`, alongside the existing GET and PATCH
endpoints.

**Insert**:

````markdown
### Trigger CVSS Recalculation

```
POST /api/v1/admin/settings/default-cvss-version/recalculate
```

Manually triggers a CVSS recalculation batch for all active tickets
with a CVE, using the current `default_cvss_version` value. Used for
recovery after partial batch failures or as a general refresh mechanism.

The endpoint uses the same shared logic as the PATCH side-effect:

1. Read the current `default_cvss_version` from the database
2. Acquire the recalculation slot (`SET cvss_recalc_active <timestamp>
   NX EX 900`)
3. Enqueue `recalc_active_tickets(version)`. On failure: release slot
   and return 503
4. Return 202 Accepted

No setting change is made. No `SettingAuditEvent` is created.

**Request body**: none.

**Response** (202 Accepted):

```json
{
  "data": {
    "message": "Recalculation batch enqueued",
    "default_cvss_version": "4.0",
    "scope": "active_tickets_with_cve"
  }
}
```

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have `manage_settings` capability |
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (slot occupied) |
| 503 | `REDIS_UNAVAILABLE` | Redis is unreachable (slot acquisition failed) |
| 503 | `CELERY_ENQUEUE_FAILED` | Task could not be enqueued (slot released) |

**Idempotency**: safe to call multiple times. If no derived values have
changed since the last run, the batch produces no mutations or audit
events (guaranteed by `recalculate_cvss_chain()` idempotency).

**`Capability: manage_settings`**
````

---

### Step 2 — `docs/features/tickets/cvss-scoring.md`

#### Step 2.1 — Replace batch recalculation subsection

**Location**: lines 719–771 (from `**Exception — batch recalculation on
default version change**:` through the end of the `**Idempotency**:`
paragraph, immediately before `## Ticket Reactivation: CVSS Catch-Up`).

**Remove** the following text (inclusive):

```markdown
**Exception — batch recalculation on default version change**: when the
Admin changes the default CVSS version (see `docs/features/platform/system-settings.md`),
the chain must run for all active tickets. This batch operation is
executed as an asynchronous Celery task to avoid blocking the API
response. The task:

1. Acquires a Redis distributed lock (key: `sentinel:batch_cvss_recalc`)
   before starting, storing the current timestamp as the lock's value. 
   Lock TTL is set to 1 hour as a safety net for worker crashes. 
   To maintain lock liveness without extending the TTL, the task updates the 
   lock key with a fresh heartbeat timestamp every 60 seconds within its 
   ticket-processing loop (using conditional `SET sentinel:batch_cvss_recalc <timestamp> XX`).
   If the lock is already held, the task retries with exponential backoff 
   and jitter, up to 10 attempts. If still locked after all retries, the task 
   reads the lock value and compares it to the current time:
   - If the timestamp is recent (less than 3 minutes ago), the task fails 
     and logs: `"Batch recalculation still in progress. Retry will occur at next version change."`
   - If the timestamp is stale (3 minutes or older), the task fails and 
     logs: `"Lock likely orphaned from worker crash. TTL will expire in approximately N minutes. No action required."`
2. After acquiring the lock, reads the current `default_cvss_version`
   from the database. This **read-after-lock** pattern ensures the task
   always uses the latest version, even if multiple version changes
   occurred while waiting for the lock.
3. Iterates over all active tickets (New, Analysis, Analyzed)
4. For each ticket, calls
   `ticket_mutations.recalculate_cvss_chain()` — a dedicated entry
   point that recalculates derived data without modifying any
   `CVECVSSAssessment` record (see
   `docs/features/tickets/ticket-mutations.md`). The task passes
   `default_cvss_version` explicitly to every
   `recalculate_cvss_chain()` call (overriding the internal read) to
   ensure all tickets in the batch use the same version.
5. Each ticket is processed in an **independent database transaction**
   (isolation: a failure on one ticket does not roll back others)
6. On error for a single ticket, the task logs the error with the
   ticket ID and continues with the remaining tickets
7. On completion, the task releases the lock and logs completion
   metrics (total tickets processed, successes, failures) to
   structured application logs (standard Python `logging` module)

**Admin feedback**: when the Admin changes the default CVSS version in
the UI, the frontend displays a confirmation dialog showing the count of
active tickets that will be recalculated (derived from data already
available in the frontend — no dedicated preview endpoint is required).
After confirmation, the batch task executes asynchronously. Completion
metrics are available in application logs only — no dedicated result
storage or audit trail enrichment is provided for the batch outcome.

**Idempotency**: `recalculate_cvss_chain()` is idempotent —
re-processing tickets already updated produces the same result. If
multiple default version changes occur in rapid succession, the lock
serializes the batch tasks and the read-after-lock pattern ensures
only the latest version is used.
```

**Replace with**:

```markdown
**Exception — batch recalculation on default version change**: when the
Admin changes the default CVSS version (see
`docs/features/platform/system-settings.md`), the chain must run for all
active tickets with a CVE. This batch operation is executed as an
asynchronous Celery task (`recalc_active_tickets`) to avoid blocking the
API response.

The PATCH endpoint acquires a **recalculation slot** (Redis key
`cvss_recalc_active`, `SET NX EX 900`) before committing the setting
change. This slot serves as a Redis liveness probe, a flip-flop guard
(409 if a batch is already running), and a crash-recovery safety net
(900-second TTL auto-expires if the worker crashes). See
`docs/features/platform/system-settings.md` (Impact of changing the
default version) for the full commit-first endpoint flow.

The task:

1. Iterates all active tickets with a CVE (status: New, Analysis,
   Analyzed; `cve_id IS NOT NULL`)
2. For each ticket, calls
   `ticket_mutations.recalculate_cvss_chain()` — a dedicated entry
   point that recalculates derived data without modifying any
   `CVECVSSAssessment` record (see
   `docs/features/tickets/ticket-mutations.md`). The task passes
   `default_cvss_version` explicitly (received as a task argument from
   the endpoint) to ensure all tickets in the batch use the same version
3. Each ticket is processed in an **independent database transaction**
   (isolation: a failure on one ticket does not roll back others)
4. On error for a single ticket, the task logs the error with the
   ticket ID and continues with the remaining tickets
5. On completion (or failure), calls `release_slot()` (`DEL
   cvss_recalc_active`) and logs completion metrics (total tickets
   processed, successes, failures) to structured application logs

The task has a hard timeout (`time_limit=900`) matching the slot TTL.
This ensures the task is terminated before its slot can expire,
preventing concurrent batches with conflicting version arguments.

A dedicated endpoint
(`POST /api/v1/admin/settings/default-cvss-version/recalculate`) allows
the admin to manually re-trigger the batch for recovery after partial
failures. It uses the same slot acquisition and enqueue logic. See
`docs/features/platform/system-settings.md` (Trigger CVSS
Recalculation).

**Idempotency**: `recalculate_cvss_chain()` is idempotent —
re-processing tickets already updated produces the same result.
Per-ticket `FOR UPDATE` locks serialize concurrent mutations on the
same ticket (e.g., batch running alongside a normal CVSS sync).
```

---

### Step 3 — `docs/features/tickets/ticket-mutations.md`

#### Step 3.1 — Update Callers table row

**Location**: line 992 in the Callers table.

**Remove** the row:

```markdown
| Admin: default CVSS version change | `recalculate_cvss_chain()` | Batch re-evaluation triggered by default CVSS version config change |
```

**Replace with**:

```markdown
| Admin: default CVSS version change | `recalculate_cvss_chain()` | Celery task `recalc_active_tickets(version)` iterates active tickets with CVE; passes version explicitly. See `docs/features/platform/system-settings.md` |
```

#### Step 3.2 — Remove stale "read-after-lock pattern" reference

**Location**: line 635 in the `recalculate_cvss_chain()` parameter table
(the `default_cvss_version` parameter description).

**Remove**:

```markdown
| `default_cvss_version` | `str \| None` | No | The CVSS version to use for severity resolution and eligibility evaluation. If `None` (default), the function reads the current version from `settings_service.get_default_cvss_version(db)`. The batch recalculation task provides this explicitly to ensure all tickets in a batch use the same version (read-after-lock pattern). Other callers should typically omit this parameter |
```

**Replace with**:

```markdown
| `default_cvss_version` | `str \| None` | No | The CVSS version to use for severity resolution and eligibility evaluation. If `None` (default), the function reads the current version from `settings_service.get_default_cvss_version(db)`. The batch recalculation task provides this explicitly (passed as a task argument from the triggering endpoint) to ensure all tickets in a batch use the same version. Other callers should typically omit this parameter |
```

---

### Step 4 — `docs/features/identity/rbac.md`

#### Step 4.1 — Add row to Endpoint Permission Map

**Location**: the "Administration" sub-table of the Endpoint Permission
Map (after line 474 — the `GET /api/v1/admin/settings/audit-log` row).

**Insert** the following row immediately after the audit-log row:

```markdown
| POST | `/api/v1/admin/settings/default-cvss-version/recalculate` | `manage_settings` | [system-settings](../platform/system-settings.md#trigger-cvss-recalculation) |
```

#### Step 4.2 — Fix audit-log endpoint anchor

**Location**: line 474 (the `GET /api/v1/admin/settings/audit-log` row).

**Remove**:

```markdown
| GET | `/api/v1/admin/settings/audit-log` | `manage_settings` | [system-settings](../platform/system-settings.md#api) |
```

**Replace with**:

```markdown
| GET | `/api/v1/admin/settings/audit-log` | `manage_settings` | [system-settings](../platform/system-settings.md#list-settings-audit-events) |
```

---

### Step 5 — `docs/api-spec.md`

#### Step 5.1 — Add error code to FETCHER prefix row

**Location**: the Error Code Categories table, line 153 (the
`FETCHER_*` row).

**Remove**:

```markdown
| `FETCHER_*` | Fetcher operations | `FETCHER_NOT_FOUND`, `FETCHER_ALREADY_RUNNING`, `FETCHER_DEREGISTERED`, `FETCHER_DISABLED`, `FETCHER_SETTING_UNKNOWN`, `FETCHER_SETTING_INVALID` |
```

**Replace with**:

```markdown
| `FETCHER_*` | Fetcher operations | `FETCHER_NOT_FOUND`, `FETCHER_ALREADY_RUNNING`, `FETCHER_DEREGISTERED`, `FETCHER_DISABLED`, `FETCHER_SETTING_UNKNOWN`, `FETCHER_SETTING_INVALID` |
| `RECALC_*` | Batch recalculation operations | `RECALC_ALREADY_IN_PROGRESS` |
```

#### Step 5.2 — Verify REDIS_UNAVAILABLE exists

**Location**: the "Infrastructure Dependency Errors (HTTP 503)" table
(line 181).

**Check**: `REDIS_UNAVAILABLE` is already listed. No change needed.

#### Step 5.3 — Verify CELERY_ENQUEUE_FAILED exists

**Location**: the same table (line 185).

**Check**: `CELERY_ENQUEUE_FAILED` is already listed. No change needed.

---

### Step 6 — `docs/reviews/system-settings.md`

#### Step 6.1 — Resolve ADM-GAP-01

**Location**: lines 11–16 (the ADM-GAP-01 finding).

**Remove**:

```markdown
### ADM-GAP-01 — Task enqueue failure behavior unspecified (High)

**Category**: Error and failure paths
**Status**: OPEN

The PATCH endpoint commits the setting change and `SettingAuditEvent` in one database transaction, then enqueues the batch recalculation Celery task. If the Redis broker is unavailable at enqueue time, the spec does not define whether: (a) the setting change is rolled back, (b) the setting persists and the API returns a 503 indicating partial success, or (c) the failure is silently swallowed. If the setting commits without the batch task, all active tickets retain severity and eligibility derived from the old CVSS version until something else triggers recalculation — an inconsistent state with no documented recovery path.
```

**Replace with**:

```markdown
### ADM-GAP-01 — Task enqueue failure behavior unspecified (High)

**Category**: Error and failure paths
**Status**: RESOLVED — The simplified design uses commit-first ordering with a recalculation slot (Redis SET NX) as a liveness probe before the commit. If Redis is unreachable, the PATCH returns 503 and nothing is committed. If the enqueue fails after commit (transient broker failure in the micro-window), the PATCH returns 200 with `recalculation_scheduled: false` and the admin uses the dedicated re-run endpoint (`POST /api/v1/admin/settings/default-cvss-version/recalculate`) to trigger the batch manually. (2026-07-03)
```

#### Step 6.2 — Resolve ADM-GAP-02

**Location**: lines 18–23 (the ADM-GAP-02 finding).

**Remove**:

```markdown
### ADM-GAP-02 — No-op behavior when setting value is unchanged (Medium)

**Category**: Idempotency
**Status**: OPEN

The spec states "Triggers recalculation for all active tickets as a background task" unconditionally. When an admin PATCHes `{"default_cvss_version": "3.1"}` and the current value is already `"3.1"`, the spec does not specify whether: (a) the batch task is triggered (expensive no-op), (b) a `SettingAuditEvent` is created with `old_value == new_value`, or (c) the operation short-circuits. The audit-trail-infrastructure spec's cross-cutting rule ("no audit event for idempotent no-ops") implies option (c), but system-settings.md contradicts this by stating the trigger unconditionally. One clarifying sentence would eliminate the ambiguity.
```

**Replace with**:

```markdown
### ADM-GAP-02 — No-op behavior when setting value is unchanged (Medium)

**Category**: Idempotency
**Status**: RESOLVED — The PATCH endpoint now includes an explicit no-op check (step 2): if the current value equals the new value, the endpoint returns 200 immediately with no audit event and no batch task. This is consistent with the audit-trail-infrastructure cross-cutting rule for idempotent no-ops. (2026-07-03)
```

#### Step 6.3 — Resolve ADM-GAP-03

**Location**: lines 25–30 (the ADM-GAP-03 finding).

**Remove**:

```markdown
### ADM-GAP-03 — PATCH success HTTP status code not explicitly stated (Low)

**Category**: Boundary conditions
**Status**: OPEN

The PATCH endpoint specifies error codes (403, 422) but does not explicitly state the success HTTP status code. Other specs in the project (e.g., ticket-mutations, package-service) consistently declare success codes. An implementer could choose 200 (standard PATCH-with-body), 202 (async side effects), or 204 (no body). The response description ("the updated settings object") implies 200, but stating it explicitly maintains consistency with other endpoint definitions in the project.
```

**Replace with**:

```markdown
### ADM-GAP-03 — PATCH success HTTP status code not explicitly stated (Low)

**Category**: Boundary conditions
**Status**: RESOLVED — The PATCH response now explicitly states "Response (200 OK)" with the full response schema including the `recalculation_scheduled` field. (2026-07-03)
```

#### Step 6.4 — Resolve ADM-COH-01

**Location**: lines 36–41 (the ADM-COH-01 finding).

**Remove**:

```markdown
### ADM-COH-01 — Configuration reference attribution for default_cvss_version (Low)

**Category**: Cross-reference consistency
**Status**: OPEN

In `docs/configuration.md`, the "Defined in" column for `default_cvss_version` points to `docs/features/tickets/cvss-scoring.md`. However, the authoritative definition of this runtime setting — its properties table, allowed values, bootstrap mechanism, CRUD API, and audit log — lives in `docs/features/platform/system-settings.md`. The `cvss-scoring.md` spec itself defers to system-settings.md in its cross-references. Since `configuration.md` states "Each setting is defined authoritatively in the feature specification linked in the 'Defined in' column", the link should point to `system-settings.md`.
```

**Replace with**:

```markdown
### ADM-COH-01 — Configuration reference attribution for default_cvss_version (Low)

**Category**: Cross-reference consistency
**Status**: RESOLVED — Updated the "Defined in" column for `default_cvss_version` in `docs/configuration.md` to point to `docs/features/platform/system-settings.md`. (2026-07-03)
```

#### Step 6.5 — Resolve ADM-API-01

**Location**: lines 59–64 (the ADM-API-01 finding).

**Remove**:

```markdown
### ADM-API-01 — Non-descriptive endpoint heading for audit-log endpoint (Low)

**Category**: Endpoint Permission Map completeness
**Status**: OPEN

The audit-log endpoint's definition heading is `### API` (producing anchor `#api`), which is excessively generic. The RBAC Endpoint Permission Map links to `[system-settings](../platform/system-settings.md#api)` — technically resolves but provides poor readability and could become ambiguous if more API sections are added. Other specs use descriptive headings like `### List Fetcher Runs` or `### Get Settings Audit Log`.
```

**Replace with**:

```markdown
### ADM-API-01 — Non-descriptive endpoint heading for audit-log endpoint (Low)

**Category**: Endpoint Permission Map completeness
**Status**: RESOLVED — Heading renamed from `### API` to `### List Settings Audit Events` (anchor: `#list-settings-audit-events`). RBAC Endpoint Permission Map link updated accordingly. (2026-07-03)
```

#### Step 6.6 — Update `.tracking.json`

**Location**: `docs/reviews/.tracking.json`, the `"system-settings"`
entry (line 826).

**Remove**:

```json
"open": {
  "GAP": {
    "H": 1,
    "M": 1,
    "L": 1
  },
  "COH": {
    "H": 0,
    "M": 0,
    "L": 1
  },
  "DES": {
    "H": 0,
    "M": 0,
    "L": 0
  },
  "SEC": {
    "H": 0,
    "M": 0,
    "L": 0
  },
  "API": {
    "H": 0,
    "M": 0,
    "L": 1
  }
},
"resolved": 3,
```

**Replace with**:

```json
"open": {
  "GAP": {
    "H": 0,
    "M": 0,
    "L": 0
  },
  "COH": {
    "H": 0,
    "M": 0,
    "L": 0
  },
  "DES": {
    "H": 0,
    "M": 0,
    "L": 0
  },
  "SEC": {
    "H": 0,
    "M": 0,
    "L": 0
  },
  "API": {
    "H": 0,
    "M": 0,
    "L": 0
  }
},
"resolved": 8,
```

#### Step 6.7 — Update `docs/reviews/README.md`

**Location**: line 64 (the `system-settings` row in the Summary Table)
and line 65 (the severity sub-row).

**Remove** lines 64–65:

```markdown
| [system-settings](system-settings.md) | 3 | 1 | 🟢 | 🟢 | 1 | 5/8 | 2026-07-02 | |
| | 1:🔴 1:🟠 1:🟡 | 1:🟡 |  |  | 1:🟡 |  |  |  |
```

**Replace with**:

```markdown
| [system-settings](system-settings.md) | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 0/8 | 2026-07-02 | |
|  |  |  |  |  |  |  |  |  |
```

**Location**: line 80 (the Total row) and line 81 (the Total severity
sub-row).

**Remove** lines 80–81:

```markdown
| **Total** | **5** | **1** | **🟢** | **🟢** | **1** | **7/845** |  |  |
| | 1:🔴 3:🟠 1:🟡 | 1:🟡 |  |  | 1:🟡 |  |  |  |
```

**Replace with**:

```markdown
| **Total** | **2** | **🟢** | **🟢** | **🟢** | **🟢** | **2/845** |  |  |
| | 2:🟠 |  |  |  |  |  |  |  |
```

Note: the total changes from 7 open to 2 open (5 resolved from
system-settings). The denominator (845) is unchanged — resolving
findings moves them from open to resolved but does not change the total
count. The 2 remaining open findings are in `package-service.md`
(2 GAP Medium). The severity sub-row loses the High (🔴), 2 of the
3 Medium (🟠), and all Low (🟡) indicators from system-settings — only
the 2 Medium from package-service remain.

---

### Step 7 — `docs/configuration.md`

#### Step 7.1 — Fix attribution link for default_cvss_version

**Location**: line 170.

**Remove**:

```markdown
| `default_cvss_version` | string | `"3.1"` | System-wide CVSS version for severity and eligibility. Allowed: `"3.1"`, `"4.0"` | `docs/features/tickets/cvss-scoring.md` |
```

**Replace with**:

```markdown
| `default_cvss_version` | string | `"3.1"` | System-wide CVSS version for severity and eligibility. Allowed: `"3.1"`, `"4.0"` | `docs/features/platform/system-settings.md` |
```

---

### Step 8 — Run reviewers

Run each reviewer in an independent session. Address any findings rated
"Needs revision" before proceeding to Step 9.

#### Step 8.1 — `@spec-coherence-reviewer`

Run on each modified spec independently:

- `docs/features/platform/system-settings.md`
- `docs/features/tickets/cvss-scoring.md`
- `docs/features/tickets/ticket-mutations.md`

#### Step 8.2 — `@spec-gap-analyzer`

Run on:

- `docs/features/platform/system-settings.md` (substantially modified)

#### Step 8.3 — `@api-convention-reviewer`

Run on:

- `docs/features/platform/system-settings.md` (new endpoint added,
  response schema changed, new error codes)

#### Step 8.4 — `@docs-reviewer`

Run on the set of modified specs to verify documentation completeness
and cross-reference integrity.

### Step 9 — Delete this draft

Remove `docs/drafts/simplify-cvss-version-change.md` from the
repository.

---

## Cross-references

- `docs/features/platform/system-settings.md` — primary spec being
  simplified
- `docs/features/tickets/cvss-scoring.md` — Chain Execution Model
- `docs/features/tickets/ticket-mutations.md` —
  `recalculate_cvss_chain()` contract, callers table
- `docs/features/identity/rbac.md` — Endpoint Permission Map
- `docs/api-spec.md` — error code registry
- `docs/configuration.md` — attribution link fix
- `docs/reviews/system-settings.md` — open findings being resolved
- `docs/reviews/.tracking.json` — review tracking counters
- `docs/reviews/README.md` — review summary table
