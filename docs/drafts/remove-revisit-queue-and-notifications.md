# Remove Revisit Queue and Notifications References

## Purpose

The "revisit queue" and "notifications" are two future features that have been
referenced across multiple specifications despite not being designed,
specified, or implemented. Their premature presence in the specs:

- Creates implementation ambiguity (specs describe steps that cannot be
  implemented)
- Introduces inter-spec coupling to non-existent features
- May mislead implementers into thinking these features are partially
  specified

This document inventories all references, categorizes them by removal
complexity, and provides a step-by-step removal plan.

## Current State

- **No implementation exists**: zero code in `backend/` or `frontend/`
  for either feature
- **No dedicated specification exists**: no `docs/features/*/revisit-queue.md`
  or `docs/features/*/notifications.md`
- **No database model exists**: neither concept appears in `docs/data-model.md`
- **All mentions are forward-looking**: every reference explicitly or
  implicitly acknowledges the feature is future/TBD

---

## Inventory: Revisit Queue

### 1. `docs/features/tickets/ticket-mutations.md:155`

**Context**: Side effects list for `reconcile_ticket_status()`

```
- May add ticket to revisit queue after sanitization
```

**Category**: Algorithm side-effect declaration (bullet point)

---

### 2. `docs/features/tickets/ticket-mutations.md:255-256`

**Context**: Inactive Assignee Sanitization algorithm, step 3

```
3. Add the ticket to the revisit queue (to be defined in a future
   specification)
```

**Category**: Algorithm step (numbered)

---

### 3. `docs/features/identity/user-service.md:190-191`

**Context**: `_unassign_active_tickets()` algorithm, step 5

```
5. Add the ticket to the revisit queue for follow-up reassignment (see
   future specification)
```

**Category**: Algorithm step (numbered)

---

### 4. `docs/features/identity/user-service.md:597-598`

**Context**: `deactivate_user()` summary of step 4

```
...creates TicketAuditEvent records, and adds tickets to
the revisit queue — all within the same transaction.
```

**Category**: Behavioral summary (inline in prose)

---

### 5. `docs/features/tickets/cve-tracking.md:319`

**Context**: CVE Rejection handling table — row for `Analysis`, `Analyzed`,
`Resolved`

```
| Analysis, Analyzed, Resolved | Do NOT change ticket status. Notify the
assignee via notify_cve_rejected. Add the ticket to the Revisit list. |
```

**Category**: Behavioral table cell (combined with notification)

---

### 6. `docs/features/tickets/cve-tracking.md:331`

**Context**: Rejection revert handling table — row for `Duplicated`

```
| Duplicated | Do NOT change ticket status. Notify the assignee via
notify_cve_rejection_reverted. Add the ticket to the Revisit list. The VA
should verify whether the duplicate mark is still valid. |
```

**Category**: Behavioral table cell (combined with notification)

---

### 7. `docs/features/packages/ibs-track-release-detection.md:154`

**Context**: Case B algorithm (ticket exists, package NOT tracked)

```
- Add the ticket to the "Revisit" list.
```

**Category**: Algorithm step (bullet point)

---

### 8. `docs/features/packages/ibs-product-release-detection.md:238`

**Context**: No-match flow algorithm

```
- Add the ticket to the **"Revisit" list** (separate feature spec, TBD).
```

**Category**: Algorithm step (bullet point)

---

### 9. `docs/features/packages/ibs-product-release-detection.md:309`

**Context**: "Dependencies on separate features" section

```
- **"Revisit" list** — Destination for tickets in the no-match flow.
  Separate feature spec.
```

**Category**: Dependency declaration (informational)

---

## Inventory: Notifications

### 1. `docs/features/tickets/cve-tracking.md:212-215`

**Context**: Core ingestion flow, step 3

```
3. When a CVE has a resolved CVSS score >= 9.0 (Critical), a notification
   is generated immediately after ingestion. The score is resolved using
   resolve_severity_score (...)
```

**Category**: Algorithm step (numbered, defines trigger condition)

---

### 2. `docs/features/tickets/cve-tracking.md:317`

**Context**: Rejection handling table — row for `New`

```
No notification — the ticket had no assignee.
```

**Category**: Behavioral table cell (negative assertion)

---

### 3. `docs/features/tickets/cve-tracking.md:319`

