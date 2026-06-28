# Catch-Up Participation Refactor

**Status**: DRAFT — pending review before application  
**Scope**: Eliminate `participates_in_catch_up` flag; adopt identity-based
catch-up predicate  
**Out of scope**: Package topology re-resolution on ticket reopen (separate
future spec)  
**Resolves**: CFI-GAP-01 (by construction — the coupling no longer exists)  
**Affected specs**: `cve-fetcher-infrastructure.md`,
`fetcher-infrastructure.md`, `git-fetcher-infrastructure.md`,
`cve-sync-kev.md`

---

## 1. Problem Statement

### History

`BaseCVEFetcher` carries two boolean class attributes controlling
catch-up participation:

| Flag | Introduced | Commit | Motivation |
|------|-----------|--------|-------------|
| `participates_in_catch_up` | 2026-06-19 | `20c62f8` → `8fea018` | Opt-out for global-scope CVE fetchers from per-ticket catch-up |
| `supports_fetch_single` | 2026-06-20 | `2d417a2` → `c9d6f13` | Opt-out for catalog-based CVE fetchers with no per-CVE API |

Both flags were introduced on consecutive days to solve problems on the
same fetcher (CISA KEV). No divergent use case has ever existed.

### Redundancy

The default `catch_up()` on `BaseCVEFetcher` delegates to
`fetch_single()`:

```python
async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
    ticket = await session.get(Ticket, UUID(ticket_id))
    if ticket and ticket.cve_id:
        result = await self.fetch_single(str(ticket.cve_id), session)
        await self.commit_and_dispatch(session, result)
```

This creates a mechanical coupling: a CVE fetcher using the default
`catch_up()` **can** participate in catch-up **if and only if**
`supports_fetch_single = True`. The two flags must always agree for
default-catch_up CVE fetchers:

| `supports_fetch_single` | `participates_in_catch_up` | Status |
|---|---|---|
| `True` | `True` | 7 fetchers (all default-catch_up CVE fetchers) |
| `False` | `False` | 1 fetcher (KEV) |
| `True` | `False` | 0 — never used; speculative |
| `False` | `True` | 0 — mechanically invalid (RuntimeError at catch-up time) |

`participates_in_catch_up` encodes a fact already implied by
`supports_fetch_single`. It is a redundant flag.

### CFI-GAP-01 — The coupling is unenforced

The `__init_subclass__` validation on `BaseCVEFetcher` validates only
`cve_source_type` (Enum membership, uniqueness). It does NOT validate
that `supports_fetch_single = False` implies
`participates_in_catch_up = False`. A fetcher that sets one without the
other passes all import-time checks and crashes only at catch-up
runtime — a rare, hard-to-diagnose moment.

The original fix proposed by CFI-GAP-01 is to enforce the coupling via
`__init_subclass__` validation. This draft proposes a stronger solution:
eliminate the redundant flag entirely, dissolving the coupling by
construction.

### Latent fragility in non-CVE catch-up detection

`get_catch_up_fetchers()` uses `'catch_up' in cls.__dict__` to detect
non-CVE fetchers with custom catch-up. This introspection check is
fragile: if a non-CVE fetcher inherits `catch_up()` from an
intermediate base class (e.g., a future `BaseIBSReleaseFetcher` shared
by track and product detection), the subclass's `__dict__` will not
contain the method, causing it to be incorrectly excluded from the
catch-up roster. This is the same inheritance problem that forced the
flag onto `BaseCVEFetcher`.

---

## 2. Decision

**Eliminate `participates_in_catch_up`.** Derive CVE catch-up
participation from `supports_fetch_single`. Replace the `__dict__`
introspection for non-CVE fetchers with an identity-based predicate
robust to inheritance depth.

---

## 3. Alternatives Considered

| Option | Description | CFI-GAP-01 | Non-CVE fragility | Complexity | Flexibility |
|--------|-------------|------------|-------------------|------------|-------------|
| 1. Enforce coupling | Add `__init_subclass__` check for flag agreement | Mitigated (guard) | Remains | Low | Keeps speculative (True,False) case |
| 2. Eliminate flag, minimal predicate | Remove flag; `get_catch_up_fetchers` checks `supports_fetch_single` for CVE, keeps `__dict__` for non-CVE | **Dissolved** | Remains | Minimal | Loses (True,False) case |
| 3. Move flag to BaseFetcher | Unify on a single flag for all fetchers | Remains (guard) | Resolved | Medium + boilerplate | Keeps (True,False) case |
| **6. Eliminate flag, robust predicate** | Remove flag; identity-based 3-way predicate | **Dissolved** | **Resolved** | Medium (centralized) | Loses (True,False) case |

