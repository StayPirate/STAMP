# Centralize Celery Task Retry Classification

## Status

Draft — pending review

## Problem Statement

Three Celery task wrappers in Sentinel independently classify exceptions to decide whether to retry or fail immediately. Each uses a different mechanism, leading to inconsistencies:

| Task wrapper | Current mechanism | Approach |
|---|---|---|
| `fetch_single_cve` | Enumerated HTTP conditions inline | Whitelist: "network, 5xx, timeout, 429 = retry; catch-all = non-retryable" |
| `run_catch_up` | Explicit non-retryable exception set | Blacklist: "retry everything EXCEPT `NotImplementedError`, `CVENotInSource`, `ValueError`" |
| `correlate_submission_request` | Implicit ("IBS API unreachable / timeout") | Undefined — relies on informal description |

### Inconsistency

`run_catch_up` claims to use "the same retry policy as `fetch_single_cve`" but its blacklist mechanism produces different results:

| Condition | `fetch_single_cve` (retry?) | `run_catch_up` (retry?) |
|---|---|---|
| Network error / 5xx / timeout | Yes | Yes |
| HTTP 429 | Yes | Yes |
| HTTP 403 | **No** (catch-all) | **Yes** (not in blacklist) |
| `JSONDecodeError` on HTTP 200 | **No** (catch-all) | **Yes** (not in blacklist) |
| `ValidationError` on HTTP 200 | **No** (catch-all) | **Yes** (not in blacklist) |

`run_catch_up` wastes up to 3 retry attempts on permanent errors (HTTP 403, parse failures) that `fetch_single_cve` correctly classifies as non-retryable.

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
3. **`correlate_submission_request`** — replaces the implicit "API unreachable / timeout" description

No other task wrappers or decision points need this function:
- `execute()` abort counter — continues using `is_infrastructure_failure()` (different question)
- AD LDAP internal retry — LDAP-specific exceptions, not HTTP
- Git read-phase retry — subprocess exit codes, not HTTP
- IBSEventConsumer reconnect — AMQP protocol, not HTTP
- Transport-level retry — already handled in the httpx transport layer

### Related fix: CSOV-GAP-02 (CompletenessGuardError signaling)

The OSV fetcher spec (`cve-sync-osv.md`) has an unresolved Open Point about the completeness guard signaling mechanism. The guard triggers when all Phase 2/3 sub-requests fail with infrastructure errors, but the spec does not specify what `fetch_single()` does after the guard triggers — return `None` or raise an exception.

This change enables the correct resolution: `fetch_single()` raises a `CompletenessGuardError` (non-retryable). Because `is_retryable_condition(CompletenessGuardError())` returns `False` (it is not an httpx exception), all three wrappers automatically handle it correctly:
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

### Step 3: Update `docs/features/platform/fetcher-infrastructure.md`

**3a.** In the `run_catch_up` section (around line 487): replace the current non-retryable exception set mechanism with the `is_retryable_condition()` approach. The retry decision becomes:

```python
except (NotImplementedError, CVENotInSource, ValueError):
    return  # Contract violations — non-retryable, silent return
except Exception as e:
    if is_retryable_condition(e):
        raise self.retry(exc=e, countdown=backoff)
    raise  # Catch-all: non-retryable, task fails
```

The explicit non-retryable set (`NotImplementedError`, `CVENotInSource`, `ValueError`) remains — these are caught BEFORE the `is_retryable_condition()` check because they represent contract violations, not HTTP errors. The difference from the current spec is that AFTER these are handled, the remaining exceptions are classified by `is_retryable_condition()` instead of being retried unconditionally.

**3b.** Update the statement "applies the same retry policy as `fetch_single_cve`" to reference `is_retryable_condition()` as the shared classification mechanism, making the claim verifiable.

**3c.** In the `run_catch_up` section, add a note explaining the `self.retry()` pattern: "`run_catch_up` is a Celery `bind=True` task. `self.retry(exc=e, countdown=...)` is Celery's built-in method that raises a `Retry` exception, re-enqueuing the task with the specified backoff delay. The `raise` is required because `self.retry()` returns the exception rather than raising it directly."

### Step 4: Update `docs/features/packages/ibs-submission-tracking.md`

