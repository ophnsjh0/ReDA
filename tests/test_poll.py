"""처리 흐름 (검증기준 2단계: LLM 호출 0회, 멱등성, 실패 격리, 동시 실행)."""
import pytest
from fakes import FakeGmail, added, make_message

from reda import poll
from reda.classify import (Classification, ClassifierBusy, ClassifierRequestRejected,
                           ClassifierUnavailable)
from reda.config import Settings
from reda.lock import AlreadyRunning, exclusive
from reda.state import StateDB

ME = 'me@corp.example.com'


class CountingClassifier:
    def __init__(self, result=Classification('답장필요', '보통', '문의'), error=None):
        self.result, self.error, self.seen = result, error, []

    def classify(self, mail):
        self.seen.append(mail.id)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    return Settings.from_env()


@pytest.fixture
def state(tmp_path):
    with StateDB(tmp_path / 'state.db') as db:
        db.set_sync('history_id', '100')
        yield db


def gmail_with(*messages, broken=()):
    history = [added(str(101 + i), m['id'], m['threadId'], ['INBOX'])
               for i, m in enumerate(messages)]
    return FakeGmail(history_pages=[{'history': history, 'historyId': '200'}],
                     messages={m['id']: m for m in messages}, broken=broken)


def customer(mid, date=0, subject='문의'):
    return make_message(mid, internal_date=date, From='lee@customer-a.example.com', To=ME,
                        Subject=subject)


def notice(mid, date=0):
    return make_message(mid, internal_date=date, From='noreply@vendor.example.com', To=ME,
                        Subject='공지')


def test_rule_filtered_mail_never_reaches_classifier(settings, state):
    gmail = gmail_with(notice('n1'), notice('n2'),
                       make_message('c1', From='lee@customer-a.example.com', To='kim@x.example.com',
                                    Cc=ME))
    classifier = CountingClassifier()
    result = poll.run(gmail, state, settings, classifier)
    assert classifier.seen == []
    assert result.by_rule == 3 and result.by_classifier == 0
    assert {mid for mid, _ in gmail.modified} == {'n1', 'n2', 'c1'}
    assert all(names == ['참고'] for _, names in gmail.modified)
    assert state.get_message('n1')['source'] == 'rule'


def test_classifier_result_is_labeled_and_recorded(settings, state):
    gmail = gmail_with(customer('m1'))
    classifier = CountingClassifier(Classification('답장필요', '높음', '장애 문의'))
    result = poll.run(gmail, state, settings, classifier)
    assert result.by_classifier == 1
    assert gmail.modified == [('m1', ['답장필요', '긴급'])]
    row = state.get_message('m1')
    assert (row['category'], row['urgency'], row['source']) == ('답장필요', '높음', 'classifier')
    assert state.history_id == '200'


def test_oldest_mail_is_processed_first(settings, state):
    gmail = gmail_with(customer('late', date=3000), customer('early', date=1000),
                       customer('mid', date=2000))
    classifier = CountingClassifier()
    poll.run(gmail, state, settings, classifier)
    assert classifier.seen == ['early', 'mid', 'late']


def test_running_twice_does_not_duplicate(settings, state):
    gmail = gmail_with(customer('m1'), notice('n1'))
    classifier = CountingClassifier()
    poll.run(gmail, state, settings, classifier)
    state.set_sync('history_id', '100')     # 같은 변경을 한 번 더 받는다
    second = poll.run(gmail, state, settings, classifier)
    assert classifier.seen == ['m1']
    assert second.labeled == 0
    assert len(gmail.modified) == 2
    assert [l for l in gmail.labels.values()].count('참고') == 1   # 라벨도 한 번만 만든다


def test_one_broken_mail_does_not_stop_others_and_is_retried(settings, state):
    gmail = gmail_with(customer('ok1', date=1), customer('bad', date=2), customer('ok2', date=3),
                       broken={'bad'})
    classifier = CountingClassifier()
    result = poll.run(gmail, state, settings, classifier)
    assert result.failed == 1 and result.by_classifier == 2
    assert [r['message_id'] for r in state.failures()] == ['bad']
    assert state.history_id == '200'

    # 다음 실행: 새 변경이 없어도 실패한 메일을 다시 시도하고, 성공하면 실패 기록을 지운다
    gmail.broken.clear()
    gmail.history_pages = [{'history': [], 'historyId': '210'}]
    retry = poll.run(gmail, state, settings, classifier)
    assert retry.by_classifier == 1
    assert state.failures() == []
    assert state.is_processed('bad')


def test_classifier_error_on_one_mail_is_isolated(settings, state):
    gmail = gmail_with(customer('m1'))
    result = poll.run(gmail, state, settings, CountingClassifier(error=TimeoutError('느림')))
    assert result.failed == 1
    assert 'TimeoutError' in state.failures()[0]['last_error']
    assert gmail.modified == []


