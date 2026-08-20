"""Public fetcher observation API endpoints.

See `docs/features/platform/fetcher-operations.md` (List Fetchers, List
Fetcher Runs, Get Fetcher Run Detail, Get Fetcher Run Timeline Data) for
the authoritative endpoint contracts this module implements. Handlers
stay thin: they validate, derive `has_manage_fetchers` from the
optional principal, delegate to `app.services.fetcher_operations`, and
map the result to the documented response — no business logic or
database query lives here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import OptionalCurrentUser
from app.celery_app import celery_app
from app.core.dates import (
    normalize_date_bound,
    parse_date_range_bound,
    validate_date_range_order,
)
from app.core.enums import Capability
from app.core.errors import AppError, ErrorCode
from app.core.permissions import get_capabilities
from app.database import DatabaseSession
from app.schemas.common import PaginationMeta, UserReference
from app.schemas.fetcher import (
    FetcherDisabledPeriodData,
    FetcherLastRunData,
    FetcherListItemData,
    FetcherListResponse,
    FetcherRunDetailData,
    FetcherRunDetailResponse,
    FetcherRunListItemData,
    FetcherRunListQuery,
    FetcherRunListResponse,
    FetcherTimelineData,
    FetcherTimelinePointData,
    FetcherTimelineQuery,
    FetcherTimelineResponse,
)
from app.services import fetcher_operations, user_service
from app.services.fetcher_operations import (
    FetcherListItem,
    FetcherNotFoundError,
    FetcherRunNotFoundError,
    FetcherRunSummary,
    FetcherTimeline,
)

router = APIRouter(prefix="/api/v1", tags=["Fetchers"])

# Maximum allowed interval between `from_date` and `to_date` for the
# timeline endpoint (see fetcher-operations.md, Get Fetcher Run
# Timeline Data, "Date range constraint").
_TIMELINE_MAX_SECONDS = 1825 * 86400
_TIMELINE_DEFAULT_LOOKBACK = timedelta(days=7)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _resolve_has_manage_fetchers(
    db: DatabaseSession, principal: OptionalCurrentUser
) -> bool:
    """Derive `has_manage_fetchers` per fetcher-operations.md (Access
    Control, `has_manage_fetchers` derivation): `False` for an
    anonymous caller, else whether the caller's current roles grant
    `manage_fetchers`. Never produces a 401/403 on these Public
    endpoints — it only controls field-level visibility."""
    if principal is None:
        return False
    roles = await user_service.get_user_roles(db, principal.user.id)
    return Capability.MANAGE_FETCHERS in get_capabilities(roles)


def _common_run_fields(run: FetcherRunSummary) -> dict[str, Any]:
    return {
        "id": run.id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": run.duration_seconds,
        "status": run.status,
        "items_created": run.items_created,
        "items_updated": run.items_updated,
        "items_failed": run.items_failed,
        "triggered_by": run.triggered_by,
        "triggered_by_user": (
            UserReference.model_validate(run.triggered_by_user)
            if run.triggered_by_user is not None
            else None
        ),
        "error_message": run.error_message,
        "stale": run.stale,
    }


def _serialize_run_summary(run: FetcherRunSummary) -> FetcherLastRunData:
    return FetcherLastRunData(**_common_run_fields(run))


def _serialize_run_list_item(run: FetcherRunSummary) -> FetcherRunListItemData:
    return FetcherRunListItemData(
        fetcher_name=run.fetcher_name, **_common_run_fields(run)
    )


def _serialize_list_item(item: FetcherListItem) -> FetcherListItemData:
    return FetcherListItemData(
        fetcher_name=item.fetcher_name,
        registered=item.registered,
        description=item.description,
        enabled=item.enabled,
        effective_schedule=item.effective_schedule,
        schedule_is_override=item.schedule_is_override,
        default_schedule=item.default_schedule,
        cve_source_type=item.cve_source_type,
        next_run_at=item.next_run_at,
        custom_settings_count=item.custom_settings_count,
        last_run=_serialize_run_summary(item.last_run)
        if item.last_run is not None
        else None,
    )


def _serialize_timeline(timeline: FetcherTimeline) -> FetcherTimelineData:
    return FetcherTimelineData(
        points=[
            FetcherTimelinePointData(
                run_id=point.run_id,
                timestamp=point.timestamp,
                duration_seconds=point.duration_seconds,
                items_created=point.items_created,
                items_updated=point.items_updated,
                items_failed=point.items_failed,
                status=point.status,
            )
            for point in timeline.points
        ],
        disabled_periods=[
            FetcherDisabledPeriodData(
                disabled_at=period.disabled_at,
                disabled_by=(
                    UserReference.model_validate(period.disabled_by)
                    if period.disabled_by is not None
                    else None
                ),
                enabled_at=period.enabled_at,
                enabled_by=(
                    UserReference.model_validate(period.enabled_by)
                    if period.enabled_by is not None
                    else None
                ),
            )
            for period in timeline.disabled_periods
        ],
    )


def _not_found(exc: Exception) -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code=ErrorCode.FETCHER_NOT_FOUND,
        detail=str(exc),
    )


# ---------------------------------------------------------------------------
# Query parameter builders
#
# Declared as individual `Query()`-annotated parameters (rather than a
# single `Annotated[Model, Query()]` query-parameter-model field) so
# each field is visible individually to the shared query-length-limit
# dependency's route-dependant walk (`app.core.query_limits`). Mirrors
# `app/api/v1/identity_audit.py` and `app/api/v1/settings.py`.
# ---------------------------------------------------------------------------


def _fetcher_run_list_query(
    *,
    status: Annotated[
        str | None,
        Query(description="Filter by run status (success, failure, partial, running)."),
    ] = None,
    from_date: Annotated[
        str | None,
        Query(
            description="ISO 8601 date/datetime; inclusive lower bound on started_at."
        ),
    ] = None,
    to_date: Annotated[
        str | None,
        Query(
            description="ISO 8601 date/datetime; inclusive upper bound on started_at."
        ),
    ] = None,
    page: Annotated[int, Query(ge=1, le=2_147_483_647, description="Page number.")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Items per page; maximum 100.")
    ] = 20,
) -> FetcherRunListQuery:
    parsed_from = parse_date_range_bound("from_date", from_date)
    parsed_to = parse_date_range_bound("to_date", to_date)
    validate_date_range_order(parsed_from, parsed_to)
    return FetcherRunListQuery(
        status=status,
        from_date=parsed_from,
        to_date=parsed_to,
        page=page,
        per_page=per_page,
    )


def _fetcher_timeline_query(
    *,
    from_date: Annotated[
        str | None,
        Query(
            description="ISO 8601 date/datetime; inclusive lower bound. Default: -7d."
        ),
    ] = None,
    to_date: Annotated[
        str | None,
        Query(
            description="ISO 8601 date/datetime; inclusive upper bound. Default: now."
        ),
    ] = None,
) -> FetcherTimelineQuery:
    parsed_from = parse_date_range_bound("from_date", from_date)
    parsed_to = parse_date_range_bound("to_date", to_date)
    validate_date_range_order(parsed_from, parsed_to)
    return FetcherTimelineQuery(from_date=parsed_from, to_date=parsed_to)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/fetchers",
    response_model=FetcherListResponse,
    summary="List all fetchers",
    description=(
        "Returns every fetcher — registered and deregistered — merged with "
        "its latest run and, for enabled registered fetchers, its next "
        "scheduled run time. Optional authentication: anonymous callers "
        "see sanitized fields only; callers with manage_fetchers "
        "additionally see the manual-trigger actor."
    ),
)
async def list_fetchers(
    principal: OptionalCurrentUser,
    db: DatabaseSession,
) -> FetcherListResponse:
    """List all fetchers — see
    `docs/features/platform/fetcher-operations.md` (List Fetchers)."""
    has_manage_fetchers = await _resolve_has_manage_fetchers(db, principal)
    items = await fetcher_operations.list_fetchers(
        db, has_manage_fetchers=has_manage_fetchers, celery_app=celery_app
    )
    return FetcherListResponse(data=[_serialize_list_item(item) for item in items])


@router.get(
    "/fetchers/{fetcher_name}/runs",
    response_model=FetcherRunListResponse,
    summary="List fetcher run history",
    description=(
        "Returns a paginated, fixed reverse-chronological list of runs for "
        "the named fetcher. Supports filtering by status and date range."
    ),
)
async def list_fetcher_runs(
    fetcher_name: str,
    principal: OptionalCurrentUser,
    db: DatabaseSession,
    query: Annotated[FetcherRunListQuery, Depends(_fetcher_run_list_query)],
) -> FetcherRunListResponse:
    """List fetcher run history — see
    `docs/features/platform/fetcher-operations.md` (List Fetcher
    Runs)."""
    has_manage_fetchers = await _resolve_has_manage_fetchers(db, principal)
    try:
        page = await fetcher_operations.list_fetcher_runs(
            db,
            fetcher_name=fetcher_name,
            has_manage_fetchers=has_manage_fetchers,
            page=query.page,
            per_page=query.per_page,
            status=query.status,
            from_date=query.from_date,
            to_date=query.to_date,
        )
    except FetcherNotFoundError as exc:
        raise _not_found(exc) from exc
    return FetcherRunListResponse(
        data=[_serialize_run_list_item(item) for item in page.items],
        meta=PaginationMeta(total=page.total, page=page.page, per_page=page.per_page),
    )


@router.get(
    "/fetchers/{fetcher_name}/runs/{run_id}",
    response_model=FetcherRunDetailResponse,
    response_model_exclude_unset=True,
    summary="Get fetcher run detail",
    description=(
        "Returns full detail for a single run. Raw error detail and "
        "traceback are present only for callers with manage_fetchers."
    ),
)
async def get_fetcher_run(
    fetcher_name: str,
    run_id: UUID,
    principal: OptionalCurrentUser,
    db: DatabaseSession,
) -> FetcherRunDetailResponse:
    """Get fetcher run detail — see
    `docs/features/platform/fetcher-operations.md` (Get Fetcher Run
    Detail)."""
    has_manage_fetchers = await _resolve_has_manage_fetchers(db, principal)
    try:
        run = await fetcher_operations.get_fetcher_run(
            db,
            fetcher_name=fetcher_name,
            run_id=run_id,
            has_manage_fetchers=has_manage_fetchers,
        )
    except (FetcherNotFoundError, FetcherRunNotFoundError) as exc:
        raise _not_found(exc) from exc

    extra: dict[str, Any] = {}
    if has_manage_fetchers:
        extra["error_detail"] = run.error_detail
        extra["error_traceback"] = run.error_traceback
    data = FetcherRunDetailData(
        fetcher_name=run.fetcher_name, **_common_run_fields(run), **extra
    )
    return FetcherRunDetailResponse(data=data)


@router.get(
    "/fetchers/{fetcher_name}/timeline",
    response_model=FetcherTimelineResponse,
    summary="Get fetcher run timeline data",
    description=(
        "Returns time-series run data and disabled periods for chart "
        "rendering, bounded by an optional date range (maximum 1825 days)."
    ),
)
async def get_fetcher_timeline(
    fetcher_name: str,
    principal: OptionalCurrentUser,
    db: DatabaseSession,
    query: Annotated[FetcherTimelineQuery, Depends(_fetcher_timeline_query)],
) -> FetcherTimelineResponse:
    """Get fetcher run timeline data — see
    `docs/features/platform/fetcher-operations.md` (Get Fetcher Run
    Timeline Data)."""
    has_manage_fetchers = await _resolve_has_manage_fetchers(db, principal)

    now = datetime.now(UTC)
    to_date = (
        normalize_date_bound(query.to_date, end_of_day=True)
        if query.to_date is not None
        else now
    )
    from_date = (
        normalize_date_bound(query.from_date, end_of_day=False)
        if query.from_date is not None
        else now - _TIMELINE_DEFAULT_LOOKBACK
    )
    if (to_date - from_date).total_seconds() > _TIMELINE_MAX_SECONDS:
        raise AppError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.DATE_RANGE_TOO_WIDE,
            detail="Date range must not exceed 1825 days.",
        )

    try:
        timeline = await fetcher_operations.get_fetcher_timeline(
            db,
            fetcher_name=fetcher_name,
            has_manage_fetchers=has_manage_fetchers,
            from_date=from_date,
            to_date=to_date,
        )
    except FetcherNotFoundError as exc:
        raise _not_found(exc) from exc
    return FetcherTimelineResponse(data=_serialize_timeline(timeline))
