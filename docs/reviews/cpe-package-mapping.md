# Review: cpe-package-mapping

**Spec**: `docs/features/packages/cpe-package-mapping.md`
**Last reviewed**: 2026-07-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CPM-GAP-01 — Escaped colon in lookup key produces ambiguous key format (Medium)

**Status**: RESOLVED — Spec simplified: removed replace-split-restore, added early-detection + skip for CPEs with escaped colons; removed malformed entry from the mapping file (2026-06-01)

### CPM-GAP-02 — Hot-reload behavior after deployment (Medium)

**Status**: RESOLVED — Spec updated: added operational note clarifying mapping is source code with read-once-per-process semantics, updates require commit + new deployment (2026-06-01)

### CPM-GAP-03 — Case sensitivity of lookup keys (Medium)

**Status**: RESOLVED — Spec updated: added lowercase normalization step (step 6) in parsing algorithm and CI validation rule for mapping keys (2026-06-01)

### CPM-GAP-04 — Duplicate package batch failure handling (Low)

**Status**: RESOLVED — Spec updated: added best-effort loop semantics to cve-service.md Post-Ingestion Side Effects section (2026-06-01)

### CPM-GAP-05 — Mapping file read-once semantics not explicit (Low)

**Status**: RESOLVED — Cross-agent duplicate of CPM-GAP-02 (2026-06-01)

---

## Coherence

_No findings._

---

## Design

### CPM-DES-01 — No mechanism to detect stale/missing mappings at scale (Medium)

**Status**: RESOLVED — Accepted risk: observability for unmapped CPEs deferred as premature optimization (2026-06-01)

### CPM-DES-02 — Escaped colon in lookup key creates ambiguous keys (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-01)

### CPM-DES-03 — lru_cache has no clean invalidation path (Low)

**Status**: RESOLVED — Cross-agent duplicate of CPM-GAP-02 (2026-06-01)

### CPM-DES-04 — Race between mapping deployment and in-flight ingestion (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-01)

### CPM-DES-05 — Raw-product fallback generates fruitless SMELT queries (Low)

**Status**: RESOLVED — Accepted risk: fruitless SMELT queries deferred as premature optimization (2026-06-01)

---

## Security

### CPM-SEC-01 — Escaped colon in vendor produces ambiguous lookup key (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-06-01)

### CPM-SEC-02 — No integrity verification of mapping file at load time (Low)

**Status**: RESOLVED — Accepted risk: integrity verification unnecessary given mapping is committed source code in immutable container image (2026-06-01)

### CPM-SEC-03 — Raw product fallback passes unvalidated string to SMELT (Low)

**Status**: RESOLVED — Accepted risk: raw product string from NVD (trusted source) poses no practical injection risk to SMELT internal API (2026-06-01)

---

## API Conventions

_No findings._
