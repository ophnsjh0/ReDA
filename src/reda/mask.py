"""LLM에 보내기 전에 고객사 IP·계정 정보와 개인정보를 가린다.

가리는 것 (결정 2-4)
- IP: IPv4, IPv6
- 계정·비밀: `계정:`·`비밀번호:`·`ID/PW:` 형태, 장비 설정의 사용자·비밀번호·SNMP community
- 개인정보: 전화·휴대폰 번호, 주민등록번호, 카드 번호, 계좌 번호(`계좌`·`입금` 같은 말 뒤에 올 때),
  메일 주소의 앞부분(`[EMAIL_1]@customer-a.com`. 고객사·제조사를 구분하려고 도메인은 남긴다)
- 사람 이름과 회사 이름은 가리지 않는다.

같은 값은 같은 표식(`[IP_1]`)으로 바꾸고, 대응표는 Masker 객체 안에만 둔다(LLM으로 보내지 않는다).
초안을 저장할 때 `unmask`로 원래 값을 되돌린다.

넓게 가리는 쪽으로 만들었다. 네 자리로 된 소프트웨어 버전처럼 IPv4 모양인 값도 가리지만,
`unmask`로 되돌리므로 초안에는 원래 값이 남는다.
"""
import ipaddress
import re

# 계정·비밀 값 앞에 오는 이름. `이름: 값`, `이름=값` 형태만 본다.
_ACCOUNT_KEYS = r'id|user|username|userid|user[-_ ]id|login|account|acct|계정|아이디|사용자\s*id'
_SECRET_KEYS = (r'pw|pwd|passwd|password|pass|passphrase|passcode|secret|community|psk|'
                r'pre-shared-key|api[-_ ]?key|token|비밀번호|비번|패스워드|암호')
_VALUE = r'''["']?([^\s,;"'<>()\[\]]+)["']?'''
_SEP = r'\s*[:=：]\s*'

# 'ID/PW: admin/admin123'
_RE_ID_PW = re.compile(rf'(?i)(?<![\w])(?:id|계정|아이디)\s*/\s*(?:pw|pwd|password|비밀번호|비번)'
                       rf'{_SEP}([^\s/,;]+)\s*/\s*([^\s,;]+)')
_RE_ACCOUNT = re.compile(rf'(?i)(?<![\w-])(?:{_ACCOUNT_KEYS}){_SEP}{_VALUE}')
_RE_SECRET = re.compile(rf'(?i)(?<![\w-])(?:{_SECRET_KEYS}){_SEP}{_VALUE}')
# 장비 설정 형태 (Cisco 등)
_RE_CFG_USER = re.compile(r'(?i)\busername\s+(\S+)\s+(?:privilege\s+\d+\s+)?'
                          r'(?:password|secret)\s+(?:\d\s+)?(\S+)')
_RE_CFG_SECRET = re.compile(r'(?i)\b(?:password|secret|key)\s+\d\s+(\S+)')
_RE_CFG_COMMUNITY = re.compile(r'(?i)\bsnmp-server\s+community\s+(\S+)')

_RE_IPV4 = re.compile(r'(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?!\w|\.\d)')
_RE_IPV6 = re.compile(r'(?<![\w:.])((?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4})(?![\w:])')
# 개인정보
_RE_EMAIL = re.compile(r'(?<![\w.%+-])([A-Za-z0-9][A-Za-z0-9._%+-]*)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*'
                       r'\.[A-Za-z]{2,})')
_RE_PHONE = re.compile(
    r'(?<![\w.+-])('
    r'\+82[-. ]?\(?0?\d{1,2}\)?[-. ]?\d{3,4}[-. ]?\d{4}'        # +82 10-1234-5678
    r'|01[016789][-. ]?\d{3,4}[-. ]?\d{4}'                       # 010-1234-5678, 01012345678
    r'|\(?0\d{1,2}\)?[-. )]\s?\d{3,4}[-. ]\d{4}'                  # 02-123-4567, (031) 123-4567
    r'|1[5-9]\d{2}-\d{4}'                                         # 1588-1234
    r')(?![\w-]|\.\d)')
