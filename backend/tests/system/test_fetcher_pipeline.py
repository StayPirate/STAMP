"""End-to-end local process system test for the generic scheduled
fetcher pipeline.

See `docs/features/platform/testing-strategy.md` (Local Process System
Testing) for the full behavioral contract this test proves: real worker
and Beat processes, real PostgreSQL and Redis, registration → config
bootstrap → RedBeat scheduling → broker delivery → worker execution →
`FetcherRun` finalization → Public API visibility, with no domain-table
mutation and no `FetcherAuditEvent` for a scheduled run.

This is the ONLY test in this module — the harness fixture
(`fetcher_pipeline_harness` in `conftest.py`) owns all process spawning
and deterministic cleanup, so a single comprehensive test avoids paying
the worker/Beat startup cost more than once while still proving every
required step end-to-end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from celery.schedules import crontab
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.fetchers as fetchers_module
from app.database import get_db
from app.main import app
from tests.system.conftest import SYSTEM_FETCHER_NAME, FetcherPipelineHarness


@pytest.mark.system
async def test_scheduled_fetcher_pipeline_end_to_end(
    fetcher_pipeline_harness: FetcherPipelineHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = fetcher_pipeline_harness

    # Baseline: capture the domain-table snapshot BEFORE any process is
    # spawned, so the final comparison proves this test introduced no
    # domain mutation (testing-strategy.md, Behavioral Requirements,
    # point 6) — independent of whatever pre-existing state the shared
    # test database happens to hold.
    domain_snapshot_before = await harness.snapshot_domain_tables()

    # 1. A real worker starts, becomes reachable, and has the generic
    # `run_fetcher` task registered.
    harness.start_worker()
    harness.wait_worker_ready()

    # 2. A real Beat starts, bootstraps the test fetcher's
    # `FetcherConfig`, and reconciles a RedBeat entry for it.
    harness.start_beat()
    config = await harness.wait_config_row()
    assert config.fetcher_name == SYSTEM_FETCHER_NAME
    assert config.enabled is True

    entry = harness.wait_redbeat_entry()
    assert entry.task == "run_fetcher"
    assert entry.enabled is True
    assert entry.kwargs == {
        "fetcher_name": SYSTEM_FETCHER_NAME,
        "triggered_by": "schedule",
    }
    assert entry.schedule == crontab.from_string("* * * * *")

    # 3. Make the existing entry immediately overdue via RedBeat's own
    # public API — Beat still performs the actual dispatch through the
    # broker on its next tick (bounded by --max-interval=5), the broker
    # path is never bypassed with a manual send_task().
    harness.make_due()

    # 4. The worker executes the fetcher and finalizes a `FetcherRun`.
    run = await harness.wait_finalized_run()

    # Stop Beat immediately — before any further assertion — so a
    # second scheduling tick cannot fire another dispatch while this
    # test still runs (the schedule is "* * * * *"; only one finalized
    # run must exist for the entire test).
    harness.stop_beat()

    assert run.status == "success", (
        f"expected a successful run, got {run.status!r}: "
        f"error_message={run.error_message!r} error_detail={run.error_detail!r}"
    )
    assert run.items_created == 0
    assert run.items_updated == 0
    assert run.items_failed == 0
    assert run.triggered_by == "schedule"
    assert run.triggered_by_user_id is None
    assert run.error_message is None
    assert run.error_detail is None
    assert run.error_traceback is None
    assert run.cursor is None
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.finished_at >= run.started_at

    # Exactly one finalized run for the test fetcher — no duplicates
    # from a concurrent/second dispatch.
    all_runs = await harness.list_runs()
    assert len(all_runs) == 1, [r.id for r in all_runs]

    # No `FetcherAuditEvent` is created for a scheduled run.
    audit_events = await harness.list_audit_events()
    assert audit_events == []

    # 5. The finalized run is visible through the Public fetcher
    # observation API, via the in-process ASGI client with an
    # independently connecting database session and the isolated test
    # Celery/Redis instance.
    monkeypatch.setattr(fetchers_module, "celery_app", harness.celery_app)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with harness.session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/fetchers")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    body = response.json()
    item = next(
        entry_data
        for entry_data in body["data"]
        if entry_data["fetcher_name"] == SYSTEM_FETCHER_NAME
    )
    assert item["registered"] is True
    assert item["last_run"] is not None
    assert item["last_run"]["status"] == "success"
    assert item["last_run"]["items_created"] == 0
    assert item["last_run"]["triggered_by"] == "schedule"

    # 6. No domain tables are mutated — compare the full pre/post
    # snapshot of every table outside the fetcher infrastructure.
    domain_snapshot_after = await harness.snapshot_domain_tables()
    assert domain_snapshot_after == domain_snapshot_before
