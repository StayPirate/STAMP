"""Request/response/query schemas for the Public fetcher observation API.

See `docs/features/platform/fetcher-operations.md` (List Fetchers, List
Fetcher Runs, Get Fetcher Run Detail, Get Fetcher Run Timeline Data) for
the authoritative request/response contract these schemas implement.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import PaginationMeta, UserReference

# ---------------------------------------------------------------------------
# Query parameters
# ---------------------------------------------------------------------------


class FetcherRunListQuery(BaseModel):
    """Query parameters for `GET /api/v1/fetchers/{fetcher_name}/runs`.

    `status` is intentionally `str | None`, not a typed
    `FetcherRunStatus`: an invalid value must be silently ignored and
    produce an empty result (`docs/api-spec.md`, Enum Filter
    Validation) rather than the schema-validation `422` a typed enum
    field would raise. The service validates the value itself, after
    the fetcher-existence check (see `fetcher_operations.list_fetcher_runs`).

    `from_date`/`to_date` are already-parsed `date`/`datetime` values by
    the time this model is constructed — the API layer's
    `app.core.dates.parse_date_range_bound()` performs the strict ISO
    8601 parsing (and its `422` rejection) before this model sees them.
    An inverted normalized range is validated separately by
    `app.core.dates.validate_date_range_order()` (`400
    DATE_RANGE_INVERTED`).
    """

    status: str | None = None
    from_date: date | datetime | None = None
    to_date: date | datetime | None = None
    page: int = Field(default=1, ge=1, le=2_147_483_647)
    per_page: int = Field(default=20, ge=1, le=100)


class FetcherTimelineQuery(BaseModel):
    """Query parameters for `GET /api/v1/fetchers/{fetcher_name}/timeline`.

    `from_date`/`to_date` are already-parsed `date`/`datetime` values
    (see `FetcherRunListQuery`). Both default to `None` here — the
    endpoint resolves the documented defaults (7 days ago / now) and
    the 1825-day maximum interval, since those require the current
    time at request time, not a static schema default.
    """

    from_date: date | datetime | None = None
    to_date: date | datetime | None = None


# ---------------------------------------------------------------------------
# List Fetchers
# ---------------------------------------------------------------------------


class FetcherLastRunData(BaseModel):
    """The `last_run` object nested in a `List Fetchers` item.

    `triggered_by_user` is a `UserReference` when `triggered_by` is
    `manual` and the caller holds `manage_fetchers`, otherwise `null`.
    `error_detail`/`error_traceback` are never included at this level
    (list response) — see `docs/features/platform/fetcher-operations.md`
    (List Fetchers, Fields)."""

    id: UUID
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    status: str
    items_created: int
    items_updated: int
    items_failed: int
    triggered_by: str
    triggered_by_user: UserReference | None
    error_message: str | None
    stale: bool


class FetcherListItemData(BaseModel):
    """One entry in the `GET /api/v1/fetchers` response — a registered
    or deregistered fetcher merged with its latest run and, when
    applicable, its next scheduled run time."""

    fetcher_name: str
    registered: bool
    description: str | None
    enabled: bool
    effective_schedule: str | None
    schedule_is_override: bool | None
    default_schedule: str | None
    cve_source_type: str | None
    next_run_at: datetime | None
    custom_settings_count: int
    last_run: FetcherLastRunData | None


class FetcherListResponse(BaseModel):
    """Response body for `GET /api/v1/fetchers`. Not paginated — see
    `docs/features/platform/fetcher-operations.md` (List Fetchers,
    Pagination)."""

    data: list[FetcherListItemData]


# ---------------------------------------------------------------------------
# List Fetcher Runs / Get Fetcher Run Detail
# ---------------------------------------------------------------------------


class FetcherRunListItemData(FetcherLastRunData):
    """One entry in the `GET /api/v1/fetchers/{fetcher_name}/runs`
    response — the same shape as `FetcherLastRunData` plus
    `fetcher_name` (needed here since a run list item is not already
    nested under a fetcher-scoped object)."""

    fetcher_name: str


class FetcherRunListResponse(BaseModel):
    """Response body for `GET /api/v1/fetchers/{fetcher_name}/runs`."""

    data: list[FetcherRunListItemData]
    meta: PaginationMeta


class FetcherRunDetailData(FetcherRunListItemData):
    """The `data` object for `GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}`.

    `error_detail`/`error_traceback` default to unset. The endpoint
    constructs this model without passing them for callers lacking
    `manage_fetchers` and declares `response_model_exclude_unset=True`,
    so Pydantic renders them as fully absent JSON keys — not
    `"error_detail": null` — for those callers, per
    `docs/features/platform/fetcher-operations.md` (Get Fetcher Run
    Detail, Fields). Every other field on this model is always
    explicitly set at construction time, so `exclude_unset` has no
    effect on them.
    """

    error_detail: str | None = None
    error_traceback: str | None = None


class FetcherRunDetailResponse(BaseModel):
    """Response body for `GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}`."""

    data: FetcherRunDetailData


# ---------------------------------------------------------------------------
# Get Fetcher Run Timeline Data
# ---------------------------------------------------------------------------


class FetcherTimelinePointData(BaseModel):
    """One chart data point — a single `FetcherRun` record."""

    run_id: UUID
    timestamp: datetime
    duration_seconds: float | None
    items_created: int
    items_updated: int
    items_failed: int
    status: str


class FetcherDisabledPeriodData(BaseModel):
    """One derived disabled period. `disabled_by`/`enabled_by` are
    `UserReference` objects only when the caller holds
    `manage_fetchers`, otherwise `null` — never absent (unlike the run
    detail's raw diagnostic fields), since these are always meaningful
    as a null value regardless of capability."""

    disabled_at: datetime
    disabled_by: UserReference | None
    enabled_at: datetime | None
    enabled_by: UserReference | None


class FetcherTimelineData(BaseModel):
    points: list[FetcherTimelinePointData]
    disabled_periods: list[FetcherDisabledPeriodData]


class FetcherTimelineResponse(BaseModel):
    """Response body for `GET /api/v1/fetchers/{fetcher_name}/timeline`."""

    data: FetcherTimelineData
