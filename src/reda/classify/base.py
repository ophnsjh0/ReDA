"""분류기 공통 타입. 제공자 모듈(llm, anthropic_llm, jev)은 이 모듈만 가져온다."""
from dataclasses import dataclass, field
from typing import Protocol

from reda.gmail.client import LABEL_CHECK, LABEL_FYI, LABEL_REPLY

CATEGORIES = (LABEL_REPLY, LABEL_FYI, LABEL_CHECK)
URGENCIES = ('높음', '보통', '낮음')


@dataclass(frozen=True)
class Classification:
    category: str
    urgency: str
    reason: str
    # 분류기가 확률을 주면 고른 분류의 확률(0~1). LLM 구현은 None
    confidence: float | None = None
    # 본문 인사말이 부르는 사람 (본문에 적힌 그대로, 없으면 ''). 나인지는 reda.identity가 판정한다
    addressee: str = ''
    # 이름은 못 뽑지만 "인사말이 특정한 한 사람을 부르는가"의 확률은 주는 분류기(Jev)용
    greets_specific: float | None = None
    # 긴급도 점수 0~3 (클수록 급함). 우선순위 계산에 쓴다 (reda.priority). 없으면 긴급도로 대신한다
    urgency_score: float | None = None
    # 답장과 따로 보는 할 일 표시: '일정' / '할 일' / ''. 참고 메일에도 붙는다 (9단계 일정 제안)
    todo: str = ''
    # 분류기가 준 판단 값(질문별 확률 등). 상태 DB에 JSON으로 남겨 기준값 조정에 쓴다. 본문은 넣지 않는다
    detail: dict | None = field(default=None, compare=False)


class Classifier(Protocol):
    name: str            # 상태 DB의 source에 남는다. 예: 'anthropic/claude-opus-5-5'

    def classify(self, mail) -> Classification: ...


class ClassifierUnavailable(Exception):
    """API 키·권한·모델 설정처럼 메일마다 다시 해도 소용없는 오류. 이번 실행을 멈춘다.

    실행을 멈추면 시작점(historyId)을 옮기지 않고 메일별 실패 횟수도 늘리지 않는다.
    """


class ClassifierBusy(ClassifierUnavailable):
    """사용량 제한·과부하·서버 오류·연결 실패처럼 모든 메일에 같이 걸리는 일시 장애.
    메일별 재시도 횟수를 쓰지 않도록 이번 실행을 멈추고 다음 실행에서 다시 한다."""


class ClassifierRequestRejected(Exception):
    """분류기가 요청을 거부함(HTTP 400/422). 이 메일 내용 탓인지 설정 탓인지 바로 알 수 없다.
    처리 흐름이 같은 실행의 다른 메일 결과를 보고 판단한다 (poll.py)."""
