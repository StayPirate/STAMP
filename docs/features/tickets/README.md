# Tickets

Core workflow entity — CVE ingestion, triage, severity, and audit trail.

## Specs

```
tickets.md           Ticket lifecycle, status gates, centralized evaluation
cve-tracking.md      CVE ingestion from NVD/MITRE, on-demand fetch
cvss-scoring.md      Multi-provider CVSS assessments, severity resolution
ticket-audit-log.md    TicketAuditEvent audit trail, event type contract
```

## Relationships

- `tickets.md` is the central spec — it defines the ticket entity,
  status machine, and the `ticket_mutations` module contract.
- `cve-tracking.md` feeds tickets: each ingested CVE creates a ticket.
- `cvss-scoring.md` drives ticket severity and product eligibility
  (consumed by `tickets.md` and `packages/package-tracking.md`).
- `ticket-audit-log.md` defines the event contract that all ticket-mutating
  operations must satisfy.
