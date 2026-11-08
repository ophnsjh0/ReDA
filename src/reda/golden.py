"""골든 세트: 실제 메일에 사람이 정답을 달고, 규칙 필터 + 분류기의 정확도를 잰다.

    reda golden export    메일 목록을 CSV로 내보낸다. `정답` 칸을 사람이 채운다
    reda golden eval      정답이 있는 메일을 분류해 보고 놓침·확인필요 비율을 낸다

파일은 실제 메일 정보를 담으므로 git에서 뺀 `tests/private/`에 둔다. 두 명령 모두 Gmail과
상태 DB를 바꾸지 않는다. eval은 규칙에 걸리지 않은 메일을 분류기(LLM 등)로 보낸다.

판정 (검증기준 2단계)
- 놓침: 정답이 답장필요인데 `참고`로 분류한 메일. 목표 5% 이하
- `확인필요`는 사용자가 직접 보게 되므로 놓침으로 치지 않는다. 대신 전체의 20% 이하가 목표
- 수신 방식(개인 To·그룹 To·개인 Cc·그룹 Cc·숨은참조)별로도 나눠 본다. 팀 메일 판정(결정 2-5)이
  맞는지 보려는 것이다
"""
import csv
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from reda import identity as ident
from reda import rules
from reda.gmail import client
from reda.gmail.client import LABEL_CHECK, LABEL_FYI, LABEL_REPLY
from reda.mail import Mail

# 실행 위치와 관계없이 이 저장소의 tests/private/ (git 제외)
DEFAULT_PATH = Path(__file__).resolve().parents[2] / 'tests' / 'private' / 'golden.csv'
FIELDS = ('message_id', '날짜', '보낸사람', '제목', '수신방식', '규칙결과', '정답', '메모')
RESULT_FIELDS = FIELDS + ('예측', '할일', '긴급도', '우선순위', '출처', '인사말대상', '대상판정',
                          '이유', '판단값')
ANSWERS = (LABEL_REPLY, LABEL_FYI)
# 사람이 짧게 적어도 알아듣는다
_ALIASES = {'r': LABEL_REPLY, '답장': LABEL_REPLY, 'o': LABEL_REPLY,
            'f': LABEL_FYI, 'x': LABEL_FYI}


def normalize_answer(value):
    value = (value or '').strip()
    value = _ALIASES.get(value.lower(), value)
    return value if value in ANSWERS else ''