**Context**: Rejection handling table — row for `Analysis`, `Analyzed`,
`Resolved`

```
Notify the assignee via notify_cve_rejected.
```

**Category**: Behavioral table cell (action)

---

### 4. `docs/features/tickets/cve-tracking.md:329`

**Context**: Rejection revert handling table — row for `Ignored`

```
Notify via notify_cve_rejection_reverted.
```

**Category**: Behavioral table cell (action)

---

### 5. `docs/features/tickets/cve-tracking.md:330`

**Context**: Rejection revert handling table — row for `Analysis`, `Analyzed`,
`Resolved`

```
Notify the assignee via notify_cve_rejection_reverted (informational: "CVE
rejection reverted").
```

**Category**: Behavioral table cell (action)

---

### 6. `docs/features/tickets/cve-tracking.md:331`

**Context**: Rejection revert handling table — row for `Duplicated`

```
Notify the assignee via notify_cve_rejection_reverted.
```

**Category**: Behavioral table cell (action)

---

### 7. `docs/features/tickets/cve-tracking.md:343`

**Context**: Rationale paragraph for rejection handling

```
...Sentinel notifies the assignee but does not change status...
```

**Category**: Rationale prose

---

### 8. `docs/features/tickets/cve-tracking.md:512-522`

**Context**: "Non-fetcher background tasks" section (3 named tasks)

```
- notify_critical_cve: on-demand task that sends notifications for critical
  CVEs (CVSS >= 9.0) after ingestion
- notify_cve_rejected: on-demand task enqueued when a CVE's cve_state changes
  to REJECTED...
- notify_cve_rejection_reverted: on-demand task enqueued when a CVE's
  cve_state changes from REJECTED to PUBLISHED...
```

**Category**: Task definitions section (entire subsection)

---

### 9. `docs/features/tickets/cve-service.md:261-263`

**Context**: Post-ingestion tasks list, item 4

```
4. **Critical CVE notification**: when the severity-resolved CVSS score
   >= 9.0 (...)
```

**Category**: Numbered list item (defines trigger)

---

### 10. `docs/features/tickets/cve-service.md:354-359`

**Context**: Note paragraph about notification dispatch

```
**Note**: notifications (critical CVE, CVE rejection) use their own
dispatch mechanism independent of PostIngestTasks. They are triggered
by condition checks inside upsert_cve() but their actual enqueue
timing relative to commit is managed by the caller (same
commit-then-dispatch pattern).
```

**Category**: Implementation note paragraph

---

### 11. `docs/features/tickets/cve-service.md:1221`

**Context**: Internal side effects paragraph

```
...CVSS recalculation chain, critical CVE notification, CVE rejection
handling...
```

**Category**: Inline list reference

---

### 12. `docs/features/tickets/tickets.md:264`

**Context**: Note on NVD Rejections

```
...instead, a notification is sent to the assignee for manual review.
```

**Category**: Behavioral note

---

### 13. `docs/features/tickets/tickets.md:786`

**Context**: Table of features not applicable to non-CVE tickets

```
| Critical CVE notification | Not applicable |
```

**Category**: Table row

---

### 14. `docs/features/tickets/cvss-scoring.md:26`

**Context**: Design principles, item 3

```
...notifications). Initially set to 3.1, changeable by Admin.
```

**Category**: Parenthetical inline reference

---

### 15. `docs/features/tickets/cvss-scoring.md:56`

**Context**: Severity Resolution Cascade "Used for" line

```
Used for: severity derivation, display, notifications, and any future
informational/triage logic.
```

**Category**: Purpose/consumer declaration

---

### 16. `docs/features/tickets/cvss-scoring.md:506`

**Context**: Severity API response description

```
Used for display, triage, and notifications.
```

**Category**: Purpose/consumer declaration

---

### 17. `docs/features/tickets/cvss-scoring.md:756`

**Context**: Batch recalculation paragraph

```
...no dedicated result storage, audit trail enrichment, or notification
mechanism is provided for the batch outcome.
```

**Category**: Negative assertion (explicitly states no notification exists)

---

### 18. `docs/features/tickets/cve-sync-kernel.md:250`

**Context**: Race condition analysis

```
...may trigger notifications.
```

**Category**: Behavioral observation (consequence of race)

---

### 19. `docs/features/tickets/cve-sync-nvd.md:767`

