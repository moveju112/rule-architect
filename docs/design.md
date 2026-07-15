# rule-architect 설계 (2026-07-15)

## 목적

새 프로젝트에 hnote 수준의 고도화된 AI 룰 세트(CLAUDE.md + docs/*.md)를 자동 생성하는 독립 스킬.
AI 에이전트가 룰을 **토큰 효율적으로** 소비하도록 구조를 강제한다.

## 결정 사항 (사용자 승인)

- 이름: `rule-architect`, repo: `moveju112/rule-architect` (독립 repo)
- 생성 범위: CLAUDE.md + docs/*.md 세트 (옵션 B)
- md-en-kr: 의존 아님. 설치 감지 시 마지막에 영어 압축 **제안만**
- graphify 연동 제외 (별도 스킬로 필요 시 수동)

## 동작 5단계

1. **스캔**: 병렬 탐색(구조/컨벤션/프로토콜/DB/테스트). 기존 룰 파일 존재 여부 확인
2. **문서 세트 선택**: 스택 감지 기반 조건부 매트릭스
   - 항상: ARCHITECTURE.md, CODING_RULES.md, NAMING_CONVENTIONS.md
   - 조건부: CONTROLLER_RULES.md(MVC), ENUM_CODES.md(enum 존재), RESPONSE_KEYS.md(API 응답 규약), DB_RULES.md(DB 레이어), DEPLOY.md(배포 스크립트 존재)
3. **docs 생성**: 실코드에서 예시 추출. UPPERCASE=AI룰 / lowercase=참고 규약
4. **CLAUDE.md 생성**: 슬림 인덱스(≤60줄). Quick Reference 표
5. **검증**: 링크 유효성·placeholder 부재·줄수 예산 + 서브에이전트 퀴즈(룰만 보고 프로젝트 질문 답변)

## AI 효율 원칙

- Progressive disclosure: 매 세션 로드 토큰 최소화
- 코드에서 유도 가능한 사실 기록 금지 (non-derivable only)
- 파일별 예산: CLAUDE.md ≤60줄, docs 각 ≤150줄
- `--update` 모드: 기존 룰 diff 갱신

## 테스트 계획 (TDD)

- RED: tracker 프로젝트, 스킬 없이 룰 생성 → 실패 패턴 기록
- GREEN: SKILL.md 작성 → 같은 시나리오 준수 확인
- REFACTOR: 루프홀 카운터 추가
