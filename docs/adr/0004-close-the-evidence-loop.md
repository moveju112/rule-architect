# ADR 0004 — Close the scan-to-verification evidence loop

Date: 2026-09-07
Status: accepted

## Context

The scanner emitted conditional-document booleans, but its output was not persisted and
verification did not consume it. A capped scan could therefore look like a complete negative,
and a model could ignore positive signals while still passing the form gate. Vendor bundles and
fixture files also produced false signals. The correction harvester only searched Claude Code's
exact start-directory bucket, missing Codex sessions and work started from a parent directory.

Citation checking had the inverse problem: any slash-shaped token looked like evidence, so imports,
API routes, URIs, and templates obscured real stale paths.

## Decision

1. `scan.py` emits stable decision IDs and tri-state `met` / `not_met` / `unknown` results. Negative
   signals become `unknown` when the relevant scan is incomplete. Vendor, fixture, and minified code
   cannot decide project rules; response rules require observable serialization code. Deployment
   workflows under `.github/` and `.forgejo/` remain visible even when the source scan is capped.
2. `.rule-architect/decisions.json` records all six conditional-doc choices. `verify_rules.py`
   rejects missing decisions, unresolved unknowns, selected-but-unlinked docs, and source commits
   made after the scan. Rule-output-only commits do not stale the record.
3. `manifest@3` requires that decision record. Older generated layouts remain readable and warn
   until their next rule-architect record/update migrates them.
4. `harvest.py` reads both local Claude Code and Codex stores, attributes sessions with structured
   cwd/tool-path evidence, rejects sibling-prefix matches and subagent prompts, and deduplicates
   cross-runtime copies.
5. Backticked `evidence: path:line` explicitly opts into freshness checking. Existing implicit path
   citations remain compatible, while common non-path code tokens are excluded.

## Consequences

- Conditional documents now have an auditable path from measurement to final routing.
- Partial evidence causes an explicit review instead of silently suppressing a document.
- Existing projects are not broken immediately; recording with the current manifest upgrades the
  gate and requires decisions from that run onward.
- Harvest coverage is broader but remains bounded and local. Per-source counts and truncation flags
  expose what was not inspected.
- The scanner and citation checker remain heuristics. Ambiguous evidence can always use the explicit
  marker, and ambiguous scan results remain `unknown` until a human resolves them.
