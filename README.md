# ReDA (Reply Draft Agent)

기술지원(TAC) 엔지니어의 Gmail을 대신 정리해 주는 에이전트입니다.

**데모 영상:** https://youtu.be/LGgl9eFyeZw (1분, 지어낸 고객 메일 → 분류·라벨 → 답장 초안 → 일정 버튼 → 캘린더 등록)

## 누구를 위한 에이전트인가

고객 장애·설정·케이스 문의 메일을 매일 많이 받는 기술지원 엔지니어를 위한 에이전트입니다. 엔지니어는 그동안 이런 일에 시간을 써 왔습니다.

- 답장할 메일을 고르는 일
- 같은 고객사의 지난 메일을 찾아 답장을 쓰는 일
- 메일 속 일정을 챙기는 일

ReDA가 이 일을 대신합니다.

- **분류:** 15분마다 새 메일을 `답장필요` / `확인필요` / `참고`로 나누고, 긴급도와 함께 Gmail 라벨을 붙입니다.
- **답장 초안:** 답장이 필요한 메일은 스레드와 같은 고객사의 지난 메일을 보고, 근거를 단 답장 초안을 Gmail 임시보관함에 넣습니다.
- **알림:** 출근 전(08:00)에 처리 결과를 요약해 텔레그램으로 보내고, 급한 메일은 받는 즉시 알립니다.
- **일정:** 메일 속 일정은 [캘린더에 등록] [시간 수정] [무시] 버튼으로 확인받고, 누른 일정만 Google 캘린더에 등록합니다.

메일을 보내는 기능은 만들지 않습니다. 초안은 사람이 읽고 직접 보냅니다.

## 입력과 결과 예시

| 받은 메일 (지어낸 예) | ReDA가 한 일 |
|---|---|
| `[긴급] 방화벽 이중화 절체 후 DMZ 웹서버 외부 접속 간헐 끊김`, 11/18(수) 14시 원격 점검 요청 | `답장필요`·`긴급` 라벨, 텔레그램 긴급 알림, 일정 버튼(11/18 14:00–15:00) |
| `L4 스위치 SSL 인증서 교체 작업 가능 여부 문의`, 본문에 장비 관리자 비밀번호 포함 | 답장 초안: 모르는 내용(무중단 교체, 체인 인증서, 원격 지원 가능 여부)은 `[확인 필요]`로 남기고 필요한 정보를 되물음. 비밀번호는 초안에 다시 쓰지 않고 "메일로 재전송하지 말아 달라"고 안내. 일정 버튼 → 캘린더 11/20 22:00 |

## 구성

- **분류:** 규칙 필터 + TypeSafe **Jev**. 판단은 Jev가 확률로 하고, 최종 결정은 코드의 판정표가 합니다.
- **초안:** **Hermes Agent**가 ReDA MCP 도구로 씁니다. 에이전트가 쓸 수 있는 동작은 스레드 읽기, 지난 메일 검색, 초안 저장, 보류, 라벨뿐입니다. 터미널·파일·브라우저 도구는 주지 않습니다.
- **메신저:** Telegram 봇 하나 (요약, 알림, 일정 버튼)
- **운영:** VPS의 Docker 컨테이너 2개 (`reda-poll` 15분마다, `reda-schedule-bot` 버튼 처리). 로그인할 수 없는 서비스 계정 권한으로 돌고, 밖으로 여는 포트가 없습니다.
- **코드:** Python 3.13, uv, SQLite (사용자마다 상태 DB 하나)

## 안전장치

- **발송 차단:** Gmail 발송 API(`messages.send`, `drafts.send`)를 부르는 코드가 없고, 테스트가 이를 검사합니다.
- **마스킹:** 고객 IP·계정·비밀번호·전화번호 등은 LLM에 보내기 전에 가립니다. 비밀번호·계정 같은 값은 초안에서도 되살리지 않습니다.
- **메일 속 지시 무시:** 메일 본문에 "이전 지시를 무시하라" 같은 문장이 있어도 에이전트는 라벨·초안·보류만 할 수 있습니다. 공격 메일 10종 시험을 모두 통과했습니다.
- **근거 없는 내용 금지:** 근거를 찾지 못한 내용은 지어내지 않고 `[확인 필요]`로 남깁니다. 초안 끝의 `[참고 — 보내기 전 삭제]` 블록에 근거(원문 메일 링크, 검색 결과)를 적습니다.
- **캘린더:** 사용자가 버튼을 누른 일정만 등록합니다. 날짜가 흐리면("다음 주쯤") 제안하지 않고 묻습니다.

## 진행 상황

### 되는 것
- VPS에서 24시간 운영 중 (2026-11-14부터)
- 규칙 필터와 Jev 분류, 라벨과 긴급도
  - 팀 메일(그룹 주소)은 인사말과 담당 이력으로 내 일인지 판단합니다
