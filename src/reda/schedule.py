"""메일 속 일정 찾기 (9단계 2번). 날짜는 코드가 정하고, 어느 후보가 일정인지는 분류기(Jev)가 고른다.

- 문장마다 날짜 표현(절대 날짜, 오늘·내일, 이번 주·다음 주 요일, 영문 표현)을 찾아 메일을 받은 시각
  (한국 시각)을 기준으로 실제 날짜로 바꾼다. 연도가 없으면 받은 해로 보고, 두 달 넘게 지난 날짜면
  다음 해로 본다 (연말에 받은 "1월 3일").
- 시간: "오후 2시", "14:00", "2시 30분", "2pm", 범위("2시부터 4시까지", "14:00~16:00").
  오전·오후가 없는 1~6시는 오후로 본다 (업무 시간). 시간 없이 "오후"만 있으면 14:00~16:00 (결정 9-1).
- 날짜가 흐리면("다음 주쯤", "조만간", "next week") 날짜를 정하지 않고 `vague`로 표시한다.
  요일이 날짜와 맞지 않으면("11월 12일(수)"인데 12일이 목요일) `conflict`로 표시한다. 둘 다 제안하지
  않고 사용자에게 날짜를 묻는다.
- 근거 문장(원문)을 함께 남긴다.

시간대는 메일에 적힌 것(UTC, PST 같은 표시)이 있으면 그것, 없으면 한국 시각이다.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9), 'KST')
TZ_OFFSETS = {'KST': 9, 'JST': 9, 'UTC': 0, 'GMT': 0, 'CET': 1, 'CEST': 2,
              'PST': -8, 'PDT': -7, 'EST': -5, 'EDT': -4, 'SGT': 8}
# 여러 뜻인 시간대 (CST: 미국 중부 -6 / 중국 +8, IST: 인도 / 아일랜드 / 이스라엘) → KST로 두지 않고 묻는다
AMBIGUOUS_TZ = re.compile(r'\b(CST|IST)\b')

WEEKDAYS_KO = '월화수목금토일'
WEEKDAYS_EN = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
MONTHS_EN = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
AFTERNOON_DEFAULT = (time(14, 0), time(16, 0))      # 결정 9-1
MORNING_DEFAULT = (time(10, 0), time(12, 0))
PAST_LIMIT_DAYS = 60                                # 연도 없는 날짜가 이보다 지났으면 다음 해

_WD_KO = f'([{WEEKDAYS_KO}])(?:요일)?'
_WD_EN = r'(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|rsday|urday)?'
_MON_EN = (r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May|MAY)|june?|july?|aug(?:ust)?'
           r'|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?(?![a-z])')

DATE_PATTERNS = [
    # 2026-11-12, 2026.11.12, 2026/11/12, 2026년 11월 12일
    ('ymd', re.compile(r'(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?(?!\d)')),
    # 11월 12일
    ('md', re.compile(r'(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일')),
    # 11/12 (뒤에 숫자·점이 이어지면 IP·버전으로 보고 뺀다)
    ('md', re.compile(r'(?<![\d./])(\d{1,2})/(\d{1,2})(?![\d./])')),
    # 11.12(목), 11.12일 (점은 버전 번호와 헷갈리므로 요일 표시나 '일'이 붙을 때만)
    ('md', re.compile(r'(?<![\d.])(\d{1,2})\.(\d{1,2})(?=\s*(?:\(|일))')),
    # Nov 12, November 12th
    ('en_md', re.compile(rf'\b{_MON_EN}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b', re.I)),
    # 12 Nov
    ('en_dm', re.compile(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON_EN}\b', re.I)),
    ('rel', re.compile(r'(오늘|금일|내일|명일|익일|모레|글피)')),
    ('rel_en', re.compile(r'\b(today|tomorrow)\b', re.I)),
    # 이번 주 목요일, 다음 주 화요일, 차주 월요일, 다다음 주 수요일
    ('week_ko', re.compile(rf'(이번\s*주|금주|이번주|다음\s*주|차주|담주|다다음\s*주)\s*'
                           rf'([{WEEKDAYS_KO}])(?:요일|(?![가-힣]))')),
    ('week_en', re.compile(rf'\b(this|next)\s+{_WD_EN}\b', re.I)),
    # 목요일 (단독)
    ('wd_ko', re.compile(r'(?<![가-힣])([월화수목금토일])요일')),
    ('wd_en', re.compile(rf'\b(?:on\s+){_WD_EN}\b', re.I)),
]
# 날짜 옆의 요일 표시: "11월 12일(목)", "12월 1일 화요일", "Nov 16, Monday", "Monday, Nov 16".
# 따로 세지 않고 그 날짜의 요일 검사에 쓴다 (따로 세면 '한 문장에 날짜가 여러 개'가 된다)
ABSOLUTE_KINDS = ('ymd', 'md', 'en_md', 'en_dm')
WEEKDAY_AFTER = re.compile(rf'^\s*(?:\(\s*(?:[{WEEKDAYS_KO}](?:요일)?|{_WD_EN})\s*\)'
                           rf'|,?\s*(?:[{WEEKDAYS_KO}]요일|{_WD_EN}\b))', re.I)
WEEKDAY_BEFORE = re.compile(rf'(?:(?<![가-힣])[{WEEKDAYS_KO}]요일|\b{_WD_EN})\s*,?\s*$', re.I)
_WD_TOKEN = re.compile(rf'([{WEEKDAYS_KO}])(?:요일)?|{_WD_EN}', re.I)
VAGUE = re.compile(r'(다음\s*주\s*(?:쯤|중|경|즈음|초|말)|이번\s*주\s*(?:중|쯤|내)|다음\s*달|이번\s*달\s*(?:중|말)'
                   r'|월말|조만간|추후|나중에|빠른\s*시일|일정\s*(?:미정|조율)|날짜\s*(?:미정|조율)'
                   r'|\bnext\s+week\b(?!\s*(?:mon|tue|wed|thu|fri))|\bsometime\b|\bsoon\b|\bTBD\b)', re.I)

TIME_KO = re.compile(r'(오전|오후|저녁|밤|새벽|낮)?\s*(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분|\s*(반))?')
TIME_COLON = re.compile(r'(오전|오후|저녁|밤|새벽|AM|PM|am|pm)?\s*(?<![\d.])([01]?\d|2[0-3]):([0-5]\d)(?![\d.])\s*(AM|PM|am|pm|a\.m\.|p\.m\.)?')
TIME_EN = re.compile(r'(?<![\d:])(\d{1,2})(?::([0-5]\d))?\s*(am|pm|a\.m\.|p\.m\.)', re.I)
PART_ONLY = re.compile(r'(오전|오후|morning|afternoon)(?!\s*\d)', re.I)
TZ_MARK = re.compile(r'\b(KST|JST|UTC|GMT|CET|CEST|PST|PDT|EST|EDT|SGT)\b')
# 문장 끝: 줄바꿈, 또는 공백이 이어지는 !?。 와 마침표 (숫자 뒤 점은 날짜·IP·버전일 수 있어 끊지 않는다)
SENTENCE_END = re.compile(r'\n+|(?<=[!?。])\s+|(?<=[^\d\s]\.)\s+')


@dataclass
class Found:
    """메일 속 일정 후보 하나 (문장 하나)."""
    sentence: str                 # 근거 문장 (원문)
    date: date | None = None
    start: time | None = None
    end: time | None = None
    tz: str = 'KST'
    vague: bool = False           # 날짜가 흐림 → 묻는다
    conflict: str = ''            # 요일과 날짜가 다름 같은 모순 → 묻는다
    assumed: str = ''             # 코드가 짐작한 것 (오후로 봄, 기본 시간 등)
    past: bool = False            # 받은 날보다 앞선 날짜 (지난 일 이야기) → 제안하지 않는다

    @property
    def clear(self):
        return self.date is not None and not self.vague and not self.conflict and not self.past

    def starts_at(self):
        """시작 시각 (시간대 포함). 시간이 없으면 None (종일 일정)."""
        if not self.date or not self.start:
            return None
        return datetime.combine(self.date, self.start, tzinfo=timezone(timedelta(hours=TZ_OFFSETS[self.tz]), self.tz))

    def ends_at(self):
        if not self.date or not self.start:
            return None
        end = self.end or (datetime.combine(self.date, self.start) + timedelta(hours=1)).time()
        start_dt = self.starts_at()
        end_dt = start_dt.replace(hour=end.hour, minute=end.minute)
        return end_dt if end_dt > start_dt else end_dt + timedelta(days=1)

    def describe(self):
        if not self.date:
            return '날짜 확인 필요'
        text = f'{self.date.month}/{self.date.day}({WEEKDAYS_KO[self.date.weekday()]})'
        if self.start:
            end = self.ends_at()
            text += f' {self.start:%H:%M}~{end:%H:%M}'
        else:
            text += ' 종일'
        return text + ('' if self.tz == 'KST' else f' {self.tz}')


def _year_for(month, day, ref):
    try:
        candidate = date(ref.year, month, day)
    except ValueError:
        return None
    if (ref - candidate).days > PAST_LIMIT_DAYS:
        try:
            return date(ref.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def _monday(d):
    return d - timedelta(days=d.weekday())


def _wd_index(token):
    token = token.lower()
    if token in WEEKDAYS_KO:
        return WEEKDAYS_KO.index(token)
    return WEEKDAYS_EN.index(token[:3])


def _month_index(token):
    return MONTHS_EN.index(token.lower()[:3]) + 1


def _resolve(kind, m, ref):
    """(날짜, 날짜 뒤에 바로 붙은 요일 표시를 검사할지)."""
    g = m.groups()
    try:
        if kind == 'ymd':
            return date(int(g[0]), int(g[1]), int(g[2])), True
        if kind == 'md':
            month, day = int(g[0]), int(g[1])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None, False
            return _year_for(month, day, ref), True
        if kind == 'en_md':
            return _year_for(_month_index(g[0]), int(g[1]), ref), True
        if kind == 'en_dm':
            return _year_for(_month_index(g[1]), int(g[0]), ref), True
    except ValueError:
        return None, False
    if kind in ('rel', 'rel_en'):
        offset = {'오늘': 0, '금일': 0, 'today': 0, '내일': 1, '명일': 1, '익일': 1, 'tomorrow': 1,
                  '모레': 2, '글피': 3}[g[0].lower()]
        return ref + timedelta(days=offset), False
    if kind == 'week_ko':
        which = re.sub(r'\s', '', g[0])
        weeks = {'이번주': 0, '금주': 0, '다음주': 1, '차주': 1, '담주': 1, '다다음주': 2}[which]
        return _monday(ref) + timedelta(weeks=weeks, days=_wd_index(g[1])), False
    if kind == 'week_en':
        weeks = 0 if g[0].lower() == 'this' else 1
        return _monday(ref) + timedelta(weeks=weeks, days=_wd_index(g[1])), False
    if kind in ('wd_ko', 'wd_en'):
        ahead = (_wd_index(g[0]) - ref.weekday()) % 7 or 7      # 다가오는 그 요일 (오늘이면 다음 주)
        return ref + timedelta(days=ahead), False
    return None, False


def _hour24(hour, part):
    part = (part or '').lower().replace('.', '')
    if part in ('오후', '저녁', '밤', 'pm', '낮') and hour < 12:
        return hour + 12 if not (part == '낮' and hour >= 11) else hour
    if part in ('오전', '새벽', 'am') and hour == 12:
        return 0
    return hour


def _times(sentence):
    """문장의 (시작, 끝, 짐작한 것)."""
    found = []     # (위치, 시, 분, 오전/오후 표시)
    for m in TIME_COLON.finditer(sentence):
        found.append((m.start(), int(m.group(2)), int(m.group(3)), m.group(1) or m.group(4), 'colon'))
    taken = [(m.start(), m.end()) for m in TIME_COLON.finditer(sentence)]
    for m in TIME_KO.finditer(sentence):
        if any(a <= m.start(2) < b for a, b in taken):
            continue
        minute = 30 if m.group(4) else int(m.group(3) or 0)
        found.append((m.start(), int(m.group(2)), minute, m.group(1), 'ko'))
    for m in TIME_EN.finditer(sentence):
        if any(a <= m.start(1) < b for a, b in taken):
            continue
        found.append((m.start(), int(m.group(1)), int(m.group(2) or 0), m.group(3), 'en'))
    found = sorted(f for f in found if 0 <= f[1] <= 24)
    if not found:
        part = PART_ONLY.search(sentence)
        if part:
            if part.group(1).lower() in ('오후', 'afternoon'):
                return (*AFTERNOON_DEFAULT, '시간이 없어 오후 14:00~16:00으로 둠')
            return (*MORNING_DEFAULT, '시간이 없어 오전 10:00~12:00으로 둠')
        return None, None, ''
    assumed = ''
    start_pos, h, mi, part, form = found[0]
    h24 = _hour24(h, part)
    if not part and 1 <= h <= 6 and form == 'ko':      # "3시 회의"는 오후, "03:00 작업"은 새벽
        h24, assumed = h + 12, '오전·오후 표시가 없어 오후로 봄'
    start = time(h24 % 24, mi)
    end = None
    if len(found) > 1:
        _, h2, mi2, part2, form2 = found[1]
        e24 = _hour24(h2, part2 or part)
        if not part2 and h2 == 12:
            e24 = 12                    # "오전 10시~12시"의 12시는 정오
        if not (part2 or part) and 1 <= h2 <= 6 and form2 == 'ko' and h24 >= 12:
            e24 = h2 + 12
        if e24 <= h24 and e24 + 12 < 24 and e24 + 12 > h24:
            e24 += 12
        end = time(e24 % 24, mi2)
    return start, end, assumed


def _clip_words(text, limit):
    """단어 중간에서 자르지 않는다. IP·메일 주소가 잘린 채 남으면 나중에 마스킹 패턴에 걸리지 않는다."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(' ')
    return cut[:space] if space > 0 else ''


