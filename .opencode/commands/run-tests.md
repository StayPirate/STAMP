---
description: Run the full test suite for the backend
agent: build
---

Run the complete test suite for the Sentinel project:

1. Run backend tests:
   ```
   cd backend && uv run pytest -v
   ```

2. Run backend linting:
   ```
   cd backend && uv run ruff check . && uv run ruff format --check .
   ```

3. Report a summary of results:
   - Total tests run and passed/failed for each
   - Any linting errors found
   - Suggested fixes for any failures
