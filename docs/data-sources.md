# External Data Sources

## Overview

Sentinel integrates with multiple external data sources — both public services
and SUSE-internal infrastructure — to ingest CVE data, track product
lifecycle information, detect security update releases, and coordinate the
patch management workflow. This document catalogs all known data sources,
including those not yet integrated but potentially useful in the future.

For details on how Sentinel architecturally integrates with each active source,
see `docs/architecture.md` and the relevant feature specifications in
`docs/features/`.

### Summary

| Source | Scope | Data Provided | Integration Status |
|---|---|---|---|
| NVD | Public | CVE data, CVSS scores, CPE matches | Active |
| MITRE CVE Services | Public | Early CVE assignments | Active |
| Red Hat Security Data | Public | CVSS assessments | Active |
| IBS | Internal | Source packages, builds, repos (SUSE commercial) | Active |
| OBS | Public | Source packages, builds, repos (openSUSE) | Not planned |
| IBS RabbitMQ | Internal | Real-time build and publish events | Active |
| OBS RabbitMQ | Public | Real-time build and publish events | Not planned |
| SMELT | Internal | Product catalog, package-codestream mapping | Active |
| AIMAAS | Internal | Product lifecycle dates, CVSS thresholds | Active |
| SUSE Active Directory | Internal | Employee identity, line manager, groups | Active |
| SUSE OpenLDAP | Internal | Employee identity, POSIX accounts | Not integrated |
| SUSE Bugzilla | Internal | Bug tracking, security issues | Reference only |
| CISA KEV | Public | Known exploited vulnerabilities catalog | Specified |
| EPSS | Public | Exploit probability scores | Planned |
| GHSA | Public | Security advisories, CVSS, CWE | Specified |
| Linux Kernel CVE | Public | Kernel CVE data, fix/introduce commits | Specified |
| OSV | Public | Aggregated vulnerability data | Specified |
| SMASH | Internal | Security update management (predecessor to Sentinel) | Not planned |
| PackTrack | Internal | Patch submission tracking for maintainers | Not integrated |
| embedded-code-wg-data | Internal | Embedded third-party library tracking per SUSE/openSUSE package | Not integrated |
| Package Server | Internal | Package search, embedded code lookup, build dependencies (REST API) | Not integrated |
| git.suse.de | Internal | Package sources for next-gen SUSE products | Not integrated |
| openQA | Internal/Public | Automated OS testing in the release pipeline | Not integrated |

---

## CVE and Vulnerability Data

### NVD (National Vulnerability Database)

The National Vulnerability Database is a public vulnerability repository
maintained by NIST (National Institute of Standards and Technology). It is
the most comprehensive public source for CVE data, providing enriched
vulnerability information including severity scores, affected product
configurations, and references to advisories and patches.

- **Relevant data**: CVE identifiers, descriptions, CVSS v3.1 scores
  (v4.0 support is being gradually added by NVD), both primary NVD
  assessments and secondary CNA assessments, CWE identifiers (weakness
  type classification from the `weaknesses` array), CPE-based affected
  product configurations with version ranges (extracted as structured
  affected version data), vulnerability status (e.g. Analyzed, Rejected),
  and reference links to advisories and patches
- **Access**: REST API v2 at `services.nvd.nist.gov/rest/json/cves/2.0`.
  Public access without authentication is rate-limited; an API key
  (free registration) provides higher rate limits. A companion Source API
  at `services.nvd.nist.gov/rest/json/source/2.0` resolves CNA identifiers
  to human-readable names
- **Integration status**: **Active**. Sentinel syncs CVE data every 6 hours
  via the `sync_nvd_cves` fetcher. NVD is also used for CVSS score
  ingestion, CWE extraction, affected version parsing, and on-demand
  single-CVE lookups
- **Documentation**: https://nvd.nist.gov/developers

### MITRE CVE Services

MITRE Corporation operates the CVE Program and assigns CVE identifiers. The
CVE Services API provides early access to newly assigned CVEs, often before
they are enriched by NVD with CVSS scores and CPE configurations. This makes
MITRE a valuable source for early awareness of new vulnerabilities.

- **Relevant data**: CVE identifiers, descriptions, CNA-provided metadata.
  Data is typically less enriched than NVD (no CVSS scores from MITRE
  itself, limited CPE data) but available earlier. Additionally, the CVE
  5.x record format includes an `adp` (Authorized Data Publisher) block
  in `containers.adp`. Multiple ADP providers may contribute containers.
  Sentinel extracts common data (affected versions, CVSS) from **all**
  ADP containers. The CISA ADP container (when present with
  `title: "CISA ADP Vulnrichment"`) additionally provides:
  - **SSVC** decision points (Exploitation, Automatable, Technical Impact)
    in `metrics[].other.type == "ssvc"`
  - **KEV** status (date added, reference URL) in
    `metrics[].other.type == "kev"`
  - **CWE** identifiers from CISA analysis
- **Access**: `cvelistV5` GitHub repository (bare clone + fetch). Public access
- **Integration status**: **Active**. Sentinel syncs every 6 hours via the
  `sync_mitre_cves` fetcher, with on-demand single-CVE fetch support.
  The fetcher extracts the CNA block (CVE core data), all ADP blocks
  (affected versions, CVSS), and CISA-specific enrichment (SSVC, KEV,
  CWE) when present
- **Documentation**: https://www.cve.org/,
  https://cveawg.mitre.org/api-docs/openapi.json

### Red Hat Security Data

Red Hat publishes its own CVSS assessments for CVEs affecting Red Hat
products. Since Red Hat Enterprise Linux and SUSE Linux Enterprise share a
common upstream heritage for many packages, Red Hat's severity assessments
provide a useful secondary perspective when evaluating vulnerabilities.

- **Relevant data**: CVSS v2.0 and v3.1 base scores and scoring vectors, CWE identifiers, reference URLs, source package names for CVEs
  affecting Red Hat products, CWE identifiers (weakness classification),
  and reference links (CVE references, KEV catalog, upstream commits)
- **Access**: REST API at
  `access.redhat.com/hydra/rest/securitydata/cve/{CVE-ID}.json`. Public
  access, no authentication required. Does not support incremental
  fetching — each CVE must be queried individually
- **Integration status**: **Active**. Sentinel syncs daily via the
  `sync_redhat_cves` fetcher, re-fetching CVSS data, CWE identifiers,
  and reference links for all active tickets
- **Documentation**:
  https://docs.redhat.com/en/documentation/red_hat_security_data_api/1.0/html-single/red_hat_security_data_api/index

### CISA KEV (Known Exploited Vulnerabilities)

The CISA Known Exploited Vulnerabilities catalog is a curated list of CVEs
with confirmed active exploitation in the wild, maintained by the
Cybersecurity and Infrastructure Security Agency (US). The catalog contains
approximately 1,200 CVEs and is updated almost daily. Presence in the KEV
catalog is a strong signal for prioritization — it indicates that the
vulnerability is being actively used by threat actors.

