# CVSS Scoring

## Purpose

Manage CVSS (Common Vulnerability Scoring System) assessments from multiple
providers for each CVE. STAMP ingests CVSS data from external sources,
allows incident managers (IMs) to provide SUSE's own assessment, and uses
the scores to derive severity, determine product eligibility, and control
ticket workflow progression.

## Key Principles

1. **Multi-provider**: each CVE can have CVSS assessments from multiple
   providers (NVD, CNA vendors, Red Hat, SUSE). Each assessment is stored
   independently.
2. **Multi-version**: STAMP supports CVSS v3.1 and v4.0. A single provider
   may supply assessments for one or both versions. Other versions (e.g.,
   v2.0) may arrive from external sources and are stored and displayed but
   not used for decisions.
3. **Configurable default version**: a system-wide setting determines which
   CVSS version is used for all automated decisions (severity, eligibility,
   notifications). Initially set to `3.1`, changeable by Admin. See
   `docs/features/admin.md`.
4. **SUSE assessment is authoritative**: when STAMP needs a CVSS score to
   make a decision, it follows the resolution cascade defined below.
5. **Default version awareness**: every component of the system that needs
   a CVSS score for any decision MUST resolve the version from the system
   configuration — never hardcode `3.1` or `4.0`.

## CVSS Score Resolution Cascade

Whenever STAMP needs a CVSS score for a decision (severity calculation,
eligibility threshold comparison, or any future logic), it MUST follow
this cascade:

1. **SUSE assessment** of the configured default CVSS version. If present,
   use this score.
2. **Highest score** among all providers for the configured default CVSS
   version. If at least one assessment of the default version exists from
   any provider, use the highest score.
3. **No score available**. If no assessment of the default version exists
   from any provider, the score is treated as absent.

When the score is absent and a decision requires a numeric value (e.g.,
eligibility threshold comparison), the system uses **10.0** (worst-case,
conservative approach). See `docs/features/package-tracking.md` for
eligibility rules.

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
- **Provider name in STAMP**: `"NVD"`
- **CVSS versions**: currently v3.1; v4.0 expected in the future
- **Fetch mechanism**: extracted from `cvssMetricV31` and `cvssMetricV40`
  arrays in the NVD CVE API response

#### CNA (via NVD Secondary)

- **Source**: NVD REST API v2, assessments with `type: "Secondary"`
- **Type**: assessment provided by the CVE Numbering Authority (the vendor
  or organization that assigned the CVE)
- **Identified by**: `source` field containing the CNA's email (e.g.,
  `secure@intel.com`), `type: "Secondary"`
- **Provider name in STAMP**: resolved to a human-readable name using the
  NVD Source API (`services.nvd.nist.gov/rest/json/source/2.0`). For
  example, `secure@intel.com` resolves to `"Intel Corporation"`
- **CVSS versions**: varies by CNA; may include v3.1, v4.0, or both
- **Name resolution**: during CVE sync, the ingestion service resolves
  `source` email addresses to display names via the NVD Source API. Source
  API data is cached in-memory during each sync run (the dataset is small,
  ~215 organizations, and changes infrequently)
- **Deduplication with direct sources**: if a direct source (e.g., Red Hat)
  provides an assessment for the same provider and CVSS version, the direct
  source takes priority and overwrites the NVD Secondary data

#### Red Hat

- **Source**: Red Hat Security Data API
  (`access.redhat.com/hydra/rest/securitydata/cve/{CVE-ID}.json`)
- **Type**: independent assessment by Red Hat Product Security
- **Provider name in STAMP**: `"Red Hat"`
- **CVSS versions**: currently v3.1 only (field `cvss3` in the response).
  v4.0 will be supported when Red Hat adds it
- **Response format**: the `cvss3` object contains `cvss3_base_score`
  (string), `cvss3_scoring_vector` (string), and `status` (`"draft"` or
  `"verified"`)
