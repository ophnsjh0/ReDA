"""한 번의 동기화: 새 메일을 가져와 분류하고 Gmail 라벨을 붙인다.

순서
1. History API로 지난번 이후의 변경을 가져온다.
2. 사용자의 라벨 변경과 놓친 답장을 기록한다 (측정용).
3. 새 메일과 지난번에 실패한 메일을 오래된 것부터 처리한다.
   규칙 필터에 걸리면 LLM을 부르지 않는다. 메일 한 통의 오류는 기록만 하고 다음 메일로 넘어간다.
   개인 주소 없이 그룹 주소로 받은 `답장필요`는 담당자 단서가 있어야 그대로 둔다 (identity.resolve).
4. 다음 시작점(historyId)을 저장한다.

분류기 오류는 세 가지로 나눈다.
- 설정 오류·일시 장애(ClassifierUnavailable, ClassifierBusy): 모든 메일에 같이 걸리므로 실행을 멈춘다.
  시작점을 옮기지 않고 메일별 실패 횟수도 늘리지 않는다. 다음 실행이 같은 변경을 다시 가져온다
  (이미 처리한 메일은 건너뛴다).
- 요청 거부(ClassifierRequestRejected, HTTP 400/422): 같은 실행에서 분류기가 다른 메일을 하나라도
  분류했으면 그 메일만의 문제로 보고 실패로 기록한다. 하나도 분류하지 못했고 거부된 메일 중 새 메일이
  있으면 설정 문제(예: 모델이 지원하지 않는 옵션)로 보고 실행을 멈춘다. 거부된 메일이 모두 지난번에
  이미 실패한 메일이면 그 메일들의 문제로 보고 실패 횟수를 늘린다. 그러지 않으면 늘 거부되는 메일
  하나가 매 실행을 멈춰 동기화가 영영 진행되지 않는다.
"""
from dataclasses import dataclass

from googleapiclient.errors import HttpError

from reda import identity as ident
from reda import rules
from reda.classify.mailtext import customer_of
from reda.priority import priority
from reda.classify import Classification, ClassifierRequestRejected, ClassifierUnavailable
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


@dataclass(frozen=True)
class Outcome:
    classification: Classification
    source: str                  # 'rule' 또는 'classifier:<제공자/모델>' (모델별 정확도 비교용)
    recipient_mode: str
    addressee_match: str = ''    # 분류기로 간 메일만: me / other / none / unknown
    priority: float = 0.0        # 처음 계산한 우선순위 (reda.priority). 요약 때 기다린 시간을 더해 다시 계산


def classify(mail, settings, classifier, identity=None, *, replied_in_thread=None):
    """규칙 필터 → 분류기 → 담당자 판정. 규칙에 걸리면 분류기를 부르지 않는다.

    replied_in_thread(mail) -> bool: 그룹으로 받은 답장필요의 담당자를 가릴 때만 부른다.
    """
    identity = identity or ident.Identity.from_settings(settings)
    mode = identity.recipient_mode(mail)
    hit = rules.check(mail, settings, identity)
    if hit:
        return Outcome(Classification(hit.category, hit.urgency, hit.reason), 'rule', mode)
    name = getattr(classifier, 'name', '')
    raw = classifier.classify(mail)
    match = ident.addressee_match(raw, mail, identity)
    owned = any(a in identity.owned_groups for a in (*mail.to, *mail.cc))
    decided = ident.resolve(raw, mode, match,
                            lambda: bool(replied_in_thread and replied_in_thread(mail)),
                            owned_group=owned)
    decided = ident.resolve_todo(decided, mode, match)
    customer = bool(customer_of(mail, settings.customer_domains))
    return Outcome(decided, f'classifier:{name}' if name else 'classifier', mode, match,
                   priority(decided, customer=customer))


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
        max_results=50, identity=None):
    """identity: 내 주소·그룹·이름 (reda.identity.load). 주지 않으면 여기서 불러온다.
    분류기에 준 것과 같은 것을 넘겨야 규칙 필터·분류기·담당자 판정이 같은 주소를 쓴다."""
    result = PollResult()
    changes = history.fetch_changes(service, state.history_id, resync_query=resync_query,
                                    max_results=max_results)
    result.full_resync = changes.full_resync

    tracking = record_changes(state, changes)
    result.label_events, result.missed_replies = tracking.label_events, tracking.missed_replies

    refs = {m['id']: m for m in changes.new_messages}
    retrying = set()                # 지난번에 실패해서 다시 해 보는 메일
    for row in state.failures(MAX_ATTEMPTS):
        retrying.add(row['message_id'])
        refs.setdefault(row['message_id'], {'id': row['message_id'], 'threadId': row['thread_id']})
    todo = [ref for ref in refs.values() if not state.is_processed(ref['id'])]

    identity = identity or ident.load(settings, service=service, state=state, persist=True)
    replied = lambda m: client.thread_has_my_reply(service, m.thread_id, m.internal_date)  # noqa: E731
    labeler = Labeler(service)
    rejected = []                 # (mail, 오류) 분류기가 거부한 메일. 판단은 반복이 끝난 뒤에
    for mail in _fetch(service, state, todo, result):
        if labeler.classification_ids() & set(mail.label_ids):
            result.skipped += 1
            state.clear_failure(mail.id)
            continue
        try:
            outcome = classify(mail, settings, classifier, identity, replied_in_thread=replied)
            decision, source = outcome.classification, outcome.source
            label_ids = [labeler.id(decision.category)]
            if decision.urgency == '높음':
                label_ids.append(labeler.id(LABEL_URGENT))
            client.add_labels(service, mail.id, label_ids)
            state.record_classification(mail.id, mail.thread_id, decision.category,
                                        urgency=decision.urgency, reason=decision.reason,
                                        source=source, recipient_mode=outcome.recipient_mode,
                                        addressee=decision.addressee or None,
                                        addressee_match=outcome.addressee_match or None,
                                        detail=decision.detail,
                                        priority=outcome.priority)
        except ClassifierUnavailable:
            raise
        except ClassifierRequestRejected as exc:
            rejected.append((mail, exc))
            continue
        except Exception as exc:
            state.record_failure(mail.id, mail.thread_id, f'분류·라벨: {exc!r}')
            result.failed += 1
            continue
        state.clear_failure(mail.id)
        if source == 'rule':
            result.by_rule += 1
        else:
            result.by_classifier += 1

    if rejected:
        if not result.by_classifier and any(m.id not in retrying for m, _ in rejected):
            raise ClassifierUnavailable(
                f'분류기가 요청 {len(rejected)}건을 모두 거부함. 모델·옵션 설정을 확인한다 '
                f'(uv run reda classifier --try). 마지막 오류: {rejected[-1][1]}')
        for mail, exc in rejected:
            state.record_failure(mail.id, mail.thread_id, f'분류기 요청 거부: {exc}')
            result.failed += 1

    state.set_sync('history_id', changes.history_id)
    result.history_id = changes.history_id
    return result
