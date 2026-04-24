# External Data Sources

## Overview

STAMP integrates with multiple external data sources — both public services
and SUSE-internal infrastructure — to ingest CVE data, track product
lifecycle information, detect security update releases, and coordinate the
patch management workflow. This document catalogs all known data sources,
including those not yet integrated but potentially useful in the future.

For details on how STAMP architecturally integrates with each active source,
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
| SUSE Bugzilla | Internal | Bug tracking, security issues | Reference only |
| CISA KEV | Public | Known exploited vulnerabilities catalog | Planned |
| EPSS | Public | Exploit probability scores | Planned |
| GHSA | Public | Security advisories, CVSS, CWE | Planned |
| Linux Kernel CVE | Public | Kernel CVE data, fix/introduce commits | Planned |
| OSV | Public | Aggregated vulnerability data | Planned |
| SMASH | Internal | Security update management (predecessor to STAMP) | Not planned |
| PackTrack | Internal | Patch submission tracking for maintainers | Not integrated |
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
- **Integration status**: **Active**. STAMP syncs CVE data every 6 hours
  via the `sync_cves_nvd` fetcher. NVD is also used for CVSS score
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
  in `containers.adp`. When present with `title: "CISA ADP Vulnrichment"`,
  this block provides CISA enrichment data:
  - **SSVC** decision points (Exploitation, Automatable, Technical Impact)
    in `metrics[].other.type == "ssvc"`
  - **KEV** status (date added, reference URL) in
    `metrics[].other.type == "kev"`
  - **CVSS** scores from CISA (stored as provider `CISA-ADP`)
  - **CWE** identifiers from CISA analysis
  - **Affected product** data (CPE, version ranges)
- **Access**: CVE Services REST API. Public access
- **Integration status**: **Active**. STAMP syncs every 6 hours via the
  `sync_cves_mitre` fetcher, with on-demand single-CVE fetch support.
  The fetcher extracts both the CNA block (CVE core data) and the CISA
  ADP block (SSVC, KEV, CVSS, CWE, affected versions) when present
- **Documentation**: https://www.cve.org/,
  https://cveawg.mitre.org/api-docs/openapi.json

### Red Hat Security Data

Red Hat publishes its own CVSS assessments for CVEs affecting Red Hat
products. Since Red Hat Enterprise Linux and SUSE Linux Enterprise share a
common upstream heritage for many packages, Red Hat's severity assessments
provide a useful secondary perspective when evaluating vulnerabilities.

- **Relevant data**: CVSS v3.1 base scores and scoring vectors for CVEs
  affecting Red Hat products, CWE identifiers (weakness classification),
  and reference links (CVE references, KEV catalog, upstream commits)
- **Access**: REST API at
  `access.redhat.com/hydra/rest/securitydata/cve/{CVE-ID}.json`. Public
  access, no authentication required. Does not support incremental
  fetching — each CVE must be queried individually
- **Integration status**: **Active**. STAMP syncs daily via the
  `sync_cvss_redhat` fetcher, re-fetching CVSS data, CWE identifiers,
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

- **Relevant data**: CVE ID, vulnerability name, vendor/product, date
  added to the KEV catalog, remediation deadline, required action notes
- **Access**: JSON feed at
  `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`.
  No authentication required, no significant rate limits. Single file
  (~500KB), complete download each sync
- **Integration status**: **Planned**. New `sync_cisa_kev` fetcher.
  Schedule: TBD. Data is stored in a dedicated KEV table linked to CVE
  records. KEV reference URLs are also stored as `TicketReference` entries
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
- **Integration status**: **Planned**. New `sync_epss` fetcher. Schedule:
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

