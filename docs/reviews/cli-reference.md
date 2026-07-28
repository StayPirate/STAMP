# Review: cli-reference

**Spec**: `docs/cli-reference.md`
**Last reviewed**: 2026-07-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CLIR-GAP-001 — Multiple --role filter semantics on manage-user list (Medium)

**Category**: Boundary conditions
**Status**: OPEN

The `sentinel manage-user list` command accepts repeatable `--role` parameters but neither `cli-reference.md` nor the owning spec `user-management.md` specifies whether multiple values are combined with OR logic (show users who have ANY of the specified roles) or AND logic (show users who have ALL specified roles). Two implementers could plausibly choose different behaviors. OR is more conventional for filters, but AND is also defensible for a "show me power users" use case. The API List Users endpoint uses a singular `role` parameter (not repeatable), so there's no API precedent to derive from.

### CLIR-GAP-002 — No --role validation for invalid values on manage-user list (Medium)

**Category**: Error paths
**Status**: OPEN

The `manage-user list` command accepts a `--role` filter parameter but its behavior section has no validation step for the role value. In contrast, the `create` and `update` commands in the same group explicitly validate role values and exit with `"Error: Invalid role '{value}'. Valid roles are: {list}."` An implementer might either (a) validate against the role enum and reject invalid values with exit 1 (consistent with sibling commands), or (b) pass the invalid value through to the query, returning zero results silently (consistent with the spec as written). Silent empty results for a typo'd role name would be surprising to operators.

---

## Coherence

### CLIR-COH-001 — Exit code 0 description drift vs conventions.md (Low)

**Category**: Source-of-truth conflict
**Status**: OPEN

`cli-reference.md` states exit codes are "defined in `docs/conventions.md`, Exit Codes" and then defines exit code 0 as "Success (includes idempotent no-ops and user-cancelled confirmations)." However, `conventions.md` defines exit code 0 as "Success (includes idempotent no-ops)" without the "user-cancelled confirmations" addition. The extension is semantically correct (backed by `cli-infrastructure.md` which defines that declining a confirmation prompt exits with code 0), but `cli-reference.md` claims to restate the `conventions.md` table while silently extending it.

### CLIR-COH-002 — API key states terminology inconsistency in authentication.md (Low)

**Category**: Terminology
**Status**: OPEN

`cli-reference.md` describes `api-key list` as listing "active, revoked, and expired" keys, while the owning spec `authentication.md` describes the same command as listing "active and revoked" keys. However, `authentication.md` itself then includes "expired" as a distinct status in its output format line: `status (active/revoked/expired)`. `cli-reference.md` is actually more accurate by including all three states. The inconsistency is internal to `authentication.md` (its description omits "expired" despite including it in the output format).

---

## Design

### CLIR-DES-001 — No CLI command for API key creation — headless bootstrapping gap (Medium)

**Category**: Operational completeness
**Status**: OPEN

The `sentinel api-key` group defines only `list` and `revoke` commands, but no `create` command. In a headless environment (container, CI staging, server without a browser), there is no CLI way to create API keys for programmatic access. The only path is a multi-step curl sequence: `POST /api/v1/auth/login` to get a session cookie, then `POST /api/v1/auth/api-keys`. The CLI already has the mutating `api-key revoke` command, so adding `api-key create --username <username> [--name <label>] [--expires-at <datetime>]` would be consistent. The `authentication.md` self-replication security concern ("API-key-authenticated requests cannot create new API keys") doesn't apply to CLI where shell access implies maximum trust.

---

## Security

🟢 No issues found.

---

## API Conventions

🟢 No issues found.
