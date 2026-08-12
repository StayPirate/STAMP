"""Test-only subprocess entry point for SIGINT/SIGTERM readiness tests.

Not part of the packaged CLI (`app.cli`) — invoked directly via
`subprocess.Popen([sys.executable, <this file>])` from
`test_main.py::test_signal_produces_documented_exit_code`.

Installs the exact same signal handlers `app.cli.main()` installs
(`install_signal_handlers()` is idempotent — `main()` below installs
them again, harmlessly) as this script's own first statement, then
prints an unbuffered readiness marker so the parent test process can
wait for a deterministic point instead of guessing with a fixed sleep —
see `docs/features/platform/testing-strategy.md` (CLI Commands,
"observable readiness point"). Execution then proceeds through the
real, unmodified `app.cli.main()` entry point (dispatching to
`manage-user list`), so the rest of this process's behavior (bootstrap,
dispatch, exit-code mapping) is identical to `sentinel manage-user
list` / `python -m app.cli manage-user list`.
"""

from __future__ import annotations

import sys

from app.cli import main
from app.cli._runtime import install_signal_handlers

install_signal_handlers()
print("READY", flush=True)

sys.argv = ["sentinel", "manage-user", "list"]
main()
