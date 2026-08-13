# Contributing to Sentinel

Thank you for your interest in contributing to Sentinel! This guide
covers everything you need to get started.

## Code of Conduct

This project follows the [Contributor Covenant Code of
Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to
uphold this code. Please report unacceptable behavior to
security@suse.com.

## How to Contribute

### Reporting Bugs

1. Check the [existing issues](https://github.com/StayPirate/sentinel/issues)
   to avoid duplicates
2. Open a new issue using the **Bug Report** template
3. Include steps to reproduce, expected behavior, and actual behavior
4. Add the Sentinel version and your environment details

### Suggesting Features

1. Check the [existing issues](https://github.com/StayPirate/sentinel/issues)
   and [feature specifications](docs/features/) to see if it has been
   discussed
2. Open a new issue using the **Feature Request** template
3. Describe the problem you are trying to solve and the proposed
   solution

### Submitting Code Changes

1. **Fork** the repository and create a topic branch from `master`
2. Follow the [branch naming conventions](#branch-naming)
3. Make your changes following the [coding standards](#coding-standards)
4. Write tests for your changes (see [Testing](#testing))
5. Ensure all checks pass locally (see [Pre-Submission
   Checklist](#pre-submission-checklist))
6. Open a **pull request** against `master`

## Development Setup

### Prerequisites

- **Python 3.13** (managed via [uv](https://docs.astral.sh/uv/))
- **Podman** or **Docker** (for local PostgreSQL and Redis)

### Quick Start

```bash
# Clone your fork
git clone git@github.com:<your-username>/sentinel.git
cd sentinel

# Install Python dependencies
cd backend && uv sync

# Start local infrastructure (PostgreSQL + Redis)
cd .. && ./scripts/dev-env.sh up

# Run database migrations
cd backend && uv run alembic upgrade head

# Run the test suite
uv run pytest
```

## Coding Standards

Sentinel follows strict coding conventions documented in
[docs/conventions.md](docs/conventions.md). The key points are:

### Language

- All code, comments, docstrings, and documentation **must be in
  English**

### Python Style

- **Formatter**: ruff format (black-compatible, 88-character line
  length)
- **Linter**: ruff check
- **Type hints**: required on all function signatures (mypy strict
  mode)
- **Imports**: sorted by ruff (isort-compatible)

### Architecture

- **Thin API handlers**: validate input, call service, return response
- **Business logic in services**: all logic lives in `app/services/`
- **Validation in schemas**: use Pydantic schemas in `app/schemas/`

See the full [Architecture](docs/architecture.md) document for the
layer structure and dependency rules.

## Branch Naming

| Prefix      | Use                         |
|-------------|-----------------------------|
| `feature/`  | New features                |
| `fix/`      | Bug fixes                   |
| `docs/`     | Documentation changes       |
| `refactor/` | Code refactoring            |
| `chore/`    | Infrastructure, config, deps|
| `ci/`       | CI/CD pipeline changes      |
| `test/`     | Test-only changes           |

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
type[(scope)]: description
```

- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`
- Keep the first line under 72 characters
- Use imperative mood: "add feature" not "added feature"

Examples:

```
feat: add CVE severity filtering to dashboard
fix: correct CVSS score parsing for NVD API v2
docs: update data model with Product table
test: add integration tests for CVE sync service
```

Pull request titles follow the same format and must stay under 72
characters (validated by CI).

## Testing

Every code change **must** include tests. The project uses pytest with
async support.

### Running Tests

```bash
cd backend

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov

# Run specific test file
uv run pytest tests/api/v1/test_health.py

# Run tests matching a pattern
uv run pytest -k "test_create_user"
```

### Test Requirements

- **New API endpoints**: test happy path, validation errors,
  auth/permissions
- **New models**: test creation, constraints, relationships
- **New services**: test business logic, edge cases, error handling
- **Bug fixes**: add a regression test that reproduces the bug

### Test Markers

```python
@pytest.mark.unit          # Fast, isolated (no DB, no Redis)
@pytest.mark.integration   # Tests with real PostgreSQL
@pytest.mark.e2e           # Full HTTP request/response cycle
```

See [Testing Strategy](docs/features/platform/testing-strategy.md) for
the full testing conventions.

## Pre-Submission Checklist

Before opening a pull request, verify:

```bash
cd backend

# Lint
uv run ruff check .
uv run ruff format --check .

# Type check
uv run mypy .

# Tests
uv run pytest
```

All four checks run in CI and must pass for a PR to be merged.

## Pull Request Process

1. Fill in the PR template completely
2. Link the PR to a tracking issue using `Closes #<issue>` in the
   description
3. Ensure CI passes on your branch
4. Request review from a maintainer
5. PRs are **squash-merged** — the PR title becomes the commit
   message on `master`

## Specifications

Sentinel follows a **specs-first** development model. Feature
specifications live in `docs/features/` and define behavioral
contracts before implementation begins. If you are proposing a
significant new feature, consider drafting a specification first.

See the [existing specifications](docs/features/) for examples of the
expected format and level of detail.

## License

By contributing to Sentinel, you agree that your contributions will be
licensed under the [Apache License 2.0](LICENSE).