- **Relevant data**: CVSS scores (GitHub's own assessment), CWE
  identifiers, affected packages with precise version ranges across
  multiple ecosystems, reference links. Supports filtering by CVE ID via
  the `identifier` parameter in the GraphQL API
- **Access**: GraphQL API at `https://api.github.com/graphql`. Requires a
  GitHub personal access token (free). Supports incremental sync via the
  `updatedSince` parameter. Rate limit: 5,000 points/hour. The advisory
  database is also available as a Git repository at
  `https://github.com/github/advisory-database.git`
- **Integration status**: **Planned**. New `sync_ghsa` fetcher. Schedule:
  TBD. CVSS scores are stored as `CVECVSSAssessment` entries with
  `provider_name = "GitHub"`. CWE identifiers, affected versions, and
  reference URLs are stored in their respective tables
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
  patches
- **Access**: Git repository at
  `https://git.kernel.org/pub/scm/linux/security/vulns.git/`. Each CVE is
  a JSON file in CVE Record 5.0 format, organized by year. No
  authentication required. Sync via Git clone/pull
- **Integration status**: **Planned**. New `sync_kernel_cves` fetcher.
  Schedule: TBD. CVSS scores are stored as `CVECVSSAssessment` entries
  with `provider_name = "Linux Kernel CNA"`. Fix/introduce commit hashes
  are stored in a dedicated kernel commits table. Affected kernel versions
  and reference URLs are stored in their respective tables
- **Documentation**: https://docs.kernel.org/process/cve.html

### OSV (Open Source Vulnerabilities)

OSV is an aggregated vulnerability database operated by Google that unifies
advisories from 20+ databases (GitHub, PyPI, crates.io, Go, Debian,
Alpine, Linux kernel, and more) into a standardized format. It provides a
simple REST API that supports queries by CVE ID. While OSV overlaps with
other sources STAMP already integrates (NVD, GHSA), it can provide
additional affected version data and reference links with type
classification (FIX, EVIDENCE, ARTICLE, ADVISORY).

- **Relevant data**: CVSS scores (aggregated from source databases),
  affected package version ranges across multiple ecosystems, reference
  links with type tags (FIX, EVIDENCE, ARTICLE, ADVISORY), related
  advisory identifiers (including SUSE security advisories when available)
- **Access**: REST API at `https://api.osv.dev/v1/vulns/{id}`. No
  authentication required. Supports query by CVE ID
- **Integration status**: **Planned**. New `sync_osv` fetcher. Schedule:
  TBD. CVSS scores are stored as `CVECVSSAssessment` entries. Affected
  versions and reference URLs (with tags) are stored in their respective
  tables
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

IBS is the primary source for STAMP's release detection: STAMP queries IBS
to determine whether security fixes have landed in source codestreams and
whether update advisories have been published to product repositories.

- **Relevant data**: Source package revisions and MD5 checksums (for
  change detection), source diffs with embedded CVE and Bugzilla references
  (to confirm which vulnerabilities a commit addresses), build results,
  and published repository metadata including `updateinfo.xml` (which
  contains advisory details with CVE references and release dates)
- **Access**: REST API at `api.suse.de` (HTTP Basic Auth or API tokens).
  Download server at `download.suse.de/ibs` for repository data. Key
  endpoints:
  - `GET /source/{project}?view=info` — package listing with `srcmd5`
    checksums
  - `POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1` —
    source diff with CVE/Bugzilla tracker references
- **Integration status**: **Active**. Codestream-level release detection
  uses two complementary mechanisms: the `IBSEventConsumer` (real-time
  via IBS RabbitMQ, see `docs/features/ibs-rabbitmq-integration.md`) and
  the periodic `check_codestream_releases` fetcher (catch-up every 24
  hours at 02:00 UTC). Product-level release detection
  (`check_product_releases`) runs as a periodic `BaseFetcher` subclass
- **Documentation**: https://build.suse.de (internal). The OBS API
  documentation at https://api.opensuse.org/apidocs/ applies to IBS as
  both run the same software
- **See also**: `docs/features/obs-integration.md`,
  `docs/features/package-tracking.md`

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
  to integrate openSUSE package tracking into STAMP. This may be evaluated
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
    consumed** by STAMP for codestream-level release detection
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
- **Integration status**: **Active**. STAMP consumes
  `suse.obs.package.commit` events from IBS for near-real-time
  codestream-level release detection. The periodic polling fetcher
  (`check_codestream_releases`, every 24 hours at 02:00 UTC) serves as
  a catch-up mechanism for events missed during consumer downtime, since
  queues are exclusive and transient. See
  `docs/features/ibs-rabbitmq-integration.md` for the full specification
- **Documentation**: https://rabbit.opensuse.org (OBS),
  https://github.com/openSUSE/suse_msg/blob/master/amqp_infra.md,
  OBS event types: https://github.com/openSUSE/open-build-service/tree/master/src/api/app/models/event

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
- **Integration status**: **Active**. STAMP periodically syncs the product
  catalog (`sync_smelt_products` fetcher) and queries package maintenance
  information on demand when adding packages to tickets. CPE identifiers
  from SMELT are the primary join key between STAMP's product records and
  AIMAAS lifecycle data
- **Documentation**: https://smelt.suse.de (internal)
- **See also**: `docs/features/package-tracking.md`

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
  LTSS/ESPOS phases). Products are matched to STAMP's local records via
  CPE identifiers (identical between SMELT and AIMAAS)
