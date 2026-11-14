#!/bin/sh
# 개발용 Mac → VPS로 운영을 넘긴다 (4단계 5번, 6단계 전까지 쓰는 임시 방법).
#   sh deploy/mac-handoff.sh <관리계정@서버> [SSH 키]
# 하는 일:
#   1. Mac의 15분 자동 실행(local.reda.poll)을 끄고, 돌고 있는 reda poll이 끝나기를 기다린다
#      (두 곳에서 같은 메일함을 처리하면 초안이 두 번 생긴다)
#   2. 상태 DB를 일관된 복사본으로 뜬다 (sqlite backup)
#   3. 비밀 파일을 서버의 준비 폴더(~/reda-staging/secrets, 권한 700/600)로 보낸다
#      Gmail 토큰, 캘린더 토큰(있으면), 상태 DB, ReDA .env, Hermes 프로필 .env(텔레그램)
# 파일 내용은 화면에 출력하지 않는다. 서버에서는 server-setup.sh가 서비스 계정 소유로 옮기고 준비 폴더를 지운다.
set -eu
TARGET="${1:?사용법: sh deploy/mac-handoff.sh <관리계정@서버> [SSH 키]}"
KEY="${2:-$HOME/.ssh/id_ed25519}"
REPO=$(cd "$(dirname "$0")/.." && pwd)
# .env와 자동 실행 스크립트는 운영 중인 작업 폴더(git의 첫 worktree)에 있다
MAIN=$(git -C "$REPO" worktree list --porcelain | sed -n '1s/^worktree //p')
REPO="${MAIN:-$REPO}"
DATA="${REDA_DATA_DIR:-$HOME/.reda}"
HERMES_PROFILE_ENV="${REDA_HERMES_PROFILE_ENV:-$HOME/.hermes/profiles/reda-dev/.env}"
SSH="ssh -i $KEY -o BatchMode=yes"

echo '1) Mac 자동 실행 끄기'
sh "$REPO/scripts/local_poll_agent.sh" uninstall || true
while pgrep -f 'reda poll' >/dev/null 2>&1; do echo '   돌고 있는 reda poll을 기다림...'; sleep 5; done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

echo '2) 상태 DB 복사본'
python3 -I - "$DATA/state.db" "$TMP/state.db" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close()
print('   스키마', src.execute('PRAGMA user_version').fetchone()[0])
EOF

cp "$DATA/token.json" "$TMP/token.json"
# 캘린더 토큰은 9단계 시험 때 복사본 폴더(~/.reda-stage9)에서 받았을 수 있다
for c in "$DATA/calendar_token.json" "$HOME/.reda-stage9/calendar_token.json"; do
    if [ -f "$c" ]; then cp "$c" "$TMP/calendar_token.json"; break; fi
done
[ -f "$TMP/calendar_token.json" ] || echo '   캘린더 토큰 없음: 서버에서 reda calendar auth가 필요 (일정 등록만 안 됨)'
cp "$REPO/.env" "$TMP/reda.env"
cp "$HERMES_PROFILE_ENV" "$TMP/hermes.env"
chmod 600 "$TMP"/*

echo '3) 서버 준비 폴더로 보내기'
$SSH "$TARGET" 'umask 077; mkdir -p ~/reda-staging/secrets'
scp -q -i "$KEY" -o BatchMode=yes "$TMP"/* "$TARGET:reda-staging/secrets/"
$SSH "$TARGET" 'chmod 700 ~/reda-staging ~/reda-staging/secrets; chmod 600 ~/reda-staging/secrets/*; ls ~/reda-staging/secrets'
echo '끝. 서버에서 server-setup.sh를 실행한다. Mac 자동 실행을 다시 켜려면: sh scripts/local_poll_agent.sh install'
