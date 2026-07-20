# Draft: Application Logging Infrastructure — Change Plan

**Status**: DRAFT — for review before execution. This file is temporary
and MUST be deleted once the plan below has been fully applied (see
Step 12).

**Purpose of this document**: describe, in full and prescriptive
detail, a specification change that introduces application/operational
logging as a first-class, documented concern in Sentinel. This draft is
meant to be reviewed by a human before any spec file is touched, so
that design problems can be caught cheaply. Nothing in `docs/features/`,
`docs/*.md`, or `backend/` is modified by writing this draft — it is
pure planning.

---

## 1. Background and Problem Statement

### 1.1 What triggered this

An analysis of the current state of logging in Sentinel found:

- No dedicated feature specification for application/operational
  logging exists anywhere in `docs/features/platform/`.
- The only logging code in the backend is a bare
  `logger = logging.getLogger(__name__)` in `backend/app/config.py`,
  with a few `logger.warning(...)` calls. There is no
  `logging.basicConfig()`, no `dictConfig`, no formatter, no handler,
  no structured logging library, and no request-id correlation
  mechanism anywhere in `backend/app/main.py` or elsewhere.
- Dozens of feature specs prescribe specific log statements (e.g.
  "log WARNING", "log the error") assuming an implicit logging system
  that has never been formally defined (levels, format, destination,
  correlation).
- `docs/api-spec.md` (Request Tracing) explicitly promises that
  "The request ID is propagated to all log entries produced during
  request processing" — a contract with no corresponding
  implementation mechanism specified anywhere.