**Context**: CVE rejection summary

```
Tickets in other statuses are not modified (assignee is notified).
```

**Category**: Parenthetical behavioral note

---

### 20. `docs/features/packages/ibs-product-release-detection.md:236-237`

**Context**: No-match flow algorithm

```
- Notify the ticket's assignee (notification mechanism is TBD at the system
  level, see Open Items).
```

**Category**: Algorithm step (bullet point)

---

### 21. `docs/features/packages/ibs-product-release-detection.md:311-312`

**Context**: "Dependencies on separate features" section

```
- **Notifications** — Mechanism (in-app, email) for notifying the
  assignee in the no-match flow. Separate feature spec.
```

**Category**: Dependency declaration (informational)

---

### 22. `docs/features/packages/ibs-track-release-detection.md:153`

**Context**: Case B algorithm (ticket exists, package NOT tracked)

```
- Notify the ticket's assignee.
```

**Category**: Algorithm step (bullet point)

---

### 23. `docs/features/packages/package-bugowner.md:18-19`

**Context**: Purpose list, item 4

```
4. Future notification system to alert maintainers about new tickets
   affecting their packages (separate spec)
```

**Category**: Purpose list item (future)

---

### 24. `docs/features/packages/package-bugowner.md:436-438`

**Context**: "Future Considerations" section

```
- **Notification system**: automated notifications to bugowners when new
  tickets are created for their packages, or when ticket status changes
  require their attention. Will be specified in a separate feature spec.
```

**Category**: Future Considerations bullet

---

### 25. `docs/features/packages/maintainer.md:368`

**Context**: "Future Considerations" section

```
- **Notifications**: automated email or chat notifications to bugowners
  when new pending fixes appear, linking to the per-ticket view
```

**Category**: Future Considerations bullet

---

### 26. `docs/features/identity/user-management.md:978-988`

**Context**: Security Considerations — accepted risk

```
- **No notification on admin password reset (accepted risk)**: when an
  admin resets a user's password [...] the target user receives no
  notification (no email, no in-app alert). [...] adding a notification
  system (SMTP infrastructure, templates, bounce handling) is
  disproportionate to the residual risk...
```

**Category**: Security considerations (accepted risk documentation)

---

### 27. `docs/architecture.md:202`

**Context**: SUSE Active Directory integration

```
- Direct line manager (manager DN) is resolved and stored for
  notification escalation and maintainer task management
```

**Category**: Purpose declaration for `manager_id` field

---

### 28. `docs/api-spec.md:553`

**Context**: PATCH vs POST method selection — list of permitted "domain
cascading consequences" as side effects

```
- Notification dispatch
```

**Category**: API convention (side-effect type example)

---

### 30. `docs/drafts/open-points.md:170`

**Context**: OP-7 (status reconciliation drift detection)

```
...WARNING + optional webhook notification to ops channel...
```

**Category**: Open design decision option

---

### 31. `docs/drafts/open-points.md:292`

**Context**: OP-11 (Anomaly Observer)

```
...replace the passive warning log with an active notification to the VA.
```

**Category**: Open design decision description

---

### Excluded (not part of removal)

| File | Line | Reason |
|------|------|--------|
| `docs/ui-design-system.md` | 28 | User decision: keep as placeholder |
| `docs/features/platform/fetcher-infrastructure.md` | 2640, 2652 | Generic "notify caller" — not about notification feature |
| `.opencode/agents/api-parity-reviewer.md` | 99 | "UI toast notifications" — API design convention |
| `.opencode/agents/api-convention-reviewer.md` | 54 | "notifications" as example side-effect — API method convention |
| `backend/app/data/cpe-package-mapping.json` | 1167 | `libnotify` package — unrelated |
| `docs/features/packages/ibs-submission-tracking.md` | 1155 | IBS `notify_params` method — unrelated |
| `docs/reviews/*` | various | Review findings (resolved) — informational |

---

## Risk Assessment

### Low Risk (simple text removal, no behavioral change)

These can be removed with no impact on specified behavior:

- All "Future Considerations" bullets (package-bugowner, maintainer)
- All "Dependencies on separate features" entries (ibs-product-release-detection)
- Purpose list items marked "(future)" or "(separate spec)"
- The `user-management.md` security consideration (it documents the absence
  of notifications and explains why — becomes unnecessary when notifications
  are not referenced anywhere)
