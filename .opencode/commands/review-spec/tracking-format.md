# Tracking Format Reference

This document defines the `.tracking.json` schema used by the
`/review-spec` command. The file is stored at
`docs/reviews/.tracking.json`.

## Schema

```json
{
  "specs": {
    "tickets": {
      "enabled": true,
      "abbr": "TKT",
      "cache": {
        "last_review": "2026-05-06T14:30:00+0200",
        "open": {
          "GAP": { "H": 1, "M": 2, "L": 0 },
          "COH": { "H": 0, "M": 1, "L": 0 },
          "DES": { "H": 0, "M": 0, "L": 1 },
          "SEC": { "H": 1, "M": 0, "L": 0 },
          "API": { "H": 0, "M": 0, "L": 0 }
        },
        "resolved": 8,
        "not_reviewed": []
      }
    },
    "rbac": {
      "enabled": true,
      "abbr": "RBAC",
      "cache": null
    },
    "pages": {
      "enabled": false,
      "abbr": "PAG",
      "cache": null
    }
  }
}
```

## Field definitions

- `enabled`: whether the spec is tracked for reviews
- `abbr`: uppercase abbreviation used in finding IDs (e.g., `TKT-GAP-01`)
- `cache`: review status summary, or `null` if never reviewed
  - `last_review`: timestamp of last review (ISO 8601 with timezone:
    `YYYY-MM-DDTHH:MM:SS±HHMM`, e.g., `2026-05-06T14:30:00+0200`).
    This precision is required so that stale detection can identify
    spec modifications committed on the same day as the review
  - `open`: OPEN finding counts per section, per severity (H/M/L)
  - `resolved`: total count of RESOLVED findings
  - `not_reviewed`: array of section abbreviations still showing
    `_Not yet reviewed._` (e.g., `["SEC", "API"]`)

## Initialization and sync rules

When loading `.tracking.json`:

- **File does not exist** (first run): create it with ALL specs currently
  in `docs/features/**/` set to `"enabled": true`, with auto-generated
  `abbr` and `"cache": null`. Write the file to disk immediately.
- **File exists**: load it. For any spec in `docs/features/**/` that is
  NOT present in the JSON, add it as `"enabled": false` with
  auto-generated `abbr` and `"cache": null` (new spec discovered —
  disabled by default). Write the updated file back only if changed.
- For any spec listed in the JSON that no longer exists in
  `docs/features/**/`, remove it from the JSON (stale entry cleanup).

**No review file parsing at startup**: the `cache` field in
`.tracking.json` is always trusted. It is updated by the command itself
whenever a review file is written or modified. The subagent MUST NOT read
or parse review files during data gathering.

## Abbreviation derivation rules

The `abbr` field is generated automatically when a spec is first added
to `.tracking.json` and MUST NEVER be modified afterward (finding IDs
depend on it). Derivation rules:

1. Single-word spec, ≤4 letters: full name uppercased (`rbac` → `RBAC`)
2. Single-word spec, >4 letters: first 3 letters uppercased
   (`tickets` → `TKT`, `admin` → `ADM`)
3. Hyphenated spec, 4+ words: first letter of each word uppercased,
   max 4 chars (`ibs-track-release-detection` → `ITRD`)
4. Hyphenated spec, 2-3 words: take letters from each word to reach
   3-4 chars, prioritizing recognizability
   (`package-model` → `PKM`, `user-service` → `USVC`,
   `sso-authentication` → `SSOA`)
5. If collision with an existing `abbr`: append successive letters from
   the last word until unique (e.g., `ICRD` collides → `ICRE`)
