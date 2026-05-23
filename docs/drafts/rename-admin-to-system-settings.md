# Draft: Rename `admin.md` to `system-settings.md`

## Context

The spec `docs/features/platform/admin.md` defines system-wide platform
settings (currently only `default_cvss_version`) and the associated setting
audit log. The name `admin.md` is too generic — it could refer to user
management, RBAC, or any other administrative function. The new name
`system-settings.md` clearly communicates that the spec covers global
configuration settings managed by admins.

Note: API endpoint paths (`/api/v1/admin/settings`) remain unchanged — only
the spec file is being renamed.

## Execution Plan

### 1. Rename the file

```bash
git mv docs/features/platform/admin.md docs/features/platform/system-settings.md
```

### 2. Update the spec heading and opening paragraph

Inside `docs/features/platform/system-settings.md`:

- Line 1: `# Administration` → `# System Settings`
- Line 6: "The Admin panel provides settings…" → "The System Settings page
  provides settings…"

### 3. Update references (19 occurrences across 10 files)

| File | Lines | Change |
|------|-------|--------|
| `docs/data-model.md` | 434, 962 | `platform/admin.md` → `platform/system-settings.md` |
| `docs/features/tickets/cvss-scoring.md` | 23, 588, 631 | idem |
| `docs/features/packages/package-model.md` | 375, 1895 | idem |
| `docs/features/packages/product-catalog.md` | 249 | idem |
| `docs/features/identity/rbac.md` | 271-273 | Update links and anchors: `../platform/system-settings.md#...`; change link text from `[admin]` to `[system settings]` |
| `docs/features/platform/audit-trail-infrastructure.md` | 301, 365 | idem |
| `docs/features/platform/README.md` | 11, 20 | Update filename references |
| `docs/features/README.md` | 52 | Update link and description |
| `docs/system-map.md` | 576 | `[admin](features/platform/admin.md)` → `[system-settings](features/platform/system-settings.md)` |
| `docs/drafts/ideas.md` | 12 | Update path |

### 4. Update the review directory

- `docs/reviews/admin.md`:
  - Line 1: `# Review: admin` → `# Review: system-settings`
  - Line 3: `docs/features/platform/admin.md` → `docs/features/platform/system-settings.md`
  - Line 37: `docs/features/platform/admin.md` → `docs/features/platform/system-settings.md`
  - Line 41: `docs/features/platform/admin.md` → `docs/features/platform/system-settings.md`
- Rename the review file:
  ```bash
  git mv docs/reviews/admin.md docs/reviews/system-settings.md
  ```
- `docs/reviews/README.md` (line 56): `- admin` → `- system-settings`
- `docs/reviews/.tracking.json` (line 19): rename the JSON key `"admin"` →
  `"system-settings"`

### 5. Run necessary reviewers

- `@docs-reviewer` — verify documentation remains coherent after the rename
- `@spec-coherence-reviewer` — verify no cross-reference is left orphaned

### 6. Delete this draft

- Remove `docs/drafts/rename-admin-to-system-settings.md` once execution is
  complete
