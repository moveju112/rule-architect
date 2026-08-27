# 단위 테스트 결과 — 런타임 중립 룰 구조

## 요약

총 87개 케이스가 모두 통과했습니다.

결과는 PASS 87, FAIL 0, SKIP 0입니다.

`AI_RULES.md` 정본, 양쪽 심볼릭 링크, 휴대용 포인터, 매니페스트 호환성까지 확인했습니다.

## 대상

- `scripts/verify_rules.py:241` — 런타임 진입점 모드와 링크 대상 검증입니다.
- `scripts/manifest.py:47` — 실파일 해시와 심볼릭 링크 타입·대상 기록입니다.
- `scripts/quiz.py:73` — 중립 정본을 한 번만 제공하는 퀴즈 입력 구성입니다.
- `scripts/scan.py:270` — 기존 룰 파일과 깨진 진입점 탐지입니다.
- `tests/test_rules.py:162` — 심볼릭 링크·포인터·개인 룰·이전 매니페스트 회귀 케이스입니다.

## 케이스 표

| # | 관점 | 입력 | 기대 | 실제 | 판정 |
|---|---|---|---|---|---|
| 1 | 정상 | 두 진입점이 `AI_RULES.md` 상대 심링크 | strict 검증 통과 | `entryMode=symlink` | PASS |
| 2 | 정상 | 두 진입점이 짧은 일반 포인터 | portable 검증 통과 | 종료 코드 0 | PASS |
| 3 | 경계 | 휴대용 포인터 16줄 초과 | 줄 제한 실패 | `portable pointer exceeds` | PASS |
| 4 | 누락 | `AGENTS.md` 없음 | 진입점 누락 실패 | `runtime entry not found` | PASS |
| 5 | 오류 | 존재하지 않는 링크 대상 | 깨진 링크 실패 | `broken symlink` | PASS |
| 6 | 오류 | 다른 문서를 가리키는 진입점 | 정본 대상 검증 실패 | `must target AI_RULES.md` | PASS |
| 7 | 오류 | 심링크와 일반 포인터 혼용 | 혼합 모드 실패 | `mix symlink and pointer` | PASS |
| 8 | 경계 | 정본 자체가 심링크 | 정본 실파일 조건 실패 | `neutral index must be a regular file` | PASS |
| 9 | 호환 | `AI_RULES.local.md`와 `docs_local/` | 사용자 지정 경로 통과 | 종료 코드 0 | PASS |
| 10 | 이전 호환 | schema 1 일반파일 매니페스트 | 기존 기록을 clean으로 판정 | `CLEAN` | PASS |
| 11 | 부작용 | 기록된 심링크를 일반파일로 치환 | modified 충돌 판정 | `AGENTS.md modified` | PASS |
| 12 | 결정성 | 같은 프로젝트를 두 번 스캔 | JSON 바이트 동일 | 동일 출력 | PASS |
| 13 | 중복 방지 | 퀴즈 입력 파일 탐색 | 정본은 한 번, 진입점은 제외 | `AI_RULES.md`만 포함 | PASS |
| 14 | 전체 회귀 | `python3 tests/test_rules.py` | 모든 기존·신규 케이스 통과 | PASS 87 / FAIL 0 | PASS |

## 실패 상세

실패한 케이스가 없습니다.

## 미커버 위험

실제 Claude Code와 Codex 새 세션이 프로젝트 심볼릭 링크를 로딩하는 통합 동작은 단위 테스트 범위가 아닙니다.

Windows에서 Git 심볼릭 링크가 일반파일로 체크아웃되는 동작은 실제 Windows 환경 없이 검증하지 않았습니다.

이 경우를 위한 두 일반 포인터 fallback 계약과 검증 로직은 단위 테스트로 확인했습니다.

## 환경

- 실행일: 2026-08-27
- 런타임: Python 3.12.3
- 전체 테스트: `python3 tests/test_rules.py`
- 스킬 검사: `python3 /home/ubuntu/.codex/skills/.system/skill-creator/scripts/quick_validate.py .`
- 형식 검사: `git diff --check`
- 외부 연결: 사용하지 않음
