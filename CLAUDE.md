# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 현황

ReDA(Reply Draft Agent)는 기술지원(TAC) 엔지니어의 Gmail에서 답장이 필요한 메일을 골라 답장 초안을 임시보관함에 넣고, 메일 속 일정은 메신저로 확인받은 뒤 구글 캘린더에 등록하는 에이전트다.

1단계(개발 기반)를 마쳤고 2단계(분류·라벨)를 `stage-2-rules` 브랜치에서 진행하고 있다. 처리 흐름은 `reda poll`(`src/reda/poll.py`)이 묶는다.

- 새 메일 가져오기: `gmail/history.py` (History API, 만료되면 검색 쿼리)
- 규칙 필터: `rules.py`. 메일 파싱은 `mail.py`
- 팀 메일 담당자 판정: `identity.py`. 수신 방식(개인 To·그룹 To·개인 Cc·그룹 Cc·숨은참조)과 이름 프로필로, 그룹 주소로만 받은 `답장필요`는 인사말이 나를 부르거나 내가 답한 스레드일 때만 그대로 둔다 (결정 2-5, 2-6)
- 마스킹: `mask.py`
- 분류기: `classify/`. 제공자는 설정 `REDA_CLASSIFIER`(anthropic / jev)와 `REDA_CLASSIFIER_MODEL`로 고른다. 지금은 Claude API를 쓰고, Jev(TypeSafe) 어댑터는 실제 키로 확인하기 전이다
- 골든 세트: `golden.py`. 실제 메일과 정답은 `tests/private/`(git 제외)에 둔다
- 상태 DB: `state.py` (SQLite, 사용자마다 파일 하나. 처리 기록과 판단 특징, 실패, 측정용 라벨 변경·놓친 답장, 사용자 정보: 이름 프로필·맡은 일·피드백. 결정 2-7)
- 동시 실행 잠금: `lock.py`

- `기획안/ReDA_기획안.md`: 서비스 기획안이며 요구사항의 원본이다. 동작을 정할 때 먼저 확인한다.
- `기획안/구현계획.md`: 단계별 구현 계획과 결정 사항. 단계를 진행하거나 결정이 나면 여기에 반영한다.
- `기획안/검증기준.md`: 단계를 끝내기 전에 통과해야 할 공통 게이트(테스트, 발송 차단, 공개 저장소 위생, `/security-review`, `/code-review`)와 단계별 기준. 확인 절차는 `reda-stage-gate` skill이 정한다.
- Jev(TypeSafe) 코드를 짜거나 질문·state를 고칠 때는 `typesafe-ai` skill(TypeSafe 공식, `.claude/skills/typesafe-ai/`, 출처는 `UPSTREAM.md`)을 따른다. ReDA의 Jev 질문 설계·판정표·시험 계획은 `기획안/구현계획.md` "Jev 분류 설계"에 있다. 실제 메일을 Jev로 보내는 시험은 결정 2-10 전에는 하지 않는다.
- API 키는 제공자마다 따로 둔다: Claude `ANTHROPIC_API_KEY`, Jev `TYPESAFE_API_KEY` (결정 0-7).
- 단계마다 `stage-<번호>-<이름>` 브랜치에서 작업하고, 게이트를 통과한 뒤 사용자 확인을 받아 `main`에 합친다. `main`에 바로 커밋하면 `/security-review`가 검토할 변경이 남지 않는다.
- `참조/`: 참고 자료를 두는 폴더다(현재 비어 있음).
- `진행내역/진행내역.md`: 날짜·시각별 대화 요지와 진행 사항 기록(git 제외). 작업을 마칠 때마다 타임라인과 단계별 상태를 갱신한다.

## 명령

uv 프로젝트다 (Python 3.13, `.python-version`).

