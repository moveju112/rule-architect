---
description: Generate or update an advanced AI rule set (CLAUDE.md + docs/*.md) for a project
argument-hint: "[project-path] [--update]"
---

Invoke the `rule-architect` skill.

Target: $ARGUMENTS (default: current working directory).

Follow the skill's SKILL.md exactly:

1. `scan.py` for the signal manifest — it decides the conditional doc set, and its
   `truncated` flags say whether the scan was complete.
2. On an existing rule set, `manifest.py check` BEFORE editing. Exit 1 is a conflict:
   stop and report, never overwrite a hand-edited file. Exit 2 means legacy — add only.
3. Write docs, then CLAUDE.md, then the AGENTS.md pointer.
4. `manifest.py record` every file written.
5. Both gates: `verify_rules.py` (strict — do not reach for `--lenient` to get a green
   run) and the quiz via `quiz.py scaffold` → isolated subagent → `quiz.py grade`.

Report which conditional docs were generated and why, any decision taken against the
scan signals, and both gate results.
