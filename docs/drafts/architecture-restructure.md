# Architecture Restructure Plan

## Objective

Reorganize `docs/architecture.md` to improve structural consistency,
reduce section imbalance, and fix misplaced subsections — without
changing the semantic content of the document.

## Current Problems

1. **"Deployment Portability" is oversized and misnamed** — 9 subsections
   (~150 lines, one-third of the file), several of which are not about
   portability (Repository Scope, Clock Synchronization, API Routing)
2. **Orphaned tail sections** — "Environments" (3 bullets), "Security
   Considerations" (4 bullets), "Observability" (1 paragraph) give the
   impression of an unfinished document
3. **Missing Table of Contents** — 459 lines with deep nesting, no TOC
4. **Data Flow asymmetry** — "Manual Ticket Creation" is a single
   paragraph, not a flow, and already documented in `tickets.md`
5. **External Integrations detail asymmetry** — IBS gets 22 lines with
   implementation details (event names, schedules) while others get 3-5
   lines; all detail already lives in feature specs
6. **Inconsistent heading casing** — "Configuration And Secrets",
   "Health And Readiness" capitalize conjunctions

## Decisions Made

- "Clock Synchronization" stays in Deployment (operational requirement)
- "Security Considerations" and "Observability" remain as placeholders
  for future expansion
- Implementation detail removed from External Integrations is confirmed
  to exist in feature specs (all 9 items verified)
- "API Routing" stays in Deployment (deployment topology concern, not
  app code) — reviewer feedback confirmed it would be miscategorized
  under Backend
- AIMAAS section must have an explicit cross-ref to
  `product-catalog.md` after trimming (consistency with IBS/SMELT)
- Release Tracking Flow must also be trimmed (same detail that is
  removed from External Integrations appears there too)

## Target Structure

```
# Architecture
├── Contents (TOC)
├── System Overview
├── High-Level Architecture (diagram)
├── Components
│   ├── Backend (FastAPI)
│   │   └── Backend Layers
│   ├── Task Queue (Celery)
│   ├── Database (PostgreSQL)
│   └── External Integrations
│       ├── CVE Sources
│       ├── IBS (trimmed)
│       ├── SMELT (trimmed)
│       ├── AIMAAS (trimmed)
│       ├── OBS
│       └── External Identity Provider
├── Data Flow
│   ├── CVE Ingestion Flow
│   ├── Package Affectedness Flow
│   └── Release Tracking Flow
├── Deployment [renamed from "Deployment Portability"]
│   ├── Target Environments [merged from "Environments"]
│   ├── Container Images
│   ├── Runtime State
│   ├── Configuration and Secrets [casing fix]
│   ├── Database Migrations
│   ├── Singleton Processes
│   ├── Clock Synchronization
│   ├── API Routing [kept here — deployment topology]
│   └── Health and Readiness [casing fix]
├── Repository Scope [promoted to top-level]
├── Observability
└── Security Considerations
```

## Execution Phases

### Phase 1 — Structural skeleton and headings

**Changes to `docs/architecture.md`:**
- Add `## Contents` (TOC) after `# Architecture`
- Rename `## Deployment Portability` to `## Deployment`. Preserve the
  introductory paragraph (lines 277-282, the portability rationale) as
  the intro to the renamed section — it must NOT be dropped
- Normalize heading casing:
  - "Configuration And Secrets" → "Configuration and Secrets"
  - "Health And Readiness" → "Health and Readiness"
- Merge `### Deployment Target` and `## Environments` into a single
  `### Target Environments` subsection inside `## Deployment`
  (positioned as first subsection). Structure of the merged content:
  1. First: the portability principle from "Deployment Target" (the
     bullet list and the closing paragraph about runtime differences)
  2. Then: the concrete environment list from "Environments" (dev,
     staging, production bullets)
- Remove the now-empty `## Environments` section

**Cross-reference updates:**
- `docs/features/platform/health-endpoints.md:187` — update "Health And
  Readiness" → "Health and Readiness"

**Verification:** commit, then diff to confirm no content lost.

### Phase 2 — Relocate misplaced subsections

