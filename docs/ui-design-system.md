# UI Design System

This document defines the visual and structural conventions for the Sentinel
frontend. All UI components must follow these guidelines to ensure consistency
across the platform.

## General Principles

- Clean, professional interface suitable for security operations
- Information-dense but not cluttered: security teams need to see data quickly
- Consistent spacing, typography, and color usage across all pages
- Responsive: must work on desktop (primary) and tablet
- Accessibility: follow WCAG 2.1 AA guidelines

## Component Library

- **Base**: shadcn/ui (built on Radix UI primitives)
- All custom components must be built on top of shadcn primitives
- Custom reusable components go in `frontend/src/components/ui/`
- Page-specific components go in `frontend/src/components/`
- NEVER use raw HTML elements for interactive UI (buttons, inputs, selects,
  modals, etc.) — always use shadcn/ui components

## Layout

- **Sidebar navigation**: main sections (Inbox, My Tickets, All Tickets,
  Fetchers, Admin Settings)
- **Top bar**: user info, notifications, environment indicator
- **Content area**: consistent padding and max-width
- **Breadcrumbs**: for navigation context on detail pages

## Color Palette

Colors are defined via CSS variables and Tailwind configuration. Specific
values will be defined during implementation, but the semantic usage is:

| Semantic Name  | Usage                                           |
|----------------|-------------------------------------------------|
| `primary`      | Main actions, navigation highlights, links      |
| `secondary`    | Secondary actions, less prominent elements      |
| `destructive`  | Delete actions, critical severity CVEs          |
| `warning`      | Medium severity, caution states                 |
| `success`      | Fixed/resolved status, low severity             |
| `muted`        | Disabled states, placeholder text               |
| `accent`       | Highlights, focus indicators                    |

### CVE Severity Colors

| Severity   | Color Intent    | Usage                              |
|------------|----------------|------------------------------------|
| Critical   | `destructive`  | Red-toned, high visual prominence  |
| High       | `warning`      | Orange/amber-toned                 |
| Medium     | `warning`      | Yellow-toned, less intense         |
| Low        | `success`      | Green-toned, low visual prominence |
| None       | `secondary` or `outline` | CVSS 0.0 / informational, minimal visual weight |
| Unresolved | `muted`        | Null/unresolved severity; show "—" placeholder |

### Ticket Status Colors

| Status      | Color Intent   |
|-------------|---------------|
| New         | `muted`       |
| Analysis    | `primary`     |
| Analyzed    | `accent`      |
| Resolved    | `success`     |
| Ignored     | `muted`       |
| Duplicated  | `muted`       |

### Package Status Colors

| Status           | Color Intent   |
|------------------|---------------|
| Analysis         | `muted`       |
| Affected (red)   | `destructive` |
| Affected (green) | `success`     |
| Not Affected     | `success`     |
| Won't Fix        | `success`     |
| Fixed            | `success`     |

### Delivery Status Colors

| Status      | Color Intent |
|-------------|-------------|
| Pending     | `muted`     |
| In Progress | `warning`   |
| Released    | `success`   |

## Typography

- **Font family**: system font stack (no custom font loading)
- **Headings**: consistent hierarchy h1-h4, used semantically
- **Body text**: base size from Tailwind defaults
- **Monospace**: used for CVE IDs, package names, version strings, code

## Spacing

- Use Tailwind spacing scale consistently
- Standard content padding: `p-6` for page content
- Standard gap between sections: `space-y-6`
- Card internal padding: `p-4` or `p-6`
- Consistent margins between related elements

## Common Patterns

### Data Tables

Tables are used extensively throughout the platform. All tables must:

- Use the project's Table component (shadcn/ui based)
- Support column sorting (click on header)
- Support pagination with consistent controls
- Show loading state while data is fetched
- Show empty state with helpful message when no data
- Use consistent column alignment (text left, numbers right)

### Status Badges

Status indicators appear throughout the platform (CVE severity, ticket status,
product active/inactive). All status badges must:

- Use the project's Badge component
- Use consistent colors as defined in the color palette above
- Use consistent sizing and shape

### Forms

- Consistent label positioning (above input)
- Inline validation feedback (show errors below the field)
- Consistent button placement (primary action right, cancel left)
- Loading state on submit buttons

### Detail Pages

- Header with title and primary actions
- Breadcrumb navigation
- Content organized in cards/sections
- Related data in tabs when multiple categories exist

### Empty States

- Centered message with icon
- Brief description of what would appear here
- Call-to-action button when applicable

### Loading States

- Skeleton loading for initial page load
- Inline spinners for actions (button loading states)
- Never show a blank page while loading

## Data Formatting Conventions

### CVSS Version Display

When displaying CVSS version numbers in the UI (tabs, labels, dropdowns,
tooltips, timeline events), always prefix with "v" to distinguish versions
from scores:

- Correct: `v3.1`, `v4.0`, `v2.0`
- Incorrect: `3.1`, `4.0`, `2.0`

Internal storage and API fields use the raw number without prefix (e.g.,
`cvss_version = "3.1"`). The "v" prefix is a display-only convention applied
at the rendering layer.

## Icons

- Use a single icon library consistently (Lucide React, included with shadcn)
- Do not mix icon libraries

## Responsive Behavior

- **Desktop (>1024px)**: full layout with sidebar
- **Tablet (768-1024px)**: collapsible sidebar
- **Mobile (<768px)**: not a primary target, but should not break

## Accessibility

- All interactive elements must be keyboard accessible
- Proper ARIA labels on icons and non-text elements
- Sufficient color contrast (WCAG AA)
- Focus indicators on all interactive elements
