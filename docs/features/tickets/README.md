# Tickets

Core workflow entity — CVE ingestion, triage, severity, and audit trail.

## Specs

```
tickets.md              Ticket lifecycle, status gates, API endpoints
ticket-service.md       ticket_service module contract (non-gate lifecycle operations, confidentiality management)
ticket-mutations.md     ticket_mutations module contract (CVSS/severity mutations, status evaluation, manual-zone exits)
cve-tracking.md         CVE ingestion from NVD/MITRE, on-demand fetch
cvss-scoring.md         Multi-provider CVSS assessments, severity resolution
ticket-audit-log.md     TicketAuditEvent audit trail, event type contract
ticket-references.md    External links on tickets (auto-classified by type, manual with manage_references capability)
```

## Relationships

- `tickets.md` is the central spec — it defines the ticket entity,
  status machine, gate conditions, and API endpoints.
- `ticket-service.md` is the service-layer companion for non-gate
  operations — it defines the `ticket_service` module contract (creation,
  CVE management, assignment, ignore, mark-as-duplicate, soft-delete/restore,
  confidentiality, access grants). Some operations call
  `reconcile_ticket_status` due to indirect gate effects.
- `ticket-mutations.md` is the service-layer companion for gate-relevant
  mutations — it defines the `ticket_mutations` module contract
  (CVSS/severity mutations, status evaluation, manual-zone exits,
  concurrency control, `auto_assign_actor()`).
  Package-centric mutations are in `packages/package-service.md`.
- `cve-tracking.md` feeds tickets: each ingested CVE creates a ticket.
- `cvss-scoring.md` drives ticket severity and product eligibility
  (consumed by `tickets.md` and `packages/package-model.md`).
- `ticket-audit-log.md` defines the event contract that all ticket-mutating
  operations must satisfy.
