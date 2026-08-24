#!/usr/bin/env python3
"""Compile machine-checkable rules into a working PreToolUse guard.

Usage:
  python3 hookgen.py emit  <project-root> --rules <spec.json> [--write]
  python3 hookgen.py check <project-root>

A rule a regex can catch ("never call create_async_engine directly") is worth
more as an enforced hook than as a paragraph an agent may or may not have loaded.
`emit` takes the promotion candidates, validates them, and installs:

  .rule-architect/hooks.json      the rules, normalized (the guard reads this)
  .claude/hooks/rule_guard.py     the generic guard (copied, never customised)

Without `--write` the settings.json entry is printed for the user to paste.
With `--write` it is merged into `<root>/.claude/settings.json` — additive and
idempotent, so re-running never duplicates the entry. Only pass `--write` when
the user asked for it: this edits their harness configuration.

Spec format (`--rules`):
  {"rules": [{"id": "no-direct-engine",
              "glob": "src/**/*.py",
              "forbid": "create_async_engine\\\\(",
              "message": "reuse get_engine() — docs/DB_RULES.md",
              "evidence": "src/db.py:12"}]}

Exit codes: 0 = ok, 1 = invalid spec or write failure.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

SPEC_REL = Path('.rule-architect') / 'hooks.json'
GUARD_REL = Path('.claude') / 'hooks' / 'rule_guard.py'
SETTINGS_REL = Path('.claude') / 'settings.json'
MATCHER = 'Edit|Write|MultiEdit|NotebookEdit'
ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,63}$')
SCHEMA = 'rule-architect/hooks@1'


def hookCommand(root):
    return f'python3 "$CLAUDE_PROJECT_DIR/{GUARD_REL.as_posix()}"'


# Every rule must be enforceable as written: a bad regex or a missing glob is a
# silently dead rule, which is worse than no rule at all
def validate(rules):
    problems, seen = [], set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            problems.append(f'rule {index}: must be an object')
            continue
        identifier = str(rule.get('id', ''))
        if not ID_RE.match(identifier):
            problems.append(f'rule {index}: id {identifier!r} must be lowercase '
                            f'letters, digits, dashes (2-64 chars)')
        elif identifier in seen:
            problems.append(f'rule {index}: duplicate id {identifier!r}')
        seen.add(identifier)
        for field in ('glob', 'forbid', 'message'):
            if not str(rule.get(field, '')).strip():
                problems.append(f'rule {index}: empty `{field}`')
        pattern = str(rule.get('forbid', ''))
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f'rule {index}: forbid is not a valid regex: {exc}')
    return problems


def normalize(rules):
    keep = ('id', 'glob', 'forbid', 'message', 'evidence')
    return [{field: rule[field] for field in keep if field in rule} for rule in rules]


# Merge the guard entry into the project's settings.json without touching anything else
def mergeSettings(root):
    path = root / SETTINGS_REL
    settings = {}
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            print(f'FAIL: {SETTINGS_REL.as_posix()} is not valid JSON: {exc}', file=sys.stderr)
            return 1
        if not isinstance(settings, dict):
            print(f'FAIL: {SETTINGS_REL.as_posix()} is not an object', file=sys.stderr)
            return 1
    command = hookCommand(root)
    hooks = settings.setdefault('hooks', {})
    entries = hooks.setdefault('PreToolUse', [])
    if not isinstance(entries, list):
        print('FAIL: hooks.PreToolUse is not a list', file=sys.stderr)
        return 1
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get('hooks', []) or []:
            if isinstance(hook, dict) and hook.get('command') == command:
                print(f'ALREADY INSTALLED: {SETTINGS_REL.as_posix()} already runs the guard')
                return 0
    entries.append({'matcher': MATCHER,
                    'hooks': [{'type': 'command', 'command': command}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    print(f'MERGED: guard registered in {SETTINGS_REL.as_posix()}')
    return 0


def commandEmit(root, rulesPath, write):
    try:
        payload = json.loads(Path(rulesPath).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'FAIL: cannot read rules spec: {exc}', file=sys.stderr)
        return 1
    rules = payload.get('rules') if isinstance(payload, dict) else payload
    if not isinstance(rules, list) or not rules:
        print('FAIL: spec must carry a non-empty `rules` list', file=sys.stderr)
        return 1
    problems = validate(rules)
    if problems:
        for problem in problems:
            print(f'FAIL: {problem}', file=sys.stderr)
        return 1

    spec = root / SPEC_REL
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(json.dumps({'schema': SCHEMA, 'rules': normalize(rules)},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    guard = root / GUARD_REL
    guard.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parent / 'rule_guard.py', guard)
    guard.chmod(0o755)
    print(f'EMITTED: {len(rules)} rules -> {SPEC_REL.as_posix()}, '
          f'guard -> {GUARD_REL.as_posix()}')

    if write:
        return mergeSettings(root)
    snippet = {'hooks': {'PreToolUse': [
        {'matcher': MATCHER, 'hooks': [{'type': 'command', 'command': hookCommand(root)}]}]}}
    print(f'\nAdd this to {SETTINGS_REL.as_posix()} (or re-run with --write):')
    print(json.dumps(snippet, ensure_ascii=False, indent=2))
    return 0


# Report what is installed — used on an update run before touching anything
def commandCheck(root):
    spec, guard = root / SPEC_REL, root / GUARD_REL
    rules = []
    if spec.is_file():
        try:
            rules = json.loads(spec.read_text(encoding='utf-8')).get('rules') or []
        except json.JSONDecodeError:
            print(f'FAIL: {SPEC_REL.as_posix()} is not valid JSON', file=sys.stderr)
            return 1
    registered = False
    settings = root / SETTINGS_REL
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            data = {}
        command = hookCommand(root)
        for entry in (data.get('hooks', {}) or {}).get('PreToolUse', []) or []:
            if not isinstance(entry, dict):
                continue
            registered = registered or any(
                isinstance(hook, dict) and hook.get('command') == command
                for hook in entry.get('hooks', []) or [])
    report = {'specPresent': spec.is_file(), 'guardPresent': guard.is_file(),
              'registered': registered, 'ruleCount': len(rules),
              'ruleIds': [rule.get('id') for rule in rules if isinstance(rule, dict)]}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser(description='rule-architect hook compiler')
    sub = parser.add_subparsers(dest='command', required=True)

    emitParser = sub.add_parser('emit')
    emitParser.add_argument('root')
    emitParser.add_argument('--rules', required=True)
    emitParser.add_argument('--write', action='store_true',
                            help="merge the entry into the project's .claude/settings.json")

    checkParser = sub.add_parser('check')
    checkParser.add_argument('root')

    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    if args.command == 'emit':
        return commandEmit(root, args.rules, args.write)
    return commandCheck(root)


if __name__ == '__main__':
    sys.exit(main())
