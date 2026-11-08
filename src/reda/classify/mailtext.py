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
