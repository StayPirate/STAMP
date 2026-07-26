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
2. **Multi-version**: Sentinel supports CVSS v3.1 and v4.0 as its primary
   decision versions. A single provider may supply assessments for one or
   both versions. Other versions (e.g., v2.0, v3.0) may arrive from
   external sources and are stored. All stored versions participate in
   the Severity Resolution Cascade as fallback (steps 2 and 4) — if they
   are the only available score, they are used for severity derivation
   rather than falling back to absent. Only the configured default version
   (v3.1 or v4.0) is used for the Eligibility Score Resolution.
3. **Configurable default version**: a system-wide setting determines which
   CVSS version is used for all automated decisions (severity, eligibility). Initially set to `3.1`, changeable by Admin. See
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
   **Two-level severity derivation**: severity at the per-assessment level
   (`CVECVSSAssessment.severity`) is derived using the library's
   version-specific FIRST scale (v2: Low/Medium/High; v3/v4:
   None/Low/Medium/High/Critical). Severity at the ticket level
   (`CVE.severity`) is derived from the resolved score via
   `calculate_severity()` using the unified v3/v4 scale regardless of
   source version. See "Severity Rating Scale" below.

## CVSS Score Resolution

Sentinel uses two distinct resolution strategies depending on the consumer.
Each is described below.

### Severity Resolution Cascade

Used for: severity derivation, display, and any future
informational/triage logic.

This cascade resolves the best available CVSS score, preferring SUSE's
assessment and the configured default version, but falling back to other
providers and versions to maximize informational coverage:

1. **SUSE assessment, default version**. If SUSE has published an
   assessment for the configured default CVSS version, use this score.
2. **SUSE assessment, other version**. If SUSE has published an assessment
   for a non-default version, use it. If multiple non-default versions
   exist, prefer the highest by version priority order
   (`4.0 > 3.1 > 3.0 > 2.0`).
3. **Highest provider, default version**. If at least one external provider
   has an assessment for the default version, use the highest score among
   them.
4. **Highest provider, other version**. If at least one external provider
   has an assessment for any non-default version, use the highest score
   among those. If multiple non-default versions exist, prefer the
   highest by version priority order (`4.0 > 3.1 > 3.0 > 2.0`); within
   the same version, prefer the highest score.
5. **Absent**. No provider has published any assessment for any version.
   The score is treated as absent (`CVE.severity` is set to `NULL`).

**Cross-version severity mapping**: when the resolved score comes from a
version other than the default (steps 2 or 4), severity is mapped using
the rating scale thresholds specific to that score's version. Sentinel
uses the standard CVSS thresholds for each version (see Severity Rating
Scale above).

**SUSE Internal Severity Scale**: internally, SUSE processes also utilize a
non-standard rating scale consisting of four tiers: Low, Moderate, Important,
and Critical. For the purposes of Sentinel's core database representation,
API endpoints, and calculation logic, the standard CVSS scale (Low, Medium,
High, Critical) is used exclusively. Where external SUSE metadata (such as
IBS/OBS patchinfo/updateinfo.xml files) contains the internal scale (e.g.,
Moderate, Important), these values are treated as informational or mapped
statically to their standard CVSS counterparts (Moderate → Medium, Important
→ High) on the boundary, with no impact on core data models or calculations.

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

This resolution is implemented by `resolve_eligibility_score` in
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
- **CVSS versions**: v2.0, v3.0, v3.1, and v4.0 (all metric arrays
  present in the NVD API response are extracted)
- **Fetch mechanism**: extracted from `cvssMetricV2`, `cvssMetricV30`,
  `cvssMetricV31`, and `cvssMetricV40` arrays in the NVD CVE API
  response

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
  `docs/features/tickets/cve-sync-nvd.md` (NVD Source API Caching) for
  the caching strategy
- **Convergence with direct sources**: both NVD Secondary and direct-source
  fetchers (e.g., Red Hat) write to the same UPSERT conflict key
  `(cve_id, provider_name, cvss_version)` — last-writer-wins. Since direct
  sources run on independent schedules, data converges to the direct-source
  value within one fetcher cycle. Temporary oscillation (NVD overwriting a
  fresher direct-source score between cycles) is transient and harmless —
  CVSS scores rarely change after publication

#### Red Hat

- **Source**: Red Hat Security Data API
  (`access.redhat.com/hydra/rest/securitydata/cve/{CVE-ID}.json`)
- **Type**: independent assessment by Red Hat Product Security
- **Provider name in Sentinel**: `"Red Hat"`
- **CVSS versions**: v2.0 and v3.x (version derived from vector string
  prefix). v4.0 will be supported when Red Hat adds it