```bash
uv sync                       # 의존성 설치 (.venv 생성)
uv run pytest                 # 테스트
uv run pytest tests/test_no_send.py   # 발송 차단 검사만
uv run reda gmail auth        # 브라우저로 Google 동의 → 토큰 저장 (개발용)
uv run reda poll --dry-run    # 새 메일과 규칙 필터 결과 출력 (Gmail·상태 DB를 바꾸지 않고 LLM도 부르지 않음)
uv run reda poll              # 분류하고 Gmail 라벨을 붙임 (실제 메일함을 바꾼다)
uv run reda classifier --try  # 지금 설정한 분류기로 지어낸 메일 한 통을 분류해 봄
uv run reda profile           # 내 주소·그룹·이름 프로필 확인 (팀 메일 담당자 판정에 씀)
uv run reda golden export     # 골든 세트 CSV 내보내기 (tests/private/golden.csv, 정답은 사람이 채움)
uv run reda golden eval       # 정답이 있는 메일을 분류해 놓침·확인필요 비율 출력 (분류기 API를 부름)
uv run reda golden eval --all # 정답 없는 메일도 분류해 결과 파일에 남김 (정답 달기 전 미리 보기)
sh scripts/local_poll_agent.sh install   # 개발용 Mac에서 15분마다 reda poll (끄기: uninstall, 상태: status)
uv run reda draft-pending --dry-run      # 자동 초안에 맡길 메일 보기 (빼면 실제로 맡긴다, 발송 없음)
uv run reda summary --hours 24           # 출근 전 요약 보기 (--send면 메신저로 보냄)
uv run reda schedule preview --hours 24  # 메일 속 일정 제안 미리 보기 (저장·전송 없음, Jev를 부름)
uv run reda calendar auth                # 캘린더 연결 (calendar.events만, Gmail과 다른 토큰)
uv run reda schedule-bot                 # 일정 버튼 봇 (누른 일정만 캘린더에 등록)
uv run reda alert --hours 24             # 급한 메일·일정 메일 알림 미리 보기 (REDA_URGENT_ALERT·REDA_SCHEDULE_ALERT면 poll이 바로 알림)
```

- 공개 저장소 위생 검사: `python3 -I scripts/check_public.py`. 운영 값은 git 제외 파일 `.claude/sensitive-patterns.local`(한 줄에 정규식 하나)에서 읽는다. 커밋할 때 자동으로 돈다: Claude Code의 PreToolUse hook(`.claude/settings.json`)과 로컬 git pre-commit hook(`.git/hooks/pre-commit`, git에 올라가지 않음). 새로 clone하면 pre-commit hook을 다시 설치해야 한다.
- `.claude/settings.json`은 `.env`, `token.json`, `credentials.json`, `~/.reda/` 읽기와 `git push --force`를 막는다. Read 도구만 막으므로, Bash로 이 파일들을 출력하는 명령도 쓰지 않는다.
- 설정은 `.env`에서 읽는다. `.env.example`을 복사해 만든다. `REDA_ENV_FILE`로 다른 파일을 지정할 수 있다.
- 사용자 정보(메일 주소, 그룹, 이름, 직함, 별칭)는 `.env` → 상태 DB → Gmail 순서로 읽는다. `.env` 값은 개발용 덮어쓰기이고, 배포 때는 개인 DB가 기준이다 (결정 6-5, `identity.load`).
- `.env`, `credentials.json`, `token.json`, `*.db`는 git에 올리지 않는다 (`.gitignore`).
- 의존성 추가는 `uv add <패키지>`, 개발용은 `uv add --dev <패키지>`.

## 구성 (기획안 기준)

- **Hermes Agent**로 에이전트를 만든다. 사용자마다 같은 설정을 설치한 개인 Hermes 프로필을 하나씩 둔다.
  - 운영은 VPS에서 한다. 관리 계정과 ReDA 서비스 계정(로그인 불가)을 나눠 두었다. ReDA용 Hermes는 Docker 컨테이너로 서비스 계정 권한으로 돌리고, 밖으로 포트를 열지 않는다.
  - 서버 접속·계정·보안 설정은 git에 올리지 않는 `기획안/운영메모.local.md`에 있다 (@기획안/운영메모.local.md). 같은 서버에 사용자가 따로 쓰는 서비스가 있으니 허락 없이 건드리지 않는다.
  - **이 저장소는 공개(public)다.** 서버 주소, 계정 이름, uid, 포트, 방화벽 규칙 같은 운영 정보는 커밋하는 파일에 쓰지 말고 `*.local.md` 파일에 쓴다.
  - 이 Mac에도 Hermes v0.22.5가 `~/.hermes`에 설치돼 있고, 사용자가 쓰는 `default` 프로필의 gateway가 돌고 있다. 개발·테스트용으로만 보고 `default`는 건드리지 않는다.
