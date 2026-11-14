#!/bin/sh
# VPS에서 ReDA 컨테이너를 만들고 띄운다 (4단계 5~6번). 관리 계정으로 sudo 실행:
#   sudo sh ~/reda-staging/src/deploy/server-setup.sh ~/reda-staging
# 준비 폴더(<staging>)에 있어야 하는 것:
#   compose.env            REDA_ROOT, REDA_UID, REDA_GID, HERMES_IMAGE(이미지 ID), REDA_AGENT_PROVIDER, REDA_AGENT_MODEL
#   src/                   ReDA 소스 (git archive)
#   secrets/               mac-handoff.sh가 보낸 token.json, calendar_token.json, state.db, reda.env, hermes.env
#   secrets/agent.env      (선택) 초안 모델 API 키 한 줄 (예: ANTHROPIC_API_KEY=...). hermes.env 뒤에 붙인다
# 다시 실행해도 된다: 이미지만 새로 만들고, 데이터 폴더에 비밀 파일이 이미 있으면 준비 폴더 것으로 바꾼다.
set -eu
[ "$(id -u)" = 0 ] || { echo 'sudo로 실행한다' >&2; exit 1; }
STAGING=$(cd "${1:?준비 폴더 경로}" && pwd)
. "$STAGING/compose.env"
: "${REDA_ROOT:?}" "${REDA_UID:?}" "${REDA_GID:?}" "${HERMES_IMAGE:?}" "${REDA_AGENT_PROVIDER:?}" "${REDA_AGENT_MODEL:?}"
SRC="$STAGING/src"
SECRETS="$STAGING/secrets"
VERSION=$(cat "$SRC/deploy/VERSION" 2>/dev/null || date +%Y%m%d%H%M)
IMAGE="reda:$VERSION"

# 기반 이미지는 ID로 고정한다 (원래 태그가 latest라 언제든 바뀔 수 있다)
BASE="reda-base:hermes-$(echo "$HERMES_IMAGE" | sed 's/^sha256://' | cut -c1-12)"
docker tag "$HERMES_IMAGE" "$BASE"
echo "1) 이미지 $IMAGE (기반 $BASE)"
docker build --progress=plain -f "$SRC/deploy/Dockerfile" --build-arg "HERMES_IMAGE=$BASE" -t "$IMAGE" "$SRC"

echo '2) 데이터 폴더'
install -d -m 700 -o "$REDA_UID" -g "$REDA_GID" "$REDA_ROOT" "$REDA_ROOT/hermes" "$REDA_ROOT/data" \
    "$REDA_ROOT/hermes/profiles" "$REDA_ROOT/hermes/profiles/reda"
PROFILE="$REDA_ROOT/hermes/profiles/reda"
{
    printf 'model:\n  default: %s\n  provider: %s\n' "$REDA_AGENT_MODEL" "$REDA_AGENT_PROVIDER"
    if [ "$REDA_AGENT_PROVIDER" = openai-codex ]; then
        printf '  base_url: https://chatgpt.com/backend-api/codex\n  api_mode: codex_responses\n'
    fi
    cat "$SRC/deploy/hermes-profile/config.yaml"
} > "$PROFILE/config.yaml"
cp "$SRC/distribution/SOUL.md" "$PROFILE/SOUL.md"
printf "description: 'ReDA 운영: 답장필요 메일의 초안을 reda-mcp 도구로만 쓴다. 메일 발송 불가.'\ndescription_auto: false\n" \
    > "$PROFILE/profile.yaml"

if [ -d "$SECRETS" ] && [ -n "$(ls -A "$SECRETS" 2>/dev/null)" ]; then
    echo '3) 비밀 파일 옮기기 (서비스 계정 소유, 600)'
    for f in token.json calendar_token.json state.db reda.env; do
        [ -f "$SECRETS/$f" ] && install -m 600 -o "$REDA_UID" -g "$REDA_GID" "$SECRETS/$f" "$REDA_ROOT/data/$f"
    done
    if [ -f "$SECRETS/hermes.env" ]; then
        cat "$SECRETS/hermes.env" > "$PROFILE/.env"
        [ -f "$SECRETS/agent.env" ] && { echo; cat "$SECRETS/agent.env"; } >> "$PROFILE/.env"
        # 초안 모델 키가 ReDA .env에 있으면 Hermes 프로필 .env로도 옮긴다 (화면에 출력하지 않는다)
        if [ -f "$SECRETS/reda.env" ]; then
            grep -E '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|OPENROUTER_API_KEY|GEMINI_API_KEY)=' "$SECRETS/reda.env" \
                >> "$PROFILE/.env" || true
        fi
    fi
    # 준비 폴더의 비밀 파일은 지운다 (관리 계정 홈에 남기지 않는다)
    find "$SECRETS" -type f -exec shred -u {} \; 2>/dev/null || rm -f "$SECRETS"/*
else
    echo '3) 준비 폴더에 비밀 파일 없음: 데이터 폴더의 것을 그대로 쓴다'
fi
chown -R "$REDA_UID:$REDA_GID" "$PROFILE"
chmod 600 "$PROFILE"/.env 2>/dev/null || true
chmod 600 "$PROFILE/config.yaml"

echo '4) compose 설정'
install -m 600 -o root -g root /dev/null "$REDA_ROOT.compose.env"
{ grep -v '^REDA_IMAGE=' "$STAGING/compose.env"; echo "REDA_IMAGE=$IMAGE"; } > "$REDA_ROOT.compose.env"
mkdir -p /opt/reda-deploy && cp "$SRC/deploy/compose.yml" /opt/reda-deploy/compose.yml

COMPOSE="docker compose -p reda --env-file $REDA_ROOT.compose.env -f /opt/reda-deploy/compose.yml"
if [ "$REDA_AGENT_PROVIDER" = openai-codex ] && [ ! -s "$PROFILE/auth.json" ]; then
    echo '5) 구독 로그인 (처음 한 번): 아래에 나오는 주소를 브라우저로 열고 코드를 넣는다'
    $COMPOSE run --rm --no-deps --entrypoint hermes poll -p reda auth add openai-codex
fi

echo '6) 시작'
docker compose -p reda --env-file "$REDA_ROOT.compose.env" -f /opt/reda-deploy/compose.yml up -d
docker compose -p reda --env-file "$REDA_ROOT.compose.env" -f /opt/reda-deploy/compose.yml ps
echo "로그: sudo docker logs -f reda-poll   /   sudo docker logs -f reda-schedule-bot"
