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
- **Editability**: the SUSE assessment can be modified at any time,
  regardless of ticket status. Changes trigger severity and eligibility
  recalculation

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
- The SUSE assessment is added or modified by an VA

### Severity Override by CVSS

The SUSE CVSS assessment is mandatory for ticket progression (see Workflow
Gates). Once both SUSE assessments (v3.1 and v4.0) are provided, the
severity is always calculated automatically from the SUSE score of the
default version. There is no manual severity selection.

## Workflow Gates

### SUSE CVSS Required for Ticket Progression (Tickets with CVE)

For tickets with an associated CVE, the ticket CANNOT transition from
`Analysis` to `Analyzed` (or any subsequent state) unless the VA has
provided BOTH:

- SUSE CVSS v3.1 assessment (vector string → calculated score)
- SUSE CVSS v4.0 assessment (vector string → calculated score)

This ensures that:

1. Every ticket with a CVE that progresses beyond Analysis has a
   SUSE-determined severity
2. The severity is always calculated (never manually selected)
3. Both CVSS versions are available for current and future use

**Tickets without CVE**: this gate does not apply. Instead, the VA must
set `severity_override` before the ticket can progress. See
`docs/features/tickets/tickets.md` (Gate: Analysis → Analyzed) for the full
gate conditions applicable to all ticket types.

### Severity Required

Severity is always required for the Analysis → Analyzed transition. For
tickets with a CVE, severity is derived from SUSE CVSS (which is
mandatory). For tickets without a CVE, severity must be set via
`severity_override`. A ticket with no severity (`None`) cannot progress.

## Eligibility Threshold

Product eligibility for security updates is determined by comparing a CVSS
score against the product's `cvss_threshold` (from AIMAAS). The score
selection follows the resolution cascade with a conservative fallback:

1. **SUSE assessment** of the default CVSS version → use this score
2. **Highest score** among all providers for the default version → use this
3. **No score available** → treat as **10.0** (worst-case; the product is
   always eligible)

This ensures that a CVE without any CVSS data is never excluded from a
product due to threshold rules.

See `docs/features/packages/package-model.md` for the full eligibility logic.

## Data Sync

### NVD Sync (Incremental)

The `sync_cves_nvd` fetcher runs every 6 hours and performs an
incremental sync of CVEs from the NVD REST API v2. During each sync, it
parses CVSS assessments (Primary and Secondary) from the `cvssMetricV*`
arrays into a `CVEIngestPayload` and passes them to
`cve_service.upsert_cve()` (see `docs/features/tickets/cve-service.md`).
The service distributes CVSS data to `CVECVSSAssessment` records via
`ticket_mutations.create_cvss_assessment()` in Phase 1. CNA display
names for Secondary assessments are resolved via the NVD Source API. If
any CVSS assessment changed for a CVE with an active ticket, the
recalculation cascade is triggered (see Recalculation Cascade below).

For the full fetcher definition — including the incremental algorithm,
NVD Source API caching strategy, first-run behavior, and error handling
— see `docs/features/tickets/cve-tracking.md` (Fetcher:
`sync_cves_nvd`).

### Red Hat Sync

Red Hat's API does NOT support incremental fetching (no `modified_after`
parameter). The API provides `after`/`before` parameters for creation date
only.

**Strategy — initial fetch**:

1. When a new ticket is created via `cve_service.upsert_cve()`, the Red
   Hat fetcher is triggered to fetch CVSS for that CVE:
   ```
   GET /hydra/rest/securitydata/cve/{CVE-ID}.json
   ```
2. Extract `cvss3.cvss3_scoring_vector`
3. Pass the vector to `cve_service.upsert_cve()` via
   `CVEIngestPayload.cvss_assessments` with `provider = "Red Hat"`. The
   service routes it to `ticket_mutations.create_cvss_assessment()`,
   which derives `cvss_version` and `score` from the vector automatically
4. If the assessment differs from an existing NVD Secondary with the same
   provider name -> overwrite

**Strategy — periodic re-fetch**:

1. Once per day, a Celery task iterates over all CVEs with active tickets
   (status: New, Analysis, Analyzed; `deleted_at IS NULL` — see
   `docs/data-model.md` for the authoritative definition)
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
`Analyzed` with `deleted_at IS NULL` (see `docs/data-model.md` for the
authoritative definition of active tickets).

When a ticket transitions to `Resolved`, `Ignored`, or `Duplicated`, Sentinel
stops monitoring CVSS updates for that CVE. The existing CVSS data remains
in the database but is no longer refreshed.

## Recalculation Cascade

When a CVSS assessment changes (added, modified, or removed) for a CVE
with an active ticket, Sentinel performs the following recalculation:

1. **Recalculate severity**: apply the resolution cascade to determine the
   new severity. If severity changed, update the CVE's `severity` field.
2. **Recalculate product eligibility**: for every
   `TicketPackageProduct` linked to the ticket, re-evaluate the `eligible`
   flag using the new score:
   - If the new score is **above** a product's threshold (and the product
     was previously below): set `eligible = true`
   - If the new score is **below** a product's threshold (and the product
     was previously above): set `eligible = false`
   - Products with `is_eligible_override = true` are not modified
   - Products in Reactive LTSS phase remain `eligible = false` regardless
3. **Ticket status re-evaluation**: eligibility changes in step 2 MUST be
   applied through the `ticket_mutations` module, which then calls
   `reconcile_ticket_status` to re-evaluate the ticket status (see
   `docs/features/tickets/ticket-mutations.md`).
   The centralized evaluator determines the correct target status.
   **Note**: this re-evaluation can only occur when a VA manually modifies
   a SUSE CVSS assessment on a Resolved ticket. Automated sync (NVD, Red
   Hat) and default CVSS version changes only process active tickets
   (New, Analysis, Analyzed) — Resolved tickets are excluded from those
   scopes.
