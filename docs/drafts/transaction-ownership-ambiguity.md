# Transaction Ownership Ambiguity in CVE Service

**Status**: Draft — architecture decided, spec modifications pending
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
| `BaseCVEFetcher.commit_and_dispatch(session, post_ingest)` | Helper method: commits the session, then dispatches post-ingest tasks (if any). Standard per-CVE finalization. |
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
| `fetcher-infrastructure.md` | `BaseCVEFetcher` Concrete Methods | Add `commit_and_dispatch(session, post_ingest)` helper method |
| `fetcher-infrastructure.md` | `fetch_single()` signature | Change return type from `-> None` to `-> PostIngestTasks \| None` |
| `fetcher-infrastructure.md` | `fetch_single` Signaling Convention | Update table with new return semantics |
| `fetcher-infrastructure.md` | New "Session Lifecycle for API-based CVE Fetchers" section | Document per-CVE commit pattern with `commit_and_dispatch()` |
| `fetcher-infrastructure.md` | `BaseGitFetcher` `execute()` step 10 | Update: `process_item()` returns `PostIngestTasks \| None`, template calls `commit_and_dispatch()` |
| `fetcher-infrastructure.md` | `process_item()` hook | Change return type from `-> None` to `-> PostIngestTasks \| None` |
| `fetcher-infrastructure.md` | `catch_up()` default | Add `commit_and_dispatch()` after `fetch_single()` |
| `cve-tracking.md` | `fetch_single_cve` orchestrator | Add explicit commit/rollback/dispatch steps |
| `cve-tracking.md` | Common CVE Fetcher Error Handling | Add rollback + per-CVE commit to the error handling pattern |
| `cve-sync-kev.md` | Per-entry isolation (line 108) | Replace "managed internally by `upsert_cve()`" with accurate description |
| `cve-sync-kev.md` | `upsert_references()` failure row (line 175) | Remove "CVE data already committed" — both are in the same transaction |
| `ticket-references.md` | Transaction boundary (lines 284-291) | Replace "separate transaction" with "same per-CVE transaction" |

### 5.2 Must be updated (fetcher pseudocode alignment)

| File | Change required |
|------|-----------------|
| `cve-sync-redhat.md` | Update `execute()` pseudocode: add `commit_and_dispatch()`, fix `fetch_single()` return type. **Fix inconsistency**: line 264 declares `-> None` but line 282 uses `result.data_changed` — see section 5.4 |
| `cve-sync-osv.md` | Update `execute()` pseudocode: add `commit_and_dispatch()`, update `fetch_single()` return type |
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
   - `cpe_matches: list[CPEMatch]`
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
   - Subclass calls `upsert_cve()` + `upsert_references()` +
     `build_post_ingest_tasks()` internally
   - Subclass records metrics internally (`record_created`/
     `record_updated` where `UpsertResult.action` is available)

7. Update `catch_up()` default implementation:
   - After `self.fetch_single()`, call
     `self.commit_and_dispatch(session, result)`

### Step 3: Update `cve-tracking.md`

1. Rewrite `fetch_single_cve` Orchestrator Behavior with explicit
   commit/rollback/dispatch:
   - Success → `fetcher.commit_and_dispatch(session, result)`
   - `CVENotInSource` → `record_source_status("missing")`, commit
   - Retryable error → rollback, Celery retry; after exhaustion →
     `record_source_status("failure")`, commit
   - Non-retryable error → rollback,
     `record_source_status("failure")`, commit

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

## 7. Open Questions

### Resolved

| # | Question | Resolution |
|---|----------|------------|
| OQ-1 | API fetcher: explicit commit or template? | Helper method `commit_and_dispatch()` on `BaseCVEFetcher`, not a forced template. API fetchers keep their own `execute()` structure but use the shared helper for per-CVE finalization. |
| OQ-2 | `upsert_references()`: same transaction or separate? | Same transaction. All `upsert_references()` failure modes are handled internally (skip-and-continue, IntegrityError catch). Independent failure is effectively impossible under normal operation. No nested savepoint needed. |
| OQ-3 | Is `record_source_status("failure")` in `execute()` needed? | **No.** Removed from the `execute()` batch path. `upsert_cve()` writes "success" as part of Phase 1; if the transaction rolls back, the "success" is discarded and `CVESource` preserves its previous state. The rollback itself is sufficient signal — no explicit "failure" write needed. The `fetch_single_cve` orchestrator (on-demand path) continues to write "failure"/"missing" because user-triggered fetches require visible feedback. KEV's explicit `record_source_status("failure")` was based on the incorrect assumption that `upsert_cve()` committed internally; it is removed in the new architecture. |

### No open questions remain

## 8. Session Log

| Date | Work done |
|------|-----------|
| 2026-06-21 | Initial analysis during EPSS draft work. Identified ambiguity, collected evidence from all fetcher specs, documented three possible architectures (A: service commits, B: caller commits with hooks, C: infrastructure template) |
| 2026-06-21 | Deep analysis session. Evaluated 5 architectural solutions (pure caller, orchestrator wrapper, after_commit hooks, outbox pattern, unified template). Rejected after_commit hooks (rollback semantics issue), outbox (unnecessary complexity), and full template (API fetchers too diverse). Decided on pure service + explicit `commit_and_dispatch()` helper. Resolved OQ-1 (helper method, not template) and OQ-2 (same transaction — `upsert_references()` failure modes are all internal). Discovered `cve-sync-redhat.md` return type inconsistency. Discovered `ticket-references.md` "separate transaction" statement needs correction. Formulated 7-step resolution plan. Resolved OQ-3: `record_source_status("failure")` removed from `execute()` batch path — rollback preserves previous CVESource state naturally; explicit failure writes only needed in on-demand path for user feedback. KEV's explicit failure write was based on incorrect architectural assumption. All open questions resolved |
| 2026-06-21 | Fetcher alignment verification. Categorized all 7 CVE fetchers into 4 patterns (A: discovery/inline, B: enrichment/delegate, C: git-based/template, D: catalog). Identified missing changes in draft for NVD and GHSA (fetch_single return type, build_post_ingest_tasks in inline flow, session.rollback in error path, Phase 2 text). Expanded Step 6 with per-pattern instructions and inline code examples. Confirmed that manual-vs-scheduled distinction for Pattern B fetchers (RedHat, OSV) is handled entirely by the caller's error handling — `fetch_single()` itself is context-agnostic |
