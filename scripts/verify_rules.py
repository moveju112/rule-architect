#!/usr/bin/env python3
"""Verification script for rule-architect output.

Usage: python3 verify_rules.py <project-root> [--lenient] [--json]
       [--index CLAUDE.md] [--docs-dir docs]  # 개인 룰 세트는 CLAUDE.local.md + docs_local/

Checks:
1. CLAUDE.md exists + line budget (60 target, hard limit 80)
2. docs/*.md line budget (150 target, hard limit 190)
3. docs/tasks/*.md playbook line budget (80 target, hard limit 100)
4. Bidirectional link integrity:
   - every docs file CLAUDE.md links actually exists
   - every generated docs/**/UPPERCASE.md is linked from CLAUDE.md
5. leftover placeholder scan (TBD, TODO, FIXME, XXX, <placeholder>)
6. linked docs use UPPERCASE naming
7. evidence freshness: every `path[:line]` a rule cites must exist, and the
   line number must fall inside the file
8. AGENTS.md pointer exists + references CLAUDE.md (not a content copy)
9. required docs present (ARCHITECTURE, CODING_RULES, PITFALLS)
10. Core Rules bullet count <= 10
11. Routing table rows carry a non-empty trigger that is not just the file name
12. graded rules (MUST/NEVER/PREFER) carry `why:` and a correct example

Strict by default: exceeding a target budget fails. Pass --lenient to demote
target overruns back to warnings; hard limits fail in both modes.

Exit code: 0 = pass, 1 = fail (reasons on stderr)
"""
import json
import re
import sys
from pathlib import Path

# Budget constants
INDEX_TARGET, INDEX_HARD = 60, 80
# 인덱스 파일명과 룰 디렉터리명. --index / --docs-dir 로 덮어쓴다.
# (개인 룰 세트는 CLAUDE.local.md + docs_local/ 처럼 다른 이름을 쓴다)
INDEX_NAME = 'CLAUDE.md'
DOCS_DIRNAME = 'docs'
DOC_TARGET, DOC_HARD = 150, 190
TASK_TARGET, TASK_HARD = 80, 100
CORE_RULES_MAX = 10
AGENTS_MAX_LINES = 15

REQUIRED_DOCS = ('ARCHITECTURE.md', 'CODING_RULES.md', 'PITFALLS.md')

PLACEHOLDER_RE = re.compile(r'\b(TBD|TODO|FIXME|XXX)\b|<placeholder>', re.IGNORECASE)
LINK_RE = re.compile(r'\[[^\]]*\]\((docs/[^)]+\.md)\)')
ANY_LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')
BACKTICK_RE = re.compile(r'`([^`\n]+)`')
# A citation may end with :42 or :42-58
LINE_SUFFIX_RE = re.compile(r'^(.*?):(\d+)(?:-(\d+))?$')
GRADE_RE = re.compile(r'^\s*[-*]\s*\*\*\[?(MUST|NEVER|PREFER)\b')
HEADING_RE = re.compile(r'^#{1,6}\s')

GIT_REF_PREFIXES = ('origin/', 'upstream/', 'refs/')
GIT_REFS = {'HEAD', 'FETCH_HEAD', 'ORIG_HEAD'}

# Extensionless build files that are still real path citations
BARE_FILES = {
    'Dockerfile', 'Makefile', 'Procfile', 'Justfile', 'Rakefile', 'Gemfile',
    'Vagrantfile', 'Brewfile', 'Caddyfile', 'Jenkinsfile',
}


# Count a file's lines
def lineCount(path):
    return path.read_text(encoding='utf-8').count('\n') + 1


# Judge a file against its budget; strict mode fails on target overrun
def checkBudget(path, target, hard, errors, warnings, strict):
    lines = lineCount(path)
    if lines > hard:
        errors.append(f'{path.name}: {lines} lines > hard limit {hard}')
    elif lines > target:
        bucket = errors if strict else warnings
        bucket.append(f'{path.name}: {lines} lines > target {target}')
    return lines


