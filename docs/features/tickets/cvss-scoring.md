# CVSS Scoring

## Purpose

Manage CVSS (Common Vulnerability Scoring System) assessments from multiple
providers for each CVE. Sentinel ingests CVSS data from external sources,
allows vulnerability analysts (VAs) to provide SUSE's own assessment, and uses
the scores to derive severity, determine product eligibility, and control
ticket workflow progression.

## Key Principles

1. **Multi-provider**: each CVE can have CVSS assessments from multiple
   providers (NVD, CNA vendors, Red Hat, SUSE). Each assessment is stored
   independently.
2. **Multi-version**: Sentinel supports CVSS v3.1 and v4.0. A single provider
   may supply assessments for one or both versions. Other versions (e.g.,
   v2.0) may arrive from external sources and are stored and displayed but
   not used for decisions.
3. **Configurable default version**: a system-wide setting determines which
   CVSS version is used for all automated decisions (severity, eligibility,
   notifications). Initially set to `3.1`, changeable by Admin. See
   `docs/features/platform/system-settings.md`.
4. **SUSE assessment is authoritative**: when Sentinel needs a CVSS score to
   make a decision, it follows one of the two resolution strategies defined
   below (Severity Resolution Cascade or Eligibility Score Resolution),
   both of which prioritize SUSE's assessment.
5. **Default version awareness**: every component of the system that needs
   a CVSS score for any decision MUST resolve the version from the system
   configuration — never hardcode `3.1` or `4.0`.
6. **Always derived from vector**: the `vector_string` is the single
   source of truth for all CVSS assessments. Score, version, and
   severity are never accepted as independent inputs — they are always
   parsed and derived locally from the vector string using the `cvss`
   library. Providers that supply only a numeric score without a valid
   vector string are not imported.

## CVSS Score Resolution

Sentinel uses two distinct resolution strategies depending on the consumer.
Each is described below.

### Severity Resolution Cascade

Used for: severity derivation, display, notifications, and any future
informational/triage logic.

This cascade resolves the best available CVSS score, preferring SUSE's
assessment and the configured default version, but falling back to other
providers and versions to maximize informational coverage:

1. **SUSE assessment, default version**. If SUSE has published an
   assessment for the configured default CVSS version, use this score.
2. **SUSE assessment, other version**. If SUSE has published an assessment
   for a non-default version, use it. If multiple non-default versions
   exist, prefer the most recent version number.
3. **Highest provider, default version**. If at least one external provider
   has an assessment for the default version, use the highest score among
   them.
4. **Highest provider, other version**. If at least one external provider
   has an assessment for any non-default version, use the highest score
   among those. If multiple non-default versions exist, prefer the most
   recent version number; within the same version, prefer the highest
   score.
5. **Absent**. No provider has published any assessment for any supported
   version. The score is treated as absent (severity = `None`).

**Cross-version severity mapping**: when the resolved score comes from a
version other than the default (steps 2 or 4), severity is mapped using
the rating scale thresholds specific to that score's version. Sentinel
uses SUSE-defined severity thresholds per version. Until explicit
thresholds are configured, the standard CVSS thresholds for each version
apply (see Severity Rating Scale above).

This cascade is implemented by `resolve_severity_score` in
`services/cvss.py`.

### Eligibility Score Resolution

Used for: product eligibility threshold comparison (see
`docs/features/packages/package-model.md`, Axis 2: Eligibility).

This resolution uses **only** the SUSE assessment of the configured default
CVSS version. No fallback to other providers or other versions is applied:

1. **SUSE assessment, default version**. If present, use this score.
2. **Not resolvable**. If the SUSE assessment for the default version does
   not exist (for any reason: the ticket has no associated CVE, the CVE
   has no SUSE assessment, or SUSE has not scored the default version),
   the score is treated as **10.0** (worst-case, conservative approach —
   the product is always eligible unless excluded by the Reactive LTSS
   override).

**Rationale**: eligibility drives automated decisions about which products
receive a fix. Only the authoritative internal assessment (SUSE) should
determine this. External provider scores are informational and useful for
triage (severity cascade) but not authoritative for eligibility decisions.
The 10.0 fallback ensures that products are never silently excluded before
SUSE has assessed the vulnerability — blocked resolution is visible and
correctable; silent omission is not.

