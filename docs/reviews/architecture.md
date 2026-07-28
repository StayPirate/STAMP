# Review: architecture

**Spec**: `docs/architecture.md`
**Last reviewed**: 2026-07-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### ARCH-GAP-001 — "HTTP APIs only" constraint heading contradicts git-based fetcher infrastructure (Medium)

**Category**: Scope inconsistency
**Status**: OPEN

The Design Constraints section uses the heading "HTTP APIs only" and states: "When integrating with external services (IBS, SMELT, AIMAAS, Bugzilla, etc.), Sentinel uses their HTTP/REST APIs directly. Command-line tools (`osc`, `secbox`, etc.) are for ad-hoc exploratory testing only and must not be used in application code or background tasks." However, the same document describes `BaseGitFetcher` in the Integration Patterns section, which uses delta-flow (clone + fetch + diff) via the `git` binary through `asyncio.create_subprocess_exec`. The `git-fetcher-infrastructure.md` confirms that `git_operations.py` invokes the `git` binary via subprocess — a command-line tool used in application code. The heading implies a blanket prohibition on non-HTTP integrations, but git-based fetchers (MITRE, Linux Kernel) have no REST API alternative. The constraint needs to either explicitly carve out the git exception or clarify that the prohibition targets external service CLI wrappers (like `osc`) rather than transport-protocol clients (like `git`).

### ARCH-GAP-002 — CLI entry point not addressed in layer architecture table (Medium)

**Category**: Coverage gap
**Status**: OPEN

The Backend Layer Architecture section defines six layers (API, Service, Model, Schema, Core, Task) with explicit "May depend on" rules. CLI commands live in `app/cli/` (per the project structure in AGENTS.md) and serve as a distinct entry point alongside API handlers and Celery tasks. The architecture document mentions CLI in passing under "Async-only database layer" but the layer architecture table does not include CLI as an entry point and does not specify which layers CLI commands may depend on. The Task layer (which is analogous — both are entry points that call services) lists "Service, Core" as dependencies, and CLI likely follows the same pattern, but this is not stated. An implementer building a CLI command cannot determine from this document which layers they may import from.

### ARCH-GAP-003 — Cross-Reference Index incomplete (Low)

**Category**: Cross-reference completeness
**Status**: OPEN

The Integration Patterns section body text references three documents that are absent from the Cross-Reference Index table at the bottom: `docs/features/platform/cve-fetcher-infrastructure.md`, `docs/features/platform/git-fetcher-infrastructure.md`, and `docs/features/integrations/ibs-rabbitmq-integration.md`. The index already includes `docs/features/platform/fetcher-infrastructure.md` (the base fetcher spec), so the omission of the two sub-hierarchy specs and the sole event-driven integration spec is inconsistent.

---

## Coherence

### ARCH-COH-001 — system-map.md misrepresents MITRE integration protocol and host (Medium)

**Category**: Cross-document contradiction
**Status**: OPEN

`system-map.md` (referenced by architecture.md as "Component diagrams and data flow visuals") labels the MITRE integration as "REST API" at `cveawg.mitre.org`. This contradicts architecture.md itself (Integration Patterns: "BaseGitFetcher — uses delta-flow instead of REST API polling"), data-sources.md (MITRE: "Access: cvelistV5 GitHub repository, bare clone + fetch"), and deployment.md (Network Access: "GitHub | github.com | 443 | MITRE cvelistV5 repository clone/fetch"). The MITRE data source is `github.com` (git clone/fetch), not `cveawg.mitre.org` (REST API). Additionally, `sync_mitre_cves` runs on the git worker (via `queue="git"`), not on the regular Celery worker shown in the diagram.

### ARCH-COH-002 — system-map.md omits git worker from process topology (Low)

**Category**: Diagram completeness
**Status**: OPEN

The System Components diagram in `system-map.md` shows only "Celery Beat" and "Celery Workers" in the Task Queue subgraph. Architecture.md and deployment.md enumerate 5 process roles including a distinct git worker with volume affinity. The git worker runs git-based fetchers on a dedicated Celery queue with a persistent volume. Omitting it from the component diagram misrepresents the process topology. The diagram also omits `git.kernel.org` and SUSE IdP (`id.suse.com`) from its external services, although both appear in deployment.md's Network Access table.

---

## Design

🟢 No issues found.

---

## Security

🟢 No issues found.

---

## API Conventions

🟢 No issues found.