def test_gives_up_after_max_attempts(settings, state):
    gmail = gmail_with(customer('bad'), broken={'bad'})
    poll.run(gmail, state, settings, CountingClassifier())
    gmail.history_pages = [{'history': [], 'historyId': '200'}]
    for _ in range(poll.MAX_ATTEMPTS + 2):
        poll.run(gmail, state, settings, CountingClassifier())
    assert state.failures()[0]['attempts'] == poll.MAX_ATTEMPTS


def test_classifier_unavailable_stops_without_moving_start(settings, state):
    gmail = gmail_with(customer('m1'))
    with pytest.raises(ClassifierUnavailable):
        poll.run(gmail, state, settings, CountingClassifier(error=ClassifierUnavailable('키 없음')))
    assert state.history_id == '100'
    assert state.failures() == []


def test_mail_user_already_labeled_is_skipped(settings, state):
    msg = customer('m1')
    gmail = gmail_with(msg)
    gmail.labels['Label_9'] = '참고'
    msg['labelIds'].append('Label_9')
    classifier = CountingClassifier()
    result = poll.run(gmail, state, settings, classifier)
    assert result.skipped == 1 and classifier.seen == [] and gmail.modified == []


def test_deleted_mail_is_dropped_quietly(settings, state):
    gmail = gmail_with(customer('m1'))
    del gmail.messages['m1']
    result = poll.run(gmail, state, settings, CountingClassifier())
    assert result.failed == 0 and state.failures() == []


def test_second_run_exits_while_first_holds_lock(tmp_path):
    with exclusive(tmp_path / 'poll.lock'):
        with pytest.raises(AlreadyRunning):
            with exclusive(tmp_path / 'poll.lock'):
                pass
    with exclusive(tmp_path / 'poll.lock'):   # 끝나면 다시 잡을 수 있다
        pass


def test_classifier_name_is_recorded_as_source(settings, state):
    classifier = CountingClassifier()
    classifier.name = 'jev/jev-latest'
    gmail = gmail_with(customer('c1'))
    poll.run(gmail, state, settings, classifier)
    assert state.get_message('c1')['source'] == 'classifier:jev/jev-latest'



class SelectiveClassifier:
    """지정한 메일만 거부하고 나머지는 분류한다."""

    def __init__(self, reject=(), error=ClassifierRequestRejected('HTTP 400')):
        self.reject, self.error = set(reject), error

    def classify(self, mail):
        if mail.id in self.reject:
            raise self.error
        return Classification('답장필요', '보통', '문의')


def test_rejection_of_one_mail_is_recorded_when_others_succeed(settings, state):
    gmail = gmail_with(customer('c1', 1), customer('c2', 2))
    result = poll.run(gmail, state, settings, SelectiveClassifier(reject={'c1'}))
    assert result.by_classifier == 1 and result.failed == 1
    assert [r['message_id'] for r in state.failures(poll.MAX_ATTEMPTS)] == ['c1']
    assert state.history_id == '200'


def test_rejection_of_every_mail_stops_without_counting_failures(settings, state):
    gmail = gmail_with(customer('c1', 1), customer('c2', 2))
    with pytest.raises(ClassifierUnavailable, match='모두 거부'):
        poll.run(gmail, state, settings, SelectiveClassifier(reject={'c1', 'c2'}))
    assert state.failures(poll.MAX_ATTEMPTS) == []
    assert state.history_id == '100'            # 설정을 고친 뒤 다시 가져온다


def test_busy_classifier_stops_without_using_attempts(settings, state):
    gmail = gmail_with(customer('c1', 1))
    with pytest.raises(ClassifierBusy):
        poll.run(gmail, state, settings, SelectiveClassifier(reject={'c1'}, error=ClassifierBusy('429')))
    assert state.failures(poll.MAX_ATTEMPTS) == []
    assert state.history_id == '100'


# ---------------------------------------------------------------- 팀 메일 담당자 판정 (결정 2-5)

GROUP = 'tac-team@corp.example.com'


