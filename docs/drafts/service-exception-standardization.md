# Draft: Standardize service exception patterns across modules

**Status**: Draft — pending approval
**Domain**: platform (cross-cutting convention)
**Related specs**: `ticket-service.md`, `ticket-mutations.md`,
`user-service.md`, `api-key-service.md`, `package-service.md`

## Problem statement

The project has five service modules that raise domain-specific
exceptions. Each uses a different documentation format for its Service
Exceptions section — different column counts, different column names,
inconsistent placement of HTTP status codes, and missing base class
declarations. This makes it difficult for implementers to know how to
structure exception handling in API endpoint handlers.

One module (`package-service.md`) already follows the complete target
pattern: base class, 4-column table (Exception, HTTP, Code, Raised
when), and a separate sub-table for system-internal exceptions. The
remaining four modules need to be brought into alignment.

## Current state

| Module | Base class | Exceptions | Table format | HTTP mapping |
|--------|-----------|-----------|--------------|-------------|
| `ticket-service.md` | `TicketServiceError` | 11 | 3 col (Exception, Code, Raised by) | Error codes only; no HTTP status column |
| `ticket-mutations.md` | None | 10 | 2 col (Exception, Raised when) | 6/10 inline in prose; 4 missing entirely |
| `user-service.md` | None | 10 | 2 col (Exception, Raised when) | 0/10 (deferred to `user-management.md`) |
| `api-key-service.md` | None | 6 | 2 separate tables | Complete (in a second table) |
| `package-service.md` | `PackageServiceError` | 12 | 4 col + system-internal sub-table | **Complete — already standardized** |

**Out of scope**: `cve-service.md` is excluded from this
standardization. The module defines no custom exception classes and
handles all errors internally (catch-and-continue pattern). No
exceptions propagate to API callers.

### Specific gaps

1. **No base class**: 3 of 5 modules lack a common base exception class.
   Without it, API handlers cannot use a single `except ModuleError`
   catch-all — they must enumerate every possible exception individually.

2. **HTTP status absent from exception tables**: `ticket-service.md` has
   error codes but no HTTP status column. `user-service.md` defers all
   mapping to `user-management.md`. `ticket-mutations.md` embeds partial
   mappings as inline prose. This forces implementers to cross-reference
   multiple documents to determine the correct HTTP response.

3. **Table structure inconsistent**: five different formats are used
   across the five modules (4-column, 3-column with "Raised by",
   3-column with "HTTP Status" + "Error Code", 2-column, and 2-column
   with separate mapping table).

4. **`ticket-mutations.md` partial inline mapping**: 6 of 10 exceptions
   have their HTTP/code mapping embedded in algorithm description prose
   rather than in a structured column. Four exceptions
   (`TicketNotFoundError`, `TicketNotMutableError`,
   `DuplicateCycleDetectedError`, `DuplicateChainDepthError`) have
   mappings only discoverable by searching other specs or inline prose
   outside the exceptions section.

5. **Multi-mapping exception**: `InvalidAssigneeError` in
   `ticket-service.md` maps to two different error codes based on a
   `reason` attribute, breaking the 1:1 exception-to-code principle.

6. **Missing exceptions in `ticket-service.md` table**:
   `InactiveUserError` (used by `grant_access`), `SeverityDerivedError`
   (used by `create_ticket`), and `DuplicateCycleDetectedError` (used by
   `mark_as_duplicate` via `resolve_canonical_target`) are referenced in
   function descriptions but absent from the Service Exceptions table.

7. **Naming inconsistency**: `ticket-service.md` uses
   `InvalidTransitionError` while `ticket-mutations.md` uses
   `TicketInvalidTransitionError` for the same `TICKET_INVALID_TRANSITION`
   error code. The shared exception rule requires a single canonical name.

8. **Unregistered error code**: `api-key-service.md` maps
   `InactiveUserError` to `AUTH_USER_INACTIVE`, but this code is not
   registered in `api-spec.md` Error Code Categories. The already-
   registered `USER_INACTIVE` should be used instead.

