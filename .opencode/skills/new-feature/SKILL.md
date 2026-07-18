---
name: new-feature
description: Guided workflow for adding a new feature to the Sentinel platform. Ensures specification is written first, then implementation follows the project conventions.
---

## Workflow: Adding a New Feature

Follow these steps in order when adding a new feature to Sentinel.

### Step 0: Check ideas list

1. Read `docs/drafts/ideas.md`
2. Check if any existing idea corresponds to the feature being implemented
3. If a matching idea is found, ask the user:
   "The idea '<idea text>' in docs/drafts/ideas.md seems to correspond to this
   feature. Should I remove it from the ideas list?"
4. If the user confirms, remove the bullet point from the file, stage ONLY
   `docs/drafts/ideas.md`, and commit with message:
   `docs: remove idea promoted to spec - <feature-name>`
   Do NOT include any other file in this commit.
5. If no matching idea is found, or the user declines, proceed to Step 1

### Step 1: Write the specification

1. Create a new file in `docs/features/<domain>/<feature-name>.md`
   where `<domain>` is one of: `identity`, `tickets`, `packages`,
   `integrations`, `platform`. Ask the user which domain
   subdirectory is appropriate if it is not obvious from context.
2. The specification MUST include:
   - **Purpose**: what the feature does and why it is needed
   - **Data Model**: new or modified database tables/columns
   - **API Endpoints**: new or modified endpoints with request/response schemas
   - **Business Rules**: logic, validations, constraints
   - **Security**: auth/permission requirements
   - **Background Tasks**: any async processing needed
3. If the specification is a **sub-specification** (i.e., it specializes or
   implements a part of a broader parent spec), it MUST include immediately
   after the H1 title and before the `---` separator:

   ```markdown
   **Parent spec**: `docs/features/<domain>/<parent-name>.md`
   **Sibling specs**: `docs/features/<domain>/<sibling-1>.md`, `docs/features/<domain>/<sibling-2>.md`
   **Inherited concerns**: <concise list of responsibilities defined in the
   parent spec that apply to this sub-spec without being redefined here>
   ```

   - **Parent spec** (required): path to the parent specification.
   - **Sibling specs** (optional): other sub-specs at the same level under the
     same parent. Include only when siblings exist. Helps the reader understand
     the full picture without navigating back to the parent.
   - **Inherited concerns** (required): signals which rules (e.g., token
     format, error code namespace, session lifecycle) are defined in the parent
     and not repeated locally. The reader knows to load the parent spec for
     those details.

4. Review the specification with the user before proceeding

### Step 2: Update shared specifications

1. If the feature introduces new models, update `docs/data-model.md`
2. If the feature adds API endpoints, update `docs/api-spec.md`
3. If the feature affects architecture, update `docs/architecture.md`

### Step 3: Implement the backend

1. Create SQLAlchemy models in `backend/app/models/`
2. Create Pydantic schemas in `backend/app/schemas/`
3. Create service layer in `backend/app/services/`
4. Create API endpoints in `backend/app/api/v1/`
5. If needed, create Celery tasks in `backend/app/tasks/`
6. Generate Alembic migration: `cd backend && alembic revision --autogenerate -m "description"`

### Step 4: Write tests

1. Backend tests in `backend/tests/` mirroring the `app/` structure
2. Apply markers per `docs/features/platform/testing-strategy.md`:
   - `@pytest.mark.unit` for pure logic (no DB/Redis/network)
   - `@pytest.mark.integration` for service-layer tests (with DB)
   - `@pytest.mark.e2e` for API endpoint tests (with HTTP client)
3. Use the shared fixtures: `db_session` for integration, `client` for e2e
4. For every mutation covered by an audit trail, assert correct event creation
   (see testing-strategy.md, Audit Trail Testing)
5. Run all tests and verify they pass: `cd backend && pytest`

### Step 5: Review

1. Invoke `@test-reviewer` for test quality review
2. Verify CI/CD pipelines don't need updates
