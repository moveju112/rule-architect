---
description: Generate or update a runtime-neutral AI rule set (AI_RULES.md + docs/*.md)
argument-hint: "[project-path] [--update]"
---

Invoke the `rule-architect` skill.

Target: $ARGUMENTS (default: current working directory).

Follow the skill's SKILL.md exactly:

1. Map existing rule entries and run `manifest.py check` before any write. Then use
   `scan.py --output <temporary-scan.json>` for the signal manifest — its
   `met` / `not_met` / `unknown` states and `truncated` flags define what is proven.
   Stop if `brokenRuleLinks` is non-empty.
2. On an existing rule set, exit 1 from that manifest check is a conflict:
   stop and report, never overwrite a hand-edited file. Exit 2 means legacy — preserve one
   identifiable source body while structurally migrating it.
3. On every update, create the normal-file `AI_RULES.md` source when absent and replace both
   runtime entries with same-mode direct pointers to it. A custom loader name may stay; a custom
   neutral index name may not.
4. Run `decisions.py init` from the saved scan, resolve every `unknown`, and record a reason
   for every override. Preserve reviewed resolutions on update.
5. Write or diff-edit docs, then AI_RULES.md, then both runtime entry files in one mode.
6. `manifest.py record` every generated file written; the current manifest requires the
   persisted decision record.
7. Both gates: `verify_rules.py` (strict — do not reach for `--lenient` to get a green
   run) and the quiz via `quiz.py scaffold` → isolated subagent → `quiz.py grade`.

Report which conditional docs were generated and why, any decision taken against the
scan signals, and both gate results.
