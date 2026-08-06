# Identity

User authentication, authorization, and lifecycle management.

## Specs

```
authentication.md              Session/JWT and credential validation
├── sso-authentication.md      OIDC SSO login flow (deferred)
└── local-authentication.md    Username/password login, lockout

api-key-management.md          API key lifecycle, REST API, and CLI
api-key-service.md             API key mutation and query service
identity-provisioning.md       External provisioning, role mapping (deferred)
user-service.md                Service-layer contract for user mutations
user-management.md             Admin CLI and API for user operations
rbac.md                        Role definitions and endpoint permission map
identity-audit-log.md          Identity audit trail (IdentityAuditEvent)
```

## Relationships

- `authentication.md` is the parent spec for SSO and local login. It also
  validates API keys as credentials; `api-key-management.md` owns their
  lifecycle and consumer surfaces, and `api-key-service.md` owns database
  operations.
- `identity-provisioning.md` (deferred) defines how external users are
  provisioned and how group memberships map to roles; `rbac.md` defines
  what those roles grant.
- `user-service.md` is the centralized service contract consumed by
  `user-management.md`, `identity-provisioning.md`, and any future
  entry point that mutates users.
