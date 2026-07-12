# Centralize Celery Task Retry Classification

## Status

Draft — pending review

## Problem Statement

Three Celery task wrappers in Sentinel independently classify exceptions to decide whether to retry or fail immediately. Each uses a different mechanism, leading to inconsistencies:

| Task wrapper | Current mechanism | Approach |
|---|---|---|
| `fetch_single_cve` | Enumerated HTTP conditions inline | Whitelist: "network, 5xx, timeout, 429 = retry; catch-all = non-retryable" |
| `run_catch_up` | Explicit non-retryable exception set | Blacklist: "retry everything EXCEPT `NotImplementedError`, `CVENotInSource`, `ValueError`" |
| `correlate_submission_request` | Informal: "unreachable / timeout" and "4xx/5xx" in error table | Catch-all: retries all HTTP errors (including permanent 4xx) |

### Inconsistency

`run_catch_up` claims to use "the same retry policy as `fetch_single_cve`" but its blacklist mechanism produces different results. `correlate_submission_request` has a similar over-retry problem with its informal catch-all approach:

| Condition | `fetch_single_cve` (retry?) | `run_catch_up` (retry?) | `correlate_submission_request` (retry?) |
|---|---|---|---|
| Network error / 5xx / timeout | Yes | Yes | Yes |
| HTTP 429 | Yes | Yes | **Yes** (4xx catch-all) |
| HTTP 403 | **No** (catch-all) | **Yes** (not in blacklist) | **Yes** (4xx catch-all) |
| `JSONDecodeError` on HTTP 200 | **No** (catch-all) | **Yes** (not in blacklist) | **Unspecified** |
| `ValidationError` on HTTP 200 | **No** (catch-all) | **Yes** (not in blacklist) | **Unspecified** |

`run_catch_up` and `correlate_submission_request` both waste retry attempts on permanent errors (HTTP 403, other 4xx) that `fetch_single_cve` correctly classifies as non-retryable. `correlate_submission_request` additionally has unspecified behavior for non-HTTP errors (parsing failures).

## Proposed Solution

### New function: `is_retryable_condition()`

Add a new classification function in the same module as `is_infrastructure_failure()` (`backend/app/services/http_client.py`, specified in `docs/features/platform/networking.md`).

**Definition:**

```python
def is_retryable_condition(exc: Exception) -> bool:
    """Classify whether a post-transport exception is worth retrying.

    Used by Celery task wrappers (fetch_single_cve, run_catch_up,
    correlate_submission_request) to decide self.retry() vs immediate
    failure.

    Returns True for transient conditions where a subsequent attempt
    may succeed: infrastructure failures (network, timeout, proxy,
    protocol errors, HTTP 5xx) and rate limiting (HTTP 429).

    Returns False for permanent conditions where retrying would hit
    the same error: HTTP 4xx (except 429), parsing errors on HTTP 200,
    and any non-httpx exception.

    Post-transport context: exceptions reaching this function have
    already exhausted transport-level retry (4 attempts with
    1s/2s/4s backoff). A True result means the transport layer
    attempted up to 4 requests and all failed — the condition is
    persistent at the transport level but may be transient at the
    Celery task level (minutes/hours timescale vs seconds timescale).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status == 429
    return isinstance(exc, INFRA_FAILURE_TYPES)
```

**Relationship with `is_infrastructure_failure()`:**

`is_retryable_condition()` is a **superset** of `is_infrastructure_failure()`:

```
is_infrastructure_failure(e) == True  →  is_retryable_condition(e) == True  (always)
is_retryable_condition(e) == True  →  is_infrastructure_failure(e) == True  (EXCEPT HTTP 429)
```

| Condition | `is_infrastructure_failure()` | `is_retryable_condition()` |
|---|---|---|
| Network error / timeout / proxy | `True` (API unreachable) | `True` (transient) |
| HTTP 5xx | `True` (API error) | `True` (transient) |
| HTTP 429 | **`False`** (API reachable, rate-limited) | **`True`** (transient — will clear after backoff) |
| HTTP 403 / other 4xx | `False` | `False` |
| `JSONDecodeError` / `ValidationError` | `False` | `False` |
| Any non-httpx exception | `False` | `False` |

