"""테스트용 가짜 Gmail 서비스. service.users().X().Y(**kw).execute() 호출 모양만 흉내 낸다."""
import httplib2
from googleapiclient.errors import HttpError


class _Request:
    def __init__(self, fn, kwargs):
        self._fn, self._kwargs = fn, kwargs

    def execute(self):
        return self._fn(**self._kwargs)


class _Resource:
    def __init__(self, methods):
        self._methods = methods

    def __getattr__(self, name):
        try:
            fn = self._methods[name]
        except KeyError:
            raise AttributeError(f'가짜 Gmail에 없는 메서드: {name}') from None
        if isinstance(fn, _Resource):          # users().labels() 같은 하위 리소스
            return lambda: fn
        return lambda **kwargs: _Request(fn, kwargs)


class FakeGmail:
    """
    labels: {'Label_1': '답장필요', ...}
    history_pages: history.list가 차례로 돌려줄 응답 목록. 'expired'면 404를 낸다.
    query_messages: messages.list가 돌려줄 메일 목록
    """

    def __init__(self, *, labels=None, profile_history_id='1000', history_pages=None,
                 query_messages=None):
        self.labels = labels or {}
        self.profile_history_id = profile_history_id
        self.history_pages = history_pages if history_pages is not None else []
        self.query_messages = query_messages or []
        self.calls = []

    def users(self):
        return _Resource({
            'getProfile': self._get_profile,
            'labels': _Resource({'list': self._labels_list}),
            'history': _Resource({'list': self._history_list}),
            'messages': _Resource({'list': self._messages_list}),
        })

    def _get_profile(self, userId):
        self.calls.append(('getProfile',))
        return {'emailAddress': 'me@corp.com', 'historyId': self.profile_history_id}

    def _labels_list(self, userId):
        return {'labels': [{'id': k, 'name': v} for k, v in self.labels.items()]}

    def _history_list(self, userId, startHistoryId, historyTypes=None, pageToken=None):
        self.calls.append(('history.list', startHistoryId, pageToken))
        if self.history_pages == 'expired':
            raise HttpError(httplib2.Response({'status': 404}), b'{"error": "notFound"}')
        index = int(pageToken) if pageToken else 0
        return self.history_pages[index]

    def _messages_list(self, userId, q, maxResults):
        self.calls.append(('messages.list', q))
        return {'messages': self.query_messages}


def added(history_id, message_id, thread_id, label_ids):
    return {'id': history_id, 'messagesAdded': [
        {'message': {'id': message_id, 'threadId': thread_id, 'labelIds': label_ids}}]}


def label_change(history_id, key, message_id, thread_id, label_ids):
    return {'id': history_id, key: [
        {'message': {'id': message_id, 'threadId': thread_id}, 'labelIds': label_ids}]}
