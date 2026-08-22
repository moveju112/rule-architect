#!/usr/bin/env python3
"""Quiz-gate scaffolding and grading for rule-architect.

Usage:
  python3 quiz.py scaffold <project-root> [--lang ko|en] [--run-id ID]
  python3 quiz.py grade    <project-root> --run-id ID --results <results.json>

This script does NOT run the quiz. There is no portable way for a script to
spawn the isolated subagent the gate depends on, and pretending otherwise would
turn the only content-quality gate into theatre. The host skill dispatches the
subagent; this script owns the parts a script can own honestly:

  scaffold — emit the isolation prompt, the required question mix, and the
             result schema the host must fill in
  grade    — enforce the composition rule and the pass rule, then archive the
             run under .rule-architect/quiz/<run-id>.json so a claimed pass is
             reproducible instead of a sentence in a chat log

Pass rule: at least 4 of 5 correct AND the negative question passed.
Exit codes: 0 = pass, 1 = fail or malformed results.
"""
import argparse
import json
import sys
from pathlib import Path

QUIZ_DIR = Path('.rule-architect') / 'quiz'
REQUIRED_MIX = {'recall': 3, 'judgment': 1, 'negative': 1}
PASS_MIN_CORRECT = 4

PROMPT_EN = """You are answering questions about a project you cannot see.

You have been given ONLY the generated rule files: CLAUDE.md, AGENTS.md, and the
docs/*.md files it links. You have NO access to the source code. Do not guess and
do not reason from what projects usually do.

Answer each question from the rules alone. If the rules do not cover a question,
answer exactly: "not in the rules, check the source".

Return one JSON object matching the schema you were given. No prose outside it."""

PROMPT_KO = """당신은 볼 수 없는 프로젝트에 대해 답한다.

주어진 것은 생성된 룰 파일뿐이다 — CLAUDE.md, AGENTS.md, 그리고 거기서 링크한
docs/*.md. 소스 코드 접근은 없다. 추측하지 말고, 보통 프로젝트가 이럴 것이라는
일반론으로 답하지 마라.

각 질문을 룰만 보고 답하라. 룰이 다루지 않는 질문이면 정확히 이렇게 답하라:
"룰에 없음, 소스 확인 필요".

주어진 스키마에 맞는 JSON 객체 하나만 반환하라. 그 밖의 산문은 쓰지 마라."""

RESULT_SCHEMA = {
    'runId': 'string — matches --run-id',
    'lang': 'ko | en',
    'questions': [{
        'id': 'string',
        'type': 'recall | judgment | negative',
        'question': 'string — asked in the language the rules are written in',
        'expected': 'string — what the rules actually say (filled by the host)',
        'answer': 'string — the isolated subagent answer, verbatim',
        'correct': 'boolean — host judgement against `expected`',
    }],
}


# The prompt and question mix the host must honour when it dispatches the subagent
def commandScaffold(root, lang, runId):
    scaffold = {
        'schema': 'rule-architect/quiz@1',
        'runId': runId,
        'lang': lang,
        'isolationPrompt': PROMPT_KO if lang == 'ko' else PROMPT_EN,
        'requiredMix': REQUIRED_MIX,
        'passRule': f'>= {PASS_MIN_CORRECT} of 5 correct AND the negative question passed',
        'ruleFiles': sorted(discoverRuleFiles(root)),
        'resultSchema': RESULT_SCHEMA,
        'note': ('The host skill dispatches the subagent with ONLY the files in '
                 'ruleFiles. This script never executes a model.'),
    }
    print(json.dumps(scaffold, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


# The rule set the isolated agent is allowed to see
def discoverRuleFiles(root):
    files = [name for name in ('CLAUDE.md', 'AGENTS.md') if (root / name).is_file()]
    docsDir = root / 'docs'
    if docsDir.is_dir():
        files += [doc.relative_to(root).as_posix() for doc in docsDir.rglob('*.md')
                  if doc.stem == doc.stem.upper()]
    return files


# Enforce composition and the pass rule, then archive the run
def commandGrade(root, runId, resultsPath):
    try:
        results = json.loads(Path(resultsPath).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'FAIL: cannot read results: {exc}', file=sys.stderr)
        return 1

    questions = results.get('questions')
    if not isinstance(questions, list):
        print('FAIL: results.questions must be a list', file=sys.stderr)
        return 1

    problems = []
    counts = dict.fromkeys(REQUIRED_MIX, 0)
    for index, item in enumerate(questions):
        kind = item.get('type')
        if kind not in counts:
            problems.append(f'question {index}: unknown type {kind!r}')
            continue
        counts[kind] += 1
        if not isinstance(item.get('correct'), bool):
            problems.append(f'question {index}: `correct` must be a boolean')
        if not str(item.get('answer', '')).strip():
            problems.append(f'question {index}: empty answer')
    for kind, wanted in REQUIRED_MIX.items():
        if counts[kind] != wanted:
            problems.append(f'composition: {kind} = {counts[kind]}, required {wanted}')

    if problems:
        for problem in problems:
            print(f'FAIL: {problem}', file=sys.stderr)
        return 1

    correct = sum(1 for item in questions if item['correct'])
    negativePassed = all(item['correct'] for item in questions if item['type'] == 'negative')
    passed = correct >= PASS_MIN_CORRECT and negativePassed

    record = dict(results)
    record.update({'runId': runId, 'correct': correct, 'total': len(questions),
                   'negativePassed': negativePassed, 'passed': passed})
    archive = root / QUIZ_DIR / f'{runId}.json'
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    summary = (f'{correct}/{len(questions)} correct, negative '
               f'{"passed" if negativePassed else "FAILED"} '
               f'-> {(QUIZ_DIR / f"{runId}.json").as_posix()}')
    if passed:
        print(f'PASS: {summary}')
        return 0
    print(f'FAIL: {summary}', file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description='rule-architect quiz gate')
    sub = parser.add_subparsers(dest='command', required=True)

    scaffoldParser = sub.add_parser('scaffold')
    scaffoldParser.add_argument('root')
    scaffoldParser.add_argument('--lang', choices=('ko', 'en'), default='en')
    scaffoldParser.add_argument('--run-id', default='run')

    gradeParser = sub.add_parser('grade')
    gradeParser.add_argument('root')
    gradeParser.add_argument('--run-id', required=True)
    gradeParser.add_argument('--results', required=True)

    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    if args.command == 'scaffold':
        return commandScaffold(root, args.lang, args.run_id)
    return commandGrade(root, args.run_id, args.results)


if __name__ == '__main__':
    sys.exit(main())
