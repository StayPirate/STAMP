# SSO Authentication

## Purpose

Provide single sign-on authentication for Sentinel using the SUSE
corporate identity provider (`id.suse.com`) via the OpenID Connect
(OIDC) protocol. This is the primary authentication method for SUSE
employees whose accounts are managed by Active Directory and synced into
Sentinel via the `sync_ldap_directory` fetcher.

SSO authentication is available only to users with `ldap_uid IS NOT NULL`
(LDAP-synced users). Local users (`ldap_uid = NULL`) authenticate via
the local login endpoint — see `docs/features/local-authentication.md`.

## Configuration

| Setting             | Type   | Default | Env var               |
|---------------------|--------|---------|-----------------------|
| `sso_issuer_url`    | string | —       | `SSO_ISSUER_URL`      |
| `sso_client_id`     | string | —       | `SSO_CLIENT_ID`       |
| `sso_client_secret` | string | —       | `SSO_CLIENT_SECRET`   |
| `sso_redirect_uri`  | string | —       | `SSO_REDIRECT_URI`    |

All settings are required for SSO to function. If any is missing, the
SSO login button on the login page should still be displayed, but
clicking it returns an error: `"SSO is not configured in this
environment."` This allows the same frontend build to be deployed in
both SSO-capable and SSO-less environments.

### Discovery

Sentinel uses OIDC Discovery to resolve the authorization endpoint,
token endpoint, and JWKS URI automatically from:

```
{SSO_ISSUER_URL}/.well-known/openid-configuration
```

This avoids hardcoding endpoint URLs and allows seamless migration if
the IdP changes its URL structure.

## OIDC Flow: Authorization Code

Sentinel uses the Authorization Code flow (not Implicit) as recommended
by OAuth 2.1 and OIDC best practices. PKCE (Proof Key for Code
Exchange) is used if supported by the IdP.

### Step 1: Login initiation

When the user clicks "Login with SUSE SSO" on the login page, the
frontend calls:

#### `GET /api/v1/auth/sso/authorize`

**Authentication**: none (public endpoint).

**Behavior**:

1. Generate a cryptographically random `state` parameter (32 bytes,
   hex-encoded) and store it in a short-lived Redis key
   (`sso_state:{state}`, TTL 10 minutes)
2. If PKCE is used: generate `code_verifier` and `code_challenge`, store
   the verifier in Redis alongside the state
3. Construct the authorization URL:
   ```
   {authorization_endpoint}?
     response_type=code&
     client_id={SSO_CLIENT_ID}&
     redirect_uri={SSO_REDIRECT_URI}&
     scope=openid profile email&
     state={state}&
     code_challenge={code_challenge}&      (if PKCE)
     code_challenge_method=S256            (if PKCE)
   ```
4. Return the URL to the frontend

**Response** (200):

```json
{
  "authorization_url": "https://id.suse.com/authorize?..."
}
```

The frontend then redirects the browser to this URL.

### Step 2: IdP authentication

The user authenticates at `id.suse.com` (credentials managed by SUSE
AD). On success, the IdP redirects the browser back to Sentinel's
callback URL with an authorization `code` and `state` parameter.

### Step 3: Callback processing

#### `POST /api/v1/auth/sso/callback`

**Authentication**: none (public endpoint).

**Request body**:

```json
{
  "code": "string (required)",
  "state": "string (required)"
}
```

**Behavior**:

1. Validate `state`: look up `sso_state:{state}` in Redis. If not found
   or expired, return HTTP 400:
   `"Invalid or expired SSO state. Please try again."`
2. Delete the state key from Redis (one-time use)
3. Exchange the `code` for tokens at the IdP's token endpoint:
   ```
   POST {token_endpoint}
   grant_type=authorization_code&
   code={code}&
   redirect_uri={SSO_REDIRECT_URI}&
   client_id={SSO_CLIENT_ID}&
   client_secret={SSO_CLIENT_SECRET}&
   code_verifier={code_verifier}          (if PKCE)
   ```
4. If the token exchange fails, return HTTP 401:
   `"SSO authentication failed. Please try again."`
5. Validate the ID token:
   - Verify signature against the IdP's JWKS
   - Verify `iss` matches `SSO_ISSUER_URL`
   - Verify `aud` contains `SSO_CLIENT_ID`
   - Verify `exp` has not passed
6. Extract the `sub` claim from the ID token
7. Look up the user by `ldap_uid = sub` in the `User` table
8. If user not found, return HTTP 401:
   `"No Sentinel account found for this identity. Contact your
   administrator."`
9. If user is inactive (`active = false`), return HTTP 401:
   `"Your account has been deactivated. Contact your administrator."`
