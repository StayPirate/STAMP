"""Test-owned Celery application launcher for the local process system suite.

Spawned worker and Beat processes use this module as their `-A` app path
(`celery -A tests.support.system_fetcher_app worker ...`) instead of
`app.celery_app` directly. Importing `tests.support.system_fetcher` first
registers `EvaluateTestPipeline` into the spawned process's
`FETCHER_REGISTRY` *before* `app.celery_app` is imported — this ordering
matters because worker/Beat startup handlers
(`app.tasks.worker_startup`, `app.tasks.beat_startup`) call
`bootstrap_fetcher_configs()` / `reconcile_beat_schedule()` against
whatever `FETCHER_REGISTRY` contains at that point, and both read the
registry only after `app.celery_app` finishes its own import-time
`app.services.fetcher_discovery` import.

This module MUST NOT be imported by any production entrypoint — see
`docs/features/platform/testing-strategy.md` (Local Process System
Testing, Registration Boundary).
"""

from __future__ import annotations

import tests.support.system_fetcher  # noqa: F401  (registration side effect)
from app.celery_app import celery_app

__all__ = ["celery_app"]
