"""팀 메일 담당자 판정 (결정 2-5, 2-6): 수신 방식, 이름 프로필, 인사말 대상, 담당자 판정.

이름은 모두 지어낸 것이다 (실제 사용자 이름을 쓰지 않는다).
"""
import pytest
from fakes import FakeGmail, make_message

from reda import identity as ident
from reda.classify import Classification
from reda.config import Settings
from reda.mail import Mail
from reda.state import StateDB

ME = 'gdhong@corp.example.com'
GROUP = 'tac-team@corp.example.com'
ADC = 'adc@corp.example.com'
LIST_ADC = '<adc.corp.example.com>'


def me(**kwargs):
    defaults = dict(personal=[ME], groups=[GROUP], display_names=['홍길동'], titles=['부장'])
    defaults.update(kwargs)
    return ident.build(**defaults)


def mail(body='', **headers):
    headers.setdefault('From', 'lee@customer-a.example.com')
    return Mail.from_gmail(make_message('m1', body=body, **headers))


# ---------------------------------------------------------------- 수신 방식

@pytest.mark.parametrize('headers, mode', [
    (dict(To=ME), ident.PERSONAL_TO),
    (dict(To=f'{GROUP}, {ME}'), ident.PERSONAL_TO),
    (dict(To=GROUP, Cc=ME), ident.GROUP_TO),          # 그룹이 받는사람이고 나는 참조
    (dict(To='kim@corp.example.com', Cc=ME), ident.PERSONAL_CC),
    (dict(To='kim@corp.example.com', Cc=GROUP), ident.GROUP_CC),
    (dict(To='kim@corp.example.com'), ident.HIDDEN),
    (dict(To=ADC, list_id=LIST_ADC), ident.GROUP_TO),  # 헤더로 알아낸 그룹
])
def test_recipient_mode(headers, mode):
    assert me().recipient_mode(mail(**headers)) == mode


def test_group_listed_as_my_address_is_still_a_group():
    # 그룹 주소를 개인 주소 목록에 잘못 적었어도, 메일 헤더가 그룹이라고 하면 그룹으로 본다
    identity = ident.build(personal=[ME, 'support@corp.example.com'])
    m = mail(From="'Vendor' via 기술부 <support@corp.example.com>", To='support@corp.example.com',
             x_original_sender='case@vendor.example.com')
    assert identity.recipient_mode(m) == ident.GROUP_TO