- The `api-spec.md` "Notification dispatch" bullet (example of a permitted
  PATCH side effect — removes a reference to a non-existent capability)

### Medium Risk (behavioral spec changes requiring careful rewording)

These describe specified behavior that references notifications/revisit as
a step. The step must be removed and surrounding text adjusted:

- `ticket-mutations.md` — side effects list and algorithm steps
- `user-service.md` — algorithm steps and deactivate_user summary
- `cve-tracking.md` — rejection/revert handling table cells and task
  definitions
- `cve-service.md` — post-ingestion tasks list and dispatch note
- `ibs-track-release-detection.md` — Case B steps
- `ibs-product-release-detection.md` — no-match flow steps

### Low-Medium Risk (cross-reference adjustments)

References in other specs that point to notification behavior need to be
simplified to describe only the actual behavior (status change or no-op):

- `tickets.md:264` — NVD Rejection note
- `tickets.md:786` — non-CVE ticket feature table row
- `cve-sync-nvd.md:767` — parenthetical "(assignee is notified)"
- `cve-sync-kernel.md:250` — "may trigger notifications"
- `cvss-scoring.md` — "notifications" in consumer lists
- `architecture.md:202` — "notification escalation" purpose

### Special Case (open-points.md)

The references in `docs/drafts/open-points.md` (OP-7 and OP-11) describe
future design decisions that mention notifications as one possible
direction. Since these are already in a drafts/open-decisions context and
the notification system will be designed later, these can remain as-is or
be lightly reworded. Decision: leave them in place — they are design
discussion, not specifications.

---

## Removal Plan

### Phase 1: Revisit Queue (6 files)

#### 1.1 `docs/features/tickets/ticket-mutations.md`

**Line 155** — Remove bullet from side effects list:

```diff
 - May null `assignee_id` and create an `assignment` audit event if the
   current assignee is inactive (inactive assignee sanitization)
-- May add ticket to revisit queue after sanitization
 - May call `recalculate_cvss_chain()` when an inactive → active
```

**Lines 255-256** — Remove step 3, renumber step 4 to step 3:

```diff
 1. Set `assignee_id = NULL`
 2. Create `TicketAuditEvent` with `event_type = assignment`
    (system-initiated, `user_id = NULL`,
    `comment = "Unassigned from {username}: employee deactivated"`)
-3. Add the ticket to the revisit queue (to be defined in a future
-   specification)
-4. Emit a warning-level log: `"Inactive assignee {user_id} detected on
+3. Emit a warning-level log: `"Inactive assignee {user_id} detected on
    ticket {ticket_id} during reconciliation — this should have been
    handled by _unassign_active_tickets"`
```

#### 1.2 `docs/features/identity/user-service.md`

**Lines 190-191** — Remove step 5 from `_unassign_active_tickets()`:

```diff
 3. Set `assignee_id = NULL`
 4. Create a `TicketAuditEvent` with `event_type = assignment`:
    - `user_id = NULL` (system action)
    - `old_value` = user's username
    - `new_value` = `NULL`
    - `comment` = `"Unassigned from {username}: {reason}"`
-5. Add the ticket to the revisit queue for follow-up reassignment (see
-   future specification)
```

**Lines 597-598** — Remove revisit queue mention from `deactivate_user()`
summary:

```diff
 4. Unassign active tickets: call
    `_unassign_active_tickets(db, user_id, reason)` where `reason` is the
    value passed to `deactivate_user()`. This clears `assignee_id` on all
-   active tickets, creates `TicketAuditEvent` records, and adds tickets to
-   the revisit queue — all within the same transaction. Ticket status is
+   active tickets and creates `TicketAuditEvent` records — all within the
+   same transaction. Ticket status is
    not changed (see Architectural Invariant in `tickets.md`). See
    Private Helpers for the full contract.