- `docs/deployment.md` includes a pre-production checklist item ("Log
  aggregation configured") with no supporting specification of what
  is being aggregated, in what format, or from where.
- `docs/drafts/ideas.md` already flags this gap: "Investigate how
  application logs are handled. Where are they saved? When are they
  rotated? Backup?"

### 1.2 What this change is NOT about

This change does **not** touch the existing audit trail infrastructure
(`docs/features/platform/audit-trail-infrastructure.md`,
`TicketAuditEvent`, `IdentityAuditEvent`, `SettingAuditEvent`,
`FetcherAuditEvent`). Audit trails are business events, persisted in
PostgreSQL, already fully specified, and out of scope here. This change
is exclusively about **operational/diagnostic logging** — the
transient, developer/operator-facing log stream (stdout/stderr),
distinct in purpose, persistence model, and consumption pattern from
audit trails.

### 1.3 Why now

The project is spec-first with **no implemented code beyond
scaffolding** and **no database** (no migrations exist). This is the
cheapest possible point to define the logging contract — before dozens
of services and fetchers are implemented against an ad hoc,
undocumented logging setup.

---

## 2. Scope of This Change

### 2.1 In scope

1. A new feature specification: `docs/features/platform/logging.md`.
2. Reconciliation edits to existing cross-cutting docs that currently
   reference logging informally or make promises about it:
   - `docs/configuration.md`
   - `docs/conventions.md`
   - `docs/architecture.md`
   - `docs/deployment.md`
   - `docs/api-spec.md`
   - `docs/features/platform/README.md`
   - `docs/drafts/ideas.md` (remove the now-addressed idea entry)
3. Alignment of the small amount of existing backend code
   (`backend/app/config.py`, `backend/.env.example`) with the new
   configuration variables introduced by the spec. **This is a spec
   change project — the alignment described here is limited to
   configuration surface (env var names/defaults) that already exists
   in code today, NOT new logging implementation code.** No new
   Python modules, middleware, or logging setup code will be written
   as part of executing this plan. Full implementation (structlog
   wiring, middleware, Celery signals) is explicitly deferred to a
   future implementation task, after this spec is approved.
4. Registration of the new spec in the review tracking system
   (`docs/reviews/.tracking.json` and `docs/reviews/README.md`),
   enabled for review.
5. Execution of the relevant spec reviewers against the new spec and
   the specs it touches, to catch problems introduced by this change
   (or pre-existing problems newly surfaced by it).
6. Deletion of this draft file once all steps are complete.

### 2.2 Out of scope (explicitly deferred)

- Writing actual logging implementation code (`app/core/logging.py`,
  middleware, Celery signal handlers, structlog configuration). This
  happens in a separate, future implementation task, after this
  specification is reviewed and approved.
- Adding `structlog` (or any logging library) to
  `backend/pyproject.toml` dependencies. Dependency changes are code
  changes, not spec changes, and belong to the implementation task.
- Rewriting the dozens of pre-existing "log WARNING/ERROR" statements
  scattered across other feature specs (fetcher specs, package specs,
  etc.). Those remain valid as-is; they become implicitly governed by
  the new `logging.md` spec as the canonical reference for level
  semantics, format, and correlation — no per-spec rewording is
  needed or planned.
- OpenTelemetry / distributed tracing / metrics. Explicitly rejected
  for this phase as premature (see Section 4).
- Any change to the audit trail infrastructure or any audit log
  feature spec.
- Any database schema change. No new tables, no migrations. Logs are
  never persisted in PostgreSQL.

---

## 3. Design Decisions (already agreed with stakeholder)

These decisions were discussed and confirmed before this draft was
written. They are restated here in full so the reviewer can evaluate
them in context, not just trust that a discussion happened.

### D1 — Library: `structlog`

Structured logging will use `structlog`, built on top of (and
integrated with) the Python standard library `logging` module so that
third-party library logs (uvicorn, Celery, SQLAlchemy, httpx) are
captured in the same pipeline and format, not just application code.

**Rationale**: better developer ergonomics for context binding
(`bind()`/`contextvars`) than hand-rolled stdlib JSON formatting;
mature, widely adopted, integrates cleanly with stdlib `logging` so
third-party libraries are not orphaned in a different format.

**Rejected alternative**: stdlib-only JSON formatter — technically
sufficient but more boilerplate for context propagation across
async/Celery boundaries, and worse ergonomics for the team.

**Rejected alternative**: `loguru` — pleasant API but weaker
multi-process/Celery integration story than `structlog` + stdlib
bridging.

### D2 — Output format and destination: structured, stdout/stderr only

- Log output is **JSON** in `LOG_FORMAT=json` (intended for
  production/staging) or a **human-readable console renderer** in
  `LOG_FORMAT=console` (intended for local development).
- Logs are written **exclusively to stdout/stderr**. The application
  **never** writes log files, never rotates files, never manages log
  backup.

**Rationale**: this is the only choice consistent with
`docs/architecture.md` (Runtime State: "Application containers are
stateless... must not rely on local persistent filesystem state for
correctness") and the container-per-process-role model already
defined for Sentinel (API, Celery worker, Git worker, Beat, IBS
consumer — 5 independently deployable/scalable roles per
`docs/architecture.md` Container Images / `docs/deployment.md` Process
Architecture). Rotation, aggregation, retention, and backup of log
*output* become the deployment platform's responsibility (Docker
`json-file`/`local` logging driver with rotation, journald, or a log
shipper such as Fluent Bit/Vector feeding an aggregator such as Loki
or an ELK stack in Kubernetes). This directly answers the "where are
logs saved / rotated / backed up" question raised in
`docs/drafts/ideas.md`: **nowhere, by the application; the platform
owns it.**

**Rejected alternative**: optional file output with in-app rotation —
rejected as unnecessary complexity that contradicts the stateless
container principle already established for the project. Simple
deployments (Docker Compose) still get rotation for free via the
Docker daemon's logging driver configuration; this does not require
any Sentinel-side code.

### D3 — Correlation IDs via `contextvars`

Every log entry MUST be correlatable to the unit of work that produced
it:

- **API requests**: `request_id` — adopts the incoming `X-Request-ID`
  header if present (per `docs/api-spec.md`, Request Tracing),
  otherwise generates a UUID. This is the existing promise in
  `api-spec.md` line ~305, currently unimplemented; this plan gives it
  a concrete mechanism (ASGI middleware + `contextvars`, described in
  the new spec).
- **Celery tasks**: `celery_task_id` — bound via Celery signals
  (`task_prerun`/`task_postrun`).
- **Fetcher runs**: `fetcher_run_id` — bound by `BaseFetcher.execute()`
  wrapper (see `docs/features/platform/fetcher-infrastructure.md`),
  linking log lines to the corresponding `FetcherRun` row without
  duplicating fetcher metrics into the log stream.
- All correlation IDs use Python's `contextvars` module (not
  thread-locals), because the API layer is async (FastAPI) and
  thread-locals do not propagate correctly across `await` boundaries.

**Rationale**: without this, "check the logs" in
`docs/deployment.md` (Troubleshooting) is not actionable at scale —
operators need to filter by request/task/run, not grep free text.

### D4 — `DEBUG` and `LOG_LEVEL` are fully orthogonal

Two independent configuration axes:

| Variable | Type | Meaning | Existing? |
|---|---|---|---|
| `DEBUG` | bool | **Application mode**: e.g., verbose error responses, auto-reload. Already exists in `backend/app/config.py` and `docs/configuration.md`. | Yes |
| `LOG_LEVEL` | enum (`DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL`) | **Log verbosity**. `DEBUG` here is a *value* of this variable, not a separate boolean. | No — new |

**Explicit rule**: `DEBUG` (the app-mode flag) MUST NOT influence the
default value of `LOG_LEVEL`, and vice versa. `LOG_LEVEL` always
defaults to `INFO` regardless of `DEBUG`.

**Rationale (rejected the "convenience coupling" alternative)**:
coupling them (e.g., "`DEBUG=true` defaults `LOG_LEVEL` to `DEBUG`")
was considered and explicitly rejected because:

1. It creates a security incentive to set `DEBUG=true` in production
   just to get verbose logs, which would also expose stack traces in
   HTTP error responses — a real regression from a security posture
   already established via `SecretStr` handling
   (`docs/conventions.md`, Secret Field Typing) and Guardrail #23 (no
   real personal data / PII discipline).
2. It violates the "explicit over implicit" principle stated at the
   top of `docs/conventions.md` — a hidden coupling between two
   same-named-but-different concepts ("debug mode" vs. "debug log
   level") is a textbook source of confusion.
3. The only benefit (convenience in local dev) is fully achievable
   without coupling, by shipping `backend/.env.example` with both
   `DEBUG=true` and `LOG_LEVEL=DEBUG` pre-set.

### D5 — Process role identification is delegated to the platform (no `LOG_PROCESS_ROLE`)

**Revised decision** (superseding an earlier draft of this plan that
proposed a `LOG_PROCESS_ROLE` application env var): the application
does **not** emit a process-role field in its own log records. Which of
the 5 runtime roles (per `docs/architecture.md`, Container Images —
`api`, `celery-worker`, `git-worker`, `beat`, `ibs-consumer`) produced a
given log line is identified exclusively via **platform-provided
metadata**: Kubernetes pod/container labels, or the Docker Compose
service name (`com.docker.compose.service` label already attached by
the Compose engine to container logs). Operators configure their log
collector (Fluent Bit/Vector/Promtail, etc.) to attach this metadata
when shipping logs to the aggregator.

**Rationale**: this is the only choice consistent with D2 — if the
application's job is to emit structured log lines to stdout and the
platform owns aggregation/rotation/persistence, then "which
container/process emitted this line" is itself platform metadata,
not application data. An app-level env var describing the process role
would be a second, independently-set source of truth that can silently
drift from the actual launch command (e.g., a container started as
`beat` but with a stale/wrong `LOG_PROCESS_ROLE` value would mislabel
every line it emits) and would need a value even in the default
`Dockerfile` `CMD` (currently unconditional), adding friction for no
correctness benefit.

**Why this is not actually a functional gap**: most cross-process
correlation is already solved by other fields, not by a role label:
- API log lines already carry `request_id`.
- Celery task log lines already carry `celery_task_id`.
- Fetcher log lines already carry `fetcher_run_id`.
- The one case with no other distinguishing field is telling
  `celery-worker` apart from `git-worker` — but these two roles always
  run in **separate containers** (per `docs/deployment.md`, Process
  Architecture: the git worker requires its own dedicated persistent
  volume and worker affinity), so the platform's own container/pod
  identity already disambiguates them without any help from the
  application.

**Trade-off accepted**: a raw, unenriched log line read directly from
a single container's stdout (e.g., via `docker logs <container>` with
no collector in front of it) will not self-report its role. This is
judged acceptable because the operator already knows which container
they attached to in that scenario — the ambiguity only matters once
logs from multiple roles are merged into one stream, at which point the
collector has already attached the metadata.

### D6 — Secrets/PII discipline

The new spec will state explicitly: log statements MUST NEVER include
values obtained via `SecretStr.get_secret_value()`, raw credential
material, or personal data as defined in `docs/conventions.md`
("Example Data in Documentation") and Guardrail #23. This is a
documentation rule for this phase (spec-first project) — the future
implementation task is expected to additionally consider a redaction
processor, but that is an implementation detail deferred out of this
plan.

---

## 4. Alternatives Considered and Rejected

Documented here for reviewer transparency — these were evaluated and
explicitly not chosen.

| Alternative | Why rejected |
|---|---|
| Plain text logs, no structure (minimal stdlib `basicConfig`) | Not machine-parseable; breaks the `X-Request-ID` correlation promise already made in `api-spec.md`; would likely need to be redone later, at higher cost, once real aggregation is needed. |
| OpenTelemetry (logs+traces+metrics unified) | Architecturally attractive long-term, but `docs/architecture.md` explicitly states the deployment target (Docker/Podman vs. Kubernetes) is not yet fixed. Adding an OTel collector dependency now is premature relative to the project's own stated deployment uncertainty. Revisit once the deployment target and a production instance exist (ties into the `1.0.0` graduation criteria in `docs/conventions.md`). |
| In-app file logging with rotation (e.g., `RotatingFileHandler`) | Directly contradicts `docs/architecture.md` Runtime State ("must not rely on local persistent filesystem state for correctness") and the stateless container principle. Also duplicates functionality the container runtime already provides for free. |
| Coupling `DEBUG` and `LOG_LEVEL` | See D4 above — security and clarity regression for a convenience gain that is achievable another way. |
| Logging to PostgreSQL (treating operational logs like audit events) | Would conflate two fundamentally different concerns (business audit trail vs. operational diagnostics), contradicting the clean separation the project has already established for audit trails. Operational log volume is also unsuited to a relational audit table (no query patterns, no retention policy, high write volume from DEBUG/INFO-level noise). |

