#!/usr/bin/env python3
"""Deterministic signal scanner for rule-architect.

Usage: python3 scan.py <project-root> [--max-files N] [--max-bytes N]
                       [--max-commits N] [--entries FILE,FILE] [--output FILE]

Emits one JSON manifest on stdout describing the observable signals that decide
which docs get generated. The point is reproducibility: the same commit must
produce the same document set and the same evidence, run after run. Anything the
agent decides on top of this manifest is judgement; anything in here is measured.

Every traversal is bounded. When a cap is hit the corresponding `truncated` flag
is set, so a partial scan can never be mistaken for a complete one.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

DEFAULT_MAX_FILES = 20000
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_COMMITS = 500
ALLOWED_HIDDEN_DIRS = {'.github', '.forgejo'}

# Directories that are vendored, generated, or otherwise not the project's own code
EXCLUDED_DIRS = {
    '.git', '.hg', '.svn', 'node_modules', 'vendor', 'venv', '.venv', 'env',
    '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.tox',
    'dist', 'build', 'target', 'out', 'bin', 'obj', '.next', '.nuxt',
    '.gradle', '.idea', '.vscode', 'coverage', '.terraform', 'Pods',
    'bower_components', 'jspm_packages', '.cache', '.parcel-cache',
    'logs', 'hnote_log', 'graphify-out',
}

# extension -> stack label
STACK_BY_EXT = {
    'py': 'python', 'js': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript',
    'ts': 'typescript', 'tsx': 'typescript', 'jsx': 'javascript', 'go': 'go',
    'rs': 'rust', 'java': 'java', 'kt': 'kotlin', 'rb': 'ruby', 'php': 'php',
    'cs': 'csharp', 'swift': 'swift', 'dart': 'dart', 'ex': 'elixir',
    'exs': 'elixir', 'scala': 'scala', 'c': 'c', 'cpp': 'cpp', 'hpp': 'cpp',
    'sh': 'shell', 'sql': 'sql',
}

LAYER_HINTS = {
    'controller': ('controller', 'controllers', 'handler', 'handlers', 'resource', 'resources'),
    'api': ('api', 'routes', 'router', 'routers', 'endpoints'),
    'model': ('model', 'models', 'entity', 'entities', 'schema', 'schemas'),
    'migration': ('migration', 'migrations', 'alembic'),
    'service': ('service', 'services', 'usecase', 'usecases', 'domain'),
    'test': ('test', 'tests', 'spec', 'specs', '__tests__'),
}

ENUM_RE = re.compile(
    r'\b(class\s+\w+\s*\(\s*\w*Enum\w*\s*\)|enum\s+\w+|IntEnum|StrEnum|'
    r'Object\.freeze\s*\(|const\s+\w+\s*=\s*\{[^}]*\}\s*as\s+const)\b'
)
DEPLOY_NAMES = ('dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'procfile')
DEPLOY_SUFFIXES = ('.service', '.timer')
DEPLOY_DIR_HINTS = ('deploy', 'deployment', 'ansible', 'helm', 'k8s', 'kubernetes', 'terraform')
SOURCE_EXTS = tuple(STACK_BY_EXT)
SIGNAL_EXCLUDED_DIRS = {name.lower() for name in EXCLUDED_DIRS} | {
    'vendors', 'third_party', 'third-party', 'fixtures', '__fixtures__', 'testdata',
}
MINIFIED_RE = re.compile(r'(?:\.min|\.bundle(?:\.legacy)?|\.umd)\.(?:js|css)$', re.IGNORECASE)
# 산출물·사진·압축 파일은 소스 신호가 아니며 바이트 예산도 소비하지 않는다.
BINARY_SUFFIXES = {
    '.7z', '.aab', '.aar', '.aof', '.apk', '.apks', '.avi', '.bin', '.class', '.db',
    '.dex', '.dll', '.dmg', '.exe', '.gif', '.gz', '.ico', '.ipa', '.jar',
    '.jpeg', '.jpg', '.jks', '.keystore', '.mp3', '.mp4', '.pdf', '.png',
    '.rar', '.so', '.sqlite', '.sqlite3', '.svg', '.tar', '.ttf', '.wav',
    '.webp', '.woff', '.woff2', '.zip',
}
LEGACY_FINGERPRINT_EXTS = set(SOURCE_EXTS) | {
    'gradle', 'json', 'kts', 'properties', 'proto', 'toml', 'xml', 'yaml', 'yml',
}
FINGERPRINT_EXTS = LEGACY_FINGERPRINT_EXTS | {
    'css', 'graphql', 'html', 'scss', 'svelte', 'vue',
}
FINGERPRINT_NAMES = {'makefile', 'justfile', 'gemfile'}
API_ROUTE_RE = re.compile(
    r'@(?:app|router|api|bp|blueprint)\.(?:get|post|put|patch|delete|route)\s*\('
    r'\s*[\'\"]/(?:api(?:/|[\'\"])|v\d+/)', re.IGNORECASE,
)
API_PREFIX_RE = re.compile(
    r'\bAPIRouter\s*\([^)]*\bprefix\s*=\s*[\'\"]/(?:api(?:/|[\'\"])|v\d+/)',
    re.IGNORECASE | re.DOTALL,
)
ROUTER_METHOD_RE = re.compile(r'@router\.(?:get|post|put|patch|delete|route)\s*\(')
ORM_RE = re.compile(
    r'(?:\bdeclarative_base\s*\(|\b(?:mapped_column|Column|relationship)\s*\(|'
    r'\bgorm\.Model\b|'
    r'\bmodels\.Model\b|\bdb\.Model\b|\bApplicationRecord\b|'
    r'\bActiveRecord::Base\b|@Entity\b|@Database\b|gorm:[\'\"])'
)
SQL_RE = re.compile(r'\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b', re.IGNORECASE)
DB_CLIENT_RE = re.compile(
    r'\b(?:sql\.DB|database/sql|mysqli|PDO|sqlite3\.connect|create_engine)\b|DB::'
)
DATABASE_DIR_HINTS = {'db', 'database', 'repository', 'repositories', 'dao'}
RESPONSE_RE = re.compile(
    r'json\.NewEncoder\s*\(|\b(?:json_encode|jsonify|JSONResponse|JsonResponse)\s*\(|'
    r'\b(?:c|ctx|res|response)\.JSON\s*\(|\b(?:c|ctx|res|response)\.json\s*\(|'
    r'\breturn\s*\[\s*[\'\"][A-Za-z_][A-Za-z0-9_]*[\'\"]\s*=>',
    re.IGNORECASE,
)


# Walk the tree once, bounded by file count and total bytes
def walkProject(root, maxFiles, maxBytes, docsDirname=None,
                entryNames=('CLAUDE.md', 'AGENTS.md'), excludedDirs=()):
    files, totalBytes = [], 0
    truncatedFiles = truncatedBytes = False
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            # 낮은 이름부터 내려가 숨은 CI 설정이 대형 소스 트리보다 먼저 측정되게 한다.
            entries = sorted(current.iterdir(), key=lambda p: p.name, reverse=True)
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            relative = entry.relative_to(root).as_posix()
            if entry.is_dir():
                if (relative == docsDirname or relative in excludedDirs
                        or entry.name in EXCLUDED_DIRS
                        or entry.name.startswith('.') and entry.name not in ALLOWED_HIDDEN_DIRS):
                    continue
                stack.append(entry)
                continue
            if (not entry.is_file() or entry.suffix.lower() in BINARY_SUFFIXES
                    or docsDirname and isRuleOutput(relative, docsDirname,
                                                    entryNames=entryNames)):
                continue
            if len(files) >= maxFiles:
                truncatedFiles = True
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            if totalBytes + size > maxBytes:
                truncatedBytes = True
                continue
            totalBytes += size
            files.append(entry)
    files.sort(key=lambda p: p.as_posix())
    return files, totalBytes, truncatedFiles, truncatedBytes


# 스캔·Git 이력·결정 검증이 같은 생성 룰 경계를 사용한다.
def isRuleOutput(path, docsDirname='docs/ai-rules', indexName='AI_RULES.md',
                 entryNames=('CLAUDE.md', 'AGENTS.md')):
    return (path == indexName or path in entryNames or path == '.cursorrules'
            or path.startswith(docsDirname.rstrip('/') + '/')
            or path.startswith('.rule-architect/'))


# 명시적으로 금지된 하위 트리는 내용·Git 신호·변경 검사 모두에서 제외한다.
def isExcludedPath(raw, excludedDirs):
    return any(raw == prefix or raw.startswith(prefix.rstrip('/') + '/')
               for prefix in excludedDirs)


# Git 변경 경로와 소스 파일 모두 같은 신호 필터를 적용한다.
def isSignalRelative(raw, docsDirname='docs/ai-rules',
                     entryNames=('CLAUDE.md', 'AGENTS.md'), excludedDirs=()):
    path = Path(raw)
    if isRuleOutput(raw, docsDirname, entryNames=entryNames):
        return False
    if isExcludedPath(raw, excludedDirs):
        return False
    if any(part.lower() in SIGNAL_EXCLUDED_DIRS for part in path.parts[:-1]):
        return False
    return not MINIFIED_RE.search(path.name)


# 프로젝트 파일은 루트 상대 경로로 정규화하여 Git 신호와 판정을 맞춘다.
def isSignalFile(path, root, docsDirname='docs/ai-rules',
                 entryNames=('CLAUDE.md', 'AGENTS.md'), excludedDirs=()):
    return isSignalRelative(path.relative_to(root).as_posix(), docsDirname,
                            entryNames, excludedDirs)


# Count extensions to name the stack, biggest share first
def detectStack(files):
    counter = Counter()
    for path in files:
        ext = path.suffix.lstrip('.').lower()
        label = STACK_BY_EXT.get(ext)
        if label:
            counter[label] += 1
    return [{'stack': name, 'files': count} for name, count in counter.most_common()]


# Map directory names onto architectural layers
def detectLayers(files, root):
    found = {layer: [] for layer in LAYER_HINTS}
    seen = set()
    for path in files:
        for parent in path.relative_to(root).parents:
            rel = parent.as_posix()
            if rel in ('.', '') or rel in seen:
                continue
            seen.add(rel)
            name = parent.name.lower()
            for layer, hints in LAYER_HINTS.items():
                if name in hints and (layer not in ('api', 'controller')
                                      or path.suffix.lstrip('.').lower() in SOURCE_EXTS):
                    found[layer].append(rel)
    return {layer: sorted(set(paths)) for layer, paths in found.items() if paths}


# 스캔에 영향을 주는 텍스트 소스·설정의 내용 지문을 만든다. 구 기록은 종전 범위를 유지한다.
def fingerprintSources(files, root, docsDirname='docs/ai-rules',
                       entryNames=('CLAUDE.md', 'AGENTS.md'), excludedDirs=(), legacy=False):
    digest = hashlib.sha256()
    for path in files:
        if not isSignalFile(path, root, docsDirname, entryNames, excludedDirs):
            continue
        name = path.name.lower()
        suffix = path.suffix.lstrip('.').lower()
        extensions = LEGACY_FINGERPRINT_EXTS if legacy else FINGERPRINT_EXTS
        if (suffix not in extensions and name not in DEPLOY_NAMES
                and not name.endswith(DEPLOY_SUFFIXES)
                and (legacy or name not in FINGERPRINT_NAMES
                     and not name.startswith('.env'))):
            continue
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        try:
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
        except OSError:
            digest.update(b'<unreadable>')
        digest.update(b'\0')
    return digest.hexdigest()


# Files that define enum-like value sets (heuristic, reported with evidence)
def detectEnums(files, root, limit=40):
    hits = []
    for path in files:
        if path.suffix.lstrip('.').lower() not in SOURCE_EXTS:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if ENUM_RE.search(text):
            hits.append(path.relative_to(root).as_posix())
        if len(hits) >= limit:
            break
    return hits


# 평면 FastAPI/Flask 모듈의 명시적 /api 또는 /vN 라우트를 API 계층 근거로 삼는다.
def detectApiRoutes(files, root, limit=40):
    hits = []
    for path in files:
        relative = path.relative_to(root)
        if path.suffix.lower() != '.py' or any(
                part.lower() in ('test', 'tests', 'spec', 'specs') for part in relative.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if (API_ROUTE_RE.search(text)
                or API_PREFIX_RE.search(text) and ROUTER_METHOD_RE.search(text)):
            hits.append(relative.as_posix())
        if len(hits) >= limit:
            break
    return hits


# 모델 디렉터리의 단순 데이터 클래스와 실제 ORM/DB 매핑을 구분한다.
def detectOrmModels(files, root, limit=40):
    hits = []
    modelNames = set(LAYER_HINTS['model'])
    for path in files:
        if path.suffix.lstrip('.').lower() not in SOURCE_EXTS:
            continue
        relative = path.relative_to(root)
        if not any(part.lower() in modelNames for part in relative.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if ORM_RE.search(text):
            hits.append(relative.as_posix())
        if len(hits) >= limit:
            break
    return hits


# ORM 없이 SQL을 직접 다루는 모델·DB 계층도 DB 규칙의 실제 근거로 삼는다.
def detectSqlAccessFiles(files, root, limit=40):
    hits = []
    layerNames = set(LAYER_HINTS['model']) | DATABASE_DIR_HINTS
    for path in files:
        if path.suffix.lstrip('.').lower() not in SOURCE_EXTS:
            continue
        relative = path.relative_to(root)
        if (path.name.endswith('_test.go') or path.name.startswith('test_')
                or any(part.lower() in ('test', 'tests', 'spec', 'specs')
                       for part in relative.parts[:-1])
                or not any(part.lower() in layerNames for part in relative.parts[:-1])):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if SQL_RE.search(text) or DB_CLIENT_RE.search(text):
            hits.append(relative.as_posix())
        if len(hits) >= limit:
            break
    return hits


# 응답 문서는 API 디렉터리 또는 평면 라우트의 실제 직렬화 코드로 판단한다.
def detectResponseConventions(files, root, routeFiles=(), limit=40):
    hits = []
    layerNames = set(LAYER_HINTS['api'] + LAYER_HINTS['controller'])
    for path in files:
        if path.suffix.lstrip('.').lower() not in SOURCE_EXTS:
            continue
        relative = path.relative_to(root)
        if (not any(part.lower() in layerNames for part in relative.parts[:-1])
                and relative.as_posix() not in routeFiles):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if RESPONSE_RE.search(text):
            hits.append(relative.as_posix())
        if len(hits) >= limit:
            break
    return hits


# Deploy artifacts: container files, unit files, deploy directories
def detectDeploy(files, root, limit=40):
    hits = []
    for path in files:
        relative = path.relative_to(root)
        rel = relative.as_posix()
        name = path.name.lower()
        workflowDeploy = bool(
            relative.parts and relative.parts[0].lower() in ALLOWED_HIDDEN_DIRS
            and 'workflows' in (part.lower() for part in relative.parts[:-1])
            and re.search(r'(?:deploy|release)', name))
        if (name in DEPLOY_NAMES or name.endswith(DEPLOY_SUFFIXES)
                or any(part.lower() in DEPLOY_DIR_HINTS for part in relative.parts[:-1])
                or workflowDeploy):
            hits.append(rel)
        if len(hits) >= limit:
            break
    return sorted(set(hits))


# Run a git command inside root; None when git is unusable there
def runGit(root, *args):
    try:
        result = subprocess.run(['git', '-C', str(root), *args],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


# File sets that keep changing together — the signal for a task playbook.
# History is scoped to the project directory, not the enclosing repository: a
# project nested inside a bigger repo would otherwise inherit that repo's commits
# and switch on a task playbook for files it does not contain.
def detectCoChange(root, maxCommits, minTimes=3, maxPerCommit=12, limit=10,
                   docsDirname='docs/ai-rules', entryNames=('CLAUDE.md', 'AGENTS.md'),
                   excludedDirs=()):
    unavailable = {'available': False, 'groups': [], 'truncated': False}
    prefix = runGit(root, 'rev-parse', '--show-prefix')
    if prefix is None:
        return unavailable
    prefix = prefix.strip()
    # 한 건 더 읽어 상한 도달과 실제 잘림을 구분한다.
    log = runGit(root, 'log', f'-n{maxCommits + 1}', '--name-only', '--no-merges',
                 '--pretty=format:%H', '--', '.')
    if log is None:
        return unavailable

    commits, current = [], None
    for line in log.splitlines():
        if not line.strip():
            continue
        if re.fullmatch(r'[0-9a-f]{40}', line.strip()):
            if current is not None:
                commits.append(current)
            current = []
            continue
        path = line.strip()
        # git prints paths from the repository root; keep only this project's own
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        # 생성 룰·금지 경로·과거 삭제 경로는 반복 작업의 근거가 될 수 없다.
        if (not isSignalRelative(path, docsDirname, entryNames, excludedDirs)
                or not (root / path).is_file()):
            continue
        if current is not None:
            current.append(path)
    if current is not None:
        commits.append(current)

    truncated = len(commits) > maxCommits
    scannedCommits = commits[:maxCommits]
    pairs = Counter()
    for changed in scannedCommits:
        files = sorted(set(changed))
        if len(files) < 2 or len(files) > maxPerCommit:
            continue
        for index, left in enumerate(files):
            for right in files[index + 1:]:
                pairs[(left, right)] += 1

    groups = [{'files': [left, right], 'times': count}
              for (left, right), count in pairs.most_common()
              if count >= minTimes]
    groups.sort(key=lambda g: (-g['times'], g['files']))
    return {
        'available': True,
        'groups': groups[:limit],
        'truncated': truncated,
        'commitsScanned': len(scannedCommits),
        'commitsWithCurrentFiles': sum(bool(commit) for commit in scannedCommits),
    }


# Turn measured signals into the conditional doc set from SKILL.md
def decideDocs(layers, apiRoutes, ormModels, sqlAccess, enums, responses,
               deploy, coChange, truncated):
    decisions = []

    def record(identifier, doc, condition, met, evidence, incomplete=False):
        status = 'met' if met else ('unknown' if incomplete else 'not_met')
        decisions.append({'id': identifier, 'doc': doc, 'condition': condition,
                          'status': status, 'met': True if met else (None if incomplete else False),
                          'evidence': evidence[:5]})

    fileIncomplete = truncated['files'] or truncated['bytes']
    record('controller', 'CONTROLLER_RULES.md',
           'dedicated controller/handler layer directory exists',
           bool(layers.get('controller')), layers.get('controller', []), fileIncomplete)
    record('api', 'API_RULES.md', 'api/routes source layer or explicit /api or /vN routes exist',
           bool(layers.get('api') or apiRoutes), layers.get('api', []) + apiRoutes, fileIncomplete)
    record('enum-codes', 'ENUM_CODES.md', '>=3 files define enum/status-code value sets',
           len(enums) >= 3, enums, fileIncomplete)
    record('response-keys', 'RESPONSE_KEYS.md',
           'response serialization or envelope conventions exist in an API/controller layer',
           bool(responses), responses, fileIncomplete)
    record('database', 'DB_RULES.md', 'ORM, SQL access layer, or migrations exist',
           bool(ormModels or sqlAccess or layers.get('migration')),
           ormModels + sqlAccess + layers.get('migration', []), fileIncomplete)
    record('deploy', 'DEPLOY.md',
           'deploy scripts, deployment workflows, unit files, or container files exist',
           bool(deploy), deploy, fileIncomplete)
    record('recurring-task', 'tasks/ADD_<TASK>.md',
           'a file set changed together >=3 times', bool(coChange.get('groups')),
           [' + '.join(g['files']) for g in coChange.get('groups', [])],
           truncated['commits'] or not coChange.get('available'))
    return decisions


def main():
    parser = argparse.ArgumentParser(description='Scan a project for rule-architect signals.')
    parser.add_argument('root')
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument('--max-bytes', type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument('--max-commits', type=int, default=DEFAULT_MAX_COMMITS)
    parser.add_argument('--entries', default='CLAUDE.md,AGENTS.md',
                        help='comma-separated runtime entry names')
    parser.add_argument('--docs-dir', default='docs/ai-rules',
                        help='generated rule directory, excluded from scanning')
    parser.add_argument('--exclude-dir', action='append', default=[],
                        help='project-relative directory never traversed (repeatable)')
    parser.add_argument('--output', default=None,
                        help='also write the JSON manifest to this file')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    if args.max_files < 1 or args.max_bytes < 1 or args.max_commits < 0:
        print('FAIL: file/byte limits must be positive and --max-commits must be non-negative',
              file=sys.stderr)
        return 1
    entryNames = tuple(name.strip() for name in args.entries.split(',') if name.strip())
    if (not entryNames or len(set(entryNames)) != len(entryNames)
            or any(Path(name).is_absolute() or Path(name).parent != Path('.')
                   for name in entryNames)):
        print('FAIL: --entries must name unique files in the project root', file=sys.stderr)
        return 1

    scopedDirs = (args.docs_dir, *args.exclude_dir)
    if any(Path(name).is_absolute() or not name or name in ('.', '..')
           or '..' in Path(name).parts or '.' in Path(name).parts
           for name in scopedDirs):
        print('FAIL: --docs-dir and --exclude-dir must be project-relative directories',
              file=sys.stderr)
        return 1
    args.docs_dir = Path(args.docs_dir).as_posix()
    excludedDirs = sorted({Path(name).as_posix() for name in args.exclude_dir})
    files, totalBytes, truncFiles, truncBytes = walkProject(
        root, args.max_files, args.max_bytes, args.docs_dir, entryNames, excludedDirs)
    signalFiles = [path for path in files if isSignalFile(
        path, root, args.docs_dir, entryNames, excludedDirs)]
    layers = detectLayers(signalFiles, root)
    apiRoutes = detectApiRoutes(signalFiles, root)
    ormModels = detectOrmModels(signalFiles, root)
    sqlAccess = detectSqlAccessFiles(signalFiles, root)
    enums = detectEnums(signalFiles, root)
    responses = detectResponseConventions(signalFiles, root, apiRoutes)
    deploy = detectDeploy(signalFiles, root)
    coChange = detectCoChange(root, args.max_commits, docsDirname=args.docs_dir,
                              entryNames=entryNames, excludedDirs=excludedDirs)

    customClaudeEntries = sorted(path.name for path in root.glob('CLAUDE*.md')
                                 if path.name != 'CLAUDE.md')
    ruleNames = tuple(dict.fromkeys(
        ('AI_RULES.md', '.cursorrules', *entryNames, *customClaudeEntries)))
    existing = sorted(name for name in ruleNames
                      if (root / name).is_file() or (root / name).is_symlink())
    # 깨진 진입점은 기존 룰 없음으로 숨기지 않고 업데이트 차단 신호로 드러낸다.
    brokenRuleLinks = sorted(name for name in ruleNames
                             if (root / name).is_symlink() and not (root / name).is_file())

    truncated = {'files': truncFiles, 'bytes': truncBytes,
                 'commits': coChange.get('truncated', False)}
    head = runGit(root, 'rev-parse', 'HEAD')
    manifest = {
        'schema': 'rule-architect/scan@3',
        'root': root.as_posix(),
        'gitHead': head.strip() if head else None,
        'limits': {'maxFiles': args.max_files, 'maxBytes': args.max_bytes,
                   'maxCommits': args.max_commits},
        'scanScope': {'docsDir': args.docs_dir, 'entries': list(entryNames),
                      'excludedDirs': excludedDirs},
        'truncated': truncated,
        'counts': {'files': len(files), 'signalFiles': len(signalFiles), 'bytes': totalBytes},
        'stack': detectStack(signalFiles),
        'sourceFingerprint': fingerprintSources(
            files, root, args.docs_dir, entryNames, excludedDirs),
        'layers': layers,
        'apiRouteFiles': apiRoutes,
        'ormModelFiles': ormModels,
        'sqlAccessFiles': sqlAccess,
        'enumFiles': enums,
        'responseFiles': responses,
        'deployFiles': deploy,
        'coChange': coChange,
        'existingRuleFiles': existing,
        'brokenRuleLinks': brokenRuleLinks,
        'decisions': decideDocs(layers, apiRoutes, ormModels, sqlAccess, enums, responses,
                                deploy, coChange, truncated),
    }
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if args.output:
        output = Path(args.output).expanduser()
        output = output if output.is_absolute() else root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding='utf-8')
    print(rendered, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
