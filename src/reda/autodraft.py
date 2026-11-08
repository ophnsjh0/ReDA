"""자동 초안 (3단계, 결정 3-2: 분류 직후). `reda draft-pending`.

`답장필요`로 분류됐고 아직 초안·보류가 없는 메일을 골라, 초안 에이전트(Hermes 프로필)에게 하나씩 맡긴다.
에이전트는 `reda-mcp` 도구로 스레드를 읽고 초안을 저장하거나 보류한다. 여기서는 맡기기 전에 걸러 내고,
끝난 뒤 결과를 상태 DB(`draft_jobs`)에 남기기만 한다.

맡기기 전에 건너뛰는 메일 (비용과 오판을 줄인다)
- 사용자가 이미 그 스레드에 답장했다
- 사용자가 `답장필요` 라벨을 뗐다 (라벨을 바꿨다)
- 메일이 지워졌다

한 번에 맡기는 메일 수(`REDA_DRAFT_MAX_PER_RUN`)와 메일마다 기다리는 시간(`REDA_DRAFT_TIMEOUT`)에
상한을 둔다. 실패한 메일은 다음 실행에서 다시 하되 3번까지만 한다.
"""
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from googleapiclient.errors import HttpError

from reda.gmail import client
from reda.gmail.client import LABEL_REPLY

MAX_ATTEMPTS = 3
LOOKBACK_DAYS = 3            # 이보다 오래전에 분류한 메일에는 초안을 쓰지 않는다
PROMPT = 'thread_id {thread_id} 스레드의 답장 초안을 지침대로 써서 저장해 줘.'


@dataclass
class DraftResult:
    drafted: int = 0
    held: int = 0
    skipped: int = 0
    failed: int = 0
    details: list = field(default_factory=list)     # (message_id, 상태, 설명)


def hermes_command(settings):
    found = settings.hermes_bin or shutil.which('hermes')
    if not found:
        candidate = Path.home() / '.local' / 'bin' / 'hermes'
        found = str(candidate) if candidate.exists() else ''
    if not found:
        raise FileNotFoundError('hermes 명령을 찾지 못함 (REDA_HERMES_BIN에 경로를 적는다)')
    return found


def run_agent(settings, thread_id):
    """에이전트를 한 번 돌리고 마지막 답 한 줄을 돌려준다. 시간을 넘기면 TimeoutError."""
    command = [hermes_command(settings), '-p', settings.draft_profile, '-z',
               PROMPT.format(thread_id=thread_id)]
    try:
        out = subprocess.run(command, capture_output=True, text=True,
                             timeout=settings.draft_timeout, cwd=str(Path.home()))
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f'{settings.draft_timeout}초 안에 끝나지 않음') from exc
    lines = [line for line in (out.stdout or '').splitlines() if line.strip()]
    if out.returncode != 0:
        tail = (out.stderr or '').strip().splitlines()[-1:] or ['']
        raise RuntimeError(f'hermes 종료 코드 {out.returncode}: {tail[0][:200]}')
    return lines[-1] if lines else ''


def _skip_reason(service, message_id, thread_id, reply_label_id):
    try:
        message = client.get_message_metadata(service, message_id, ('Subject',))
    except HttpError as exc:
        if getattr(exc.resp, 'status', None) == 404:
            return '메일이 지워짐'
        raise
    if reply_label_id and reply_label_id not in message.get('labelIds', []):
        return '사용자가 답장필요 라벨을 뗌'
    if client.thread_replied_after(service, thread_id, int(message.get('internalDate') or 0)):
        return '이미 답장함'
    return ''


def _held(state, thread_id):
    for row in state.feedback('draft_hold'):
        try:
            if json.loads(row['detail'] or '{}').get('thread_id') == thread_id:
                return True
        except ValueError:
            continue
    return False


def draft_pending(service, state, settings, *, agent=run_agent, now=None, dry_run=False):
    """초안을 기다리는 메일을 에이전트에게 맡긴다. agent(settings, thread_id) -> 마지막 답.
    dry_run이면 맡길 메일과 건너뛸 이유만 돌려주고 아무것도 기록·실행하지 않는다."""
    result = DraftResult()
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=LOOKBACK_DAYS)).isoformat(timespec='seconds')
    candidates = state.draft_candidates(since=since, max_attempts=MAX_ATTEMPTS)
    if not candidates:
        return result
    labels = {name: lid for lid, name in
              ((l['id'], l['name']) for l in client.execute(
                  service.users().labels().list(userId='me')).get('labels', []))}
    reply_label_id = labels.get(LABEL_REPLY)
    taken = 0
    for row in candidates:
        if taken >= settings.draft_max_per_run:
            break
        message_id, thread_id = row['message_id'], row['thread_id']
        reason = _skip_reason(service, message_id, thread_id, reply_label_id)
        if dry_run:
            result.details.append((message_id, 'skip' if reason else 'would_draft', reason))
            taken += 0 if reason else 1
            continue
        if reason:
            state.record_draft_job(message_id, thread_id, 'skipped', reason)
            result.skipped += 1
            result.details.append((message_id, 'skipped', reason))
            continue
        taken += 1
        try:
            answer = agent(settings, thread_id)
        except Exception as exc:  # 에이전트 실패는 이 메일만의 실패로 남기고 다음으로
            state.record_draft_job(message_id, thread_id, 'failed', f'{exc.__class__.__name__}: {exc}')
            result.failed += 1
            result.details.append((message_id, 'failed', str(exc)))
            continue
        # 결과는 에이전트의 말이 아니라 상태 DB로 판단한다 (reda-mcp가 저장·보류를 기록한다)
        if state.drafts_for_thread(thread_id):
            status = 'drafted'
        elif _held(state, thread_id):
            status = 'held'
        else:
            status = 'failed'
        state.record_draft_job(message_id, thread_id, status, answer)
        setattr(result, status, getattr(result, status) + 1)
        result.details.append((message_id, status, answer))
    return result