```

#### 1.3 `docs/features/tickets/cve-tracking.md`

**Line 319** — Remove "Add the ticket to the Revisit list" from rejection
table:

```diff
-| `Analysis`, `Analyzed`, `Resolved` | Do NOT change ticket status. Notify the assignee via `notify_cve_rejected`. Add the ticket to the Revisit list. |
+| `Analysis`, `Analyzed`, `Resolved` | Do NOT change ticket status. |
```

(Note: `Notify the assignee via notify_cve_rejected` is also removed as
part of Phase 2 notifications removal.)

**Line 331** — Remove "Add the ticket to the Revisit list" from revert
table:

```diff
-| `Duplicated` | Do NOT change ticket status. Notify the assignee via `notify_cve_rejection_reverted`. Add the ticket to the Revisit list. The VA should verify whether the duplicate mark is still valid. |
+| `Duplicated` | Do NOT change ticket status. The VA should verify whether the duplicate mark is still valid. |
```

(Note: notification removal is handled in Phase 2.)

#### 1.4 `docs/features/packages/ibs-track-release-detection.md`

**Line 153-154** — Remove revisit and notification bullets from Case B:

```diff
 - Create a `TicketAuditEvent` with `event_type = package_added`,
   `user_id = NULL`, comment: "Package `{P}` auto-added: CVE fix
   detected in `{C}`".
-- Notify the ticket's assignee.
-- Add the ticket to the "Revisit" list.
```

(Note: the notification bullet is also removed in Phase 2.)

#### 1.5 `docs/features/packages/ibs-product-release-detection.md`

**Line 238** — Remove revisit bullet from no-match flow:

```diff
-- Add the ticket to the **"Revisit" list** (separate feature spec, TBD).
 - **No automatic modification** is made to the ticket's package records.
```

**Lines 309-310** — Remove from "Dependencies on separate features":

```diff
-- **"Revisit" list** — Destination for tickets in the no-match flow.
-  Separate feature spec.
```

---

### Phase 2: Notifications (13 files)

#### 2.1 `docs/features/tickets/cve-tracking.md`

**Lines 212-215** — Remove notification step from core ingestion flow:

```diff
    references are upserted by URL (insert-or-update, no stale cleanup).
    See `docs/features/tickets/ticket-references.md` for the ingestion
    flow and upsert strategy.
-3. When a CVE has a resolved CVSS score >= 9.0 (Critical), a notification
-   is generated immediately after ingestion. The score is resolved using
-   `resolve_severity_score` (the severity resolution cascade — see
-   `docs/features/tickets/cvss-scoring.md`, Severity Resolution Cascade)
-4. Duplicate CVEs from different sources are merged by CVE ID, with source
+3. Duplicate CVEs from different sources are merged by CVE ID, with source
    data preserved in CVESource records
```

(Renumber all subsequent items.)

**Line 317** — Remove "No notification" from `New` row:

```diff
-| `New` | Transition to `Ignored` automatically. Create a `TicketAuditEvent` [...]. No notification — the ticket had no assignee. |
+| `New` | Transition to `Ignored` automatically. Create a `TicketAuditEvent` [...]. |
```

**Line 319** — Remove notification from `Analysis`/`Analyzed`/`Resolved`
row (combined with Phase 1 revisit removal):

```diff
-| `Analysis`, `Analyzed`, `Resolved` | Do NOT change ticket status. Notify the assignee via `notify_cve_rejected`. Add the ticket to the Revisit list. |
+| `Analysis`, `Analyzed`, `Resolved` | Do NOT change ticket status. |
```

**Line 329** — Remove notification from `Ignored` revert row:

```diff
-| `Ignored` | System reopens the ticket via `ticket_mutations.reopen_from_ignored()`: restores last assignee (if active) or leaves unassigned [...]. Notify via `notify_cve_rejection_reverted`. |
+| `Ignored` | System reopens the ticket via `ticket_mutations.reopen_from_ignored()`: restores last assignee (if active) or leaves unassigned [...]. |
```

**Line 330** — Remove notification from `Analysis`/`Analyzed`/`Resolved`
revert row:

```diff
-| `Analysis`, `Analyzed`, `Resolved` | Do NOT change ticket status. Notify the assignee via `notify_cve_rejection_reverted` (informational: "CVE rejection reverted"). |
+| `Analysis`, `Analyzed`, `Resolved` | Do NOT change ticket status. |
```

**Line 331** — Remove notification from `Duplicated` revert row (combined
with Phase 1):

```diff
-| `Duplicated` | Do NOT change ticket status. Notify the assignee via `notify_cve_rejection_reverted`. Add the ticket to the Revisit list. The VA should verify whether the duplicate mark is still valid. |
+| `Duplicated` | Do NOT change ticket status. The VA should verify whether the duplicate mark is still valid. |
```

**Line 343** — Reword rationale paragraph:

```diff
-Similarly, when the CNA reverses a rejection, tickets in `Ignored` status
-are automatically reopened (since they were automatically set to Ignored by
-the original rejection). For tickets in other statuses, Sentinel notifies
-the assignee but does not change status, as the VA may have already made an
-independent decision.
+Similarly, when the CNA reverses a rejection, tickets in `Ignored` status
+are automatically reopened (since they were automatically set to Ignored by
+the original rejection). Tickets in other statuses are not changed, as the
+VA may have already made an independent decision.
```

**Lines 510-522** — Remove entire "Non-fetcher background tasks" section:

```diff
-### Non-fetcher background tasks
-
-- `notify_critical_cve`: on-demand task that sends notifications for
-  critical CVEs (CVSS >= 9.0) after ingestion
-- `notify_cve_rejected`: on-demand task enqueued when a CVE's
-  `cve_state` changes to `REJECTED` and the ticket is in status
-  `Analysis`, `Analyzed`, or `Resolved`. Notifies the assigned VA. Tickets
-  in `New` are automatically transitioned to `Ignored` without
-  notification. See "CVE Rejection Handling" above
-- `notify_cve_rejection_reverted`: on-demand task enqueued when a CVE's
-  `cve_state` changes from `REJECTED` to `PUBLISHED`. Notifies the
-  assignee (if present) that the CVE rejection has been reverted. See
-  "Rejection revert handling" above
```

#### 2.2 `docs/features/tickets/cve-service.md`

**Lines 261-263** — Remove item 4 from post-ingestion tasks list,
renumber item 5:

```diff
 3. **CVSS recalculation chain**: when CVSS assessments change, trigger
    the resolution cascade (see `docs/features/tickets/cvss-scoring.md`)
