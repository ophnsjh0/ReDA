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


# ---------------------------------------------------------------- 그룹 주소 자동 인식 (실제 메일 점검 2026-11-07)

@pytest.fixture
def only_me(monkeypatch, tmp_path):
    """설정에 그룹 주소를 적지 않은 경우 (그룹은 헤더로 알아내야 한다)."""
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    return Settings.from_env()


ADC = 'adc@corp.example.com'
LIST_ADC = '<adc.corp.example.com>'


def test_group_from_list_id_makes_colleague_thread_cc_only(only_me):
    # 동료에게 온 고객 메일을 그룹이 참조로 받아 나에게 전달됨 → 참조 전용
    m = mail(From='lee@customer-a.example.com', To='colleague@corp.example.com',
             Cc=f'For ADC <{ADC}>', list_id=LIST_ADC)
    assert rules.check(m, only_me).reason == '그룹이 참조(Cc)로만 받음'


def test_customer_mail_to_group_goes_to_classifier(only_me):
    m = mail(From='lee@customer-a.example.com', To=f'For ADC <{ADC}>', list_id=LIST_ADC)
    assert rules.check(m, only_me) is None


def test_rewritten_from_is_treated_as_group(only_me):
    # 그룹이 From을 "'X' via 기술부" <support@...>로 바꾼 메일. 진짜 발신자로 판정한다
    m = mail(From="'Vendor Alerts' via 기술부 <support@corp.example.com>",
             To='support@corp.example.com', x_original_sender='alerts@mail.vendor.example.com',
             Reply_To='feedback@vendor.example.com', list_id='<tech-group.corp.example.com>')
    assert rules.check(m, only_me).reason == '자동 발송 주소 "alerts@"'


def test_rewritten_from_customer_reply_goes_to_classifier(only_me):
    m = mail(From="'Engineer Kim' via 기술부 <support@corp.example.com>",
             To='colleague@corp.example.com', Cc='support@corp.example.com',
             x_original_sender='kim@vendor.example.com', list_id='<tech-group.corp.example.com>')
    # 그룹(support@)이 참조로만 받았다 → 참고. 같은 메일을 그룹이 To로 받으면 분류기로 간다
    assert rules.check(m, only_me).reason == '그룹이 참조(Cc)로만 받음'
    m2 = replace(m, to=['support@corp.example.com'], cc=['colleague@corp.example.com'])
    assert rules.check(m2, only_me) is None


def test_list_id_of_other_domain_is_not_mine(only_me):
    m = mail(From='a@partner.example.net', To='b@partner.example.net', Cc='c@partner.example.net',
             list_id='<team.partner.example.net>')
    assert rules.check(m, only_me) is None


def test_bounce_return_path_is_automated(only_me):
    m = mail(From='"항공사" <club@airline.example.com>', To=ME,
             return_path='<mailer@airline.example.com>')
    assert rules.check(m, only_me).reason == '발송 시스템 반송 주소 "mailer@"'


def test_group_bounce_return_path_is_not_automated(only_me):
    m = mail(From='lee@customer-a.example.com', To=ADC, list_id=LIST_ADC,
             return_path='<adc+bncABCDEF@corp.example.com>')
    assert rules.check(m, only_me) is None


def test_bcc_broadcast_to_no_reply_is_fyi(only_me):
    m = mail(From="'Vendor' via 기술부 <support@corp.example.com>", To='no-reply@vendor.example.com',
             x_original_sender='webadmin@vendor.example.com', list_id='<tech-group.corp.example.com>')
    assert rules.check(m, only_me).reason == '받는사람이 자동 발송 주소뿐 (숨은참조 일괄 발송)'



@pytest.mark.parametrize('return_path', [
    '<bounces+SRS=abc=XY@tenant.example.onmicrosoft.com>',   # Microsoft 365 SRS
    '<bounces+123-abc-lee=customer-a.example.com@em.customer-a.example.com>',  # SendGrid
    '<bounce+a1b2@mg.customer-a.example.com>',               # Mailgun
])
def test_esp_bounce_addresses_of_human_mail_are_not_filtered(only_me, return_path):
    m = mail(From='lee@customer-a.example.com', To=ME, return_path=return_path)
    assert rules.check(m, only_me) is None


def test_rewritten_from_is_group_even_without_list_id(only_me):
    m = mail(From="'Vendor Alerts' via 기술부 <support@corp.example.com>",
             To='support@corp.example.com', x_original_sender='alerts@mail.vendor.example.com')
    assert rules.check(m, only_me).reason == '자동 발송 주소 "alerts@"'


def test_list_id_uses_longest_matching_domain(monkeypatch, tmp_path):
    from reda.mail import group_addresses
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    m = mail(From='a@corp.example.com', To='x@corp.example.com', list_id='<team.corp.example.com>')
    assert group_addresses(m, ('me@corp.example.com', 'me@example.com')) == {
        'team@corp.example.com'}


def test_cc_mail_greeting_me_is_not_cc_only(only_me):
    from reda import identity as ident
    identity = ident.build(personal=[ME], display_names=['홍길동'], titles=['부장'])
    m = mail(From='lee@customer-a.example.com', To='colleague@corp.example.com',
             Cc=f'For ADC <{ADC}>', list_id=LIST_ADC, body='길동 부장님, 의견 부탁드립니다.')
    assert rules.check(m, only_me, identity) is None
    # 이름 프로필이 없으면 예전처럼 참고
    assert rules.check(m, only_me).reason == '그룹이 참조(Cc)로만 받음'
    m2 = mail(From='lee@customer-a.example.com', To='colleague@corp.example.com', Cc=ME,
              body='홍 부장님도 참고 부탁드립니다.')
    assert rules.check(m2, only_me, identity) is None


def test_notification_senders_are_narrow(monkeypatch, tmp_path):
    """리뷰 지적: 알림류 이름을 앞부분만 맞춰 걸러 내면 티켓 시스템·그룹 반송 주소까지 걸린다."""
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    monkeypatch.setenv('REDA_CUSTOMER_DOMAINS', 'customer-a.example.com=고객사A')
    s = Settings.from_env()
    # 제조사의 alerts@는 자동 발송
    assert rules.check(mail(From='alerts@vendor.example.com'), s).reason == '자동 발송 주소 "alerts@"'
    # 고객사 티켓 시스템의 notifications@는 분류기가 본다
    assert rules.check(mail(From='notifications@customer-a.example.com'), s) is None
    # alert-tac 같은 이름은 정확히 같지 않으므로 걸리지 않는다
    assert rules.check(mail(From='alert-tac@corp.example.com'), s) is None
    # 그룹 반송 주소 alert-tac+bnc…@ 도 걸리지 않는다
    m = mail(From='lee@customer-a.example.com', return_path='<alert-tac+bncXYZ@corp.example.com>')
    assert rules.check(m, s) is None
    # no-reply 계열은 예전처럼 앞부분만 맞아도 걸린다
    assert rules.check(mail(From='noreply-alerts@vendor.example.com'), s)
