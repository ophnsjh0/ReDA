"""일정 찾기 (9단계 2번): 날짜 변환(요일·월말·연말·영문), 시간, 흐린 날짜, 모순."""
from datetime import date, datetime, time

import pytest

from reda.schedule import KST, find

SUN = datetime(2026, 11, 8, 19, 0, tzinfo=KST)          # 2026-11-08 일요일
WED = datetime(2026, 11, 11, 9, 0, tzinfo=KST)          # 수요일
MONTH_END = datetime(2026, 4, 29, 10, 0, tzinfo=KST)    # 4월 29일 수요일
YEAR_END = datetime(2026, 12, 28, 10, 0, tzinfo=KST)    # 12월 28일 월요일


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
                                  '일정 조율 후 다시 연락드리겠습니다', 'Let us meet sometime next week.'])
def test_vague_dates_are_not_proposed(body):
    item = one(body, WED)
    assert item.vague and not item.clear


def test_weekday_conflict_and_past_dates():
    assert '요일이 맞지 않음' in one('11월 12일(수) 점검', SUN).conflict
    assert one('11월 2일 점검 결과 공유드립니다', SUN).past          # 지난 일
    weekend = one('이번 주 목요일 점검', SUN)                        # 일요일에 받은 '이번 주'
    assert not weekend.clear and '이번 주' in weekend.conflict


def test_not_dates():
    assert find('버전 3.2.1 로 업그레이드했고 IP는 192.0.2.15 입니다. 2시간 걸렸습니다.', SUN) == []
    assert find('장비 2대, 포트 443, 50% 사용', SUN) == []


def test_timezone_mark():
    item = one('The window is Nov 12 9:00 PST.', SUN)
    assert item.tz == 'PST' and item.starts_at().utcoffset().total_seconds() == -8 * 3600
