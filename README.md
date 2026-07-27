# rule-architect

프로젝트에 고도화된 AI 룰 세트를 자동 생성하는 Claude Code 스킬.

---

# ⚠️ 필수: 모델 요구사항

> **이 스킬은 판단력에 의존한다. 약한 모델로 돌리면 룰이 망가진다.**
> 특히 `--update`는 "변경된 섹션만 diff 수정" 지시를 지켜야 하는데,
> 약한 모델은 전체 파일을 재생성해서 **손으로 추가한 룰을 날려버린다.**

| 도구 | 최소 | 권장 | 금지 |
|---|---|---|---|
| **Claude Code** | Sonnet 5 (신규 생성만) | **Opus 5 / Fable 5 + effort `high`** | Haiku 계열 |
| **Codex CLI / GPT** | GPT-5 계열 flagship | **GPT-5 Codex + reasoning `high`** | `mini` · `nano` 계열 |

**`--update` 실행 시에는 최소 등급으로 돌리지 마라.**
Claude는 Opus 5 / Fable 5, GPT는 flagship 등급을 써라.
그리고 실행 전에 **기존 CLAUDE.md를 커밋해 둬라.** 사고 나도 되돌린다.

모델과 무관한 부분: `scripts/verify_rules.py`.
줄 수·링크·인용 실존 검사는 결정적이라 모델 영향을 받지 않는다.

---

## 산출물

- **CLAUDE.md** (≤60줄) — 항상 로드되는 슬림 인덱스. Core Rules + 실행 명령 + **Routing 표**
  - Routing 표는 "주제 → 파일"이 아니라 **"상황/작업 → 읽을 파일"**. 트리거 없는 행은 금지
- **AGENTS.md** — Codex CLI 등 비-Claude 에이전트용 포인터. CLAUDE.md를 가리키기만 함 (내용 복제 안 함 = 드리프트 방지)
- **docs/*.md** (각 ≤150줄) — 주제별 룰 문서. 필요할 때만 로드됨
  - 항상: `ARCHITECTURE.md`, `CODING_RULES.md`, `PITFALLS.md`
  - 조건부: `CONTROLLER_RULES.md`, `ENUM_CODES.md`, `RESPONSE_KEYS.md`, `DB_RULES.md`, `DEPLOY.md`
- **docs/tasks/*.md** (각 ≤80줄) — 반복 작업 playbook. 번호 절차 + 단계마다 `file:line` 근거
  - git log에서 같은 파일 묶음이 3회 이상 함께 수정된 작업만 생성 (예: `ADD_API.md`, `ADD_MODEL.md`)
  - 크로스파일 결합 룰은 별도 룰로 두지 않고 playbook 단계로 기록

## 룰 형식

모든 룰은 등급 + 이유 + 대비 예시를 갖는다.

```markdown
- **[MUST|NEVER|PREFER]** 룰 한 줄
  - why: 한 줄
  - ❌ 이 프로젝트의 실제 위반 예 (file:line 또는 스니펫)
  - ✅ 올바른 예
```

충돌 시 우선순위: `NEVER` > `MUST` > `PREFER`.
`PITFALLS.md`는 **증상 → 원인 → 수정** 형식. 증상은 에러 메시지 그대로 적어 grep 가능하게 한다.

## 핵심 원칙

**코드가 말해주지 못하는 것만 기록한다.**
`ls` 한 번, 파일 하나 읽기, grep 한 번으로 알 수 있는 사실은 룰이 아니다.
크로스파일 결합, 필수 순서, 금지 행위, 진행 중인 마이그레이션 — 이런 것만 룰이 된다.

## 설치

```bash
git clone https://github.com/moveju112/rule-architect.git ~/.claude/skills/rule-architect

# 슬래시 명령으로 쓰려면 (선택)
ln -s ~/.claude/skills/rule-architect/commands/rule-architect.md ~/.claude/commands/
```

링크를 걸지 않으면 `/rule-architect` 슬래시 명령은 등록되지 않는다.
그래도 자연어 트리거로는 스킬이 동작한다.

## 사용

```
/rule-architect [project-path]
/rule-architect [project-path] --update   # 기존 룰 diff 갱신
```

자연어: "이 프로젝트 룰 만들어줘", "CLAUDE.md 고도화해줘"

## 검증

- `scripts/verify_rules.py` — 링크 무결성(양방향), 줄 수 예산, placeholder 스캔, **evidence 신선도**
  - evidence 신선도: 룰이 인용한 `path/file.ext:42`가 실제 존재하는지 검사. 죽은 인용 = 실패 (stale 룰 탐지)
- 퀴즈 테스트 — 생성된 룰만 보고(소스 접근 없이) 서브에이전트가 5문항에 답한다. 구성 고정:
  - **회상 3** — "새 모델은 어디에 등록하나?"
  - **판단 1** — "X 하려는데 Y 방식 써도 되나?" (룰 적용 능력 측정)
  - **negative 1** — 룰에 없는 걸 묻는다. "룰에 없음, 소스 확인 필요"라 답해야 통과. 지어내면 실패
  - 4문항 이상 정답 **AND** negative 통과 → pass

## Hook 승격

lint·hook으로 기계 강제 가능한 룰(네이밍, 금지 import, 포맷)은 **문서에 기록하지 않는다.**
최종 리포트에 "hook 승격 후보"로만 제시하고, 적용은 사용자가 명시 요청할 때만 한다.
문서 룰 총량이 줄면 항상 로드되는 컨텍스트 품질이 올라간다.

## 선택 연동

[md-en-kr](https://github.com/moveju112/md-en-kr) 스킬이 설치돼 있으면 마지막에 영어 압축을 제안한다 (강제 아님).

## License

MIT
