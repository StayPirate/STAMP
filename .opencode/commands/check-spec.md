---
description: Verify that implementation code conforms to its feature specification
agent: build
---

Review the implementation for the feature specified in $ARGUMENTS.

1. Find and read the feature specification matching `docs/features/**/$ARGUMENTS.md`
   (spec filenames are unique across subdirectories; use Glob to locate the file)
2. Read all related implementation files (models, schemas, services, endpoints,
   frontend components)
3. Compare the implementation against the specification
4. Report any discrepancies:
   - Missing functionality that is specified but not implemented
   - Implementation that deviates from the specification
   - Code that exists without corresponding specification
5. Suggest corrections for any issues found
