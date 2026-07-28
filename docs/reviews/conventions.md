# Review: conventions

**Spec**: `docs/conventions.md`
**Last reviewed**: 2026-07-27
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

No findings.

---

## Coherence

### CONV-COH-001 — Exit code 0 description drift vs cli-reference.md (Low)

**Status**: RESOLVED — Updated exit code 0 row in conventions.md Exit Codes table to include user-cancelled confirmations, matching cli-reference.md (2026-07-28)

---

## Design

No findings.

---

## Security

No findings.

---

## API Conventions

### CONV-API-001 — resolve_user_identifier uses HTTPException instead of service exception (Low)

**Status**: RESOLVED — Fixed: replaced HTTPException with UserNotFoundError in reference implementation (2026-07-27)
