# ADR 0003 — Update normalizes legacy rule entries

Date: 2026-09-07
Status: accepted

Amends: ADR 0002

## Context

ADR 0002 established `AI_RULES.md` as the neutral source but required separate migration approval
when an existing generated layout still stored its index inside a runtime-owned entry. As a result,
running `--update` could complete without producing the output contract that the same skill requires
for a new rule set.

## Decision

1. An explicit `--update` request authorizes structural migration to `AI_RULES.md`.
2. The legacy index body is preserved before runtime entries are replaced.
3. Both runtime entries point directly to `AI_RULES.md` using one entry mode.
4. Intentional custom loader names may remain, but the neutral index is always `AI_RULES.md`.
5. Manifest conflicts, divergent source bodies, and broken pointers still stop the update.
6. The complete migrated rule set is recorded before ordinary diff edits continue.

## Consequences

- Fresh generation and update finish with the same runtime-neutral file contract.
- A user no longer needs a second approval merely to normalize structure after requesting an update.
- Structural migration preserves content; it does not authorize silent conflict resolution or a
  whole-file content rewrite.
