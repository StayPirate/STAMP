---
description: Run the full test suite for backend and frontend
agent: build
---

Run the complete test suite for the Sentinel project:

1. Run backend tests:
   ```
   cd backend && pytest -v
   ```

2. Run frontend tests:
   ```
   cd frontend && npm test
   ```

3. Run backend linting:
   ```
   cd backend && ruff check . && ruff format --check .
   ```

4. Run frontend linting:
   ```
   cd frontend && npm run lint
   ```

5. Report a summary of results:
   - Total tests run and passed/failed for each
   - Any linting errors found
   - Suggested fixes for any failures
