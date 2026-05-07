# Application Layout

## Shell Structure

The application uses a fixed shell layout composed of three areas:

```
┌──────────────────────────────────────────────────────┐
│  [Logo]            Top Bar            [User / Login] │
├────────────┬─────────────────────────────────────────┤
│            │                                         │
│  Sidebar   │            Content Area                 │
│            │                                         │
│  • Section │   ┌─────┬─────┬─────┬─────┐           │
│  • Section │   │ Tab │ Tab │ Tab │ Tab │           │
│  • Section │   ├─────┴─────┴─────┴─────┤           │
│            │   │                         │           │
│            │   │      Page Content       │           │
│            │   │                         │           │
└────────────┴───┴─────────────────────────┴───────────┘
```

## Top Bar

Horizontal bar fixed at the top of the application.

| Element           | Position | Description                                         |
|-------------------|----------|-----------------------------------------------------|
| Application logo  | Left     | Sentinel logo/wordmark. Links to default route      |
| User menu         | Right    | Authenticated: user name + avatar, dropdown with logout. Unauthenticated: Login button (initiates SSO via id.suse.com — see `docs/features/sso-authentication.md`) |

## Sidebar Navigation

Vertical menu providing primary navigation between application sections.
The sidebar is always visible when authenticated. Selecting a section
navigates to the first tab of that section.

| Section    | Route prefix    | Description                |
|------------|-----------------|----------------------------|
| Tickets    | `/`             | Ticket triage and analysis |
| Packages   | `/my-packages`  | Maintainer dashboards      |
| Fetchers   | `/fetchers`     | Data sync monitoring       |
| Admin      | `/admin`        | System administration      |

### Visibility Rules

- **Tickets**: visible to all authenticated users
- **Packages**: visible to all authenticated users
- **Fetchers**: visible to Vulnerability Analyst role and above
- **Admin**: visible to Admin role only

## Tab Groups

Each sidebar section displays its pages as horizontal tabs in the content
area.

### Tickets Section

| Tab            | Route              | Page spec                          |
|----------------|--------------------|------------------------------------|
| Inbox          | `/inbox`           | [inbox.md](inbox.md)               |
| My Tickets     | `/my-tickets`      | [my-tickets.md](my-tickets.md)     |
| Orphan Tickets | `/orphan-tickets`  | [orphan-tickets.md](orphan-tickets.md) |
| All Tickets    | `/tickets`         | [all-tickets.md](all-tickets.md)   |

### Packages Section

| Tab         | Route         | Page spec                        |
|-------------|---------------|----------------------------------|
| My Packages | `/my-packages`| [my-packages.md](my-packages.md) |

### Fetchers Section

| Tab              | Route       | Page spec                    |
|------------------|-------------|------------------------------|
| Fetcher Overview | `/fetchers` | [fetchers.md](fetchers.md)   |

### Admin Section

| Tab      | Route            | Page spec                              |
|----------|------------------|----------------------------------------|
| Settings | `/admin/settings`| [admin-settings.md](admin-settings.md) |

## Active State

- The sidebar highlights the active section based on the current route
  prefix
- The tab bar highlights the active tab based on the exact route match
- Ticket Detail (`/tickets/:id`) is not a tab — it is reached by clicking
  a ticket row from any ticket list. The "Tickets" sidebar section and the
  originating tab remain highlighted

## Unauthenticated State

When not authenticated:

- The sidebar is hidden
- The top bar shows only the logo and a Login button
- The content area shows publicly accessible pages (Inbox, All Tickets,
  Ticket Detail) without tabs — full-width layout

## Responsive Behavior

TODO: define mobile/tablet behavior if relevant.
