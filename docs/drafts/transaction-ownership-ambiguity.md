# Transaction Ownership Ambiguity in CVE Service

**Status**: Draft — architecture decided, reviewer findings integrated, spec modifications pending
**Created**: 2026-06-21
**Context**: project is in specification phase — no code implemented, no database

## 1. Problem Statement

The specification for `cve_service.upsert_cve()` does not explicitly document
whether the function commits the database transaction internally or leaves
commit responsibility to the caller. This ambiguity propagates to:

- API-based CVE fetchers' `execute()` loops (who commits per-CVE?)
- The `fetch_single_cve` orchestrator (who commits after success/failure?)
- Phase 2 task enqueue timing (how are post-commit tasks triggered?)

The ambiguity creates a risk: an implementer could reasonably choose either
architecture, leading to inconsistencies between fetchers or subtle bugs
(e.g., premature commit releasing a FOR UPDATE lock, or CVESource "success"
persisted before a rollback-worthy failure).

## 2. Evidence of Ambiguity

### 2.1 Conflicting statements across specs

| Source | Statement | Implies |
|--------|-----------|---------|
| `cve-service.md` (Phase 1) | "Same database transaction" containing upsert + children + `record_source_status("success")` + ticket creation | All writes are in one transaction (but who commits?) |
| `cve-service.md` (Phase 2) | "Enqueued after the Phase 1 transaction commits successfully" | Something triggers the commit before Phase 2 enqueue |
| `cve-sync-kev.md` (line 109) | "Transaction boundaries are managed internally by `upsert_cve()`" | `upsert_cve()` commits |
| `cve-sync-kev.md` (line 186) | "`upsert_references()` failure on entry: CVE data **already committed**; reference failure is non-critical" | Phase 1 is committed before `upsert_references()` runs |
| `ticket_mutations` | "The module does NOT commit or roll back. Commit responsibility belongs to the caller." | Service modules don't commit |
| `package_service` | Same "does NOT commit" declaration | Service modules don't commit |
| `user_service` | Same pattern (Transactionality section) | Service modules don't commit |
| `fetcher-infrastructure.md` (BaseGitFetcher) | "the session is committed or rolled back respectively before proceeding to the next item" | The infrastructure commits (not `upsert_cve()`) |
| `fetcher-infrastructure.md` (BaseFetcher) | "`run()` manages its own database sessions internally" | Infrastructure manages sessions |
| `cve-tracking.md` (fetch_single_cve) | Lists outcomes but no mention of commit/rollback | Transaction lifecycle undocumented |

### 2.2 Inconsistency: `record_source_status("failure")` in `execute()`

The Common CVE Fetcher Error Handling (`cve-tracking.md`) requires:

> "If the CVE record already exists in the database, call
> `record_source_status(session, cve_id, self.cve_source_type, "failure")`"

However, the pseudocode in `cve-sync-redhat.md` and `cve-sync-osv.md` shows
only `self.record_failed()` in the `except` block, without
`record_source_status("failure")`. Either:

- The pseudocode is simplified (omits the call for brevity)
- The fetchers intentionally skip it (undocumented deviation)
- The Common Error Handling rule is aspirational but not enforced

### 2.3 Session lifecycle for API-based fetchers is undocumented

`BaseGitFetcher` explicitly documents per-item commits. For API-based fetchers
(NVD, RedHat, GHSA, OSV, KEV, EPSS), there is no equivalent documentation.
The abstract `execute(session: AsyncSession)` signature passes a session but
does not specify:

- Whether the fetcher should commit per-CVE
- Whether `run()` commits on behalf of `execute()`
- What happens if the fetcher never commits

## 3. Possible Architectures

### Architecture A: `upsert_cve()` commits internally

```
execute() loop:
  └─ upsert_cve(session, payload)
       ├─ SELECT ... FOR UPDATE (lock)
       ├─ Write CVE + children + CVESource "success"
       ├─ COMMIT (lock released)
       └─ Enqueue Phase 2 tasks
  └─ upsert_references(session, ...)  ← separate transaction
```

**Pros**:
- Self-contained — caller doesn't need to know about commit
- Phase 2 enqueue is straightforward (post-commit, inline)
- KEV spec's statements are literally correct

**Cons**:
- Breaks composability (can't combine `upsert_cve()` with other operations
  in the same transaction)
- Inconsistent with `ticket_mutations`, `package_service`, `user_service`
- If `fetch_single()` does post-upsert work that fails, CVESource "success"
  is already committed (cannot roll back)
- FOR UPDATE lock is released immediately, reducing protection window

### Architecture B: Caller commits (with after_commit hooks for Phase 2)

```
execute() loop:
  └─ upsert_cve(session, payload)
       ├─ SELECT ... FOR UPDATE (lock)
       ├─ Write CVE + children + CVESource "success" (buffer)
       └─ Register after_commit hook → enqueue Phase 2 tasks
  └─ session.commit()  ← caller responsibility
       ├─ All writes persisted atomically
       ├─ Lock released
       └─ after_commit hook fires → Phase 2 tasks enqueued
  └─ upsert_references(session, ...)  ← same or next transaction
```

**Pros**:
- Consistent with all other service modules
- Composable — multiple operations can share a transaction
- `fetch_single()` failures after `upsert_cve()` are naturally rolled back
  (session not yet committed)
- FOR UPDATE lock held until caller commits (full protection)

**Cons**:
- Caller must remember to commit
- Phase 2 enqueue requires a mechanism (after_commit hooks or explicit call)
- KEV spec statements become inaccurate and need correction

### Architecture C: Infrastructure commits (template method in BaseFetcher)

```
run() / BaseAPIFetcher.execute():
  └─ for each item:
       └─ [fetcher].process_item(session)
            └─ upsert_cve(session, payload) — writes to buffer
       └─ session.commit()  ← infrastructure responsibility
       └─ Phase 2 hooks fire
```

**Pros**:
- Fetchers never think about commits — infrastructure handles it
- Consistent: both git-based and API-based share the same contract
- Already documented for BaseGitFetcher

**Cons**:
- Requires API-based fetchers to also use a template method (or `run()`
  commits per operation somehow)
- Less flexible if a fetcher needs custom transaction boundaries

## 4. Architecture Decision

### 4.1 Chosen architecture: Pure service + explicit `commit_and_dispatch`

`upsert_cve()` is a pure service function — identical in transaction
ownership to `ticket_mutations`, `package_service`, and `user_service`.
It writes to the session buffer and returns a result. It does NOT commit,
roll back, register hooks, or enqueue tasks.

Post-ingest task dispatch (Phase 2) is an explicit, separate step
performed by the caller after committing.

### 4.2 Why not the other architectures

**Architecture A (upsert_cve commits internally)**: rejected. Breaks
composability, inconsistent with all other service modules, releases
FOR UPDATE lock prematurely, and prevents rollback if `fetch_single()`
does post-upsert work that fails.

**Architecture B (after_commit hooks)**: rejected. While `upsert_cve()`
would not commit (consistent with other modules), registering implicit
hooks on the session creates subtle problems:
- If `process_item()` fails and the session is rolled back, hooks
  registered before the rollback survive on the session and fire on the
  NEXT successful commit — dispatching Phase 2 tasks for the wrong CVE
- Fixing this requires explicit `after_rollback` cleanup listeners,
  adding complexity without benefit over the explicit approach
- Implicit behavior is harder to test and debug

**Architecture C (full template method for API fetchers)**: rejected.
API-based fetchers have fundamentally different iteration patterns
(NVD: paginated REST with diff-window, RedHat: loop over active CVEs,
GHSA: GraphQL cursor pagination, KEV: catalog download). A template that
abstracts over this diversity must be very general — and a very general
template adds cognitive overhead without proportional enforcement value.
Additionally, `fetch_single_cve` (a standalone Celery task, not a
BaseFetcher) cannot use the template, creating two parallel patterns.

### 4.3 Key contracts

| Component | Responsibility |
|-----------|----------------|
| `upsert_cve(session, ...)` → `UpsertResult` | Phase 1: writes CVE + children + CVESource + ticket to session buffer. Does NOT commit, roll back, or enqueue. |
| `build_post_ingest_tasks(result, payload)` → `PostIngestTasks \| None` | Stateless helper: constructs Celery task args for post-ingest dispatch. Returns `None` if no ticket or no package data. |
| `BaseCVEFetcher.commit_and_dispatch(session, post_ingest)` | Helper method: commits the session, then dispatches exactly one `resolve_ticket_packages` task (if `post_ingest` is not `None`). Standard per-CVE finalization. If `post_ingest` is `None`, commits without dispatching. If `session.commit()` raises, the exception propagates to the caller — no dispatch is attempted. If `apply_async()` raises after a successful commit (e.g., Celery broker unreachable), the error is logged at WARNING level and the function returns normally — Phase 2 recovery relies on the next sync cycle (see `cve-service.md`, Crash Recovery). Re-invocation safe: duplicate dispatch produces idempotent Phase 2 execution. |
| `fetch_single(cve_id, session)` → `PostIngestTasks \| None` | Fetches from external source, calls `upsert_cve()` + `upsert_references()`, records metrics, returns pre-built post-ingest args. |
| `process_item(path, content, session)` → `PostIngestTasks \| None` | Same as `fetch_single` but for git-based fetchers. Parses file content, calls `upsert_cve()` + `upsert_references()`, records metrics, returns post-ingest args. |

