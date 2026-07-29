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
equivalent filtering, pagination, and sorting capabilities. The API must remain
independent of any specific client or hosting strategy.

**HTTP APIs for external services.** When integrating with external
services that expose HTTP/REST APIs (IBS, SMELT, AIMAAS, Bugzilla,
etc.), Sentinel uses those APIs directly. Service-wrapper CLI tools
(`osc`, `secbox`, etc.) add an unnecessary process dependency on top
of APIs that are already directly usable, and must not be used in
application code or background tasks.

This constraint does not apply to transport-protocol clients where the
data source has no HTTP API equivalent. The `git` binary is the
transport protocol for cloning and diffing repositories (MITRE
cvelistV5, Linux Kernel vulns.git) — there is no REST API alternative
for these data sources. Git-based fetchers invoke `git` via
`asyncio.create_subprocess_exec` through the `BaseGitFetcher`
infrastructure (see Integration Patterns below and
`docs/features/platform/git-fetcher-infrastructure.md`).

**Environment-variable configuration.** Deployment and infrastructure
configuration is provided through environment variables. Secrets must
not be baked into images or committed to the repository. Business
settings that require runtime modification without restart are stored
in the database (see `docs/configuration.md`, Runtime Database
Settings). See `docs/configuration.md` for the variable reference and
`docs/conventions.md` (Configuration Management) for the governance
model.

---

## Backend Layer Architecture

The backend is organized into seven layers with strict dependency
direction. Upper layers may depend on lower layers; lower layers must
not import from upper layers.

| Layer | Location | Responsibility | May depend on |
|---|---|---|---|
| **API** | `app/api/v1/` | Thin endpoint handlers: validate input, call services, return responses. No business logic. | Service, Schema, Model (for DI type annotations), Core |
| **CLI** | `app/cli/` | Thin command handlers: parse arguments, call services, format output. No business logic. | Service, Model (direct queries limited to read-only; writes go through Service), Core |
| **Service** | `app/services/` | All business logic. Accept typed parameters, perform database operations, return typed results. | Model, Core |
| **Model** | `app/models/` | SQLAlchemy ORM models: tables, columns, relationships, constraints. | Core (for enums only) |
| **Schema** | `app/schemas/` | Pydantic models for request/response validation and serialization. | Model (for `from_attributes`), Core |
| **Core** | `app/core/` | Cross-cutting concerns: authentication, authorization, configuration, exceptions, enums. | (no application imports) |
| **Task** | `app/tasks/` | Celery task definitions: thin wrappers that call service-layer functions. | Service, Core |

**Key rules:**

- API handlers must not contain business logic — they validate, delegate
  to a service, and format the response
- CLI commands must not contain business logic — they parse arguments,
  delegate to a service (or perform direct read-only queries), and
  format the output
- Task definitions must not contain business logic — they are thin
  wrappers that call service-layer functions
- Service modules are the only layer that performs database operations
- The Core layer has no application-level imports; it is a leaf
  dependency

---

## Integration Patterns

Sentinel integrates with 12+ external services (see `docs/data-sources.md`
for the full catalog). All integrations follow one of two patterns:

**Scheduled (periodic fetchers).** The majority of integrations use
periodic fetching via Celery Beat. Every scheduled integration is
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
submission state changes. A periodic fetcher runs alongside as
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
| CVE fetcher infrastructure | `docs/features/platform/cve-fetcher-infrastructure.md` |
| Git-based fetcher infrastructure | `docs/features/platform/git-fetcher-infrastructure.md` |
| IBS RabbitMQ integration (event-driven) | `docs/features/integrations/ibs-rabbitmq-integration.md` |
| Operational logging | `docs/features/platform/logging.md` |
