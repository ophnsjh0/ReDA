"""ReDA 상태 DB (SQLite, 사용자마다 REDA_DATA_DIR/state.db 하나).

무엇을 처리했는지, 사용자가 분류를 어떻게 고쳤는지를 남긴다. 뒤의 두 가지(label_events,
missed_replies)는 분류 정확도 측정(목표: 답장필요 누락률 5% 이하)에 쓴다.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    category    TEXT NOT NULL,          -- 답장필요 / 참고 / 확인필요
    urgency     TEXT,                   -- 높음 / 보통 / 낮음
    reason      TEXT,
    source      TEXT NOT NULL,          -- rule(규칙 필터) / classifier:<제공자/모델>
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


# 3: 사용자 정보 (결정 2-7, 구현계획 "사용자 정보 저장"). 메일 본문은 저장하지 않는다.
_SCHEMA_V3 = """
ALTER TABLE messages ADD COLUMN recipient_mode TEXT;   -- personal_to / group_to / personal_cc / group_cc / hidden
ALTER TABLE messages ADD COLUMN addressee TEXT;        -- 분류기가 추출한 인사말 대상
ALTER TABLE messages ADD COLUMN addressee_match TEXT;  -- me / other / none / unknown

CREATE TABLE IF NOT EXISTS user_profile (
    key         TEXT PRIMARY KEY,       -- display_name, titles, ...
    value       TEXT NOT NULL,
    source      TEXT NOT NULL,          -- config / sendas / signature / user / directory
    updated_at  TEXT NOT NULL
);

-- 나를 부르는 이름. 후보는 쓰면서 쌓이고, 확정된 것만 판정에 쓴다
CREATE TABLE IF NOT EXISTS identity_aliases (
    alias       TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- display_name / alias
    source      TEXT NOT NULL,          -- config / sendas / greeting / user / directory
    status      TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    hits        INTEGER NOT NULL DEFAULT 0,
    last_seen   TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (alias, kind)
);

-- 내가 맡은 것 (고객사, 제조사, 제품, 케이스 번호, 그룹). 6단계부터 채운다
CREATE TABLE IF NOT EXISTS responsibilities (
    value       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('customer', 'vendor', 'product', 'case', 'group')),
    source      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    hits        INTEGER NOT NULL DEFAULT 0,
    last_seen   TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (value, kind)
);

