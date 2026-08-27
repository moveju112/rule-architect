#!/usr/bin/env python3
"""Deterministic signal scanner for rule-architect.

Usage: python3 scan.py <project-root> [--max-files N] [--max-bytes N] [--max-commits N]

Emits one JSON manifest on stdout describing the observable signals that decide
which docs get generated. The point is reproducibility: the same commit must
produce the same document set and the same evidence, run after run. Anything the
agent decides on top of this manifest is judgement; anything in here is measured.

Every traversal is bounded. When a cap is hit the corresponding `truncated` flag
is set, so a partial scan can never be mistaken for a complete one.
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

DEFAULT_MAX_FILES = 20000
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_COMMITS = 500

# Directories that are vendored, generated, or otherwise not the project's own code
EXCLUDED_DIRS = {
    '.git', '.hg', '.svn', 'node_modules', 'vendor', 'venv', '.venv', 'env',
    '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.tox',
    'dist', 'build', 'target', 'out', 'bin', 'obj', '.next', '.nuxt',
    '.gradle', '.idea', '.vscode', 'coverage', '.terraform', 'Pods',
    'bower_components', 'jspm_packages', '.cache', '.parcel-cache',
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
    'api': ('api', 'routes', 'router', 'routers', 'endpoints', 'views'),
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


# Walk the tree once, bounded by file count and total bytes
def walkProject(root, maxFiles, maxBytes):
    files, totalBytes = [], 0
    truncatedFiles = truncatedBytes = False
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in EXCLUDED_DIRS or entry.name.startswith('.') and entry.name != '.github':
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
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
                if name in hints:
                    found[layer].append(rel)
    return {layer: sorted(set(paths)) for layer, paths in found.items() if paths}


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


# Deploy artifacts: container files, unit files, deploy directories
def detectDeploy(files, root, limit=40):
    hits = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        name = path.name.lower()
        if (name in DEPLOY_NAMES or name.endswith(DEPLOY_SUFFIXES)
                or any(part.lower() in DEPLOY_DIR_HINTS for part in path.relative_to(root).parts[:-1])):
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
def detectCoChange(root, maxCommits, minTimes=3, maxPerCommit=12, limit=10):
    unavailable = {'available': False, 'groups': [], 'truncated': False}
    prefix = runGit(root, 'rev-parse', '--show-prefix')
    if prefix is None:
        return unavailable
    prefix = prefix.strip()
    log = runGit(root, 'log', f'-n{maxCommits}', '--name-only', '--no-merges',
                 '--pretty=format:%H', '--', '.')
    if log is None:
        return unavailable

    commits, current = [], []
    for line in log.splitlines():
        if not line.strip():
            continue
        if re.fullmatch(r'[0-9a-f]{40}', line.strip()):
            if current:
                commits.append(current)
            current = []
            continue
        path = line.strip()
        # git prints paths from the repository root; keep only this project's own
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        current.append(path)
    if current:
        commits.append(current)

    pairs = Counter()
    for changed in commits:
        files = sorted({f for f in changed if not any(
            part in EXCLUDED_DIRS for part in Path(f).parts)})
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
        'truncated': len(commits) >= maxCommits,
        'commitsScanned': len(commits),
    }


# Turn measured signals into the conditional doc set from SKILL.md
def decideDocs(layers, enums, deploy, coChange):
    decisions = []

    def record(doc, condition, met, evidence):
        decisions.append({'doc': doc, 'condition': condition,
                          'met': met, 'evidence': evidence[:5]})

    record('CONTROLLER_RULES.md', 'dedicated controller/handler layer directory exists',
           bool(layers.get('controller')), layers.get('controller', []))
    record('ENUM_CODES.md', '>=3 files define enum/status-code value sets',
           len(enums) >= 3, enums)
    record('RESPONSE_KEYS.md', 'an api/routes layer exists',
           bool(layers.get('api')), layers.get('api', []))
    record('DB_RULES.md', 'ORM models or a migrations directory exists',
           bool(layers.get('model') or layers.get('migration')),
           layers.get('model', []) + layers.get('migration', []))
    record('DEPLOY.md', 'deploy scripts, unit files, or container files exist',
           bool(deploy), deploy)
    record('tasks/ADD_<TASK>.md', 'a file set changed together >=3 times',
           bool(coChange.get('groups')),
           [' + '.join(g['files']) for g in coChange.get('groups', [])])
    return decisions


def main():
    parser = argparse.ArgumentParser(description='Scan a project for rule-architect signals.')
    parser.add_argument('root')
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument('--max-bytes', type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument('--max-commits', type=int, default=DEFAULT_MAX_COMMITS)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1

    files, totalBytes, truncFiles, truncBytes = walkProject(root, args.max_files, args.max_bytes)
    layers = detectLayers(files, root)
    enums = detectEnums(files, root)
    deploy = detectDeploy(files, root)
    coChange = detectCoChange(root, args.max_commits)

    ruleNames = ('AI_RULES.md', 'CLAUDE.md', 'AGENTS.md', '.cursorrules')
    existing = sorted(name for name in ruleNames
                      if (root / name).is_file() or (root / name).is_symlink())
    # 깨진 진입점은 기존 룰 없음으로 숨기지 않고 업데이트 차단 신호로 드러낸다.
    brokenRuleLinks = sorted(name for name in ruleNames
                             if (root / name).is_symlink() and not (root / name).is_file())

    manifest = {
        'schema': 'rule-architect/scan@1',
        'root': root.as_posix(),
        'limits': {'maxFiles': args.max_files, 'maxBytes': args.max_bytes,
                   'maxCommits': args.max_commits},
        'truncated': {'files': truncFiles, 'bytes': truncBytes,
                      'commits': coChange.get('truncated', False)},
        'counts': {'files': len(files), 'bytes': totalBytes},
        'stack': detectStack(files),
        'layers': layers,
        'enumFiles': enums,
        'deployFiles': deploy,
        'coChange': coChange,
        'existingRuleFiles': existing,
        'brokenRuleLinks': brokenRuleLinks,
        'decisions': decideDocs(layers, enums, deploy, coChange),
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
