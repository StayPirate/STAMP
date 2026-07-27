# Restructure Deployment Guide

## Objective

Reorganize the structure of `docs/deployment.md` to improve readability,
navigability, and logical grouping — without modifying any content
(prose, tables, code blocks, or technical details remain unchanged).

## Problems Addressed

1. **No Table of Contents** — 756-line document with no navigation aid
2. **Timezone/Locale misplaced** — under "Production Deployment" but
   applies to all environments
3. **No separation between environment-specific and cross-cutting
   sections** — reader cannot quickly scan for their deployment context
4. **"Release Process" interrupts operational flow** — CI/CD pipeline
   details sit between environment guides and operational references
5. **"Database Migrations" too thin as standalone H2** — 20 lines,
   content partially duplicated in environment sections
6. **Inconsistent hierarchy depth** — External Service Registration
   uses H4 for a single procedure; Health Checks is flat H2

## Target Structure (Final Outline)

```
# Deployment Guide
  [Table of Contents]

  ## Prerequisites
    ### Software Requirements
    ### Network Access
    ### Timezone and Locale Requirements
      #### Locale for git worker containers

  ## External Service Registration
    ### IdP Client Registration (id.suse.com)
      #### Steps
      #### Environment-Specific Configuration

  ## Environments
    ### Local Development
      #### Quick Start
      #### Local Environment Variables
      #### Creating the First Local User
    ### Staging Deployment
      #### Configuration Checklist
      #### Deployment Steps
      #### Staging-Specific Notes
    ### Production Deployment
      #### Configuration Checklist
      #### Deployment Steps
      #### Pre-Production Checklist
      #### Production-Specific Notes

  ## Release Process
    ### How It Works
    ### Creating a Release
    ### Squash Merge
    ### Changelog
    ### Pipeline Chain
    ### Version Locations
    ### Configuration Files
    ### Repository Secret

  ## Process Architecture
    ### Singleton Processes
    ### Startup Ordering
    ### Git Worker Volume

  ## Operations
    ### Database Migrations
    ### Health Checks
    ### Redis Durability, Memory, and Persistence
      #### Persistence is Disabled by Design
      #### Memory Configuration
      #### Monitoring Scheduler Liveness (Recommended)
    ### Log Aggregation
      #### Docker / Podman
      #### Kubernetes
      #### Process-role identification
      #### LOG_LEVEL=DEBUG risk in production
    ### Troubleshooting
      #### SSO Login Fails
      #### Celery Tasks Not Running
```

## Execution Phases

Each phase produces exactly one commit. After each commit, verify via
`git diff HEAD~1` that no content was lost or altered — only structural
placement and heading levels change.

### Phase 1 — Move Timezone and Locale to Prerequisites

**What moves**:
- "Timezone and Locale Requirements" (H3) and its child "Locale for git
  worker containers" (H4) move from under "Production Deployment" to
  under "Prerequisites" (as the last H3 in that section)

**Heading level changes**: none (stays H3/H4)

**Verification**: diff shows only line relocation within the file; no
content bytes added or removed.

### Phase 2 — Group environments under "Environments" H2

**What changes**:
- New H2 section "Environments" created before "Local Development"
- "Local Development", "Staging Deployment", "Production Deployment"
  become H3 sections under it
- All their children drop one heading level (H3 → H4)

**Heading level changes**:
| Current | New |
|---------|-----|
| `## Local Development` | `### Local Development` |
| `### Quick Start` | `#### Quick Start` |
| `## Staging Deployment` | `### Staging Deployment` |
| `### Configuration Checklist` | `#### Configuration Checklist` |
| `## Production Deployment` | `### Production Deployment` |
| `### Configuration Checklist` | `#### Configuration Checklist` |
| (etc.) | |

**Verification**: diff shows heading level changes + new H2 line; all
content paragraphs/tables/code blocks unchanged.

### Phase 3 — Move Database Migrations to Operations