### 4.4 Universal caller pattern

All callers — regardless of context — follow the same two-line pattern
after performing their per-CVE work:

```python
post_ingest = await <per_cve_operation>(session, ...)
await <fetcher>.commit_and_dispatch(session, post_ingest)
```

Where `<fetcher>` is `self` inside a BaseCVEFetcher subclass, or the
instantiated fetcher object in standalone task wrappers (e.g.,
`fetcher.commit_and_dispatch(...)` in `fetch_single_cve`).

This applies to:
- API-based fetcher `execute()` loops (`self.commit_and_dispatch`)
- BaseGitFetcher template per-item step (`self.commit_and_dispatch`)
- `fetch_single_cve` Celery task (`fetcher.commit_and_dispatch`)
- `catch_up()` default implementation (`self.commit_and_dispatch`)

**Non-success paths also use `commit_and_dispatch()`**: when
`fetch_single()` raises `CVENotInSource`, the orchestrator writes
`record_source_status("missing")` and then calls
`commit_and_dispatch(session, None)` — the helper commits the session
(persisting the "missing" status) and skips dispatch (since
`post_ingest` is `None`). The same applies to the "failure" path after
retry exhaustion. This avoids introducing a secondary bare
`session.commit()` pattern alongside the helper.

### 4.5 Naming convention

"Phase 1" and "Phase 2" remain as documentation concepts in the
Transaction Boundaries section of `cve-service.md` (they explain the
temporal design). In code interfaces and cross-spec references, use:

| Concept | Code-level term |
|---------|-----------------|
| Phase 2 task args | `PostIngestTasks` |
| Phase 2 arg builder | `build_post_ingest_tasks()` |
| Commit + dispatch helper | `commit_and_dispatch()` |
| Phase 2 Celery task | `resolve_ticket_packages` (existing name) |

### 4.6 `upsert_references()` transaction boundary

**Decision**: `upsert_references()` runs in the **same per-CVE
transaction** as `upsert_cve()`. Both write to the session buffer; the
caller commits via `commit_and_dispatch()` after both complete.

**Rationale**: `upsert_references()` is extremely unlikely to fail
independently of `upsert_cve()`. All its documented failure modes are
handled internally:

| Failure mode | Handling | Propagates exception? |
|---|---|---|
| URL > 2048 chars | Skip-and-continue (WARNING log) | No |
| Control characters in URL | Skip-and-continue (WARNING log) | No |
| Non-http/https scheme | Skip-and-continue (WARNING log) | No |
| IntegrityError (unique constraint) | Catch → re-query → merge | No |
| DB connectivity failure | Exception | Yes — but `upsert_cve()` would have already failed |

No nested savepoint or separate transaction is needed. The current
`ticket-references.md` "separate transaction" statement (lines 284-291)
must be corrected.

## 5. Affected Specifications

### 5.1 Must be updated (resolve ambiguity)

| File | Section | Change required |
|------|---------|-----------------|
| `cve-service.md` | New "Transaction ownership" section | Add: "does NOT commit or roll back. Commit responsibility belongs to the caller." |
| `cve-service.md` | Transaction Boundaries (Phase 2) | Reword: Phase 2 tasks are enqueued explicitly by the caller via `commit_and_dispatch()` after committing, not by `upsert_cve()` itself |
| `cve-service.md` | New `PostIngestTasks` type | Add dataclass definition with `ticket_id`, `cpe_matches`, `affected_cpes`, `vendor_products`, `resolved_packages` |
| `cve-service.md` | New `build_post_ingest_tasks()` function | Stateless helper: `(UpsertResult, CVEIngestPayload) → PostIngestTasks \| None` |
| `cve-service.md` | Callers table (lines 1212-1225) | Update batch fetcher rows: remove `record_source_status()` (failure path), add `build_post_ingest_tasks()` for Pattern A/D fetchers. Update `fetch_single_cve` row with `commit_and_dispatch()` |
| `fetcher-infrastructure.md` | `BaseCVEFetcher` Concrete Methods | Add `commit_and_dispatch(session, post_ingest)` helper method |
| `fetcher-infrastructure.md` | `fetch_single()` signature | Change return type from `-> None` to `-> PostIngestTasks \| None` |
| `fetcher-infrastructure.md` | `fetch_single` Signaling Convention | Update table with new return semantics |
| `fetcher-infrastructure.md` | New "Session Lifecycle for API-based CVE Fetchers" section | Document per-CVE commit pattern with `commit_and_dispatch()` |
| `fetcher-infrastructure.md` | `BaseGitFetcher` `execute()` step 10 | Update: `process_item()` returns `PostIngestTasks \| None`, template calls `commit_and_dispatch()` |
| `fetcher-infrastructure.md` | `process_item()` hook | Change return type from `-> None` to `-> PostIngestTasks \| None` |
| `fetcher-infrastructure.md` | `catch_up()` default | Add `commit_and_dispatch()` after `fetch_single()` |
| `fetcher-infrastructure.md` | `catch_up()` interface contract (lines 750-757) | Update "commits on return" phrasing: the default `catch_up()` now commits internally via `commit_and_dispatch()`, not via `run_catch_up` on return |
| `fetcher-infrastructure.md` | `BaseCVEFetcher` Non-Modification Statement (lines 1392-1403) | Add `commit_and_dispatch()` to the list of concrete methods provided by `BaseCVEFetcher` |
| `fetcher-infrastructure.md` | `BaseFetcher.run()` session description (lines 47-50) | Add clarification: "The session passed to `execute()` may be committed and rolled back multiple times (per-item transaction boundaries)" — pre-existing for git-based fetchers, now formalized for API-based |
| `fetcher-infrastructure.md` | `process_item()` hook documentation (lines 2137-2159) | Disambiguate `None` return: (a) item was skipped (already up-to-date, no work done), vs. (b) item was processed but no post-ingest tasks are needed (e.g., enrichment-only upsert with no ticket or no CPE data). Both cases result in `commit_and_dispatch(session, None)` — commit without dispatch |
| `cve-tracking.md` | `fetch_single_cve` orchestrator | Add explicit commit/rollback/dispatch steps |
| `cve-tracking.md` | Common CVE Fetcher Error Handling | Add rollback + per-CVE commit to the error handling pattern |
| `cve-sync-kev.md` | Per-entry isolation (line 108) | Replace "managed internally by `upsert_cve()`" with accurate description |
| `cve-sync-kev.md` | `upsert_references()` failure row (line 175) | Remove "CVE data already committed" — both are in the same transaction |
| `ticket-references.md` | Transaction boundary (lines 284-291) | Replace "separate transaction" with "same per-CVE transaction" |

### 5.2 Must be updated (fetcher pseudocode alignment)

| File | Change required |
|------|-----------------|
| `cve-sync-redhat.md` | Update `execute()` pseudocode: add `commit_and_dispatch()`, fix `fetch_single()` return type. **Fix inconsistency**: line 264 declares `-> None` but line 282 uses `result.data_changed` — see section 5.4. **Fix Phase 2 text** (lines 145-147): replace "the fetcher does not manage this step" with accurate dispatch description |
| `cve-sync-osv.md` | Update `execute()` pseudocode: add `commit_and_dispatch()`, update `fetch_single()` return type. **Fix Phase 2 text** (lines 456-458): replace "the fetcher does not manage this step" with accurate dispatch description |
| `cve-sync-nvd.md` | (1) Update `fetch_single()` signature from `-> None` to `-> PostIngestTasks \| None` (line 59). (2) Add full per-CVE transaction pattern in `execute()` page loop: `upsert_cve()` → `upsert_references()` → `build_post_ingest_tasks()` → `commit_and_dispatch()`. (3) Add `session.rollback()` in per-CVE error path. (4) Update Phase 2 text (line 172): replace "enqueued by `cve_service` after Phase 1 commit" with "dispatched by the fetcher via `commit_and_dispatch()` after per-CVE commit" |
| `cve-sync-ghsa.md` | (1) Update `fetch_single()` signature from `-> None` to `-> PostIngestTasks \| None` (line 47). (2) Add full per-advisory transaction pattern in `execute()` page loop: `upsert_cve()` → `upsert_references()` → `build_post_ingest_tasks()` → `commit_and_dispatch()`. (3) Add `session.rollback()` in per-advisory error path. (4) Update Phase 2 text (line 178): same as NVD |
| `cve-sync-kev.md` | (1) Add full per-entry transaction pattern: CVE lookup → `upsert_cve()` → `upsert_references()` → `build_post_ingest_tasks()` → `commit_and_dispatch()`. (2) Replace error handling steps 3e/3f: remove `record_source_status("failure")`, add `session.rollback()` → `record_failed()` → continue. (3) No `fetch_single()` changes needed (`supports_fetch_single = False`) |
| `cve-sync-mitre.md` | Update "Phase 2 side effects" text: replace "enqueued by `cve_service` after Phase 1 commit" with "dispatched by `BaseGitFetcher` template via `commit_and_dispatch()`" |
| `cve-sync-kernel.md` | Same as mitre |

