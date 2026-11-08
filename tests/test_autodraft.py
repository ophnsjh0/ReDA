"""자동 초안 (결정 3-2): 고르기, 건너뛰기, 결과 기록, 상한. 에이전트는 흉내 낸다 (reda-mcp가 하는 기록만)."""
from datetime import datetime, timezone

import pytest
from fakes import FakeGmail, make_message

from reda import autodraft
from reda.config import Settings
from reda.state import StateDB

ME = 'me@corp.example.com'
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    monkeypatch.setenv('REDA_DRAFT_MAX_PER_RUN', '2')
    return Settings.from_env()


@pytest.fixture
def state(tmp_path):
    with StateDB(tmp_path / 'state.db') as db:
        yield db


def mailbox(*ids, replied=(), unlabeled=()):
    labels = {'Label_1': '답장필요'}
    messages, threads = {}, {}
    for i, mid in enumerate(ids):
        tid = f't-{mid}'
        msg = make_message(mid, thread_id=tid, internal_date=100 + i, From='lee@cust.example.com',
                           To=ME, Subject=f'문의 {mid}',
                           labels=('INBOX',) if mid in unlabeled else ('INBOX', 'Label_1'))
        messages[mid] = msg
        threads[tid] = [msg] + ([{'labelIds': ['SENT'], 'internalDate': '999'}] if mid in replied else [])
    return FakeGmail(labels=labels, messages=messages, threads=threads)


def classify(state, *ids, category='답장필요'):
    for mid in ids:
        state.record_classification(mid, f't-{mid}', category, source='classifier:test')


class Agent:
    """에이전트 흉내: reda-mcp처럼 초안 또는 보류를 상태 DB에 기록한다."""

    def __init__(self, state, plan):
        self.state, self.plan, self.calls = state, plan, []

    def __call__(self, settings, thread_id):
        self.calls.append(thread_id)
        action = self.plan.get(thread_id, 'draft')
        if action == 'draft':
            self.state.record_draft(f'd-{thread_id}', thread_id.removeprefix('t-'), thread_id, [])
            return '저장: d, [확인 필요] 1곳'
        if action == 'hold':
            self.state.record_feedback('draft_hold', message_id='x',
                                       detail={'thread_id': thread_id, 'reason': '다른 사람 담당'})
            return '보류: 다른 사람 담당'
        if action == 'nothing':
            return '모르겠습니다'
        raise RuntimeError('모델 응답 없음')


def test_drafts_and_holds_are_recorded_from_state_not_agent_words(settings, state):
    classify(state, 'a', 'b')
    agent = Agent(state, {'t-a': 'draft', 't-b': 'hold'})
    result = autodraft.draft_pending(mailbox('a', 'b'), state, settings, agent=agent, now=NOW)
    assert (result.drafted, result.held) == (1, 1)
    assert state.draft_job('a')['status'] == 'drafted' and state.draft_job('b')['status'] == 'held'
    # 다음 실행에서는 다시 맡기지 않는다
    agent.calls.clear()
    autodraft.draft_pending(mailbox('a', 'b'), state, settings, agent=agent, now=NOW)
    assert agent.calls == []


def test_already_replied_or_unlabeled_mail_is_skipped_without_agent(settings, state):
    classify(state, 'a', 'b', 'c')
    agent = Agent(state, {})
    result = autodraft.draft_pending(mailbox('a', 'b', 'c', replied={'a'}, unlabeled={'b'}),
                                     state, settings, agent=agent, now=NOW)
    assert agent.calls == ['t-c']
    assert state.draft_job('a')['detail'] == '이미 답장함'
    assert state.draft_job('b')['detail'] == '사용자가 답장필요 라벨을 뗌'
    assert result.skipped == 2 and result.drafted == 1


def test_only_reply_needed_and_per_run_limit(settings, state):
    classify(state, 'a', 'b', 'c')
    classify(state, 'n', category='참고')
    agent = Agent(state, {})
    autodraft.draft_pending(mailbox('a', 'b', 'c', 'n'), state, settings, agent=agent, now=NOW)
    assert agent.calls == ['t-a', 't-b']                # 한 번에 2통까지, 참고는 맡기지 않음
    autodraft.draft_pending(mailbox('a', 'b', 'c', 'n'), state, settings, agent=agent, now=NOW)
    assert agent.calls == ['t-a', 't-b', 't-c']


def test_failures_are_retried_up_to_three_times(settings, state):
    classify(state, 'a')
    agent = Agent(state, {'t-a': 'error'})
    for _ in range(5):
        autodraft.draft_pending(mailbox('a'), state, settings, agent=agent, now=NOW)
    assert len(agent.calls) == autodraft.MAX_ATTEMPTS
    job = state.draft_job('a')
    assert job['status'] == 'failed' and job['attempts'] == 3 and '모델 응답 없음' in job['detail']


def test_agent_that_neither_drafts_nor_holds_is_a_failure(settings, state):
    classify(state, 'a')
    result = autodraft.draft_pending(mailbox('a'), state, settings,
                                     agent=Agent(state, {'t-a': 'nothing'}), now=NOW)
    assert result.failed == 1 and state.draft_job('a')['detail'] == '모르겠습니다'


def test_old_classifications_are_not_drafted(settings, state):
    classify(state, 'a')
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    agent = Agent(state, {})
    autodraft.draft_pending(mailbox('a'), state, settings, agent=agent, now=later)
    assert agent.calls == []


def test_auto_draft_setting(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_AUTO_DRAFT', 'true')
    monkeypatch.setenv('REDA_DRAFT_TIMEOUT', '120')
    s = Settings.from_env()
    assert s.auto_draft and s.draft_timeout == 120 and s.draft_profile == 'reda-dev'


def test_dry_run_lists_without_recording(settings, state):
    classify(state, 'a', 'b')
    agent = Agent(state, {})
    result = autodraft.draft_pending(mailbox('a', 'b', replied={'b'}), state, settings,
                                     agent=agent, now=NOW, dry_run=True)
    assert result.details == [('a', 'would_draft', ''), ('b', 'skip', '이미 답장함')]
    assert agent.calls == [] and state.draft_job('a') is None
