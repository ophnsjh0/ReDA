"""한 번의 동기화: 새 메일을 가져와 분류하고 Gmail 라벨을 붙인다.

순서
1. History API로 지난번 이후의 변경을 가져온다.
2. 사용자의 라벨 변경과 놓친 답장을 기록한다 (측정용).
3. 새 메일과 지난번에 실패한 메일을 오래된 것부터 처리한다.
   규칙 필터에 걸리면 LLM을 부르지 않는다. 메일 한 통의 오류는 기록만 하고 다음 메일로 넘어간다.
4. 다음 시작점(historyId)을 저장한다.

분류기 설정 오류(ClassifierUnavailable)는 메일마다 반복될 오류라 실행을 멈춘다. 이때는 시작점을
옮기지 않으므로 다음 실행이 같은 변경을 다시 가져온다 (이미 처리한 메일은 건너뛴다).
"""
from dataclasses import dataclass

from googleapiclient.errors import HttpError

from reda import rules
from reda.classify import Classification, ClassifierUnavailable
from reda.gmail import client, history
from reda.gmail.client import CLASSIFICATION_LABELS
from reda.mail import Mail
from reda.tracking import record_changes

LABEL_URGENT = '긴급'      # 결정 2-2: 긴급도 높음만 라벨로, 나머지는 요약에만
MAX_ATTEMPTS = 5           # 이만큼 실패한 메일은 더 시도하지 않고 요약에서 알린다


@dataclass
class PollResult:
    history_id: str = ''
    full_resync: bool = False
    by_rule: int = 0
    by_classifier: int = 0
    skipped: int = 0         # 사용자가 이미 분류 라벨을 붙인 메일
    failed: int = 0
    label_events: int = 0
    missed_replies: int = 0

    @property
    def labeled(self):
        return self.by_rule + self.by_classifier


class Labeler:
    """라벨 이름 → id. 한 번 실행하는 동안 목록을 한 번만 읽고, 없는 라벨은 만든다."""

    def __init__(self, service):
        self._service = service
        self._ids = {name: lid for lid, name in history.label_names_by_id(service).items()}

    def id(self, name):
        if name not in self._ids:
            self._ids[name] = client.get_or_create_label(self._service, name)
        return self._ids[name]

    def classification_ids(self):
        return {self._ids[n] for n in CLASSIFICATION_LABELS if n in self._ids}


def classify(mail, settings, classifier):
    """(Classification, source). 규칙에 걸리면 분류기를 부르지 않는다."""
    hit = rules.check(mail, settings)
    if hit:
        return Classification(hit.category, hit.urgency, hit.reason), 'rule'
    return classifier.classify(mail), 'classifier'


def _is_not_found(exc):
    return isinstance(exc, HttpError) and getattr(exc.resp, 'status', None) == 404


def _fetch(service, state, refs, result):
    mails = []
    for ref in refs:
        try:
            mails.append(Mail.from_gmail(client.get_message(service, ref['id'])))
        except Exception as exc:
            if _is_not_found(exc):          # 그 사이에 지워진 메일
                state.clear_failure(ref['id'])
                continue
            state.record_failure(ref['id'], ref.get('threadId', ''), f'가져오기: {exc!r}')
            result.failed += 1
    return sorted(mails, key=lambda m: m.internal_date)


def run(service, state, settings, classifier, *, resync_query='in:inbox newer_than:2d',
        max_results=50):
    result = PollResult()
    changes = history.fetch_changes(service, state.history_id, resync_query=resync_query,
                                    max_results=max_results)
    result.full_resync = changes.full_resync

    tracking = record_changes(state, changes)
    result.label_events, result.missed_replies = tracking.label_events, tracking.missed_replies

    refs = {m['id']: m for m in changes.new_messages}
    for row in state.failures(MAX_ATTEMPTS):
        refs.setdefault(row['message_id'], {'id': row['message_id'], 'threadId': row['thread_id']})
    todo = [ref for ref in refs.values() if not state.is_processed(ref['id'])]

    labeler = Labeler(service)
    for mail in _fetch(service, state, todo, result):
        if labeler.classification_ids() & set(mail.label_ids):
            result.skipped += 1
            state.clear_failure(mail.id)
            continue
        try:
            decision, source = classify(mail, settings, classifier)
            label_ids = [labeler.id(decision.category)]
            if decision.urgency == '높음':
                label_ids.append(labeler.id(LABEL_URGENT))
            client.add_labels(service, mail.id, label_ids)
            state.record_classification(mail.id, mail.thread_id, decision.category,
                                        urgency=decision.urgency, reason=decision.reason,
                                        source=source)
        except ClassifierUnavailable:
            raise
        except Exception as exc:
            state.record_failure(mail.id, mail.thread_id, f'분류·라벨: {exc!r}')
            result.failed += 1
            continue
        state.clear_failure(mail.id)
        if source == 'rule':
            result.by_rule += 1
        else:
            result.by_classifier += 1

    state.set_sync('history_id', changes.history_id)
    result.history_id = changes.history_id
    return result
