"""사용자 정체성: 내 주소(개인·그룹)와 이름 프로필. 팀 메일에서 누가 담당인지 가리는 데 쓴다.

결정 2-5: 수신 방식을 개인 To·그룹 To·개인 Cc·그룹 Cc·숨은참조로 나눈다. 개인 주소 없이 그룹 주소로
         받은 요청은 담당자 단서(인사말이 나를 부름, 내가 답한 스레드)가 있을 때만 `답장필요`다.
결정 2-6: 인사말이 부르는 사람은 분류기가 추출만 하고(`Classification.addressee`), 이름 프로필과의
         대조는 이 모듈이 한다. 사용자 이름을 분류기에 보내지 않는다.

이름 대조는 "정확히 같은 이름"만 나로 본다. 이름 일부가 겹치는 다른 사람(홍길동 ↔ 김길동,
김진 ↔ 김진우, Gildong Hong ↔ Minsu Hong)을 나로 보면 남의 메일이 `답장필요`로 남거나
참조 규칙을 빠져나간다.

이름 프로필은 설정(개발용)과 Gmail 표시 이름에서 만들고 상태 DB `identity_aliases`에 남긴다.
쓰면서 배운 별칭도 같은 표에 쌓인다 (`기획안/구현계획.md` "사용자 정보 저장").
"""
import re
from dataclasses import dataclass, field, replace
from datetime import date
from email.utils import parseaddr
from functools import cached_property

from googleapiclient.errors import HttpError

from reda.gmail.client import (LABEL_CHECK, LABEL_FYI, LABEL_REPLY, get_header,
                               get_message_metadata)
from reda.mail import group_addresses

# 수신 방식. 앞쪽이 우선한다 (개인 주소가 To에 있으면 그룹이 어디 있든 개인 To)
PERSONAL_TO, GROUP_TO, PERSONAL_CC, GROUP_CC, HIDDEN = (
    'personal_to', 'group_to', 'personal_cc', 'group_cc', 'hidden')
MODE_LABELS = {PERSONAL_TO: '개인 To', GROUP_TO: '그룹 To', PERSONAL_CC: '개인 Cc',
               GROUP_CC: '그룹 Cc', HIDDEN: '숨은참조·기타'}
# 그룹 주소로 받은 수신 방식. 이 메일의 답장필요는 담당자 단서가 있어야 한다.
# HIDDEN(내 주소가 안 보임)은 숨은참조나 설정하지 않은 별칭 주소일 수 있어 여기 넣지 않는다
GROUP_MODES = (GROUP_TO, GROUP_CC)

# 인사말 대상 판정
ME, OTHER, NONE, UNKNOWN = 'me', 'other', 'none', 'unknown'

TITLES = ('부사장', '본부장', '파트장', '연구원', '매니저', '사장', '실장', '팀장', '부장', '차장',
          '과장', '대리', '주임', '사원', '이사', '상무', '전무', '대표', '책임', '선임', '수석',
          '프로', '위원')
_HONORIFICS = ('께서는', '께서', '님께', '님', '께', '씨', '귀하')
# 특정한 사람이 아닌 인사말
_GENERIC = {'담당자', '관계자', '여러분', '모두', '각위', '팀원', '엔지니어', '고객', '귀사',
            'all', 'team', 'everyone', 'sir', 'madam', 'sirs', 'hi', 'hello', 'dear'}
_LATIN_PREFIX = re.compile(r'^(?:(?:dear|hi|hello)\s+)?(?:(mr|mrs|ms|dr)\.?\s+)?', re.I)
_HANGUL = re.compile(r'[가-힣]')
_LATIN_NAME = re.compile(r'[A-Za-z]+(?:[ .\-]+[A-Za-z]+){0,2}')
_SPLIT = re.compile(r'\s*(?:,|/|·|&|;|\s및\s|\sand\s)\s*', re.I)
# 인용·전달이 시작되는 줄. 그 아래는 이전 메일이다
_QUOTE_START = re.compile(
    r'^(?:-{2,}\s*(?:original message|forwarded message|원본 메시지|전달된 메시지)|'
    r'(?:from|보낸\s*사람|보낸사람)\s*:|on .+ wrote:|.+님이 작성:|>)', re.I)
GREETING_LINES = 5


def _norm(text):
    return re.sub(r'[\s.\-_]', '', (text or '').lower())


def _words(text):
    return [w for w in re.split(r'[\s.\-_]+', (text or '').lower()) if w]


def _is_hangul(text):
    return bool(text) and all(_HANGUL.match(ch) for ch in text)


def _dedupe(items):
    return tuple(dict.fromkeys(i for i in items if i))