@pytest.fixture
def team_settings(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    monkeypatch.setenv('REDA_MY_GROUPS', GROUP)
    monkeypatch.setenv('REDA_MY_TITLES', '부장')
    return Settings.from_env()


def team_gmail(*messages, threads=None):
    gmail = gmail_with(*messages)
    gmail.display_name = '홍길동'
    gmail.threads = threads or {}
    return gmail


def group_request(mid, body, **headers):
    headers.setdefault('To', GROUP)
    return make_message(mid, internal_date=10, From='lee@customer-a.example.com',
                        Subject='설정 문의', body=body, **headers)


def test_group_request_greeting_someone_else_becomes_fyi(team_settings, state):
    classifier = CountingClassifier(Classification('답장필요', '보통', '문의', addressee='김철수 과장님'))
    gmail = team_gmail(group_request('g1', '김철수 과장님, 설정 확인 부탁드립니다.'))
    poll.run(gmail, state, team_settings, classifier)
    row = state.get_message('g1')
    assert row['category'] == '참고'
    assert (row['recipient_mode'], row['addressee'], row['addressee_match']) == (
        'group_to', '김철수 과장님', 'other')
    assert gmail.modified == [('g1', ['참고'])]


def test_group_request_greeting_me_stays_reply(team_settings, state):
    classifier = CountingClassifier(Classification('답장필요', '보통', '문의', addressee='길동 부장님'))
    poll.run(team_gmail(group_request('g1', '길동 부장님, 확인 부탁드립니다.')), state,
             team_settings, classifier)
    assert state.get_message('g1')['category'] == '답장필요'
    assert state.get_message('g1')['addressee_match'] == 'me'


def test_group_request_without_clue_is_check_unless_i_replied(team_settings, state):
    gmail = team_gmail(group_request('g1', '안녕하세요. 확인 부탁드립니다.'),
                       make_message('g2', thread_id='t-mine', internal_date=20,
                                    From='lee@customer-a.example.com', To=GROUP,
                                    body='안녕하세요. 추가 문의입니다.'),
                       threads={'t-mine': [{'labelIds': ['SENT'], 'internalDate': '5'}]})
    poll.run(gmail, state, team_settings, CountingClassifier())
    assert state.get_message('g1')['category'] == '참고'          # 단서 없는 그룹 요청
    assert state.get_message('g2')['category'] == '답장필요'      # 내가 답한 스레드


def test_owned_group_request_without_clue_is_check(monkeypatch, team_settings, state):
    monkeypatch.setenv('REDA_MY_OWNED_GROUPS', GROUP)
    s = Settings.from_env()
    poll.run(team_gmail(group_request('g1', '안녕하세요. 확인 부탁드립니다.')), state, s,
             CountingClassifier())
    assert state.get_message('g1')['category'] == '확인필요'


def test_group_cc_greeting_me_goes_to_classifier(team_settings, state):
    classifier = CountingClassifier()
    m = group_request('g1', '길동 부장님, 참조로 넣었습니다. 의견 부탁드립니다.',
                      To='kim@corp.example.com', Cc=GROUP)
    poll.run(team_gmail(m), state, team_settings, classifier)
    assert classifier.seen == ['g1']
    assert state.get_message('g1')['category'] == '답장필요'


def test_group_cc_without_greeting_stays_rule(team_settings, state):
    classifier = CountingClassifier()
    m = group_request('g1', '김철수 과장님, 확인 부탁드립니다.', To='kim@corp.example.com', Cc=GROUP)
    poll.run(team_gmail(m), state, team_settings, classifier)
    assert classifier.seen == []
    row = state.get_message('g1')
    assert (row['category'], row['source'], row['recipient_mode']) == ('참고', 'rule', 'group_cc')


def test_personal_request_is_not_changed_by_owner_check(team_settings, state):
    classifier = CountingClassifier(Classification('답장필요', '보통', '문의', addressee='김철수 과장님'))
    poll.run(team_gmail(customer('c1')), state, team_settings, classifier)
    assert state.get_message('c1')['category'] == '답장필요'
    assert state.get_message('c1')['recipient_mode'] == 'personal_to'


def test_poll_remembers_display_name(team_settings, state):
    poll.run(team_gmail(customer('c1')), state, team_settings, CountingClassifier())
    assert state.get_profile('display_name') == '홍길동'


def test_always_rejected_retry_does_not_stall_sync(settings, state):
    """리뷰 지적: 늘 거부되는 메일 하나가 다음 실행들을 계속 멈추면 안 된다."""
    gmail = gmail_with(customer('c1', 1), customer('c2', 2))
    classifier = SelectiveClassifier(reject={'c1'})
    poll.run(gmail, state, settings, classifier)                 # c1 실패 기록, c2 분류
    assert [r['attempts'] for r in state.failures()] == [1]

    gmail.history_pages = [{'history': [], 'historyId': '300'}]  # 새 메일 없음
    result = poll.run(gmail, state, settings, classifier)        # c1만 다시 시도 → 거부
    assert result.failed == 1 and state.history_id == '300'      # 멈추지 않고 진행
    assert [r['attempts'] for r in state.failures()] == [2]


def test_poll_works_without_user_info_in_env(monkeypatch, tmp_path, state):
    """배포 때처럼 .env에 사용자 정보가 없으면 Gmail 계정 주소로 판정한다."""
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', '')
    s = Settings.from_env()
    gmail = gmail_with(make_message('c1', From='lee@customer-a.example.com', To='me@corp.com'),
                       make_message('c2', From='lee@customer-a.example.com',
                                    To='kim@corp.example.com', Cc='me@corp.com'))
    classifier = CountingClassifier()
    poll.run(gmail, state, s, classifier)
    assert state.get_message('c1')['recipient_mode'] == 'personal_to'
    assert state.get_message('c2')['reason'] == '참조(Cc)로만 받음'
    assert classifier.seen == ['c1']
