#!/usr/bin/env python3
"""Persist and verify the conditional-document decisions from scan.py.

Usage:
  python3 decisions.py init  <project-root> --scan <scan.json> [--docs-dir docs] [--replace]
  python3 decisions.py check <project-root> [--index AI_RULES.md] [--docs-dir docs]
                             [--entries CLAUDE.md,AGENTS.md] [--json]

The scan measures signals. This file records which document satisfies each positive
signal and why an uncertain or negative signal was overridden. verify_rules.py calls
the same validator so a model cannot ignore scan.py and still pass the form gate.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 검증 실행이 설치된 스킬 디렉터리에 캐시 파일을 남기지 않게 한다.
sys.dont_write_bytecode = True

from scan import runGit

DECISIONS_REL = Path('.rule-architect') / 'decisions.json'
SCHEMA = 'rule-architect/decisions@1'
SUPPORTED_SCAN_SCHEMAS = {'rule-architect/scan@1', 'rule-architect/scan@2'}
DECISION_ID_BY_DOC = {
    'CONTROLLER_RULES.md': 'controller',
    'ENUM_CODES.md': 'enum-codes',
    'RESPONSE_KEYS.md': 'response-keys',
    'DB_RULES.md': 'database',
    'DEPLOY.md': 'deploy',
    'tasks/ADD_<TASK>.md': 'recurring-task',
}
REQUIRED_DECISION_IDS = set(DECISION_ID_BY_DOC.values())
DEFAULT_DOC_BY_ID = {identifier: doc for doc, identifier in DECISION_ID_BY_DOC.items()}


# JSON 파일 하나를 객체로 읽는다.
def readJson(path):
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, 'top level must be an object'
    return payload, None


# 현재 로컬 HEAD를 읽되 Git 저장소가 아니면 None을 반환한다.
def gitHead(root):
    output = runGit(root, 'rev-parse', 'HEAD')
    return output.strip() if output else None


# 두 커밋 사이에서 대상 프로젝트에 바뀐 파일을 읽는다.
def changedFilesSince(root, oldHead, currentHead):
    output = runGit(root, 'diff', '--relative', '--name-only', f'{oldHead}..{currentHead}', '--', '.')
    return output.splitlines() if output is not None else None


# 룰 생성 자체로 바뀐 파일은 스캔 노후화 신호에서 제외한다.
def isRuleOutput(path, docsDirname, indexName, entryNames):
    return (path == indexName or path in entryNames or path.startswith(f'{docsDirname.rstrip("/")}/')
            or path.startswith('.rule-architect/'))


# 스캔 결정을 검증 가능한 선택 기록으로 변환한다.
def buildRecord(root, scan, docsDirname):
    decisions = []
    for source in scan.get('decisions') or []:
        if not isinstance(source, dict):
            continue
        observed = source.get('status')
        if observed not in ('met', 'not_met', 'unknown'):
            met = source.get('met')
            observed = 'met' if met is True else ('unknown' if met is None else 'not_met')
        defaultDoc = str(source.get('doc') or '')
        selected = None
        if observed == 'met' and defaultDoc and '<TASK>' not in defaultDoc:
            selected = f'{docsDirname}/{defaultDoc}'
        decisions.append({
            'id': source.get('id') or DECISION_ID_BY_DOC.get(defaultDoc, defaultDoc),
            'observed': observed,
            'condition': source.get('condition'),
            'evidence': source.get('evidence') or [],
            'defaultDoc': defaultDoc,
            'selectedDoc': selected,
            'resolution': None,
            'reason': None,
        })
    return {
        'schema': SCHEMA,
        'root': root.as_posix(),
        'scanSchema': scan.get('schema'),
        'gitHead': scan.get('gitHead'),
        'truncated': scan.get('truncated') or {},
        'decisions': decisions,
    }


# 기존 결정을 덮지 않고 새 선택 기록을 만든다.
def commandInit(root, scanPath, docsDirname, replace):
    scan, problem = readJson(scanPath)
    if problem:
        print(f'FAIL: cannot read scan manifest: {problem}', file=sys.stderr)
        return 1
    if scan.get('schema') not in SUPPORTED_SCAN_SCHEMAS:
        print(f'FAIL: unsupported scan schema: {scan.get("schema")}', file=sys.stderr)
        return 1
    try:
        scannedRoot = Path(scan.get('root') or '').resolve()
    except (OSError, RuntimeError):
        scannedRoot = None
    if scannedRoot != root:
        print(f'FAIL: scan root does not match project: {scan.get("root")}', file=sys.stderr)
        return 1
    target = root / DECISIONS_REL
    if target.exists() and not replace:
        print(f'FAIL: {DECISIONS_REL.as_posix()} already exists; pass --replace after review',
              file=sys.stderr)
        return 1
    record = buildRecord(root, scan, docsDirname)
    identifiers = [str(item.get('id') or '') for item in record['decisions']]
    missingIds = sorted(REQUIRED_DECISION_IDS - set(identifiers))
    if missingIds or len(identifiers) != len(set(identifiers)):
        detail = ', '.join(missingIds) if missingIds else 'duplicated ids'
        print(f'FAIL: scan decision set is incomplete or ambiguous: {detail}', file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    unresolved = [item['id'] for item in record['decisions']
                  if item['observed'] == 'unknown' or (
                      item['observed'] == 'met' and not item['selectedDoc'])]
    print(f'RECORDED: {len(record["decisions"])} decisions -> {DECISIONS_REL.as_posix()}')
    if unresolved:
        print('REVIEW REQUIRED: ' + ', '.join(str(item) for item in unresolved))
    return 0


# 선택 기록과 실제 라우팅 문서가 일치하는지 검사한다.
def validateDecisionRecord(root, linked, docsDirname, errors, warnings,
                           indexName='AI_RULES.md', entryNames=('CLAUDE.md', 'AGENTS.md')):
    initialErrors = len(errors)
    path = root / DECISIONS_REL
    manifestPath = root / '.rule-architect' / 'manifest.json'
    manifest, _ = readJson(manifestPath) if manifestPath.is_file() else (None, None)
    if not path.is_file():
        marker = root / 'AI_RULES.md'
        generated = marker.is_file() and '<!-- generated by rule-architect -->' in \
            marker.read_text(encoding='utf-8', errors='ignore')
        if manifest and manifest.get('schema') == 'rule-architect/manifest@3':
            errors.append('decision manifest missing; manifest@3 requires scan decisions')
            return 'invalid'
        if generated or manifestPath.is_file():
            warnings.append('decision manifest missing; conditional docs were not cross-checked')
        return 'missing'
    payload, problem = readJson(path)
    if problem:
        errors.append(f'decision manifest unreadable: {problem}')
        return 'invalid'
    if payload.get('schema') != SCHEMA:
        errors.append(f'decision manifest schema must be {SCHEMA}')
        return 'invalid'
    if payload.get('scanSchema') not in SUPPORTED_SCAN_SCHEMAS:
        errors.append('decision manifest has an unsupported scan schema')
    if (manifest and manifest.get('schema') == 'rule-architect/manifest@3'
            and DECISIONS_REL.as_posix() not in (manifest.get('files') or {})):
        errors.append('manifest@3 must track .rule-architect/decisions.json')
    try:
        recordedRoot = Path(payload.get('root') or '').resolve()
    except (OSError, RuntimeError):
        recordedRoot = None
    if recordedRoot != root.resolve():
        errors.append('decision manifest root does not match project')
    recordedHead = payload.get('gitHead')
    currentHead = gitHead(root)
    if recordedHead and currentHead and recordedHead != currentHead:
        changed = changedFilesSince(root, recordedHead, currentHead)
        if changed is None:
            errors.append('decision manifest is stale: recorded Git HEAD cannot be compared')
        else:
            relevant = [path for path in changed
                        if not isRuleOutput(path, docsDirname, indexName, entryNames)]
            if relevant:
                errors.append('decision manifest is stale: source changed since scan: '
                              + ', '.join(relevant[:5]))
    items = payload.get('decisions')
    if not isinstance(items, list) or not items:
        errors.append('decision manifest has no decisions')
        return 'invalid'
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f'decision {index}: must be an object')
            continue
        identifier = str(item.get('id') or '')
        if not identifier or identifier in seen:
            errors.append(f'decision {index}: id is empty or duplicated: {identifier!r}')
        seen.add(identifier)
        observed = item.get('observed')
        resolution = item.get('resolution')
        reason = str(item.get('reason') or '').strip()
        if item.get('defaultDoc') != DEFAULT_DOC_BY_ID.get(identifier):
            errors.append(f'decision {identifier}: default doc does not match the decision id')
        if not isinstance(item.get('condition'), str) or not item['condition'].strip():
            errors.append(f'decision {identifier}: condition is missing')
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or not all(isinstance(value, str) for value in evidence):
            errors.append(f'decision {identifier}: evidence must be a string list')
        elif observed == 'met' and not evidence:
            errors.append(f'decision {identifier}: observed positive has no evidence')
        if observed not in ('met', 'not_met', 'unknown'):
            errors.append(f'decision {identifier}: invalid observed state {observed!r}')
            continue
        if resolution not in (None, 'met', 'not_met'):
            errors.append(f'decision {identifier}: resolution must be met or not_met')
            continue
        if observed == 'unknown' and resolution is None:
            errors.append(f'decision {identifier}: unknown scan result must be resolved')
            continue
        if resolution is not None and not reason:
            errors.append(f'decision {identifier}: manual resolution needs a reason')
        effective = resolution or observed
        selected = item.get('selectedDoc')
        if selected is not None and not isinstance(selected, str):
            errors.append(f'decision {identifier}: selectedDoc must be a string or null')
            continue
        selected = (selected or '').strip()
        if effective == 'met' and not selected:
            errors.append(f'decision {identifier}: effective positive has no selected doc')
            continue
        if effective == 'not_met' and selected:
            errors.append(f'decision {identifier}: effective negative cannot select a doc')
            continue
        if not selected:
            continue
        if '<TASK>' in selected:
            errors.append(f'decision {identifier}: replace the task placeholder with a real file')
            continue
        if Path(selected).is_absolute() or '..' in Path(selected).parts:
            errors.append(f'decision {identifier}: selected doc escapes the project')
            continue
        expectedPrefix = docsDirname.rstrip('/') + '/'
        if not selected.startswith(expectedPrefix):
            errors.append(f'decision {identifier}: selected doc must be under {docsDirname}/')
        elif selected not in linked:
            errors.append(f'decision {identifier}: selected doc is not linked: {selected}')
    missingIds = sorted(REQUIRED_DECISION_IDS - seen)
    if missingIds:
        errors.append('decision manifest is incomplete: ' + ', '.join(missingIds))
    return 'valid' if len(errors) == initialErrors else 'invalid'


# 독립 실행 시 인덱스의 링크를 읽어 같은 검사를 수행한다.
def commandCheck(root, indexName, docsDirname, entryNames, asJson):
    index = root / indexName
    errors, warnings = [], []
    if not index.is_file():
        errors.append(f'{indexName} not found')
        linked = set()
    else:
        pattern = re.compile(rf'\[[^\]]*\]\(({re.escape(docsDirname)}/[^)]+\.md)\)')
        linked = set(pattern.findall(index.read_text(encoding='utf-8')))
    status = validateDecisionRecord(
        root, linked, docsDirname, errors, warnings, indexName, entryNames)
    report = {'pass': not errors, 'status': status, 'errors': errors, 'warnings': warnings}
    if asJson:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for warning in warnings:
            print(f'WARN: {warning}', file=sys.stderr)
        for error in errors:
            print(f'FAIL: {error}', file=sys.stderr)
        if not errors:
            print(f'PASS: {DECISIONS_REL.as_posix()} matches linked docs')
    return 1 if errors else 0


# 하위 명령을 파싱해 기록 또는 검사를 실행한다.
def main():
    parser = argparse.ArgumentParser(description='rule-architect conditional-doc decisions')
    sub = parser.add_subparsers(dest='command', required=True)

    initParser = sub.add_parser('init')
    initParser.add_argument('root')
    initParser.add_argument('--scan', required=True)
    initParser.add_argument('--docs-dir', default='docs')
    initParser.add_argument('--replace', action='store_true')

    checkParser = sub.add_parser('check')
    checkParser.add_argument('root')
    checkParser.add_argument('--index', default='AI_RULES.md')
    checkParser.add_argument('--docs-dir', default='docs')
    checkParser.add_argument('--entries', default='CLAUDE.md,AGENTS.md')
    checkParser.add_argument('--json', action='store_true')

    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    if args.command == 'init':
        scanPath = Path(args.scan).expanduser()
        scanPath = scanPath if scanPath.is_absolute() else root / scanPath
        return commandInit(root, scanPath, args.docs_dir, args.replace)
    entryNames = tuple(name.strip() for name in args.entries.split(',') if name.strip())
    if (not entryNames or len(set(entryNames)) != len(entryNames)
            or any(Path(name).is_absolute() or Path(name).parent != Path('.')
                   for name in entryNames)):
        print('FAIL: --entries must name unique files in the project root', file=sys.stderr)
        return 1
    return commandCheck(root, args.index, args.docs_dir, entryNames, args.json)


if __name__ == '__main__':
    sys.exit(main())
