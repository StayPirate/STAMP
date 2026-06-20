# BaseFetcher All-Items-Failed Safety Check

Status: **Approved** — ready to apply.

Decision: **Option A** (automatic for all fetchers, no opt-out attribute).

## Problem

When a `BaseFetcher` subclass's `execute()` method returns normally but all
items have failed (`items_failed > 0` and `items_created + items_updated == 0`),
the run is marked as `partial`. This is semantically incorrect — `partial`
implies some items succeeded, but in this case nothing worked.

`BaseGitFetcher` already addresses this with a safety check at step 11 of its
template method:

```python
if items_failed > 0 and items_created + items_updated == 0:
    raise RuntimeError(
        f"All {items_failed} items failed — cursor not advanced for safety"
    )
```

This converts a `partial` into a `failure`, which is the correct semantic
status. However, this safety check is specific to `BaseGitFetcher` —
`BaseFetcher` and `BaseCVEFetcher` do not have it.

## Affected Fetchers

Non-git fetchers that iterate over items and could theoretically have all items
fail without raising from `execute()`:

| Fetcher | Base class | Affected? |
|---------|-----------|-----------|
| `sync_nvd_cves` | `BaseCVEFetcher` | Yes — page-level errors abort, but per-CVE errors within pages are isolated |
| `sync_redhat_cves` | `BaseCVEFetcher` | Yes — iterates over CVE IDs with per-entry isolation |
| `sync_osv_cves` | `BaseCVEFetcher` | Yes — iterates over CVE IDs with per-entry isolation (has abort threshold at 3 consecutive failures, but below-threshold scattered failures could still result in 100% failure) |
| `sync_cisa_kev` | `BaseCVEFetcher` | Yes — iterates over catalog entries with per-entry isolation |
| `sync_epss_scores` | `BaseCVEFetcher` | Yes (planned) — same catalog pattern as KEV |
| `sync_ghsa_advisories` | `BaseCVEFetcher` | Yes — page-level errors abort, but per-advisory errors within pages are isolated |
| `sync_smelt_products` | `BaseFetcher` | Yes — iterates over product records |
| `sync_aimaas_lifecycle` | `BaseFetcher` | Yes — iterates over product records |
| `sync_aimaas_thresholds` | `BaseFetcher` | Yes — iterates over threshold records |
| `detect_ibs_track_releases` | `BaseFetcher` | Yes — iterates over tracks |
| `detect_ibs_product_releases` | `BaseFetcher` | Yes — iterates over products |
| `sync_ldap_directory` | `BaseFetcher` | Yes — iterates over AD entries |
| `sync_ibs_requests` | `BaseFetcher` | Yes — iterates over IBS requests |

Git-based fetchers (`sync_mitre_cves`, `sync_kernel_cves`) already have the
safety check via `BaseGitFetcher`.

## Decision

**Option A**: promote the safety check to `BaseFetcher.run()`. All fetcher
subclasses benefit automatically. The redundant step 11 in `BaseGitFetcher`
is removed.

**Implementation approach**: `run()` checks the condition directly after
`execute()` returns normally and sets `status = failure` with
`error_message = "All {N} items failed"` without raising a `RuntimeError`.
Since the check lives inside `run()` itself (not inside `execute()`), there
is no need to communicate via exception — `run()` can assign the failure
status directly. This avoids the awkward pattern of raising an exception to
be caught by the same function.

**Status determination logic in `run()` after `execute()` returns normally**:

```
if items_failed > 0 and items_created + items_updated == 0:
    status = failure
    error_message = f"All {items_failed} items failed"
elif items_failed > 0:
    status = partial
else:
    status = success
```

## Side Effects