- **Relevant data**: CVE ID, date added to the KEV catalog, CWE
  classifications; reference URL constructed per-CVE
- **Access**: JSON feed at
  `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`.
  No authentication required, no significant rate limits. Single file
  (~1.5MB), complete download each sync
- **Integration status**: **Specified**. `sync_cisa_kev` fetcher.
  Schedule: 4x daily (`0 4,10,18,22 * * *`), aligned to CISA US Eastern
  business-hours publication window. Data is stored in the
  `CVEKEVEntry` table (date_added, reference_url) and `CVECWE` table
  (CWE classifications with `source="CISA KEV"`). KEV reference URLs
  are also stored as `TicketReference` entries
- **Documentation**:
  https://www.cisa.gov/known-exploited-vulnerabilities-catalog

### EPSS (Exploit Prediction Scoring System)

EPSS is a probabilistic scoring system developed by FIRST.org that
estimates the likelihood that a CVE will be exploited in the wild within
the next 30 days. It provides a score (0.0 to 1.0) and a percentile
ranking for each CVE. EPSS covers 200,000+ CVEs and is updated daily.
EPSS complements CVSS by adding an exploitation probability dimension —
CVSS measures severity, EPSS measures likelihood of exploitation.

- **Relevant data**: EPSS score (0.0-1.0), percentile ranking per CVE
- **Access**: REST API at `https://api.first.org/data/v1/epss`. Supports
  single-CVE queries and bulk queries with pagination. Supports date
  filtering for incremental sync. No authentication required. Also
  available as a bulk CSV download (~15MB compressed)
- **Integration status**: **Planned**. New `sync_epss_scores` fetcher. Schedule:
  TBD. Data is stored in a dedicated EPSS table linked to CVE records
- **Documentation**: https://www.first.org/epss/,
  https://www.first.org/epss/api

### GHSA (GitHub Security Advisories)

The GitHub Advisory Database contains security advisories for open source
packages, maintained by GitHub with community contributions. Each advisory
has a GHSA-ID and is typically associated with a CVE-ID. Advisories cover
packages across multiple ecosystems (npm, pip, Go, Maven, RubyGems, Cargo,
NuGet, etc.). While these ecosystems differ from RPM, SUSE packages
frequently wrap upstream packages from these ecosystems, making the version
information relevant for Vulnerability Analysts.

- **Relevant data**: CVSS scores (GitHub's own assessment, v3.x and
  v4.0), CWE identifiers, affected packages with precise version ranges
  across multiple ecosystems, reference links, GHSA-IDs (stored as
  `CVEExternalIdentifier`). GitHub is a CNA — may publish CVEs before
  other sources
- **Access**: REST API at `https://api.github.com/advisories`. Requires a
  GitHub personal access token (free). Supports incremental sync via the
  `modified` parameter (ISO 8601 date range syntax). Rate limit: 5,000
  requests/hour with token (60/hour without — insufficient for
  production). The advisory database is also available as a Git repository
  at `https://github.com/github/advisory-database.git` (not used by
  Sentinel — REST API chosen for `fetch_single(cve_id)` support)
- **Integration status**: **Planned**. `sync_ghsa_advisories` fetcher.
  Schedule: every 3 hours (`0 */3 * * *`). Discovery fetcher (can create
  tickets). CVSS scores stored as `CVECVSSAssessment` entries with
  `provider_name = "GitHub"`. GHSA-ID stored as `CVEExternalIdentifier`
  with `source = GHSA`. CWE identifiers, affected versions, resolved
  packages, and reference URLs stored in their respective tables
- **Documentation**:
  https://docs.github.com/en/code-security/security-advisories/working-with-global-security-advisories-from-the-github-advisory-database

### Linux Kernel CVE Feed

The Linux kernel project operates as a CVE Numbering Authority (CNA) since
February 2024, publishing kernel-specific CVE data in a dedicated Git
repository. This feed is particularly valuable for SUSE because the kernel
is one of the most critical maintained packages. Unlike generic CVE
sources, the kernel feed includes the exact Git commit hashes that
introduced the vulnerability and the commits that fix it, enabling
backport verification.

- **Relevant data**: CVE identifiers, CVSS scores (kernel CNA
  assessment), affected kernel version ranges, Git commit hashes for the
  introducing (offending) and fixing commits, reference links to kernel
  patches, affected source files (`programFiles`)
- **Access**: Git repository at
  `https://git.kernel.org/pub/scm/linux/security/vulns.git/`. Each CVE is
  a JSON file in CVE Record 5.1.1 format (published) or 5.0 format
  (rejected), organized by year. No authentication required. Sync via
  bare clone + fetch (NO `--filter=blob:none` — server does not
  advertise the `filter` capability)
- **Integration status**: **Active**. `sync_kernel_cves` fetcher, every
  3 hours. CVSS scores are stored as `CVECVSSAssessment` entries with
  `provider_name = "Linux"`. Fix/introduce commit hashes are stored as
  `CVEAffectedVersion` records with `version_type = "git"` (introducing
  commit in `version`, fixing commit in `version_end`). Affected kernel
  versions (semver blocks) and reference URLs are stored in their
  respective tables. `source_container = "cna"` (same as MITRE —
  content is identical by construction)
- **Documentation**: https://docs.kernel.org/process/cve.html

**Access note — Anubis bot protection**: the `git.kernel.org` web
interface (cgit) is protected by Anubis (proof-of-work anti-bot
system). Raw file access via HTTP (`/plain/` URLs) is blocked for
automated tools. Git protocol access (clone, fetch) is unaffected.
When investigation of repository content is needed, use a local bare
clone — do NOT attempt HTTP access to individual files.

**Clone command**: `git clone --bare --single-branch https://git.kernel.org/pub/scm/linux/security/vulns.git`

#### Repository Structure

```
vulns.git/cve/
├── published/YEAR/
│   ├── CVE-YEAR-ID             # Empty (0 bytes)
│   ├── CVE-YEAR-ID.json        # ★ Full CVE record (JSON 5.1.1) — PROCESSED by fetcher
│   ├── CVE-YEAR-ID.sha1        # Fixing commit SHA (redundant with JSON)
│   ├── CVE-YEAR-ID.mbox        # Email announcement format
│   ├── CVE-YEAR-ID.dyad        # Vulnerable:fixed version pairs (redundant with JSON affected[])
│   ├── CVE-YEAR-ID.vulnerable  # (optional) Introducing commit SHA (redundant with JSON)
│   ├── CVE-YEAR-ID.reference   # (optional) Additional URLs (redundant with JSON references[])
│   ├── CVE-YEAR-ID.cvss        # (optional) CVSS vector (redundant with JSON metrics[])
│   └── CVE-YEAR-ID.message     # (optional, 7 files) Description override
├── rejected/YEAR/
│   ├── CVE-YEAR-ID             # Empty
│   ├── CVE-YEAR-ID.json        # ★ CVE record (state field unreliable!) — PROCESSED by fetcher
│   ├── CVE-YEAR-ID.dyad        # (redundant)
│   ├── CVE-YEAR-ID.sha1
│   ├── CVE-YEAR-ID.mbox
│   └── CVE-YEAR-ID.mbox.rejected  # Rejection announcement
├── reserved/YEAR/              # Reserved CVE-IDs (no .json files, not processed)
└── returned/YEAR/              # Returned CVE-IDs (no .json files, not processed)
```

