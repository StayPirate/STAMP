# Stateless Fetcher Abort Pattern — Omologation

## Summary

This draft proposes omologating the consecutive failure abort pattern
across the three stateless per-CVE fetchers (EPSS, Red Hat, OSV). The
change introduces a shared infrastructure failure classification
function, updates the session lifecycle template in
`cve-fetcher-infrastructure.md`, and aligns all three fetcher specs to
the standardized pattern.

## Motivation

### Current state

Three CVE fetchers (`sync_epss_scores`, `sync_redhat_cves`,
`sync_osv_advisories`) implement a consecutive failure counter that
aborts the batch run after 3 consecutive infrastructure failures. The
implementations are near-identical in intent but diverge in:

1. **Code completeness**: Red Hat describes the counter in prose (error
   handling table, lines 346-357) but does NOT implement it in the
   `execute()` pseudocode (lines 270-287). An implementer reading only
   the code would produce a fetcher without abort logic. Additionally,
   EPSS places its threshold check (`if consecutive_failures >= 3`)
   outside the `except` blocks (at loop-body indentation level, line
   256), meaning it executes on every iteration — including after
   branches that reset the counter to zero. While functionally harmless
   (a just-reset counter never reaches 3), this structural choice
   diverges from the cleaner pattern of checking inside the increment
   branch only.

2. **Exception classification**: EPSS has a separate `ValidationError`
   branch that resets the counter (line 249). Red Hat and OSV have no
   equivalent — `ValidationError` falls into the generic `Exception`
   handler (which increments the counter). Yet OSV's prose (line 386)
   states "Any CVE where Phase 1 returns HTTP 200 resets the counter to
   zero" — contradicting its own code where a parse error on HTTP 200
   would increment.

3. **HTTP 429 semantics**: EPSS's error table (line 309) explicitly
   states HTTP 429 increments the counter. EPSS's own prose (lines
   314-317) says "Only uninterrupted sequences of infrastructure
   failures (HTTP 5xx post-transport-retry, network timeout, DNS error)
   count" — an exhaustive list that excludes 429. Red Hat's prose (line
   350) uses the same exhaustive list (excluding 429). This is an
   internal contradiction in EPSS (finding CSEP-COH-01) and an
   inter-spec inconsistency.

4. **JSONDecodeError**: EPSS does not classify `JSONDecodeError` in the
   counter logic (finding CSEP-GAP-01). A malformed HTTP 200 response
   body falls into the generic `Exception` handler, incrementing the
   counter — yet semantically, HTTP 200 received proves API
   reachability.

5. **Template gap**: the session lifecycle template 1 in
   `cve-fetcher-infrastructure.md` (lines 275-292) contains no
   consecutive failure counter. The infrastructure spec delegates abort
   logic to individual fetcher specs (lines 792-796), but provides no
   recommended pattern, increasing the risk of divergent
   implementations.

6. **Missing context**: only EPSS includes a transport-level retry note
   before its error tables (lines 280-284). Red Hat and OSV lack this
   context, leaving implicit whether tables describe pre- or
   post-transport behavior.

7. **Scope snapshot**: only EPSS explicitly declares that the CVE-ID set
   is queried once at the start of `execute()` (line 50). Red Hat and
   OSV imply this but do not state it.

### Why this matters

These inconsistencies mean that an implementer would produce different
abort behavior depending on which spec they read first, or which parts
of a spec they prioritize (code vs. prose). The consecutive failure
counter exists for a single, well-defined purpose: detecting that the
external API is unreachable and aborting early to avoid wasting time on
doomed requests. The classification of what constitutes "unreachable"
should be uniform and unambiguous across all fetchers that use this
pattern.

## Design

### Core principle

The consecutive failure counter answers one question: **"Is the external
API reachable?"**

- If the server sent ANY HTTP response (200, 404, 429, or any other
  status code that survived transport-level retry): the API is reachable
  → **reset** the counter
- If NO HTTP response was received after transport-level retry
  exhaustion (connection error, timeout, DNS failure): the API is
  unreachable → **increment** the counter
- HTTP 5xx is included in "increment" because 4 consecutive 5xx
  responses (after 4 transport-level attempts each = 16 total failed
  requests) signal a server in persistent fault state — functionally
  equivalent to unreachability for the purpose of this counter

### Classification function

A shared utility function encapsulates the classification logic.

