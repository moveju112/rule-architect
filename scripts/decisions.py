#!/usr/bin/env python3
"""Persist and verify the conditional-document decisions from scan.py.

Usage:
  python3 decisions.py init  <project-root> --scan <scan.json> [--docs-dir docs/ai-rules] [--replace]
  python3 decisions.py refresh <project-root> --scan <scan.json> [--write]
  python3 decisions.py check <project-root> [--index AI_RULES.md] [--docs-dir docs/ai-rules]
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

from scan import (DEFAULT_MAX_BYTES, DEFAULT_MAX_FILES, fingerprintSources,
                  isExcludedPath, isRuleOutput, runGit, walkProject)
from manifest import commandCheck as checkGeneratedManifest, commandRecord as recordGeneratedFiles

DECISIONS_REL = Path('.rule-architect') / 'decisions.json'
SCHEMA = 'rule-architect/decisions@1'
SUPPORTED_SCAN_SCHEMAS = {'rule-architect/scan@1', 'rule-architect/scan@2',
                          'rule-architect/scan@3'}
DECISION_ID_BY_DOC = {
    'CONTROLLER_RULES.md': 'controller',
    'API_RULES.md': 'api',
    'ENUM_CODES.md': 'enum-codes',
    'RESPONSE_KEYS.md': 'response-keys',
    'DB_RULES.md': 'database',
    'DEPLOY.md': 'deploy',
    'tasks/ADD_<TASK>.md': 'recurring-task',
}
REQUIRED_DECISION_IDS = set(DECISION_ID_BY_DOC.values())
LEGACY_DECISION_IDS = REQUIRED_DECISION_IDS - {'api'}


# 이전 스캔은 API 계층 결정을 생성하지 않았으므로 기존 기록은 여섯 건만 요구한다.
def requiredDecisionIds(scanSchema):
    return REQUIRED_DECISION_IDS if scanSchema == 'rule-architect/scan@3' else LEGACY_DECISION_IDS
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
    record = {
        'schema': SCHEMA,
        'root': root.as_posix(),
        'scanSchema': scan.get('schema'),
        'gitHead': scan.get('gitHead'),
        'truncated': scan.get('truncated') or {},
        'decisions': decisions,
    }
    if 'sourceFingerprint' in scan:
        record['sourceFingerprint'] = scan['sourceFingerprint']
        record['scanLimits'] = scan.get('limits') or {
            'maxFiles': DEFAULT_MAX_FILES, 'maxBytes': DEFAULT_MAX_BYTES,
        }
    if 'scanScope' in scan:
        record['scanScope'] = scan['scanScope']
    return record


# 스캔 출처와 조건별 문서 집합을 검사한 뒤 새 선택 기록을 만든다.
def loadScanRecord(root, scanPath, docsDirname, entryNames=None):
    scan, problem = readJson(scanPath)
    if problem:
        print(f'FAIL: cannot read scan manifest: {problem}', file=sys.stderr)
        return None
    if scan.get('schema') not in SUPPORTED_SCAN_SCHEMAS:
        print(f'FAIL: unsupported scan schema: {scan.get("schema")}', file=sys.stderr)
        return None
    try:
        scannedRoot = Path(scan.get('root') or '').resolve()
    except (OSError, RuntimeError):
        scannedRoot = None
    if scannedRoot != root:
        print(f'FAIL: scan root does not match project: {scan.get("root")}', file=sys.stderr)
        return None
    scope = scan.get('scanScope')
    if scope is not None and (not isinstance(scope, dict)
                              or scope.get('docsDir') != docsDirname
                              or entryNames is not None
                              and scope.get('entries') != list(entryNames)):
        print('FAIL: --docs-dir and --entries must match the scan scope', file=sys.stderr)
        return None
    record = buildRecord(root, scan, docsDirname)
    identifiers = [str(item.get('id') or '') for item in record['decisions']]
    missingIds = sorted(requiredDecisionIds(scan.get('schema')) - set(identifiers))
    if missingIds or len(identifiers) != len(set(identifiers)):
        detail = ', '.join(missingIds) if missingIds else 'duplicated ids'
        print(f'FAIL: scan decision set is incomplete or ambiguous: {detail}', file=sys.stderr)
        return None
    return record


# 기존 결정을 덮지 않고 새 선택 기록을 만든다.
def commandInit(root, scanPath, docsDirname, replace):
    record = loadScanRecord(root, scanPath, docsDirname)
    if record is None:
        return 1
    target = root / DECISIONS_REL
    if target.exists() and not replace:
        print(f'FAIL: {DECISIONS_REL.as_posix()} already exists; pass --replace after review',
              file=sys.stderr)
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


# 매니페스트가 깨끗할 때만 기존 수동 판정과 문서 선택을 새 관측치에 합친다.
def commandRefresh(root, scanPath, docsDirname, entryNames, write, acceptChanges):
    record = loadScanRecord(root, scanPath, docsDirname, entryNames)
    if record is None:
        return 1
    target = root / DECISIONS_REL
    if target.is_symlink():
        print('FAIL: decision manifest is a symlink; refusing to replace it', file=sys.stderr)
        return 1
    previous, problem = readJson(target)
    if problem or previous.get('schema') != SCHEMA or previous.get('root') != root.as_posix():
        print(f'FAIL: existing decision manifest is missing or invalid: {problem}', file=sys.stderr)
        return 1
    oldById = {item.get('id'): item for item in previous.get('decisions', [])
               if isinstance(item, dict)}
    if len(oldById) != len(previous.get('decisions', [])):
        print('FAIL: existing decisions contain duplicates or invalid entries', file=sys.stderr)
        return 1
    if set(oldById) - {item['id'] for item in record['decisions']}:
        print('FAIL: scan omits a previously recorded decision', file=sys.stderr)
        return 1
    changed = []
    unresolved = []
    for item in record['decisions']:
        old = oldById.get(item['id'])
        if old is not None:
            if old.get('observed') != item['observed']:
                changed.append(f'{item["id"]}: {old.get("observed")} -> {item["observed"]}')
            for key in ('selectedDoc', 'resolution', 'reason'):
                item[key] = old.get(key)
            if (item['observed'] == 'met' and old.get('observed') == 'not_met'
                    and item['resolution'] is None and item['selectedDoc'] is None):
                item['selectedDoc'] = f'{docsDirname}/{item["defaultDoc"]}'
        effective = item['resolution'] or item['observed']
        if (effective == 'unknown' or effective == 'met' and not item['selectedDoc']
                or effective == 'not_met' and item['selectedDoc']
                or item['resolution'] is not None and not item['reason']):
            unresolved.append(item['id'])
    # 선택 문서가 실제 라우팅되는지 확인해 잘못된 새 기록의 해시 등록을 막는다.
    index = root / 'AI_RULES.md'
    linked = linkedDocPaths(index, docsDirname)
    for item in record['decisions']:
        selected = item['selectedDoc']
        if selected and (selected not in linked or not (root / selected).is_file()
                         or Path(selected).is_absolute() or '..' in Path(selected).parts
                         or not selected.startswith(docsDirname.rstrip('/') + '/')):
            unresolved.append(f'{item["id"]}: selected doc missing or unlinked: {selected}')
    print(f'REFRESH PREVIEW: {len(record["decisions"])} decisions; '
          f'{len(changed)} observed changes; old head {str(previous.get("gitHead"))[:12]} '
          f'-> {str(record.get("gitHead"))[:12]}')
    for detail in changed:
        print('  REVIEW: ' + detail)
    if not write:
        return 0
    if changed and not acceptChanges or unresolved:
        print('FAIL: review changed signals (--accept-observed-changes) and '
              'resolve missing document choices before writing: ' + ', '.join(unresolved),
              file=sys.stderr)
        return 1
    scope = record.get('scanScope')
    limits = record.get('scanLimits')
    if (not isinstance(scope, dict) or not isinstance(limits, dict)
            or not isinstance(scope.get('excludedDirs'), list)
            or any(not isinstance(name, str) or not name or Path(name).is_absolute()
                   or '..' in Path(name).parts or Path(name).as_posix() != name
                   for name in scope['excludedDirs'])
            or any(type(limits.get(key)) is not int or limits[key] < 1
                   for key in ('maxFiles', 'maxBytes'))
            or not isinstance(record.get('sourceFingerprint'), str)):
        print('FAIL: refresh requires a valid scoped source fingerprint', file=sys.stderr)
        return 1
    if checkGeneratedManifest(root, False, docsDirname) != 0:
        print('FAIL: generated-file conflict blocks decision refresh', file=sys.stderr)
        return 1
    files, _, _, _ = walkProject(root, limits['maxFiles'], limits['maxBytes'],
                                 docsDirname, entryNames, scope['excludedDirs'])
    if (record.get('gitHead') != gitHead(root)
            or fingerprintSources(files, root, docsDirname, entryNames,
                                  scope['excludedDirs']) != record['sourceFingerprint']):
        print('FAIL: source changed since scan; rescan before refreshing', file=sys.stderr)
        return 1
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    return recordGeneratedFiles(root, [DECISIONS_REL.as_posix()])


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
    # HEAD가 같거나 Git이 없어도 소스 변경을 잡고, 새 기록은 금지 경로를 열지 않는다.
    scope = payload.get('scanScope')
    exclusions = ()
    if 'sourceFingerprint' in payload:
        digest = payload['sourceFingerprint']
        limits = payload.get('scanLimits')
        validLimits = (isinstance(limits, dict)
                       and all(type(limits.get(key)) is int and limits[key] > 0
                               for key in ('maxFiles', 'maxBytes')))
        validScope = (scope is None or isinstance(scope, dict)
                      and scope.get('docsDir') == docsDirname
                      and scope.get('entries') == list(entryNames)
                      and isinstance(scope.get('excludedDirs'), list)
                      and all(isinstance(name, str) and name
                              and not Path(name).is_absolute()
                              and '..' not in Path(name).parts
                              and Path(name).as_posix() == name
                              for name in scope['excludedDirs']))
        if (not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None
                or not validLimits or not validScope):
            errors.append('decision manifest has invalid source fingerprint, scan limits or scope')
        else:
            if scope is None:
                # 이전 지문은 문서 경로와 텍스트 확장자에 한정되어 있었으므로 재해석하지 않는다.
                files, _, _, _ = walkProject(root, limits['maxFiles'], limits['maxBytes'])
                current = fingerprintSources(files, root, legacy=True)
            else:
                exclusions = scope['excludedDirs']
                files, _, _, _ = walkProject(root, limits['maxFiles'], limits['maxBytes'],
                                             docsDirname, entryNames, exclusions)
                current = fingerprintSources(files, root, docsDirname, entryNames, exclusions)
            if current != digest:
                errors.append('decision manifest is stale: uncommitted source changed since scan')
    recordedHead = payload.get('gitHead')
    currentHead = gitHead(root)
    if recordedHead and currentHead and recordedHead != currentHead:
        changed = changedFilesSince(root, recordedHead, currentHead)
        if changed is None:
            errors.append('decision manifest is stale: recorded Git HEAD cannot be compared')
        else:
            relevant = [path for path in changed
                        if not isRuleOutput(path, docsDirname, indexName, entryNames)
                        and not isExcludedPath(path, exclusions)]
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
    missingIds = sorted(requiredDecisionIds(payload.get('scanSchema')) - seen)
    if missingIds:
        errors.append('decision manifest is incomplete: ' + ', '.join(missingIds))
    return 'valid' if len(errors) == initialErrors else 'invalid'


# 인덱스에서 선택 문서의 라우팅 경로를 읽는다.
def linkedDocPaths(index, docsDirname):
    if not index.is_file():
        return set()
    pattern = re.compile(rf'\[[^\]]*\]\(({re.escape(docsDirname)}/[^)]+\.md)\)')
    return set(pattern.findall(index.read_text(encoding='utf-8')))


# 독립 실행 시 인덱스의 링크를 읽어 같은 검사를 수행한다.
def commandCheck(root, indexName, docsDirname, entryNames, asJson):
    index = root / indexName
    errors, warnings = [], []
    if not index.is_file():
        errors.append(f'{indexName} not found')
        linked = set()
    else:
        linked = linkedDocPaths(index, docsDirname)
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
    initParser.add_argument('--docs-dir', default='docs/ai-rules')
    initParser.add_argument('--replace', action='store_true')

    refreshParser = sub.add_parser('refresh')
    refreshParser.add_argument('root')
    refreshParser.add_argument('--scan', required=True)
    refreshParser.add_argument('--docs-dir', default='docs/ai-rules')
    refreshParser.add_argument('--entries', default='CLAUDE.md,AGENTS.md')
    refreshParser.add_argument('--write', action='store_true')
    refreshParser.add_argument('--accept-observed-changes', action='store_true')

    checkParser = sub.add_parser('check')
    checkParser.add_argument('root')
    checkParser.add_argument('--index', default='AI_RULES.md')
    checkParser.add_argument('--docs-dir', default='docs/ai-rules')
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
    if args.command == 'refresh':
        scanPath = Path(args.scan).expanduser()
        scanPath = scanPath if scanPath.is_absolute() else root / scanPath
        return commandRefresh(root, scanPath, args.docs_dir, entryNames,
                              args.write, args.accept_observed_changes)
    return commandCheck(root, args.index, args.docs_dir, entryNames, args.json)


if __name__ == '__main__':
    sys.exit(main())
