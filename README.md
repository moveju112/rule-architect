# rule-architect

프로젝트에 고도화된 AI 룰 세트를 자동 생성하는 Claude Code 스킬.

## 산출물

- **CLAUDE.md** (≤60줄) — 항상 로드되는 슬림 인덱스. Core Rules + 실행 명령 + Quick Reference 표
- **AGENTS.md** — Codex CLI 등 비-Claude 에이전트용 포인터. CLAUDE.md를 가리키기만 함 (내용 복제 안 함 = 드리프트 방지)
- **docs/*.md** (각 ≤150줄) — 주제별 룰 문서. 필요할 때만 로드됨
  - 항상: `ARCHITECTURE.md`, `CODING_RULES.md`, `PITFALLS.md`
  - 조건부: `CONTROLLER_RULES.md`, `ENUM_CODES.md`, `RESPONSE_KEYS.md`, `DB_RULES.md`, `DEPLOY.md`

## 핵심 원칙

**코드가 말해주지 못하는 것만 기록한다.**
`ls` 한 번, 파일 하나 읽기, grep 한 번으로 알 수 있는 사실은 룰이 아니다.
크로스파일 결합, 필수 순서, 금지 행위, 진행 중인 마이그레이션 — 이런 것만 룰이 된다.

## 설치

```bash
git clone https://github.com/moveju112/rule_architect.git ~/.claude/skills/rule-architect
```

## 사용

```
/rule-architect [project-path]
/rule-architect [project-path] --update   # 기존 룰 diff 갱신
```

자연어: "이 프로젝트 룰 만들어줘", "CLAUDE.md 고도화해줘"

## 검증

- `scripts/verify_rules.py` — 링크 무결성(양방향), 줄 수 예산, placeholder 스캔
- 퀴즈 테스트 — 생성된 룰만 보고 서브에이전트가 프로젝트 질문 5개 중 4개 이상 답하면 통과

## 선택 연동

[md-en-kr](https://github.com/moveju112/md-en-kr) 스킬이 설치돼 있으면 마지막에 영어 압축을 제안한다 (강제 아님).

## License

MIT
