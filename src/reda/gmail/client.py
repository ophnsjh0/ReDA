"""Gmail API 클라이언트: OAuth, 메일·스레드 조회, 라벨, 답장 초안 저장.

발송 함수는 일부러 두지 않는다. 초안 저장에 필요한 gmail.modify 권한에는 발송 권한도
들어 있어서, OAuth 범위로는 발송을 막을 수 없다. 이 모듈에서 messages.send·drafts.send를
부르지 않는 것이 발송을 막는 유일한 장치다. 이 모듈에 발송 기능을 추가하지 않는다.
"""
import base64
import re
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# 읽기 + 라벨 붙이기 + 초안 생성. (캘린더 범위는 일정 등록 기능을 붙일 때 추가한다.)
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

LABEL_REPLY = '답장필요'
LABEL_FYI = '참고'
LABEL_CHECK = '확인필요'
# 분류 라벨이 하나라도 붙은 메일은 이미 처리한 메일로 본다.
CLASSIFICATION_LABELS = (LABEL_REPLY, LABEL_FYI, LABEL_CHECK)


class GmailAuthError(Exception):
    pass


def load_credentials(settings, interactive=False):
    """저장된 토큰을 읽고 필요하면 갱신한다.

    interactive=True면 토큰이 없을 때 브라우저로 동의 화면을 연다(개발용).
    사용자에게 배포할 때는 봇이 보내는 링크로 동의를 받는 흐름으로 바꾼다.
    """
    token_path = settings.gmail_token_file
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        if not settings.gmail_credentials_file.exists():
            raise GmailAuthError(
                f'Gmail OAuth 클라이언트 파일이 없습니다: {settings.gmail_credentials_file}. '
                'Google Cloud Console에서 credentials.json을 내려받아 이 경로에 두세요.')
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(
            str(settings.gmail_credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
    else:
        raise GmailAuthError('Gmail이 연결되지 않았습니다. `reda gmail auth`로 먼저 연결하세요.')

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    token_path.chmod(0o600)
    return creds


def get_gmail_service(settings, interactive=False):
    return build('gmail', 'v1', credentials=load_credentials(settings, interactive),
                 cache_discovery=False)


def get_or_create_label(service, name):
    """라벨 이름의 id를 돌려준다. 없으면 만든다."""
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    for label in labels:
        if label['name'] == name:
            return label['id']
    created = service.users().labels().create(
        userId='me',
        body={'name': name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'},
    ).execute()
    return created['id']


def list_new_messages(service, query='in:inbox newer_than:2d', max_results=50):
    """분류 라벨이 아직 없는 메일 id 목록."""
    excluded = ' '.join(f'-label:"{name}"' for name in CLASSIFICATION_LABELS)
    response = service.users().messages().list(
        userId='me', q=f'{query} {excluded}', maxResults=max_results).execute()
    return response.get('messages', [])


def get_message(service, message_id):
    return service.users().messages().get(userId='me', id=message_id, format='full').execute()


def get_message_metadata(service, message_id, headers=('From', 'To', 'Cc', 'Subject', 'Date')):
    """본문 없이 헤더만 가져온다. 목록 출력과 규칙 필터용."""
    return service.users().messages().get(
        userId='me', id=message_id, format='metadata', metadataHeaders=list(headers)).execute()


def get_thread(service, thread_id):
    return service.users().threads().get(userId='me', id=thread_id, format='full').execute()


def add_labels(service, message_id, label_ids):
    service.users().messages().modify(
        userId='me', id=message_id, body={'addLabelIds': list(label_ids)}).execute()


# ---------------------------------------------------------------- 답장 초안

_RE_REPLY_SUBJECT = re.compile(r'^\s*(re|답장)\s*:', re.IGNORECASE)


def build_reply_draft(message, body_text, cc=''):
    """원래 메일에 대한 답장 초안의 drafts.create 요청 본문을 만든다.

    threadId와 In-Reply-To/References를 함께 넣어야 Gmail이 초안을 원래 스레드 안에 붙인다.
    받는 사람은 Reply-To가 있으면 Reply-To, 없으면 From이다.
    """
    subject = get_header(message, 'Subject')
    if not _RE_REPLY_SUBJECT.match(subject):
        subject = f'Re: {subject}'
    # EmailMessage(기본 policy)는 주소 헤더의 한글 이름만 인코딩하고 주소는 그대로 둔다.
    # MIMEText는 '이차장 <lee@a.com>' 전체를 인코딩해 Gmail이 받는 사람을 읽지 못한다.
    mime = EmailMessage()
    mime.set_content(body_text, charset='utf-8', cte='base64')
    mime['To'] = get_header(message, 'Reply-To') or get_header(message, 'From')
    if cc:
        mime['Cc'] = cc
    mime['Subject'] = subject
    message_id = get_header(message, 'Message-ID')
    if message_id:
        mime['In-Reply-To'] = message_id
        mime['References'] = f"{get_header(message, 'References')} {message_id}".strip()
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    return {'message': {'raw': raw, 'threadId': message['threadId']}}


def create_reply_draft(service, message, body_text, cc=''):
    """원래 메일 스레드의 임시보관함에 답장 초안을 저장한다. (발송하지 않는다.)"""
    return service.users().drafts().create(
        userId='me', body=build_reply_draft(message, body_text, cc)).execute()


# ---------------------------------------------------------------- 본문 추출

def extract_body(payload):
    """Gmail 메시지 payload에서 일반 텍스트 본문을 뽑는다.

    text/plain을 우선하고, 없으면 text/html을 텍스트로 바꾼다.
    """
    plain, html = _walk_parts(payload)
    if plain:
        return _normalize_text(plain)
    if html:
        return html_to_text(html)
    return ''


# 줄바꿈으로 취급할 블록 요소. 인라인 태그(span/a/b 등)는 줄을 나누지 않아야
# 서명(명함)처럼 서식이 많은 부분이 "T / 070-… / M / …"으로 갈라지지 않는다.
_PARA_TAGS = ['p', 'blockquote', 'pre', 'table', 'ul', 'ol',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
_LINE_TAGS = ['div', 'tr', 'li', 'section', 'article', 'header', 'footer', 'hr']
# HTML 원본 텍스트에는 등장할 수 없는 제어문자를 개행 표식으로 쓴다
_LINE_MARK, _PARA_MARK = '\x00', '\x01'


def html_to_text(html):
    """HTML 메일 본문을 블록 구조를 살린 일반 텍스트로 변환한다.

    p 등 문단 요소는 빈 줄로, div/tr/li·<br>은 한 줄 개행으로 바꾸고
    HTML 소스의 들여쓰기·개행은 브라우저처럼 공백 1개로 접는다.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'head', 'title']):
        tag.decompose()
    for br in soup.find_all('br'):
        br.replace_with(_LINE_MARK)
    for cell in soup.find_all(['td', 'th']):  # 같은 행의 셀은 공백으로 구분
        cell.append(' ')
    for tag in soup.find_all(_PARA_TAGS):
        tag.insert_before(_PARA_MARK)
        tag.append(_PARA_MARK)
    for tag in soup.find_all(_LINE_TAGS):
        tag.insert_before(_LINE_MARK)
        tag.append(_LINE_MARK)
    text = soup.get_text()
    text = re.sub(r'\s+', ' ', text)  # 소스 개행/들여쓰기 → 공백 1개
    # 인접한 div/tr(닫힘+열림)이 만드는 연속 줄 표식은 개행 1개로 충분하다
    text = re.sub(f'(?:{_LINE_MARK} ?){{2,}}', _LINE_MARK, text)
    text = text.replace(_PARA_MARK, '\n\n').replace(_LINE_MARK, '\n')
    text = '\n'.join(line.strip() for line in text.split('\n'))
    return _normalize_text(text)


def _normalize_text(text):
    # 줄 끝 공백을 지우고 연속 빈 줄은 문단 구분 1개로 줄인다.
    # (앞쪽 들여쓰기는 CLI 출력·설정 덤프에 의미가 있을 수 있어 유지)
    text = re.sub(r'[ \t]+\n', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _walk_parts(payload):
    plain, html = '', ''
    mime = payload.get('mimeType', '')
    data = payload.get('body', {}).get('data')
    if data:
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        if mime == 'text/plain':
            plain += decoded
        elif mime == 'text/html':
            html += decoded
    for part in payload.get('parts', []) or []:
        p, h = _walk_parts(part)
        plain += p
        html += h
    return plain, html


def get_header(message, name):
    for header in message.get('payload', {}).get('headers', []):
        if header['name'].lower() == name.lower():
            return header['value']
    return ''
