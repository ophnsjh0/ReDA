"""일정 제안 흐름 (9단계): 후보 고르기, 제안 저장, 버튼 봇, 캘린더 일정 내용."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fakes import make_message

from reda import gcalendar, schedule
from reda.schedule import KST, find
from reda.schedule_bot import ScheduleBot, buttons, proposal_text
from reda.state import StateDB

SUN_MS = int(datetime(2026, 11, 8, 19, 0, tzinfo=KST).timestamp() * 1000)
CHAT = '12345'
SETTINGS = SimpleNamespace(jev_min_probability=0.7)
BODY = ('신정훈 부장님, 안녕하세요.\n'
        '11월 12일(목) 오후 2시부터 4시까지 본사 전산실에서 L4 스위치 정기점검을 진행하려고 합니다.\n'
        '지난 11월 2일 점검 결과도 첨부합니다.\n'
        '다음 주쯤 교육 일정도 잡겠습니다.\n감사합니다.')


@pytest.fixture
def state(tmp_path):
    with StateDB(tmp_path / 'state.db') as db:
        yield db


def always(p):
    return lambda mail: (lambda candidates: {cid: p for cid in candidates})


def test_pick_skips_past_and_low_probability():
    found = find(BODY, datetime(2026, 11, 8, 19, 0, tzinfo=KST))
    asked = {}

    def ask(candidates):
        asked.update(candidates)
        return {cid: (0.9 if item.clear else 0.4) for cid, item in candidates.items()}
    picked = schedule.pick(found, ask)
    assert all(not item.past for item in asked.values())         # 지난 날짜는 묻지도 않는다
    assert [p.item.describe() for p in picked] == ['11/12(목) 14:00~16:00']


def test_title_from_subject():
    assert schedule.title_from('Re: [외부메일] Fwd: 다음 주 정기점검 일정 안내') == '다음 주 정기점검 일정 안내'


def seed_mail(state, mid='m1', todo='일정', body=BODY):
    state.record_classification(mid, f't-{mid}', '답장필요', urgency='보통', source='classifier:jev',
                                detail={'todo': todo})
    return make_message(mid, thread_id=f't-{mid}', internal_date=SUN_MS, body=body,
                        From='홍과장 <hong@cust.example.com>', Subject='Re: 다음 주 정기점검 일정 안내')


def test_scan_first_run_sets_baseline_then_saves_once(state):
    messages = {}
    get = messages.__getitem__
    assert schedule.scan(None, state, SETTINGS, always(0.9), get_message=get) == []   # 기준점만
    state.set_sync(schedule.SCAN_KEY, '2000-01-01T00:00:00+00:00')
    messages['m1'] = seed_mail(state)
    messages['m2'] = seed_mail(state, 'm2', todo='')                                  # 일정 표시 없음
    created = schedule.scan(None, state, SETTINGS, always(0.9), get_message=get)
    rows = [state.proposal(pid) for pid in created]
    clear = [r for r in rows if r['start_at']]
    assert len(clear) == 1 and clear[0]['start_at'] == '2026-11-12T14:00+09:00'
    assert clear[0]['end_at'] == '2026-11-12T16:00+09:00' and clear[0]['title'] == '다음 주 정기점검 일정 안내'
    vague = [r for r in rows if not r['start_at']]
    assert len(vague) == 1 and vague[0]['note'] == '날짜가 정해지지 않음'
    assert all(r['message_id'] == 'm1' and r['status'] == 'new' for r in rows)
    # 같은 메일을 다시 봐도 두 번 제안하지 않는다
    state.set_sync(schedule.SCAN_KEY, '2000-01-01T00:00:00+00:00')
    assert schedule.scan(None, state, SETTINGS, always(0.9), get_message=get) == []


class FakeTelegram:
    def __init__(self):
        self.calls, self._id = [], 100

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'sendMessage':
            self._id += 1
            return {'message_id': self._id}
        return True

    def sent(self, method='sendMessage'):
        return [p for m, p in self.calls if m == method]


def proposal(state, start_at='2026-11-12T14:00+09:00', end_at='2026-11-12T16:00+09:00', sentence='s1'):
    return state.add_proposal('m1', 't-m1', sentence=sentence, title='L4 정기점검 192.0.2.15',
                              start_at=start_at, end_at=end_at, all_day=False, note='', status='new')


def press(action, pid, chat=CHAT, user=CHAT):
    return {'callback_query': {'id': 'q1', 'data': f'{action}:{pid}', 'from': {'id': int(user)},
                               'message': {'chat': {'id': int(chat)}}}}


def make_bot(state, created=None):
    tg = FakeTelegram()
    created = created if created is not None else []

    def create_event(p):
        created.append(p['id'])
        return 'ev1', 'https://calendar.google.com/event?eid=x'
    return ScheduleBot(tg, state, CHAT, create_event,
                       now=lambda: datetime(2026, 11, 9, 9, 0, tzinfo=KST)), tg, created


def test_bot_sends_buttons_and_registers_only_on_press(state):
    pid = proposal(state)
    bot, tg, created = make_bot(state)
    assert bot.send_new() == 1 and bot.send_new() == 0                      # 한 번만 보낸다
    sent = tg.sent()[0]
    assert sent['chat_id'] == CHAT and '11/12(목) 14:00~16:00' in sent['text']
    assert '192.0.2.15' not in sent['text']                                # 메신저로 나가는 글은 가린다
    labels = [b['text'] for row in sent['reply_markup']['inline_keyboard'] for b in row]
    assert labels == ['캘린더에 등록', '시간 수정', '무시']
    assert created == [] and state.proposal(pid)['status'] == 'proposed'     # 누르기 전에는 등록 없음
    assert bot.handle(press('add', pid)) == 'added'
    assert created == [pid] and state.proposal(pid)['event_id'] == 'ev1'
    assert bot.handle(press('add', pid)) == 'ignored: done'                  # 두 번 눌러도 한 번
    assert created == [pid]


def test_bot_ignores_other_people_and_unknown_buttons(state):
    pid = proposal(state)
    bot, tg, created = make_bot(state)
    bot.send_new()
    assert bot.handle(press('add', pid, chat='999', user='999')) == 'ignored: other chat'
    assert bot.handle(press('add', pid, user='999')) == 'ignored: other chat'
    assert bot.handle(press('add', 9999)) == 'ignored: done'
    assert bot.handle(press('drop_table', pid)) == 'ignored: unknown'
    assert created == []


def test_bot_ignore_and_edit_time(state):
    a, b = proposal(state, sentence='a'), proposal(state, sentence='b')
    bot, tg, created = make_bot(state)
    bot.send_new()
    assert bot.handle(press('ignore', a)) == 'ignored'
    assert state.proposal(a)['status'] == 'ignored'
    assert bot.handle(press('edit', b)) == 'editing'
    message = {'message': {'chat': {'id': int(CHAT)}, 'text': '11/12 15:00-17:00'}}
    assert bot.handle(message) == 'edited'
    row = state.proposal(b)
    assert row['start_at'] == '2026-11-12T15:00+09:00' and row['end_at'] == '2026-11-12T17:00+09:00'
    assert row['status'] == 'proposed' and '15:00~17:00' in tg.sent()[-1]['text']   # 새 버튼을 다시 보냄
    assert created == []


def test_bot_asks_for_date_when_unclear(state):
    pid = proposal(state, start_at=None, end_at=None)
    bot, tg, created = make_bot(state)
    bot.send_new()
    sent = tg.sent()[0]
    assert sent['text'].startswith('📅 일정 날짜를 알려 주세요')
    labels = [b['text'] for row in sent['reply_markup']['inline_keyboard'] for b in row]
    assert '캘린더에 등록' not in labels and state.proposal(pid)['status'] == 'asked'
    assert bot.handle(press('add', pid)) == 'ignored: no date' and created == []


def test_calendar_event_body(state):
    pid = proposal(state)
    row = state.proposal(pid)
    body = gcalendar.event_body(row)
    assert body['start'] == {'dateTime': '2026-11-12T14:00:00+09:00'}   # 초까지 (RFC 3339)
    assert body['reminders']['overrides'] == [{'method': 'popup', 'minutes': 1440},
                                              {'method': 'popup', 'minutes': 30}]
    assert body['id'].startswith('reda') and set(body['id']) <= set('abcdefghijklmnopqrstuv0123456789')
    assert 'mail.google.com' in body['description']
    allday = state.proposal(state.add_proposal('m1', 't-m1', sentence='c', title='t',
                                               start_at='2026-11-13', end_at='2026-11-14', all_day=True,
                                               note='', status='new'))
    assert gcalendar.event_body(allday)['start'] == {'date': '2026-11-13'}


def test_calendar_create_is_idempotent(state):
    from googleapiclient.errors import HttpError
    pid = proposal(state)
    row = state.proposal(pid)

    class Events:
        def __init__(self):
            self.inserted = 0

        def insert(self, calendarId, body):
            self.inserted += 1
            if self.inserted > 1:
                raise HttpError(SimpleNamespace(status=409, reason='duplicate'), b'{}')
            return SimpleNamespace(execute=lambda: {'id': body['id'], 'htmlLink': 'link'})

        def get(self, calendarId, eventId):
            return SimpleNamespace(execute=lambda: {'id': eventId, 'htmlLink': 'link'})
    events = Events()
    service = SimpleNamespace(events=lambda: events)
    first = gcalendar.create_event(service, row)
    assert gcalendar.create_event(service, row) == first
    with pytest.raises(ValueError):
        gcalendar.create_event(service, {**dict(row), 'start_at': None})


def test_proposal_text_and_buttons_shape(state):
    row = state.proposal(proposal(state))
    assert 'mail.google.com' in proposal_text(row)
    assert buttons(row)['inline_keyboard'][0][0]['callback_data'] == f"add:{row['id']}"


def test_schedule_bot_replaces_schedule_alert(state):
    from reda import summary
    state.set_sync(summary.ALERT_KEY, '2000-01-01T00:00:00+00:00')
    seed_mail(state)
    sent = []
    with_bot = SimpleNamespace(urgent_alert=True, schedule_alert=True, schedule_bot_token='x',
                               schedule_chat_id='1', classifier='jev')
    assert summary.send_alerts(None, state, with_bot, sender=lambda s, t: sent.append(t)) == 0
    assert sent == []


def test_schedule_alert_stays_when_bot_cannot_propose(state, monkeypatch):
    # 봇 토큰만 있고 대화방이 없거나 분류기가 Jev가 아니면 제안이 생기지 않으니 📅 알림을 그대로 보낸다
    from reda import summary
    monkeypatch.setattr(summary, 'fill_items', lambda service, items: None)
    for half_set in (dict(schedule_chat_id='', classifier='jev'), dict(schedule_chat_id='1', classifier='anthropic')):
        state.set_sync(summary.ALERT_KEY, '2000-01-01T00:00:00+00:00')
        state.conn.execute('DELETE FROM messages')
        seed_mail(state)
        sent = []
        settings = SimpleNamespace(urgent_alert=True, schedule_alert=True, schedule_bot_token='x', **half_set)
        assert summary.send_alerts(None, state, settings, sender=lambda s, t: sent.append(t)) == 1
        assert '📅' in sent[0]


def test_stale_button_answer_failure_still_registers(state):
    from reda.schedule_bot import TelegramError
    pid = proposal(state)
    bot, tg, created = make_bot(state)
    bot.send_new()
    real_call = tg.call

    def call(method, **params):
        if method == 'answerCallbackQuery':
            raise TelegramError('query is too old')           # 봇 재시작 뒤 받은 오래된 버튼
        return real_call(method, **params)
    tg.call = call
    assert bot.handle(press('add', pid)) == 'added' and created == [pid]


def test_one_bad_update_does_not_stop_the_bot(state):
    bot, tg, _ = make_bot(state)
    tg.call = lambda method, **p: [{'update_id': 1, 'callback_query': {'data': 'add:1'}},   # id·chat 없음
                                   {'update_id': 2, 'message': {'chat': {'id': int(CHAT)}, 'text': '목록'}}] \
        if method == 'getUpdates' else {'message_id': 1}
    bot.handle = lambda update: (_ for _ in ()).throw(ValueError('bad')) if update['update_id'] == 1 else 'ok'
    assert bot.poll_once() == ['error: ValueError', 'ok']


def test_scan_skips_a_mail_that_fails_and_continues(state):
    good = seed_mail(state, 'm2')
    seed_mail(state, 'm1')
    state.conn.execute("UPDATE messages SET labeled_at = '2026-11-08T00:00:00+00:00' WHERE message_id = 'm1'")
    state.conn.execute("UPDATE messages SET labeled_at = '2026-11-08T00:00:01+00:00' WHERE message_id = 'm2'")
    state.set_sync(schedule.SCAN_KEY, '2000-01-01T00:00:00+00:00')

    def get(mid):
        if mid == 'm1':
            raise LookupError('지워진 메일')
        return good
    created = schedule.scan(None, state, SETTINGS, always(0.9), get_message=get)
    assert created and all(state.proposal(p)['message_id'] == 'm2' for p in created)
