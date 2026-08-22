# ADR 0001 — Move quality from judgement to enforced contract

Date: 2026-08-22
Status: accepted

## Context

`SKILL.md` stated a detailed output contract — budgets, rule format, routing triggers,
non-derivable filter — but `verify_rules.py` only checked line counts, links,
placeholders, and file existence. Everything else rode on the model's judgement, so a
weaker model produced a rule set that failed the contract and still exited 0.

Three further gaps compounded it. Citation freshness checked only that a file existed,
never that the cited line was inside it. Update Mode relied on one marker at the bottom
of CLAUDE.md, which cannot distinguish a hand-written rule from a stale generated one.
The quiz — the only content gate — had no runner, no schema, and no archived result, so
"the quiz passed" was a sentence in a chat log.

## Decision

1. `verify_rules.py` is strict by default and enforces the contract it documents:
   required docs, Core Rules budget, routing triggers, graded-rule format, and citation
   line ranges. `--lenient` exists for a project that has consciously accepted a bigger
   budget, and its use must be stated in the report.
2. `scan.py` measures the signals that decide the conditional doc set and prints them
   as JSON with explicit caps and `truncated` flags, so the same commit yields the same
   document set.
3. `manifest.py` records a hash per generated file. On mismatch the policy is to stop
   and report a conflict rather than guess which side is authoritative. Projects with no
   manifest are treated as entirely hand-written.
4. `quiz.py` owns the parts a script can own honestly — prompt, required mix, pass rule,
   archived result — and explicitly does not execute a model, because there is no
   portable way for it to spawn the isolated subagent the gate depends on.
5. `tests/test_rules.py` clones one known-good fixture per case and breaks exactly one
   contract, so every check has a test that fails when the check stops working.

## Consequences

- A rule set that violates the documented contract now fails instead of passing.
- Update runs stop on a hand edit instead of overwriting it, which costs a round trip
  and removes a class of silent data loss.
- Rule-format enforcement applies only to lines starting with a grade marker.
  `ARCHITECTURE.md` (a directory map) and `PITFALLS.md` (symptom → cause → fix) keep
  their own shapes; forcing the graded form on them would contradict `SKILL.md` itself.
- Bare filename patterns such as `UPPERCASE.md` are deliberately not treated as
  citations. The cost is that a root-level `manage.py` cited without a line number goes
  unchecked; the alternative failed every File Layout section that documents a naming
  convention.
