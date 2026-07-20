# RFC: Align Environment Variable Naming Between Spec and Code

## Problem Statement

The implementation (`backend/app/config.py`) and the specification
(`docs/configuration.md`) use different names for 5 environment
variables. This creates a situation where an operator following the
documentation would configure variables that the application ignores,
resulting in silently broken deployments.

Additionally, no convention exists to prevent this drift from
recurring.

## Root Cause

`config.py` was created in the initial scaffolding commit (Apr 2026)
with generic FastAPI boilerplate names. The feature specifications
evolved the naming and semantics over subsequent months (May-Jul 2026)
but the code was never updated to match.

## Affected Variables

| # | Current (code) | Specified (docs) | Issue |
|---|---|---|---|
| 1 | `SECRET_KEY` | `JWT_SECRET_KEY` | Different name; code has insecure default; no length validation |
| 2 | `ACCESS_TOKEN_EXPIRE_MINUTES` | `JWT_EXPIRY_HOURS` | Different name, different unit (min vs hr), different default (60 min vs 72 hr) |
| 3 | `OBS_API_URL` | `IBS_API_URL` | Wrong prefix (OBS != IBS); `OBS_*` is reserved for future OBS integration |
| 4 | `OBS_USERNAME` | `IBS_USERNAME` | Same as above |
| 5 | `OBS_PASSWORD` | `IBS_PASSWORD` | Same as above |

## Additional Gap

No convention codifies the relationship between the three configuration
artifacts (`docs/configuration.md`, `backend/app/config.py`,
`backend/.env.example`), making future drift likely.

## Decisions

1. Rename all 5 variables to match the specification
2. Remove the insecure default for `JWT_SECRET_KEY` (make it required)
3. Add startup validation for `JWT_SECRET_KEY` (>= 32 chars) and
   `JWT_EXPIRY_HOURS` (>= 1, warning if > 720)
4. Add a "Configuration Management" convention to `docs/conventions.md`
5. Fix `docs/configuration.md`: move `DATABASE_URL` to "Required
   Connection Settings"; change `IBS_USERNAME`/`IBS_PASSWORD` defaults
   from `—` to `""` (IBS is an optional integration — the app must
   start without IBS credentials)
6. Do NOT add `SESSION_MAX_LIFETIME_DAYS` or `LOGIN_*` to `config.py`
   — those features are not yet implemented; adding them now would
   create dead settings
7. Update `ibs-integration.md` to explicitly declare `""` as the default
   for `IBS_USERNAME`/`IBS_PASSWORD` (establishes the source-of-truth
   before `configuration.md` mirrors it)
8. Make `generate_openapi.py` self-contained by providing required
   settings via `os.environ.setdefault` before the app import (same
   pattern as `conftest.py`) — avoids fragile CI env var propagation

---

## Action Plan

### Step 1: Add "Configuration Management" section to `docs/conventions.md`

**File**: `docs/conventions.md`

**Location**: Insert as a new `### Configuration Management` subsection
immediately after the existing `### Timestamps & Timezones` subsection
(which ends before `## Python (Backend)`) — this places it in the
"General" section alongside other cross-cutting conventions.

**Content to insert** (after line 182, before line 184 `## Python
(Backend)`):

```markdown
### Configuration Management

Sentinel uses four configuration artifacts with distinct roles:

| Artifact | Role | Authority |
|----------|------|-----------|
| Feature spec (`Defined in` column) | Defines semantics, name, type, default, bounds | Source of truth — wins in case of conflict |
| `docs/configuration.md` | Aggregated operational index for operators | Mirrors feature specs; all artifacts MUST agree |
| `backend/app/config.py` | Implementation (Pydantic `Settings` class) | Field names are the `lower_snake_case` form of the env var name defined in the feature spec |
| `backend/.env.example` | Developer quickstart template | Subset of `config.py` fields — see inclusion criteria below |

**Invariant**: every field in `config.py` MUST correspond to an entry in
`docs/configuration.md`. A field that exists in code but not in the
registry is undocumented; a registry entry without a corresponding field
is either not-yet-implemented (acceptable during incremental development),
consumed by a specialized module outside the Settings class (e.g., Celery
app factory, subprocess environment), or a drift bug.

**`.env.example` inclusion criteria**: a variable appears in
`.env.example` if and only if a developer MUST or WILL LIKELY customize
it for local development. Variables excluded:

- Infrastructure URLs with stable defaults (e.g., `IBS_API_URL`,
  `SMELT_API_URL`) — usable only on SUSE internal network
- Fixed operational constants (e.g., `CELERY_TIMEZONE`) — must not be
  changed
- Optional API keys for external services (e.g., `NVD_API_KEY`) — empty
  default is functional for development

**Feature development workflow** (configuration aspect):

1. Define the variable in the owning feature spec (authoritative
   semantics)
2. Add an entry to `docs/configuration.md` (operator reference)
3. Implement the field in `config.py` when the feature is implemented
4. Add to `.env.example` only if it meets the inclusion criteria
```

