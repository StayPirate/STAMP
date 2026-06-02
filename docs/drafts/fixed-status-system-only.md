# Draft: Restrict FIXED status to system-managed (automatic detection)

**Status**: Draft — pending approval
**Domain**: packages (track affectedness model)
**Related specs**: `package-model.md`, `package-service.md`,
`ibs-track-release-detection.md`

## Problem statement

The current package model allows VAs to manually set a track's
affectedness status to `FIXED`. This creates a structural problem:

1. The VA asserts "the fix is in the codestream" — an unverified claim
2. The Resolved gate (clause b) then requires all eligible products
   under that `FIXED` track to have `released_at IS NOT NULL`
3. `released_at` is set exclusively by product release detection
   (advisory in `updateinfo.xml`)
4. If the fix was never actually submitted to the products (no
   submission request, no advisory published), `released_at` is never
   set
5. The ticket is blocked in Analyzed permanently — a silent deadlock

The VA has no mechanism to unblock this state without reverting the
track to a non-FIXED status. The root cause is that `FIXED` is a
**verifiable fact** being treated as a **human judgment**.

## Current behavior

From `package-model.md` (Manual Transitions):

> The VA can manually change the affectedness status of any track to any
> value without restriction.

From `package-model.md` (Design Decision 5):

> - The VA can set `FIXED` manually
> - The system sets `FIXED` automatically when track release detection
>   confirms the fix in the codestream (MD5 match)

From `package-service.md` (`set_track_status()`):

> VA callers (`acting_user_id` present) can transition from any state —
> the VA has full override authority.

The delivery status axis is already system-managed:

> The VA cannot manually change the delivery status — it is
> system-managed.

## Proposed change

**`FIXED` becomes a system-managed status on the affectedness axis.** VAs
can manually set tracks to `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, or
`WONT_FIX`. Only the track release detection mechanism (MD5 match) can
transition a track to `FIXED`.

### New status taxonomy

| Status | Set by | Nature |
|--------|--------|--------|
| `ANALYSIS` | VA | Decision — "not yet analyzed" |
| `AFFECTED` | VA | Judgment — "code is vulnerable" |
| `NOT_AFFECTED` | VA | Judgment — "code is not vulnerable" |
| `WONT_FIX` | VA | Decision — "vulnerable, will not fix" |
| `FIXED` | System | Fact — "fix verified in codestream via MD5 match" |

### Validation rule

`set_track_status()` rejects `status = FIXED` when
`acting_user_id IS NOT NULL` **and** `force is False`. The API layer
is responsible for checking whether the caller holds `admin_ticket_ops`
and passing `force=True` only when authorized.

Exception (service layer): `TrackFixedStatusRestrictedError`

> Track status FIXED can only be set by the system (automatic
> detection) or via the admin escape hatch.

This exception maps to `403 AUTH_INSUFFICIENT_PERMISSION` at the API
layer. In practice, the API layer catches the condition first (Hard
Conditional Check), so this exception is defense-in-depth for non-API
callers.

System callers (`acting_user_id = None`) retain the ability to set
`FIXED` — this is the path used by the track release detection
mechanism (`IBSEventConsumer` and `check_ibs_track_releases` fetcher).

## Rationale

### 1. Semantic alignment — facts vs. judgments

The four VA-settable statuses are all human assessments that cannot be
mechanically verified. `FIXED` is unique: it asserts a factual state
(fix present in codestream) that the system can and does verify
automatically via MD5 comparison. Allowing manual assertion of a
verifiable fact bypasses the verification mechanism.

### 2. Consistency with product level

`released_at` on products is already system-managed — set exclusively by
product release detection. If the factual "fix delivered to product
repository" is system-managed, the factual "fix present in codestream"
should follow the same principle. This eliminates the current asymmetry
between the two levels:

| Level | Factual state | Who sets it |
|-------|--------------|-------------|
| Track | `status = FIXED` | Currently: VA or system. **Proposed**: system only |
| Product | `released_at IS NOT NULL` | System only (already) |

### 3. Deadlock prevention

The permanent deadlock scenario (VA sets FIXED, no submission happens,
`released_at` never set, ticket blocked) becomes structurally
impossible. If the system sets `FIXED`, it means the fix was genuinely
detected in the codestream — the submission pipeline has already
started, and `released_at` will eventually follow.

### 4. Manual FIXED does not accelerate resolution

Even when a VA correctly knows a fix is in the codestream before
detection runs, manually setting `FIXED` does not unblock the ticket —
the Resolved gate still requires `released_at` on eligible products,
which depends on product release detection. The VA gains no practical
benefit from the manual override.

## Impact on `set_track_status()`

Current behavior (`package-service.md:125-174`):

```
Step 5: If acting_user_id is None and current status is final
        → reject (system cannot transition out of final states)
