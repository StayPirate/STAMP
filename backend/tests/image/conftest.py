"""Fixtures for the black-box image smoke test suite.

This suite exercises the built Docker image as a running container, over
HTTP and via ``compose exec`` — it is NOT an in-process test suite. It
therefore deliberately does NOT reuse the ``db_session`` / ``client``
fixtures from ``backend/tests/conftest.py`` (those wire an ASGI
transport and a test database into the in-process app). Here the app
runs inside a container started by ``scripts/image-smoke.sh``.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing).
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import httpx
import pytest

# Default bound for every `compose_exec` invocation. All existing exec
# calls (import checks, `celery report`, `alembic check`, `id -u`, etc.)
# complete in well under a second inside the container; 30s leaves ample
# margin for a loaded CI runner while still failing fast — and with a
# clear diagnostic — if a command hangs instead of silently blocking the
# test run indefinitely. Callers with a legitimately longer bounded
# operation (e.g. a poll loop for a broker-delivered task effect) pass an
# explicit `timeout=` override.
_DEFAULT_EXEC_TIMEOUT = 30.0
_DIAGNOSTIC_TIMEOUT = 15.0
_COMPOSE_CMD = ("docker", "compose")


def _resolve_compose_invocation() -> tuple[list[str], str]:
    """Resolve the shared ``(file_args, project)`` pair from the env vars
    scripts/image-smoke.sh exports (see the ``compose_exec``/``compose_run``
    fixture docstrings below for the meaning of each variable). Shared by the
    fixtures so the resolution logic lives in exactly one place.
    """
    compose_files = os.environ.get("COMPOSE_FILES", "docker-compose.smoke.yml").split(
        ":"
    )
    project = os.environ.get("COMPOSE_PROJECT", "sentinel-smoke")
    file_args: list[str] = []
    for compose_file in compose_files:
        file_args.extend(["-f", compose_file])
    return file_args, project


def _run_compose_bounded(
    cmd: list[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run an assembled compose command, bounded by ``timeout`` seconds.

    Shared by ``compose_exec`` and ``compose_run``. A command that
    exceeds the bound fails the test immediately via ``pytest.fail``
    with the full command, timeout value, and any output captured
    before the kill — instead of hanging the whole suite (and, in CI,
    the job) until the external runner-level timeout intervenes.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"compose command timed out after {timeout}s: cmd={cmd!r} "
            f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
        )


def _restart_compose_service(
    service: str,
    *,
    file_args: list[str],
    project: str,
    wait_for_ready: bool = True,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Stop and start one compose service, optionally waiting for readiness."""
    common_args = [*_COMPOSE_CMD, "-p", project, *file_args]
    stop_result = _run_compose_bounded(
        [*common_args, "stop", service],
        timeout,
    )
    if stop_result.returncode != 0:
        return stop_result

    wait_args = ["--wait"] if wait_for_ready else []
    return _run_compose_bounded(
        [*common_args, "up", "-d", *wait_args, service],
        timeout,
    )


def _capture_compose_diagnostics(phase: str) -> list[tuple[str, str]]:
    """Capture bounded primary-stack state and logs for one failed phase."""
    file_args, project = _resolve_compose_invocation()
    common_args = [*_COMPOSE_CMD, "-p", project, *file_args]
    commands = [
        (
            "compose ps --all",
            [*common_args, "ps", "--all", "--format", "json"],
        ),
        (
            "compose logs (tail=100)",
            [
                *common_args,
                "logs",
                "--timestamps",
                "--tail=100",
                "--no-color",
            ],
        ),
    ]
    sections: list[tuple[str, str]] = []

    for title, command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=_DIAGNOSTIC_TIMEOUT,
            )
            output = (
                (result.stdout or "") + (result.stderr or "")
            ).strip() or "[no output]"
            if result.returncode != 0:
                output = f"[diagnostic command exited {result.returncode}]\n{output}"
        except (subprocess.TimeoutExpired, OSError) as exc:
            output = f"[diagnostic command failed: {exc!r}]"
        sections.append((f"{title} ({phase})", output))

    return sections