### 5.3 No longer an inconsistency

The pseudocode in `cve-sync-redhat.md`, `cve-sync-osv.md`,
`cve-sync-nvd.md`, and `cve-sync-ghsa.md` omits
`record_source_status("failure")` in the `execute()` error path. Per
the OQ-3 resolution, this is now **correct by design** — the `execute()`
batch path does NOT write failure status. Only the `fetch_single_cve`
orchestrator (on-demand path) writes failure/missing status for
user-visible feedback.

The Common CVE Fetcher Error Handling must be updated to reflect this
(see Step 3.2 in the Resolution Plan).

### 5.4 Discovered inconsistency: `cve-sync-redhat.md` return type

`cve-sync-redhat.md` has a contradictory pseudocode block:

```python
# Line 264: fetch_single declares -> None
async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
    ...

# Lines 281-282: execute() uses the return value
result = await self.fetch_single(cve_id, session)
if result.data_changed:
    self.record_updated()
```

`fetch_single()` is declared as returning `None`, but `execute()` assigns
its return value to `result` and accesses `result.data_changed`. This
cannot work as written.

**Resolution**: when updating this spec, change `fetch_single()` to
return `PostIngestTasks | None` (per the new architecture) and move
metric recording (`record_created`/`record_updated`) inside
`fetch_single()` where it has access to `UpsertResult.action`. The
`execute()` loop simplifies to:

```python
async def execute(self, session: AsyncSession) -> None:
    for cve_id in active_ticket_cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
        except CVENotInSource:
            pass
        except Exception:
            await session.rollback()
            self.record_failed()
        await asyncio.sleep(self.settings.throttle_delay_seconds)
```

### 5.5 Downstream impact (after resolution)

| File | Impact |
|------|--------|
| `docs/features/tickets/cve-sync-epss.md` | Can be completed with certainty; EPSS enriches CVE EPSS data without package resolution — `commit_and_dispatch()` will dispatch no post-ingest tasks (no CPE/package data in payload) |
| `docs/drafts/epss-fetcher-workplan.md` | OP-4 fully resolved by this decision |
| `docs/drafts/http-client-infrastructure.md` | May reference transaction patterns — no blocking impact |

## 6. Resolution Plan

### Step 1: Update `cve-service.md`

1. Add "Transaction ownership" section (after Service Exceptions, before
   Transaction Boundaries):

   > The module does NOT commit or roll back. All operations execute
   > within the caller's database session. Commit responsibility belongs
   > to the caller.
   >
   > This matches the `ticket_mutations`, `package_service`, and
   > `user_service` pattern.

2. Add `PostIngestTasks` dataclass definition (after `UpsertResult`):
   - `ticket_id: UUID`
   - `cpe_matches: list[CPEMatchEntry]`
   - `affected_cpes: list[str]`
   - `vendor_products: list[tuple[str, str]]`
   - `resolved_packages: list[str]`

3. Add `build_post_ingest_tasks(result, payload)` function specification:
   - Returns `None` if `result.ticket is None` or no package data exists
   - Otherwise extracts package resolution data from payload + ticket ID
     from result

4. Reword Transaction Boundaries Phase 2 description: Phase 2 tasks are
   enqueued by the caller after committing via `commit_and_dispatch()`,
   not arranged by `upsert_cve()` itself. Add: "Phase 1 operations write
   to the session buffer. The caller commits when ready."

5. Verify Crash Recovery section still accurate (the commit-then-enqueue
   window is now more explicit — self-healing via re-sync is unchanged)

### Step 2: Update `fetcher-infrastructure.md`

1. Add `commit_and_dispatch()` to BaseCVEFetcher Concrete Methods table:
   - Signature: `async def commit_and_dispatch(self, session, post_ingest)`
   - Behavior: `await session.commit()`, then dispatch `post_ingest`
     if not None

2. Change `fetch_single()` return type from `-> None` to
   `-> PostIngestTasks | None`:
   - Returns `PostIngestTasks` on success (data written via
     `upsert_cve()`, ready for caller to commit and dispatch)
   - Returns `None` if success but no post-ingest needed
   - Document: metrics (`record_created`/`record_updated`) are called
     inside `fetch_single()` where `UpsertResult.action` is available

3. Update `fetch_single` Signaling Convention table:
   - `Returns normally (PostIngestTasks | None)` →
     `commit_and_dispatch(session, result)` (commits; dispatches only
     if result is not None)
   - `Raises CVENotInSource` → `record_source_status("missing")` then
     commit
   - `Raises other exception` → rollback, Celery retry

4. Add "Session Lifecycle for API-based CVE Fetchers" section:
   - API-based fetchers MUST commit per-CVE in their `execute()` loop
   - Standard pattern: `post_ingest = fetch_single(...)` then
     `commit_and_dispatch(session, post_ingest)`
   - Each iteration has its own transaction boundary

5. Update BaseGitFetcher `execute()` template step 10:
   - `process_item()` now returns `PostIngestTasks | None`
   - After successful `process_item()`: call
     `commit_and_dispatch(session, post_ingest)`
   - On exception: `session.rollback()`, `record_failed()`

6. Update `process_item()` hook contract:
    - Return type from `-> None` to `-> PostIngestTasks | None`
    - Disambiguate `None` return: (a) item was skipped (already
      up-to-date, no work done), vs. (b) item was processed but no
      post-ingest tasks are needed (e.g., enrichment-only upsert with
      no ticket or no CPE data). Both cases result in
      `commit_and_dispatch(session, None)` — commit without dispatch
    - Subclass calls `upsert_cve()` + `upsert_references()` +
      `build_post_ingest_tasks()` internally
    - Subclass records metrics internally (`record_created`/
      `record_updated` where `UpsertResult.action` is available)

7. Update `catch_up()` default implementation:
    - After `self.fetch_single()`, call
      `self.commit_and_dispatch(session, result)`

8. Update `catch_up()` interface contract (lines 750-757):
    - Replace "commits on return" with explicit statement: the default
      `catch_up()` commits internally via `commit_and_dispatch()`, not
      via `run_catch_up` on return

9. Update `BaseCVEFetcher` Non-Modification Statement (lines 1392-1403):
    - Add `commit_and_dispatch()` to the list of concrete methods
      provided by `BaseCVEFetcher`

10. Update `BaseFetcher.run()` session description (lines 47-50):
    - Add clarification: "The session passed to `execute()` may be
      committed and rolled back multiple times (per-item transaction
      boundaries)." This was already true for git-based fetchers; the
      change formalizes it for API-based fetchers as well

### Step 3: Update `cve-tracking.md`

1. Rewrite `fetch_single_cve` Orchestrator Behavior with explicit
    commit/rollback/dispatch:
    - Success → `fetcher.commit_and_dispatch(session, result)`
    - `CVENotInSource` → `record_source_status("missing")`,
      `fetcher.commit_and_dispatch(session, None)` (commits the
      "missing" status, skips dispatch)
    - Retryable error → rollback, Celery retry; after exhaustion →
      `record_source_status("failure")`,
      `fetcher.commit_and_dispatch(session, None)`
    - Non-retryable error → rollback,
      `record_source_status("failure")`,
      `fetcher.commit_and_dispatch(session, None)`

2. Update Common CVE Fetcher Error Handling:
   - Remove `record_source_status("failure")` requirement from the
     `execute()` batch path. The rollback discards the "success" written
     by `upsert_cve()`, naturally preserving the previous CVESource state.
     Explicit "failure" writes are only required in the `fetch_single_cve`
     orchestrator (on-demand path, user-visible feedback)
   - Add `await session.rollback()` as step 2 (clean session for next
     item)
   - Simplified error path: rollback → `record_failed()` → continue

### Step 4: Update `cve-sync-kev.md`

1. Replace per-entry isolation paragraph (line 108-111):
   - Old: "Transaction boundaries are managed internally by
     `upsert_cve()`"
   - New: "Each entry operates in its own transaction boundary.
     `upsert_cve()` acquires a `FOR UPDATE` lock on the CVE row; the
     lock is held until the caller commits via `commit_and_dispatch()`
     after all writes for that entry complete."

2. Fix `upsert_references()` failure row (line 175):
   - Old: "CVE data already committed; reference failure is non-critical"
   - New: "`upsert_references()` failure modes are handled internally
     (skip-and-continue). Under normal operation, no exception
     propagates. Both CVE and reference data are committed together."

