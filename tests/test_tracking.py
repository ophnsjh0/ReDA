import pytest

from reda.gmail.history import Changes, LabelChange, SentMessage
from reda.state import StateDB
from reda.tracking import record_changes


@pytest.fixture
def state(tmp_path):
    with StateDB(tmp_path / 'state.db') as db:
        yield db


def test_own_labels_are_not_recorded_but_user_changes_are(state):
    state.record_classification('m1', 't1', '참고', source='rule')
    changes = Changes(history_id='200', label_changes=[
        LabelChange('m1', 't1', '참고', 'added', '150'),       # ReDA가 붙인 라벨
        LabelChange('m1', 't1', '참고', 'removed', '160'),     # 사용자가 뗌
        LabelChange('m1', 't1', '답장필요', 'added', '161'),   # 사용자가 바꿈
    ])
    result = record_changes(state, changes)
    assert result.label_events == 2
    assert [(e['label'], e['action']) for e in state.label_events()] == [
        ('참고', 'removed'), ('답장필요', 'added')]


def test_reply_to_reply_needed_thread_is_not_missed(state):
    state.record_classification('m1', 't1', '답장필요')
    result = record_changes(state, Changes(history_id='1', sent_messages=[SentMessage('s1', 't1')]))
    assert result.missed_replies == 0


def test_reply_to_fyi_or_unknown_thread_is_missed(state):
    state.record_classification('m2', 't2', '참고', source='rule')
    changes = Changes(history_id='1', sent_messages=[
        SentMessage('s2', 't2'),     # 참고로 분류했는데 사용자가 답장함
        SentMessage('s3', 't3'),     # ReDA가 처리한 적 없는 스레드
    ])
    result = record_changes(state, changes)
    assert result.missed_replies == 2
    assert {(r['thread_id'], r['prior_category']) for r in state.missed_replies()} == {
        ('t2', '참고'), ('t3', None)}


def test_same_changes_twice_are_recorded_once(state):
    changes = Changes(history_id='1',
                      label_changes=[LabelChange('m1', 't1', '확인필요', 'removed', '9')],
                      sent_messages=[SentMessage('s1', 't1')])
    record_changes(state, changes)
    again = record_changes(state, changes)
    assert (again.label_events, again.missed_replies) == (0, 0)


def test_agent_label_changes_are_not_counted_as_user_corrections(state):
    state.record_classification('m1', 't1', '답장필요')
    state.record_feedback('draft_hold', message_id='m1', detail={'thread_id': 't1', 'reason': '동료가 답함'})
    state.record_classification('m2', 't2', '답장필요')
    state.record_feedback('agent_label', message_id='m2', detail={'label': '참고'})
    changes = Changes(history_id='300', label_changes=[
        LabelChange('m1', 't1', '답장필요', 'removed', '201'),   # 에이전트 보류
        LabelChange('m1', 't1', '확인필요', 'added', '201'),
        LabelChange('m2', 't2', '답장필요', 'removed', '202'),   # 에이전트 set_label
        LabelChange('m2', 't2', '참고', 'added', '202'),
        LabelChange('m1', 't1', '확인필요', 'removed', '210'),   # 그 뒤 사용자가 뗌
    ])
    result = record_changes(state, changes)
    assert result.label_events == 1
    assert [(e['message_id'], e['label'], e['action']) for e in state.label_events()] == [
        ('m1', '확인필요', 'removed')]
