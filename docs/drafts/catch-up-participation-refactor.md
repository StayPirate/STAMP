# Catch-Up Participation Refactor

**Status**: DRAFT — pending review before application  
**Revision**: 2 (complete rewrite; supersedes Option 6 draft reviewed
2026-06-28 — see git history for prior version)  
**Scope**: Promote `participates_in_catch_up` to `BaseFetcher` with
uniform predicate; auto-derive for CVE fetchers from
`supports_fetch_single`  
**Out of scope**: Package topology re-resolution on ticket reopen
(separate future spec)  
**Resolves**: CFI-GAP-01 (coupling dissolved by auto-derivation)  
**Affected specs**: `cve-fetcher-infrastructure.md`,
`fetcher-infrastructure.md`, `cve-sync-kev.md`

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
same fetcher (CISA KEV). No divergent use case has ever existed: every
fetcher sets both to the same value.

### Redundancy on the CVE side

The default `catch_up()` on `BaseCVEFetcher` delegates to
`fetch_single()`:

```python
async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
    ticket = await session.get(Ticket, UUID(ticket_id))
    if ticket and ticket.cve_id:
        result = await self.fetch_single(str(ticket.cve_id), session)
        await self.commit_and_dispatch(session, result)
```

A CVE fetcher using this default **can** participate in catch-up **if
and only if** `supports_fetch_single = True`. The two flags must always
agree for default-catch_up CVE fetchers:

| `supports_fetch_single` | `participates_in_catch_up` | Status |
|---|---|---|
| `True` | `True` | 7 fetchers (all default-catch_up CVE fetchers) |
| `False` | `False` | 1 fetcher (KEV) |
| `True` | `False` | 0 — speculative, never used |
| `False` | `True` | 0 — mechanically invalid (RuntimeError) |

### CFI-GAP-01 — Unenforced coupling

The `__init_subclass__` validation on `BaseCVEFetcher` validates only
`cve_source_type`. A fetcher that sets `supports_fetch_single = False`
but forgets `participates_in_catch_up = False` passes all import-time
checks and crashes at catch-up runtime.

### Fragility on the non-CVE side

