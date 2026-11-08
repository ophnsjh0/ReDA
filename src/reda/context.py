"""답장 초안에 쓸 맥락 모으기 (3단계 1번). 에이전트에게는 모두 마스킹해서 준다.

- 스레드: 메일마다 새로 쓴 부분만(인용 제거), 보낸 사람, 날짜, 첨부 이름
- 같은 고객사의 이전 메일: Gmail 검색, 최근 6개월(결정 3-3). 검색 범위(도메인)는 코드가 정한다.
  에이전트가 다른 고객사 메일을 찾아 초안에 옮기지 못하게 하려는 것이다 (프롬프트 인젝션 대비)
- 내 말투 표본: 보낸편지함의 최근 답장

메일 본문은 믿을 수 없는 입력이다. 여기서 만드는 내용은 자료일 뿐이고, 에이전트는 그 안의 지시를 따르지
않는다 (SOUL.md).
"""
from dataclasses import dataclass, field

from reda import identity as ident
from reda.classify.mailtext import customer_of
from reda.gmail import client
from reda.mail import Mail, address_of
from reda.mask import Masker

MESSAGE_CHARS = 4000        # 메일 한 통에서 보여 줄 새로 쓴 부분
HISTORY_MONTHS = 6          # 결정 3-3
HISTORY_MAX = 10
SAMPLE_COUNT = 5
SAMPLE_CHARS = 1200


def gmail_link(thread_id):
    return f'https://mail.google.com/mail/u/0/#all/{thread_id}'


def attachment_names(payload):
    names = []
    stack = [payload or {}]
    while stack:
        part = stack.pop()
        if part.get('filename'):
            names.append(part['filename'])
        stack.extend(part.get('parts', []) or [])
    return names


def _clip(text, limit):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit] + '\n(뒷부분 생략)'


@dataclass
class ThreadContext:
    """에이전트에게 보여 준 한 스레드의 맥락. 같은 Masker로 가렸으므로 초안을 저장할 때 되돌릴 수 있다."""
    thread_id: str
    masker: Masker
    reply_to: dict                       # 답장할 원래 메일 (Gmail API 응답, 마스킹 전)
    customer: str | None
    domain: str                          # 이전 메일 검색 범위 (상대방 도메인)
    known: dict = field(default_factory=dict)   # message_id → {date, from, subject, thread_id}

    def remember(self, mail):
        self.known[mail.id] = {'date': mail.date, 'from': mail.sender, 'subject': mail.subject,
                               'thread_id': mail.thread_id}


def _counterpart_domain(mail, identity):
    """상대방 도메인: 원래 보낸 사람(그룹 중계면 진짜 보낸 사람)의 도메인. 우리 회사 도메인이면 ''."""
    sender = address_of(mail.original_sender) or mail.sender_address
    if '@' not in sender:
        return ''
    domain = sender.rsplit('@', 1)[1]
    own = {a.split('@', 1)[1] for a in identity.personal | identity.groups if '@' in a}
    return '' if domain in own else domain


def load_thread(service, thread_id, settings, identity):
    """스레드를 가져와 ThreadContext와 에이전트용(마스킹) 내용을 돌려준다."""
    thread = client.get_thread(service, thread_id)
    messages = thread.get('messages', [])
    if not messages:
        raise ValueError(f'스레드에 메일이 없음: {thread_id}')
    mails = [(m, Mail.from_gmail(m)) for m in messages]
    # 답장할 메일: 내가 보내지 않은 가장 최근 메일
    theirs = [(m, mail) for m, mail in mails if 'SENT' not in mail.label_ids]
    target_raw, target = (theirs or mails)[-1]
    ctx = ThreadContext(thread_id=thread_id, masker=Masker(), reply_to=target_raw,
                        customer=customer_of(target, settings.customer_domains),
                        domain=_counterpart_domain(target, identity))
    mask = ctx.masker.mask
    shown = []
    for raw, mail in mails:
        ctx.remember(mail)
        latest, _ = ident.split_quoted(mail.body or '')
        shown.append({
            'message_id': mail.id,
            'date': mail.date,
            'from': mask(mail.sender),
            'to': [mask(a) for a in mail.to],
            'cc': [mask(a) for a in mail.cc],
            'sent_by_me': 'SENT' in mail.label_ids,
            'text': mask(_clip(latest, MESSAGE_CHARS)),
            'attachments': attachment_names(raw.get('payload')),
        })
    view = {
        'thread_id': thread_id,
        'subject': mask(target.subject),
        'customer': ctx.customer,
        'reply_to_message_id': target.id,
        'link': gmail_link(thread_id),
        'messages': shown,
    }
    return ctx, view


def search_history(service, ctx, query='', *, months=HISTORY_MONTHS, max_results=HISTORY_MAX):
    """같은 상대방 도메인과 주고받은 이전 메일 (이 스레드 제외). 결과도 같은 Masker로 가린다."""
    if not ctx.domain:
        return []
    words = ' '.join(w for w in (query or '').split() if ':' not in w)[:200]  # 검색 연산자는 막는다
    q = f'(from:{ctx.domain} OR to:{ctx.domain}) newer_than:{months * 30}d {words}'.strip()
    refs = client.execute(service.users().messages().list(userId='me', q=q,
                                                          maxResults=max_results * 2))
    found = []
    for ref in refs.get('messages', []):
        if ref.get('threadId') == ctx.thread_id:
            continue
        mail = Mail.from_gmail(client.get_message(service, ref['id']))
        ctx.remember(mail)
        latest, _ = ident.split_quoted(mail.body or '')
        found.append({
            'message_id': mail.id,
            'date': mail.date,
            'from': ctx.masker.mask(mail.sender),
            'subject': ctx.masker.mask(mail.subject),
            'text': ctx.masker.mask(_clip(latest, 800)),
            'link': gmail_link(mail.thread_id),
        })
        if len(found) >= max_results:
            break
    return found


def writing_samples(service, *, count=SAMPLE_COUNT):
    """내가 최근에 보낸 답장 (새로 쓴 부분만, 마스킹). 말투와 서명을 따라 하는 데 쓴다."""
    refs = client.execute(service.users().messages().list(
        userId='me', q='in:sent newer_than:90d subject:re', maxResults=count * 2))
    masker = Masker()
    samples = []
    for ref in refs.get('messages', []):
        mail = Mail.from_gmail(client.get_message(service, ref['id']))
        latest, _ = ident.split_quoted(mail.body or '')
        if len(latest) < 20:
            continue
        samples.append({'subject': masker.mask(mail.subject),
                        'text': masker.mask(_clip(latest, SAMPLE_CHARS))})
        if len(samples) >= count:
            break
    return samples
