"""출근 전 요약 (4단계 1번): 모으기, 글 형식, 보낼 시각, 보내기 명령."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fakes import FakeGmail, make_message

from reda import summary
from reda.config import ConfigError, Settings
from reda.state import StateDB

ME = 'me@corp.example.com'


@pytest.fixture
def state(tmp_path):
    with StateDB(tmp_path / 'state.db') as db:
        yield db


def seed(state):
    state.record_classification('r1', 't-r1', '답장필요', urgency='높음', source='classifier:jev',
                                detail={'urgency_score': 2.9, 'todo': '일정'})
    state.record_classification('r2', 't-r2', '답장필요', urgency='보통', source='classifier:jev',
                                detail={'urgency_score': 1.0})
    state.record_classification('c1', 't-c1', '확인필요', urgency='보통', source='classifier:jev')
    state.record_classification('f1', 't-f1', '참고', source='rule')
    state.record_classification('f2', 't-f2', '참고', source='classifier:jev', detail={'todo': '할 일'})
    state.record_draft('d1', 'r2', 't-r2', [])


def gmail():
    msgs = {mid: make_message(mid, thread_id=f't-{mid}', From=f'이차장 <lee@cust.example.com>',
                              Subject=f'제목 {mid} 192.0.2.10')
            for mid in ('r1', 'r2', 'c1', 'f1', 'f2')}
    return FakeGmail(messages=msgs)


def test_summary_counts_order_and_masking(state):
    seed(state)
    collected = summary.collect(state, since='2000-01-01')
    summary.fill_headers(gmail(), collected)
    text = summary.render(collected, period='최근 24시간')
    assert text.startswith('📬 최근 24시간 메일 5통 확인')
    assert '· 답장필요 2건 — 초안 1건을 임시보관함에 넣어 두었어요' in text
    assert '· 확인필요 1건' in text and '· 참고 2건' in text
    reply_part = text.split('✉️ 답장필요', 1)[1].split('❓', 1)[0]
    assert reply_part.index('제목 r1') < reply_part.index('제목 r2')     # 급한 것이 먼저
    assert '🔴 제목 r1' in reply_part and '(초안 있음)' in reply_part
    assert '[일정] 제목 r1' in text and '[할 일] 제목 f2' in text
    assert '192.0.2.10' not in text and '#all/t-r1' in text


def test_empty_summary(state):
    assert '새로 분류한 메일이 없어요' in summary.render(summary.collect(state, since='2000-01-01'))


def test_since_uses_last_summary(state):
    now = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    since, period = summary.since_for(state, now=now)
    assert period == '최근 24시간' and since.startswith('2026-11-08T00:00')
    state.set_sync(summary.SYNC_KEY, '2026-11-08T23:00:00+00:00')
    assert summary.since_for(state, now=now) == ('2026-11-08T23:00:00+00:00', '지난 요약 이후')


def test_due_once_a_day_after_time(state):
    settings = SimpleNamespace(summary_at='08:20')
    local = datetime.now().astimezone()
    before = local.replace(hour=8, minute=0)
    after = local.replace(hour=8, minute=30)
    assert not summary.due(settings, state, now=before)
    assert summary.due(settings, state, now=after)
    state.set_sync(summary.SYNC_KEY, after.isoformat())
    assert not summary.due(settings, state, now=after + timedelta(minutes=15))
    assert not summary.due(SimpleNamespace(summary_at=''), state, now=after)


def test_deliver_uses_hermes_send_without_llm(monkeypatch):
    calls = {}

    def fake_run(command, **kwargs):
        calls['command'], calls['input'] = command, kwargs.get('input')
        return SimpleNamespace(returncode=0, stdout='{"success": true}', stderr='')
    monkeypatch.setattr(summary.subprocess, 'run', fake_run)
    settings = SimpleNamespace(hermes_bin='/bin/hermes', summary_profile='reda-dev', summary_to='telegram')
    summary.deliver(settings, '요약')
    assert calls['command'] == ['/bin/hermes', '-p', 'reda-dev', 'send', '--to', 'telegram',
                                '--json', '--file', '-']
    assert calls['input'] == '요약'
    monkeypatch.setattr(summary.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='no token'))
    with pytest.raises(RuntimeError, match='no token'):
        summary.deliver(settings, '요약')
    # --json의 오류 문구를 그대로 알린다 (예: 대화방을 찾지 못함)
    monkeypatch.setattr(summary.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=1, stdout='{"error": "Telegram send failed: Chat not found"}', stderr=''))
    with pytest.raises(RuntimeError, match='Chat not found'):
        summary.deliver(settings, '요약')


def test_summary_time_setting(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_SUMMARY_AT', '8:5')
    assert Settings.from_env().summary_at == '08:05'
    monkeypatch.setenv('REDA_SUMMARY_AT', '25:00')
    with pytest.raises(ConfigError, match='HH:MM'):
        Settings.from_env()


def test_urgent_alert_first_run_sets_baseline_only(state):
    seed(state)
    sent = []
    assert summary.send_alerts(gmail(), state, None, sender=lambda s, t: sent.append(t)) == 0
    assert sent == [] and state.get_sync(summary.ALERT_KEY)     # 예전 메일은 한꺼번에 알리지 않는다


def test_urgent_alert_only_new_high_reply_or_check(state):
    state.set_sync(summary.ALERT_KEY, '2000-01-01T00:00:00+00:00')
    seed(state)
    state.record_classification('c2', 't-c2', '확인필요', urgency='높음', source='classifier:jev')
    state.record_classification('f3', 't-f3', '참고', urgency='높음', source='classifier:jev')
    service = gmail()
    service.messages['c2'] = make_message('c2', thread_id='t-c2', From='박과장 <park@cust.example.com>',
                                          Subject='[긴급] 장애 확인 198.51.100.7')
    sent = []
    assert summary.send_alerts(service, state, None, sender=lambda s, t: sent.append(t)) == 2
    text = sent[0]
    assert text.startswith('🚨 급한 메일 2통')
    assert '[답장 필요] 제목 r1' in text and '[직접 확인 필요] [긴급] 장애 확인' in text
    assert '제목 r2' not in text and '제목 c1' not in text and 'f3' not in text   # 보통·참고는 요약에서
    assert '192.0.2.10' not in text and '198.51.100.7' not in text and '#all/t-c2' in text
    # 같은 메일을 다시 알리지 않는다
    assert summary.send_alerts(service, state, None, sender=lambda s, t: sent.append(t)) == 0
    assert len(sent) == 1


def test_urgent_alert_retries_after_send_failure(state):
    state.set_sync(summary.ALERT_KEY, '2000-01-01T00:00:00+00:00')
    seed(state)

    def broken(settings, text):
        raise RuntimeError('hermes send 실패')
    with pytest.raises(RuntimeError):
        summary.send_alerts(gmail(), state, None, sender=broken)
    assert state.get_sync(summary.ALERT_KEY) == '2000-01-01T00:00:00+00:00'   # 기준점을 옮기지 않음
    sent = []
    assert summary.send_alerts(gmail(), state, None, sender=lambda s, t: sent.append(t)) == 1


def test_urgent_alert_setting(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_ENV_FILE', str(tmp_path / 'none.env'))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    monkeypatch.delenv('REDA_URGENT_ALERT', raising=False)
    assert Settings.from_env().urgent_alert is False
    monkeypatch.setenv('REDA_URGENT_ALERT', 'true')
    assert Settings.from_env().urgent_alert is True