-4. **Critical CVE notification**: when the severity-resolved CVSS score
-   >= 9.0 (resolved via `resolve_severity_score`, the 5-step severity
-   cascade — see `docs/features/tickets/cvss-scoring.md`)
-5. **CVE rejection handling**: when `cve_state` changes to `REJECTED`
+4. **CVE rejection handling**: when `cve_state` changes to `REJECTED`
    or reverts from `REJECTED` to `PUBLISHED` — see
```

**Lines 354-359** — Remove notification dispatch note:

```diff
-**Note**: notifications (critical CVE, CVE rejection) use their own
-dispatch mechanism independent of `PostIngestTasks`. They are triggered
-by condition checks inside `upsert_cve()` but their actual enqueue
-timing relative to commit is managed by the caller (same
-commit-then-dispatch pattern). See `cve-tracking.md`, Non-fetcher
-background tasks for the task definitions. `PostIngestTasks` carries
-only package resolution data.
+`PostIngestTasks` carries only package resolution data.
```

**Line 1221** — Remove "critical CVE notification" from inline list:

```diff
-CVSS recalculation chain, critical CVE notification, CVE
-rejection handling) are each controlled by their own trigger condition
+CVSS recalculation chain, CVE rejection handling) are each controlled
+by their own trigger condition
```

#### 2.3 `docs/features/tickets/tickets.md`

**Line 264** — Reword NVD Rejection note:

```diff
-**Note on NVD Rejections**: When a CVE's `vulnStatus` changes to Rejected
-in NVD, only tickets in `New` status are automatically transitioned to
-`Ignored`. Tickets in `Analysis` or later statuses are NOT automatically
-transitioned; instead, a notification is sent to the assignee for manual
-review. For the complete flow regarding NVD rejections and rejection
-reverts, see `docs/features/tickets/cve-tracking.md` ("Rejection handling"
-and "Rejection revert handling").
+**Note on NVD Rejections**: When a CVE's `vulnStatus` changes to Rejected
+in NVD, only tickets in `New` status are automatically transitioned to
+`Ignored`. Tickets in `Analysis` or later statuses are NOT automatically
+transitioned — the VA must review the rejection manually. For the complete
+flow regarding NVD rejections and rejection reverts, see
+`docs/features/tickets/cve-tracking.md` ("Rejection handling" and
+"Rejection revert handling").
```

**Line 786** — Remove "Critical CVE notification" row from non-CVE ticket
table:

```diff
-| Critical CVE notification | Not applicable |
```

#### 2.4 `docs/features/tickets/cvss-scoring.md`

**Line 26** — Remove "notifications" from parenthetical:

```diff
-   notifications). Initially set to `3.1`, changeable by Admin. See
+   eligibility). Initially set to `3.1`, changeable by Admin. See
```

**Line 56** — Remove "notifications" from consumer list:

```diff
-Used for: severity derivation, display, notifications, and any future
-informational/triage logic.
+Used for: severity derivation, display, and any future
+informational/triage logic.
```

**Line 506** — Remove "notifications" from purpose description:

```diff
-display, triage, and notifications.
+display and triage.
```

**Line 756** — Remove "or notification mechanism" (already a negative
assertion):

```diff
-...no dedicated result storage, audit trail enrichment, or notification
-mechanism is provided for the batch outcome.
+...no dedicated result storage or audit trail enrichment is provided
+for the batch outcome.
```

#### 2.5 `docs/features/tickets/cve-sync-kernel.md`

**Line 250** — Remove "and may trigger notifications":

```diff
-trigger notifications. The window is bounded: at most one MITRE sync
+The window is bounded: at most one MITRE sync
```

(Adjust the preceding sentence to end cleanly.)

#### 2.6 `docs/features/tickets/cve-sync-nvd.md`

**Line 767** — Remove "(assignee is notified)":

```diff
-not modified (assignee is notified).
+not modified.
```

#### 2.7 `docs/features/packages/ibs-product-release-detection.md`

**Lines 236-237** — Remove notification bullet from no-match flow:

```diff
-- Notify the ticket's assignee (notification mechanism is TBD at the system
-  level, see [Open Items](#open-items)).
```

**Lines 311-312** — Remove from "Dependencies on separate features":

```diff
-- **Notifications** — Mechanism (in-app, email) for notifying the
-  assignee in the no-match flow. Separate feature spec.
```

#### 2.8 `docs/features/packages/ibs-track-release-detection.md`

**Line 153** — Remove notification bullet from Case B:

```diff
-- Notify the ticket's assignee.
```

(Combined with Phase 1 — both bullets removed from Case B.)

#### 2.9 `docs/features/packages/package-bugowner.md`

**Lines 18-19** — Remove item 4 from purpose list:

```diff
 3. Future integration with a maintainer dashboard where each maintainer
    can see pending submissions and track progress (separate spec)
-4. Future notification system to alert maintainers about new tickets
-   affecting their packages (separate spec)
```

**Lines 436-438** — Remove notification bullet from Future Considerations:

```diff
-- **Notification system**: automated notifications to bugowners when new
-  tickets are created for their packages, or when ticket status changes
-  require their attention. Will be specified in a separate feature spec.
```

#### 2.10 `docs/features/packages/maintainer.md`

**Line 368** — Remove notification bullet from Future Considerations:

```diff
-- **Notifications**: automated email or chat notifications to bugowners
-  when new pending fixes appear, linking to the per-ticket view
```

#### 2.11 `docs/features/identity/user-management.md`

**Lines 978-988** — Reword the "No notification on admin password reset"
accepted risk to remove notification system references while preserving
the security rationale:

```diff
-- **No notification on admin password reset (accepted risk)**: when an
-  admin resets a user's password via `POST /api/v1/admin/users/{user}/password`,
-  the target user receives no notification (no email, no in-app alert).
-  A compromised admin could covertly take over an account. This is
-  accepted because: (1) the admin trust level already implies full system
-  access; (2) the `IdentityAuditEvent` (`password_reset`) provides a
-  forensic trail of acting admin and target user; (3) adding a notification
-  system (SMTP infrastructure, templates, bounce handling) is
-  disproportionate to the residual risk in an internal tool. If the
-  threat model evolves (e.g., multi-tenant admin roles), user-facing
-  notifications should be reconsidered
+- **Admin password reset has no out-of-band alert (accepted risk)**: when
+  an admin resets a user's password via
+  `POST /api/v1/admin/users/{user}/password`, the target user receives no
+  out-of-band alert. A compromised admin could covertly take over an
+  account. This is accepted because: (1) the admin trust level already
+  implies full system access; (2) the `IdentityAuditEvent`
+  (`password_reset`) provides a forensic trail of acting admin and target
+  user
```

#### 2.12 `docs/architecture.md`

**Line 202** — Remove "notification escalation" from manager purpose:

```diff
-- Direct line manager (`manager` DN) is resolved and stored for
-  notification escalation and maintainer task management
+- Direct line manager (`manager` DN) is resolved and stored for
+  maintainer task management
```

#### 2.13 `docs/api-spec.md`

**Line 553** — Remove "Notification dispatch" from the list of permitted
domain cascading consequences for PATCH operations:

```diff
 - Status propagation to related entities
 - Eligibility or threshold re-evaluation
 - Audit event creation
-- Notification dispatch
```

---

### Phase 3: Cross-reference cleanup (AGENTS.md)

#### 3.1 `AGENTS.md`

**Line 456** (in Guardrail 13 description) — Remove "notifications" from
the severity resolution cascade consumer list. This mirrors the change in
`cvss-scoring.md`:

```diff
-   derivation, display, notifications, triage
+   derivation, display, triage
```

---

### Items explicitly NOT removed

| Item | File | Reason |
|------|------|--------|
| "notifications" in top bar | `docs/ui-design-system.md:28` | User decision: keep as placeholder |
| OP-7 "webhook notification" | `docs/drafts/open-points.md:170` | Open design decision, not a spec |
| OP-11 "active notification" | `docs/drafts/open-points.md:292` | Open design decision, not a spec |
| Review findings | `docs/reviews/*.md` | Historical records, not actionable specs |

---

## Validation Checklist

After completing all removals:

1. **Grep validation**: run `grep -rn "revisit\|Revisit" docs/` and
   `grep -rn "notif" docs/` — only the explicitly excluded items should
   remain
2. **Algorithm step numbering**: verify all renumbered steps in affected
   files are sequential with no gaps
3. **Table consistency**: verify rejection/revert handling tables in
   `cve-tracking.md` still have actions for every status row (no empty
   action cells)
4. **Cross-reference integrity**: verify no remaining spec references
   `notify_critical_cve`, `notify_cve_rejected`, or
   `notify_cve_rejection_reverted` by name
5. **Behavioral completeness**: for the CVE rejection handling, verify
   that the specified behavior for tickets in `Analysis`/`Analyzed`/
   `Resolved` is still clear (answer: "do not change ticket status" —
   the VA discovers the rejection through the ticket's CVE data display)
6. **Spec coherence**: run `@spec-coherence-reviewer` on the modified
   specs to ensure no contradictions were introduced

## Execution Order

The recommended execution order minimizes the risk of intermediate
inconsistencies:

1. **Phase 1 + Phase 2 combined per file** — since several files have
   both revisit and notification references (e.g., `cve-tracking.md`,
   `ibs-track-release-detection.md`), edit each file once with all
   changes applied together
2. **Cross-reference files last** (AGENTS.md, architecture.md) — these
   reference the primary specs, so update them after the primary specs
   are clean
3. **Validation** — run grep checks and coherence review after all edits

## File Change Summary

| File | Revisit changes | Notification changes |
|------|----------------|---------------------|
| `docs/features/tickets/ticket-mutations.md` | 2 edits | — |
| `docs/features/identity/user-service.md` | 2 edits | — |
| `docs/features/tickets/cve-tracking.md` | 2 edits | 8 edits |
| `docs/features/tickets/cve-service.md` | — | 3 edits |
| `docs/features/tickets/tickets.md` | — | 2 edits |
| `docs/features/tickets/cvss-scoring.md` | — | 4 edits |
| `docs/features/tickets/cve-sync-kernel.md` | — | 1 edit |
| `docs/features/tickets/cve-sync-nvd.md` | — | 1 edit |
| `docs/features/packages/ibs-track-release-detection.md` | 1 edit | 1 edit |
| `docs/features/packages/ibs-product-release-detection.md` | 2 edits | 2 edits |
| `docs/features/packages/package-bugowner.md` | — | 2 edits |
| `docs/features/packages/maintainer.md` | — | 1 edit |
| `docs/features/identity/user-management.md` | — | 1 edit |
| `docs/api-spec.md` | — | 1 edit |
| `docs/architecture.md` | — | 1 edit |
| `AGENTS.md` | — | 1 edit |
| **Total** | **9 edits** | **29 edits** |
