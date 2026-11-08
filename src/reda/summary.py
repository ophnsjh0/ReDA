"""출근 전 요약 메시지 (4단계 1번, 기획안 3장 예시). `reda summary`.

지난 요약 이후(처음이면 최근 24시간) 분류한 메일을 상태 DB에서 모아 메신저로 보낼 글을 만든다.
- 메일 수, 답장필요 n건(초안 n건, 보류 n건), 확인필요 n건, 참고 n건
- 급한 메일은 맨 위에. 답장필요·확인필요는 우선순위 순으로 제목·보낸 사람·초안 여부
- 할 일 표시(일정 / 할 일)가 붙은 메일 (9단계 일정 제안 전까지는 목록만)
- 처리하지 못한 메일(실패)이 있으면 알린다

제목·보낸 사람은 Gmail에서 가져오고, IP·계정 같은 값은 가린다 (메신저 서버로 나가는 글이다).
보내기는 `hermes -p <프로필> send --to telegram`이 맡는다 (LLM을 거치지 않는다).
"""
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from reda import priority as prio
from reda.autodraft import hermes_command
from reda.classify import Classification
from reda.context import gmail_link
from reda.gmail import client
from reda.gmail.client import LABEL_CHECK, LABEL_FYI, LABEL_REPLY
from reda.mail import address_of
from reda.mask import Masker

LIST_MAX = 10                 # 목록마다 보여 줄 메일 수
SYNC_KEY = 'last_summary_at'
ALERT_KEY = 'last_alert_at'     # 급한 메일 알림을 어디까지 확인했는가 (labeled_at)
URGENT_CATEGORIES = (LABEL_REPLY, LABEL_CHECK)


@dataclass
class Item:
    message_id: str
    thread_id: str
    category: str
    urgency: str
    todo: str
    draft: str                # '초안 있음' / '보류' / '초안 준비 중' / ''
    priority: float
    subject: str = ''
    sender: str = ''


@dataclass
class Summary:
    since: str
    total: int = 0
    counts: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    failures: int = 0


def _draft_state(state, row):
    if state.drafts_for_thread(row['thread_id']):
        return '초안 있음'
    job = state.draft_job(row['message_id'])
    if job is None:
        return ''
    return {'drafted': '초안 있음', 'held': '보류', 'skipped': '', 'failed': '초안 실패'}[job['status']]


def _item(state, row, now):
    detail = json.loads(row['classifier_detail'] or '{}')
    labeled = datetime.fromisoformat(row['labeled_at'])
    waiting = max(0.0, (now - labeled).total_seconds() / 3600)
    c = Classification(row['category'], row['urgency'] or '보통', row['reason'] or '',
                       urgency_score=detail.get('urgency_score'))
    return Item(
        message_id=row['message_id'], thread_id=row['thread_id'], category=row['category'],
        urgency=row['urgency'] or '보통', todo=detail.get('todo', ''), draft=_draft_state(state, row),
        priority=prio.priority(c, waiting_hours=waiting))


def collect(state, *, since):
    rows = state.conn.execute(
        'SELECT * FROM messages WHERE labeled_at >= ? ORDER BY labeled_at', (since,)).fetchall()
    summary = Summary(since=since, total=len(rows))
    now = datetime.now(timezone.utc)
    for row in rows:
        summary.counts[row['category']] = summary.counts.get(row['category'], 0) + 1
        item = _item(state, row, now)
        if row['category'] == LABEL_FYI and not item.todo:
            continue
        summary.items.append(item)
    summary.failures = len(state.failures())
    return summary


def fill_headers(service, summary):
    """보여 줄 메일의 제목·보낸 사람을 Gmail에서 가져와 가린다."""
    masker = Masker()
    shown = [i for i in summary.items if i.category != LABEL_FYI][:LIST_MAX * 2]
    shown += [i for i in summary.items if i.category == LABEL_FYI][:LIST_MAX]
    for item in shown:
        try:
            meta = client.get_message_metadata(service, item.message_id, ('Subject', 'From'))
        except Exception:
            item.subject = '(메일을 가져오지 못함)'
            continue
        sender = client.get_header(meta, 'From')
        name = sender.split('<')[0].strip().strip('"') or address_of(sender)
        item.subject = masker.mask(client.get_header(meta, 'Subject'))[:80]
        item.sender = masker.mask(name)[:40]


def _line(item, *, link=True):
    mark = '🔴 ' if item.urgency == '높음' else ''
    extra = f' ({item.draft})' if item.draft else ''
    todo = f' [{item.todo}]' if item.todo and item.category != LABEL_FYI else ''
    text = f'{mark}{item.subject} — {item.sender}{extra}{todo}'
    return text + (f'\n  {gmail_link(item.thread_id)}' if link else '')


