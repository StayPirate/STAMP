# Architecture Rewrite Plan

## Objective

Replace `docs/architecture.md` with a new document focused exclusively
on **architectural decisions, design constraints, and structural rules**
— the "why" of the system's shape. Operational topology (process roles,
singleton constraints, clock sync, health checks) moves to
`docs/deployment.md` where it naturally belongs.

The current file (459 lines) mixes architectural decisions with
operational procedures, integration catalogs, and data flow details that
already have authoritative homes in other documents. This creates drift
risk and maintenance burden.

The new `architecture.md` will be ~260 lines. `deployment.md` absorbs
the operational pieces that were misplaced in the current architecture
file.

## Problems with the Current File

1. **Deployment Portability is one-third of the file** — 9 subsections
   (~150 lines) covering operational concerns already in `deployment.md`
   (Database Migrations, Configuration And Secrets, API Routing,
   Environments, Deployment Target)
2. **External Integrations duplicates `data-sources.md`** — per-service
   details (endpoint URLs, query parameters, event names, schedules)
   that are maintained in feature specs and `data-sources.md`
3. **Data Flow duplicates `system-map.md`** — 4 flow descriptions that
   `system-map.md` covers with mermaid diagrams and narrative
4. **No architectural decisions documented** — the file describes WHAT
   the system is (components, flows) but never explains WHY it is
   shaped this way (async-only, single image, Celery, no result
   backend, capability-based RBAC)
5. **No dependency rules** — backend layers are listed but the
   import/dependency rules between them are not specified
6. **Tail sections are stubs** — Environments (3 bullets),
   Observability (1 paragraph), Security Considerations (4 bullets)
   add nothing beyond what already exists in `deployment.md`,
   `logging.md`, and `rbac.md`
7. **No scope section** — the document does not define what it covers
   or what it does not cover, leading to content creep
8. **Operational topology mixed with architecture** — Container Images,
   Singleton Processes, Clock Synchronization, and Health And Readiness
   are deployment topology and infrastructure requirements, not
   architectural decisions. They ended up in `architecture.md`
   historically and other documents reference them there, but they
   belong in `deployment.md`

## Supersedes

This draft supersedes `docs/drafts/architecture-restructure.md`, which
planned an incremental restructure of the existing file. The approach
here is a clean rewrite — the old draft will be deleted as part of
execution.

## Information Loss Analysis

Every section of the current file has been audited against the rest of
the documentation. No information is lost by this rewrite:

| Current section | Lines | Disposition | New location |
|---|---|---|---|
| System Overview | 3-7 | Absorbed | new `architecture.md` (Scope + System Boundary) |
| High-Level Architecture (ASCII) | 9-52 | Dropped | `system-map.md` (mermaid version) |
| Backend (FastAPI) tech stack | 56-63 | Absorbed | new `architecture.md` (Architectural Decisions) |
| Backend Layers | 65-78 | Absorbed | new `architecture.md` (Backend Layer Architecture) |
| Task Queue (Celery) | 80-103 | Split | new `architecture.md` (Arch. Decisions + Integration Patterns) |
| Database (PostgreSQL) | 105-109 | Absorbed | new `architecture.md` (1-line in Arch. Decisions) |
| External Integrations (6 sub-secs) | 111-197 | Dropped | `data-sources.md` + feature specs |
| Data Flow (4 sub-secs) | 199-273 | Dropped | `system-map.md` (diagrams + narrative) |
| Deployment Target | 284-293 | Absorbed | new `architecture.md` (Design Constraints) |
| Container Images | 295-321 | Moved | `deployment.md` (Process Architecture > Container Images) |
| Runtime State | 323-337 | Absorbed | new `architecture.md` (Design Constraints) |
| Configuration And Secrets | 339-349 | Dropped | `configuration.md` + `conventions.md` |
| Database Migrations | 351-359 | Dropped | `deployment.md` (Operations > Database Migrations) |
| Singleton Processes | 361-370 | Moved | `deployment.md` (Process Architecture > Singleton Processes) |
| API Routing | 372-376 | Dropped | `api-spec.md` (line 7) + `deployment.md` (line 261) |
| Repository Scope | 378-388 | Absorbed | new `architecture.md` (System Boundary) |
| Clock Synchronization | 390-406 | Moved | `deployment.md` (Process Architecture > Clock Synchronization) |
| Health And Readiness | 408-423 | Moved | `deployment.md` (Operations > Health Checks) |
| Environments | 425-431 | Dropped | `deployment.md` (Environments section) |
| Observability | 433-450 | Dropped | `logging.md` + `deployment.md` (Log Aggregation) |
| Security Considerations | 452-459 | Dropped | `rbac.md` + feature specs |

