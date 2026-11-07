"""처리 흐름 (검증기준 2단계: LLM 호출 0회, 멱등성, 실패 격리, 동시 실행)."""
import pytest
from fakes import FakeGmail, added, make_message

from reda import poll
from reda.classify import Classification, ClassifierUnavailable
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
