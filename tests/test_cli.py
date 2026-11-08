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
    monkeypatch.setattr(classify, 'make_classifier', lambda settings, identity=None: None)  # 규칙으로 끝난다
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


def test_classifier_command_shows_provider_and_model(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeGmail())
    monkeypatch.setenv('REDA_CLASSIFIER', 'jev')
    monkeypatch.delenv('REDA_CLASSIFIER_MODEL', raising=False)
    assert cli.main(['classifier']) == 0
    out = capsys.readouterr().out
    assert '분류기: jev' in out and '모델: jev-1.14.0 (제공자 기본값)' in out


def test_classifier_try_classifies_sample_mail(monkeypatch, tmp_path, capsys):
    from reda import classify
    from reda.classify import Classification
    _setup(monkeypatch, tmp_path, FakeGmail())
    seen = []

    class Fake:
        def classify(self, mail):
            seen.append(mail)
            return Classification('답장필요', '높음', '장애 문의', 0.9)

    monkeypatch.setattr(classify, 'make_classifier', lambda settings: Fake())
    assert cli.main(['classifier', '--try']) == 0
    assert '시험 메일 → 답장필요 / 긴급도 높음, 확률 0.90' in capsys.readouterr().out
    assert '192.0.2.10' in seen[0].body          # 마스킹은 분류기 안에서 한다


def test_unknown_classifier_reports_error(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeGmail())
    monkeypatch.setenv('REDA_CLASSIFIER', 'nope')
    assert cli.main(['classifier']) == 1
    assert '알 수 없는 분류기' in capsys.readouterr().err



def test_classifier_try_reports_transient_error_in_one_line(monkeypatch, tmp_path, capsys):
    from reda import classify
    _setup(monkeypatch, tmp_path, FakeGmail())

    class Down:
        def classify(self, mail):
            raise RuntimeError('connection reset')

    monkeypatch.setattr(classify, 'make_classifier', lambda settings: Down())
    assert cli.main(['classifier', '--try']) == 1
    assert '오류: RuntimeError: connection reset' in capsys.readouterr().err


def test_golden_default_path_is_inside_repo_private_folder():
    from pathlib import Path

    from reda import golden
    repo = Path(cli.__file__).resolve().parents[2]
    assert golden.DEFAULT_PATH == repo / 'tests' / 'private' / 'golden.csv'


def test_profile_shows_addresses_and_name(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeGmail(display_name='홍길동'))
    monkeypatch.setenv('REDA_MY_ADDRESSES', 'me@corp.example.com')
    monkeypatch.setenv('REDA_MY_GROUPS', 'tac-team@corp.example.com')
    assert cli.main(['profile']) == 0
    out = capsys.readouterr().out
    assert '개인 주소: me@corp.example.com' in out and 'tac-team@corp.example.com' in out
    assert '전체 이름: 홍길동' in out and '이름: 길동' in out and 'Gmail 보내는 이름' in out
    with StateDB(tmp_path / 'state.db') as db:
        assert db.get_profile('display_name') is None      # 보기만 하고 저장하지 않는다


def test_golden_eval_without_file_reports_error(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, FakeGmail())
    assert cli.main(['golden', 'eval', '--file', str(tmp_path / 'none.csv')]) == 1
    assert '골든 세트 파일이 없음' in capsys.readouterr().err
