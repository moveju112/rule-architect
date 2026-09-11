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
DECISIONS = ROOT / 'scripts' / 'decisions.py'
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

    # 코드 예시처럼 보이는 비경로 토큰을 문서에 넣는다.
    def addNonPathTokens(fixture):
        fixture.append(
            'docs/ARCHITECTURE.md',
            '\nNon-path tokens: `go-chi/chi/v5`, `/api/runs`, `s3://bucket/key`, '
            '`localhost:8080`, `@/views/Home.vue`, `src/{name}/file.py`.\n')

    case('imports URIs routes and templates are not citations',
         addNonPathTokens, expectFail=False)

    case('explicit evidence checks an otherwise ambiguous path',
         lambda f: f.append('docs/ARCHITECTURE.md', '\nProof: `evidence: missing/path`.\n'),
         needle='missing/path')

    case('evidence cannot escape the project',
         lambda f: f.append('docs/ARCHITECTURE.md', '\nProof: `evidence: ../outside`.\n'),
         needle='escapes the project')

    case('existing extensionless directory citation passes',
         lambda f: f.append('docs/ARCHITECTURE.md', '\nScripts: `scripts`.\n'),
         expectFail=False)

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
        fixture.path('docs').rename(fixture.path('docs_local'))
        fixture.path('CLAUDE.md').unlink()
        fixture.path('CLAUDE.local.md').symlink_to('AI_RULES.md')
        indexPath = fixture.path('AI_RULES.md')
        indexPath.write_text(indexPath.read_text(encoding='utf-8').replace(
            'docs/', 'docs_local/'), encoding='utf-8')

    case('custom personal layout passes', usePersonalLayout, expectFail=False,
         extraArgs=('--index', 'AI_RULES.md', '--docs-dir', 'docs_local',
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
        path = fixture.path('.rule-architect/manifest.json')
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['schema'] = 'rule-architect/manifest@2'
        path.write_text(json.dumps(payload), encoding='utf-8')

    case('unlinked hand-written doc only warns when a manifest exists',
         addUnlinkedDocWithManifest, expectFail=False)

    # 최신 매니페스트만 기록해 결정 파일 누락 상태를 만든다.
    def addCurrentManifestWithoutDecisions(fixture):
        run(MANIFEST, 'record', fixture.root, 'AI_RULES.md')

    case('current manifest requires persisted scan decisions',
         addCurrentManifestWithoutDecisions, needle='manifest@3 requires scan decisions')

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
        check('manifest check reports migratable legacy without a manifest',
              code == 2 and 'LEGACY' in out and 'migration' in out, out[:160])

        outside = fixture.root.parent / 'outside.md'
        outside.write_text('# Outside\n', encoding='utf-8')
        code, out = run(MANIFEST, 'record', fixture.root, '../outside.md')
        check('manifest refuses to record a path outside the project',
              code == 1 and 'outside the project' in out, out[:160])

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

        fixture.path('docs_local').mkdir()
        fixture.path('docs_local/LOCAL_RULES.md').write_text('# Local\n', encoding='utf-8')
        code, out = run(MANIFEST, 'check', fixture.root, '--json', '--docs-dir', 'docs_local')
        payload = json.loads(out)
        check('manifest check scopes untracked docs to a custom directory',
              code == 1 and payload['untracked'] == ['docs_local/LOCAL_RULES.md'], out[:160])
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

        shutil.copytree(fixture.path('docs'), fixture.path('docs_local'))
        code, out = run(QUIZ, 'scaffold', fixture.root, '--lang', 'ko', '--run-id', 'local',
                        '--index', 'AI_RULES.md', '--docs-dir', 'docs_local')
        custom = json.loads(out)
        check('quiz scaffold supports a custom neutral layout',
              code == 0 and 'AI_RULES.md' in custom['ruleFiles']
              and any(name.startswith('docs_local/') for name in custom['ruleFiles'])
              and 'AI_RULES.md' in custom['isolationPrompt'], out[:160])

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
        workflow = fixture.path('.forgejo/workflows/deploy.yml')
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: deploy\n', encoding='utf-8')
        code, out = run(SCAN, fixture.root)
        payload = json.loads(out)
        check('scan emits a manifest with decisions',
              code == 0 and payload['schema'] == 'rule-architect/scan@2'
              and len(payload['decisions']) == 6, out[:160])
        check('scan reports no truncation on a small project',
              payload['truncated'] == {'files': False, 'bytes': False, 'commits': False}, out[:160])
        check('scan detects the deploy signal from the Dockerfile',
              any(d['doc'] == 'DEPLOY.md' and d['met'] for d in payload['decisions']), out[:160])
        check('scan detects Forgejo deployment workflows',
              '.forgejo/workflows/deploy.yml' in payload['deployFiles'], out[:160])
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
              payload['truncated']['files'] is True
              and any(item['status'] == 'unknown' for item in payload['decisions']), out[:160])

        code, out = run(SCAN, fixture.root, '--max-files', '0')
        check('scan rejects invalid traversal limits', code == 1 and 'limits' in out, out[:160])

        vendor = fixture.path('web/static/vendors/options.min.js')
        vendor.parent.mkdir(parents=True)
        vendor.write_text('enum Fake { A, B }\n', encoding='utf-8')
        response = fixture.path('src/api/response.py')
        response.parent.mkdir(parents=True)
        response.write_text("def send():\n    return jsonify({'ok': True})\n", encoding='utf-8')
        fixture.path('CLAUDE.local.md').symlink_to('AI_RULES.md')
        code, out = run(SCAN, fixture.root, '--entries', 'CLAUDE.local.md,AGENTS.md',
                        '--output', '.rule-architect/scan.json')
        payload = json.loads(out)
        check('scan excludes vendored minified code from rule signals',
              code == 0 and not any('vendors/' in item for item in payload['enumFiles']), out[:160])
        check('scan requires observable response code for response-key docs',
              any(item['id'] == 'response-keys' and item['status'] == 'met'
                  and 'src/api/response.py' in item['evidence']
                  for item in payload['decisions']), out[:160])
        check('scan supports a custom runtime entry and persisted output',
              'CLAUDE.local.md' in payload['existingRuleFiles']
              and fixture.path('.rule-architect/scan.json').read_text(encoding='utf-8') == out,
              out[:160])

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
            nested.path('OLD_A.md').write_text(f'a{round_}\n', encoding='utf-8')
            nested.path('OLD_B.md').write_text(f'b{round_}\n', encoding='utf-8')
            subprocess.run(git + ['add', '-A'], capture_output=True, timeout=60)
            subprocess.run(git + ['commit', '-q', '-m', f'r{round_}'], capture_output=True, timeout=60)
        nested.path('OLD_A.md').unlink()
        nested.path('OLD_B.md').unlink()
        subprocess.run(git + ['add', '-A'], capture_output=True, timeout=60)
        subprocess.run(git + ['commit', '-q', '-m', 'remove old'], capture_output=True, timeout=60)
        code, out = run(SCAN, nested.root)
        payload = json.loads(out)
        groups = payload['coChange']['groups']
        check('co-change ignores commits outside the project directory',
              code == 0 and not any('OUTSIDE' in f for g in groups for f in g['files']),
              json.dumps(groups)[:160])
        check('co-change ignores paths that no longer exist',
              not any('OLD_' in f for g in groups for f in g['files']), json.dumps(groups)[:160])
    finally:
        nested.cleanup()


# 스캔 결정 기록이 라우팅·노후화 검증까지 이어지는지 확인한다.
def decisionCases():
    fixture = Fixture()
    try:
        git = ['git', '-C', str(fixture.root)]
        subprocess.run(git + ['init', '-q'], capture_output=True, timeout=60)
        subprocess.run(git + ['config', 'user.email', 't@example.com'], capture_output=True, timeout=60)
        subprocess.run(git + ['config', 'user.name', 'test'], capture_output=True, timeout=60)
        subprocess.run(git + ['add', '-A'], capture_output=True, timeout=60)
        subprocess.run(git + ['commit', '-q', '-m', 'baseline'], capture_output=True, timeout=60)
        baselineHead = subprocess.run(
            git + ['rev-parse', 'HEAD'], capture_output=True, text=True, timeout=60).stdout.strip()
        decisions = [
            ('controller', 'CONTROLLER_RULES.md', 'not_met'),
            ('enum-codes', 'ENUM_CODES.md', 'not_met'),
            ('response-keys', 'RESPONSE_KEYS.md', 'not_met'),
            ('database', 'DB_RULES.md', 'not_met'),
            ('deploy', 'DEPLOY.md', 'met'),
            ('recurring-task', 'tasks/ADD_<TASK>.md', 'not_met'),
        ]
        scan = {
            'schema': 'rule-architect/scan@2',
            'root': fixture.root.as_posix(),
            'gitHead': baselineHead,
            'truncated': {'files': False, 'bytes': False, 'commits': False},
            'decisions': [
                {'id': identifier, 'doc': doc, 'status': status,
                 'met': status == 'met', 'condition': 'fixture', 'evidence': ['Dockerfile']}
                for identifier, doc, status in decisions
            ],
        }
        scanPath = fixture.path('.rule-architect/scan.json')
        scanPath.parent.mkdir(parents=True)
        incompleteScan = dict(scan)
        incompleteScan['decisions'] = scan['decisions'][:-1]
        scanPath.write_text(json.dumps(incompleteScan), encoding='utf-8')
        code, out = run(DECISIONS, 'init', fixture.root, '--scan', '.rule-architect/scan.json')
        check('decision init rejects an incomplete scan manifest',
              code == 1 and 'incomplete or ambiguous' in out
              and not fixture.path('.rule-architect/decisions.json').exists(), out[:160])

        scanPath.write_text(json.dumps(scan), encoding='utf-8')
        code, out = run(DECISIONS, 'init', fixture.root, '--scan', '.rule-architect/scan.json')
        check('decision init persists every scan decision',
              code == 0 and fixture.path('.rule-architect/decisions.json').is_file(), out[:160])

        code, out = run(VERIFY, fixture.root)
        check('verify rejects a selected conditional doc that is not routed',
              code == 1 and 'selected doc is not linked' in out, out[:160])

        fixture.path('docs/DEPLOY.md').write_text('# Deploy\n\nDeployment rules.\n', encoding='utf-8')
        fixture.edit(
            'AI_RULES.md',
            '| an error or surprising behavior appears | [docs/PITFALLS.md](docs/PITFALLS.md) |\n',
            '| an error or surprising behavior appears | [docs/PITFALLS.md](docs/PITFALLS.md) |\n'
            '| changing deployment artifacts | [docs/DEPLOY.md](docs/DEPLOY.md) |\n')
        run(MANIFEST, 'record', fixture.root, 'AI_RULES.md', 'docs/DEPLOY.md')
        code, out = run(VERIFY, fixture.root)
        check('current manifest must track the decision record',
              code == 1 and 'must track .rule-architect/decisions.json' in out, out[:160])
        run(MANIFEST, 'record', fixture.root, '.rule-architect/decisions.json')
        code, out = run(VERIFY, fixture.root)
        check('verify accepts a complete conditional-doc decision record', code == 0, out[:160])

        decisionPath = fixture.path('.rule-architect/decisions.json')
        payload = json.loads(decisionPath.read_text(encoding='utf-8'))
        controller = next(item for item in payload['decisions'] if item['id'] == 'controller')
        controller['selectedDoc'] = 'docs/CONTROLLER_RULES.md'
        controller['reason'] = 'manual include without resolution'
        decisionPath.write_text(json.dumps(payload), encoding='utf-8')
        code, out = run(VERIFY, fixture.root)
        check('verify requires an explicit resolution for a negative override',
              code == 1 and 'effective negative cannot select a doc' in out, out[:160])
        controller['selectedDoc'] = None
        controller['reason'] = None

        recurring = next(item for item in payload['decisions'] if item['id'] == 'recurring-task')
        recurring['observed'] = 'unknown'
        decisionPath.write_text(json.dumps(payload), encoding='utf-8')
        code, out = run(VERIFY, fixture.root)
        check('verify rejects an unresolved partial-scan decision',
              code == 1 and 'unknown scan result must be resolved' in out, out[:160])

        recurring['resolution'] = 'not_met'
        recurring['reason'] = 'fixture has no Git history'
        decisionPath.write_text(json.dumps(payload), encoding='utf-8')
        code, out = run(VERIFY, fixture.root)
        check('verify accepts a reasoned manual resolution', code == 0, out[:160])

        subprocess.run(git + ['add', '-A'], capture_output=True, timeout=60)
        subprocess.run(git + ['commit', '-q', '-m', 'rules'], capture_output=True, timeout=60)
        code, out = run(VERIFY, fixture.root)
        check('rule-only commits do not make scan decisions stale', code == 0, out[:160])

        fixture.append('src/db.py', '\n# source changed\n')
        subprocess.run(git + ['add', 'src/db.py'], capture_output=True, timeout=60)
        subprocess.run(git + ['commit', '-q', '-m', 'source'], capture_output=True, timeout=60)
        code, out = run(VERIFY, fixture.root)
        check('source commits make old scan decisions stale',
              code == 1 and 'source changed since scan' in out, out[:160])

        payload['decisions'] = [item for item in payload['decisions'] if item['id'] != 'database']
        decisionPath.write_text(json.dumps(payload), encoding='utf-8')
        code, out = run(VERIFY, fixture.root)
        check('verify rejects an incomplete decision record',
              code == 1 and 'decision manifest is incomplete' in out, out[:160])
    finally:
        fixture.cleanup()


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


# Codex JSONL 레코드를 테스트용 한 줄로 만든다.
def codexLine(recordType, payload, stamp):
    return json.dumps({'timestamp': stamp, 'type': recordType, 'payload': payload})


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

        code, out = run(HARVEST, root, '--transcript-dir', transcripts.parent, '--days', '-1')
        check('harvest rejects invalid limits', code == 1 and 'non-negative' in out, out[:200])
    finally:
        fixture.cleanup()

    mixed = Fixture()
    try:
        root = mixed.root
        claudeRoot = Path(mixed.tmp) / 'claude-projects'
        claudeSession = claudeRoot / re.sub(r'[^A-Za-z0-9]', '-', str(root))
        claudeSession.mkdir(parents=True)
        duplicate = '절대 generated 파일 수정하지마'
        (claudeSession / 'same.jsonl').write_text(
            transcriptLine(str(root), duplicate, '2026-08-10T00:00:00.000Z') + '\n',
            encoding='utf-8')

        codexRoot = Path(mixed.tmp) / 'codex-sessions'
        codexDay = codexRoot / '2026/08/10'
        codexDay.mkdir(parents=True)
        parentLines = [
            codexLine('session_meta', {'id': 'codex-parent', 'cwd': str(root.parent)},
                      '2026-08-10T00:00:00.000Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '이게 아니라 아직 대상이 없어'}]},
                      '2026-08-10T00:00:01.000Z'),
            codexLine('response_item', {'type': 'custom_tool_call',
                                        'input': f'workdir: "{root}"'},
                      '2026-08-10T00:00:02.000Z'),
            codexLine('turn_context', {'cwd': str(root.parent)},
                      '2026-08-10T00:00:03.000Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': duplicate}]},
                      '2026-08-10T00:00:00.000Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '또 docs 폴더 수정하지 마'}]},
                      '2026-08-10T00:00:04.000Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [{
                'type': 'input_text',
                'text': '# Context from my IDE setup:\n\n## Open tabs:\n- db.py\n\n'
                        '## My request for Codex:\n절대 기존 룰 수정하지마'}]},
                      '2026-08-10T00:00:05.000Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [{
                'type': 'input_text',
                'text': '계획을 롤백하는가?\n\n--- 응답 형식 ---\nVERDICT: AGREE'}]},
                      '2026-08-10T00:00:06.000Z'),
        ]
        (codexDay / 'parent.jsonl').write_text('\n'.join(parentLines) + '\n', encoding='utf-8')
        sibling = Path(str(root) + '-other')
        siblingLines = [
            codexLine('session_meta', {'id': 'codex-sibling', 'cwd': str(sibling)},
                      '2026-08-10T00:01:00.000Z'),
            codexLine('response_item', {'type': 'custom_tool_call',
                                        'input': f'workdir: "{sibling}"'},
                      '2026-08-10T00:01:00.500Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '절대 다른 프로젝트 파일 수정하지마'}]},
                      '2026-08-10T00:01:01.000Z'),
        ]
        (codexDay / 'sibling.jsonl').write_text('\n'.join(siblingLines) + '\n', encoding='utf-8')
        subagentLines = [
            codexLine('session_meta', {'id': 'codex-subagent', 'cwd': str(root),
                                       'thread_source': 'subagent'},
                      '2026-08-10T00:02:00.000Z'),
            codexLine('response_item', {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '절대 하위 에이전트 지시를 규칙으로 만들지마'}]},
                      '2026-08-10T00:02:01.000Z'),
        ]
        (codexDay / 'subagent.jsonl').write_text(
            '\n'.join(subagentLines) + '\n', encoding='utf-8')

        code, out = run(HARVEST, root, '--source', 'all', '--claude-dir', claudeRoot,
                        '--codex-dir', codexRoot, '--days', 0)
        payload = json.loads(out)
        texts = [item['text'] for item in payload['corrections']]
        check('harvest combines Claude and Codex project sessions',
              code == 0 and set(payload['sources']) == {'claude', 'codex'}
              and payload['counts']['sessionsMatched'] == 2, out[:200])
        check('harvest attributes a parent-cwd session from structured tool use',
              any('docs 폴더' in text for text in texts)
              and not any('대상이 없어' in text for text in texts), out[:200])
        check('harvest rejects sibling-prefix projects',
              not any('다른 프로젝트' in text for text in texts), out[:200])
        check('harvest rejects Codex subagent prompts',
              not any('하위 에이전트' in text for text in texts), out[:200])
        check('harvest deduplicates the same correction across runtimes',
              texts.count(duplicate) == 1 and payload['counts']['duplicatesDropped'] == 1,
              out[:200])
        check('harvest strips IDE context and evaluator prompts',
              '절대 기존 룰 수정하지마' in texts
              and not any('Open tabs' in text or 'VERDICT:' in text for text in texts), out[:200])
    finally:
        mixed.cleanup()


def main():
    verifyCases()
    manifestCases()
    quizCases()
    scanCases()
    decisionCases()
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
