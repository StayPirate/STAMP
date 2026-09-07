#!/usr/bin/env python3
"""Run a command with a timeout and terminate its entire process group."""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from types import FrameType
from typing import NoReturn

TIMEOUT_STATUS = 124
DEFAULT_GRACE_PERIOD = 10.0
GROUP_POLL_INTERVAL = 0.05
KILL_DRAIN_TIMEOUT = 5.0


@dataclass(frozen=True)
class SupervisorOptions:
    """Validated command supervision options."""

    label: str
    timeout: float
    grace_period: float
    command: list[str]


class SupervisorInterruptedError(Exception):
    """Raised when the supervisor receives an operator termination signal."""

    def __init__(self, signal_number: int) -> None:
        self.signal_number = signal_number


def _positive_finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def _nonnegative_finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number")
    return number


def _parse_args(argv: Sequence[str] | None) -> SupervisorOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="name of the supervised stage")
    parser.add_argument(
        "--timeout",
        required=True,
        type=_positive_finite,
        help="maximum command runtime in seconds",
    )
    parser.add_argument(
        "--grace-period",
        type=_nonnegative_finite,
        default=DEFAULT_GRACE_PERIOD,
        help="seconds to wait between TERM and KILL (default: %(default)s)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command and arguments, conventionally preceded by --",
    )
    args = parser.parse_args(argv)

    command: list[str] = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after the options")

    return SupervisorOptions(
        label=args.label,
        timeout=args.timeout,
        grace_period=args.grace_period,
        command=command,
    )


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group(
    process: subprocess.Popen[bytes], process_group_id: int, grace_period: float
) -> bool:
    deadline = time.monotonic() + grace_period
    while True:
        process.poll()
        if not _process_group_exists(process_group_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(GROUP_POLL_INTERVAL, remaining))


def _signal_process_group(process_group_id: int, signal_number: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process_group_id, signal_number)


def _raise_interrupted(signal_number: int, frame: FrameType | None) -> NoReturn:
    del frame
    raise SupervisorInterruptedError(signal_number)


def _ignore_interrupt_signals() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _terminate_process_group(
    process: subprocess.Popen[bytes], *, label: str, grace_period: float, reason: str
) -> None:
    process_group_id = process.pid
    _signal_process_group(process_group_id, signal.SIGTERM)
    if _wait_for_process_group(process, process_group_id, grace_period):
        return

    print(
        f"{reason} escalation: label={label!r} grace_period={grace_period:g}s; "
        "sending KILL to process group",
        file=sys.stderr,
        flush=True,
    )
    _signal_process_group(process_group_id, signal.SIGKILL)
    if not _wait_for_process_group(process, process_group_id, KILL_DRAIN_TIMEOUT):
        print(
            f"{reason} cleanup incomplete: label={label!r}; "
            "process group still exists after KILL",
            file=sys.stderr,
            flush=True,
        )


def _status_from_returncode(returncode: int) -> int:
    if returncode >= 0:
        return returncode
    return 128 - returncode


def supervise(options: SupervisorOptions) -> int:
    """Run one command and enforce its timeout across a dedicated process group."""
    try:
        process = subprocess.Popen(options.command, start_new_session=True)
    except OSError as exc:
        print(
            f"Cannot start {options.label!r}: command={shlex.join(options.command)}: "
            f"{exc}",
            file=sys.stderr,
        )
        return 127 if isinstance(exc, FileNotFoundError) else 126

    previous_sigint = signal.signal(signal.SIGINT, _raise_interrupted)
    previous_sigterm = signal.signal(signal.SIGTERM, _raise_interrupted)
    try:
        try:
            return _status_from_returncode(process.wait(timeout=options.timeout))
        except subprocess.TimeoutExpired:
            print(
                f"Timeout: label={options.label!r} timeout={options.timeout:g}s "
                f"command={shlex.join(options.command)}",
                file=sys.stderr,
                flush=True,
            )
            _ignore_interrupt_signals()
            _terminate_process_group(
                process,
                label=options.label,
                grace_period=options.grace_period,
                reason="Timeout",
            )
            return TIMEOUT_STATUS
        except SupervisorInterruptedError as exc:
            _ignore_interrupt_signals()
            print(
                f"Interrupted: label={options.label!r} signal={exc.signal_number} "
                f"command={shlex.join(options.command)}",
                file=sys.stderr,
                flush=True,
            )
            _terminate_process_group(
                process,
                label=options.label,
                grace_period=options.grace_period,
                reason="Interruption",
            )
            return 128 + exc.signal_number
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


def main(argv: Sequence[str] | None = None) -> int:
    return supervise(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