---

### Step 1b: Update `docs/features/integrations/ibs-integration.md`

**File**: `docs/features/integrations/ibs-integration.md`

**Rationale**: The proposed Configuration Management convention establishes
that feature specs are the source of truth for variable semantics,
including defaults. Before `configuration.md` can declare `""` as the
default for `IBS_USERNAME`/`IBS_PASSWORD`, the authoritative feature spec
must declare it first.

**Modification**: Replace lines 22-23:

```markdown
  - `IBS_USERNAME`: IBS API username
  - `IBS_PASSWORD`: IBS API password
```

with:

```markdown
  - `IBS_USERNAME`: IBS API username (default: `""` — app starts without IBS credentials; IBS-dependent fetchers fail at runtime)
  - `IBS_PASSWORD`: IBS API password (default: `""` — same rationale as `IBS_USERNAME`)
```

This is consistent with Business Rule #1 (line 260): "IBS credentials
are validated at startup; warn if not configured" — which already implies
optionality.

---

### Step 2: Fix `docs/configuration.md`

**File**: `docs/configuration.md`

**Rationale**: the coherence review identified two misclassifications in
the configuration registry that must be corrected as part of this
alignment:

1. `DATABASE_URL` is in "Required Secrets" but it is a connection string
   (same nature as `REDIS_URL` and `CELERY_BROKER_URL`). The code
   provides a local-development default, which contradicts the "Required
   Secrets" contract ("app refuses to start if missing"). It belongs in
   "Required Connection Settings".
2. `IBS_USERNAME` and `IBS_PASSWORD` show `—` (no default, required) but
   the app must start without IBS credentials — IBS is an optional
   integration, and most fetchers operate independently of it. Their
   default should be `""` (empty string).

**Modification A** — Move `DATABASE_URL` from "Required Secrets" to
"Required Connection Settings":

Remove the `DATABASE_URL` row from the "Required Secrets" table (line
17), leaving only `JWT_SECRET_KEY`. Then add it to the "Required
Connection Settings" table:

```markdown
| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `DATABASE_URL` | string | `postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel` | PostgreSQL async connection string | `docs/architecture.md` |
| `REDIS_URL` | string | `redis://localhost:6379/0` | Redis URL for session cache and rate limiting | `docs/architecture.md` |
| `CELERY_BROKER_URL` | string | `redis://localhost:6379/1` | Celery task broker URL | `docs/architecture.md` |
```

**Modification B** — Change `IBS_USERNAME` and `IBS_PASSWORD` defaults
from `—` to `""`:

In the IBS section, replace:

```markdown
| `IBS_USERNAME` | string | — | IBS HTTP Basic Auth username | ...
| `IBS_PASSWORD` | string | — | IBS HTTP Basic Auth password | ...
```

with:

```markdown
| `IBS_USERNAME` | string | `""` | IBS HTTP Basic Auth username. Empty default allows app startup without IBS credentials; IBS-dependent fetchers will fail at runtime | `docs/features/integrations/ibs-integration.md` |
| `IBS_PASSWORD` | string | `""` | IBS HTTP Basic Auth password. Same rationale as `IBS_USERNAME` | `docs/features/integrations/ibs-integration.md` |
```

---

### Step 3: Modify `backend/app/config.py`

**File**: `backend/app/config.py`

**Replace the entire file content with**:

```python
"""Application configuration using pydantic-settings."""

from __future__ import annotations

