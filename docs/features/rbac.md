# Role-Based Access Control (RBAC)

## Purpose

Control access to platform features based on user roles. Users can hold
zero, one, or multiple roles. An unauthenticated user or an authenticated
user with no roles has read-only access to public data.

## Access Levels

### Unauthenticated / No Roles

Read-only access to public data:
- View tickets, CVEs, and products
- View fetcher dashboard (list, detail, charts, run history, error messages)

### Incident Manager

Operates the triage and assessment workflow:
- Create tickets manually (see `docs/features/tickets.md`)
- Assign and reassign tickets
- Change ticket status (New, Analysis, Analyzed, Resolved, Ignored,
  Duplicated)
- Mark tickets as duplicate and revert duplicate status
- Associate a CVE with a ticket (see `docs/features/tickets.md`)
- Set and update severity override for tickets without CVE
- Add and remove packages from tickets
- Change codestream and product affectedness status
- Add, edit, and delete SUSE CVSS assessments
- Add, edit, and delete ticket references
- Trigger CVE synchronization

### Admin

Administers the platform:
- Manage users (create, update roles, deactivate)
- View and update system settings (e.g., default CVSS version)
- Soft-delete and restore tickets
- View soft-deleted tickets
- Trigger manual fetcher runs
- Enable and disable fetchers
- Modify fetcher configuration (schedule, parameters)
- View fetcher audit log
- View fetcher error tracebacks

Admin does NOT inherit Incident Manager permissions. A user who needs both
capabilities must hold both roles.

## Permission Matrix

### Incident Manager Operations

| Action                           | Admin | IM  | Unauth |
|----------------------------------|-------|-----|--------|
| Create ticket manually           | No    | Yes | No     |
| Assign/reassign ticket           | No    | Yes | No     |
| Change ticket status             | No    | Yes | No     |
| Mark as duplicate / revert       | No    | Yes | No     |
| Associate CVE with ticket        | No    | Yes | No     |
| Set/update severity override     | No    | Yes | No     |
| Add/remove packages              | No    | Yes | No     |
| Change codestream/product status | No    | Yes | No     |
| Add/edit/delete SUSE CVSS        | No    | Yes | No     |
| Add/edit/delete references       | No    | Yes | No     |
| Trigger CVE sync                 | No    | Yes | No     |

### Admin Operations

| Action                           | Admin | IM  | Unauth |
|----------------------------------|-------|-----|--------|
| Manage users                     | Yes   | No  | No     |
| View/update system settings      | Yes   | No  | No     |
| Soft-delete ticket               | Yes   | No  | No     |
| Restore deleted ticket           | Yes   | No  | No     |
| View deleted tickets             | Yes   | No  | No     |
| Trigger manual fetcher run       | Yes   | No  | No     |
| Enable/disable fetchers          | Yes   | No  | No     |
| Modify fetcher config            | Yes   | No  | No     |
| View fetcher audit log           | Yes   | No  | No     |
| View fetcher error tracebacks    | Yes   | No  | No     |

### Public Operations

| Action                           | Admin | IM  | Unauth |
|----------------------------------|-------|-----|--------|
| View tickets / CVEs (active)     | Yes   | Yes | Yes    |
| View products                    | Yes   | Yes | Yes    |
| View ticket references           | Yes   | Yes | Yes    |
| View fetcher dashboard           | Yes   | Yes | Yes    |

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

Response: current user profile and roles. Requires authentication.

### User Management (Admin only)

```
GET /api/v1/users
```

List all users. Admin only.

```
POST /api/v1/users
```

Create a new user. Admin only. A new user may be created with zero, one,
or multiple roles.

Request body:
- `username` (string, required)
- `email` (string, required)
- `full_name` (string, optional)
- `roles` (list[enum], optional): list of roles to assign (default: none)
- `password` (string, required)

```
PUT /api/v1/users/{id}
```

Update user details and roles. Admin only.

```
DELETE /api/v1/users/{id}
```

Deactivate a user (soft delete). Admin only.

## Implementation Details

### Authentication Mechanism

TBD -- options under consideration:
- JWT tokens (stateless, good for API clients)
- Session-based (simpler, better for SPA)

Decision will be made during implementation of this feature.

### Permission Checking

- Permissions are checked at the API endpoint level using FastAPI
  dependencies
- A `require_role()` dependency factory returns a dependency that checks
  whether the current user holds the required role
- Example: `Depends(require_role(Role.INCIDENT_MANAGER))`
- Public endpoints (ticket list, CVE list, product list, fetcher dashboard)
  do not require authentication
- The `require_role()` check queries the `UserRole` junction table

### Password Security

- Passwords are hashed using bcrypt
- Minimum password length: 12 characters
- Password requirements TBD

## Data Model

See `docs/data-model.md`. Key tables:

- **User**: username, email, active status
- **UserRole**: junction table linking users to roles (M2M)
- **Role** enum: `Admin`, `Incident Manager`

## UI Requirements

### Login Page

- Username and password form
- Error messages for invalid credentials
- No registration (users are created by admins)

### User Management Page (Admin only)

- Table of all users with their assigned roles
- Create user form (with role multi-select)
- Edit user details and roles
- Activate/deactivate user

### User Profile

- View own profile and roles
- Change own password

## Business Rules

1. There must always be at least one active user with the Admin role
2. Users cannot change their own roles
3. Users cannot deactivate their own account
4. Deactivated users cannot log in
5. All authentication events are logged (login, logout, failed attempts)
6. Session timeout: TBD (configurable)
7. A user with no roles has the same access as an unauthenticated user
   (read-only on public data)
8. Admin bootstrap mechanism: TBD (initial admin creation strategy will be
   decided during implementation)
