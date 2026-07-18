---
name: new-api-endpoint
description: Guided workflow for creating a new API endpoint in the Sentinel backend. Ensures proper schema, service layer, and test coverage.
---

## Workflow: Creating a New API Endpoint

Follow these steps when adding a new API endpoint.

### Step 1: Verify specification

1. Read the relevant feature specification in `docs/features/<domain>/`
   (use Glob with `docs/features/**/<name>.md` to locate the file)
2. Verify the endpoint is defined in the specification
3. If not, update the specification first

### Step 2: Define the Pydantic schemas

1. Create or update schemas in `backend/app/schemas/`
2. Define request body schema (if POST/PUT/PATCH)
3. Define response schema
4. Define query parameter schema (if needed for filtering/pagination)

### Step 3: Implement the service layer

1. Create or update service functions in `backend/app/services/`
2. Business logic goes here, NOT in the endpoint handler
3. The service should accept typed parameters and return typed results
4. Handle errors by raising appropriate exceptions

### Step 4: Create the endpoint

1. Add the endpoint in the appropriate router in `backend/app/api/v1/`
2. Use dependency injection for database session, current user, permissions
3. The endpoint should be thin: validate input, call service, return response
4. Add proper OpenAPI documentation (summary, description, response codes)

### Step 5: Update API specification

1. Update `docs/api-spec.md` with the new endpoint details

### Step 6: Write tests

1. Create tests in `backend/tests/test_api/`
2. Mark all endpoint tests with `@pytest.mark.e2e`
3. Use the `client` fixture (provides HTTP test client with DB override)
4. Test cases required:
   - Happy path with valid data
   - Validation errors (invalid input)
   - Authentication (unauthenticated request → 401)
   - Authorization (insufficient permissions → 403)
   - Edge cases (empty results, not found, etc.)
5. For mutations, assert audit event creation with correct field values
   (see `docs/features/platform/testing-strategy.md`, Audit Trail Testing)
6. Run `cd backend && pytest` and verify all tests pass
