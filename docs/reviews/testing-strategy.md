# Review: testing-strategy

**Spec**: `docs/features/platform/testing-strategy.md`
**Last reviewed**: 2026-07-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TST-GAP-01 — Redis Test Infrastructure Undefined (Medium)

**Status**: RESOLVED — Added the Redis Strategy, planned `redis_client` fixture contract, and local/CI/test-author guidance to `testing-strategy.md` (2026-07-18)

---

## Coherence

_No findings._

---

## Design

### TST-DES-02 — Coverage Configuration documents non-existent config.py omission (Low)

**Category**: Specification accuracy
**Status**: OPEN

The Coverage Configuration section (line 372) lists `app/config.py` as omitted from coverage measurement. However, the actual `pyproject.toml` does NOT omit `config.py` — only `*/tests/*`, `*/alembic/*`, and `app/database.py` are in the omit list. The spec is factually wrong about what is omitted.

This matters because a developer reading the spec might "correct" `pyproject.toml` to match the documented omissions, which would remove coverage enforcement from `config.py` — a module containing security-critical validators (JWT secret length, password policy, credential handling). The fix is to remove `app/config.py` from the documented omissions list in the spec.

### TST-DES-01 — Concurrency Testing Infrastructure Gap (Medium)

**Status**: RESOLVED — Documented db_session_factory fixture and concurrency testing pattern in Database Strategy (2026-07-18)

---

## Security

_No findings._

---

## API Conventions

_No findings — spec does not define API endpoints._
