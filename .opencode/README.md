# OpenCode Tooling

This directory contains the OpenCode agent, command, and skill definitions
for the Sentinel project. This README serves as a quick-reference catalog.

For details on how agents are triggered automatically, see the Guardrails
section in `AGENTS.md`.

## Primary Agents

Primary agents are the main interaction modes, switchable with the Tab key.
They are configured in `opencode.json`.

| Agent | Scope | Permissions |
|-------|-------|-------------|
| **Plan** | Analysis and planning | Read-only (built-in) |
| **Spec** | Specifications and declarative configuration | Edit: `docs/**`, `AGENTS.md`, `.opencode/**`, `opencode.json`; shell: Git/GitHub workflow, read-only inspection, tests |
| **Code** | Implementation, tests, CI/CD, infrastructure | Edit: all files (`docs/**` requires confirmation) |

- **Plan** — read-only mode for analysis, planning, and discussion without
  making changes. Uses the OpenCode built-in Plan agent.
- **Spec** — writes and maintains feature specifications, data model, API
  spec, conventions, agent definitions, and all declarative project
  configuration. It can run tests and manage its GitHub workflow, but cannot
  modify implementation code. Prompt:
  `.opencode/prompts/spec.md`
- **Code** — implements features from specifications, writes tests, and
  maintains all executable artifacts. Must signal unresolved behavioral or
  contract gaps, while retaining freedom over compliant internal technical
  choices. Prompt:
  `.opencode/prompts/code.md`

## Subagents

All subagents are defined in `.opencode/agents/`. Every subagent is a
**read-only reviewer**: it analyzes code or specifications and reports
findings without modifying files. All writing is owned by the primary
agents.

All reviewer agents apply the proportionality filter in `AGENTS.md`
Guardrail 26 before reporting findings. Speculative, unnecessary,
over-documenting, or disproportionate findings are omitted rather than turned
into project requirements.

| Agent | Type | Trigger | Purpose |
|-------|------|---------|---------|
| `@api-convention-reviewer` | Reviewer | Guardrail 20 | Verifies API endpoint definitions in specs conform to project conventions |
| `@api-parity-reviewer` | Reviewer | Guardrail 12 | Verifies the REST API exposes all operations defined in feature specifications |
| `@cicd-reviewer` | Reviewer | Guardrail 5 | Reviews CI/CD artifacts (workflows, Dockerfile, compose, git hooks, release-please config) for convention conformity and pipeline coherence |
| `@data-model-reviewer` | Reviewer | Guardrail 8 | Reviews data model changes for simplicity, consistency, and conventions |
| `@design-reviewer` | Reviewer | Guardrail 26 | Applies a simplicity-first review to new or substantially modified feature specs |
| `@docs-placement-reviewer` | Reviewer | Guardrail 21 | Verifies rules and patterns are placed in the most appropriate location (not misplaced or duplicated) |
| `@docs-reviewer` | Reviewer | Guardrail 9 | Reviews documentation completeness and coherence with implementation |
| `@external-contract-verifier` | Reviewer | On-demand | Verifies external service request/response structures match real upstream contracts |
| `@fetcher-compliance-reviewer` | Reviewer | Guardrail 14 | Verifies fetchers inherit from BaseFetcher (or BaseCVEFetcher for CVE fetchers), report metrics correctly, and exclude `SoftTimeLimitExceeded` from per-item catches |
| `@security-reviewer` | Reviewer | Guardrail 10 | Reviews code for security vulnerabilities and insecure patterns |
| `@spec-coherence-reviewer` | Reviewer | Guardrail 15 | Detects contradictions and inconsistencies across feature specifications |
| `@spec-conformance-reviewer` | Reviewer | Pre-PR (unconditional) | Verifies a pull request implements what its issue and owning specs require, and introduces no unspecified behavior |
| `@spec-gap-analyzer` | Reviewer | Guardrail 17 | Identifies uncovered functional cases and missing edge-case handling in specs |
| `@test-reviewer` | Reviewer | Guardrail 6 | Reviews test quality, coverage, audit trail assertions, and adherence to testing conventions |
| `@identity-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies IdentityAuditEvent audit trail compliance and detail JSONB schema completeness for identity mutations |
| `@ticket-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies TicketAuditEvent audit trail and ticket_mutations module compliance |

