# Fetcher Infrastructure

## Purpose

Define the mandatory infrastructure that all data fetchers in Sentinel must
use. Fetchers are background tasks that periodically pull data from
external sources (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS) and update
the local database. This specification covers the `BaseFetcher` abstract
base class, the fetcher registry, Celery integration, concurrency
control, data model, and data retention.

For the monitoring dashboard (API endpoints, frontend pages, CLI
commands) that consumes this infrastructure, see
`docs/features/platform/fetcher-operations.md`.

## Terminology

| Term | Definition |
|---|---|
| **Fetcher** | A background task that retrieves data from an external source and creates/updates local records. Implemented as a subclass of `BaseFetcher`. |
| **Run** | A single execution of a fetcher, tracked from start to finish with metrics (duration, item counts, status). |
| **Registry** | An in-memory dictionary of all registered fetcher classes, populated automatically via `BaseFetcher` auto-discovery. |

## BaseFetcher Base Class

All fetchers MUST inherit from `BaseFetcher`, an abstract base class in
`backend/app/services/base_fetcher.py`. The base class provides:

1. **Auto-registration**: a metaclass or `__init_subclass__` hook that
   automatically registers each concrete fetcher in a global registry
   keyed by the fetcher's `name` property
2. **Run lifecycle management**: a `run()` method (not meant to be
   overridden) that wraps the fetcher's `execute()` method with:
   - Creation of a `FetcherRun` record with status `running`
   - Reset of all metric counters (`items_created`, `items_updated`,
     `items_failed`) to zero before each execution. This ensures correct
     behavior regardless of instance lifecycle (singleton vs. per-run
     instantiation)
   - Automatic `started_at` timestamp capture
   - Automatic `finished_at` timestamp and `duration_seconds` calculation
   - Exception handling: if `execute()` raises, the run is marked `failure`
     with `error_message`, `error_detail`, and `error_traceback` populated
     (see "Error Message Sanitization" for the three-tier field
     architecture)
    - Final status set to `success` or `partial` (if `items_failed > 0`)
   - **Status determination precedence**: if `execute()` raises an exception,
     the run status is always `failure` regardless of metric counters
     (`items_failed`, `items_created`, `items_updated` are preserved in the
     record for diagnostic purposes but do not influence the final status).
     The `partial` status is assigned only when `execute()` returns normally
     and `items_failed > 0`
3. **Metric helpers**: methods that concrete fetchers call within their
   `execute()` to report work done:
   - `self.record_created(count=1)` — increment `items_created`
   - `self.record_updated(count=1)` — increment `items_updated`
   - `self.record_failed(count=1)` — increment `items_failed`
4. **Enabled check**: before executing, `run()` checks `FetcherConfig` for
   the fetcher. If `enabled` is `false`, the run is skipped (no
   `FetcherRun` record is created, the task returns immediately). A
   DEBUG-level log is emitted before returning:
   `logger.debug("Fetcher '%s' is disabled — skipping scheduled run", self.name)`

**FetcherRun creation failure**: if the database INSERT for the `FetcherRun`
record fails (e.g., database connection error), the task MUST:

1. Log a CRITICAL-level message:
   `CRITICAL: Fetcher '{name}' aborted — failed to create FetcherRun record before execution: {error}`
2. Re-raise the exception immediately — Celery does NOT retry top-level
   fetcher tasks

No `FetcherRun` record is produced (since the database is unreachable).
Visibility of this failure is provided by: application logs (CRITICAL level)
and the Celery result backend (task marked as FAILED). Recovery happens at
the next scheduled cycle — no explicit Celery retry is configured for
top-level fetcher tasks.

## Abstract Interface

Concrete fetchers MUST implement:

```python
class MyConcreteFetcher(BaseFetcher):
    name: str = "my_fetcher"             # unique identifier, snake_case
    description: str = "Human-readable description"
    default_schedule: str = "0 */6 * * *"  # cron expression (every 6h)

    # Optional: URL pattern for human-readable CVE page.
    # Only applicable to CVE fetchers. When set, a TicketReference
    # is automatically created with this URL for each processed CVE.
    # Uses {cve_id} as placeholder (e.g., "CVE-2026-3317").
    # See docs/features/tickets/ticket-references.md for details.
    source_reference_url_pattern: str | None = None

    # Optional: per-fetcher operational parameters configurable at
    # runtime via the admin dashboard. See "Custom Settings Schema"
    # section below for the schema format and validation rules.
    class Settings(BaseModel):  # optional inner class
        ...

    async def execute(self, session: AsyncSession) -> None:
        """Fetch data from the external source.

        Use self.record_created(), self.record_updated(), and
        self.record_failed() to report metrics.
        """
        ...
```

## On-demand Single-Item Fetch

CVE fetchers MUST additionally implement the `fetch_single` method:

```python
async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
    """Fetch a single CVE from the external source.

    Called on-demand when Sentinel encounters an unknown CVE-ID during
    ticket creation or CVE association. Writes data to the standard
    models (CVE, CVESource, CVECVSSAssessment, CVEExternalIdentifier,
    TicketReference) via cve_service.upsert_cve().

    Raises CVENotInSource if the external source explicitly confirms
    the CVE does not exist (e.g., HTTP 404, empty response).
    """
    ...
```

This method is **optional** for non-CVE fetchers and **required** for
CVE fetchers. The system discovers all fetchers that implement
`fetch_single` via the registry and invokes them in parallel when an
on-demand fetch is needed (see `docs/features/tickets/cve-tracking.md`,
"On-demand Single-CVE Fetch").

The `fetch_single` method does NOT create a `FetcherRun` record. It is
a sub-operation invoked as a standalone Celery task, not a full fetcher
execution. Metric reporting (`record_created`, etc.) is not used.