def _said_weekday(text):
    m = _WD_TOKEN.search(text)
    return _wd_index(m.group(1) or m.group(2))


def _weekday_note(sentence, m):
    """절대 날짜 m 옆에 쓴 요일 (요일 번호, 차지한 구간). 없으면 (None, None)."""
    after = WEEKDAY_AFTER.match(sentence[m.end():])
    if after:
        return _said_weekday(after.group(0)), (m.end(), m.end() + after.end())
    before = WEEKDAY_BEFORE.search(sentence[:m.start()])
    if before:
        return _said_weekday(before.group(0)), (before.start(), m.start())
    return None, None


def find(body, received):
    """메일 본문에서 일정 후보를 찾는다. received: 받은 시각(datetime, 시간대 포함)."""
    ref = received.astimezone(KST).date()
    results = []
    for sentence in SENTENCE_END.split(body or ''):
        sentence = sentence.strip()
        if not sentence:
            continue
        hits, taken = [], []
        for kind, pattern in DATE_PATTERNS:
            for m in pattern.finditer(sentence):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue                    # 앞선 (더 구체적인) 표현이나 요일 표시와 겹침
                said, span = _weekday_note(sentence, m) if kind in ABSOLUTE_KINDS else (None, None)
                hits.append((m.start(), m.end(), kind, m, said))
                taken.append((m.start(), m.end()))
                if span:
                    taken.append(span)
        vague = VAGUE.search(sentence)
        if not hits and not vague:
            continue
        item = Found(sentence=_clip_words(sentence, 200))
        tz = TZ_MARK.search(sentence)
        if tz:
            item.tz = tz.group(1)
        ambiguous = AMBIGUOUS_TZ.search(sentence)
        if hits:
            hits.sort(key=lambda h: h[0])
            valid = [h for h in hits if _resolve(h[2], h[3], ref)[0] is not None]
            hits = valid or hits
            start, end, kind, m, said = hits[0]
            resolved, _ = _resolve(kind, m, ref)
            item.date = resolved
            if resolved is None:
                item.conflict = '날짜를 읽지 못함'
            if resolved and said is not None:
                if said != resolved.weekday():
                    item.conflict = (f'요일이 맞지 않음 ({resolved.month}/{resolved.day}은 '
                                     f'{WEEKDAYS_KO[resolved.weekday()]}요일)')
            if resolved and resolved < ref:
                if kind == 'week_ko' and re.sub(r'\s', '', m.group(1)) in ('이번주', '금주'):
                    item.conflict = '이번 주가 어느 주인지 확인 필요 (주말에 받은 메일)'
                else:
                    item.past = True
            others = {(_resolve(k, mm, ref)[0]) for _, _, k, mm, _ in hits[1:]} - {None, resolved}
            if others:
                item.conflict = item.conflict or '한 문장에 날짜가 여러 개'
        if hits and ambiguous:
            item.conflict = item.conflict or f'시간대 {ambiguous.group(1)}가 여러 뜻이라 확인 필요'
        if vague:      # "다음 주쯤 목요일", "일정 조율 후 목요일"도 날짜가 흐리다 → 제안하지 않고 묻는다
            item.vague = True
        item.start, item.end, item.assumed = _times(sentence)
        results.append(item)
    return results


