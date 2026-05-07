# Pages

## Purpose

Define the main pages of the Sentinel platform. The platform is designed around
a ticket-based workflow where vulnerability analysts (VAs) triage, analyze, and
resolve security issues that affect maintained products.

A **Ticket** is the primary work unit for VAs. Tickets may or may not be
associated with a CVE. See `docs/features/tickets.md` for the full ticket
specification (identification, creation pathways, lifecycle, severity
resolution, and status transition rules).

## Application Layout

The application shell structure (top bar, sidebar navigation, tab groups,
and navigation hierarchy) is defined in [pages/layout.md](pages/layout.md).

## Pages Overview

| Page                 | Route                         | Spec                                              |
|----------------------|-------------------------------|---------------------------------------------------|
| Inbox                | `/inbox`                      | [pages/inbox.md](pages/inbox.md)                  |
| My Tickets           | `/my-tickets`                 | [pages/my-tickets.md](pages/my-tickets.md)        |
| Orphan Tickets       | `/orphan-tickets`             | [pages/orphan-tickets.md](pages/orphan-tickets.md)|
| All Tickets          | `/tickets`                    | [pages/all-tickets.md](pages/all-tickets.md)      |
| Ticket Detail        | `/tickets/:id`                | [pages/ticket-detail.md](pages/ticket-detail.md)  |
| My Packages          | `/my-packages`                | [pages/my-packages.md](pages/my-packages.md)      |
| My Packages (Ticket) | `/my-packages/ticket/:ticketId` | [pages/my-packages-ticket.md](pages/my-packages-ticket.md) |
| Fetchers             | `/fetchers`                   | [pages/fetchers.md](pages/fetchers.md)            |
| Fetcher Detail       | `/fetchers/:name`             | [pages/fetcher-detail.md](pages/fetcher-detail.md)|
| Admin Settings       | `/admin/settings`             | [pages/admin-settings.md](pages/admin-settings.md)|

**Note**: Login is handled in the top bar, not as a standalone page. See
[pages/login.md](pages/login.md).