3. Remove `record_source_status("failure")` from error handling (lines
   100-102):
   - Old: `record_source_status(session, cve_id, "kev", "failure")`,
     `record_failed()`, log error, continue
   - New: `await session.rollback()`, `record_failed()`, log error,
     continue. The rollback discards any partial writes; `CVESource`
     preserves its previous state (last successful fetch or absent)

### Step 5: Update `ticket-references.md`

1. Replace Transaction Boundary section (lines 284-291):
   - Old: "`upsert_references()` runs in a **separate transaction**
     from `cve_service.upsert_cve()`. Although both receive the same
     `AsyncSession`, the fetcher commits the CVE upsert (Phase 1)
     before calling `upsert_references()`. This means reference
     failures cannot roll back CVE data."
   - New: "`upsert_references()` runs in the **same per-CVE
     transaction** as `cve_service.upsert_cve()`. Both write to the
     session buffer; the caller commits via `commit_and_dispatch()`
     after both operations complete. Since all `upsert_references()`
     failure modes are handled internally (URL validation gate:
     skip-and-continue; IntegrityError: catch-and-merge), no exception
     propagates to the caller under normal operation."

### Step 6: Update individual fetcher specs

**Pattern B — Enrichment fetchers (delegate to `fetch_single()` in execute):**

1. **`cve-sync-redhat.md`**: fix return type inconsistency (section
   5.4), update `execute()` pseudocode with `commit_and_dispatch()`,
   move metric recording inside `fetch_single()`. The `execute()` loop
   becomes: `post_ingest = await self.fetch_single(...)` →
   `await self.commit_and_dispatch(session, post_ingest)`. Error path:
   `session.rollback()` → `record_failed()`.

2. **`cve-sync-osv.md`**: same pattern as RedHat — update
   `fetch_single()` return type, add `commit_and_dispatch()` in loop,
   add `session.rollback()` in error path.

**Pattern A — Discovery fetchers (inline `upsert_cve()` in execute):**

3. **`cve-sync-nvd.md`**:
   - Update `fetch_single()` signature (line 59): `-> PostIngestTasks | None`
   - Add full per-CVE transaction pattern in the page loop (step 4e):
     ```
     result = await upsert_cve(session, cve_id, "nvd", payload)
     await upsert_references(session, ...)
     post_ingest = build_post_ingest_tasks(result, payload)
     await self.commit_and_dispatch(session, post_ingest)
     # record_created/record_updated based on result.action
     ```
   - Add error path per-CVE: `await session.rollback()` →
     `record_failed()` → continue to next CVE
   - Update "Phase 2 side effects" text (line 172)

4. **`cve-sync-ghsa.md`**:
   - Update `fetch_single()` signature (line 47): `-> PostIngestTasks | None`
   - Add full per-advisory transaction pattern (step 6.d after iv+v):
     same as NVD inline pattern
   - Add error path per-advisory (step 6.d.vii): `session.rollback()` →
     `record_failed()` → continue
   - Update "Phase 2 side effects" text (line 178)

**Pattern D — Catalog fetcher (inline `upsert_cve()`, no fetch_single):**

5. **`cve-sync-kev.md`**:
   - Add full per-entry transaction pattern in step 3d:
     ```
     result = await upsert_cve(session, cve_id, "kev", payload)
     await upsert_references(session, ...)  # if ticket exists
     post_ingest = build_post_ingest_tasks(result, payload)
     await self.commit_and_dispatch(session, post_ingest)
     record_updated()
     ```
   - Replace error handling steps 3e/3f: remove
     `record_source_status("failure")`, add `session.rollback()` →
     `record_failed()` → continue
   - No `fetch_single()` changes (not implemented)

**Pattern C — Git-based fetchers (BaseGitFetcher template):**

6. **`cve-sync-mitre.md`**: update Phase 2 text to reference
   `BaseGitFetcher` template and `commit_and_dispatch()`. No pseudocode
   changes needed — the template handles commit/dispatch automatically.

7. **`cve-sync-kernel.md`**: same as mitre

### Step 7: Validate coherence

Invoke `@spec-coherence-reviewer` on the primary modified specs:
- `cve-service.md`
- `fetcher-infrastructure.md`
- `cve-tracking.md`
- `cve-sync-kev.md`
- `ticket-references.md`

### Step 8: Post-application review

Run the appropriate reviewers on each modified spec to verify that the
draft was applied correctly and no new issues were introduced:

- `@spec-coherence-reviewer` — one session per primary modified spec
  (same list as Step 7) to verify inter-spec consistency after all
  changes are in place
- `@spec-gap-analyzer` — on `cve-service.md` and
  `fetcher-infrastructure.md` (the two specs with the most structural
  changes) to verify functional completeness of the new sections
- `@docs-reviewer` — on `cve-service.md` and
  `fetcher-infrastructure.md` to verify documentation accuracy and
  completeness

If any reviewer identifies "Needs revision" issues, address them before
proceeding to Step 9.

### Step 9: Delete draft

Delete `docs/drafts/transaction-ownership-ambiguity.md`. The draft has
served its purpose — the architecture decision and all changes are now
captured in the authoritative feature specifications.

## 7. Open Questions

### Resolved

| # | Question | Resolution |
|---|----------|------------|
| OQ-1 | API fetcher: explicit commit or template? | Helper method `commit_and_dispatch()` on `BaseCVEFetcher`, not a forced template. API fetchers keep their own `execute()` structure but use the shared helper for per-CVE finalization. |
| OQ-2 | `upsert_references()`: same transaction or separate? | Same transaction. All `upsert_references()` failure modes are handled internally (skip-and-continue, IntegrityError catch). Independent failure is effectively impossible under normal operation. No nested savepoint needed. |
| OQ-3 | Is `record_source_status("failure")` in `execute()` needed? | **No.** Removed from the `execute()` batch path. `upsert_cve()` writes "success" as part of Phase 1; if the transaction rolls back, the "success" is discarded and `CVESource` preserves its previous state. The rollback itself is sufficient signal — no explicit "failure" write needed. The `fetch_single_cve` orchestrator (on-demand path) continues to write "failure"/"missing" because user-triggered fetches require visible feedback. KEV's explicit `record_source_status("failure")` was based on the incorrect assumption that `upsert_cve()` committed internally; it is removed in the new architecture. |

### No open questions remain

## 8. Detailed Execution Plan

This section contains the edit-level execution plan with verified line
numbers from the current state of each file. Line numbers were verified
on 2026-06-21 and may drift if files are modified independently before
this plan is applied.

### Execution order

| Order | Step | File(s) | Dependency |
|-------|------|---------|------------|
| 1 | Step 1 | `cve-service.md` | None — defines `PostIngestTasks` and transaction ownership |
| 2 | Step 2 | `fetcher-infrastructure.md` | Step 1 (`PostIngestTasks` type) |
| 3 | Step 5 | `ticket-references.md` | None — can run in parallel with Step 3 |
| 4 | Step 3 | `cve-tracking.md` | Step 2 (`commit_and_dispatch()` definition) |
| 5 | Step 4 | `cve-sync-kev.md` | Step 3 (updated error handling pattern) |
| 6 | Step 6 | 7 fetcher specs | Steps 1-5 (patterns and types defined) |
| 7 | Steps 7-8 | — | Steps 1-6 complete |
| 8 | Step 9 | This draft | Steps 7-8 pass |

### Step 1: `cve-service.md`

#### Edit 1.1 — New "Transaction Ownership" section

**Location**: after line 923 (end of "Exceptions" section), before
line 935 ("CVEIngestPayload Schema").

**Type**: insert new section.

**Content**:

```markdown
## Transaction Ownership

The module does NOT commit or roll back. All operations execute within
the caller's database session. Commit responsibility belongs to the
caller.

This matches the `ticket_mutations`, `package_service`, and
`user_service` pattern.
```

#### Edit 1.2 — `PostIngestTasks` and `build_post_ingest_tasks()`

**Location**: after line 1208 (end of "UpsertResult Design Context"),
before line 1210 ("Callers").

**Type**: insert new subsections.

**Content**:

```markdown
## PostIngestTasks

Dataclass carrying the arguments needed to dispatch the Phase 2
package resolution task after the caller commits. Built by
`build_post_ingest_tasks()` and consumed by `commit_and_dispatch()`.

~~~python
@dataclass
class PostIngestTasks:
    ticket_id: UUID
    cpe_matches: list[CPEMatchEntry]
    affected_cpes: list[str]
    vendor_products: list[tuple[str, str]]
    resolved_packages: list[str]
~~~

All list fields may be empty. The dataclass carries the union of all
package resolution data extracted from the `CVEIngestPayload` —
consumed once by `commit_and_dispatch()` and not persisted.

### `build_post_ingest_tasks()`

~~~python
def build_post_ingest_tasks(
    result: UpsertResult,
    payload: CVEIngestPayload,
) -> PostIngestTasks | None:
~~~

Stateless helper. Returns `None` if `result.ticket is None` (no ticket
to resolve packages for) or if the payload contains no package
resolution data (all of `cpe_matches`, `affected_versions` with CPE or
vendor:product, and `resolved_packages` are empty/None). Otherwise
constructs a `PostIngestTasks` instance by extracting:

- `ticket_id` from `result.ticket.id`
- `cpe_matches` from `payload.cpe_matches` (or empty)
- `affected_cpes` — CPE strings from `payload.affected_versions`
  entries where `cpe` is not None
- `vendor_products` — `(vendor, product)` tuples from
  `payload.affected_versions` entries where both fields are not None
- `resolved_packages` from `payload.resolved_packages` (or empty)

This function does not access the database or enqueue tasks. It is a
pure data extraction step.
```

