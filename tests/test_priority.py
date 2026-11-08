"""우선순위: 긴급도와 나눠 코드가 정한다."""
from reda.classify import Classification
from reda.priority import priority


def test_priority_adds_customer_and_waiting():
    c = Classification('답장필요', '보통', 'x', urgency_score=1.0)
    assert priority(c) == 1.0
    assert priority(c, customer=True) == 1.5
    assert priority(c, waiting_hours=48) == 2.0               # 하루 0.5, 최대 1.0
    assert priority(c, waiting_hours=24 * 10) == 2.0


def test_priority_without_score_uses_urgency_and_fyi_is_zero():
    assert priority(Classification('답장필요', '높음', 'x')) == 3.0
    assert priority(Classification('확인필요', '높음', 'x')) == 2.4
    assert priority(Classification('참고', '높음', 'x')) == 0.0
