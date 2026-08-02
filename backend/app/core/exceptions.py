"""Shared application exception hierarchy.

`ServiceError` is the common root for all exceptions raised by
service-layer modules and propagated to API handlers. Per
`docs/conventions.md` (Service Exception Conventions), every module's
own base class (e.g., `TicketServiceError`, `UserServiceError`)
inherits from `ServiceError`, and exceptions shared across multiple
modules inherit from `ServiceError` directly.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Root exception for all service-layer errors."""


class TLSConfigurationError(ServiceError):
    """Raised when the configured SUSE CA certificate is unusable.

    This signals a configuration error (corrupt or unparseable
    certificate file) — not a transient condition. Retrying without
    fixing the file is pointless. See
    `docs/features/platform/networking.md` (TLS Trust Store
    Configuration).
    """

    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"Failed to load SUSE CA certificate from '{path}': {detail}")
