# Draft: Secret Field Typing in Application Configuration

Status: **Draft — pending review**

## Problem Statement

`backend/app/config.py` declares all settings fields — including secrets
— as plain `str`. Because `Settings` inherits from Pydantic's
`BaseSettings` (a `BaseModel` subclass), the default `repr()` includes
every field's value verbatim. If the `settings` object (or any field
that holds a secret) is ever included in a traceback, a debug log
statement, an exception message captured by an error tracker, or an
accidental `model_dump()`/`dict()` call, the plaintext secret is
exposed.

Currently affected fields in `backend/app/config.py`:

| Field | Nature | Risk |
|-------|--------|------|
| `jwt_secret_key` | Symmetric JWT signing key | High — full auth bypass if leaked |
| `ibs_password` | IBS HTTP Basic Auth password | High — internal credential |
| `nvd_api_key` | NVD API key | Medium — rate-limit bypass only |
| `database_url` | Contains embedded `user:password` | Medium — DB credential |
| `redis_url` | May contain embedded credentials in production | Low today, Medium in prod |
| `celery_broker_url` | Same Redis instance, same risk profile as `redis_url` | Low today, Medium in prod |

No instance of `SecretStr` currently exists anywhere in the codebase
(verified via full-repo grep). No convention currently governs this in
`docs/conventions.md`.

## Goal

1. Retype pure-secret fields (`jwt_secret_key`, `ibs_password`,
   `nvd_api_key`) as Pydantic `SecretStr`.
2. Retype credential-bearing URL fields (`database_url`, `redis_url`,
   `celery_broker_url`) with `Field(..., repr=False)` — `SecretStr` is
   unsuitable here because downstream libraries (SQLAlchemy's
   `create_async_engine`, Celery) require a plain `str`, and wrapping
   the whole URL in `SecretStr` would also mask the non-secret parts
   (host, port, scheme) that are useful in logs.
3. Codify this as a permanent convention in `docs/conventions.md` so
   that all future secret fields added to `Settings` (e.g.
   `sso_client_secret`, `github_token`, `ibs_rabbitmq_url` — currently
   documented in `docs/configuration.md` but not yet implemented) follow
   the same pattern without requiring a design decision at
   implementation time.
4. Add regression tests that mechanically verify secrets are not
   exposed via `repr()` or `model_dump()`.

## Non-Goals

- This change does NOT introduce a `dump_redacted()` helper or any
  custom serialization method. Pydantic's default `SecretStr`
  serialization (`'**********'` in `repr()` and in `model_dump()`) is
  sufficient and is the chosen behavior (confirmed with stakeholder).
- This change does NOT modify `docs/configuration.md`. That document
  records env var name, type (as exposed to operators, e.g. "string"),
  default, and description — it is operator-facing and unaffected by
  the internal Python type used to hold the value. No row in
  `docs/configuration.md` needs to change.
- This change does NOT touch `SSO_CLIENT_SECRET`, `GITHUB_TOKEN`, or
  `IBS_RABBITMQ_URL` fields directly, because they are not yet
  implemented in `config.py` (they exist only as planned entries in
  `docs/configuration.md`). The new convention will apply to them
  automatically when they are implemented — no separate follow-up
  ticket is needed, but this draft explicitly calls it out in Step 5
  below so implementers of those features are not caught by surprise.
- This change does NOT add `SecretStr` to `cors_origins`, `app_name`,
  `debug`, `jwt_expiry_hours`, `ibs_username`, `ibs_api_url`,
  `ibs_download_base_url` — none of these carry secret material.
  `ibs_username` is a username, not a credential, and usernames for
  internal service accounts are not treated as secrets elsewhere in the
  codebase (e.g., they appear in plaintext in IBS API URLs and logs
  already).

## Design Decisions (resolved)

