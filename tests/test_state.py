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
