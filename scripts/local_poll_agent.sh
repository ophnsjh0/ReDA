#!/bin/sh
# 개발용 Mac에서 `reda poll`을 15분마다 돌린다 (2단계 1주일 시험 운영). 운영은 VPS의 Hermes cron이 맡는다.
#
#   sh scripts/local_poll_agent.sh install    등록하고 바로 한 번 실행
#   sh scripts/local_poll_agent.sh status     상태와 최근 로그
#   sh scripts/local_poll_agent.sh uninstall  끄고 지우기
#
# 로그: ~/Library/Logs/reda/poll.log. 메일함에 라벨을 붙인다 (발송 기능은 없다).
set -eu

LABEL=local.reda.poll
REPO=$(cd "$(dirname "$0")/.." && pwd)
UV=$(command -v uv || true)
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/reda"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
install)
    [ -n "$UV" ] || { echo "uv를 찾지 못함 (PATH 확인)"; exit 1; }
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string><string>-c</string>
        <string>echo "=== \$(date '+%Y-%m-%d %H:%M:%S')"; exec "$UV" run --project "$REPO" reda poll</string>
    </array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>StartInterval</key><integer>900</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$LOG_DIR/poll.log</string>
    <key>StandardErrorPath</key><string>$LOG_DIR/poll.log</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "등록함: 15분마다 reda poll. 로그 $LOG_DIR/poll.log"
    ;;
status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "state =|last exit code|run interval" || echo "등록되지 않음"
    [ -f "$LOG_DIR/poll.log" ] && tail -n 12 "$LOG_DIR/poll.log" || true
    ;;
uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "껐음 (로그는 $LOG_DIR에 남아 있음)"
    ;;
*)
    echo "사용법: sh $0 install | status | uninstall"; exit 2
    ;;
esac
