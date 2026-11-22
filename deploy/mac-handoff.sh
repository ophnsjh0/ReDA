#!/bin/sh
# 개발용 Mac → VPS로 운영을 넘긴다 (4단계 5번, 6단계 전까지 쓰는 임시 방법).
#   sh deploy/mac-handoff.sh <관리계정@서버> [SSH 키]
# 하는 일:
#   1. Mac의 15분 자동 실행(local.reda.poll)을 끄고, 돌고 있는 reda poll이 끝나기를 기다린다
#      (두 곳에서 같은 메일함을 처리하면 초안이 두 번 생긴다)
#   2. 상태 DB를 일관된 복사본으로 뜬다 (sqlite backup)
#   3. 비밀 파일을 서버의 준비 폴더(~/reda-staging/secrets, 권한 700/600)로 보낸다
#      Gmail 토큰·OAuth 클라이언트 파일, 캘린더 토큰(있으면), 상태 DB, ReDA .env, Hermes 프로필 .env(텔레그램)
# 파일 내용은 화면에 출력하지 않는다. 서버에서는 server-setup.sh가 서비스 계정 소유로 옮기고 준비 폴더를 지운다.
#
# 시연용 인스턴스(운영과 다른 메일함)는 환경 변수로 다른 설정을 보낸다:
#   REDA_HANDOFF_ENV_FILE=.env.demo REDA_HANDOFF_STAGING=reda-demo-staging \
#   REDA_HERMES_PROFILE_ENV=<시연 봇 토큰이 든 Hermes .env> sh deploy/mac-handoff.sh <관리계정@서버>
# 이때는 Mac 자동 실행(운영 메일함)을 끄지 않고, 상태 DB가 없으면 서버에서 새 DB로 시작한다.
# 시연용 설정이 운영 데이터 폴더·Gmail 토큰·Hermes .env(텔레그램 봇)를 가리키면 멈춘다.
set -eu
TARGET="${1:?사용법: sh deploy/mac-handoff.sh <관리계정@서버> [SSH 키]}"
KEY="${2:-$HOME/.ssh/id_ed25519}"
REPO=$(cd "$(dirname "$0")/.." && pwd)
# .env와 자동 실행 스크립트는 운영 중인 작업 폴더(git의 첫 worktree)에 있다
MAIN=$(git -C "$REPO" worktree list --porcelain | sed -n '1s/^worktree //p')
REPO="${MAIN:-$REPO}"
MAIN_ENV="$REPO/.env"
MAIN_HERMES_ENV="$HOME/.hermes/profiles/reda-dev/.env"
ENV_FILE="${REDA_HANDOFF_ENV_FILE:-$MAIN_ENV}"
case "$ENV_FILE" in /*) ;; *) ENV_FILE="$REPO/$ENV_FILE" ;; esac
[ -f "$ENV_FILE" ] || { echo "없음: $ENV_FILE" >&2; exit 1; }
STAGING="${REDA_HANDOFF_STAGING:-reda-staging}"
echo "$STAGING" | grep -Eq '^[A-Za-z0-9._-]+$' || { echo "REDA_HANDOFF_STAGING은 서버 홈 아래 폴더 이름 하나: $STAGING" >&2; exit 1; }

# 심볼릭 링크를 풀어 같은 파일인지 비교한다 (없는 파일은 경로 그대로)
real() {
    if [ -e "$1" ]; then (cd "$(dirname "$1")" && echo "$(pwd -P)/$(basename "$1")"); else echo "$1"; fi
}
if [ "$(real "$ENV_FILE")" = "$(real "$MAIN_ENV")" ]; then MAIN_MODE=1; else MAIN_MODE=0; fi
if [ "$MAIN_MODE" = 1 ]; then
    HERMES_PROFILE_ENV="${REDA_HERMES_PROFILE_ENV:-$MAIN_HERMES_ENV}"
else
    # 기본값을 두지 않는다: 운영 봇 토큰이 시연용으로 가지 않게
    HERMES_PROFILE_ENV="${REDA_HERMES_PROFILE_ENV:?시연용은 REDA_HERMES_PROFILE_ENV로 시연 봇의 Hermes .env를 지정한다}"
fi
SSH="ssh -i $KEY -o BatchMode=yes"

# 경로는 ReDA가 실제로 쓰는 .env 기준으로 정한다 (셸 환경 변수만 보면 다른 폴더를 보낼 수 있다)
paths_of() {
    (cd "$REPO" && REDA_ENV_FILE="$1" uv run --quiet python -I -c '
import os
from dotenv import load_dotenv
load_dotenv(os.environ["REDA_ENV_FILE"], override=False)
from reda.config import Settings
s = Settings.from_env()
for p in (s.data_dir, s.gmail_token_file, s.gmail_credentials_file, s.calendar_token_file):
    print(p.expanduser())
')
}
PATHS=$(paths_of "$ENV_FILE")
DATA=$(echo "$PATHS" | sed -n 1p)
TOKEN=$(echo "$PATHS" | sed -n 2p)
CREDS=$(echo "$PATHS" | sed -n 3p)
CAL=$(echo "$PATHS" | sed -n 4p)
for f in "$TOKEN" "$CREDS" "$ENV_FILE" "$HERMES_PROFILE_ENV"; do
    [ -f "$f" ] || { echo "없음: $f (경로를 확인한다)" >&2; exit 1; }
done
# 운영은 상태 DB가 꼭 있어야 한다 (경로가 틀려 빈 DB로 넘기면 처리 기록을 잃는다)
[ "$MAIN_MODE" = 0 ] || [ -f "$DATA/state.db" ] || { echo "없음: $DATA/state.db (경로를 확인한다)" >&2; exit 1; }

if [ "$MAIN_MODE" = 0 ]; then
    # 시연용 설정이 운영 메일함·운영 봇을 가리키면 멈춘다
    MAIN_PATHS=$(paths_of "$MAIN_ENV")
    MAIN_DATA=$(echo "$MAIN_PATHS" | sed -n 1p)
    MAIN_TOKEN=$(echo "$MAIN_PATHS" | sed -n 2p)
    [ "$(real "$DATA")" != "$(real "$MAIN_DATA")" ] || { echo "시연용 REDA_DATA_DIR가 운영과 같다: $DATA" >&2; exit 1; }
    [ "$(real "$TOKEN")" != "$(real "$MAIN_TOKEN")" ] || { echo "시연용 Gmail 토큰이 운영 토큰이다: $TOKEN" >&2; exit 1; }
    [ "$(real "$HERMES_PROFILE_ENV")" != "$(real "$MAIN_HERMES_ENV")" ] \
        || { echo "시연용 Hermes .env가 운영 봇 설정이다: $HERMES_PROFILE_ENV" >&2; exit 1; }
    echo '1) 시연용: Mac 자동 실행(운영 메일함)은 그대로 둔다'
else
    echo '1) Mac 자동 실행 끄기'
    # launchctl bootout은 돌고 있는 reda poll을 바로 끊는다 (초안·라벨은 생겼는데 상태 DB에 기록이 없는 메일이 생김).
    # 그래서 돌고 있는 poll이 끝나기를 먼저 기다리고 끈 뒤, 그사이 시작된 것이 있으면 다시 기다린다
    while pgrep -f 'reda poll' >/dev/null 2>&1; do echo '   돌고 있는 reda poll을 기다림...'; sleep 5; done
    sh "$REPO/scripts/local_poll_agent.sh" uninstall || true
    while pgrep -f 'reda poll' >/dev/null 2>&1; do echo '   돌고 있는 reda poll을 기다림...'; sleep 5; done
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

if [ -f "$DATA/state.db" ]; then
    echo '2) 상태 DB 복사본'
    python3 -I - "$DATA/state.db" "$TMP/state.db" <<'EOF'
import sqlite3, sys
# 읽기 전용으로 연다: 경로가 틀려도 빈 DB를 새로 만들어 보내지 않는다
src = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close()
print('   스키마', src.execute('PRAGMA user_version').fetchone()[0])
EOF
else
    echo '2) 상태 DB 없음: 서버에서 새 DB로 시작한다'
fi

cp "$TOKEN" "$TMP/token.json"
cp "$CREDS" "$TMP/credentials.json"   # 서버에서 토큰 갱신·reda calendar auth에 필요
# 운영 캘린더 토큰은 9단계 시험 때 복사본 폴더(~/.reda-stage9)에서 받았을 수 있다 (시연용은 자기 것만)
CAL_FALLBACK="$HOME/.reda-stage9/calendar_token.json"
[ "$MAIN_MODE" = 1 ] || CAL_FALLBACK="$CAL"
for c in "$CAL" "$CAL_FALLBACK"; do
    if [ -f "$c" ]; then cp "$c" "$TMP/calendar_token.json"; break; fi
done
[ -f "$TMP/calendar_token.json" ] || echo '   캘린더 토큰 없음: 서버에서 reda calendar auth가 필요 (일정 등록만 안 됨)'
cp "$ENV_FILE" "$TMP/reda.env"
cp "$HERMES_PROFILE_ENV" "$TMP/hermes.env"
chmod 600 "$TMP"/*

echo "3) 서버 준비 폴더(~/$STAGING)로 보내기"
$SSH "$TARGET" "umask 077; mkdir -p ~/$STAGING/secrets"
scp -q -i "$KEY" -o BatchMode=yes "$TMP"/* "$TARGET:$STAGING/secrets/"
$SSH "$TARGET" "chmod 700 ~/$STAGING ~/$STAGING/secrets; chmod 600 ~/$STAGING/secrets/*; ls ~/$STAGING/secrets"
if [ "$MAIN_MODE" = 1 ]; then
    echo '끝. 서버에서 server-setup.sh를 실행한다. Mac 자동 실행을 다시 켜려면: sh scripts/local_poll_agent.sh install'
else
    echo "끝. 서버에서: sudo sh ~/$STAGING/src/deploy/server-setup.sh ~/$STAGING"
fi