| Decision | Resolution |
|----------|------------|
| Field type for pure secrets | `pydantic.SecretStr` |
| Field type for credential-bearing URLs | `str` + `Field(..., repr=False)` |
| `model_dump()` behavior for `SecretStr` fields | Pydantic default (`'**********'`) — no custom override |
| Accessing the real value | Exclusively via `.get_secret_value()` |
| Convention location | `docs/conventions.md`, new `### Secret Field Typing` subsection under `## Python (Backend)`, placed immediately after `### Pydantic Conventions` (before `### Audit Trail`) |
| Scope of the convention | Applies to `backend/app/config.py` `Settings` fields only. Does not apply to Pydantic schemas in `backend/app/schemas/` (request/response models), which have their own existing rule of "never echo passwords back in responses" — a different mechanism for a different problem. This draft does not touch schemas |

## Action Plan

Execute the following steps in order. Each step is self-contained and
specifies the exact file, exact change, and exact verification.

### Step 1 — Add the convention to `docs/conventions.md`

**File**: `docs/conventions.md`

**Location**: insert a new subsection between the end of the existing
`### Pydantic Conventions` subsection (which ends with the line
`- Validate at the schema level, not in endpoints or services`) and the
start of the existing `### Audit Trail` subsection. Both are inside the
`## Python (Backend)` section.

**Exact content to insert**:

```markdown
### Secret Field Typing

Configuration fields in `backend/app/config.py` (`Settings` class) that
contain secrets MUST use Pydantic's `SecretStr` type instead of plain
`str`. This prevents accidental exposure of secret values via `repr()`,
tracebacks, debug logging, or serialization (`model_dump()` renders
`SecretStr` fields as `'**********'`).

Classification:

| Field nature | Type | Example |
|---|---|---|
| Pure secret (signing key, password, token, API key) | `SecretStr` | `jwt_secret_key: SecretStr` |
| URL that may embed credentials (`user:password@host`) | `str` with `Field(..., repr=False)` | `database_url: str = Field(default="...", repr=False)` |
| Non-secret configuration (usernames, public URLs, flags) | plain type (default) | `ibs_api_url: str = "..."` |

Rules:

- Access the real value of a `SecretStr` field exclusively via
  `.get_secret_value()`. Never rely on implicit `str()` conversion,
  which returns the masked representation (`'**********'`), not the
  real value
- Validators (`@model_validator`) that inspect a `SecretStr` field MUST
  call `.get_secret_value()` before performing checks (e.g., length
  validation, emptiness checks)
- URL fields that may embed credentials use `Field(..., repr=False)`
  rather than `SecretStr`, because downstream libraries (SQLAlchemy's
  `create_async_engine`, Celery, httpx) require a plain `str` argument.
  `repr=False` hides the field from `repr(settings)` and from
  Pydantic's default logging integrations while preserving direct
  string compatibility with those libraries
- A username field (e.g., `ibs_username`) is NOT treated as a secret
  by this convention — only the paired credential (e.g., `ibs_password`)
  is
- When adding a new field to `Settings` that holds credential material,
  apply this classification before implementation. If genuinely
  uncertain whether a value counts as a secret, treat it as a secret
- Never serialize the full `Settings` object (`model_dump()`,
  `model_dump_json()`) in API responses, health endpoints, or error
  payloads — credential-bearing URL fields remain plain strings and are
  not masked by serialization; only `repr()` is affected by
  `Field(..., repr=False)`
```

**Verification**: re-read the resulting file section to confirm:
1. The new subsection sits between `### Pydantic Conventions` and
   `### Audit Trail`
2. No existing content was altered or duplicated
3. Markdown table syntax renders correctly (3 columns, aligned pipes)

### Step 2 — Modify `backend/app/config.py`

**File**: `backend/app/config.py`

**Change 2a** — imports. Replace:

```python
from pydantic import model_validator
```

with:

```python
from pydantic import Field, SecretStr, model_validator
```

**Change 2b** — field retyping. Replace the `Database` field:

```python
    # Database
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
```

with:

```python
    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel",
        repr=False,
    )
```

Replace the `Redis` field:

```python
    # Redis
    redis_url: str = "redis://localhost:6379/0"
```