- 근거 블록이 달린 답장 초안 자동 작성 (ChatGPT 구독 모델)
- 텔레그램으로 출근 전 요약, 급한 메일 알림, 일정 버튼 → Google 캘린더 등록
- 자동 테스트 460여 개, 메일 발송 차단 검사, 공개 저장소 위생 검사

### 아직 안 되는 것
- 실제 메일로 분류 정확도·초안 품질 측정 (목표: 답장필요 메일 누락률 5% 이하)
- 다른 사용자 연결 (Google "허용"만 누르면 연결되는 흐름), 팀원·팀 추가
- 개인 봇에서 비서처럼 대화하기 ("오늘 일정 알려줘"). 지금은 메일 속 지시가 대화로 에이전트를 움직이지 못하게 대화 경로를 막아 두었습니다
- 첨부 파일 분석, 기술 문서 검색

## 실행 (개발용 Mac)

```bash
uv sync
cp .env.example .env              # 내 주소, 분류기 키 등 설정
uv run reda gmail auth            # 브라우저로 Google 동의 (Gmail)
uv run reda calendar auth         # 캘린더 연결 (일정 등록을 쓸 때)
uv run reda poll --dry-run        # 메일함을 바꾸지 않고 새 메일과 규칙 필터 결과 보기
uv run reda poll                  # 분류하고 라벨 붙이기 (REDA_AUTO_DRAFT=true면 초안까지)
uv run reda summary --hours 24    # 출근 전 요약 미리 보기 (--send면 메신저로 보냄)
uv run pytest                     # 테스트
```

Google Cloud Console에서 만든 OAuth 클라이언트 파일(`credentials.json`)이 필요합니다. 설정 항목은 `.env.example`에 설명이 있습니다.

## 배포 (VPS)

서버에 Docker와 Hermes Agent 이미지가 있어야 합니다. 비밀 값(토큰, API 키)은 git에도 화면에도 남기지 않습니다. 아래의 `<…>`는 각자의 값으로 바꿉니다.

**1. 서비스 계정 만들기 (서버, 처음 한 번)**

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin reda
id reda                           # uid·gid를 compose.env에 적는다
```

**2. 소스와 설정 보내기 (Mac)**

```bash
ssh <관리계정@서버> 'mkdir -p ~/reda-staging/src'
git archive HEAD | ssh <관리계정@서버> 'tar -x -C ~/reda-staging/src'
ssh <관리계정@서버> "echo deploy-$(git rev-parse --short HEAD) > ~/reda-staging/src/deploy/VERSION"
```

서버의 `~/reda-staging/compose.env` (권한 600):

```bash
REDA_ROOT=/srv/reda               # 데이터 폴더
REDA_UID=<서비스 계정 uid>
REDA_GID=<서비스 계정 gid>
HERMES_IMAGE=<서버의 Hermes 이미지 ID>   # docker images로 확인, latest 대신 ID로 고정
REDA_AGENT_PROVIDER=openai-codex  # 초안 모델 제공자 (ChatGPT 구독)
REDA_AGENT_MODEL=<모델 이름>
```

**3. 비밀 파일 넘기기 (Mac)**

```bash
sh deploy/mac-handoff.sh <관리계정@서버>
```

Mac의 15분 자동 실행을 끄고(두 곳에서 같은 메일함을 처리하지 않게), 상태 DB 복사본, Gmail·캘린더 토큰, OAuth 클라이언트 파일, `.env`, 텔레그램 설정을 서버의 준비 폴더로 보냅니다.

**4. 설치 (서버)**

```bash
sudo sh ~/reda-staging/src/deploy/server-setup.sh ~/reda-staging
```

이미지를 만들고, 비밀 파일을 서비스 계정 소유(600)로 옮기고, 준비 폴더의 비밀 파일을 지운 뒤 컨테이너를 띄웁니다. 처음에는 ChatGPT 구독 로그인을 묻습니다. 제공자 목록에서 OpenAI Codex를 고르고, 나오는 주소를 브라우저로 열어 코드를 넣습니다. 다시 실행해도 됩니다(코드를 고친 뒤 2·4번).

**5. 확인 (서버)**

```bash
sudo docker ps --filter name=reda                 # reda-poll, reda-schedule-bot 둘 다 Up
sudo docker logs --tail 20 reda-poll              # 15분마다 "라벨 N통 ... 실패 0"
sudo docker exec reda-poll hermes -p reda auth status openai-codex   # openai-codex: logged in
sudo docker restart reda-poll                     # 켜지자마자 한 번 돌기 (시험할 때)
```

자세한 계획과 결정 사항은 `기획안/`에 있습니다.