The two functions answer different questions:
- `is_infrastructure_failure()`: "Is the external API unreachable?" — drives the consecutive failure abort counter in `execute()` batch loops
- `is_retryable_condition()`: "Is there a reasonable chance the next attempt will succeed?" — drives Celery task retry decisions

Neither function is modified by this change. `is_infrastructure_failure()` retains its existing scope and consumers unchanged.

### Consumers

After this change, `is_retryable_condition()` will have exactly three consumers:

1. **`fetch_single_cve`** — replaces inline condition enumeration
2. **`run_catch_up`** — replaces the inconsistent blacklist approach
3. **`correlate_submission_request`** — replaces the informal catch-all retry (all HTTP errors retried)

No other task wrappers or decision points need this function:
- `execute()` abort counter — continues using `is_infrastructure_failure()` (different question)
- AD LDAP internal retry — LDAP-specific exceptions, not HTTP
- Git read-phase retry — subprocess exit codes, not HTTP
- IBSEventConsumer reconnect — AMQP protocol, not HTTP
- Transport-level retry — already handled in the httpx transport layer

### Related fix: CSOV-GAP-02 (CompletenessGuardError signaling)

The OSV fetcher spec (`cve-sync-osv.md`) has an unresolved Open Point about the completeness guard signaling mechanism. The guard triggers when all Phase 2/3 sub-requests fail with infrastructure errors, but the spec does not specify what `fetch_single()` does after the guard triggers — return `None` or raise an exception.

This change enables the correct resolution: `fetch_single()` raises a `CompletenessGuardError` (non-retryable). `CompletenessGuardError` inherits from `Exception` directly (not from `FetcherError`) and is defined in `backend/app/services/base_cve_fetcher.py` alongside `CVENotInSource`. It is a per-CVE condition, not a whole-run failure — inheriting from `FetcherError` would incorrectly trigger the run-level error message sanitization path in `BaseFetcher.run()`.

Because `is_retryable_condition(CompletenessGuardError())` returns `False` (it is not an httpx exception), all three wrappers automatically handle it correctly:
- `fetch_single_cve`: catch-all — non-retryable — writes "failure" status — no retry
- `run_catch_up` (after fix): `is_retryable_condition()` returns `False` — no retry
- `execute()`: `except Exception` handler — `_isolated_status_commit("failure")` — `record_failed()` — `is_infrastructure_failure()` returns `False` — reset consecutive failures

## Action Plan

### Step 1: Update `docs/features/platform/networking.md`

Add a new section "Celery Retry Classification" after the existing "Infrastructure Failure Classification" section. Define `is_retryable_condition()` with:
- Function signature and docstring (as shown above)
- The relationship table with `is_infrastructure_failure()`
- The "Post-transport context" note (same pattern as the existing note for `is_infrastructure_failure()`)
- Consumer list: `fetch_single_cve`, `run_catch_up`, `correlate_submission_request`

Do NOT modify the existing `is_infrastructure_failure()` section — it remains unchanged with its existing consumers.

### Step 2: Update `docs/features/platform/cve-fetcher-infrastructure.md`

**2a.** In the "Retry Policy for `fetch_single`" section (around line 515): replace the inline enumeration of retryable/non-retryable conditions with a reference to `is_retryable_condition()`. The section should state:

- Retryable conditions: `is_retryable_condition(exc)` returns `True` (network errors, HTTP 5xx, timeout, HTTP 429 — see `networking.md`, "Celery Retry Classification")
- Non-retryable conditions: `CVENotInSource` (caught separately — "missing" status), everything else where `is_retryable_condition()` returns `False` (HTTP 403, other 4xx, parsing errors, any non-httpx exception)
- The catch-all rule remains: "any condition not explicitly retryable is non-retryable"

**2b.** In the "Batch Error Handling" section: add a note clarifying that `is_infrastructure_failure()` (used by the abort counter in `execute()`) and `is_retryable_condition()` (used by Celery task wrappers) are distinct functions answering different questions. Reference `networking.md` for both definitions.

**2c.** In the "Metric placement" note: add a sentence clarifying that `fetch_single()` MUST NOT call `record_failed()` when it intends to raise an exception — the caller (`execute()` exception handler) is responsible for metric recording on the failure path. This prevents double-counting.

