# Review: product-lifecycle-transitions

**Spec**: `docs/features/packages/product-lifecycle-transitions.md`
**Last reviewed**: 2026-06-02
**Reviewers**: Manual (audit log contract consistency check)

---

## Manual Findings

### PLT-MAN-01 — Structured data placed in `comment` column instead of `detail` JSONB (Medium)

**Category**: Spec inconsistency
**Status**: OPEN

`product-lifecycle-transitions.md` (lines 142-148) populates the
`comment` column with structured data for `product_eligibility_changed`
and `product_excluded` audit events. Examples:

- `"track_name package_name product_id reactive_ltss"`
- `"track_name package_name product_id eol"`

This contradicts the authoritative contract in
`ticket-audit-log.md` (line 44), which specifies:

- `product_eligibility_changed`: `comment = NULL`, structured context
  belongs in `detail` JSONB
- Rule (lines 67-70): "`comment` is used exclusively for free-text
  notes [...] It MUST NOT contain structured data intended for
  programmatic parsing"

The structured data (`track_name`, `package_name`, `product_id`,
trigger reason) should be placed in the `detail` JSONB column, not
`comment`. The `comment` column should remain `NULL` for these
automatic system events per the contract.

**Resolution**: update `product-lifecycle-transitions.md` to move the
structured context from `comment` to `detail` JSONB, following the
schema pattern established by other event types (e.g.,
`track_status_changed` uses `{"track": "...", "package": "..."}`).