# ---------------------------------------------------------------- 제안 만들기
SUBJECT_PREFIX = re.compile(r'^\s*((re|fw|fwd|회신|전달|답장)\s*:\s*|\[(외부메일|external|외부)\]\s*)+', re.I)


def title_from(subject):
    title = SUBJECT_PREFIX.sub('', subject or '').strip()
    return title[:80] or '(제목 없음)'


@dataclass
class Picked:
    item: Found
    probability: float | None          # Jev가 '받는 사람의 일정'이라고 본 확률 (안 물었으면 None)
    reasons: list = field(default_factory=list)


def pick(found, ask, *, yes=0.7):
    """후보 중 제안할 것. ask({id: Found}) -> {id: 확률}: 분류기(Jev)에게 묻는 함수.
    - 날짜가 분명한 후보: 확률 >= yes면 제안
    - 흐리거나 모순인 후보: 확률 >= yes면 날짜를 묻는 제안 (버튼 없이 [무시]만)
    - 지난 날짜는 묻지도 않는다
    같은 날짜·시각 후보가 여러 문장에 있으면 첫 문장 하나만 남긴다."""
    asked = {f'c{n}': item for n, item in enumerate((f for f in found if not f.past), 1)}
    if not asked:
        return []
    probabilities = ask(asked)
    picked, seen = [], set()
    for cid, item in asked.items():
        p = probabilities.get(cid)
        if p is None or p < yes:
            continue
        key = (item.date, item.start) if item.clear else (item.sentence,)
        if key in seen:
            continue
        seen.add(key)
        picked.append(Picked(item, p))
    return picked


