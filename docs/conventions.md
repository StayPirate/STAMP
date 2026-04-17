# Code Conventions

## General

- All code, comments, docstrings, and documentation MUST be in English
- Follow the principle of least surprise: code should do what a reader expects
- Prefer explicit over implicit
- Keep functions short and focused on a single responsibility
- **API-first**: the REST API is the primary interface of the platform. The
  web UI is a consumer of the API. Every operation available through the UI
  must be achievable through the API alone, with equivalent filtering,
  pagination, and sorting capabilities. The API may expose additional
  capabilities not present in the UI, but the reverse is a defect

## Python (Backend)

### Style

- **Formatter**: ruff format (black-compatible)
- **Linter**: ruff check
- **Line length**: 88 characters (ruff default)
- **Quotes**: double quotes for strings
- **Imports**: sorted by ruff (isort-compatible), grouped as:
  1. Standard library
  2. Third-party
  3. Local application

### Type Hints

- All function signatures MUST have type hints for parameters and return values
- Use `from __future__ import annotations` for modern annotation syntax
- Use `Optional[X]` or `X | None` for nullable types

### Naming

- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions/methods**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private**: prefix with single underscore `_`

### FastAPI Conventions

- Endpoint handlers should be thin: validate, call service, return response
- Use dependency injection (`Depends()`) for database sessions, auth, etc.
- All endpoints must have OpenAPI documentation (summary, description)
- Use appropriate HTTP status codes and response models

### SQLAlchemy Conventions

- Use SQLAlchemy 2.0 style (mapped_column, declarative base)
- All models inherit from a common `Base` class
- Use UUID primary keys
- Always include `created_at` and `updated_at` timestamps
- Define relationships explicitly with `back_populates`

### Pydantic Conventions

- Separate schemas for Create, Update, and Response
- Use `model_config = ConfigDict(from_attributes=True)` for ORM integration
- Validate at the schema level, not in endpoints or services

### Testing Conventions

- Test files mirror the `app/` directory structure
- Use `pytest` with async support (`pytest-asyncio`)
- Use fixtures for database sessions, test data, authenticated clients
- Test naming: `test_<what>_<condition>_<expected_result>`
- Example: `test_get_cve_not_found_returns_404`

## TypeScript (Frontend)

### Style

- **Formatter**: Prettier (via ESLint)
- **Linter**: ESLint with TypeScript plugin
- **Line length**: 80 characters
- **Quotes**: double quotes
- **Semicolons**: required

### Naming

- **Files**: `PascalCase.tsx` for components, `camelCase.ts` for utilities
- **Components**: `PascalCase`
- **Functions/hooks**: `camelCase`
- **Types/interfaces**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`

### React Conventions

- Functional components only (no class components)
- Use custom hooks for reusable logic
- Keep components focused: one component, one file
- Props interfaces defined in the same file as the component
- Use React Query for server state management

### Component Structure

```typescript
// 1. Imports
import { useState } from "react";
import { Button } from "@/components/ui/button";

// 2. Types
interface MyComponentProps {
  title: string;
  onAction: () => void;
}

// 3. Component
export function MyComponent({ title, onAction }: MyComponentProps) {
  // hooks first
  const [state, setState] = useState(false);

  // handlers
  const handleClick = () => { ... };

  // render
  return ( ... );
}
```

### Testing Conventions

- Use Vitest as test runner
- Use React Testing Library for component tests
- Test user behavior, not implementation details
- Co-locate tests with components when practical

## Git Conventions

### Branch Naming

- `feature/<short-description>` — new features
- `fix/<short-description>` — bug fixes
- `docs/<short-description>` — documentation changes
- `refactor/<short-description>` — code refactoring

### Commit Messages

- Use conventional commits format: `type: description`
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`
- Keep the first line under 72 characters
- Use imperative mood: "add feature" not "added feature"
- Examples:
  - `feat: add CVE severity filtering to dashboard`
  - `fix: correct CVSS score parsing for NVD API v2`
  - `docs: update data model with SecurityUpdate table`
  - `test: add integration tests for CVE sync service`
