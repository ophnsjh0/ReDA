"""분류 정확도 측정 기록.

History API에서 가져온 변경(Changes)을 상태 DB에 남긴다.
- 사용자가 분류 라벨을 붙이거나 뗐으면 label_events에 남긴다. ReDA가 붙인 라벨과,
  초안 에이전트가 보류(hold_draft)나 set_label로 바꾼 라벨은 남기지 않는다.
- 사용자가 보낸 메일의 스레드가 답장필요로 분류된 적이 없으면 missed_replies에 남긴다.
"""
import json
from dataclasses import dataclass

from reda.gmail.client import CLASSIFICATION_LABELS, LABEL_CHECK, LABEL_REPLY

AGENT_FEEDBACK = ('draft_hold', 'agent_label')


@dataclass
class TrackingResult:
    label_events: int = 0
    missed_replies: int = 0


def agent_changes(state, message_id):
    """초안 에이전트가 이 메일에서 바꾼 라벨 {(action, label)}. 에이전트는 피드백 기록을 남긴다 (mcp_server)."""
    changes = set()
    for row in state.feedback_for(message_id, AGENT_FEEDBACK):
        if row['kind'] == 'draft_hold':
            changes |= {('added', LABEL_CHECK), ('removed', LABEL_REPLY)}
        else:
            label = json.loads(row['detail']).get('label')
            changes.add(('added', label))
            changes |= {('removed', other) for other in CLASSIFICATION_LABELS if other != label}
    return changes


def is_own_label(state, change):
    """ReDA가 바꾼 라벨인가. 분류할 때는 상태 DB에 같은 분류를 남기고, 에이전트가 바꿀 때는 피드백을 남긴다.
    에이전트가 바꾼 뒤 사용자가 같은 라벨을 다시 붙였다 떼는 드문 경우는 구분하지 못한다."""
    if (change.action, change.label) in agent_changes(state, change.message_id):
        return True
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
