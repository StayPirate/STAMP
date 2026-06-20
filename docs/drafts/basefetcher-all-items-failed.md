# BaseFetcher All-Items-Failed Safety Check

Status: **Draft** — Cross-cutting infrastructure change, not yet scheduled.

## Problem

When a `BaseFetcher` subclass's `execute()` method returns normally but all
items have failed (`items_failed > 0` and `items_created + items_updated == 0`),
the run is marked as `partial`. This is semantically incorrect — `partial`
implies some items succeeded, but in this case nothing worked.

`BaseGitFetcher` already addresses this with a safety check at step 11 of its
template method:

```python
if items_failed > 0 and items_created + items_updated == 0:
    raise RuntimeError(
        f"All {items_failed} items failed — cursor not advanced for safety"
    )
```

This converts a `partial` into a `failure`, which is the correct semantic
status. However, this safety check is specific to `BaseGitFetcher` —
`BaseFetcher` and `BaseCVEFetcher` do not have it.

## Affected Fetchers

Non-git fetchers that iterate over items and could theoretically have all items
fail without raising from `execute()`:

| Fetcher | Base class | Affected? |
|---------|-----------|-----------|
| `sync_nvd_cves` | `BaseCVEFetcher` | Yes — page-level errors abort, but per-CVE errors within pages are isolated |
| `sync_redhat_cves` | `BaseCVEFetcher` | Yes — iterates over CVE IDs with per-entry isolation |
| `sync_osv_cves` | `BaseCVEFetcher` | Yes — iterates over CVE IDs with per-entry isolation (has abort threshold at 3 consecutive failures, but below-threshold scattered failures could still result in 100% failure) |
| `sync_cisa_kev` | `BaseCVEFetcher` | Yes — iterates over catalog entries with per-entry isolation |
| `sync_epss_scores` | `BaseCVEFetcher` | Yes (planned) — same catalog pattern as KEV |
| `sync_ghsa_advisories` | `BaseCVEFetcher` | Yes — page-level errors abort, but per-advisory errors within pages are isolated |
| `sync_smelt_products` | `BaseFetcher` | Yes — iterates over product records |
| `sync_aimaas_lifecycle` | `BaseFetcher` | Yes — iterates over product records |
| `sync_aimaas_thresholds` | `BaseFetcher` | Yes — iterates over threshold records |
| `detect_ibs_track_releases` | `BaseFetcher` | Yes — iterates over tracks |
| `detect_ibs_product_releases` | `BaseFetcher` | Yes — iterates over products |
| `sync_ldap_directory` | `BaseFetcher` | Yes — iterates over AD entries |
| `sync_ibs_requests` | `BaseFetcher` | Yes — iterates over IBS requests |

Git-based fetchers (`sync_mitre_cves`, `sync_kernel_cves`) already have the
safety check via `BaseGitFetcher`.

## Proposed Solution

Promote the safety check from `BaseGitFetcher` to `BaseFetcher.run()`, so it
applies automatically to all fetcher subclasses.

### Option A: In `BaseFetcher.run()` (automatic for all)

After `execute()` returns normally and before writing the final status, add:

```python
if self.items_failed > 0 and self.items_created + self.items_updated == 0:
    raise RuntimeError(
        f"All {self.items_failed} items failed"
    )
```

This turns the run into `failure` with the RuntimeError message captured in
`error_message`. The existing `BaseGitFetcher` step 11 becomes redundant and
can be removed.

**Pros**: zero opt-in needed, all fetchers benefit, single implementation
**Cons**: changes behavior for all existing fetchers (though the scenario
"all items failed but execute() didn't raise" should already be treated as
failure)

### Option B: In `BaseFetcher.run()` with opt-out

Same as Option A but with a class attribute `all_failed_is_partial: bool =
False` that fetchers can set to `True` if they genuinely want `partial` status
when all items fail. No known use case for this today.

**Pros**: backward-compatible escape hatch
**Cons**: adds complexity for a case that likely doesn't exist

### Recommendation

**Option A** is simpler and the behavioral change is correct — no fetcher
should silently report `partial` when everything failed.

## Impact on Specifications

| File | Change |
|------|--------|
| `docs/features/platform/fetcher-infrastructure.md` | Add the safety check to `BaseFetcher.run()` lifecycle (after `execute()` returns, before final status assignment). Update status determination precedence to document this case. Remove redundant step 11 from `BaseGitFetcher` template method |
| `docs/data-model.md` | Update `FetcherRunStatus.partial` description: "Completed but some items failed (`items_failed > 0` and at least one item succeeded)" |

## Side Effects

- **Cursor behavior**: `failure` status does not advance the cursor (per
  `fetcher-infrastructure.md` line 615-617). This is correct — if all items
  failed, the cursor should not advance so the next run retries the same
  window
- **Dashboard visibility**: `failure` is more prominent than `partial` in the
  fetcher dashboard, which is the desired behavior for a total failure

## Origin

Identified during CISA KEV fetcher draft review (Session 5, 2026-06-20).
The question arose when analyzing the impact of 1600 consecutive per-entry
failures with DB down: the fetcher would complete as `partial` with
`items_failed=1600, items_updated=0`, which is semantically a `failure`.
