# Draft: Per-Ticket Catch-Up + Red Hat Fetcher — Merged Application Plan

## Status

**Draft** — pending review before application.

## Origin

This draft merges two agreed drafts into a single application plan,
organized by target file:

- `docs/drafts/catch-up-architecture.md` — refactors the per-ticket
  catch-up mechanism from standalone `fetch_single_<name>(ticket_id)`
  Celery tasks to a `catch_up()` method on `BaseFetcher`
- `docs/drafts/redhat-cvss-fetcher-spec-changes.md` — completes the
  `SyncRedhatCves` fetcher specification (CVSS v2+v3, CWE, references,
  packages, error handling, `fetch_single` method)

Both drafts have all design decisions marked as "Agreed". This document
does not introduce new design decisions — it organizes the agreed
changes by target file for application.

### Key decisions (agreed, summarized for context)

1. **`catch_up()` replaces `fetch_single_<name>(ticket_id)`**: a method
   on `BaseFetcher` with default implementation for CVE fetchers
   (delegates to `fetch_single(cve_id)`), custom overrides for non-CVE
   fetchers, generic `run_catch_up` Celery task wrapper, and
   `get_catch_up_fetchers()` registry accessor
2. **`SyncRedhatCves` implements `fetch_single(cve_id)`**: core per-CVE
   logic extracted into `fetch_single`, `execute()` delegates to it in a
   loop, default `catch_up()` inherited from `BaseFetcher`
3. **Red Hat fetcher relocated**: full definition moves from
   `cvss-scoring.md` to `cve-tracking.md`; `cvss-scoring.md` retains a
   consumer summary
4. **Red Hat fetcher expanded**: CVSS v2+v3 extraction, CWE extraction,
   reference extraction, best-effort package addition, error handling
5. **Enrichment catch-up step removed from `cve-service.md`**: no longer
   needed because `trigger_on_demand_fetch()` covers all CVE fetchers
   (including Red Hat, which now has `fetch_single`)
6. **Sub-operation contract consolidation skipped**: `catch_up()` adds a
   cross-reference to `fetch_single`'s shared sub-operation rules instead
   of extracting them into a separate section

---

## Changes by File

### 1. `docs/features/platform/fetcher-infrastructure.md`

#### 1a. CVE fetcher example update (lines 177-189)

The current "enrichment fetcher (no `fetch_single`)" example uses
`SyncRedhatCves` without `fetch_single`. After this merge, all CVE
fetchers implement `fetch_single`, making the enrichment/discovery
distinction obsolete for examples.

**Current** (lines 177-189):

```
**CVE fetcher example** — enrichment fetcher (no `fetch_single`):

\```python
class SyncRedhatCves(BaseFetcher):
    name = "sync_redhat_cves"              # registry key
    cve_source_type = "redhat"             # CVESourceType identifier
    description = "Sync CVE data from Red Hat Security API"
    default_schedule = "0 3 * * *"

    async def execute(self, session: AsyncSession) -> None:
        # Uses self.cve_source_type — never a hardcoded string:
        await upsert_cve(db, cve_id, source=self.cve_source_type, ...)
\```
```

**Replacement**:

```
**CVE fetcher example** — delegates `execute()` to `fetch_single()`:

\```python
class SyncRedhatCves(BaseFetcher):
    name = "sync_redhat_cves"              # registry key
    cve_source_type = "redhat"             # CVESourceType identifier
    description = "Sync CVE data from Red Hat Security API"
    default_schedule = "0 3 * * *"

    async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
        # Core per-CVE logic: call Red Hat API, upsert CVSS/CWE/refs
        await upsert_cve(db, cve_id, source=self.cve_source_type, ...)

    async def execute(self, session: AsyncSession) -> None:
        for cve_id in active_ticket_cve_ids:
            await self.fetch_single(cve_id, session)
\```
```

#### 1b. CVE Source Type Identity — simplify enrichment/discovery distinction (lines 390-392, 447)

**Current** (lines 390-392):

> ALL fetchers that write to `CVESource` — both **discovery fetchers**
> (those implementing `fetch_single()`) and **enrichment fetchers** (those
> calling `upsert_cve()` without `fetch_single()`) — MUST declare a
> `cve_source_type: str` class attribute [...]

**Replacement**:

> ALL CVE fetchers — those that write to `CVESource` via
> `upsert_cve()` — MUST declare a `cve_source_type: str` class
> attribute [...]

**Current** (line 447):

> This convention provides runtime enforcement for enrichment fetchers:
> if a fetcher calls [...]

**Replacement**:

> This convention provides runtime enforcement for CVE fetchers: if a
> fetcher calls [...]

#### 1c. Per-Ticket Catch-Up section — full rewrite (lines 509-590)

Replace the entire "Per-Ticket Catch-Up: `fetch_single` Capability"
section (lines 509-590) with the following:

---