## New `docs/architecture.md`: Complete Content

The following is the exact content of the new file.

```markdown
# Architecture

## Contents

- [Scope](#scope)
- [System Boundary](#system-boundary)
- [Architectural Decisions](#architectural-decisions)
- [Design Constraints](#design-constraints)
- [Backend Layer Architecture](#backend-layer-architecture)
- [Integration Patterns](#integration-patterns)
- [Cross-Reference Index](#cross-reference-index)

---

## Scope

This document defines the structural decisions and design constraints of
the Sentinel platform — the reasoning behind the system's shape. It is
the entry point for understanding why the system is built the way it is.

This document does NOT contain:

| Information type | Authoritative location |
|---|---|
| Database schema (tables, columns, relationships) | `docs/data-model.md` |
| External data source catalog (hosts, protocols, credentials) | `docs/data-sources.md` |
| Deployment procedures, process topology, operations | `docs/deployment.md` |
| Environment variable reference | `docs/configuration.md` |
| Code patterns, naming, style conventions | `docs/conventions.md` |
| API envelope format, errors, pagination | `docs/api-spec.md` |
| Component diagrams and data flow visuals | `docs/system-map.md` |

---

## System Boundary

Sentinel is a security update management platform for SUSE and
openSUSE-based Linux distributions. It automates CVE tracking, impact
analysis, and update coordination across multiple maintained
distribution versions.

**What Sentinel does:**

- Ingests CVE data from multiple public and internal sources
- Creates and manages security tickets from CVE detections
- Tracks which packages, codestreams, and products are affected
- Detects when security fixes are released via IBS
- Evaluates product eligibility based on CVSS thresholds and lifecycle
- Provides an API for vulnerability analysts, team leads, and
  automation

**What Sentinel does NOT do:**

- Build or submit packages (IBS handles builds)
- Manage product lifecycle or release schedules (SMELT/AIMAAS own this)
- Provision user identities (deferred to external identity provider)
- Provide a web UI (frontend is developed in a separate repository)

**Repository scope:** this repository contains all product
specifications (in `docs/features/`) and the backend implementation
(FastAPI, Celery workers, migrations, CI/CD). The frontend will be
developed in a dedicated repository against the published OpenAPI
contract.

---

## Architectural Decisions

Decisions that shape the system at a structural level. Each entry
states the choice and its rationale.

### Async-only database layer

Sentinel uses `asyncpg` with SQLAlchemy's async API for all database
access — API handlers, service modules, Celery tasks, and CLI commands.
No synchronous database driver or engine is maintained. Synchronous
entry points (CLI commands, Celery signal handlers) bridge into the
async layer via `asyncio.run()`.

**Rationale:** a single database access model eliminates the risk of
accidentally mixing sync and async sessions (which causes subtle bugs
with connection pools and transaction isolation). It also ensures that
service-layer code is reusable across all entry points without
adaptation.

### Single Docker image, multiple entrypoints

All runtime processes — API server, Celery worker, git worker, Celery
Beat, IBS RabbitMQ consumer — run from the same OCI image with
different entrypoint commands. There are no per-process image variants.
See `docs/deployment.md` (Container Images) for the canonical process
role enumeration.

**Rationale:** all processes share the same codebase and dependencies.
Separate images would multiply build time and storage without
functional benefit, and risk version skew between components that must
run the same code.

### Celery + Redis with no result backend

Sentinel uses Celery with Redis as broker. The Celery result backend is
disabled (`task_ignore_result = True`). Task outcomes are tracked in
PostgreSQL via `FetcherRun` records, not in Redis.

**Rationale:** fetcher runs need durable, queryable outcome data
(duration, item counts, error messages, cursor state) that survives
Redis restarts. The `FetcherRun` table satisfies this. A Celery result
backend would duplicate this data in Redis with inferior queryability
and durability.

### PostgreSQL as single source of truth

All persistent state lives in PostgreSQL. Redis is used only for
ephemeral coordination (task queue, schedule entries, caches, locks).
Redis persistence is disabled by design — all Redis state is either
TTL-bounded and self-healing, or fully reconstructible from PostgreSQL
at process startup.

**Rationale:** a single source of truth simplifies backup, recovery,
and operational reasoning. See `docs/deployment.md` (Redis Durability,
Memory, and Persistence) for the operational implications.

### Capability-based RBAC

Authorization uses a capability model: roles map to sets of
capabilities, and endpoint access is determined by checking whether
the caller possesses the required capability. This is distinct from
pure role-based checking ("is the user an admin?") and from
attribute-based access control (ABAC).

**Rationale:** capability-based checking decouples endpoint
authorization from role definitions. New roles can be created by
combining existing capabilities without modifying endpoint code. See
`docs/features/identity/rbac.md` for the full model.

### Specs-first development

Every feature must be specified in `docs/features/` before
implementation begins. The specification must be complete enough that
an implementer can write a correct implementation without making
autonomous design decisions.

**Rationale:** specifications serve as the contract between design and
implementation. They enable parallel review (spec review before code
review), prevent scope creep during implementation, and provide a
stable reference for future maintenance.

---

## Design Constraints

Non-negotiable principles that inform every feature specification and
implementation decision.

**Stateless containers.** Application containers must not rely on local
persistent filesystem state for correctness. Persistent state belongs
in PostgreSQL, Redis, or external services. Recoverable caches (e.g.,
git clone volumes used by CVE fetchers) may use persistent local
storage for performance, provided the application remains correct
without them.

**Deployment-agnostic packaging.** The application must not depend on a
specific runtime orchestrator. Docker, Podman, and Kubernetes must
consume the same images without environment-specific variants. Runtime
differences belong in deployment configuration, not in application
code. Kubernetes-specific manifests are deferred until the
infrastructure target is decided.

**API-first.** The REST API is the primary interface. Every operation
that could be needed by any consumer (web UI, CLI, scripts,
third-party integrations) must be achievable through the API, with
appropriate filtering, pagination, and sorting. The API must remain
independent of any specific client or hosting strategy.

**HTTP APIs only.** When integrating with external services (IBS, SMELT,
AIMAAS, Bugzilla, etc.), Sentinel uses their HTTP/REST APIs directly.
Command-line tools (`osc`, `secbox`, etc.) are for ad-hoc exploratory
testing only and must not be used in application code or background
tasks.

**Environment-variable configuration.** All runtime configuration is
provided through environment variables. Secrets must not be baked into
images or committed to the repository. See `docs/configuration.md` for
the variable reference and `docs/conventions.md` (Configuration
Management) for the governance model.

---

## Backend Layer Architecture

The backend is organized into six layers with strict dependency
direction. Upper layers may depend on lower layers; lower layers must
not import from upper layers.

| Layer | Location | Responsibility | May depend on |
|---|---|---|---|
| **API** | `app/api/v1/` | Thin endpoint handlers: validate input, call services, return responses. No business logic. | Service, Schema, Core |
| **Service** | `app/services/` | All business logic. Accept typed parameters, perform database operations, return typed results. | Model, Core |
| **Model** | `app/models/` | SQLAlchemy ORM models: tables, columns, relationships, constraints. | Core (for enums only) |
| **Schema** | `app/schemas/` | Pydantic models for request/response validation and serialization. | Model (for `from_attributes`), Core |
| **Core** | `app/core/` | Cross-cutting concerns: authentication, authorization, configuration, exceptions, enums. | (no application imports) |
| **Task** | `app/tasks/` | Celery task definitions: thin wrappers that call service-layer functions. | Service, Core |

**Key rules:**

- API handlers must not contain business logic — they validate, delegate
  to a service, and format the response
- Task definitions must not contain business logic — they are thin
  wrappers that call service-layer functions
- Service modules are the only layer that performs database operations
- The Core layer has no application-level imports; it is a leaf
  dependency

---

## Integration Patterns

Sentinel integrates with 12+ external services (see `docs/data-sources.md`
for the full catalog). All integrations follow one of two patterns:

**Polling (scheduled fetchers).** The majority of integrations use
periodic polling via Celery Beat. Every polling integration is
implemented as a `BaseFetcher` subclass, which provides automatic
execution tracking, metric collection, and registry. The fetcher class
hierarchy is:

- `BaseFetcher` — generic base for all fetchers (product sync, release
  detection, etc.). See `docs/features/platform/fetcher-infrastructure.md`.
- `BaseCVEFetcher` — extends `BaseFetcher` for CVE data sources.
  Adds `cve_source_type` identity, optional `fetch_single()` for
  on-demand fetch, and a default `catch_up()` implementation. See
  `docs/features/platform/cve-fetcher-infrastructure.md`.
- `BaseGitFetcher` — extends `BaseCVEFetcher` for git-based CVE
  sources. Uses delta-flow (clone + fetch + diff) instead of REST API
  polling. Requires a persistent volume on a dedicated git worker. See
  `docs/features/platform/git-fetcher-infrastructure.md`.

**Event-driven (message consumer).** The IBS integration additionally
uses a real-time AMQP consumer connected to the IBS RabbitMQ message
bus. This provides near-real-time detection of package commits and
submission state changes. A periodic polling fetcher runs alongside as
a catch-up mechanism for events missed during consumer downtime. See
`docs/features/integrations/ibs-rabbitmq-integration.md`.

---

## Cross-Reference Index

| Topic | Document |
|---|---|
| Database schema (tables, columns, constraints) | `docs/data-model.md` |
| External data sources (catalog, protocols, hosts) | `docs/data-sources.md` |
| Component diagrams and data flow visuals | `docs/system-map.md` |
| API surface (endpoints, errors, pagination) | `docs/api-spec.md` |
| Code patterns and conventions | `docs/conventions.md` |
| Environment variables and configuration | `docs/configuration.md` |
| Deployment, process topology, operations | `docs/deployment.md` |
| CLI commands reference | `docs/cli-reference.md` |
| Identity and access control | `docs/features/identity/rbac.md` |
| Fetcher infrastructure | `docs/features/platform/fetcher-infrastructure.md` |
| Operational logging | `docs/features/platform/logging.md` |
```