In the `correlate_submission_request` error handling section: replace the implicit "IBS API unreachable / timeout" retry description with a reference to `is_retryable_condition()`. State that the retry decision uses the same centralized classification as `fetch_single_cve` and `run_catch_up`.

### Step 5: Update `docs/features/tickets/cve-sync-osv.md` (resolve CSOV-GAP-02)

**5a.** In the Algorithm section, step 11 (completeness guard): change "skip `upsert_cve()`, call `record_failed()`" to "skip `upsert_cve()`, raise `CompletenessGuardError` (non-retryable)". Remove the reference to `record_failed()` — the caller handles metric recording.

**5b.** Add `CompletenessGuardError` to the spec's exception definitions (or a "Service Exceptions" section if one exists). Define it as:
- Inherits from `Exception` (or an appropriate base)
- Non-retryable (not an httpx exception — `is_retryable_condition()` returns `False`)
- Raised when: the completeness guard triggers (all Phase 2/3 sub-requests failed with infrastructure errors)

**5c.** In the `fetch_single()` error handling table: update the completeness guard row from "Skip upsert, `record_failed()` — completeness guard" to "Skip upsert, raise `CompletenessGuardError` — non-retryable".

**5d.** Remove the Open Point about "Completeness guard and `record_failed()` call site ambiguity" (lines 527-540). The ambiguity is resolved: `fetch_single()` raises, the caller handles rollback + status commit + metric.

**5e.** Update the `execute()` pseudocode if it contains any guard-specific handling. The `except Exception` handler already handles all non-`CVENotInSource` exceptions uniformly — `CompletenessGuardError` falls into this path naturally.

### Step 6: Update review tracking (resolve CSOV-GAP-02)

**6a.** In `docs/reviews/cve-sync-osv.md`: mark CSOV-GAP-02 as RESOLVED with compact format:
`**Status**: RESOLVED — Fixed: completeness guard raises CompletenessGuardError; record_failed() responsibility moved to execute() caller; Open Point removed (YYYY-MM-DD)`

**6b.** Update `docs/reviews/.tracking.json`: decrement GAP Medium count by 1 for cve-sync-osv, increment resolved by 1.

**6c.** Update `docs/reviews/README.md` to reflect new counts.

### Step 7: Run reviewers on affected specs

Launch the following reviewers on the specs modified in steps 1-5:

| Spec | Reviewers | Rationale |
|---|---|---|
| `networking` | `spec-gap-analyzer`, `spec-coherence-reviewer` | New function added; verify completeness and coherence with existing `is_infrastructure_failure()` |
| `cve-fetcher-infrastructure` | `spec-gap-analyzer`, `spec-coherence-reviewer` | Retry policy and metric placement rules changed |
| `fetcher-infrastructure` | `spec-gap-analyzer`, `spec-coherence-reviewer` | `run_catch_up` mechanism changed |
| `ibs-submission-tracking` | `spec-coherence-reviewer` | Retry reference updated |
| `cve-sync-osv` | `spec-gap-analyzer`, `spec-coherence-reviewer` | Completeness guard signaling changed, Open Point removed |

Also launch `docs-placement-reviewer` on `networking.md` (new pattern added — verify it belongs there and not in a cross-cutting location).

Address any findings rated "Needs revision" before proceeding.

### Step 8: Delete this draft

Once all changes from steps 1-7 are applied and reviewers pass, delete `docs/drafts/retryable-condition-centralization.md`.

## Cross-references

- `docs/features/platform/networking.md` — Infrastructure Failure Classification (existing)
- `docs/features/platform/cve-fetcher-infrastructure.md` — fetch_single Signaling Convention, Retry Policy, Session Lifecycle
- `docs/features/platform/fetcher-infrastructure.md` — run_catch_up, run_fetcher
- `docs/features/packages/ibs-submission-tracking.md` — correlate_submission_request
- `docs/features/tickets/cve-sync-osv.md` — Completeness guard, Open Points
- `docs/features/tickets/cve-service.md` — fetch_single_cve orchestrator (referenced but not modified — its inline implementation aligns with step 2a)
- `docs/conventions.md` — Celery `bind=True` task pattern (`self.retry()` is Celery's built-in retry mechanism)
