# Draft: Versioning and Release Process

Status: **Draft — pending review**

## Motivation

Sentinel is transitioning from specification phase to gradual
implementation. Before writing code, the project needs a versioning
strategy and release process. Currently:

- The version `0.1.0` is hardcoded in two unsynchronized locations
  (`backend/pyproject.toml` line 3 and `backend/app/main.py` line 13)
- No changelog exists
- No release tooling or automation is configured
- `docs/deployment.md` mentions production deploys from `v*` tags but
  no process defines how those tags are created
- `.github/workflows/build-images.yml` already supports semver Docker
  image tagging from git tags, but nothing creates those tags

This draft defines the versioning strategy, release process, and a
prescriptive action plan for applying the changes to the project
specifications and configuration.

---

## Analysis

### Architectural Constraint: Single Deployable Unit

All five process roles run from the **same Docker image** with different
entrypoints (per `docs/architecture.md`, Container Images):

1. API server (uvicorn)
2. Celery worker
3. Git worker (Celery)
4. Celery Beat
5. IBS RabbitMQ consumer

They cannot be deployed at different versions. This is a hard
architectural constraint that eliminates per-component versioning.

### Fetchers Are Built-In, Not Plugins

Fetchers are Python classes registered via `__init_subclass__` at import
time (per `docs/features/platform/fetcher-infrastructure.md`). Adding a
fetcher requires adding code to the repository and rebuilding the image.
There is no plugin API, no dynamic loading, and no independent
deployment. Per-fetcher versioning has no practical meaning.

### Frontend Lives in a Separate Repository

The frontend will be developed in a dedicated repository against the
published OpenAPI contract (per `docs/architecture.md`, Repository
Scope). It will have its own independent version lifecycle. There is no
need to coordinate frontend/backend versions within this repository.

### Conventional Commits Already Adopted

The project already uses Conventional Commits format
(`docs/conventions.md`, Git Conventions). This provides the semantic
signal needed for automated version bumping.

### Existing CI/CD Infrastructure

`.github/workflows/build-images.yml` already:

- Triggers on `v*` tag pushes
- Uses `docker/metadata-action` with `type=semver` patterns
- Pushes to `ghcr.io` with tags `<version>`, `<major>.<minor>`, and
  `latest`

This infrastructure is ready to consume version tags without
modification.

---

## Decisions

### D1: Single Platform Version

**Decision**: Sentinel uses one version number for the entire platform.

**Rationale**: a single Docker image means a single deployable unit.
Per-component versioning (e.g., per-fetcher or separate API version)
would add management overhead without practical benefit, since all
components are always deployed together. The existing URL-path API
versioning (`/api/v1/`) already handles API compatibility for consumers.

### D2: Semantic Versioning (SemVer 2.0.0)

