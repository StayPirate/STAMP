# Review: deployment

**Spec**: `docs/deployment.md`
**Last reviewed**: 2026-07-27
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### DEP-GAP-001 — Failed Database Migration: No Recovery Procedure (High)

**Category**: Error and failure paths
**Status**: OPEN

The spec documents that migrations must run as a separate step before deploying new code (as a Kubernetes Job or explicit command). However, there is no documented recovery procedure when a migration fails midway through execution. An operator facing a partially-applied migration (e.g., DDL succeeded but `alembic_version` stamp failed due to a transient connection drop) has no guidance on: (1) how to detect partial vs. complete failure (check `alembic_version` table), (2) whether to attempt `alembic downgrade` (requires migrations to be reversible — no convention stated), (3) whether the old application version can still function against the partially-migrated schema, (4) whether to fix-forward by addressing the root cause and re-running. This is a common production scenario — not an edge case — and forces operators to make unsupported decisions under pressure.

### DEP-GAP-002 — Application Version Rollback Procedure (High)

**Category**: Error and failure paths
**Status**: OPEN

The spec states that production is deployed manually from version tags (`v*`) but provides no guidance on rolling back to a previous version when a critical bug is discovered post-deployment. An operator attempting a rollback needs to know: (1) whether deploying an older container image is safe when the database schema has already been migrated forward (schema backward-compatibility policy is never stated), (2) whether `alembic downgrade` is supported for production migrations (reversibility convention is not declared), (3) what the expected rollback sequence is (stop services → downgrade DB → deploy old image, or just deploy old image if schema is backward-compatible). Without this guidance, an operator could deploy code incompatible with the current schema, causing service failures or data corruption.

---

## Coherence

_No findings._

---

## Design

### DEP-DES-001 — No Zero-Downtime JWT Secret Rotation Strategy (Medium)

**Category**: Operational resilience
**Status**: OPEN

The spec acknowledges that `JWT_SECRET_KEY` rotation invalidates all active sessions and recommends planning for an off-peak maintenance window. However, for a security platform used by vulnerability analysts during active incident response, a forced mass-logout during a CVE emergency is operationally disruptive — and emergency key rotation (suspected compromise) is precisely when "off-peak" planning is impossible. A dual-key verification scheme (`JWT_SECRET_KEY` + `JWT_SECRET_KEY_PREVIOUS`) would eliminate this limitation: the API verifies tokens against the primary key first, falls back to the previous key; new tokens are always signed with the primary key. During rotation, set the old key as `_PREVIOUS` and deploy the new key as primary — all existing sessions remain valid until natural expiry. Implementation cost is ~10 lines of verification code and one additional env var. The operational benefit during incident response is significant.

---

## Security

_No findings._

---

## API Conventions

_No findings._
