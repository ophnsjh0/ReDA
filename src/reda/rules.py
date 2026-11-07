"""규칙 필터: 답장할 필요가 없는 게 분명한 메일을 LLM을 부르지 않고 `참고`로 분류한다.

일부러 쓰지 않는 신호
- `List-Unsubscribe`: 사내 Google Groups가 중계하는 모든 메일(고객 문의 포함)에 붙는다.
- `Precedence: list`: Google Groups가 중계 메일에 붙인다. `bulk`, `junk`만 대량 발송으로 본다.

그룹으로 중계된 메일은 From이 그룹 주소로 바뀐다. From이 내 주소(그룹 포함)면 진짜 발신자는
`X-Original-Sender`와 `Reply-To`에서 찾는다.
"""
from dataclasses import dataclass

from reda.gmail.client import LABEL_FYI
from reda.mail import address_of

# 자동 발송 전용 주소의 로컬 부분. 정확히 같거나 '-', '.', '_', '+'로 이어지면 해당한다.
NO_REPLY_LOCALS = ('no-reply', 'noreply', 'no_reply', 'do-not-reply', 'donotreply',
                   'do_not_reply', 'mailer-daemon', 'postmaster')
# 사람이 답장을 기다리지 않는 마케팅 발신자
MARKETING_LOCALS = ('marketing', 'newsletter', 'news', 'promo', 'promotion', 'events')
BULK_PRECEDENCE = ('bulk', 'junk')
_SEPARATORS = ('-', '.', '_', '+')


@dataclass(frozen=True)
class RuleResult:
    category: str
    urgency: str
    reason: str


def _fyi(reason):
    return RuleResult(LABEL_FYI, '낮음', reason)


def _matches(local, names):
    return any(local == n or local.startswith(tuple(n + s for s in _SEPARATORS)) for n in names)


def authors(mail, my_addresses):
    """발신자 판정에 쓸 주소들. 그룹 중계 메일이면 진짜 발신자 쪽을 본다."""
    sender = mail.sender_address
    if sender and sender in my_addresses:
        candidates = [mail.original_sender, mail.reply_to]
    else:
        candidates = [mail.sender, mail.original_sender]
    result = []
    for value in candidates:
        addr = address_of(value)
        if addr and addr not in result:
            result.append(addr)
    return result


def check(mail, settings):
    """규칙에 걸리면 RuleResult, 아니면 None(분류기로 보낸다)."""
    my = set(settings.my_addresses)

    subject = (mail.subject or '').lower()
    for keyword in settings.exclude_subjects:
        if keyword.lower() in subject:
            return _fyi(f'제목 키워드 "{keyword}"')

    for addr in authors(mail, my):
        local = addr.split('@', 1)[0]
        if _matches(local, NO_REPLY_LOCALS):
            return _fyi(f'자동 발송 주소 "{local}@"')
        if _matches(local, MARKETING_LOCALS):
            return _fyi(f'마케팅 발신자 "{local}@"')

    if mail.precedence in BULK_PRECEDENCE:
        return _fyi(f'대량 발송 헤더 (Precedence: {mail.precedence})')
    if mail.auto_submitted and mail.auto_submitted != 'no':
        return _fyi(f'자동 생성 메일 (Auto-Submitted: {mail.auto_submitted})')

    if my:
        in_to = any(a in my for a in mail.to)
        in_cc = any(a in my for a in mail.cc)
        if in_cc and not in_to:
            return _fyi('참조(Cc)로만 받음')
    return None
