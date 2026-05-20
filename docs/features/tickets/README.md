# Tickets

Core workflow entity — CVE ingestion, triage, severity, and audit trail.

## Specs

```
tickets.md              Ticket lifecycle, status gates, API endpoints
ticket-mutations.md     ticket_mutations module contract (function signatures, concurrency, orphan invariants)
cve-tracking.md         CVE ingestion from NVD/MITRE, on-demand fetch
cvss-scoring.md         Multi-provider CVSS assessments, severity resolution
ticket-audit-log.md     TicketAuditEvent audit trail, event type contract
ticket-references.md    External links on tickets (auto + manual fetcher ingestion)
```

## Relationships

- `tickets.md` is the central spec — it defines the ticket entity,
  status machine, gate conditions, and API endpoints.
- `ticket-mutations.md` is the service-layer companion — it defines the
  `ticket_mutations` module contract (function signatures, concurrency
  control, orphan invariants, architectural test requirements).
- `cve-tracking.md` feeds tickets: each ingested CVE creates a ticket.
- `cvss-scoring.md` drives ticket severity and product eligibility
  (consumed by `tickets.md` and `packages/package-tracking.md`).
- `ticket-audit-log.md` defines the event contract that all ticket-mutating
  operations must satisfy.
