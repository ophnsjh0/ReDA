"""Google 캘린더 등록 (9단계). 사용자가 일정 버튼 봇에서 [캘린더에 등록]을 누른 제안만 넣는다.

- 권한은 `calendar.events` 하나만 받고, Gmail 토큰과 다른 파일(`calendar_token.json`)에 둔다.
  Gmail 토큰에 범위를 더하면 기존 토큰 갱신이 실패해 15분 자동 실행이 멈출 수 있다.
- 기본 캘린더(결정 9-3), 알림 하루 전 + 30분 전(결정 9-2).
- 일정 id를 제안마다 정해 두어(`reda<제안 번호><메일 id>`) 버튼을 두 번 눌러도 한 번만 생긴다.
- 지우기·고치기 도구는 두지 않는다. 만드는 것만 한다.
"""
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from reda.context import gmail_link

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
REMINDERS = [1440, 30]          # 분: 하루 전, 30분 전 (결정 9-2)


class CalendarAuthError(Exception):
    pass


def load_credentials(settings, interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token_path = settings.calendar_token_file
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        if not settings.gmail_credentials_file.exists():
            raise CalendarAuthError(f'OAuth 클라이언트 파일이 없습니다: {settings.gmail_credentials_file}')
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(settings.gmail_credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
    else:
        raise CalendarAuthError('캘린더가 연결되지 않았습니다. `reda calendar auth`로 먼저 연결하세요.')
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    token_path.chmod(0o600)
    return creds


def get_service(settings, interactive=False):
    return build('calendar', 'v3', credentials=load_credentials(settings, interactive),
                 cache_discovery=False)


def event_id(proposal):
    """Google 일정 id (영문 소문자 a~v와 숫자만). 같은 제안이면 항상 같다."""
    return f"reda{int(proposal['id']):06d}{proposal['message_id'].lower()}"


def _rfc3339(value):
    """캘린더 API는 초까지 적힌 RFC 3339 시각만 받는다 (2026-11-12T15:00:00+09:00)."""
    return datetime.fromisoformat(value).isoformat(timespec='seconds')


def event_body(proposal):
    when = ({'start': {'date': proposal['start_at']}, 'end': {'date': proposal['end_at']}}
            if proposal['all_day'] else
            {'start': {'dateTime': _rfc3339(proposal['start_at'])},
             'end': {'dateTime': _rfc3339(proposal['end_at'])}})
    lines = [f"메일 근거: {proposal['sentence']}", gmail_link(proposal['thread_id'])]
    if proposal['note']:
        lines.append(f"참고: {proposal['note']}")
    lines.append('ReDA가 메일에서 찾아 확인받은 일정')
    return {
        'id': event_id(proposal),
        'summary': proposal['title'],
        'description': '\n'.join(lines),
        **when,
        'reminders': {'useDefault': False,
                      'overrides': [{'method': 'popup', 'minutes': m} for m in REMINDERS]},
    }


def create_event(service, proposal, calendar_id='primary'):
    """일정을 만들고 일정 링크를 돌려준다. 이미 있으면(같은 제안을 다시 누름) 그 일정을 돌려준다."""
    if not proposal['start_at']:
        raise ValueError('날짜가 정해지지 않은 제안은 등록하지 않는다')
    body = event_body(proposal)
    try:
        event = service.events().insert(calendarId=calendar_id, body=body).execute()
    except HttpError as exc:
        if getattr(exc.resp, 'status', None) != 409:
            raise
        event = service.events().get(calendarId=calendar_id, eventId=body['id']).execute()
    return event.get('id', body['id']), event.get('htmlLink', '')