**Decision**: follow [SemVer 2.0.0](https://semver.org/).

**Interpretation for Sentinel** (a deployed platform, not a library):

| Bump | Trigger |
|------|---------|
| MAJOR | Breaking REST API changes (removal/renaming of fields, semantic changes to existing behavior, error code changes), database migrations requiring manual operator intervention, fundamental architectural changes |
| MINOR | New API endpoints, new fetchers, new features, non-breaking database migrations, new CLI commands |
| PATCH | Bug fixes, security patches, performance improvements, operational fixes |

Only `feat:` and `fix:` commits (and their `!` breaking variants)
trigger version bumps. Commits with `docs:`, `chore:`, `test:`,
`refactor:`, or `ci:` types do not produce a release on their own.

**Pre-1.0 rules** (current phase):

- The API is not considered stable
- Breaking changes MAY occur in minor version bumps (`0.x` → `0.x+1`)
- Consumers should pin to exact versions, not ranges

### D3: 1.0.0 Graduation Criteria

The project reaches `1.0.0` when ALL of the following conditions are
met:

1. **Production operational**: a production instance is deployed and
   serving real users
2. **Core ingestion functional**: all core CVE fetchers (NVD, MITRE, and
   at least one additional source) are implemented and running in
   production
3. **Ticket lifecycle complete**: the full ticket lifecycle — from CVE
   ingestion through analysis to resolution — is functional end-to-end
4. **Authentication operational**: both local authentication and SSO are
   implemented and operational in production
5. **API stability demonstrated**: the REST API v1 surface has had no
   breaking changes for at least 4 weeks of production operation
6. **Schema stability demonstrated**: the database schema has had no
   breaking migrations (requiring manual intervention) for at least 2
   consecutive minor releases

From `1.0.0` onward, breaking REST API changes require a major version
bump and the API versioning policy in `docs/api-spec.md` (Versioning)
takes full effect.

**Rationale**: this criterion is pragmatic and based on observable
stability rather than feature completeness. New features continue to be
added in `1.x.y` releases. The 4-week API stability window gives
consumers (the future frontend) enough time to validate the contract.
The 2-release schema stability window confirms that the data model has
settled.

### D4: release-please for Automation

**Decision**: use
[release-please](https://github.com/googleapis/release-please) (GitHub
Action) for automated versioning, changelog generation, and GitHub
Releases.

**Alternatives considered**:

| Tool | Rejected because |
|------|-----------------|
| semantic-release | Auto-publishes on merge with no review gate. Requires Node.js/npm in the project. More complex plugin ecosystem |
| Manual (bump-my-version) | No changelog generation, no GitHub Release automation, relies on human discipline for every release |
| CalVer | Does not communicate API compatibility. SemVer is the Python ecosystem standard (PEP 440 compatible) |

**Why release-please**:

- **PR-based workflow**: creates a Release PR that accumulates changes
  and can be reviewed before release. Merging the PR triggers the
  release — giving the team full control over timing
- **Zero project dependencies**: runs as a GitHub Action, no npm/Node.js
  in the repository
- **Conventional Commits native**: reads the commit types the project
  already uses
- **Python support**: the `python` release type updates `pyproject.toml`
  and generates `CHANGELOG.md` automatically
- **Compatible with existing CI/CD**: tags created by release-please
  (`v*`) trigger the existing `build-images.yml` without modification
- **Squash-merge friendly**: works best with squash merge (PR title
  becomes commit message), which the project should adopt

### D5: Version Source of Truth

**Decision**: `backend/pyproject.toml` is the single source of truth for
the version number. `backend/app/main.py` reads it dynamically via
`importlib.metadata.version("sentinel")`.

**Current state** (problematic):

```python
# backend/pyproject.toml line 3
version = "0.1.0"

# backend/app/main.py line 13
version="0.1.0",  # hardcoded, can drift
```

**Target state**:

```python
# backend/pyproject.toml — managed by release-please
version = "0.1.0"

# backend/app/main.py — reads dynamically
from importlib.metadata import version as get_version
...
version=get_version("sentinel"),
```

### D6: No Custom OpenCode Tooling

**Decision**: no new agents, commands, or skills for versioning.

**Rationale**: the release process is fully automated by release-please
and requires no local tooling. The existing `@cicd` agent is sufficient
for maintaining the release workflow. Adding a `/release` command or
`@version-reviewer` agent would be over-engineering — the process is:
write conventional commits → merge Release PR → done.

### D7: Documentation Placement

Versioning information is split between two existing documents according
to content type:

| Content | Document | Rationale |
|---------|----------|-----------|
| SemVer rules, bump interpretation, pre-1.0 rules, 1.0.0 criteria | `docs/conventions.md` (new "Versioning" subsection under "Git Conventions") | These are project conventions, alongside existing commit message and branch naming conventions |
| Release process, release-please workflow, changelog management, pipeline chain | `docs/deployment.md` (new "Release Process" section) | This is an operational process, part of the deployment lifecycle alongside existing environment and deployment documentation |

A dedicated spec (`docs/features/platform/versioning.md`) is not
appropriate because versioning is not a product feature — it is
project-level infrastructure that naturally splits between conventions
(rules) and deployment (process).

---

## Action Plan

Each step below is prescriptive: it specifies exactly what to modify,
where, and with what content. Steps are ordered by dependency — later
steps may reference content added by earlier steps.

### Step 1: Add "Versioning" subsection to `docs/conventions.md`

**File**: `docs/conventions.md`

**Location**: insert after the "Commit Messages" subsection (currently
ending at line 629) and before "## Feature Specifications" (currently at
line 631). This places versioning as a third subsection under
"## Git Conventions", alongside "Branch Naming" and "Commit Messages".

**Content to insert** (between line 629 and line 631):

```markdown
### Versioning

Sentinel uses a single platform version following [Semantic Versioning
2.0.0](https://semver.org/). All components (API server, Celery workers,
Celery Beat, IBS consumer, migrations) are built from the same Docker
image and share the same version number.

#### Version Source of Truth

The version in `backend/pyproject.toml` is the single source of truth.
`backend/app/main.py` reads it dynamically via
`importlib.metadata.version("sentinel")`. Git tags
(`v<major>.<minor>.<patch>`) are created automatically by the release
process (see `docs/deployment.md`, Release Process) and consumed by the
Docker image build pipeline.

#### SemVer Interpretation

Sentinel is a deployed platform, not a library. SemVer bumps are
interpreted as follows:

| Bump | Trigger |
|------|---------|
| MAJOR | Breaking REST API changes (removal/renaming of fields, semantic changes to existing behavior, error code changes), database migrations requiring manual operator intervention, fundamental architectural changes |
| MINOR | New API endpoints, new fetchers, new features, non-breaking database migrations, new CLI commands |
| PATCH | Bug fixes, security patches, performance improvements, operational fixes |

Only `feat:` and `fix:` commits (and their `!` breaking variants)
trigger version bumps. Commits with `docs:`, `chore:`, `test:`,
`refactor:`, or `ci:` types do not produce a release on their own.

#### Pre-1.0 Rules

While the version is `0.x.y`:

- The API is not considered stable
- Breaking changes MAY occur in minor version bumps (`0.x` → `0.x+1`)
- Consumers should pin to exact versions, not ranges

#### 1.0.0 Graduation Criteria

The project reaches `1.0.0` when ALL of the following conditions are
met:

1. **Production operational**: a production instance is deployed and
   serving real users
2. **Core ingestion functional**: all core CVE fetchers (NVD, MITRE, and
   at least one additional source) are implemented and running in
   production
3. **Ticket lifecycle complete**: the full ticket lifecycle — from CVE
   ingestion through analysis to resolution — is functional end-to-end
4. **Authentication operational**: both local authentication and SSO are
   implemented and operational in production
5. **API stability demonstrated**: the REST API v1 surface has had no
   breaking changes for at least 4 weeks of production operation
6. **Schema stability demonstrated**: the database schema has had no
   breaking migrations (requiring manual intervention) for at least 2
   consecutive minor releases

From `1.0.0` onward, breaking REST API changes require a major version
bump and the API versioning policy in `docs/api-spec.md` (Versioning)
takes full effect.

#### Why Single Version

All five process roles (API server, Celery worker, Git worker, Celery
Beat, IBS consumer) run from the same Docker image with different
entrypoints. They cannot be deployed at different versions.
Per-component versioning (e.g., per-fetcher) would add overhead without
practical benefit since fetchers are built-in classes, not independently
deployable plugins.
```

### Step 2: Add "Release Process" section to `docs/deployment.md`

**File**: `docs/deployment.md`

**Location**: insert as a new `##` section between the `---` separator
(line 285, which closes the "Production Deployment" section) and
"## Database Migrations" (line 287). The new content goes after the
existing `---` separator (line 286, empty line) and before line 287.
This is the natural position: the release process produces the artifacts
that are then deployed to production, and database migrations are a
subsequent operational step.

**Content to insert** (between line 286 and line 287 — the content does
NOT start with `---` because the separator already exists on line 285;
it DOES end with `---` to separate from the next section):

```markdown
## Release Process

Sentinel uses [release-please](https://github.com/googleapis/release-please)
to automate versioning, changelog generation, and GitHub Releases. The
process is driven entirely by Conventional Commit messages on the
`master` branch.

### How It Works

1. Developers merge PRs to `master` using conventional commits
   (`feat:`, `fix:`, etc.). Squash merge is recommended so the PR title
   becomes the commit message (see Squash Merge below)
2. The `release-please` GitHub Action
   (`.github/workflows/release-please.yml`) analyzes new commits and
   creates (or updates) a **Release PR** with:
   - Version bump in `backend/pyproject.toml`
   - Updated `CHANGELOG.md`
   - Summary of all changes since the last release
3. The Release PR stays open and is updated automatically as more
   commits land on `master`
4. When the team decides to release, a maintainer merges the Release PR
5. On merge, release-please:
   - Creates a git tag (`v<major>.<minor>.<patch>`)
   - Creates a GitHub Release with release notes
6. The tag triggers `build-images.yml`, which builds and pushes the
   Docker image to `ghcr.io` with semver tags

### Creating a Release

To create a release, merge the open Release PR. No manual version
bumping, tagging, or changelog editing is required.

To force a specific version (e.g., to reach `1.0.0`), use the
`Release-As` footer in a commit message:

```
chore: prepare 1.0.0 release

Release-As: 1.0.0
```

### Squash Merge

All PRs to `master` SHOULD use squash merge. This keeps the git history
linear and gives release-please a clean, single commit to analyze per
PR. With squash merge, the PR title becomes the commit message — ensure
it follows the Conventional Commits format defined in
`docs/conventions.md` (Git Conventions).

### Changelog

`CHANGELOG.md` at the repository root is maintained automatically by
release-please. Do not edit it manually. It groups changes by type
(Features, Bug Fixes, etc.) and links to commits and PRs.

### Pipeline Chain

```
master branch commits
     │
     ▼
release-please.yml → creates/updates Release PR
     │ (on merge)
     ▼
creates git tag (v*) + GitHub Release
     │
     ▼
build-images.yml → builds and pushes Docker image to ghcr.io
     │
     ▼
manual deployment from tag (staging/production)
```

### Version Locations

| Location | Mechanism |
|----------|-----------|
| `backend/pyproject.toml` | Updated by release-please (source of truth) |
| `backend/app/main.py` | Reads dynamically via `importlib.metadata` |
| Git tag | Created by release-please (`v1.2.3`) |
| Docker image tag | Derived from git tag by `build-images.yml` |
| GitHub Release | Created by release-please with changelog |
| `CHANGELOG.md` | Updated by release-please |

### Configuration Files

The release-please configuration lives in two files at the repository
root:

- `release-please-config.json` — release strategy and package
  configuration
- `.release-please-manifest.json` — current version tracking

These files are managed by release-please and should not be edited
manually except during initial setup or to force a version via
`Release-As`.

---
```

### Step 3: Create `.githooks/commit-msg` validation hook

**File**: `.githooks/commit-msg` (new file)

**Purpose**: validates that every commit message follows the
Conventional Commits format defined in `docs/conventions.md` (Git
Conventions, Commit Messages). This is a prerequisite for
release-please: incorrectly formatted commit messages produce wrong
version bumps or are silently excluded from the changelog.

**Content**:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Conventional Commits format validation.
# Allowed types: feat, fix, docs, refactor, test, chore, ci
# Format: type[(scope)][!]: description
#
# Also allows git-generated messages: Merge, Revert, fixup!, squash!
# and release-please messages: chore(main): release

commit_msg_file="$1"
commit_msg=$(head -1 "$commit_msg_file")

# Skip merge commits, reverts, fixups, and squash commits
if [[ "$commit_msg" =~ ^(Merge|Revert|fixup!|squash!) ]]; then
    exit 0
fi

# Conventional Commits regex:
# type(optional-scope)optional-!: description
pattern="^(feat|fix|docs|refactor|test|chore|ci)(\([a-z0-9 _-]+\))?\!?: .{1,}"

if ! [[ "$commit_msg" =~ $pattern ]]; then
    echo "Error: commit message does not follow Conventional Commits format." >&2
    echo "" >&2
    echo "  Expected: type[(scope)][!]: description" >&2
    echo "  Types:    feat, fix, docs, refactor, test, chore, ci" >&2
    echo "  Example:  feat: add CVE severity filtering" >&2
    echo "  Example:  fix(auth): correct token expiration check" >&2
    echo "  Example:  feat!: remove deprecated endpoint" >&2
    echo "" >&2
    echo "  Got: $commit_msg" >&2
    exit 1
fi

# Validate first line length (72 chars max)
if [ ${#commit_msg} -gt 72 ]; then
    echo "Error: commit message first line exceeds 72 characters (${#commit_msg})." >&2
    echo "  Got: $commit_msg" >&2
    exit 1
fi
```

**How it integrates with existing hooks**: `.githooks/` already contains
`pre-commit` (ruff + unit tests) and `pre-push` (full test suite). The
`commit-msg` hook runs between the two — after the user writes the
message and before the commit is finalized. It follows the same pattern:
bash script, `set -euo pipefail`, clear error messages to stderr.

**Note**: the hook must be made executable (`chmod +x`). The existing
hooks in `.githooks/` are already executable. Git hooks from
`.githooks/` are activated by setting `core.hooksPath`:
`git config core.hooksPath .githooks` (this is already configured for
the project given the existing hooks).

### Step 4: Create `release-please-config.json`

**File**: `release-please-config.json` (new file at repository root)

**Content**:

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": {
    "backend": {
      "release-type": "python",
      "package-name": "sentinel",
      "include-component-in-tag": false,
      "changelog-path": "../CHANGELOG.md"
    }
  }
}
```

**Explanation of each field**:

- `"release-type": "python"` — tells release-please to update
  `pyproject.toml` (and optionally `setup.py`, `setup.cfg`,
  `__init__.py` if present)
- `"package-name": "sentinel"` — the Python package name
- `"include-component-in-tag": false` — produces tags `v1.2.3` instead
  of `backend-v1.2.3` (single-package repo)
- `"changelog-path": "../CHANGELOG.md"` — places the changelog at the
  repository root (not inside `backend/`), since the changelog covers
  the entire platform

### Step 5: Create `.release-please-manifest.json`

**File**: `.release-please-manifest.json` (new file at repository root)

**Content**:

```json
{
  "backend": "0.1.0"
}
```

**Purpose**: tells release-please the current version without requiring
an existing git tag. The first release-please run will analyze commits
since the repository beginning (or since the last recognized release)
and create a Release PR bumping from `0.1.0`.

### Step 6: Create `.github/workflows/release-please.yml`

**File**: `.github/workflows/release-please.yml` (new file)

**Content**:

```yaml
name: Release Please

on:
  push:
    branches: [master]

permissions:
  contents: write
  pull-requests: write

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

**How it integrates with existing pipelines**:

- Runs on every push to `master` (separate from `ci.yml`)
- When a Release PR is merged, release-please creates a `v*` tag
- The `v*` tag triggers `build-images.yml` (line 9:
  `tags: ["v*"]`) — no changes needed to that workflow
- `ci.yml` continues to run independently on pushes and PRs

### Step 7: Update `docs/architecture.md` — Environments section

**File**: `docs/architecture.md`

**Location**: line 418

**Current text**:

```
- **Production**: manually deployed from version tags (`v*`)
```

**Replace with**:

```
- **Production**: manually deployed from version tags (`v*`) created by
  the release-please process (see `docs/deployment.md`, Release Process)
```

### Step 8: Update `.opencode/agents/cicd.md`

**File**: `.opencode/agents/cicd.md`

**Change 1** — Add to the "## Conventions" section (after line 38,
"Production deploys from version tags..."):

Add one new bullet:

```
- Release versioning is automated by release-please
  (`.github/workflows/release-please.yml`). Configuration lives in
  `release-please-config.json` and `.release-please-manifest.json` at
  the repository root
```

**Change 2** — Update the pipeline chain at line 54.

Current text:

```
   `ci.yml` → `build-images.yml` (deploy workflows deferred)
```

Replace with:

```
   `ci.yml` → `build-images.yml` (deploy workflows deferred)
   `release-please.yml` → Release PR → (on merge) → tag `v*` → triggers `build-images.yml`
```

**Change 3** — Add `release-please-config.json` and
`.release-please-manifest.json` to the edit permissions (line 10-13).

Current:

```yaml
permission:
  edit:
    ".github/workflows/*": allow
    "backend/Dockerfile": allow
    "docker-compose*.yml": allow
    "*": deny
```

Add two new lines:

```yaml
permission:
  edit:
    ".github/workflows/*": allow
    "backend/Dockerfile": allow
    "docker-compose*.yml": allow
    "release-please-config.json": allow
    ".release-please-manifest.json": allow
    "*": deny
```

### Step 9: Update `AGENTS.md` — Guardrail 5

**File**: `AGENTS.md`

**Location**: Guardrail 5, "CI/CD awareness" (lines 208-214)

**Current text**:

```markdown
### 5. CI/CD awareness

When modifying backend dependencies, build configuration, or Docker
setup, verify that the CI pipeline (`.github/workflows/`) does not need
corresponding updates. If it does, update the workflows in the same PR.

For CI/CD-specific changes, delegate to the `@cicd` subagent.
```

**Replace with**:

```markdown
### 5. CI/CD awareness

When modifying backend dependencies, build configuration, or Docker
setup, verify that the CI pipeline (`.github/workflows/`) does not need
corresponding updates. If it does, update the workflows in the same PR.

When modifying release-related configuration
(`release-please-config.json`, `.release-please-manifest.json`), verify
that the release-please workflow
(`.github/workflows/release-please.yml`) and the downstream
`build-images.yml` pipeline are not affected.

For CI/CD-specific changes, delegate to the `@cicd` subagent.
```

### Step 10: Run reviewers on affected specifications

After all changes from Steps 1-9 have been applied, run the following
reviewers to verify correctness:

1. **`@docs-placement-reviewer`** on `docs/conventions.md` — verify
   that the Versioning subsection is correctly placed under Git
   Conventions and does not duplicate or conflict with content in other
   documents (particularly `docs/deployment.md` and `docs/api-spec.md`)

2. **`@docs-placement-reviewer`** on `docs/deployment.md` — verify that
   the Release Process section is correctly placed and does not duplicate
   content from `docs/conventions.md`

3. **`@spec-coherence-reviewer`** on `docs/deployment.md` — verify
   coherence with `docs/architecture.md` (Environments section) and
   `docs/api-spec.md` (Versioning section), ensuring no contradictions
   between the release process description and the existing deployment
   and API versioning documentation

4. **`@docs-reviewer`** — verify documentation completeness: that the
   new sections cross-reference each other correctly, that the pipeline
   chain description matches the actual workflow files, and that no
   existing documentation references are broken

5. **`@cicd`** agent — verify that the new
   `release-please.yml` workflow is correct, that
   `release-please-config.json` and `.release-please-manifest.json` are
   valid, and that the pipeline chain (`release-please.yml` →
   `build-images.yml`) integrates correctly with the existing CI/CD
   setup

### Step 11: Delete this draft

After all reviewer findings have been addressed and the changes are
confirmed correct, delete this file:

**File to delete**: `docs/drafts/versioning-and-release-process.md`

---

## Files Modified (Summary)

| # | File | Action | Step |
|---|------|--------|------|
| 1 | `docs/conventions.md` | Insert "Versioning" subsection under "Git Conventions" | Step 1 |
| 2 | `docs/deployment.md` | Insert "Release Process" section before "Database Migrations" | Step 2 |
| 3 | `.githooks/commit-msg` | Create (commit message format validation hook) | Step 3 |
| 4 | `release-please-config.json` | Create (new file at repo root) | Step 4 |
| 5 | `.release-please-manifest.json` | Create (new file at repo root) | Step 5 |
| 6 | `.github/workflows/release-please.yml` | Create (new workflow) | Step 6 |
| 7 | `docs/architecture.md` | Update Environments bullet (line 418) | Step 7 |
| 8 | `.opencode/agents/cicd.md` | Add release-please convention, update pipeline chain, add edit permissions | Step 8 |
| 9 | `AGENTS.md` | Extend Guardrail 5 with release-please awareness | Step 9 |
| 10 | `docs/drafts/versioning-and-release-process.md` | Delete after review | Step 11 |

## Notes

### About `backend/app/main.py`

The `importlib.metadata` change in `main.py` is an **implementation
change**, not a specification change. Since the project is currently in
spec phase and no implementation code changes are being made, this
change will be applied when implementation begins. The specification
(Step 1) declares the convention ("reads dynamically via
`importlib.metadata.version("sentinel")`"), and the implementer will
apply it when first touching `main.py`.

### About Squash Merge

The draft recommends squash merge as SHOULD (not MUST). This is a GitHub
repository setting, not a code change. The team should enable "Squash
merging" as the default (or only) merge strategy in the repository
settings on GitHub. This is an operational step, not a spec change.

### About `CHANGELOG.md`

The `CHANGELOG.md` file will be created automatically by release-please
on the first release. No manual creation is needed. Its location
(repository root) is configured in `release-please-config.json`
(`"changelog-path": "../CHANGELOG.md"` relative to the `backend/`
package path).

### About `build-images.yml`

No modifications are needed to `build-images.yml`. It already triggers
on `v*` tag pushes and produces semver Docker image tags. The tags
created by release-please (`v1.2.3`) match the existing trigger pattern.

### Relationship to `docs/api-spec.md` (Versioning)

The API versioning section in `docs/api-spec.md` (lines 546-559)
describes URL-path API versioning (`/api/v1/`). This is orthogonal to
platform versioning:

- **Platform version** (SemVer, e.g., `1.2.3`): tracks the overall
  software release. Defined in `docs/conventions.md`
- **API version** (URL path, e.g., `/api/v1/`): tracks the API contract
  surface. Defined in `docs/api-spec.md`

Both `1.0.0` and `2.0.0` of the platform may serve `/api/v1/`. A new
API version (`/api/v2/`) would be introduced only for breaking API
changes after `1.0.0` (which would also trigger a major platform version
bump). No changes to `docs/api-spec.md` are needed — the two versioning
schemes are complementary, not conflicting.
