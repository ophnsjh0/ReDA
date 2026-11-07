from fakes import FakeGmail, added

from reda import cli
from reda.gmail import client
from reda.state import StateDB


def _setup(monkeypatch, tmp_path, gmail):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_ENV_FILE', str(tmp_path / 'none.env'))  # 실제 .env를 읽지 않게
    monkeypatch.setattr(client, 'get_gmail_service', lambda settings, interactive=False: gmail)
    monkeypatch.setattr(client, 'get_message_metadata', lambda service, mid: {'payload': {
        'headers': [{'name': 'Subject', 'value': f'제목 {mid}'},
                    {'name': 'From', 'value': 'lee@customer-a.com'}]}})


def test_poll_dry_run_lists_new_mail_without_saving(monkeypatch, tmp_path, capsys):
    gmail = FakeGmail(history_pages=[{'history': [added('101', 'm1', 't1', ['INBOX'])],
                                      'historyId': '110'}])
    _setup(monkeypatch, tmp_path, gmail)
    with StateDB(tmp_path / 'state.db') as db:
        db.set_sync('history_id', '100')

    assert cli.main(['poll', '--dry-run']) == 0
    out = capsys.readouterr().out
    assert 'historyId 100 이후' in out
    assert '제목 m1' in out
    with StateDB(tmp_path / 'state.db') as db:
        assert db.history_id == '100'   # dry-run은 시작점을 옮기지 않는다


def test_poll_without_dry_run_is_not_ready(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeGmail())
    assert cli.main(['poll']) == 2
    assert '2단계' in capsys.readouterr().err