**Chosen: Option 6.** Rationale:

- Dissolves CFI-GAP-01 by construction (no coupling to enforce)
- Resolves the latent non-CVE fragility (no `__dict__` introspection)
- Reduces conceptual surface (one flag instead of two)
- The lost (True,False) case is speculative, has never been used, and is
  cheaply re-introducible as an additive change if ever needed
- Cost is contained: the predicate complexity is centralized in one
  function

---

## 4. New Predicate Specification

### `get_catch_up_fetchers()` — identity-based 3-way predicate

```python
def get_catch_up_fetchers() -> dict[str, type[BaseFetcher]]:
    """Return fetchers with catch-up capability, keyed by fetcher name.

    A fetcher has catch-up capability if:
    1. Its resolved catch_up method is BaseCVEFetcher's default AND
       it has supports_fetch_single = True (default delegates to
       fetch_single — only works if fetch_single is implemented), OR
    2. Its resolved catch_up method is a real override (neither
       BaseFetcher's abstract placeholder nor BaseCVEFetcher's default)
       — this covers both non-CVE fetchers with custom catch_up and
       CVE fetchers with custom catch_up implementations.

    Fetchers whose resolved catch_up is BaseFetcher.catch_up (the
    abstract placeholder that raises NotImplementedError) are excluded.
    """
    from app.services.base_cve_fetcher import BaseCVEFetcher

    fetchers: dict[str, type[BaseFetcher]] = {}
    for name, cls in FETCHER_REGISTRY.items():
        resolved = cls.catch_up
        if resolved is BaseFetcher.catch_up:
            # Abstract placeholder — no catch-up capability
            continue
        elif resolved is BaseCVEFetcher.catch_up:
            # Default CVE catch_up — delegates to fetch_single
            if cls.supports_fetch_single:
                fetchers[name] = cls
        else:
            # Real override (non-CVE custom OR CVE custom) — always included
            fetchers[name] = cls
    return fetchers
```

### Trace table — verification across all hierarchy levels

| Concrete class | Hierarchy depth | `catch_up` resolved to | Branch | `supports_fetch_single` | In roster? |
|---|---|---|---|---|---|
| `SyncNvdCves` | 3 (BF→BCVEF→concrete) | `BaseCVEFetcher.catch_up` | branch 2 | `True` | Yes |
| `SyncMitreCves` | 4 (BF→BCVEF→BGF→concrete) | `BaseCVEFetcher.catch_up` (via BGF) | branch 2 | `True` | Yes |
| `SyncKernelCves` | 4 (BF→BCVEF→BGF→concrete) | `BaseCVEFetcher.catch_up` (via BGF) | branch 2 | `True` | Yes |
| `SyncCisaKev` | 3 (BF→BCVEF→concrete) | `BaseCVEFetcher.catch_up` | branch 2 | `False` | **No** |
| `SyncRedhatCves` | 3 | `BaseCVEFetcher.catch_up` | branch 2 | `True` | Yes |
| `SyncEpssScores` | 3 | `BaseCVEFetcher.catch_up` | branch 2 | `True` | Yes |
| `SyncGhsaAdvisories` | 3 | `BaseCVEFetcher.catch_up` | branch 2 | `True` | Yes |
| `SyncOsvAdvisories` | 3 | `BaseCVEFetcher.catch_up` | branch 2 | `True` | Yes |
| `DetectIbsTrackReleases` | 2 (BF→concrete) | own override | branch 3 | N/A | Yes |
| `DetectIbsProductReleases` | 2 (BF→concrete) | own override | branch 3 | N/A | Yes |
| `SyncIbsRequests` | 2 (BF→concrete) | own override | branch 3 | N/A | Yes |
| `EvaluateLifecycleTransitions` | 2 (BF→concrete) | own override | branch 3 | N/A | Yes |
| `SyncIbsBugowners` | 2 (BF→concrete) | own override | branch 3 | N/A | Yes |
| `SyncSmeltProducts` | 2 (BF→concrete) | `BaseFetcher.catch_up` | branch 1 | N/A | **No** |
| `SyncLdapDirectory` | 2 (BF→concrete) | `BaseFetcher.catch_up` | branch 1 | N/A | **No** |
| *future* `BaseIBSReleaseFetcher` → `DetectIbsTrackReleases` | 3 (BF→intermediate→concrete) | override on intermediate | branch 3 | N/A | Yes |

