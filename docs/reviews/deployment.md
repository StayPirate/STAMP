# Review: deployment

**Spec**: `docs/deployment.md`
**Last reviewed**: 2026-07-27
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### DEP-GAP-001 — Failed Database Migration: No Recovery Procedure (High)

**Status**: RESOLVED — Fix-forward migration recovery policy, PostgreSQL transactional DDL documentation, and stop-the-world deployment model added to deployment.md; reversibility note clarified in conventions.md (2026-07-28)

### DEP-GAP-002 — Application Version Rollback Procedure (High)

**Status**: RESOLVED — Post-deployment recovery procedure added: fix-forward with hotfix release, explicit prohibition of previous image rollback, pre-deployment backup recommendation (2026-07-28)

### DEP-GAP-003 — CLI operational access pattern undocumented (Medium)

**Category**: Coverage gap
**Status**: OPEN

The deployment documentation tells operators that CLI commands "require direct shell access to the host or container running the backend" (prerequisite from cli-reference.md) but does not document the recommended pattern for obtaining that access in staging or production environments. The `sentinel` console script is available in PATH inside any container (all process roles share the same Docker image), but the documentation does not specify whether operators should: (a) `docker exec` into a running API container, (b) launch a one-off container (`docker run --rm --env-file .env sentinel:latest sentinel <command>`), or (c) use a dedicated CLI pod/container. The one-off container pattern is demonstrated in the "Database Migrations" section for Alembic migrations but is not generalized for CLI commands. Given that CLI commands need database access and potentially Redis access (for some operations), and that the deployment target (Kubernetes vs Docker Compose) remains undecided (noted in deployment.md's staging section), documenting at least the Docker Compose and the future Kubernetes patterns would allow operators to use CLI commands without guessing.

---

## Coherence

_No findings._

---

## Design

### DEP-DES-001 — No Zero-Downtime JWT Secret Rotation Strategy (Medium)

**Status**: RESOLVED — By design: immediate session invalidation on JWT secret rotation is intentional; API keys use independent SHA-256 hash validation and are unaffected. Dual-key rotation declined as unnecessary complexity (2026-07-28)

---

## Security

_No findings._

---

## API Conventions

_No findings._
