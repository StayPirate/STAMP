# IBS Product Release Detection

## Purpose

Detect when CVE fixes are published to product update repositories by
parsing `updateinfo.xml` metadata from IBS download infrastructure. This is
the **product-level** release detection mechanism.

For the overall release tracking architecture (two independent levels —
codestream and product), see `docs/features/packages/package-model.md`, section
"Release Tracking". For codestream-level detection, see
`docs/features/packages/ibs-track-release-detection.md`.

## Context

Sentinel monitors two independent levels of release for each affected
package:

1. **Codestream level** (separate spec): the fix has been added to the
   codestream's IBS project.
2. **Product level** (this spec): the fix has been published to the
   product's update repository (e.g., the SLES 15 SP6 update repository
   consumed by `zypper`).

The product level sets `TicketPackageProduct.released_at` as soon as the
fix appears in that specific product's update repository. Products do
not have their own affectedness status — release confirmation is tracked
exclusively via the `released_at` timestamp.

## Detection Mechanism

Sentinel uses an internal abstraction `ProductReleaseDetector` based on the
standard `updateinfo.xml` metadata file published in every product update
repository (the same metadata file consumed by `zypper`). This is the
ground-truth source: an advisory present in `updateinfo.xml` is, by
definition, available to end users of that product.

### Procedure