def jev_ask(classifier, mail, settings, identity, masker=None):
    """Jev에게 후보마다 묻는 함수를 만든다 (pick의 ask). 문장은 가려서 보낸다."""
    from reda.classify import jev_questions as jq
    from reda.classify.mailtext import structured_state
    from reda.mask import Masker
    masker = masker or Masker()

    def ask(candidates):
        state = structured_state(mail, settings, identity, masker, reader=jq.READER)
        state['candidates'] = {cid: {'sentence': masker.mask(item.sentence),
                                     'date': item.describe() if item.date else '날짜 불분명'}
                               for cid, item in candidates.items()}
        payload = {'model': classifier._model, 'state': state,
                   'questions': {cid: jq.schedule_question(cid) for cid in candidates}}
        body = classifier._request(payload)
        answers = body.get('answers') if isinstance(body, dict) else None
        out = {}
        for cid in candidates:
            try:
                out[cid] = float(answers[cid]['noul'])
            except (KeyError, TypeError, ValueError):
                out[cid] = None
        return out
    return ask


# ---------------------------------------------------------------- 분류한 메일에서 제안 모으기
SCAN_KEY = 'last_schedule_scan_at'
TODO_KINDS = ('일정', '할 일')


def _iso(item):
    """(start_at, end_at, all_day). 종일이면 날짜만, 날짜를 모르면 None."""
    if not item.date:
        return None, None, False
    if not item.start:
        return item.date.isoformat(), (item.date + timedelta(days=1)).isoformat(), True
    return (item.starts_at().isoformat(timespec='minutes'), item.ends_at().isoformat(timespec='minutes'),
            False)