- **Access**: REST API at `aimaas.suse.de/api`. Key endpoints:
  - `GET /api/entity/products/{slug}` — individual product lifecycle dates
  - `GET /api/entity/cvss-threshold` (paginated) — CVSS thresholds
- **Integration status**: **Active**. STAMP periodically syncs lifecycle
  dates (`sync_aimaas_lifecycle` fetcher) and CVSS thresholds
  (`sync_aimaas_thresholds` fetcher). When thresholds or lifecycle dates
  change, STAMP re-evaluates eligibility for all active tickets
  referencing the affected products
- **Documentation**: https://aimaas.suse.de (internal)
- **See also**: `docs/features/package-tracking.md`,
  `docs/features/cvss-scoring.md`

---

## Related Internal Tools

### SMASH (SUSE Maintenance And Security Helper)

SMASH is the predecessor platform to STAMP. It currently fulfills the same
role that STAMP is being built to replace: managing and tracking security
updates across SUSE's maintained product portfolio. STAMP's design is
directly informed by lessons learned from operating SMASH.

SMASH has a rich fetcher/worker architecture with integrations to many of
the same sources STAMP uses (NVD, MITRE, Bugzilla, IBS, SMELT, AIMAAS, Red
Hat) as well as additional sources that STAMP does not yet integrate
(Google Project Zero, ZDI, GitHub Security Advisories, Linux Kernel CVE
feeds, Oracle CSAF, Amazon ALAS, Debian Security, oss-security mailing
list, IBM Java advisories, and Jira/ECO). SMASH's `TrackedReleaseFetcher`
— which uses MD5 checksum comparison against IBS source info to detect
codestream-level releases — directly inspired STAMP's equivalent mechanism.

SMASH manages "issues" (equivalent to STAMP's tickets) through a workflow
of states: New, Analysis, Analyzed/Pending, Running, Resolved. Each issue
tracks affected packages across codestreams and products, CVSS scores from
multiple providers, and references to external bug trackers and advisories.

- **Relevant data**: Issue tracking, affected software per codestream and
  product, CVSS assessments, audit logs, embargoed bug tracking,
  maintenance update coordination
- **Access**: Web UI and REST API at `smash.suse.de`. API authentication
  via personal tokens. Endpoints include `/api/issues/`,
  `/api/embargoed-bugs/`, `/api2/issues/`, `/api2/cvss/`, and more
- **Integration status**: **Not integrated**. STAMP is designed as SMASH's
  successor, not as an integration partner. However, a data migration path
  from SMASH to STAMP may be needed during the transition period
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

While openQA is not a direct data source for STAMP's core workflow, it
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
  between the build phase (tracked by STAMP via IBS) and the publication
  phase (tracked by STAMP via `updateinfo.xml`)
- **Documentation**: https://open.qa/docs/,
  https://openqa.suse.de (internal)

---

## Reference Sources

### SUSE Bugzilla

SUSE's Bugzilla instance is the primary bug tracking system for SUSE
products. Two web interfaces exist — `bugzilla.suse.com` (primary, for SUSE
products) and `bugzilla.opensuse.org` (for openSUSE) — but they share the
same underlying database. STAMP always references `bugzilla.suse.com`
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

All three prefixes refer to the same database and the same bug IDs. STAMP
normalizes all forms to the canonical `bsc#` prefix.

- **Relevant data**: Bug reports, security issue tracking, embargo status,
  CVE-to-bug associations. IBS source diffs include Bugzilla tracker
  references (with tracker type `bnc`) alongside CVE references
- **Access**: Web UI at `bugzilla.suse.com`. Bugzilla REST API and
  XML-RPC API available. SMASH integrates deeply with Bugzilla for issue
  creation, status tracking, and embargo detection
- **Integration status**: **Reference only**. Bugzilla IDs appear in IBS
  source diffs and can be manually linked to tickets via the
  `TicketReference` system. There is no automated Bugzilla sync in STAMP.
  SMASH's extensive Bugzilla integration (fetchers for recent bugs,
  foreign bugs, CVE alias correction, CVSS marking, and reopen detection)
  provides a reference for what a deeper integration could look like
