# OpenCode Tooling

This directory contains the OpenCode agent, command, and skill definitions
for the Sentinel project. This README serves as a quick-reference catalog.

For details on how agents are triggered automatically, see the Guardrails
section in `AGENTS.md`.

## Agents

All agents are defined in `.opencode/agents/`. Unless noted otherwise, agents
are **read-only reviewers** that analyze code or specifications and report
findings without modifying files.

| Agent | Type | Trigger | Purpose |
|-------|------|---------|---------|
| `@api-parity-reviewer` | Reviewer | Guardrail 12 | Ensures the REST API provides at least the same operability as the web UI |
| `@cicd` | Implementation | Guardrail 5 | CI/CD pipeline expert for GitHub Actions, Dockerfiles, and deployment configs |
| `@data-model-reviewer` | Reviewer | Guardrail 8 | Reviews data model changes for simplicity, consistency, and conventions |
| `@design-reviewer` | Reviewer | On-demand | Evaluates architectural decisions, complexity, and alternatives in feature specs |
| `@docs-reviewer` | Reviewer | Guardrail 9 | Reviews documentation completeness and coherence with implementation |
| `@fetcher-compliance-reviewer` | Reviewer | Guardrail 14 | Verifies fetchers inherit from BaseFetcher and report metrics correctly |
| `@security-reviewer` | Reviewer | Guardrail 10 | Reviews code for security vulnerabilities and insecure patterns |
| `@spec-coherence-reviewer` | Reviewer | Guardrail 15 | Detects contradictions and inconsistencies across feature specifications |
| `@spec-gap-analyzer` | Reviewer | Guardrail 17 | Identifies uncovered functional cases and missing edge-case handling in specs |
| `@test-reviewer` | Reviewer | Guardrail 6 | Reviews test quality, coverage, and adherence to testing conventions |
| `@ticket-integrity-reviewer` | Reviewer | Guardrail 11 | Verifies TicketEvent audit trail and ticket_mutations module compliance |
| `@ui-reviewer` | Reviewer | Guardrail 7 | Reviews frontend components for UI consistency and design system compliance |

## Commands

Commands are defined in `.opencode/commands/` and invoked with `/command-name`.

| Command | Purpose |
|---------|---------|
| `/check-spec` | Verify that implementation code conforms to its feature specification |
| `/idea` | Add a new idea to the brainstorming list in `docs/drafts/ideas.md` |
| `/run-tests` | Run the full test suite (backend + frontend tests and linting) |

## Skills

Skills are defined in `.opencode/skills/` and provide guided, multi-step
workflows for common tasks. They are loaded automatically when a task matches
their description.

| Skill | Purpose |
|-------|---------|
| `new-api-endpoint` | Guided workflow for creating a new API endpoint with proper schema, service layer, and tests |
| `new-feature` | Guided workflow for adding a new feature following the spec-first approach |

## Directory Structure

```
.opencode/
├── agents/           # Agent definitions (one .md file per agent)
├── commands/         # Slash command definitions
├── skills/           # Multi-step workflow definitions
├── package.json      # Plugin dependency (@opencode-ai/plugin)
└── README.md         # This file
```