This cascade is implemented by `resolve_eligibility_score` in
`services/cvss.py`.

## Providers

### Provider Model

Each CVSS assessment is identified by the tuple `(cve_id, provider_name,
cvss_version)`. The `provider_name` is a human-readable string stored
directly on the assessment record.

### External Providers

#### NVD (Primary)

- **Source**: NVD REST API v2 (`services.nvd.nist.gov/rest/json/cves/2.0`)
- **Type**: independent assessment by NVD analysts
- **Identified by**: `source` field with value `nvd@nist.gov`,
  `type: "Primary"` in the API response
- **Provider name in Sentinel**: `"NVD"`
- **CVSS versions**: currently v3.1; v4.0 expected in the future
- **Fetch mechanism**: extracted from `cvssMetricV31` and `cvssMetricV40`
  arrays in the NVD CVE API response

#### CNA (via NVD Secondary)

- **Source**: NVD REST API v2, assessments with `type: "Secondary"`
- **Type**: assessment provided by the CVE Numbering Authority (the vendor
  or organization that assigned the CVE)
- **Identified by**: `source` field containing the CNA's email (e.g.,
  `secure@intel.com`), `type: "Secondary"`
- **Provider name in Sentinel**: resolved to a human-readable name using the
  NVD Source API (`services.nvd.nist.gov/rest/json/source/2.0`). For
  example, `secure@intel.com` resolves to `"Intel Corporation"`
- **CVSS versions**: varies by CNA; may include v3.1, v4.0, or both
- **Name resolution**: during CVE sync, the ingestion service resolves
  `source` email addresses to display names via the NVD Source API. See
  `docs/features/tickets/cve-tracking.md` (NVD Source API Caching) for
  the caching strategy
- **Deduplication with direct sources**: if a direct source (e.g., Red Hat)
  provides an assessment for the same provider and CVSS version, the direct
  source takes priority and overwrites the NVD Secondary data

#### Red Hat

- **Source**: Red Hat Security Data API
  (`access.redhat.com/hydra/rest/securitydata/cve/{CVE-ID}.json`)
- **Type**: independent assessment by Red Hat Product Security
- **Provider name in Sentinel**: `"Red Hat"`
- **CVSS versions**: currently v3.1 only (field `cvss3` in the response).
  v4.0 will be supported when Red Hat adds it
- **Response format**: the `cvss3` object contains `cvss3_base_score`
  (string), `cvss3_scoring_vector` (string), and `status` (`"draft"` or
  `"verified"`). Only the vector string is used — the score is recomputed
  locally by the `cvss` library for consistency
- **Deduplication**: if Red Hat also appears as a CNA Secondary in NVD
  (same provider name `"Red Hat"`), the direct fetch from the Red Hat API
  takes priority and overwrites the NVD Secondary record

### Internal Provider

#### SUSE

- **Source**: manual input by VA
- **Provider name in Sentinel**: `"SUSE"`
- **CVSS versions**: the VA MUST provide both v3.1 and v4.0 assessments
  before the ticket can progress beyond Analysis (see Workflow Gates)
- **Input method**: the VA enters a CVSS vector string (which embeds the
  version in its prefix); the backend derives the CVSS version from the
  prefix and calculates the score automatically using the `cvss` library
- **Editability**: CVSS mutations are subject to
  `ensure_ticket_operable()` when the CVE has an associated ticket —
   mutations are rejected with `409 TICKET_NOT_MUTABLE` if the ticket is
   in Ignored or Duplicated status. Ticketless CVEs are always mutable. Changes trigger
  severity and eligibility recalculation

## CVSS Versions

### CVSS v3.1 — Base Metrics

8 base metrics parsed from the vector string:

| Abbreviation | Metric               | Possible Values                  |
|--------------|----------------------|----------------------------------|
| AV           | Attack Vector        | Network, Adjacent, Local, Physical |
| AC           | Attack Complexity    | Low, High                        |
| PR           | Privileges Required  | None, Low, High                  |
| UI           | User Interaction     | None, Required                   |
| S            | Scope                | Unchanged, Changed               |
| C            | Confidentiality      | None, Low, High                  |
| I            | Integrity            | None, Low, High                  |
| A            | Availability         | None, Low, High                  |

### CVSS v4.0 — Base Metrics

11 base metrics parsed from the vector string:

| Abbreviation | Metric                       | Possible Values                  |
|--------------|------------------------------|----------------------------------|
| AV           | Attack Vector                | Network, Adjacent, Local, Physical |
| AC           | Attack Complexity            | Low, High                        |
| AT           | Attack Requirements          | None, Present                    |
| PR           | Privileges Required          | None, Low, High                  |
| UI           | User Interaction             | None, Passive, Active            |
| VC           | Vuln. Confidentiality Impact | None, Low, High                  |
| VI           | Vuln. Integrity Impact       | None, Low, High                  |
| VA           | Vuln. Availability Impact    | None, Low, High                  |
| SC           | Sub. Confidentiality Impact  | None, Low, High                  |
| SI           | Sub. Integrity Impact        | None, Low, High                  |
| SA           | Sub. Availability Impact     | None, Low, High                  |

### Severity Rating Scale

Both CVSS v3.1 and v4.0 use the same severity rating scale:

| Score Range | Severity |
|-------------|----------|
| 0.0         | None     |
| 0.1 – 3.9  | Low      |
| 4.0 – 6.9  | Medium   |
| 7.0 – 8.9  | High     |
| 9.0 – 10.0 | Critical |

## Severity Derivation

The `severity` field on the CVE table is a denormalized field, always
derived from CVSS assessments. It is never set manually.

**Note**: for tickets without a CVE, severity is determined by the
`severity_override` field on the Ticket, set manually by the VA. The
CVE severity derivation described below applies only to tickets with an
associated CVE. See `docs/features/tickets/tickets.md` (Severity Resolution)
for the unified resolution logic.

### Calculation Rules

1. Resolve the CVSS score using the **Severity resolution cascade** (see
   above): SUSE default version → SUSE other version → highest provider
   default version → highest provider other version → absent
2. If a score is found: map it to a severity using the rating scale
   thresholds for the version of that score
3. If no score is found (absent): severity is `None`

### When Severity is Recalculated

Severity is recalculated whenever:

- A CVSS assessment is added, modified, or removed for the CVE
- The system-wide default CVSS version is changed by an Admin
- The SUSE assessment is added or modified by a VA
- A ticket is reactivated from Ignored or Duplicated status —
  `recalculate_cvss_cascade()` is called synchronously during the
  reactivation, plus `fetch_single` tasks are enqueued for catch-up
- A CVE is associated with a ticket (or a ticket is created with a
   CVE) — `recalculate_cvss_cascade()` is called synchronously after
   commit, plus `fetch_single` tasks are enqueued for enrichment
   catch-up
- A ticket regresses from Resolved to an active status —
   `recalculate_cvss_cascade()` is called synchronously by the caller
   of `reconcile_ticket_status()` when a backward transition from
   Resolved is detected

### Severity Override by CVSS

The SUSE CVSS assessment is mandatory for ticket progression (see Workflow
Gates). Once both SUSE assessments (v3.1 and v4.0) are provided, the
severity is always calculated automatically from the SUSE score of the
default version. There is no manual severity selection.

## Workflow Gates

### SUSE CVSS Required for Ticket Progression (Tickets with CVE)

The SUSE CVSS v3.1 and v4.0 assessments are a prerequisite for the
Analysis → Analyzed gate (see [`tickets.md`](tickets.md), Gate condition
\#4). This ensures severity and eligibility are computable before the
ticket progresses.

**Tickets without CVE**: this gate does not apply. Instead, the VA must
set `severity_override` before the ticket can progress. See
[`tickets.md`](tickets.md) (Gate: Analysis → Analyzed) for the full gate
conditions applicable to all ticket types.

## Eligibility Threshold

Product eligibility for security updates is determined by comparing a CVSS
score against the product's `cvss_threshold` (from AIMAAS). The score
selection follows the **Eligibility Score Resolution** cascade (see above)
— no fallback to other providers is applied.

See `docs/features/packages/package-model.md` for the full eligibility logic.

## Data Sync

### NVD Sync (Incremental)

The `sync_cves_nvd` fetcher runs every 6 hours and ingests CVSS
assessments (Primary and Secondary) from the NVD REST API v2. CNA
display names for Secondary assessments are resolved via the NVD Source
API. Changes are persisted via `cve_service` (see
[`cve-service.md`](cve-service.md)). If any CVSS assessment changed for
a CVE with an active ticket, the recalculation cascade is triggered (see
Recalculation Cascade below).

For the full fetcher definition — including the incremental algorithm,
NVD Source API caching strategy, first-run behavior, and error handling
— see [`cve-tracking.md`](cve-tracking.md) (Fetcher:
`sync_cves_nvd`).

### Red Hat Sync

Red Hat's API does NOT support incremental fetching (no `modified_after`
parameter). The API provides `after`/`before` parameters for creation date
only.

