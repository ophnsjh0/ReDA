#!/bin/sh
# 15분마다 `reda poll`: 분류·라벨 → (REDA_AUTO_DRAFT) 자동 초안 → 급한 메일·일정 → 출근 전 요약.
# 한 번 실패해도 다음 회차에 다시 한다. 잠금은 reda poll이 직접 건다.
set -u
INTERVAL="${REDA_POLL_INTERVAL:-900}"
while true; do
    started=$(date +%s)
    echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z')"
    reda poll || echo "poll 실패 (exit $?)"
    elapsed=$(( $(date +%s) - started ))
    if [ "$elapsed" -lt "$INTERVAL" ]; then
        sleep $(( INTERVAL - elapsed ))
    fi
done