**Changes to `docs/architecture.md`:**
- Move `### Repository Scope` to top-level `## Repository Scope`
  (between "Deployment" and "Observability")

**Cross-reference updates:** none expected (no external file references
"Repository Scope" by name).

**Verification:** commit, then diff to confirm no content lost.

### Phase 3 — Content balancing

**Changes to `docs/architecture.md` — High-Level Architecture diagram:**
- Rename the box title from `IBSEventConsumer` to `IBS RabbitMQ Consumer`
  (matches the canonical process role name in Container Images section;
  diagram uses title case for box labels)
- Simplify the box content (lines 47-50): remove specific event names
  (`package.commit`, `request.create`, `request.state_change`).
  Replace with generic description. Full target box:
  ```
  ┌──────────────────┐     ┌──────────────────────────────────┐
  │  IBS RabbitMQ    │────▶│     IBS RabbitMQ Consumer         │
  │ (rabbit.suse.de) │     │                                  │
  └──────────────────┘     │  Consumes IBS events for         │
                           │  release & submission tracking.  │
                           └──────────────────────────────────┘
  ```
  Rationale: same trimming philosophy as the text sections — event
  names and class names are implementation detail documented in the
  feature spec.

**Changes to `docs/architecture.md` — External Integrations:**
- **IBS** — per-bullet decisions:

  | # | Current bullet | Decision | Result |
  |---|---|---|---|
  | 1 | "Internal OBS instance at build.suse.de..." | KEEP as-is | Architectural definition |
  | 2 | "Source packages are maintained in codestream projects..." | KEEP as-is | Architectural context |
  | 3 | "Sentinel queries IBS to detect when security fixes..." | KEEP as-is | Architectural role |
  | 4 | "Real-time event consumer: Sentinel connects to..." (7 lines) | TRIM | See target text below |
  | 5 | "Submission tracking: the same RabbitMQ consumer..." (4 lines) | REMOVE | Absorbed into trimmed bullet 4 |
  | 6 | "Package bugowner resolution: Sentinel queries IBS..." (3 lines) | KEEP as-is | Already at architectural level |
  | 7 | "See `docs/features/packages/package-model.md`..." | KEEP as-is | Pure cross-ref |

  **Bullet 4 target text** (replaces current bullets 4+5):
  > - **Real-time event consumer**: Sentinel connects to the IBS RabbitMQ
  >   message bus for near-real-time release and submission detection. A
  >   periodic fetcher serves as catch-up for events missed during
  >   downtime. See `docs/features/integrations/ibs-rabbitmq-integration.md`
  >   for the full specification.

- **SMELT** — replace current section (lines 151-165) with:
  > #### SMELT
  >
  > - Internal SUSE aggregator service (REST API at `smelt.suse.de/api`)
  > - SMELT internally reads from IBS, channel files, and other sources
  > - Sentinel uses SMELT for two purposes: periodic product catalog
  >   sync, and on-demand package-to-track resolution when adding a
  >   package to a ticket
  > - See `docs/features/packages/product-catalog.md` and
  >   `docs/features/packages/package-model.md` for full integration
  >   details

  Removes: endpoint URLs, query parameters, pagination detail,
  `ProductRepository` table name. Keeps: what SMELT is, the two
  architectural use cases, cross-refs.

- **AIMAAS** — replace current section (lines 167-180) with:
  > #### AIMAAS
  >
  > - Internal SUSE service (REST API at `aimaas.suse.de/api`) for
  >   product lifecycle data and CVSS thresholds
  > - Sentinel uses AIMAAS for two purposes: product lifecycle phase
  >   dates (used for product phase determination) and CVSS eligibility
  >   thresholds (used for product eligibility evaluation)
  > - When thresholds or lifecycle dates change, Sentinel re-evaluates
  >   eligibility for active tickets referencing the affected products
  > - See `docs/features/packages/product-catalog.md` for the full
  >   AIMAAS integration specification

  Removes: endpoint URLs, field name lists, CPE matching detail, entry
  count. Keeps: what AIMAAS is, the two architectural use cases,
  re-evaluation behavior, cross-ref.