## Changes to `docs/deployment.md`

Three pieces move from `architecture.md` to `deployment.md`:
Container Images + Singleton Processes (into Process Architecture),
Clock Synchronization (new subsection), and Health and Readiness
(merged into existing Health Checks).

### Change A — Restructure Process Architecture section

Replace the current section intro and add a `### Container Images`
subsection. The existing table gains the process role context that
was previously deferred to `architecture.md`.

**Current** (lines 387-402):

```
## Process Architecture

Sentinel's process roles are defined in `docs/architecture.md`
(Container Images). This section documents their operational properties
for deployment.

| Process | Role | Scalable |
|---------|------|----------|
| API server (uvicorn) | HTTP request handling | Yes (multiple replicas) |
| Celery worker | Background task execution | Yes (multiple workers) |
| Git worker (Celery) | Background git-based fetcher execution | No (single volume affinity) |
| Celery Beat | Periodic task scheduling | No (singleton) |
| IBS RabbitMQ consumer | Real-time event consumption | No (singleton — see spec) |

Alembic migration jobs are one-shot processes, not runtime services —
see Database Migrations (below) for operational details.
```

**New:**

```
## Process Architecture

### Container Images

All runtime processes run from the same OCI image with different
entrypoint commands — see `docs/architecture.md` (Single Docker image,
multiple entrypoints) for the rationale. This is the canonical
enumeration of all process roles.

**Runtime processes** (long-running):

| Process | Role | Scalable |
|---------|------|----------|
| API server (uvicorn) | HTTP request handling | Yes (multiple replicas) |
| Celery worker | Background task execution | Yes (multiple workers) |
| Git worker (Celery) | Git-based fetcher execution | No (single, volume affinity — see [Git Worker Volume](#git-worker-volume)) |
| Celery Beat | Periodic task scheduling | No (singleton) |
| IBS RabbitMQ consumer | Real-time event consumption | No (singleton — see `docs/features/integrations/ibs-rabbitmq-integration.md`) |

**One-shot jobs:**

- Alembic migration job — see [Database Migrations](#database-migrations)
```

