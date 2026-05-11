# Identity

User authentication, authorization, and lifecycle management.

## Specs

```
authentication.md          Session/JWT/API-key framework (umbrella)
├── sso-authentication.md  OIDC SSO login flow
└── local-authentication.md  Username/password login, lockout

ad-integration.md          SUSE AD sync, role mapping
user-service.md            Service-layer contract for user mutations
user-management.md         Admin CLI and API for user operations
rbac.md                    Role definitions and endpoint permission map
```

## Relationships

- `authentication.md` is the parent spec for SSO and local login —
  shared concerns (session lifecycle, token format, API keys) are
  defined there and inherited by sub-specs.
- `ad-integration.md` provisions users and derives roles via AD group
  mappings; `rbac.md` defines what those roles grant.
- `user-service.md` is the centralized service contract consumed by
  `user-management.md`, `ad-integration.md`, and any future entry point
  that mutates users.