### `CVENotInSource` Signal

`CVENotInSource` is a dedicated signal class (not an error) provided by
the fetcher infrastructure module. It indicates that the external source
explicitly confirmed the CVE does not exist (e.g., HTTP 404, empty
response). The orchestrator (`fetch_single_cve` task wrapper in
`cve-tracking.md`) catches this specific exception and records
`status=missing` via `record_source_status()`.

`CVENotInSource` does NOT inherit from `FetcherError` — it is not a
failure condition. It is a distinct outcome that maps to the `missing`
status in `CVESourceFetchStatus`.

### `fetch_single` Signaling Convention

This convention applies to ALL CVE fetchers implementing `fetch_single`:

| Behavior | Meaning | Orchestrator action |
|----------|---------|---------------------|
| Returns normally | Data written via `upsert_cve()` | `status = success` (already written by `upsert_cve` via `record_source_status`) |
| Raises `CVENotInSource` | CVE not present in source | `record_source_status(cve_id, source, "missing")` |
| Raises other exception | Transient error | Celery retries → then `record_source_status(cve_id, source, "failure")` |

Fetchers MUST NOT catch transient exceptions internally — they must
propagate to allow Celery retry to function. Fetchers MUST raise
`CVENotInSource` (not return a sentinel value) when the source explicitly
indicates the CVE does not exist.

### Retry Policy for `fetch_single`

The Celery task wrapping `fetch_single` (`fetch_single_cve`) uses native
Celery retry:

- **Max retries**: 3
- **Backoff**: 5s → 10s → 20s (exponential with cap)
- **Retryable conditions**: network errors, HTTP 5xx, timeout, HTTP 429
- **Non-retryable conditions**: `CVENotInSource` (→ `missing`), HTTP 403
  (→ `failure` immediately), other 4xx (→ `failure`), parsing errors on
  HTTP 200 (→ `failure`)

After retries are exhausted, the task writes
`record_source_status(cve_id, source, "failure")`.

### Error Categorization

| Condition | Retry? | Final status |
|-----------|--------|-------------|
| Network unreachable, DNS failure, connection refused | Yes (3x) | `failure` |
| HTTP 5xx (server error) | Yes (3x) | `failure` |
| Request timeout | Yes (3x) | `failure` |
| HTTP 429 (rate limit) | Yes (3x) | `failure` |
| HTTP 404, empty response | No | `missing` |
| HTTP 403 (forbidden) | No | `failure` |
| HTTP 400, 401, 405, other 4xx (not 404/403/429) | No | `failure` |
| HTTP 200 with valid data | — | `success` |
| HTTP 200 with unparseable data (schema mismatch, missing fields) | No | `failure` |

**Catch-all rule**: any HTTP status code or error condition not explicitly
listed above is treated as non-retryable → immediate `failure`. This
prevents wasting retry attempts on permanent errors.

**Parsing errors**: an HTTP 200 response that cannot be parsed (unexpected
schema, missing required fields, malformed JSON) is a non-retryable
condition. Retrying would hit the same response from the same source. The
fetcher MUST NOT catch parsing exceptions internally — it should let them
propagate, but the orchestrator should classify them as non-retryable
(immediate `failure`) rather than feeding them into the Celery retry loop.

**HTTP 429 and `Retry-After`**: this design deliberately ignores the
`Retry-After` header for simplicity. The fixed backoff (5s → 10s → 20s,
total 35s) naturally clears most rate-limit windows (NVD: 30s window). For
longer rate-limit windows, all 3 retries may fail — this is an acceptable
trade-off for v1.

### Isolation Guarantee

When multiple fetchers are invoked in parallel for the same CVE-ID:

- Each fetcher runs as an independent Celery task
- Failure of one fetcher does NOT cancel, block, or affect other fetchers
- Each fetcher writes its own `CVESource` record independently
- The CVE record may end up with partial data (some sources succeeded,
  others failed)

## Error Message Sanitization

The `error_message` field in `FetcherRun` is visible to **all users**
(including unauthenticated callers via the fetcher dashboard). Raw Python
exception messages often contain infrastructure details — internal
hostnames, IP addresses, file paths, connection strings, or service
names — that MUST NOT be exposed publicly.

Sentinel uses a **three-tier error field architecture**:

| Field | Audience | Content |
|-------|----------|---------|
| `error_message` | All users (public) | Intentional, sanitized message written by the developer or by BaseFetcher's generic fallback |
| `error_detail` | `manage_fetchers` only | Raw exception message (`str(exception)`) |
| `error_traceback` | `manage_fetchers` only | Full Python traceback |

### Fetcher responsibilities

Each concrete fetcher MUST catch known exceptions in its `execute()`
method and raise a `FetcherError` with a sanitized message that describes
the failure without revealing infrastructure details:

```python
async def execute(self, session: AsyncSession) -> None:
    try:
        response = await self.http_client.get(IBS_API_URL)
    except ConnectionError as e:
        raise FetcherError("Failed to connect to IBS") from e
    except HTTPStatusError as e:
        raise FetcherError(f"IBS returned HTTP {e.response.status_code}") from e
```

`FetcherError` is a dedicated exception class provided by the fetcher
infrastructure module. When `BaseFetcher.run()` catches a `FetcherError`,
it stores the exception message in `error_message` (public) and
`str(exception.__cause__)` in `error_detail` (admin-only). If
`__cause__` is `None` (no chained exception), `error_detail` is set to
`NULL`.

### BaseFetcher fallback

When `execute()` raises an exception that is NOT a `FetcherError` (i.e.,
an unhandled exception), `BaseFetcher.run()` applies a **generic category
fallback** — it maps the exception type to a safe, generic message:

| Exception category | `error_message` |
|--------------------|-----------------|
| `ConnectionError`, `Timeout` | `"External service unreachable"` |
| `HTTPStatusError` (4xx) | `"External service rejected request"` |
| `HTTPStatusError` (5xx) | `"External service returned server error"` |
| `SoftTimeLimitExceeded` | `"Execution timed out"` |
| Any other exception | `"Unexpected error"` |

In all cases, `error_detail` receives `str(exception)` and
`error_traceback` receives the full traceback.

### What constitutes infrastructure details

Error messages MUST NOT contain:

- Internal hostnames (e.g., `build.suse.de`, `pan.suse.de`,
  `smelt.suse.de`)
- IP addresses or port numbers
- File system paths
- Database or Redis connection strings
- API keys, tokens, or credentials
- Internal URL paths beyond the service name

### Referencing error handling in fetcher specifications

Feature specifications that define fetchers MUST include an "Error
Handling" section documenting which exceptions the fetcher catches and
what sanitized messages it produces. The `@fetcher-compliance-reviewer`
agent verifies this documentation exists.

Fetchers that only interact with the local database (e.g.,
`aggregate_fetcher_runs`, `check_lifecycle_phase_transitions`) are exempt
from this requirement — their failure modes do not involve external
service details.

Error handling is one of the mandatory sections in the minimum
documentation template — see "Fetcher Documentation Requirements" below
for the full template.

## Custom Settings Schema

Fetcher-specific operational parameters (throttle delays, retry counts,
lookback windows, retention periods) can be declared by each fetcher and
managed at runtime through the admin dashboard without worker restart.

This mechanism complements the generic `FetcherConfig` fields (`enabled`,
`schedule_override`, `timeout_seconds`, `rate_limit`) which apply
uniformly to all fetchers. Custom settings are fetcher-specific — each
fetcher declares its own schema and reads its own values.

### What belongs in custom_settings

Operational parameters that:

- Are safe to change at runtime without security implications
- May need tuning based on workload or external service behavior
- Benefit from visibility and changeability through the admin dashboard

### What stays as environment variables

| Category | Examples | Reason |
|----------|----------|--------|
| Credentials | `IBS_USERNAME`, `IBS_PASSWORD`, `NVD_API_KEY` | Secrets — managed via env vars / Kubernetes Secrets |
| Connection URIs | `LDAP_URI`, `IBS_API_URL` | Infrastructure — changes with deployment environment |
| TLS configuration | `LDAP_CA_CERT_PATH` | Infrastructure — tied to certificate management |

### Schema declaration

Each `BaseFetcher` subclass MAY declare an inner class named `Settings`
that inherits from `pydantic.BaseModel`. If not declared (or set to
`None`), the fetcher accepts no custom settings and the
`custom_settings` JSONB column in `FetcherConfig` remains `{}`.

```python
from pydantic import BaseModel, Field


class SyncCvssRedhat(BaseFetcher):
    name = "sync_cvss_redhat"
    description = "Re-fetches Red Hat CVSS for active tickets"
    default_schedule = "0 3 * * *"

    class Settings(BaseModel):
        throttle_delay_seconds: float = Field(
            default=2.0,
            ge=0.1,
            le=30.0,
            description="Delay between consecutive Red Hat API requests.",
        )
```

### Supported field types and constraints

Settings fields are limited to scalar types. Pydantic `Field()`
constraints express all validation rules declaratively:

| Type | Supported constraints |
|------|----------------------|
| `int` | `ge`, `le`, `gt`, `lt` |
| `float` | `ge`, `le`, `gt`, `lt` |
| `str` | `max_length`, `pattern`, `json_schema_extra={"choices": [...]}` |
| `bool` | (no additional constraints) |

Nested objects, lists, and complex structures are not supported — only
scalar fields are allowed in the `Settings` model. This is enforced at
import time (see below).

To declare allowed choices for string or integer fields, use
`json_schema_extra`:

```python
class Settings(BaseModel):
    output_format: str = Field(
        default="json",
        json_schema_extra={"choices": ["json", "xml", "csv"]},
        description="Response format preference.",
    )
```

To attach a safety warning for dangerous settings, use
`json_schema_extra`:

```python
class Settings(BaseModel):
    max_concurrent_requests: int = Field(
        default=5,
        ge=1,
        le=50,
        json_schema_extra={"warning": "Values above 20 may trigger rate limiting on the external service."},
        description="Maximum parallel HTTP requests.",
    )
```

### Import-time validation

The following rules are enforced at **import time** by
`BaseFetcher.__init_subclass__`. If any rule is violated, the worker
fails to start with a clear error message identifying the fetcher and
the invalid field.

1. The fetcher's `name` MUST be unique across the entire registry. If a
   concrete fetcher declares a `name` already present in
   `FETCHER_REGISTRY`, `__init_subclass__` MUST raise an exception at
   import time, preventing the worker from starting. The error message
   MUST identify both classes in conflict (the already-registered class
   and the class attempting registration)
2. If `Settings` is declared, it MUST be a subclass of
   `pydantic.BaseModel`
3. All fields in `Settings` MUST have a default value (no required
   fields) — see "Design decisions" below
4. All field types MUST be scalar (`int`, `float`, `str`, `bool`).
   Complex types (lists, dicts, nested models) are rejected
5. Field names MUST be `snake_case` (lowercase letters, digits, and
   underscores only)

Pydantic itself enforces type correctness of defaults, constraint
consistency (e.g., `default` respects `ge`/`le`), and field descriptor
validity at class definition time — no custom validation is needed for
these.

### Accessing settings at runtime

`BaseFetcher` provides a `get_setting(key)` method that resolves values
with a clear precedence:

1. Value in `FetcherConfig.custom_settings` (DB) — if the key exists
2. Default from `Settings` model — if the key is a declared field
3. `KeyError` — if the key is not declared in `Settings`

```python
# Inside execute():
delay = self.get_setting("throttle_delay_seconds")  # returns DB value or 2.0
```

Settings are read from the DB at the start of each `run()` invocation
(not cached across runs). This means an admin can change a setting and
the next run picks it up immediately.

#### Runtime validation of stored values

At the start of each `run()`, all stored values from
`FetcherConfig.custom_settings` are validated by instantiating the
`Settings` model with the stored values merged over the defaults. If
Pydantic validation fails, `run()` terminates with status `failure` and
raises a `FetcherConfigError`. The error message must identify: the
fetcher name, the invalid field(s), the stored value(s), the constraint
violated, and a suggested corrective action (update the setting via the
API). No silent fallback to the default is performed.

This situation only occurs after a code change (fetcher Settings model
modification + redeploy) or direct DB manipulation — both moments when
operators monitor fetcher health closely, making quick detection and
correction likely.

### Schema registration and API exposure

The `BaseFetcher` registry (populated at import time via
`__init_subclass__`) collects the `Settings` class from each subclass.
This registry is used by:

- The API layer: the GET config endpoint returns
  `Settings.model_json_schema()` as the `settings_schema` field,
  providing a standard JSON Schema that the admin UI renders
  dynamically
- The API validation layer: the PATCH endpoint instantiates the
  `Settings` model with the submitted values to validate them
- The `sentinel fetcher config` CLI command (settings display)

Because Pydantic produces standard JSON Schema, the admin UI can render
settings forms without custom serialization logic — field types,
constraints, defaults, descriptions, choices, and warnings are all
present in the schema output.

### Design decisions

- **All settings have defaults**: no required fields are allowed in
  `Settings`. Every field must have a `default` value so that fetchers
  work out of the box. If a parameter has no reasonable default, it
  likely belongs as an environment variable, not a custom setting.
- **Orphaned keys are ignored silently**: when a field is removed from
  a fetcher's `Settings` model in a future version, old values in the
  JSONB column become orphaned. Pydantic's `model_validate()` ignores
  extra fields by default (using `model_config =
  ConfigDict(extra="ignore")`), so orphaned keys are inert. The PATCH
  endpoint rejects unknown keys, preventing new writes to orphaned
  settings. No cleanup migration is needed.
- **No environment variable override**: there is no mechanism to override
  custom_settings values via environment variables. The purpose of
  custom_settings is to avoid the env-var-requires-restart pattern. If
  deployment-level defaults are needed, an init script can call the
  PATCH API after deployment.
- **Why Pydantic and not a bespoke DSL**: the project already uses
  Pydantic extensively (request/response schemas, configuration). Using
  Pydantic for fetcher settings provides type safety, validation,
  JSON Schema generation, and IDE support for free — avoiding a custom
  validator, custom serialization, and custom documentation format.

### Referencing custom settings in fetcher specifications

Feature specifications that define fetchers with custom settings MUST
include a "Custom Settings" section with a table of settings and a
cross-reference to this document for the schema structure and validation
rules. The specification MUST NOT repeat the schema format, validation
rules, or `get_setting()` behavior inline — it should only list the
fetcher's specific settings with their values.

Example:

> **Custom Settings**
>
> This fetcher declares the following custom settings (see
> `docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
> Schema" for the schema structure and validation rules):
>
> | Setting | Type | Default | Constraints | Description |
> |---------|------|---------|-------------|-------------|
> | `my_setting` | int | 10 | 1–100 | Description of the setting |

## Fetcher Documentation Requirements

Every `BaseFetcher` subclass MUST have its complete definition in exactly
one specification document (single source of truth). Other specs may
reference it and include brief consumer-oriented summaries (see
"Cross-reference summaries" below), but MUST NOT specify the fetcher's
algorithm steps, error handling behavior, or custom settings.

### Classification Rule

The deciding factor for whether a fetcher gets a dedicated spec or lives
as a section in a feature spec is its **role**:

| Classification | Criterion | Spec treatment |
|---|---|---|
| The fetcher IS the feature | The spec would not exist without the fetcher. No distinct UI, API, or data model beyond what the fetcher requires. | Dedicated spec in the relevant domain. Named after what it does, not after the mechanism. |
| The fetcher supports a feature | The feature has its own identity (data model, API, UI, operations) and the fetcher is how data enters or exits. | Section within the feature spec, following the mandatory minimum template below. |

Test: if you removed the fetcher from the spec, would the spec still have
something meaningful to say? If yes → embedded. If no → dedicated spec.

Refinement for fetcher-centric specs: if the remaining non-fetcher
content exists primarily to support the fetcher itself (connection
details, authentication rationale, attribute mappings) rather than
serving independent consumers (APIs, UI, other specs), the spec is a
fetcher-centric spec — classify it as "the fetcher IS the feature."

### Minimum Documentation Template

Every fetcher — whether in a dedicated spec or embedded as a section —
MUST include at minimum:

1. **Properties table**:

   | Property | Value |
   |----------|-------|
   | Fetcher name | `<registry name>` |
   | Class name | `<PascalCase class>` |
   | Schedule | `<cron expression>` + human-readable |
   | Source | `<external service name>` |
   | Scope | `<what the fetcher processes per run>` |
   | Auth | `<authentication method>` |
   | Custom settings | Yes / No (link to Custom Settings section if yes) |

2. **Algorithm** (numbered steps describing what the fetcher does on each
   execution)

3. **Error handling** (what happens on failure — retry behavior, sanitized
   messages, partial progress). Exempt: fetchers that only interact with
   the local database.

4. **Metrics** (what counts as `record_created`, `record_updated`,
   `record_failed` — one sentence each)

5. **Custom settings table** (if applicable — following the format defined
   in the Custom Settings Schema section above)

A fetcher whose template contains TBD values is structurally prepared but
NOT considered compliant. Compliance requires real content in all
mandatory sections. TBD placeholders indicate that the fetcher's design
is pending and must be completed before implementation begins.

### Cross-Reference Summaries

Specs that consume data produced by a fetcher defined elsewhere may
include a brief consumer-oriented summary (3-5 sentences) alongside the
cross-reference, to provide reading continuity. The summary MUST describe
*what* the fetcher produces from the consumer's perspective, but MUST NOT
specify algorithm steps, error handling behavior, or custom settings.
The cross-referenced spec remains the single source of truth.

Example: `cvss-scoring.md` may summarize that `sync_cves_nvd` creates
CVSS assessments during each sync run, but must not describe the
incremental fetch strategy or the NVD Source API caching mechanism.

### Registry Maintenance

When defining a new fetcher, the Fetcher Registry table in
`docs/data-sources.md` MUST be updated with a row for the new fetcher.

### Domain Placement

Fetchers live in the domain they serve, not in a centralized `fetchers/`
folder:

- CVE/CVSS fetchers → `tickets/`
- Product/package fetchers → `packages/`
- Identity fetchers → `identity/`
- Platform-internal fetchers → `platform/`
- Integration-layer fetchers (if any) → `integrations/`

## Registry

The global registry is a module-level dictionary in
`backend/app/services/base_fetcher.py`:

```python
FETCHER_REGISTRY: dict[str, type[BaseFetcher]] = {}
```

Populated automatically by `BaseFetcher.__init_subclass__`. The registry
is used by:

- The API endpoints to list all known fetchers
- The Celery Beat schedule to register periodic tasks
- The dashboard frontend (indirectly, via the list endpoint)
- The on-demand single-CVE fetch system to discover fetchers that
  implement `fetch_single`
- The custom settings validation layer (schema lookup for PATCH
  endpoint and CLI display)

A fetcher class that is imported but should NOT be registered (e.g., an
intermediate abstract subclass) can set `abstract = True` as a class
attribute to opt out of registration.

## Celery Integration

Each registered fetcher corresponds to a Celery task in
`backend/app/tasks/fetchers.py`. A single generic task function handles
all fetchers:

```python
@celery_app.task(bind=True)
def run_fetcher(self, fetcher_name: str, triggered_by: str = "schedule",
                user_id: str | None = None) -> None:
    """Run a fetcher by name."""
    ...
```

The Celery Beat schedule is built dynamically from the registry at worker
startup, using each fetcher's effective schedule (config override or
default). When an admin modifies a fetcher's schedule via the API, the
Beat schedule MUST be updated accordingly (using `celery-redbeat` or
equivalent dynamic scheduler).

## Concurrency Control

Only one instance of a given fetcher can run at a time. The concurrency
check is performed at **two levels**:

1. **API level** (for manual triggers): the trigger endpoint checks for
   an active `FetcherRun` **synchronously** before enqueuing the Celery
   task. If a run is already active and not stale, the API returns 409
   Conflict immediately — no task is enqueued. If the active run is
   stale, it is marked as `failure` and the new run proceeds (see
   "Stale Run Detection" below).
2. **Task level** (for scheduled triggers): before invoking `execute()`,
   the `run_fetcher` task checks whether a `FetcherRun` record with
   `status = running` already exists for the requested `fetcher_name`.

At the task level:

- **If a run is already active and NOT stale**: the new attempt is
  discarded silently. No `FetcherRun` record is created. An
  application-level log message is emitted for observability:
  ```
  logger.info("Skipping scheduled run for '%s': already running (run_id=%s)",
              fetcher_name, active_run_id)
  ```
- **If a run is already active and stale**: the stale run is marked as
  `failure` (see "Stale Run Detection" below), then execution proceeds
  normally with a new `FetcherRun`.
- **If no run is active**: execution proceeds normally (a new `FetcherRun`
  is created with `status = running`).

This applies to all trigger sources:

| Scenario | Active run triggered by | New attempt triggered by | Behavior |
|---|---|---|---|
| Admin triggers while schedule is running | `schedule` | `manual` | API returns **409 Conflict** with message indicating the fetcher is already running |
| Schedule fires while manual run is active | `manual` | `schedule` | Silent discard with log (async — no caller to notify) |
| Schedule fires while previous schedule run is still active | `schedule` | `schedule` | Silent discard with log |
| Admin triggers while another manual run is active | `manual` | `manual` | API returns **409 Conflict** |
| Schedule fires while stale run exists | any | `schedule` | Stale run marked as `failure`, new run proceeds |
| Admin triggers while stale run exists | any | `manual` | Stale run marked as `failure`, new run proceeds (API returns **202 Accepted**) |
| Any trigger with stale run but `timeout_seconds = 0` | any | any | Stale detection disabled — treated as active run (409 or silent discard) |

The distinction is:

- **API-triggered attempts** (manual): the caller receives a synchronous
  **409 Conflict** response, so no log is needed — the caller is informed
  directly.
- **Schedule-triggered attempts**: there is no caller to notify, so the
  task logs the skip and returns without side effects.

The concurrency check SHOULD use a database query with row-level locking
(`SELECT ... FOR UPDATE`) or an equivalent atomic mechanism to prevent
race conditions between concurrent task starts.

## Stale Run Detection

A run is considered **stale** when it has been in `running` status for
longer than the fetcher's `timeout_seconds` (from `FetcherConfig`). The
default `timeout_seconds` is 3600 (1 hour). If `timeout_seconds` is set
to 0, stale detection is disabled for that fetcher — the run is never
considered stale regardless of how long it has been running.

When a stale run is detected (by the Celery task, the API trigger
endpoint, or the CLI), it is resolved by updating the stale `FetcherRun`
record:

**Operational risk of `timeout_seconds=0`**: disabling stale detection
means a fetcher that gets stuck will block all future executions
indefinitely, requiring manual intervention. When `timeout_seconds` is
set to 0 via the API or CLI, a warning is surfaced to the operator (see
`docs/features/platform/fetcher-operations.md`, "Update Fetcher Config"
for the API warning field and CLI warning message).

- `status` → `failure`
- `error_message` → `"Marked as stale (running for {elapsed}, timeout
  {timeout}s)"` for automatic resolution (Celery/API), or `"Marked as
  stale by operator via CLI"` for CLI resolution