### Change B — Make Singleton Processes canonical

Remove the cross-reference to `architecture.md` and expand with the
full content that was in the old `architecture.md`.

**Current** (lines 404-409):

```
### Singleton Processes

Celery Beat and the IBS RabbitMQ consumer must run as single instances.
Running multiple replicas causes duplicate task scheduling or duplicate
event processing. See `docs/architecture.md` (Singleton Processes) for
the architectural constraint.
```

**New:**

```
### Singleton Processes

Celery Beat and the IBS RabbitMQ consumer are singleton processes.
Running multiple instances causes duplicate task scheduling or duplicate
event processing. The git worker is constrained to a single instance by
volume affinity (ReadWriteOnce), not by a logical singleton requirement.

Local environments run one instance of each. Kubernetes deployments must
enforce singleton constraints unless a future design introduces
distributed locking or leader election.
```

### Change C — Add Clock Synchronization subsection

Insert a new `### Clock Synchronization` subsection under
`## Process Architecture`, after `### Timezone and Locale Requirements`
(after line 505). This is the full text from the old `architecture.md`
section, unchanged.

**Insert after the end of "Timezone and Locale Requirements":**

```
### Clock Synchronization

All application instances in a multi-instance deployment must have their
system clocks synchronized via NTP (or an equivalent time
synchronization protocol). Sentinel relies on timestamps for several
security and correctness mechanisms:

- SSO state parameter validation (10-minute TTL window)
- JWT `exp` and `iat` claim verification
- Session expiration enforcement
- Cache TTL calculations (discovery document, JWKS)

Clock skew between instances can shorten or lengthen time-based windows
unpredictably. For example, if the instance generating an SSO state has
a clock 2 minutes ahead of the instance processing the callback, the
effective validity window shrinks from 10 minutes to 8 minutes. While
modern NTP-synced servers typically maintain sub-second accuracy (making
this negligible in practice), operators must ensure NTP is configured
and running on all hosts.
```

