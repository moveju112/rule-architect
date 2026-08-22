#!/usr/bin/env python3
"""Test suite for the rule-architect scripts.

Usage: python3 tests/test_rules.py

One known-good fixture is copied per case and mutated to break exactly one
contract, so every check has a test that fails when the check stops working.
No framework, no fixtures directory magic — copy, mutate, run, assert.
"""
import json
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
        shutil.copytree(GOOD, self.root)

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
         lambda f: f.edit('CLAUDE.md',
                          '| an error or surprising behavior appears | [docs/PITFALLS.md](docs/PITFALLS.md) |\n',
                          ''),
         needle='required doc not linked')

    case('Core Rules over budget fails',
         lambda f: f.edit('CLAUDE.md', '- **[PREFER]** one collector module per upstream source',
                          '\n'.join(f'- **[PREFER]** filler rule {n}' for n in range(11))),
         needle='Core Rules has')

    case('routing trigger repeating the file name fails',
         lambda f: f.edit('CLAUDE.md', '| reading the directory map or stack |', '| ARCHITECTURE |'),
         needle='repeats the file name')

    case('empty routing trigger fails',
         lambda f: f.edit('CLAUDE.md', '| reading the directory map or stack |', '|  |'),
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

    case('stale directory citation fails',
         lambda f: shutil.rmtree(f.path('scripts')),
         needle='directory not found')

    case('naming patterns are not treated as citations', None, expectFail=False)

    case('placeholder left in a doc fails',
         lambda f: f.append('docs/ARCHITECTURE.md', '\nTODO: fill this in\n'),
         needle='placeholder remains')

    case('missing AGENTS.md fails',
         lambda f: f.path('AGENTS.md').unlink(),
         needle='AGENTS.md pointer not found')

    case('AGENTS.md that copies rules fails',
         lambda f: f.append('AGENTS.md', '\n'.join(f'line {n}' for n in range(20))),
         needle='too long')

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
        run(MANIFEST, 'record', fixture.root, 'CLAUDE.md')

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
    fixture = Fixture()
    try:
        code, out = run(MANIFEST, 'check', fixture.root)
        check('manifest check reports legacy without a manifest', code == 2 and 'LEGACY' in out, out[:160])

        code, out = run(MANIFEST, 'record', fixture.root, 'CLAUDE.md', 'docs/CODING_RULES.md')
        check('manifest record writes the file', code == 0 and (
            fixture.path('.rule-architect/manifest.json')).is_file(), out[:160])

        code, out = run(MANIFEST, 'check', fixture.root)
        check('manifest check is clean right after record', code == 0 and 'CLEAN' in out, out[:160])

        fixture.append('CLAUDE.md', '\n- hand-added rule\n')
        code, out = run(MANIFEST, 'check', fixture.root)
        check('hand edit is reported as a conflict, not overwritten',
              code == 1 and 'modified' in out and 'CLAUDE.md' in out, out[:160])

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
              code == 0 and 'CLAUDE.md' in payload['ruleFiles']
              and all(f.endswith('.md') for f in payload['ruleFiles']), out[:160])
        check('quiz scaffold states the required mix',
              payload['requiredMix'] == {'recall': 3, 'judgment': 1, 'negative': 1}, out[:160])

        def results(correctFlags, types=('recall', 'recall', 'recall', 'judgment', 'negative')):
            return {'runId': 't1', 'lang': 'en', 'questions': [
                {'id': f'q{i}', 'type': kind, 'question': 'q', 'expected': 'e',
                 'answer': 'a', 'correct': flag}
                for i, (kind, flag) in enumerate(zip(types, correctFlags))]}

        path = fixture.path('results.json')
        path.write_text(json.dumps(results([True] * 5)), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't1', '--results', path)
        check('quiz grade passes a clean run and archives it',
              code == 0 and fixture.path('.rule-architect/quiz/t1.json').is_file(), out[:160])

        path.write_text(json.dumps(results([True, True, True, True, False])), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't2', '--results', path)
        check('quiz fails when the negative question fails despite 4 correct',
              code == 1 and 'negative FAILED' in out, out[:160])

        path.write_text(json.dumps(results([True] * 5, ('recall',) * 4 + ('negative',))), encoding='utf-8')
        code, out = run(QUIZ, 'grade', fixture.root, '--run-id', 't3', '--results', path)
        check('quiz rejects the wrong question mix',
              code == 1 and 'composition' in out, out[:160])

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

        code, second = run(SCAN, fixture.root)
        check('scan output is byte-identical across runs', out == second, 'differs')

        code, out = run(SCAN, fixture.root, '--max-files', '1')
        payload = json.loads(out)
        check('scan flags truncation when the file cap is hit',
              payload['truncated']['files'] is True, out[:160])
    finally:
        fixture.cleanup()


def main():
    verifyCases()
    manifestCases()
    quizCases()
    scanCases()
    print()
    if FAILED:
        print(f'{len(FAILED)} FAILED: ' + ', '.join(FAILED))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
