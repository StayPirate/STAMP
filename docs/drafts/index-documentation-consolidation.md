# Draft: Consolidate Index Documentation Inline

## Summary

Adopt a single convention for documenting database indexes in
`docs/data-model.md`: every non-PK, non-unique-constraint index is
documented **inline under its owning table** using a standardized
`**Indexes**:` block. The standalone `## Indexes` section is removed.

## Motivation

The current state of `docs/data-model.md` documents indexes
inconsistently:

- Some tables use inline `**Indexes**:` blocks (`ApiKey`, `FetcherRun`).
- One table uses a singular `**Index**:` label (`Session`).
- One index lives in a separate `## Indexes` section
  (`ix_ticket_duplicate_of_id` for the `Ticket` table).

This creates ambiguity about where to document new indexes: inline or
centrally? The inconsistency also makes it harder to find all indexes
for a given table — you must check both the table section and the
central section.

## Decision

**All indexes belong inline under their owning table.** Rationale:

1. Every PostgreSQL index is defined on a single table — there is no
   such thing as a "cross-table index". Therefore every index has
   exactly one natural home: its table.
2. Inline placement preserves **locality**: when reading a table's
   schema, the reader sees columns, constraints, and indexes together
   without jumping to a distant section.
3. A central `## Indexes` section that contains only *some* indexes is
   the worst outcome — it is neither a complete inventory (no lookup
   value) nor local (no context value). It creates a partial view that
   invites confusion about what belongs where.
4. A *complete* central inventory is unjustified at ~5 indexes: the
   maintenance cost (keeping inline + summary in sync) outweighs the
   discoverability benefit. This can be revisited if the schema grows to
   dozens of non-trivial indexes.

## Convention (the rule being established)

> Non-primary-key, non-unique-constraint indexes are documented inline
> under their owning table in an `**Indexes**:` block. There is no
> centralized index section in this document.

This rule will be added to the `## Notes` section of `data-model.md` to
make it discoverable by future authors.

## What counts as an "index" for this convention

| Item | Documented in `**Indexes**:` block? |
|------|--------------------------------------|
| Primary key (PK) | No — implicit from the column table |
| UNIQUE constraint (standalone) | No — documented as `**Unique constraint**:` or in the Constraints column |
| Partial unique index (e.g., `UNIQUE ... WHERE ...`) | **Yes** — it is both a constraint and an index with non-trivial WHERE logic |
| Composite performance index | **Yes** |
| Partial index (non-unique, with WHERE clause) | **Yes** |
| FK column marked `indexed` in the Constraints column | **No change needed** — the `indexed` annotation in the column table is already inline and already readable (see Scope Exclusions below) |

## Scope

### In scope

Only `docs/data-model.md` is modified, with one exception: Step 4 also
updates `docs/features/platform/fetcher-infrastructure.md` to keep its
`FetcherRun` table definition in sync. No other spec, convention, or
code file is affected.

### Scope exclusions

1. **Column-level `indexed` annotations** in
   `FetcherAuditEvent.fetcher_name` — this already appears inline in
   the Constraints column of its table. It is a different documentation
   idiom (column-level flag vs. standalone block), already satisfies
   the locality requirement, and is left unchanged. (The analogous
   annotation on `FetcherRun.fetcher_name` was found redundant by
   `@data-model-reviewer` and is removed in Step 4.)

2. **Indexes mentioned in feature specs** (e.g.,
   `audit-trail-infrastructure.md` indexing criteria,
   `maintainer.md` performance indexes) — these serve a different
   purpose (specifying implementation criteria or performance
   requirements) and are owned by their respective specs. They are not
   relocated or duplicated into `data-model.md`.

3. **~~The FetcherRun potential index redundancy~~** — RESOLVED.
   `@data-model-reviewer` adjudicated that the standalone `indexed`
   annotation is redundant given the composite index. Step 4 removes it.

4. **Index naming convention** — only `ix_ticket_duplicate_of_id` has
   an explicit name. The other indexes are described by their columns.
   Establishing a naming convention for all indexes is a separate future
   decision, not part of this consolidation.

