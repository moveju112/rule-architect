#!/usr/bin/env python3
"""Correction harvester: mine past sessions for the rules a cold scan cannot see.

Usage: python3 harvest.py <project-root> [--transcript-dir DIR] [--days N]
                          [--limit N] [--max-files N]

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

DEFAULT_TRANSCRIPT_ROOT = Path.home() / '.claude' / 'projects'
DEFAULT_DAYS = 180
DEFAULT_LIMIT = 60
DEFAULT_MAX_FILES = 400
TEXT_CAP = 240

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
}


# Transcript directory name for a path: every non-alphanumeric byte becomes a dash
def encodePath(path):
    return re.sub(r'[^A-Za-z0-9]', '-', Path(path).as_posix())


# Sessions for this project, plus sessions started in its subdirectories
def findTranscripts(root, transcriptRoot, maxFiles):
    encoded = encodePath(root)
    if not transcriptRoot.is_dir():
        return [], False
    directories = [entry for entry in sorted(transcriptRoot.iterdir())
                   if entry.is_dir() and (entry.name == encoded
                                          or entry.name.startswith(encoded + '-'))]
    files = []
    for directory in directories:
        files += sorted(directory.glob('*.jsonl'))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:maxFiles], len(files) > maxFiles


# The user's own prose out of one transcript record, or None
def userText(record):
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
    text = text.strip()
    if not text or text.startswith(NON_PROSE_PREFIXES) or len(text) > MAX_PROSE_CHARS:
        return None
    return text


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


def collect(root, files, cutoff, limit):
    corrections, documentFrequency = [], Counter()
    scanned = 0
    for path in files:
        scanned += 1
        try:
            handle = path.open(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # a transcript directory can hold sessions from a sibling path
                cwd = record.get('cwd')
                if cwd and not Path(cwd).as_posix().startswith(root.as_posix()):
                    continue
                text = userText(record)
                if text is None:
                    continue
                marker = classify(text)
                if marker is None:
                    continue
                stamp = parseTimestamp(record.get('timestamp'))
                if cutoff and stamp and stamp < cutoff:
                    continue
                clean = redact(text)
                corrections.append({
                    'at': record.get('timestamp'),
                    'sessionId': record.get('sessionId'),
                    'marker': marker,
                    'text': clean[:TEXT_CAP],
                })
                documentFrequency.update(tokenize(clean))
    corrections.sort(key=lambda item: item['at'] or '', reverse=True)
    return corrections[:limit], len(corrections), documentFrequency, scanned


def main():
    parser = argparse.ArgumentParser(
        description='Harvest user corrections from past session transcripts.')
    parser.add_argument('root')
    parser.add_argument('--transcript-dir', default=None,
                        help=f'transcript root (default {DEFAULT_TRANSCRIPT_ROOT})')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                        help='ignore corrections older than this (0 = no limit)')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f'FAIL: {root} is not a directory', file=sys.stderr)
        return 1
    transcriptRoot = Path(args.transcript_dir).expanduser() if args.transcript_dir \
        else DEFAULT_TRANSCRIPT_ROOT

    files, truncatedFiles = findTranscripts(root, transcriptRoot, args.max_files)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)) if args.days else None
    corrections, total, frequency, scanned = collect(root, files, cutoff, args.limit)

    report = {
        'schema': 'rule-architect/harvest@1',
        'root': root.as_posix(),
        'transcriptRoot': transcriptRoot.as_posix(),
        'window': {'days': args.days or None},
        'counts': {'sessionsScanned': scanned, 'corrections': total},
        'truncated': {'files': truncatedFiles, 'corrections': total > len(corrections)},
        'corrections': corrections,
        # recurring words across DIFFERENT corrections — the recurrence signal
        'repeatedTerms': [{'term': term, 'corrections': count}
                          for term, count in frequency.most_common(30) if count >= 2],
        'promotion': ('A candidate becomes a rule only when it repeats (>=2 corrections), '
                      'is recent, belongs to this project, and does not conflict with the '
                      'current source. Verify against the code before writing it down.'),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
