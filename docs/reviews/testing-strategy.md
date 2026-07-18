# Review: testing-strategy

**Spec**: `docs/features/platform/testing-strategy.md`
**Last reviewed**: 2026-07-18
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TST-GAP-01 — Redis Test Infrastructure Undefined (Medium)

**Category**: Boundary conditions / Configuration
**Status**: OPEN

The spec explicitly promises "Redis available via fixture when needed" in the Tier 2 Integration Test properties table. However, the Fixture Catalog lists no Redis fixture — neither in Available Fixtures nor in Planned Fixtures. No provisioning strategy is defined (testcontainers for Redis, consistent with PostgreSQL? A `TEST_REDIS_URL` environment variable fallback?). No test isolation strategy for Redis is specified — PostgreSQL uses per-test transaction rollback, but Redis has no equivalent mechanism (flush between tests? separate database index? key prefix?). Multiple features depend on Redis: session liveness (`session_liveness:*`), login lockout (`login_attempts:*`), fetch deduplication (`fetch_pending:*`), and CVSS recalculation lock (`cvss_recalc_active`). A developer implementing tests for any of these features has no fixture to import and no guidance on provisioning or isolation, forcing ad-hoc solutions that risk inconsistency across the test suite.

---

## Coherence

_No findings._

---

## Design

### TST-DES-01 — Concurrency Testing Infrastructure Gap (Medium)

**Category**: Design gap
**Status**: OPEN

The Mandatory Test Scenarios section mandates testing "concurrent modifications" as an edge case for API Endpoints. However, the per-test transaction rollback pattern means all database operations within a single test share a single connection and outer transaction. `SELECT ... FOR UPDATE` locks acquired within the same transaction are no-ops — they do not block. The project's correctness model depends heavily on `FOR UPDATE` locking (per `docs/conventions.md`, every public function in a centralized mutation module MUST acquire a row-level lock), but the testing infrastructure provides no mechanism, pattern, or escape hatch for tests that need multiple independent transactions to verify lock serialization. A documented concurrency testing pattern (e.g., a `db_engine` fixture creating independent connections outside the rollback pattern, with explicit setup/teardown) would fill this gap.

---

## Security

_No findings._

---

## API Conventions

_No findings — spec does not define API endpoints._
