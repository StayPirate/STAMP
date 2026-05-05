# Local Authentication

## Purpose

Provide a credential-based login mechanism for local user accounts.
Local authentication allows users who do not have access to the SUSE SSO
(`id.suse.com`) to log in with a username and password. This covers
three primary use cases:

1. **Development and staging environments** where the SUSE internal
   network is not reachable
2. **AI agents and bots** that need to log in once to create an API key
   for ongoing programmatic access
3. **Environments without SSO** where Sentinel is deployed outside the
   SUSE corporate network

Local authentication is only available to users with `ldap_uid = NULL`
(local users). Users managed by LDAP sync (`ldap_uid IS NOT NULL`)
authenticate exclusively via SSO — see
`docs/features/sso-authentication.md`.

## Login Endpoint

### `POST /api/v1/auth/login`

Authenticates a local user with username and password, creates a
session, and returns a JWT.

**Authentication**: none (public endpoint).

**Request body**:

```json
{
  "username": "string (required)",
  "password": "string (required)"
}
```

**Behavior**:

1. If the provided password exceeds 128 characters, return HTTP 401 with
   generic message immediately (prevents resource exhaustion via hashing
   of large inputs — no database lookup needed)
2. Normalize the username: strip leading and trailing whitespace, then
   convert to lowercase. All subsequent steps (lookup, lockout counter)
   use the normalized value.
3. Look up the user by normalized `username`
4. If user not found, return HTTP 401 with generic message (see below)
5. If the account is locked (see Rate Limiting), return HTTP 423 with
   message: `"Account temporarily locked. Try again later."`
6. If user is inactive (`active = false`), return HTTP 401 with generic
   message
7. If user has `ldap_uid IS NOT NULL` (SSO user), return HTTP 401 with
   generic message — SSO users cannot use local login
8. If user has no `password_hash` set (local user without password),
   return HTTP 401 with generic message
9. Verify the provided password against the stored `password_hash`
10. If verification fails, increment the failed attempt counter and
    return HTTP 401 with generic message
11. On success: reset the failed attempt counter, create a `Session`
    record, update `user.last_login_at = now()`, issue a JWT (see
    `docs/features/authentication.md` for token format and claims),
    return the token

**Success response** (200):

```json
{
  "access_token": "eyJhbG...",
  "token_type": "bearer",
  "expires_at": "ISO8601"
}
```

**Error response** (401):

```json
{
  "detail": "Invalid username or password."
}
```

The error message is intentionally generic and identical for all failure
cases (user not found, wrong password, inactive user, SSO user) to
prevent username enumeration.

## Password Management

### Storage

Passwords are stored as Argon2id hashes in the `password_hash` column of
the `User` table. This column is nullable — it is `NULL` for SSO users
and for local users who have not yet been assigned a password.

### Hashing configuration

| Parameter       | Value                           |
|-----------------|---------------------------------|
| Algorithm       | Argon2id                        |
| Time cost       | 3 iterations                    |
| Memory cost     | 64 MiB                          |
| Parallelism     | 1                               |
| Hash length     | 32 bytes                        |
| Salt            | 16 bytes (auto-generated)       |

These values follow OWASP recommendations for Argon2id as of 2024.

### Setting a password

Passwords are set in two scenarios:

1. **At user creation** (CLI or admin UI): the admin provides the
   initial password for the local user
2. **Password reset** (CLI or admin UI): an admin sets a new password
   for an existing local user

There is no self-service password reset or password change in the
initial implementation. Users who need a password change must request it
from an admin.

### CLI commands

#### `sentinel manage-user create` (updated)

The existing `create` command gains an additional required parameter for
local users:

```
sentinel manage-user create \
  --username <username> \
  --email <email> \
  --password <password> \
  [--full-name <name>] \
  [--role <role>] ...
```

| Parameter    | Required | Description                           |
|--------------|----------|---------------------------------------|
| `--password` | Yes      | Initial password for the local user   |

**Password validation**:
- Minimum 12 characters
- Maximum 128 characters (prevents resource exhaustion via Argon2id
  hashing of arbitrarily large inputs)
- No complexity rules (uppercase, numbers, symbols) — length is the
  primary defense

If the password is too short, exit with error:
`"Error: Password must be at least 12 characters."`

If the password is too long, exit with error:
`"Error: Password must be at most 128 characters."`

#### `sentinel manage-user set-password`

Sets or resets the password for a local user.

```
sentinel manage-user set-password \
  --username <username> \
  --password <new_password>
```

**Behavior**:

1. Look up the user by `username` — if not found, exit with error:
   `"Error: User '{username}' not found."`
2. Call `user_service.reset_password(user_id, new_password,
   acting_user_id=None)` — this handles validation, hashing, and
   session invalidation (see `docs/features/user-lifecycle.md`)
3. Print: `"Password updated for user '{username}'. All active sessions
   have been invalidated."`

On `SSOUserPasswordError`: exit with error:
`"Error: Cannot set password for SSO user '{username}'. SSO users
authenticate via id.suse.com."`

On `PasswordValidationError`: exit with error:
`"Error: Password must be between 12 and 128 characters."`

**Exit codes**: 0 on success, 1 on validation error.

### Admin UI: password reset