The fetcher processes **only `.json` files** from `cve/published/` and
`cve/rejected/`. All other file types are redundant with JSON content
and are ignored (see `docs/features/tickets/cve-sync-kernel.md` for the
full rationale).

#### Volume and Publishing Pattern

The kernel CNA publishes CVEs in **large batches** aligned with stable
kernel releases, followed by periods of inactivity. Observed 2026 data:

- Peak: 173 CVEs published on a single day (2026-04-25)
- Typical batch: 60-100 CVEs per release day
- Total published `.json` files: 12,118
- Total rejected `.json` files: 292
- Bare clone size: ~91 MB

### OSV (Open Source Vulnerabilities)

OSV is an aggregated vulnerability database operated by Google that unifies
advisories from 20+ databases (GitHub, PyPI, crates.io, Go, Debian,
Alpine, Linux kernel, and more) into a standardized format. It provides a
simple REST API that supports queries by CVE ID. The OSV fetcher is an
**enrichment fetcher** — it enriches CVEs already tracked by Sentinel with
additional metadata from ecosystem-specific advisory databases.

- **Relevant data**: GIT fix/introduce commit SHAs, ecosystem-specific
  affected version ranges (PyPI, npm, Go, crates.io, Maven, etc.),
  reference links with type tags (FIX, ADVISORY, REPORT, ARTICLE),
  package names for best-effort SMELT resolution, external identifiers
  (GHSA, PYSEC, RUSTSEC) via alias records, and related advisory
  identifiers (including SUSE-SU when available)
- **Access**: REST API at `https://api.osv.dev/v1/vulns/{id}`. No
  authentication required. No rate limits (confirmed in OSV docs/FAQ)
- **Integration status**: **Specified**. The `sync_osv_advisories`
  fetcher uses a three-phase per-CVE approach: (1) fetch CVE record for
  GIT ranges and references, (2) fetch alias records for ecosystem data,
  (3) fetch related records for distribution advisory references.
  Schedule: daily at 05:00 UTC. CVSS scores are explicitly NOT extracted
  (OSV provides no provider attribution; all CVSS data already covered by
  dedicated fetchers with explicit provenance)
- **Documentation**: https://osv.dev/,
  https://google.github.io/osv.dev/api/

---

## Build and Package Infrastructure

### IBS (Internal Build Service)

IBS is the internal instance of the Open Build Service operated by SUSE at
`build.suse.de`. It is used for building and maintaining packages for SUSE
commercial products (SLE, SLES, SLED, SUSE Linux Enterprise Micro, etc.).
Source packages for maintenance updates are maintained in codestream projects
(e.g., `SUSE:SLE-15-SP6:Update`), and released binaries are published to
product-specific update repositories.

IBS is the primary source for Sentinel's release detection: Sentinel queries IBS
to determine whether security fixes have landed in source codestreams and
whether update advisories have been published to product repositories.

- **Relevant data**: Source package revisions and MD5 checksums (for
  change detection), source diffs with embedded CVE and Bugzilla references
  (to confirm which vulnerabilities a commit addresses), build results,
  published repository metadata including `updateinfo.xml` (which
  contains advisory details with CVE references and release dates),
  and package bugowner roles (person or group responsible for package
  maintenance)
- **Access**: REST API at `api.suse.de` (HTTP Basic Auth or API tokens).
  Download server at `download.suse.de/ibs` for repository data. Key
  endpoints:
  - `GET /source/{project}?view=info` — package listing with `srcmd5`
    checksums
  - `POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1` —
    source diff with CVE/Bugzilla tracker references
  - `GET /search/owner?package={name}&filter=bugowner` — resolve
    effective bugowner of a package through the project hierarchy
  - `GET /person/{userid}` — user details (email, real name)
  - `GET /group/{group_name}` — group details (email, member list)
- **Integration status**: **Active**. Codestream-level release detection
  uses two complementary mechanisms: the `IBSEventConsumer` (real-time
  via IBS RabbitMQ, see `docs/features/integrations/ibs-rabbitmq-integration.md`) and
  the periodic `detect_ibs_track_releases` fetcher (catch-up every 24
  hours at 02:00 UTC). Product-level release detection
  (`detect_ibs_product_releases`) runs as a periodic `BaseFetcher` subclass.
  Package bugowner resolution uses the owner search, person, and group
  endpoints — see `docs/features/packages/package-bugowner.md`
- **Documentation**: https://build.suse.de (internal). The OBS API
  documentation at https://api.opensuse.org/apidocs/ applies to IBS as
  both run the same software
- **Source code verification**: The official OBS/IBS API documentation is
  often incomplete or outdated. When evaluating available endpoints,
  accepted parameters, response formats, or edge-case behaviors, always
  verify against the OBS source code repository
  (`openSUSE/open-build-service` on GitHub — particularly
  `src/api/app/controllers/` for endpoint logic and `src/api/config/routes.rb`
  for route definitions) rather than relying solely on the published docs
- **See also**: `docs/features/integrations/ibs-integration.md`,
  `docs/features/packages/package-model.md`,
  `docs/features/packages/ibs-track-release-detection.md`,
  `docs/features/packages/ibs-product-release-detection.md`,
  `docs/features/packages/package-bugowner.md`

### OBS (Open Build Service)

OBS is the public instance of the Open Build Service at
`build.opensuse.org`, used for building and maintaining packages for
openSUSE community distributions (Tumbleweed, Leap). It runs the same
software as IBS and exposes the same REST API, but operates independently
with separate projects, packages, and user accounts.

- **Relevant data**: Same types as IBS (source packages, build results,
  published repositories) but for openSUSE distributions
- **Access**: REST API at `api.opensuse.org`. Supports anonymous read
  access for public projects; write operations require authentication
- **Integration status**: **Not planned**. There is currently no intention
  to integrate openSUSE package tracking into Sentinel. This may be evaluated
  in the future if there is demand for tracking security updates across
  openSUSE distributions
- **Documentation**: https://openbuildservice.org/help/,
  https://api.opensuse.org/apidocs/

### IBS/OBS RabbitMQ Event Bus

Both IBS and OBS operate RabbitMQ message brokers that publish real-time
notifications of events occurring in the build service — package commits,
build completions, repository publications, and more. This event bus
provides an alternative to polling for detecting changes, enabling
near-real-time reactivity.