with:

```python
    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)
```

Replace the `Celery` field:

```python
    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
```

with:

```python
    # Celery
    celery_broker_url: str = Field(default="redis://localhost:6379/1", repr=False)
```

Replace the IBS password field. Current block:

```python
    # IBS Integration
    ibs_api_url: str = "https://api.suse.de"
    ibs_username: str = ""
    ibs_password: str = ""
    ibs_download_base_url: str = "https://download.suse.de/ibs"
```

New block (only `ibs_password` changes type; the other three fields are
unchanged):

```python
    # IBS Integration
    ibs_api_url: str = "https://api.suse.de"
    ibs_username: str = ""
    ibs_password: SecretStr = SecretStr("")
    ibs_download_base_url: str = "https://download.suse.de/ibs"
```

Replace the NVD API key field:

```python
    # NVD API
    nvd_api_key: str = ""
```

with:

```python
    # NVD API
    nvd_api_key: SecretStr = SecretStr("")
```

Replace the JWT secret key field:

```python
    # Security
    jwt_secret_key: str
    jwt_expiry_hours: int = 72
```

with:

```python
    # Security
    jwt_secret_key: SecretStr
    jwt_expiry_hours: int = 72
```

**Change 2c** — update `_validate_security_settings` validator. Current:

```python
    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """Fail fast on invalid security configuration."""
        if len(self.jwt_secret_key) < 32:
            msg = (
                f"Invalid JWT_SECRET_KEY: must be at least 32 characters "
                f"(got: {len(self.jwt_secret_key)})"
            )
            raise ValueError(msg)
```

New:

```python
    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """Fail fast on invalid security configuration."""
        jwt_secret_key_length = len(self.jwt_secret_key.get_secret_value())
        if jwt_secret_key_length < 32:
            msg = (
                f"Invalid JWT_SECRET_KEY: must be at least 32 characters "
                f"(got: {jwt_secret_key_length})"
            )
            raise ValueError(msg)
```

The rest of `_validate_security_settings` (the `jwt_expiry_hours`
checks) is unchanged — it does not touch `jwt_secret_key`.

**Change 2d** — update `_validate_ibs_settings` validator. Current:

```python
    @model_validator(mode="after")
    def _validate_ibs_settings(self) -> Settings:
        """Warn if IBS credentials are not configured."""
        if not self.ibs_username or not self.ibs_password:
            logger.warning(
                "IBS credentials not configured (IBS_USERNAME / IBS_PASSWORD "
                "empty). IBS-dependent fetchers will fail at runtime."
            )
        return self
```

New:

```python
    @model_validator(mode="after")
    def _validate_ibs_settings(self) -> Settings:
        """Warn if IBS credentials are not configured."""
        if not self.ibs_username or not self.ibs_password.get_secret_value():
            logger.warning(
                "IBS credentials not configured (IBS_USERNAME / IBS_PASSWORD "
                "empty). IBS-dependent fetchers will fail at runtime."
            )
        return self
```

**Verification**: after editing, the full file must import cleanly:

```bash
cd backend && uv run python -c "from app.config import Settings"
```

(This will fail without `JWT_SECRET_KEY` set in env/`.env` — that is
expected and pre-existing behavior, not a regression. Use the test
suite in Step 3 for actual verification.)

### Step 3 — Update `backend/tests/test_config.py`

**File**: `backend/tests/test_config.py`

**Change 3a** — fix the one existing assertion that reads
`jwt_secret_key` as a plain string. Current:

```python
    def test_exactly_32_chars_accepted(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        s = Settings(_env_file=None)
        assert s.jwt_secret_key == "a" * 32
```

New:

```python
    def test_exactly_32_chars_accepted(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        s = Settings(_env_file=None)
        assert s.jwt_secret_key.get_secret_value() == "a" * 32
```

No other existing test reads `jwt_secret_key`, `ibs_password`, or
`nvd_api_key` directly as a comparison target — all other tests only
set env vars and assert on `caplog` warning text or `ValidationError`
matches, which are unaffected by the type change.