```

New behavior:

```
Step 5a: If status == FIXED and acting_user_id is not None
         AND force is False
         → raise TrackFixedStatusRestrictedError
         (only system detection or admin force can set FIXED)

Step 5b: If acting_user_id is None and current status is final
         → reject with warning log
         (system cannot transition out of final states)
```

Step 5a is evaluated before step 5b. The existing final-status
protection for system callers remains unchanged.

### Signature change

`set_track_status()` gains a new parameter:

```python
async def set_track_status(
    db: AsyncSession,
    track_id: UUID,
    status: PackageStatus,
    acting_user_id: UUID | None,
    force: bool = False,          # NEW — admin escape hatch
) -> TicketPackageTrack:
```

The `force` parameter defaults to `False`. The service layer rejects
`status == FIXED` when `acting_user_id is not None` and `force is
False`. The service does NOT query the RBAC system — it trusts the
caller to have verified the capability before passing `force=True`.

### Two-layer enforcement

The FIXED restriction is enforced at two levels:

1. **API layer (primary)**: the endpoint handler checks whether the
   request body contains `status = FIXED`. If so, it verifies that the
   authenticated user holds `admin_ticket_ops` (Hard Conditional Check).
   If the user lacks it, the endpoint returns `403
   AUTH_INSUFFICIENT_PERMISSION` immediately without calling the service.
   If the user holds it, the endpoint passes `force=True` to the
   service.

2. **Service layer (defensive)**: `set_track_status()` independently
   rejects `status == FIXED` when `acting_user_id is not None` and
   `force is False`. This prevents any future caller (CLI, other
   service, migration script) from accidentally setting FIXED without
   going through the capability check.

System callers (track release detection, IBS event consumer) pass
`acting_user_id=None` — the `force` parameter is irrelevant for them
and they bypass both checks.

CLI commands that need to force-FIXED MUST verify `admin_ticket_ops`
before passing `force=True`. Passing `force=True` without capability
verification is a bug.

### Caller categories after this change

| Caller | `acting_user_id` | `force` | Can set FIXED? |
|--------|-------------------|---------|----------------|
| System (detection) | `None` | N/A (ignored) | Yes |
| VA (API) | UUID | `False` | No |
| Admin (force, API) | UUID | `True` | Yes |

### VA can still transition OUT of FIXED

A VA retains the ability to change a track **from** `FIXED` to another
status (e.g., back to `AFFECTED` if the fix is insufficient). This is
not restricted — the VA has full override authority on **non-FIXED
target** transitions. The restriction is only on setting the target to
`FIXED`.

This preserves the existing behavior documented in `package-model.md`:

> The VA can change `FIXED` back to `AFFECTED` if the fix is
> insufficient

## Edge cases

### Detection failure (false negative)

If track release detection fails to detect a genuine fix (MD5 mismatch
due to rebuild, algorithm bug, format change), the track remains in
`AFFECTED` or `ANALYSIS` and the VA cannot manually override to `FIXED`.

**Mitigation**: this is the same class of problem that already exists at
the product level — if product release detection fails to find an
advisory, `released_at` is never set and the ticket is blocked
regardless of track status. The correct resolution in both cases is to
fix the detection algorithm, not to work around it with manual
overrides.

If a systematic detection failure is discovered in production:

1. Fix the detection algorithm
2. Re-run detection (trigger the fetcher manually via the operations
   dashboard)
3. The system sets `FIXED` automatically once detection succeeds

For localized issues where detection cannot be fixed immediately, an
admin can use the escape hatch (see below).

### Detection lag

Track release detection runs via two mechanisms:

- `IBSEventConsumer` — near-real-time (RabbitMQ events)
- `check_ibs_track_releases` — periodic catch-up (every 24h at 02:00
  UTC)

Between a fix landing in the codestream and detection:

- If the IBS RabbitMQ consumer is running: typically seconds to minutes
- If only the periodic fetcher: up to 24 hours

During this lag, the track shows `AFFECTED` (or `ANALYSIS`) even though
the fix exists. This is acceptable — the lag is temporary and
self-correcting. The VA does not need to intervene.

### VA reverts FIXED to AFFECTED

A VA can revert a track from `FIXED` to `AFFECTED` (e.g., the detected
fix is insufficient or for the wrong CVE). After reversion, the track is
back in a non-final state. The next detection cycle will detect the same
fix again (same MD5 match) and re-set `FIXED`.

**This is expected behavior, not a defect.** The detection system is
reporting a verifiable fact: the fix IS present in the codestream. If the
VA disagrees with the system's assessment (e.g., the commit addresses a
different CVE, or the fix is incomplete), they can revert to `AFFECTED`
again. In practice:

1. This scenario is rare — it requires a fix to land in the codestream
   that the detection correctly identifies but that the VA considers
   insufficient
2. The VA always retains override authority to revert back to `AFFECTED`
3. No deadlock occurs — the ticket is not blocked by this cycle
4. Once a new commit lands in the codestream with a proper fix, the
   detection will pick up the new MD5 and the cycle resolves naturally

No additional mechanism (suppression flags, special statuses) is
introduced for this edge case. The cost of the rare VA-vs-system
disagreement is a minor operational inconvenience, not a correctness
issue.

### Interaction with the resolution-complete predicate

This change is orthogonal to the resolution-complete predicate (clause
c, defined in `docs/features/tickets/tickets.md`). The two work
together:

- Clause (c) handles `AFFECTED` tracks with all-ineligible products
- This change ensures `FIXED` only appears when genuinely verified
- Combined: a track is either waiting for a verified fix (stays
  `AFFECTED`, may resolve via clause c if all products are ineligible)
  or has a verified fix (system sets `FIXED`, clause b requires product
  releases)

No interaction conflicts exist.

## Admin escape hatch

An admin can force `FIXED` on a track when the detection system is
broken or unable to detect a genuine fix (e.g., MD5 mismatch due to
rebuild, IBS format change, algorithm bug).

### Capability

Force-FIXED is covered by the existing `admin_ticket_ops` capability.
This is consistent with the capability's purpose — exceptional
administrative operations on ticket data (currently: CVE removal from
tickets).

| Capability | Operations Covered |
|---|---|
| `admin_ticket_ops` | Remove CVE from ticket, force track to FIXED status |

The `vulnerability_analyst` and `automation_agent` roles do NOT hold
`admin_ticket_ops` — even though they hold `manage_packages` (which
covers normal track status changes), forcing FIXED is an exceptional
operational action reserved for administrators.

### Mechanism

The API endpoint detects `status == FIXED` in the request body and
checks whether the authenticated user holds `admin_ticket_ops` (Hard
Conditional Check, same pattern as `manage_confidentiality` on ticket
creation). If yes, it calls:

```python
await set_track_status(
    db, track_id, PackageStatus.FIXED, user.id, force=True
)
```

The service layer sees `force=True` and allows the transition. If the
user does NOT hold the capability, the API returns `403
AUTH_INSUFFICIENT_PERMISSION` before calling the service.

The system caller path (detection, `acting_user_id = None`) remains
unchanged and does not use the `force` parameter.

### API error response

When a user without `admin_ticket_ops` attempts to set `status = FIXED`:

| HTTP | Code | Detail |
|------|------|--------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Setting track status to FIXED requires the admin_ticket_ops capability. |

This follows the Hard Conditional Check pattern established by
`manage_confidentiality` on `POST /api/v1/tickets`: the user passes the
initial capability check (`manage_packages` grants endpoint access), but
a specific operation within the request requires an additional
capability. The response uses the standard `AUTH_INSUFFICIENT_PERMISSION`
code with a descriptive `detail` message.

### Audit trail

Force-FIXED operations use the existing `track_status_changed` event
type. They are distinguishable from automatic detection and normal VA
status changes by the combination of fields:

| Scenario | `user_id` | `new_value` |
|----------|-----------|-------------|
| Automatic detection set FIXED | `NULL` | `FIXED` |
| VA changed status (non-FIXED) | user UUID | e.g. `AFFECTED` |
| Admin forced FIXED | user UUID | `FIXED` |

An admin force-FIXED is identifiable as: `user_id IS NOT NULL` AND
`new_value = 'FIXED'`. This combination is impossible under normal VA
workflow (VAs cannot set FIXED), making it unambiguous without additional
markers.

The `detail` JSONB follows the existing `track_status_changed` schema:
`{"track": "<codestream>", "package": "<name>"}`.

The `comment` column is `NULL` for all `track_status_changed` events
(both manual and automatic). See "Audit log contract simplification"
below.

### When to use

This escape hatch is intended for exceptional operational scenarios:

1. Detection algorithm has a known bug that prevents FIXED detection for
   specific packages or codestreams
2. A critical ticket is blocked waiting for detection while a fix is
   already deployed
3. IBS API is returning unexpected formats that break the diff/MD5 logic

It is NOT intended as a substitute for fixing the detection system. Every
use should be accompanied by a bug report or investigation into why
detection failed.

## Spec changes required

1. **`docs/features/packages/package-model.md`**:
   - Design Decision 5: remove "The VA can set `FIXED` manually",
     replace with "`FIXED` is system-managed — set only by track release
     detection (or admin force via `admin_ticket_ops` capability)"
   - Manual Transitions: restrict to `ANALYSIS`, `AFFECTED`,
     `NOT_AFFECTED`, `WONT_FIX`
   - Automatic Transitions table: add note that FIXED is the only
     system-settable status
   - Change Track Status endpoint: update valid status values for VA
     callers to exclude `FIXED`, add `403 AUTH_INSUFFICIENT_PERMISSION`
     error response for FIXED without `admin_ticket_ops`, add
     `†admin_ticket_ops` Hard Conditional Check annotation
   - Affectedness-Delivery table (line 551): update `FIXED + PENDING`
     meaning from "Fix confirmed (manually or via track release
     detection)" to "Fix confirmed via track release detection; no SR
     submitted yet"

2. **`docs/features/packages/package-service.md`**:
   - `set_track_status()`: add `force: bool = False` parameter
   - Add step 5a validation (raise `TrackFixedStatusRestrictedError`
     when `acting_user_id is not None` and `force is False`)
   - Update "VA callers have full override authority" to clarify the
     FIXED exception
   - Add caller rule: "CLI commands MUST verify `admin_ticket_ops`
     before passing `force=True`. Passing `force=True` without
     capability verification is a bug."
   - Add `PackageServiceError` base class with module-level rule: "All
     exceptions inherit from `PackageServiceError`. API endpoint handlers
     catch `PackageServiceError` subclasses and map them to the
     corresponding HTTP status code and error code per `api-spec.md`."
   - Name the 5 currently unnamed exceptions:
     `PackageAlreadyExcludedError`, `PackageNotExcludedError`,
     `PackageRestoreBlockedError`, `SmeltUnavailableError`,
     `PackageNotFoundInSmeltError`
   - Rewrite Service Exceptions table as two sub-tables:

     **API-facing exceptions** (caught by endpoint handlers):

     | Exception | HTTP | Code | Raised when |
     |-----------|------|------|-------------|
     | `TicketNotFoundError` | 404 | `TICKET_NOT_FOUND` | `FOR UPDATE` returns no row |
     | `TicketNotMutableError` | 409 | `TICKET_NOT_MUTABLE` | Ticket in manual zone |
     | `TrackNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Track ID does not exist |
     | `ProductNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Product ID does not exist |
     | `PackageNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Package ID does not exist |
     | `PackageAlreadyExcludedError` | 409 | `PACKAGE_ALREADY_EXCLUDED` | Soft-delete on record with `deleted_at IS NOT NULL` |
     | `PackageNotExcludedError` | 422 | `PACKAGE_NOT_EXCLUDED` | Restore on record with `deleted_at IS NULL` |
     | `PackageRestoreBlockedError` | 422 | `PACKAGE_RESTORE_BLOCKED` | Restore precondition not met (no valid child chain) |
     | `SmeltUnavailableError` | 503 | `SMELT_UNAVAILABLE` | SMELT API unreachable |
     | `PackageNotFoundInSmeltError` | 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returns zero tracks |
     | `TrackFixedStatusRestrictedError` | 403 | `AUTH_INSUFFICIENT_PERMISSION` | VA attempts `status=FIXED` without force |

     **System-internal exceptions** (handled by system callers directly):

     | Exception | Raised when | Handling |
     |-----------|-------------|----------|
     | `InvalidDeliveryStatusTransition` | Illegal delivery status transition (e.g., regression from RELEASED) | Caller logs warning and continues (`RequestSyncFetcher`) or avoids via pre-check (`IBSEventConsumer`) |

