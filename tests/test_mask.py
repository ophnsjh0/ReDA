"""마스킹 (검증기준 2단계: IP·계정·비밀번호 표본이 전부 가려진다)."""
import pytest

from reda.mask import Masker

# 표본: (원문, LLM에 가면 안 되는 값들)
SAMPLES = [
    ('장비 IP는 192.0.2.10 이고 관리 IP는 198.51.100.7/24 입니다.', ['192.0.2.10', '198.51.100.7']),
    ('VIP 203.0.113.5:443 으로 세션이 끊깁니다.', ['203.0.113.5']),
    ('IPv6 2001:db8::1 과 2001:0db8:85a3:0000:0000:8a2e:0370:7334 확인', ['2001:db8::1',
                                                                         '2001:0db8:85a3:0000:0000:8a2e:0370:7334']),
    ('접속 계정: admin01 / 비밀번호: P@ssw0rd!', ['admin01', 'P@ssw0rd!']),
    ('ID/PW: tacuser/Secr3t#', ['tacuser', 'Secr3t#']),
    ('id=operator, password="q1w2e3"', ['operator', 'q1w2e3']),
    ('Login: root\nPassword: toor123', ['root', 'toor123']),
    ('username netadmin privilege 15 secret 5 $1$abcd$EFGHijkl', ['netadmin', '$1$abcd$EFGHijkl']),
    ('enable secret 5 $1$zzzz$yyyy', ['$1$zzzz$yyyy']),
    ('snmp-server community Publ1cRO RO', ['Publ1cRO']),
    ('API key: sk-test-1234567890', ['sk-test-1234567890']),
    ('서버(192.0.2.33)에 접속 안 됨.', ['192.0.2.33']),
    # 값 바로 뒤에 조사가 붙는 한국어 표현 (경계를 \w로 보면 한글도 글자라 놓친다)
    ('장비 192.0.2.10에서 오류, 198.51.100.7로 우회', ['192.0.2.10', '198.51.100.7']),
    ('IP는203.0.113.9입니다', ['203.0.113.9']),
    ('2001:db8::1에서 끊김', ['2001:db8::1']),
    ('host_192.0.2.44_eth0 로그', ['192.0.2.44']),
    ('담당 lee@customer-a.example.com으로 회신', ['lee@']),
    ('010-1234-5678로 연락 주세요', ['010-1234-5678']),
    ('주민번호 900101-1234567을 보냅니다', ['900101-1234567']),
    ('카드 4111 1111 1111 1111로 결제', ['4111 1111 1111 1111']),
]


@pytest.mark.parametrize('text,secrets', SAMPLES)
def test_samples_are_masked(text, secrets):
    masked = Masker().mask(text)
    for secret in secrets:
        assert secret not in masked, (secret, masked)


@pytest.mark.parametrize('text,secrets', SAMPLES)
def test_unmask_restores_original(text, secrets):
    masker = Masker()
    assert masker.unmask(masker.mask(text)) == text


def test_same_value_gets_same_token_across_calls():
    masker = Masker()
    a = masker.mask('원본 192.0.2.10')
    b = masker.mask('다시 192.0.2.10, 그리고 192.0.2.11')
    assert a == '원본 [IP_1]'
    assert b == '다시 [IP_1], 그리고 [IP_2]'
    assert masker.mapping == {'[IP_1]': '192.0.2.10', '[IP_2]': '192.0.2.11'}


def test_ordinary_text_is_left_alone():
    text = '회의는 10:30 에 시작합니다. MAC 00:1a:2b:3c:4d:5e, 케이스 #12345, 버전 v9.2'
    assert Masker().mask(text) == text


def test_invalid_ip_like_numbers_are_left_alone():
    assert Masker().mask('번호 999.1.1.1') == '번호 999.1.1.1'


# ---------------------------------------------------------------- 개인정보
PII_SAMPLES = [
    ('담당자 휴대폰 010-1234-5678 로 연락 주세요', ['010-1234-5678'], '[PHONE_1]'),
    ('연락처 01012345678', ['01012345678'], '[PHONE_1]'),
    ('사무실 02-123-4567, 지사 (031) 123-4567', ['02-123-4567', '123-4567'], '[PHONE_2]'),
    ('Tel +82 10 1234 5678', ['10 1234 5678'], '[PHONE_1]'),
    ('고객센터 1588-1234', ['1588-1234'], '[PHONE_1]'),
    ('주민등록번호 900101-1234567', ['900101-1234567'], '[RRN_1]'),
    ('카드 4111 1111 1111 1111 결제', ['4111 1111 1111 1111'], '[CARD_1]'),
    ('입금 계좌: 국민은행 123-45-678901 (예금주 홍길동)', ['123-45-678901'], '[BANK_1]'),
    ('계좌번호 1002-123-456789', ['1002-123-456789'], '[BANK_1]'),
    ('문의는 lee.kim+tac@customer-a.example.com 으로', ['lee.kim+tac'],
     '[EMAIL_1]@customer-a.example.com'),
]


@pytest.mark.parametrize('text,secrets,token', PII_SAMPLES)
def test_personal_info_is_masked_and_restored(text, secrets, token):
    masker = Masker()
    masked = masker.mask(text)
    for secret in secrets:
        assert secret not in masked, (secret, masked)
    assert token in masked
    assert masker.unmask(masked) == text


def test_email_keeps_domain_so_customer_and_vendor_stay_distinct():
    masked = Masker().mask('From: 이차장 <lee@customer-a.example.com>, Cc: tac@vendor.example.com')
    assert masked == 'From: 이차장 <[EMAIL_1]@customer-a.example.com>, Cc: [EMAIL_2]@vendor.example.com'


@pytest.mark.parametrize('text', [
    '2026-11-07 10:30 회의',
    '케이스 #12345, TAC-2026-0012',
    '시리얼 FGT60F1234567890',
    '견적 1,234,567원, 수량 1588개',
    '버전 v7.2.10 build 1234',
    '번호 1234 5678 9012 3456',          # 카드 형식이지만 Luhn 검사에 맞지 않음
])
def test_work_numbers_are_not_mistaken_for_personal_info(text):
    assert Masker().mask(text) == text