**Change 3b** — add a new test class at the end of the file (after
`TestIbsCredentialWarning`), covering secret redaction:

```python
@pytest.mark.unit
class TestSecretFieldRedaction:
    """Secret fields must never leak their value via repr() or model_dump()."""

    def test_repr_does_not_expose_jwt_secret_key(self, monkeypatch):
        secret_value = "x" * 32
        monkeypatch.setenv("JWT_SECRET_KEY", secret_value)
        s = Settings(_env_file=None)
        assert secret_value not in repr(s)
        assert secret_value not in str(s)

    def test_repr_does_not_expose_ibs_password(self, monkeypatch):
        secret_value = "super-secret-ibs-password"
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_PASSWORD", secret_value)
        s = Settings(_env_file=None)
        assert secret_value not in repr(s)

    def test_repr_does_not_expose_nvd_api_key(self, monkeypatch):
        secret_value = "super-secret-nvd-api-key"
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("NVD_API_KEY", secret_value)
        s = Settings(_env_file=None)
        assert secret_value not in repr(s)

    def test_repr_does_not_expose_database_url_credentials(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://sentinel_user:sentinel_pw@db:5432/sentinel",
        )
        s = Settings(_env_file=None)
        assert "sentinel_pw" not in repr(s)

    def test_repr_does_not_expose_redis_url_credentials(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv(
            "REDIS_URL",
            "redis://:redis_secret_pw@redis:6379/0",
        )
        s = Settings(_env_file=None)
        assert "redis_secret_pw" not in repr(s)

    def test_model_dump_masks_secret_str_fields(self, monkeypatch):
        secret_value = "x" * 32
        monkeypatch.setenv("JWT_SECRET_KEY", secret_value)
        s = Settings(_env_file=None)
        dumped = s.model_dump()
        assert dumped["jwt_secret_key"].get_secret_value() == secret_value
        assert secret_value not in repr(dumped["jwt_secret_key"])
        assert secret_value not in str(dumped)

    def test_model_dump_json_masks_secret_str_fields(self, monkeypatch):
        secret_value = "x" * 32
        monkeypatch.setenv("JWT_SECRET_KEY", secret_value)
        s = Settings(_env_file=None)
        dumped_json = s.model_dump_json()
        assert secret_value not in dumped_json
```

Note on `test_model_dump_masks_secret_str_fields`: Pydantic's
`model_dump()` (Python-mode dump, not JSON-mode) returns the actual
`SecretStr` object by default, not the masked string — masking happens
in its `repr()`/`str()`, and separately in JSON-mode dumps
(`model_dump_json()` or `model_dump(mode="json")`). The test above
accounts for this by asserting on `repr()`/`str()` of the dumped value
for the Python-mode case, and on the raw JSON string for the JSON-mode
case. This is standard Pydantic v2 `SecretStr` behavior, not a defect
to fix.

**Verification**:

```bash
cd backend && uv run pytest tests/test_config.py -v
```

All tests (existing + new) must pass.