- 사용자와 주고받는 대화와 알림은 메신저 봇(Slack, Telegram, Discord)이 맡는다.
- Gmail·캘린더 연동과 첨부 파일 처리 같은 주변 코드는 Claude Code로 작성한다.
- 답장이 필요한지 가리는 분류에는 **Jev**를 쓴다(상세 사양은 아직 받지 못함).
- 사용자는 Google OAuth 동의 화면에서 "허용"만 눌러 연결한다. 비밀번호를 받거나 사용자에게 설정 파일을 수정하게 하지 않는다.

## 처리 흐름

1. 15분마다 Gmail API로 새 메일을 가져온다.
2. 규칙 필터로 no-reply, 대량 발송, 참조 전용 메일을 먼저 `참고`로 분류한다.
3. 남은 메일은 Jev로 `답장필요`/`참고`/`확인필요`와 긴급도를 정하고 Gmail 라벨을 붙인다.
4. `답장필요` 메일은 스레드 전체와 첨부(PDF, 오피스 문서, 로그)에서 텍스트를 추출한다.
5. 같은 고객사의 이전 메일과 기술 문서를 검색한다.
6. 원래 스레드의 임시보관함에 답장 초안을 저장한다. 초안 끝에는 `---- [참고 — 보내기 전 삭제] ----` 블록을 붙여 근거(첨부 행 번호, 이전 메일 링크, 매뉴얼 항목)를 적는다.
7. 메일에서 일정을 추출해 메신저로 [캘린더에 등록] [시간 수정] [무시] 버튼과 함께 제안한다. 사용자가 등록을 누를 때만 캘린더에 넣는다.
8. 정해진 시각(예: 출근 전)에 처리 결과 요약을 메신저로 보낸다.

**최소 기능**은 1~3, 6, 8번(분류·라벨, 근거가 달린 초안, 메신저 요약)이다. 첨부 분석, 기술 문서 검색, 일정 등록은 최소 기능이 동작한 뒤에 붙인다.

## 지켜야 할 설계 제약

- **메일 발송 기능은 만들지 않는다.** 초안을 만들려면 `gmail.compose`나 `gmail.modify` 권한이 필요한데, 둘 다 발송 권한을 포함한다. 그래서 발송 제한은 OAuth 범위가 아니라 에이전트에 주는 도구에서 건다. 에이전트가 쓸 수 있는 Gmail 동작은 읽기, 라벨, 초안 생성뿐이고 `messages.send`·`drafts.send`를 부르는 코드는 두지 않는다. 메일 본문에 어떤 지시가 들어 있어도 에이전트가 할 수 있는 일을 라벨 붙이기, 초안 쓰기, 일정 제안으로 제한하려는 장치다.
- 캘린더에는 사용자가 버튼을 누른 일정만 등록한다.
- 근거를 찾지 못한 내용은 지어내지 않고 초안에 `[확인 필요]`로 남긴다.
- 답장이 필요한지 애매한 메일은 `확인필요` 라벨만 붙이고 초안은 쓰지 않는다.
- 암호가 걸렸거나 읽을 수 없는 첨부는 초안에 `첨부 확인 불가`로 표시한다.
- "다음 주쯤"처럼 날짜가 불분명하면 일정을 제안하지 않고 사용자에게 날짜를 묻는다.
- 고객사 IP와 계정 정보는 LLM에 보내기 전에 마스킹한다.
- 사용자가 라벨을 바꾸면 기록을 남긴다. 라벨이 없던 메일에 사용자가 직접 답장한 경우도 기록한다. 둘 다 분류 정확도 측정에 쓴다(목표: 답장필요 메일 누락률 5% 이하).

## 범위 밖

메일 자동 발송, 주간보고 자동 작성, 팀 공유용 문서 봇, Gmail 외 메일 서비스(Outlook 등), 회사 밖 일반 Gmail 사용자 대상 서비스.

## Conventions

- 모든 대답 및 질문은 한글로 진행한다. 