---

## Action Plan

**Execution note**: line numbers below refer to the **current**
(pre-edit) state of `docs/data-model.md`. Each step provides exact text
to match, so the implementer should locate content by text matching, not
by line number. If applying steps sequentially, earlier insertions shift
later line numbers — this is expected and does not affect correctness
when matching on content.

### Step 1 — Add `**Indexes**:` block to the `Ticket` table

**File**: `docs/data-model.md`

**Location**: immediately after the `**CHECK constraints**:` block
(currently ending at line 1097), before the blank line that precedes
`**Deletion policy**:`.

**Insert the following text** between line 1097 and the existing blank
line:

```markdown

**Indexes**:

- `ix_ticket_duplicate_of_id`: partial index on `duplicate_of_id` WHERE
  `duplicate_of_id IS NOT NULL` — supports `mark_as_duplicate` finding
  dependents of the source ticket.
```

**Result**: the Ticket section will have its structural elements in this
order: column table → CHECK constraints → Indexes → Deletion policy →
Status transitions.

### Step 2 — Standardize the `Session` index block format

**File**: `docs/data-model.md`

**Location**: lines 1038–1039 (the current `**Index**:` block under
Session).

**Replace**:

```
**Index**: (user_id, is_active) — for efficient bulk invalidation on
user deactivation.
```

**With**:

```
**Indexes**:

- (user_id, is_active) — for efficient bulk invalidation on user
  deactivation.
```

**Rationale**: aligns with the bulleted-list format already used by
`ApiKey` and `FetcherRun`.

### Step 3 — Standardize the `FetcherRun` index block format

**File**: `docs/data-model.md`

**Location**: lines 1422–1423 (the current `**Indexes**:` block under
FetcherRun).

**Replace**:

```
**Indexes**: `(fetcher_name, started_at)` composite index — supports
timeline queries at any date range efficiently.
```

**With**:

```
**Indexes**:

- (fetcher_name, started_at) — composite index supporting timeline
  queries at any date range.
```

**Rationale**: the label is already correct (`**Indexes**:`), but the
entry is currently on the same line as the label rather than in a
bulleted list. Normalize to the same bulleted-list shape as `ApiKey` and
the newly added `Ticket` block.

### Step 4 — Remove redundant `indexed` from `FetcherRun.fetcher_name`

**Files**: `docs/data-model.md`, `docs/features/platform/fetcher-infrastructure.md`

**Rationale**: the composite index `(fetcher_name, started_at)` covers
single-column lookups on `fetcher_name` via leftmost-prefix. The
standalone `indexed` annotation is therefore redundant and misleading —
an implementer could create a separate single-column index that wastes
resources. This was adjudicated by `@data-model-reviewer` during review.

**Note**: `FetcherAuditEvent.fetcher_name` (which has `indexed` but NO
composite index) is NOT affected — its annotation is correct.

**In `docs/data-model.md`** — replace:

```
| fetcher_name         | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier (matches `BaseFetcher.name`) |
```

With:

```
| fetcher_name         | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL | Fetcher identifier (matches `BaseFetcher.name`) |
```

**In `docs/features/platform/fetcher-infrastructure.md`** — replace:

```
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier (matches `BaseFetcher.name`) |
```

With:

```
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL | Fetcher identifier (matches `BaseFetcher.name`) |
```

### Step 5 — Delete the `## Indexes` section

**File**: `docs/data-model.md`

**Location**: lines 1519–1521 (the `## Indexes` header and its single
bullet entry), plus the blank line following the entry (line 1522).

**Delete the following 4 lines** (1519–1522 inclusive):

```
## Indexes

- `ix_ticket_duplicate_of_id`: index on `Ticket.duplicate_of_id` where `duplicate_of_id IS NOT NULL` — used by `mark_as_duplicate` to find dependents of the source ticket

```

**Result**: after the `SubmissionRequestTrack` unique constraint (line
1517) and its trailing blank line (1518), the next line becomes
`## Notes`.