@pytest.hookimpl(tryfirst=True, wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Attach primary-stack state and logs to failed image-test phases.

    Fires on every test in this suite, but only acts when an image-marked
    test's setup, call, or teardown phase fails. Captures ``compose ps
    --all --format json`` plus timestamped ``compose logs --tail=100``
    with no service filter. Including stopped containers preserves clean
    and failed process exits; timestamped, interleaved logs preserve the
    events that led to them. Both snapshots are attached as report
    sections before the runner tears down the stack.

    Scoped to this suite only (`item.get_closest_marker("image") is not
    None`). This is load-bearing today, not just a safety net for a
    hypothetical future reorganization: `pytest_collection_modifyitems`
    deselects `image` items *after* collection, so this conftest — and
    this hookimpl — is registered session-globally even during a plain
    `uv run pytest` run; the marker check is what keeps the hook inert
    on every other suite. Uses `get_closest_marker()` rather than
    `"image" in item.keywords`: `item.keywords` conflates real markers
    with path-derived tokens (a test's `keywords` mapping includes
    every ancestor node's name, e.g. the literal string `"image"` from
    the `tests/image/` directory itself), so a keyword-based check
    would accidentally match every test collected from this directory
    regardless of whether it actually carries `@pytest.mark.image` —
    `get_closest_marker()` checks only real marks. Does not affect
    `IsolatedComposeStack`-based scenarios: those run under their own,
    differently-named compose project, while this hook always targets
    the primary stack resolved by `_resolve_compose_invocation()`.

    Best-effort: each diagnostic command has its own bounded timeout. A
    non-zero exit retains its output with an annotation, while a timeout
    or missing compose binary yields a placeholder note. Neither path
    masks the original test failure.
    """
    report = yield

    if not report.failed:
        return report
    if item.get_closest_marker("image") is None:
        return report

    report.sections.extend(_capture_compose_diagnostics(report.when))
    return report


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the running ``api`` container.

    Defaults to http://localhost:18000 (the host port the ``api`` service
    is published on by docker-compose.smoke.yml — deliberately not 8000,
    to avoid clashing with a local uvicorn dev server). The runner script
    (scripts/image-smoke.sh) sets IMAGE_SMOKE_BASE_URL explicitly.
    """
    return os.environ.get("IMAGE_SMOKE_BASE_URL", "http://localhost:18000")


@pytest.fixture(scope="session")
def http_client(base_url: str) -> Iterator[httpx.Client]:
    """Synchronous HTTP client pointed at the running container.

    Synchronous by design: these tests talk to a real container over the
    network, so there is no benefit to the async ASGI transport used by
    the in-process suite.
    """
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        yield client


@pytest.fixture(scope="session")
def compose_exec() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run a command inside a running compose service and return output.

    Used by later-phase assertions (e.g. running a ``sentinel`` CLI
    command inside the container). The compose invocation is read from
    env vars exported by scripts/image-smoke.sh:

    - ``COMPOSE_FILES`` — colon-separated compose file paths (defaults to
      ``docker-compose.smoke.yml``).
    - ``COMPOSE_PROJECT`` — the compose project name (defaults to
      ``sentinel-smoke``). This MUST match the project name the runner
      brought the stack up with (``scripts/image-smoke.sh`` uses
      ``-p sentinel-smoke``); otherwise ``compose exec`` would target the
      default project and fail to find the running containers.

    Returns a callable ``(service, *args, env=None, timeout=_DEFAULT_EXEC_TIMEOUT)
    -> CompletedProcess``. ``env``, when given, overrides/adds environment
    variables for that single invocation only (via ``compose exec -e
    KEY=VAL``) — it does not persist across calls or affect the
    long-running service process. Used to exercise startup validation
    (e.g. an invalid ``LOG_LEVEL``) in a fresh process without
    restarting the service.

    ``timeout`` (seconds) bounds how long the invocation may block,
    defaulting to ``_DEFAULT_EXEC_TIMEOUT``. A command that exceeds it
    fails the test immediately via ``pytest.fail`` with the service,
    command, timeout value, and any output captured before the kill —
    instead of hanging the whole suite (and, in CI, the job) until the
    external runner-level timeout intervenes.
    """
    file_args, project = _resolve_compose_invocation()

    def _exec(
        service: str,
        *args: str,
        env: dict[str, str] | None = None,
        timeout: float = _DEFAULT_EXEC_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        env_args: list[str] = []
        for key, value in (env or {}).items():
            env_args.extend(["-e", f"{key}={value}"])
        cmd = [
            *_COMPOSE_CMD,
            "-p",
            project,
            *file_args,
            "exec",
            "-T",
            *env_args,
            service,
            *args,
        ]
        return _run_compose_bounded(cmd, timeout)

    return _exec


@pytest.fixture(scope="session")
def compose_run() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run a one-shot container from a compose service's own definition.

    Distinct from ``compose_exec``: it does not target an already-running
    container, it starts a brand-new one from the service's own image,
    user, and environment (via ``compose run --rm --no-deps``), then
    removes it. Used to verify a service that has already exited by the
    time the test suite runs (e.g. the one-shot ``migrate`` service,
    which uses ``restart: "no"``) directly against its own service
    definition, rather than inferring its properties from a
    long-running sibling service that happens to share the same image.

    ``--no-deps`` skips starting/waiting on the service's own
    ``depends_on`` entries: by the time image tests run, ``postgres``
    and ``redis`` are already up (the primary stack brought up by
    ``scripts/image-smoke.sh`` established that), so re-evaluating
    those conditions here would be redundant.

    Same env var resolution, return type, and bounded-timeout contract
    as ``compose_exec`` (see above).
    """
    file_args, project = _resolve_compose_invocation()

    def _run(
        service: str,
        *args: str,
        env: dict[str, str] | None = None,
        timeout: float = _DEFAULT_EXEC_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        env_args: list[str] = []
        for key, value in (env or {}).items():
            env_args.extend(["-e", f"{key}={value}"])
        cmd = [
            *_COMPOSE_CMD,
            "-p",
            project,
            *file_args,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            *env_args,
            service,
            *args,
        ]
        return _run_compose_bounded(cmd, timeout)

    return _run


@pytest.fixture(scope="session")
def compose_restart() -> Callable[..., subprocess.CompletedProcess[str]]:
    """Restart a service in the *primary* smoke stack and wait for readiness.

    Returns a callable ``(service, *, wait_for_ready=True, timeout=60.0)
    -> CompletedProcess``. The service is stopped and brought back with
    ``up -d --wait`` by default, so a successful result means a service
    with a healthcheck is healthy and a service without one is running.
    Callers may set ``wait_for_ready=False`` only for scenarios that
    intentionally expect startup to fail and observe that failure themselves.

    A failed ``stop`` is returned immediately without attempting ``up``.
    """
    file_args, project = _resolve_compose_invocation()

    def _restart(
        service: str, *, wait_for_ready: bool = True, timeout: float = 60.0
    ) -> subprocess.CompletedProcess[str]:
        return _restart_compose_service(
            service,
            file_args=file_args,
            project=project,
            wait_for_ready=wait_for_ready,
            timeout=timeout,
        )

    return _restart


def wait_for_status(
    http_client: httpx.Client,
    *,
    expect_healthy: bool,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
) -> bool:
    """Poll ``GET /health`` on ``http_client`` until it matches
    ``expect_healthy``, or ``timeout`` seconds elapse.

    For ``expect_healthy=True``: returns ``True`` as soon as a `200`
    response is observed (the container came back up and became
    healthy in time).

    For ``expect_healthy=False``: returns ``True`` only if the
    container NEVER responds with `200` for the entire window (a
    transient failure right after `restart` is not sufficient evidence
    that startup aborted — it must stay down for the full bound).
    Connection failures (the container is not accepting connections at
    all) count as "not healthy" in both modes.
    """
    deadline = time.monotonic() + timeout
    if expect_healthy:
        while time.monotonic() < deadline:
            try:
                if http_client.get("/health", timeout=2.0).status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            time.sleep(poll_interval)
        return False

    while time.monotonic() < deadline:
        try:
            if http_client.get("/health", timeout=2.0).status_code == 200:
                return False
        except httpx.TransportError:
            pass
        time.sleep(poll_interval)
    return True


class IsolatedComposeStack:
    """An independent, isolated compose project brought up from the
    primary compose file plus a caller-supplied override — for
    scenarios that must not disturb the primary smoke stack shared by
    every other test in this suite (e.g. verifying a failed migration
    blocks API startup). Reuses the already-built image via
    ``SENTINEL_IMAGE`` (inherited from the parent process environment
    set by ``scripts/image-smoke.sh``), so no image rebuild is
    triggered.

    See the ``isolated_compose_stack`` fixture below for construction
    and guaranteed teardown.
    """

    def __init__(
        self,
        project: str,
        file_args: list[str],
        override_path: Path,
    ) -> None:
        self.project = project
        self._file_args = file_args
        self._override_path = override_path
        self._brought_up = False

    def up(
        self, override_yaml: str, *services: str
    ) -> subprocess.CompletedProcess[str]:
        """Write ``override_yaml`` to this stack's override file and run
        ``up -d --wait`` against the primary compose file plus that
        override, under this stack's unique project name.

        ``services``, when given, restricts the invocation to those
        named services (Compose still starts their transitive
        ``depends_on`` dependencies) instead of bringing up the entire
        stack. A new Compose project isolates container names,
        networks, and volumes from the primary stack — it does NOT
        isolate host port bindings. The full stack always includes
        ``api``, which publishes a host port (see
        docker-compose.smoke.yml); bringing up the full stack under a
        second project name while the primary stack is already running
        would collide on that port. A scenario that deliberately breaks
        one service (so it never becomes ready) MUST pass that
        service's name explicitly to avoid this collision — unless the
        broken service transitively blocks every path to ``api`` ever
        starting (e.g. breaking ``migrate``, which every other service
        depends on for successful completion), in which case ``api`` is
        never created regardless of the service filter.

        The caller MUST NOT treat this method's return code as a
        pass/fail signal for a deliberately-broken service without a
        container healthcheck (e.g. ``beat`` — see
        docker-compose.smoke.yml): Compose's ``--wait`` considers a
        service without a healthcheck "ready" as soon as it reports
        "running", which can race ahead of a startup failure that
        crashes the container a moment later. Use
        ``wait_until_exited()`` and ``logs()`` below for a
        deterministic outcome instead.
        """
        self._override_path.write_text(override_yaml, encoding="utf-8")
        cmd = [
            *_COMPOSE_CMD,
            "-p",
            self.project,
            *self._file_args,
            "-f",
            str(self._override_path),
            "up",
            "-d",
            "--wait",
            *services,
        ]
        self._brought_up = True
        return _run_compose_bounded(cmd, timeout=90.0)

    def exec_check(self, service: str, *args: str) -> subprocess.CompletedProcess[str]:
        """Attempt `compose exec -T <service> <args>` against this
        stack's project — used to confirm whether `service`'s container
        is actually running: `exec` fails immediately (non-zero exit,
        no such service/container) if it was never created or already
        exited, which is a more robust signal than parsing `ps` output
        without depending on the shape of `compose ps` output.
        """
        cmd = [
            *_COMPOSE_CMD,
            "-p",
            self.project,
            *self._file_args,
            "exec",
            "-T",
            service,
            *args,
        ]
        return _run_compose_bounded(cmd, timeout=10.0)

    def wait_until_exited(
        self, service: str, *, timeout: float = 45.0, poll_interval: float = 1.0
    ) -> None:
        """Poll until ``service``'s container is no longer reachable via
        ``exec_check`` (see above for why that is the selected "is it
        running" signal).

        Used to deterministically confirm that a deliberately-broken
        service's process has actually exited, instead of trusting
        ``up()``'s return code (see ``up()`` for why that is unreliable
        for a service without a healthcheck). Fails the test via
        ``pytest.fail`` if the container is still reachable after
        ``timeout`` seconds — the process under test never exited
        within the bound.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exec_check(service, "true").returncode != 0:
                return
            time.sleep(poll_interval)
        pytest.fail(
            f"service {service!r} in project {self.project!r} did not "
            f"exit within {timeout}s"
        )

    def logs(self, service: str) -> str:
        """Return ``service``'s combined stdout+stderr container logs.

        Works after the container has exited, as long as ``teardown()``
        has not run yet — Compose retains a stopped container's logs
        until the project is torn down.
        """
        cmd = [
            *_COMPOSE_CMD,
            "-p",
            self.project,
            *self._file_args,
            "logs",
            "--no-color",
            service,
        ]
        result = _run_compose_bounded(cmd, timeout=15.0)
        return result.stdout + result.stderr

    def teardown(self) -> None:
        if self._brought_up:
            cmd = [
                *_COMPOSE_CMD,
                "-p",
                self.project,
                *self._file_args,
                "down",
                "-v",
                "--remove-orphans",
            ]
            _run_compose_bounded(cmd, timeout=60.0)


@pytest.fixture
def isolated_compose_stack(tmp_path: Path) -> Iterator[IsolatedComposeStack]:
    """Provide one `IsolatedComposeStack` per test, torn down
    unconditionally on exit (even if `up()` was never called or
    failed)."""
    file_args, _ = _resolve_compose_invocation()
    stack = IsolatedComposeStack(
        project=f"sentinel-smoke-isolated-{uuid.uuid4().hex[:8]}",
        file_args=file_args,
        override_path=tmp_path / "override.yml",
    )
    try:
        yield stack
    finally:
        stack.teardown()
