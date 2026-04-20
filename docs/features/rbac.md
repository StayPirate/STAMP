# Role-Based Access Control (RBAC)

## Purpose

Control access to platform features based on user roles. The security update
workflow involves multiple roles with different responsibilities and
permissions.

## Roles

### Admin

Full access to all platform features. Responsible for:
- Managing distributions (create, update, deactivate)
- Managing users (create, assign roles, deactivate)
- Releasing security updates (final approval)
- System configuration

### Security Team

Primary operators of the platform. Responsible for:
- Reviewing and triaging CVEs
- Creating and managing security updates
- Triggering CVE syncs
- Monitoring OBS builds
- Cannot release updates (requires Admin)
- Cannot manage distributions or users

### Packager

Assists with package preparation. Responsible for:
- Viewing CVE and update information
- Updating package-level information
- Limited write access to updates they are assigned to

### Viewer

Read-only access. Can:
- View CVEs, distributions, packages, and updates
- Cannot modify any data
- Useful for stakeholders who need visibility

## Permission Matrix

| Action                          | Admin | Security Team | Packager | Viewer |
|---------------------------------|-------|---------------|----------|--------|
| View CVEs                       | Yes   | Yes           | Yes      | Yes    |
| Change CVE status               | Yes   | Yes           | No       | No     |
| Trigger CVE sync                | Yes   | Yes           | No       | No     |
| View distributions              | Yes   | Yes           | Yes      | Yes    |
| Manage distributions            | Yes   | No            | No       | No     |
| View packages                   | Yes   | Yes           | Yes      | Yes    |
| View security updates           | Yes   | Yes           | Yes      | Yes    |
| Create security updates         | Yes   | Yes           | No       | No     |
| Edit security updates           | Yes   | Yes           | Assigned | No     |
| Change update status            | Yes   | Yes*          | No       | No     |
| Release security updates        | Yes   | No            | No       | No     |
| Trigger OBS builds              | Yes   | Yes           | No       | No     |
| Manage users                    | Yes   | No            | No       | No     |
| View system settings            | Yes   | No            | No       | No     |
| View fetcher dashboard          | Yes   | Yes           | Yes      | Yes    |
| View fetcher error tracebacks   | Yes   | No            | No       | No     |
| Trigger manual fetcher run      | Yes   | No            | No       | No     |
| Enable/disable fetchers         | Yes   | No            | No       | No     |
| Modify fetcher config           | Yes   | No            | No       | No     |
| View fetcher audit log          | Yes   | No            | No       | No     |

*Security Team can change status except for the Released transition.

See `docs/features/fetcher-dashboard.md` for detailed fetcher dashboard
access control.

## API Endpoints

### Authentication

```
POST /api/v1/auth/login
```

Request body:
- `username` (string, required)
- `password` (string, required)

Response: authentication token/session.

```
POST /api/v1/auth/logout
```

Ends the current session.

### Current User

```
GET /api/v1/users/me
```

Response: current user profile and permissions.

### User Management (Admin only)

```
GET /api/v1/users
```

List all users. Admin only.

```
POST /api/v1/users
```

Create a new user. Admin only.

Request body:
- `username` (string, required)
- `email` (string, required)
- `full_name` (string, optional)
- `role` (enum, required)
- `password` (string, required)

```
PUT /api/v1/users/{id}
```

Update user details. Admin only.

```
DELETE /api/v1/users/{id}
```

Deactivate a user (soft delete). Admin only.

## Implementation Details

### Authentication Mechanism

TBD — options under consideration:
- JWT tokens (stateless, good for API clients)
- Session-based (simpler, better for SPA)

Decision will be made during implementation of this feature.

### Permission Checking

- Permissions are checked at the API endpoint level using FastAPI dependencies
- A `require_role()` dependency factory returns a dependency that checks the
  current user's role
- Example: `Depends(require_role(Role.SECURITY_TEAM, Role.ADMIN))`

### Password Security

- Passwords are hashed using bcrypt
- Minimum password length: 12 characters
- Password requirements TBD

## Data Model

See `docs/data-model.md`. Key table:

- **User**: username, email, role, active status

## UI Requirements

### Login Page

- Username and password form
- Error messages for invalid credentials
- No registration (users are created by admins)

### User Management Page (Admin only)

- Table of all users
- Create user form
- Edit user details
- Activate/deactivate user
- Role assignment

### User Profile

- View own profile
- Change own password

## Business Rules

1. There must always be at least one active Admin user
2. Users cannot change their own role
3. Users cannot deactivate their own account
4. Deactivated users cannot log in
5. All authentication events are logged (login, logout, failed attempts)
6. Session timeout: TBD (configurable)