_RE_RRN = re.compile(r'(?<![\w-])(\d{6}-[1-8]\d{6})(?![\w-])')
_RE_CARD = re.compile(r'(?<![\w-])(\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}|\d{4}[- ]\d{6}[- ]\d{5})(?![\w-])')
_RE_BANK = re.compile(r'(?:계좌(?:\s*번호)?|입금\s*계좌|account\s*(?:no\.?|number))\s*[:：]?\s*'
                      r'(?:[가-힣A-Za-z]+(?:은행)?\s*)?(\d[\d-]{8,18}\d)', re.IGNORECASE)

_RE_TOKEN = re.compile(r'\[(?:IP|ACCOUNT|SECRET|EMAIL|PHONE|RRN|CARD|BANK)_\d+\]')


def _luhn(number):
    digits = [int(d) for d in re.sub(r'\D', '', number)][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2)
                for i, d in enumerate(digits))
    return total % 10 == 0


def _is_ipv4(value):
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def _is_ipv6(value):
    if not re.search(r'[0-9A-Fa-f]', value):
        return False
    try:
        ipaddress.IPv6Address(value)
        return True
    except ValueError:
        return False


class Masker:
    def __init__(self):
        self._token_of = {}     # (종류, 원래 값) → 표식
        self._value_of = {}     # 표식 → 원래 값
        self._counts = {}

    @property
    def mapping(self):
        """표식 → 원래 값. 로컬에만 둔다."""
        return dict(self._value_of)

    def _token(self, kind, value):
        key = (kind, value)
        if key not in self._token_of:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            token = f'[{kind}_{self._counts[kind]}]'
            self._token_of[key] = token
            self._value_of[token] = value
        return self._token_of[key]

    def _replace_groups(self, pattern, text, kinds):
        """pattern의 각 그룹 값을 kinds 순서의 표식으로 바꾼다. 나머지 글자는 그대로 둔다."""
        def repl(match):
            out, last = [], match.start()
            for index, kind in enumerate(kinds, 1):
                start, end = match.span(index)
                value = match.group(index)
                if _RE_TOKEN.fullmatch(value):
                    continue
                out.append(match.string[last:start])
                out.append(self._token(kind, value))
                last = end
            out.append(match.string[last:match.end()])
            return ''.join(out)
        return pattern.sub(repl, text)

    def mask(self, text):
        if not text:
            return text or ''
        text = self._replace_groups(_RE_ID_PW, text, ('ACCOUNT', 'SECRET'))
        text = self._replace_groups(_RE_CFG_USER, text, ('ACCOUNT', 'SECRET'))
        text = self._replace_groups(_RE_CFG_SECRET, text, ('SECRET',))
        text = self._replace_groups(_RE_CFG_COMMUNITY, text, ('SECRET',))
        text = self._replace_groups(_RE_SECRET, text, ('SECRET',))
        text = self._replace_groups(_RE_ACCOUNT, text, ('ACCOUNT',))
        text = _RE_IPV6.sub(
            lambda m: self._token('IP', m.group(1)) if _is_ipv6(m.group(1)) else m.group(0), text)
        text = _RE_IPV4.sub(
            lambda m: self._token('IP', m.group(1)) if _is_ipv4(m.group(1)) else m.group(0), text)
        text = self._replace_groups(_RE_RRN, text, ('RRN',))
        text = _RE_CARD.sub(
            lambda m: self._token('CARD', m.group(1)) if _luhn(m.group(1)) else m.group(0), text)
        text = self._replace_groups(_RE_BANK, text, ('BANK',))
        text = self._replace_groups(_RE_PHONE, text, ('PHONE',))
        text = self._replace_groups(_RE_EMAIL, text, ('EMAIL',))  # 앞부분만, 도메인은 남긴다
        return text

    def unmask(self, text):
        return _RE_TOKEN.sub(lambda m: self._value_of.get(m.group(0), m.group(0)), text or '')
