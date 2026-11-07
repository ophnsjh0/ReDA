from fakes import FakeGmail, added, make_message

from reda import cli
from reda.gmail import client
from reda.state import StateDB


def _setup(monkeypatch, tmp_path, gmail):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_ENV_FILE', str(tmp_path / 'none.env'))  # 실제 .env를 읽지 않게
    monkeypatch.setattr(client, 'get_gmail_service', lambda settings, interactive=False: gmail)


def test_poll_dry_run_lists_new_mail_without_saving(monkeypatch, tmp_path, capsys):
    gmail = FakeGmail(
        history_pages=[{'history': [added('101', 'm1', 't1', ['INBOX']),
                                    added('102', 'm2', 't2', ['INBOX'])], 'historyId': '110'}],
        messages={'m1': make_message('m1', From='lee@customer-a.com', Subject='제목 m1'),
                  'm2': make_message('m2', From='noreply@vendor.example.com', Subject='공지')})
    _setup(monkeypatch, tmp_path, gmail)
    with StateDB(tmp_path / 'state.db') as db:
        db.set_sync('history_id', '100')

    assert cli.main(['poll', '--dry-run']) == 0
    out = capsys.readouterr().out
    assert 'historyId 100 이후' in out
    assert '제목 m1 → 분류기로' in out
    assert '공지 → 참고 (자동 발송 주소' in out
    assert gmail.modified == []
    with StateDB(tmp_path / 'state.db') as db:
        assert db.history_id == '100'   # dry-run은 시작점을 옮기지 않는다


def test_poll_labels_mail_and_moves_start(monkeypatch, tmp_path, capsys):
    from reda import classify
    gmail = FakeGmail(
        history_pages=[{'history': [added('101', 'm1', 't1', ['INBOX'])], 'historyId': '110'}],
        messages={'m1': make_message('m1', From='noreply@vendor.example.com', Subject='공지')})
    _setup(monkeypatch, tmp_path, gmail)
    monkeypatch.setattr(classify, 'make_classifier', lambda settings: None)  # 규칙으로 끝난다
    with StateDB(tmp_path / 'state.db') as db:
        db.set_sync('history_id', '100')

    assert cli.main(['poll']) == 0
    assert '규칙 1' in capsys.readouterr().out
    assert gmail.modified == [('m1', ['참고'])]
    with StateDB(tmp_path / 'state.db') as db:
        assert db.history_id == '110'


def test_poll_skips_when_already_running(monkeypatch, tmp_path, capsys):
    from reda.lock import exclusive
    _setup(monkeypatch, tmp_path, FakeGmail())
    with exclusive(tmp_path / 'poll.lock'):
        assert cli.main(['poll']) == 0
    assert '건너뜁니다' in capsys.readouterr().out