# Decide whether a backticked string is a path citation at all.
# A bare word with an extension (`UPPERCASE.md`, `lowercase.md`) is a naming
# pattern far more often than a real file, so only these count as citations:
# anything containing a slash, an explicit :line suffix, a known extensionless
# build file, or a dotfile.
def isCitation(raw, hasLine):
    if not raw or any(ch.isspace() for ch in raw):
        return False
    if raw.startswith(('http://', 'https://', '<', '$')):
        return False
    # 슬래시가 있어도 프로젝트 파일이 아닌 것들. 이걸 걸러내지 않으면 오탐이 진짜 지적을 묻는다.
    if raw.startswith(GIT_REF_PREFIXES) or raw in GIT_REFS:
        return False          # git ref: origin/main, refs/heads/x, HEAD
    if raw.startswith('~/'):
        return False          # 홈 경로 — 프로젝트 루트 기준이 아니다
    if any(ch in raw for ch in '*?['):
        return False          # glob 패턴: docs_local/*.md
    return '/' in raw or hasLine or raw in BARE_FILES or raw.startswith('.')


# Split `path:42` / `path:42-58` into a path and its line range
def splitCitation(raw):
    match = LINE_SUFFIX_RE.match(raw)
    if not match:
        return raw, None, None
    path, start, end = match.group(1), int(match.group(2)), match.group(3)
    return path, start, int(end) if end else start


# Check that every path a rule cites exists, and that cited lines are in range
def checkCitations(path, root, errors, linkTargets):
    text = path.read_text(encoding='utf-8')
    seen = set()
    for raw in BACKTICK_RE.findall(text):
        raw = raw.strip()
        if raw in linkTargets or raw in seen:
            continue
        seen.add(raw)
        target, start, end = splitCitation(raw)
        if not isCitation(target, start is not None):
            continue
        if target.startswith('/'):
            errors.append(f'{path.name}: absolute path citation: {raw}')
            continue
        resolved = root / target
        # a trailing slash cites a directory, not a file
        if target.endswith('/'):
            if not resolved.is_dir():
                errors.append(f'{path.name}: stale citation, directory not found: {raw}')
            continue
        if not resolved.is_file():
            errors.append(f'{path.name}: stale citation, file not found: {raw}')
            continue
        if start is None:
            continue
        total = lineCount(resolved)
        if start < 1 or end > total:
            errors.append(
                f'{path.name}: stale citation, {target} has {total} lines '
                f'but cite points at {start}-{end}' if end != start else
                f'{path.name}: stale citation, {target} has {total} lines '
                f'but cite points at line {start}'
            )


# Find leftover placeholders in prose (code blocks excluded)
def stripCode(text):
    return re.sub(r'```.*?```', '', text, flags=re.DOTALL)


def checkPlaceholders(path, errors):
    for lineNo, line in enumerate(stripCode(path.read_text(encoding='utf-8')).splitlines(), 1):
        if PLACEHOLDER_RE.search(line):
            errors.append(f'{path.name}:{lineNo}: placeholder remains: {line.strip()[:60]}')


# Every graded rule needs a why line and a correct-form example
def checkRuleFormat(path, errors):
    lines = stripCode(path.read_text(encoding='utf-8')).splitlines()
    for index, line in enumerate(lines):
        if not GRADE_RE.match(line):
            continue
        window = lines[index + 1:index + 7]
        body = []
        for follow in window:
            if GRADE_RE.match(follow) or HEADING_RE.match(follow):
                break
            body.append(follow)
        joined = '\n'.join(body)
        # 정본을 다른 룰 문서에 위임한 항목은 면제한다. 중복 제거의 결과물이라
        # why/✅ 를 요구하면 방금 없앤 중복을 다시 쓰게 만든다.
        delegated = ANY_LINK_RE.search(line + '\n' + joined) and 'why:' not in joined
        if delegated:
            continue
        if 'why:' not in joined:
            errors.append(f'{path.name}:{index + 1}: graded rule missing `why:` line')
        if '✅' not in joined:
            errors.append(f'{path.name}:{index + 1}: graded rule missing ✅ example')