3. **`docs/features/identity/rbac.md`**:
   - Expand `admin_ticket_ops` description: "Remove CVE from ticket,
     force track to FIXED status"
   - Add `†admin_ticket_ops` conditional annotation on the PATCH track
     endpoint row in the Endpoint Permission Map

4. **`docs/features/tickets/tickets.md`**:
   - Update CVE dissociation guidance: remove `FIXED` from the list of
     statuses the VA can manually set after CVE removal (keep
     `NOT_AFFECTED` and `WONT_FIX`)

5. **`docs/features/tickets/ticket-audit-log.md`** (audit log contract
   simplification):
   - For all event types where `comment` is currently documented as
     "Optional VA note for manual": change to `NULL` for manual
     transitions. Affected event types: `status_change`, `assignment`,
     `duplicate_set`, `duplicate_removed`, `package_excluded`,
     `package_restored`, `track_status_changed`, `track_excluded`,
     `track_restored`, `product_excluded`, `product_restored`,
     `cve_removed`
   - Rationale: no API endpoint exposes `comment` as a user-provided
     input field. The "Optional VA note" contract has no delivery
     mechanism — no endpoint request body schema includes a `comment`
     field. Rather than maintaining an aspirational contract with no
     implementation path, simplify to reflect reality: `comment` is
     populated only by system-generated descriptions (creation source,
     deactivation reason, detection context), never by user input
   - If VA notes become a desired feature in the future, they should be
     introduced as a dedicated feature (new parameter across all
     relevant endpoints, consistent UX, proper spec) rather than an
     ad-hoc addition to individual endpoints

6. **`docs/features/packages/ibs-track-release-detection.md`**: no
   changes needed — detection already uses `acting_user_id = None`
   (system caller)

7. **`docs/features/integrations/ibs-rabbitmq-integration.md`**: no
   changes needed — consumer already uses system caller path

### Specs NOT modified (simplification vs. original draft)

The following specs do NOT require modification:

- **`docs/data-model.md`**: no new enum value in `TicketAuditEventType`,
  no schema change to `TicketAuditEvent` (the `comment` column remains
  as-is — it is still used by system-generated descriptions)
- **`docs/api-spec.md`**: no new error code — uses existing
  `AUTH_INSUFFICIENT_PERMISSION`