### Change D — Expand Health Checks with architectural intent

Add an introductory sentence that captures the architectural intent
previously in `architecture.md`.

**Current** (lines 532-536):

```
### Health Checks

See `docs/features/platform/health-endpoints.md` for the authoritative
endpoint specification (response schemas, failure semantics, design
decisions).
```

**New:**

```
### Health Checks

The API exposes lightweight liveness and readiness checks so
orchestrators can distinguish between a running process and a service
ready to handle traffic. See
`docs/features/platform/health-endpoints.md` for the authoritative
endpoint specification (response schemas, failure semantics, design
decisions).
```

### Change E — Update `deployment.md` TOC

Add the new subsections to the Contents listing.

**Current** (lines 30-34):

```
- [Process Architecture](#process-architecture)
  - [Singleton Processes](#singleton-processes)
  - [Startup Ordering](#startup-ordering)
  - [Git Worker Volume](#git-worker-volume)
  - [Timezone and Locale Requirements](#timezone-and-locale-requirements)
```

**New:**

```
- [Process Architecture](#process-architecture)
  - [Container Images](#container-images)
  - [Singleton Processes](#singleton-processes)
  - [Startup Ordering](#startup-ordering)
  - [Git Worker Volume](#git-worker-volume)
  - [Timezone and Locale Requirements](#timezone-and-locale-requirements)
  - [Clock Synchronization](#clock-synchronization)
```

### Change F — Update `deployment.md` intro cross-reference

**Current** (line 7-8):

```
For environment variable reference, see `docs/configuration.md`.
For architectural decisions and portability constraints, see
`docs/architecture.md`.
```

**New:**

```
For environment variable reference, see `docs/configuration.md`.
For architectural decisions and design constraints, see
`docs/architecture.md`.
```

### Change G — Update internal references within `deployment.md`

Three lines reference `docs/architecture.md` (Container Images) for
the process role enumeration. Since Container Images is now in
`deployment.md` itself, these become internal references.