- **Deduplication**: if Red Hat also appears as a CNA Secondary in NVD
  (same provider name `"Red Hat"`), the direct fetch from the Red Hat API
  takes priority and overwrites the NVD Secondary record

### Internal Provider

#### SUSE

- **Source**: manual input by IM via the Ticket Detail page
- **Provider name in STAMP**: `"SUSE"`
- **CVSS versions**: the IM MUST provide both v3.1 and v4.0 assessments
  before the ticket can progress beyond Analysis (see Workflow Gates)
- **Input method**: the IM enters a CVSS vector string; the backend
  validates the vector format and calculates the score automatically
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

### Calculation Rules

1. Resolve the CVSS score using the resolution cascade (SUSE default
   version → highest default version → absent)
2. If a score is found: map it to a severity using the rating scale above
3. If no score is found (no assessment of the default version exists from
   any provider): severity is `None`

### When Severity is Recalculated

Severity is recalculated whenever:

- A CVSS assessment is added, modified, or removed for the CVE
- The system-wide default CVSS version is changed by an Admin
- The SUSE assessment is added or modified by an IM

### Severity Override by CVSS

The SUSE CVSS assessment is mandatory for ticket progression (see Workflow
Gates). Once both SUSE assessments (v3.1 and v4.0) are provided, the
severity is always calculated automatically from the SUSE score of the
default version. There is no manual severity selection.

## Workflow Gates

### SUSE CVSS Required for Ticket Progression

The ticket CANNOT transition from `Analysis` to `Analyzed` (or any
subsequent state) unless the IM has provided BOTH:

- SUSE CVSS v3.1 assessment (vector string → calculated score)
- SUSE CVSS v4.0 assessment (vector string → calculated score)

This ensures that:

1. Every ticket that progresses beyond Analysis has a SUSE-determined
   severity
2. The severity is always calculated (never manually selected)
3. Both CVSS versions are available for current and future use

### Severity Required

Since the SUSE CVSS is mandatory and severity is derived from it, severity
is always determined for any ticket that passes the Analysis gate. A ticket
with `severity = None` (no CVSS data) cannot progress.

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

See `docs/features/package-tracking.md` for the full eligibility logic.

## Data Sync

### NVD Sync (Incremental)

NVD supports incremental fetching via the `lastModStartDate` and
`lastModEndDate` parameters (max 120-day range). A CVE's `lastModified`
timestamp changes when NVD or a CNA modifies the record, including CVSS
changes.

**Strategy**:

1. STAMP stores `last_nvd_sync_at` (timestamp of the last successful sync)
2. Every 6 hours, a Celery task fetches CVEs modified since
   `last_nvd_sync_at`:
   ```
   GET /rest/json/cves/2.0?lastModStartDate={last_sync}&lastModEndDate={now}
   ```
3. For each returned CVE:
   - Update CVE metadata (description, references, etc.)
   - Extract all CVSS assessments from `cvssMetricV31`, `cvssMetricV40`,
     and any other `cvssMetricV*` arrays
   - For Primary assessments (`type: "Primary"`): save with
     `provider_name = "NVD"`
   - For Secondary assessments (`type: "Secondary"`): resolve `source`
     email to display name via NVD Source API, save with the resolved name
   - Skip Secondary assessments where a direct source (e.g., Red Hat)
     already has data for the same `provider_name` and `cvss_version`
4. If any CVSS assessment changed for a CVE with an active ticket →
   trigger recalculation (see Recalculation Cascade)
5. Update `last_nvd_sync_at`

**NVD Source API caching**: during each sync run, the service fetches the
full NVD Source API dataset (`GET /rest/json/source/2.0`) into an
in-memory dictionary mapping `source_identifier → display_name`. The
dataset is small (~215 entries) and changes infrequently.

### Red Hat Sync

Red Hat's API does NOT support incremental fetching (no `modified_after`
parameter). The API provides `after`/`before` parameters for creation date
only.

**Strategy — initial fetch**:

1. When a new ticket is created (CVE ingested), STAMP fetches the Red Hat
   CVSS for that CVE:
   ```
   GET /hydra/rest/securitydata/cve/{CVE-ID}.json
   ```
