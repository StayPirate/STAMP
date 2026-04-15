# OBS Integration

## Purpose

Integration with Open Build Service (OBS) for package source management, build
triggering, and build status monitoring. OBS is used both as a source hosting
platform and as the build system for security updates.

## Integration Modes

### Mode 1: OBS-hosted sources

Some distributions host their package sources entirely in OBS. For these:
- Package metadata is fetched from OBS
- Patches are submitted directly to OBS
- Builds are triggered via OBS API

### Mode 2: Git-hosted sources with OBS builds

Some distributions host sources in Git repositories with CI/CD pipelines that
submit to OBS for building. For these:
- Package metadata may come from Git or OBS
- Source changes happen in Git
- OBS is used only for building
- Build status is monitored via OBS API

## OBS API Integration

### Authentication

- OBS API uses HTTP Basic Auth or API tokens
- Credentials are stored as environment variables, never in code
- Configuration: `OBS_API_URL`, `OBS_USERNAME`, `OBS_PASSWORD`

### Key API Operations

#### Package Metadata

```
GET /source/{project}/{package}/_meta
```

Fetch package metadata including description, maintainers, and build targets.

#### Package Source Files

```
GET /source/{project}/{package}
```

List source files for a package.

#### Build Status

```
GET /build/{project}/{repository}/{arch}/{package}/_status
```

Get build status for a specific package in a specific repository/architecture.

#### Build Results

```
GET /build/{project}/_result
```

Get build results for all packages in a project.

#### Trigger Rebuild

```
POST /build/{project}?cmd=rebuild&package={package}
```

Trigger a rebuild of a specific package.

## Data Model

Package and Distribution tables include OBS-related fields:
- `Distribution.obs_project`: OBS project name for this distribution
- `Package.obs_project`: OBS project containing the package
- `Package.obs_package`: OBS package name (if different from package name)

## Service Layer

### OBSClient (`backend/app/services/obs_client.py`)

Encapsulates all OBS API communication:
- `get_package_meta(project, package)`: fetch package metadata
- `get_build_status(project, package, repo, arch)`: get build status
- `get_project_results(project)`: get all build results for a project
- `trigger_rebuild(project, package)`: trigger a package rebuild
- `list_packages(project)`: list all packages in a project

### OBSSyncService

- Syncs package lists from OBS projects to the local database
- Updates build status for packages with active security updates

## Background Tasks

- `sync_obs_packages`: periodic sync of package lists from OBS projects
- `monitor_obs_builds`: checks build status for active security updates
- `trigger_obs_build`: triggers a build in OBS for a security update

## API Endpoints

### Get OBS Build Status

```
GET /api/v1/obs/builds/{project}/{package}
```

Response: build status across all repositories and architectures.

### Trigger Build

```
POST /api/v1/obs/builds/{project}/{package}/rebuild
```

Triggers a rebuild. Requires Security Team or Admin role.

### Sync Packages from OBS

```
POST /api/v1/obs/sync/{project}
```

Triggers a package list sync from an OBS project. Requires Admin role.

## Business Rules

1. OBS credentials are validated at startup; warn if not configured
2. OBS API calls use retry logic with exponential backoff
3. Build status is cached in Redis with a short TTL (5 minutes)
4. Failed builds generate notifications to the update owner
5. All OBS operations are logged for audit purposes

## Security

- OBS credentials are managed via environment variables
- OBS API calls are made server-side only; credentials are never exposed
  to the frontend
- OBS operations require Security Team or Admin role
