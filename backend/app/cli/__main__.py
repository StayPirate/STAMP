"""`python -m app.cli` entry point.

Delegates to the same `main()` used by the installed `sentinel` console
script — see `docs/features/platform/cli-infrastructure.md` (Package
Entry Point & Invocation).
"""

from __future__ import annotations

from app.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
