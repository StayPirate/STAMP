# Identity

User authentication, authorization, and lifecycle management.

## Specs

```
authentication.md              Session/JWT framework, middleware
├── sso-authentication.md      OIDC SSO login flow (deferred)
└── local-authentication.md    Username/password login, lockout

api-key-management.md          API key feature (data model, endpoints, CLI)
api-key-service.md             API key lifecycle service contract
identity-provisioning.md       External provisioning, role mapping (deferred)
user-service.md                Service-layer contract for user mutations
user-management.md             Admin CLI and API for user operations
rbac.md                        Role definitions and endpoint permission map
identity-audit-log.md          Identity audit trail (IdentityAuditEvent)
```

## Relationships

- `authentication.md` is the parent spec for SSO and local login —
  shared concerns (session lifecycle, token format, middleware) are
  defined there and inherited by sub-specs.
- `api-key-management.md` is the single source of truth for the API key
  feature. `api-key-service.md` defines the service-layer implementation
  contract consumed by endpoint handlers and CLI commands.
- `identity-provisioning.md` (deferred) defines how external users are
  provisioned and how group memberships map to roles; `rbac.md` defines
  what those roles grant.
- `user-service.md` is the centralized service contract consumed by
  `user-management.md`, `identity-provisioning.md`, and any future
  entry point that mutates users.