- **Relevant data**: Real-time events with JSON payloads, published to a
  `pubsub` topic exchange. Topics follow the format
  `SCOPE.APPLICATION.OBJECT.ACTION`. The IBS scope prefix is `suse`
  (e.g., `suse.obs.package.commit`); the OBS scope prefix is `opensuse`.
  The complete list of available event types is:

  **Package events:**
  - `*.obs.package.commit` — source package committed (payload includes
    `project`, `package`, `rev`, `srcmd5`, `files`, `user`). **Currently
    consumed** by Sentinel for codestream-level release detection
  - `*.obs.package.version_change` — package version changed (payload
    includes `project`, `package`, `oldversion`, `newversion`)
  - `*.obs.package.upstream_version_change` — upstream package version
    changed
  - `*.obs.package.create` — new package created in a project
  - `*.obs.package.update` — package metadata updated
  - `*.obs.package.delete` — package deleted from a project
  - `*.obs.package.undelete` — package restored after deletion
  - `*.obs.package.upload` — file uploaded to a package
  - `*.obs.package.branch_command` — package branched

  **Build events:**
  - `*.obs.package.build_success` — package build completed successfully
    (payload includes `project`, `package`, `repository`, `arch`,
    `srcmd5`)
  - `*.obs.package.build_fail` — package build failed (payload includes
    `project`, `package`, `repository`, `arch`, `srcmd5`)
  - `*.obs.package.build_unchanged` — package build completed with no
    changes in output

  **Repository events:**
  - `*.obs.repo.published` — repository published (payload includes
    `project`, `repo`, `buildid`)
  - `*.obs.repo.build_started` — repository build started
  - `*.obs.repo.build_finished` — repository build finished
  - `*.obs.repo.publish_state` — repository publish state changed
  - `*.obs.repo.container_published` — container image published

  **Request events (maintenance/submit requests):**
  - `*.obs.request.create` — new request created
  - `*.obs.request.change` — request modified
  - `*.obs.request.statechange` — request state changed (e.g., new →
    accepted → revoked)
  - `*.obs.request.delete` — request deleted
  - `*.obs.request.reviews_done` — all reviews for a request completed
  - `*.obs.request.review_changed` — individual review changed
  - `*.obs.request.review_wanted` — review requested

  **Project events:**
  - `*.obs.project.create` — new project created
  - `*.obs.project.update` — project metadata updated
  - `*.obs.project.update_config` — project build config updated
  - `*.obs.project.delete` — project deleted
  - `*.obs.project.undelete` — project restored after deletion

  **Service events:**
  - `*.obs.package.service_success` — source service completed
    successfully
  - `*.obs.package.service_fail` — source service failed

  **Comment events:**
  - `*.obs.comment.for_package` — comment on a package
  - `*.obs.comment.for_project` — comment on a project
  - `*.obs.comment.for_request` — comment on a request
  - `*.obs.comment.for_report` — comment on a report

  **Status check events:**
  - `*.obs.status_check.for_build` — status check for a build
  - `*.obs.status_check.for_published` — status check for published repo
  - `*.obs.status_check.for_request` — status check for a request

  **User/group events:**
  - `*.obs.group.added_user` — user added to a group
  - `*.obs.group.removed_user` — user removed from a group
  - `*.obs.relationship.create` — role relationship created
  - `*.obs.relationship.delete` — role relationship deleted
  - `*.obs.role.global_assigned` — global role assigned
  - `*.obs.token.membership_update` — token membership updated

  **Moderation events:**
  - `*.obs.report.for_comment` — report filed for a comment
  - `*.obs.report.for_package` — report filed for a package
  - `*.obs.report.for_project` — report filed for a project
  - `*.obs.report.for_request` — report filed for a request
  - `*.obs.report.for_user` — report filed for a user
  - `*.obs.decision.cleared` — moderation decision cleared
  - `*.obs.decision.favored` — moderation decision favored
  - `*.obs.appeal.created` — appeal created

  **Workflow events:**
  - `*.obs.workflow_run.fail` — workflow run failed
- **Access**:
  - IBS: `amqps://suse:suse@rabbit.suse.de`
  - OBS: `amqps://opensuse:opensuse@rabbit.opensuse.org`
  - Both use the exchange named `pubsub` (type: topic, durable). Consumers
    must declare an exclusive queue, bind it to the exchange with a
    routing key filter, and consume messages. The exchange must be declared
    with `passive=True` and `durable=True` (consumers cannot create it)
- **Integration status**: **Active**. Sentinel consumes
  `suse.obs.package.commit` events from IBS for near-real-time
  codestream-level release detection. The periodic polling fetcher
  (`detect_ibs_track_releases`, every 24 hours at 02:00 UTC) serves as
  a catch-up mechanism for events missed during consumer downtime, since
  queues are exclusive and transient. See
  `docs/features/integrations/ibs-rabbitmq-integration.md` for the full specification
- **Documentation**: https://rabbit.opensuse.org (OBS),
  https://github.com/openSUSE/suse_msg/blob/master/amqp_infra.md,
  OBS event types: https://github.com/openSUSE/open-build-service/tree/master/src/api/app/models/event

### embedded-code-wg-data (Embedded Code Tracking)

The Embedded Code Working Group data repository is a Git-based dataset
maintained by the SUSE Security team. It contains structured records
produced by automated scanners that analyze source packages in IBS/OBS to
determine which third-party libraries are bundled (embedded) within each
package. This data is critical for security analysis because embedded
libraries do not receive updates through the system package manager — when
a CVE affects `libwebp`, packages that bundle their own copy of `libwebp`
are also affected but will not appear in standard CPE-based or
SMELT-based package resolution.

Multiple language-specific scanners contribute data:

| Language | Scanner | Method |
|---|---|---|
| C/C++ | `idlib` + `osc-prep-source` | Source code analysis of unpacked sources |
| Go | `dep-scanner` | Go module dependency extraction |
| JavaScript | `dep-scanner` | Node.js dependency extraction |
| Ruby | `dep-scanner` | Gemfile/gemspec analysis |
| Rust | `dep-scanner` | Cargo.lock analysis |
| Other | spec file parser | `Provides: ?bundled(...)` annotations in RPM spec files |

- **Relevant data**: CSV records mapping each SUSE/openSUSE source package
  to the third-party libraries it embeds, with version information where
  detectable. Data format: `PROJECT,PACKAGE,REVISION,PROVIDES_NAME,PROVIDES_VERSION`.
  Example: `openSUSE:Factory,389-ds,64,byteorder,1.4.3`. Covers all
  actively maintained codestream projects across SLE, SLFO, and openSUSE
  distributions. The dataset contains approximately 1.4 million embedding
  records across ~58,500 packages
- **Access**: Git repository at
  `https://gitlab.suse.de/security/embedded-code-wg-data`. Branch:
  `master`. No authentication required for clone (internal network).
  Data files are plain CSV with `.txt` extension, organized in
  subdirectories. The repository is updated regularly by automated
  scanner pipelines (~1,009 commits since December 2022)
- **Integration status**: **Not integrated**. The data is currently
  consumed and exposed via Package Server (see below), which provides a
  REST API for querying. Direct Git-based consumption is also possible
  for batch processing. Potential Sentinel use cases:
  - **Embedded vulnerability propagation**: given a CVE affecting an
    upstream library (e.g., `curl`, `libpng`, `openssl`), identify all
    SUSE packages that bundle a vulnerable version of that library
  - **Package resolution enhancement**: supplement CPE-based and
    SMELT-based package matching with embedded code data for broader
    coverage