**Safety check (already verified)**: no document in the repository links
to the `#indexes` anchor.

### Step 6 — Add convention note to `## Notes`

**File**: `docs/data-model.md`

**Location**: at the end of the `## Notes` bulleted list (currently the
last bullet is about `CVECVSSAssessment` at line 1558–1559).

**Append the following bullet**:

```
- Non-primary-key, non-unique-constraint indexes are documented inline
  under their owning table in an `**Indexes**:` block. There is no
  centralized index section. Partial unique indexes (with a `WHERE`
  clause) are documented in the `**Indexes**:` block rather than as
  standalone unique constraints, because their filtering logic is part
  of the index definition
```

**Rationale**: makes the convention discoverable and explicit, prevents
future authors from re-creating a central section.

### Step 7 — Verify consistency

After applying steps 1–6, verify the following (manual or automated):

1. **No residual `## Indexes` header** exists in the file.
2. **Every table that has non-PK, non-unique indexes** has an
   `**Indexes**:` block:
   - `Session`: 1 entry (user_id, is_active)
   - `ApiKey`: 2 entries (user_id, revoked_at) and partial UNIQUE
     (user_id, name)
   - `Ticket`: 1 entry (ix_ticket_duplicate_of_id)
   - `FetcherRun`: 1 entry (fetcher_name, started_at)
3. **All `**Indexes**:` blocks use the same format**: label on its own
   line, followed by a blank line, followed by bulleted entries.
4. **`## Notes` contains the convention bullet**.
5. **No broken cross-references**: re-grep for `data-model.md#indexes`
   across the repo (expect zero matches — already verified, but confirm
   after edit).

### Step 8 — Run reviewers

Invoke the following reviewers on the modified `docs/data-model.md`:

1. **`@data-model-reviewer`** (Guardrail 8) — verifies:
   - Schema documentation remains correct after the edits.
   - No unintended semantic changes were introduced.
2. **`@docs-placement-reviewer`** (Guardrail 21-E) — verifies:
   - The new convention (all indexes inline) is the appropriate
     placement for this information type.
   - No information was misplaced or lost during the consolidation.

**Not invoked** (with rationale):

- `@spec-coherence-reviewer`: no entities, relationships, constraints,
  or business rules were changed — only the placement of existing index
  documentation. No feature spec references the deleted section.
- `@docs-reviewer`: single-file cosmetic reorganization with no
  behavioral change. Triggered only if the reviewers above surface
  issues that require broader documentation impact assessment.

### Step 9 — Delete this draft

Once the reviewers confirm no issues (or issues have been resolved),
delete this file (`docs/drafts/index-documentation-consolidation.md`).
The convention is now embedded in `data-model.md` itself (the `## Notes`
bullet) and does not need a separate document.

---

## Post-change state (reference)

After all steps are applied, the index documentation landscape in
`docs/data-model.md` is:

| Table | `**Indexes**:` block contents |
|-------|-------------------------------|
| Session | (user_id, is_active) |
| ApiKey | (user_id, revoked_at); UNIQUE (user_id, name) WHERE revoked_at IS NULL |
| Ticket | `ix_ticket_duplicate_of_id`: partial on duplicate_of_id WHERE NOT NULL |
| FetcherRun | (fetcher_name, started_at) |
| FetcherAuditEvent | *(none — uses column-level `indexed` annotation, unchanged)* |

The `## Indexes` section no longer exists. The `## Notes` section
documents the convention for future authors.

## Open flag for reviewer — RESOLVED

The `FetcherRun` table had both:
- `fetcher_name` marked `indexed` in the column Constraints cell
- A composite index `(fetcher_name, started_at)` in the `**Indexes**:`
  block

`@data-model-reviewer` adjudicated: the standalone `indexed` annotation
is redundant (leftmost-prefix coverage). **Resolved by Step 4** — the
`indexed` annotation is removed from `FetcherRun.fetcher_name` in both
`docs/data-model.md` and `docs/features/platform/fetcher-infrastructure.md`.