**Strategy — initial fetch**:

When a ticket is created with a CVE, or a CVE is associated with an
existing ticket, a `fetch_single_redhat` task is enqueued to retrieve
the Red Hat CVSS for that CVE (see Sub-operation: `fetch_single_redhat`
below). The same mechanism is used for catch-up when a ticket is
reactivated from Ignored/Duplicated or regresses from Resolved. This
makes `fetch_single_redhat` the single on-demand mechanism for Red Hat
data retrieval, regardless of the trigger.

The task:

1. Queries the Red Hat API for the ticket's CVE:
   ```
   GET /hydra/rest/securitydata/cve/{CVE-ID}.json
   ```
2. Extracts `cvss3.cvss3_scoring_vector`
3. Persists the assessment via `cve_service` (see
   [`cve-service.md`](cve-service.md)) with `provider = "Red Hat"`.
   `cvss_version` and `score` are derived from the vector automatically
4. If the assessment differs from an existing NVD Secondary with the same
   provider name → overwrites

**Scope gap**: the fetch scope is "CVEs with active tickets (New,
Analysis, Analyzed)" due to Red Hat API rate limits (per-CVE lookup, no
bulk/incremental endpoint). CVEs whose tickets are in Ignored,
Duplicated, or Resolved status do NOT receive Red Hat CVSS updates
during the inactive period. This gap is mitigated by the
`fetch_single_redhat` mechanism: when a ticket is reactivated, a
`fetch_single_redhat` task is enqueued to retrieve the latest Red Hat
data for that CVE (see Ticket Reactivation: CVSS Catch-Up below).

**Strategy — periodic re-fetch**:

1. Once per day, a Celery task iterates over all CVEs with active tickets
   (status: New, Analysis, Analyzed — see `docs/data-model.md` for the
   authoritative definition)
2. For each CVE, fetch the Red Hat CVSS:
   ```
   GET /hydra/rest/securitydata/cve/{CVE-ID}.json
   ```
3. A configurable delay (controlled by the `throttle_delay_seconds`
   custom setting, default: **2 seconds**) is added between requests to
   avoid overloading the Red Hat API. Speed is not important.
4. Compare the fetched vector with the stored vector
5. If different → update the assessment via
   `ticket_mutations.update_cvss_assessment()` (triggers recalculation)
6. `last_redhat_sync_at` is derived from the `started_at` timestamp of
   the most recent successful `FetcherRun` for the `sync_cvss_redhat`
   fetcher. Used for operational monitoring only (Red Hat sync is not
   incremental)

### Sync Scope

CVSS sync (both NVD incremental and Red Hat re-fetch) is performed only
for CVEs with **active tickets** — tickets in status `New`, `Analysis`, or
`Analyzed` (see `docs/data-model.md` for the authoritative definition of
active tickets).

When a ticket transitions to `Resolved`, `Ignored`, or `Duplicated`, Sentinel
stops monitoring CVSS updates for that CVE. The existing CVSS data remains
in the database but is no longer refreshed.

### CVSS Fetcher Data Convention

CVSS fetchers MUST separate data persistence (`CVECVSSAssessment` records)
from recalculation of derived data (severity, eligibility, ticket status):

1. **Persistence scope**: the ticket-status filter ("active tickets only")
   applies ONLY to the recalculation cascade, NEVER to the persistence of
   `CVECVSSAssessment` records — unless the external API's design or rate
   limits make broader persistence impractical (e.g., per-CVE lookup APIs
   with no bulk/incremental endpoint).
2. **Gap documentation**: when a fetcher's fetch scope is narrower than
   "all CVEs with tickets" due to API constraints, the fetcher's
   specification MUST document the gap explicitly and the system MUST
   provide a catch-up mechanism (via `fetch_single`) for tickets
   reactivated after a period of inactivity.
