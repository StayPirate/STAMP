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
| IBS/OBS RabbitMQ | Internal/Public | Real-time build and publish events | Partially integrated |
| SMELT | Internal | Product catalog, package-codestream mapping | Active |
| AIMAAS | Internal | Product lifecycle dates, CVSS thresholds | Active |
| SUSE Bugzilla | Internal | Bug tracking, security issues | Reference only |
| SMASH | Internal | Security update management (predecessor to STAMP) | Not integrated |
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
  assessments and secondary CNA assessments, CPE-based affected product
  configurations, vulnerability status (e.g. Analyzed, Rejected), and
  reference links to advisories and patches
- **Access**: REST API v2 at `services.nvd.nist.gov/rest/json/cves/2.0`.
  Public access without authentication is rate-limited; an API key
  (free registration) provides higher rate limits. A companion Source API
  at `services.nvd.nist.gov/rest/json/source/2.0` resolves CNA identifiers
  to human-readable names
- **Integration status**: **Active**. STAMP syncs CVE data every 6 hours
  via the `sync_cves_nvd` fetcher. NVD is also used for CVSS score
  ingestion and on-demand single-CVE lookups
- **Documentation**: https://nvd.nist.gov/developers

### MITRE CVE Services

MITRE Corporation operates the CVE Program and assigns CVE identifiers. The
CVE Services API provides early access to newly assigned CVEs, often before
they are enriched by NVD with CVSS scores and CPE configurations. This makes
MITRE a valuable source for early awareness of new vulnerabilities.

- **Relevant data**: CVE identifiers, descriptions, CNA-provided metadata.
  Data is typically less enriched than NVD (no CVSS scores from MITRE
  itself, limited CPE data) but available earlier
- **Access**: CVE Services REST API. Public access
- **Integration status**: **Active**. STAMP syncs every 6 hours via the
  `sync_cves_mitre` fetcher, with on-demand single-CVE fetch support
- **Documentation**: https://www.cve.org/,
  https://cveawg.mitre.org/api-docs/openapi.json

### Red Hat Security Data

Red Hat publishes its own CVSS assessments for CVEs affecting Red Hat
products. Since Red Hat Enterprise Linux and SUSE Linux Enterprise share a
common upstream heritage for many packages, Red Hat's severity assessments
provide a useful secondary perspective when evaluating vulnerabilities.

- **Relevant data**: CVSS v3.1 base scores and scoring vectors for CVEs
  affecting Red Hat products
- **Access**: REST API at
  `access.redhat.com/hydra/rest/securitydata/cve/{CVE-ID}.json`. Public
  access, no authentication required. Does not support incremental
  fetching — each CVE must be queried individually
- **Integration status**: **Active**. STAMP syncs daily via the
  `sync_cvss_redhat` fetcher, re-fetching CVSS data for all active tickets
- **Documentation**:
  https://docs.redhat.com/en/documentation/red_hat_security_data_api/1.0/html-single/red_hat_security_data_api/index

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
  `SCOPE.APPLICATION.OBJECT.ACTION`:
  - `*.obs.package.commit` — source package committed (payload includes
    `project`, `package`, `rev`, `srcmd5`, `files`, `user`)
  - `*.obs.package.version_change` — package version changed (payload
    includes `project`, `package`, `oldversion`, `newversion`)
  - `*.obs.package.build_success` / `*.obs.package.build_fail` — build
    completed (payload includes `project`, `package`, `repository`,
    `arch`, `srcmd5`)
  - `*.obs.repo.published` — repository published (payload includes
    `project`, `repo`, `buildid`)
  - The IBS scope prefix is `suse` (e.g., `suse.obs.package.commit`);
    the OBS scope prefix is `opensuse`
- **Access**:
  - IBS: `amqps://suse:suse@rabbit.suse.de`
  - OBS: `amqps://opensuse:opensuse@rabbit.opensuse.org`
  - Both use the exchange named `pubsub` (type: topic, durable). Consumers
    must declare an exclusive queue, bind it to the exchange with a
    routing key filter, and consume messages. The exchange must be declared
    with `passive=True` and `durable=True` (consumers cannot create it)
- **Integration status**: **Partially integrated**. STAMP consumes
  `suse.obs.package.commit` events from IBS for near-real-time
  codestream-level release detection. The periodic polling fetcher
  (`check_codestream_releases`, every 24 hours at 02:00 UTC) serves as
  a catch-up mechanism for events missed during consumer downtime, since
  queues are exclusive and transient. Other events (`repo.published`,
  `build_success`, etc.) are not consumed. See
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