import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "sentinel"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # IBS Integration
    ibs_api_url: str = "https://api.suse.de"
    ibs_username: str = ""
    ibs_password: str = ""
    ibs_download_base_url: str = "https://download.suse.de/ibs"

    # NVD API
    nvd_api_key: str = ""

    # Security
    jwt_secret_key: str
    jwt_expiry_hours: int = 72

    @model_validator(mode="after")
    def _validate_security_settings(self) -> "Settings":
        """Fail fast on invalid security configuration."""
        if len(self.jwt_secret_key) < 32:
            msg = (
                f"Invalid JWT_SECRET_KEY: must be at least 32 characters "
                f"(got: {len(self.jwt_secret_key)})"
            )
            raise ValueError(msg)
        if self.jwt_expiry_hours < 1:
            msg = (
                f"Invalid JWT_EXPIRY_HOURS: must be >= 1 "
                f"(got: {self.jwt_expiry_hours})"
            )
            raise ValueError(msg)
        if self.jwt_expiry_hours > 720:
            logger.warning(
                "JWT_EXPIRY_HOURS is set to %d (>720 hours). "
                "Long-lived tokens increase the window of exposure "
                "if a token is compromised.",
                self.jwt_expiry_hours,
            )
        return self

    @model_validator(mode="after")
    def _validate_ibs_settings(self) -> "Settings":
        """Warn if IBS credentials are not configured."""
        if not self.ibs_username or not self.ibs_password:
            logger.warning(
                "IBS credentials not configured (IBS_USERNAME / IBS_PASSWORD "
                "empty). IBS-dependent fetchers will fail at runtime."
            )
        return self


settings = Settings()
```

**Changes explained**:

- `secret_key` → `jwt_secret_key` (no default — required field)
- `access_token_expire_minutes` → `jwt_expiry_hours` (default: 72)
- `obs_api_url` → `ibs_api_url` (default: `https://api.suse.de`,
  matching spec)
- `obs_username` → `ibs_username`
- `obs_password` → `ibs_password`
- Added `_validate_security_settings` validator for startup validation
  (>= 32 chars, >= 1 hour) and warning when > 720 hours (per
  `configuration.md`)