3. **Goal**: `CVECVSSAssessment` records are as complete as possible
   regardless of ticket lifecycle state. Reopened tickets converge to
   accurate derived data quickly via the synchronous recalculation
   cascade (immediate best-effort) followed by asynchronous `fetch_single`
   tasks (data catch-up).

## Recalculation Cascade

When a CVSS assessment changes (added, modified, or removed) for a CVE
with an active ticket, Sentinel performs the following recalculation:

1. **Recalculate severity**: call `resolve_severity_score()` (5-step
   severity cascade) to determine the new resolved score. Map the result
   to a severity label via `calculate_severity()`. If severity changed,
   update the CVE's `severity` field.
2. **Recalculate product eligibility**: call `resolve_eligibility_score()`
   (2-step SUSE-only cascade — separate call with different semantics; the
   eligibility score may differ from the severity score when SUSE has not
   assessed the default version). Re-evaluate the `eligible` flag for every
   `TicketPackageProduct` linked to the ticket, applying the eligibility
   rules defined in [`package-model.md`](../packages/package-model.md)
   (Axis 2: Eligibility).
   *(Note: because of the strictly unidirectional dependency from `package_service` to `ticket_mutations`, these eligibility updates are executed inline directly within the `ticket_mutations` module during CVSS mutations. See [`ticket-mutations.md`](ticket-mutations.md) for the module boundary contract.)*
3. **Ticket status re-evaluation**: call `reconcile_ticket_status()` to
   re-evaluate the ticket status based on current gate conditions (see
   [`ticket-mutations.md`](ticket-mutations.md)).
   **Note**: for VA-initiated SUSE CVSS changes on a Resolved ticket,
   this re-evaluation may cause a status regression. Automated sync (NVD,
   Red Hat) and default CVSS version changes only process active tickets
   (New, Analysis, Analyzed) — Resolved tickets are excluded from those
   scopes.
4. **Audit trail**: create `TicketAuditEvent` records for each change
   (severity, product eligibility, ticket status). See
   [`ticket-mutations.md`](ticket-mutations.md) for the per-operation
   audit contract and [`ticket-audit-log.md`](ticket-audit-log.md) for
   event field semantics.

## API Endpoints

### Get CVSS Assessments for a CVE

```
GET /api/v1/cves/{cve_id}/cvss
```

Public (no authentication required).

The `{cve_id}` path parameter accepts either the CVE's UUID or the
CVE-ID string (e.g., `CVE-2025-1234`). See `docs/api-spec.md` (CVE
Identifier Resolution) for the dual-identifier resolution pattern.

Response: list of all CVSS assessments for the CVE, grouped by version.
Pagination is intentionally omitted — the number of CVSS assessments per
CVE is naturally bounded (one per provider-version combination, typically
fewer than 20 records).

```json
{
  "data": {
    "assessments": [
      {
        "id": "uuid",
        "provider_name": "NVD",
        "cvss_version": "3.1",
        "score": 8.1,
        "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "metrics": {
          "attack_vector": "Network",
          "attack_complexity": "Low",
          "privileges_required": "None",
          "user_interaction": "None",
          "scope": "Unchanged",
          "confidentiality": "High",
          "integrity": "High",
          "availability": "High"
        },
        "created_at": "2025-03-15T10:30:00Z",
        "updated_at": "2025-03-15T10:30:00Z"
      }
    ],
    "default_cvss_version": "3.1",
    "resolved_score": 8.1,
    "resolved_provider": "NVD",
    "resolved_severity": "High"
  }
}
```

The `resolved_*` fields reflect the result of the **severity** resolution
cascade for the current default version — identifying which score and
provider Sentinel uses for severity derivation and display. These fields do
NOT reflect the eligibility resolution (which is SUSE-only; see Eligibility
Score Resolution).

### Set or Update SUSE CVSS Assessment

```
POST /api/v1/cves/{cve_id}/cvss/suse
```

Request body:

```json
{
  "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
}
```

- `vector`: valid CVSS vector string. The CVSS version is derived from the
  vector prefix (`CVSS:4.0/` → 4.0, `CVSS:3.1/` → 3.1, `CVSS:3.0/` → 3.0,
  no prefix → 2.0). The base score is computed automatically by the `cvss`
  library.

The backend parses the vector, derives the version and score, and saves the
assessment. If an existing SUSE assessment for the derived version exists, it
is updated (upsert). Triggers recalculation cascade.