9. **HTTP status inconsistencies**:
   - `InactiveUserError` → 403 in `api-key-service.md`: this is not an
     authorization failure but a target resource state conflict (admin IS
     authorized; the target user is inactive). Should be 409.
   - `AssigneeInactiveError` → 400 in `ticket-service.md`: same pattern
     as `InactiveUserError` — the request is structurally valid, the
     acting user is authorized, but the target user's current state
     (inactive) prevents the operation. Should be 409.
   - `ADDerivedRoleError` → 400 in `user-service.md`: same pattern as
     `ADUserStatusReadOnlyError` (409) and `ADUserFieldReadOnlyError`
     (409) — the request is structurally valid but the resource state
     prevents the operation. Should be 409.
   - `ADUserPasswordError` → 400 in `user-service.md`: same pattern as
     all other AD-related exceptions (409) — "cannot do X because user
     is AD-sourced" is a state conflict, not a malformed request. Should
     be 409.

10. **Generic `VALIDATION_ERROR` reuse**: `PasswordValidationError` in
    `user-service.md` and `ApiKeyNameValidationError` in
    `api-key-service.md` both map to the generic `VALIDATION_ERROR` code,
    making them indistinguishable from Pydantic schema validation errors
    at the client level. Domain-specific codes should be used.

## Reference pattern

`package-service.md` (lines 848-879) defines the authoritative pattern:

- All exceptions inherit from a common `PackageServiceError` base class
- API-facing exceptions table has 4 columns: `Exception`, `HTTP`,
  `Code`, `Raised when`
- System-internal exceptions use a 3-column sub-table: `Exception`,
  `Raised when`, `Handling`
- Module-level rule: "API endpoint handlers catch
  `PackageServiceError` subclasses and map them to the corresponding
  HTTP status code and error code per `api-spec.md`."
- Every exception maps to exactly one HTTP status + error code (1:1)

## Proposed standard

Every service module that raises exceptions MUST follow this structure:

### 1. Base class declaration

```
All exceptions in this module inherit from `<Module>ServiceError`.
API endpoint handlers catch `<Module>ServiceError` subclasses and map
them to the corresponding HTTP status code and error code per
`api-spec.md`.
```

Base class names:

| Module | Base class |
|--------|-----------|
| `ticket-service` | `TicketServiceError` (already exists) |
| `ticket-mutations` | `TicketMutationsError` |
| `user-service` | `UserServiceError` |
| `api-key-service` | `ApiKeyServiceError` |
| `package-service` | `PackageServiceError` (already exists) |

### 2. Service Exceptions table format

All modules adopt the same 4-column table for API-facing exceptions:

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `ExampleError` | 409 | `DOMAIN_ERROR_CODE` | Brief semantic condition |

For exceptions that never reach an API handler (system-internal or
CLI/fetcher-only), a separate sub-table is used:

| Exception | Raised when | Handling |
|-----------|-------------|----------|
| `InternalError` | Semantic condition | How callers handle it |

#### "Raised when" column rules

- Describes the **semantic condition** that triggers the exception in
  one brief sentence
- Does NOT list function names or code paths (avoids drift)
- The condition should be stable — it is tied to the exception's
  meaning, not to implementation details

#### 1:1 mapping rule

Every API-facing exception MUST map to exactly one HTTP status code and
one error code. If an exception currently maps to multiple codes based
on an attribute (e.g., `reason`), it MUST be split into separate
exception classes — one per distinct error code.

#### Domain-specific validation codes

Domain-specific error codes (e.g., `USER_PASSWORD_POLICY_VIOLATION`,
`AUTH_API_KEY_NAME_INVALID`) do NOT carry the `errors` array in the
response body. The `errors` array remains exclusive to `VALIDATION_ERROR`
responses (Pydantic schema validation). Domain validation errors use the
standard `code` + `detail` envelope format.

### 3. Mapping lives in the service spec

