import json
import sqlite3
import stat

import pytest

from reda.state import SCHEMA_VERSION, StateDB


@pytest.fixture
def state(tmp_path):
    with StateDB(tmp_path / 'state.db') as db:
        yield db


def test_creates_schema_and_private_file(tmp_path):
    path = tmp_path / 'sub' / 'state.db'
    StateDB(path).close()
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    conn = sqlite3.connect(path)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_reopen_keeps_data(tmp_path):
    path = tmp_path / 'state.db'
    with StateDB(path) as db:
        db.set_sync('history_id', 1234)
    with StateDB(path) as db:
        assert db.history_id == '1234'


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / 'state.db'
    conn = sqlite3.connect(path)
    conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
    conn.close()
    with pytest.raises(RuntimeError):
        StateDB(path)


def test_classification_upsert(state):
    state.record_classification('m1', 't1', '참고', source='rule')
    state.record_classification('m1', 't1', '답장필요', urgency='높음', reason='장애 문의')
    row = state.get_message('m1')
    assert (row['category'], row['urgency'], row['source']) == ('답장필요', '높음', 'classifier')
    assert state.is_processed('m1') and not state.is_processed('m2')
    assert state.thread_categories('t1') == ['답장필요']


def test_draft_evidence_roundtrip(state):
    state.record_draft('d1', 'm1', 't1', ['10/28 고객사 A 메일', 'session_1105.txt 42~57행'])
    [draft] = state.drafts_for_thread('t1')
    assert draft['evidence'] == ['10/28 고객사 A 메일', 'session_1105.txt 42~57행']


def test_measurement_records_are_idempotent(state):
    assert state.record_label_event('m1', 't1', '답장필요', 'added', '500')
    assert not state.record_label_event('m1', 't1', '답장필요', 'added', '500')
    assert state.record_missed_reply('s1', 't1', '참고')
    assert not state.record_missed_reply('s1', 't1', '참고')
    assert len(state.label_events()) == 1
    assert len(state.missed_replies()) == 1


def test_rejects_unknown_label_action(state):
    with pytest.raises(ValueError):
        state.record_label_event('m1', 't1', '답장필요', 'renamed')


def test_v1_db_is_upgraded_with_failures_table(tmp_path):
    import sqlite3

    from reda.state import _SCHEMA
    path = tmp_path / 'state.db'
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute('PRAGMA user_version = 1')
    conn.execute("INSERT INTO sync(key, value) VALUES ('history_id', '42')")
    conn.commit()
    conn.close()
    with StateDB(path) as db:
        assert db.history_id == '42'
        db.record_failure('m1', 't1', 'boom')
        db.record_failure('m1', 't1', 'boom again')
        row = db.failures()[0]
        assert (row['attempts'], row['last_error']) == (2, 'boom again')
        assert db.failures(max_attempts=2) == []


def test_v2_db_is_upgraded_with_user_tables(tmp_path):
    import sqlite3

    from reda.state import _SCHEMA, _SCHEMA_V2
    path = tmp_path / 'state.db'
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA + _SCHEMA_V2)
    conn.execute('PRAGMA user_version = 2')
    conn.execute("INSERT INTO messages(message_id, thread_id, category, source, labeled_at)"
                 " VALUES ('m1', 't1', '참고', 'rule', 'x')")
    conn.commit()
    conn.close()
    with StateDB(path) as state:
        assert state.get_message('m1')['recipient_mode'] is None
        state.record_classification('m2', 't2', '확인필요', recipient_mode='group_to',
                                    addressee='김철수 과장님', addressee_match='other')
        assert state.get_message('m2')['addressee_match'] == 'other'
        state.set_profile('display_name', '홍길동', source='sendas')
        assert state.get_profile('display_name') == '홍길동'
        tables = {r[0] for r in state.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'user_profile', 'identity_aliases', 'responsibilities', 'feedback'} <= tables


def test_alias_status_is_kept_on_upsert(tmp_path):
    with StateDB(tmp_path / 'state.db') as state:
        state.upsert_alias('GD', 'alias', 'config')
        state.set_alias_status('GD', 'alias', 'rejected')
        state.upsert_alias('GD', 'alias', 'config')          # 거절한 별칭을 되살리지 않는다
        assert [r['alias'] for r in state.aliases('rejected')] == ['GD']
        with pytest.raises(ValueError):
            state.upsert_alias('X', 'alias', 'config', status='maybe')


def test_v3_migration_is_atomic_and_rerunnable(tmp_path, monkeypatch):
    """리뷰 지적: 이전 도중 실패하면 되돌려야 하고, 열 일부가 이미 있어도 다시 실행할 수 있어야 한다."""
    import sqlite3

    from reda import state as state_module
    from reda.state import _SCHEMA, _SCHEMA_V2
    path = tmp_path / 'state.db'
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA + _SCHEMA_V2)
    conn.execute('PRAGMA user_version = 2')
    conn.commit()
    conn.close()

    broken = state_module._SCHEMA_V3 + '\nCREATE TABLE broken (;\n'
    monkeypatch.setattr(state_module, '_SCHEMA_V3', broken)
    with pytest.raises(sqlite3.OperationalError):
        StateDB(path)
    conn = sqlite3.connect(path)
    columns = {r[1] for r in conn.execute('PRAGMA table_info(messages)')}
    assert 'recipient_mode' not in columns                     # 되돌려졌다
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
    # 예전 코드로 열 하나만 들어간 DB도 다시 이전할 수 있다
    conn.execute('ALTER TABLE messages ADD COLUMN recipient_mode TEXT')
    conn.commit()
    conn.close()
    monkeypatch.undo()
    with StateDB(path) as db:
        assert db.conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_v4_stores_classifier_detail_and_priority(tmp_path):
    with StateDB(tmp_path / 'state.db') as state:
        state.record_classification('m1', 't1', '답장필요', source='classifier:jev/jev-1.14.0',
                                    detail={'asks_reply': 0.91, 'model': 'jev-1.14.0'},
                                    priority=1.5)
        row = state.get_message('m1')
        assert json.loads(row['classifier_detail'])['asks_reply'] == 0.91
        assert row['priority'] == 1.5