### Intermediate CVE base class assumption

**Design rule**: intermediate CVE base classes (e.g., `BaseGitFetcher`,
or any future intermediate between `BaseCVEFetcher` and a concrete
fetcher) MUST NOT override `catch_up()`. They inherit the default
delegation from `BaseCVEFetcher`.

**Rationale**: if an intermediate base overrides `catch_up()` with its
own delegation to `fetch_single()`, the identity check
`resolved is BaseCVEFetcher.catch_up` would fail, causing the fetcher
to land in branch 3 (unconditionally included) regardless of
`supports_fetch_single`. This would be incorrect for a subclass that
sets `supports_fetch_single = False`.

**Extension path**: if this constraint ever needs to be relaxed (an
intermediate CVE base needs a custom `catch_up()`), the predicate can
be extended by marking default-delegating catch_up methods with a
sentinel attribute:

```python
# Mark default-delegating catch_up methods
BaseCVEFetcher.catch_up._is_default_cve_catch_up = True

# In get_catch_up_fetchers:
elif getattr(resolved, '_is_default_cve_catch_up', False):
    if cls.supports_fetch_single:
        fetchers[name] = cls
```

This is deferred until needed (YAGNI).

### Why `supports_fetch_single` remains a flag (not identity-detected like `catch_up`)

Natural question: if `supports_fetch_single` merely records whether a
fetcher implements `fetch_single()`, why not detect it by identity
(`cls.fetch_single is not BaseCVEFetcher.fetch_single`) and eliminate
the flag — the same approach this refactor uses for `catch_up`?

The asymmetry is deliberate, rooted in the opposite nature of the two
defaults:

- **`BaseCVEFetcher.catch_up`** is a **usable** default (delegates to
  `fetch_single()`). Every default-catch_up CVE fetcher inherits it
  correctly without override → identity detection is **forced** (cannot
  infer participation from "did you override?" because nobody does).
- **`BaseCVEFetcher.fetch_single`** is a **sentinel** default (raises
  `RuntimeError`). Representing the capability as an opt-out flag
  (default `True`) is a choice that buys two properties identity
  detection would lose:

  1. **Explicit intent**: `supports_fetch_single = False` is a
     deliberate declaration (KEV), distinguishable from an accidental
     omission (bug).
  2. **Loud failure**: with default `True`, a fetcher that forgets the
     override gets included → on-demand/catch-up call `fetch_single()`
     → `RuntimeError` caught immediately in tests. With identity
     detection, the same mistake silently excludes the fetcher from
     both rosters — the bug is masked.

This reflects opt-out vs opt-in semantics: `fetch_single` support is
the norm for CVE fetchers (7 of 8) → opt-out with default-`True` flag
catches omissions loudly. Custom `catch_up()` on non-CVE fetchers is
the exception → opt-in by presence/identity, where silent exclusion is
correct default behavior.

Pragmatically, the flag also keeps the catch-up predicate readable:
it tests `cls.supports_fetch_single` (a clean boolean) instead of
`cls.fetch_single is not BaseCVEFetcher.fetch_single` (obscure,
coupled to sentinel name).

### Import-time validation adjustment

The existing `__init_subclass__` validation in `BaseCVEFetcher` does
NOT need a new check for `participates_in_catch_up` (since the flag no
longer exists). No new validation rule is introduced — the catch-up
roster is derived entirely from the predicate at runtime.

The existing `BaseFetcher.__init_subclass__` rule that validates the
`catch_up()` signature (riga 829 of `fetcher-infrastructure.md`) still
applies unchanged: "If a fetcher defines `catch_up()` in its
`__dict__`, it must accept `(self, ticket_id: str, session:
AsyncSession) -> None`."

---

## 5. Detailed Application Plan

### File 1: `docs/features/platform/cve-fetcher-infrastructure.md`

