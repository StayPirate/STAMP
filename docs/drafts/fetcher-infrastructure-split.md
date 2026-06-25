# Draft: Fetcher Infrastructure Spec Split

Split `docs/features/platform/fetcher-infrastructure.md` (163 KB, 3378
lines) into four focused documents aligned to the class hierarchy and
cross-cutting concerns.

## Context

The current spec contains four distinct concerns:

| Layer | Content | ~Lines | Share | Audience |
|---|---|---|---|---|
| **A. Generic fetcher core** | BaseFetcher, naming, error sanitization, custom settings, catch_up mechanism, registry, Celery, concurrency, data model, retention, deregistration, doc requirements | ~1,430 | 42% | every fetcher author |
| **B. CVE base class** | BaseCVEFetcher, fetch_single, CVENotInSource, cve_source_type, CVE catch_up default, CVE conventions, session lifecycle | ~630 | 19% | only CVE fetchers |
| **C. Git base class** | git_operations.py catalog, BaseGitFetcher template, bare clone, cursor, recovery, worker affinity | ~964 | 29% | only 2 fetchers (MITRE, kernel) |
| **D. Networking** | Shared HTTP client factory, transport-level retry, TLS trust store, proxy, non-fetcher components | ~262 | 8% | fetchers + IBSClient + LDAP + AMQP |

**Primary issues:**

1. Layer D (Networking) is cross-cutting: `IBSClient`
   (`ibs-integration.md`), `sync_ldap_directory` (`ad-integration.md`),
   and `IBSEventConsumer` (`ibs-rabbitmq-integration.md`) all reference
   `fetcher-infrastructure.md` for their transport/TLS configuration.
   An AMQP consumer should not need to open a fetcher base class spec
   for TLS trust store documentation
2. Layer C (Git) is self-contained with its own module
   (`git_operations.py`), exception hierarchy, and template-method
   class, yet is consumed by only 2 fetchers
3. Layer B (CVE) defines `BaseCVEFetcher` and all CVE-specific
   conventions, making it a natural intermediate spec between the core
   and individual CVE fetcher specs
4. Guardrail 21 (Information placement) flags cross-cutting rules
   trapped in feature specs — Layers B, C, and D all qualify

## Goals & Non-Goals

**Goals:**

- Separate four concerns into four focused documents
- Maintain all semantic content unchanged (no rewording, no new rules)
- Keep every cross-reference valid after each phase
- Allow independent execution and review per phase
- Section order in new files follows the normative skeleton defined per
  phase (may differ from source order for improved readability)

**Non-goals:**

- Rewriting prose within sections (only reparenting)
- Adding new specifications or changing behavior
- Modifying historical review finding files

## Target Structure

### After split

| File | Layer | Content | Domain |
|---|---|---|---|
| `platform/fetcher-infrastructure.md` | A | BaseFetcher base class, naming, error sanitization, custom settings, catch_up mechanism (generic override-point + boundary conditions), registry, Celery integration, concurrency, stale run detection, data model, retention, deregistered lifecycle, doc requirements, BaseFetcher HTTP integration | `platform/` |
| `platform/networking.md` | D | Shared HTTP client factory, default config, User-Agent, timeouts, transport retry, compression, proxy, non-fetcher components, TLS trust store, protocol-specific integration | `platform/` |
| `platform/cve-fetcher-infrastructure.md` | B | BaseCVEFetcher class, on-demand single-item fetch, CVENotInSource, fetch_single signaling/retry/errors, CVE source type identity, default catch_up (CVE delegation logic), CVE fetcher conventions (batch errors, first run, metrics), session lifecycle | `platform/` |
| `platform/git-fetcher-infrastructure.md` | C | Bare clone pattern, cursor persistence, first-run detection, env config, volumes, worker affinity, concurrency rules, recovery, runtime deps, error classification, git_operations.py catalog, BaseGitFetcher class (template method, hooks, fetch_single impl) | `platform/` |

### Inheritance chain preserved across documents

```
fetcher-infrastructure.md
  BaseFetcher (lifecycle, metrics, FetcherRun, cursor, registry, Settings)
    │
    │  cve-fetcher-infrastructure.md
    └── BaseCVEFetcher (cve_source_type, fetch_single, catch_up, conventions)
        │
        │  git-fetcher-infrastructure.md
        └── BaseGitFetcher (clone, fetch, delta, recovery, template execute())
```

Each document opens with a "Position in hierarchy" section pointing to
its parent and children. The `__init_subclass__` chaining documentation
lives in `cve-fetcher-infrastructure.md` (CVE-specific validation +
execution order + `_CVE_SOURCE_TYPE_MAP`) with an explicit
cross-reference to the BaseFetcher validation rules in the core spec
(source 1086-1145, "Import-time validation"), which retains the
generic abstract/MRO/super()-chaining discussion and remains the
canonical home for the multi-class registration contract.

### Scope boundaries for `networking.md`

`networking.md` covers exclusively: HTTP client factory configuration,
transport-level retry, TLS trust store. It does NOT own protocol-level
reconnection logic (e.g., AMQP reconnection in
`ibs-rabbitmq-integration.md`), LDAP connection pooling, or any
application-level retry policy — those belong to their respective
integration specs. The Purpose section must make this boundary
explicit.