#### Edit 1.3 — Phase 2 description reword

**Location**: lines 344-349 ("Phase 2 — Asynchronous Celery tasks").

**Type**: replace paragraph.

**Old text** (lines 346-349):

> Package resolution and critical CVE notification (email/webhook —
> external HTTP I/O). Enqueued after the Phase 1 transaction commits
> successfully. Failures in Phase 2 do not roll back the CVE record or
> ticket creation.

**New text**:

> Package resolution (SMELT queries — external HTTP I/O). Phase 1
> operations write to the session buffer. The caller commits when ready.
> Phase 2 tasks are dispatched explicitly by the caller via
> `commit_and_dispatch()` (see
> `docs/features/platform/fetcher-infrastructure.md`, BaseCVEFetcher
> Concrete Methods) after committing — not by `upsert_cve()` itself.
> Failures in Phase 2 do not roll back the CVE record or ticket creation.
>
> **Note**: critical CVE notification and CVE rejection notifications are
> planned future capabilities. When designed, they will extend this
> dispatch mechanism or use an independent one. Until then,
> `PostIngestTasks` carries only package resolution data.

#### Edit 1.4 — Crash recovery verification

**Location**: lines 397-407.

**Action**: no modification needed. The text already describes the
commit-then-enqueue window correctly ("if the process crashes between
Phase 1 commit and Phase 2 task enqueue"). Verified compatible with the
new architecture.

#### Edit 1.5 — Callers table update

**Location**: line 1222 (Callers table, `fetch_single_cve` row).

**Type**: update cell.

**Old text**:

> `fetch_single_cve` (orchestrator) | `record_source_status()` (missing/failure path)

**New text**:

> `fetch_single_cve` (orchestrator) | `record_source_status()` (missing/failure path), `commit_and_dispatch()` (all paths)

#### Edit 1.6 — Callers table: batch fetcher rows

**Location**: lines 1214-1221 (Callers table, batch fetcher rows).

**Type**: replace rows.

**Old rows** (lines 1214-1221):

> | `sync_nvd_cves` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path) |
> | `sync_mitre_cves` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path) |
> | `sync_kernel_cves` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path) |
> | `sync_redhat_cves` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path) |
> | `sync_cisa_kev` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path), `reference_service.upsert_references()` |
> | `sync_epss_scores` (fetcher) | `upsert_cve()` |
> | `sync_ghsa_advisories` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path) |
> | `sync_osv_advisories` (fetcher) | `upsert_cve()`, `record_source_status()` (failure path) |

**New rows**:

> | `sync_nvd_cves` (fetcher) | `upsert_cve()`, `build_post_ingest_tasks()`, `reference_service.upsert_references()` |
> | `sync_mitre_cves` (fetcher) | `upsert_cve()` (via `process_item()`) |
> | `sync_kernel_cves` (fetcher) | `upsert_cve()` (via `process_item()`) |
> | `sync_redhat_cves` (fetcher) | `upsert_cve()` (via `fetch_single()`) |
> | `sync_cisa_kev` (fetcher) | `upsert_cve()`, `build_post_ingest_tasks()`, `reference_service.upsert_references()` |
> | `sync_epss_scores` (fetcher) | `upsert_cve()` |
> | `sync_ghsa_advisories` (fetcher) | `upsert_cve()`, `build_post_ingest_tasks()`, `reference_service.upsert_references()` |
> | `sync_osv_advisories` (fetcher) | `upsert_cve()` (via `fetch_single()`) |

**Rationale**: batch fetchers no longer call `record_source_status()`
in the `execute()` path (OQ-3 resolution — rollback preserves previous
CVESource state). Pattern A/D fetchers (NVD, GHSA, KEV) call
`build_post_ingest_tasks()` directly in their inline `upsert_cve()`
loop. Pattern B fetchers (RedHat, OSV) and Pattern C fetchers (MITRE,
kernel) delegate to `fetch_single()` / `process_item()`, which handles
`build_post_ingest_tasks()` internally. All batch fetchers use
`commit_and_dispatch()` (inherited from `BaseCVEFetcher`), which is not
a `cve_service` operation and therefore not listed in this table.

#### Edit 2.1 — `run()` session description

**Location**: lines 47-50 (session description in `run()` signature
section).

