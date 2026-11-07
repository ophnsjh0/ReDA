"""분류 정확도 측정 기록.

History API에서 가져온 변경(Changes)을 상태 DB에 남긴다.
- 사용자가 분류 라벨을 붙이거나 뗐으면 label_events에 남긴다. ReDA가 붙인 라벨은 남기지 않는다.
- 사용자가 보낸 메일의 스레드가 답장필요로 분류된 적이 없으면 missed_replies에 남긴다.
"""
from dataclasses import dataclass

from reda.gmail.client import LABEL_REPLY


@dataclass
class TrackingResult:
    label_events: int = 0
    missed_replies: int = 0


def is_own_label(state, change):
    """ReDA가 붙인 라벨인가. ReDA는 라벨을 떼지 않고, 붙일 때는 상태 DB에 같은 분류를 남긴다."""
    if change.action != 'added':
        return False
    row = state.get_message(change.message_id)
    return row is not None and row['category'] == change.label


def record_changes(state, changes):
    result = TrackingResult()
    for change in changes.label_changes:
        if is_own_label(state, change):
            continue
        if state.record_label_event(change.message_id, change.thread_id, change.label,
                                    change.action, change.history_id):
            result.label_events += 1
    for sent in changes.sent_messages:
        categories = state.thread_categories(sent.thread_id)
        if LABEL_REPLY in categories:
            continue
        prior = categories[-1] if categories else None
        if state.record_missed_reply(sent.message_id, sent.thread_id, prior):
            result.missed_replies += 1
    return result