@dataclass(frozen=True)
class Identity:
    personal: frozenset = frozenset()   # 개인 주소
    groups: frozenset = frozenset()     # 설정한 그룹 주소 (헤더로 알아내는 그룹은 메일마다 더한다)
    full_names: tuple = ()              # 한글 전체 이름 ('홍길동')
    given_names: tuple = ()             # 한글 이름에서 성을 뺀 것 ('길동'). 님·직함과 함께일 때만
    surnames: tuple = ()                # 성 ('홍', 'hong'). 직함이나 Mr. 같은 호칭과 함께일 때만
    latin_names: tuple = ()             # 영문 이름과 별칭, 단어 튜플 (('gildong', 'hong'), ('gildong',))
    titles: tuple = ()                  # 직함 ('부장')
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_settings(cls, settings):
        """주소만 담은 프로필 (이름 없음). 분류기에 수신 방식을 알려 줄 때 쓴다."""
        groups = frozenset(settings.my_groups)
        return cls(personal=frozenset(settings.my_addresses) - groups, groups=groups)

    @property
    def has_names(self):
        return bool(self.full_names or self.given_names or self.latin_names)

    def groups_for(self, mail):
        key = ('groups', mail.id, mail.list_id, mail.sender, mail.original_sender)
        if key not in self._cache:
            self._cache.clear()               # 메일 한 통 분량만 기억한다
            self._cache[key] = frozenset(
                set(self.groups) | group_addresses(mail, self.personal | self.groups))
        return self._cache[key]

    def recipient_mode(self, mail):
        groups = self.groups_for(mail)
        personal = set(self.personal) - groups   # 그룹으로 드러난 주소는 개인 주소가 아니다
        if any(a in personal for a in mail.to):
            return PERSONAL_TO
        if any(a in groups for a in mail.to):
            return GROUP_TO
        if any(a in personal for a in mail.cc):
            return PERSONAL_CC
        if any(a in groups for a in mail.cc):
            return GROUP_CC
        return HIDDEN

    @cached_property
    def greeting_patterns(self):
        return _greeting_patterns(self)


# ------------------------------------------------------------ 이름 형태

def name_forms(display_name):
    """표시 이름 → (한글 전체 이름들, 한글 이름들, 성들, 영문 이름 단어 튜플들).

    '홍길동', 'Gildong Hong', '홍길동 (Gildong Hong)', '홍길동/기술1팀' 같은 형태를 받는다.
    영문 이름은 어느 단어가 성인지 모르므로 단어 하나만으로는 나로 보지 않는다 ('Hong'만으로는
    같은 성의 다른 사람과 구분되지 않는다). 두 단어 모두(어느 순서든) 또는 Mr. + 성일 때만 나다.
    """
    full, given, surnames, latin = [], [], [], []
    for part in re.split(r'[()/|,\[\]]', display_name or ''):
        part = part.strip()
        compact = part.replace(' ', '')
        if _is_hangul(compact) and 2 <= len(compact) <= 4:
            full.append(compact)
            if len(compact) == 3:            # 흔한 한 글자 성 + 두 글자 이름
                given.append(compact[1:])
                surnames.append(compact[0])
            elif len(compact) == 2:
                surnames.append(compact[0])
        elif _LATIN_NAME.fullmatch(part) and len(_words(part)) >= 2:
            words = tuple(_words(part))
            latin += [words, tuple(reversed(words))]
            surnames += [words[0], words[-1]]
    return _dedupe(full), _dedupe(given), _dedupe(surnames), _dedupe(latin)


def build(*, personal=(), groups=(), display_names=(), aliases=(), titles=()):
    full, given, surnames, latin = [], [], [], []
    for name in display_names:
        f, g, s, l_ = name_forms(name)
        full += f
        given += g
        surnames += s
        latin += l_
    for alias in aliases:                    # 별칭: 한글이면 이름(님·직함과 함께), 영문이면 그대로
        if _is_hangul(alias.replace(' ', '')):
            given.append(alias.replace(' ', ''))
        elif _words(alias):
            latin.append(tuple(_words(alias)))
    groups = frozenset(a.lower() for a in groups)
    return Identity(personal=frozenset(a.lower() for a in personal) - groups, groups=groups,
                    full_names=_dedupe(full), given_names=_dedupe(given),
                    surnames=_dedupe(surnames), latin_names=_dedupe(latin),
                    titles=_dedupe(_norm(t) for t in titles))


# ------------------------------------------------------------ 인사말 대상 대조

def _strip_suffix(text, suffixes):
    text = text.rstrip()
    for s in suffixes:
        if text.endswith(s) and len(text) > len(s):
            return text[:-len(s)].rstrip(), s
    return text, ''


