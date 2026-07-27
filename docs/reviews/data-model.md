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

**Status**: RESOLVED — Added explicit DEFAULT true for User.active in table and ER diagram (2026-07-27)

### DM-COH-02 — Delivery regression terminology contradicts SubmissionRequestState finality definitions (Medium)

**Status**: RESOLVED — Delivery regression terminology rewritten using positive operational criterion; removed incorrect "final state" label for declined (2026-07-27)

### DM-COH-03 — Delivery regression prose includes superseded but detail explicitly excludes it (Medium)

**Status**: RESOLVED — Resolved together with DM-COH-02; superseded no longer listed as regression trigger in summary prose (2026-07-27)

---

## Design

---

## Security

---

## API Conventions

### DM-API-01 — Inconsistent path parameter name in cross-referenced endpoint (Low)

**Status**: RESOLVED — Path parameter corrected from {id} to {ticket_id} in cross-reference (2026-07-27)