**Change G1** (line 210-211):

```
BEFORE:
4. **Start all runtime processes** defined in `docs/architecture.md`
   (Container Images) — each as a separate container/process

AFTER:
4. **Start all runtime processes** defined in
   [Container Images](#container-images) — each as a separate
   container/process
```

**Change G2** (line 243-244):

```
BEFORE:
2. **Deploy all runtime processes** defined in `docs/architecture.md`
   (Container Images)

AFTER:
2. **Deploy all runtime processes** defined in
   [Container Images](#container-images)
```

**Change G3** (line 465):

```
BEFORE:
All Sentinel runtime processes (see `docs/architecture.md`, Container
Images) MUST operate with UTC as the system timezone.

AFTER:
All Sentinel runtime processes (see [Container Images](#container-images))
MUST operate with UTC as the system timezone.
```

## Execution Plan

### Step 1 — Replace `docs/architecture.md`

Delete the current `docs/architecture.md` (459 lines) and write the
content from the "New `docs/architecture.md`: Complete Content" section
above.

**Verification:** confirm the new file has the sections: Scope, System
Boundary, Architectural Decisions, Design Constraints, Backend Layer
Architecture, Integration Patterns, Cross-Reference Index.

### Step 2 — Update `docs/deployment.md`

Apply changes A through G from the "Changes to `docs/deployment.md`"
section above:

- A: Restructure Process Architecture section with Container Images
  subsection
- B: Make Singleton Processes canonical (remove architecture.md ref)
- C: Add Clock Synchronization subsection
- D: Expand Health Checks intro
- E: Update TOC
- F: Update intro cross-reference
- G: Convert 3 external references to internal links (G1, G2, G3)

**Verification:** confirm new subsection anchors exist: `container-images`,
`clock-synchronization`. Confirm no remaining references to
`docs/architecture.md` within `deployment.md` for sections that were
moved.

### Step 3 — Update `docs/features/platform/logging.md`

Four references need updating:

**Change 3a** (line 41):

```
BEFORE:
   the only choice consistent with `docs/architecture.md` (Runtime
   State: "Application containers are stateless... must not rely on
   local persistent filesystem state for correctness") and 12-factor

AFTER:
   the only choice consistent with `docs/architecture.md` (Design
   Constraints: "Application containers must not rely on local
   persistent filesystem state for correctness") and 12-factor
```

**Change 3b** (line 147):

```
BEFORE:
`git-worker`, `beat`, `ibs-consumer` — per `docs/architecture.md`,
Container Images) emitted it.

AFTER:
`git-worker`, `beat`, `ibs-consumer` — per `docs/deployment.md`,
Container Images) emitted it.
```

**Change 3c** (line 260):

```
BEFORE:
`docs/architecture.md`, Container Images) performs per-message

AFTER:
`docs/deployment.md`, Container Images) performs per-message
```

**Change 3d** (line 433):

```
BEFORE:
- `docs/architecture.md` (Runtime State, Container Images) — the
  stateless-container principle and the 5 runtime roles referenced
  throughout this document.

AFTER:
- `docs/architecture.md` (Design Constraints) — the
  stateless-container principle.
- `docs/deployment.md` (Container Images) — the 5 runtime roles
  referenced throughout this document.
```

### Step 4 — Update `docs/features/platform/health-endpoints.md`

**Change** (line 187):

```
BEFORE:
- `docs/architecture.md` — Health And Readiness (architectural intent)

AFTER:
- `docs/deployment.md` — Health Checks (architectural intent and
  operational configuration)
```

### Step 5 — Update `docs/features/identity/sso-authentication.md`

**Change** (line 175):

```
BEFORE:
see `docs/architecture.md`, Clock Synchronization.

AFTER:
see `docs/deployment.md`, Clock Synchronization.
```

### Step 6 — Update `docs/conventions.md`

**Change** (line 1013):

```
BEFORE:
with different entrypoints (see `docs/architecture.md`, Container
Images).

AFTER:
with different entrypoints (see `docs/deployment.md`, Container
Images).
```

### Step 7 — Update `docs/data-sources.md`

**Change** (lines 11-13):

