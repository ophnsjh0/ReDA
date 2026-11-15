#!/bin/sh
# 일정 버튼 봇 (9단계): [캘린더에 등록] [시간 수정] [무시]를 코드가 처리한다 (LLM 없음).
# 같은 봇 토큰으로 두 곳에서 돌리면 버튼 응답을 나눠 받으므로, 이 컨테이너에서만 돌린다.
set -u
while true; do
    reda schedule-bot || echo "일정 봇 종료 (exit $?), 30초 뒤 다시 시작"
    sleep 30
done
