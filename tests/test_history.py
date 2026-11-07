from fakes import FakeGmail, added, label_change

from reda.gmail import history

LABELS = {'Label_R': '답장필요', 'Label_F': '참고', 'Label_C': '확인필요', 'Label_X': '개인'}


def test_first_sync_uses_query_and_takes_history_id_first():
    gmail = FakeGmail(labels=LABELS, profile_history_id='900',
                      query_messages=[{'id': 'm1', 'threadId': 't1'}])
    changes = history.fetch_changes(gmail, None)
    assert changes.full_resync
    assert changes.history_id == '900'
    assert changes.new_messages == [{'id': 'm1', 'threadId': 't1'}]
    # 시작점을 쿼리보다 먼저 잡아야 그 사이에 온 메일을 놓치지 않는다
    assert [c[0] for c in gmail.calls] == ['getProfile', 'messages.list']


def test_expired_history_id_falls_back_to_query():
    gmail = FakeGmail(labels=LABELS, profile_history_id='2000', history_pages='expired',
                      query_messages=[{'id': 'm9', 'threadId': 't9'}])
    changes = history.fetch_changes(gmail, '5')
    assert changes.full_resync
    assert changes.history_id == '2000'
    assert changes.new_messages == [{'id': 'm9', 'threadId': 't9'}]


def test_incremental_sync_sorts_changes_and_follows_pages():
    pages = [
        {'history': [
            added('101', 'm1', 't1', ['INBOX', 'UNREAD']),
            added('102', 'd1', 't1', ['DRAFT']),                  # 초안은 무시
            added('103', 's1', 't2', ['SENT']),                   # 보낸 메일
        ], 'nextPageToken': '1', 'historyId': '103'},
        {'history': [
            added('104', 'm1', 't1', ['INBOX']),                  # 같은 메일 중복
            label_change('105', 'labelsAdded', 'm2', 't2', ['Label_R', 'Label_X']),
            label_change('106', 'labelsRemoved', 'm3', 't3', ['Label_F', 'STARRED']),
        ], 'historyId': '110'},
    ]
    gmail = FakeGmail(labels=LABELS, history_pages=pages)
    changes = history.fetch_changes(gmail, '100')

    assert not changes.full_resync
    assert changes.history_id == '110'
    assert changes.new_messages == [{'id': 'm1', 'threadId': 't1'}]
    assert [(s.message_id, s.thread_id) for s in changes.sent_messages] == [('s1', 't2')]
    assert [(c.message_id, c.label, c.action, c.history_id) for c in changes.label_changes] == [
        ('m2', '답장필요', 'added', '105'),
        ('m3', '참고', 'removed', '106'),
    ]
    assert [c for c in gmail.calls if c[0] == 'history.list'] == [
        ('history.list', '100', None), ('history.list', '100', '1')]


def test_no_changes_keeps_history_id_from_response():
    gmail = FakeGmail(labels=LABELS, history_pages=[{'historyId': '150'}])
    changes = history.fetch_changes(gmail, '100')
    assert changes.history_id == '150'
    assert not (changes.new_messages or changes.label_changes or changes.sent_messages)