- Added `_validate_ibs_settings` validator: logs WARNING at startup
  when IBS credentials are empty (per `ibs-integration.md` business
  rule #1)
- Added `import logging` and module-level `logger` for the warnings

**Not changed**:

- `database_url` — keeps local development default (Step 2 moves it
  to "Required Connection Settings" in `configuration.md` to match)
- `app_name`, `debug`, `cors_origins` — unchanged
- `nvd_api_key` — unchanged (optional, empty default)

---

### Step 4: Modify `backend/.env.example`

**File**: `backend/.env.example`

**Replace the entire file content with**:

```
# Security (REQUIRED — no default; app refuses to start without this)
JWT_SECRET_KEY=dev-only-not-for-production-use-min-32-chars

# JWT token lifetime in hours (default: 72)
JWT_EXPIRY_HOURS=72

# Database
DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel

# Redis
REDIS_URL=redis://localhost:6379/0

# Celery
CELERY_BROKER_URL=redis://localhost:6379/1

# CORS
CORS_ORIGINS=["http://localhost:5173"]

# Debug
DEBUG=true
```

**Changes explained**:

- `SECRET_KEY` → `JWT_SECRET_KEY` with a clearly-named dev-only value
  (44 chars, satisfies >= 32 requirement)
- `ACCESS_TOKEN_EXPIRE_MINUTES=60` → `JWT_EXPIRY_HOURS=72`
- Removed `OBS_API_URL`, `OBS_USERNAME`, `OBS_PASSWORD` — per the new
  convention, IBS infrastructure URLs with stable defaults do not
  belong in `.env.example` (only usable on SUSE internal network,
  developers outside that network cannot use them regardless)
- Removed `NVD_API_KEY` — optional, empty default is functional for
  development (per convention: optional API keys excluded)
- `JWT_SECRET_KEY` placed first with a prominent comment explaining it
  is required

---

### Step 5: Update `backend/tests/conftest.py`

**File**: `backend/tests/conftest.py`

**Rationale**: With `jwt_secret_key` now required (no default), the
Settings class will raise a `ValidationError` when imported unless the
variable is provided. Tests import `app.main` which imports
`app.config`, triggering Settings instantiation. We need to ensure the
test environment provides this value.

**Modification**: Insert the following lines between the third-party
imports (after line 19, the closing `)` of the sqlalchemy import) and
the app-local imports (before line 21, `from app.database import ...`):

```python

# Provide required settings for test environment (must precede app imports)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-min-32-chars")

```

This placement is semantically clear: "before importing the app, set the
required environment variable." It uses `setdefault` so CI or developer
overrides still take precedence. The value is 47 characters (satisfies
>= 32 validation).

**Linting**: inserting an executable statement between import groups
triggers ruff's **E402** rule ("module level import not at top of file")
on the subsequent local imports (`from app.database ...`, `from
app.main ...`). This is a known, legitimate pattern for test
configuration. Add a targeted `per-file-ignores` in `pyproject.toml`
(see Step 5a below) rather than suppressing the entire rule or adding
inline `# noqa` comments.

---

### Step 5a: Modify `backend/pyproject.toml`

**File**: `backend/pyproject.toml`

**Modification A — per-file-ignores for conftest.py**:

Step 5 introduces an executable statement before local imports in
`conftest.py`. The project has `"E"` in ruff's select rules, which
includes E402. Rather than excluding E402 globally (it is useful
everywhere else) or cluttering import lines with `# noqa` comments, a
targeted `per-file-ignores` is the idiomatic ruff solution.

Add the following section after the existing `[tool.ruff.lint.isort]`
block (after line 56):

```toml

[tool.ruff.lint.per-file-ignores]
"tests/conftest.py" = ["E402"]
```

**Modification B — remove `app/config.py` from coverage omit**:

Step 5b adds explicit unit tests for `config.py`. The current omit list
excludes it from coverage reporting (comment: "env-driven, tested via
integration"), which means the new tests would not contribute to the
coverage metric. Remove the `"app/config.py",` line from the
`[tool.coverage.run]` omit list.

Replace:

```toml
[tool.coverage.run]
source = ["app"]
omit = [
    "*/tests/*",
    "*/alembic/*",
    "app/config.py",
    "app/database.py",
]
```

with:

```toml
[tool.coverage.run]
source = ["app"]
omit = [
    "*/tests/*",
    "*/alembic/*",
    "app/database.py",
]
```

---

### Step 5b: Add tests for Settings validation

**File**: `backend/tests/test_config.py` (new file)

**Rationale**: Step 3 introduces a `model_validator` with startup
rejection logic (key too short, expiry too low) and a warning path
(expiry > 720, empty IBS credentials). Per Guardrail 6, new code must
have test coverage.

```python
"""Tests for Settings startup validation (backend/app/config.py)."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.unit
class TestJwtSecretKeyValidation:
    """JWT_SECRET_KEY startup validation."""

    def test_missing_jwt_secret_key_raises(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_short_jwt_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(_env_file=None)

    def test_31_chars_jwt_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 31)
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(_env_file=None)

    def test_exactly_32_chars_accepted(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        s = Settings(_env_file=None)
        assert s.jwt_secret_key == "a" * 32


@pytest.mark.unit
class TestJwtExpiryValidation:
    """JWT_EXPIRY_HOURS startup validation."""

    def test_zero_expiry_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "0")
        with pytest.raises(ValidationError, match="must be >= 1"):
            Settings(_env_file=None)

    def test_negative_expiry_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "-1")
        with pytest.raises(ValidationError, match="must be >= 1"):
            Settings(_env_file=None)

    def test_excessive_expiry_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "721")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert ">720 hours" in caplog.text

    def test_720_does_not_warn(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "720")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert ">720 hours" not in caplog.text


@pytest.mark.unit
class TestIbsCredentialWarning:
    """IBS credential startup warning."""

    def test_empty_ibs_credentials_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "")
        monkeypatch.setenv("IBS_PASSWORD", "")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" in caplog.text

    def test_only_username_empty_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "")
        monkeypatch.setenv("IBS_PASSWORD", "secret")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" in caplog.text

    def test_only_password_empty_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "jdoe")
        monkeypatch.setenv("IBS_PASSWORD", "")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" in caplog.text

    def test_configured_ibs_credentials_no_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "jdoe")
        monkeypatch.setenv("IBS_PASSWORD", "secret-password-here")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" not in caplog.text
```

**Note**: all tests use `Settings(_env_file=None)` to prevent
interference from any `.env` file on the developer's filesystem.
`pydantic-settings` reads `.env` as fallback after environment
variables — without `_env_file=None`, a test like
`test_missing_jwt_secret_key_raises` would silently pass if no `.env`
exists but fail if the developer has `backend/.env` with
`JWT_SECRET_KEY` set (copied from `.env.example`). The `_env_file=None`
argument disables file-based loading for the instance, ensuring tests
depend only on `os.environ` (controlled by `monkeypatch`).

---

### Step 6: Make `generate_openapi.py` self-contained

**File**: `backend/scripts/generate_openapi.py`

**Rationale**: The script imports `app.main` which triggers `Settings()`
instantiation. With `jwt_secret_key` now required, the script would fail
without the env var — but it never uses the JWT secret (its purpose is
purely to extract OpenAPI metadata from route decorators). Rather than
requiring every CI workflow that imports the app to carry `JWT_SECRET_KEY`,
the script provides its own dummy value before the import.

This is the same pattern used in `conftest.py` (Step 5) and makes the
script self-contained: future additions of required settings only need a
line here, not CI workflow changes.

**Replace the entire file content with**:

```python
#!/usr/bin/env python3
"""Generate OpenAPI JSON schema from the FastAPI application.

This script imports the FastAPI app and dumps its OpenAPI schema to stdout
as formatted JSON. It does not require a running server, database, or Redis
connection — FastAPI builds the schema statically from route decorators.

Usage:
    python scripts/generate_openapi.py > openapi.json
"""

from __future__ import annotations

import json
import os
import sys

# Provide required settings for schema generation (no runtime needed).
# This must precede the app import which triggers Settings() instantiation.
os.environ.setdefault(
    "JWT_SECRET_KEY", "openapi-schema-generation-only-not-for-runtime-use"
)

from app.main import app  # noqa: E402


def main() -> None:
    """Print the OpenAPI schema as formatted JSON to stdout."""
    schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
```

**Changes explained**:

- Added `import os` and `os.environ.setdefault("JWT_SECRET_KEY", ...)`
  before the `app.main` import
- The dummy value is 53 characters (satisfies >= 32 validation)
- Added `# noqa: E402` on the deferred import (ruff would flag it)
- No changes to `deploy-api-docs.yml` needed — the script is now
  self-sufficient

---

### Step 6b: Update CI workflow environment

**File**: `.github/workflows/ci.yml`

**Rationale**: The CI `backend-test` job runs `alembic upgrade head &&
alembic check` outside of pytest (does not pass through `conftest.py`).
With `JWT_SECRET_KEY` now required, we must provide it in CI for the
Alembic steps.

**Modification**: In the `env:` block of the test job (after line 77
`CELERY_BROKER_URL`), add:

```yaml
        JWT_SECRET_KEY: ci-test-secret-key-not-for-production-min-32-chars
```

**Note**: The `conftest.py` `setdefault` covers pytest execution, but the
Alembic step needs the env var explicitly.

---

### Step 7: Verify no other references to old variable names exist

**Verification command** (to be run after applying Steps 2-5):

```bash
cd /home/crazybyte/Workspace/Sentinel
grep -r "SECRET_KEY\|ACCESS_TOKEN_EXPIRE\|OBS_API_URL\|OBS_USERNAME\|OBS_PASSWORD" \
  --include="*.py" --include="*.yml" --include="*.yaml" --include="*.env*" \
  --include="*.ini" --include="*.toml" \
  backend/ .github/ \
  | grep -v "JWT_SECRET_KEY\|# OBS" | grep -v ".pyc"
```

**Expected result**: no matches. If any match is found, update that
reference to use the new name.

Also verify the documentation does not reference the old code names:

```bash
grep -r "SECRET_KEY\b" docs/ | grep -v "JWT_SECRET_KEY" | grep -v "drafts/"
grep -r "ACCESS_TOKEN_EXPIRE" docs/ | grep -v "drafts/"
grep -r "OBS_API_URL\|OBS_USERNAME\|OBS_PASSWORD" docs/ | grep -v "drafts/" | grep -v "would be"
```

**Expected result**: no matches outside this draft. The one reference
to `OBS_API_URL`/`OBS_USERNAME`/`OBS_PASSWORD` in
`docs/features/integrations/ibs-integration.md` (line 279) is
intentional — it describes the future OBS public integration, which
correctly uses the `OBS_*` prefix for a different system.

---

### Step 8: Run tests locally

```bash
cd backend && pytest
```

**Expected**: all tests pass. The `conftest.py` `setdefault` provides
`JWT_SECRET_KEY` for the test environment. No test references the old
variable names (confirmed by grep in analysis phase).

---

### Step 9: Run linter

```bash
cd backend && ruff check . && ruff format --check .
```

**Expected**: no violations. If `ruff format` reports formatting
differences, run `ruff format .` and include in the commit.

---

### Step 10: Commit

Single commit with message:

```
refactor: align env var naming between spec and code

Rename environment variables in backend/app/config.py and
backend/.env.example to match the authoritative names defined in
feature specifications (as indexed in docs/configuration.md):

- SECRET_KEY → JWT_SECRET_KEY (now required, >= 32 chars validated)
- ACCESS_TOKEN_EXPIRE_MINUTES → JWT_EXPIRY_HOURS (default: 72)
- OBS_API_URL → IBS_API_URL (default: https://api.suse.de)
- OBS_USERNAME → IBS_USERNAME
- OBS_PASSWORD → IBS_PASSWORD

Update ibs-integration.md to explicitly declare default "" for
IBS_USERNAME/IBS_PASSWORD (source-of-truth for configuration.md).

Fix docs/configuration.md: move DATABASE_URL to "Required Connection
Settings" (has a local-dev default), change IBS_USERNAME/IBS_PASSWORD
defaults from required to empty string (IBS is an optional integration).

Add "Configuration Management" convention to docs/conventions.md
defining the relationship between configuration artifacts.

Make generate_openapi.py self-contained (setdefault for JWT_SECRET_KEY
before app import — schema generation does not need runtime secrets).

Update test infrastructure (conftest.py, ci.yml) to provide the
now-required JWT_SECRET_KEY. Add Settings validation tests.
Remove app/config.py from coverage omit list.
```

Files in commit:

- `docs/features/integrations/ibs-integration.md`
- `docs/configuration.md`
- `docs/conventions.md`
- `backend/app/config.py`
- `backend/.env.example`
- `backend/pyproject.toml`
- `backend/scripts/generate_openapi.py`
- `backend/tests/conftest.py`
- `backend/tests/test_config.py`
- `.github/workflows/ci.yml`

---

### Step 11: Run reviewers

After the commit is applied, invoke the following reviewers on the
relevant specs to verify no issues were introduced:

1. **`@spec-coherence-reviewer`** on `docs/configuration.md` — verify
   that the new convention in `docs/conventions.md` does not contradict
   any existing configuration references across feature specs

2. **`@docs-placement-reviewer`** on `docs/conventions.md` — verify
   that the "Configuration Management" section is correctly placed (it
   is a cross-cutting development convention, not feature-specific)

3. **`@test-reviewer`** on `backend/tests/conftest.py` — verify that
   the `setdefault` pattern is appropriate for providing required
   settings in tests

4. **`@cicd`** on `.github/workflows/ci.yml` — verify that the added
   environment variable does not break the CI pipeline

---

### Step 12: Delete this draft

Once all steps are applied and reviewers confirm no issues:

```bash
rm docs/drafts/align-env-vars-spec-to-code.md
```

Include the deletion in the commit (or as a separate `chore:` commit
if the main refactor is already merged).

---

## Out of Scope

The following are explicitly NOT part of this change:

- Adding `SESSION_MAX_LIFETIME_DAYS`, `LOGIN_MAX_ATTEMPTS`,
  `LOGIN_LOCKOUT_MINUTES` to `config.py` — these features are not yet
  implemented
- Adding SSO variables (`SSO_*`) to `config.py` — SSO is not yet
  implemented
- Adding `GITHUB_TOKEN`, `GIT_CLONE_BASE_DIR`, `SUSE_CA_CERT_PATH` to
  `config.py` — the fetchers that consume them are not yet implemented
- Making `DATABASE_URL` required (no default) — the local development
  default is intentional; Step 2 moves it to the correct spec section

## Risk Assessment

**Low risk**. Justification:

- The old variable names (`SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`,
  `OBS_*`) are not referenced anywhere in the codebase except
  `config.py` and `.env.example` (confirmed by grep)
- No test uses these variables directly
- No CI workflow references these variables
- The application has no downstream consumers that depend on the old
  names
- The only user-facing impact is that anyone with a custom `.env` file
  needs to update variable names — but since the project is pre-1.0
  with no production deployment, this affects only developers