`@spec-conformance-reviewer` is the only subagent whose trigger is a moment
rather than a kind of change: it runs on every pull request, before the pull
request is opened or marked ready. It can also be invoked manually with an
explicit pull request reference, including on closed pull requests. It is not
yet backed by a guardrail — it is under calibration, and its invocation is
declared in `.opencode/prompts/code.md` and `.opencode/prompts/spec.md`.

### Model Tiering

Subagents have no `model` pinned by default: they inherit the model of the
primary agent that invoked them (`spec` or `code`, see Primary Agents above).
Subagents whose task involves deep analytical reasoning — finding subtle
security flaws, evaluating architectural complexity, discovering unspecified
scenarios, or reconciling nuanced cross-document/cross-diff detail — are
pinned to a more capable model with extended thinking enabled, since GitHub
Copilot prices all Claude Opus versions identically per token regardless of
version. All other reviewer subagents are pinned to a mid-tier model with a
high reasoning-effort preset, so their analytical depth is consistent
regardless of the invoking primary agent, at lower cost/latency than the
Opus 5 tier. No subagent currently relies on the inherited-model default;
any future subagent added without a `model` field falls back to it.

| Tier | Model | Agents |
|------|-------|--------|
| 1 (pinned) | `github-copilot/claude-opus-5`, `variant: high` | `@security-reviewer`, `@design-reviewer`, `@spec-gap-analyzer`, `@spec-conformance-reviewer`, `@spec-coherence-reviewer` |
| 2 (pinned) | `github-copilot/claude-sonnet-5`, `variant: xhigh` | `@api-convention-reviewer`, `@api-parity-reviewer`, `@cicd-reviewer`, `@data-model-reviewer`, `@docs-placement-reviewer`, `@docs-reviewer`, `@external-contract-verifier`, `@fetcher-compliance-reviewer`, `@identity-integrity-reviewer`, `@test-reviewer`, `@ticket-integrity-reviewer` |
| 3 (inherited, no current members) | Invoking primary agent's model | — |

Tier 1 and Tier 2 agents use the top-level `variant` frontmatter field, not a
raw `options.thinking` block. `variant` selects one of the model's
provider-defined reasoning-effort presets, and OpenCode translates it into
whatever wire-level thinking configuration the specific model/provider pair
requires. Hand-crafting `options.thinking` directly is discouraged for
adaptive-thinking models (Opus ≥ 4.7, including `claude-opus-5`): their wire
protocol differs from older Opus versions and from the API contract accepted
by this model, and a hand-written thinking block can silently target the
wrong protocol version.

## Commands

Commands are defined in `.opencode/commands/` and invoked with `/command-name`.

| Command | Purpose |
|---------|---------|
| `/idea` | Add a new idea to the brainstorming list in `docs/drafts/ideas.md` |
| `/review-spec` | Interactive spec review and finding resolution workflow |

## Skills

Skills are defined in `.opencode/skills/` and provide guided, multi-step
workflows for common tasks. They are loaded automatically when a task matches
their description.

| Skill | Purpose |
|-------|---------|
| `new-api-endpoint` | Guided workflow for adding/modifying an endpoint in an existing feature (schema, service, thin route, tests, and reviews) |

## Directory Structure

```
.opencode/
├── agents/           # Subagent definitions (one .md file per agent)
├── commands/         # Slash command definitions
├── prompts/          # Primary agent prompt files
│   ├── spec.md       # Spec agent instructions
│   └── code.md       # Code agent instructions
├── skills/           # Multi-step workflow definitions
├── package.json      # Plugin dependency (@opencode-ai/plugin)
└── README.md         # This file
```