# Core Rules section must stay within its bullet budget
def checkCoreRules(claudeMd, errors):
    lines = stripCode(claudeMd.read_text(encoding='utf-8')).splitlines()
    start = None
    for index, line in enumerate(lines):
        if HEADING_RE.match(line) and 'core rules' in line.lower():
            start = index + 1
            break
        if re.match(r'^\s*\d+\.\s*\*\*Core Rules\*\*', line):
            start = index + 1
            break
    if start is None:
        errors.append(f'{INDEX_NAME}: no Core Rules section found')
        return
    bullets = 0
    for line in lines[start:]:
        if HEADING_RE.match(line):
            break
        if re.match(r'^\s*[-*]\s+\S', line):
            bullets += 1
    if bullets > CORE_RULES_MAX:
        errors.append(f'{INDEX_NAME}: Core Rules has {bullets} bullets > {CORE_RULES_MAX}')


# Routing rows must carry a trigger that says more than the file name
def checkRoutingTable(claudeMd, errors):
    lines = claudeMd.read_text(encoding='utf-8').splitlines()
    rows = 0
    for lineNo, line in enumerate(lines, 1):
        if not line.strip().startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 2:
            continue
        linked = ANY_LINK_RE.findall(cells[-1]) or ANY_LINK_RE.findall(line)
        if not linked:
            continue
        docTarget = next((t for t in linked if t.endswith('.md')), None)
        if docTarget is None:
            continue
        rows += 1
        trigger = cells[0]
        if not trigger or set(trigger) <= {'-', ':', ' '}:
            errors.append(f'{INDEX_NAME}:{lineNo}: routing row has an empty trigger')
            continue
        stem = Path(docTarget).stem.lower()
        normalized = re.sub(r'[^a-z0-9]', '', trigger.lower())
        if normalized in (stem.replace('_', ''), stem.replace('_', '') + 'md'):
            errors.append(
                f'{INDEX_NAME}:{lineNo}: routing trigger just repeats the file name '
                f'({trigger!r}) — describe the situation instead'
            )
    if rows == 0:
        errors.append(f'{INDEX_NAME}: no routing table rows link a docs file')


def parseNamedOption(argv, flag, default):
    """--flag value 또는 --flag=value 를 읽고 소비한 토큰을 argv 에서 뺀다."""
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            value = argv[index + 1]
            del argv[index:index + 2]
            return value
        if token.startswith(flag + '='):
            value = token.split('=', 1)[1]
            del argv[index]
            return value
    return default