10. Create a `Session` record (see
    `docs/features/authentication.md`, Session Management)
11. Issue a JWT with the session and user claims
12. Return the token

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
  "detail": "error message"
}
```

Unlike the local login endpoint, SSO error messages can be specific
(not generic) because:
- The username is not provided by the user (it comes from the IdP)
- There is no brute-force vector (the IdP handles credential
  verification)

### Frontend flow

1. User clicks "Login with SUSE SSO"
2. Frontend calls `GET /api/v1/auth/sso/authorize`
3. Frontend redirects browser to the returned `authorization_url`
4. User authenticates at id.suse.com
5. Browser is redirected back to Sentinel (e.g. `/auth/callback`)
6. Frontend extracts `code` and `state` from URL parameters
7. Frontend calls `POST /api/v1/auth/sso/callback` with code and state
8. On success: store JWT, redirect to dashboard
9. On error: display error message on login page

## Identity Mapping

The `sub` claim from the IdP's ID token is matched against the
`ldap_uid` field in the `User` table. This works because:

- The `sync_ldap_directory` fetcher imports users from SUSE Active
  Directory and stores their `sAMAccountName` as `ldap_uid`
- `id.suse.com` uses the same AD as its identity source, so its `sub`
  claim corresponds to the `sAMAccountName`

**Critical assumption**: the `sub` claim from `id.suse.com` is the bare
`sAMAccountName` (e.g. `"mrossi"`), not a decorated form (e.g.
`"mrossi@suse.com"` or a UUID). If the IdP changes its `sub` format,
all SSO logins will fail silently. The implementation MUST:

1. Log the `sub` value at DEBUG level on every SSO login attempt
2. Log a WARNING when a `sub` value does not match any `ldap_uid`,
   including the unmatched value for diagnostic purposes
3. The matching is case-sensitive and exact (no normalization)

### No auto-provisioning

If the `sub` claim does not match any `ldap_uid` in the database, the
login **fails**. Sentinel does not auto-create user records during SSO
login. The user must already exist in the database (created by the LDAP
sync process).

This is a deliberate design choice:

- It prevents orphan accounts from users who are not in Sentinel's
  managed AD groups
- It ensures that role mappings (based on AD group membership) are
  applied before the user can access the system
- It keeps the LDAP sync as the single source of truth for SSO user
  provisioning

## Logout

### Sentinel logout

The standard logout endpoint (`POST /api/v1/auth/logout`) invalidates
the Sentinel session. This is the same endpoint used by local users (see
`docs/features/authentication.md`).

### No Single Logout (SLO)

Logging out of Sentinel does **not** log the user out of `id.suse.com`,
and logging out of `id.suse.com` does **not** invalidate the Sentinel
session.

This is a deliberate design decision:

- SLO (Single Logout) adds significant complexity (backchannel logout
  endpoints, session tracking at the IdP)
- For an internal tool, the risk is minimal: if a user's session is
  compromised, an admin can deactivate the user, which immediately
  invalidates all sessions
- The Sentinel JWT expires naturally within the configured duration
  (default 7 days)

## Login Page

The login page displays both authentication options:

```
┌─────────────────────────────────────┐
│             Sentinel                │
│                                     │
│   [ Login with SUSE SSO ]           │
│                                     │
│   ─────────── or ───────────       │
│                                     │
│   Username: [__________________]    │
│   Password: [__________________]    │
│   [ Login ]                         │
│                                     │
└─────────────────────────────────────┘
```

Both options are always visible. If SSO is not configured, clicking the
SSO button shows an error message inline (does not redirect).

## Security Considerations

- **Authorization Code flow**: the client secret is never exposed to
  the browser. Token exchange happens server-side.
- **PKCE**: provides additional protection against authorization code
  interception, even for confidential clients.
- **State parameter**: prevents CSRF attacks on the callback endpoint.
  Single-use with a short TTL.
- **ID token signature verification**: prevents token forgery. JWKS are
  fetched from the IdP and cached (with periodic refresh).
- **SSO_CLIENT_SECRET**: must be stored securely (environment variable,
  never in code or logs).
- **No auto-provisioning**: limits the blast radius of an IdP
  compromise — only pre-existing users in Sentinel's database can
  obtain access.
- **Specific error messages**: SSO errors can be descriptive (unlike
  local login) because the identity comes from the IdP, not from user
  input.

## Cross-references

- `docs/features/authentication.md` — shared authentication framework
  (JWT format, session model, API keys, middleware)
- `docs/features/local-authentication.md` — local login (alternative
  provider)
- `docs/features/ldap-directory.md` — LDAP sync that provisions SSO
  user accounts
- `docs/features/user-lifecycle.md` — deactivation side effects
