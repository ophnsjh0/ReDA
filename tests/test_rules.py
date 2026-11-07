"""규칙 필터 (검증기준 2단계: 규칙 필터, 그룹 중계 메일, List-Unsubscribe 오판 방지)."""
from dataclasses import replace

import pytest
from fakes import make_message

from reda import rules
from reda.config import Settings
from reda.mail import Mail, parse_addresses

ME = 'me@corp.example.com'
GROUP = 'tac-team@corp.example.com'


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', f'{ME},{GROUP}')
    monkeypatch.setenv('REDA_EXCLUDE_SUBJECTS', '주간 뉴스레터,[광고]')
    return Settings.from_env()


def mail(**headers):
    headers.setdefault('To', ME)
    return Mail.from_gmail(make_message('m1', **headers))


def test_parse_addresses_handles_outlook_semicolons_and_quoted_commas():
    assert parse_addresses('"Xu, Chengri" <x@a.example.com>; 이차장 <LEE@b.example.com>',
                           'c@c.example.com') == [
        'x@a.example.com', 'lee@b.example.com', 'c@c.example.com']


@pytest.mark.parametrize('sender', [
    'noreply@vendor.example.com',
    '"Vendor" <no-reply@vendor.example.com>',
    'do-not-reply@vendor.example.com',
    'noreply-alerts@vendor.example.com',
    'MAILER-DAEMON@corp.example.com',
    'postmaster@corp.example.com',
])
def test_auto_senders_are_fyi(settings, sender):
    hit = rules.check(mail(From=sender, Subject='알림'), settings)
    assert hit and hit.category == '참고' and '자동 발송' in hit.reason


@pytest.mark.parametrize('sender', ['marketing@vendor.example.com', 'newsletter.kr@vendor.example.com',
                                    'events@vendor.example.com'])
def test_marketing_senders_are_fyi(settings, sender):
    hit = rules.check(mail(From=sender, Subject='초대'), settings)
    assert hit and '마케팅' in hit.reason


def test_similar_but_human_local_parts_are_not_filtered(settings):
    for sender in ('newsom@customer-a.example.com', 'eventsupport@vendor.example.com',
                   'noreplyteam-lead@x.example.com'):
        assert rules.check(mail(From=sender, Subject='문의'), settings) is None, sender


def test_subject_keyword_is_fyi(settings):
    hit = rules.check(mail(From='lee@customer-a.example.com', Subject='[광고] 신제품'), settings)
    assert hit and '제목 키워드' in hit.reason


def test_bulk_precedence_and_auto_submitted_are_fyi(settings):
    assert rules.check(mail(From='a@x.example.com', Precedence='bulk'), settings)
    assert rules.check(mail(From='a@x.example.com', auto_submitted='auto-replied'), settings)
    assert rules.check(mail(From='a@x.example.com', auto_submitted='no'), settings) is None


def test_google_groups_headers_alone_do_not_make_bulk(settings):
    """그룹 중계 메일에는 List-Unsubscribe와 Precedence: list가 붙는다. 고객 문의일 수 있다."""
    m = mail(From='lee@customer-a.example.com', To=GROUP, Subject='장애 문의',
             List_Unsubscribe='<mailto:x@corp.example.com>', Precedence='list')
    assert rules.check(m, settings) is None


def test_cc_only_is_fyi_but_group_in_to_is_mine(settings):
    hit = rules.check(mail(From='lee@customer-a.example.com', To='kim@corp.example.com',
                           Cc=f'{ME}, park@corp.example.com'), settings)
    assert hit and '참조' in hit.reason
    # 내 주소 없이 그룹 주소로만 받은 고객 문의는 "나에게 온 메일"
    assert rules.check(mail(From='lee@customer-a.example.com', To=GROUP), settings) is None
    # Outlook식 ; 구분도 To로 읽는다
    assert rules.check(mail(From='lee@customer-a.example.com',
                            To=f'kim@corp.example.com; {ME}', Cc=GROUP), settings) is None


def test_group_relay_uses_original_sender(settings):
    relayed = dict(From=f"'Vendor' via TAC <{GROUP}>", To=GROUP, Subject='공지')
    hit = rules.check(mail(**relayed, x_original_sender='noreply@vendor.example.com'), settings)
    assert hit and '자동 발송' in hit.reason
    hit = rules.check(mail(**relayed, Reply_To='newsletter@vendor.example.com'), settings)
    assert hit and '마케팅' in hit.reason
    # 고객이 그룹으로 보낸 문의는 걸러지지 않는다
    assert rules.check(mail(**relayed, x_original_sender='lee@customer-a.example.com',
                            Reply_To='lee@customer-a.example.com'), settings) is None


def test_example_3_vendor_notice_is_fyi(settings):
    """기획안 예시 ③: 제조사 펌웨어 공지 (no-reply 대량 발송) → 참고, 긴급도 낮음."""
    m = mail(From='"Vendor Notice" <no-reply@vendor.example.com>', To=ME,
             Subject='[공지] 펌웨어 4.2.1 배포 안내', Precedence='bulk')
    hit = rules.check(m, settings)
    assert (hit.category, hit.urgency) == ('참고', '낮음')


def test_without_my_addresses_cc_rule_is_skipped(settings):
    s = replace(settings, my_addresses=())
    assert rules.check(mail(From='lee@customer-a.example.com', To='kim@corp.example.com',
                            Cc=ME), s) is None
