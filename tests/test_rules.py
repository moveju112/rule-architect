#!/usr/bin/env python3
"""Test suite for the rule-architect scripts.

Usage: python3 tests/test_rules.py

One known-good fixture is copied per case and mutated to break exactly one
contract, so every check has a test that fails when the check stops working.
No framework, no fixtures directory magic — copy, mutate, run, assert.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOOD = ROOT / 'tests' / 'fixtures' / 'good'
VERIFY = ROOT / 'scripts' / 'verify_rules.py'
MANIFEST = ROOT / 'scripts' / 'manifest.py'
QUIZ = ROOT / 'scripts' / 'quiz.py'
SCAN = ROOT / 'scripts' / 'scan.py'
HARVEST = ROOT / 'scripts' / 'harvest.py'
HOOKGEN = ROOT / 'scripts' / 'hookgen.py'
GUARD = ROOT / 'scripts' / 'rule_guard.py'

FAILED = []


def run(script, *args):
    result = subprocess.run([sys.executable, str(script), *[str(a) for a in args]],
                            capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout + result.stderr


def check(label, condition, detail=''):
    if condition:
        print(f'ok   - {label}')
    else:
        print(f'FAIL - {label}{(" :: " + detail) if detail else ""}')
        FAILED.append(label)


# Copy the good fixture into a temp dir, apply a mutation, return the root
class Fixture:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix='rule-architect-test-')
        self.root = Path(self.tmp) / 'project'
        shutil.copytree(GOOD, self.root, symlinks=True)

    def path(self, rel):
        return self.root / rel

    def edit(self, rel, old, new):
        target = self.path(rel)
        text = target.read_text(encoding='utf-8')
        assert old in text, f'mutation anchor missing in {rel}: {old[:40]}'
        target.write_text(text.replace(old, new, 1), encoding='utf-8')

    def append(self, rel, text):
        with self.path(rel).open('a', encoding='utf-8') as handle:
            handle.write(text)

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def case(label, mutate, expectFail=True, needle=None, extraArgs=()):
    fixture = Fixture()
    try:
        if mutate:
            mutate(fixture)
        code, out = run(VERIFY, fixture.root, *extraArgs)
        if expectFail:
            check(label, code == 1 and (needle is None or needle in out), out.strip()[:160])
        else:
            check(label, code == 0, out.strip()[:160])
    finally:
        fixture.cleanup()


def verifyCases():
    case('good fixture passes strict', None, expectFail=False)

    case('missing required doc fails',
         lambda f: f.path('docs/PITFALLS.md').unlink(),
         needle='required doc missing')

    case('required doc present but unlinked fails',
         lambda f: f.edit('AI_RULES.md',
                          '| an error or surprising behavior appears | [docs/PITFALLS.md](docs/PITFALLS.md) |\n',
                          ''),
         needle='required doc not linked')

    case('Core Rules over budget fails',
         lambda f: f.edit('AI_RULES.md', '- **[PREFER]** one collector module per upstream source',
                          '\n'.join(f'- **[PREFER]** filler rule {n}' for n in range(11))),
         needle='Core Rules has')

    case('routing trigger repeating the file name fails',
         lambda f: f.edit('AI_RULES.md', '| reading the directory map or stack |', '| ARCHITECTURE |'),
         needle='repeats the file name')

    case('empty routing trigger fails',
         lambda f: f.edit('AI_RULES.md', '| reading the directory map or stack |', '|  |'),
         needle='empty trigger')

    case('graded rule without why fails',
         lambda f: f.edit('docs/CODING_RULES.md',
                          '  - why: settings read os.environ at import time, so a late load yields empty values\n', ''),
         needle='missing `why:`')

    case('graded rule without a correct example fails',
         lambda f: f.edit('docs/CODING_RULES.md',
                          '  - ✅ `src/config.py:1` is imported only after `load_project_env()` runs\n', ''),
         needle='missing ✅')

    case('stale citation to a deleted file fails',
         lambda f: f.path('src/db.py').unlink(),
         needle='file not found')

    case('citation past the end of a file fails',
         lambda f: f.edit('docs/CODING_RULES.md', '`src/db.py:3`', '`src/db.py:9999`'),
         needle='cite points at line')

    case('stale citation to a missing bare build file fails',
         lambda f: f.path('Dockerfile').unlink(),
         needle='Dockerfile')

    def citeMakefile(fixture):
        fixture.append('docs/ARCHITECTURE.md', '\nBuild entry point: `Makefile`.\n')

    case('bare Makefile citation is checked and fails when absent',
         citeMakefile, needle='Makefile')

    def citeDotfile(fixture):
        fixture.append('docs/ARCHITECTURE.md', '\nConfig template: `.env.example`.\n')

    case('dotfile citation is checked and fails when absent',
         citeDotfile, needle='.env.example')

    def citeExistingDotfile(fixture):
        fixture.path('.env.example').write_text('DSN=\n', encoding='utf-8')
        citeDotfile(fixture)

    case('dotfile citation passes when the file exists',
         citeExistingDotfile, expectFail=False)

    case('stale directory citation fails',
         lambda f: shutil.rmtree(f.path('scripts')),
         needle='directory not found')

    case('naming patterns are not treated as citations', None, expectFail=False)

    case('placeholder left in a doc fails',
         lambda f: f.append('docs/ARCHITECTURE.md', '\nTODO: fill this in\n'),
         needle='placeholder remains')

    case('missing AGENTS.md fails',
         lambda f: f.path('AGENTS.md').unlink(),
         needle='AGENTS.md: runtime entry not found')

    # 깨진 런타임 링크를 만든다.
    def breakAgentsLink(fixture):
        fixture.path('AGENTS.md').unlink()
        fixture.path('AGENTS.md').symlink_to('MISSING_RULES.md')

    case('broken runtime symlink fails', breakAgentsLink, needle='broken symlink')

    # 두 진입점을 휴대 가능한 일반 포인터로 바꾼다.
    def usePortablePointers(fixture):
        for name in ('CLAUDE.md', 'AGENTS.md'):
            fixture.path(name).unlink()
            fixture.path(name).write_text(
                '# Project AI Rules\n\nRead [AI_RULES.md](AI_RULES.md) first.\n',
                encoding='utf-8')

    case('portable pointer fallback passes', usePortablePointers, expectFail=False)

    # 심링크와 일반 포인터가 섞인 잘못된 상태를 만든다.
    def mixEntryModes(fixture):
        fixture.path('AGENTS.md').unlink()
        fixture.path('AGENTS.md').write_text(
            '# Project AI Rules\n\nRead [AI_RULES.md](AI_RULES.md) first.\n',
            encoding='utf-8')

    case('mixed runtime entry modes fail', mixEntryModes, needle='mix symlink and pointer')

    # 진입점 하나를 다른 문서로 잘못 연결한다.
    def pointAtWrongIndex(fixture):
        fixture.path('AGENTS.md').unlink()
        fixture.path('AGENTS.md').symlink_to('docs/ARCHITECTURE.md')

    case('runtime symlink to the wrong file fails', pointAtWrongIndex,
         needle='must target AI_RULES.md')

    # 중립 정본 자체가 심링크인 금지 상태를 만든다.
    def linkTheNeutralIndex(fixture):
        fixture.path('AI_RULES_SOURCE.md').write_text(
            fixture.path('AI_RULES.md').read_text(encoding='utf-8'), encoding='utf-8')
        fixture.path('AI_RULES.md').unlink()
        fixture.path('AI_RULES.md').symlink_to('AI_RULES_SOURCE.md')

    case('neutral index itself cannot be a symlink', linkTheNeutralIndex,
         needle='neutral index must be a regular file')

    # 휴대용 포인터의 줄 수 제한을 넘긴다.
    def useLongPortablePointers(fixture):
        usePortablePointers(fixture)
        fixture.path('AGENTS.md').write_text(
            'AI_RULES.md\n' + '\n'.join(f'line {n}' for n in range(16)), encoding='utf-8')

    case('portable pointer over line budget fails', useLongPortablePointers,
         needle='portable pointer exceeds')

    # 개인 룰용 정본·진입점·문서 디렉터리 옵션을 구성한다.
    def usePersonalLayout(fixture):
        fixture.path('AI_RULES.md').rename(fixture.path('AI_RULES.local.md'))
        fixture.path('docs').rename(fixture.path('docs_local'))
        fixture.path('CLAUDE.md').unlink()
        fixture.path('AGENTS.md').unlink()
        fixture.path('CLAUDE.local.md').symlink_to('AI_RULES.local.md')
        fixture.path('AGENTS.md').symlink_to('AI_RULES.local.md')
        indexPath = fixture.path('AI_RULES.local.md')
        indexPath.write_text(indexPath.read_text(encoding='utf-8').replace(
            'docs/', 'docs_local/'), encoding='utf-8')

    case('custom personal layout passes', usePersonalLayout, expectFail=False,
         extraArgs=('--index', 'AI_RULES.local.md', '--docs-dir', 'docs_local',
                    '--entries', 'CLAUDE.local.md,AGENTS.md'))

    # over the 150-line target but under the 190-line hard limit
    filler = '\n'.join(f'- filler line {n}' for n in range(160))
    case('doc over target budget fails in strict mode',
         lambda f: f.append('docs/ARCHITECTURE.md', '\n' + filler),
         needle='> target')
    case('doc over target budget only warns in lenient mode',
         lambda f: f.append('docs/ARCHITECTURE.md', '\n' + filler),
         expectFail=False, extraArgs=('--lenient',))

    hard = '\n'.join(f'- filler line {n}' for n in range(200))
    case('doc over the hard limit fails even in lenient mode',
         lambda f: f.append('docs/ARCHITECTURE.md', '\n' + hard),
         needle='hard limit', extraArgs=('--lenient',))

    def addUnlinkedDoc(fixture):
        fixture.path('docs/EXTRA.md').write_text('# Extra\n\nhand written\n', encoding='utf-8')

    case('unlinked UPPERCASE doc fails without a manifest',
         addUnlinkedDoc, needle='generated doc not linked')

    def addUnlinkedDocWithManifest(fixture):
        addUnlinkedDoc(fixture)
        run(MANIFEST, 'record', fixture.root, 'AI_RULES.md')

    case('unlinked hand-written doc only warns when a manifest exists',
         addUnlinkedDocWithManifest, expectFail=False)

    # --json keeps the same verdict as the text output
    fixture = Fixture()
    try:
        code, out = run(VERIFY, fixture.root, '--json')
        payload = json.loads(out)
        check('--json reports pass with the doc list',
              code == 0 and payload['pass'] is True and len(payload['docs']) == 3, out[:160])
    finally:
        fixture.cleanup()


def manifestCases():
    legacy = Fixture()
    try:
        legacy.path('CLAUDE.md').unlink()
        legacy.path('CLAUDE.md').write_text(
            legacy.path('AI_RULES.md').read_text(encoding='utf-8'), encoding='utf-8')
        digest = hashlib.sha256(legacy.path('CLAUDE.md').read_bytes()).hexdigest()
        manifestPath = legacy.path('.rule-architect/manifest.json')
        manifestPath.parent.mkdir(parents=True)
        manifestPath.write_text(json.dumps({
            'schema': 'rule-architect/manifest@1',
            'files': {'CLAUDE.md': {'sha256': digest, 'lines': 1}},
        }), encoding='utf-8')
        code, out = run(MANIFEST, 'check', legacy.root)
        check('schema 1 regular-file manifests remain readable',
              code == 0 and 'CLEAN' in out, out[:160])
    finally:
        legacy.cleanup()

    fixture = Fixture()
    try:
        code, out = run(MANIFEST, 'check', fixture.root)
        check('manifest check reports legacy without a manifest', code == 2 and 'LEGACY' in out, out[:160])

        code, out = run(MANIFEST, 'record', fixture.root, 'AI_RULES.md',
                        'CLAUDE.md', 'AGENTS.md', 'docs/CODING_RULES.md')
        check('manifest record writes the file', code == 0 and (
            fixture.path('.rule-architect/manifest.json')).is_file(), out[:160])

        code, out = run(MANIFEST, 'check', fixture.root)
        check('manifest check is clean right after record', code == 0 and 'CLEAN' in out, out[:160])

        fixture.append('AI_RULES.md', '\n- hand-added rule\n')
        code, out = run(MANIFEST, 'check', fixture.root)
        check('hand edit is reported as a conflict, not overwritten',
              code == 1 and 'modified' in out and 'AI_RULES.md' in out, out[:160])

        # recording a second file must not erase the first one's hash
        code, out = run(MANIFEST, 'record', fixture.root, 'docs/ARCHITECTURE.md')
        payload = json.loads(fixture.path('.rule-architect/manifest.json').read_text(encoding='utf-8'))
        check('partial record merges instead of wiping earlier hashes',
              code == 0 and set(payload['files']) == {
                  'AI_RULES.md', 'CLAUDE.md', 'AGENTS.md',
                  'docs/CODING_RULES.md', 'docs/ARCHITECTURE.md'}, out[:160])

        code, out = run(MANIFEST, 'record', fixture.root, 'AGENTS.md', '--replace')
        payload = json.loads(fixture.path('.rule-architect/manifest.json').read_text(encoding='utf-8'))
        check('--replace records a symlink target instead of duplicated contents',
              code == 0 and payload['files'] == {
                  'AGENTS.md': {'type': 'symlink', 'target': 'AI_RULES.md'}}, out[:160])
        run(MANIFEST, 'record', fixture.root, 'AI_RULES.md', 'CLAUDE.md', 'AGENTS.md',
            'docs/CODING_RULES.md')

        fixture.path('AGENTS.md').unlink()
        fixture.path('AGENTS.md').write_text('AI_RULES.md\n', encoding='utf-8')
        code, out = run(MANIFEST, 'check', fixture.root)
        check('a symlink replaced by a regular file is modified',
              code == 1 and 'AGENTS.md' in out and 'modified' in out, out[:160])
        fixture.path('AGENTS.md').unlink()
        fixture.path('AGENTS.md').symlink_to('AI_RULES.md')

        fixture.path('docs/CODING_RULES.md').unlink()
        code, out = run(MANIFEST, 'check', fixture.root, '--json')
        payload = json.loads(out)
        check('deleted generated file is reported as missing',
              code == 1 and 'docs/CODING_RULES.md' in payload['missing'], out[:160])
        check('untracked UPPERCASE docs are listed',
              'docs/ARCHITECTURE.md' in payload['untracked'], out[:160])
    finally:
        fixture.cleanup()


def quizCases():
    fixture = Fixture()
    try:
        code, out = run(QUIZ, 'scaffold', fixture.root, '--lang', 'en', '--run-id', 't1')
        payload = json.loads(out)
        check('quiz scaffold lists only rule files',
              code == 0 and 'AI_RULES.md' in payload['ruleFiles']
              and 'CLAUDE.md' not in payload['ruleFiles']
              and 'AGENTS.md' not in payload['ruleFiles']
              and all(f.endswith('.md') for f in payload['ruleFiles']), out[:160])
        check('quiz scaffold states the required mix',
              payload['requiredMix'] == {'recall': 3, 'judgment': 1, 'negative': 1}, out[:160])

        fixture.path('AI_RULES.local.md').write_text(
            fixture.path('AI_RULES.md').read_text(encoding='utf-8'), encoding='utf-8')
        shutil.copytree(fixture.path('docs'), fixture.path('docs_local'))
        code, out = run(QUIZ, 'scaffold', fixture.root, '--lang', 'ko', '--run-id', 'local',
                        '--index', 'AI_RULES.local.md', '--docs-dir', 'docs_local')
        custom = json.loads(out)
        check('quiz scaffold supports a custom neutral layout',
              code == 0 and 'AI_RULES.local.md' in custom['ruleFiles']
              and any(name.startswith('docs_local/') for name in custom['ruleFiles'])
              and 'AI_RULES.local.md' in custom['isolationPrompt'], out[:160])

        def results(runId, correctFlags,
                    types=('recall', 'recall', 'recall', 'judgment', 'negative')):
            return {'runId': runId, 'lang': 'en', 'questions': [
                {'id': f'q{i}', 'type': kind, 'question': 'q', 'expected': 'e',
                 'answer': 'a', 'correct': flag}
                for i, (kind, flag) in enumerate(zip(types, correctFlags))]}

        path = fixture.path('results.json')
        path.write_text(json.dumps(results('t1', [True] * 5)), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't1', '--results', path)
        check('quiz grade passes a clean run and archives it',
              code == 0 and fixture.path('.rule-architect/quiz/t1.json').is_file(), out[:160])

        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't1', '--results', path)
        check('quiz refuses to overwrite an archived run',
              code == 1 and 'already exists' in out, out[:160])

        path.write_text(json.dumps(results('t2', [True, True, True, True, False])), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't2', '--results', path)
        check('quiz fails when the negative question fails despite 4 correct',
              code == 1 and 'negative FAILED' in out, out[:160])

        path.write_text(json.dumps(results('t1', [True] * 5)), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't9', '--results', path)
        check('quiz rejects results recorded for a different run',
              code == 1 and 'does not match' in out, out[:160])

        path.write_text(json.dumps(results('t3', [True] * 5, ('recall',) * 4 + ('negative',))),
                        encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't3', '--results', path)
        check('quiz rejects the wrong question mix',
              code == 1 and 'composition' in out, out[:160])

        missing = results('t7', [True] * 5)
        del missing['runId']
        path.write_text(json.dumps(missing), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't7', '--results', path)
        check('quiz rejects results with no runId',
              code == 1 and 'does not match' in out
              and not fixture.path('.rule-architect/quiz/t7.json').exists(), out[:160])

        noLang = results('t8', [True] * 5)
        del noLang['lang']
        path.write_text(json.dumps(noLang), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't8', '--results', path)
        check('quiz rejects results with no lang',
              code == 1 and 'lang must be' in out
              and not fixture.path('.rule-architect/quiz/t8.json').exists(), out[:160])

        blank = results('t5', [True] * 5)
        blank['questions'][0]['expected'] = ''
        path.write_text(json.dumps(blank), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't5', '--results', path)
        check('quiz rejects a question with no expected answer',
              code == 1 and 'expected' in out, out[:160])

        path.write_text(json.dumps(results('t6', [True] * 5)), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', '../escape', '--results', path)
        check('quiz rejects a run id that could escape the archive directory',
              code == 1 and 'invalid --run-id' in out
              and not (fixture.root.parent / 'escape.json').exists(), out[:160])

        path.write_text(json.dumps({'questions': 'nope'}), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't4', '--results', path)
        check('quiz rejects malformed results', code == 1, out[:160])
    finally:
        fixture.cleanup()


def scanCases():
    fixture = Fixture()
    try:
        code, out = run(SCAN, fixture.root)
        payload = json.loads(out)
        check('scan emits a manifest with decisions',
              code == 0 and payload['schema'] == 'rule-architect/scan@1'
              and len(payload['decisions']) == 6, out[:160])
        check('scan reports no truncation on a small project',
              payload['truncated'] == {'files': False, 'bytes': False, 'commits': False}, out[:160])
        check('scan detects the deploy signal from the Dockerfile',
              any(d['doc'] == 'DEPLOY.md' and d['met'] for d in payload['decisions']), out[:160])
        check('scan detects python as the stack',
              payload['stack'] and payload['stack'][0]['stack'] == 'python', out[:160])
        check('scan reports the neutral rule file and healthy runtime links',
              payload['existingRuleFiles'] == ['AGENTS.md', 'AI_RULES.md', 'CLAUDE.md']
              and payload['brokenRuleLinks'] == [], out[:160])

        code, second = run(SCAN, fixture.root)
        check('scan output is byte-identical across runs', out == second, 'differs')

        code, out = run(SCAN, fixture.root, '--max-files', '1')
        payload = json.loads(out)
        check('scan flags truncation when the file cap is hit',
              payload['truncated']['files'] is True, out[:160])

        fixture.path('AGENTS.md').unlink()
        fixture.path('AGENTS.md').symlink_to('MISSING_RULES.md')
        code, out = run(SCAN, fixture.root)
        payload = json.loads(out)
        check('scan exposes a broken runtime link',
              code == 0 and payload['brokenRuleLinks'] == ['AGENTS.md'], out[:160])
    finally:
        fixture.cleanup()

    # a project nested in a bigger repo must not inherit the parent's history
    nested = Fixture()
    try:
        repo = nested.root.parent
        git = ['git', '-C', str(repo)]
        subprocess.run(git + ['init', '-q'], capture_output=True, timeout=60)
        subprocess.run(git + ['config', 'user.email', 't@example.com'], capture_output=True, timeout=60)
        subprocess.run(git + ['config', 'user.name', 'test'], capture_output=True, timeout=60)
        for round_ in range(4):
            (repo / 'OUTSIDE_A.md').write_text(f'a{round_}\n', encoding='utf-8')
            (repo / 'OUTSIDE_B.md').write_text(f'b{round_}\n', encoding='utf-8')
            subprocess.run(git + ['add', '-A'], capture_output=True, timeout=60)
            subprocess.run(git + ['commit', '-q', '-m', f'r{round_}'], capture_output=True, timeout=60)
        code, out = run(SCAN, nested.root)
        payload = json.loads(out)
        groups = payload['coChange']['groups']
        check('co-change ignores commits outside the project directory',
              code == 0 and not any('OUTSIDE' in f for g in groups for f in g['files']),
              json.dumps(groups)[:160])
    finally:
        nested.cleanup()


# Feed a PreToolUse payload to a guard installed under `root`
def runGuard(root, payload):
    result = subprocess.run([sys.executable, str(root / '.claude' / 'hooks' / 'rule_guard.py')],
                            input=json.dumps(payload), capture_output=True, text=True,
                            timeout=60, env={**os.environ, 'CLAUDE_PROJECT_DIR': str(root)})
    return result.returncode, result.stdout + result.stderr


def hookgenCases():
    fixture = Fixture()
    try:
        spec = fixture.path('spec.json')
        spec.write_text(json.dumps({'rules': [{
            'id': 'no-direct-engine', 'glob': 'src/**/*.py',
            'forbid': r'create_async_engine\(',
            'message': 'reuse get_engine()', 'evidence': 'src/db.py:3'}]}), encoding='utf-8')
        code, out = run(HOOKGEN, 'emit', fixture.root, '--rules', spec)
        check('hookgen emits the spec and the guard',
              code == 0 and fixture.path('.rule-architect/hooks.json').is_file()
              and fixture.path('.claude/hooks/rule_guard.py').is_file(), out[:160])
        check('hookgen prints the settings entry when not writing',
              'PreToolUse' in out and 'settings.json' in out, out[:160])

        target = str(fixture.path('src/x.py'))
        code, out = runGuard(fixture.root, {'tool_name': 'Write', 'tool_input': {
            'file_path': target, 'content': 'engine = create_async_engine(dsn)'}})
        check('guard blocks a forbidden write inside the glob',
              code == 2 and 'no-direct-engine' in out, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Write', 'tool_input': {
            'file_path': target, 'content': 'engine = get_engine()'}})
        check('guard allows a clean write', code == 0, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Write', 'tool_input': {
            'file_path': str(fixture.path('tests/x.py')),
            'content': 'create_async_engine(1)'}})
        check('guard ignores a path outside the glob', code == 0, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'MultiEdit', 'tool_input': {
            'file_path': target,
            'edits': [{'new_string': 'x = 1'}, {'new_string': 'create_async_engine(2)'}]}})
        check('guard inspects every MultiEdit chunk', code == 2, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Read', 'tool_input': {
            'file_path': target}})
        check('guard ignores non-write tools', code == 0, out[:160])

        code, out = runGuard(fixture.root, {'not': 'a payload'})
        check('guard fails open on a payload it cannot use', code == 0, out[:160])

        code, out = run(HOOKGEN, 'emit', fixture.root, '--rules', spec, '--write')
        code2, out2 = run(HOOKGEN, 'emit', fixture.root, '--rules', spec, '--write')
        settings = json.loads(fixture.path('.claude/settings.json').read_text(encoding='utf-8'))
        entries = settings['hooks']['PreToolUse']
        check('--write merges once and stays idempotent',
              code == 0 and code2 == 0 and len(entries) == 1 and 'ALREADY' in out2,
              (out + out2)[:160])

        code, out = run(HOOKGEN, 'check', fixture.root)
        check('hookgen check reports the installed state',
              code == 0 and json.loads(out)['registered'] is True, out[:160])

        denySpec = fixture.path('deny.json')
        denySpec.write_text(json.dumps({'rules': [{
            'id': 'no-team-docs', 'glob': 'docs/**', 'tools': ['Read', 'Grep', 'Glob'],
            'deny': True, 'message': 'docs/ is team-owned; read docs_local/'}]}), encoding='utf-8')
        code, out = run(HOOKGEN, 'emit', fixture.root, '--rules', denySpec)
        check('hookgen accepts a deny rule with no forbid regex', code == 0, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Read', 'tool_input': {
            'file_path': str(fixture.path('docs/ARCHITECTURE.md'))}})
        check('guard denies reading a denied path', code == 2 and 'no-team-docs' in out, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Grep', 'tool_input': {
            'pattern': 'x', 'path': 'docs'}})
        check('guard denies a Grep scoped into a denied directory', code == 2, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Grep', 'tool_input': {'pattern': 'x'}})
        check('guard allows a repo-wide Grep with no path', code == 0, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Read', 'tool_input': {
            'file_path': str(fixture.path('src/db.py'))}})
        check('guard allows reading outside the denied path', code == 0, out[:160])

        code, out = runGuard(fixture.root, {'tool_name': 'Write', 'tool_input': {
            'file_path': str(fixture.path('docs/ARCHITECTURE.md')), 'content': 'x'}})
        check('deny rule does not fire for a tool it did not list', code == 0, out[:160])

        bad = fixture.path('bad.json')
        bad.write_text(json.dumps({'rules': [{'id': 'Bad ID', 'glob': '', 'forbid': '([',
                                              'message': ''}]}), encoding='utf-8')
        code, out = run(HOOKGEN, 'emit', fixture.root, '--rules', bad)
        check('hookgen rejects an unenforceable spec',
              code == 1 and 'not a valid regex' in out, out[:160])
    finally:
        fixture.cleanup()


# One transcript record as Claude Code writes it
def transcriptLine(cwd, text, stamp, session='s1'):
    return json.dumps({'type': 'user', 'cwd': cwd, 'sessionId': session,
                       'timestamp': stamp, 'message': {'role': 'user', 'content': text}})


def harvestCases():
    fixture = Fixture()
    try:
        root = fixture.root
        transcripts = Path(fixture.tmp) / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(root))
        transcripts.mkdir(parents=True)
        lines = [
            transcriptLine(str(root), '절대 docs 폴더는 직접 수정하지마', '2026-08-01T00:00:00.000Z'),
            transcriptLine(str(root), 'docs 폴더 건드리지 말라고 말했잖아', '2026-08-02T00:00:00.000Z'),
            transcriptLine(str(root), '스키마 좀 정리해줘', '2026-08-03T00:00:00.000Z'),
            transcriptLine('/somewhere/else', '이게 아니라 저거야', '2026-08-04T00:00:00.000Z'),
            transcriptLine(str(root), 'password = hunter2 로 하지마', '2026-08-05T00:00:00.000Z'),
            json.dumps({'type': 'user', 'cwd': str(root), 'timestamp': '2026-08-06T00:00:00.000Z',
                        'isMeta': True, 'message': {'role': 'user', 'content': '틀렸어'}}),
            transcriptLine(str(root), '틀렸어 ' + ('x' * 700), '2026-08-07T00:00:00.000Z'),
        ]
        (transcripts / 'a.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

        code, out = run(HARVEST, root, '--transcript-dir', transcripts.parent, '--days', 0)
        payload = json.loads(out)
        texts = [item['text'] for item in payload['corrections']]
        check('harvest picks up corrections and skips plain requests',
              code == 0 and payload['counts']['corrections'] == 3
              and not any('스키마' in text for text in texts), out[:200])
        check('harvest ignores sessions from another project',
              not any('저거야' in text for text in texts), out[:200])
        check('harvest ignores meta records and long specs',
              not any(text.startswith('틀렸어') for text in texts), out[:200])
        check('harvest redacts credential-shaped text',
              any('<redacted>' in text for text in texts)
              and not any('hunter2' in text for text in texts), out[:200])
        check('harvest reports recurring terms across corrections',
              any(term['term'] == 'docs' and term['corrections'] >= 2
                  for term in payload['repeatedTerms']), out[:200])
        check('harvest orders corrections newest first',
              [item['at'] for item in payload['corrections']]
              == sorted((item['at'] for item in payload['corrections']), reverse=True), out[:200])

        code, out = run(HARVEST, root, '--transcript-dir', transcripts.parent, '--days', 1)
        check('harvest honours the recency window',
              code == 0 and json.loads(out)['counts']['corrections'] == 0, out[:200])
    finally:
        fixture.cleanup()


def main():
    verifyCases()
    manifestCases()
    quizCases()
    scanCases()
    hookgenCases()
    harvestCases()
    print()
    if FAILED:
        print(f'{len(FAILED)} FAILED: ' + ', '.join(FAILED))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
