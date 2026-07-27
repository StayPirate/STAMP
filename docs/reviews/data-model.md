# Review: data-model

**Spec**: `docs/data-model.md`
**Last reviewed**: 2026-07-27
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### DM-GAP-01 — Product.display_name NOT NULL but no initial value during SMELT-only sync (Medium)

**Status**: RESOLVED — SMELT's `friendly_name` field added as source for `display_name` during product creation, eliminating the NOT NULL gap (2026-07-27)

---

## Coherence

### DM-COH-01 — User.active default value incomplete in data-model.md (Medium)

**Category**: Documentation inconsistency
**Status**: OPEN

The `User` table in `data-model.md` specifies `active BOOLEAN NOT NULL, DEFAULT` without stating the actual default value. Compare with `Product.active` in the same document, which correctly states `NOT NULL, DEFAULT true`. The ER diagram for the Identity domain also omits the default: `BOOLEAN active "NOT NULL"`. An implementer cannot determine from the data model alone whether the database default for `User.active` is `true` or `false`.

### DM-COH-02 — Delivery regression terminology contradicts SubmissionRequestState finality definitions (Medium)

**Category**: Terminology contradiction
**Status**: OPEN

`package-model.md` (delivery status transitions table) describes the `IN_PROGRESS → PENDING` regression condition as: "All SRs linked to this track reach a **negative final state** (`revoked` or `declined`)." However, `declined` is explicitly defined as **non-final** in both `data-model.md` (SubmissionRequestState enum: "`declined` — Request was declined. **Non-final** — can revert to `open` on reopen") and `ibs-submission-tracking.md` (Final? column: **No**). Using "final state" for `declined` directly contradicts the authoritative enum definitions. The condition itself is likely correct (delivery regresses when no SRs remain in positive states), but the terminology "negative final state" is inaccurate for `declined`.

### DM-COH-03 — Delivery regression prose includes superseded but detail explicitly excludes it (Medium)

**Category**: Internal contradiction
**Status**: OPEN

`package-model.md` (delivery regression prose) states: "`delivery_status` can regress from `IN_PROGRESS` back to `PENDING` when all submission requests linked to the track reach a negative terminal or non-final state (**`revoked`, `declined`, or `superseded`**)." However, the per-state detail section immediately below explicitly states: "**SR `superseded`**: **no regression** — a superseding SR already exists and inherits the delivery role." The summary lists `superseded` as triggering regression while the detail says it does NOT trigger regression. An implementer reading only the summary would implement the regression condition incorrectly.

---

## Design

---

## Security

---

## API Conventions

### DM-API-01 — Inconsistent path parameter name in cross-referenced endpoint (Low)

**Category**: Path naming
**Status**: OPEN

In the `Ticket` table's `cve_id` column description, `data-model.md` references the endpoint as `POST /api/v1/tickets/{id}/associate-cve`. The canonical path — as defined in both the owning spec (`docs/features/tickets/tickets.md`) and the Endpoint Permission Map (`docs/features/identity/rbac.md`) — uses `{ticket_id}`, not `{id}`. While this is a descriptive cross-reference (not an endpoint definition), the inconsistency could mislead implementers consulting the data model.
