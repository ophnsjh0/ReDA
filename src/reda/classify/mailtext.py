"""분류기에 보낼 메일 설명문. 어느 제공자로 보내든 같은 내용을 쓰고, 모두 마스킹한다."""
from reda import identity as ident
from reda.mail import address_of
from reda.mask import Masker

MAX_BODY_CHARS = 8000


_NOTES = {
    ident.PERSONAL_TO: '받는사람(To)에 이 엔지니어 개인 주소가 있음',
    ident.GROUP_TO: '이 엔지니어 개인 주소 없이 소속 그룹 주소로 받음 (받는사람 To)',
    ident.PERSONAL_CC: '이 엔지니어 개인 주소가 참조(Cc)로 받음',
    ident.GROUP_CC: '소속 그룹 주소가 참조(Cc)로만 받음',
    ident.HIDDEN: '받는사람·참조에 이 엔지니어 주소가 없음 (숨은참조나 다른 경로)',
}


def recipient_note(mail, identity):
    if not (identity.personal or identity.groups_for(mail)):
        return '알 수 없음'
    return _NOTES[identity.recipient_mode(mail)]


def customer_of(mail, customer_domains):
    for value in (mail.sender, mail.original_sender, mail.reply_to):
        addr = address_of(value)
        domain = addr.rsplit('@', 1)[-1] if '@' in addr else ''
        if not domain:
            continue
        for known, name in customer_domains.items():
            if domain == known or domain.endswith('.' + known):
                return name
    return None


def describe(mail, settings, masker=None, identity=None):
    """보낸사람·수신 방식·제목·본문을 한 덩어리 텍스트로 만들고 마스킹한다."""
    masker = masker or Masker()
    identity = identity or ident.Identity.from_settings(settings)
    body = mail.body or ''
    truncated = len(body) > MAX_BODY_CHARS
    if truncated:
        body = body[:MAX_BODY_CHARS]
    customer = customer_of(mail, settings.customer_domains)
    lines = [
        f'보낸사람: {mail.sender}',
        f'원래 보낸사람(그룹 중계): {mail.original_sender}' if mail.original_sender else None,
        f'고객사: {customer}' if customer else None,
        f'수신 방식: {recipient_note(mail, identity)}',
        f'제목: {mail.subject}',
        '',
        '본문:',
        body,
        '(본문이 길어 뒷부분을 생략함)' if truncated else None,
    ]
    return masker.mask('\n'.join(line for line in lines if line is not None))


LATEST_CHARS = 6000      # 이번 메일에 새로 쓴 부분
EARLIER_CHARS = 2000     # 인용된 이전 대화 (맥락용 앞부분)
_FORWARD_PREFIX = ('fwd:', 'fw:', '전달:')


def is_forward(mail):
    subject = (mail.subject or '').strip().lower()
    return subject.startswith(_FORWARD_PREFIX) or 'forwarded message' in (mail.body or '').lower()


def sender_kind(mail, settings, identity):
    """보낸 사람 구분 (코드가 아는 사실): 고객사 이름 / 사내 / 외부."""
    customer = customer_of(mail, settings.customer_domains)
    if customer:
        return f'고객사 ({customer})'
    own = {a.split('@', 1)[1] for a in identity.personal | identity.groups if '@' in a}
    sender = address_of(mail.original_sender) or mail.sender_address
    domain = sender.rsplit('@', 1)[-1] if '@' in sender else ''
    if domain and any(domain == d or domain.endswith('.' + d) for d in own):
        return '사내 (같은 회사 사람이나 팀 메일)'
    return '외부'


def structured_state(mail, settings, identity, masker=None, *, reader=''):
    """Jev처럼 state를 JSON으로 받는 분류기용. 이름 붙은 필드로 나누고 모두 마스킹한다.

    빈 필드는 넣지 않는다 (빈 목록도 신호로 읽힐 수 있다).
    """
    masker = masker or Masker()
    latest, earlier = ident.split_quoted(mail.body or '')
    mail_state = {
        'sender': mail.sender,
        'original_sender': mail.original_sender,
        'sender_kind': sender_kind(mail, settings, identity),
        'received_as': recipient_note(mail, identity),
        'subject': mail.subject,
        'is_forward': is_forward(mail),
        'latest_message': latest[:LATEST_CHARS] + ('\n(뒷부분 생략)' if len(latest) > LATEST_CHARS else ''),
        'earlier_messages': earlier[:EARLIER_CHARS] + ('\n(뒷부분 생략)' if len(earlier) > EARLIER_CHARS else ''),
    }
    masked = {k: masker.mask(v) if isinstance(v, str) else v
              for k, v in mail_state.items() if v not in ('', None)}
    if 'latest_message' not in masked:
        masked['latest_message'] = '(본문 없음)'
    state = {'mail': masked}
    if reader:
        state['reader'] = reader
    return state