## Phased Execution Plan

Phases are ordered **parent-before-child** to minimize dangling
references: networking (no inheritance dependency) → CVE (parent of
Git) → Git (child of CVE, can reference its parent directly) →
cross-ref migration.

Each phase is a **single atomic commit directly on master**. If a
phase produces an incorrect result, revert the commit (`git revert`)
before proceeding to the next phase.

Bridge sections (pointer stubs) are left in the core spec after each
extraction so that generic references remain functional during
inter-phase commits. Phase 4 consolidates all stubs into a single
"Related specifications" section and fixes all external
cross-references.

---

### Phase 1: Extract Networking (`platform/networking.md`)

**Risk**: lowest. Self-contained; no inheritance chain dependencies.

#### Sections to extract from `fetcher-infrastructure.md`

| Current section | Current header level |
|---|---|
| Shared HTTP Client | `##` |
| — Factory Module | `###` |
| — Default Configuration | `###` |
| — User-Agent | `####` |
| — Timeouts | `####` |
| — TLS Configuration | `####` (in-section pointer — drop after merge into same file) |
| — Transport-Level Retry | `####` |
| — HTTP Response Compression | `####` |
| — Proxy Configuration | `####` |
| — Non-Fetcher Components | `###` |
| TLS Trust Store Configuration | `##` |
| — Protocol-Specific Integration | `###` |

**Excluded from extraction** (stays in core):

| Section | Reason |
|---|---|
| BaseFetcher Integration (`###` under Shared HTTP Client) | Fetcher-specific: lazy property, override mechanism, fetch_single/catch_up lifecycle |

**Note**: The in-section `#### TLS Configuration` pointer (source
1333-1338) becomes redundant once the factory and TLS sections live in
the same file. Drop it during extraction (not a content deletion — it
was always a forward pointer, not substantive content).

#### New file structure: `platform/networking.md`

```
# Networking Infrastructure

## Purpose
Cross-cutting HTTP client and TLS trust store infrastructure used by
all Sentinel components that make outgoing network connections: fetchers
(via BaseFetcher), IBSClient, sync_ldap_directory (LDAP), and
IBSEventConsumer (AMQP).

Scope boundary: this spec covers the shared HTTP client factory and TLS
trust store configuration. Protocol-level reconnection logic, connection
pooling, and application retry policies belong to their respective
integration specs.

## Shared HTTP Client
### Factory Module
### Default Configuration
#### User-Agent
#### Timeouts
#### Transport-Level Retry
#### HTTP Response Compression
#### Proxy Configuration
### Non-Fetcher Components

## TLS Trust Store Configuration
### Protocol-Specific Integration

## Cross-references
- `fetcher-infrastructure.md` — BaseFetcher HTTP integration (lazy property, overrides)
- `ibs-integration.md` — IBSClient usage
- `ad-integration.md` — LDAP TLS configuration
- `ibs-rabbitmq-integration.md` — AMQP TLS configuration
- `configuration.md` — environment variable index
```

#### Bridge section in core spec

Replace the extracted sections with:

```markdown
## Shared HTTP Client

All outgoing HTTP requests from fetchers use a shared HTTP client
infrastructure. For the factory module, default configuration, transport
retry, TLS trust store, and non-fetcher usage, see
`docs/features/platform/networking.md`.

### BaseFetcher Integration

[existing content stays: Lazy Property, Override Mechanism,
fetch_single/catch_up Lifecycle — unchanged]
```

The `## TLS Trust Store Configuration` section is replaced with a
single-line pointer:

```markdown
## TLS Trust Store Configuration

See `docs/features/platform/networking.md` (TLS Trust Store
Configuration).
```

#### Definition of done

- [ ] `platform/networking.md` exists with all extracted content
- [ ] In-section `#### TLS Configuration` pointer dropped (redundant)
- [ ] Core spec contains bridge sections with pointers
- [ ] `### BaseFetcher Integration` subsection unchanged in core
- [ ] No broken internal cross-references within either file

---

### Phase 2: Extract CVE Infrastructure (`platform/cve-fetcher-infrastructure.md`)

**Risk**: medium. Content is interleaved with the generic catch_up
mechanism — requires precise sub-section-level separation.

**Note on phase ordering**: this phase extracts CVE (the parent class)
BEFORE Git (the child class) so that Phase 3 can reference
`cve-fetcher-infrastructure.md` directly without dangling links.

#### Sections to extract from `fetcher-infrastructure.md`

| Current section | Current header level | Notes |
|---|---|---|
| On-demand Single-Item Fetch | `##` | |
| — CVENotInSource Signal | `###` | |
| — fetch_single Signaling Convention | `###` | |
| — Retry Policy for fetch_single | `###` | |
| — Error Categorization | `###` | |
| — Isolation Guarantee | `###` | |
| CVE Source Type Identity | `##` | |
| — cve_source_type class attribute | `###` | |
| — Data contract stability rule | `###` | |
| — Code convention: self.cve_source_type usage | `###` | |
| — Registry accessor: get_fetch_single_fetchers() | `###` | |
| **Partial**: Default implementation for CVE fetchers | `###` (under catch_up) | **Only CVE-specific lines** — see cut details below |
| BaseCVEFetcher Class | `##` | |
| — Class Attributes | `###` | |
| — Concrete Methods | `###` | |
| — __init_subclass__ Validation | `###` | |
| — Non-Modification Statement | `###` | |
| — Session Lifecycle for API-based CVE Fetchers | `###` | |
| CVE Fetcher Conventions | `##` | |
| — Batch Error Handling | `###` | |
| — First Run Behavior | `###` | |
| — Metric Definitions | `###` | |