**What moves**:
- "Database Migrations" H2 section (lines 400-419) moves to the
  beginning of "Operations" as its first H3 subsection (before "Health
  Checks")

**Rationale**: Database migrations are an operational procedure executed
by operators during deployment — not part of the CI/CD release pipeline.
An operator looking for "how do I run migrations?" would naturally scan
"Operations", not "Release Process". Placing it as the first Operations
entry gives chronological sense: migrate first, then verify health.

**Heading level changes**:
| Current | New |
|---------|-----|
| `## Database Migrations` | `### Database Migrations` |

**Verification**: diff shows removal at old location + insertion at new
location; content identical byte-for-byte.

### Phase 4 — Create Operations grouping

**What changes**:
- New H2 section "Operations" created after "Process Architecture"
- "Health Checks", "Redis Durability, Memory, and Persistence",
  "Log Aggregation", and "Troubleshooting" become H3 sections under it
- Their children drop one heading level:
  - H3 → H4 (e.g., "Docker / Podman", "SSO Login Fails")

**Heading level changes**:
| Current | New |
|---------|-----|
| `## Health Checks` | `### Health Checks` |
| `## Redis Durability, Memory, and Persistence` | `### Redis Durability, Memory, and Persistence` |
| `### Persistence is Disabled by Design` | `#### Persistence is Disabled by Design` |
| `### Memory Configuration` | `#### Memory Configuration` |
| `### Monitoring Scheduler Liveness (Recommended)` | `#### Monitoring Scheduler Liveness (Recommended)` |
| `## Log Aggregation` | `### Log Aggregation` |
| `### Docker / Podman` | `#### Docker / Podman` |
| `### Kubernetes` | `#### Kubernetes` |
| `### Process-role identification` | `#### Process-role identification` |
| `### LOG_LEVEL=DEBUG risk in production` | `#### LOG_LEVEL=DEBUG risk in production` |
| `## Troubleshooting` | `### Troubleshooting` |
| `### SSO Login Fails` | `#### SSO Login Fails` |
| `### Celery Tasks Not Running` | `#### Celery Tasks Not Running` |

**Verification**: diff shows heading level changes + new H2 line; all
content unchanged.

### Phase 5 — Fix hierarchy depth (External Service Registration)

**What changes**:
- "External Service Registration" currently has:
  H2 → H3 (IdP Client Registration) → H4 (Steps, Environment-Specific
  Configuration)
- This is already 3 levels deep which is acceptable for a registration
  procedure. However, if the hierarchy feels excessive for a single
  entry, we can flatten:
  - Keep H2 → H3 → H4 as-is (it's correct for future extensibility —
    more services may be added)

**Decision**: no change needed. The current depth is justified by the
content structure (registration procedure with sub-steps). This phase
becomes a no-op verification that the hierarchy is already consistent
with the rest of the document after phases 1-4.

**If the phase is a no-op**: skip the commit, document the decision in
the commit log of the next phase.

### Phase 6 — Add Table of Contents

**What adds**:
- A `## Contents` section immediately after the introductory paragraph
  (before the first `---`), listing all H2 and H3 sections with
  markdown anchor links

**Format** (matches `docs/conventions.md` style):
```markdown
## Contents

- [Prerequisites](#prerequisites)
  - [Software Requirements](#software-requirements)
  - [Network Access](#network-access-stagingproduction)
  - [Timezone and Locale Requirements](#timezone-and-locale-requirements)
- [External Service Registration](#external-service-registration)
  - [IdP Client Registration](#idp-client-registration-idsusecom)
- [Environments](#environments)
  - [Local Development](#local-development)
  - [Staging Deployment](#staging-deployment)
  - [Production Deployment](#production-deployment)
- [Release Process](#release-process)
  - [How It Works](#how-it-works)
  - ...
- [Process Architecture](#process-architecture)
  - ...
- [Operations](#operations)
  - [Health Checks](#health-checks)
  - [Redis Durability, Memory, and Persistence](#redis-durability-memory-and-persistence)
  - [Log Aggregation](#log-aggregation)
  - [Troubleshooting](#troubleshooting)
```

**Verification**: diff shows only new lines added (no existing content
modified).

### Phase 7 — Fix broken anchor links across the project

**What to check**:
- Search all `docs/**/*.md` and `AGENTS.md` for links targeting
  `docs/deployment.md` with anchors (e.g.,
  `docs/deployment.md#database-migrations`,
  `docs/deployment.md#process-architecture`)
- Identify anchors that changed due to heading level or section moves
- Update each broken reference to the new anchor

**Known anchors that may break**:
- `#database-migrations` — now nested under Operations (anchor
  itself unchanged, but verify)
- `#timezone-and-locale-requirements` — moved to Prerequisites (anchor
  unchanged, just position)
- `#health-checks` — now under Operations (anchor unchanged)
- Any anchor referencing the old standalone H2 sections that are now H3

**Note**: Markdown anchors are generated from heading text, not heading
level. Moving a section or changing its level (H2 → H3) does NOT change
the anchor. Only renaming the heading text changes the anchor. Therefore
most links should remain valid. This phase verifies rather than assumes.

**Verification**: grep for all links to `deployment.md`, confirm each
anchor resolves to an existing heading in the restructured file.

### Phase 8 — Run reviewers

Invoke the following reviewers on the restructured `docs/deployment.md`:

1. **`@docs-reviewer`** — verify documentation completeness and
   coherence with implementation specs
2. **`@docs-placement-reviewer`** — verify that no information was
   misplaced during the restructuring

Address any issues identified before proceeding.

### Phase 9 — Delete this draft

Remove `docs/drafts/restructure-deployment-guide.md` and commit.

## Rollback

If any phase introduces information loss that cannot be corrected:

```bash
git revert <commit-hash>
```

Each phase is an independent commit, so individual phases can be
reverted without affecting others (unless later phases depend on the
structural change — in which case, revert in reverse order).

## Out of Scope

- Content modifications (rewording, adding/removing information)
- Changes to other documentation files (except anchor link fixes)
- Any implementation code changes