Response (200 OK): the created or updated assessment object wrapped in
the standard `{"data": ...}` envelope.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVE_NOT_FOUND` | CVE not found or inaccessible (see `docs/api-spec.md`, CVE Accessibility Check) |
| 409 | `TICKET_NOT_MUTABLE` | Associated ticket is in Ignored or Duplicated status |
| 422 | `CVSS_INVALID_VECTOR` | Vector string is malformed or unparseable |

The `409` error applies only when the CVE has an associated ticket. CVEs
without an associated ticket are always mutable.

**`Capability: manage_cvss`**

### Delete SUSE CVSS Assessment

```
DELETE /api/v1/cves/{cve_id}/cvss/suse/{cvss_version}
```

Removes the SUSE CVSS assessment for the specified version. Triggers
recalculation cascade. The ticket may no longer meet the progression gate
requirements.

Response: 204 No Content.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVE_NOT_FOUND` | CVE not found or inaccessible (see `docs/api-spec.md`, CVE Accessibility Check) |
| 404 | `RESOURCE_NOT_FOUND` | No SUSE assessment exists for the specified version |
| 409 | `TICKET_NOT_MUTABLE` | Associated ticket is in Ignored or Duplicated status |

The `409` error applies only when the CVE has an associated ticket. CVEs
without an associated ticket are always mutable.

**`Capability: manage_cvss`**

## Service Architecture

CVSS logic is split across two service modules with distinct
responsibilities:

### `services/cvss.py` — Pure Resolution Logic

This module contains **read-only, side-effect-free** functions that
implement the CVSS resolution and scoring algorithms. These functions
never mutate the database — they receive data and return results.

| Function                    | Input                                      | Output                          | Description                                              |
|-----------------------------|--------------------------------------------|---------------------------------|----------------------------------------------------------|
| `resolve_severity_score`    | CVE assessments, default CVSS version      | (score, provider) or None       | Implements the severity resolution cascade (5-step: SUSE default → SUSE other version → highest provider default → highest provider other → absent) |
| `resolve_eligibility_score` | CVE assessments, default CVSS version      | Decimal (score)                 | Implements the eligibility score resolution (2-step, SUSE-only: SUSE default version → 10.0 fallback). Always returns a value |
| `calculate_severity`        | CVSS score (float)                         | Severity enum                   | Maps score to severity using the rating scale            |
| `validate_cvss_vector`      | Vector string                              | Parsed metrics + version + calculated score | Parses vector, detects version from prefix, validates format, and computes the base score |