#### The catch_up sub-section-level cut (W2 fix)

The "Default implementation for CVE fetchers" subsection (source
630-681) is **mixed**. Extract only the CVE-specific lines; keep the
generic `BaseFetcher` override-point contract in core:

**Stays in core** (under `## Per-Ticket Catch-Up`):

| Source lines | Content | Why |
|---|---|---|
| 632-648 | `class BaseFetcher: catch_up()` raising `NotImplementedError` | Generic override-point |
| 650-654 | `NotImplementedError` is non-retryable in `run_catch_up` | Generic task wrapper rule |
| 663-671 | "Boundary conditions for custom `catch_up()` overrides (applies to all fetchers — CVE and non-CVE)" | Explicitly cross-cutting |
| 679-681 | "Non-CVE fetchers override `catch_up()` with custom logic" | Generic guidance |

**Moves to CVE spec** (as "Default catch_up Implementation"):

| Source lines | Content | Why |
|---|---|---|
| 656-661 | "`BaseCVEFetcher` provides the concrete default implementation that all CVE fetchers inherit…" | CVE-specific |
| 672-678 | "CVE fetchers with `supports_fetch_single = True` only need to implement `fetch_single(cve_id)`…" | CVE-specific |

The resulting core section is renamed from "Default implementation for
CVE fetchers" to "Override-point contract" (or integrated into the
existing catch_up intro). The bridge pointer says:

```markdown
For the concrete default implementation inherited by CVE fetchers
(delegation to `fetch_single()`), see
`docs/features/platform/cve-fetcher-infrastructure.md` (Default
catch_up Implementation).
```

#### What stays in core (catch_up section)

The `## Per-Ticket Catch-Up: catch_up() Method` section stays in core
WITH the following subsections:

- Intro + method signature
- **Override-point contract** (lines 632-654 + 663-671 + 679-681 — the
  generic `BaseFetcher.catch_up()` definition + non-retryable rule +
  boundary conditions for ALL overrides)
- Registry accessor: `get_catch_up_fetchers()`
- Celery task wrapper
- Interface contract (generic — CVE-specific term references like
  `CVENotInSource`, `commit_and_dispatch()` must gain inline pointers:
  `(see cve-fetcher-infrastructure.md)`)
- Invocation points
- Fetcher inventory (reference table listing all fetchers)

#### __init_subclass__ fragmentation note (W4)

The generic abstract/MRO/super()-chaining discussion (source
1117-1133, within Custom Settings → Import-time validation) stays in
core and remains the canonical "multi-class registration contract."

The CVE spec's `__init_subclass__ Validation` section contains the
CVE-specific execution order example and `_CVE_SOURCE_TYPE_MAP`
mechanism. It MUST open with an explicit cross-reference:

> "For the generic `BaseFetcher.__init_subclass__` validation rules
> (name uniqueness, Settings validation, abstract exemption, MRO
> chaining), see `fetcher-infrastructure.md` (Custom Settings Schema →
> Import-time validation)."

This ensures an engineer adding a new intermediate class can follow
the full chaining story: core (generic rules) → CVE spec (CVE link in
the chain + execution order example).

#### New file structure: `platform/cve-fetcher-infrastructure.md`

```
# CVE Fetcher Infrastructure

## Purpose
Intermediate abstract class and conventions for all CVE fetchers —
those that ingest or enrich CVE-related data from external sources.
Extends BaseFetcher (see fetcher-infrastructure.md) with CVE-specific
contracts: cve_source_type, optional fetch_single(), default
catch_up(), and shared conventions.

## Position in Hierarchy
[hierarchy diagram: BaseFetcher → BaseCVEFetcher → (BaseGitFetcher, concrete fetchers)]

## BaseCVEFetcher Class
### Class Attributes
### Concrete Methods
### __init_subclass__ Validation
### Non-Modification Statement
### Session Lifecycle for API-based CVE Fetchers

## On-demand Single-Item Fetch
### CVENotInSource Signal
### fetch_single Signaling Convention
### Retry Policy for fetch_single
### Error Categorization
### Isolation Guarantee

## CVE Source Type Identity
### cve_source_type class attribute
### Data contract stability rule
### Code convention: self.cve_source_type usage
### Registry accessor: get_fetch_single_fetchers()

## Default catch_up Implementation
[CVE-specific delegation logic extracted from source 656-661, 672-678,
plus the BaseCVEFetcher.catch_up() code block from source 1561-1578]

## CVE Fetcher Conventions
### Batch Error Handling
### First Run Behavior
### Metric Definitions

## Cross-references
- `fetcher-infrastructure.md` — BaseFetcher base class, catch_up
  mechanism, registry, import-time validation (generic __init_subclass__)
- `git-fetcher-infrastructure.md` — BaseGitFetcher (child class)
- `cve-service.md` — upsert_cve(), fetch_single_cve orchestrator
- `cve-tracking.md` — CVE tracking feature, fetcher specifications
  table
- `networking.md` — Shared HTTP client
```

