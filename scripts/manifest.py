#!/usr/bin/env python3
"""Generated-file manifest for rule-architect update safety.

Usage:
  python3 manifest.py record <project-root> <file> [<file> ...]
  python3 manifest.py check  <project-root> [--json]

`record` stores a hash of every file the generator wrote. `check` compares the
current files against those hashes before an update run.

Why a hash and not a marker: a single marker at the bottom of CLAUDE.md cannot
tell a hand-edited rule apart from a stale generated one, so an update either
destroys hand-written rules or keeps rules that should have been removed. A
per-file hash separates the two cases, and the policy on a mismatch is to stop
rather than guess:

  clean     — file is byte-identical to what was generated. Safe to regenerate.
  modified  — a human edited it after generation. CONFLICT: never overwrite.
  missing   — recorded file is gone. Report; the caller decides.
  untracked — an UPPERCASE doc nobody recorded. Treat as hand-written.

Exit codes: 0 = clean, 1 = conflict (modified or missing), 2 = legacy project
(no manifest — treat every existing rule file as hand-written and only add).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

MANIFEST_REL = Path('.rule-architect') / 'manifest.json'
SCHEMA = 'rule-architect/manifest@1'


# sha256 of a file's bytes; None when the file is gone
def hashFile(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def manifestPath(root):
    return root / MANIFEST_REL


def loadManifest(root):
    path = manifestPath(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data.get('files'), dict) else None


# Record the hash of every file this run generated
def commandRecord(root, targets):
    files = {}
    for raw in targets:
        path = Path(raw)
        resolved = path if path.is_absolute() else root / path
        if not resolved.is_file():
            print(f'FAIL: cannot record missing file: {raw}', file=sys.stderr)
            return 1
        rel = resolved.relative_to(root).as_posix()
        files[rel] = {'sha256': hashFile(resolved), 'lines': resolved.read_text(
            encoding='utf-8').count('\n') + 1}
    target = manifestPath(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(
        {'schema': SCHEMA, 'files': dict(sorted(files.items()))},
        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'RECORDED: {len(files)} files -> {MANIFEST_REL.as_posix()}')
    return 0


# Compare the working tree against the recorded hashes
def commandCheck(root, asJson):
    data = loadManifest(root)
    if data is None:
        report = {'status': 'legacy', 'clean': [], 'modified': [], 'missing': [],
                  'untracked': sorted(uppercaseDocs(root))}
        emit(report, asJson,
             'LEGACY: no manifest — treat every existing rule file as hand-written; add only')
        return 2

    recorded = data['files']
    clean, modified, missing = [], [], []
    for rel, entry in sorted(recorded.items()):
        current = hashFile(root / rel)
        if current is None:
            missing.append(rel)
        elif current == entry.get('sha256'):
            clean.append(rel)
        else:
            modified.append(rel)

    untracked = sorted(set(uppercaseDocs(root)) - set(recorded))
    report = {'status': 'conflict' if (modified or missing) else 'clean',
              'clean': clean, 'modified': modified, 'missing': missing,
              'untracked': untracked}
    if report['status'] == 'clean':
        emit(report, asJson, f'CLEAN: {len(clean)} generated files unchanged')
        return 0
    lines = [f'CONFLICT: {len(modified)} modified, {len(missing)} missing']
    lines += [f'  modified (do not overwrite): {rel}' for rel in modified]
    lines += [f'  missing: {rel}' for rel in missing]
    emit(report, asJson, '\n'.join(lines))
    return 1


# Every UPPERCASE doc in the project, generated or not
def uppercaseDocs(root):
    docsDir = root / 'docs'
    if not docsDir.is_dir():
        return []
    return [doc.relative_to(root).as_posix() for doc in docsDir.rglob('*.md')
            if doc.stem == doc.stem.upper()]


def emit(report, asJson, text):
    if asJson:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        stream = sys.stdout if report['status'] == 'clean' else sys.stderr
        print(text, file=stream)


def main():
    parser = argparse.ArgumentParser(description='rule-architect generated-file manifest')
    sub = parser.add_subparsers(dest='command', required=True)

    recordParser = sub.add_parser('record')
    recordParser.add_argument('root')
    recordParser.add_argument('files', nargs='+')

    checkParser = sub.add_parser('check')
    checkParser.add_argument('root')
    checkParser.add_argument('--json', action='store_true')

    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    if args.command == 'record':
        return commandRecord(root, args.files)
    return commandCheck(root, args.json)


if __name__ == '__main__':
    sys.exit(main())
