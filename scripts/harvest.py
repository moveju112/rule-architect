#!/usr/bin/env python3
"""Correction harvester: mine past sessions for the rules a cold scan cannot see.

Usage: python3 harvest.py <project-root> [--source auto|claude|codex|all]
                          [--claude-dir DIR] [--codex-dir DIR]
                          [--days N] [--limit N] [--max-files N]

`scan.py` answers "what is in this project". This answers "what did the agent
actually get wrong here" — the user's own corrections, quoted from past session
transcripts. A rule harvested from a real correction beats a rule inferred from
a directory name, because someone already paid for it once.

The script only MEASURES. It groups nothing and promotes nothing: clustering
corrections into rules is judgement, and judgement belongs to the caller. What
comes out is a redacted, recency-ordered list of correction messages plus a
document-frequency table of the words that keep coming back.

Promotion rule (enforced by the caller, not here): a candidate becomes a rule
only when it repeats, is recent, comes from this project, and does not conflict
with the current source. One angry message is not a rule.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_CLAUDE_TRANSCRIPT_ROOT = Path.home() / '.claude' / 'projects'
DEFAULT_CODEX_TRANSCRIPT_ROOT = Path.home() / '.codex' / 'sessions'
# 기존 상수를 가져다 쓰는 호출자를 위해 이름을 유지한다.
DEFAULT_TRANSCRIPT_ROOT = DEFAULT_CLAUDE_TRANSCRIPT_ROOT
DEFAULT_DAYS = 180
DEFAULT_LIMIT = 60
DEFAULT_MAX_FILES = 400
TEXT_CAP = 240
SOURCE_NAMES = ('claude', 'codex')
IDE_REQUEST_RE = re.compile(r'(?im)^##?\s*My request for Codex:\s*')
EVALUATION_PROMPT_RE = re.compile(
    r'(?is)(?:---\s*응답 형식.*VERDICT:|response format.*VERDICT:)')

# A correction marker names WHY a message was picked up, so the caller can weigh
# a flat "아니" differently from an explicit "말했잖아". Order matters: the first
# match names the message.
MARKERS = (
    ('repeat-instruction', r'말했잖|했잖아|아까\s*말|이미\s*말|또\s*그|다시\s*말|I\s+(?:already\s+)?said|told you'),
    ('forbid', r'하지\s*마|말라고|하면\s*안\s*[되돼]|절대\s*\S*\s*(?:하지|마|금지)|'
                r'never\s+do|don\'?t\s+(?:ever\s+)?(?:do|use|touch|change)|do not\s+'),
    ('wrong', r'틀렸|잘못\s*(?:했|됐|이|된)|(?:그게|이게|그건|그런\s*게)\s*아니|아니야|아닌데|'
               r'that\'?s\s+wrong|incorrect|not\s+what\s+I'),
    ('undo', r'되돌려|롤백|원복|취소해|revert|roll\s*back|undo'),
    ('redo', r'다시\s*해|다시해|재작업|redo|try again|do it again'),
    ('why-did-you', r'왜\s+\S*\s*(?:했|했어|한거|바꿨|지웠)|why did you|who told you'),
)
MARKER_RES = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in MARKERS)

# Slash commands, hook injections, and pasted tool output are not user prose
NON_PROSE_PREFIXES = ('<command-name>', '<local-command', '<system-reminder>',
                      '<user-prompt-submit-hook>', '<bash-input>', '<bash-stdout>',
                      '[Request interrupted', 'Caveat:', 'This session is being continued',
                      'Analysis:', '<summary>')

# A correction is short. Past this, the message is a spec or a plan that happens to
# contain a forbidding word, and counting it as a correction poisons the signal.
MAX_PROSE_CHARS = 600

SECRET_RES = (
    re.compile(r'(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|bearer)'
               r'(\s*[:=]\s*|\s+)\S+'),
    re.compile(r'-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----', re.DOTALL),
    re.compile(r'\b[A-Za-z0-9+/]{32,}={0,2}\b'),
    re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.]+\b'),
)

# Words too common to signal anything; a token here never reaches the frequency table
STOPWORDS = {
    '그리고', '그게', '그거', '그건', '이거', '이건', '저거', '해줘', '해봐', '하지', '하고',
    '있는', '없는', '그럼', '근데', '지금', '다시', '아니', '진행', '확인', '수정', '작업',
    '너는', '너가', '내가', '우리', '이제', '먼저', '그래', '좋아', '그리', '그러',
    'the', 'and', 'for', 'you', 'this', 'that', 'with', 'not', 'but', 'was', 'are',
    'have', 'from', 'just', 'dont', 'did', 'why', 'how', 'what', 'again', 'said',
    'redacted',
}


# Transcript directory name for a path: every non-alphanumeric byte becomes a dash
def encodePath(path):
    return re.sub(r'[^A-Za-z0-9]', '-', Path(path).as_posix())


# 경로가 프로젝트 루트 또는 하위인지 확인한다.
def pathWithin(path, root):
    try:
        candidate = Path(path).expanduser().resolve()
        boundary = Path(root).expanduser().resolve()
        candidate.relative_to(boundary)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


# 상위 디렉터리에서 시작한 Claude 세션까지 후보 디렉터리에 포함한다.
def claudeDirectoryNames(root):
    names = {encodePath(root)}
    home = Path.home().resolve()
    if pathWithin(root, home):
        for parent in root.parents:
            if not pathWithin(parent, home):
                break
            names.add(encodePath(parent))
            if parent == home:
                break
    return names


# 읽을 수 없는 세션 파일은 가장 오래된 항목처럼 처리한다.
def safeMtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


# 프로젝트·하위·상위에서 시작한 Claude 세션을 찾고 본문에서 다시 귀속을 확인한다.
def findClaudeTranscripts(root, transcriptRoot, maxFiles):
    encoded = encodePath(root)
    if not transcriptRoot.is_dir():
        return [], False, 0
    ancestors = claudeDirectoryNames(root)
    directories = [entry for entry in sorted(transcriptRoot.iterdir())
                   if entry.is_dir() and (entry.name == encoded
                                          or entry.name.startswith(encoded + '-')
                                          or entry.name in ancestors)]
    files = []
    for directory in directories:
        files += sorted(directory.glob('*.jsonl'))
    files.sort(key=safeMtime, reverse=True)
    return files[:maxFiles], len(files) > maxFiles, len(files)


# 날짜별로 저장되는 Codex 세션은 JSONL 본문에서 프로젝트 귀속을 판정한다.
def findCodexTranscripts(transcriptRoot, maxFiles):
    if not transcriptRoot.is_dir():
        return [], False, 0
    try:
        files = list(transcriptRoot.rglob('*.jsonl'))
    except OSError:
        files = []
    files.sort(key=safeMtime, reverse=True)
    return files[:maxFiles], len(files) > maxFiles, len(files)


# 기존 Claude 전용 호출자를 위한 호환 함수를 유지한다.
def findTranscripts(root, transcriptRoot, maxFiles):
    files, truncated, _ = findClaudeTranscripts(root, transcriptRoot, maxFiles)
    return files, truncated


# 런타임별 레코드에서 사용자가 직접 쓴 본문만 꺼낸다.
def userText(record, source='claude'):
    if source == 'claude':
        if record.get('type') != 'user' or record.get('isMeta') or record.get('isSidechain'):
            return None
        message = record.get('message')
        if not isinstance(message, dict):
            return None
        content = message.get('content')
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [block.get('text', '') for block in content
                     if isinstance(block, dict) and block.get('type') == 'text']
            text = '\n'.join(part for part in parts if part)
        else:
            return None
    else:
        payload = record.get('payload')
        if (record.get('type') != 'response_item' or not isinstance(payload, dict)
                or payload.get('type') != 'message' or payload.get('role') != 'user'):
            return None
        content = payload.get('content')
        if not isinstance(content, list):
            return None
        parts = [block.get('text', '') for block in content
                 if isinstance(block, dict) and block.get('type') in ('input_text', 'text')]
        text = '\n'.join(part for part in parts if part)
    text = text.strip()
    # IDE가 붙인 탭·선택 영역은 사용자 요청이 아니므로 마지막 요청 본문만 남긴다.
    requestMarkers = list(IDE_REQUEST_RE.finditer(text))
    if requestMarkers:
        text = text[requestMarkers[-1].end():].strip()
    # 다른 에이전트의 판정 형식을 강제하는 프롬프트는 사용자 교정이 아니다.
    if EVALUATION_PROMPT_RE.search(text):
        return None
    if not text or text.startswith(NON_PROSE_PREFIXES) or len(text) > MAX_PROSE_CHARS:
        return None
    return text


# 레코드가 선언한 현재 작업 디렉터리를 읽는다.
def recordCwd(record, source):
    if source == 'claude':
        return record.get('cwd')
    if record.get('type') not in ('session_meta', 'turn_context'):
        return None
    payload = record.get('payload')
    return payload.get('cwd') if isinstance(payload, dict) else None


# 런타임별 세션 식별자를 읽는다.
def recordSessionId(record, source):
    if source == 'claude':
        return record.get('sessionId')
    if record.get('type') != 'session_meta':
        return None
    payload = record.get('payload')
    return (payload.get('id') or payload.get('session_id')) if isinstance(payload, dict) else None


# Codex가 내부 평가·탐색용으로 만든 하위 에이전트 세션은 사용자 기록에서 제외한다.
def isIgnoredSession(record, source):
    if source != 'codex' or record.get('type') != 'session_meta':
        return False
    payload = record.get('payload')
    return isinstance(payload, dict) and payload.get('thread_source') == 'subagent'


# 사용자 문장보다 구조화된 도구 입력을 강한 프로젝트 근거로 사용한다.
def toolInputs(record, source):
    if source == 'claude':
        if record.get('type') != 'assistant':
            return []
        message = record.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        return [block.get('input') for block in content
                if isinstance(block, dict) and block.get('type') == 'tool_use']
    payload = record.get('payload')
    if (record.get('type') != 'response_item' or not isinstance(payload, dict)
            or payload.get('type') not in ('custom_tool_call', 'function_call')):
        return []
    return [payload.get('input') or payload.get('arguments')]


# 문자열 안 경로가 접두어 오탐 없이 정확한 경계로 등장하는지 확인한다.
def pathMentioned(text, target):
    delimiters = set('/\\\t\r\n \'"`:,=()[]{};|&<>')
    start = 0
    while True:
        index = text.find(target, start)
        if index < 0:
            return False
        before = text[index - 1] if index else ''
        afterIndex = index + len(target)
        after = text[afterIndex] if afterIndex < len(text) else ''
        if (not before or before in delimiters) and (not after or after in delimiters):
            return True
        start = index + 1


# 중첩된 도구 입력이 경계가 일치하는 대상 프로젝트 경로를 가리키는지 확인한다.
def valueMentionsProject(value, root, cwd=None):
    if isinstance(value, dict):
        return any(valueMentionsProject(item, root, cwd) for item in value.values())
    if isinstance(value, list):
        return any(valueMentionsProject(item, root, cwd) for item in value)
    if not isinstance(value, str):
        return False
    rootText = root.as_posix()
    if pathMentioned(value, rootText):
        return True
    if cwd:
        try:
            relative = root.relative_to(Path(cwd).expanduser().resolve()).as_posix()
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        return bool(relative and relative != '.' and pathMentioned(value, relative))
    return False


# Name the correction marker this message tripped, or None for ordinary prose
def classify(text):
    for name, pattern in MARKER_RES:
        if pattern.search(text):
            return name
    return None


# Strip credential-shaped substrings before anything is printed
def redact(text):
    for pattern in SECRET_RES:
        text = pattern.sub('<redacted>', text)
    return text


def parseTimestamp(raw):
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


# Content words of one message, deduplicated: a rant repeating a word ten times
# is still one observation of it
def tokenize(text):
    tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_.\-]{2,}|[가-힣]{2,}', text)
    keep = set()
    for token in tokens:
        normalized = token.lower().strip('.-_')
        if len(normalized) < 3 or normalized in STOPWORDS:
            continue
        keep.add(normalized)
    return keep


# 한 런타임의 세션에서 대상 프로젝트 교정만 수집한다.
def collectSource(root, files, source, cutoff):
    corrections = []
    matchedSessions = 0
    encoded = encodePath(root)
    for path in files:
        sessionCorrections = []
        ignored = False
        active = source == 'claude' and (
            path.parent.name == encoded or path.parent.name.startswith(encoded + '-'))
        matched = active
        currentCwd = None
        sessionId = path.stem
        try:
            handle = path.open(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isIgnoredSession(record, source):
                    ignored = True
                    break
                foundSessionId = recordSessionId(record, source)
                if foundSessionId:
                    sessionId = foundSessionId
                cwd = recordCwd(record, source)
                if cwd:
                    currentCwd = cwd
                    # 상위 경로에서 시작한 세션은 직전 프로젝트 도구 사용 뒤 교정이 온다.
                    # 상위 cwd는 근거를 유지하고, 무관한 cwd는 즉시 귀속을 끊는다.
                    if pathWithin(cwd, root):
                        active = True
                        matched = True
                    elif not pathWithin(root, cwd):
                        active = False
                if any(valueMentionsProject(value, root, currentCwd)
                       for value in toolInputs(record, source)):
                    active = True
                    matched = True
                text = userText(record, source)
                if text is None:
                    continue
                if not active:
                    continue
                marker = classify(text)
                if marker is None:
                    continue
                stamp = parseTimestamp(record.get('timestamp'))
                if cutoff and stamp and stamp < cutoff:
                    continue
                clean = redact(text)
                sessionCorrections.append({
                    'at': record.get('timestamp'),
                    'sessionId': sessionId,
                    'source': source,
                    'marker': marker,
                    'text': clean[:TEXT_CAP],
                })
        if ignored:
            continue
        corrections.extend(sessionCorrections)
        matchedSessions += int(matched)
    return corrections, matchedSessions


# 런타임에 중복 기록된 같은 시각·본문 교정을 하나로 합친다.
def deduplicate(corrections):
    unique, seen = [], set()
    for item in sorted(corrections, key=lambda value: value['at'] or '', reverse=True):
        stamp = parseTimestamp(item.get('at'))
        stampKey = stamp.astimezone(timezone.utc).isoformat() if stamp else item.get('at')
        key = (stampKey, item.get('text')) if stampKey else (
            item.get('source'), item.get('sessionId'), item.get('text'))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def main():
    parser = argparse.ArgumentParser(
        description='Harvest user corrections from past session transcripts.')
    parser.add_argument('root')
    parser.add_argument('--source', choices=('auto', 'all', *SOURCE_NAMES), default='auto')
    parser.add_argument('--transcript-dir', default=None,
                        help='legacy alias for --claude-dir')
    parser.add_argument('--claude-dir', default=None,
                        help=f'Claude transcript root (default {DEFAULT_CLAUDE_TRANSCRIPT_ROOT})')
    parser.add_argument('--codex-dir', default=None,
                        help=f'Codex transcript root (default {DEFAULT_CODEX_TRANSCRIPT_ROOT})')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                        help='ignore corrections older than this (0 = no limit)')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    if args.days < 0 or args.limit < 0 or args.max_files < 1:
        print('FAIL: --days/--limit must be non-negative and --max-files must be positive',
              file=sys.stderr)
        return 1
    if args.transcript_dir and args.claude_dir:
        print('FAIL: use only one of --transcript-dir and --claude-dir', file=sys.stderr)
        return 1
    if args.transcript_dir and args.source == 'codex':
        print('FAIL: --transcript-dir is a Claude transcript option', file=sys.stderr)
        return 1
    claudeRoot = Path(args.claude_dir or args.transcript_dir).expanduser() \
        if (args.claude_dir or args.transcript_dir) else DEFAULT_CLAUDE_TRANSCRIPT_ROOT
    codexRoot = Path(args.codex_dir).expanduser() if args.codex_dir \
        else DEFAULT_CODEX_TRANSCRIPT_ROOT
    selectedSources = SOURCE_NAMES if args.source in ('auto', 'all') else (args.source,)
    # A legacy custom transcript root was historically an isolated Claude fixture/run.
    if args.transcript_dir and args.source == 'auto' and not args.codex_dir:
        selectedSources = ('claude',)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)) if args.days else None
    allCorrections, sourceReports = [], {}
    roots = {'claude': claudeRoot, 'codex': codexRoot}
    for source in selectedSources:
        if source == 'claude':
            files, truncated, discovered = findClaudeTranscripts(
                root, claudeRoot, args.max_files)
        else:
            files, truncated, discovered = findCodexTranscripts(codexRoot, args.max_files)
        corrections, matched = collectSource(root, files, source, cutoff)
        allCorrections.extend(corrections)
        sourceReports[source] = {
            'root': roots[source].as_posix(),
            'sessionsDiscovered': discovered,
            'sessionsScanned': len(files),
            'sessionsMatched': matched,
            'corrections': len(corrections),
            'truncatedFiles': truncated,
        }

    corrections = deduplicate(allCorrections)
    documentFrequency = Counter()
    for item in corrections:
        documentFrequency.update(tokenize(item['text']))
    total = len(corrections)
    limited = corrections[:args.limit]

    report = {
        'schema': 'rule-architect/harvest@2',
        'root': root.as_posix(),
        'transcriptRoot': claudeRoot.as_posix(),
        'transcriptRoots': {source: roots[source].as_posix() for source in selectedSources},
        'sources': sourceReports,
        'window': {'days': args.days or None},
        'counts': {
            'sessionsScanned': sum(item['sessionsScanned'] for item in sourceReports.values()),
            'sessionsMatched': sum(item['sessionsMatched'] for item in sourceReports.values()),
            'corrections': total,
            'duplicatesDropped': len(allCorrections) - total,
        },
        'truncated': {
            'files': any(item['truncatedFiles'] for item in sourceReports.values()),
            'corrections': total > len(limited),
        },
        'corrections': limited,
        # recurring words across DIFFERENT corrections — the recurrence signal
        'repeatedTerms': [{'term': term, 'corrections': count}
                          for term, count in documentFrequency.most_common(30) if count >= 2],
        'promotion': ('A candidate becomes a rule only when it repeats (>=2 corrections), '
                      'is recent, belongs to this project, and does not conflict with the '
                      'current source. Verify against the code before writing it down.'),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