#### Bridge sections in core spec

1. Replace `## On-demand Single-Item Fetch` through
   `### Isolation Guarantee` with:

   ```markdown
   ## On-demand Single-Item Fetch

   For the `fetch_single()` method, `CVENotInSource` signal, signaling
   convention, retry policy, error categorization, and isolation
   guarantee, see
   `docs/features/platform/cve-fetcher-infrastructure.md`.
   ```

2. Replace `## CVE Source Type Identity` through
   `### Registry accessor: get_fetch_single_fetchers()` with:

   ```markdown
   ## CVE Source Type Identity

   For the `cve_source_type` class attribute, data contract stability
   rule, code convention, and `get_fetch_single_fetchers()` registry
   accessor, see
   `docs/features/platform/cve-fetcher-infrastructure.md`.
   ```

3. In the catch_up section, after the retained override-point contract,
   add the CVE pointer (see above).

4. Replace `## BaseCVEFetcher Class` through
   `### Session Lifecycle for API-based CVE Fetchers` with:

   ```markdown
   ## BaseCVEFetcher Class

   For the intermediate abstract class for CVE fetchers
   (class attributes, concrete methods, __init_subclass__ validation,
   session lifecycle), see
   `docs/features/platform/cve-fetcher-infrastructure.md`.
   ```

5. Replace `## CVE Fetcher Conventions` through
   `### Metric Definitions` with:

   ```markdown
   ## CVE Fetcher Conventions

   For shared CVE fetcher conventions (batch error handling, first run
   behavior, metric definitions), see
   `docs/features/platform/cve-fetcher-infrastructure.md`.
   ```

#### Definition of done

- [ ] `platform/cve-fetcher-infrastructure.md` exists with all
      extracted content
- [ ] Core spec contains bridge pointers for all 5 replaced blocks
- [ ] catch_up section retains generic override-point contract (lines
      632-654 + 663-671 + 679-681) with pointer for CVE default
- [ ] Interface contract has inline `(see cve-fetcher-infrastructure.md)`
      for CVE-only terms (`CVENotInSource`, `commit_and_dispatch()`)
- [ ] `__init_subclass__ Validation` section opens with cross-reference
      to core's Import-time validation
- [ ] Section order follows the normative skeleton above

---

### Phase 3: Extract Git Infrastructure (`platform/git-fetcher-infrastructure.md`)

**Risk**: low. Self-contained block (single `##` section with all
subsections). Now that CVE is already extracted, all references to
`BaseCVEFetcher` can point directly to
`cve-fetcher-infrastructure.md`.

#### Sections to extract from `fetcher-infrastructure.md`

The entire `## Git-Based Fetchers` section and all its subsections:

| Current section | Current header level |
|---|---|
| Git-Based Fetchers | `##` |
| — Bare Clone Pattern | `###` |
| — Cursor Persistence | `###` |
| — First-Run Detection | `###` |
| — Environment Configuration | `###` |
| — Volume Requirements | `###` |
| — Worker Affinity | `###` |
| — Concurrency Rules | `###` |
| — Recovery | `###` |
| — Runtime Dependencies | `###` |
| — Error Classification | `###` |
| — Implementation Location | `###` (includes git_operations.py catalog) |
| — BaseGitFetcher Class | `###` (includes template method, hooks, fetch_single impl) |

#### New file structure: `platform/git-fetcher-infrastructure.md`

```
# Git-Based Fetcher Infrastructure

## Purpose
Shared infrastructure for fetchers that synchronize data from external
Git repositories. Defines the BaseGitFetcher template-method class, the
git_operations.py utility module, and operational requirements (clone
pattern, cursor, recovery, worker affinity).

Current consumers: sync_mitre_cves, sync_kernel_cves.

Note: git_operations.py is independently usable by non-BaseGitFetcher
fetchers that need git operations without the template-method lifecycle
(see "When NOT to Use BaseGitFetcher" section).

## Position in Hierarchy
[hierarchy diagram: BaseFetcher → BaseCVEFetcher → BaseGitFetcher]

[All subsections from the extracted block, maintaining their current
structure and header levels (### becomes ##, #### becomes ###)]

## Cross-references
- `fetcher-infrastructure.md` — BaseFetcher base class
- `cve-fetcher-infrastructure.md` — BaseCVEFetcher class (parent)
- `cve-sync-mitre.md` — MITRE CVE fetcher (consumer)
- `cve-sync-kernel.md` — Kernel CVE fetcher (consumer)
- `networking.md` — Shared HTTP client (used by fetch_single blob download)
```

#### Bridge section in core spec

Replace the extracted section with:

```markdown
## Git-Based Fetchers

For the BaseGitFetcher template-method class, git_operations.py utility
module, and all git-specific infrastructure (bare clone pattern, cursor
persistence, recovery, worker affinity, error classification), see
`docs/features/platform/git-fetcher-infrastructure.md`.
```

#### Definition of done

- [ ] `platform/git-fetcher-infrastructure.md` exists with all
      extracted content