-- 사용자 피드백 ([내 일 아님] 버튼, 별칭 확인 등). 본문 없이 메일 ID와 특징만 남긴다
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT,
    kind        TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',   -- JSON
    observed_at TEXT NOT NULL
);
"""


# 4: 분류기 판단 값과 우선순위 (결정 2-8). detail은 질문별 확률 같은 JSON이고 본문은 넣지 않는다
_SCHEMA_V4 = """
ALTER TABLE messages ADD COLUMN classifier_detail TEXT;
ALTER TABLE messages ADD COLUMN priority REAL;
"""


# 5: 자동 초안 작업 (결정 3-2). 메일마다 시도 결과를 남겨 같은 메일을 계속 다시 하지 않는다
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS draft_jobs (
    message_id  TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('drafted', 'held', 'skipped', 'failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    detail      TEXT,                   -- 건너뛴 이유, 에이전트의 마지막 답, 오류
    updated_at  TEXT NOT NULL
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _statements(script):
    """SQL 묶음을 문장으로 나눈다. 주석(--)은 지운다. 문자열 안의 ;는 쓰지 않는다."""
    lines = [line.split('--', 1)[0] for line in script.splitlines()]
    return [s.strip() for s in '\n'.join(lines).split(';') if s.strip()]


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
        if version < 3:
            self._migrate_atomic(3, _SCHEMA_V3)
        if version < 4:
            self._migrate_atomic(4, _SCHEMA_V4)
        if version < 5:
            self._migrate_atomic(5, _SCHEMA_V5)

    def _migrate_atomic(self, version, script):
        """한 트랜잭션으로 묶는다. sqlite3 모듈은 DDL 앞에 BEGIN을 넣지 않으므로 직접 연다.
        도중에 실패하면 되돌리고, 이미 있는 열은 건너뛰어 다시 실행해도 된다."""
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            columns = {r['name'] for r in self.conn.execute('PRAGMA table_info(messages)')}
            for statement in _statements(script):
                added = re.match(r'ALTER TABLE messages ADD COLUMN (\w+)', statement)
                if added and added.group(1) in columns:
                    continue
                self.conn.execute(statement)
            self.conn.execute(f'PRAGMA user_version = {int(version)}')
            self.conn.execute('COMMIT')
        except BaseException:
            self.conn.execute('ROLLBACK')
            raise

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
                              reason=None, source='classifier', recipient_mode=None,
                              addressee=None, addressee_match=None, detail=None,
                              priority=None):
        detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
        with self.conn:
            self.conn.execute(
                'INSERT INTO messages(message_id, thread_id, category, urgency, reason, source,'
                ' labeled_at, recipient_mode, addressee, addressee_match, classifier_detail,'
                ' priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(message_id) DO UPDATE SET category = excluded.category,'
                ' urgency = excluded.urgency, reason = excluded.reason,'
                ' source = excluded.source, labeled_at = excluded.labeled_at,'
                ' recipient_mode = excluded.recipient_mode, addressee = excluded.addressee,'
                ' addressee_match = excluded.addressee_match,'
                ' classifier_detail = excluded.classifier_detail, priority = excluded.priority',
                (message_id, thread_id, category, urgency, reason, source, _now(),
                 recipient_mode, addressee, addressee_match, detail_json, priority))

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

    # ------------------------------------------------------------ 사용자 정보
    def get_profile(self, key):
        row = self.conn.execute('SELECT value FROM user_profile WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None

    def set_profile(self, key, value, *, source):
        with self.conn:
            self.conn.execute(
                'INSERT INTO user_profile(key, value, source, updated_at) VALUES (?, ?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source,'
                ' updated_at = excluded.updated_at', (key, str(value), source, _now()))

    def profile_rows(self):
        return self.conn.execute('SELECT * FROM user_profile ORDER BY key').fetchall()

    # ------------------------------------------------------------ 자동 초안
    def draft_candidates(self, *, since, max_attempts):
        """답장필요로 분류됐고 초안·보류가 아직 없는 메일 (오래된 것부터)."""
        return self.conn.execute(
            "SELECT m.message_id, m.thread_id FROM messages m "
            "LEFT JOIN draft_jobs j ON j.message_id = m.message_id "
            "WHERE m.category = '답장필요' AND m.labeled_at >= ? "
            "AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.message_id = m.message_id) "
            "AND (j.message_id IS NULL OR (j.status = 'failed' AND j.attempts < ?)) "
            "ORDER BY m.labeled_at", (since, max_attempts)).fetchall()

    def record_draft_job(self, message_id, thread_id, status, detail=''):
        if status not in ('drafted', 'held', 'skipped', 'failed'):
            raise ValueError(f'초안 작업 상태가 잘못됨: {status!r}')
        with self.conn:
            self.conn.execute(
                'INSERT INTO draft_jobs(message_id, thread_id, status, attempts, detail, updated_at)'
                ' VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT(message_id) DO UPDATE SET'
                ' status = excluded.status, attempts = attempts + 1, detail = excluded.detail,'
                ' updated_at = excluded.updated_at',
                (message_id, thread_id, status, str(detail)[:500], _now()))

    def draft_job(self, message_id):
        return self.conn.execute('SELECT * FROM draft_jobs WHERE message_id = ?',
                                 (message_id,)).fetchone()

    def record_feedback(self, kind, *, message_id=None, detail=None):
        """사용자·에이전트 피드백 (초안 보류, [내 일 아님] 버튼 등). 본문은 넣지 않는다."""
        with self.conn:
            self.conn.execute(
                'INSERT INTO feedback(message_id, kind, detail, observed_at) VALUES (?, ?, ?, ?)',
                (message_id, kind, json.dumps(detail or {}, ensure_ascii=False), _now()))

    def feedback(self, kind=None):
        if kind is None:
            return self.conn.execute('SELECT * FROM feedback ORDER BY id').fetchall()
        return self.conn.execute('SELECT * FROM feedback WHERE kind = ? ORDER BY id',
                                 (kind,)).fetchall()

    def feedback_for(self, message_id, kinds):
        marks = ','.join('?' * len(kinds))
        return self.conn.execute(
            f'SELECT * FROM feedback WHERE message_id = ? AND kind IN ({marks}) ORDER BY id',
            (message_id, *kinds)).fetchall()

    def upsert_alias(self, alias, kind, source, status='confirmed'):
        """없으면 더한다. 이미 있으면 상태를 바꾸지 않는다 (사용자가 거절한 별칭을 되살리지 않는다)."""
        if status not in ('candidate', 'confirmed', 'rejected'):
            raise ValueError(f'별칭 상태는 candidate/confirmed/rejected만 됩니다: {status!r}')
        with self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO identity_aliases(alias, kind, source, status, created_at)'
                ' VALUES (?, ?, ?, ?, ?)', (alias, kind, source, status, _now()))

    def set_alias_status(self, alias, kind, status):
        if status not in ('candidate', 'confirmed', 'rejected'):
            raise ValueError(f'별칭 상태는 candidate/confirmed/rejected만 됩니다: {status!r}')
        with self.conn:
            self.conn.execute('UPDATE identity_aliases SET status = ? WHERE alias = ? AND kind = ?',
                              (status, alias, kind))

    def aliases(self, status='confirmed'):
        return self.conn.execute(
            'SELECT * FROM identity_aliases WHERE status = ? ORDER BY created_at, alias',
            (status,)).fetchall()

    # ------------------------------------------------------------ drafts
    def record_draft(self, draft_id, message_id, thread_id, evidence):
        with self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO drafts(draft_id, message_id, thread_id, evidence,'
                ' created_at) VALUES (?, ?, ?, ?, ?)',
                (draft_id, message_id, thread_id, json.dumps(list(evidence), ensure_ascii=False),
                 _now()))

    def draft_for_message(self, message_id):
        """이 메일에 답하는 ReDA 초안 (메일 단위로 하나). 같은 스레드에 새 고객 메일이 오면 새로 만든다."""
        return self.conn.execute('SELECT * FROM drafts WHERE message_id = ? LIMIT 1',
                                 (message_id,)).fetchone()

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