2. Extract `cvss3.cvss3_base_score` and `cvss3.cvss3_scoring_vector`
3. Save as `CVECVSSAssessment` with `provider_name = "Red Hat"`,
   `cvss_version = "3.1"`
4. If the assessment differs from an existing NVD Secondary with the same
   provider name → overwrite

**Strategy — periodic re-fetch**:

1. Once per day, a Celery task iterates over all CVEs with active tickets
   (status: New, Analysis, Analyzed)
2. For each CVE, fetch the Red Hat CVSS:
   ```
   GET /hydra/rest/securitydata/cve/{CVE-ID}.json
   ```
3. A hardcoded delay of 2 seconds is added between requests to avoid
   overloading the Red Hat API. Speed is not important.
4. Compare the fetched score and vector with the stored values
5. If different → update the assessment and trigger recalculation
6. STAMP stores `last_redhat_sync_at` for operational monitoring

### Sync Scope

CVSS sync (both NVD incremental and Red Hat re-fetch) is performed only
for CVEs with **active tickets** — tickets in status `New`, `Analysis`, or
`Analyzed`.

When a ticket transitions to `Resolved`, `Ignored`, or `Duplicated`, STAMP
stops monitoring CVSS updates for that CVE. The existing CVSS data remains
in the database but is no longer refreshed.

## Recalculation Cascade

When a CVSS assessment changes (added, modified, or removed) for a CVE
with an active ticket, STAMP performs the following recalculation:

1. **Recalculate severity**: apply the resolution cascade to determine the
   new severity. If severity changed, update the CVE's `severity` field.
2. **Recalculate product eligibility**: for every
   `TicketPackageProduct` linked to the ticket, re-evaluate eligibility
   using the new score:
   - If the new score is **above** a product's threshold (and the product
     was previously below): `AFFECTED_RESOLVED` → `AFFECTED`
   - If the new score is **below** a product's threshold (and the product
     was previously above): `AFFECTED` → `AFFECTED_RESOLVED`
   - Products with `is_override = true` are not modified
   - Products in protected states (`WONT_FIX`, `IGNORED`) are not modified
   - Products in Reactive LTSS phase remain `AFFECTED_RESOLVED` regardless
3. **Ticket state adjustment**: if the ticket is in `Resolved` state and
   any product has transitioned to a non-final state (`AFFECTED`), the
   ticket is moved back to `Analyzed` (not `Analysis`, because all
   codestream statuses were already set by the IM)
4. **Audit trail**: create `TicketEvent` records for each change:
   - Severity change: `event_type = "severity_changed"`, `old_value` and
     `new_value` with severity labels
   - Product status change: `event_type = "product_status_overridden"` (or
     a dedicated type for system-initiated eligibility changes)
   - Ticket state change: `event_type = "status_change"` with
     `user_id = NULL` (system action)

## UI — CVSS Card

The CVSS Card is a dedicated section in the Ticket Detail page
(`/tickets/:id`), displayed after the CVE Information Card. See
`docs/features/pages.md` for the full page layout.

### Structure

The card contains:

1. **Tabs**: one tab per CVSS version, ordered by version ascending (e.g.,
   v2.0 → v3.1 → v4.0)
2. **Tab visibility**:
   - Tabs for v3.1 and v4.0 are **always visible**, even if empty
   - Tabs for other versions (e.g., v2.0) are visible only if at least one
     assessment of that version exists for the CVE
3. **Active tab on load**: the tab corresponding to the system-wide default
   CVSS version
4. **Tab content**: a table of assessments for that version (see below)
5. **SUSE CVSS action**: below the table (only in v3.1 and v4.0 tabs):
   - If SUSE assessment is absent: button "Add SUSE CVSS"
   - If SUSE assessment is present: button "Edit SUSE CVSS"
   - Both open a modal with a vector string input field
6. **Empty state**: when a tab has no assessments, display
   "No CVSS data available for this version" with the SUSE action button
   (if applicable)