- [ ] Core spec contains bridge pointer
- [ ] Header levels adjusted (### → ##, #### → ###)
- [ ] Internal cross-references within the git spec updated (e.g.,
      references to "Cursor Persistence" section)
- [ ] References to BaseFetcher sections point back to core spec
- [ ] References to BaseCVEFetcher point to
      `cve-fetcher-infrastructure.md` (no dangling links — CVE file
      already exists from Phase 2)
- [ ] In-prose references to CVE concepts (`fetch_single`,
      `commit_and_dispatch`, `CVENotInSource`, `process_item` return
      type `PostIngestTasks`) verified and pointed to CVE spec where
      needed (grep extracted block for these terms)
- [ ] Section order follows the normative skeleton above

---

### Phase 4: Cross-Reference Migration + Reviews + Cleanup

**Risk**: lowest (mechanical). All content is already in the correct
files. This phase updates pointers.

#### 4a. Guardrail and Subagent section trim

In `fetcher-infrastructure.md`, replace the `## Guardrail: Fetcher
Base Class Compliance` and `## Subagent:
@fetcher-compliance-reviewer` sections with concise pointers:

```markdown
## Guardrail: Fetcher Base Class Compliance

See AGENTS.md (Guardrail 14) for the full compliance rules.

## Subagent: @fetcher-compliance-reviewer

See `.opencode/agents/fetcher-compliance-reviewer.md` for trigger
conditions, checks, and output format.
```

This removes ~84 lines of content duplicated from AGENTS.md and the
agent definition file.

#### 4b. Bridge consolidation

Replace the scattered bridge stubs (created in Phases 1-3) with a
**single consolidated "Related specifications" section** placed after
`## Terminology` in core:

```markdown
## Related Specifications

This document specifies the generic `BaseFetcher` contract. The
fetcher infrastructure is documented across four complementary specs:

| Spec | Content |
|---|---|
| **This document** | BaseFetcher base class, naming, error sanitization, custom settings, catch_up mechanism (generic), registry, Celery, concurrency, stale run detection, data model, retention, deregistered lifecycle, doc requirements |
| `cve-fetcher-infrastructure.md` | BaseCVEFetcher class, on-demand fetch_single, CVE source type identity, CVE catch_up default, CVE conventions |
| `git-fetcher-infrastructure.md` | BaseGitFetcher class, git_operations.py, clone/delta infrastructure |
| `networking.md` | Shared HTTP client factory, transport retry, TLS trust store (cross-cutting) |
| `fetcher-operations.md` | Monitoring dashboard, API endpoints, CLI diagnostics |
```

Remove the individual bridge stubs from their original locations.
Promote the kept `### BaseFetcher Integration` to a top-level
`## BaseFetcher HTTP Client Integration` section (no longer orphaned
under a stub parent).

#### 4c. Fix residual core→child internal references (W3)

Sections that **stay** in core contain in-document references ("see X
below") whose targets were extracted. These become broken internal
pointers that the anchor-grep cannot detect. Convert each to an
explicit cross-file reference.

**Known instances** (verify during execution; more may exist):

| Core location (source lines) | Broken reference text | Fix (new text) |
|---|---|---|
| 53-55 (BaseFetcher `run()`) | "see 'Session Lifecycle for API-based CVE Fetchers' and 'BaseGitFetcher Class' (step 10…)" | "see `cve-fetcher-infrastructure.md` (Session Lifecycle) and `git-fetcher-infrastructure.md` (BaseGitFetcher Class, step 10…)" |
| 84 (BaseFetcher `run()`) | "See 'Git-Based Fetchers — Cursor Persistence' for the full mechanism" | "See `git-fetcher-infrastructure.md` (Cursor Persistence)" |
| 179-180 (Abstract Interface) | "CVE fetchers inherit from `BaseCVEFetcher` (see 'BaseCVEFetcher Class' below)" | "CVE fetchers inherit from `BaseCVEFetcher` (see `cve-fetcher-infrastructure.md`)" |
| 3085 (FetcherRun data model) | "See 'Git-Based Fetchers' for the git-specific usage pattern" | "See `git-fetcher-infrastructure.md` (Cursor Persistence)" |

**Verification command** (add to the main verification checklist):

```bash
# Grep core for moved section titles — each hit must be a cross-file
# reference, not a bare "see X below" pointer
grep -n 'BaseGitFetcher Class\|Session Lifecycle for API-based\|Git-Based Fetchers\|BaseCVEFetcher Class\|CVE Fetcher Conventions\|Batch Error Handling\|Metric Definitions' \
  docs/features/platform/fetcher-infrastructure.md
```

#### 4d. Update core `## Purpose`

The current Purpose section (source 3-14) claims to cover everything.
After the split, update it to reflect the narrowed scope and reference
the sibling specs.

#### 4e. Cross-reference migration

See the "Cross-Reference Migration Map" section below for the
complete file-by-file update plan.

#### 4f. AGENTS.md updates

Guardrail 14 currently references
`docs/features/platform/fetcher-infrastructure.md` for all three base
classes. After split:

| Reference | Current target | New target |
|---|---|---|
| `BaseFetcher` | `fetcher-infrastructure.md` | unchanged |
| `BaseCVEFetcher` | `fetcher-infrastructure.md` | `cve-fetcher-infrastructure.md` |
| `BaseGitFetcher` | `fetcher-infrastructure.md` | `git-fetcher-infrastructure.md` |
| "See ... for the full specification" | `fetcher-infrastructure.md` | add all 3 new specs |

#### 4g. `docs/conventions.md` updates

The Naming section references all three classes. Update:

| Reference | New target |
|---|---|
| `BaseCVEFetcher` (`base_cve_fetcher.py`) | add: "See `cve-fetcher-infrastructure.md`" |
| `BaseGitFetcher` (`base_git_fetcher.py`) | add: "See `git-fetcher-infrastructure.md`" |

#### 4h. `docs/features/platform/README.md` update

Replace the current Specs list with:

```
fetcher-infrastructure.md       BaseFetcher base class, registry, execution tracking
cve-fetcher-infrastructure.md   BaseCVEFetcher class, CVE-specific contracts and conventions
git-fetcher-infrastructure.md   BaseGitFetcher class, git_operations.py, clone/delta infrastructure
networking.md                   Shared HTTP client, TLS trust store (cross-cutting)
fetcher-operations.md           Monitoring, API, and CLI diagnostics for fetchers
audit-trail-infrastructure.md   BaseAuditLog base class, AuditEventMixin
system-settings.md              System settings (default CVSS version, etc.)
```

Update the Relationships section to reflect the new structure.

#### 4i. `docs/configuration.md` pointer updates

| Env var | Current source link | New source link |
|---|---|---|
| `SUSE_CA_CERT_PATH` | `fetcher-infrastructure.md` | `networking.md` |
| `GIT_CLONE_BASE_DIR` | `fetcher-infrastructure.md` | `git-fetcher-infrastructure.md` |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | (generic) | `networking.md` |

#### 4j. `docs/data-sources.md` update

The Fetcher Registry table header references
`fetcher-infrastructure.md` for infrastructure details. Add references
to the new specs:

> See `docs/features/platform/fetcher-infrastructure.md` for base
> class infrastructure,
> `docs/features/platform/cve-fetcher-infrastructure.md` for CVE
> fetcher conventions, and
> `docs/features/platform/git-fetcher-infrastructure.md` for git-based
> fetcher infrastructure.

#### 4k. `docs/architecture.md` update

References to `BaseCVEFetcher` and `BaseGitFetcher` should include
pointers to their respective new specs.

#### 4l. Reviews changes

**`.tracking.json`** — add 3 new entries:

```json
"networking": {
  "enabled": true,
  "abbr": "NET",
  "cache": null
},
"cve-fetcher-infrastructure": {
  "enabled": true,
  "abbr": "CFI",
  "cache": null
},
"git-fetcher-infrastructure": {
  "enabled": true,
  "abbr": "GFI",
  "cache": null
}
```

**`reviews/README.md`** — add 3 rows to the Summary Table (not yet
reviewed):

```
| networking | — | — | — | — | — | —/— | — | |
| cve-fetcher-infrastructure | — | — | — | — | — | —/— | — | |
| git-fetcher-infrastructure | — | — | — | — | — | —/— | — | |
```

Mark `fetcher-infrastructure` row with `⚠️` stale indicator if not
already present (the row already has `⚠️` as of 2026-06-25 — verify
before adding a duplicate).

Update the "Total" row counts.

**`reviews/fetcher-infrastructure.md`** — NO CHANGES. All 20 `FEI-*`
findings (all RESOLVED) relate to sections that remain in the core
spec (status precedence, custom settings, concurrency, error_message
sanitization). Historical findings are preserved as-is.

**No new review finding files** — those are created by actual reviews
after the split is applied.

#### 4m. Re-review invocations

After all phases are complete, invoke the following reviewers on the
new specs:

| Spec | Reviewers | Reason |
|---|---|---|
| `networking.md` | `@docs-reviewer`, `@spec-gap-analyzer` | New spec; verify completeness |
| `cve-fetcher-infrastructure.md` | `@docs-reviewer`, `@spec-coherence-reviewer`, `@spec-gap-analyzer` | New spec; verify coherence with CVE fetcher specs |
| `git-fetcher-infrastructure.md` | `@docs-reviewer`, `@spec-gap-analyzer` | New spec |
| `fetcher-infrastructure.md` | `@docs-reviewer`, `@spec-coherence-reviewer` | Substantially modified; verify bridge consolidation is coherent |

#### 4n. Draft cleanup

Delete `docs/drafts/fetcher-infrastructure-split.md`. The plan has been
fully executed; the file serves no further purpose and its presence would
suggest the split is still pending.

#### Definition of done (Phase 4)

- [ ] Bridge stubs consolidated into single "Related specifications"
      section
- [ ] All residual core→child internal references fixed (4c)
- [ ] Core `## Purpose` updated to reflect narrowed scope
- [ ] All files in the Cross-Reference Migration Map updated
- [ ] Guardrail/Subagent sections trimmed in core spec
- [ ] AGENTS.md updated with new spec references
- [ ] conventions.md updated
- [ ] platform/README.md updated
- [ ] configuration.md pointers updated
- [ ] data-sources.md header updated
- [ ] architecture.md updated
- [ ] .tracking.json has 3 new entries
- [ ] reviews/README.md has 3 new rows + stale marker
- [ ] Verification checklist passes (see below)
- [ ] `docs/drafts/fetcher-infrastructure-split.md` deleted

---

## Cross-Reference Migration Map

Files referencing `fetcher-infrastructure.md` and their post-split
targets. The "Action" column indicates what changes.

### Deep-anchor references (must change file + anchor target)

| File | Current reference | New target | Anchor |
|---|---|---|---|
| `tickets/cve-sync-mitre.md` (line 283) | `fetcher-infrastructure.md#batch-error-handling` | `cve-fetcher-infrastructure.md#batch-error-handling` | same |
| `tickets/cve-sync-kernel.md` (line 115) | `fetcher-infrastructure.md#batch-error-handling` | `cve-fetcher-infrastructure.md#batch-error-handling` | same |
| `tickets/cve-sync-kernel.md` (line 258) | `fetcher-infrastructure.md#batch-error-handling` | `cve-fetcher-infrastructure.md#batch-error-handling` | same |
| `tickets/cve-sync-ghsa.md` (line 169) | `fetcher-infrastructure.md#batch-error-handling` | `cve-fetcher-infrastructure.md#batch-error-handling` | same |
| `tickets/cve-sync-ghsa.md` (line 509) | `fetcher-infrastructure.md#batch-error-handling` | `cve-fetcher-infrastructure.md#batch-error-handling` | same |
| `tickets/cve-sync-osv.md` (line 406) | `fetcher-infrastructure.md#metric-definitions` | `cve-fetcher-infrastructure.md#metric-definitions` | same |
| `tickets/cve-sync-nvd.md` (line 609) | `fetcher-infrastructure.md#fetch_single-signaling-convention` | `cve-fetcher-infrastructure.md#fetch_single-signaling-convention` | same |

**Note**: `cve-sync-epss.md` was previously listed but has no anchored
references (only generic). `cve-sync-redhat.md` similarly has no
anchored references.

**Verification command** (run after Phase 4):

```bash
grep -rn 'fetcher-infrastructure\.md#' docs/ --include='*.md'
```

Expected output: zero matches (all anchored references have been
migrated to the correct new file).

### Generic references (update file target only)

The following files contain generic references to
`fetcher-infrastructure.md` (no section anchor). After the split, each
reference should point to the most specific document. References that
are genuinely about the generic BaseFetcher contract stay pointing to
`fetcher-infrastructure.md`.

**Action legend:**

- **keep** — reference is about generic BaseFetcher → no change
- **repoint** — reference is about a specific layer → change filename
- **split** — file references multiple layers → some refs stay, others
  repoint
- **add** — add references to new specs alongside existing ones

| File | Ref count | Action | Details |
|---|---|---|---|
| `tickets/cve-sync-mitre.md` | 8 | split | BaseGitFetcher → `git-fetcher-infrastructure.md`; BaseCVEFetcher → `cve-fetcher-infrastructure.md`; generic refs keep |
| `tickets/cve-sync-kernel.md` | 8 | split | same as mitre |
| `tickets/cve-service.md` | 8 | split | fetch_single/cve_source_type → `cve-fetcher-infrastructure.md`; generic refs keep |
| `tickets/cve-sync-osv.md` | 6 | split | CVE conventions → `cve-fetcher-infrastructure.md`; generic refs keep |
| `tickets/cve-sync-ghsa.md` | 6 | split | same as osv |
| `platform/fetcher-operations.md` | 6 | keep | all references are about generic BaseFetcher concepts (concurrency, registry, FetcherRun) |
| `tickets/cve-sync-redhat.md` | 5 | split | CVE conventions → `cve-fetcher-infrastructure.md`; generic refs keep |
| `tickets/cve-sync-nvd.md` | 5 | split | CVE conventions + session lifecycle → `cve-fetcher-infrastructure.md`; line 220 "Shared HTTP Client — Transport-Level" → `networking.md`; generic refs keep |
| `data-model.md` | 5 | keep | references FetcherRun/FetcherConfig data model (stays in core) |
| `tickets/cve-tracking.md` | 4 | split | CVE fetcher specs table → add `cve-fetcher-infrastructure.md`; generic refs keep |
| `tickets/cve-sync-epss.md` | 4 | split | CVE conventions → `cve-fetcher-infrastructure.md`; generic refs keep |
| `packages/ibs-submission-tracking.md` | 4 | keep | references generic BaseFetcher |
| `tickets/ticket-references.md` | 3 | split | source_reference_url_pattern → `cve-fetcher-infrastructure.md`; generic refs keep |
| `tickets/cvss-scoring.md` | 3 | keep | references generic fetcher concepts |
| `platform/README.md` | 3 | add | see Phase 4h |
| `conventions.md` | 3 | split | see Phase 4g |
| `configuration.md` | 3 | repoint | see Phase 4i |
| `system-map.md` | 2 | add | add new specs to system map |
| `tickets/ticket-service.md` | 2 | keep | generic refs |
| `tickets/cve-sync-kev.md` | 2 | split | CVE conventions → `cve-fetcher-infrastructure.md` |
| `platform/audit-trail-infrastructure.md` | 2 | keep | references FetcherAuditLog |
| `integrations/ibs-rabbitmq-integration.md` | 2 | repoint | TLS → `networking.md` |
| `integrations/ibs-integration.md` | 2 | repoint | HTTP client/TLS → `networking.md` |
| `architecture.md` | 2 | add | see Phase 4k |
| `identity/ad-integration.md` | 1 | keep + add | Existing ref is to Custom Settings (stays in core) → keep. Add separate `networking.md` ref for TLS |
| `deployment.md` | 1 | repoint | "Git-Based Fetchers" → `git-fetcher-infrastructure.md` |
| `data-sources.md` | 1 | add | see Phase 4j |
| `drafts/open-points.md` | 1 | keep | informal reference |
| `features/README.md` | 1 | keep | |
| `tickets/README.md` | 1 | keep | |
| `tickets/ticket-mutations.md` | 1 | keep | |
| `packages/product-lifecycle-transitions.md` | 1 | keep | generic ref |
| `packages/product-catalog.md` | 1 | repoint | "TLS Trust Store" → `networking.md` |
| `packages/package-bugowner.md` | 1 | keep | generic ref |
| `packages/ibs-track-release-detection.md` | 1 | keep | generic ref |
| `packages/ibs-product-release-detection.md` | 1 | keep | generic ref |
| `reviews/fetcher-infrastructure.md` | 4 | keep | historical findings — no changes |
| `reviews/README.md` | 1 | keep | link to review file |
| Other review files | various | keep | incidental mentions |

---

## Risks

| Risk | Mitigation |
|---|---|
| Broken cross-references after partial execution | Bridge sections ensure generic refs remain functional during inter-phase commits. Phase 4 makes refs precise. Verification commands catch stragglers |
| Content drift (same information in bridge + new file) | Bridges are transitional (consolidated in 4b); new files own the content |
| Stale reviews on core spec | Addressed by re-review invocations in Phase 4m |
| New specs never reviewed | tracking.json entries with `cache: null` + enabled ensure they appear in review pipeline |
| Core→child internal references missed by grep | Phase 4c explicitly enumerates known instances + provides title-grep verification |
| catch_up sub-section split loses context | The override-point contract + boundary conditions (retained in core) provide complete generic guidance; CVE-specific delegation is naturally separate |

**Commit discipline**: each phase is a single commit directly on
master. If a phase produces an incorrect result, `git revert` the
commit before starting the next phase.

## Verification Checklist

Run after Phase 4 is complete:

```bash
# 1. No anchored references to old file for migrated sections
grep -rn 'fetcher-infrastructure\.md#batch-error-handling' docs/ --include='*.md'
grep -rn 'fetcher-infrastructure\.md#metric-definitions' docs/ --include='*.md'
grep -rn 'fetcher-infrastructure\.md#fetch_single' docs/ --include='*.md'
# Expected: zero matches for all three

# 2. New files exist
ls -la docs/features/platform/networking.md
ls -la docs/features/platform/cve-fetcher-infrastructure.md
ls -la docs/features/platform/git-fetcher-infrastructure.md

# 3. No dangling references to new files that don't exist
# (only relevant if phases are applied incrementally)
for f in networking.md cve-fetcher-infrastructure.md git-fetcher-infrastructure.md; do
  echo "=== References to $f ==="
  grep -rn "$f" docs/ --include='*.md' | head -5
done

# 4. Core spec: no broken internal pointers to moved sections
grep -n 'BaseGitFetcher Class\|Session Lifecycle for API-based\|Git-Based Fetchers\|BaseCVEFetcher Class\|CVE Fetcher Conventions\|Batch Error Handling\|Metric Definitions' \
  docs/features/platform/fetcher-infrastructure.md
# Each hit must be inside a cross-file reference (pointing to the new
# file), NOT a bare "see X below" or "see the Y section" pointer

# 5. Bridge stubs removed (replaced by consolidated section)
grep -c 'See.*cve-fetcher-infrastructure\|See.*git-fetcher-infrastructure\|See.*networking' \
  docs/features/platform/fetcher-infrastructure.md
# Expected: concentrated in the "Related specifications" section,
# plus the kept BaseFetcher HTTP Integration and catch_up pointer

# 6. tracking.json has new entries
python3 -c "
import json
with open('docs/reviews/.tracking.json') as f:
    data = json.load(f)
for name in ['networking', 'cve-fetcher-infrastructure', 'git-fetcher-infrastructure']:
    assert name in data['specs'], f'Missing: {name}'
    print(f'{name}: abbr={data[\"specs\"][name][\"abbr\"]}, enabled={data[\"specs\"][name][\"enabled\"]}')
"
# Expected: NET/CFI/GFI, all enabled=true

# 7. Reviews README has new rows
grep -c 'networking\|cve-fetcher-infrastructure\|git-fetcher-infrastructure' docs/reviews/README.md
# Expected: >= 3
```

## Review History

This plan was reviewed pre-execution by `@docs-placement-reviewer` and
`@design-reviewer` (2026-06-25). Verdicts: Minor issues / Minor
concerns. Findings incorporated:

- W1: Phase reorder (networking → CVE → Git) — adopted
- W2: Sub-section-level catch_up cut — adopted
- W3: Core→child internal references step added — adopted
- W4: __init_subclass__ fragmentation note — adopted
- W5: Bridge consolidation in Phase 4 — adopted
- W6: Section ordering declared normative — adopted
- Migration map: ad-integration.md corrected, deep-anchor inventory
  noted for verification
- Minor: Purpose update, git_operations note, networking scope boundary

## Open Questions

None at this time.