- **Documentation**: README in repository
- **Source code**: https://gitlab.suse.de/security/embedded-code-wg-data
- **Related tooling**:
  - `dep-scanner`: https://gitlab.suse.de/security/dep-scanner
  - `idlib`: https://github.com/wfrisch/idlib
  - `psc` (CLI client): https://gitlab.suse.de/security/psc
  - `imtools`: https://gitlab.suse.de/security/imtools

### Package Server

Package Server is an internal REST API service developed by the SUSE
Security team that aggregates package data from multiple sources into a
unified searchable index. It is used by Vulnerability Analysts to look up
packages, discover embedded (bundled) third-party libraries, check build
dependencies, and filter results by version. The service consolidates data
that would otherwise require querying IBS, OBS, SMELT, and the
embedded-code-wg-data repository individually.

The server loads data from all configured sources at startup and refreshes
periodically (default: every 24 hours). It exposes an OpenAPI 3.1.0
specification at `/openapi.json` and interactive documentation at `/docs`.

#### Aggregated Sources

| Source Type | Description |
|---|---|
| `embedded` | embedded-code-wg-data Git repository (see above) — provides the embedded library mapping |
| `channels` | `SUSE:Channels` IBS project — channel metadata (`_channel` files) |
| `smelt` | SMELT `/maintainedpackage/` API — maintained package catalog |
| `buildfiles` | OBS/IBS `_buildinfo` and `_buildenv` files — build-time dependency data |
| `project` | Direct IBS/OBS project tracking (e.g., `SUSE:SLE-15-SP6:Update`, `SUSE:SLFO:Main`, `openSUSE:Factory`) |

- **Relevant data**: Unified package index with package names, binary
  names, embedded third-party library names and versions, build
  dependencies, and support status. Key capabilities:
  - Search packages by name (substring, case-insensitive)
  - Find packages that embed a specific library at specific versions
  - Query build dependency relationships (which packages require a given
    package)
  - Filter by version using comparison operators (`=`, `!=`, `<`, `<=`,
    `>`, `>=`) with support for combining multiple constraints
  - Restrict results to trusted sources only
  - Retrieve full package details including embedded libraries, binaries,
    support status, and data provenance
- **Access**: REST API. Live instance at
  `https://sec-gsonnu.suse.de/package-server`. No authentication required
  (authentication is supported but disabled on the live instance). Key
  endpoints:
  - `GET /package/{query}` — search packages by name
  - `GET /binary/{query}` — find packages containing matching binaries
  - `GET /embeds/{query}` — find packages embedding a given library
    (supports `?version=<=X.Y.Z` filtering)
  - `GET /search/{query}` — generic search across packages, binaries,
    and embedded libraries (configurable via `packages`, `binaries`,
    `embedded` parameters)
  - `GET /show/{query}` — detailed package information (binaries,
    embedded libs, support status, sources, build dependencies)
  - `GET /requires/{query}` — packages with a build dependency on the
    given package
  - `GET /support/{query}` — support status (regular, ltss, reactive,
    unknown)
  - `GET /stats` — database statistics and last update timestamp
  - `GET /config` — current server configuration and source list
  - `GET /openapi.json` — OpenAPI 3.1.0 specification
- **Scale** (live instance, as observed): ~58,500 packages, ~120,000
  binaries, ~1.4 million embedded package records, ~3.2 million build
  dependency relationships across 80+ IBS/OBS projects. Version 2.2.1
- **Integration status**: **Not integrated**. Potential Sentinel use cases:
  - **Embedded vulnerability propagation**: when a CVE affects an
    upstream library, query `/embeds/{lib}?version=<=<vulnerable_version>&trusted_only=true`
    to find all SUSE packages bundling vulnerable versions. This covers
    cases invisible to CPE-based and SMELT-based resolution (e.g., a
    `libcurl` CVE also affects `MozillaFirefox`, `git`, `nodejs20` which
    bundle their own copies)
  - **Alternative package name resolution**: when CPE mapping fails,
    `/package/{name}?trusted_only=true` can serve as a fallback to
    locate packages across codestreams
  - **Build dependency blast radius**: `/requires/{pkg}` reveals which
    packages depend on a vulnerable package at build time, useful for
    assessing indirect impact
  - **Version-filtered triage**: the version comparison operators allow
    precise identification of packages at vulnerable versions without
    client-side filtering
- **Documentation**: OpenAPI spec at
  `https://sec-gsonnu.suse.de/package-server/openapi.json`, interactive
  docs at `https://sec-gsonnu.suse.de/package-server/docs`
- **Source code**: https://gitlab.suse.de/security/package-server
- **See also**: embedded-code-wg-data (above) — primary source for
  embedded library data

### git.suse.de (SUSE Source Management)

`git.suse.de` (also accessible as `src.suse.de`) is a Gitea-based source
management platform for next-generation SUSE products. These products follow
a new development workflow that does not use the traditional maintenance
update (MU) process via IBS. Instead, package sources are managed directly
in Git repositories, organized by product and source pool.

- **Relevant data**: Package source repositories for upcoming SUSE
  products. The platform organizes content into product-specific groups
  (visible at `src.suse.de/products`) and a shared source pool
  (`src.suse.de/pool/`)
- **Access**: Web UI and Git over HTTPS at `src.suse.de`. Authentication
  via SUSE SSO (SAML). Gitea provides a REST API for repository and
  organization management
- **Integration status**: **Not integrated**. Integration for tracking
  security updates in these next-generation products will be evaluated in
  the future as the new workflow matures and the products enter
  maintenance phases. The tracking mechanism will likely differ
  significantly from the IBS-based approach due to the different update
  workflow
- **Documentation**: https://src.suse.de/products,
  https://src.suse.de/pool/

---

## Product and Lifecycle Data

### SMELT

SMELT is an internal SUSE aggregator service that consolidates data from
IBS, channel configuration files, and other sources into a unified view of
the SUSE product and package landscape. It serves as the authoritative
source of truth for which products are currently maintained, which packages
belong to which codestreams, and which repositories serve each product.

- **Relevant data**: Product catalog (name, version, CPE identifier,
  associated repository project names) and per-package maintenance
  information (which codestreams contain a given package and which target
  repositories it is published to)
- **Access**: REST API at `smelt.suse.de/api`. Key endpoints:
  - `GET /api/v1/basic/products/` (paginated) — product listing
  - `GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1`
    (paginated) — codestream and repository mapping for a package
- **Integration status**: **Active**. Sentinel periodically syncs the product
  catalog (`sync_smelt_products` fetcher) and queries package maintenance
  information on demand when adding packages to tickets. CPE identifiers
  from SMELT are the primary join key between Sentinel's product records and
  AIMAAS lifecycle data
- **Documentation**: https://smelt.suse.de (internal)
- **See also**: `docs/features/packages/product-catalog.md` (product
  sync), `docs/features/packages/package-model.md` (package query)