- **Cursor behavior**: `failure` status does not advance the cursor (per
  `fetcher-infrastructure.md`). When `run()` sets failure directly, it
  skips cursor persistence — the next run retries the same window. For
  `BaseGitFetcher`, removing the old step 11 (safety check) is safe
  because `run()` intercepts the all-items-failed condition after
  `execute()` returns: although the new step 11 always sets
  `self._cursor`, `run()` does not persist it when the final status is
  `failure`.
- **Dashboard visibility**: `failure` is more prominent than `partial` in the
  fetcher dashboard, which is the desired behavior for a total failure.
- **Error fields**: `error_message` is populated with the concise message.
  `error_detail` and `error_traceback` are NULL (no exception was raised,
  so there is no raw exception or traceback to capture). Per-item failure
  details are available in application logs (each fetcher logs per-item
  errors before calling `record_failed()`).

## Origin

Identified during CISA KEV fetcher draft review (Session 5, 2026-06-20).
The question arose when analyzing the impact of 1600 consecutive per-entry
failures with DB down: the fetcher would complete as `partial` with
`items_failed=1600, items_updated=0`, which is semantically a `failure`.

---

## Application Plan

This section contains all the specification changes required to implement
this decision. Each change is self-contained and can be applied
independently. Apply them in order.

### Change 1: `BaseFetcher.run()` lifecycle — status determination

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: lines 71 and 78-82 (inside "Run lifecycle management"
numbered list, item 2)

**Current text (line 71)**:

```
        - Final status set to `success` or `partial` (if `items_failed > 0`)
```

**Replace with**:

```
        - Final status determined by status precedence rules (see below)
```

**Current text (lines 78-82)**:

```
   - **Status determination precedence**: if `execute()` raises an exception,
     the run status is always `failure` regardless of metric counters
     (`items_failed`, `items_created`, `items_updated` are preserved in the
     record for diagnostic purposes but do not influence the final status).
     The `partial` status is assigned only when `execute()` returns normally
     and `items_failed > 0`
```

**Replace with**:

```
   - **Status determination precedence**: the final status is assigned as
     follows (evaluated in order):
     1. If `execute()` raises an exception: `failure`. Metric counters are
        preserved for diagnostics but do not influence the status
     2. If `execute()` returns normally and all items failed
        (`items_failed > 0` and `items_created + items_updated == 0`):
        `failure`. `error_message` is set to
        `"All {items_failed} items failed"`. `error_detail` and
        `error_traceback` are NULL (no exception). The cursor is NOT
        persisted (same behavior as exception-driven failure)
     3. If `execute()` returns normally and `items_failed > 0` (with at
        least one item created or updated): `partial`
     4. Otherwise: `success`
```

### Change 2: `FetcherRunStatus.partial` description

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: line 2643

**Current text**:

```
| `partial` | Completed but some items failed (`items_failed > 0`). Implies `execute()` returned normally (no exception raised) |
```

**Replace with**:

```
| `partial` | Completed but some items failed (`items_failed > 0`) and at least one item succeeded (`items_created + items_updated > 0`). Implies `execute()` returned normally (no exception raised) |
```