`get_catch_up_fetchers()` uses `'catch_up' in cls.__dict__` to detect
non-CVE fetchers. This breaks when a non-CVE fetcher inherits
`catch_up()` from an intermediate base class (method in the base's
`__dict__`, not the subclass's). This is a latent fragility that
becomes a **concrete near-term problem** given the project roadmap:
delivery detection will expand to git (beyond IBS), and products will
be sourced from 3+ providers — both scenarios naturally produce
intermediate base classes sharing a `catch_up()` implementation.

---

## 2. Decision

**Promote `participates_in_catch_up` to `BaseFetcher`** (default
`False`). On `BaseCVEFetcher`, **auto-derive** the flag from
`supports_fetch_single` via `__init_subclass__` unless explicitly
overridden. Replace the current two-branch predicate with a **uniform
single-flag check**.

---

## 3. Alternatives Considered

| Option | Description | CFI-GAP-01 | Non-CVE intermediate bases | Complexity | Notes |
|--------|-------------|------------|---------------------------|------------|-------|
| 1. Enforce coupling | Add `__init_subclass__` check for flag agreement | Mitigated (guard) | Not addressed | Low | Treats symptom, not cause |
| 2. Eliminate flag, `__dict__` predicate | Remove `participates_in_catch_up`; check `supports_fetch_single` for CVE, `__dict__` for non-CVE | Dissolved | **Not addressed** — breaks with intermediate bases | Minimal | Good for simple hierarchies; insufficient for multi-source roadmap |
| 6. Eliminate flag, identity predicate | Remove flag; identity-based 3-way predicate (`resolved is BaseCVEFetcher.catch_up`) | Dissolved | Addressed | Medium | Reviewed and rejected: introduces new fragilities (decorators, intermediate CVE overrides) requiring sentinel + import-time guard — lateral complexity move |
| **3+D. Uniform flag + derivation** | Promote flag to BaseFetcher; auto-derive on CVE side from `supports_fetch_single` | **Dissolved by derivation** | **Addressed natively** | Medium (contained) | Chosen — robust to MRO depth, decorators, intermediate bases on both sides |

### Why Option 6 was rejected

Option 6 was reviewed by design, coherence, and gap-analysis agents
(2026-06-28). Reviewer findings:

- The identity-based predicate requires a sentinel attribute
  (`_is_default_cve_catch_up`) to handle intermediate CVE bases that
  override `catch_up()` — adding enforcement machinery instead of
  eliminating it
- Decorators on `catch_up()` break identity comparison (different
  concern category than the current spec)
- Net assessment: complexity was redistributed, not reduced

The roadmap confirmation (git-based delivery detection, multi-source
products) then made Option 2's `__dict__` predicate untenable for
non-CVE fetchers, leaving Option 3 + derivation as the design that
addresses both sides with minimal machinery.

---

## 4. New Design

### `BaseFetcher` — new class attribute

```python
class BaseFetcher:
    participates_in_catch_up: bool = False
```

Default `False`: non-CVE fetchers opt IN explicitly (by setting `True`
on their class or an intermediate base). This is consistent with the
non-CVE norm (most non-CVE fetchers do not participate in catch-up).

### `BaseCVEFetcher` — auto-derivation in `__init_subclass__`

```python
class BaseCVEFetcher(BaseFetcher):
    supports_fetch_single: bool = True

    def __init_subclass__(cls, **kwargs):
        if not cls.__dict__.get('abstract', False):
            # CVE-specific validation (cve_source_type) ...
            ...

            # Auto-derive participates_in_catch_up from supports_fetch_single
            # unless the subclass explicitly declares it
            if 'participates_in_catch_up' not in cls.__dict__:
                cls.participates_in_catch_up = cls.supports_fetch_single

        super().__init_subclass__(**kwargs)
        # ... registration in _CVE_SOURCE_TYPE_MAP ...
```

**Effect**: a CVE fetcher only needs to set `supports_fetch_single`.
The catch-up participation derives automatically. KEV sets
`supports_fetch_single = False` → `participates_in_catch_up` becomes
`False` without explicit declaration.

**Explicit override preserved**: a future CVE fetcher CAN set
`participates_in_catch_up = False` explicitly (even with
`supports_fetch_single = True`) to opt out of catch-up while retaining
on-demand capability. This is the `(True, False)` case — expressible
but not required.

### `get_catch_up_fetchers()` — uniform predicate

```python
def get_catch_up_fetchers() -> dict[str, type[BaseFetcher]]:
    """Return fetchers that participate in per-ticket catch-up.

    Selection is based solely on the participates_in_catch_up class
    attribute. This attribute is:
    - Auto-derived from supports_fetch_single for BaseCVEFetcher
      subclasses (unless explicitly overridden)
    - Set explicitly by non-CVE fetchers (on the concrete class or
      an intermediate base)
    - Default False on BaseFetcher (non-participating unless declared)

    Note: this predicate selects by CAPABILITY, not by enabled state.
    The enabled check is performed downstream in run_catch_up at task
    execution time. A disabled fetcher is still returned here but
    skipped silently at runtime.
    """
    return {
        name: cls
        for name, cls in FETCHER_REGISTRY.items()
        if cls.participates_in_catch_up
    }
```

### Trace table — all hierarchy levels

| Concrete class | Hierarchy | `participates_in_catch_up` | Source | In roster? |
|---|---|---|---|---|
| `SyncNvdCves` | BF→BCVEF→concrete | `True` | derived from `supports_fetch_single=True` | Yes |
| `SyncMitreCves` | BF→BCVEF→BGF→concrete | `True` | derived (BGF doesn't override) | Yes |
| `SyncKernelCves` | BF→BCVEF→BGF→concrete | `True` | derived | Yes |
| `SyncCisaKev` | BF→BCVEF→concrete | `False` | derived from `supports_fetch_single=False` | **No** |
| `SyncRedhatCves` | BF→BCVEF→concrete | `True` | derived | Yes |
| `SyncEpssScores` | BF→BCVEF→concrete | `True` | derived | Yes |
| `SyncGhsaAdvisories` | BF→BCVEF→concrete | `True` | derived | Yes |
| `SyncOsvAdvisories` | BF→BCVEF→concrete | `True` | derived | Yes |
| `DetectIbsTrackReleases` | BF→concrete | `True` | explicit on class | Yes |
| `DetectIbsProductReleases` | BF→concrete | `True` | explicit on class | Yes |
| `SyncIbsRequests` | BF→concrete | `True` | explicit on class | Yes |
| `EvaluateLifecycleTransitions` | BF→concrete | `True` | explicit on class | Yes |
| `SyncIbsBugowners` | BF→concrete | `True` | explicit on class | Yes |
| `SyncSmeltProducts` | BF→concrete | `False` | inherited default | **No** |
| `SyncLdapDirectory` | BF→concrete | `False` | inherited default | **No** |
| *future* `BaseTrackReleaseFetcher` → `DetectIbsTrackReleases` | BF→intermediate→concrete | `True` | explicit on intermediate, inherited by concrete | Yes |
| *future* `BaseTrackReleaseFetcher` → `DetectGitTrackReleases` | BF→intermediate→concrete | `True` | inherited from intermediate | Yes |

### Intermediate non-CVE base classes

The pattern for shared catch-up logic across delivery/product sources:

```python
class BaseTrackReleaseFetcher(BaseFetcher):
    participates_in_catch_up = True      # set once on the base
    abstract = True                       # not registered in FETCHER_REGISTRY

    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Shared catch-up logic for track release detection."""
        ...

class DetectIbsTrackReleases(BaseTrackReleaseFetcher):
    name = "detect_ibs_track_releases"
    # inherits participates_in_catch_up = True
    # inherits catch_up()

class DetectGitTrackReleases(BaseTrackReleaseFetcher):
    name = "detect_git_track_releases"
    # inherits participates_in_catch_up = True
    # inherits catch_up()
```

Both concrete fetchers participate in catch-up via inherited flag —
no `__dict__` fragility, no convention workarounds.

### Why `supports_fetch_single` remains a flag

`supports_fetch_single` is NOT auto-detected from method presence
(unlike what Option 6 proposed for `catch_up`). The asymmetry is
deliberate:

- **`BaseCVEFetcher.fetch_single`** is a **sentinel** default (raises
  `RuntimeError`). An opt-out flag (default `True`) provides:
  1. **Explicit intent**: `supports_fetch_single = False` is a
     deliberate declaration, distinguishable from a forgotten override
  2. **Loud failure**: a fetcher that forgets the override is included
     → `RuntimeError` caught immediately in tests (not silently
     excluded)
- **`participates_in_catch_up`** is an **explicit participation
  signal** that works uniformly across all fetcher types via MRO
  attribute lookup — no introspection needed

### Caching semantics clarification

The current spec contains a contradiction (lines 439-444 of
`fetcher-infrastructure.md`): it states the function is "computed on
each call (not cached)" but requires a `_clear_catch_up_cache()` test
helper. Resolution:

**Adopt "no cache"**: the predicate iterates `FETCHER_REGISTRY` on
each call. The `_clear_catch_up_cache()` test helper is **removed** —
test suites that dynamically register mock fetcher classes need only
clean `FETCHER_REGISTRY` (already required for `_clear_fetch_single_cache()`
and general fetcher test isolation). This eliminates the contradiction
and removes an unnecessary abstraction.

---

## 5. Detailed Application Plan

### File 1: `docs/features/platform/cve-fetcher-infrastructure.md`

| Location | Current | Action |
|----------|---------|--------|
| Line 55 (Class Attributes table, `participates_in_catch_up` row) | Row with default `True`, description references `get_catch_up_fetchers()` | **Reword**: default remains `True`, but description changes to: "Whether the fetcher participates in per-ticket catch-up. Inherited from `BaseFetcher` (default `False`); on `BaseCVEFetcher` subclasses, **auto-derived** from `supports_fetch_single` via `__init_subclass__` unless explicitly overridden in the subclass body. Global-scope CVE fetchers (KEV) set `supports_fetch_single = False` and the derivation produces `participates_in_catch_up = False` automatically" |
| Line 56 (Class Attributes, `supports_fetch_single` description) | "...are never dispatched by `fetch_single_cve`, and do not need to override `fetch_single()`" | **Extend**: append that `supports_fetch_single = False` also causes `participates_in_catch_up` to derive as `False`, excluding the fetcher from the catch-up roster. Makes the dual role explicit |
| Line 65 (Concrete Methods, `catch_up` description) | "...fetchers with `False` also set `participates_in_catch_up = False` (so `catch_up()` is never invoked)" | **Reword**: "...fetchers with `supports_fetch_single = False` have `participates_in_catch_up` auto-derived as `False` (so `catch_up()` is never invoked for them)" |
| Lines 166-188 (pseudocode `__init_subclass__`) | Only validates `cve_source_type` | **Add** the auto-derivation block after uniqueness check and before `super().__init_subclass__()`: `if 'participates_in_catch_up' not in cls.__dict__: cls.participates_in_catch_up = cls.supports_fetch_single` |
| Line 207 (Non-Modification Statement, item 4) | "The `participates_in_catch_up` opt-out for catch-up participation" | **Reword**: "The `participates_in_catch_up` auto-derivation from `supports_fetch_single` (catch-up roster inclusion)" |
| Line 579 (Default catch_up section) | "(they also set `participates_in_catch_up = False`)" | **Reword**: "(their `participates_in_catch_up` is auto-derived as `False` from `supports_fetch_single = False`)" |

### File 2: `docs/features/platform/fetcher-infrastructure.md`

| Location | Current | Action |
|----------|---------|--------|
| BaseFetcher class attributes section (near line 50) | No `participates_in_catch_up` attribute | **Add row**: `participates_in_catch_up` / `bool` / `False` / "Whether the fetcher participates in per-ticket catch-up. Default `False` — non-CVE fetchers opt in explicitly (on the concrete class or an intermediate base). `BaseCVEFetcher` auto-derives this from `supports_fetch_single` for its subclasses" |
| Lines 403-437 (`get_catch_up_fetchers()` section) | Two-branch predicate with `participates_in_catch_up` + `'catch_up' in cls.__dict__` | **Replace entirely** with the new uniform predicate (Section 4 of this draft) |
| Lines 439-444 (Caching semantics) | Contradictory text about no-cache + `_clear_catch_up_cache()` | **Replace**: "Computed on each call from the current registry state (not cached). No dedicated cache-clearing test helper is needed — test suites that dynamically register fetcher classes clean `FETCHER_REGISTRY` directly" |
| Line 615 (excluded fetchers table, `sync_cisa_kev` row) | "Syncs entire KEV catalog (sets `participates_in_catch_up = False`)" | **Reword**: "Syncs entire KEV catalog (`supports_fetch_single = False` → `participates_in_catch_up` derived as `False`)" |
| Lines 617-622 (KEV exclusion explanatory note) | References `participates_in_catch_up = False` as explicit opt-out | **Reword** to explain it is now auto-derived from `supports_fetch_single = False` |
| Import-time validation (line 829) | "If a fetcher defines `catch_up()` in its `__dict__`..." | **Keep unchanged** — the signature validation still applies regardless of how participation is signaled |

### File 3: `docs/features/tickets/cve-sync-kev.md`

| Location | Current | Action |
|----------|---------|--------|
| Line 37 (code example) | `participates_in_catch_up = False` | **Remove line** (auto-derived from `supports_fetch_single = False`) |
| Line 64 (attributes table) | Row: `participates_in_catch_up` / `False` | **Remove row** or **reword** to: "auto-derived as `False` from `supports_fetch_single`" (author's choice; removing is cleaner since the derivation is documented in the infrastructure spec) |

### File 4: `docs/features/platform/git-fetcher-infrastructure.md`

| Location | Current | Action |
|----------|---------|--------|
| Lines 962-968 (Registry Detection section) | References `BaseCVEFetcher subclass detection` for `get_catch_up_fetchers()` | **Reword**: "`get_catch_up_fetchers()` uses the `participates_in_catch_up` class attribute (auto-derived from `supports_fetch_single` for CVE fetchers). `BaseGitFetcher` does not override `supports_fetch_single` (default `True`) nor `participates_in_catch_up` — all git-based fetchers participate in catch-up automatically" |

### Verification files (confirm no changes needed)

| File | Reference | Expected action |
|------|-----------|-----------------|
| `ticket-mutations.md:213` | "...via `get_catch_up_fetchers()`" | No change — function name unchanged |
| `cvss-scoring.md:790` | "...via `get_catch_up_fetchers()` — not limited to CVSS fetchers" | No change — factual, still true |

### Post-application: review file update

- Mark `CFI-GAP-01` as RESOLVED in `docs/reviews/cve-fetcher-infrastructure.md`:
  `**Status**: RESOLVED — Coupling dissolved by auto-derivation; participates_in_catch_up now derives from supports_fetch_single via __init_subclass__ (YYYY-MM-DD)`
- Update `.tracking.json` cache (decrement GAP Medium by 1)
- Update `docs/reviews/README.md`

### Post-application: verification

Run reviewers on each modified spec:

| Reviewer | Target spec | What to verify |
|----------|-------------|----------------|
| `@spec-coherence-reviewer` | `cve-fetcher-infrastructure.md` | Derivation description consistent with `fetcher-infrastructure.md` |
| `@spec-coherence-reviewer` | `fetcher-infrastructure.md` | New predicate and attribute consistent with all consumer specs |
| `@spec-gap-analyzer` | `fetcher-infrastructure.md` | New attribute + predicate covers all edge cases |

### Post-application: cleanup

Once all modifications are applied and verified:

- **Delete** this draft file (authoritative definitions live in the
  modified spec files)

---

## 6. Edge Cases

### KEV: only needs `supports_fetch_single = False`

After the change, KEV no longer explicitly sets
`participates_in_catch_up = False`. The derivation in
`__init_subclass__` sets it automatically from
`supports_fetch_single = False`. This eliminates the coupling footgun
entirely — there is only one flag to manage.

### CVE fetcher with custom `catch_up()` override

A CVE fetcher that overrides `catch_up()` with custom logic (not
delegating to `fetch_single()`) inherits `participates_in_catch_up`
from the auto-derivation. If it has `supports_fetch_single = True`
(the default), it auto-derives `participates_in_catch_up = True` →
included in catch-up. If it has `supports_fetch_single = False` but
still wants to participate (its custom `catch_up()` doesn't use
`fetch_single()`), it explicitly sets
`participates_in_catch_up = True` → the `'participates_in_catch_up'
not in cls.__dict__` guard preserves the explicit declaration.

### Non-CVE fetcher forgets the flag but defines `catch_up()`

With the uniform predicate, a non-CVE fetcher that defines
`catch_up()` but forgets `participates_in_catch_up = True` is
**silently excluded** from the catch-up roster. This is the one
trade-off vs the old `__dict__` approach (where defining the method
was sufficient). Mitigation:

- For intermediate bases (the common case going forward): set the flag
  **once on the base** — all subclasses inherit it. The risk of
  omission is confined to the base definition, not repeated per
  subclass
- A `__init_subclass__` warning (not error) on `BaseFetcher` could
  detect "defines `catch_up()` but `participates_in_catch_up` is
  False" and log a warning at import time. This is optional and
  deferred — the mismatch would surface in integration tests when
  catch-up doesn't run

### The `(True, False)` case

A CVE fetcher that supports on-demand `fetch_single` but wants to opt
out of automatic catch-up can explicitly set
`participates_in_catch_up = False` in its class body. The derivation
guard (`'participates_in_catch_up' not in cls.__dict__`) preserves
this explicit declaration. No current fetcher uses this; it remains
expressible without additional machinery.

---

## 7. Review Checklist

Before applying the plan, this draft should be reviewed by:

- [ ] `@design-reviewer` — validate uniform flag + derivation design,
  trade-off of silent exclusion for non-CVE fetchers that forget flag
- [ ] `@spec-coherence-reviewer` — verify no contradictions across
  modified specs
- [ ] `@spec-gap-analyzer` — verify predicate + derivation covers all
  edge cases

---

## Cross-references

- `docs/features/platform/cve-fetcher-infrastructure.md` — BaseCVEFetcher
  class, `supports_fetch_single`, default `catch_up()`, `__init_subclass__`
- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher,
  `get_catch_up_fetchers()`, Per-Ticket Catch-Up
- `docs/features/platform/git-fetcher-infrastructure.md` — BaseGitFetcher
  (intermediate, inherits all defaults)
- `docs/features/tickets/cve-sync-kev.md` — sole fetcher with
  `supports_fetch_single = False`
- `docs/reviews/cve-fetcher-infrastructure.md` — CFI-GAP-01 finding
