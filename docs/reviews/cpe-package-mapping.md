# Review: cpe-package-mapping

**Spec**: `docs/features/packages/cpe-package-mapping.md`
**Last reviewed**: 2026-06-01
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CPM-GAP-01 — Escaped colon in lookup key produces ambiguous key format (Medium)

**Status**: RESOLVED — Spec semplificata: rimosso replace-split-restore, aggiunto early-detection + skip per CPE con escaped colons; rimossa entry malformata dal file di mapping (2026-06-01)

### CPM-GAP-02 — Hot-reload behavior after deployment (Medium)

**Category**: Temporal/concurrency scenario
**Status**: OPEN

When the application is redeployed with an updated mapping file, the `lru_cache` retains the old mapping for the lifetime of the process. The spec does not specify whether workers must be restarted to pick up changes, or whether there's a cache invalidation mechanism. In a Celery worker pool, long-lived workers would use the stale mapping indefinitely until manually restarted. The spec should state that a process restart is required after mapping file changes.

### CPM-GAP-03 — Case sensitivity of lookup keys (Medium)

**Category**: Missing validation
**Status**: OPEN

NVD CPE strings are nominally lowercase, but the spec does not specify whether the lookup is case-sensitive or case-insensitive. If a CPE string arrives as `cpe:2.3:a:Apache:Commons_Compress:...` (mixed case), a case-sensitive lookup against a key `apache:commons_compress` would miss. The spec should state whether keys and lookups are normalized to lowercase.

### CPM-GAP-04 — Duplicate package batch failure handling (Low)

**Category**: Unspecified error path
**Status**: OPEN

The spec states to collect all returned package names into a set and call `add_package_to_ticket()` once per unique package name. However, it does not specify what happens if `add_package_to_ticket()` fails for one package (e.g., SMELT timeout) while succeeding for others. Is it partial success (some packages added), or should the entire batch be retried?

### CPM-GAP-05 — Mapping file read-once semantics not explicit (Low)

**Category**: Unspecified behavior
**Status**: OPEN

With `lru_cache`, the file is loaded exactly once per process and then cached. The spec covers the fail-fast case at startup but does not explicitly state that runtime file corruption is irrelevant since the file is never re-read. Clarifying the read-once-per-process semantics would remove ambiguity about runtime file access.

---

## Coherence

_No findings._

---

## Design

### CPM-DES-01 — No mechanism to detect stale/missing mappings at scale (Medium)

**Category**: Observability gap
**Status**: OPEN

When a new upstream project accumulates CVEs without a corresponding mapping entry, `resolve_cpe_packages()` returns the raw CPE product name, SMELT returns nothing, and the package is silently not added. This could go unnoticed for weeks/months. A lightweight counter/metric for "fallback used + SMELT miss" would surface systematic mapping gaps without adding architectural complexity (~5 lines of code in ingestion pipeline).

### CPM-DES-02 — Escaped colon in lookup key creates ambiguous keys (Low)

**Category**: Key format design
**Status**: OPEN

If a vendor name contains an escaped colon (e.g., `foo\:bar`), the resulting lookup key `foo:bar:product` is indistinguishable from a key where the vendor is `foo` and the product is `bar:product`. An alternative would be to use a separator that cannot appear in CPE field values (e.g., unit separator) or a tuple `(vendor, product)` as the dict key internally. Accepted risk given no current real-world collisions exist in the NVD dataset.

### CPM-DES-03 — lru_cache has no clean invalidation path (Low)

**Category**: Extensibility concern
**Status**: OPEN

The `lru_cache` approach works well for the current deploy-based update model (new container = fresh cache). However, if a future "reload mapping" admin action is desired, `lru_cache` has no clean invalidation path without importing and calling `cache_clear()`. Acceptable for current design but worth documenting.

### CPM-DES-04 — Race between mapping deployment and in-flight ingestion (Low)

**Category**: Temporal race
**Status**: OPEN

A CVE sync task starts with the old mapping in memory. Meanwhile, a new deployment rolls out with an updated mapping. The in-flight task continues with the old mapping. A few CVEs in the transition batch may miss newly-added mappings. Given mapping changes are rare (a few times/year) and the operational workflow expects manual remediation, this is acceptable.

### CPM-DES-05 — Raw-product fallback generates fruitless SMELT queries (Low)

**Category**: Performance consideration
**Status**: OPEN

For unmapped CPEs, the raw product name is sent to SMELT which returns nothing. For high-volume CVE sources (~25,000 CVEs/year from NVD), this could generate thousands of wasted SMELT queries per sync cycle. If SMELT latency becomes a concern, consider caching "known-missing" package names. Not needed now.

---

## Security

### CPM-SEC-01 — Escaped colon in vendor produces ambiguous lookup key (Low)

**Category**: Input parsing
**Status**: OPEN

A crafted NVD entry with an escaped colon in the vendor could cause a lookup to match an unintended mapping entry, leading to incorrect package association on a ticket. Impact is limited to incorrect package association (which a VA would review), not code execution or data breach. Could use a non-ambiguous separator internally.

### CPM-SEC-02 — No integrity verification of mapping file at load time (Low)

**Category**: Supply chain
**Status**: OPEN

The mapping file is loaded from disk with no checksum or signature verification. If an attacker gains filesystem write access (container escape, supply chain attack), they could modify the mapping to suppress or inject false package associations. Acceptable given threat model (filesystem access implies broader compromise). For defense-in-depth, consider logging file SHA-256 at startup.

### CPM-SEC-03 — Raw product fallback passes unvalidated string to SMELT (Low)

**Category**: Input validation
**Status**: OPEN

When no mapping exists, the raw CPE product field is passed directly as a query parameter to SMELT. The product name is not validated against any character format. If SMELT's API has injection vulnerabilities, the unvalidated string would be passed directly. Defense-in-depth: validate that fallback product names contain only expected characters before SMELT queries.

---

## API Conventions

_No findings._