def _parse_token(raw):
    """'Mr. Hong', '기술1팀 길동 부장님' → (이름 후보들, 직함, 영문 호칭이 붙었는지).

    이름 후보: 남은 글 전체, 마지막 단어, 마지막 두 단어 (앞에 붙은 부서 이름을 떼려는 것).
    """
    text = raw.strip()
    prefix = _LATIN_PREFIX.match(text)
    prefixed = bool(prefix and prefix.group(1))
    text = text[prefix.end():] if prefix else text
    while True:
        text, honorific = _strip_suffix(text, _HONORIFICS)
        if not honorific:
            break
    text, title = _strip_suffix(text, TITLES)
    if not text.strip() or text.strip() in _HONORIFICS or text.strip() in TITLES:
        return (), title, prefixed           # '부장님'처럼 직함만 있으면 특정한 사람이 아니다
    words = text.split()
    candidates = _dedupe([_norm(text), _norm(words[-1]), _norm(''.join(words[-2:]))])
    return candidates, title, prefixed


def _is_me(candidates, title, prefixed, identity):
    title_ok = not title or not identity.titles or title in identity.titles
    for name in candidates:
        if name in identity.full_names:
            return True                      # 전체 이름은 직함이 달라도 나 (직함이 바뀌었을 수 있다)
        if any(name == ''.join(words) for words in identity.latin_names):
            return True
        if title_ok and name in identity.given_names:
            return True
        if name in identity.surnames and ((title and title in identity.titles) or prefixed):
            return True                      # '홍 부장님', 'Mr. Hong'
    return False


def match_addressee(addressee, identity):
    """분류기가 추출한 인사말 대상 → ME / OTHER / NONE / UNKNOWN(이름 프로필 없음)."""
    specific = False
    for raw in _SPLIT.split(addressee or ''):
        if not raw.strip():
            continue
        candidates, title, prefixed = _parse_token(raw)
        if not candidates or candidates[0] in _GENERIC:
            continue                         # '부장님', '담당자님'처럼 특정한 사람이 아님
        specific = True
        if identity.has_names and _is_me(candidates, title, prefixed, identity):
            return ME
    if not specific:
        return NONE
    return OTHER if identity.has_names else UNKNOWN


def greeting_area(body, lines=GREETING_LINES):
    """본문 맨 앞의 몇 줄. 인용·전달된 이전 메일과 그 아래는 보지 않는다."""
    picked = []
    for line in (body or '').splitlines():
        line = line.strip()
        if not line:
            continue
        if _QUOTE_START.match(line):
            break
        picked.append(line[:200])
        if len(picked) >= lines:
            break
    return picked


def _greeting_patterns(identity):
    titles = '|'.join(TITLES)
    mine = '|'.join(re.escape(t) for t in identity.titles)
    # 한글 이름 뒤에 올 수 있는 것: 끝, 한글이 아닌 글자, 호칭, 직함 ('홍길동님', '홍길동 부장님')
    after = rf'(?=$|[^가-힣]|님|께|씨|귀하|\s*(?:{titles}))'
    patterns = []
    for f in identity.full_names:
        patterns.append(rf'(?<![가-힣]){re.escape(f)}{after}')
    for g in identity.given_names:
        # 이름 + (직함) + 님, 또는 이름 + 내 직함. 앞이 한글이면 다른 사람 이름의 일부다
        tail = rf'(?:\s*(?:{titles}))?\s*님' + (rf'|\s*(?:{mine})' if mine else '')
        patterns.append(rf'(?<![가-힣]){re.escape(g)}(?:{tail})')
    for words in identity.latin_names:
        patterns.append(r'\b' + r'[\s.\-]*'.join(re.escape(w) for w in words) + r'\b')
    for s in identity.surnames:
        if _is_hangul(s):
            if mine:
                patterns.append(rf'(?<![가-힣]){re.escape(s)}\s*(?:{mine})')
        else:
            patterns.append(rf'\b(?:mr|mrs|ms|dr)\.?\s*{re.escape(s)}\b')
    return [re.compile(p, re.I) for p in patterns]


def greets_me(body, identity):
    """본문 첫 몇 줄(인사말)이 나를 부르는가. 분류기를 부르기 전 규칙 필터에서도 쓴다."""
    if not identity.has_names:
        return False
    lines = greeting_area(body)
    # 이름 사이 공백만 지운 줄도 본다 ('홍 길동 부장님'). 단어 사이 공백은 그대로 둔다
    texts = lines + [re.sub(r'(?<![가-힣])([가-힣]) ([가-힣]{1,2})(?=\s|님|$)', r'\1\2', line)
                     for line in lines]
    return any(p.search(text) for p in identity.greeting_patterns for text in texts)


def addressee_match(classification, mail, identity):
    if greets_me(mail.body, identity):
        return ME
    return match_addressee(classification.addressee, identity)


