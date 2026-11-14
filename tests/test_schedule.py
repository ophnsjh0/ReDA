"""일정 찾기 (9단계 2번): 날짜 변환(요일·월말·연말·영문), 시간, 흐린 날짜, 모순."""
from datetime import date, datetime, time

import pytest

from reda.schedule import KST, find

SUN = datetime(2026, 11, 8, 19, 0, tzinfo=KST)          # 2026-11-08 일요일
WED = datetime(2026, 11, 11, 9, 0, tzinfo=KST)          # 수요일
MONTH_END = datetime(2026, 4, 29, 10, 0, tzinfo=KST)    # 4월 29일 수요일
YEAR_END = datetime(2026, 12, 28, 10, 0, tzinfo=KST)    # 12월 28일 월요일
FRI = datetime(2026, 11, 13, 17, 0, tzinfo=KST)         # 금요일 저녁
LEAP = datetime(2028, 2, 27, 10, 0, tzinfo=KST)         # 2028 윤년, 2/27 일요일


def one(body, received=WED):
    found = find(body, received)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize('body, received, expected', [
    ('11월 12일(목) 오후 2시부터 4시까지 점검합니다.', SUN, date(2026, 11, 12)),
    ('2026-11-20 방문 예정입니다.', SUN, date(2026, 11, 20)),
    ('2026년 11월 20일에 뵙겠습니다.', SUN, date(2026, 11, 20)),
    ('11/13 오전 10시 회의', SUN, date(2026, 11, 13)),
    ('11.13(금) 회의', SUN, date(2026, 11, 13)),
    ('내일 오전 10시 점검', WED, date(2026, 11, 12)),
    ('모레 방문 부탁드립니다', WED, date(2026, 11, 13)),
    ('이번 주 금요일 14:00 회의', WED, date(2026, 11, 13)),
    ('다음 주 화요일 오후 3시 방문', WED, date(2026, 11, 17)),
    ('차주 월요일 점검', WED, date(2026, 11, 16)),
    ('목요일에 방문하겠습니다', WED, date(2026, 11, 12)),
    ('수요일에 방문하겠습니다', WED, date(2026, 11, 18)),   # 오늘과 같은 요일은 다음 주
    # 월말
    ('다음 주 월요일 점검', MONTH_END, date(2026, 5, 4)),
    ('이번 주 금요일 점검', MONTH_END, date(2026, 5, 1)),
    ('5월 1일 점검', MONTH_END, date(2026, 5, 1)),
    # 연말: 연도가 없으면 다음 해
    ('1월 5일 오후 2시 정기점검', YEAR_END, date(2027, 1, 5)),
    ('다음 주 화요일 점검', YEAR_END, date(2027, 1, 5)),
    ('Jan 5 maintenance window', YEAR_END, date(2027, 1, 5)),
    # 영문
    ('The maintenance is scheduled for Nov 12 at 2pm.', SUN, date(2026, 11, 12)),
    ('Can we meet on 13th November?', SUN, date(2026, 11, 13)),
    ('Let us meet next Tuesday at 10:30 am.', WED, date(2026, 11, 17)),
    ('Are you available tomorrow?', WED, date(2026, 11, 12)),
    ('Could you join on Friday?', WED, date(2026, 11, 13)),
    # 형식 (검증기준 9단계: 50개 이상)
    ('11월 30일(월) 오전 9시 점검', SUN, date(2026, 11, 30)),
    ('12월 1일 화요일 방문', SUN, date(2026, 12, 1)),               # 괄호 없는 요일
    ('2026.11.25 작업 예정', SUN, date(2026, 11, 25)),
    ('2026/11/25 작업 예정', SUN, date(2026, 11, 25)),
    ('26년 11월 25일 작업', SUN, date(2026, 11, 25)),
    ('오는 11월 20일 금요일 회의', SUN, date(2026, 11, 20)),
    ('금주 금요일 점검', WED, date(2026, 11, 13)),
    ('다음주 수요일 오후 2시', WED, date(2026, 11, 18)),
    ('다다음 주 월요일 점검', WED, date(2026, 11, 23)),
    # 주말 직전에 받은 메일
    ('내일 오후 4시 통화', FRI, date(2026, 11, 14)),
    ('월요일 오전 10시 점검', FRI, date(2026, 11, 16)),
    ('다음 주 금요일 점검', FRI, date(2026, 11, 20)),
    ('next Monday works for us', FRI, date(2026, 11, 16)),
    # 월말
    ('5월 4일(월) 점검', MONTH_END, date(2026, 5, 4)),
    ('4월 30일 마감', MONTH_END, date(2026, 4, 30)),
    ('모레 점검', MONTH_END, date(2026, 5, 1)),
    # 연말·윤년
    ('12월 31일 작업', YEAR_END, date(2026, 12, 31)),
    ('1월 2일 오전 작업', YEAR_END, date(2027, 1, 2)),
    ('2027년 1월 4일 회의', YEAR_END, date(2027, 1, 4)),
    ('다음 주 월요일 회의', YEAR_END, date(2027, 1, 4)),
    ('on 12/31 we will patch', YEAR_END, date(2026, 12, 31)),
    ('2월 29일 점검', LEAP, date(2028, 2, 29)),
    ('다음 주 수요일 점검', LEAP, date(2028, 3, 1)),
    # 영문
    ('Meeting on November 20, 2026 at 3 PM', SUN, date(2026, 11, 20)),
    ('Maintenance: 2026-11-20 22:00 KST', SUN, date(2026, 11, 20)),
    ('See you on Monday, Nov 16.', SUN, date(2026, 11, 16)),        # 날짜 앞에 쓴 요일
    ('Please join the call on Dec 1st.', SUN, date(2026, 12, 1)),
    ('this Friday at 3pm', WED, date(2026, 11, 13)),
])
def test_dates(body, received, expected):
    item = one(body, received)
    assert item.date == expected and item.clear, item