| Location | Current | Action |
|----------|---------|--------|
| Line 55 (Class Attributes table) | Row for `participates_in_catch_up` | **Remove row** |
| Line 56 (Class Attributes, `supports_fetch_single` description) | "...are never dispatched by `fetch_single_cve`, and do not need to override `fetch_single()`" | **Extend**: append that `supports_fetch_single = False` also excludes the fetcher from the catch-up roster (`get_catch_up_fetchers()`), because the default `catch_up()` delegates to `fetch_single()`. Makes the dual role of the attribute explicit |
| Line 65 (Concrete Methods table, `catch_up` description) | "...fetchers with `False` also set `participates_in_catch_up = False` (so `catch_up()` is never invoked)" | **Reword**: "...fetchers with `supports_fetch_single = False` are excluded from `get_catch_up_fetchers()` results (so `catch_up()` is never invoked for them)" |
| Line 166 (pseudocode `__init_subclass__`) | `participates_in_catch_up: bool = True` | **Remove line** |
| Line 207 (Non-Modification Statement, item 4) | "The `participates_in_catch_up` opt-out for catch-up participation" | **Remove item** (renumber subsequent items) |
| Line 579 (Default catch_up section) | "(they also set `participates_in_catch_up = False`)" | **Reword**: "(they are excluded from `get_catch_up_fetchers()` because `supports_fetch_single = False`)" |

### File 2: `docs/features/platform/fetcher-infrastructure.md`

| Location | Current | Action |
|----------|---------|--------|
| Lines 403-437 (`get_catch_up_fetchers()` section) | Two-branch predicate with `participates_in_catch_up` + `'catch_up' in cls.__dict__` | **Replace entirely** with the new 3-way identity-based predicate (Section 4 of this draft). Update docstring, code block, and explanatory text. Lines 439-444 (Caching semantics + `_clear_catch_up_cache()`) are preserved unchanged below the new section |
| Line 615 (excluded fetchers table, `sync_cisa_kev` row) | "Syncs entire KEV catalog (sets `participates_in_catch_up = False`)" | **Reword**: "Syncs entire KEV catalog (`supports_fetch_single = False` — excluded from catch-up by predicate)" |
| Lines 617-622 (KEV exclusion explanatory note) | Full paragraph: "Note: `sync_cisa_kev` inherits from `BaseCVEFetcher` but opts out of catch-up via `participates_in_catch_up = False` because its `execute()` syncs the entire catalog on every run — there is no gap to recover after ticket reactivation. It also sets `supports_fetch_single = False` because CISA KEV is a monolithic catalog with no per-CVE API — the `fetch_single_cve` task is never dispatched for this fetcher." | **Replace entire paragraph** with: "Note: `sync_cisa_kev` inherits from `BaseCVEFetcher` but sets `supports_fetch_single = False` because CISA KEV is a monolithic catalog with no per-CVE API. This single attribute excludes it from both `get_fetch_single_fetchers()` (never dispatched by `fetch_single_cve`) and `get_catch_up_fetchers()` (the predicate's branch 2 checks `supports_fetch_single` before including default-catch_up CVE fetchers). Its `execute()` syncs the entire catalog on every run, so there is no gap to recover after ticket reactivation." |
| Line 829 (import-time validation rule) | "If a fetcher defines `catch_up()` in its `__dict__`, it must accept..." | **Keep rule semantics** (the validation still applies to any fetcher that defines `catch_up()` in its `__dict__`) — no change needed |
| After the new predicate section | (does not exist) | **Add**: "Intermediate CVE base class assumption" paragraph (Section 4 of this draft). Insert between the new predicate explanatory text and the existing "Caching semantics" paragraph (current line 439) |

### File 3: `docs/features/tickets/cve-sync-kev.md`

| Location | Current | Action |
|----------|---------|--------|
| Line 37 (code example) | `participates_in_catch_up = False` | **Remove line** |
| Line 64 (attributes table) | Row: `participates_in_catch_up` / `False` | **Remove row** |
| Surrounding text (if any explanation references the flag) | "...sets `participates_in_catch_up = False`..." | **Reword** to reference `supports_fetch_single = False` as the sole opt-out mechanism |

### File 4: `docs/features/platform/git-fetcher-infrastructure.md`

| Location | Current | Action |
|----------|---------|--------|
| Lines 962-968 (Registry Detection Predicate Update) | "The `get_fetch_single_fetchers()` and `get_catch_up_fetchers()` registry accessors use `_CVE_SOURCE_TYPE_MAP` and `BaseCVEFetcher` subclass detection respectively" | **Reword**: replace "BaseCVEFetcher subclass detection" with "identity-based method resolution (3-way predicate)" to reflect the new mechanism. The rest of the paragraph (BaseGitFetcher inherits from BaseCVEFetcher → automatic inclusion) remains factually correct |

### Verification files (confirm no changes needed)