### AIMAAS

AIMAAS is an internal SUSE service that manages product lifecycle metadata
and CVSS score thresholds. It provides the authoritative dates for when
products enter and exit various support phases (general support, LTSS,
ESPOS, Reactive LTSS), as well as the minimum CVSS score thresholds that
determine whether a product in extended support phases is eligible for a
security update.

- **Relevant data**: Product lifecycle dates (`fcs`, `end_of_gs`,
  `end_of_ltss`, `end_of_espos`, `end_of_reactive_ltss`) and CVSS
  thresholds per product (approximately 24 entries for products in
  LTSS/ESPOS phases). Products are matched to Sentinel's local records via
  CPE identifiers (identical between SMELT and AIMAAS)
- **Access**: REST API at `aimaas.suse.de/api`. Key endpoints:
  - `GET /api/entity/products/{slug}` — individual product lifecycle dates
  - `GET /api/entity/cvss-threshold` (paginated) — CVSS thresholds
- **Integration status**: **Active**. Sentinel periodically syncs lifecycle
  dates (`sync_aimaas_lifecycle` fetcher) and CVSS thresholds
  (`sync_aimaas_thresholds` fetcher). When thresholds or lifecycle dates
  change, Sentinel re-evaluates eligibility for all active tickets
  referencing the affected products
- **Documentation**: https://aimaas.suse.de (internal)
- **See also**: `docs/features/packages/product-catalog.md`,
  `docs/features/tickets/cvss-scoring.md`

---

## Identity and Directory Services

### SUSE Active Directory (`pan.suse.de`)

SUSE operates a corporate Active Directory environment that serves as the
authoritative source for employee identity data across the organization.
The LDAP endpoint at `pan.suse.de` is an **OpenLDAP proxy** (using the
`back-ldap` backend with a `pcache` overlay) that forwards queries to the
underlying Microsoft Active Directory domain controllers. From a client
perspective the distinction is mostly transparent — the proxy speaks
standard LDAPv3 and relays AD responses including AD-specific attributes
(`sAMAccountName`, `objectGUID`, `MEMBEROF`, etc.) — but it has
implications for schema discovery, error diagnostics, caching behavior,
and TLS termination (see `docs/features/identity/ad-integration.md`,
Implementation Notes).

The directory contains records for all active SUSE employees with detailed
profile information including organizational hierarchy, group memberships,
and employment status.