- **Documentation**: https://bugzilla.suse.com

---

## Fetcher Registry

All background tasks that fetch data from external sources inherit from
`BaseFetcher` (`backend/app/services/base_fetcher.py`) and are
automatically registered in the fetcher registry. The table below lists all
fetchers — both active and planned — with their schedule, authentication
requirements, rate limits, and data ingested. See
`docs/features/fetcher-dashboard.md` for infrastructure details.

| Fetcher | Source | Schedule | Auth | Rate Limits | Data Ingested |
|---------|--------|----------|------|-------------|---------------|
| `sync_cves_nvd` | NVD | Every 6 hours | API key (free, optional) | Without key: 5 req/30s; with key: 50 req/30s | CVE records, CVSS (NVD Primary + CNA Secondary), CWE, affected versions (CPE), references |
| `sync_cves_mitre` | MITRE CVE Services | Every 6 hours | None | None known | CVE records, CISA ADP data (SSVC, KEV, CVSS CISA, CWE, affected versions), references |
| `sync_cvss_redhat` | Red Hat Security Data | Daily | None | Undocumented; STAMP uses 2s delay between requests | CVSS Red Hat, CWE, references |
| `sync_smelt_products` | SMELT | TBD | TBD (internal) | N/A (internal) | Product catalog (name, version, CPE, repositories) |
| `sync_aimaas_lifecycle` | AIMAAS | TBD | TBD (internal) | N/A (internal) | Product lifecycle dates |
| `sync_aimaas_thresholds` | AIMAAS | TBD | TBD (internal) | N/A (internal) | CVSS thresholds per product |
| `check_codestream_releases` | IBS | Daily at 02:00 UTC | HTTP Basic / API token (internal) | N/A (internal) | Codestream-level release detection (MD5 checksums) |
| `check_product_releases` | IBS | TBD | HTTP Basic / API token (internal) | N/A (internal) | Product-level release detection (updateinfo.xml) |
| `sync_cisa_kev` | CISA KEV | TBD | None | None (single JSON file) | KEV records (exploit flag, dateAdded, deadline), references |
| `sync_epss` | FIRST.org EPSS | TBD | None | None known | EPSS score + percentile per CVE |
| `sync_ghsa` | GitHub Advisory DB | TBD | GitHub token (free) | 5,000 points/hour | CVSS GitHub, CWE, affected versions (multi-ecosystem), references |
| `sync_kernel_cves` | Linux Kernel CNA | TBD | None | None (Git clone/pull) | CVSS kernel, fix/introduce commits, affected kernel versions, references |
| `sync_osv` | OSV (osv.dev) | TBD | None | None known | CVSS, affected versions, references |

Note: `IBSEventConsumer` (real-time codestream release detection via IBS
RabbitMQ) is a continuous service, not a `BaseFetcher` subclass. See
`docs/features/ibs-rabbitmq-integration.md`.

### New Data Structures

The planned fetchers require the following new tables, all linked to the
`CVE` table via a `cve_id` foreign key. These are documented here at a
high level; full schema details will be added to `docs/data-model.md` when
each source is implemented.

| Table | Fields | Populated By |
|-------|--------|--------------|
| `CVESSVC` | exploitation, automatable, technical_impact, version | `sync_cves_mitre` (ADP block) |
| `CVEEPSS` | score, percentile | `sync_epss` |
| `CVEKEV` | date_added, remediation_deadline | `sync_cisa_kev`, `sync_cves_mitre` (ADP block) |
| `CVECWE` | cve_id, cwe_id (many-to-many, unique on pair) | `sync_cves_nvd`, `sync_cves_mitre`, `sync_cvss_redhat`, `sync_ghsa` |
| `CVEAffectedVersion` | package_name, ecosystem, version_start, version_end, fixed_version | `sync_cves_nvd`, `sync_cves_mitre`, `sync_ghsa`, `sync_kernel_cves`, `sync_osv` |
| `CVEKernelCommit` | introducing_commit, fixing_commit | `sync_kernel_cves` |
