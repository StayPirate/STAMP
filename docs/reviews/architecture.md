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

**Status**: RESOLVED — Added three missing entries (cve-fetcher-infrastructure, git-fetcher-infrastructure, ibs-rabbitmq-integration) to Cross-Reference Index (2026-07-28)

---

## Coherence

### ARCH-COH-001 — system-map.md misrepresents MITRE integration protocol and host (Medium)

**Status**: RESOLVED — Fixed the System Components diagram in system-map.md: renamed the MITRE node to "MITRE cvelistV5 (github.com)", changed its connection label to "Git (clone/fetch)", introduced a distinct "Git Worker" node in the Task Queue subgraph connected to MITRE, and added a "Linux Kernel (git.kernel.org)" external service node also connected to the Git Worker (2026-07-28)

### ARCH-COH-002 — system-map.md omits git worker from process topology (Low)

**Status**: RESOLVED — Added SUSE IdP (id.suse.com) node to External Services subgraph with OIDC/SSO edge from API; Git Worker and git.kernel.org were already added by ARCH-COH-001 (2026-07-28)

---

## Design

🟢 No issues found.

---

## Security

🟢 No issues found.

---

## API Conventions

🟢 No issues found.
