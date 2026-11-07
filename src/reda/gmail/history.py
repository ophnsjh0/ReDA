"""Gmail History API로 지난번 이후의 변경을 가져온다.

한 번의 동기화에서 세 가지를 뽑는다.
- 새로 받은 메일 (INBOX에 들어온 메일) → 분류 대상
- 분류 라벨이 붙거나 떨어진 기록 → 사용자가 라벨을 바꿨는지 판단 (측정용)
- 사용자가 보낸 메일 → 답장필요가 아니던 스레드에 직접 답장했는지 판단 (측정용)

historyId가 너무 오래돼 Gmail이 404를 돌려주면 검색 쿼리로 다시 동기화한다.
"""
from dataclasses import dataclass, field

from googleapiclient.errors import HttpError

from reda.gmail.client import CLASSIFICATION_LABELS, list_new_messages

HISTORY_TYPES = ('messageAdded', 'labelAdded', 'labelRemoved')


@dataclass
class LabelChange:
    message_id: str
    thread_id: str
    label: str              # 라벨 이름 (답장필요 / 참고 / 확인필요)
    action: str             # added / removed
    history_id: str


@dataclass
class SentMessage:
    message_id: str
    thread_id: str


@dataclass
class Changes:
    history_id: str                                  # 다음 동기화의 시작점
    new_messages: list = field(default_factory=list)  # [{'id':..., 'threadId':...}]
    label_changes: list = field(default_factory=list)
    sent_messages: list = field(default_factory=list)
    full_resync: bool = False                        # historyId가 없거나 만료돼 쿼리로 가져왔는지


def label_names_by_id(service):
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    return {label['id']: label['name'] for label in labels}


def current_history_id(service):
    return str(service.users().getProfile(userId='me').execute()['historyId'])


def fetch_changes(service, start_history_id, *, resync_query='in:inbox newer_than:2d',
                  max_results=50):
    """start_history_id 이후의 변경을 돌려준다. None이면 처음 동기화로 보고 쿼리로 가져온다."""
    if start_history_id:
        try:
            return _from_history(service, str(start_history_id))
        except HttpError as exc:
            if getattr(exc.resp, 'status', None) != 404:
                raise
    # 처음이거나 historyId가 만료됨: 시작점을 먼저 잡고 쿼리로 가져온다.
    # (시작점을 먼저 잡아야 그 사이에 온 메일을 다음 동기화에서 놓치지 않는다.)
    history_id = current_history_id(service)
    messages = list_new_messages(service, query=resync_query, max_results=max_results)
    return Changes(history_id=history_id, new_messages=messages, full_resync=True)


def _from_history(service, start_history_id):
    names = label_names_by_id(service)
    changes = Changes(history_id=start_history_id)
    seen_new = set()
    page_token = None
    while True:
        response = service.users().history().list(
            userId='me', startHistoryId=start_history_id, historyTypes=list(HISTORY_TYPES),
            pageToken=page_token).execute()
        for record in response.get('history', []):
            _collect(record, names, changes, seen_new)
        page_token = response.get('nextPageToken')
        if not page_token:
            changes.history_id = str(response.get('historyId', changes.history_id))
            return changes


def _collect(record, names, changes, seen_new):
    history_id = str(record.get('id', ''))
    for item in record.get('messagesAdded', []):
        msg = item['message']
        label_ids = set(msg.get('labelIds', []))
        if 'DRAFT' in label_ids:
            continue  # ReDA나 사용자가 만든 초안
        if 'SENT' in label_ids:
            changes.sent_messages.append(SentMessage(msg['id'], msg['threadId']))
        elif 'INBOX' in label_ids and msg['id'] not in seen_new:
            seen_new.add(msg['id'])
            changes.new_messages.append({'id': msg['id'], 'threadId': msg['threadId']})
    for key, action in (('labelsAdded', 'added'), ('labelsRemoved', 'removed')):
        for item in record.get(key, []):
            msg = item['message']
            for label_id in item.get('labelIds', []):
                name = names.get(label_id)
                if name in CLASSIFICATION_LABELS:
                    changes.label_changes.append(
                        LabelChange(msg['id'], msg['threadId'], name, action, history_id))
