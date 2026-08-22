#!/usr/bin/env python3
"""Verification script for rule-architect output.

Usage: python3 verify_rules.py <project-root>

Checks:
1. CLAUDE.md exists + line budget (60 target, fail above 80)
2. docs/*.md line budget (150 target, fail above 190)
3. docs/tasks/*.md playbook line budget (80 target, fail above 100)
4. Bidirectional link integrity:
   - every docs file CLAUDE.md links actually exists
   - every generated docs/**/UPPERCASE.md is linked from CLAUDE.md
5. leftover placeholder scan (TBD, TODO, FIXME, XXX, <placeholder>)
6. linked docs use UPPERCASE naming
7. evidence freshness: every `path/file.ext[:line]` a rule cites must exist
8. AGENTS.md pointer exists + references CLAUDE.md (not a content copy)

Exit code: 0 = pass, 1 = fail (reasons on stderr)
"""
import re
import sys
from pathlib import Path

# Budget constants
INDEX_TARGET, INDEX_HARD = 60, 80
DOC_TARGET, DOC_HARD = 150, 190
TASK_TARGET, TASK_HARD = 80, 100
PLACEHOLDER_RE = re.compile(r'\b(TBD|TODO|FIXME|XXX)\b|<placeholder>', re.IGNORECASE)
LINK_RE = re.compile(r'\[[^\]]*\]\((docs/[^)]+\.md)\)')
# Backticked path citation: contains a slash + an extension, optional :line suffix
CITATION_RE = re.compile(r'`([\w][\w./-]*/[\w.-]+\.[A-Za-z]{1,4})(?::\d+)?`')


# Count a file's lines and judge it against the budget
def checkBudget(path, target, hard, errors, warnings):
    lines = path.read_text(encoding='utf-8').count('\n') + 1
    if lines > hard:
        errors.append(f'{path.name}: {lines} lines > hard limit {hard}')
    elif lines > target:
        warnings.append(f'{path.name}: {lines} lines > target {target}')
    return lines


# Check that every path a rule cites exists in the project (evidence freshness)
def checkCitations(path, root, errors):
    text = path.read_text(encoding='utf-8')
    for rel in sorted(set(CITATION_RE.findall(text))):
        # doc-to-doc links are covered by the link integrity check
        if rel.startswith('docs/'):
            continue
        if not (root / rel).is_file():
            errors.append(f'{path.name}: stale citation, file not found: {rel}')


# Find leftover placeholders in prose (code blocks excluded)
def checkPlaceholders(path, errors):
    text = path.read_text(encoding='utf-8')
    # strip code blocks before checking
    stripped = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    for lineNo, line in enumerate(stripped.splitlines(), 1):
        if PLACEHOLDER_RE.search(line):
            errors.append(f'{path.name}:{lineNo}: placeholder remains: {line.strip()[:60]}')


def main():
    if len(sys.argv) != 2:
        print('usage: verify_rules.py <project-root>', file=sys.stderr)
        return 1
    root = Path(sys.argv[1])
    claudeMd = root / 'CLAUDE.md'
    docsDir = root / 'docs'
    errors, warnings = [], []

    # 1. CLAUDE.md exists + budget
    if not claudeMd.is_file():
        print(f'FAIL: {claudeMd} not found', file=sys.stderr)
        return 1
    checkBudget(claudeMd, INDEX_TARGET, INDEX_HARD, errors, warnings)
    checkPlaceholders(claudeMd, errors)
    checkCitations(claudeMd, root, errors)

    # 2. collect the docs CLAUDE.md links
    linked = set(LINK_RE.findall(claudeMd.read_text(encoding='utf-8')))

    # 3. link targets exist + UPPERCASE naming
    for rel in sorted(linked):
        target = root / rel
        if not target.is_file():
            errors.append(f'CLAUDE.md links missing file: {rel}')
            continue
        stem = target.stem
        if stem != stem.upper():
            errors.append(f'linked doc not UPPERCASE: {rel}')
        # task playbooks use their own budget
        if rel.startswith('docs/tasks/'):
            checkBudget(target, TASK_TARGET, TASK_HARD, errors, warnings)
        else:
            checkBudget(target, DOC_TARGET, DOC_HARD, errors, warnings)
        checkPlaceholders(target, errors)
        checkCitations(target, root, errors)

    # 4. reverse direction: find docs/**/UPPERCASE.md that nothing links
    if docsDir.is_dir():
        for doc in sorted(docsDir.rglob('*.md')):
            relPath = doc.relative_to(root).as_posix()
            if doc.stem == doc.stem.upper() and relPath not in linked:
                errors.append(f'generated doc not linked in CLAUDE.md: {relPath}')

    # 5. AGENTS.md pointer — exists, references CLAUDE.md, is not a content copy
    agentsMd = root / 'AGENTS.md'
    if not agentsMd.is_file():
        errors.append('AGENTS.md pointer not found')
    else:
        agentsText = agentsMd.read_text(encoding='utf-8')
        if 'CLAUDE.md' not in agentsText:
            errors.append('AGENTS.md does not reference CLAUDE.md')
        # a pointer must stay short — copying rule bodies causes drift
        if agentsMd.read_text(encoding='utf-8').count('\n') + 1 > 15:
            errors.append('AGENTS.md too long — should be a pointer, not a rule copy')

    # print results
    for w in warnings:
        print(f'WARN: {w}', file=sys.stderr)
    if errors:
        for e in errors:
            print(f'FAIL: {e}', file=sys.stderr)
        return 1
    print(f'PASS: CLAUDE.md + {len(linked)} docs verified')
    return 0


if __name__ == '__main__':
    sys.exit(main())