### Assessment Table

Each tab displays a table with the following columns:

**CVSS v3.1 tab:**

| Provider | Score | Attack Vector | Attack Complexity | Privileges Required | User Interaction | Scope | Confidentiality | Integrity | Availability |
|----------|-------|---------------|-------------------|---------------------|------------------|-------|-----------------|-----------|--------------|

**CVSS v4.0 tab:**

| Provider | Score | Attack Vector | Attack Complexity | Attack Requirements | Privileges Required | User Interaction | Vuln. Confidentiality | Vuln. Integrity | Vuln. Availability | Sub. Confidentiality | Sub. Integrity | Sub. Availability |
|----------|-------|---------------|-------------------|---------------------|---------------------|------------------|-----------------------|-----------------|--------------------|----------------------|----------------|-------------------|

- One row per provider
- Metric columns show human-readable values parsed from the vector string
  (e.g., "Network", "Low", "High")
- Providers that only supply certain versions appear only in the
  corresponding tabs (e.g., Red Hat appears only in v3.1 if they only
  provide v3.1). No placeholder rows for missing versions.
- Other CVSS versions (e.g., v2.0): columns match the base metrics of that
  version's specification

### SUSE CVSS Modal

When the IM clicks "Add SUSE CVSS" or "Edit SUSE CVSS":

1. A modal opens with:
   - Title: "SUSE CVSS v{version}" (matching the active tab)
   - Input field: CVSS vector string (text input, monospace)
   - Pre-filled with existing vector if editing
   - Validation feedback inline (invalid vector format → error message)
2. On submit:
   - Backend validates the vector string format for the specified version
   - Backend calculates the score from the vector
   - Assessment is saved with `provider_name = "SUSE"`
   - Severity and eligibility are recalculated (see Recalculation Cascade)
   - The table updates to show the new/updated SUSE row

## API Endpoints

### Get CVSS Assessments for a CVE

```
GET /api/v1/tickets/{ticket_id}/cvss
```

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
the current default version (which score and provider STAMP is using for
decisions).

### Set or Update SUSE CVSS Assessment

```
POST /api/v1/tickets/{ticket_id}/cvss/suse
```

Request body:

```json
{
  "cvss_version": "3.1",
  "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
}
```

- `cvss_version`: must be `"3.1"` or `"4.0"`
- `vector`: valid CVSS vector string for the specified version

The backend validates the vector, calculates the score, and saves the
assessment. If an existing SUSE assessment for the same version exists, it
is updated (upsert). Triggers recalculation cascade.

Response: the created or updated assessment object.

Requires: Security Team or Admin role.

### Delete SUSE CVSS Assessment

```
DELETE /api/v1/tickets/{ticket_id}/cvss/suse/{cvss_version}
```

Removes the SUSE CVSS assessment for the specified version. Triggers
recalculation cascade. The ticket may no longer meet the progression gate
requirements.

Requires: Security Team or Admin role.

## Background Tasks

| Task                     | Schedule    | Description                                  |
|--------------------------|-------------|----------------------------------------------|
| `sync_cves_nvd`          | Every 6h    | Incremental NVD CVE sync. Extracts all CVSS assessments (Primary + Secondary). Resolves CNA names via NVD Source API. |
| `sync_cvss_redhat`       | Daily       | Re-fetches Red Hat CVSS for all CVEs with active tickets. Configurable delay between requests (default: 2s). |
| `recalculate_cvss_cascade` | On-demand | Triggered after any CVSS change. Recalculates severity, eligibility, and ticket state. |

## Data Model

See `docs/data-model.md` for the full schema. This feature introduces the
`CVECVSSAssessment` table and modifies the `CVE` table.

## Security

- Viewing CVSS data: all authenticated users
- Adding/editing/deleting SUSE CVSS: Security Team or Admin role
- Changing default CVSS version: Admin role only (see
  `docs/features/admin.md`)
- External CVSS data is read-only — cannot be modified through STAMP