### Change 3: Remove step 11 from `BaseGitFetcher.execute()` template method

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: lines 2052-2059 (step 11 and step 12 inside "Template
Method: `execute()`")

**Current text**:

```
11. **Safety check**: if `items_failed > 0` AND
    `items_created + items_updated == 0`, raise `RuntimeError` ("All
    {N} items failed — cursor not advanced for safety"). This prevents
    cursor advance when every item failed (e.g., network drops in
    blobless clone making every `show_file()` fail). Note: items
    skipped in step 10b (file not at HEAD) do not increment any
    counter and do not contribute to the safety check trigger
12. Set cursor to `{"sha": head_sha, "committed_at": head_date}`
```

**Replace with**:

```
11. Set cursor to `{"sha": head_sha, "committed_at": head_date}`

Note: the all-items-failed safety check (preventing cursor advance when
every item fails) is handled by `BaseFetcher.run()` after `execute()`
returns — see "Status determination precedence" in the BaseFetcher
section. Items skipped in step 10b (file not at HEAD) do not increment
any counter and do not trigger the safety check.
```

### Change 4: Update `BaseGitFetcher` Status Determination table

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: lines 2095-2102 (Status Determination table)

**Current text**:

```
| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 12) |
| All items succeed | `success` | Yes (step 12) |
| Some items fail, some succeed | `partial` | Yes (step 12) |
| All items fail (safety check) | `failure` | No (step 11) |
| Infrastructure error | `failure` | No (propagates) |
```

**Replace with**:

```
| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 11) |
| All items succeed | `success` | Yes (step 11) |
| Some items fail, some succeed | `partial` | Yes (step 11) |
| All items fail | `failure` | No (`BaseFetcher.run()` safety check) |
| Infrastructure error | `failure` | No (propagates) |
```

### Change 5: Update `BaseGitFetcher` safety check explanation

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: lines 2083-2088 (paragraph explaining the safety check)

**Current text**:

```
The **safety check** (step 11) prevents a dangerous edge case: if all
items fail (e.g., network drops after fetch in a blobless clone, making
every `show_file()` fail), the cursor must NOT advance — otherwise those
items are permanently lost. The `RuntimeError` causes `BaseFetcher.run()`
to record `status = failure` and preserve the previous cursor, so the
next run retries the same delta.
```

**Replace with**:

```
The **all-items-failed safety check** is now handled by
`BaseFetcher.run()` (see "Status determination precedence" in the
BaseFetcher section). If all items fail (e.g., network drops after fetch
in a blobless clone, making every `show_file()` fail), `run()` sets
`status = failure` directly after `execute()` returns. Since the cursor
is only persisted on `success` or `partial`, the previous cursor is
preserved and the next run retries the same delta. This applies to all
fetcher subclasses uniformly, not just git-based fetchers.
```

### Change 6: Clarify `cursor` column description in `data-model.md`

**File**: `docs/data-model.md`

**Location**: line 1394

**Current text**:

```
| cursor               | JSONB       | nullable                 | Fetcher-defined checkpoint for the next run (e.g., `{"sha": "...", "committed_at": "..."}` for git-based fetchers). Written on successful completion; read by the next run to determine starting point. NULL for fetchers that derive cursors from other fields |
```

**Replace with**:

```
| cursor               | JSONB       | nullable                 | Fetcher-defined checkpoint for the next run (e.g., `{"sha": "...", "committed_at": "..."}` for git-based fetchers). Written when the final run status is `success` or `partial`; read by the next run to determine starting point. NULL for fetchers that derive cursors from other fields |
```

### Change 7: Qualify Cursor Persistence overview bullet

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: lines 72-76 (inside "Run lifecycle management" numbered
list, item 2, bullet "Cursor persistence")

**Current text**:

```
   - **Cursor persistence**: if `execute()` sets `self._cursor` (a dict),
     `run()` writes it to the `FetcherRun.cursor` column in the same
     transaction that sets `status` and `finished_at`. If `self._cursor`
     is None (not set), no cursor is written. See "Git-Based Fetchers —
     Cursor Persistence" for the full mechanism and query pattern
```

**Replace with**:

```
   - **Cursor persistence**: if `execute()` returns normally, the final
     status is `success` or `partial`, and `self._cursor` is set (a
     dict), `run()` writes it to the `FetcherRun.cursor` column in the
     same transaction that sets `status` and `finished_at`. Cursor is
     NOT written when: `self._cursor` is None (not set), `execute()`
     raised an exception (failure path), or the all-items-failed safety
     check triggers (status set to `failure` despite normal return). See
     "Git-Based Fetchers — Cursor Persistence" for the full mechanism
     and query pattern
```

### Change 8: Qualify Write Mechanism section

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: lines 1472-1476 (inside "Cursor Persistence > Write
Mechanism")

**Current text**:

```
Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` reads
`self._cursor` during finalization and writes it to the `FetcherRun`
row in the same transaction that sets `status` and `finished_at`.
If `self._cursor` is None (not set), no cursor is written.
```

**Replace with**:

```
Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` determines the
final status (see "Status determination precedence") and then, only if
the final status is `success` or `partial`, reads `self._cursor` and
writes it to the `FetcherRun` row in the same transaction that sets
`status` and `finished_at`. If `self._cursor` is None (not set), or
the final status is `failure` (including the all-items-failed case),
no cursor is written.
```

---

### Change 9: Update `FetcherRunStatus.failure` description

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: line 2642 (inside "FetcherRunStatus Enum" table)

**Current text**:

```
| `failure` | Terminated with an unhandled exception |
```

**Replace with**:

```
| `failure` | Execution failed. Either: (a) `execute()` raised an unhandled exception, or (b) `execute()` returned normally but all items failed (`items_failed > 0` and `items_created + items_updated == 0`) — see "Status determination precedence" |
```

### Change 10: Update `error_message` column description

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: line 2610 (inside "FetcherRun" data model table)

**Current text**:

```
| error_message | TEXT | nullable | Sanitized error description (for all users). Written explicitly by the fetcher (`FetcherError`) or by BaseFetcher's generic fallback (see "Error Message Sanitization") |
```

**Replace with**:

```
| error_message | TEXT | nullable | Sanitized error description (for all users). Written explicitly by the fetcher (`FetcherError`), by BaseFetcher's generic fallback (see "Error Message Sanitization"), or by the all-items-failed safety check (`"All {N} items failed"` — see "Status determination precedence") |
```

### Change 11: Clarify `cursor` column description

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: line 2615 (inside "FetcherRun" data model table)

**Current text**:

```
| cursor | JSONB | nullable | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written on successful completion; read by the next run to determine the starting point. See "Git-Based Fetchers" for the git-specific usage pattern |
```

**Replace with**:

```
| cursor | JSONB | nullable | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written when the final run status is `success` or `partial`; read by the next run to determine the starting point. See "Git-Based Fetchers" for the git-specific usage pattern |
```

---

## Post-Application Steps

After all specification changes above have been applied:

1. **Run spec-coherence-reviewer**: invoke `@spec-coherence-reviewer` on
   `docs/features/platform/fetcher-infrastructure.md` to verify no
   contradictions were introduced with other specs that reference fetcher
   status semantics (e.g., `fetcher-operations.md`, individual fetcher
   specs).

2. **Run docs-reviewer**: invoke `@docs-reviewer` on
   `docs/features/platform/fetcher-infrastructure.md` and
   `docs/data-model.md` to verify documentation completeness and
   coherence.

3. **Run docs-placement-reviewer**: invoke `@docs-placement-reviewer` on
   `docs/features/platform/fetcher-infrastructure.md` to verify no rules
   were misplaced.

4. **Close OP-14 in `docs/drafts/open-points.md`**: replace the current
   OP-14 section with:

   ```markdown
   ## 14. BaseFetcher All-Items-Failed Safety Check

   **Status**: RESOLVED (YYYY-MM-DD)

   **Resolution**: promoted the all-items-failed safety check from
   `BaseGitFetcher.execute()` (step 11) to `BaseFetcher.run()`. When
   `execute()` returns normally but all items failed (`items_failed > 0`
   and `items_created + items_updated == 0`), `run()` now sets
   `status = failure` directly (no `RuntimeError`). The `partial` status
   is reserved for runs where at least one item succeeded. The redundant
   step 11 in `BaseGitFetcher` was removed and renumbered. See
   `docs/features/platform/fetcher-infrastructure.md` (Status
   determination precedence).
   ```

   (Replace `YYYY-MM-DD` with the actual date of application.)

5. **Delete this draft file**: remove
   `docs/drafts/basefetcher-all-items-failed.md` from the repository.