Active Directory is the only directory service that provides the correct
**direct line manager** for each employee (single-value `manager`
attribute pointing to the manager's DN). This makes it the preferred
source for identity data in Sentinel over the OpenLDAP instance at
`ldap.suse.de`.

- **Relevant data**: Employee identity (`sAMAccountName`, `cn`, `mail`),
  direct line manager (`manager` — single-value DN reference), employment
  status (`EMPLOYEESTATUS`), AD group memberships (`MEMBEROF`), job title
  (`title`), office location (`SITELOCATION`), country (`co`), employee
  start date (`EMPLOYEESTARTDATE`), employee ID (`EMPLOYEEID`)
- **Access**: LDAPS protocol at `ldaps://pan.suse.de` (port 636, TLS).
  Supports anonymous bind — no credentials required for read access.
  Server certificate validated against SUSE Trust Root CA
  (`certs/SUSE_Trust_Root.crt`). Base DN:
  `OU=User accounts,DC=corp,DC=suse,DC=com`. Approximately 913 active
  employee records (as of 2026). Security groups are located under
  `OU=Security Groups,OU=Groups,DC=corp,DC=suse,DC=com`
- **Integration status**: **Active**. Sentinel syncs employee data daily via
  the `sync_ldap_directory` fetcher. Data consumed: `sAMAccountName`,
  `cn`, `mail`, `manager`, `EMPLOYEESTATUS`, `MEMBEROF` (transient, for
  role mapping). See `docs/features/identity/ad-integration.md` for the full
  specification
- **Proxy caching**: the `pcache` overlay on `pan.suse.de` caches query
  results transparently. For the daily background sync this is harmless —
  staleness within a 24-hour window is acceptable. However, on-demand
  queries (e.g., verifying whether an AD group exists) may receive cached
  results rather than real-time data from AD. In practice this means
  recently created AD groups or recently modified group memberships may not
  be immediately visible. There is no reliable client-side mechanism to
  force a cache miss through an LDAP proxy — callers should be aware of
  this limitation during development and debugging
- **Documentation**: Internal — no public documentation available

### SUSE OpenLDAP (`ldap.suse.de`)

SUSE also operates an OpenLDAP instance at `ldap.suse.de` with a custom
schema (`suseudbobject`). This directory contains employee identity data,
POSIX account information, and department structures. It predates the
Active Directory deployment and uses a different schema with
SUSE-specific attributes.

The OpenLDAP instance has a **multi-value `manager` attribute** that
contains the entire managerial chain flattened into a list of DNs (from
direct manager up to the CEO). This makes it impossible to determine the
direct line manager from this field alone — all employees under the same
organizational branch share the same `manager` values. For this reason,
Sentinel uses Active Directory instead.

- **Relevant data**: Employee identity (`uid`, `cn`, `mail`,
  `suseudbemailalias` for `.de` email), SUSE-specific identifiers
  (`suseid`), managerial chain (`manager` — multi-value, entire hierarchy
  flattened), employment type (`employeeType`), office location (`l`,
  `roomNumber`, `postalAddress`), department structure
  (`ou=departments`), POSIX accounts (`ou=accounts` — `uidNumber`,
  `gidNumber`, `homeDirectory`, `loginShell`)
- **Access**: LDAP protocol at `ldap://ldap.suse.de` (port 389). Supports
  anonymous bind — no credentials required for read access. Base DN:
  `dc=suse,dc=de`. Organization units: `ou=people` (employee profiles),
  `ou=accounts` (POSIX accounts), `ou=departments` (department
  structure), `ou=mounts`, `ou=Netgroup`
- **Integration status**: **Not integrated**. Documented here for
  reference. Sentinel uses Active Directory (`pan.suse.de`) instead because
  it provides the correct direct line manager relationship. The `.de`
  email alias available on OpenLDAP (`suseudbemailalias`) is not imported
- **Documentation**: Internal — no public documentation available

---

## Related Internal Tools

### SMASH (SUSE Maintenance And Security Helper)

SMASH is the predecessor platform to Sentinel. It currently fulfills the same
role that Sentinel is being built to replace: managing and tracking security
updates across SUSE's maintained product portfolio. Sentinel's design is
directly informed by lessons learned from operating SMASH.

SMASH has a rich fetcher/worker architecture with integrations to many of
the same sources Sentinel uses (NVD, MITRE, Bugzilla, IBS, SMELT, AIMAAS, Red
Hat) as well as additional sources that Sentinel does not yet integrate
(Google Project Zero, ZDI, GitHub Security Advisories, Linux Kernel CVE
feeds, Oracle CSAF, Amazon ALAS, Debian Security, oss-security mailing
list, IBM Java advisories, and Jira/ECO). SMASH's `TrackedReleaseFetcher`
— which uses MD5 checksum comparison against IBS source info to detect
codestream-level releases — directly inspired Sentinel's equivalent mechanism.

SMASH manages "issues" (equivalent to Sentinel's tickets) through a workflow
of states: New, Analysis, Analyzed/Pending, Running, Resolved. Each issue
tracks affected packages across codestreams and products, CVSS scores from
multiple providers, and references to external bug trackers and advisories.

- **Relevant data**: Issue tracking, affected software per codestream and
  product, CVSS assessments, audit logs, embargoed bug tracking,
  maintenance update coordination
- **Access**: Web UI and REST API at `smash.suse.de`. API authentication
  via personal tokens. Endpoints include `/api/issues/`,
  `/api/embargoed-bugs/`, `/api2/issues/`, `/api2/cvss/`, and more
- **Integration status**: **Not integrated**. Sentinel is designed as SMASH's
  successor, not as an integration partner. However, a data migration path
  from SMASH to Sentinel may be needed during the transition period
- **Documentation**: https://tools.io.suse.de/smash/
- **Source code**: https://gitlab.suse.de/tools/smash

### PackTrack

PackTrack is a dashboard tool designed to simplify packaging work for SUSE
package maintainers. It serves as a centralized hub where packagers can
monitor their packages, track bug reports, and manage OBS requests. PackTrack
aggregates data from OBS, Bugzilla, SMELT, and SMASH, and notably includes
its own RabbitMQ consumer for receiving real-time OBS events.

While PackTrack serves a different audience (package maintainers rather than
security analysts), it operates in the same update pipeline and tracks
complementary information — specifically the patch submission and build
status from the maintainer's perspective.

- **Relevant data**: Patch submission tracking, build status per package
  and codestream, bug associations, OBS request state. Data model includes
  bugs, packages, projects, and software entities
- **Access**: Web UI at `packtrack.suse.cz`. REST API v1 with
  documentation at `packtrack.suse.cz/api/v1/docs`. Authentication via
  SUSE SSO
- **Integration status**: **Not integrated**. PackTrack serves a different
  audience (package maintainers) but operates in the same update pipeline.
  Its API could potentially be useful for cross-referencing patch
  submission status in the future
- **Documentation**: https://packaging.io.suse.de/packtrack/
- **API documentation**: https://packtrack.suse.cz/api/v1/docs
- **Source code**: https://gitlab.suse.de/packaging/packtrack/

### openQA

openQA is an automated testing tool for operating systems. It creates
virtual machines, performs installation and usage test sequences, and
verifies expected behavior through screenshot matching ("needles") and
serial console output analysis. In the SUSE/openSUSE ecosystem, openQA
is a critical part of the release pipeline: maintenance updates are tested
by openQA before they are published to customers.

While openQA is not a direct data source for Sentinel's core workflow, it
occupies an important position in the update release pipeline between "fix
submitted to IBS" and "update published to customers." openQA can also
emit AMQP events to RabbitMQ, meaning its test results could theoretically
be consumed in real time.

- **Relevant data**: Test results for builds and maintenance updates
  (pass/fail per test suite), test job status and history
- **Access**:
  - SUSE internal: `openqa.suse.de`
  - openSUSE public: `openqa.opensuse.org`
  - REST API for job management, results querying, and test triggering.
    AMQP event emission is configurable for publishing test completion
    events to RabbitMQ
- **Integration status**: **Not integrated**. Mentioned here for
  completeness as part of the release pipeline context. openQA sits
  between the build phase (tracked by Sentinel via IBS) and the publication
  phase (tracked by Sentinel via `updateinfo.xml`)
- **Documentation**: https://open.qa/docs/,
  https://openqa.suse.de (internal)

---

## Reference Sources

### SUSE Bugzilla

SUSE's Bugzilla instance is the primary bug tracking system for SUSE
products. Two web interfaces exist — `bugzilla.suse.com` (primary, for SUSE
products) and `bugzilla.opensuse.org` (for openSUSE) — but they share the
same underlying database. Sentinel always references `bugzilla.suse.com`
regardless of which interface was originally used.

Bugzilla bugs frequently appear in the security update workflow: a Bugzilla
bug is typically opened for each CVE that affects SUSE products, and IBS
source commits reference these bugs via tracker annotations in the commit
metadata.

Multiple historical prefixes exist for Bugzilla IDs due to SUSE's corporate
history:

- `bnc#` — bugzilla.novell.com (legacy, from when SUSE was owned by Novell)
- `bsc#` — bugzilla.suse.com (current canonical form)
- `boo#` — bugzilla.opensuse.org

All three prefixes refer to the same database and the same bug IDs. Sentinel
normalizes all forms to the canonical `bsc#` prefix.

- **Relevant data**: Bug reports, security issue tracking, embargo status,
  CVE-to-bug associations. IBS source diffs include Bugzilla tracker
  references (with tracker type `bnc`) alongside CVE references
- **Access**: Web UI at `bugzilla.suse.com`. Bugzilla REST API and
  XML-RPC API available. SMASH integrates deeply with Bugzilla for issue
  creation, status tracking, and embargo detection
- **Integration status**: **Reference only**. Bugzilla IDs appear in IBS
  source diffs and can be manually linked to tickets via the
  `TicketReference` system. There is no automated Bugzilla sync in Sentinel.
  SMASH's extensive Bugzilla integration (fetchers for recent bugs,
  foreign bugs, CVE alias correction, CVSS marking, and reopen detection)
  provides a reference for what a deeper integration could look like
- **Documentation**: https://bugzilla.suse.com

---

## Fetcher Registry

All `BaseFetcher` subclasses are automatically registered in the fetcher
registry. The table below lists all fetchers — both active and planned —
with their schedule, authentication requirements, rate limits, and data
ingested. See `docs/features/platform/fetcher-infrastructure.md` for infrastructure
details.

**Spec Status** indicates how completely the fetcher is specified in the
feature documentation (not its implementation status):

| Status | Meaning |
|--------|---------|
| Complete | All required sections present: properties table, algorithm, error handling, metrics |
| Partial | Fetcher section exists but one or more required sections are missing or stubbed |
| TBD | No dedicated fetcher section exists, or the entire section is placeholder |

| Fetcher | Source | Schedule | Auth | Rate Limits | Data Ingested | Spec | Spec Status |
|---------|--------|----------|------|-------------|---------------|------|-------------|
| `sync_nvd_cves` | NVD | Every 6 hours | API key (free, optional) | Without key: 5 req/30s; with key: 50 req/30s | CVE records, CVSS (NVD Primary + CNA Secondary), CWE, CPE applicability statements, references | [cve-sync-nvd.md](features/tickets/cve-sync-nvd.md#fetcher-definition) | Complete |
| `sync_mitre_cves` | MITRE cvelistV5 (Git) | Every 6 hours | None | None (bare clone + fetch) | CVE records, all ADP data (affected versions, CVSS), CISA-specific (SSVC, KEV, CWE), references | [cve-sync-mitre.md](features/tickets/cve-sync-mitre.md#fetcher-definition) | Complete |
| `sync_redhat_cves` | Red Hat Security Data | Daily at 03:00 UTC | None | Undocumented; Sentinel uses 2s delay between requests | CVSS Red Hat, CWE, references, best-effort package names | [cve-sync-redhat.md](features/tickets/cve-sync-redhat.md#fetcher-definition) | Complete |
| `sync_smelt_products` | SMELT | TBD | TBD (internal) | N/A (internal) | Product catalog (name, version, CPE, repositories) | [product-catalog.md](features/packages/product-catalog.md#fetcher-sync_smelt_products) | TBD |
| `sync_aimaas_lifecycle` | AIMAAS | TBD | TBD (internal) | N/A (internal) | Product lifecycle dates | [product-catalog.md](features/packages/product-catalog.md#fetcher-sync_aimaas_lifecycle) | TBD |
| `sync_aimaas_thresholds` | AIMAAS | TBD | TBD (internal) | N/A (internal) | CVSS thresholds per product | [product-catalog.md](features/packages/product-catalog.md#fetcher-sync_aimaas_thresholds) | TBD |
| `detect_ibs_track_releases` | IBS | Daily at 02:00 UTC | HTTP Basic / API token (internal) | N/A (internal) | Codestream-level release detection (MD5 checksums) | [ibs-track-release-detection.md](features/packages/ibs-track-release-detection.md#fetcher-detect_ibs_track_releases) | Partial |
| `detect_ibs_product_releases` | IBS | TBD | HTTP Basic / API token (internal) | N/A (internal) | Product-level release detection (updateinfo.xml) | [ibs-product-release-detection.md](features/packages/ibs-product-release-detection.md#fetcher-detect_ibs_product_releases) | Partial |
| `sync_ibs_bugowners` | IBS | Every 14 days at 03:00 UTC | HTTP Basic / API token (internal) | Admin-configurable via `FetcherConfig.rate_limit` | Package bugowner cache maintenance (cleanup, update, repair) | [package-bugowner.md](features/packages/package-bugowner.md#fetcher-properties) | Partial |
| `sync_ldap_directory` | SUSE Active Directory | Daily at 04:00 UTC | None (anonymous bind) | N/A (internal) | Employee identity, line manager, group memberships for role mapping | [ad-integration.md](features/identity/ad-integration.md#fetcher-sync_ldap_directory) | Partial |
| `evaluate_lifecycle_transitions` | Local (no external source) | Daily at 04:00 UTC | N/A | N/A | Lifecycle phase evaluation and ticket re-evaluation for products in Reactive LTSS or EOL | [product-lifecycle-transitions.md](features/packages/product-lifecycle-transitions.md#fetcher-evaluate_lifecycle_transitions) | Partial |
| `sync_ibs_requests` | IBS | Daily at 02:30 UTC | HTTP Basic / API token (internal) | N/A (internal) | IBS submission request and release request tracking | [ibs-submission-tracking.md](features/packages/ibs-submission-tracking.md#fetcher-sync_ibs_requests) | Partial |
| `sync_cisa_kev` | CISA KEV | 4x daily (`0 4,10,18,22 * * *`) | None | None (single JSON file) | KEV date_added, reference_url, CWE classifications | [cve-sync-kev.md](features/tickets/cve-sync-kev.md#fetcher-definition) | Complete |
| `sync_epss_scores` | FIRST.org EPSS | TBD | None | None known | EPSS score + percentile per CVE | [cve-sync-epss.md](features/tickets/cve-sync-epss.md#fetcher-definition) | TBD |
| `sync_ghsa_advisories` | GitHub Advisory DB | Every 3 hours (`0 */3 * * *`) | GitHub token (free) | 5,000 req/hour with token | CVSS GitHub (v3.x + v4.0, `provider_name = "GitHub"`), GHSA-ID (as CVEExternalIdentifier), CWE, affected versions (multi-ecosystem, `source_container = "ghsa"`), resolved packages (best-effort SMELT), references | [cve-sync-ghsa.md](features/tickets/cve-sync-ghsa.md#fetcher-definition) | Complete |
| `sync_kernel_cves` | Linux Kernel CNA | Every 3 hours (`0 */3 * * *`) | None | None (bare clone + fetch) | CVSS kernel (`provider_name = "Linux"`), fix/introduce commits (as CVEAffectedVersion with version_type=git), affected kernel versions (semver), programFiles, references. Sets `resolved_packages = ["kernel-source"]` for direct package resolution. `source_container = "cna"` | [cve-sync-kernel.md](features/tickets/cve-sync-kernel.md#fetcher-definition) | Complete |
| `sync_osv_advisories` | OSV (osv.dev) | Daily at 05:00 UTC (`0 5 * * *`) | None | None (no rate limits) | GIT fix/introduce commits (CVEAffectedVersion), ecosystem affected versions (CVEAffectedVersion with ecosystem), references (TicketReference), external identifiers (GHSA/PYSEC/RUSTSEC), resolved packages (best-effort SMELT). `source_container = "osv"` | [cve-sync-osv.md](features/tickets/cve-sync-osv.md#fetcher-definition) | Complete |

Note: `IBSEventConsumer` (real-time codestream release detection via IBS
RabbitMQ) is a continuous service, not a `BaseFetcher` subclass. See
`docs/features/integrations/ibs-rabbitmq-integration.md`.

### CVE Enrichment Data Structures

The following tables store CVE enrichment data from multiple sources.
All are linked to the `CVE` table via a `cve_id` foreign key. Full
schema details are in `docs/data-model.md`.

| Table | Summary | Populated By |
|-------|---------|--------------|
| `CVEAffectedVersion` | Affected product/version data from CVE JSON 5.x `affected[]` arrays. Also stores kernel fix/introduce commits (`version_type = "git"`). CPE and vendor:product data from these records is used for best-effort package resolution in Phase 2 (see `docs/features/tickets/cve-service.md`) | `sync_mitre_cves`, `sync_ghsa_advisories`, `sync_kernel_cves`, `sync_osv_advisories` |
| `CVECWE` | CWE identifiers with multi-provider tracking | `sync_nvd_cves`, `sync_mitre_cves`, `sync_redhat_cves`, `sync_ghsa_advisories`, `sync_cisa_kev` |
| `CVESSVCAssessment` | CISA SSVC decision points (1:1 with CVE) | `sync_mitre_cves` (ADP block) |
| `CVEKEVEntry` | CISA KEV catalog data (1:1 with CVE) | `sync_cisa_kev`, `sync_mitre_cves` (ADP block) |
| `CVEEPSSScore` | FIRST EPSS score snapshot (1:1 with CVE, overwritten daily) | `sync_epss_scores` |
| `CVEExternalIdentifier` | External vulnerability identifiers (e.g., GHSA-ID, PYSEC-ID, RUSTSEC-ID) | `sync_ghsa_advisories`, `sync_osv_advisories` |
