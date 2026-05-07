# Integrations

Technical integration layers with external services.

## Specs

```
ibs-integration.md            IBS REST API client, endpoints, authentication
ibs-rabbitmq-integration.md   IBS RabbitMQ consumer, connection management
```

## Relationships

- `ibs-integration.md` defines the HTTP client used by multiple
  consumers: codestream release detection, product release detection,
  submission tracking, and package bugowner resolution.
- `ibs-rabbitmq-integration.md` defines the event consumer process that
  feeds real-time data to codestream release detection and submission
  tracking.
- Both specs are pure infrastructure — business logic lives in
  `packages/` and `tickets/` specs that consume these layers.