The HTTP/code mapping for each exception MUST be documented in the
service module spec itself — not deferred to endpoint-level error
tables in other specs. Endpoint error tables reference the service
exception (for traceability) but the authoritative mapping is in the
service spec.

Every error code appearing in a service exception table MUST also be
registered in the Error Code Categories table in `api-spec.md`. The
service spec is the authoritative source for the exception-to-HTTP/code
mapping; `api-spec.md` is the authoritative registry of all valid error
codes.

#### Authority chain

The three-tier authority relationship for error information is:

1. **`api-spec.md`** — authoritative registry of all valid error codes
   (prefix, domain, enumeration)
2. **Service spec** — authoritative source for each exception's
   HTTP status code and error code mapping
3. **Endpoint error tables** (in feature specs like `tickets.md`,
   `user-management.md`) — reference the service spec's exceptions for
   traceability; not authoritative for the HTTP/code mapping

### 4. Shared exceptions

Exceptions used by multiple service modules (e.g., `TicketNotFoundError`
used by `ticket-service`, `ticket-mutations`, and `package-service`) are
defined once in a common exceptions module and imported by each service
that raises them. Each service spec MUST still list the exception in its
own table for completeness — the reader should not need to consult
another spec to know what a module can raise.

Shared exceptions are marked with `†` in the target tables below.

#### Inheritance hierarchy

All module base classes (`TicketServiceError`, `TicketMutationsError`,
etc.) inherit from a common `ServiceError` root class. Module-specific
exceptions inherit from their module's base class. Shared exceptions
(used across multiple modules) inherit from `ServiceError` directly —
they are NOT subclasses of any individual module's base class.

#### Handler requirements

Consequence for API handlers: handlers MUST catch each exception class
listed in the module's table individually. Using `except ModuleServiceError`
as a catch-all is insufficient when the table includes shared exceptions
(marked `†`), since those do not inherit from the module's base class.

A `except ServiceError` fallback MAY be added after all specific catches
for defense-in-depth, but MUST log a warning since it indicates a missing
specific handler.

### 5. Endpoint error tables (post-standardization)

After standardization, endpoint-level error tables in feature specs
(e.g., `tickets.md`, `user-management.md`) MUST NOT repeat the HTTP
status code for service exceptions. They reference the exception class
name and error code for traceability — the authoritative HTTP mapping
lives in the service spec. This eliminates dual-authority drift between
the service spec and the endpoint table.

Endpoint error tables retain their own HTTP status column only for
errors that do NOT originate from a service exception (e.g., framework-
level 401/403 from auth dependencies, 404 from path parameter
resolution).

## Spec changes required

### 1. `docs/features/tickets/ticket-service.md`

- Add `HTTP` column to the Service Exceptions table
- Replace `Raised by` column with `Raised when` (semantic condition)
- Split `InvalidAssigneeError` into two separate exceptions:
  - `AssigneeNotVAError` → 400, `TICKET_ASSIGNEE_NOT_VA`
  - `AssigneeInactiveError` → 409, `TICKET_ASSIGNEE_INACTIVE`
- Add missing exceptions: `InactiveUserError`, `SeverityDerivedError`,
  `DuplicateCycleDetectedError` (all referenced in function descriptions
  but absent from the current table)
- Update all function descriptions that reference `InvalidAssigneeError`
  to use the appropriate new exception name