@pytest.mark.parametrize('body, start, end', [
    ('11월 12일 오후 2시부터 4시까지 점검', time(14), time(16)),
    ('11월 12일 14:00~16:00 점검', time(14), time(16)),
    ('11월 12일 2시 30분 회의', time(14, 30), None),
    ('11월 12일 오전 10시 ~ 12시 회의', time(10), time(12)),
    ('11월 12일 오후 2시 반 회의', time(14, 30), None),
    ('Nov 12, 2pm-4pm', time(14), time(16)),
    ('11월 12일 9시 회의', time(9), None),
    ('11월 12일 오후에 방문', time(14), time(16)),      # 결정 9-1
    ('11월 12일 방문', None, None),                    # 종일
])
def test_times(body, start, end):
    item = one(body, SUN)
    assert (item.start, item.end) == (start, end), item


def test_assumed_afternoon_and_duration():
    item = one('11월 12일 3시 회의', SUN)
    assert item.start == time(15) and '오후로 봄' in item.assumed
    assert item.describe() == '11/12(목) 15:00~16:00'
    assert one('11월 12일 오후에 방문', SUN).assumed.startswith('시간이 없어')


@pytest.mark.parametrize('body', ['다음 주쯤 방문 가능하실까요?', '조만간 점검 일정 잡겠습니다',
                                  '일정 조율 후 다시 연락드리겠습니다', 'Let us meet sometime next week.',
                                  # 흐린 표현에 요일이 붙어도 묻는다 (틀린 주로 제안하지 않는다)
                                  '다음 주쯤 목요일에 방문 부탁드립니다', 'next week on Tuesday we will visit',
                                  '다음 주 중 화요일 오후 2시 점검', '일정 조율 후 목요일 방문'])
def test_vague_dates_are_not_proposed(body):
    item = one(body, WED)
    assert item.vague and not item.clear


def test_weekday_conflict_and_past_dates():
    assert '요일이 맞지 않음' in one('11월 12일(수) 점검', SUN).conflict
    assert '요일이 맞지 않음' in one('12월 1일 수요일 방문', SUN).conflict      # 괄호 없는 요일도 검사
    assert '요일이 맞지 않음' in one('See you on Tuesday, Nov 16.', SUN).conflict
    assert one('11월 2일 점검 결과 공유드립니다', SUN).past          # 지난 일
    weekend = one('이번 주 목요일 점검', SUN)                        # 일요일에 받은 '이번 주'
    assert not weekend.clear and '이번 주' in weekend.conflict


def test_not_dates():
    assert find('버전 3.2.1 로 업그레이드했고 IP는 192.0.2.15 입니다. 2시간 걸렸습니다.', SUN) == []
    assert find('장비 2대, 포트 443, 50% 사용', SUN) == []


def test_timezone_mark():
    item = one('The window is Nov 12 9:00 PST.', SUN)
    assert item.tz == 'PST' and item.starts_at().utcoffset().total_seconds() == -8 * 3600


def test_long_sentence_is_not_cut_inside_an_address():
    body = '11월 12일 점검 ' + '가' * 180 + ' 대상 192.0.2.123 장비'
    item = one(body, SUN)
    assert len(item.sentence) <= 200 and '192.0' not in item.sentence


@pytest.mark.parametrize('body, start, end', [
    ('11월 20일 02:00~04:00 정기 점검 작업', time(2), time(4)),     # 콜론 형식은 쓴 그대로 (새벽 작업)
    ('11/12 03:00-05:00', time(3), time(5)),                        # 봇의 시간 수정 예시 형식
    ('11월 12일 3시~5시 회의', time(15), time(17)),                 # 'N시'만 오후로 짐작
])
def test_colon_times_are_24_hour(body, start, end):
    item = one(body, SUN)
    assert (item.start, item.end) == (start, end), item


@pytest.mark.parametrize('body', [
    '다음 주 일정 공유드립니다', '이번 주 목표는 안정화입니다', '다음 주 수정 예정입니다',
    'The error rate will decrease 10 percent', 'We marked 3 tickets as done', 'Step 2 may fail',
    'The novel 5 method',
])
def test_ordinary_words_are_not_dates(body):
    assert [f for f in find(body, SUN) if f.date] == []


def test_invalid_first_match_does_not_hide_a_real_date():
    item = one('24/7 지원 드리며 11월 20일 14시 방문 예정', SUN)
    assert item.date == date(2026, 11, 20) and item.clear


def test_ambiguous_time_zone_is_asked():
    item = one('Call at 2pm CST on Nov 20', SUN)
    assert not item.clear and 'CST' in item.conflict


def test_bare_weekday_after_week_word_still_works():
    assert one('다음 주 화(17일) 방문', WED).date == date(2026, 11, 17)
    assert one('May 5 maintenance', SUN).date == date(2027, 5, 5)