def main():
    global INDEX_NAME, DOCS_DIRNAME, LINK_RE
    argv = [a for a in sys.argv[1:]]
    INDEX_NAME = parseNamedOption(argv, '--index', INDEX_NAME)
    DOCS_DIRNAME = parseNamedOption(argv, '--docs-dir', DOCS_DIRNAME)
    # 룰 디렉터리명이 바뀌면 링크 정규식도 따라가야 한다 — 안 그러면 링크를 하나도 못 잡아
    # 모든 문서가 "링크 안 됨"으로 오판된다
    LINK_RE = re.compile(rf'\[[^\]]*\]\(({re.escape(DOCS_DIRNAME)}/[^)]+\.md)\)')
    strict = '--lenient' not in argv
    asJson = '--json' in argv
    positional = [a for a in argv if not a.startswith('--')]
    if len(positional) != 1:
        print(
            'usage: verify_rules.py <project-root> [--lenient] [--json] '
            '[--index CLAUDE.md] [--docs-dir docs]',
            file=sys.stderr,
        )
        return 1
    root = Path(positional[0])
    claudeMd = root / INDEX_NAME
    docsDir = root / DOCS_DIRNAME
    errors, warnings = [], []

    # 1. CLAUDE.md exists + budget
    if not claudeMd.is_file():
        print(f'FAIL: {claudeMd} not found', file=sys.stderr)
        return 1

    claudeText = claudeMd.read_text(encoding='utf-8')
    linkTargets = set(ANY_LINK_RE.findall(claudeText))
    checkBudget(claudeMd, INDEX_TARGET, INDEX_HARD, errors, warnings, strict)
    checkPlaceholders(claudeMd, errors)
    checkCitations(claudeMd, root, errors, linkTargets)
    checkCoreRules(claudeMd, errors)
    checkRoutingTable(claudeMd, errors)

    # 2. collect the docs CLAUDE.md links
    linked = set(LINK_RE.findall(claudeText))

    # 3. link targets exist + UPPERCASE naming + per-file checks
    for rel in sorted(linked):
        target = root / rel
        if not target.is_file():
            errors.append(f'{INDEX_NAME} links missing file: {rel}')
            continue
        stem = target.stem
        if stem != stem.upper():
            errors.append(f'linked doc not UPPERCASE: {rel}')
        # task playbooks use their own budget
        if rel.startswith(f'{DOCS_DIRNAME}/tasks/'):
            checkBudget(target, TASK_TARGET, TASK_HARD, errors, warnings, strict)
        else:
            checkBudget(target, DOC_TARGET, DOC_HARD, errors, warnings, strict)
        docLinks = set(ANY_LINK_RE.findall(target.read_text(encoding='utf-8')))
        checkPlaceholders(target, errors)
        checkCitations(target, root, errors, docLinks)
        checkRuleFormat(target, errors)

    # 4. required docs must exist and be linked
    for required in REQUIRED_DOCS:
        rel = f'{DOCS_DIRNAME}/{required}'
        if not (root / rel).is_file():
            errors.append(f'required doc missing: {rel}')
        elif rel not in linked:
            errors.append(f'required doc not linked in {INDEX_NAME}: {rel}')

    # 5. reverse direction: find generated docs that nothing links
    manifest = readManifest(root)
    if docsDir.is_dir():
        for doc in sorted(docsDir.rglob('*.md')):
            relPath = doc.relative_to(root).as_posix()
            if doc.stem != doc.stem.upper() or relPath in linked:
                continue
            if manifest is not None and relPath not in manifest:
                warnings.append(f'hand-written doc not linked in {INDEX_NAME}: {relPath}')
            else:
                errors.append(f'generated doc not linked in {INDEX_NAME}: {relPath}')

    # 6. AGENTS.md pointer — exists, references CLAUDE.md, is not a content copy
    agentsMd = root / 'AGENTS.md'
    if not agentsMd.is_file():
        errors.append('AGENTS.md pointer not found')
    else:
        agentsText = agentsMd.read_text(encoding='utf-8')
        if INDEX_NAME not in agentsText:
            errors.append(f'AGENTS.md does not reference {INDEX_NAME}')
        # a pointer must stay short — copying rule bodies causes drift
        if lineCount(agentsMd) > AGENTS_MAX_LINES:
            errors.append('AGENTS.md too long — should be a pointer, not a rule copy')

    # print results
    if asJson:
        print(json.dumps({
            'pass': not errors,
            'strict': strict,
            'docs': sorted(linked),
            'errors': errors,
            'warnings': warnings,
        }, ensure_ascii=False, indent=2))
        return 1 if errors else 0
    for w in warnings:
        print(f'WARN: {w}', file=sys.stderr)
    if errors:
        for e in errors:
            print(f'FAIL: {e}', file=sys.stderr)
        return 1
    print(f'PASS: {INDEX_NAME} + {len(linked)} docs verified')
    return 0


# Managed-file map written by the generator; absent on hand-built rule sets
def readManifest(root):
    path = root / '.rule-architect' / 'manifest.json'
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    files = data.get('files')
    return files if isinstance(files, dict) else None


if __name__ == '__main__':
    sys.exit(main())