> **Input contract**: both `resolve_severity_score` and
> `resolve_eligibility_score` receive the **complete, unfiltered** set of
> all `CVECVSSAssessment` records associated with the CVE (from all
> providers and all CVSS versions), plus the system's configured default
> CVSS version. Filtering by provider and/or version is the internal
> responsibility of each function — never the caller's. Passing a
> pre-filtered subset is a caller bug, because it may alter fallback
> behavior (e.g., removing non-SUSE assessments would suppress the
> severity cascade's provider fallback steps). This design preserves
> function purity (database-free, side-effect-free) and encapsulates the
> resolution strategy entirely within each function.

These functions are used in two contexts:

1. **Read path** (API `GET .../cvss`): to compute the `resolved_score`,
   `resolved_provider`, and `resolved_severity` response fields without
   any side effects
2. **Write path** (via `ticket_mutations`): as building blocks for the
   recalculation cascade — `ticket_mutations` calls these functions to
   determine the new severity and eligibility, then persists the results

### `services/ticket_mutations.py` — CVSS Mutations

All operations that create, update, or delete `CVECVSSAssessment`
records MUST go through the `ticket_mutations` module. When a CVSS
mutation function is invoked, it conceptually: locks the ticket,
validates operability, persists the assessment change, resolves derived
data, emits audit events, and reconciles ticket status — all within a
single database transaction (atomicity guarantee).

Two resolution functions from `services/cvss.py` are invoked during
the write path, each serving a distinct purpose:

- **`resolve_severity_score()`** (5-step cascade): determines the
  resolved CVSS score used to derive `CVE.severity`. This is the
  exclusive source of truth for `CVE.severity` — `resolve_eligibility_score()`
  is never used for this purpose.
- **`resolve_eligibility_score()`** (2-step SUSE-only cascade):
  determines the score compared against product CVSS thresholds to
  evaluate `TicketPackageProduct.eligible`. This is the exclusive
  source of truth for product eligibility — `resolve_severity_score()`
  is never used for this purpose.

The resolution cascade logic is **never reimplemented** inside
`ticket_mutations` — it always delegates to `services/cvss.py`.

For per-function implementation details (parameters, pre-conditions,
step sequences), see `docs/features/tickets/ticket-mutations.md`.

### `services/settings.py` — System Settings

The default CVSS version is read from the `SystemSetting` table via a
dedicated settings service module. `services/cvss.py` does not access
`SystemSetting` directly — the caller (API endpoint or
`ticket_mutations` function) resolves the default version and passes it
as a parameter. This keeps `cvss.py` free of database dependencies and
makes it straightforward to test with any CVSS version.

## Cascade Execution Model

The recalculation cascade is a **synchronous service-layer operation**
executed within the same database transaction as the CVSS change that
triggered it. This guarantees atomicity: if the CVSS change is committed,
the severity, eligibility, and ticket state adjustments are committed
together.

**Exception — batch recalculation on default version change**: when the
Admin changes the default CVSS version (see `docs/features/platform/system-settings.md`),
the cascade must run for all active tickets. This batch operation is
executed as an asynchronous Celery task to avoid blocking the API
response. The task:

1. Iterates over all active tickets (New, Analysis, Analyzed)
2. For each ticket, calls
   `ticket_mutations.recalculate_cvss_cascade()` — a dedicated entry
   point that recalculates derived data without modifying any
   `CVECVSSAssessment` record (see
   `docs/features/tickets/ticket-mutations.md`)
3. Each ticket is processed in an **independent database transaction**
   (isolation: a failure on one ticket does not roll back others)
4. On error for a single ticket, the task logs the error with the
   ticket ID and continues with the remaining tickets
5. On completion, the task reports the total number of tickets processed,
   successes, and failures

## Ticket Reactivation: CVSS Catch-Up

When a ticket transitions from a non-active state (Resolved, Ignored,
Duplicated) to an active state (New, Analysis, Analyzed), two catch-up
mechanisms execute to reconcile CVSS-derived data:

1. **Synchronous** (within the reactivation transaction):
   `recalculate_cvss_cascade()` is called to reconcile derived data
   (severity, eligibility) with the current `default_cvss_version` and
   any `CVECVSSAssessment` updates that occurred while the ticket was
   inactive. This provides immediate best-effort accuracy using
   whatever assessment data is already persisted.

2. **Asynchronous** (enqueued after commit): `fetch_single` tasks are
   enqueued for every registered fetcher that exposes the `fetch_single`
   capability — not limited to CVSS fetchers. This catches up on data
   that was not fetched during the inactive period (e.g., Red Hat CVSS
   updates via `fetch_single_redhat`, IBS release detection, submission
   tracking). Each `fetch_single` task operates independently; if it
   discovers changed data, the normal mutation path handles the
   recalculation cascade.

The ticket may transition rapidly as async tasks complete (e.g.,
re-open → Analysis, then a fetch discovers a release → Resolved). This
is expected and correct behavior — the system converges to the accurate
state.

See [`ticket-service.md`](ticket-service.md) for the un-ignore /
un-duplicate hooks, [`ticket-mutations.md`](ticket-mutations.md) for
the post-regression hook and `recalculate_cvss_cascade()` contract, and
[`fetcher-infrastructure.md`](../platform/fetcher-infrastructure.md) for
the `fetch_single` capability contract.

## Background Tasks

The `sync_cves_nvd` fetcher (defined in
`docs/features/tickets/cve-tracking.md`) also produces CVSS assessments
during CVE ingestion. See "NVD Sync (Incremental)" above for the
consumer-oriented summary.

### Fetcher: `sync_cvss_redhat`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_cvss_redhat` |
| Class name | `SyncCvssRedhat` |
| `cve_source_type` | `"redhat"` |
| Schedule | Daily at 03:00 UTC (`0 3 * * *`) |
| Source | Red Hat Security Data API (`access.redhat.com/hydra/rest/securitydata`) |
| Scope | All CVEs with active tickets (New, Analysis, Analyzed) |
| Auth | None (public API) |
| Custom settings | Yes (see below) |

#### Algorithm

See "Red Hat Sync" section above for the full algorithm (initial fetch
and periodic re-fetch strategies).

#### Error Handling

TBD

#### Metrics

- `record_created`: N/A (Red Hat data is always an upsert against
  existing CVE records)
- `record_updated`: a Red Hat CVSS assessment was created or updated
  for a CVE
- `record_failed`: a CVE's Red Hat CVSS could not be fetched (API
  error, timeout, malformed response)