### Step 4 — Run lint and full backend test suite

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
cd backend && uv run pytest
```

Both commands must complete with no errors. The full suite run (not
just `test_config.py`) is required because `settings.database_url`,
`settings.jwt_secret_key`, etc. are consumed in other modules
(`app/database.py`, `alembic/env.py`, JWT issuing/verification code
under `app/core/`). Any code that currently treats these fields as
plain `str` in a way incompatible with `SecretStr` (e.g., string
concatenation, `.startswith()`, passing directly to a function typed
`str`) will surface as a test failure or a type-checking issue at this
step.

**Known call sites requiring no change** (validated during drafting,
listed here so the reviewer can cross-check completeness):

| File | Line (approx.) | Usage | Why unaffected |
|------|------|-------|-----------------|
| `backend/app/database.py` | 17 | `settings.database_url` passed to `create_async_engine(...)` | `database_url` stays `str` (only `repr=False` added) — no signature change |
| `backend/alembic/env.py` | 23 | `config.set_main_option("sqlalchemy.url", settings.database_url)` | Same as above |
| `backend/app/main.py` | ~20 | `settings.cors_origins` | Unrelated field, not retyped |

**Call sites requiring investigation** (not confirmed absent from the
codebase at drafting time — the implementer executing this plan MUST
grep for these before considering Step 4 complete):

```bash
cd backend && grep -rn "jwt_secret_key\|ibs_password\|nvd_api_key" app/ --include="*.py" | grep -v "app/config.py"
```

If this search returns any usage of `settings.jwt_secret_key`,
`settings.ibs_password`, or `settings.nvd_api_key` outside
`config.py` (e.g., in JWT encoding/decoding code, IBS HTTP client
authentication, or NVD fetcher request headers), each occurrence MUST
be updated to call `.get_secret_value()` before using the value in a
string context (HTTP header construction, JWT library call, etc.).
This draft does not enumerate those call sites because they belong to
features (authentication, IBS integration, NVD fetcher) outside the
scope of this configuration-only change — the implementer must locate
and fix them as part of executing this plan, using the grep command
above as the authoritative discovery step. Do not proceed to Step 5
until this grep returns zero unhandled occurrences (either zero
matches, or every match already calls `.get_secret_value()`).

### Step 5 — Note for future secret fields (no action now)

No code change is required in this step. This step is a documentation
cross-check to perform manually: when any of the following
currently-unimplemented settings are eventually added to `config.py`
(tracked as planned entries in `docs/configuration.md`), the engineer
implementing them MUST apply the convention added in Step 1 without
raising it as a new design question:

| Future field | Type to use |
|---|---|
| `sso_client_secret` | `SecretStr` |
| `github_token` | `SecretStr` |
| `ibs_rabbitmq_url` | `str` with `Field(..., repr=False)` (embeds AMQP credentials, same pattern as `database_url`) |

No file is modified in this step — it exists purely so the reviewer
confirms the convention text from Step 1 is generic enough to cover
these cases without amendment. If the reviewer determines the convention
wording does NOT clearly cover these future fields, Step 1's text
MUST be revised before this draft is considered ready for execution.

### Step 6 — Invoke relevant reviewers

After Steps 1–4 are applied (in the real spec/code, not in this draft),
invoke the following reviewers to verify correct application:

1. **`@docs-placement-reviewer`** — verify that the new "Secret Field
   Typing" convention in `docs/conventions.md` is correctly placed
   (cross-cutting Python convention, not feature-specific) and does not
   duplicate or contradict any existing rule.
2. **`@security-reviewer`** — verify the `config.py` change itself
   introduces no new vulnerability (e.g., confirm no leftover code path
   still logs a secret in plaintext, confirm `.get_secret_value()` calls
   are only used where strictly necessary and not re-exposed via a
   return value, log line, or exception message).
3. **`@test-reviewer`** — verify the new tests in
   `test_config.py` (`TestSecretFieldRedaction`) are correctly asserting
   the intended behavior and are not tautological or overly coupled to
   implementation details.

If any reviewer flags a "Needs revision" issue, fix it before
proceeding to Step 7. Minor issues should be fixed in the same change.

### Step 7 — Delete this draft file

Once Steps 1–6 are complete and all reviewer findings are resolved,
delete this file (`docs/drafts/secret-field-typing.md`). Its content
has been fully absorbed into `docs/conventions.md` (permanent
convention) and `backend/app/config.py` /
`backend/tests/test_config.py` (implementation). Retaining the draft
after execution would create a stale duplicate of information that now
lives in its proper authoritative location, violating the project's
information placement principle (Guardrail 21).

## Files Touched by This Plan (summary)

| File | Type of change |
|------|----------------|
| `docs/conventions.md` | New subsection added (Step 1) |
| `backend/app/config.py` | Field retyping + validator updates (Step 2) |
| `backend/tests/test_config.py` | One assertion fixed + new test class (Step 3) |
| *(any file found by the Step 4 grep)* | `.get_secret_value()` calls added where needed |
| `docs/drafts/secret-field-typing.md` | Deleted at the end (Step 7) |

No changes to `docs/configuration.md`, `backend/.env.example`, or any
feature specification are required — this is a pure internal-typing
hardening with no change to external behavior, env var names, defaults,
or operator-facing documentation.

## Internal Consistency Check (self-review)

- Scope is bounded: only `config.py` fields with confirmed secret
  material are retyped; fields explicitly excluded are listed with
  justification.
- The convention text in Step 1 is generic (keyed on "field nature", not
  on specific field names), so it already covers the three future
  fields listed in Step 5 without requiring future amendment — verified
  by re-reading the convention against each of the three future fields.
- Every code change in Step 2 has a matching test update in Step 3
  (validators touching `jwt_secret_key` and `ibs_password` are both
  covered).
- Step 4 explicitly acknowledges the one open unknown (whether secret
  fields are consumed elsewhere in `app/`) and gives a concrete,
  executable command to resolve it deterministically, rather than
  assuming completeness.
- The reviewer step (Step 6) targets the three review dimensions
  actually relevant to this change (placement, security, test quality)
  and omits reviewers irrelevant to a configuration-typing change (e.g.,
  `@data-model-reviewer`, `@ticket-integrity-reviewer` — no DB schema or
  ticket mutation is involved).
- The draft deletion (Step 7) is ordered last and made conditional on
  reviewer sign-off, preventing premature loss of the plan if a
  reviewer finding requires revisiting an earlier step.

## Pre-Execution Review (completed)

`@docs-placement-reviewer`, `@security-reviewer`, and `@test-reviewer`
were run against this draft before execution. Findings and resolutions:

| Reviewer | Finding | Resolution |
|----------|---------|------------|
| `@docs-placement-reviewer` | None | N/A — placement confirmed correct |
| `@security-reviewer` | Test secret value `"super-secret-jwt-key-value-1234"` is 31 characters, below the 32-char minimum enforced by `_validate_security_settings` — three tests would raise `ValidationError` before reaching their assertions | Fixed — replaced with `"x" * 32` in all three affected tests |
| `@security-reviewer` | `model_dump()`/`model_dump_json()` still expose credential-bearing URL fields in plaintext (out of scope for `repr=False`) | Addressed — added an explicit rule in the convention text (Step 1) prohibiting full `Settings` serialization in API responses, health endpoints, or error payloads |
| `@security-reviewer` | `SecretStr` truthiness footgun (`bool(SecretStr(""))` is `True`) not documented | Not addressed — already implicitly covered by the existing rule "Access the real value ... exclusively via `.get_secret_value()`". Adding a dedicated bullet was assessed as non-blocking documentation polish, deferred at author's discretion per the Insufficiency/Excess test balance in `docs/conventions.md` |
| `@security-reviewer` | `repr=False` does not protect against traceback tools that capture stack frame locals (Sentry, Datadog) | Not addressed — the convention text does not claim this protection; no change needed |
| `@test-reviewer` | Same 31-char secret value issue (Must fix) | Fixed — see above |
| `@test-reviewer` | Missing test for `redis_url`/`celery_broker_url` `repr=False` behavior | Fixed — added `test_repr_does_not_expose_redis_url_credentials` |
| `@test-reviewer` | `test_model_dump_masks_secret_str_fields` name is misleading about Pydantic v2 behavior | Not addressed — the "Note on" paragraph immediately following the test class already clarifies the exact behavior; renaming was assessed as documentation polish, not a functional gap |
| `@test-reviewer` | Minor redundancy (`str(s)` vs `repr(s)`), missing `model_dump(mode="json")` test, no per-field JSON masking coverage | Not addressed — assessed as low-value additions that would increase test maintenance burden without meaningfully increasing coverage (SecretStr masking behavior is uniform across fields) |

All "Needs revision" / "Must fix" findings have been resolved. This
draft is ready for execution (Steps 1–7).