- **Response format**: the `cvss3` object contains `cvss3_base_score`
  (string), `cvss3_scoring_vector` (string), and `status` (`"draft"` or
  `"verified"`). Only the vector string is used — the score is recomputed
  locally by the `cvss` library for consistency
- **Deduplication**: if Red Hat also appears as a CNA Secondary in NVD
  (same provider name `"Red Hat"`), both write to the same UPSERT conflict
  key — last-writer-wins. Since Red Hat runs daily (after NVD's 6h cycle),
  the Red Hat value typically persists

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

A score of exactly 0.0 maps to severity `None` — this is a valid CVSS
rating indicating no security impact, distinct from `NULL` (no assessment
available / unresolved).

## Severity Derivation

The `severity` field on the CVE table is a denormalized, nullable field,
always derived from CVSS assessments. It is never set manually.
`NULL` indicates that no CVSS assessment is available from any provider
(unresolved). The enum value `None` indicates a resolved CVSS score of
exactly 0.0 (the standard CVSS "None" rating — no security impact).

**Note**: for tickets without a CVE, severity is determined by the
`severity_manual` field on the Ticket, set manually by the VA. The
CVE severity derivation described below applies only to tickets with an
associated CVE. See `docs/features/tickets/tickets.md` (Severity Resolution)
for the unified resolution logic.

### Calculation Rules

1. Resolve the CVSS score using the **Severity resolution cascade** (see
   above): SUSE default version → SUSE other version → highest provider
   default version → highest provider other version → absent
2. If a score is found: map it to a severity using the rating scale
   thresholds for the version of that score
3. If no score is found (absent): `CVE.severity` is set to `NULL`
   (unresolved)

### When Severity is Recalculated

Severity is recalculated whenever:

- A CVSS assessment is added, modified, or removed for the CVE
- The system-wide default CVSS version is changed by an Admin
- A ticket transitions from an inactive status (Resolved, Ignored,
  Duplicated) to an active status — `recalculate_cvss_chain()` is
  called synchronously by `reconcile_ticket_status()` when it detects
  the transition, plus `catch_up()` tasks are enqueued internally
- A CVE is associated with a ticket (or a ticket is created with a
   CVE) — `recalculate_cvss_chain()` is called synchronously within
   the transaction (see `ticket-service.md`, `associate_cve()` step 9)

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
set `severity_manual` before the ticket can progress. See
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

The `sync_nvd_cves` fetcher runs every 6 hours and ingests CVSS
assessments (Primary and Secondary) from the NVD REST API v2. CNA
display names for Secondary assessments are resolved via the NVD Source
API. Changes are persisted via `cve_service` (see
[`cve-service.md`](cve-service.md)). If any CVSS assessment changed for
a CVE with an active ticket, the recalculation chain is triggered (see
Recalculation Chain below).

For the full fetcher definition — including the incremental algorithm,
NVD Source API caching strategy, first-run behavior, and error handling
— see [`cve-sync-nvd.md`](cve-sync-nvd.md) (Fetcher:
`sync_nvd_cves`).

### Red Hat Sync

Red Hat's API does NOT support incremental fetching (no
`modified_after` parameter). The `sync_redhat_cves` fetcher runs daily
(03:00 UTC) and re-fetches Red Hat data for all CVEs with active
tickets.

Red Hat provides both CVSS v3 and CVSS v2 assessments. Both versions
are imported as `CVECVSSAssessment` records with
`provider_name = "Red Hat"`. The fetcher also extracts CWE
identifiers, references, and source package names from the same API
response.

