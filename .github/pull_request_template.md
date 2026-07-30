## Summary

<!-- What does this PR do? Link the tracking issue and owning spec. -->

<!-- Required: enter `Closes #N`. Use `N/A - a specific reason` only for a qualifying cosmetic exemption under docs/conventions.md. -->
- Issue linkage:
- Owning spec:
- Roadmap piece ID (if applicable):

## Changes

<!-- List the main changes introduced by this PR. -->

-

## Data model / Migrations

<!-- New or modified tables, columns, constraints. Write "None" if not applicable. -->

None

## Tests

<!-- Tests added or modified, with markers (unit/integration/e2e). -->

-

## Verification checklist

- [ ] `cd backend && uv run pytest` passes
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .` passes
- [ ] `cd backend && uv run alembic upgrade head && uv run alembic check` passes (if database artifacts exist or changed)
- [ ] Manual verification is recorded below (or marked N/A with a reason)
- [ ] Image smoke coverage follows the testing-strategy Growth Rule (or is marked N/A below)

## Manual / image verification

<!-- Record commands and observed results, or explain why each item is N/A. -->

- Manual verification:
- Image smoke verification:

## External contract verification

<!-- For external integrations, record the live source, fixture, and fields verified. Otherwise write "N/A". -->

- Evidence / fixture / unverified fields:

## Reviewers

<!-- Which reviewer agents were invoked and their outcome. -->

-

## Notes

<!-- Risks, limitations, stubs introduced, follow-up work. -->

-
