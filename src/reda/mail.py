"""Gmail 메시지(API 응답)를 규칙 필터와 분류기가 쓰는 형태로 바꾼다."""
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr

from reda.gmail.client import extract_body, get_header

def parse_addresses(*values):
    """주소 헤더 값들에서 소문자 주소 목록을 뽑는다.

    getaddresses는 따옴표 안의 쉼표('"Xu, Chengri" <x@a.com>')를 처리하지만 Outlook식 `;`
    구분은 나누지 않으므로 먼저 나눈다.
    """
    fields = []
    for value in values:
        fields.extend((value or '').split(';'))
    return [addr.strip().lower() for _, addr in getaddresses(fields) if '@' in addr]


def address_of(value):
    """'이름 <a@b.com>' → 'a@b.com'"""
    return parseaddr(value or '')[1].strip().lower()


@dataclass
class Mail:
    id: str
    thread_id: str
    internal_date: int                   # 밀리초. 오래된 메일부터 처리하는 데 쓴다
    label_ids: tuple
    sender: str                          # From 헤더 원문
    reply_to: str
    original_sender: str                 # X-Original-Sender (그룹 중계 메일의 진짜 발신자)
    to: list = field(default_factory=list)
    cc: list = field(default_factory=list)
    subject: str = ''
    date: str = ''
    precedence: str = ''
    auto_submitted: str = ''
    list_id: str = ''                    # 그룹(메일링 리스트)으로 받은 메일. 예: '<team.corp.example.com>'
    return_path: str = ''
    body: str = ''                       # format='full'일 때만 채워진다

    @classmethod
    def from_gmail(cls, message):
        h = lambda name: get_header(message, name)  # noqa: E731
        payload = message.get('payload', {})
        return cls(
            id=message['id'],
            thread_id=message.get('threadId', ''),
            internal_date=int(message.get('internalDate') or 0),
            label_ids=tuple(message.get('labelIds', [])),
            sender=h('From'),
            reply_to=h('Reply-To'),
            original_sender=h('X-Original-Sender'),
            to=parse_addresses(h('To')),
            cc=parse_addresses(h('Cc')),
            subject=h('Subject'),
            date=h('Date'),
            precedence=h('Precedence').strip().lower(),
            auto_submitted=h('Auto-Submitted').strip().lower(),
            list_id=h('List-Id').strip().lower(),
            return_path=address_of(h('Return-Path')),
            body=extract_body(payload),  # format='metadata'면 본문이 없어 ''
        )

    @property
    def sender_address(self):
        return address_of(self.sender)


def group_addresses(mail, my_addresses):
    """이 메일을 나에게 전달한 그룹 주소. 설정에 그룹 주소를 적지 않아도 헤더로 알아낸다.

    - List-Id `<team.corp.example.com>`이고 내 주소 도메인이 corp.example.com이면 team@corp.example.com
    - 그룹 중계로 From이 바뀐 메일(X-Original-Sender가 From과 다름)이면 From 주소가 그룹 주소다
    """
    groups = set()
    if mail.list_id:
        name = mail.list_id.split('<')[-1].strip('> ')
        # 내 도메인이 여럿이면(corp.example.com, example.com) 가장 긴 도메인 하나만 쓴다
        domains = [d for d in {a.split('@', 1)[1] for a in my_addresses if '@' in a}
                   if name.endswith('.' + d)]
        if domains:
            domain = max(domains, key=len)
            groups.add(f'{name[:-len(domain) - 1]}@{domain}')
    original = address_of(mail.original_sender)
    if original and mail.sender_address and original != mail.sender_address:
        groups.add(mail.sender_address)
    return groups