4. **Audit trail**: create `TicketAuditEvent` records for each change:
   - Severity change: `event_type = "severity_changed"`, `old_value` and
     `new_value` with severity labels
   - Product eligibility change: `event_type = "product_eligibility_changed"`,
      `old_value` and `new_value` with eligibility boolean values
   - Ticket status change (if the ticket status changed as a result
     of re-evaluation): `event_type = "status_change"`, with `old_value` and
     `new_value` reflecting the actual transition, `user_id = NULL`
     (system action)

## API Endpoints

### Get CVSS Assessments for a CVE

```
GET /api/v1/tickets/{ticket_id}/cvss
```

Public (no authentication required).

**Tickets without CVE**: returns 400 Bad Request with
`{"code": "TICKET_CVE_NOT_SET", "detail": "This ticket has no associated CVE. CVSS assessments are not available."}`.
The same 400 response applies to `POST .../cvss/suse` and
`DELETE .../cvss/suse/{version}` when called on a ticket without a CVE.

Response: list of all CVSS assessments for the ticket's CVE, grouped by
version.

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

The `resolved_*` fields reflect the result of the resolution cascade for
the current default version (which score and provider Sentinel is using for
decisions).

### Set or Update SUSE CVSS Assessment

```
POST /api/v1/tickets/{ticket_id}/cvss/suse
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
| 400 | `TICKET_CVE_NOT_SET` | Ticket has no associated CVE |
| 404 | `TICKET_NOT_FOUND` | Ticket not found |
| 422 | `CVSS_INVALID_VECTOR` | Vector string is malformed or unparseable |

**Capability**: `manage_cvss`.

### Delete SUSE CVSS Assessment

```
DELETE /api/v1/tickets/{ticket_id}/cvss/suse/{cvss_version}
```

Removes the SUSE CVSS assessment for the specified version. Triggers
recalculation cascade. The ticket may no longer meet the progression gate
requirements.

Response: 204 No Content.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `TICKET_CVE_NOT_SET` | Ticket has no associated CVE |
| 404 | `TICKET_NOT_FOUND` | Ticket not found |
| 404 | `RESOURCE_NOT_FOUND` | No SUSE assessment exists for the specified version |

**Capability**: `manage_cvss`.

## Service Architecture

CVSS logic is split across two service modules with distinct
responsibilities:

### `services/cvss.py` — Pure Resolution Logic

This module contains **read-only, side-effect-free** functions that
implement the CVSS resolution and scoring algorithms. These functions
never mutate the database — they receive data and return results.

| Function                | Input                                      | Output                          | Description                                              |
|-------------------------|--------------------------------------------|---------------------------------|----------------------------------------------------------|
| `resolve_cvss_score`    | CVE assessments, default CVSS version      | (score, provider) or None       | Implements the 3-step resolution cascade                 |
| `calculate_severity`    | CVSS score (float)                         | Severity enum                   | Maps score to severity using the rating scale            |
| `validate_cvss_vector`  | Vector string                              | Parsed metrics + version + calculated score | Parses vector, detects version from prefix, validates format, and computes the base score |

These functions are used in two contexts:

1. **Read path** (API `GET .../cvss`): to compute the `resolved_score`,
   `resolved_provider`, and `resolved_severity` response fields without
   any side effects
2. **Write path** (via `ticket_mutations`): as building blocks for the
   recalculation cascade — `ticket_mutations` calls these functions to
   determine the new severity and eligibility, then persists the results

### `services/ticket_mutations.py` — CVSS Mutations

All operations that create, update, or delete `CVECVSSAssessment`
records MUST go through the `ticket_mutations` module (see
`docs/features/tickets/ticket-mutations.md`). This module:

1. Persists the `CVECVSSAssessment` record change
2. Calls `cvss.resolve_cvss_score()` to determine the new resolved score
3. Calls `cvss.calculate_severity()` to derive the new severity
4. Updates `CVE.severity` if it changed
5. Re-evaluates product eligibility using the new score
6. Creates `TicketAuditEvent` records for each change
7. Calls `reconcile_ticket_status()` to re-evaluate the ticket status

All steps execute within the **same database transaction** as the
triggering change (atomicity guarantee).

The resolution cascade logic is **never reimplemented** inside
`ticket_mutations` — it always delegates to `services/cvss.py`.

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

1. Iterates over all active tickets (New, Analysis, Analyzed;
   `deleted_at IS NULL`)
2. For each ticket, calls the same `ticket_mutations` functions used for
   individual CVSS changes — no separate batch-optimized code path
3. Each ticket is processed in an **independent database transaction**
   (isolation: a failure on one ticket does not roll back others)
4. On error for a single ticket, the task logs the error with the
   ticket ID and continues with the remaining tickets
5. On completion, the task reports the total number of tickets processed,
   successes, and failures

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
| Schedule | Daily at 03:00 UTC (`0 3 * * *`) |
| Source | Red Hat Security Data API (`access.redhat.com/hydra/rest/securitydata`) |
| Scope | All CVEs with active tickets (New, Analysis, Analyzed; `deleted_at IS NULL`) |
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

| Setting | Type | Default | Range | Description |
|---------|------|---------|-------|-------------|
| `throttle_delay_seconds` | float | 2.0 | 0.1–30.0 | Delay between consecutive Red Hat API requests |

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

- `docs/features/tickets/cve-service.md` — CVE Service Layer
  (`upsert_cve()`, `CVEIngestPayload`, Phase 1/Phase 2 transaction model)
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