**Changes to `docs/architecture.md` — Data Flow:**
- Remove `### Manual Ticket Creation` as standalone subsection
- Add one sentence in the `## Data Flow` intro: "Tickets can also be
  created manually without a CVE — see
  `docs/features/tickets/tickets.md`."
- **Release Tracking Flow**: replace points 1, 2, and 3 with the
  target text below. Points 4-5 remain unchanged (already at
  architectural level).

  **Point 1 target text** (replaces current lines 247-251):
  > 1. Track-level detection uses two complementary mechanisms: a
  >    real-time event consumer (via IBS RabbitMQ) and a periodic
  >    catch-up fetcher. See
  >    `docs/features/integrations/ibs-rabbitmq-integration.md` and
  >    `docs/features/packages/ibs-track-release-detection.md`.

  **Point 2 target text** (replaces current lines 252-259):
  > 2. **Track level**: the consumer or fetcher queries IBS to detect
  >    whether the fix for the ticket's CVE has landed in the codestream
  >    project. When detected, the track is marked as fixed. Separately,
  >    delivery status transitions to released when the Release Request
  >    (RR) is accepted. The two axes are independent. See
  >    `docs/features/packages/ibs-track-release-detection.md` and
  >    `docs/features/packages/ibs-submission-tracking.md`.

  **Point 3 target text** (replaces current lines 260-265):
  > 3. **Product level**: workers query product update repositories to
  >    detect advisories that reference the ticket's CVE. A multi-step
  >    package match identifies the specific source package fixed by the
  >    advisory. When matched, the product is recorded with the
  >    advisory's issue date. See
  >    `docs/features/packages/ibs-product-release-detection.md`.

  Removes from point 3: file names (`updateinfo.xml`, `primary.xml`),
  model field name (`TicketPackageProduct.released_at`), cascade step
  names, XML element name. Keeps: the architectural mechanism (query
  repos → find advisories → match packages → record date).

**Verification for removed detail:** all items confirmed to exist in:
- `docs/features/integrations/ibs-rabbitmq-integration.md` (IBS events)
- `docs/features/packages/ibs-track-release-detection.md` (track fetcher)
- `docs/features/packages/ibs-submission-tracking.md` (submission fetcher)
- `docs/features/packages/ibs-product-release-detection.md` (product detection)
- `docs/features/packages/package-bugowner.md` (bugowner)
- `docs/features/packages/product-catalog.md` (SMELT/AIMAAS endpoints)
- `docs/features/packages/package-model.md` (SMELT maintainedpackage)

**Cross-reference updates:** verify no external file points to
"Manual Ticket Creation" as a section name.

**Verification:** commit, then diff to confirm no information lost
(all removed detail exists in referenced specs).

### Phase 4 — Final cross-reference audit

- Full grep of all `.md` files for references to `architecture.md`
  with section names
- Verify each textual reference matches an existing heading
- Fix any remaining broken references

**Verification:** commit only if fixes are needed.

### Final step 1 — Reviewers

Run the following reviewers on `docs/architecture.md`:
- `@docs-reviewer`
- `@docs-placement-reviewer`
- `@spec-coherence-reviewer`

Address any "Needs revision" findings before proceeding.

### Final step 2 — Delete this draft

Remove `docs/drafts/architecture-restructure.md` and commit.

## Cross-Reference Inventory

Section names in `architecture.md` referenced by other files:

| Section name | Referenced by | Action |
|---|---|---|
| "Singleton Processes" | `docs/deployment.md:408` | Unchanged |
| "Container Images" | `docs/conventions.md:1013`, `docs/features/platform/logging.md` (x3), `docs/deployment.md` (lines 210, 243, 389, 465) | Unchanged |
| "Runtime State" | `docs/features/platform/logging.md` (x2) | Unchanged |
| "Clock Synchronization" | `docs/features/identity/sso-authentication.md:175` | Unchanged |
| "Health And Readiness" | `docs/features/platform/health-endpoints.md:187` | → "Health and Readiness" (Phase 1) |
| "Deployment Portability" | `docs/reviews/sso-authentication.md:33` | Historical review note, no update needed |