| File | Reference | Expected action |
|------|-----------|-----------------|
| `ticket-mutations.md:213` | "...via `get_catch_up_fetchers()`" | No change — references the function name, not the predicate internals |
| `cvss-scoring.md:790` | "...via `get_catch_up_fetchers()` — not limited to CVSS fetchers" | No change — factual statement remains true |

### Post-application: review file update

After all spec changes are applied:

- Mark `CFI-GAP-01` as RESOLVED in `docs/reviews/cve-fetcher-infrastructure.md`:
  `**Status**: RESOLVED — Flag eliminated; coupling dissolved by construction (YYYY-MM-DD)`
- Update `.tracking.json` cache for `cve-fetcher-infrastructure`:
  `"GAP" → "M"`: current value `3` → target `2`
- Update `docs/reviews/README.md`, row `cve-fetcher-infrastructure`:
  GAP total `8` → `7`, breakdown `3:🟠 5:🟡` → `2:🟠 5:🟡`,
  Open column `11/11` → `10/11`

### Post-application: verification

Run `@spec-coherence-reviewer` on each modified spec to verify that
the applied changes do not introduce contradictions with consumer specs:

| Reviewer | Target spec | What to verify |
|----------|-------------|----------------|
| `@spec-coherence-reviewer` | `cve-fetcher-infrastructure.md` | Internal references to/from `fetcher-infrastructure.md` and `cve-sync-kev.md` are consistent |
| `@spec-coherence-reviewer` | `fetcher-infrastructure.md` | New predicate section does not contradict consumer specs (`ticket-mutations.md`, `cvss-scoring.md`) |
| `@spec-coherence-reviewer` | `git-fetcher-infrastructure.md` | Updated mechanism description is consistent with BaseGitFetcher sections |

If any reviewer identifies issues rated "Needs revision", fix them
before proceeding to cleanup.

### Post-application: cleanup

Once all modifications are applied, verified, and review files updated:

- **Delete** `docs/drafts/catch-up-participation-refactor.md` (this
  file). It is a working document, not a permanent spec — the
  authoritative definitions now live in the modified spec files

---

## 6. Edge Cases and Future Considerations

### Loss of the (True, False) case

A CVE fetcher that supports `fetch_single` but wants to opt out of
automatic catch-up is no longer expressible with a single flag. If this
ever becomes necessary:

1. Re-introduce `participates_in_catch_up` (additive, non-breaking)
2. Add a branch in the predicate:
   `elif resolved is BaseCVEFetcher.catch_up: if cls.supports_fetch_single and cls.participates_in_catch_up:`
3. Default remains `True` — existing fetchers unaffected

This is deferred until a concrete use case materializes. The catch-up
operation is a single `fetch_single()` call (idempotent, cheap) — the
motivation to opt out while retaining on-demand capability is weak.

### CVE fetcher with custom `catch_up()` override

A future CVE fetcher that overrides `catch_up()` with custom logic
(not delegating to `fetch_single()`) would land in branch 3
(unconditionally included). This is correct: if it has a custom
implementation, it should participate regardless of
`supports_fetch_single`. The custom override is responsible for its
own correctness.

### Package topology re-resolution on reopen

This draft explicitly does NOT address the gap identified during
analysis: no catch-up fetcher currently re-queries SMELT to discover
new tracks/products for existing packages on a ticket. A future
non-CVE fetcher implementing this feature would land in branch 3 of
the new predicate (real override → always included) — the refactored
design supports it cleanly.

---

## 7. Review Checklist

Before applying the plan, this draft should be reviewed by:

- [ ] `@design-reviewer` — validate predicate design, identity-based
  approach, intermediate base class assumption
- [ ] `@spec-coherence-reviewer` — verify no contradictions introduced
  across `cve-fetcher-infrastructure.md`, `fetcher-infrastructure.md`,
  `cve-sync-kev.md`, and consumer specs
- [ ] `@spec-gap-analyzer` — verify the new predicate spec covers all
  edge cases (hierarchy depths, abstract classes, disabled fetchers)

---

## Cross-references

- `docs/features/platform/cve-fetcher-infrastructure.md` — BaseCVEFetcher
  class, `supports_fetch_single`, default `catch_up()`
- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher,
  `get_catch_up_fetchers()`, Per-Ticket Catch-Up
- `docs/features/platform/git-fetcher-infrastructure.md` — BaseGitFetcher
  (intermediate, does not override `catch_up()`)
- `docs/features/tickets/cve-sync-kev.md` — sole fetcher with explicit
  `False` flags
- `docs/reviews/cve-fetcher-infrastructure.md` — CFI-GAP-01 finding
