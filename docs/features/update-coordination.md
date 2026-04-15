# Update Coordination

## Purpose

Coordinate the preparation and release of security updates that address one or
more CVEs. This is the core workflow of the platform: from identifying a
vulnerability to releasing a patch.

## Update Lifecycle

```
Draft → In Progress → Testing → Released
                   ↘ Cancelled
```

1. **Draft**: update is created, CVEs are linked, initial planning
2. **In Progress**: patches are being prepared, packages are being built
3. **Testing**: packages are built and undergoing QA
4. **Released**: update is published to repositories
5. **Cancelled**: update was abandoned (CVE resolved differently, etc.)

## Data Model

See `docs/data-model.md` for the full schema. Key tables:

- **SecurityUpdate**: the update record with status and metadata
- **UpdatePackage**: links updates to specific package versions per distribution
- **CVE** (M2M via junction table): CVEs addressed by this update

## API Endpoints

### List Security Updates

```
GET /api/v1/updates
```

Query parameters:
- `search` (string): search in title and description
- `status` (enum): filter by update status
- `severity` (enum): filter by severity
- `distribution_id` (UUID): filter by target distribution
- `cve_id` (string): filter updates addressing a specific CVE
- `page`, `per_page`: pagination
- `sort_by`, `sort_order`: sorting

### Create Security Update

```
POST /api/v1/updates
```

Request body:
- `title` (string, required): update title
- `description` (string, optional): detailed description
- `severity` (enum, required): Critical, Important, Moderate, Low
- `cve_ids` (list[UUID], required): CVEs this update addresses
- `packages` (list): target packages and distributions

Requires Security Team or Admin role.

### Get Update Details

```
GET /api/v1/updates/{id}
```

Response: full update details including linked CVEs, packages, build status,
and activity history.

### Update Security Update

```
PUT /api/v1/updates/{id}
```

Partial update of metadata, linked CVEs, or packages.
Requires Security Team or Admin role.

### Change Update Status

```
POST /api/v1/updates/{id}/status
```

Request body:
- `status` (enum, required): target status
- `comment` (string, optional): reason for status change

Status transitions are validated (see business rules).

### Release Update

```
POST /api/v1/updates/{id}/release
```

Marks the update as released. Requires all builds to be successful.
Requires Admin role.

## Business Rules

1. An update must address at least one CVE
2. An update must target at least one package in at least one distribution
3. Status transitions are restricted:
   - Draft → In Progress (requires Security Team or Admin)
   - In Progress → Testing (requires successful OBS builds)
   - Testing → Released (requires Admin approval)
   - Any non-Released → Cancelled (requires Security Team or Admin)
   - Released and Cancelled are terminal states
4. Update severity is initially set by the user but should default to the
   highest severity among linked CVEs
5. When an update is released, all linked CVE statuses in the affected
   distributions should be updated to Fixed
6. All status changes are logged with timestamp, user, and comment

## Background Tasks

- `check_update_builds`: monitors OBS build status for updates in progress
- `auto_transition_testing`: moves updates to Testing when all builds succeed

## UI Requirements

### Update List Page

- Filterable, sortable table
- Status and severity badges
- CVE count per update
- Distribution targets
- Quick actions: view, edit, change status

### Update Detail Page

- Header with title, status, severity, and action buttons
- Linked CVEs section with severity indicators
- Target packages section with build status per distribution
- Activity timeline (status changes, comments)
- OBS build links

### Create/Edit Update Form

- CVE selector (search and multi-select)
- Package/distribution selector
- Severity selector (auto-suggested from CVEs)

## Security

- Update list is viewable by all authenticated users
- Create/edit requires Security Team or Admin
- Status changes follow role restrictions per transition
- Release requires Admin