For each product P with an associated update repository URL `<repo_url>`
(see [Update Repository URL Resolution](#update-repository-url-resolution)
below for how `<repo_url>` is constructed):

1. Download `<repo_url>/repodata/repomd.xml`.
2. Locate the `<data type="updateinfo">` element and extract the location
   of the `updateinfo.xml.gz` file (path relative to `<repo_url>`).
3. Download and parse `updateinfo.xml`.
4. Iterate the `<update>` elements. For each `<update>` U, check whether its
   `<references>` block contains a `<reference type="cve" id="CVE-XXXX-YYYY">`
    matching the CVE-ID of any active ticket whose `TicketPackageProduct`
    records reference P and have `released_at IS NULL`. Soft-deleted
    products are included — release detection applies regardless of
    exclusion status (see hierarchical exclusion model in
    `docs/features/packages/package-model.md`).
5. For each such advisory, apply the
   [Advisory ↔ Source Package Match](#advisory--source-package-match) chain
   below to identify which specific source package of the ticket received
   the fix.

### Outcome per matched (ticket, product, package)

- `TicketPackageProduct.released_at` is set to the `<issued date>` attribute
  of the advisory through the `package_service` module.

### Update Repository URL Resolution

Sentinel does not store a separate URL field for update repositories. The HTTP
URL is constructed at runtime from each `ProductRepository.repo_name` using
the pattern:

```
{IBS_DOWNLOAD_BASE_URL}/{repo_name.replace(':', '/')}/update/
```

where `IBS_DOWNLOAD_BASE_URL` is an environment variable (default:
`https://download.suse.de/ibs`). For example, repo name
`SUSE:Updates:SLE-Module-Basesystem:15-SP7:x86_64` produces the URL
`https://download.suse.de/ibs/SUSE/Updates/SLE-Module-Basesystem/15-SP7/x86_64/update/`.

Only `ProductRepository` entries with prefix `SUSE:Updates:` are relevant
for release tracking. Other repository types are excluded:

- `SUSE:Products:*` — base product/pool repos, never contain
  `updateinfo.xml`.
- Repos whose last segment is `debug` or `src` — companion repos for
  debuginfo and source packages, never contain advisory metadata.
- Repos targeting non-RPM distributions (Debian, Ubuntu, or
  `MultiLinuxManagerTools` targeting Debian/Ubuntu) — these use apt
  format, not RPM repodata.

If a product has no eligible `SUSE:Updates:*` entries in
`ProductRepository`, it is skipped during release tracking with a
WARNING-level log. This is expected for products that are not yet released
(e.g., SLE 16.x) or deprecated.

### Multi-architecture Handling

SMELT repository names fall into two categories:

- **Single-arch repos**: name ends with a known architecture segment.
  Known architectures: `x86_64`, `aarch64`, `s390x`, `ppc64le`, `i586`,
  `i686`, `ia64`, `ppc64`.
  Example: `SUSE:Updates:SLE-Module-Basesystem:15-SP7:x86_64`.
- **Multi-arch repos**: name does NOT end with an architecture segment.
  These repos contain packages for all architectures in a single
  repository.
  Example: `SUSE:Updates:openSUSE-SLE:15.6`.

   Sentinel does NOT track release status per architecture — a match on any
   architecture is sufficient to set `released_at`.

**Scanning strategy per product**:

1. From the product's `ProductRepository` entries, select those eligible
   for release tracking (prefix `SUSE:Updates:`, excluding `debug`,
   `src`, and non-RPM repos as described above).
2. If a multi-arch repo exists, scan it first (it covers all
   architectures in a single repository).
3. If no match was found (or no multi-arch repo exists), scan single-arch
   repos: `x86_64` first (primary architecture), then remaining
   architectures in alphabetical order.
4. As soon as a match is found on any repo, set `released_at` and stop —
   do not scan remaining repos.

This approach handles the common case efficiently (most advisories land on
x86_64) while also covering arch-specific packages like `s390-tools` that
are only released for `s390x`.

### Error Handling

The `ProductReleaseDetector` handles the following error conditions
gracefully:

- **HTTP 404** (repository does not exist on `download.suse.de`): skip
  with WARNING-level log. This is expected for brand-new products whose
  repos have not yet been created (e.g., SLE 16.x, SL-Micro 6.x).
- **HTTP 403** (access restricted): skip with WARNING-level log. Some
  partner repos may have access restrictions.
- **`repomd.xml` exists but has no `<data type="updateinfo">`**: skip
  silently. This means the repository exists but has had zero security
  updates published to it. This is normal for newly launched or niche
  products.
- **Network errors / timeouts**: skip with ERROR-level log, retry on the
  next scheduled run of `detect_ibs_product_releases`.

## Advisory ↔ Source Package Match

This match procedure is defined once and applies to the **product-level**
detection only. It operates on `<update>` entries from `updateinfo.xml`.

The codestream-level detector does not use this match chain — the IBS diff
endpoint (`POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1`)
already provides an explicit `CVE -> source package` link via the `<issues>`
response, so the package that received the fix is known directly. See
`docs/features/packages/ibs-track-release-detection.md`.

**Why this matters**: a single CVE can affect multiple distinct source
packages, typically when a vulnerable library is statically linked into
binding packages (e.g., a CVE in a Go or Rust library that impacts
`containerd`, `podman`, `golang-1.21`, and others — each requiring its own
independent fix). Sentinel must identify **which specific source package** of
the ticket has been fixed by a given advisory, so that only the
corresponding `TicketPackageProduct` record has its `released_at` set,
leaving the others untouched until their own fixes land.

The match is a cascade — the first step that produces a positive match
wins; on failure, processing falls through to the next step.

### Step 1 — Title pattern match

- Apply the regular expression
  `^(Security|Recommended|Optional|Feature) update for (\S+)$` to the
  advisory's `<title>`.
- **Pattern not recognized** (no match for the regex above): emit a
  WARNING-level application log including `advisory_id`, `repo`, and the
  raw `title`, then fall through to Step 2. These warnings will feed the
  future admin "Sync diagnostics" page (separate spec).
- **Pattern recognized and the captured group `<X>` exactly equals one of
  the ticket's `package_name` values**: MATCH on that package.
- **Pattern recognized but `<X>` does not equal any ticket package**: fall
  through silently to Step 2 (this is the normal case for advisories that
  legitimately use a title package name distinct from the source name).

### Step 2 — Heuristic prefix match

For each `package_name` PT of the ticket, PT is a candidate match if it
appears either:

- in the package name `<X>` extracted from the title (rule:
  `X == PT` OR `X.startswith(PT + "-")`), **or**
- in at least one `<package name="B">` of the `<pkglist>` (rule:
  `B == PT` OR `B.startswith(PT + "-")`).

Then:

- **No candidate** → fall through to Step 3.
- **Exactly one candidate** → MATCH on that package.
- **Multiple candidates**: the longest PT wins (most specific match).
  Example: a ticket containing both `openssl` and `openssl-3` against an
  advisory whose pkglist includes `libopenssl-3-devel` resolves to
  `openssl-3`.
- **Ambiguity not resolved by length** (two or more PT of the same length
  matching) → fall through to Step 3.

### Step 3 — `primary.xml` exact source match

- Download `primary.xml` of the repository (also referenced from
  `repomd.xml`).
- For each binary RPM listed in the advisory's `<pkglist>`, read its
  `<rpm:sourcerpm>` element (e.g.,
  `openssl-3-3.1.4-150600.5.9.1.src.rpm`) and derive the source package
  name by stripping the trailing `-version-release.arch.src.rpm`
  components (yielding e.g. `openssl-3`).
- Compare the resulting source names against the ticket's `package_name`
  values (exact equality).
- **No match** → proceed with the no-match flow below.
- **Exactly one ticket package matches** → MATCH on that package.
- **Multiple ticket packages match** (e.g., the advisory ships SRPMs for
  several source packages that are all in the ticket): apply the same
  tie-breaker as Step 2 — the longest `package_name` wins. If two or more
  matching packages have the same length, fall through to the no-match
  flow (this is conservative: better to surface the case for VA review
  than to risk flipping the wrong record).

## Match Outcomes

### Positive match (source package S of the ticket on product P)

- `TicketPackageProduct(S, P).released_at` = advisory's `<issued date>`.

### No-match (advisory cites the ticket's CVE but no ticket package matches, even via `primary.xml`)

- Create a `TicketAuditEvent` of informational type recording: `advisory_id`,
  the source name derived from `primary.xml` if available, and a note that
  no ticket package matched.
- Notify the ticket's assignee (notification mechanism is TBD at the system
  level, see [Open Items](#open-items)).
- Add the ticket to the **"Revisit" list** (separate feature spec, TBD).
- **No automatic modification** is made to the ticket's package records.

Note: codestream-level no-match behavior (CVE found in diff but package
not tracked in ticket, or no ticket exists at all) is described in
`docs/features/packages/ibs-track-release-detection.md` (Cases B and C).

## Background Task

### Fetcher: `detect_ibs_product_releases`

| Property | Value |
|----------|-------|
| Fetcher name | `detect_ibs_product_releases` |
| Class name | `DetectIbsProductReleases` |
| Schedule | TBD |
| Source | IBS download infrastructure (`download.suse.de`) |
| Scope | All `TicketPackageProduct` records with `released_at IS NULL` belonging to active tickets. Soft-deleted products are included |
| Auth | HTTP Basic / API token (internal) |
| Custom settings | No |

#### Metrics

- `record_created`: N/A
- `record_updated`: a `TicketPackageProduct.released_at` was set
  (advisory matched)
- `record_failed`: a product repository could not be fetched or parsed

## Open Items

The following aspects of product-level release tracking are intentionally
left open in this revision of the spec. They will be closed in subsequent
sessions before implementation begins.

### Product-level detection

- **Repodata caching** — Strategy for caching `repomd.xml` /
  `updateinfo.xml` / `primary.xml` (ETag, Last-Modified, incremental
  parsing) to avoid redundant downloads.
- **Backfill of pre-existing advisories** — Behavior when a new ticket is
  opened for a CVE for which an advisory already exists in the product
  repository (set `released_at` retroactively with a historical date, or
  ignore advisories older than the ticket).
- **Formal definition of "relevant advisory"** — Edge cases (e.g.,
  `<update status>` values other than `stable`, advisories with empty
  `<pkglist>`, retracted advisories) need formalization.

### Cross-cutting

- **Released advisory persistence** — Whether to store a reference to the
  advisory that caused the `released_at` to be set (e.g., a
  `released_advisory_id` field on `TicketPackageProduct` holding the
  `SUSE-SU-YYYY:NNNN` identifier) for traceability and UI display, or to
  rely solely on `released_at` plus the audit log.

### Dependencies on separate features

- **"Revisit" list** — Destination for tickets in the no-match flow.
  Separate feature spec.
- **Notifications** — Mechanism (in-app, email) for notifying the
  assignee in the no-match flow. Separate feature spec.
- **Admin "Sync diagnostics" page** — Destination for unrecognized title
  warnings (and, potentially, products without a configured update
  repository URL). Separate feature spec.