def render(summary, *, period='지난 요약 이후'):
    c = summary.counts
    replies = sorted((i for i in summary.items if i.category == LABEL_REPLY),
                     key=lambda i: -i.priority)
    checks = sorted((i for i in summary.items if i.category == LABEL_CHECK),
                    key=lambda i: -i.priority)
    todos = [i for i in summary.items if i.todo]
    drafted = sum(1 for i in replies if i.draft == '초안 있음')
    held = sum(1 for i in replies if i.draft == '보류')
    lines = [f'📬 {period} 메일 {summary.total}통 확인']
    reply_note = f'초안 {drafted}건을 임시보관함에 넣어 두었어요' if drafted else '초안 없음'
    if held:
        reply_note += f', 보류 {held}건'
    lines.append(f'· 답장필요 {c.get(LABEL_REPLY, 0)}건 — {reply_note}')
    lines.append(f'· 확인필요 {c.get(LABEL_CHECK, 0)}건 — 직접 확인해 주세요')
    lines.append(f'· 참고 {c.get(LABEL_FYI, 0)}건 — 라벨만 붙였어요')
    if replies:
        lines += ['', '✉️ 답장필요'] + [f'{n}. {_line(i)}' for n, i in enumerate(replies[:LIST_MAX], 1)]
        if len(replies) > LIST_MAX:
            lines.append(f'… 외 {len(replies) - LIST_MAX}건')
    if checks:
        lines += ['', '❓ 확인필요'] + [f'{n}. {_line(i)}' for n, i in enumerate(checks[:LIST_MAX], 1)]
        if len(checks) > LIST_MAX:
            lines.append(f'… 외 {len(checks) - LIST_MAX}건')
    if todos:
        lines += ['', '📅 일정·할 일 (캘린더 등록은 9단계에서)']
        lines += [f'· [{i.todo}] {i.subject} — {i.sender}' for i in todos[:LIST_MAX]]
    if summary.failures:
        lines += ['', f'⚠️ 처리하지 못한 메일 {summary.failures}통 (다음 실행에서 다시 시도)']
    if summary.total == 0:
        lines = [f'📬 {period} 새로 분류한 메일이 없어요']
    return '\n'.join(lines)


def urgent(state, *, after):
    """after(labeled_at) 뒤에 분류한 메일 중 알릴 것과 새 기준점. 기준점은 본 메일의 마지막 labeled_at."""
    rows = state.conn.execute(
        'SELECT * FROM messages WHERE labeled_at > ? ORDER BY labeled_at', (after,)).fetchall()
    now = datetime.now(timezone.utc)
    items = [_item(state, row, now) for row in rows
             if row['category'] in URGENT_CATEGORIES and row['urgency'] == '높음']
    return sorted(items, key=lambda i: -i.priority), (rows[-1]['labeled_at'] if rows else after)


def render_alert(items):
    head = f'🚨 급한 메일 {len(items)}통'
    lines = [head]
    for n, item in enumerate(items[:LIST_MAX], 1):
        what = '답장 필요' if item.category == LABEL_REPLY else '직접 확인 필요'
        extra = f', {item.draft}' if item.draft else ''
        lines.append(f'{n}. [{what}{extra}] {item.subject} — {item.sender}')
        lines.append(f'  {gmail_link(item.thread_id)}')
    if len(items) > LIST_MAX:
        lines.append(f'… 외 {len(items) - LIST_MAX}통')
    return '\n'.join(lines)


def send_alerts(service, state, settings, *, sender=None):
    """새로 분류한 급한 메일을 알린다. 보낸 수를 돌려준다. 처음이면 기준점만 잡는다.
    보내기에 실패하면 기준점을 옮기지 않아 다음 실행에서 다시 알린다."""
    sender = sender or deliver
    after = state.get_sync(ALERT_KEY)
    if after is None:
        state.set_sync(ALERT_KEY, datetime.now(timezone.utc).isoformat(timespec='seconds'))
        return 0
    items, mark = urgent(state, after=after)
    if items:
        fill_headers(service, Summary(since=after, items=items))
        sender(settings, render_alert(items))
    if mark != after:
        state.set_sync(ALERT_KEY, mark)
    return len(items)


def since_for(state, *, hours=None, now=None):
    now = now or datetime.now(timezone.utc)
    if hours is not None:
        return (now - timedelta(hours=hours)).isoformat(timespec='seconds'), f'최근 {hours}시간'
    last = state.get_sync(SYNC_KEY)
    if last:
        return last, '지난 요약 이후'
    return (now - timedelta(hours=24)).isoformat(timespec='seconds'), '최근 24시간'


def deliver(settings, text):
    """메신저로 보낸다 (hermes send, LLM 없음). 실패하면 RuntimeError."""
    # --quiet는 실패 이유까지 숨기므로 --json으로 받아 오류 문구를 남긴다
    command = [hermes_command(settings), '-p', settings.summary_profile, 'send',
               '--to', settings.summary_to, '--json', '--file', '-']
    out = subprocess.run(command, input=text, capture_output=True, text=True, timeout=60)
    try:
        result = json.loads(out.stdout or '{}')
    except ValueError:
        result = {}
    if out.returncode != 0 or not result.get('success'):
        reason = result.get('error') or ((out.stderr or out.stdout or '').strip().splitlines() or [''])[-1]
        raise RuntimeError(f'hermes send 실패 (종료 코드 {out.returncode}): {str(reason)[:200]}')


def due(settings, state, now=None):
    """정해진 요약 시각(REDA_SUMMARY_AT, 이 컴퓨터의 시각)이 지났고 오늘 아직 안 보냈는가."""
    if not settings.summary_at:
        return False
    local = (now or datetime.now(timezone.utc)).astimezone()
    hour, minute = (int(x) for x in settings.summary_at.split(':'))
    if (local.hour, local.minute) < (hour, minute):
        return False
    last = state.get_sync(SYNC_KEY)
    return not last or datetime.fromisoformat(last).astimezone().date() < local.date()
