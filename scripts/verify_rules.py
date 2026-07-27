#!/usr/bin/env python3
"""rule-architect 산출물 검증 스크립트.

사용법: python3 verify_rules.py <project-root>

검사 항목:
1. CLAUDE.md 존재 + 줄 수 예산 (60줄 목표, 80줄 초과 시 실패)
2. docs/*.md 줄 수 예산 (150줄 목표, 190줄 초과 시 실패)
3. docs/tasks/*.md playbook 줄 수 예산 (80줄 목표, 100줄 초과 시 실패)
4. 링크 무결성 양방향:
   - CLAUDE.md가 링크한 docs 파일이 실제 존재
   - 생성된 docs/**/UPPERCASE.md가 전부 CLAUDE.md에서 링크됨
5. placeholder 잔존 스캔 (TBD, TODO, FIXME, XXX, <placeholder>)
6. docs 링크 대상이 UPPERCASE 네이밍인지
7. evidence 신선도: 룰이 인용한 `path/file.ext[:line]`이 실존하는지
8. AGENTS.md 포인터 존재 + CLAUDE.md 참조 (내용 복제 아님)

종료 코드: 0 = 통과, 1 = 실패 (사유는 stderr)
"""
import re
import sys
from pathlib import Path

# 예산 상수
INDEX_TARGET, INDEX_HARD = 60, 80
DOC_TARGET, DOC_HARD = 150, 190
TASK_TARGET, TASK_HARD = 80, 100
PLACEHOLDER_RE = re.compile(r'\b(TBD|TODO|FIXME|XXX)\b|<placeholder>', re.IGNORECASE)
LINK_RE = re.compile(r'\[[^\]]*\]\((docs/[^)]+\.md)\)')
# 백틱으로 감싼 경로 인용: 슬래시 포함 + 확장자, 뒤에 :라인번호 허용
CITATION_RE = re.compile(r'`([\w][\w./-]*/[\w.-]+\.[A-Za-z]{1,4})(?::\d+)?`')


# 파일 줄 수를 세고 예산 초과 여부를 판정한다
def checkBudget(path, target, hard, errors, warnings):
    lines = path.read_text(encoding='utf-8').count('\n') + 1
    if lines > hard:
        errors.append(f'{path.name}: {lines} lines > hard limit {hard}')
    elif lines > target:
        warnings.append(f'{path.name}: {lines} lines > target {target}')
    return lines


# 룰이 인용한 파일 경로가 프로젝트에 실존하는지 검사한다 (evidence 신선도)
def checkCitations(path, root, errors):
    text = path.read_text(encoding='utf-8')
    for rel in sorted(set(CITATION_RE.findall(text))):
        # 문서 간 링크는 링크 무결성 검사가 담당
        if rel.startswith('docs/'):
            continue
        if not (root / rel).is_file():
            errors.append(f'{path.name}: stale citation, file not found: {rel}')


# 본문에서 placeholder 잔존을 찾는다 (코드블록 내부 제외)
def checkPlaceholders(path, errors):
    text = path.read_text(encoding='utf-8')
    # 코드블록 제거 후 검사
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

    # 1. CLAUDE.md 존재 + 예산
    if not claudeMd.is_file():
        print(f'FAIL: {claudeMd} not found', file=sys.stderr)
        return 1
    checkBudget(claudeMd, INDEX_TARGET, INDEX_HARD, errors, warnings)
    checkPlaceholders(claudeMd, errors)
    checkCitations(claudeMd, root, errors)

    # 2. CLAUDE.md가 링크한 docs 수집
    linked = set(LINK_RE.findall(claudeMd.read_text(encoding='utf-8')))

    # 3. 링크 대상 존재 + UPPERCASE 검사
    for rel in sorted(linked):
        target = root / rel
        if not target.is_file():
            errors.append(f'CLAUDE.md links missing file: {rel}')
            continue
        stem = target.stem
        if stem != stem.upper():
            errors.append(f'linked doc not UPPERCASE: {rel}')
        # tasks playbook은 별도 예산 적용
        if rel.startswith('docs/tasks/'):
            checkBudget(target, TASK_TARGET, TASK_HARD, errors, warnings)
        else:
            checkBudget(target, DOC_TARGET, DOC_HARD, errors, warnings)
        checkPlaceholders(target, errors)
        checkCitations(target, root, errors)

    # 4. 역방향: docs/**/UPPERCASE.md 중 미링크 파일 검사
    if docsDir.is_dir():
        for doc in sorted(docsDir.rglob('*.md')):
            relPath = doc.relative_to(root).as_posix()
            if doc.stem == doc.stem.upper() and relPath not in linked:
                errors.append(f'generated doc not linked in CLAUDE.md: {relPath}')

    # 5. AGENTS.md 포인터 검사 — 존재 + CLAUDE.md 참조 + 내용 복제 아님
    agentsMd = root / 'AGENTS.md'
    if not agentsMd.is_file():
        errors.append('AGENTS.md pointer not found')
    else:
        agentsText = agentsMd.read_text(encoding='utf-8')
        if 'CLAUDE.md' not in agentsText:
            errors.append('AGENTS.md does not reference CLAUDE.md')
        # 포인터는 짧아야 함 — 룰 본문을 복제하면 드리프트
        if agentsMd.read_text(encoding='utf-8').count('\n') + 1 > 15:
            errors.append('AGENTS.md too long — should be a pointer, not a rule copy')

    # 결과 출력
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
