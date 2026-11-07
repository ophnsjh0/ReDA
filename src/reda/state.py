"""ReDA 상태 DB (SQLite, 사용자마다 REDA_DATA_DIR/state.db 하나).

무엇을 처리했는지, 사용자가 분류를 어떻게 고쳤는지를 남긴다. 뒤의 두 가지(label_events,
missed_replies)는 분류 정확도 측정(목표: 답장필요 누락률 5% 이하)에 쓴다.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    category    TEXT NOT NULL,          -- 답장필요 / 참고 / 확인필요
    urgency     TEXT,                   -- 높음 / 보통 / 낮음
    reason      TEXT,
    source      TEXT NOT NULL,          -- rule(규칙 필터) / classifier(분류기)
    labeled_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id    TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    evidence    TEXT NOT NULL,          -- JSON 목록: 초안 끝 참고 블록에 적은 근거
    created_at  TEXT NOT NULL
);

-- 사용자가 분류 라벨을 붙이거나 뗀 기록. ReDA가 붙인 라벨은 남기지 않는다.
CREATE TABLE IF NOT EXISTS label_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    label       TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('added', 'removed')),
    history_id  TEXT,
    observed_at TEXT NOT NULL,
    UNIQUE (message_id, label, action, history_id)
);

-- 답장필요가 아니었던 스레드에 사용자가 직접 답장한 기록 (= 놓친 메일).
-- prior_category가 NULL이면 ReDA가 처리한 적 없는 스레드다.
CREATE TABLE IF NOT EXISTS missed_replies (
    sent_message_id TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    prior_category  TEXT,
    observed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 2: 처리에 실패한 메일. 다음 실행에서 다시 시도하고, 성공하면 지운다.
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS failures (
    message_id  TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    last_error  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class StateDB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._migrate()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @classmethod
    def open(cls, settings):
        return cls(settings.data_dir / 'state.db')

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def transaction(self):
        with self.conn:
            yield self.conn

    def _migrate(self):
        version = self.conn.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f'상태 DB 버전({version})이 이 ReDA({SCHEMA_VERSION})보다 새롭습니다: {self.path}')
        if version < 1:
            with self.conn:
                self.conn.executescript(_SCHEMA)
                self.conn.execute('PRAGMA user_version = 1')
        if version < 2:
            with self.conn:
                self.conn.executescript(_SCHEMA_V2)
                self.conn.execute('PRAGMA user_version = 2')

    # ------------------------------------------------------------ sync
    def get_sync(self, key, default=None):
        row = self.conn.execute('SELECT value FROM sync WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default

    def set_sync(self, key, value):
        with self.conn:
            self.conn.execute(
                'INSERT INTO sync(key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, str(value)))

    @property
    def history_id(self):
        return self.get_sync('history_id')

    # ------------------------------------------------------------ messages
    def record_classification(self, message_id, thread_id, category, *, urgency=None,
                              reason=None, source='classifier'):
        with self.conn:
            self.conn.execute(
                'INSERT INTO messages(message_id, thread_id, category, urgency, reason, source,'
                ' labeled_at) VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(message_id) DO UPDATE SET category = excluded.category,'
                ' urgency = excluded.urgency, reason = excluded.reason,'
                ' source = excluded.source, labeled_at = excluded.labeled_at',
                (message_id, thread_id, category, urgency, reason, source, _now()))

    def get_message(self, message_id):
        return self.conn.execute(
            'SELECT * FROM messages WHERE message_id = ?', (message_id,)).fetchone()

    def is_processed(self, message_id):
        return self.get_message(message_id) is not None

    def thread_categories(self, thread_id):
        rows = self.conn.execute(
            'SELECT category FROM messages WHERE thread_id = ? ORDER BY labeled_at',
            (thread_id,)).fetchall()
        return [r['category'] for r in rows]

    # ------------------------------------------------------------ drafts
    def record_draft(self, draft_id, message_id, thread_id, evidence):
        with self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO drafts(draft_id, message_id, thread_id, evidence,'
                ' created_at) VALUES (?, ?, ?, ?, ?)',
                (draft_id, message_id, thread_id, json.dumps(list(evidence), ensure_ascii=False),
                 _now()))

    def drafts_for_thread(self, thread_id):
        rows = self.conn.execute(
            'SELECT * FROM drafts WHERE thread_id = ? ORDER BY created_at', (thread_id,)).fetchall()
        return [{**dict(r), 'evidence': json.loads(r['evidence'])} for r in rows]

    # ------------------------------------------------------------ 측정 기록
    def record_label_event(self, message_id, thread_id, label, action, history_id=None):
        """새로 기록했으면 True, 이미 있던 기록이면 False."""
        # INSERT OR IGNORE는 CHECK 위반도 조용히 건너뛰므로 여기서 먼저 막는다
        if action not in ('added', 'removed'):
            raise ValueError(f'라벨 동작은 added/removed만 됩니다: {action!r}')
        with self.conn:
            cur = self.conn.execute(
                'INSERT OR IGNORE INTO label_events(message_id, thread_id, label, action,'
                ' history_id, observed_at) VALUES (?, ?, ?, ?, ?, ?)',
                (message_id, thread_id, label, action, history_id, _now()))
        return cur.rowcount == 1

    def label_events(self):
        return self.conn.execute('SELECT * FROM label_events ORDER BY id').fetchall()

    def record_missed_reply(self, sent_message_id, thread_id, prior_category):
        with self.conn:
            cur = self.conn.execute(
                'INSERT OR IGNORE INTO missed_replies(sent_message_id, thread_id, prior_category,'
                ' observed_at) VALUES (?, ?, ?, ?)',
                (sent_message_id, thread_id, prior_category, _now()))
        return cur.rowcount == 1

    def missed_replies(self):
        return self.conn.execute(
            'SELECT * FROM missed_replies ORDER BY observed_at').fetchall()

    # ------------------------------------------------------------ 실패
    def record_failure(self, message_id, thread_id, error):
        with self.conn:
            self.conn.execute(
                'INSERT INTO failures(message_id, thread_id, attempts, last_error, updated_at)'
                ' VALUES (?, ?, 1, ?, ?) ON CONFLICT(message_id) DO UPDATE SET'
                ' attempts = attempts + 1, last_error = excluded.last_error,'
                ' updated_at = excluded.updated_at',
                (message_id, thread_id, str(error)[:500], _now()))

    def clear_failure(self, message_id):
        with self.conn:
            self.conn.execute('DELETE FROM failures WHERE message_id = ?', (message_id,))

    def failures(self, max_attempts=None):
        """max_attempts를 주면 아직 그만큼 시도하지 않은 것만 돌려준다."""
        if max_attempts is None:
            return self.conn.execute('SELECT * FROM failures ORDER BY updated_at').fetchall()
        return self.conn.execute(
            'SELECT * FROM failures WHERE attempts < ? ORDER BY updated_at',
            (max_attempts,)).fetchall()