Target table (11 → 15 exceptions after split and additions):

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `TicketNotFoundError` † | 404 | `TICKET_NOT_FOUND` | Ticket ID does not exist |
| `TicketNotMutableError` † | 409 | `TICKET_NOT_MUTABLE` | Ticket is in a terminal status |
| `InvalidTransitionError` † | 409 | `TICKET_INVALID_TRANSITION` | Requested status transition is not allowed |
| `TicketCVEAlreadySetError` | 400 | `TICKET_CVE_ALREADY_SET` | Ticket already has a CVE associated |
| `TicketCVENotSetError` | 400 | `TICKET_CVE_NOT_SET` | Ticket has no CVE to dissociate |
| `TicketCVEConflictError` | 409 | `TICKET_CVE_CONFLICT` | CVE is already associated with another ticket |
| `AssigneeNotVAError` | 400 | `TICKET_ASSIGNEE_NOT_VA` | Target user lacks the vulnerability_analyst role |
| `AssigneeInactiveError` | 409 | `TICKET_ASSIGNEE_INACTIVE` | Target user is inactive (for assignment) |
| `InactiveUserError` † | 409 | `USER_INACTIVE` | Target user is inactive (for access grant) |
| `SelfDuplicateError` | 400 | `TICKET_SELF_DUPLICATE` | Ticket cannot be marked as duplicate of itself |
| `DuplicateCycleDetectedError` † | 409 | `TICKET_DUPLICATE_CYCLE_DETECTED` | Duplicate resolution would create a cycle |
| `DuplicateChainDepthError` † | 409 | `TICKET_DUPLICATE_CHAIN_DEPTH` | Duplicate chain exceeds maximum allowed depth |
| `SeverityDerivedError` † | 409 | `TICKET_SEVERITY_DERIVED` | Cannot manually set severity when it is auto-derived |
| `TicketNotConfidentialError` | 409 | `TICKET_NOT_CONFIDENTIAL` | Operation requires a confidential ticket |
| `UserNotFoundError` † | 404 | `USER_NOT_FOUND` | Referenced user does not exist |

† Shared exception — inherits from `ServiceError`, not from
`TicketServiceError`. Handlers must catch it explicitly.

### 2. `docs/features/tickets/ticket-mutations.md`

- Add `TicketMutationsError` base class declaration
- Restructure Service Exceptions table to 4-column format
- Move all inline HTTP/code mappings from algorithm prose into the table
- Add missing mappings for shared exceptions

Target table:

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `TicketNotFoundError` † | 404 | `TICKET_NOT_FOUND` | Ticket ID does not exist |
| `TicketNotMutableError` † | 409 | `TICKET_NOT_MUTABLE` | Ticket is in a terminal status |
| `DuplicateCycleDetectedError` † | 409 | `TICKET_DUPLICATE_CYCLE_DETECTED` | Duplicate resolution would create a cycle |
| `DuplicateChainDepthError` † | 409 | `TICKET_DUPLICATE_CHAIN_DEPTH` | Duplicate chain exceeds maximum allowed depth |
| `DuplicateCVSSAssessmentError` | 409 | `CVSS_DUPLICATE_ASSESSMENT` | Assessment for this provider+version already exists |
| `CVSSAssessmentNotFoundError` | 404 | `CVSS_ASSESSMENT_NOT_FOUND` | CVSS assessment ID does not exist |
| `InvalidCVSSVectorError` | 422 | `CVSS_INVALID_VECTOR` | CVSS vector string is malformed or invalid |
| `CVSSVersionMismatchError` | 409 | `CVSS_VERSION_MISMATCH` | Vector version does not match the declared version |
| `InvalidTransitionError` † | 409 | `TICKET_INVALID_TRANSITION` | Requested status transition is not allowed |
| `SeverityDerivedError` † | 409 | `TICKET_SEVERITY_DERIVED` | Cannot manually set severity when it is auto-derived |

† Shared exception — inherits from `ServiceError`, not from
`TicketMutationsError`. Handlers must catch it explicitly.

### 3. `docs/features/identity/user-service.md`

- Add `UserServiceError` base class declaration
- Restructure to 4-column format with HTTP/code mappings consolidated
  from `user-management.md`
- Move `UsernameFormatError` to system-internal sub-table (no API
  endpoint triggers it; consumed only by CLI and LDAP sync fetcher)