**Scope gap**: the fetch scope is "CVEs with active tickets" due to
Red Hat API rate limits. CVEs whose tickets are in Ignored,
Duplicated, or Resolved status do NOT receive Red Hat CVSS updates
during the inactive period. This gap is mitigated by the `catch_up()`
mechanism: when a ticket is reactivated, the default `catch_up()`
(inherited from `BaseCVEFetcher`) calls `fetch_single(cve_id)` to
retrieve the latest Red Hat data. See
[fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
("Per-Ticket Catch-Up: `catch_up()` Method").

For the full fetcher definition — including the complete algorithm,
CWE/reference extraction, package best-effort addition, error
handling, and `fetch_single` method — see
[`cve-sync-redhat.md`](cve-sync-redhat.md) (Fetcher: `sync_redhat_cves`).

### Sync Scope

CVSS sync scope varies by fetcher:

- **NVD** (`sync_nvd_cves`): global scope — fetches all CVEs modified
  in the time window, regardless of ticket status. Persistence is
  unrestricted (consistent with the Data Convention below)
- **Red Hat** (`sync_redhat_cves`): scoped to CVEs with **active
  tickets** — tickets in status `New`, `Analysis`, or `Analyzed` (see
  `docs/data-model.md` for the authoritative definition of active
  tickets). This restriction exists because the Red Hat API requires
  per-CVE lookups (no bulk/incremental endpoint)

When a ticket transitions to `Resolved`, `Ignored`, or `Duplicated`,
Red Hat CVSS sync stops monitoring that CVE. NVD data continues to be
persisted regardless of ticket status (time-window-based fetching is
independent of ticket lifecycle). In both cases, existing CVSS data
remains in the database. If the ticket is later reopened, the
recalculation chain re-derives severity and eligibility from the
current `CVECVSSAssessment` records (which may have been updated by
NVD in the interim).

### CVSS Fetcher Data Convention

CVSS fetchers MUST separate data persistence (`CVECVSSAssessment` records)
from recalculation of derived data (severity, eligibility, ticket status):

1. **Persistence scope**: the ticket-status filter ("active tickets only")
   applies ONLY to the recalculation chain, NEVER to the persistence of
   `CVECVSSAssessment` records — unless the external API's design or rate
   limits make broader persistence impractical (e.g., per-CVE lookup APIs
   with no bulk/incremental endpoint).
2. **Gap documentation**: when a fetcher's fetch scope is narrower than
   "all CVEs with tickets" due to API constraints, the fetcher's
   specification MUST document the gap explicitly and the system MUST
   provide a catch-up mechanism (via `catch_up()`) for tickets
   reactivated after a period of inactivity.
3. **Goal**: `CVECVSSAssessment` records are as complete as possible
   regardless of ticket lifecycle state. Reopened tickets converge to
   accurate derived data quickly via the synchronous recalculation
   chain (immediate best-effort) followed by asynchronous `catch_up()`
   tasks (data catch-up).

## Recalculation Chain

When a CVSS assessment changes (added, modified, or removed) for a CVE
with an active ticket, Sentinel performs the following recalculation:

1. **Recalculate severity**: call `resolve_severity_score()` (5-step
   severity cascade) to determine the new resolved score. If a score is
   found, map it to a severity label via `calculate_severity()`. If no
   score is found (absent), set `CVE.severity` to `NULL`. If severity changed,
   update the CVE's `severity` field.
2. **Recalculate product eligibility**: call `resolve_eligibility_score()`
   (2-step SUSE-only cascade — separate call with different semantics; the
   eligibility score may differ from the severity score when SUSE has not
   assessed the default version). Re-evaluate the `eligible` flag for every
   `TicketPackageProduct` linked to the ticket (including soft-deleted
   products — see [`package-model.md`](../packages/package-model.md) Design
   Decision 8), applying the eligibility rules defined in
   [`package-model.md`](../packages/package-model.md) (Axis 2: Eligibility).
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

**Access: Public**

The `{cve_id}` path parameter accepts a CVE-ID string (e.g.,
`CVE-2025-1234`). See `docs/api-spec.md` (CVE Identifier Resolution).

Response: composite CVSS view for the CVE — the list of raw assessments
alongside the computed severity and eligibility results, returned as a
single conceptual resource in the `data` envelope. Pagination is
intentionally omitted — the number of CVSS assessments per CVE is
naturally bounded (one per provider-version combination, typically fewer
than 20 records). Client-controlled sorting is not supported; assessments
are returned in a fixed order grouped by CVSS version.

```json
{
  "data": {
    "assessments": [
      {
        "id": "uuid",
        "provider_name": "NVD",
        "cvss_version": "3.1",
        "score": 9.8,
        "vector_string": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
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
    "severity": {
      "score": 9.8,
      "version": "3.1",
      "provider": "NVD",
      "label": "Critical"
    },
    "eligibility": {
      "score": 9.8,
      "source": "suse"
    }
  }
}
```

The `severity` and `eligibility` objects expose the results of the two
distinct CVSS resolution strategies:

- **`severity`**: result of the Severity Resolution Cascade (5-step,
  multi-provider). `score` is the resolved CVSS score, `version` is the
  CVSS version of the resolved score (e.g., `"3.1"`, `"4.0"`), `provider`
  is the provider that supplied it, and `label` is the severity rating
  (`"None"`, `"Low"`, `"Medium"`, `"High"`, `"Critical"`). Used for
  display and triage.

  The `severity` object is formally `null` when no CVSS assessments are
  available (Absent severity cascade step 5).

  The `severity.provider` matches the `provider_name` column of
  `CVECVSSAssessment` (VARCHAR(100), set open, with examples such as
  `"NVD"`, `"SUSE"`, `"Red Hat"`, or CNA names like `"Intel Corporation"`).
- **`eligibility`**: result of the Eligibility Score Resolution (2-step,
  SUSE-only). `score` is the CVSS score used for product eligibility
  threshold comparison. `source` indicates where the score came from:
  `"suse"` when the score comes from a SUSE assessment for the default
  version, `"fallback"` when no SUSE assessment exists for the default
  version (score defaults to 10.0 — conservative worst-case). Used for
  product eligibility threshold comparison.

Example response when no CVSS assessments are available (absent severity):

```json
{
  "data": {
    "assessments": [],
    "default_cvss_version": "3.1",
    "severity": null,
    "eligibility": {
      "score": 10.0,
      "source": "fallback"
    }
  }
}
```

### Set or Update SUSE CVSS Assessment

```
POST /api/v1/cves/{cve_id}/cvss/suse
```

Request body:

```json
{
  "vector_string": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
}
```

- `vector_string`: valid CVSS vector string (maximum 200 characters). The CVSS version is derived from the
  vector prefix (`CVSS:4.0/` → 4.0, `CVSS:3.1/` → 3.1, `CVSS:3.0/` → 3.0,
  no prefix → 2.0). The base score is computed automatically by the `cvss`
  library.

The API accepts any valid CVSS version for SUSE assessments (including v2.0
and v3.0 from historical or cross-referenced data). However, the ticket
progression gate requires SUSE assessments for both v3.1 AND v4.0 (see
Workflow Gates) — assessments for other versions are stored but do not
satisfy the gate. The UI presents only v3.1 and v4.0 as input options to
VAs.

The backend parses the vector, derives the version and score, and saves the
assessment via `upsert_cvss_assessment()`. If an existing SUSE assessment for
the derived version exists, it is updated; otherwise a new one is created.
Triggers recalculation chain (unless the vector is unchanged — no-op
short-circuit).

Response: **201 Created** when a new assessment is created, **200 OK** when
an existing one is updated or unchanged. The response body is the assessment
object wrapped in the standard `{"data": ...}` envelope.

**Note on POST with upsert semantics**: POST is used instead of PATCH or PUT
because the target resource is not fully identified by the URL — the CVSS
version (which determines which specific assessment record is created or
updated) is derived from parsing the vector prefix in the request body, not
from an explicit path parameter. Additionally, the operation may create a new
entity rather than update an existing field, making POST semantically
appropriate per the "Mutation Patterns" convention in `api-spec.md`. The
differentiated response codes (201 for creation, 200 for update) make the
upsert behavior explicit to clients.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `CVSS_INVALID_VECTOR` | Vector string is malformed or unparseable |

The `TICKET_NOT_MUTABLE` scoped response applies only when the CVE has an
associated ticket. CVEs without an associated ticket are always mutable.

**`Capability: manage_cvss`**

### Delete SUSE CVSS Assessment

```
DELETE /api/v1/cves/{cve_id}/cvss/suse/{cvss_version}
```

The `{cvss_version}` path parameter accepts: `2.0`, `3.0`, `3.1`, `4.0`.
Unrecognized values are treated as not found (404
`CVSS_ASSESSMENT_NOT_FOUND`).

Removes the SUSE CVSS assessment for the specified version. Triggers
recalculation chain. The ticket may no longer meet the progression gate
requirements.

Response: 204 No Content.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVSS_ASSESSMENT_NOT_FOUND` | No SUSE assessment exists for the specified version |

The `TICKET_NOT_MUTABLE` scoped response applies only when the CVE has an
associated ticket. CVEs without an associated ticket are always mutable.

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
| `resolve_severity_score`    | CVE assessments, default CVSS version      | (score, version, provider) or None | Implements the severity resolution cascade (5-step: SUSE default → SUSE other version → highest provider default → highest provider other → absent) |
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

1. **Read path** (API `GET .../cvss`): to compute the `severity.score`,
   `severity.version`, `severity.provider`, `severity.label`, and
   `eligibility.score` response fields without any side effects
2. **Write path** (via `ticket_mutations`): as building blocks for the
   recalculation chain — `ticket_mutations` calls these functions to
   determine the new severity and eligibility, then persists the results

### `services/ticket_mutations.py` — CVSS Mutations

All operations that create, update, or delete `CVECVSSAssessment`
records MUST go through the `ticket_mutations` module. The module
exposes `upsert_cvss_assessment()` (create-or-update) and
`delete_cvss_assessment()`. When a CVSS mutation function is invoked,
it conceptually: locks the ticket, validates operability, persists the
assessment change, resolves derived data, emits audit events, and
reconciles ticket status — all within a single database transaction
(atomicity guarantee).

Two resolution functions from `services/cvss.py` are invoked during
the write path, each serving a distinct purpose:

- **`resolve_severity_score()`** (5-step cascade): determines the
  resolved CVSS score used to derive `CVE.severity`. This is the
  exclusive source of truth for `CVE.severity` — `resolve_eligibility_score()`
  is never used for this purpose. When the cascade returns no score
  (absent), `CVE.severity` is set to `NULL` (unresolved).
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

## Chain Execution Model

The recalculation chain is a **synchronous service-layer operation**
executed within the same database transaction as the CVSS change that
triggered it. This guarantees atomicity: if the CVSS change is committed,
the severity, eligibility, and ticket state adjustments are committed
together.

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

## Ticket Reactivation: CVSS Catch-Up

When a ticket transitions from an inactive status (Resolved, Ignored,
Duplicated) to an active status (Analysis, Analyzed), two catch-up
mechanisms execute to reconcile CVSS-derived data:

1. **Synchronous** (within the reactivation transaction):
   `recalculate_cvss_chain()` is called to reconcile derived data
   (severity, eligibility) with the current `default_cvss_version` and
   any `CVECVSSAssessment` updates that occurred while the ticket was
   inactive. This provides immediate best-effort accuracy using
   whatever assessment data is already persisted.

2. **Asynchronous** (enqueued during `reconcile_ticket_status()`
   execution, before the caller's commit — safe because `catch_up()` is
   idempotent by contract and does not read ticket status as a
   precondition): `catch_up()` tasks are enqueued for every registered
   fetcher via `get_catch_up_fetchers()` — not limited to CVSS fetchers.
   This catches up on data that was not fetched during the inactive
   period. Each `catch_up()` task operates independently; if it discovers
   changed data, the normal mutation path handles the recalculation
   chain.

Both mechanisms are handled internally by `reconcile_ticket_status()`
(step 4) — no caller or endpoint handler action is required.

The ticket may transition rapidly as async tasks complete (e.g.,
re-open → Analysis, then a fetch discovers a release → Resolved). This
is expected and correct behavior — the system converges to the accurate
state.

See [`ticket-mutations.md`](ticket-mutations.md) for the
`reconcile_ticket_status()` step 4 behavior and
`recalculate_cvss_chain()` contract,
[`ticket-service.md`](ticket-service.md) for the reactivation context,
and [`fetcher-infrastructure.md`](../platform/fetcher-infrastructure.md)
for the `catch_up()` method contract.

## Background Tasks

The `sync_nvd_cves` fetcher (defined in
`docs/features/tickets/cve-sync-nvd.md`) also produces CVSS assessments
during CVE ingestion. See "NVD Sync (Incremental)" above for the
consumer-oriented summary.

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
  `recalculate_cvss_chain()`, `reconcile_ticket_status()`, per-operation
  audit contract, module boundary, manual-zone exit operations
- `docs/features/tickets/ticket-service.md` — Non-gate ticket lifecycle
  operations, un-ignore / un-duplicate hooks
- `docs/features/tickets/ticket-audit-log.md` — `TicketAuditEvent` type
  contract, field semantics
- `docs/features/tickets/cve-service.md` — CVE Service Layer
  (`upsert_cve()`, `CVEIngestPayload`, Phase 1/Phase 2 transaction model)
- `docs/features/tickets/cve-sync-nvd.md` — `sync_nvd_cves` fetcher
  definition (incremental algorithm, NVD Source API caching)
- `docs/features/packages/package-model.md` — Three Orthogonal Dimensions,
  Axis 2: Eligibility (rules, override model, Reactive LTSS)
- `docs/features/platform/system-settings.md` — `default_cvss_version`
  setting, batch recalculation trigger
- `docs/features/platform/fetcher-infrastructure.md` — `BaseFetcher`
  contract, `catch_up()` method, sub-operation exception
- `docs/features/platform/cve-fetcher-infrastructure.md` — `BaseCVEFetcher`
  contract, `fetch_single` capability
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses), CVE Accessibility Check, CVE Identifier
  Resolution