**2d.** In the "`CVENotInSource` Signal" section: add an explicit positive inheritance statement. The current spec says "`CVENotInSource` does NOT inherit from `FetcherError`" but does not state what it inherits from. Add: "`CVENotInSource` inherits from `Exception` directly." This aligns with the same convention used for `CompletenessGuardError` (step 5b) — both are per-CVE signal classes in `base_cve_fetcher.py` that inherit from `Exception`, not from `FetcherError`.

### Step 3: Update `docs/features/tickets/cve-service.md`

In the `fetch_single_cve` orchestrator section (around line 988): replace the inline enumeration of retryable/non-retryable conditions with a reference to `is_retryable_condition()`. The current text lists specific conditions inline ("Raises retryable exception (network, HTTP 5xx, timeout, 429)" / "Raises non-retryable exception (HTTP 403, other 4xx, parsing error)"). After this change, the authoritative classification lives in `networking.md` — the orchestrator section should reference `is_retryable_condition()` instead of restating the condition list, to prevent drift.

### Step 4: Update `docs/features/platform/fetcher-infrastructure.md`

**4a.** In the `run_catch_up` section (around line 487): replace the current non-retryable exception set mechanism with the `is_retryable_condition()` approach. The task decorator changes from `@celery_app.task` to `@celery_app.task(bind=True)` — this fixes a pre-existing inconsistency where the spec's "Retry interaction" section (line 1185) already claims `self.retry()` usage, but the code block lacks the required `bind=True`. The retry decision becomes:

```python
except (NotImplementedError, CVENotInSource, ValueError):
    return  # Contract violations — non-retryable, silent return
except Exception as e:
    if is_retryable_condition(e):
        # Backoff: same policy as fetch_single_cve
        # (see cve-fetcher-infrastructure.md, "Retry Policy for fetch_single")
        self.retry(exc=e, countdown=...)
    raise  # Catch-all: non-retryable, task fails
```

The explicit non-retryable set (`NotImplementedError`, `CVENotInSource`, `ValueError`) remains — these are caught BEFORE the `is_retryable_condition()` check because they represent contract violations, not HTTP errors. The difference from the current spec is that AFTER these are handled, the remaining exceptions are classified by `is_retryable_condition()` instead of being retried unconditionally.

**4b.** Update the statement "applies the same retry policy as `fetch_single_cve`" to reference `is_retryable_condition()` as the shared classification mechanism, making the claim verifiable.

**4c.** In the `run_catch_up` section, add a note: "`run_catch_up` is a Celery `bind=True` task. `self.retry(exc=e, countdown=...)` raises a `Retry` exception internally, re-enqueuing the task with the specified backoff delay."

### Step 5: Update `docs/features/packages/ibs-submission-tracking.md`

**5a.** In the `correlate_submission_request` error handling section: replace the informal "IBS API unreachable / timeout" and "4xx/5xx" retry rows with a reference to `is_retryable_condition()`. The task decorator must be `@celery_app.task(bind=True, max_retries=3)` — `bind=True` is required for `self.retry()`. The error handling structure wraps the entire pipeline:

```python
@celery_app.task(bind=True, max_retries=3)
def correlate_submission_request(self, submission_id):
    try:
        # Pipeline steps 1-5
        # (see "Celery Task: correlate_submission_request" above)
        ...
    except Exception as e:
        if is_retryable_condition(e):
            # Backoff: same policy as fetch_single_cve
            # (see cve-fetcher-infrastructure.md, "Retry Policy for fetch_single")
            self.retry(exc=e, countdown=...)
        raise  # Non-retryable: task fails, catch-up fetcher recovers
```

The `is_retryable_condition()` check wraps the entire task. In practice, only step 1 (IBS diff API call) can raise httpx exceptions (retryable); steps 2-5 (local parsing and DB operations) raise non-httpx exceptions that `is_retryable_condition()` classifies as non-retryable.

**5b. Behavioral change**: the current spec retries all HTTP errors (both "unreachable / timeout" and "4xx/5xx" rows in the error table). With `is_retryable_condition()`, HTTP 4xx (except 429) becomes non-retryable — this aligns `correlate_submission_request` with `fetch_single_cve` behavior and eliminates wasted retry attempts on permanent errors like HTTP 403. The current catch-all retry behavior was likely a first-draft simplification rather than an intentional design choice (the spec has not been reviewed yet).