> ## Per-Ticket Catch-Up: `catch_up()` Method
>
> Fetchers whose `execute()` scope is filtered by ticket status (e.g.,
> `sync_redhat_cves` scopes to CVEs with active tickets) skip inactive
> tickets during periodic runs. When a ticket is reactivated (from
> Ignored, Duplicated, or Resolved), the system enqueues per-ticket
> catch-up tasks to recover data missed during the inactive period.
>
> The catch-up mechanism is a method on `BaseFetcher`:
>
> ```python
> async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
>     """Per-ticket catch-up after reactivation.
>
>     Called when a ticket transitions from an inactive status
>     (Ignored, Duplicated, Resolved) to an active status. The
>     fetcher retrieves data that was missed during the inactive
>     period.
>
>     Optional. Only applicable to fetchers whose execute() scope
>     is filtered by ticket status. Global fetchers (product catalog,
>     AD sync, etc.) do not implement this.
>     """
>     ...
> ```
>
> ### Default implementation for CVE fetchers
>
> `BaseFetcher` provides a default implementation of `catch_up()` that
> delegates to `fetch_single()`:
>
> ```python
> class BaseFetcher:
>     async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
>         """Default: extract cve_id from ticket, call fetch_single().
>
>         CVE fetchers that implement fetch_single() inherit this
>         default catch_up() automatically. Non-CVE fetchers override
>         with custom logic.
>         """
>         if 'fetch_single' not in type(self).__dict__:
>             return  # no fetch_single, no default catch_up
>         ticket = await session.get(Ticket, UUID(ticket_id))
>         if ticket and ticket.cve_id:
>             await self.fetch_single(str(ticket.cve_id), session)
> ```
>
> CVE fetchers only need to implement `fetch_single(cve_id)`:
>
> - `execute()` calls `self.fetch_single()` in a loop over active CVEs
> - `catch_up()` is derived automatically from `fetch_single()`
>
> Non-CVE fetchers override `catch_up()` with custom logic specific to
> their data domain.
>
> ### Registry accessor: `get_catch_up_fetchers()`
>
> ```python
> def get_catch_up_fetchers() -> dict[str, type[BaseFetcher]]:
>     """Return fetchers implementing catch_up(), keyed by fetcher name.
>
>     A fetcher "implements catch_up" if:
>     - It defines catch_up() in its own __dict__ (explicit override), OR
>     - It defines fetch_single() in its own __dict__ (inherits the
>       default catch_up from BaseFetcher)
>     """
>     ...
> ```
>
> The detection predicate combines two checks:
>
> 1. `'catch_up' in cls.__dict__` — fetcher explicitly overrides
>    `catch_up()` (non-CVE fetchers like `DetectIbsTrackReleases`)
> 2. `'fetch_single' in cls.__dict__` — fetcher implements
>    `fetch_single()`, which means it inherits the default `catch_up()`
>    (CVE fetchers like `SyncRedhatCves`, `SyncNvdCves`)
>
> Fetchers that match neither condition (global fetchers) are excluded.
>
> **Caching semantics**: same as `get_fetch_single_fetchers()` — the
> result is computed lazily on first access, not at import time, to
> ensure all fetcher modules have been registered. The returned dict
> MUST NOT be mutated by callers (return `types.MappingProxyType`).
> A `_clear_catch_up_cache()` test helper MUST be provided.
>
> ### Celery task wrapper
>
> A single generic Celery task wraps all `catch_up()` invocations:
>
> ```python
> @celery_app.task
> def run_catch_up(fetcher_name: str, ticket_id: str) -> None:
>     """Generic catch-up task — replaces per-fetcher tasks."""
>     fetcher_cls = FETCHER_REGISTRY.get(fetcher_name)
>     if fetcher_cls is None:
>         logger.error("run_catch_up: unknown fetcher %s — skipping", fetcher_name)
>         return  # non-retryable — fetcher was removed between enqueue and execution
>     fetcher = fetcher_cls()
>     async def _run():
>         async with get_async_session() as session:
>             await fetcher.catch_up(ticket_id, session)
>     async_run(_run())
> ```
>
> If `fetcher_name` is not found in the registry (e.g., a deployment
> removed the fetcher between enqueue and execution), the task logs an
> error and returns without retry.
>
> ### Interface contract
>
> `catch_up()` shares the same sub-operation classification as
> `fetch_single()` (see "On-demand Single-Item Fetch" above): no
> `FetcherRun` record, no metric reporting, not a `BaseFetcher`
> execution. The following additional rules apply:
>
> - **Parameter**: `ticket_id` (UUID as string)
> - **Idempotent**: if external data is unchanged, no side effects
> - **Mutation path**: when changed data is found, persists through the
>   normal mutation path (service modules), which triggers the standard
>   chain (audit events, reconciliation)
> - **No direct ticket mutations**: MUST NOT acquire `FOR UPDATE` locks
>   on the Ticket row — delegates to the appropriate service module
> - **Session management**: the `run_catch_up` Celery task wrapper
>   creates and manages the `AsyncSession`, following the same pattern
>   as `fetch_single_cve`. The session is passed to `catch_up()` as a
>   parameter. Transaction boundaries depend on the implementation:
>   - **Default `catch_up()`** (CVE fetchers): single transaction —
>     reads the ticket, calls `fetch_single()`, commits on return
>   - **Custom `catch_up()` overrides** (non-CVE fetchers): the method
>     receives the session for read-only queries (ticket lookup, item
>     enumeration). Mutations on each item are delegated to the
>     appropriate service module, which manages its own transaction
>     lifecycle. Each item MUST be committed independently so that a
>     failure on item N does not roll back items 1..N-1
> - **Error handling**:
>   - **Retry policy**: the `run_catch_up` Celery task wrapper applies
>     the same retry policy as `fetch_single_cve` (3 retries with
>     exponential backoff). Reserved for infrastructure failure
>     (external service completely unreachable)
>   - **CVE fetchers** (default `catch_up()`): the `fetch_single`
>     signaling convention applies (`CVENotInSource` → no-op, transient
>     errors → retry)
>   - **Non-CVE fetchers** (custom `catch_up()` override): MUST use
>     per-item error handling — if one item (track, product, package)
>     fails, continue with the remaining items rather than aborting the
>     entire catch-up. Detailed error categorization is defined in each
>     fetcher's own specification
>   - **Raise/return contract for non-CVE overrides**: custom
>     `catch_up()` overrides MUST catch per-item exceptions internally.
>     The method MUST only propagate an exception when all items have
>     failed, indicating infrastructure failure. Partial failure (some
>     items succeed, some fail) MUST result in a normal return — the
>     failed items are logged per-item and will be recovered by the next
>     periodic `execute()` run
> - **Post-commit enqueue**: `run_catch_up` tasks MUST be enqueued
>   after the caller's transaction commits, consistent with the
>   post-commit enqueue pattern used by `trigger_on_demand_fetch()`.
>   Enqueuing before commit risks catch-up tasks running against
>   uncommitted data
> - **Concurrency safety**: no guard on ticket status is required before
>   executing `catch_up()`. If a ticket is re-deactivated after catch-up
>   tasks are enqueued but before they execute, the tasks run to
>   completion. This is safe by design: mutations produced by
>   `catch_up()` are factually correct (the external data is real
>   regardless of ticket status), and `reconcile_ticket_status()`
>   respects the current ticket status. Duplicate enqueuing (e.g., two
>   rapid reactivations) is also safe because `catch_up()` is idempotent
>
> ### Invocation points
>
> `catch_up()` is enqueued exclusively by **ticket reactivation** hooks:
>
> - `ticket_service.reopen_from_ignored()`: after un-ignore
> - `ticket_service.revert_duplicate()`: after un-duplicate
> - `ticket_mutations.reconcile_ticket_status()`: after regression from
>   Resolved to an active status
>
> At each invocation point, the system calls
> `get_catch_up_fetchers()` and enqueues a `run_catch_up` Celery task
> for each registered fetcher.
>
> ### Fetcher inventory
>
> #### Fetchers that implement `catch_up()` (scope filtered by ticket status)
>
> | Fetcher | `execute()` scope filter | `catch_up()` type | What catch-up does |
> |---|---|---|---|
> | `sync_redhat_cves` | CVEs with active tickets | **Default** (via `fetch_single`) | Extract `cve_id` → call Red Hat API → upsert CVSS/CWE/refs/packages |
> | `sync_nvd_cves` | All CVEs (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Already has `fetch_single` for on-demand discovery; catch-up is free |
> | `sync_mitre_cves` | All CVEs (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Same as NVD |
> | `sync_kernel_cves` | All CVEs (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Same as NVD |
> | `detect_ibs_track_releases` | Tracks in active tickets | **Custom override** | Extract ticket's `TicketPackageTrack` records → check IBS for releases on each codestream |
> | `detect_ibs_product_releases` | Products in active tickets | **Custom override** | Extract ticket's `TicketPackageProduct` records → check `updateinfo.xml` for advisories |
> | `sync_ibs_requests` | Codestreams in active tickets | **Custom override** | Extract ticket's codestream names → query IBS Request Search API → correlate SRs/RRs |
> | `evaluate_lifecycle_transitions` | Products in active tickets | **Custom override** | Extract ticket's products → re-evaluate lifecycle phase and eligibility |
> | `sync_ibs_bugowners` | Packages in active tickets | **Custom override** | Extract ticket's package names → refresh bugowner cache for each |
>
> Note: for NVD, MITRE, and kernel CVE fetchers, `execute()` is global
> (not filtered by ticket status), but they still benefit from
> `catch_up()` because their `fetch_single()` method already exists for
> on-demand discovery. The default `catch_up()` gives them ticket
> reactivation support for free.
>
> #### Fetchers that do NOT need `catch_up()` (global scope)
>
> | Fetcher | Why no catch-up needed |
> |---|---|
> | `sync_smelt_products` | Syncs entire product catalog regardless of ticket state |
> | `sync_aimaas_lifecycle` | Syncs all product lifecycle dates |
> | `sync_aimaas_thresholds` | Syncs all CVSS thresholds |
> | `sync_ldap_directory` | Syncs all employee records |
> | `sync_cisa_kev` | Syncs entire KEV catalog |
> | `sync_epss_scores` | Syncs all EPSS scores |
> | `sync_ghsa_advisories` | Syncs all GHSA advisories |
> | `sync_osv_advisories` | Syncs all OSV advisories |
> | `aggregate_fetcher_runs` | Maintenance — no external data, no ticket scope |

---

#### 1d. Import-time validation — add `catch_up()` rule (after line 792)

Add the following rule to the existing import-time validation list (after
rule 8):

> 9. If a fetcher defines `catch_up()` in its `__dict__`, it must accept
>    the signature `(self, ticket_id: str, session: AsyncSession) -> None`
> 10. If a non-CVE fetcher needs catch-up, it MUST define `catch_up()`
>     explicitly in its own class body — the default implementation only
>     works for fetchers that also implement `fetch_single()`

---

### 2. `docs/features/tickets/cve-tracking.md`

#### 2a. Red Hat fetcher section — full content (replaces stub at lines 760-764)

Replace the stub:

```
### Fetcher: `sync_redhat_cves`

- `sync_redhat_cves`: daily fetcher that re-fetches Red Hat CVE data
  (CVSS, CWE, references) for all CVEs with active tickets. Defined in
  `docs/features/tickets/cvss-scoring.md`.
```

With the following full section:

---

> ### Fetcher: `sync_redhat_cves`
>
> | Property | Value |
> |----------|-------|
> | Fetcher name | `sync_redhat_cves` |
> | Class name | `SyncRedhatCves` |
> | `cve_source_type` | `"redhat"` |
> | Schedule | Daily at 03:00 UTC (`0 3 * * *`) |
> | Source | Red Hat Security Data API (`access.redhat.com/hydra/rest/securitydata`) |
> | Scope | All CVEs with active tickets (New, Analysis, Analyzed) |
> | Auth | None (public API) |
> | Custom settings | Yes (see below) |
> | `fetch_single()` | Yes — Red Hat Security Data API single CVE query |
> | `source_reference_url_pattern` | `https://access.redhat.com/security/cve/{cve_id}` |
>
> #### Algorithm
>
> The fetcher operates on individual CVEs (one API call per CVE-ID).
> The core per-CVE logic is implemented in `fetch_single()`, and
> `execute()` delegates to it in a loop over all CVEs with active
> tickets.
>
> Red Hat's API does NOT support incremental fetching (no
> `modified_after` parameter). The `GET /cve.json` list endpoint
> filters by `public_date` (`after`/`before`) and internal creation
> date (`created_days_ago`), but cannot detect modifications to
> existing CVEs (CVSS score changes, `draft` to `verified` transitions,
> CWE updates). The per-CVE poll is therefore the only mechanism for
> both new assessments and modifications.
>
> For each CVE fetched (both periodic `execute()` and on-demand
> `fetch_single()`):
>
> 1. Query the Red Hat API:
>    ```
>    GET /hydra/rest/securitydata/cve/{CVE-ID}.json
>    ```
>
> 2. **CVSS v3**: if the response contains a `cvss3` object and its
>    `cvss3_scoring_vector` is non-empty, parse with the `cvss` library
>    (`CVSS3` class), derive version (`"3.1"`), score, and severity.
>    Persist as `CVECVSSAssessment` with `provider_name = "Red Hat"`.
>
> 3. **CVSS v2**: if the response contains a `cvss` object and its
>    `cvss_scoring_vector` is non-empty, parse with the `cvss` library
>    (`CVSS2` class), derive version (`"2.0"`), score, and severity.
>    Persist as `CVECVSSAssessment` with `provider_name = "Red Hat"`.
>    The unique constraint `(cve_id, provider_name, cvss_version)`
>    ensures v2 and v3 assessments from the same provider coexist as
>    separate rows.
>
> 4. Each CVSS field is processed independently — a response may have
>    both, one, or neither. If neither field is present, no CVSS
>    assessment is recorded.
>
>    **Boundary condition**: the gate for CVSS extraction is the vector
>    string's presence AND non-emptiness. If a `cvss3` (or `cvss`)
>    object is present but its scoring vector is `null`, `""`, or
>    whitespace-only, the object is treated as absent (skip, do not
>    raise or fail). The `cvss` library is never invoked with an empty
>    string.
>
> 5. The `status` field (`"draft"` or `"verified"`) inside `cvss` and
>    `cvss3` objects is **not evaluated**. The only gate is whether
>    the vector string is present and parseable by the `cvss` library.
>    A `"draft"` assessment from Red Hat is still more information than
>    no assessment at all.
>
> 6. **CWE**: if the response contains a `cwe` field (string, e.g.,
>    `"CWE-200"`), persist a `CVECWE` record with `source = "Red Hat"`
>    via the `cve_service` upsert path. The `cwe` field is always a
>    single CWE identifier (not a chain). If the field is absent, skip
>    (no CWE to record). Unique constraint `(cve_id, cwe_id, source)`
>    prevents duplicates; re-syncs for the same source are upserted.
>
> 7. **References**: if the response contains a `references` field
>    (array of strings), split each element on `\n` to extract
>    individual URLs. For each URL, call
>    `reference_service.upsert_references()` with
>    `source = "sync_redhat_cves"`. Type auto-classification: URLs
>    matching known patterns (e.g., `nvd.nist.gov` → `advisory`,
>    `github.com/.../commit` → `patch`) are classified; others default
>    to `NULL` (uncategorized). Unique constraint `(ticket_id, url)`
>    prevents duplicates; upsert applies fill-NULL-only semantics.
>
> 8. **Package best-effort**: if the response contains a
>    `package_state` array, extract `package_name` values (clean source
>    package names, e.g., `"xz"`, `"openssl"`), discard entries where
>    `package_name` is `null`, empty, or whitespace-only, discard names
>    containing `/` (Red Hat container image paths, not source
>    packages), deduplicate, and pass the remaining names as
>    `resolved_packages` in the `CVEIngestPayload` to `upsert_cve()`.
>    The service layer enqueues one `add_package_to_ticket()` background
>    task per package name as a Phase 2 side effect — the fetcher does
>    not manage this step. `add_package_to_ticket()` queries SMELT; if
>    SMELT does not recognize the name, no records are created
>    (best-effort). `add_package_to_ticket()` checks for existing
>    `TicketPackage` records before creating new ones (unique constraint
>    on `(ticket_id, package_name)`) — if the package is already on the
>    ticket, the call is a no-op. The fetcher does NOT use
>    `affected_release[].package` (NEVRA format requires parsing and
>    Red Hat release-specific epoch/version would need to be stripped).
>    Package addition metrics are tracked by the
>    `add_package_to_ticket` Phase 2 tasks, not by the fetcher itself.
>
> **Scope gap**: the fetch scope is "CVEs with active tickets" due to
> Red Hat API rate limits. CVEs whose tickets are in Ignored,
> Duplicated, or Resolved status do NOT receive updates during the
> inactive period. This gap is mitigated by the default `catch_up()`
> mechanism (see
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md),
> "Per-Ticket Catch-Up: `catch_up()` Method").
>
> #### `fetch_single` method
>
> `SyncRedhatCves` implements `fetch_single(cve_id)` as a method on
> the class. This provides:
>
> 1. **On-demand discovery**: `get_fetch_single_fetchers()`
>    auto-discovers the method, so `trigger_on_demand_fetch()` invokes
>    it in parallel with NVD/MITRE when Sentinel encounters a new
>    CVE-ID. Red Hat enrichment happens immediately at ticket creation,
>    not only at the next scheduled run
> 2. **Ticket catch-up for free**: the default `catch_up()` on
>    `BaseFetcher` extracts `cve_id` from the ticket and calls
>    `self.fetch_single()`. No separate catch-up task needed
> 3. **DRY**: the core logic (call Red Hat API → build payload →
>    `upsert_cve()` with CVSS/CWE/refs/packages) exists in one place
>
> Class structure:
>
> ```python
> class SyncRedhatCves(BaseFetcher):
>     name = "sync_redhat_cves"
>     cve_source_type = "redhat"
>     description = "Sync CVE data from Red Hat Security API"
>     default_schedule = "0 3 * * *"
>
>     class Settings(BaseModel):
>         throttle_delay_seconds: float = Field(
>             default=2.0, ge=0.1, le=30.0,
>             description="Delay between consecutive Red Hat API requests.",
>         )
>
>     source_reference_url_pattern = (
>         "https://access.redhat.com/security/cve/{cve_id}"
>     )
>
>     async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
>         """Fetch a single CVE from the Red Hat Security Data API.
>
>         GET /hydra/rest/securitydata/cve/{CVE-ID}.json
>
>         Extracts: CVSS v2 + v3, CWE, references, package names.
>         Builds a CVEIngestPayload with resolved_packages and calls
>         upsert_cve(). Raises CVENotInSource if HTTP 404 or response
>         contains no extractable data.
>         """
>         ...
>
>     async def execute(self, session: AsyncSession) -> None:
>         """Periodic batch: iterate over CVEs with active tickets."""
>         for cve_id in active_ticket_cve_ids:
>             try:
>                 await self.fetch_single(cve_id, session)
>                 self.record_updated()
>             except CVENotInSource:
>                 pass  # skip — no Red Hat data for this CVE
>             except Exception:
>                 self.record_failed()
>             await asyncio.sleep(self.settings.throttle_delay_seconds)
>
>     # catch_up(ticket_id) — inherited from BaseFetcher default:
>     #   extracts cve_id from ticket → calls self.fetch_single(cve_id)
> ```
>
> Signaling convention: follows the standard `fetch_single` signaling
> convention defined in
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> (section "`fetch_single` Signaling Convention").
>
> #### Error Handling
>
> **`fetch_single()` — on-demand (single CVE)**
>
> | Condition | Retry? | Final status | Action |
> |-----------|--------|--------------|--------|
> | HTTP 200 with extractable data (CVSS, CWE, refs, or packages) | — | `success` | Upsert available data |
> | HTTP 200 with no extractable data (no CVSS, no CWE, no refs, no packages) | No | `missing` | Raise `CVENotInSource` — Red Hat has no actionable data for this CVE |
> | HTTP 404 | No | `missing` | Raise `CVENotInSource` — CVE not in Red Hat's database |
> | HTTP 429 | Yes (3x) | `failure` | Standard Celery retry (5s → 10s → 20s) |
> | HTTP 5xx | Yes (3x) | `failure` | Standard Celery retry |
> | Network timeout / DNS / connection refused | Yes (3x) | `failure` | Standard Celery retry |
> | HTTP 200 with unparseable JSON or invalid vector | No | `failure` | Non-retryable — log and fail |
> | HTTP 403, other 4xx (not 404/429) | No | `failure` | Non-retryable |
>
> If the response contains any extractable data — even without CVSS
> (e.g., CWE and references only) — the fetcher upserts what is
> available and returns normally (`success`). The fetcher's scope
> extends beyond CVSS: discarding valid CWE/reference/package data
> solely because CVSS is absent would lose actionable information.
>
> **Partial extraction failures**: if some data types are upserted
> successfully but a parsing failure occurs on another (e.g., CVSS
> upserted but CWE string is malformed), the CVE counts as
> `record_updated` (data was saved) with a WARNING log for the failed
> sub-extraction. The partial failure does not invalidate data already
> persisted.
>
> **Data preservation**: existing Red Hat data — `CVECVSSAssessment`
> records, `CVECWE` records, and `TicketReference` entries created by
> this fetcher — is **not deleted** when a later response returns
> HTTP 404 or lacks previously-present fields. The data was valid when
> fetched; absence in a later response does not invalidate it.
>
> **`execute()` — periodic batch**
>
> Error handling is **per-CVE**, not per-run:
>
> | Condition | Action |
> |-----------|--------|
> | HTTP 200 with extractable data | Upsert, `record_updated` |
> | HTTP 200 with no extractable data | Skip CVE, no metric (not a failure — Red Hat has no actionable data) |
> | HTTP 404 | Skip CVE, no metric (not a failure) |
> | HTTP 429 | `record_failed` for this CVE, **continue** to next CVE (after throttle delay) |
> | HTTP 5xx | `record_failed` for this CVE, continue to next |
> | Network timeout | `record_failed` for this CVE, continue to next |
> | HTTP 200 with unparseable data | `record_failed` for this CVE, continue to next |
> | Persistent network failure (e.g., DNS down) | After 3 consecutive failures, abort entire run with `FetcherError` |
>
> The consecutive failure counter resets to zero after any successful
> CVE fetch (HTTP 200 with data upserted) or clean skip (HTTP 404, 200
> with no data). Only uninterrupted sequences of infrastructure failures
> (HTTP 5xx, network timeout, DNS error) count toward the 3-failure
> abort threshold.
>
> The batch run **never aborts on a single CVE failure** — it continues
> to the next CVE after recording the failure. The only abort condition
> is persistent infrastructure failure (3 consecutive errors suggesting
> the network or API is down entirely).
>
> **Sanitized messages**: per `fetcher-infrastructure.md` requirement,
> the fetcher produces these sanitized `FetcherError` messages:
>
> | Failure mode | `FetcherError` message |
> |---|---|
> | Connection error | `"Failed to connect to Red Hat Security Data API"` |
> | HTTP 5xx | `"Red Hat Security Data API returned HTTP {status_code}"` |
> | Persistent infra failure | `"Red Hat Security Data API unreachable — 3 consecutive failures"` |
> | Unparseable JSON | `"Red Hat API returned unparseable response for {cve_id}"` |
> | Invalid CVSS vector | `"Red Hat API returned invalid CVSS vector for {cve_id}"` |
>
> #### Metrics
>
> - `record_created`: N/A. The Red Hat fetcher never creates new CVE
>   records — it enriches existing ones via `upsert_cve()`. New `CVECWE`
>   and `TicketReference` records created as a side effect of enrichment
>   are not counted as `record_created` (consistent with NVD, where
>   `record_created` means "a new CVE record was inserted for the first
>   time"). The fetcher uses only `record_updated` and `record_failed`
> - `record_updated`: incremented only when `upsert_cve()` actually
>   modifies data — CVSS assessments (v2 and/or v3), CWE records,
>   reference URLs, or package names inserted or updated. A single CVE
>   fetch that upserts both CVSS and CWE counts as one `record_updated`
>   (not two). If the API returns HTTP 200 but no data has changed (all
>   upserts are no-ops), no metric is recorded for that CVE — consistent
>   with NVD/MITRE fetchers
> - `record_failed`: incremented per CVE on non-retryable errors or
>   after retry exhaustion
>
> #### Custom Settings
>
> This fetcher declares the following custom settings (see
> `docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
> Schema" for the schema structure and validation rules):
>
> | Setting | Type | Default | Constraints | Description |
> |---------|------|---------|-------------|-------------|
> | `throttle_delay_seconds` | float | 2.0 | 0.1–30.0 | Delay between consecutive Red Hat API requests |

#### 2b. Common CVE Fetcher Error Handling — qualify (line 369)

In the "Common CVE Fetcher Error Handling" section, qualify the
statement "A batch must never abort entirely due to a single CVE
failure" to add: "Source-specific abort conditions (e.g., persistent
infrastructure failure after N consecutive errors) are documented in
the individual fetcher sections below."

---

### 3. `docs/features/tickets/cvss-scoring.md`

#### 3a. Red Hat Sync — trim to consumer summary (lines 310-346)

Replace lines 310-346 with a consumer summary focused on CVSS scoring
behavior. Keep 3-4 paragraphs covering: what CVSS data arrives from Red
Hat, periodicity, scope gap, catch-up mechanism. Add cross-reference at
the end.

**Replacement**:

> ### Red Hat Sync
>
> Red Hat's API does NOT support incremental fetching (no
> `modified_after` parameter). The `sync_redhat_cves` fetcher runs daily
> (03:00 UTC) and re-fetches Red Hat data for all CVEs with active
> tickets.
>
> Red Hat provides both CVSS v3 and CVSS v2 assessments. Both versions
> are imported as `CVECVSSAssessment` records with
> `provider_name = "Red Hat"`. The fetcher also extracts CWE
> identifiers, references, and source package names from the same API
> response.
>
> **Scope gap**: the fetch scope is "CVEs with active tickets" due to
> Red Hat API rate limits. CVEs whose tickets are in Ignored,
> Duplicated, or Resolved status do NOT receive Red Hat CVSS updates
> during the inactive period. This gap is mitigated by the `catch_up()`
> mechanism: when a ticket is reactivated, the default `catch_up()`
> calls `fetch_single(cve_id)` to retrieve the latest Red Hat data.
> See [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> ("Per-Ticket Catch-Up: `catch_up()` Method").
>
> For the full fetcher definition — including the complete algorithm,
> CWE/reference extraction, package best-effort addition, error
> handling, and `fetch_single` method — see
> [`cve-tracking.md`](cve-tracking.md) (Fetcher: `sync_redhat_cves`).

#### 3b. Ticket Reactivation: CVSS Catch-Up — update async step (lines 704-722)

Replace lines 704-722 with:

> 2\. **Asynchronous** (enqueued after commit): `catch_up()` tasks are
>    enqueued for every registered fetcher via `get_catch_up_fetchers()`
>    — not limited to CVSS fetchers. This catches up on data that was not
>    fetched during the inactive period. Each `catch_up()` task operates
>    independently; if it discovers changed data, the normal mutation path
>    handles the recalculation chain.
>
> The ticket may transition rapidly as async tasks complete (e.g.,
> re-open → Analysis, then a fetch discovers a release → Resolved). This
> is expected and correct behavior — the system converges to the accurate
> state.
>
> See [`ticket-service.md`](ticket-service.md) for the un-ignore /
> un-duplicate hooks, [`ticket-mutations.md`](ticket-mutations.md) for
> the post-regression hook and `recalculate_cvss_chain()` contract, and
> [`fetcher-infrastructure.md`](../platform/fetcher-infrastructure.md) for
> the `catch_up()` method contract.

#### 3c. Fetcher: `sync_redhat_cves` — remove (lines 731-770)

Remove the entire fetcher section (properties table, algorithm
reference, error handling TBD, metrics, custom settings). This
content is now in `cve-tracking.md` (section 2a above).

The preceding line (730) and subsequent section ("Sub-operation:
`fetch_single_redhat`" at line 772) are both removed, so the "Data
Model" section (line 803) follows directly after "Background Tasks"
(line 724). The `sync_nvd_cves` note at lines 726-729 remains:

> The `sync_nvd_cves` fetcher (defined in [...]
> `cve-tracking.md`) also produces CVSS assessments during CVE
> ingestion. See "NVD Sync (Incremental)" above for the
> consumer-oriented summary.

#### 3d. Sub-operation: `fetch_single_redhat` — remove (lines 772-801)

Remove the entire sub-operation section. Replaced by:

- `fetch_single()` method on the `SyncRedhatCves` class in
  `cve-tracking.md` (section 2a above)
- Default `catch_up()` inherited from `BaseFetcher`
  (`fetcher-infrastructure.md`, section 1c above)

#### 3e. Provider section — CVSS version update (lines 146-159)

In the "Providers > External Providers > Red Hat" section, update
"CVSS versions: currently v3.1 only" to "CVSS versions: v2.0 and
v3.1. v4.0 will be supported when Red Hat adds it."

#### 3f. Catch-up references — update (lines 253, 256-257)

Update references to `fetch_single` tasks to `catch_up()` method
(e.g., "plus `fetch_single` tasks are enqueued for
catch-up/enrichment" → "plus `catch_up()` tasks are enqueued for
catch-up").

---

### 4. `docs/features/tickets/cve-service.md`

#### 4a. Enrichment fetcher note — update (lines 747-755)

The current text says `sync_redhat_cves` is an "enrichment-only
fetcher" that "only operates in batch mode." This is no longer true.

**Current** (lines 750-755):

> the source identifier corresponds to a registered fetcher that does
> not implement `fetch_single()` (e.g., enrichment-only fetchers like
> `sync_redhat_cves` which use `cve_source_type = "redhat"` but only
> operate in batch mode). This helps users understand why a source
> visible in CVE fetch status cannot be targeted for on-demand refetch.
> Both cases map to the same HTTP 422 / `CVE_INVALID_SOURCE` error code.

**Replacement**: remove error case (b) entirely. After this change, all
registered CVE fetchers implement `fetch_single()` — the
"enrichment-only fetcher without `fetch_single`" category is empty.
The only remaining error case is (a): the source identifier is
completely unrecognized (not a registered `CVESourceType`). Simplify
the error detail description accordingly — the distinction between
"unrecognized source" and "recognized but no `fetch_single`" no longer
applies.

#### 4b. Enrichment catch-up step — remove (lines 906-925)

Remove step 2 ("Asynchronous — enrichment catch-up") and update the
surrounding text. The two remaining steps are:

1. `trigger_on_demand_fetch(cve_id)` — CVE-level discovery and
   enrichment
2. `recalculate_cvss_chain(ticket_id)` — derived data from existing
   CVSS assessments

**Current** (lines 896-925):

> After `trigger_on_demand_fetch()`, endpoint handlers MUST perform two
> additional steps:
>
> 1\. **Synchronous — CVSS chain recalculation**: [...]
>
> 2\. **Asynchronous — enrichment catch-up**: enqueue `fetch_single` for
>    every registered fetcher [...] `fetch_single_redhat` [...]
>
> **Relationship between the three dispatch steps:**
>
> - `trigger_on_demand_fetch()` handles **CVE-level discovery** [...]
> - `recalculate_cvss_chain()` handles **ticket-level derived data** [...]
> - `fetch_single` handles **ticket-level enrichment** [...]

**Replacement**:

> After `trigger_on_demand_fetch()`, endpoint handlers MUST perform one
> additional step:
>
> 1\. **Synchronous — CVSS chain recalculation**: call
>    `ticket_mutations.recalculate_cvss_chain(ticket_id)` to calculate
>    severity and eligibility from any CVSS data already available for the
>    CVE (NVD assessments may already be persisted from periodic sync).
>    This is safe even if the CVE has no CVSS data yet — the function
>    produces no mutations in that case.
>
> **Relationship between the two dispatch steps:**
>
> - `trigger_on_demand_fetch()` handles **CVE-level discovery and
>   enrichment** — fetches the CVE record itself from all sources that
>   implement `fetch_single()` (NVD, MITRE, kernel, Red Hat)
> - `recalculate_cvss_chain()` handles **ticket-level derived data** —
>   severity and eligibility from whatever CVSS data already exists in
>   the database. See [cvss-scoring.md](cvss-scoring.md) for the chain
>   definition and CVSS-specific rationale

#### 4c. UpsertResult section — update (lines 1203-1208)

Update the "enrichment-only fetchers" example in the UpsertResult
section — Red Hat is no longer enrichment-only. Use a generic
description or reference future fetchers (CISA KEV, EPSS) as examples
of fetchers that manage their own metrics.

---

### 5. `docs/features/tickets/ticket-service.md`

#### 5a. Reactivation hook — update (lines 536-544)

**Current** (lines 536-544):

> 2\. **Asynchronous — per-ticket external data fetch**: enqueue
>    `fetch_single` for every registered fetcher that exposes the
>    capability, passing the `ticket_id`. This catches up on external data
>    not fetched during the inactive period (e.g., Red Hat CVSS updates —
>    the `sync_redhat_cves` fetcher scopes to active tickets and skips
>    Ignored/Duplicated ones). See
>    [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
>    ("Per-Ticket Catch-Up: `fetch_single` Capability") for the capability
>    contract.

**Replacement**:

> 2\. **Asynchronous — per-ticket catch-up**: enqueue `catch_up()` for
>    every registered fetcher via `get_catch_up_fetchers()`, passing the
>    `ticket_id`. This catches up on external data not fetched during the
>    inactive period (e.g., Red Hat CVSS updates — the `sync_redhat_cves`
>    fetcher scopes to active tickets and skips Ignored/Duplicated ones).
>    See
>    [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
>    ("Per-Ticket Catch-Up: `catch_up()` Method") for the method contract.

---

### 6. `docs/features/tickets/ticket-mutations.md`

#### 6a. Post-regression hook — update (lines 258-273)

**Current** (lines 258-273):

> 2\. Enqueue `fetch_single` for every registered fetcher that exposes the
>    capability. Same mechanism as the un-ignore/un-duplicate hook (see
>    [ticket-service.md](ticket-service.md), "Ticket Reactivation").
>
> **Pattern for callers**:
>
> ```python
> old_status = ticket.status
> reconcile_ticket_status(ticket, db)
> new_status = ticket.status
> if old_status == TicketStatus.RESOLVED and new_status in (
>     TicketStatus.NEW, TicketStatus.ANALYSIS, TicketStatus.ANALYZED
> ):
>     default_cvss_version = await settings_service.get_default_cvss_version(db)
>     recalculate_cvss_chain(db, ticket_id=ticket.id, default_cvss_version=default_cvss_version)
>     # enqueue fetch_single for all capable fetchers (async)
> ```

**Replacement**:

> 2\. Enqueue `catch_up()` for every registered fetcher via
>    `get_catch_up_fetchers()`. Same mechanism as the
>    un-ignore/un-duplicate hook (see
>    [ticket-service.md](ticket-service.md), "Ticket Reactivation").
>
> **Pattern for callers**:
>
> ```python
> old_status = ticket.status
> reconcile_ticket_status(ticket, db)
> new_status = ticket.status
> if old_status == TicketStatus.RESOLVED and new_status in (
>     TicketStatus.NEW, TicketStatus.ANALYSIS, TicketStatus.ANALYZED
> ):
>     default_cvss_version = await settings_service.get_default_cvss_version(db)
>     recalculate_cvss_chain(db, ticket_id=ticket.id, default_cvss_version=default_cvss_version)
>     # enqueue catch_up for all registered fetchers (async, post-commit)
> ```

---

### 7. `docs/data-sources.md`

#### 7a. Red Hat fetcher row — update (line 790)

**Current**:

> | `sync_redhat_cves` | Red Hat Security Data | Daily at 03:00 UTC | None | Undocumented; Sentinel uses 2s delay between requests | CVSS Red Hat, CWE, references | [cvss-scoring.md](features/tickets/cvss-scoring.md#fetcher-sync_redhat_cves) | Partial |

**Replacement**:

> | `sync_redhat_cves` | Red Hat Security Data | Daily at 03:00 UTC | None | Undocumented; Sentinel uses 2s delay between requests | CVSS Red Hat, CWE, references, best-effort package names | [cve-tracking.md](features/tickets/cve-tracking.md#fetcher-sync_redhat_cves) | Complete |

#### 7b. Red Hat "Relevant data" — update (line 107)

In the "Red Hat Security Data" section (line 107), update "Relevant
data" from "CVSS v3.1 base scores and scoring vectors" to "CVSS v2.0
and v3.1 base scores and scoring vectors, CWE identifiers, reference
URLs, source package names."

---

### 8. Non-CVE fetcher specs — `catch_up()` placeholder sections

For each non-CVE fetcher that needs a custom `catch_up()` override, add
a `#### Catch-Up` subsection within the fetcher definition. Each
placeholder follows a common template describing the method, scope, and
noting that detailed specification is deferred to implementation.

#### 8a. `docs/features/packages/ibs-track-release-detection.md`

Insert after the existing catch-up note at lines 209-211 (replace the
2-line note), before `#### Metrics` at line 213:

> #### Catch-Up
>
> `DetectIbsTrackReleases` implements `catch_up()` as a custom override
> (not the default CVE fetcher implementation). See
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> ("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
> contract.
>
> **Scope**: extracts the ticket's `TicketPackageTrack` records and
> checks IBS for source changes on each codestream, using the same
> diff-based detection logic as `execute()` but scoped to a single
> ticket.
>
> **Detailed specification**: to be defined during implementation.

#### 8b. `docs/features/packages/ibs-product-release-detection.md`

Insert after the properties table (line 257), before `#### Metrics` at
line 259:

> #### Catch-Up
>
> `DetectIbsProductReleases` implements `catch_up()` as a custom
> override. See
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> ("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
> contract.
>
> **Scope**: extracts the ticket's `TicketPackageProduct` records and
> checks `updateinfo.xml` from each product's update repository for
> advisories referencing the ticket's CVE.
>
> **Detailed specification**: to be defined during implementation.

#### 8c. `docs/features/packages/ibs-submission-tracking.md`

Insert after `#### Algorithm` (line 1011), before `#### Metrics` at
line 1013:

> #### Catch-Up
>
> `SyncIbsRequests` implements `catch_up()` as a custom override. See
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> ("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
> contract.
>
> **Scope**: extracts the ticket's codestream names and queries the IBS
> Request Search API to discover and correlate submission requests
> (SRs) and release requests (RRs) that were created or changed while
> the ticket was inactive.
>
> **Detailed specification**: to be defined during implementation.

#### 8d. `docs/features/packages/product-lifecycle-transitions.md`

Insert after the schedule line (line 80):

> #### Catch-Up
>
> `EvaluateLifecycleTransitions` implements `catch_up()` as a custom
> override. See
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> ("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
> contract.
>
> **Scope**: extracts the ticket's `TicketPackageProduct` records and
> re-evaluates lifecycle phase and eligibility for each product. While
> the ticket was inactive, products may have transitioned between
> lifecycle phases (e.g., entered LTSS or reached end-of-life),
> affecting eligibility thresholds.
>
> **Detailed specification**: to be defined during implementation.

#### 8e. `docs/features/packages/package-bugowner.md`

Insert after the properties table (line 263), before
`### Operation 1: Cleanup` at line 265:

> #### Catch-Up
>
> `SyncIbsBugowners` implements `catch_up()` as a custom override. See
> [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
> ("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
> contract.
>
> **Scope**: extracts the ticket's package names and refreshes the
> bugowner cache for each package from IBS. While the ticket was
> inactive, bugowner assignments may have changed.
>
> **Detailed specification**: to be defined during implementation.

---

### 9. Review files

#### 9a. `docs/reviews/cvss-scoring.md` — mark GAP-CVS-008 RESOLVED

**Current** (lines 15-20):

> ### GAP-CVS-008 — `sync_cvss_redhat` fetcher error handling explicitly marked TBD (High)
>
> **Category**: Unspecified error paths
> **Status**: OPEN
>
> `sync_cvss_redhat` fetcher error handling is explicitly marked "TBD" in the spec. Multiple failure modes are unspecified: HTTP 404 (CVE not in Red Hat's database — should existing assessment be deleted or preserved?), HTTP 429, HTTP 5xx, network timeouts, and malformed responses.

**Replacement**:

> ### GAP-CVS-008 — `sync_redhat_cves` fetcher error handling explicitly marked TBD (High)
>
> **Category**: Unspecified error paths
> **Status**: RESOLVED — Error handling fully specified in `cve-tracking.md` (Fetcher: `sync_redhat_cves`, Error Handling section): HTTP 404 and no-CVSS-fields map to `missing` (existing assessments preserved), HTTP 429/5xx/timeout retry 3x then `failure`, unparseable data is non-retryable `failure`, batch run uses per-CVE error handling with 3-consecutive-failure abort

#### 9b. `docs/reviews/ticket-mutations.md` — add TKM-GAP-18

Insert after the last Gap Analysis finding (after line 77 — TKM-GAP-17
RESOLVED), before the `---` separator at line 79:

> ### TKM-GAP-18 — Post-regression catch-up delegation is caller-dependent (Medium)
>
> **Category**: Missing error paths
> **Status**: OPEN
>
> The spec delegates regression detection to **callers** of `reconcile_ticket_status()` — each caller must check old vs new status and enqueue catch-up tasks. The spec does not enumerate which callers are responsible for this check. A new caller of `reconcile_ticket_status()` could omit the post-regression hook and silently skip catch-up enqueuing. Consider moving regression detection inside `reconcile_ticket_status()` itself (returning old/new status or emitting a signal) so the hook is automatic. This gap predates the `catch_up()` refactoring and is not introduced by it.

---

## Application Order

All changes in a single pass, ordered to avoid intermediate
inconsistencies:

1. **`fetcher-infrastructure.md`** (1a + 1b + 1c + 1d) — establishes
   the new `catch_up()` architecture and updates CVE fetcher examples.
   Must be first because all other specs reference it
2. **`cve-tracking.md`** (2a) — receives the full Red Hat fetcher
   definition. Depends on step 1 for cross-references
3. **`cvss-scoring.md`** (3a + 3b + 3c + 3d) — trims Red Hat sections
   and updates catch-up references. Depends on steps 1 and 2 (points
   to both)
4. **`cve-service.md`** (4a + 4b) — removes enrichment catch-up step.
   Depends on step 1 (all CVE fetchers now have `fetch_single`, so
   `trigger_on_demand_fetch()` covers them all)
5. **`ticket-service.md`** (5a) — renames invocation point references.
   Depends on step 1
6. **`ticket-mutations.md`** (6a) — renames invocation point references.
   Depends on step 1
7. **`data-sources.md`** (7a) — updates Red Hat row. Depends on step 2
8. **Non-CVE fetcher specs** (8a-8e) — adds catch-up placeholders.
   Depends on step 1
9. **Review files** (9a + 9b) — updates findings. Independent

## Post-Application

1. Run `@spec-coherence-reviewer` on each affected spec (one session per
   spec): `fetcher-infrastructure.md`, `cvss-scoring.md`,
   `cve-tracking.md`, `cve-service.md`, `ticket-service.md`,
   `ticket-mutations.md`
2. Run `@docs-placement-reviewer` on `fetcher-infrastructure.md` to
   verify catch-up mechanism is correctly centralized
3. Delete draft files:
   - `docs/drafts/catch-up-architecture.md`
   - `docs/drafts/redhat-cvss-fetcher-spec-changes.md`
   - `docs/drafts/catchup-redhat-merge.md` (this file)

## Supersedes

- `docs/drafts/catch-up-architecture.md`
- `docs/drafts/redhat-cvss-fetcher-spec-changes.md`