def _picked_for(row, get_message, ask_for, yes):
    """(메일, 제안할 후보) — 일정·할 일 표시가 없거나 날짜가 없으면 (None, [])."""
    import json as _json

    from reda.identity import split_quoted
    from reda.mail import Mail
    todo = _json.loads(row['classifier_detail'] or '{}').get('todo', '')
    if todo not in TODO_KINDS:
        return None, []
    mail = Mail.from_gmail(get_message(row['message_id']))
    received = datetime.fromtimestamp(mail.internal_date / 1000, timezone.utc)
    latest, _ = split_quoted(mail.body or '')
    found = find(latest, received)
    if not found:
        return mail, []
    return mail, pick(found, ask_for(mail), yes=yes)


def _save(state, row, mail, picked):
    item = picked.item
    # 날짜가 분명하지 않으면 시각을 비워 둔다 → 버튼 봇이 등록 버튼 대신 날짜를 묻는다
    start_at, end_at, all_day = _iso(item) if item.clear else (None, None, False)
    note = item.conflict or ('날짜가 정해지지 않음' if item.vague else item.assumed)
    return state.add_proposal(row['message_id'], row['thread_id'], sentence=item.sentence,
                              title=title_from(mail.subject), start_at=start_at, end_at=end_at,
                              all_day=all_day, note=note, status='new')


