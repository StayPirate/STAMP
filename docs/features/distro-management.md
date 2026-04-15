# Distribution Management

## Purpose

Manage the set of SUSE and openSUSE-based distributions that the platform
tracks and maintains. Each distribution version is an independent entity with
its own set of packages and update lifecycle.

## Supported Distribution Types

- **SUSE Linux Enterprise Server (SLES)**: e.g., SLES 15 SP5, SLES 15 SP6
- **SUSE Linux Enterprise Desktop (SLED)**: corresponding desktop versions
- **openSUSE Leap**: e.g., openSUSE Leap 15.5, 15.6
- **openSUSE Tumbleweed**: rolling release
- Other SUSE-based distributions as needed

## Data Model

See `docs/data-model.md` for the full schema. Key tables:

- **Distribution**: represents a specific distribution version
- **Package**: software packages tracked across distributions
- **DistributionPackage** (implicit via AffectedPackage): links packages to
  distributions

## API Endpoints

### List Distributions

```
GET /api/v1/distributions
```

Query parameters:
- `search` (string): search in name and codename
- `active` (boolean): filter by active/inactive status
- `page`, `per_page`: pagination

Response: paginated list of distributions.

### Create Distribution

```
POST /api/v1/distributions
```

Request body:
- `name` (string, required): distribution name
- `version` (string, required): version string
- `codename` (string, optional): codename
- `active` (boolean, default: true): whether actively maintained
- `obs_project` (string, optional): OBS project name

Requires Admin role.

### Get Distribution Details

```
GET /api/v1/distributions/{id}
```

Response: full distribution details including package count and CVE statistics.

### Update Distribution

```
PUT /api/v1/distributions/{id}
```

Request body: same as create (partial update supported).
Requires Admin role.

### List Distribution Packages

```
GET /api/v1/distributions/{id}/packages
```

Query parameters:
- `search` (string): search in package name
- `has_cves` (boolean): filter packages with open CVEs
- `page`, `per_page`: pagination

Response: paginated list of packages in this distribution with CVE counts.

## Business Rules

1. Distribution name + version must be unique
2. Deactivating a distribution does not delete data; it stops CVE impact
   analysis from including it
3. Distributions cannot be deleted if they have associated security updates
4. When a distribution is created, an initial package sync from OBS can be
   triggered (if OBS project is configured)
5. Only Admin role can create, modify, or deactivate distributions

## UI Requirements

### Distribution List Page

- Table with all distributions
- Active/inactive status badge
- Package count and open CVE count per distribution
- Filter by active status
- Quick actions: edit, activate/deactivate

### Distribution Detail Page

- Distribution metadata
- Package list tab
- CVE summary tab (CVEs affecting this distribution)
- Security updates tab
- OBS project link (if configured)

## Security

- Distribution list is viewable by all authenticated users
- Create/update/deactivate requires Admin role