```
BEFORE:
For details on how Sentinel architecturally integrates with each active source,
see `docs/architecture.md` and the relevant feature specifications in
`docs/features/`.

AFTER:
For integration patterns (fetcher hierarchy, polling vs event-driven),
see `docs/architecture.md`. For per-source specifications, see the
relevant feature specifications in `docs/features/`.
```

### Step 8 — Update `AGENTS.md`

**Change 8a** (Guardrail 21, Cross-cutting document mapping table):

```
BEFORE:
| External system integration (protocols, URLs, auth) | `docs/data-sources.md` / `docs/architecture.md` |

AFTER:
| External system integration (protocols, URLs, auth) | `docs/data-sources.md` |
```

### Step 9 — Delete `docs/drafts/architecture-restructure.md`

Delete the old restructure plan. It is superseded by this rewrite.

### Step 10 — Verify no broken anchors

Run a grep across the full repository for references to
`architecture.md` with section names. Verify that every textual section
reference matches an existing heading in the new file.

**Expected matches and their status after all steps:**

| Reference pattern | Files | Status |
|---|---|---|
| `architecture.md` (Design Constraints) | `logging.md` (×1) | Created by Step 3a — matches new heading |
| `architecture.md` (Single Docker image, multiple entrypoints) | `deployment.md` (×1) | Created by Change A — matches new heading |
| `architecture.md` (generic, no section) | `AGENTS.md` (×3), `.opencode/` (×8), `system-map.md` (×1) | File path unchanged — OK |
| `architecture.md` in `docs/reviews/` | 4 review files | Historical notes — no update needed |
| `deployment.md` (Container Images) | `logging.md` (×2), `conventions.md` (×1) | Created by Steps 3b, 3c, 6 — matches new heading in deployment.md |
| `deployment.md` (Clock Synchronization) | `sso-authentication.md` (×1) | Created by Step 5 — matches new heading in deployment.md |
| `deployment.md` — Health Checks | `health-endpoints.md` (×1) | Created by Step 4 — matches existing heading |
| `deployment.md` — Database Migrations | `Dockerfile` (×1) | Created by Step 11 — matches existing heading |

**Sections that must NOT appear as references to `architecture.md` after
all steps (they were moved):**

- `Container Images` → all updated to `deployment.md`
- `Singleton Processes` → reference removed (now canonical in deployment.md)
- `Clock Synchronization` → updated to `deployment.md`
- `Health And Readiness` / `Health and Readiness` → updated to `deployment.md`
- `Runtime State` → updated to `Design Constraints`
- `Database Migrations` → updated to `deployment.md` (was already stale)

### Step 11 — Update `backend/Dockerfile`

The Dockerfile has a comment (line 48) referencing
`docs/architecture.md, Database Migrations`. This reference was already
stale before this rewrite — the Database Migrations section has always
lived in `deployment.md`. Fix it for consistency with the rest of the
cross-reference cleanup.

**Change** (line 48):

```
BEFORE:
# this same image via a one-shot job — see docs/architecture.md,
# Database Migrations)

AFTER:
# this same image via a one-shot job — see docs/deployment.md,
# Database Migrations)
```

### Step 12 — Run reviewers

Run the following reviewers on the modified specifications to verify
correctness and coherence:

1. **`@docs-reviewer`** on `docs/architecture.md` — verify the new
   document is complete, accurate, and consistent with the codebase
2. **`@docs-placement-reviewer`** on `docs/architecture.md` — verify
   that no information belongs in a different document, and no
   information that belongs here was placed elsewhere
3. **`@spec-coherence-reviewer`** on `docs/architecture.md` — verify
   no contradictions with other specs
4. **`@spec-coherence-reviewer`** on `docs/deployment.md` — verify
   the absorbed sections are coherent with existing deployment content
   and no contradictions were introduced
5. **`@spec-coherence-reviewer`** on
   `docs/features/platform/logging.md` — verify the updated
   cross-references are correct
6. **`@spec-coherence-reviewer`** on
   `docs/features/platform/health-endpoints.md` — verify the updated
   cross-reference is correct

Address any "Needs revision" findings before proceeding to Step 13.

### Step 13 — Delete this draft

Delete `docs/drafts/architecture-rewrite.md` and commit.