The user management page in the administration panel includes a "Reset
password" action for local users. The admin enters the new password
(same validation: 12–128 characters). The behavior is identical to the
CLI `set-password` command, with `acting_user_id` set to the admin's
user ID.

For SSO users, the "Reset password" action is not available (greyed out
or hidden).

### `PUT /api/v1/admin/users/{user_id}/password`

API endpoint for admin password reset (used by the admin UI).

**Authentication**: required. **Permission**: `admin` role.

**Request body**:

```json
{
  "password": "string (required, 12-128 chars)"
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. Call `user_service.reset_password(user_id, password,
   acting_user_id=current_user.id)` — this handles SSO user check,
   validation, hashing, and session invalidation (see
   `docs/features/user-lifecycle.md`)
3. Return HTTP 200

On `SSOUserPasswordError`: return HTTP 400:
`"Cannot set password for SSO user. SSO users authenticate via
id.suse.com."`

On `PasswordValidationError`: return HTTP 400:
`"Password must be between 12 and 128 characters."`

**Response** (200):

```json
{
  "detail": "Password updated. All active sessions have been invalidated."
}
```

## Rate Limiting / Brute-Force Protection

### Account lockout

To protect against brute-force attacks, Sentinel tracks failed login
attempts per username using a Redis counter.

| Setting                   | Type | Default | Env var                     |
|---------------------------|------|---------|-----------------------------|
| `login_max_attempts`      | int  | `10`    | `LOGIN_MAX_ATTEMPTS`        |
| `login_lockout_minutes`   | int  | `15`    | `LOGIN_LOCKOUT_MINUTES`     |

**Behavior**:

1. On each failed login attempt for a username, increment a Redis
   counter: `login_attempts:{normalized_username}` (where
   `normalized_username` is the lowercased, trimmed input)
2. The counter has a TTL equal to `LOGIN_LOCKOUT_MINUTES` (auto-expires)
3. If the counter reaches `LOGIN_MAX_ATTEMPTS`, subsequent login
   attempts for that username are rejected immediately with HTTP 423
   until the TTL expires
4. On successful login, delete the counter

**Notes**:

- Lockout is per-username, not per-IP. This is simpler and sufficient
  for an internal tool.
- The lockout does not affect API key authentication (API keys bypass
  the login endpoint entirely).
- An admin can unlock a locked account via `sentinel manage-user unlock
  --username <name>` (CLI) or the "Unlock" action in the admin user
  management page. Alternatively, the lockout expires automatically
  after the TTL. See `docs/features/user-management.md`.
- **Redis unavailability**: if Redis is unreachable, the login endpoint
  operates in **fail-open** mode — login proceeds without rate limiting.
  This prioritizes availability over brute-force protection. The
  rationale: Sentinel is an internal tool on a trusted network; a
  Redis outage should not lock all users out of the system. The Redis
  connection failure should be logged as a warning for operators.
- **Configuration bounds**: `LOGIN_MAX_ATTEMPTS` must be >= 1.
  `LOGIN_LOCKOUT_MINUTES` must be >= 1. Values of 0 or negative are
  treated as their defaults (10 and 15 respectively) with a startup
  warning logged.

## Login Page

The login page always displays the username/password form. The "Login
with SUSE SSO" button is rendered only when SSO is configured — the
frontend determines this by calling `GET /api/v1/auth/providers` (see
`docs/features/sso-authentication.md`).

- **"Login with SUSE SSO" button** (conditional): initiates the SSO flow
- **Username/password form** (always visible): submits to
  `POST /api/v1/auth/login`

The local login form does not check whether local users exist — it
simply returns an authentication error if the credentials are invalid.

### Frontend behavior on login success

1. Store the JWT (see `docs/features/authentication.md`, Security
   Considerations for storage mechanism)
2. Redirect to the dashboard (or the originally requested page if the
   user was redirected to login)

### Frontend behavior on token expiration

When any API call returns HTTP 401 and the user has a stored token
(indicating the token has expired rather than being absent):

1. Clear the stored token
2. Redirect to the login page
3. Optionally display a message: "Your session has expired. Please log
   in again."

## Security Considerations

- **Generic error messages**: the login endpoint never reveals whether a
  username exists. All failure cases return the same 401 message.
- **Argon2id**: resistant to GPU attacks and side-channel attacks.
  Recommended over bcrypt for new implementations.
- **No password complexity rules**: research shows that length is more
  effective than complexity requirements. The 12-character minimum
  provides adequate entropy.
- **Session invalidation on password change**: prevents continued access
  with old credentials after a password reset.
- **Redis-based lockout**: survives application restarts, shared across
  all API server instances.
- **No password in JWT**: the JWT contains only user ID, session ID, and
  roles. The password hash is never included in tokens or API responses.
- **Password not returned by API**: no endpoint returns the
  `password_hash` field. Pydantic response schemas must explicitly
  exclude it.

## Cross-references

- `docs/features/authentication.md` — shared authentication framework
  (JWT format, session model, API keys, middleware)
- `docs/features/sso-authentication.md` — SSO login flow (alternative
  provider)
- `docs/features/user-management.md` — creating and managing
  local user accounts
- `docs/features/user-lifecycle.md` — deactivation side effects