---

## 5. Detailed Action Plan

Each step below states exactly what file is touched, what changes,
and what the acceptance criterion is. Steps 1–8 are documentation-only
(spec-first, no code). Steps 9 is limited configuration-surface
alignment for the small amount of existing code. Steps 10–11 are
review and cleanup.

### Step 1 — Create `docs/features/platform/logging.md`

New file. Required sections (each MUST answer the completeness
questions from `docs/conventions.md` where the section describes a
function; most of this spec is architectural/policy, not per-function,
given the precedents of other infra specs like `networking.md`):

1. **Purpose & Scope** — operational/diagnostic logging; explicit
   cross-reference distinguishing this from
   `audit-trail-infrastructure.md` (business audit events, persisted,
   authoritative) vs. this spec (operational logs, transient,
   diagnostic). State plainly: "This specification does not apply to
   audit trail events, which are defined and persisted per
   `audit-trail-infrastructure.md`."
2. **Principles** — structlog on top of stdlib `logging`; JSON or
   console rendering via `LOG_FORMAT`; stdout/stderr only, never
   files; 12-factor app alignment (explicit citation of
   `docs/architecture.md`, Runtime State).
3. **Log Levels** — definition of each level (`DEBUG/INFO/WARNING/
   ERROR/CRITICAL`) with concrete usage guidance (what belongs at each
   level, e.g., "INFO: normal lifecycle events such as fetcher run
   start/end, scheduled task execution. WARNING: recoverable
   anomalies... ERROR: failures requiring operator attention...").
   `LOG_LEVEL` env var, default `INFO`. Explicitly restate D4 (no
   coupling with `DEBUG`).
4. **Standard Log Record Schema** — table of fields every log record
   MUST include: `timestamp` (UTC, `Z` suffix — reusing the existing
   "UTC everywhere" convention from `docs/conventions.md`), `level`,
   `logger` (module path), `event` (the message), `app` (from
   `APP_NAME` — distinguishes Sentinel's own logs from other
   applications once aggregated with unrelated services; this is
   different from "process role" and is retained), and the
   correlation fields below when present. Note explicitly that
   `exception` (traceback) is included only for ERROR/CRITICAL records
   raised from an exception context. Add an explicit note per D5: the
   record does **not** include a process-role field — role
   identification is the log collector's responsibility via
   platform-provided container/pod metadata, not an application-level
   concern.
5. **Correlation IDs** — full description of D3: `request_id` (adopt
   `X-Request-ID` or generate), `celery_task_id`, `fetcher_run_id`,
   propagation mechanism (`contextvars`, async-safe), and an explicit
   cross-reference update target: `docs/api-spec.md` Request Tracing
   section now points here for the "how" behind the existing "what"
   promise.
6. **Integration with Third-Party Loggers** — uvicorn, Celery
   (`worker_hijack_root_logger=False` requirement so Celery's own
   logging setup doesn't fight structlog's), SQLAlchemy engine
   logging, httpx. State that Alembic keeps its own independent
   logging configuration (`alembic.ini`, `fileConfig`) because it is a
   one-shot migration tool outside the application runtime, not part
   of this spec's scope.
7. **Secrets and PII Discipline** — restate D6, cross-reference
   Guardrail #23 and `docs/conventions.md` Secret Field Typing.
8. **Configuration** — table of new env vars (see Step 2/9 for exact
   values) with types, defaults, and description, matching the format
   used in `docs/configuration.md`.
9. **Consumption Model** — how operators read logs in each deployment
   mode: `docker compose logs <service>` / `kubectl logs <pod>` /
   journald query, filtering by `request_id`/`fetcher_run_id`.
   Filtering by process role/container is done via platform-level
   metadata (Kubernetes pod/container labels, Docker Compose service
   name) at the collector/orchestrator level, per D5 — not via any
   field emitted by the application. Explicit statement: rotation,
   long-term retention, and backup of the log stream are the
   platform's responsibility, not the application's — cross-reference
   `docs/deployment.md` for the per-orchestrator guidance added in
   Step 5.
10. **Testing** — how logging configuration and correlation-ID
    propagation are expected to be tested once implemented (e.g.,
    `structlog.testing.capture_logs`, `caplog` for stdlib-level
    assertions, a dedicated test verifying the ASGI middleware adopts
    or generates `X-Request-ID` correctly). This is guidance for the
    future implementation task, written now while the design is fresh.
11. **Cross-references** — `docs/api-spec.md` (Request Tracing),
    `docs/architecture.md` (Runtime State / Observability),
    `docs/conventions.md` (Timestamps & Timezones, Secret Field
    Typing), `docs/deployment.md` (Log Aggregation),
    `docs/features/platform/audit-trail-infrastructure.md` (contrast),
    `docs/features/platform/fetcher-infrastructure.md` (fetcher_run_id
    binding).

**New configuration variables to define in this spec** (authoritative
definitions — semantics, type, default, bounds — per the "Configuration
Management" convention in `docs/conventions.md`, which requires the
spec to be the source of truth before `docs/configuration.md` mirrors
it):

| Env Var | Type | Default | Notes |
|---|---|---|---|
| `LOG_LEVEL` | enum | `INFO` | `DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL`. Independent of `DEBUG` (D4). |
| `LOG_FORMAT` | enum | `json` | `json`\|`console`. |

No process-role variable is defined — see D5: process/container
identification is delegated to platform-provided metadata, not to an
application env var.

**Acceptance criterion**: the file exists, follows the structure above,
answers Q1/Q3/Q6 completeness questions from `docs/conventions.md`
wherever a concrete behavior is described (e.g., middleware behavior
for request ID adoption), and every claim it makes about another spec
(fetcher_run_id, audit trail contrast, UTC timestamps) is verified
against that spec's actual current content, not assumed.

### Step 2 — Update `docs/configuration.md`

- Add a new subsection (placed near the existing `## Application`
  table, which currently has `APP_NAME`, `DEBUG`, `CORS_ORIGINS`) named
  `## Logging`, with a table containing the two variables from Step
  1 (`LOG_LEVEL`, `LOG_FORMAT`), each with a `Defined in` link to
  `docs/features/platform/logging.md`.
- Edit the existing `APP_NAME` row description ("used in logs, health
  endpoint") to remain accurate — no change needed, already correct,
  but verify no contradiction is introduced.
- Add one sentence directly under the new table making D4 explicit for
  readers who only skim `configuration.md`: "`LOG_LEVEL` is independent
  of `DEBUG` — see `docs/features/platform/logging.md` for rationale."

**Acceptance criterion**: `docs/configuration.md` mirrors
`logging.md` exactly (per the "Configuration Management" convention's
invariant that all artifacts MUST agree); no duplicate/contradictory
defaults.

### Step 3 — Update `docs/conventions.md`

- Add a new subsection under `## Python (Backend)`, after "Redis Error
  Handling" and before "Runtime Version" (placement chosen to keep it
  near other cross-cutting backend runtime conventions), titled
  `### Logging`.
- Content: how to obtain a logger/structlog binder in application code
  (brief pattern example), the rule that log statements must never
  include secret values (cross-reference `SecretStr` / Secret Field
  Typing section already present in this same file, and Guardrail
  #23), and a one-line pointer to
  `docs/features/platform/logging.md` as the authoritative spec for
  format/levels/correlation.
- **Important boundary**: this subsection must stay short — a pointer
  and a couple of hard rules, not a duplicate of `logging.md`. Applying
  the "Information placement" self-check (Guardrail #21): the
  authoritative content lives in `logging.md` (a feature spec is the
  correct owner for a concrete infrastructure component); a coding
  convention here about *how developers write log statements* is
  legitimately a `conventions.md`-level concern (style/pattern), same
  treatment as how "Redis Error Handling" is documented there while
  `fetcher-infrastructure.md`/others own the broader behavior.

**Acceptance criterion**: no duplication of level/format/correlation
policy between `conventions.md` and `logging.md`; `conventions.md`
only states the developer-facing coding pattern and the secrets rule.

### Step 4 — Update `docs/architecture.md`

- Add a new subsection under `## Security Considerations` — or as a
  new top-level subsection titled `## Observability`, placed after
  `## Environments` and before `## Security Considerations` (matches
  the document's existing flow from infrastructure topics toward
  security). Decision: use a new `## Observability` top-level section,
  since logging is not a security control but a cross-cutting runtime
  concern comparable in weight to `## Deployment Portability`.
- Content: 2-3 short paragraphs summarizing the logging model (stdout,
  structured, correlation IDs, platform-owned persistence) with a
  pointer to `docs/features/platform/logging.md` for full detail. Do
  NOT restate the env var table or field schema here — this is an
  architecture-level summary only.

**Acceptance criterion**: `architecture.md` gives a reader unfamiliar
with the codebase a correct one-paragraph mental model of "how logging
works here" without needing to open `logging.md`, while not duplicating
its authoritative content.

### Step 5 — Update `docs/deployment.md`

- Expand the current unlinked checklist item "Log aggregation
  configured" (Pre-Production Checklist) into an actionable, linked
  item: "Log aggregation configured (see Log Aggregation, below)".
- Add a new section `## Log Aggregation`, placed after `## Health
  Checks` and before `## Troubleshooting` (logical flow: health checks
  → logging → troubleshooting, since troubleshooting steps already
  reference "check the logs").
- Content: for each of the 3 deployment contexts already established
  in this document (local Docker/Podman, Kubernetes, current
  staging/production model) — how logs surface: Docker/Podman via the
  container logging driver (mention `json-file` with `max-size`/
  `max-file` for local rotation, as a *platform* configuration, not
  Sentinel's), Kubernetes via `kubectl logs` and the expectation that
  a cluster-level log shipper (Fluent Bit/Vector → Loki/ELK) is the
  operator's responsibility, not Sentinel's. Explicitly state (per D5)
  that process-role/container identification for filtering and
  correlation comes from platform-provided metadata — Kubernetes
  pod/container labels, or the Docker Compose service name
  (`com.docker.compose.service`) — and that configuring the collector
  to attach/propagate this metadata is the operator's responsibility;
  the application does not emit a role field of its own. Cross-reference
  `docs/features/platform/logging.md` for format/fields.
- Update the existing "Check logs for..." bullets in `##
  Troubleshooting` (SSO Login Fails, Celery Tasks Not Running) to
  mention that these messages appear as structured `event` fields once
  the logging spec is implemented, with a one-line pointer — do not
  rewrite the bullets themselves, they remain accurate as
  human-readable descriptions of what to search for.

**Acceptance criterion**: the "Log aggregation configured" checkbox is
no longer a dangling reference; `deployment.md` clearly assigns
rotation/retention/backup ownership to the platform, consistent with
D2.

### Step 6 — Update `docs/api-spec.md`

- In the `### Request Tracing` section (around line 299-307), after
  the existing sentence "The request ID is propagated to all log
  entries produced during request processing, enabling end-to-end
  debugging.", add a cross-reference sentence: "See
  `docs/features/platform/logging.md` for the correlation ID mechanism
  and log record schema." Do not otherwise alter this section's
  existing content — the promise itself is unchanged, only its
  supporting spec reference is added.

**Acceptance criterion**: the existing promise in `api-spec.md` now has
a concrete implementing spec to point to; no semantic change to the API
contract itself (no new headers, no behavior change).

### Step 7 — Update `docs/features/platform/README.md`

- Add `logging.md` to the file listing block, in a position consistent
  with the existing ordering (the current list groups fetcher specs
  first, then cross-cutting infra specs — `networking.md`,
  `audit-trail-infrastructure.md`, `system-settings.md`,
  `health-endpoints.md`). Insert `logging.md` immediately after
  `health-endpoints.md` and before `cve-record-parser.md`, grouped with
  the other cross-cutting infra specs (networking, audit-trail,
  system-settings, health-endpoints) rather than the fetcher-specific
  or CVE-specific specs.
- Add one bullet to the `## Relationships` section: "`logging.md`
  defines the operational logging model (structured logs, correlation
  IDs) consumed implicitly by every other spec that prescribes log
  statements; it is distinct from `audit-trail-infrastructure.md`,
  which governs persisted business audit events."

**Acceptance criterion**: `logging.md` is discoverable from the
platform domain README, with its relationship to
`audit-trail-infrastructure.md` stated explicitly to prevent future
confusion.

### Step 8 — Update `docs/drafts/ideas.md`

- Remove line 13 ("Investigate how application logs are handled...")
  using the same convention already used in this file for resolved
  ideas elsewhere (strikethrough + arrow + pointer to the resulting
  spec), matching the pattern of lines 11 and 12 in the current file.
  Replace with:
  `- ~~Investigate how application logs are handled. Where are they saved? When are they rotated? Backup?~~ → Promoted to spec: `docs/features/platform/logging.md``

**Acceptance criterion**: the idea is marked resolved with a working
link, consistent with the file's existing convention (verify against
lines 11-12 of the current file before editing, do not invent a new
convention).

### Step 9 — Align existing backend configuration surface

**Scope reminder**: no new logging implementation code is written
here. This step only touches the two files that already exist and
already define environment variable surface/defaults, so they do not
silently drift from the newly authoritative spec.

- `backend/.env.example`: add two lines — `LOG_LEVEL=DEBUG`,
  `LOG_FORMAT=console`. No process-role variable is added (per the
  revised D5 — process/container identification is delegated to
  platform metadata, not to an application env var). This matches the
  `.env.example` inclusion criteria in `docs/conventions.md`
  (Configuration Management): these are variables a developer will
  likely want to see locally.
- `backend/app/config.py`: **evaluate, do not blindly edit.** The
  `Settings` class currently has `app_name` and `debug` fields but no
  `log_level`/`log_format` fields. Per the "Configuration Management"
  convention, `config.py` fields are only added "when the feature is
  implemented" — and the actual logging setup code (structlog
  configuration, using these settings) is explicitly out of scope for
  this plan (Section 2.2). Therefore: **do NOT add these fields to the
  `Settings` class in this pass.** Adding fields with no consuming
  code would violate the same convention's invariant in the opposite
  direction (a field/setting that exists in code but is not wired to
  any actual behavior). Document this decision inline as a comment is
  NOT necessary — simply leave `config.py` unchanged and note in the
  completion summary that this was a deliberate no-op, to be picked up
  by the future implementation task together with the `structlog`
  dependency and logging setup module.
- Double check `backend/app/main.py`, `backend/app/database.py`,
  `backend/app/models/__init__.py`, `backend/app/services/__init__.py`,
  `backend/app/tasks/__init__.py`, `backend/app/api/v1/__init__.py`
  for any other pre-existing logging-related code (there is currently
  none beyond `config.py`'s single logger instance, confirmed during
  the investigation preceding this draft — re-verify at execution time
  in case the codebase changed since this draft was written) — if any
  is found, evaluate case-by-case whether it contradicts the new spec
  (e.g., a stray `basicConfig()` call would need to be flagged, not
  silently left).

**Acceptance criterion**: `.env.example` gives a new developer a
working local logging configuration out of the box, consistent with
D4; `config.py` is verified to have no contradicting logging setup, and
is NOT prematurely extended with unwired settings fields.

### Step 10 — Register the new spec for review tracking

- `docs/reviews/.tracking.json`: add an entry under `"specs"`:
  ```json
  "logging": {
    "enabled": true,
    "abbr": "LOG",
    "cache": null
  }
  ```
  (Abbreviation `LOG` verified free of collision against all existing
  `abbr` values in the file at draft-writing time — re-verify at
  execution time in case new specs were added meanwhile.)
- `docs/reviews/README.md`: add a new row to the main table (enabled
  specs), in correct alphabetical position (between
  `local-authentication` and `networking`), following the two-row
  format defined in `.opencode/commands/review-spec/readme-layout.md`:
  main row with `—` in all five reviewer columns (GAP/COH/DES/SEC/API,
  since none have run yet), `0/0` in the Open column, empty Last
  Review and Stale cells; empty sub-row underneath (per the layout
  spec: sub-row is empty when main row is `—`).
- Recompute the `**Total**` row's `sum_open/sum_total` fraction —
  adding a spec with `0/0` findings does not change the numeric total,
  but re-verify the total is still consistent after the insertion.

**Acceptance criterion**: `logging` appears in the tracking JSON and
the README index, enabled, in the correct alphabetical slot, using
exactly the schema and layout rules defined in
`.opencode/commands/review-spec/tracking-format.md` and
`readme-layout.md` (do not improvise a different format).

### Step 11 — Run the relevant spec reviewers

Run reviewers against the new spec and the specs it modifies, to catch
problems introduced by this change or pre-existing problems newly
surfaced by it:

1. `@spec-gap-analyzer` on `logging.md` (new spec — mandatory per
   Guardrail #17).
2. `@spec-coherence-reviewer` on `logging.md` (new spec — mandatory per
   Guardrail #15), checking specifically for contradictions with
   `audit-trail-infrastructure.md` (business vs. operational logs
   distinction) and `fetcher-infrastructure.md` (fetcher_run_id
   binding claims).
3. `@docs-placement-reviewer`, triggered because this change adds a new
   rule/pattern (Guardrail #21) and touches multiple specs in the same
   session (`configuration.md`, `conventions.md`, `architecture.md`,
   `deployment.md`, `api-spec.md`) — verify the placement decisions
   made in Steps 2-6 (especially the `conventions.md` vs. `logging.md`
   split in Step 3, and the `architecture.md` summary vs. `logging.md`
   detail split in Step 4) are correct and not either fragmented or
   over-centralized.
4. `@docs-reviewer`, triggered because this is a new feature spec plus
   multiple documentation files modified in the same change (Guardrail
   #9).
5. If any reviewer reports "Needs revision" findings (or High-severity
   gaps from the gap analyzer), fix them in the spec files before
   proceeding to Step 12. Minor/Low findings should also be fixed in
   this same pass per the applicable guardrails, unless explicitly
   deferred with a documented reason (consistent with how other specs'
   reviews handle Low-severity findings, see `docs/reviews/README.md`
   precedent of some specs retaining open Low findings).
6. Update `docs/reviews/.tracking.json` and `docs/reviews/README.md`
   for `logging` with the actual results of steps 1-2 above (replacing
   the placeholder `cache: null` / `—` row from Step 10 with real
   findings counts and `last_review` timestamp), following
   `.opencode/commands/review-spec/tracking-format.md` and
   `review-file-format.md` for how the review findings file itself
   (`docs/reviews/logging.md`) must be structured. Note: this creates a
   review findings file at `docs/reviews/logging.md`, distinct from the
   feature spec at `docs/features/platform/logging.md` — same filename,
   different directory, consistent with how every other reviewed spec
   is tracked.

**Acceptance criterion**: all four reviewers have run at least once
against the new spec; any Needs-revision/High findings are resolved;
the tracking system accurately reflects the real review outcome, not
the Step 10 placeholder.

### Step 12 — Delete this draft

- Delete `docs/drafts/logging-infrastructure-plan.md`.
- Do NOT delete `docs/drafts/ideas.md` or `docs/drafts/open-points.md`
  — only this specific draft file is temporary.

**Acceptance criterion**: the draft file no longer exists in the
repository; all its content has been durably captured in the actual
spec files touched by Steps 1-9, such that no information is lost by
deleting it.

---

## 6. Execution Order Summary

```
1. Write docs/features/platform/logging.md
2. Update docs/configuration.md
3. Update docs/conventions.md
4. Update docs/architecture.md
5. Update docs/deployment.md
6. Update docs/api-spec.md
7. Update docs/features/platform/README.md
8. Update docs/drafts/ideas.md
9. Align backend/.env.example (config.py: verified no-op, documented)
10. Register "logging" in docs/reviews/.tracking.json + README.md
11. Run @spec-gap-analyzer, @spec-coherence-reviewer,
    @docs-placement-reviewer, @docs-reviewer → fix findings → update
    tracking with real results
12. Delete this draft file
```

Steps 1-8 should be executed in this order because later steps
cross-reference the new spec created in Step 1. Step 9 depends on
Step 1 (needs the final env var names/defaults). Step 10 can run
anytime after Step 1 (needs the spec to exist) but is placed late so
the "enabled: true, cache: null" placeholder has a short lifetime
before Step 11 fills in real data. Step 11 must be last before cleanup
since it may require revising any of Steps 1-8. Step 12 is strictly
last.

---

## 7. Risks and Open Questions for the Reviewer

Being transparent about residual uncertainty rather than hiding it:

1. **Collector configuration for platform-provided role metadata**
   (D5, Step 5) is a deployment/operational responsibility, not
   something this plan can fully pin down at spec-writing time — the
   exact collector (Fluent Bit, Vector, Promtail, none at all for
   simple Compose setups) is an operator choice. This plan documents
   the *contract* (role identification is platform metadata, not an
   app field) but intentionally does not prescribe a specific
   collector product, consistent with `docs/architecture.md`'s own
   statement that the deployment target is not yet fixed.
2. **`architecture.md` section placement** (Step 4: new top-level
   `## Observability` section) is a judgment call on document
   structure. An alternative would be a subsection under `## Security
   Considerations`. This plan chose the top-level option because
   logging is broader than security, but this is arguable.
3. **No file/rotation fallback for simple non-containerized
   deployments**: D2 assumes a container runtime is always present.
   Sentinel's `docs/architecture.md` already assumes Docker/Podman/K8s
   universally, so this is consistent, but is called out here in case
   a bare-metal deployment scenario exists that this plan is unaware
   of.
4. **This plan does not propose sampling, rate-limiting, or
   log-volume budgets.** For a system with multiple fetchers running
   frequently, INFO-level logging could be voluminous. This is
   explicitly left for the future implementation task to address
   empirically (e.g., via per-logger level overrides), not decided
   here, to avoid speculative design.

---

## 8. Internal Consistency Check (self-review performed before handoff)

- Section 2.1/2.2 scope matches the 12-step plan in Section 5 (no step
  outside declared scope; Step 9 explicitly does not add unwired
  `config.py` fields, consistent with 2.2's "no new logging
  implementation code" boundary).
- Every design decision in Section 3 (D1-D6) is referenced by at least
  one concrete step in Section 5 (D1→Step1/9-deferred, D2→Step1/4/5,
  D3→Step1/6, D4→Step1/2/9, D5→Step1(schema)/Step5(deployment),
  D6→Step1/3).
- Section 4's rejected alternatives are consistent with Section 3's
  decisions (no contradiction — e.g., OTel rejection in Section 4
  matches the absence of tracing/metrics scope in Section 2.2).
- Abbreviation `LOG` for the tracking system (Step 10) was verified
  against the actual current `.tracking.json` contents (48 existing
  abbreviations checked, no collision) rather than assumed.
- Step 8's edit format was checked against the actual current content
  of `docs/drafts/ideas.md` (lines 11-12 use the strikethrough+arrow
  convention) rather than invented.
- The claim in Step 1 that `docs/api-spec.md` Request Tracing exists
  and says what this plan says it says was verified against the actual
  file content (line ~299-307) during the preceding investigation.
- The claim that no logging setup code exists in the backend beyond
  `config.py`'s single logger (Section 1.1, Step 9) was verified by
  reading every file under `backend/app/` during the preceding
  investigation, not assumed.
- Cross-reference targets used throughout (e.g.,
  `docs/conventions.md` Secret Field Typing, Timestamps & Timezones;
  `docs/architecture.md` Runtime State, Container Images;
  `docs/deployment.md` Process Architecture, Health Checks,
  Troubleshooting) were verified to exist under those exact names in
  the current documents.
- No step in this plan requires implemented code, a database, or a
  migration — consistent with the project's current spec-only state,
  as required by the stakeholder.