Target API-facing table:

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `UserNotFoundError` † | 404 | `USER_NOT_FOUND` | User identifier does not resolve to any user |
| `UserConflictError` | 409 | `USER_ALREADY_EXISTS` | Username or email already in use |
| `SelfRoleRemovalError` | 409 | `USER_SELF_ROLE_REMOVAL` | Admin attempting to remove their own admin role |
| `SelfDeactivationError` | 409 | `USER_SELF_DEACTIVATION` | Admin attempting to deactivate themselves |
| `ADUserStatusReadOnlyError` | 409 | `USER_AD_STATUS_READONLY` | Cannot manually activate/deactivate an AD user |
| `ADDerivedRoleError` | 409 | `USER_AD_ROLE_PROTECTED` | Cannot manually modify AD-derived roles |
| `ADUserFieldReadOnlyError` | 409 | `USER_AD_FIELD_READONLY` | Cannot modify AD-synced fields on an AD user |
| `ADUserPasswordError` | 409 | `USER_AD_PASSWORD_FORBIDDEN` | Cannot set password for an AD user |
| `PasswordValidationError` | 422 | `USER_PASSWORD_POLICY_VIOLATION` | Password does not meet policy requirements |

† Shared exception — inherits from `ServiceError`, not from
`UserServiceError`. Handlers must catch it explicitly.

Target system-internal sub-table:

| Exception | Raised when | Handling |
|-----------|-------------|----------|
| `UsernameFormatError` | Username does not match format rules | CLI: stderr message + exit 1; LDAP sync: logged as warning, user skipped |

### 4. `docs/features/identity/api-key-service.md`

- Add `ApiKeyServiceError` base class declaration
- Merge the two existing tables (definition + HTTP mapping) into a
  single 4-column table

Target table:

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `UserNotFoundError` † | 404 | `USER_NOT_FOUND` | Owner user does not exist |
| `InactiveUserError` † | 409 | `USER_INACTIVE` | Owner user is inactive |
| `ApiKeyNotFoundError` | 404 | `AUTH_API_KEY_NOT_FOUND` | API key ID does not exist |
| `ApiKeyNameConflictError` | 409 | `AUTH_API_KEY_NAME_CONFLICT` | Key name already in use for this user |
| `ApiKeyNameValidationError` | 422 | `AUTH_API_KEY_NAME_INVALID` | Key name does not meet format requirements |
| `ApiKeyInvalidExpiryError` | 400 | `AUTH_API_KEY_INVALID_EXPIRY` | Expiry date is in the past or exceeds maximum |

† Shared exception — inherits from `ServiceError`, not from
`ApiKeyServiceError`. Handlers must catch it explicitly.

### 5. `docs/conventions.md`

Add a "Service Exception Conventions" subsection under the existing
"Feature Specifications" section (alongside "API Cross-references" and
"Fetcher Documentation"), documenting:

- Base class requirement (`<Module>ServiceError` inheriting from `ServiceError`)
- 4-column table format (Exception, HTTP, Code, Raised when)
- System-internal sub-table format
- 1:1 mapping rule
- Domain-specific validation codes (no `errors` array)
- "Raised when" content guidelines (semantic condition, not code paths)
- Mapping authority rule and three-tier authority chain
- Error code registration obligation (must also appear in `api-spec.md`)
- Shared exception import pattern and inheritance hierarchy
- Handler requirements (MUST catch individually when shared exceptions
  are present)
- Endpoint error table format (no HTTP status repetition for service
  exceptions)

## Incidental fixes

These documentation gaps were discovered during analysis and should be
addressed alongside or immediately after the standardization.

**Atomicity requirement**: all incidental fixes MUST be applied in the
same changeset as the corresponding service spec changes to avoid
temporary inter-spec contradictions.

1. **`docs/features/tickets/tickets.md`** — endpoint error table for
   "Mark Ticket as Duplicate" (around line 1517-1525) is missing
   `TICKET_DUPLICATE_CYCLE_DETECTED` (409). The error code exists in
   `api-spec.md` and in `ticket-mutations.md` inline prose, but the
   endpoint table omits it.

2. **`docs/features/tickets/ticket-service.md`** — split
   `InvalidAssigneeError` into `AssigneeNotVAError` and
   `AssigneeInactiveError`. All function descriptions in the spec that
   reference `InvalidAssigneeError` (assign_ticket, etc.) must be
   updated to reference the appropriate new exception. The `Raised by`
   column content should be converted to `Raised when` semantic
   descriptions during the same edit.

