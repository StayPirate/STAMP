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

**Category**: Source-of-truth conflict
**Status**: OPEN

`cli-reference.md` states exit codes are "defined in `docs/conventions.md`, Exit Codes" and then defines exit code 0 as "Success (includes idempotent no-ops and user-cancelled confirmations)." However, `conventions.md` defines exit code 0 as "Success (includes idempotent no-ops)" without the "user-cancelled confirmations" addition. The extension is semantically correct (backed by `cli-infrastructure.md` which defines that declining a confirmation prompt exits with code 0), but `cli-reference.md` claims to restate the `conventions.md` table while silently extending it.

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