**5c. Retry parameters**: 3 retries with exponential backoff (5s → 10s → 20s), matching `fetch_single_cve` and `run_catch_up` (see `docs/features/platform/cve-fetcher-infrastructure.md`, "Retry Policy for `fetch_single`"). After max retries, the SR remains without correlations — the catch-up fetcher (`SyncIbsRequests`) will retry on its next run.

### Step 6: Update `docs/features/tickets/cve-sync-osv.md` (resolve CSOV-GAP-02)

**6a.** In the Algorithm section, step 11 (completeness guard): change "skip `upsert_cve()`, call `record_failed()`" to "skip `upsert_cve()`, raise `CompletenessGuardError` (non-retryable)". Remove the reference to `record_failed()` — the caller handles metric recording.

**6b.** Add `CompletenessGuardError` to the spec's exception definitions (or a "Service Exceptions" section if one exists). Define it as:
- Inherits from `Exception` directly — not from `FetcherError` (per-CVE condition, not whole-run failure; same convention as `CVENotInSource`)
- Defined in `backend/app/services/base_cve_fetcher.py` alongside `CVENotInSource`
- Non-retryable (not an httpx exception — `is_retryable_condition()` returns `False`)
- Raised when: the completeness guard triggers (all Phase 2/3 sub-requests failed with infrastructure errors)

**6c.** In the `fetch_single()` error handling table: update the completeness guard row from "Skip upsert, `record_failed()` — completeness guard" to "Skip upsert, raise `CompletenessGuardError` — non-retryable".

**6d.** Remove the Open Point about "Completeness guard and `record_failed()` call site ambiguity" (lines 527-540). The ambiguity is resolved: `fetch_single()` raises, the caller handles rollback + status commit + metric.

**6e.** Update the `execute()` pseudocode if it contains any guard-specific handling. The `except Exception` handler already handles all non-`CVENotInSource` exceptions uniformly — `CompletenessGuardError` falls into this path naturally.

### Step 7: Update review tracking (resolve CSOV-GAP-02)

**7a.** In `docs/reviews/cve-sync-osv.md`: mark CSOV-GAP-02 as RESOLVED with compact format:
`**Status**: RESOLVED — Fixed: completeness guard raises CompletenessGuardError; record_failed() responsibility moved to execute() caller; Open Point removed (YYYY-MM-DD)`

**7b.** Update `docs/reviews/.tracking.json`: decrement GAP Medium count by 1 for cve-sync-osv, increment resolved by 1.

**7c.** Update `docs/reviews/README.md` to reflect new counts.

### Step 8: Run reviewers on affected specs

Launch the following reviewers on the specs modified in steps 1-6:

| Spec | Reviewers | Rationale |
|---|---|---|
| `networking` | `spec-gap-analyzer`, `spec-coherence-reviewer` | New function added; verify completeness and coherence with existing `is_infrastructure_failure()` |
| `cve-fetcher-infrastructure` | `spec-gap-analyzer`, `spec-coherence-reviewer` | Retry policy and metric placement rules changed |
| `fetcher-infrastructure` | `spec-gap-analyzer`, `spec-coherence-reviewer` | `run_catch_up` mechanism changed |
| `cve-service` | `spec-coherence-reviewer` | Inline retry enumeration replaced with reference |
| `ibs-submission-tracking` | `spec-coherence-reviewer` | Retry behavior changed, error handling restructured |
| `cve-sync-osv` | `spec-gap-analyzer`, `spec-coherence-reviewer` | Completeness guard signaling changed, Open Point removed |

Also launch `docs-placement-reviewer` on `networking.md` (new pattern added — verify it belongs there and not in a cross-cutting location).

Address any findings rated "Needs revision" before proceeding.

### Step 9: Delete this draft

Once all changes from steps 1-8 are applied and reviewers pass, delete `docs/drafts/retryable-condition-centralization.md`.

## Cross-references

- `docs/features/platform/networking.md` — Infrastructure Failure Classification (existing)
- `docs/features/platform/cve-fetcher-infrastructure.md` — fetch_single Signaling Convention, Retry Policy, Session Lifecycle
- `docs/features/platform/fetcher-infrastructure.md` — run_catch_up, run_fetcher
- `docs/features/packages/ibs-submission-tracking.md` — correlate_submission_request
- `docs/features/tickets/cve-sync-osv.md` — Completeness guard, Open Points
- `docs/features/tickets/cve-service.md` — fetch_single_cve orchestrator (inline retry enumeration updated in step 3)