def read(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')


def _safe_cell(value):
    """엑셀·Numbers가 수식으로 실행하지 않게 한다 (외부 메일 제목이 `=HYPERLINK(...)`일 수 있다)."""
    value = '' if value is None else str(value)
    return "'" + value if value.startswith(_FORMULA_START) else value


def write(path, rows, fields=FIELDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.unlink(missing_ok=True)
    # 실제 메일 정보가 들어 있으므로 처음부터 600으로 만든다
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    # utf-8-sig: 엑셀·Numbers에서 한글이 깨지지 않게
    with open(fd, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _safe_cell(v) for k, v in row.items()})
    tmp.replace(path)


def list_message_ids(service, query, max_results):
    ids, token = [], None
    while len(ids) < max_results:
        kwargs = {'userId': 'me', 'q': query, 'maxResults': min(500, max_results - len(ids))}
        if token:
            kwargs['pageToken'] = token
        response = service.users().messages().list(**kwargs).execute()
        ids.extend(m['id'] for m in response.get('messages', []))
        token = response.get('nextPageToken')
        if not token:
            break
    return ids[:max_results]


def export(service, settings, path=DEFAULT_PATH, *, query='in:inbox newer_than:14d',
           max_results=150, identity=None):
    """(전체 행 수, 새로 더한 행 수). 이미 있는 파일의 정답·메모는 그대로 둔다."""
    path = Path(path)
    identity = identity or ident.load(settings, service=service)
    existing = {r['message_id']: r for r in read(path)} if path.exists() else {}
    added = 0
    for message_id in list_message_ids(service, query, max_results):
        if message_id in existing:
            continue
        # 규칙 필터가 인사말을 보므로 본문까지 가져온다
        mail = Mail.from_gmail(client.get_message(service, message_id))
        hit = rules.check(mail, settings, identity)
        existing[message_id] = {
            'message_id': message_id, '날짜': mail.date, '보낸사람': mail.sender,
            '제목': mail.subject,
            '수신방식': ident.MODE_LABELS[identity.recipient_mode(mail)],
            '규칙결과': f'참고 ({hit.reason})' if hit else '', '정답': '', '메모': ''}
        added += 1
    write(path, existing.values())
    return len(existing), added


@dataclass
class Report:
    total: int = 0                   # 정답이 달린 메일
    by_rule: int = 0
    confusion: Counter = field(default_factory=Counter)  # (정답, 예측) → 수
    misses: list = field(default_factory=list)          # 놓친 답장필요 행
    errors: list = field(default_factory=list)          # (message_id, 오류)
    by_mode: dict = field(default_factory=dict)         # 수신 방식 → Counter((정답, 예측))
    stopped: str = ''                # 분류기 설정 오류·일시 장애로 도중에 멈췄으면 그 이유
    predicted: Counter = field(default_factory=Counter)  # 분류한 모든 메일(정답 없는 것 포함)의 예측
    todos: Counter = field(default_factory=Counter)      # 할 일 표시 ('일정' / '할 일') 수

    @property
    def reply_total(self):
        return sum(n for (truth, _), n in self.confusion.items() if truth == LABEL_REPLY)

    @property
    def miss_rate(self):
        return len(self.misses) / self.reply_total if self.reply_total else 0.0

    @property
    def check_rate(self):
        checks = sum(n for (_, pred), n in self.confusion.items() if pred == LABEL_CHECK)
        return checks / self.total if self.total else 0.0


def evaluate(service, settings, classifier, path=DEFAULT_PATH, *, result_path=None,
             identity=None, include_unanswered=False):
    """정답이 달린 행을 분류해 Report를 돌려준다. result_path에 행마다 예측을 남긴다.

    `reda poll`과 같은 판정(규칙 → 분류기 → 담당자 판정)을 쓴다.
    include_unanswered=True면 정답이 없는 행도 분류해 결과 파일에 남긴다 (정답을 달기 전 미리 보기).
    정확도 계산에는 정답이 있는 행만 쓴다.
    """
    from reda.poll import classify
    identity = identity or ident.load(settings, service=service)
    replied = lambda m: client.thread_has_my_reply(service, m.thread_id, m.internal_date)  # noqa: E731
    report, results = Report(), []
    for row in read(path):
        truth = normalize_answer(row.get('정답'))
        if not truth and not include_unanswered:
            continue
        try:
            mail = Mail.from_gmail(client.get_message(service, row['message_id']))
            outcome = classify(mail, settings, classifier, identity, replied_in_thread=replied)
            decision, source = outcome.classification, outcome.source
        except Exception as exc:
            from reda.classify import ClassifierUnavailable
            if isinstance(exc, ClassifierUnavailable):
                # 설정 오류·일시 장애는 나머지 메일에도 같이 걸린다. 멈추되, 이미 분류한 결과는
                # 버리지 않는다 (분류기 호출에는 돈이 든다)
                report.stopped = f'{exc.__class__.__name__}: {exc}'
                break
            report.errors.append((row['message_id'], repr(exc)))
            continue
        mode = ident.MODE_LABELS[outcome.recipient_mode]
        report.predicted[(source == 'rule', decision.category)] += 1
        if truth:
            report.total += 1
            report.by_rule += source == 'rule'
            report.confusion[(truth, decision.category)] += 1
            report.by_mode.setdefault(mode, Counter())[(truth, decision.category)] += 1
            if truth == LABEL_REPLY and decision.category == LABEL_FYI:
                report.misses.append({**row, '예측': decision.category, '이유': decision.reason})
        if decision.todo:
            report.todos[decision.todo] += 1
        results.append({**row, '정답': truth, '예측': decision.category, '할일': decision.todo,
                        '긴급도': decision.urgency, '우선순위': outcome.priority, '출처': source,
                        '수신방식': mode, '인사말대상': decision.addressee,
                        '대상판정': outcome.addressee_match, '이유': decision.reason,
                        '판단값': json.dumps(decision.detail, ensure_ascii=False)
                        if decision.detail else ''})
    if result_path:
        write(result_path, results, RESULT_FIELDS)
    return report


def format_report(report):
    lines = [f'정답이 달린 메일 {report.total}통 (규칙으로 끝남 {report.by_rule}통)']
    if report.predicted:
        def count(by_rule, category):
            return report.predicted[(by_rule, category)]
        cats = (LABEL_REPLY, LABEL_FYI, LABEL_CHECK)
        lines.append('분류한 메일 전체 (정답 없는 메일 포함): '
                     f'규칙 → 참고 {count(True, LABEL_FYI)}통 / 분류기 → '
                     + ', '.join(f'{c} {count(False, c)}' for c in cats))
    if report.todos:
        lines.append('할 일 표시: ' + ', '.join(f'{k} {n}통' for k, n in report.todos.items()))
    if report.stopped:
        lines.insert(0, f'도중에 멈춤: {report.stopped}. 아래는 멈추기 전까지 평가한 {report.total}통의 결과다')
    if report.total:
        lines.append('정답 \\ 예측   답장필요  참고  확인필요')
        for truth in ANSWERS:
            cells = '  '.join(f'{report.confusion[(truth, p)]:>6}'
                              for p in (LABEL_REPLY, LABEL_FYI, LABEL_CHECK))
            lines.append(f'{truth:<10} {cells}')
        lines.append(f'답장필요 놓침: {len(report.misses)}/{report.reply_total}'
                     f' = {report.miss_rate:.1%} (목표 5% 이하)')
        lines.append(f'확인필요 비율: {report.check_rate:.1%} (목표 20% 이하)')
        lines.append('수신 방식별 (메일 수 / 정답 답장필요 / 놓침 / 확인필요 / 답장필요 오판)')
        for mode in ident.MODE_LABELS.values():
            c = report.by_mode.get(mode)
            if not c:
                continue
            total = sum(c.values())
            replies = sum(n for (truth, _), n in c.items() if truth == LABEL_REPLY)
            checks = sum(n for (_, pred), n in c.items() if pred == LABEL_CHECK)
            lines.append(f'- {mode}: {total} / {replies} / {c[(LABEL_REPLY, LABEL_FYI)]}'
                         f' / {checks} / {c[(LABEL_FYI, LABEL_REPLY)]}')
    for miss in report.misses:
        lines.append(f'- 놓침: {miss["날짜"]} | {miss["보낸사람"]} | {miss["제목"]} → {miss["이유"]}')
    for message_id, error in report.errors:
        lines.append(f'- 오류: {message_id} {error}')
    return '\n'.join(lines)
