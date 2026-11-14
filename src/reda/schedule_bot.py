"""일정 버튼 봇 (9단계, 결정 9-5). `reda schedule-bot`으로 계속 띄워 둔다.

ReDA 코드가 텔레그램 봇을 직접 돌린다 (Hermes·LLM을 거치지 않는다).
- 새 제안(status 'new')을 [캘린더에 등록] [시간 수정] [무시] 버튼과 함께 보낸다.
  날짜가 분명하지 않은 제안은 등록 버튼 없이 날짜를 묻는다.
- 버튼 응답은 설정한 대화방(REDA_SCHEDULE_CHAT_ID)에서 온 것만 받는다. 다른 사람은 무시한다.
- [캘린더에 등록]을 누른 제안만 캘린더에 넣는다. 캘린더를 부르는 곳은 여기 한 곳뿐이다.
- [시간 수정]을 누르면 다음에 보내는 글("11/12 15:00-17:00")로 시간을 바꾸고 다시 버튼을 보낸다.

토큰은 로그·오류 문구에 남기지 않는다.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from reda import schedule
from reda.context import gmail_link
from reda.mask import Masker

API = 'https://api.telegram.org/bot{token}/{method}'
POLL_TIMEOUT = 50           # getUpdates 롱 폴링 초
WEEKDAYS = '월화수목금토일'


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token, *, opener=urllib.request.urlopen):
        self._token, self._open = token, opener

    def call(self, method, **params):
        data = json.dumps(params).encode('utf-8')
        request = urllib.request.Request(API.format(token=self._token, method=method), data=data,
                                         headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self._open(request, timeout=POLL_TIMEOUT + 10) as response:
                body = json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read() or b'{}')
            except ValueError:
                body = {}
            raise TelegramError(f"텔레그램 {method} 실패 (HTTP {exc.code}): {body.get('description', '')}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise TelegramError(f'텔레그램 연결 실패 ({exc.__class__.__name__})') from None
        if not body.get('ok'):
            raise TelegramError(f"텔레그램 {method} 실패: {body.get('description', '')}")
        return body.get('result')


def _when(proposal):
    if not proposal['start_at']:
        return '날짜 확인 필요'
    start = datetime.fromisoformat(proposal['start_at'])
    day = f'{start.month}/{start.day}({WEEKDAYS[start.weekday()]})'
    if proposal['all_day']:
        return f'{day} 종일'
    end = datetime.fromisoformat(proposal['end_at'])
    tz = start.tzname() if start.utcoffset() != timedelta(hours=9) else ''
    return f'{day} {start:%H:%M}~{end:%H:%M}' + (f' {tz}' if tz else '')


def proposal_text(proposal, masker=None):
    masker = masker or Masker()
    lines = ['📅 일정 제안' if proposal['start_at'] else '📅 일정 날짜를 알려 주세요',
             f"{masker.mask(proposal['title'])}",
             f'🕑 {_when(proposal)}',
             f"근거: “{masker.mask(proposal['sentence'])[:160]}”"]
    if proposal['note']:
        lines.append(f"참고: {proposal['note']}")
    lines.append(gmail_link(proposal['thread_id']))
    return '\n'.join(lines)


def buttons(proposal):
    pid = proposal['id']
    if not proposal['start_at']:
        return {'inline_keyboard': [[{'text': '날짜 입력', 'callback_data': f'edit:{pid}'},
                                     {'text': '무시', 'callback_data': f'ignore:{pid}'}]]}
    return {'inline_keyboard': [[{'text': '캘린더에 등록', 'callback_data': f'add:{pid}'}],
                                [{'text': '시간 수정', 'callback_data': f'edit:{pid}'},
                                 {'text': '무시', 'callback_data': f'ignore:{pid}'}]]}


class ScheduleBot:
    """create_event(proposal) -> (일정 id, 링크): 캘린더 등록 (시험에서는 가짜)."""

    def __init__(self, telegram, state, chat_id, create_event, *, now=None):
        self.tg, self.state, self.chat_id = telegram, state, str(chat_id)
        self._create_event = create_event
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._offset = None

    # -------------------------------------------------------- 보내기
    def send_new(self):
        """새 제안을 보낸다. 보낸 수."""
        sent = 0
        for proposal in self.state.proposals('new'):
            message = self.tg.call('sendMessage', chat_id=self.chat_id, text=proposal_text(proposal),
                                   reply_markup=buttons(proposal), disable_web_page_preview=True)
            self.state.update_proposal(proposal['id'], status='proposed' if proposal['start_at'] else 'asked',
                                       chat_message_id=str(message['message_id']))
            sent += 1
        return sent

    def _finish(self, proposal, text):
        """버튼 메시지를 결과 글로 바꾸고 버튼을 없앤다."""
        if proposal['chat_message_id']:
            try:
                self.tg.call('editMessageText', chat_id=self.chat_id,
                             message_id=int(proposal['chat_message_id']),
                             text=proposal_text(proposal) + f'\n\n{text}', disable_web_page_preview=True)
            except TelegramError:
                self.tg.call('sendMessage', chat_id=self.chat_id, text=text)

    # -------------------------------------------------------- 받기
    def handle(self, update):
        if 'callback_query' in update:
            return self._on_button(update['callback_query'])
        message = update.get('message') or {}
        if str(message.get('chat', {}).get('id')) != self.chat_id:
            return 'ignored: other chat'
        return self._on_text(message.get('text') or '')

    def _on_button(self, query):
        chat = str(((query.get('message') or {}).get('chat') or {}).get('id'))
        if chat != self.chat_id or str((query.get('from') or {}).get('id')) != self.chat_id:
            return 'ignored: other chat'       # 버튼은 정해 둔 사람만 누를 수 있다
        try:      # 오래된 버튼 응답(봇 재시작 뒤)은 실패하지만 누른 동작은 처리한다
            self.tg.call('answerCallbackQuery', callback_query_id=query.get('id', ''))
        except TelegramError:
            pass
        action, _, pid = (query.get('data') or '').partition(':')
        proposal = self.state.proposal(int(pid)) if pid.isdigit() else None
        if proposal is None or proposal['status'] in ('added', 'ignored'):
            return 'ignored: done'
        if action == 'add':
            if not proposal['start_at']:
                return 'ignored: no date'
            try:
                event_id, link = self._create_event(proposal)
            except Exception as exc:  # 등록 실패는 알리고 다시 누를 수 있게 둔다
                self.state.update_proposal(proposal['id'], status='proposed',
                                           detail=f'{exc.__class__.__name__}: {exc}'[:300])
                self.tg.call('sendMessage', chat_id=self.chat_id,
                             text=f'⚠️ 캘린더 등록 실패: {exc.__class__.__name__}. 잠시 뒤 다시 눌러 주세요.')
                return 'failed'
            self.state.update_proposal(proposal['id'], status='added', event_id=event_id, detail='')
            self._finish(proposal, '✅ 캘린더에 등록했어요' + (f'\n{link}' if link else ''))
            return 'added'
        if action == 'ignore':
            self.state.update_proposal(proposal['id'], status='ignored')
            self._finish(proposal, '무시했어요')
            return 'ignored'
        if action == 'edit':
            for other in self.state.proposals('editing'):        # 한 번에 하나만 고친다
                self.state.update_proposal(other['id'], status='proposed' if other['start_at'] else 'asked')
            self.state.update_proposal(proposal['id'], status='editing')
            self.tg.call('sendMessage', chat_id=self.chat_id,
                         text='새 날짜·시간을 보내 주세요. 예: 11/12 15:00-17:00, 다음 주 화요일 오후 2시, 11/13 종일')
            return 'editing'
        return 'ignored: unknown'

    def _on_text(self, text):
        editing = self.state.proposals('editing')
        if not editing or text.startswith('/'):
            if text.startswith('/start'):
                self.tg.call('sendMessage', chat_id=self.chat_id,
                             text='ReDA 일정 봇입니다. 메일에서 찾은 일정을 버튼으로 물어볼게요.')
            return 'ignored: nothing to edit'
        proposal = editing[-1]
        found = [f for f in schedule.find(text, self._now()) if f.clear]
        if not found:
            self.tg.call('sendMessage', chat_id=self.chat_id,
                         text='날짜를 읽지 못했어요. 예: 11/12 15:00-17:00')
            return 'retry'
        item = found[0]
        if text.strip().endswith('종일'):
            item.start = item.end = None
        start_at, end_at, all_day = schedule._iso(item)
        self.state.update_proposal(proposal['id'], status='new', start_at=start_at, end_at=end_at,
                                   all_day=int(all_day), note='사용자가 시간을 고침')
        self._finish(proposal, '시간을 고쳤어요. 아래 새 제안을 확인해 주세요.')
        self.send_new()
        return 'edited'

    # -------------------------------------------------------- 실행
    def poll_once(self, timeout=POLL_TIMEOUT):
        params = {'timeout': timeout, 'allowed_updates': ['message', 'callback_query']}
        if self._offset is not None:
            params['offset'] = self._offset
        updates = self.tg.call('getUpdates', **params) or []
        results = []
        for update in updates:
            self._offset = update['update_id'] + 1
            try:
                results.append(self.handle(update))
            except TelegramError:
                raise
            except Exception as exc:  # 메시지 하나의 오류(DB 잠김, 이상한 입력)로 봇이 멈추지 않게
                results.append(f'error: {exc.__class__.__name__}')
        return results

    def run(self, *, sleep=time.sleep, log=print):
        log('일정 버튼 봇 시작')
        while True:
            try:
                self.send_new()
                self.poll_once()
            except Exception as exc:  # 봇이 멈추면 버튼이 모두 묵살된다. 기록하고 다시 돈다
                log(f'오류: {exc.__class__.__name__}: {exc}')
                sleep(10)