def _getter(service, get_message):
    from reda.gmail import client
    return get_message or (lambda mid: client.get_message(service, mid))


def scan(service, state, settings, ask_for, *, now=None, get_message=None):
    """기준점 뒤에 분류한 '일정'·'할 일' 메일에서 제안을 만든다. 새 제안 id 목록을 돌려준다.
    ask_for(mail) -> ask: 그 메일 후보를 Jev에 묻는 함수 (시험에서는 가짜).
    처음 실행이면 기준점만 잡는다 (예전 메일을 한꺼번에 제안하지 않게)."""
    get_message = _getter(service, get_message)
    now = now or datetime.now(timezone.utc)
    after = state.get_sync(SCAN_KEY)
    if after is None:
        state.set_sync(SCAN_KEY, now.isoformat(timespec='seconds'))
        return []
    rows = state.conn.execute('SELECT * FROM messages WHERE labeled_at > ? ORDER BY labeled_at',
                              (after,)).fetchall()
    from reda.classify import ClassifierUnavailable
    created = []
    for row in rows:
        try:
            mail, picked = _picked_for(row, get_message, ask_for, settings.jev_min_probability)
        except ClassifierUnavailable:
            raise           # 분류기 장애는 기준점을 옮기지 않고 다음 실행에서 다시
        except Exception as exc:  # 지워진 메일, 요청 거부 등은 이 메일만 건너뛴다
            print(f'일정 찾기 건너뜀 ({row["message_id"]}): {exc.__class__.__name__}', file=sys.stderr)
            picked = []
        for p in picked:
            pid = _save(state, row, mail, p)
            if pid:
                created.append(pid)
        # 메일마다 기준점을 옮긴다 (도중에 실패해도 처리한 메일을 다시 묻지 않게)
        state.set_sync(SCAN_KEY, row['labeled_at'])
    return created


def preview(service, state, settings, ask_for, *, since, get_message=None):
    """since 뒤에 분류한 메일로 제안을 미리 본다 (저장하지 않음). [(메일, Picked)]."""
    get_message = _getter(service, get_message)
    rows = state.conn.execute('SELECT * FROM messages WHERE labeled_at > ? ORDER BY labeled_at',
                              (since,)).fetchall()
    out = []
    for row in rows:
        mail, picked = _picked_for(row, get_message, ask_for, settings.jev_min_probability)
        out += [(mail, p) for p in picked]
    return out