- `finished_at` → `now()`
- `duration_seconds` → calculated from `started_at`

An application-level log message is emitted:

```
logger.warning("Marking stale run %s for '%s' as failure (running since %s, timeout %ds)",
               run_id, fetcher_name, started_at, timeout_seconds)
```

Stale run detection is a recovery mechanism for unclean process
terminations (OOM-kill, node crash, `kill -9`). It is NOT a substitute
for proper signal handling — processes that can handle `SIGINT`/`SIGTERM`
must do so (see `docs/features/platform/fetcher-operations.md`, section "CLI
Commands", "Signal handling").

## Data Model

### FetcherRun

Records every execution of a fetcher. This is the primary data source for
the dashboard charts.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier (matches `BaseFetcher.name`) |
| started_at | TIMESTAMPTZ | NOT NULL | When the run started |
| finished_at | TIMESTAMPTZ | nullable | When the run ended (NULL while running) |
| duration_seconds | FLOAT | nullable | Computed: `finished_at - started_at` in seconds |
| status | ENUM | NOT NULL | `running`, `success`, `failure`, `partial` |
| items_created | INTEGER | NOT NULL, DEFAULT 0 | Number of new records created |
| items_updated | INTEGER | NOT NULL, DEFAULT 0 | Number of existing records updated |
| items_failed | INTEGER | NOT NULL, DEFAULT 0 | Number of items that failed processing |
| error_message | TEXT | nullable | Sanitized error description (for all users). Written explicitly by the fetcher (`FetcherError`) or by BaseFetcher's generic fallback (see "Error Message Sanitization") |
| error_detail | TEXT | nullable | Raw exception message — `str(exception)` (`manage_fetchers` capability required for visibility) |
| error_traceback | TEXT | nullable | Full Python traceback (`manage_fetchers` capability required for visibility) |
| triggered_by | ENUM | NOT NULL | `schedule`, `manual` |
| triggered_by_user_id | UUID | FK(user.id), nullable | User who triggered the run (only for `manual`) |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |

**Notes**:
- `finished_at` is NULL while a run is in progress (status `running`).
  This can be used to detect stale runs (running for too long).
- `error_detail` and `error_traceback` are stored for debugging but MUST
  NOT be exposed to users without the `manage_fetchers` capability via
  the API.
- `duration_seconds` is stored (not computed at query time) because it is
  the primary Y-axis value for timeline charts and benefits from indexing.

### FetcherRunStatus Enum

| Value | Description |
|---|---|
| `running` | Execution in progress |
| `success` | Completed without errors |
| `failure` | Terminated with an unhandled exception |
| `partial` | Completed but some items failed (`items_failed > 0`). Implies `execute()` returned normally (no exception raised) |

### FetcherRunTriggeredBy Enum

| Value | Description |
|---|---|
| `schedule` | Triggered by Celery Beat schedule |
| `manual` | Triggered by an admin (via API or CLI) |

### FetcherConfig

Per-fetcher configuration, managed by admins. A record is created
automatically when a fetcher is first registered (on worker startup) if
one does not already exist. The auto-creation MUST use an idempotent
operation (`INSERT ... ON CONFLICT DO NOTHING` on the PK `fetcher_name`)
to guarantee safety when multiple workers start concurrently (common in
Kubernetes multi-replica deployments).

| Column | Type | Constraints | Description |
|---|---|---|---|
| fetcher_name | VARCHAR(100) | PK | Fetcher identifier (matches `BaseFetcher.name`) |
| enabled | BOOLEAN | NOT NULL, DEFAULT true | Whether the fetcher is active |
| schedule_override | VARCHAR(50) | nullable | Cron expression to override the fetcher's `default_schedule`. NULL means use the default. |
| timeout_seconds | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time in seconds. Also used as the stale run detection threshold. 0 disables both soft time limit and stale detection. |
| rate_limit | VARCHAR(20) | nullable | Rate limit expression (e.g., `"2/s"`, `"100/m"`). NULL means no limit. |
| custom_settings | JSONB | NOT NULL, DEFAULT `'{}'` | Per-fetcher operational parameters. Structure defined and validated by each fetcher's `Settings` Pydantic model (see "Custom Settings Schema" above). |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Last modification timestamp |

**Notes**:
- `FetcherConfig` uses `fetcher_name` as the PK (VARCHAR, not UUID) since
  fetcher names are unique identifiers defined in code.
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the Celery Beat schedule for this fetcher MUST be updated dynamically.
- `timeout_seconds` serves two purposes:
  1. **Celery soft time limit**: when > 0, enforced by the Celery task
     (`soft_time_limit`). When a fetcher exceeds this, a
     `SoftTimeLimitExceeded` exception is raised and the run is marked
     `failure`.
  2. **Stale run detection threshold**: when > 0, used by the Celery task,
     API trigger endpoint, and CLI to determine whether a `running`
     record is stale (see "Stale Run Detection" above).
  When set to 0, both mechanisms are disabled: Celery does not enforce a
  time limit, and stale detection treats the run as indefinitely active.
  The default of 3600 seconds (1 hour) applies when a `FetcherConfig`
  record is auto-created for a newly registered fetcher.

### FetcherAuditEvent

Audit trail for administrative actions on fetchers. Inherits `id`,
`created_at`, and `user_id` from `AuditEventMixin`.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Inherited from AuditEventMixin |
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier |
| event_type | ENUM | NOT NULL | See FetcherAuditEventType enum |
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Admin who performed the action. Nullable at DB level; `FetcherAuditLog.log_event()` validates presence (all fetcher admin actions are human-initiated) |
| old_value | TEXT | nullable | Previous value (e.g., old schedule expression) |
| new_value | TEXT | nullable | New value (e.g., new schedule expression) |
| detail | JSONB | nullable | Additional structured context (e.g., which config field changed) |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Inherited from AuditEventMixin |

### FetcherAuditEventType Enum

| Value | Description |
|---|---|
| `disabled` | Fetcher was disabled by an admin |
| `enabled` | Fetcher was re-enabled by an admin |
| `triggered` | Fetcher was manually triggered by an admin |
| `config_changed` | Fetcher configuration was modified (schedule, timeout, rate limit, custom settings) |

### Event Field Values

Each event type uses `old_value`, `new_value`, and `detail` as follows:

| Event Type | `old_value` | `new_value` | `detail` |
|---|---|---|---|
| `config_changed` (standard field) | Previous value (e.g., `"0 */6 * * *"`) | New value (e.g., `"0 */4 * * *"`) | `{"field": "<field_name>"}` where field is `schedule_override`, `timeout_seconds`, or `rate_limit` |
| `config_changed` (custom setting) | Previous value as string (e.g., `"2.0"`), or `null` if set for the first time | New value as string (e.g., `"5.0"`), or `null` if reset to default | `{"field": "custom_settings", "key": "<setting_key>"}` |
| `disabled` | `null` | `null` | `null` |
| `enabled` | `null` | `null` | `null` |
| `triggered` | `null` | `null` | `null` |

### One Event Per Field Rule

A single PATCH request that modifies N fields produces N separate
`config_changed` events, one for each field that actually changed.
Each custom setting sub-key counts as a separate field. Toggle
changes (`enabled` field) produce a separate `disabled` or `enabled`
event, not a `config_changed` event.

Example: a PATCH that changes `schedule_override`, `timeout_seconds`,
and `custom_settings.throttle_delay_seconds` produces three
`config_changed` events, each with its own `old_value`/`new_value`
pair and identifying `detail`. All events share the same `created_at`
timestamp and `user_id`. If the same PATCH also changes `enabled` to
`false`, a fourth event of type `disabled` is created.

## Data Retention

Individual `FetcherRun` records are retained for a configurable number
of days (default: **90 days**). After the retention period, runs are
aggregated into weekly summaries and individual records are deleted. The
retention period is controlled by the `retention_days` custom setting of
the `aggregate_fetcher_runs` fetcher (see
`docs/features/platform/fetcher-operations.md`, "Background Tasks").

### FetcherRunWeeklyAggregate

Stores weekly summaries of fetcher runs after the retention window
(default: 90 days, configurable via `retention_days` custom setting).

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier |
| week_start | DATE | NOT NULL | Monday of the aggregation week |
| run_count | INTEGER | NOT NULL | Total number of runs in the week |
| success_count | INTEGER | NOT NULL | Runs with status `success` |
| failure_count | INTEGER | NOT NULL | Runs with status `failure` |
| partial_count | INTEGER | NOT NULL | Runs with status `partial` |
| avg_duration_seconds | FLOAT | NOT NULL | Average duration across all runs |
| min_duration_seconds | FLOAT | NOT NULL | Minimum duration |
| max_duration_seconds | FLOAT | NOT NULL | Maximum duration |
| total_items_created | INTEGER | NOT NULL | Sum of `items_created` across all runs |
| total_items_updated | INTEGER | NOT NULL | Sum of `items_updated` across all runs |
| total_items_failed | INTEGER | NOT NULL | Sum of `items_failed` across all runs |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | When this aggregate was created |

**Unique constraint**: (fetcher_name, week_start)

### Aggregation Task

The aggregation algorithm is implemented by `aggregate_fetcher_runs`,
defined in `docs/features/platform/fetcher-operations.md` (Fetcher:
`aggregate_fetcher_runs`).

Before aggregating records of a given week, the task MUST force-resolve
any `FetcherRun` record with `status='running'` and `started_at` older
than the retention window. Force-resolution sets:

- `status` → `failure`
- `error_message` → `"Orphaned run resolved during aggregation (never completed)"`
- `finished_at` → `started_at`

Only after all orphaned runs in the batch are resolved does the task
proceed with the normal weekly aggregation. This ensures no record
remains indefinitely in `running` status.

## Deregistered Fetcher Lifecycle

When a fetcher class is removed from the codebase (or renamed), its
entry disappears from the in-memory `FETCHER_REGISTRY` at the next
worker restart. However, its `FetcherConfig` record and all associated
`FetcherRun`, `FetcherAuditEvent`, and `FetcherRunWeeklyAggregate`
records remain in the database. The FK constraints (`ON DELETE RESTRICT`)
on the three dependent tables prevent accidental deletion of the
`FetcherConfig` row while dependent records exist.

### Observable effects

- The fetcher is no longer present in `FETCHER_REGISTRY`
- Celery Beat does not schedule it
- The `GET /api/v1/fetchers` endpoint and `sentinel fetcher list` CLI
  command include the fetcher with `registered: false`. Code-defined
  metadata (`description`, `default_schedule`, `Settings` model)
  is unavailable and appears as `null`
- Per-fetcher **read** endpoints (`/runs`, `/timeline`, `GET /config`,
  `/audit-log`) work normally — they validate the fetcher name against
  `FetcherConfig` in the database, not against the registry
- Per-fetcher **write** endpoints (`POST /trigger`, `PATCH /config`)
  return `409 FETCHER_DEREGISTERED` — the fetcher cannot be triggered
  or configured since the code has been removed
- The `sentinel fetcher run` CLI command returns an error for
  deregistered fetchers. `sentinel fetcher config` displays a
  read-only snapshot of the stored configuration without schema context
- Historical data (runs, aggregates, audit events) remains in the
  database and is accessible through the API, CLI, and dashboard UI

### Aggregation task behavior

The `aggregate_fetcher_runs` task selects `FetcherRun` records by age,
not by registry membership. It continues to aggregate and eventually
delete old individual run records for deregistered fetchers on the same
schedule as for active fetchers. Over time, all individual runs are
replaced by `FetcherRunWeeklyAggregate` records.

**Visibility consequence**: after the retention window (default: 90
days), individual `FetcherRun` records for a deregistered fetcher are
deleted and only `FetcherRunWeeklyAggregate` records remain. The
timeline chart on the dashboard continues to display aggregated data,
but the run history table shows no entries beyond the retention window.
Operators should investigate detailed failure information (error
messages, tracebacks) within the retention period.

### No automatic cleanup

Sentinel does not automatically delete `FetcherConfig` records for
deregistered fetchers. This is intentional:

- Historical run and audit data has forensic and operational value
- The `ON DELETE RESTRICT` FK constraints make accidental cleanup
  impossible — dependent records must be removed first
- The number of deregistered fetchers grows slowly (order of units over
  the lifetime of the application) and does not create a storage or
  performance concern

If an operator needs to remove all traces of a deregistered fetcher,
the cleanup is a manual database operation that must respect FK ordering:
delete `FetcherRunWeeklyAggregate` records, then `FetcherRun` records,
then `FetcherAuditEvent` records, and finally the `FetcherConfig` row.

## Guardrail: Fetcher Base Class Compliance

See Guardrail 14 in `AGENTS.md`. Every background task that fetches data
from an external source MUST:

1. Inherit from `BaseFetcher`
2. Define `name`, `description`, and `default_schedule`
3. Implement `execute()` with proper metric reporting
4. NOT bypass the base class with a raw Celery task

**Exception — sub-operation tasks**: background tasks that fetch from
external sources as a sub-operation of an existing fetcher (not as an
independent periodic sync) are exempt from `BaseFetcher`. These tasks:

- Are triggered on-demand by a parent fetcher, not by Celery Beat
- Do not have their own schedule
- Do not appear as separate cards in the dashboard
- Their metrics are not tracked independently

Example: `create_ticket_from_detection` is enqueued by the
`check_ibs_track_releases` fetcher (Case C) and fetches CVE data from
NVD and package data from SMELT. It is a standalone Celery task, not a
`BaseFetcher` subclass, because it is a reaction to a discovery made by
the parent fetcher, not an independent sync process.

If there is a compelling reason to bypass `BaseFetcher` for a specific
case beyond this exception, the agent MUST stop and inform the user with
a detailed explanation of why the bypass is advantageous, so the decision
can be made together.

After creating or modifying a fetcher, the `@fetcher-compliance-reviewer`
agent MUST be invoked.

## Subagent: @fetcher-compliance-reviewer

A read-only reviewer agent that verifies fetcher implementations are
correctly integrated with the fetcher infrastructure.

### Trigger Conditions

Invoke `@fetcher-compliance-reviewer` when:

- A new file is created in `backend/app/tasks/` or `backend/app/services/`
  that implements fetching/sync logic
- An existing fetcher is modified in ways that affect its metrics or
  registration
- `BaseFetcher` itself is modified

### What It Checks

1. **Base class inheritance**: the fetcher class inherits from
   `BaseFetcher` (not bypassing it with a raw Celery task)
2. **Required attributes**: `name`, `description`, and `default_schedule`
   are defined on the class. For CVE fetchers,
   `source_reference_url_pattern` should be set if the source has a
   human-readable web page (see `docs/features/tickets/ticket-references.md`), and
   `fetch_single()` must be implemented (see "On-demand Single-Item
   Fetch" above)
3. **Unique name**: the fetcher's `name` does not conflict with any
   existing registered fetcher
4. **Metric reporting**: the `execute()` method calls
   `self.record_created()` and/or `self.record_updated()` where
   appropriate (creating/updating records without calling these methods
   means the dashboard will show 0 items)
5. **Test coverage**: tests exist that:
   - Verify `FetcherRun` records are created after execution
   - Verify item counts are correct
   - Verify error handling produces `failure` status
6. **No raw Celery tasks for fetching**: any background task that fetches
   external data MUST go through `BaseFetcher`, not be a standalone
   `@celery_app.task`

### Output

Structured summary with:

1. **Clean**: aspects that correctly follow the `BaseFetcher` pattern
2. **Integration issues**: problems with registration, metrics, or
   dashboard representation
3. **Test gaps**: missing test coverage for fetcher runs
4. **Verdict**: `Clean`, `Minor issues`, or `Needs revision`

## Dependencies

- Celery Beat with dynamic schedule support (`celery-redbeat` or
  equivalent)

## Audit Trail

The `FetcherAuditLog` subclass of `BaseAuditLog` provides the event
creation helper and registers the fetcher audit trail in the global
registry. See `docs/features/platform/audit-trail-infrastructure.md`
for the base class contract.

## Open Questions

None at this time.
