import base64
import email
from email import policy

from reda.gmail import client


def _message(headers, thread_id='t1'):
    return {
        'id': 'm1',
        'threadId': thread_id,
        'payload': {'headers': [{'name': k, 'value': v} for k, v in headers.items()]},
    }


def _decode(draft_body):
    raw = base64.urlsafe_b64decode(draft_body['message']['raw'])
    return email.message_from_bytes(raw, policy=policy.default)


def test_reply_draft_stays_in_thread():
    msg = _message({
        'From': '이차장 <lee@customer-a.com>',
        'Subject': '[긴급] VIP 세션 간헐적 끊김 문의',
        'Message-ID': '<abc@customer-a.com>',
        'References': '<prev@customer-a.com>',
    }, thread_id='thread-42')
    body = client.build_reply_draft(msg, '안녕하세요.\n확인했습니다.')
    assert body['message']['threadId'] == 'thread-42'
    mime = _decode(body)
    assert mime['To'] == '이차장 <lee@customer-a.com>'
    assert mime['Subject'].startswith('Re: ')
    assert mime['In-Reply-To'] == '<abc@customer-a.com>'
    assert mime['References'] == '<prev@customer-a.com> <abc@customer-a.com>'
    # 주소 부분은 인코딩되지 않아야 Gmail이 받는 사람을 읽는다
    raw = base64.urlsafe_b64decode(body['message']['raw']).decode('ascii')
    to_line = next(line for line in raw.splitlines() if line.startswith('To:'))
    assert '<lee@customer-a.com>' in to_line
    assert mime.get_content().strip() == '안녕하세요.\n확인했습니다.'


def test_reply_to_header_wins_and_subject_not_doubled():
    msg = _message({
        'From': 'support@vendor.com',
        'Reply-To': 'case-12345@vendor.com',
        'Subject': 'RE: Case #12345 — Health monitor timeout behavior',
    })
    mime = _decode(client.build_reply_draft(msg, 'Hi'))
    assert mime['To'] == 'case-12345@vendor.com'
    assert not mime['Subject'].lower().startswith('re: re')


def test_html_to_text_keeps_blocks():
    html = '<div>첫 줄</div><div>둘째 <b>줄</b></div><p>문단</p>'
    assert client.html_to_text(html) == '첫 줄\n둘째 줄\n\n문단'


# ---------------------------------------------------------------- 사용량 제한 재시도

import httplib2  # noqa: E402
import pytest  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402


class _Flaky:
    def __init__(self, errors, result='ok'):
        self.errors, self.result, self.calls = list(errors), result, 0

    def execute(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def _http_error(status, reason=''):
    return HttpError(httplib2.Response({'status': status}),
                     f'{{"error": {{"errors": [{{"reason": "{reason}"}}]}}}}'.encode())


def test_rate_limited_request_is_retried():
    sleeps = []
    req = _Flaky([_http_error(403, 'rateLimitExceeded'), _http_error(429)])
    assert client.execute(req, sleep=sleeps.append) == 'ok'
    assert sleeps == [2, 4] and req.calls == 3


def test_other_errors_are_not_retried():
    req = _Flaky([_http_error(403, 'insufficientPermissions')])
    with pytest.raises(HttpError):
        client.execute(req, sleep=lambda s: None)
    assert req.calls == 1


def test_rate_limit_gives_up_after_waits():
    sleeps = []
    req = _Flaky([_http_error(429)] * 3)
    with pytest.raises(HttpError):
        client.execute(req, sleep=sleeps.append, waits=(1, 1))
    assert sleeps == [1, 1] and req.calls == 3