**Module**: `backend/app/services/http_client.py` (alongside the
existing `create_http_client()` factory)

**Specification**:

```
is_infrastructure_failure(exception) → bool

Returns True if the exception indicates the external API is unreachable
or persistently failing (infrastructure failure). Returns False for all
other exceptions (data-quality errors, business-logic errors, parse
errors), which indicate the API responded but the response was unusable.

Classification rules:
  - Connection error (any variant): True
  - Timeout (connect, read, write, pool): True
  - HTTP status error with status_code >= 500: True
  - All other exceptions: False

This function operates on post-transport-retry exceptions only. By the
time an exception reaches this function, the transport layer has already
exhausted its retry budget (4 attempts with 1s/2s/4s backoff for 5xx
and connection/timeout errors).
```

**Behavioral notes**:

- HTTP 429 post-transport → returns `False` (the server responded; rate
  limiting proves reachability)
- `JSONDecodeError` → returns `False` (HTTP 200 was received; body
  corruption is a data-quality issue)
- `ValidationError` (Pydantic) → returns `False` (HTTP 200 with valid
  JSON but non-conforming schema)
- Database errors (`OperationalError`) → returns `False` (not an
  external API failure; handled by the all-items-failed safety check in
  `BaseFetcher.run()`)
- Programming bugs (`AttributeError`, `KeyError`, etc.) → returns
  `False` (not infrastructure; sporadic bugs should not trigger abort)

### Updated session lifecycle template

