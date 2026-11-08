from pathlib import Path

from reda.config import Settings


def test_defaults_follow_data_dir(monkeypatch, tmp_path):
    for name in ('REDA_GMAIL_CREDENTIALS_FILE', 'REDA_GMAIL_TOKEN_FILE',
                 'REDA_REFERENCE_DOCS_DIR', 'REDA_REFERENCE_DB'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    s = Settings.from_env()
    assert s.data_dir == tmp_path
    assert s.gmail_token_file == tmp_path / 'token.json'
    assert s.gmail_credentials_file == tmp_path / 'credentials.json'
    assert s.reference_db == tmp_path / 'references.db'


def test_empty_values_fall_back_to_defaults(monkeypatch, tmp_path):
    """.env.example을 그대로 복사하면 빈 값(KEY=)이 들어온다. 빈 값은 기본값을 써야 한다."""
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_GMAIL_TOKEN_FILE', '')
    monkeypatch.setenv('REDA_REFERENCE_DOCS_DIR', '')
    s = Settings.from_env()
    assert s.gmail_token_file == tmp_path / 'token.json'
    assert s.reference_docs_dir == tmp_path / 'reference_docs'


def test_lists_and_mapping(monkeypatch):
    monkeypatch.setenv('REDA_MY_ADDRESSES', 'Me@Corp.com, me2@corp.com,')
    monkeypatch.setenv('REDA_CUSTOMER_DOMAINS', 'A.com=고객사A, b.co.kr=고객사B, broken')
    s = Settings.from_env()
    assert s.my_addresses == ('me@corp.com', 'me2@corp.com')
    assert s.customer_domains == {'a.com': '고객사A', 'b.co.kr': '고객사B'}


def test_home_is_expanded(monkeypatch):
    monkeypatch.setenv('REDA_DATA_DIR', '~/.reda-test')
    assert Settings.from_env().data_dir == Path.home() / '.reda-test'


import pytest  # noqa: E402

from reda.config import ConfigError  # noqa: E402


@pytest.mark.parametrize('value', ['me.corp.example.com', 'me@', '@corp.example.com', 'a@b@c'])
def test_my_addresses_must_be_mail_addresses(monkeypatch, tmp_path, value):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', f'ok@corp.example.com,{value}')
    with pytest.raises(ConfigError, match='REDA_MY_ADDRESSES'):
        Settings.from_env()


def test_bad_config_is_reported_by_cli(monkeypatch, tmp_path, capsys):
    from reda import cli
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_ENV_FILE', str(tmp_path / 'none.env'))
    monkeypatch.setenv('REDA_MY_ADDRESSES', 'me.corp.example.com')
    assert cli.main(['classifier']) == 1
    assert 'REDA_MY_ADDRESSES' in capsys.readouterr().err



@pytest.mark.parametrize('value', ['70', '-0.1', 'nan', 'abc'])
def test_jev_probability_must_be_between_0_and_1(monkeypatch, tmp_path, value):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_JEV_MIN_PROBABILITY', value)
    with pytest.raises(ConfigError, match='REDA_JEV_MIN_PROBABILITY'):
        Settings.from_env()


def test_groups_and_name_profile(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_GROUPS', 'Team@Corp.example.com')
    monkeypatch.setenv('REDA_MY_NAME', ' 홍길동 ')
    monkeypatch.setenv('REDA_MY_TITLES', '부장, 팀장')
    monkeypatch.setenv('REDA_MY_ALIASES', 'Gildong')
    s = Settings.from_env()
    assert s.my_groups == ('team@corp.example.com',)
    assert (s.my_name, s.my_titles, s.my_aliases) == ('홍길동', ('부장', '팀장'), ('Gildong',))


def test_groups_must_be_mail_addresses(monkeypatch, tmp_path):
    import pytest

    from reda.config import ConfigError
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_GROUPS', 'team.corp.example.com')
    with pytest.raises(ConfigError, match='REDA_MY_GROUPS'):
        Settings.from_env()