# ------------------------------------------------------------ 담당자 판정 (결정 2-5)

def resolve(classification, mode, match, replied_in_thread):
    """그룹 주소로 받은 `답장필요`는 담당자 단서가 있을 때만 그대로 둔다.

    replied_in_thread: 이 스레드에 내가 답한 적이 있는지 돌려주는 함수 (필요할 때만 부른다).
    숨은참조·기타(HIDDEN)는 내 메일일 수 있으므로 `참고`로 내리지 않고, 다른 사람을 부르면
    `확인필요`로만 보낸다.
    """
    c = classification
    if c.category != LABEL_REPLY or match == ME:
        return c
    who = f' ({c.addressee})' if c.addressee else ''
    if mode == HIDDEN:
        if match == OTHER:
            return replace(c, category=LABEL_CHECK,
                           reason=f'내 주소가 보이지 않고 인사말이 다른 사람을 부름{who}. {c.reason}')
        return c
    if mode not in GROUP_MODES:
        return c
    if match == OTHER:
        if replied_in_thread():
            return replace(c, category=LABEL_CHECK,
                           reason=f'내가 답한 스레드지만 인사말이 다른 사람을 부름{who}. {c.reason}')
        return replace(c, category=LABEL_FYI,
                       reason=f'그룹으로 받은 요청이지만 인사말이 다른 사람을 부름{who}. {c.reason}')
    if replied_in_thread():
        return replace(c, reason=f'그룹으로 받은 요청, 내가 답한 적 있는 스레드. {c.reason}')
    return replace(c, category=LABEL_CHECK,
                   reason=f'그룹으로 받은 요청, 담당자 단서 없음. {c.reason}')


# ------------------------------------------------------------ 불러오기

def fetch_display_name(service):
    """내 표시 이름. Gmail 기본 보내는 주소(sendAs)의 이름, 비어 있으면 최근 보낸 메일 From의 이름.
    (Workspace에서는 sendAs 이름이 비어 있고 디렉터리 이름으로 보내는 경우가 많다.) 없으면 ''."""
    try:
        items = service.users().settings().sendAs().list(userId='me').execute().get('sendAs', [])
    except HttpError:
        items = []
    primary = next((s for s in items if s.get('isPrimary')), items[0] if items else {})
    name = (primary.get('displayName') or '').strip()
    if name:
        return name
    try:
        sent = service.users().messages().list(userId='me', q='in:sent', maxResults=1).execute()
        refs = sent.get('messages', [])
        if not refs:
            return ''
        message = get_message_metadata(service, refs[0]['id'], ('From',))
    except HttpError:
        return ''
    return parseaddr(get_header(message, 'From'))[0].strip().strip('"\'')


def load(settings, *, service=None, state=None, persist=False, today=None):
    """설정 + 상태 DB의 이름 프로필 (+ 아직 모르면 Gmail 표시 이름).

    persist=True면 알아낸 표시 이름과 설정의 이름·별칭을 상태 DB에 남긴다 (`reda poll`).
    표시 이름을 찾지 못했으면 그날은 다시 묻지 않는다 (15분마다 Gmail API를 부르지 않게).
    골든 세트 평가처럼 상태를 바꾸지 않을 때는 persist=False.
    """
    today = (today or date.today()).isoformat()
    display = settings.my_name
    source = 'config'
    if not display and state is not None:
        display = state.get_profile('display_name') or ''
        source = 'state'
    checked_today = state is not None and state.get_profile('display_name_checked') == today
    if not display and service is not None and not checked_today:
        display = fetch_display_name(service)
        source = 'gmail'
        if state is not None and persist and not display:
            state.set_profile('display_name_checked', today, source='gmail')
    titles = list(settings.my_titles)
    if not titles and state is not None:
        titles = [t for t in (state.get_profile('titles') or '').split(',') if t]
    aliases = list(settings.my_aliases)

    if state is not None and persist:
        if display and source != 'state':
            state.set_profile('display_name', display, source=source)
        if settings.my_titles:
            state.set_profile('titles', ','.join(settings.my_titles), source='config')
        for alias in aliases:
            state.upsert_alias(alias, 'alias', 'config')
        if display:
            state.upsert_alias(display, 'display_name', source)
    if state is not None:
        rejected = {r['alias'] for r in state.aliases('rejected')}
        for row in state.aliases('confirmed'):
            if row['kind'] == 'alias' and row['alias'] not in aliases:
                aliases.append(row['alias'])
        aliases = [a for a in aliases if a not in rejected]
        if display in rejected:
            display = ''
    return build(personal=settings.my_addresses, groups=settings.my_groups,
                 display_names=[display] if display else [], aliases=aliases, titles=titles)