The session lifecycle template 1 ("When `execute()` delegates to
`fetch_single()` in a loop") in `cve-fetcher-infrastructure.md` is
updated to include the consecutive failure counter and the
classification call:

```python
async def execute(self, session: AsyncSession) -> None:
    """Stateless per-CVE batch: iterate over CVEs with active tickets.

    Scope snapshot: the in-scope CVE-ID set is queried once at the
    start of execute(). New tickets created mid-run are covered by
    the default catch_up() mechanism and on-demand fetch_single().
    """
    cve_ids = await self._get_active_ticket_cve_ids(session)
    consecutive_failures = 0
    for cve_id in cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
            consecutive_failures = 0
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except CVENotInSource:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "missing")
            consecutive_failures = 0  # API responded — clean skip
        except Exception as e:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            logger.warning("Failed to process item %s: %s", cve_id, e)
            self.record_failed()
            if is_infrastructure_failure(e):
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise FetcherError(
                        f"{self.description} unreachable"
                        f" — 3 consecutive failures"
                    )
            else:
                consecutive_failures = 0  # API responded — data error
        await asyncio.sleep(self.config.request_delay)
```

**Key differences from the previous template (lines 275-292)**:

1. Added `consecutive_failures` counter with initialization
2. Added scope snapshot docstring and explicit query call
3. Added `is_infrastructure_failure(e)` classification in the `except
   Exception` block
4. Added counter reset on data-quality exceptions (`else` branch)
5. Added abort via `FetcherError` at threshold 3
6. Counter resets on success (line: `consecutive_failures = 0`)
7. Counter resets on `CVENotInSource` (with comment)

**What does NOT change**:

- `SoftTimeLimitExceeded`/`MemoryError` re-raise (unchanged)
- `CVENotInSource` handling (rollback + isolated status commit)
- `record_failed()` call for all non-`CVENotInSource` exceptions
- `asyncio.sleep(self.config.request_delay)` between iterations
- `fetch_single()` + `commit_and_dispatch()` pattern

### Template applicability note

This template is recommended for **stateless per-CVE fetchers** that
iterate over a set of CVE-IDs making one HTTP request per CVE via
`fetch_single()`. It is NOT applicable to:

- Paginated fetchers (NVD, GHSA) — which use page-level abort
- Git-based fetchers (MITRE, Kernel) — which use `BaseGitFetcher`
- Catalog fetchers (KEV) — which download a single file

Individual fetcher specs MAY add source-specific exception branches
between `CVENotInSource` and the generic `Exception` handler (e.g., for
source-specific response parsing that benefits from distinct error
reporting). Any such branch MUST document its effect on the consecutive
failure counter (reset or no-op — never increment, since these are
reached only when the API has responded).

### Consecutive failure abort — prose specification

The following prose replaces the current one-sentence delegation at
`cve-fetcher-infrastructure.md` lines 792-796 and provides a complete
specification of the abort pattern:

> **Consecutive failure abort** (stateless per-CVE fetchers):
>
> Stateless fetchers that iterate over CVE-IDs with one HTTP request
> per CVE implement a consecutive failure counter to detect sustained
> API unreachability and abort early. The pattern is embedded in session
> lifecycle template 1 (above).
>
> **Counter semantics**:
>
> - **Reset to zero** after any evidence of API reachability: successful
>   fetch, clean skip (`CVENotInSource`), or any exception where
>   `is_infrastructure_failure()` returns `False` (HTTP 200 received
>   with bad data, HTTP 429 rate limit, parse error, etc.)
> - **Increment** after infrastructure failures only: exceptions where
>   `is_infrastructure_failure()` returns `True` (connection error,
>   timeout, HTTP 5xx — all post-transport-retry exhaustion)
> - **Abort** when the counter reaches 3: raise `FetcherError` with a
>   sanitized message identifying the API and the threshold
>
> The classification is universal — all stateless per-CVE fetchers use
> `is_infrastructure_failure()` without per-fetcher customization. The
> function's definition is the single source of truth for what
> constitutes an infrastructure failure.
>
> **Transport-level retry context**: errors reaching the fetcher have
> already exhausted transport-level retry (4 attempts with 1s/2s/4s
> backoff for 5xx and connection/timeout; 1 guided retry for 429/503
> with Retry-After ≤ 120s). A single infrastructure failure at the
> fetcher level represents 4 failed HTTP attempts. Three consecutive
> infrastructure failures represent 12 failed HTTP attempts total.
>
> **Interaction with other mechanisms**:
>
> - `SoftTimeLimitExceeded`: bypasses the counter entirely (re-raised
>   before reaching any counter logic)
> - All-items-failed safety check: complementary mechanism in
>   `BaseFetcher.run()`. Catches systematic failures (database down,
>   universal code bugs) that reset the counter on every iteration but
>   fail every item. The two mechanisms cover different failure modes
> - `BaseFetcher` Celery task timeout: provides an absolute time bound
>   regardless of counter behavior

### What this does NOT introduce

- No new base class or intermediate subclass
- No new abstract method, hook, or override point on `BaseCVEFetcher`
- No `abort_threshold` attribute on any base class (the threshold 3 is
  part of the template, not a configurable parameter)
- No external dependency (the classification function is ~5 lines of
  standard Python using httpx exception types already imported by the
  HTTP client module)

## Spec changes — detailed plan

### Overview of affected files

| File | Type of change |
|------|----------------|
| `docs/features/platform/cve-fetcher-infrastructure.md` | Template update + abort pattern documentation |
| `docs/features/platform/networking.md` | Add classification function specification |
| `docs/features/tickets/cve-sync-epss.md` | Align to template; resolves 3 findings |
| `docs/features/tickets/cve-sync-redhat.md` | Align to template; add counter to code |
| `docs/features/tickets/cve-sync-osv.md` | Align to template; resolve prose/code inconsistency |

### Findings resolved by this change

| Finding | Spec | Resolution |
|---------|------|------------|
| CSEP-GAP-01 | cve-sync-epss | JSONDecodeError classified via `is_infrastructure_failure()` → returns False → counter resets |
| CSEP-COH-01 | cve-sync-epss | HTTP 429 contradiction resolved: 429 resets counter (prose was correct, table was wrong) |
| CSEP-COH-02 | cve-sync-epss | Separate fix: update README.md status label |
| CSEP-COH-03 | cve-sync-epss | Separate fix: correct cross-reference path in data-sources.md |

Note: CSEP-COH-02 and CSEP-COH-03 are unrelated to the abort pattern
but should be fixed in the same session since we are modifying the EPSS
spec.

---

## Action Plan

### Step 1: Add `is_infrastructure_failure()` to networking.md

**File**: `docs/features/platform/networking.md`

**Location**: after the "Transport-Level Retry" section (which ends
around line 220), add a new subsection.

**Add**:

```markdown
#### Infrastructure Failure Classification

The `is_infrastructure_failure()` function classifies post-transport
exceptions as either infrastructure failures (API unreachable) or
data-quality errors (API responded but data is unusable). It is used
by stateless per-CVE fetchers to drive the consecutive failure abort
counter (see `cve-fetcher-infrastructure.md`, session lifecycle
template 1).

**Location**: `backend/app/services/http_client.py`

**Signature**: `is_infrastructure_failure(exception: Exception) → bool`

**Classification rules**:

| Condition | Returns | Rationale |
|-----------|---------|-----------|
| Connection error (any variant) | `True` | No HTTP response received |
| Timeout (connect, read, write, pool) | `True` | No HTTP response received |
| HTTP status error with status >= 500 | `True` | Server persistently failing (post-transport-retry) |
| HTTP status error with status 429 | `False` | Server responded — rate limiting proves reachability |
| HTTP status error with other 4xx | `False` | Server responded — client error |
| `JSONDecodeError` | `False` | HTTP 200 received — body corruption is data-quality |
| `ValidationError` (Pydantic) | `False` | HTTP 200 received — schema mismatch is data-quality |
| Any other exception | `False` | Not an external API infrastructure issue |

**Post-transport context**: this function operates on exceptions that
have already exhausted transport-level retry. A `True` result means
the transport layer attempted 4 requests (with 1s/2s/4s backoff) and
all failed — the API is conclusively unreachable or failing.

**Consumers**: stateless per-CVE fetchers (`sync_epss_scores`,
`sync_redhat_cves`, `sync_osv_advisories`). Not used by paginated,
git-based, or catalog fetchers.
```

### Step 2: Update session lifecycle template in cve-fetcher-infrastructure.md

**File**: `docs/features/platform/cve-fetcher-infrastructure.md`

**Change 2a**: Replace the current template 1 code block (lines
275-292) with the updated template from the "Updated session lifecycle
template" section of this draft. Preserve the surrounding prose context
(the paragraph before and after the code block).

**Change 2b**: Add the following note immediately after the template
code block (before template 2):

```markdown
**Template applicability**: this template is recommended for stateless
per-CVE fetchers that iterate over a set of CVE-IDs making one HTTP
request per CVE via `fetch_single()`. Paginated (NVD, GHSA),
git-based (MITRE, Kernel), and catalog (KEV) fetchers use different
patterns and do not include the consecutive failure counter.

**Transport-level retry context**: errors reaching the fetcher have
already exhausted transport-level retry (4 attempts with 1s/2s/4s
backoff for 5xx and connection/timeout errors; 1 guided retry for
429/503 with Retry-After ≤ 120s). The error handling in the template
documents post-transport behavior only.

**Source-specific exception branches**: individual fetcher specs MAY
add exception branches between `CVENotInSource` and the generic
`Exception` handler for source-specific error reporting. Any such
branch MUST reset the consecutive failure counter (these branches are
reached only when the API has responded — otherwise the exception
would be an infrastructure failure caught by the generic handler).
```

**Change 2c**: Replace the current one-sentence abort delegation at
lines 792-796 with the full "Consecutive failure abort" prose
specification from the Design section of this draft. Keep the preceding
paragraph (lines 785-791 about log level rationale) and the following
paragraph (lines 798-onwards about distinction from on-demand path)
unchanged.

**Change 2d**: In the "Batch Error Handling" section, after the
numbered list (steps 1-3) at lines 762-774, add a reference to the
classification function:

```markdown
The distinction between infrastructure failures and data-quality
errors — which determines whether the consecutive failure counter
increments or resets — is made by `is_infrastructure_failure()` (see
`docs/features/platform/networking.md`, "Infrastructure Failure
Classification"). Fetchers do not implement this classification
individually.
```

### Step 3: Align EPSS spec to template

**File**: `docs/features/tickets/cve-sync-epss.md`

**Change 3a**: Replace the `execute()` code block (lines 228-271) with
the updated version. The EPSS-specific additions beyond the template
are:

- The staleness check (lines 260-266 of the current spec) — preserve
  this after the counter logic, before the sleep. It is purely
  diagnostic and does not affect the counter.
- EPSS does NOT need a separate `except ValidationError` branch anymore.
  The omologated pattern handles `ValidationError` via
  `is_infrastructure_failure()` returning `False` → counter resets in
  the `else` branch. Remove the dedicated `ValidationError` except
  clause.

The updated EPSS `execute()` code block:

```python
async def execute(self, session: AsyncSession) -> None:
    """Periodic batch: iterate over CVEs with active tickets.

    Scope snapshot: the in-scope CVE-ID set is queried once at the
    start of execute(). New tickets created mid-run are covered by
    the default catch_up() mechanism and on-demand fetch_single().
    """
    cve_ids = await self._get_active_ticket_cve_ids(session)
    staleness_checked = False
    consecutive_failures = 0
    for cve_id in cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
            consecutive_failures = 0
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except CVENotInSource:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "missing")
            consecutive_failures = 0  # API responded — clean skip
        except Exception as e:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            logger.warning("Failed to process item %s: %s", cve_id, e)
            self.record_failed()
            if is_infrastructure_failure(e):
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise FetcherError(
                        "EPSS API unreachable"
                        " — 3 consecutive failures"
                    )
            else:
                consecutive_failures = 0  # API responded — data error
        # Staleness check: diagnostic only, never affects metrics or abort
        if not staleness_checked and self._last_assessed_date is not None:
            try:
                self._check_staleness(self._last_assessed_date)
            except Exception:
                pass  # staleness is purely diagnostic
            staleness_checked = True
        await asyncio.sleep(self.config.request_delay)

    # catch_up(ticket_id) — inherited from BaseCVEFetcher default:
    #   extracts cve_id from ticket → calls self.fetch_single(cve_id)
```

**Change 3b**: Replace the "Consecutive failure abort" subsection (lines
123-133) with:

```markdown
**Consecutive failure abort**:

A counter tracks consecutive infrastructure failures — exceptions
where `is_infrastructure_failure()` returns `True` (connection error,
timeout, HTTP 5xx — all post-transport-retry). The counter resets to
zero after any evidence of API reachability: successful fetch, clean
skip (`CVENotInSource`), or any exception where
`is_infrastructure_failure()` returns `False` (HTTP 429, parse error,
`ValidationError`, `JSONDecodeError`, etc.).

If the counter reaches 3, abort the entire run with `FetcherError`:
`"EPSS API unreachable — 3 consecutive failures"`.

See `cve-fetcher-infrastructure.md` (session lifecycle template 1) for
the canonical pattern and `networking.md` ("Infrastructure Failure
Classification") for the classification function specification.
```

**Change 3c**: Update the `execute()` error handling table (lines
298-310). Replace with:

| Condition | Action |
|-----------|--------|
| HTTP 200 with valid score | Upsert, `record_updated`, reset failure counter |
| HTTP 200 with `data: []` | Silent skip (not a failure — CVE not yet scored), reset failure counter |
| HTTP 200 with invalid data (`ValidationError`, `JSONDecodeError`) | `record_failed`, reset failure counter (API reachable — data-quality issue) |
| HTTP 429 (post-transport) | `record_failed`, reset failure counter (API reachable — rate limiting) |
| HTTP 5xx (post-transport) | `record_failed`, increment failure counter |
| Network timeout / DNS / connection error | `record_failed`, increment failure counter |
| 3 consecutive infrastructure failures | Abort entire run with `FetcherError` |

**Change 3d**: Update the post-table prose (lines 312-327). Replace
lines 312-317 with:

```markdown
The consecutive failure counter uses `is_infrastructure_failure()` to
classify exceptions. Only infrastructure failures (connection error,
timeout, HTTP 5xx post-transport-retry) increment the counter. All
other exceptions — including HTTP 429, `JSONDecodeError`,
`ValidationError`, and any other error where the API demonstrably
responded — reset the counter to zero.

Transport-level retry context: errors reaching the fetcher have already
exhausted transport-level retry (4 attempts with 1s/2s/4s backoff for
5xx and connection/timeout; 1 guided retry for 429/503 with
Retry-After ≤ 120s). A single infrastructure failure at the fetcher
level represents 4 failed HTTP attempts. Three consecutive failures
represent 12 failed attempts total.
```

**Change 3e**: In the sanitized messages table (lines 329-337), update
the `ValidationError` row to include `JSONDecodeError`:

| Failure mode | `FetcherError` message |
|---|---|
| Connection error | `"Failed to connect to FIRST.org EPSS API"` |
| HTTP 5xx | `"FIRST.org EPSS API returned HTTP {status_code}"` |
| Persistent infra failure | `"EPSS API unreachable — 3 consecutive failures"` |
| Data-quality error (`ValidationError` / `JSONDecodeError`) | `"EPSS API returned invalid data for {cve_id}"` |

**Change 3f**: In the `fetch_single()` error handling table (lines
286-296), add a row for `JSONDecodeError`:

| HTTP 200 with non-JSON response body (`JSONDecodeError`) | No | `failure` | Non-retryable — data-quality error (API reachable); log and fail |

Insert this row after the existing `ValidationError` row.

**Change 3g**: Add a cross-reference to the infrastructure failure
classification in the Cross-references section (if not already present):

```markdown
- `docs/features/platform/networking.md` — Infrastructure Failure
  Classification (`is_infrastructure_failure()`)
```

### Step 4: Align Red Hat spec to template

**File**: `docs/features/tickets/cve-sync-redhat.md`

**Change 4a**: Replace the `execute()` code block (lines 270-290) with
the template-aligned version:

```python
async def execute(self, session: AsyncSession) -> None:
    """Periodic batch: iterate over CVEs with active tickets.

    Scope snapshot: the in-scope CVE-ID set is queried once at the
    start of execute(). New tickets created mid-run are covered by
    the default catch_up() mechanism and on-demand fetch_single().
    """
    cve_ids = await self._get_active_ticket_cve_ids(session)
    consecutive_failures = 0
    for cve_id in cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
            consecutive_failures = 0
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except CVENotInSource:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "missing")
            consecutive_failures = 0  # API responded — clean skip
        except Exception as e:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            logger.warning("Failed to process item %s: %s", cve_id, e)
            self.record_failed()
            if is_infrastructure_failure(e):
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise FetcherError(
                        "Red Hat Security Data API unreachable"
                        " — 3 consecutive failures"
                    )
            else:
                consecutive_failures = 0  # API responded — data error
        await asyncio.sleep(self.config.request_delay)

    # catch_up(ticket_id) — inherited from BaseCVEFetcher default:
    #   extracts cve_id from ticket → calls self.fetch_single(cve_id)
```

**Change 4b**: Update the `execute()` error handling table (lines
332-346). Replace with:

| Condition | Action |
|-----------|--------|
| HTTP 200 with extractable data | Upsert, `record_updated`, reset failure counter |
| HTTP 200 with no extractable data | Skip CVE, no metric (not a failure), reset failure counter |
| HTTP 404 | Skip CVE, no metric (not a failure), reset failure counter |
| HTTP 200 with unparseable JSON (`JSONDecodeError`) | `record_failed`, reset failure counter (API reachable — data-quality issue) |
| HTTP 200 with invalid CVSS vector | Partial extraction: skip vector, upsert remaining data, `record_updated` if any data saved; log WARNING. Reset failure counter |
| HTTP 429 (post-transport) | `record_failed`, reset failure counter (API reachable — rate limiting) |
| HTTP 5xx (post-transport) | `record_failed`, increment failure counter |
| Network timeout / DNS / connection error | `record_failed`, increment failure counter |
| 3 consecutive infrastructure failures | Abort entire run with `FetcherError` |

**Change 4c**: Replace the post-table prose (lines 348-357) with:

```markdown
The consecutive failure counter uses `is_infrastructure_failure()` to
classify exceptions. Only infrastructure failures (connection error,
timeout, HTTP 5xx post-transport-retry) increment the counter. All
other exceptions — including HTTP 429, `JSONDecodeError`, invalid CVSS
vectors (partial extraction), and any other error where the API
demonstrably responded — reset the counter to zero.

Transport-level retry context: errors reaching the fetcher have already
exhausted transport-level retry (4 attempts with 1s/2s/4s backoff for
5xx and connection/timeout; 1 guided retry for 429/503 with
Retry-After ≤ 120s).

The batch run **never aborts on a single CVE failure** — it continues
to the next CVE after recording the failure. The only abort condition
is persistent infrastructure failure (3 consecutive errors where
`is_infrastructure_failure()` returns `True`).

See `cve-fetcher-infrastructure.md` (session lifecycle template 1) for
the canonical pattern and `networking.md` ("Infrastructure Failure
Classification") for the classification function specification.
```

**Change 4d**: In the `fetch_single()` error handling table (lines
301-311), add a `JSONDecodeError` row (if not already distinct from
"unparseable JSON"):

The existing row "HTTP 200 with unparseable JSON" already covers this
case. Verify it is present and that the description says "Non-retryable
— entire response is unusable" (data-quality, not infrastructure). No
change needed if already correct.

**Change 4e**: Add cross-reference to networking.md (Infrastructure
Failure Classification) in the Cross-references section.

### Step 5: Align OSV spec to template

**File**: `docs/features/tickets/cve-sync-osv.md`

**Change 5a**: Replace the `execute()` code block (lines 308-336) with
the template-aligned version:

```python
async def execute(self, session: AsyncSession) -> None:
    """Periodic batch: iterate over CVEs with active tickets.

    Scope snapshot: the in-scope CVE-ID set is queried once at the
    start of execute(). New tickets created mid-run are covered by
    the default catch_up() mechanism and on-demand fetch_single().
    """
    cve_ids = await self._get_active_ticket_cve_ids(session)
    consecutive_failures = 0
    for cve_id in cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
            consecutive_failures = 0
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except CVENotInSource:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "missing")
            consecutive_failures = 0  # API responded — clean skip
        except Exception as e:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            logger.warning("Failed to process item %s: %s", cve_id, e)
            self.record_failed()
            if is_infrastructure_failure(e):
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise FetcherError(
                        "OSV API unreachable"
                        " — 3 consecutive failures"
                    )
            else:
                consecutive_failures = 0  # API responded — data error
        await asyncio.sleep(self.config.request_delay)

    # catch_up(ticket_id) — inherited from BaseCVEFetcher default:
    #   extracts cve_id from ticket → calls self.fetch_single(cve_id)
```

**Change 5b**: Replace the "Abort threshold semantics" paragraph (lines
382-386) with:

```markdown
**Abort threshold semantics**: the consecutive failure counter uses
`is_infrastructure_failure()` to classify exceptions. Only Phase 1
infrastructure failures (connection error, timeout, HTTP 5xx
post-transport-retry) increment the counter. All other exceptions
reset the counter — including HTTP 200 with parse errors, HTTP 404
(CVENotInSource), and completeness guard failures (Phase 2/3 issues
with successful Phase 1). Any CVE where Phase 1 returns any HTTP
response resets the counter to zero.

See `cve-fetcher-infrastructure.md` (session lifecycle template 1) for
the canonical pattern and `networking.md` ("Infrastructure Failure
Classification") for the classification function specification.
```

**Change 5c**: Update the `execute()` error handling table (lines
373-380). Replace with:

| Condition | Action |
|-----------|--------|
| Phase 1 HTTP 200, upsert succeeds | `record_updated()`, reset failure counter |
| HTTP 200 with no extractable data | Skip CVE, no metric, reset failure counter |
| HTTP 404 | Skip CVE, no metric, reset failure counter |
| Phase 1 HTTP 200 with parse error (`JSONDecodeError`, `ValidationError`) | `record_failed()`, reset failure counter (API reachable — data-quality issue) |
| Completeness guard triggers (Phase 2/3 all-fail) | `record_failed()`, reset failure counter (Phase 1 succeeded — API is reachable) |
| Phase 1 HTTP 5xx / network error (post-transport) | `record_failed()`, increment failure counter |
| 3 consecutive Phase 1 infrastructure failures | Abort entire run with `FetcherError` |

**Change 5d**: Add cross-reference to networking.md (Infrastructure
Failure Classification) in the Cross-references section.

### Step 6: Fix CSEP-COH-02 (README status label)

**File**: `docs/features/tickets/README.md`

Change the EPSS entry from "EPSS fetcher (planned)" to "EPSS fetcher"
(or align with whatever label format the other complete specs use in
that README). Verify by reading the current file and matching the
format of other entries marked as complete.

### Step 7: Fix broken cross-reference paths in data-sources.md

**File**: `docs/data-sources.md`

Six per-source sections contain links pointing to
`features/platform/cve-sync-*.md` but the files reside in
`features/tickets/`. The Fetcher Registry table in the same document
already has the correct paths — only the per-source section links are
wrong.

Replace `features/platform/` with `features/tickets/` in each of these
links:

| Line | Current (broken) | Corrected |
|------|-----------------|-----------|
| 69 | `features/platform/cve-sync-nvd.md` | `features/tickets/cve-sync-nvd.md` |
| 110 | `features/platform/cve-sync-redhat.md` | `features/tickets/cve-sync-redhat.md` |
| 132 | `features/platform/cve-sync-kev.md` | `features/tickets/cve-sync-kev.md` |
| 154 | `features/platform/cve-sync-epss.md` | `features/tickets/cve-sync-epss.md` |
| 180 | `features/platform/cve-sync-ghsa.md` | `features/tickets/cve-sync-ghsa.md` |
| 281 | `features/platform/cve-sync-osv.md` | `features/tickets/cve-sync-osv.md` |

This resolves CSEP-COH-03 (which identified only the EPSS link) and
fixes the same issue for the other 5 fetcher specs.

### Step 8: Update review findings

After all spec changes are applied, mark the following findings as
RESOLVED in `docs/reviews/cve-sync-epss.md`:

- **CSEP-GAP-01**: RESOLVED — JSONDecodeError classified via
  `is_infrastructure_failure()` returning False; counter resets on data-
  quality errors (omologation applied)
- **CSEP-COH-01**: RESOLVED — HTTP 429 counter conflict resolved: 429
  returns `is_infrastructure_failure() = False`, counter resets (prose
  was correct, table aligned)
- **CSEP-COH-02**: RESOLVED — README.md status label corrected
- **CSEP-COH-03**: RESOLVED — Cross-reference paths corrected in
  data-sources.md (6 links fixed: NVD, Red Hat, KEV, EPSS, GHSA, OSV)

Update `.tracking.json` cache for `cve-sync-epss` (open counts go to
zero, resolved count increases by 4).

Update `docs/reviews/README.md` accordingly.

### Step 9: Run reviewers on affected specs

Execute the following reviewers to verify the changes were applied
correctly and without introducing new issues:

| Spec | Reviewers to run |
|------|-----------------|
| `cve-fetcher-infrastructure` | `spec-gap-analyzer`, `spec-coherence-reviewer` |
| `networking` | `spec-gap-analyzer`, `spec-coherence-reviewer` |
| `cve-sync-epss` | `spec-gap-analyzer`, `spec-coherence-reviewer` |
| `cve-sync-redhat` | `spec-gap-analyzer`, `spec-coherence-reviewer` |
| `cve-sync-osv` | `spec-gap-analyzer`, `spec-coherence-reviewer` |

Rationale:
- `spec-gap-analyzer`: verify that the updated specs remain complete
  (no new gaps introduced by the template change)
- `spec-coherence-reviewer`: verify that the specs are mutually
  consistent after the cross-cutting change (no new contradictions
  between the updated specs and the rest of the documentation)

If reviewers identify issues, resolve them before considering the
change complete.

### Step 10: Delete this draft

Once all changes are applied, reviewed, and verified, delete this file:
`docs/drafts/stateless-fetcher-abort-omologation.md`

---

## Coherence verification checklist

Before applying this plan, verify these cross-cutting consistency
requirements:

- [ ] The `is_infrastructure_failure()` classification in networking.md
  matches the counter semantics described in
  cve-fetcher-infrastructure.md
- [ ] All three fetcher specs reference the same classification function
  and describe the same counter behavior
- [ ] The sanitized `FetcherError` messages in each fetcher spec match
  the pattern `"{API display name} unreachable — 3 consecutive
  failures"`
- [ ] The execute() code blocks in all three fetcher specs follow the
  same structural pattern (same branch order, same comments, same
  counter logic)
- [ ] The error handling tables in all three fetcher specs use
  consistent terminology ("reset failure counter", "increment failure
  counter", "API reachable — data-quality issue")
- [ ] The cross-references in all three fetcher specs include
  networking.md and cve-fetcher-infrastructure.md
- [ ] No other spec in the project references the old EPSS-specific
  counter semantics (grep for "data-quality error on HTTP 200" and
  "Only uninterrupted sequences" in case other specs quote the old
  prose)
- [ ] All three fetcher `execute()` code blocks include the explicit
  scope snapshot query `cve_ids = await
  self._get_active_ticket_cve_ids(session)` and the scope snapshot
  docstring (addresses Motivation item 7)
- [ ] Red Hat and OSV specs now include the transport-level retry
  context note in their post-table prose (addresses Motivation item 6)
- [ ] All four CSEP findings are marked RESOLVED in
  `docs/reviews/cve-sync-epss.md` with compact format, and
  `.tracking.json` cache is updated (open counts = 0)
- [ ] The scope snapshot docstring ("Scope snapshot: the in-scope
  CVE-ID set is queried once at the start of execute()...") appears in
  all three fetcher `execute()` methods