def test_settings_groups_are_not_personal(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', f'{ME},{GROUP}')
    monkeypatch.setenv('REDA_MY_GROUPS', GROUP)
    identity = ident.Identity.from_settings(Settings.from_env())
    assert identity.personal == {ME} and identity.groups == {GROUP}


# ---------------------------------------------------------------- 이름 형태

def test_name_forms_korean_and_english():
    assert ident.name_forms('홍길동') == (('홍길동',), ('길동',), ('홍',), ())
    full, given, surnames, latin = ident.name_forms('홍길동 (Gildong Hong)')
    assert full == ('홍길동',) and given == ('길동',)
    assert set(latin) == {('gildong', 'hong'), ('hong', 'gildong')}
    assert {'홍', 'gildong', 'hong'} <= set(surnames)
    assert ident.name_forms('홍길동/기술1팀')[0] == ('홍길동',)


# ---------------------------------------------------------------- 인사말 대상 (분류기가 추출한 값)

@pytest.mark.parametrize('addressee, expected', [
    ('홍길동 부장님', ident.ME),
    ('길동 부장님', ident.ME),
    ('길동님', ident.ME),
    ('홍 부장님', ident.ME),
    ('홍부장님', ident.ME),
    ('기술1팀 홍길동 부장님', ident.ME),
    ('철수 대리님, 길동 부장님', ident.ME),
    ('김길동 부장님', ident.OTHER),       # 이름 끝이 같아도 다른 사람
    ('길동 대리님', ident.OTHER),         # 직함이 다르면 같은 이름의 다른 사람으로 본다
    ('홍 대리님', ident.OTHER),
    ('김철수 과장님', ident.OTHER),
    ('담당자님', ident.NONE),
    ('부장님', ident.NONE),
    ('여러분', ident.NONE),
    ('', ident.NONE),
])
def test_match_addressee(addressee, expected):
    assert ident.match_addressee(addressee, me()) == expected


def test_full_name_matches_even_with_old_title():
    assert ident.match_addressee('홍길동 차장님', me()) == ident.ME


def test_without_names_specific_addressee_is_unknown():
    identity = ident.build(personal=[ME])
    assert ident.match_addressee('김철수 과장님', identity) == ident.UNKNOWN
    assert ident.match_addressee('담당자님', identity) == ident.NONE


def test_english_alias():
    identity = me(aliases=['Gildong'])
    assert ident.match_addressee('Hi Gildong', identity) == ident.ME
    assert ident.match_addressee('Dear Mr. Kim', identity) == ident.OTHER
    # 한글 이름만 있으면 영문 호칭은 알아보지 못한다
    assert ident.match_addressee('Mr. Hong', me()) == ident.OTHER


def test_english_display_name():
    identity = me(display_names=['홍길동 (Gildong Hong)'])
    assert ident.match_addressee('Mr. Hong', identity) == ident.ME
    assert ident.match_addressee('Gildong Hong', identity) == ident.ME
    assert ident.greets_me('Hi Mr. Hong,\nplease check.', identity)
    assert ident.greets_me('Dear Gildong Hong,\n', identity)
    assert not ident.greets_me('Dear Mr. Kim,\n', identity)


def test_same_surname_is_not_me():
    # 리뷰 지적: 영문 성만 같은 동료를 나로 보면 안 된다
    identity = me(display_names=['홍길동 (Gildong Hong)'])
    assert ident.match_addressee('Minsu Hong', identity) == ident.OTHER
    assert not ident.greets_me('Dear Minsu Hong,\n', identity)
    # 어느 단어가 성인지 모르므로 영문 이름 한 단어만으로는 나로 보지 않는다 (별칭으로 넣으면 된다)
    assert ident.match_addressee('Gildong', identity) == ident.OTHER
    assert ident.match_addressee('Gildong', me(aliases=['Gildong'])) == ident.ME


def test_short_full_name_needs_boundaries():
    # 리뷰 지적: 두 글자 이름이 다른 사람 이름이나 낱말의 일부와 겹치면 안 된다
    identity = ident.build(personal=[ME], display_names=['김진'], titles=['과장'])
    assert not ident.greets_me('김진우 과장님 안녕하세요', identity)
    assert not ident.greets_me('같이 김진행 부탁드립니다', identity)
    assert ident.greets_me('김진 과장님 안녕하세요', identity)
    assert ident.greets_me('김진님, 확인 부탁드립니다', identity)
    assert ident.match_addressee('김진우 과장님', identity) == ident.OTHER
    assert ident.match_addressee('김진 과장님', identity) == ident.ME


def test_multi_word_latin_alias_matches_body():
    # 리뷰 지적: 공백을 지운 별칭으로는 본문의 'Gildong Hong'을 찾지 못했다
    identity = me(aliases=['Gildong Hong'])
    assert ident.greets_me('Dear Gildong Hong,\nplease check.', identity)
    assert ident.match_addressee('Gildong Hong', identity) == ident.ME


# ---------------------------------------------------------------- 본문 인사말 (규칙 필터에서도 쓴다)

@pytest.mark.parametrize('body, expected', [
    ('길동 부장님, 안녕하세요.\n확인 부탁드립니다.', True),
    ('안녕하세요 홍길동 부장님\n', True),
    ('홍 부장님 안녕하세요', True),
    ('안녕하세요.\n\n길동님 확인 부탁드립니다.', True),
    ('김길동 부장님 안녕하세요', False),
    ('철수 과장님 안녕하세요.\n', False),
    ('안녕하세요.\n공유드립니다.', False),
    # 인용된 이전 메일의 인사말은 보지 않는다
    ('네 알겠습니다.\n\n2026년 11월 6일 (금) 오후 3:51, 이차장 <lee@x>님이 작성:\n> 길동 부장님,', False),
    ('전달드립니다.\n---------- Forwarded message ---------\n보낸사람: x\n길동 부장님', False),
    ('확인했습니다.\n> 길동 부장님', False),
])
def test_greets_me(body, expected):
    assert ident.greets_me(body, me()) is expected


def test_greeting_with_other_title_is_not_me():
    # 내 직함(부장)을 알면 같은 이름 + 다른 직함은 다른 사람이다. 직함을 모르면 받아들인다
    assert not ident.greets_me('길동 대리님 안녕하세요', me())
    assert ident.greets_me('길동 대리님 안녕하세요', me(titles=[]))
    assert ident.greets_me('길동님께, 확인 부탁드립니다', me())
    assert ident.greets_me('길동부장님 안녕하세요', me())


def test_greeting_is_only_the_first_lines():
    body = '\n'.join(['안녕하세요.'] + ['내용'] * 10 + ['길동 부장님 참고 바랍니다.'])
    assert not ident.greets_me(body, me())


def test_without_names_nobody_is_greeted():
    assert not ident.greets_me('길동 부장님', ident.build(personal=[ME]))


# ---------------------------------------------------------------- 담당자 판정

REPLY = Classification('답장필요', '보통', '고객 요청', addressee='')


def never():
    return False


def always():
    return True


@pytest.mark.parametrize('mode', [ident.PERSONAL_TO, ident.PERSONAL_CC])
def test_personal_mail_is_left_alone(mode):
    assert ident.resolve(REPLY, mode, ident.NONE, never) == REPLY


def test_group_request_greeting_me_stays_reply():
    assert ident.resolve(REPLY, ident.GROUP_TO, ident.ME, never).category == '답장필요'


def test_group_request_for_someone_else_is_fyi():
    c = Classification('답장필요', '보통', '고객 요청', addressee='김철수 과장님')
    result = ident.resolve(c, ident.GROUP_TO, ident.OTHER, never)
    assert result.category == '참고' and '김철수 과장님' in result.reason


def test_group_request_without_clue_is_fyi_unless_i_answer_that_group():
    # 2026-11-08 사용자 확인: 단서 없는 그룹 요청은 "내가 받은 메일이 아님"
    for match in (ident.NONE, ident.UNKNOWN):
        assert ident.resolve(REPLY, ident.GROUP_TO, match, never).category == '참고'
        assert ident.resolve(REPLY, ident.GROUP_TO, match, never,
                             owned_group=True).category == '확인필요'


def test_hidden_mail_is_never_dropped_to_fyi():
    # 리뷰 지적: 내 주소가 안 보이는 메일(숨은참조, 설정하지 않은 별칭)은 내 메일일 수 있다
    assert ident.resolve(REPLY, ident.HIDDEN, ident.NONE, never) == REPLY
    c = Classification('답장필요', '보통', '고객 요청', addressee='김철수 과장님')
    assert ident.resolve(c, ident.HIDDEN, ident.OTHER, never).category == '확인필요'


def test_group_request_in_my_thread_stays_reply():
    result = ident.resolve(REPLY, ident.GROUP_TO, ident.NONE, always)
    assert result.category == '답장필요' and '답한 적 있는 스레드' in result.reason


def test_my_thread_but_other_addressee_is_check():
    assert ident.resolve(REPLY, ident.GROUP_TO, ident.OTHER, always).category == '확인필요'


def test_non_reply_is_not_changed_and_thread_is_not_fetched():
    calls = []
    fyi = Classification('참고', '낮음', '공유')
    assert ident.resolve(fyi, ident.GROUP_TO, ident.NONE, lambda: calls.append(1)) == fyi
    assert ident.resolve(REPLY, ident.GROUP_TO, ident.ME, lambda: calls.append(1)).category == '답장필요'
    assert calls == []


def test_classifier_addressee_or_greeting_decides_match():
    identity = me()
    c = Classification('답장필요', '보통', 'x', addressee='')
    assert ident.addressee_match(c, mail(body='길동 부장님, 문의드립니다.'), identity) == ident.ME
    other = Classification('답장필요', '보통', 'x', addressee='김철수 과장님')
    assert ident.addressee_match(other, mail(body='문의드립니다.'), identity) == ident.OTHER


# ---------------------------------------------------------------- 불러오기와 상태 DB

def settings(monkeypatch, tmp_path, **env):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings.from_env()


def test_load_uses_gmail_display_name_and_remembers_it(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path, REDA_MY_TITLES='부장')
    gmail = FakeGmail(display_name='홍길동')
    with StateDB(tmp_path / 'state.db') as state:
        identity = ident.load(s, service=gmail, state=state, persist=True)
        assert identity.full_names == ('홍길동',) and identity.titles == ('부장',)
        assert state.get_profile('display_name') == '홍길동'
        assert [r['alias'] for r in state.aliases('confirmed')] == ['홍길동']
        # 다음 실행부터는 Gmail에 묻지 않는다
        gmail.calls.clear()
        ident.load(s, service=gmail, state=state, persist=True)
        assert ('sendAs.list',) not in gmail.calls


def test_load_without_persist_does_not_write(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path)
    with StateDB(tmp_path / 'state.db') as state:
        identity = ident.load(s, service=FakeGmail(display_name='홍길동'), state=state)
        assert identity.full_names == ('홍길동',)
        assert state.get_profile('display_name') is None


def test_config_name_overrides_and_rejected_alias_is_ignored(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path, REDA_MY_NAME='홍길동', REDA_MY_ALIASES='Gildong,GD')
    with StateDB(tmp_path / 'state.db') as state:
        ident.load(s, state=state, persist=True)
        state.set_alias_status('GD', 'alias', 'rejected')
        identity = ident.load(s, state=state)
        assert ('gildong',) in identity.latin_names and ('gd',) not in identity.latin_names


def test_learned_alias_in_state_is_used(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path, REDA_MY_NAME='홍길동')
    with StateDB(tmp_path / 'state.db') as state:
        state.upsert_alias('Andy', 'alias', 'greeting', status='confirmed')
        state.upsert_alias('Bob', 'alias', 'greeting', status='candidate')
        identity = ident.load(s, state=state)
        assert ('andy',) in identity.latin_names and ('bob',) not in identity.latin_names


def test_missing_display_name_is_checked_once_a_day(monkeypatch, tmp_path):
    from datetime import date
    s = settings(monkeypatch, tmp_path)
    gmail = FakeGmail(display_name='')
    with StateDB(tmp_path / 'state.db') as state:
        ident.load(s, service=gmail, state=state, persist=True, today=date(2026, 11, 8))
        gmail.calls.clear()
        ident.load(s, service=gmail, state=state, persist=True, today=date(2026, 11, 8))
        assert ('sendAs.list',) not in gmail.calls
        ident.load(s, service=gmail, state=state, persist=True, today=date(2026, 11, 9))
        assert ('sendAs.list',) in gmail.calls


def test_display_name_failure_is_ignored(monkeypatch, tmp_path):
    import httplib2
    from googleapiclient.errors import HttpError

    class NoSettings(FakeGmail):
        def _send_as_list(self, userId):
            raise HttpError(httplib2.Response({'status': 403}), b'{"error": "forbidden"}')

    identity = ident.load(settings(monkeypatch, tmp_path), service=NoSettings())
    assert not identity.has_names


def test_display_name_falls_back_to_sent_mail_from(monkeypatch, tmp_path):
    sent = make_message('s1', From='"홍길동" <gdhong@corp.example.com>', labels=('SENT',))
    gmail = FakeGmail(display_name='', query_messages=[{'id': 's1'}], messages={'s1': sent})
    identity = ident.load(settings(monkeypatch, tmp_path), service=gmail)
    assert identity.full_names == ('홍길동',)
    assert ('messages.list', 'in:sent') in gmail.calls


# ---------------------------------------------------------------- 사용자 정보는 .env → 상태 DB → Gmail (결정 6-5)

def test_without_env_address_gmail_account_is_used_and_remembered(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path)
    monkeypatch.setenv('REDA_MY_ADDRESSES', '')
    s = Settings.from_env()
    gmail = FakeGmail()                                  # getProfile → me@corp.com
    with StateDB(tmp_path / 'state.db') as state:
        identity = ident.load(s, service=gmail, state=state, persist=True)
        assert identity.personal == {'me@corp.com'}
        assert state.get_profile('email') == 'me@corp.com'
        # 다음부터는 Gmail 없이 상태 DB에서 읽는다
        assert ident.load(s, state=state).personal == {'me@corp.com'}


def test_env_values_seed_the_state_db(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path, REDA_MY_GROUPS=GROUP, REDA_MY_TITLES='부장')
    with StateDB(tmp_path / 'state.db') as state:
        ident.load(s, state=state, persist=True)
        # .env에서 사용자 정보를 지워도 상태 DB 값으로 같은 판정을 한다
        for key in ('REDA_MY_ADDRESSES', 'REDA_MY_GROUPS', 'REDA_MY_TITLES'):
            monkeypatch.setenv(key, '')
        identity = ident.load(Settings.from_env(), state=state)
        assert identity.personal == {ME} and identity.groups == {GROUP}
        assert identity.titles == ('부장',)


def test_env_overrides_state_db(monkeypatch, tmp_path):
    with StateDB(tmp_path / 'state.db') as state:
        state.set_profile('email', 'old@corp.example.com', source='gmail')
        identity = ident.load(settings(monkeypatch, tmp_path), state=state)
        assert identity.personal == {ME}