#### Custom Settings

This fetcher declares the following custom settings (see
`docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
Schema" for the schema structure and validation rules):

| Setting | Type | Default | Constraints | Description |
|---------|------|---------|-------------|-------------|
| `throttle_delay_seconds` | float | 2.0 | 0.1–30.0 | Delay between consecutive Red Hat API requests |

### Sub-operation: `fetch_single_redhat`

> **Note**: to be finalized — this is a template defining the interface
> and contract. Implementation details will be completed alongside the
> Red Hat fetcher.

A sub-operation task (not a `BaseFetcher` subclass — per the sub-operation
exception in
[`fetcher-infrastructure.md`](../platform/fetcher-infrastructure.md)).

- **Parameter**: `ticket_id` (UUID as string — matches the unified
  `fetch_single` interface defined in `fetcher-infrastructure.md`)
- **Behavior**: the task looks up the ticket to extract the `cve_id`
  from the ticket's associated CVE, then queries the Red Hat Security
  Data API (`GET /hydra/rest/securitydata/cve/{CVE-ID}.json`) for that
  CVE. If data is found and differs from the stored
  `CVECVSSAssessment`, persists/updates the assessment via
  `ticket_mutations.create_cvss_assessment()` or
  `ticket_mutations.update_cvss_assessment()`, which triggers the normal
  recalculation cascade.
- **Idempotent**: if the Red Hat data is unchanged from the stored
  assessment, no mutation occurs (no-op).
- **Trigger**: enqueued in three scenarios (not by Celery Beat):
  1. Ticket creation with CVE / CVE association with existing ticket
     (via `cve_service` endpoint handlers)
  2. Ticket reactivation from Ignored/Duplicated (via `ticket_service`
     reactivation hook)
  3. Ticket regression from Resolved (via `ticket_mutations`
     post-regression hook)
- **Metrics**: not tracked independently (sub-operation).

## Data Model

See `docs/data-model.md` for the full schema. This feature introduces the
`CVECVSSAssessment` table and modifies the `CVE` table.

## Security

- Viewing CVSS data: publicly accessible (no authentication required)
- Adding/editing/deleting SUSE CVSS: `manage_cvss` capability
- Changing default CVSS version: `manage_settings` capability (see
  `docs/features/platform/system-settings.md`)
- External CVSS data is read-only — cannot be modified through Sentinel

## Cross-references

- `docs/features/tickets/tickets.md` — Ticket lifecycle, gate conditions
  (Analysis → Analyzed, Analyzed → Resolved), centralized status evaluation
- `docs/features/tickets/ticket-mutations.md` — CVSS mutation functions,
  `recalculate_cvss_cascade()`, `reconcile_ticket_status()`, per-operation
  audit contract, module boundary, manual-zone exit operations
- `docs/features/tickets/ticket-service.md` — Non-gate ticket lifecycle
  operations, un-ignore / un-duplicate hooks
- `docs/features/tickets/ticket-audit-log.md` — `TicketAuditEvent` type
  contract, field semantics
- `docs/features/tickets/cve-service.md` — CVE Service Layer
  (`upsert_cve()`, `CVEIngestPayload`, Phase 1/Phase 2 transaction model)
- `docs/features/tickets/cve-tracking.md` — `sync_cves_nvd` fetcher
  definition (incremental algorithm, NVD Source API caching)
- `docs/features/packages/package-model.md` — Three Orthogonal Dimensions,
  Axis 2: Eligibility (rules, override model, Reactive LTSS)
- `docs/features/platform/system-settings.md` — `default_cvss_version`
  setting, batch recalculation trigger
- `docs/features/platform/fetcher-infrastructure.md` — `BaseFetcher`
  contract, `fetch_single` capability, sub-operation exception
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses), CVE Accessibility Check, CVE Identifier
  Resolution
