"""우선순위: 긴급도와 나눈다 (구현계획 "Jev 분류 설계").

- 긴급도: 분류기가 메일 내용만 보고 정한다 (Jev urgency 점수 0~3, LLM은 높음/보통/낮음).
- 우선순위: 코드가 긴급도에 코드가 아는 사실을 더해 정한다. 출근 전 요약(4단계)의 정렬 순서다.
  가중치는 여기서만 고친다. 기다린 시간은 요약을 만들 때 다시 계산한다.
"""
from reda.gmail.client import LABEL_CHECK, LABEL_REPLY

URGENCY_FALLBACK = {'높음': 3.0, '보통': 1.5, '낮음': 0.0}   # 점수를 주지 않는 분류기용
CUSTOMER_BONUS = 0.5          # 등록한 고객사(REDA_CUSTOMER_DOMAINS)가 보낸 메일
WAIT_BONUS_PER_DAY = 0.5      # 답하지 않고 기다린 하루마다
WAIT_BONUS_MAX = 1.0
CHECK_FACTOR = 0.8            # 확인필요는 답장필요보다 조금 뒤에


def urgency_score(classification):
    if classification.urgency_score is not None:
        return float(classification.urgency_score)
    return URGENCY_FALLBACK.get(classification.urgency, 1.5)


def priority(classification, *, customer=False, waiting_hours=0.0):
    """클수록 먼저 본다. 답장필요·확인필요가 아니면 0."""
    if classification.category not in (LABEL_REPLY, LABEL_CHECK):
        return 0.0
    score = urgency_score(classification)
    if customer:
        score += CUSTOMER_BONUS
    score += min(WAIT_BONUS_MAX, WAIT_BONUS_PER_DAY * max(0.0, waiting_hours) / 24)
    if classification.category == LABEL_CHECK:
        score *= CHECK_FACTOR
    return round(score, 3)
