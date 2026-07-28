# Review: architecture

**Spec**: `docs/architecture.md`
**Last reviewed**: 2026-07-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### ARCH-GAP-001 — "HTTP APIs only" constraint heading contradicts git-based fetcher infrastructure (Medium)

**Status**: RESOLVED — Renamed heading to "HTTP APIs for external services" in architecture.md and added disambiguation clause distinguishing service-wrapper CLIs (prohibited) from transport-protocol clients like git (permitted). Applied matching clarification in conventions.md. (2026-07-28)

### ARCH-GAP-002 — CLI entry point not addressed in layer architecture table (Medium)

**Status**: RESOLVED — Added CLI row to Backend Layer Architecture table with dependency rules and key rule (2026-07-28)

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
