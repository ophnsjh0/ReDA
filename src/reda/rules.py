"""규칙 필터: 답장할 필요가 없는 게 분명한 메일을 LLM을 부르지 않고 `참고`로 분류한다.

일부러 쓰지 않는 신호
- `List-Unsubscribe`: 사내 Google Groups가 중계하는 모든 메일(고객 문의 포함)에 붙는다.
- `Precedence: list`: Google Groups가 중계 메일에 붙인다. `bulk`, `junk`만 대량 발송으로 본다.

그룹으로 중계된 메일은 From이 그룹 주소로 바뀐다. From이 내 주소(그룹 포함)면 진짜 발신자는
`X-Original-Sender`와 `Reply-To`에서 찾는다. 내가 속한 그룹 주소는 설정(REDA_MY_GROUPS)에 적은
것에 더해 메일 헤더(List-Id, 바뀐 From)로도 알아낸다 (`mail.group_addresses`).

참조(Cc)로만 받은 메일은 `참고`로 끝내지만, 본문 인사말이 나를 부르면 분류기로 보낸다 (결정 2-5).
"""
from dataclasses import dataclass

from reda import identity as ident
from reda.gmail.client import LABEL_FYI
from reda.mail import address_of

# 자동 발송 전용 주소의 로컬 부분. 정확히 같거나 '-', '.', '_', '+'로 이어지면 해당한다.
NO_REPLY_LOCALS = ('no-reply', 'noreply', 'no_reply', 'do-not-reply', 'donotreply',
                   'do_not_reply', 'mailer-daemon', 'postmaster')
# 알림 발송 주소. 대부분 자동 발송이지만 회신으로 케이스를 갱신하는 티켓 시스템도 같은 이름을 쓴다.
# 그래서 발신자 로컬 부분이 정확히 같을 때만, 고객사 도메인이 아닐 때만 쓴다. 반송 주소(Return-Path)
# 판정에는 쓰지 않는다 (`alert-tac@` 같은 그룹의 반송 주소 `alert-tac+bnc…@`가 걸린다)
NOTIFY_LOCALS = ('alert', 'alerts', 'notification', 'notifications', 'notify')
# 반송 주소(Return-Path)가 정확히 이 이름이면 발송 시스템이 보낸 메일이다.
# `bounce(s)+…@`는 쓰지 않는다: Microsoft 365(SRS)나 SendGrid·Mailgun을 거친 사람의 메일에도 붙는다
BOUNCE_LOCALS = ('mailer',)
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


def _is_customer(domain, settings):
    return any(domain == d or domain.endswith('.' + d) for d in settings.customer_domains)


def check(mail, settings, identity=None):
    """규칙에 걸리면 RuleResult, 아니면 None(분류기로 보낸다).

    identity를 주지 않으면 설정의 주소만으로 판정한다 (이름이 없어 인사말은 보지 않는다).
    """
    identity = identity or ident.Identity.from_settings(settings)
    my = set(identity.personal) | identity.groups_for(mail)

    subject = (mail.subject or '').lower()
    for keyword in settings.exclude_subjects:
        if keyword.lower() in subject:
            return _fyi(f'제목 키워드 "{keyword}"')

    for addr in authors(mail, my):
        local, _, domain = addr.partition('@')
        if _matches(local, NO_REPLY_LOCALS):
            return _fyi(f'자동 발송 주소 "{local}@"')
        if local in NOTIFY_LOCALS and not _is_customer(domain, settings):
            return _fyi(f'자동 발송 주소 "{local}@"')
        if _matches(local, MARKETING_LOCALS):
            return _fyi(f'마케팅 발신자 "{local}@"')

    bounce_local = mail.return_path.split('@', 1)[0]
    if mail.return_path and (bounce_local in BOUNCE_LOCALS
                             or _matches(bounce_local, NO_REPLY_LOCALS)):
        return _fyi(f'발송 시스템 반송 주소 "{bounce_local}@"')

    if mail.precedence in BULK_PRECEDENCE:
        return _fyi(f'대량 발송 헤더 (Precedence: {mail.precedence})')
    if mail.auto_submitted and mail.auto_submitted != 'no':
        return _fyi(f'자동 생성 메일 (Auto-Submitted: {mail.auto_submitted})')

    mode = identity.recipient_mode(mail)
    if mode in (ident.PERSONAL_CC, ident.GROUP_CC) and not ident.greets_me(mail.body, identity):
        return _fyi('참조(Cc)로만 받음' if mode == ident.PERSONAL_CC else '그룹이 참조(Cc)로만 받음')
    if (mode == ident.HIDDEN and mail.to
            and all(_matches(a.split('@', 1)[0], NO_REPLY_LOCALS) for a in mail.to)):
        return _fyi('받는사람이 자동 발송 주소뿐 (숨은참조 일괄 발송)')
    return None
