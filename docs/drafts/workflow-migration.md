# Workflow Migration: Adopt PR-Based Development

**Status**: Pending

**Purpose**: Migrate from direct-push-to-master to a PR-based GitHub
Flow process. All code and documentation changes will pass through
feature branches, pull requests, CI verification, and explicit human
approval before reaching `master`. This draft describes every change
required and provides a step-by-step execution plan.

This document is a planning aid. It will be deleted as its final step.
No permanent artifact created during execution may reference this file.

## Contents

- [Context](#context)
- [Decisions](#decisions)
- [Illustrative Flow (Future Steady-State)](#illustrative-flow-future-steady-state)
- [Action Plan](#action-plan)
  - [Step 0 — Push Current Commit](#step-0--push-current-commit)
  - [Step 1 — GitHub Repository Settings](#step-1--github-repository-settings)
  - [Step 2 — Create Bootstrap Branch](#step-2--create-bootstrap-branch)
  - [Step 3 — Documentation](#step-3--documentation)
  - [Step 4 — OpenCode Tooling](#step-4--opencode-tooling)
  - [Step 5 — Git Hooks](#step-5--git-hooks)
  - [Step 6 — CI/CD Workflows](#step-6--cicd-workflows)
  - [Step 7 — New Files](#step-7--new-files)
  - [Step 8 — Open PR and Merge](#step-8--open-pr-and-merge)
  - [Step 9 — Post-Merge Verification](#step-9--post-merge-verification)
  - [Step 10 — Reviewers](#step-10--reviewers)
  - [Step 11 — Delete This Draft](#step-11--delete-this-draft)

---

## Context

Until now all commits have been pushed directly to `master`. This was
acceptable during the specification phase (documentation only, single
author). With implementation starting, the project needs:

- Isolation of in-progress work from the stable branch.
- CI verification before code reaches `master`.
- A durable, inspectable record of each change (PR as audit trail).
- An explicit human approval gate before merge.
- Compatibility with the spec-first workflow and the existing
  reviewer agent infrastructure.

The repository is private on GitHub Free. Branch protection and
rulesets are not available. Protection relies on local hooks, agent
instructions, and procedural discipline until the repository is made
public.

Two collaborators (`serenaferracci`, `stoyanmanolov1`) have write
access for visibility purposes. They will not push or merge.

OpenCode operates under the repository owner's GitHub account. A
single-account model is used: OpenCode may execute merges, but only
after receiving explicit human confirmation referencing the specific PR.

---

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | GitHub Flow with short-lived branches | Simple, compatible with release-please, no long-lived develop/staging branches |
| D2 | One PR per implementation-plan piece | Matches "one piece at a time" principle; keeps PRs reviewable |
| D3 | Squash merge only | Linear `master` history; PR title becomes the commit message for release-please |
| D4 | Human confirmation required before merge | OpenCode prepares everything; merge executes only after explicit user instruction |
| D5 | No direct pushes to `master` after Step 0 | Enforced by pre-push hook (local) and agent instructions (procedural) |
| D6 | No manual tags | Tags are created exclusively by release-please |
| D7 | No force pushes | Enforced by agent instructions; server-side enforcement deferred to public visibility |
| D8 | Remove redundant OpenCode commands | `/run-tests` and `/check-spec` duplicate Code agent, CI, and reviewer capabilities |
| D9 | Remove auto-commit from `/idea` | Commands edit files; commits are a separate explicit step |
| D10 | Remove `workflow_dispatch` from publishing workflows | Prevents unreviewed image publication; CI `workflow_dispatch` (diagnostic) is kept |
| D11 | Gate release-please behind CI success | Prevents release PR updates or tag creation from commits that fail CI |
| D12 | Add image smoke test to PR CI | Catches container defects before merge |
| D13 | Add Dependabot version updates | Automated dependency freshness for Python, Actions, Docker, npm |
| D14 | Repository stays private | Public visibility deferred to a future audit; not part of this change |
| D15 | Single GitHub account for OpenCode | No bot account; agent instructions and hooks provide the safety layer |
| D16 | Natural-language initiation, no dedicated command | A normal implementation request triggers the workflow automatically; no `/start-feature` or similar command exists or is needed |
| D17 | Agent owns branch creation | OpenCode creates, names, and pushes the branch; the user intervenes only at spec approval and merge |
| D18 | Delete `new-feature` skill | Mixes spec and implementation in one flow, conflicts with two-PR sequencing, duplicates guardrails; not updated — removed |

---

## Illustrative Flow (Future Steady-State)

After this migration is complete, a typical implementation request
follows this sequence:

```
User: "Implement the health endpoints defined in the spec."
  │
  ▼
Agent: verify spec exists and is sufficient
  │
  ▼
Agent: git fetch origin && git switch -c feature/health-endpoints origin/master
  │
  ▼
Agent: implement, test, commit on branch
  │
  ▼
Agent: (first push) report branch name + scope → push → open draft PR
  │
  ▼
Agent: complete Definition of Done (tests, lint, reviewers)
  │
  ▼
Agent: mark PR ready, present merge gate summary to user
  │
  ▼
User: "Merge PR #12"
  │
  ▼
Agent: gh pr merge 12 --squash --delete-branch
  │
  ▼
Agent: sync local master, verify post-merge CI
```

When the spec is missing or incomplete:

```
User: "Implement feature X."
  │
  ▼
Agent: spec not found → STOP
  │
  ▼
Agent: "No spec for X. Shall I create one first?"
  │
  ▼
User: "Yes"
  │
  ▼
Agent: git switch -c docs/feature-x origin/master
  │
  ▼
Agent: write spec → push → open PR
  │
  ▼
User: "Merge PR #13" (spec PR)
  │
  ▼
Agent: merge spec PR → git fetch origin
  │
  ▼
Agent: git switch -c feature/feature-x origin/master
  │
  ▼
Agent: implement (now spec is merged) → ... → merge gate
```

---

## Action Plan

### Step 0 — Push Current Commit

**Type**: One-time manual action (last direct push).

Push commit `248532f` (`docs: drop image-testing-setup draft, decouple
smoke-test spec`) to `origin/master`:

```
git push origin master
```

Wait for all triggered workflows to complete:

| Workflow | Expected trigger | Verify |
|----------|-----------------|--------|
| CI | `push` to `master` | All 4 jobs green |
| Release Please | `push` to `master` | Release PR #1 updated |
| Build Docker Images | `workflow_run` (CI completed) | Image built, smoke passed, `latest` pushed |

After this push, **no further direct pushes to `master` are
permitted**. All subsequent changes follow Steps 2–11.

---

### Step 1 — GitHub Repository Settings

**Type**: Manual configuration via `gh` CLI or GitHub web UI. Not part
of the PR.

These settings apply to the repository, not to code. Execute them
before creating the bootstrap branch so the first PR benefits from the
correct merge configuration.

| Setting | Command | Purpose |
|---------|---------|---------|
| Disable merge commits | `gh repo edit --enable-merge-commit=false` | Only squash merge allowed |
| Disable rebase merge | `gh repo edit --enable-rebase-merge=false` | Only squash merge allowed |
| Squash title = PR title | `gh repo edit --enable-squash-merge --squash-merge-commit-title=PR_TITLE --squash-merge-commit-message=COMMIT_MESSAGES` | PR title becomes the commit message on `master` |
| Delete branch on merge | `gh repo edit --delete-branch-on-merge` | Automatic cleanup |
| Enable Dependabot alerts | Already enabled (verified: HTTP 204) | Vulnerability notifications |
| Enable automated security fixes | `gh api -X PUT repos/{owner}/{repo}/automated-security-fixes` | Auto-PR for vulnerable dependencies |

Verify after applying:

```
gh repo view --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge
```

Expected: `squashMergeAllowed: true`, others `false`,
`deleteBranchOnMerge: true`.

---

### Step 2 — Create Bootstrap Branch

**Type**: Git operations.

```
git fetch origin
git switch -c chore/adopt-pr-workflow origin/master
```

All changes from Steps 3–7 are committed on this branch.

---

### Step 3 — Documentation

#### 3a. `docs/conventions.md` — Expand Git Conventions

Insert a new subsection **Workflow** at the beginning of the existing
"Git Conventions" section (before "Branch Naming"). Content to cover:

- **Model**: GitHub Flow — `master` is always the stable, deployable
  branch. All changes are developed on short-lived topic branches and
  merged via pull request.
- **Branch lifecycle**: create from `origin/master`, push regularly,
  open a draft PR early, mark ready when Definition of Done is met,
  squash-merge after approval, branch auto-deleted.
- **Single active branch**: one implementation piece at a time (per
  the implementation plan's "one piece at a time" principle). Multiple
  branches may exist for independent concerns (e.g., a spec fix and
  an implementation piece), but parallel domain-logic branches within
  the same phase are avoided.
- **No direct pushes to `master`**: all changes arrive via squash
  merge of a reviewed PR. The pre-push hook enforces this locally.
- **No force pushes**: never rewrite published branch history.
- **No manual tags**: tags are created exclusively by release-please.
- **Squash merge**: the only allowed merge method. The PR title
  (which must follow Conventional Commits format) becomes the commit
  message on `master`.
- **PR title**: must follow the Conventional Commits format
  (`type[(scope)][!]: description`). This is validated by CI.
- **Branch deletion**: branches are deleted automatically after merge.

- **Workflow initiation**: a concrete modification request in natural
  language (e.g., "implement feature X", "fix bug Y") is sufficient
  to start the workflow. No dedicated command or manual branch
  creation is required. The agent determines whether a request is
  operational (triggers the workflow) or exploratory (no branch
  created). Exploratory requests include questions, analysis,
  brainstorming, and spec review.

- **Branch creation responsibility**: the agent creates the topic
  branch automatically when all preconditions are met:
  1. The request is a concrete modification (not exploratory).
  2. The owning spec exists and is sufficient for the requested
     scope (Guardrail 1).
  3. The local worktree is clean or has no conflicting state.
  4. `origin/master` has been fetched.
  If any precondition fails, the agent stops and reports the
  blocker instead of creating the branch.

- **Spec-first branch sequencing**: when the owning spec does not
  exist or is incomplete for the requested change:
  1. First: a `docs/<feature>` branch and PR to create or update
     the spec. Merge requires user approval.
  2. Then: a `feature/<feature>` (or `fix/...`) branch created
     from the updated `origin/master` for the implementation.
  The agent never starts implementation on a branch where the spec
  has not yet been approved and merged.

- **Local-to-remote lifecycle**: the branch starts local. The agent
  may commit freely. After the first coherent set of changes, the
  agent pushes and opens a draft PR without waiting for explicit
  push authorization. Subsequent commits are pushed incrementally.
  The only mandatory human gate is the merge.

Expand the **Branch Naming** list to include all conventional commit
types:

| Prefix | Use |
|--------|-----|
| `feature/` | New features |
| `fix/` | Bug fixes |
| `docs/` | Documentation changes |
| `refactor/` | Code refactoring |
| `chore/` | Infrastructure, configuration, dependencies |
| `ci/` | CI/CD pipeline changes |
| `test/` | Test-only changes |

#### 3b. `docs/conventions.md` — PR Requirements

Add a new subsection **Pull Request Requirements** after "Workflow".
Content to cover:

- **Title**: Conventional Commits format (validated by CI).
- **Description**: use the repository PR template. At minimum:
  owning spec reference, scope summary, test evidence, reviewer
  results, manual verification notes.
- **CI**: all checks must pass on the latest commit before merge is
  requested.
- **Reviewers**: applicable reviewer agents must be invoked and
  findings addressed (per existing guardrails).
- **Human approval**: the repository owner must explicitly authorize
  the merge by referencing the PR number.

#### 3c. `AGENTS.md` — Add Guardrail 25 (Git operations safety)

Add a new guardrail after Guardrail 24. Title: **Git operations
safety**. Content:

**CRITICAL**: agents MUST NOT perform any of the following operations
without explicit user authorization in the current conversation:

- Push to the default branch (`master`).
- Execute `gh pr merge` or any operation that merges a PR.
- Create or push Git tags.
- Force-push any branch (`--force`, `--force-with-lease`, `-f`).
- Execute `git reset --hard`, `git clean -fd`, or any destructive Git
  operation.
- Use `--no-verify` to bypass Git hooks.

**Merge gate procedure**: when all PR requirements are satisfied
(CI green, reviewers completed, no blocking findings), the agent
MUST present the following to the user and wait for explicit
authorization:

1. PR number and title.
2. Summary of CI status (all checks passing).
3. Summary of reviewer results (which reviewers ran, outcome).
4. Any unresolved items or known risks.

The agent proceeds with the merge ONLY after the user responds with
an explicit instruction referencing the PR (e.g., "merge PR #12",
"esegui il merge della #12"). Implicit or assumed approval is not
sufficient.

**Branch workflow**: agents work exclusively on topic branches.
`master` is never checked out for editing. After merge, agents
synchronize local `master` with `git fetch origin && git branch -f
master origin/master` without checking it out.

**Commit discipline**: agents may commit and push to topic branches
without per-commit approval. Before the first push of a new branch,
the agent reports the branch name and initial scope. Before opening
a PR, the agent reports the intended title and description.

**Automatic workflow initiation**: when the user issues a concrete
modification request, the agent MUST autonomously:

1. Fetch `origin/master`.
2. Verify a clean worktree (or stash/report conflicts).
3. Verify the owning spec exists and covers the request (Guardrail 1).
4. Create a topic branch from `origin/master` with the appropriate
   naming prefix.
5. Proceed with implementation.

No dedicated command or explicit "create branch" instruction from the
user is required. The agent does NOT create a branch for exploratory
requests (questions, analysis, brainstorming, spec review without
implementation intent).

**Spec-first sequencing**: if the spec is absent or insufficient:

1. The agent stops implementation intent.
2. Creates a `docs/<name>` branch for the spec work.
3. After spec PR is approved and merged, creates a new
   implementation branch from the updated `origin/master`.
4. Never mixes unmerged spec changes with implementation on the
   same branch.

#### 3d. `docs/deployment.md` — Update Release Process

The existing "Squash Merge" subsection already recommends squash
merge. Update it to state that squash merge is the **only** allowed
method (not merely recommended), reflecting the repository setting
change.

Update the "Pipeline Chain" diagram to reflect that release-please
is now gated behind CI success (see Step 6c).

---

### Step 4 — OpenCode Tooling

#### 4a. Delete `.opencode/commands/run-tests.md`

This command duplicates the Code agent's built-in verification
behavior, the CI pipeline, and the Definition of Done checklist. It
covers only a subset of required checks (pytest and ruff, missing
alembic, bandit, shellcheck, smoke test). Remove it.

#### 4b. Delete `.opencode/commands/check-spec.md`

This command duplicates the Code agent's spec-first verification
(Guardrail 1), the Gap Protocol, and the `@api-parity-reviewer`,
`@docs-reviewer`, and domain-specific reviewers. It references the
disabled `build` agent. Remove it.

#### 4c. Modify `.opencode/commands/idea.md`

Remove step 4 (the auto-commit instruction). The command should only
edit `docs/drafts/ideas.md`. The user or agent commits the change as
a separate explicit step, following the normal branch workflow.

Replace step 4 with: "Report what was added. Do NOT commit
automatically — the file edit is staged and committed by the caller
as part of the normal workflow."

#### 4d. Delete `.opencode/skills/new-feature/`

This skill mixes spec authoring and implementation in a single
sequential flow. Under the new process:

- Spec and implementation require separate branches and PRs.
- The Spec agent handles spec authoring; the Code agent handles
  implementation.
- Guardrails 1, 25, the Definition of Done, and the reviewer
  infrastructure already govern the full lifecycle.
- Natural-language requests replace the skill's step-by-step
  orchestration (D16).

Delete the entire `.opencode/skills/new-feature/` directory.

#### 4e. Update `.opencode/skills/new-api-endpoint/SKILL.md`

This skill remains useful (specialized technical checklist for
schema → service → endpoint → tests → reviewers). Update it:

- Remove references to the deleted `new-feature` skill.
- Add a brief note at the top clarifying that the skill assumes a
  topic branch already exists. Branch creation, push, PR opening,
  and merge follow the global workflow defined in
  `docs/conventions.md` (Git Conventions) and Guardrail 25. The
  skill does NOT handle branch or PR lifecycle — only the
  implementation sequence within an already-active branch.

#### 4f. Update `.opencode/README.md`

Remove the rows for `/check-spec` and `/run-tests` from the Commands
table. The remaining commands are `/idea` and `/review-spec`.

Remove the `new-feature` row from the Skills table. The remaining
skill is `new-api-endpoint`.

#### 4g. Update `.opencode/prompts/code.md`

Add a **Git Safety** section after "Non-Feature Work". Content:

- Reference Guardrail 25 for the full rules.
- Summarize: work on topic branches only, never push to `master`,
  never merge without explicit user instruction, never force-push,
  never create tags.
- Before opening a PR, report: branch name, PR title, PR description
  summary, and list of changed files.
- Before requesting merge approval, present: PR number, CI status,
  reviewer summary, and any unresolved items.

Add a **Workflow Initiation** section after "Git Safety". Content:

- When the user requests a concrete modification (implementation,
  fix, refactor), recognize this as an operational request and
  start the branch workflow automatically.
- Do NOT wait for an explicit "create a branch" instruction or a
  slash command. Natural-language intent is sufficient.
- Before creating the branch: verify spec exists (Guardrail 1),
  fetch `origin/master`, confirm clean worktree.
- If the spec is missing or incomplete: stop, inform the user, and
  propose creating the spec first via a separate `docs/` branch
  and PR. Do not begin implementation until the spec PR is merged.
- If all preconditions are met: create the branch, announce name
  and scope, and proceed.

---

### Step 5 — Git Hooks

#### 5a. Modify `.githooks/pre-push`

Add a guard **before** the existing test execution that blocks
pushes to `master` and tag pushes. The logic:

1. Read stdin (each line: `local_ref local_sha remote_ref
   remote_sha`).
2. If any `remote_ref` equals `refs/heads/master`, print an error
   to stderr: `"Error: direct push to master is not allowed. Use
   a pull request."` and exit 1.
3. If any `local_ref` starts with `refs/tags/`, print an error to
   stderr: `"Error: local tag push is not allowed. Tags are
   created by release-please."` and exit 1.
4. If no blocked ref is found, continue to the existing test
   execution.

The hook remains bypassable with `--no-verify` (Git limitation).
The agent instructions in Guardrail 25 prohibit `--no-verify`.

---

### Step 6 — CI/CD Workflows

#### 6a. Modify `.github/workflows/ci.yml`

Add two new jobs that run **only on `pull_request`** events:

**Job: `pr-title`**

- Condition: `if: github.event_name == 'pull_request'`
- Single step: validate that `github.event.pull_request.title`
  matches the Conventional Commits regex
  `^(feat|fix|docs|refactor|test|chore|ci)(\([a-z0-9 _-]+\))?\!?: .{1,}`.
- On mismatch: fail with an error message showing expected format
  and actual title.

**Job: `image-smoke`**

- Condition: `if: github.event_name == 'pull_request'`
- Steps:
  1. Checkout.
  2. Setup uv (same version as other jobs).
  3. Read Python version from `backend/.python-version`.
  4. Setup buildx.
  5. Build backend image with `docker/build-push-action` — `push:
     false`, `load: true`, tag `sentinel-backend:smoke`, use GHA
     cache.
  6. Run `./scripts/image-smoke.sh --no-build` with
     `SENTINEL_IMAGE=sentinel-backend:smoke` and
     `COMPOSE_CMD="docker compose"`.
- No push, no registry login. This is a gate, not a publication.

#### 6b. Modify `.github/workflows/build-images.yml`

Remove the `workflow_dispatch` trigger entirely. The remaining
triggers are:

```yaml
on:
  workflow_run:
    workflows: ["CI"]
    branches: [master]
    types: [completed]
  push:
    tags: ["v*"]
```

This prevents manual runs from publishing unreviewed images. In
exceptional cases, re-run the existing workflow via `gh run rerun`
or trigger a corrective PR.

#### 6c. Modify `.github/workflows/release-please.yml`

Change the trigger from `push` on `master` to `workflow_run` gated
behind CI:

```yaml
on:
  workflow_run:
    workflows: ["CI"]
    branches: [master]
    types: [completed]
```

Add a condition to the job:

```yaml
jobs:
  release-please:
    if: github.event.workflow_run.conclusion == 'success'
```

This ensures release-please only processes commits that have
passed CI. The Release PR is updated only after CI confirms the
code is valid.

Update the checkout step to use
`github.event.workflow_run.head_sha` for consistency:

```yaml
- uses: actions/checkout@v7
  if: steps.release.outputs.pr
  with:
    ref: ${{ fromJSON(steps.release.outputs.pr).headBranchName }}
    token: ${{ secrets.RELEASE_TOKEN }}
```

(This part is unchanged — the checkout targets the Release PR
branch, not the triggering SHA.)

#### 6d. Modify `.github/workflows/deploy-api-docs.yml`

Remove the `workflow_dispatch` trigger. The remaining trigger is:

```yaml
on:
  push:
    tags: ["v*"]
```

Note: GitHub Pages is not currently enabled on the repository. This
workflow will fail on the first tag until Pages is configured. This
is a pre-existing issue, not introduced by this change.

---

### Step 7 — New Files

#### 7a. Create `.github/dependabot.yml`

Configure Dependabot version updates for all relevant ecosystems:

**Python (uv)** — `backend/`:
- Schedule: weekly (Monday).
- Commit message prefix: `chore`, include scope (produces
  `chore(deps): ...`).
- Group minor and patch updates together.
- Open PR limit: 5.
- Labels: `dependencies`.

**GitHub Actions** — `/`:
- Schedule: weekly (Monday).
- Commit message prefix: `ci`, include scope (produces
  `ci(deps): ...`).
- Open PR limit: 5.
- Labels: `dependencies`.

**Docker** — `backend/`:
- Schedule: weekly (Monday).
- Commit message prefix: `chore`, include scope.
- Open PR limit: 3.
- Labels: `dependencies`.

**npm** — `.opencode/`:
- Schedule: monthly.
- Commit message prefix: `chore`, include scope.
- Open PR limit: 2.
- Labels: `dependencies`.

All ecosystems: no auto-merge. Dependabot PRs follow the same
merge gate as any other PR (CI must pass, human approval required).

#### 7b. Create `.github/pull_request_template.md`

Provide a PR template with the following sections:

- **Summary**: what the PR does, link to owning spec, link to
  implementation plan piece (if applicable).
- **Changes**: list of main changes.
- **Data model / Migrations**: new or modified tables, columns,
  constraints. "None" if not applicable.
- **Tests**: tests added or modified, with markers
  (unit/integration/e2e).
- **Verification checklist**: checkboxes for pytest, ruff, alembic,
  manual verification, image smoke test (if applicable).
- **Reviewers**: which reviewer agents were invoked and outcome
  summary.
- **Notes**: risks, limitations, stubs introduced, follow-up work.

The template guides the agent (and future human contributors) to
provide a complete change record. Keep it concise — the sections
are prompts, not essays.

#### 7c. Create GitHub labels

Create labels referenced by Dependabot and useful for PR
categorization:

```
gh label create dependencies --color 0075ca --description "Dependency updates"
gh label create ci --color e4e669 --description "CI/CD changes"
```

(Existing labels like `bug`, `enhancement`, `documentation` are
already present.)

---

### Step 8 — Open PR and Merge

1. Commit all changes from Steps 3–7 on the
   `chore/adopt-pr-workflow` branch. Use meaningful, logically
   grouped commits (e.g., one for documentation, one for OpenCode
   tooling, one for hooks, one for CI, one for new files). Exact
   grouping is at the implementer's discretion.

2. Push the branch:
   ```
   git push -u origin chore/adopt-pr-workflow
   ```

3. Open a PR targeting `master` with title:
   ```
   chore: adopt PR-based development workflow
   ```

4. Fill the PR description using the new PR template (itself
   included in the PR — the template applies to future PRs; for
   this PR, manually follow the template structure).

5. Wait for CI to pass on the PR. This is the first PR to run
   the new `pr-title` and `image-smoke` jobs — verify they work
   correctly.

6. Present the PR to the user with the merge gate summary (per
   Guardrail 25).

7. After explicit user authorization, execute:
   ```
   gh pr merge <number> --squash --delete-branch
   ```

8. Synchronize local state:
   ```
   git fetch origin
   git switch master
   git pull --ff-only origin master
   ```

---

### Step 9 — Post-Merge Verification

After the merge lands on `master`, verify:

| Workflow | Expected behavior |
|----------|-------------------|
| CI | Runs on the merge commit; all jobs pass |
| Release Please | Runs **after CI completes** (not in parallel); updates Release PR #1 |
| Build Docker Images | Runs after CI completes; builds, smokes, pushes `latest` |

If any workflow fails, diagnose and fix via a new PR (following the
new process).

Verify that the pre-push hook blocks a direct push attempt (create a
trivial change to test, then revert):

```
echo "" >> README.md
git add README.md && git commit -m "test: verify pre-push hook"
git push origin master
# Expected: "Error: direct push to master is not allowed."
git reset --soft HEAD~1 && git checkout -- README.md
```

---

### Step 10 — Reviewers

After the PR is merged and post-merge verification is complete, run
the following reviewers to confirm the changes are correct and
consistent:

| Reviewer | Reason |
|----------|--------|
| `@cicd` | Verify all workflow changes (ci.yml, build-images.yml, release-please.yml, deploy-api-docs.yml) are correct, triggers are consistent, and no unintended side effects |
| `@docs-reviewer` | Verify documentation coherence between `docs/conventions.md`, `AGENTS.md`, `docs/deployment.md`, and `.opencode/README.md` |
| `@docs-placement-reviewer` | Verify that the new Git workflow rules and PR requirements are placed in the correct documents (conventions.md vs AGENTS.md) |

If reviewers identify issues, fix them via a follow-up PR (using the
new workflow).

---

### Step 11 — Delete This Draft

After all reviewer findings are addressed:

1. Create a branch (e.g., `chore/remove-workflow-migration-draft`).
2. Delete `docs/drafts/workflow-migration.md`.
3. Open a PR, get CI green, get user approval, merge.

This is the final step. No permanent artifact references this file.

---

## Appendix: Files Modified or Created

| File | Action | Step |
|------|--------|------|
| `docs/conventions.md` | Modify (expand Git Conventions) | 3a, 3b |
| `AGENTS.md` | Modify (add Guardrail 25) | 3c |
| `docs/deployment.md` | Modify (update Release Process) | 3d |
| `.opencode/commands/run-tests.md` | Delete | 4a |
| `.opencode/commands/check-spec.md` | Delete | 4b |
| `.opencode/commands/idea.md` | Modify (remove auto-commit) | 4c |
| `.opencode/skills/new-feature/` | Delete (entire directory) | 4d |
| `.opencode/skills/new-api-endpoint/SKILL.md` | Modify (add workflow reference) | 4e |
| `.opencode/README.md` | Modify (remove deleted commands and skill) | 4f |
| `.opencode/prompts/code.md` | Modify (add Git Safety) | 4g |
| `.githooks/pre-push` | Modify (add master/tag guard) | 5a |
| `.github/workflows/ci.yml` | Modify (add pr-title, image-smoke) | 6a |
| `.github/workflows/build-images.yml` | Modify (remove workflow_dispatch) | 6b |
| `.github/workflows/release-please.yml` | Modify (gate behind CI) | 6c |
| `.github/workflows/deploy-api-docs.yml` | Modify (remove workflow_dispatch) | 6d |
| `.github/dependabot.yml` | Create | 7a |
| `.github/pull_request_template.md` | Create | 7b |