3. **`docs/features/tickets/ticket-service.md`** — Service Exceptions
   table is missing `InactiveUserError`, `SeverityDerivedError`, and
   `DuplicateCycleDetectedError`, all of which are referenced in
   function descriptions within the same spec.

4. **`docs/features/tickets/ticket-mutations.md`** — rename
   `TicketInvalidTransitionError` to `InvalidTransitionError` (unified
   canonical name shared with `ticket-service.md`). All references in
   the spec must use the new name.

5. **`docs/features/identity/api-key-service.md`** — `InactiveUserError`
   uses unregistered error code `AUTH_USER_INACTIVE`. Changed to
   `USER_INACTIVE` (already registered in `api-spec.md`). HTTP status
   changed from 403 to 409 (state conflict, not authorization failure).

6. **`docs/api-spec.md`** — Error Code Categories must be updated to
   register two new codes: `USER_PASSWORD_POLICY_VIOLATION` (USER\_*
   prefix) and `AUTH_API_KEY_NAME_INVALID` (AUTH\_* prefix). Remove the
   unused `AUTH_USER_INACTIVE` if present.

   Naming note: `USER_PASSWORD_POLICY_VIOLATION` is preferred over the
   shorter `USER_PASSWORD_INVALID` because it communicates the specific
   failure reason (policy requirements not met) rather than being
   ambiguous with authentication-related "invalid password" errors.

7. **`docs/features/identity/user-management.md`** — endpoint error
   tables referencing `PasswordValidationError` → `VALIDATION_ERROR`
   must be updated to use `USER_PASSWORD_POLICY_VIOLATION` (422).
   Similarly, `ADDerivedRoleError` HTTP status must be updated from 400
   to 409, and `ADUserPasswordError` from 400 to 409.

## Impact assessment

- **No data model changes**
- **Specification-level API contract changes** — since no implementation
  code exists yet, all changes below are purely specification corrections:
  - `InactiveUserError` HTTP status: 403 → 409 (in `api-key-service.md`
    and `ticket-service.md`)
  - `AssigneeInactiveError` HTTP status: 400 → 409 (in
    `ticket-service.md`)
  - `ADDerivedRoleError` HTTP status: 400 → 409 (in `user-service.md`)
  - `ADUserPasswordError` HTTP status: 400 → 409 (in `user-service.md`)
  - `PasswordValidationError` HTTP status: 400 → 422 (in
    `user-service.md`) — domain validation uses 422, consistent with
    `ApiKeyNameValidationError`
  - `PasswordValidationError` error code: `VALIDATION_ERROR` →
    `USER_PASSWORD_POLICY_VIOLATION` (in `user-service.md`)
  - `ApiKeyNameValidationError` error code: `VALIDATION_ERROR` →
    `AUTH_API_KEY_NAME_INVALID` (in `api-key-service.md`)
  - `AUTH_USER_INACTIVE` (unregistered) → `USER_INACTIVE` (registered)
- **Exception renames/splits**:
  - `InvalidAssigneeError` splits into `AssigneeNotVAError` +
    `AssigneeInactiveError`
  - `TicketInvalidTransitionError` renamed to `InvalidTransitionError`
    (unified canonical name)
- **New error codes to register in `api-spec.md`**:
  `USER_PASSWORD_POLICY_VIOLATION`, `AUTH_API_KEY_NAME_INVALID`
- **Missing error code added** — `TICKET_DUPLICATE_CYCLE_DETECTED` added
  to the Mark as Duplicate endpoint error table (already exists in
  `api-spec.md`)
- **Documentation restructuring** — the bulk of the work is reformatting
  existing information into a consistent structure

## Priority

Low — this is a quality-of-life improvement for implementers. The
information already exists (scattered across endpoint-level error
tables and inline prose); this draft proposes consolidating it into a
consistent, single-source-of-truth pattern.

`package-service.md` already follows the target pattern and requires no
changes.