**Type**: extend after line 50 ("The connection is not held open during
`execute()`.").

**New text** (append after line 50):

> The session passed to `execute()` may be committed and rolled back
> multiple times during execution (per-item transaction boundaries).
> This is a documented pattern for both git-based and API-based CVE
> fetchers — see "Session Lifecycle for API-based CVE Fetchers" and
> "BaseGitFetcher Class" (step 10, transaction boundaries).

#### Edit 2.2 — NVD example `fetch_single` signature

**Location**: line 183.

**Type**: replace.

**Old**: `async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:`

**New**: `async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:`

#### Edit 2.3 — RedHat example `fetch_single` signature

**Location**: line 199.

**Type**: replace.

**Old**: `async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:`

**New**: `async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:`

#### Edit 2.4 — Standalone `fetch_single` signature and docstring

**Location**: line 311 and docstring (lines 311-322).

**Type**: replace signature and update docstring.

**Old** (line 311):

> `async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:`

**New**:

> `async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:`

Update docstring to add after "via cve_service.upsert_cve()." (line
317):

> Returns `PostIngestTasks` containing the Celery task arguments for
> Phase 2 dispatch, or `None` if no post-ingest tasks are needed
> (e.g., enrichment-only upsert with no ticket or no CPE data).
> Metrics (`record_created`/`record_updated`) are called inside
> `fetch_single()` where `UpsertResult.action` is available.

And update the paragraph after the docstring (lines 333-335):

**Old**:

> The `fetch_single` method does NOT create a `FetcherRun` record. It is
> a sub-operation invoked as a standalone Celery task, not a full fetcher
> execution. Metric reporting (`record_created`, etc.) is not used.

**New**:

> The `fetch_single` method does NOT create a `FetcherRun` record. It is
> a sub-operation invoked as a standalone Celery task, not a full fetcher
> execution. Metric reporting (`record_created`/`record_updated`) is
> performed inside `fetch_single()` where `UpsertResult.action` is
> available — the caller (`execute()` loop or `fetch_single_cve`
> orchestrator) does not record metrics.

#### Edit 2.5 — `fetch_single` Signaling Convention table

**Location**: lines 354-358.

**Type**: replace table.

**Old table** (lines 354-358):

| Behavior | Meaning | Orchestrator action |
|----------|---------|---------------------|
| Returns normally | Data written via `upsert_cve()` | `status = success` (already written by `upsert_cve` via `record_source_status`) |
| Raises `CVENotInSource` | CVE not present in source | `record_source_status(session, cve_id, fetcher_cls.cve_source_type, "missing")` |
| Raises other exception | Transient error | Celery retries → then `record_source_status(session, cve_id, fetcher_cls.cve_source_type, "failure")` |

**New table**:

| Behavior | Meaning | Caller action |
|----------|---------|---------------|
| Returns `PostIngestTasks` | Data written to session buffer via `upsert_cve()` | `commit_and_dispatch(session, result)` — commits, dispatches Phase 2 |
| Returns `None` | Data written but no post-ingest needed (enrichment-only, no ticket or no CPE data) | `commit_and_dispatch(session, None)` — commits without dispatch |
| Raises `CVENotInSource` | CVE not present in source | `record_source_status(session, cve_id, source, "missing")`, then `commit_and_dispatch(session, None)` — commits the "missing" status, no dispatch |
| Raises other exception (retryable) | Transient error | `session.rollback()`, Celery retry. After exhaustion: `record_source_status(session, cve_id, source, "failure")`, `commit_and_dispatch(session, None)` |
| Raises other exception (non-retryable) | Permanent error | `session.rollback()`, `record_source_status(session, cve_id, source, "failure")`, `commit_and_dispatch(session, None)` |

#### Edit 2.6 — New "Session Lifecycle for API-based CVE Fetchers" section

**Location**: after line 1403 (end of "Non-Modification Statement"),
before line 1405 ("Git-Based Fetchers").

**Type**: insert new section.

**Content**:

```markdown
### Session Lifecycle for API-based CVE Fetchers

API-based CVE fetchers (NVD, Red Hat, GHSA, OSV, KEV) MUST commit
per-CVE in their `execute()` loop. Each iteration has its own
transaction boundary.

**Standard pattern** (enrichment fetchers that delegate to
`fetch_single()`):

~~~python
async def execute(self, session: AsyncSession) -> None:
    for cve_id in scope:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
        except CVENotInSource:
            pass  # or: commit_and_dispatch(session, None) if status write needed
        except Exception:
            await session.rollback()
            self.record_failed()
        await asyncio.sleep(self.settings.throttle_delay_seconds)
~~~

**Standard pattern** (discovery fetchers with inline `upsert_cve()`):

~~~python
async def execute(self, session: AsyncSession) -> None:
    for item in source_items:
        try:
            result = await upsert_cve(session, cve_id, self.cve_source_type, payload)
            await upsert_references(session, ...)
            post_ingest = build_post_ingest_tasks(result, payload)
            await self.commit_and_dispatch(session, post_ingest)
            # record_created/record_updated based on result.action
        except Exception:
            await session.rollback()
            self.record_failed()
~~~

Both patterns use `commit_and_dispatch()` as the per-CVE finalization
step. The helper commits the session (releasing the `FOR UPDATE` lock
acquired by `upsert_cve()`) and dispatches Phase 2 tasks if
`post_ingest` is not `None`.

**Metric placement**: metric helpers (`record_created`,
`record_updated`, `record_failed`) are in-memory counter increments
with no database interaction. Their placement relative to
`commit_and_dispatch()` is functionally irrelevant — whether recorded
before commit (Pattern B, inside `fetch_single()`) or after commit
(Pattern A, in the `execute()` loop) does not affect correctness.

This session lifecycle was always true for git-based fetchers (the
`BaseGitFetcher` template commits per-item in step 10). This section
formalizes the same pattern for API-based fetchers.
```

#### Edit 2.7 — `commit_and_dispatch()` in Concrete Methods table

**Location**: lines 1259-1266 (BaseCVEFetcher "Concrete Methods"
table).

**Type**: add row to table.

**New row** (after the `catch_up` row):

| `commit_and_dispatch(session, post_ingest)` | Helper method: commits the session, then dispatches `post_ingest` tasks (if not `None`) via `apply_async()`. Dispatches exactly one `resolve_ticket_packages.apply_async()` call per invocation. If `post_ingest` is `None`, commits without dispatching. **Commit failure**: if `session.commit()` raises (e.g., `OperationalError` from lost DB connection), the exception propagates to the caller — no dispatch is attempted. The caller is responsible for rollback. **Celery failure**: if `apply_async()` raises after a successful commit (e.g., Celery broker unreachable), the error is logged at WARNING level and the function returns normally — Phase 2 recovery relies on the next sync cycle (see `cve-service.md`, Crash Recovery). **Re-invocation safety**: if called twice with the same `post_ingest` (e.g., Celery retry after successful first invocation), the second `session.commit()` is a no-op (empty buffer); the second dispatch sends a duplicate Phase 2 task. Phase 2 tasks are idempotent (`TicketPackage` existence check), so duplicate dispatch is safe |

#### Edit 2.8 — Non-Modification Statement extension

**Location**: lines 1392-1403 (Non-Modification Statement list).

**Type**: add item 7.

**New item**:

> 7\. The `commit_and_dispatch()` helper method for per-CVE commit and
>    Phase 2 task dispatch

#### Edit 2.9 — BaseGitFetcher `execute()` step 10 transaction boundaries

**Location**: lines 2057-2064 (step 10 "Transaction boundaries"
paragraph).

**Type**: replace paragraph.

**Old text** (lines 2057-2064):

>     **Transaction boundaries**: each iteration of the processing loop
>     operates in its own transaction boundary. After `process_item()`
>     returns successfully or raises an exception (caught by step 10d),
>     the session is committed or rolled back respectively before
>     proceeding to the next item. This ensures that a failure in one
>     item does not corrupt the session or affect the processing of
>     subsequent items, and that Phase 2 side effects (enqueued
>     post-commit by `cve_service.upsert_cve()`) are triggered per-item.

**New text**:

>     **Transaction boundaries**: each iteration of the processing loop
>     operates in its own transaction boundary. `process_item()` returns
>     `PostIngestTasks | None`; after a successful return, the template
>     calls `self.commit_and_dispatch(session, post_ingest)` which
>     commits the session and dispatches Phase 2 tasks if `post_ingest`
>     is not `None`. On exception (caught by step 10d), the template
>     calls `session.rollback()` before `record_failed()`. This ensures
>     that a failure in one item does not corrupt the session or affect
>     the processing of subsequent items.

#### Edit 2.10 — `process_item()` hook return type and semantics

**Location**: line 2137 (signature) and lines 2148-2153 (return
semantics).

**Type**: replace.

**Old signature** (line 2137):

> `##### process_item(path: str, content: bytes, session: AsyncSession) -> None`

**New signature**:

> `##### process_item(path: str, content: bytes, session: AsyncSession) -> PostIngestTasks | None`

**Old return text** (lines 2148-2153):

> The hook is responsible for:
> 1. Parsing the content and applying business logic (upsert, etc.)
> 2. Calling `self.record_created()` or `self.record_updated()` to report
>    the outcome (same pattern as non-git `BaseFetcher` subclasses)
> 3. Returning `None` if the item was skipped (already up-to-date) —
>    no metric is recorded, which is the correct behavior

**New return text**:

> The hook is responsible for:
> 1. Parsing the content and applying business logic (upsert, etc.)
> 2. Calling `self.record_created()` or `self.record_updated()` to report
>    the outcome (same pattern as non-git `BaseFetcher` subclasses)
> 3. Returning `PostIngestTasks` if post-ingest dispatch is needed, or
>    `None` in two cases: (a) the item was skipped (already up-to-date,
>    no work done — no metric is recorded), or (b) the item was
>    processed but no post-ingest tasks are needed (e.g.,
>    enrichment-only upsert with no ticket or no CPE data — metric IS
>    recorded). Both `None` cases result in
>    `commit_and_dispatch(session, None)` — the template commits without
>    dispatching Phase 2 tasks

#### Edit 2.11 — `process_item` Phase 2 side effects text

**Location**: lines 2155-2159.

**Type**: replace.

**Old text** (lines 2155-2159):

> **Phase 2 side effects**: hooks that call `cve_service.upsert_cve()`
> trigger Phase 2 processing (package resolution, notifications)
> automatically via Celery task enqueue after the Phase 1 transaction
> commits. No post-processing batch hook is needed — Phase 2 is per-item
> and self-contained.

**New text**:

> **Phase 2 side effects**: hooks that call `cve_service.upsert_cve()`
> return `PostIngestTasks` containing the Phase 2 task arguments. The
> `BaseGitFetcher` template dispatches these tasks via
> `commit_and_dispatch()` after committing the per-item transaction.
> No post-processing batch hook is needed — Phase 2 is per-item and
> self-contained.

#### Edit 2.12 — Default `catch_up()` implementation update

**Location**: lines 1268-1284 (default `catch_up()` code block).

**Type**: replace code block.

**Old code** (lines 1271-1283):

```python
class BaseCVEFetcher(BaseFetcher):
    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Default: extract cve_id from ticket, call fetch_single().

        All boundary conditions from the BaseFetcher catch_up()
        interface contract apply.
        """
        ticket = await session.get(Ticket, UUID(ticket_id))
        if ticket and ticket.cve_id:
            try:
                await self.fetch_single(str(ticket.cve_id), session)
            except CVENotInSource:
                pass  # CVE not in this source — nothing to catch up
```

**New code**:

```python
class BaseCVEFetcher(BaseFetcher):
    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Default: extract cve_id from ticket, call fetch_single().

        All boundary conditions from the BaseFetcher catch_up()
        interface contract apply.
        """
        ticket = await session.get(Ticket, UUID(ticket_id))
        if ticket and ticket.cve_id:
            try:
                result = await self.fetch_single(str(ticket.cve_id), session)
                await self.commit_and_dispatch(session, result)
            except CVENotInSource:
                await session.rollback()  # defensive: ensure clean session state
```

#### Edit 2.13 — `catch_up()` interface contract transaction text

**Location**: lines 749-757 (catch_up interface contract, transaction
boundaries — "Default `catch_up()`" bullet).

**Type**: replace bullet text.

**Old text** (lines 750-751):

>   - **Default `catch_up()`** (CVE fetchers): single transaction —
>     reads the ticket, calls `fetch_single()`, commits on return

**New text**:

>   - **Default `catch_up()`** (CVE fetchers): reads the ticket, calls
>     `fetch_single()`, then commits via `self.commit_and_dispatch()`
>     internally — not via `run_catch_up` on return

### Step 3: `cve-tracking.md`

#### Edit 3.1 — `fetch_single_cve` orchestrator behavior rewrite

**Location**: lines 468-492 (Orchestrator behavior section).

**Type**: replace steps 1-5.

**Old text** (lines 470-492): current 5-step behavior without explicit
commit/rollback/dispatch.

**New text**:

>   1. Look up the fetcher class from the registry by `fetcher_name`
>   2. Read `source = fetcher_cls.cve_source_type` (class-level access,
>      no instantiation needed for this lookup)
>   3. Instantiate the fetcher and call
>      `fetcher.fetch_single(cve_id, session)`
>   4. Handle outcomes:
>      - **Returns normally** (`PostIngestTasks | None`):
>        `await fetcher.commit_and_dispatch(session, result)` — commits
>        the session (persisting CVE data + `CVESource` "success"
>        written by `upsert_cve()`) and dispatches Phase 2 tasks if
>        `result` is not `None`
>      - **Raises `CVENotInSource`**: call
>        `record_source_status(session, cve_id, source, "missing")`,
>        then `await fetcher.commit_and_dispatch(session, None)` —
>        commits the "missing" status, no dispatch
>      - **Raises retryable exception** (network, HTTP 5xx, timeout,
>        429): `await session.rollback()`, Celery retry (3 attempts,
>        exponential backoff 5s/10s/20s). After retries exhausted:
>        `record_source_status(session, cve_id, source, "failure")`,
>        `await fetcher.commit_and_dispatch(session, None)` — commits
>        the "failure" status
>      - **Raises non-retryable exception** (HTTP 403, other 4xx,
>        parsing error): `await session.rollback()`,
>        `record_source_status(session, cve_id, source, "failure")`,
>        `await fetcher.commit_and_dispatch(session, None)`
>   5. Best-effort Redis key deletion (unchanged):
>      ```python
>      try:
>          redis.delete(f"fetch_pending:{cve_id}:{source}")
>      except RedisError:
>          pass  # TTL will clean up
>      ```

#### Edit 3.2 — Common CVE Fetcher Error Handling rewrite

**Location**: lines 361-383 (Common CVE Fetcher Error Handling section).

**Type**: replace numbered list.

**Old text** (lines 365-379): 4-step error handling with
`record_source_status("failure")` in step 2.

**New text**:

> All CVE fetchers follow the same error handling pattern for individual
> CVE parse/upsert failures during batch execution (`execute()`):
>
> 1. Log ERROR with CVE-ID and exception details
> 2. `await session.rollback()` — clean the session for the next item.
>    The rollback discards the `CVESource` "success" written by
>    `upsert_cve()`, naturally preserving the previous `CVESource` state.
>    No explicit `record_source_status("failure")` is needed in the
>    batch path
> 3. Call `self.record_failed()` and continue processing the next CVE.
>    A batch must never abort entirely due to a single CVE failure.
>    Source-specific abort conditions (e.g., persistent infrastructure
>    failure after N consecutive errors) are documented in each fetcher's
>    dedicated specification (see the CVE Fetcher Specifications table)
>
> **Distinction from the on-demand path**: the `fetch_single_cve`
> orchestrator (on-demand path) explicitly writes
> `record_source_status("failure"/"missing")` because user-triggered
> fetches require visible per-source feedback via the Fetch Status Read
> Path. The `execute()` batch path does not write explicit failure
> status — the rollback is sufficient.

### Step 4: `cve-sync-kev.md`

#### Edit 4.1 — Per-entry isolation paragraph

**Location**: lines 108-111.

**Type**: replace paragraph.

**Old text** (lines 108-111):

> **Per-entry isolation**: each entry (steps 3a–3f) is processed
> independently with per-entry error isolation. A failure at entry N does
> not affect entries 1..N-1. Transaction boundaries are managed internally
> by `upsert_cve()` (which acquires its own `FOR UPDATE` lock per CVE).

**New text**:

> **Per-entry isolation**: each entry (steps 3a–3f) is processed
> independently with per-entry error isolation. A failure at entry N does
> not affect entries 1..N-1. Each entry operates in its own transaction
> boundary. `upsert_cve()` acquires a `FOR UPDATE` lock on the CVE row;
> the lock is held until the caller commits via `commit_and_dispatch()`
> after all writes for that entry complete.

#### Edit 4.2 — Algorithm step 3d: add full transaction pattern

**Location**: lines 91-99 (algorithm step 3d, after
`upsert_references()` and `record_updated()`).

**Type**: extend step 3d with transaction finalization.

After the existing `upsert_references()` call and `record_updated()`
(line 99), add:

>       - Call `build_post_ingest_tasks(result, payload)` → receive
>         `PostIngestTasks | None`
>       - Call `self.commit_and_dispatch(session, post_ingest)` — commits
>         the session (CVE data + references) and dispatches Phase 2
>         tasks if `post_ingest` is not `None`

#### Edit 4.3 — Error handling steps 3e-3f

**Location**: lines 100-106 (algorithm steps 3e-3f).

**Type**: replace.

**Old text** (lines 100-106):

>    e. On per-entry error (after successful CVE lookup):
>       `record_source_status(session, cve_id, "kev", "failure")`,
>       `record_failed()`, log error, continue
>    f. On per-entry error (before CVE lookup or lookup failure):
>       `record_failed()`, log error, continue (no `record_source_status` —
>       CVE UUID unavailable)

**New text**:

>    e. On per-entry error (after successful CVE lookup):
>       `await session.rollback()`, `record_failed()`, log error,
>       continue. The rollback discards any partial writes; `CVESource`
>       preserves its previous state
>    f. On per-entry error (before CVE lookup or lookup failure):
>       `record_failed()`, log error, continue (no rollback needed — no
>       writes were attempted)

#### Edit 4.4 — Isolated errors table

**Location**: lines 170-176 (isolated errors table).

**Type**: update 3 rows.

Row "Invalid/missing `dateAdded` on entry" (line 173):

**Old**: `record_source_status("failure")`, `record_failed()`, skip
entry, continue

**New**: `session.rollback()`, `record_failed()`, skip entry, continue

Row "`upsert_cve()` failure on entry" (line 174):

**Old**: `record_source_status("failure")`, `record_failed()`, log,
continue

**New**: `session.rollback()`, `record_failed()`, log, continue

Row "`upsert_references()` failure on entry" (line 175):

**Old**: WARNING log, continue (CVE data already committed; reference
failure is non-critical)

**New**: WARNING log, continue (`upsert_references()` failure modes are
handled internally — skip-and-continue. Both CVE and reference data are
committed together via `commit_and_dispatch()`)

### Step 5: `ticket-references.md`

#### Edit 5.1 — Transaction boundary paragraph

**Location**: lines 284-291.

**Type**: replace paragraph.

**Old text** (lines 284-291):

> **Transaction boundary**: `upsert_references()` runs in a **separate
> transaction** from `cve_service.upsert_cve()`. Although both receive
> the same `AsyncSession`, the fetcher commits the CVE upsert (Phase 1)
> before calling `upsert_references()`. This means reference failures
> cannot roll back CVE data. Each individual reference upsert is
> independent — if a single reference fails (e.g., a URL from upstream
> data exceeds the 2048-character limit), the service logs the failure
> and continues with the remaining references (skip-and-continue).

**New text**:

> **Transaction boundary**: `upsert_references()` runs in the **same
> per-CVE transaction** as `cve_service.upsert_cve()`. Both write to the
> session buffer; the caller commits via `commit_and_dispatch()` after
> both operations complete. Since all `upsert_references()` failure modes
> are handled internally (URL validation gate: skip-and-continue;
> IntegrityError: catch-and-merge), no exception propagates to the caller
> under normal operation. Each individual reference upsert is independent
> — if a single reference fails (e.g., a URL from upstream data exceeds
> the 2048-character limit), the service logs the failure and continues
> with the remaining references (skip-and-continue).

### Step 6: Individual fetcher specs

#### Edit 6A — `cve-sync-redhat.md` (Pattern B)

**Edit 6A.1** — `fetch_single()` signature (line 264):

**Old**: `async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:`

**New**: `async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:`

Add note after signature: metrics (`record_created`/`record_updated`)
are called inside `fetch_single()` where `UpsertResult.action` is
available.

**Edit 6A.2** — `execute()` pseudocode (lines 277-293):

Replace entire block with:

```python
async def execute(self, session: AsyncSession) -> None:
    for cve_id in active_ticket_cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
        except CVENotInSource:
            await session.rollback()  # defensive: ensure clean session state
        except Exception:
            await session.rollback()
            self.record_failed()
        await asyncio.sleep(self.settings.throttle_delay_seconds)
```

This resolves the inconsistency documented in draft section 5.4
(`result.data_changed` on a `-> None` return type).

#### Edit 6A.3 — Phase 2 dispatch text (lines 145-147)

**Location**: lines 145-147 (Phase 2 side effects paragraph).

**Type**: replace sentence.

**Old text** (lines 145-147):

> The service layer enqueues one `add_package_to_ticket()` background
> task per package name as a Phase 2 side effect — the fetcher does
> not manage this step.

**New text**:

> Package names are passed as `resolved_packages` in the
> `CVEIngestPayload`. The fetcher dispatches Phase 2 tasks (package
> resolution via SMELT) after per-CVE commit via
> `commit_and_dispatch()`.

#### Edit 6B — `cve-sync-osv.md` (Pattern B)

**Edit 6B.1** — `fetch_single()` signature (line 301):

**Old**: `-> None:`

**New**: `-> PostIngestTasks | None:`

**Edit 6B.2** — `execute()` pseudocode (lines 317-338):

Update loop body: replace `await self.fetch_single(cve_id, session)` +
`self.record_updated()` with `post_ingest = await self.fetch_single()`
+ `await self.commit_and_dispatch(session, post_ingest)`. Add
`await session.rollback()` in `CVENotInSource` handler (defensive:
ensure clean session state). Error path: add
`await session.rollback()` before `self.record_failed()`. The
consecutive-failure abort pattern (`consecutive_failures >= 3`) is
preserved unchanged.

**Edit 6B.3** — Phase 2 dispatch text (lines 456-458):

**Location**: lines 456-458 (Phase 2 Side Effects section).

**Type**: replace sentence.

**Old text** (lines 456-458):

> The `upsert_cve()` service enqueues one `add_package_to_ticket()`
> background task per package name as a Phase 2 side effect — the
> fetcher does not manage this step.

**New text**:

> Package names are passed as `resolved_packages` in the
> `CVEIngestPayload`. The fetcher dispatches Phase 2 tasks (package
> resolution via SMELT) after per-CVE commit via
> `commit_and_dispatch()`.

#### Edit 6C — `cve-sync-nvd.md` (Pattern A)

**Edit 6C.1** — `fetch_single()` signature (line 59):

**Old**: `-> None:`

**New**: `-> PostIngestTasks | None:`

**Edit 6C.2** — Per-CVE processing in `execute()` page loop (lines
159-169, step 4e):

After the existing `upsert_cve()` + `upsert_references()` calls, add:

```python
post_ingest = build_post_ingest_tasks(result, payload)
await self.commit_and_dispatch(session, post_ingest)
# record_created/record_updated based on result.action
```

Add error path per-CVE: `await session.rollback()` →
`self.record_failed()` → continue to next CVE.

**Edit 6C.3** — Phase 2 text (lines 171-176):

Replace "enqueued by `cve_service` after Phase 1 commit" with
"dispatched by the fetcher via `commit_and_dispatch()` after per-CVE
commit".

#### Edit 6D — `cve-sync-ghsa.md` (Pattern A)

**Edit 6D.1** — `fetch_single()` signature (line 47):

**Old**: `-> None:`

**New**: `-> PostIngestTasks | None:`

**Edit 6D.2** — Per-advisory processing in `execute()` (lines 154-170,
step 6d):

After `upsert_cve()` (line 154) and `upsert_references()` (line 156),
add:

```python
post_ingest = build_post_ingest_tasks(result, payload)
await self.commit_and_dispatch(session, post_ingest)
```

Error path: add `await session.rollback()` before `record_failed()`.

**Edit 6D.3** — Phase 2 text (lines 177-180):

Same change as NVD (Edit 6C.3).

#### Edit 6E — `cve-sync-mitre.md` (Pattern C)

**Edit 6E.1** — Phase 2 text (lines 86-91):

Replace "enqueued by `cve_service` after Phase 1 commit" with
"dispatched by `BaseGitFetcher` template via `commit_and_dispatch()`
after per-item commit".

#### Edit 6F — `cve-sync-kernel.md` (Pattern C)

**Edit 6F.1** — Phase 2 text (lines 115-118):

Same change as MITRE (Edit 6E.1).

### Steps 7-8: Post-application review

After all edits in Steps 1-6 are applied:

**Step 7 — Spec coherence review** (one session per spec):

- `@spec-coherence-reviewer` on `cve-service.md`
- `@spec-coherence-reviewer` on `fetcher-infrastructure.md`
- `@spec-coherence-reviewer` on `cve-tracking.md`
- `@spec-coherence-reviewer` on `cve-sync-kev.md`
- `@spec-coherence-reviewer` on `ticket-references.md`

**Step 8 — Gap analysis and docs review**:

- `@spec-gap-analyzer` on `cve-service.md`
- `@spec-gap-analyzer` on `fetcher-infrastructure.md`
- `@docs-reviewer` on `cve-service.md`
- `@docs-reviewer` on `fetcher-infrastructure.md`

If any reviewer identifies "Needs revision" issues, address them before
Step 9.

### Step 9: Delete draft

Delete `docs/drafts/transaction-ownership-ambiguity.md`. The draft has
served its purpose — the architecture decision and all changes are now
captured in the authoritative feature specifications.

## 9. Session Log

| Date | Work done |
|------|-----------|
| 2026-06-21 | Initial analysis during EPSS draft work. Identified ambiguity, collected evidence from all fetcher specs, documented three possible architectures (A: service commits, B: caller commits with hooks, C: infrastructure template) |
| 2026-06-21 | Deep analysis session. Evaluated 5 architectural solutions (pure caller, orchestrator wrapper, after_commit hooks, outbox pattern, unified template). Rejected after_commit hooks (rollback semantics issue), outbox (unnecessary complexity), and full template (API fetchers too diverse). Decided on pure service + explicit `commit_and_dispatch()` helper. Resolved OQ-1 (helper method, not template) and OQ-2 (same transaction — `upsert_references()` failure modes are all internal). Discovered `cve-sync-redhat.md` return type inconsistency. Discovered `ticket-references.md` "separate transaction" statement needs correction. Formulated 7-step resolution plan. Resolved OQ-3: `record_source_status("failure")` removed from `execute()` batch path — rollback preserves previous CVESource state naturally; explicit failure writes only needed in on-demand path for user feedback. KEV's explicit failure write was based on incorrect architectural assumption. All open questions resolved |
| 2026-06-21 | Fetcher alignment verification. Categorized all 7 CVE fetchers into 4 patterns (A: discovery/inline, B: enrichment/delegate, C: git-based/template, D: catalog). Identified missing changes in draft for NVD and GHSA (fetch_single return type, build_post_ingest_tasks in inline flow, session.rollback in error path, Phase 2 text). Expanded Step 6 with per-pattern instructions and inline code examples. Confirmed that manual-vs-scheduled distinction for Pattern B fetchers (RedHat, OSV) is handled entirely by the caller's error handling — `fetch_single()` itself is context-agnostic |
| 2026-06-21 | Design review + spec coherence review. Design reviewer verdict: minor concerns — architecture is sound. Adopted recommendation: specified `commit_and_dispatch()` behavior on Celery dispatch failure (log WARNING, return normally, rely on next sync for Phase 2 recovery). Spec coherence reviewer verdict: minor issues — all major contradictions properly tracked. Integrated 4 previously untracked areas in `fetcher-infrastructure.md`: (1) `catch_up()` interface contract "commits on return" phrasing, (2) `BaseCVEFetcher` Non-Modification Statement missing `commit_and_dispatch()`, (3) `process_item()` `None` return ambiguity (skipped vs. no post-ingest), (4) `BaseFetcher.run()` session description per-item commit clarification. Clarified universal caller pattern: `CVENotInSource` and failure paths use `commit_and_dispatch(session, None)` instead of bare `session.commit()` |
| 2026-06-21 | Detailed execution plan. Read all 12 affected files and verified line numbers. Categorized edits into 30+ individual operations across 8 execution steps. For each edit: identified exact insertion/replacement point, wrote old/new text, and documented dependencies between steps. Added Section 8 (Detailed Execution Plan) to the draft with full edit-level instructions ready for sequential application |
| 2026-06-21 | Pre-application review. Ran three reviewers on the draft: `@design-reviewer` (verdict: minor concerns — architecture sound), `@spec-coherence-reviewer` (verdict: minor issues — 2 medium, 1 low), `@spec-gap-analyzer` (verdict: needs revision — 1 high, 3 medium, 4 low). **Resolved all findings**: (1) High: removed notification mentions from `PostIngestTasks` — notifications are a future capability, `PostIngestTasks` carries only package resolution data. Added future-capability note to Phase 2 reword. (2) Medium: added Edit 1.6 (Callers table batch fetcher rows update), Edit 6A.3 and Edit 6B.3 (fix "fetcher does not manage" text in RedHat and OSV specs), extended Edit 2.7 with `session.commit()` failure behavior (propagates to caller), re-invocation safety (idempotent Phase 2), and task count (exactly one `resolve_ticket_packages` per invocation). (3) Low: added defensive `session.rollback()` in `catch_up()` `CVENotInSource` handler (Edit 2.12) and batch `execute()` `CVENotInSource` handlers (Edit 6A.2, Edit 6B.2). Added metric placement note to Edit 2.6. Updated Section 5.1 and 5.2 tables to reflect new edits. Total edit count increased from ~30 to ~37 |
