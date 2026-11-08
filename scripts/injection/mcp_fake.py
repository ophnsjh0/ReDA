"""보안 시험용 reda-mcp: 실제 Gmail 대신 지어낸 공격 메일(cases.py)이 든 가짜 메일함을 쓴다.

도구 이름·설명·동작은 실제 reda-mcp(`reda.mcp_server`)와 같다. 다른 점은 Gmail이 가짜이고,
모든 도구 호출과 만든 초안을 JSONL 로그에 남긴다는 것뿐이다.

    uv run --project <저장소> python scripts/injection/mcp_fake.py --log <로그 경로>

시험 전용 Hermes 프로필(reda-sec)의 MCP 설정이 이 명령을 실행한다. 실제 메일함은 건드리지 않는다.
"""
import argparse
import base64
import email
import json
import re
import sys
import tempfile
import time
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import CASES, ME  # noqa: E402
from fakes import FakeGmail, make_message  # noqa: E402

from reda import identity as ident  # noqa: E402
from reda.config import Settings  # noqa: E402
from reda.mcp_server import ReplyTools, build_server  # noqa: E402
from reda.state import StateDB  # noqa: E402


class CaseGmail(FakeGmail):
    """검색 쿼리의 `(from:도메인 OR to:도메인)`을 흉내 낸다 (실제 Gmail처럼 상대방 도메인 메일만)."""

    def _messages_list(self, userId, q, maxResults, pageToken=None):
        self.calls.append(('messages.list', q))
        sent_only = 'in:sent' in (q or '')
        domain = re.search(r'from:([\w.\-]+)', q or '')
        refs = []
        for ref in self.query_messages:
            msg = self.messages[ref['id']]
            if sent_only != ('SENT' in msg['labelIds']):
                continue
            headers = ' '.join(h['value'] for h in msg['payload']['headers']
                               if h['name'] in ('From', 'To'))
            if domain is None or domain.group(1) in headers:
                refs.append(ref)
        return {'messages': refs[:maxResults]}


def build_gmail():
    messages, threads, history = {}, {}, []
    for case in CASES:
        for m in case['thread'] + case.get('history', []):
            raw = make_message(m['id'], thread_id=m['thread'], internal_date=m['date'],
                               labels=('SENT',) if m['sent'] else ('INBOX', '답장필요'),
                               body=m['body'], From=m['from'], To=m['to'], Cc=m['cc'],
                               Subject=m['subject'], Message_Id=f"<{m['id']}@example.com>")
            messages[m['id']] = raw
            threads.setdefault(m['thread'], []).append(raw)
            history.append({'id': m['id'], 'threadId': m['thread']})
    # 말투 표본용으로 지어낸 내 답장 한 통 (공격 메일이 표본으로 섞이지 않게)
    sample = make_message('sample-sent', thread_id='sample', internal_date=0, labels=('SENT',),
                          From=f'홍길동 <{ME}>', To='someone@cust-z.example.com',
                          Subject='Re: 장비 점검 문의',
                          body='안녕하세요 김 차장님. 홍길동입니다.\n문의 주신 점검 일정은 확인 후 회신드리겠습니다.\n'
                               '감사합니다.\n\n홍길동 드림')
    messages['sample-sent'] = sample
    history.append({'id': 'sample-sent', 'threadId': 'sample'})
    return CaseGmail(messages=messages, threads=threads, query_messages=history)


class LoggedTools(ReplyTools):
    def __init__(self, *args, log_path, **kwargs):
        super().__init__(*args, **kwargs)
        self._log_path = Path(log_path)

    def _log(self, entry):
        entry['at'] = time.time()
        with open(self._log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _logged(self, name, args, fn):
        try:
            result = fn()
        except Exception as exc:
            self._log({'tool': name, 'args': args, 'error': f'{exc.__class__.__name__}: {exc}'})
            raise
        entry = {'tool': name, 'args': args, 'result': result}
        if name == 'save_draft':
            body = self._service.drafts[-1]
            mime = email.message_from_bytes(base64.urlsafe_b64decode(body['message']['raw']),
                                            policy=policy.default)
            entry['draft'] = {'thread_id': body['message']['threadId'], 'to': mime['To'],
                              'cc': mime['Cc'] or '', 'body': mime.get_content()}
        self._log(entry)
        return result

    def get_thread(self, thread_id):
        return self._logged('get_thread', {'thread_id': thread_id},
                            lambda: super(LoggedTools, self).get_thread(thread_id))

    def search_customer_history(self, thread_id, query=''):
        return self._logged('search_customer_history', {'thread_id': thread_id, 'query': query},
                            lambda: super(LoggedTools, self).search_customer_history(thread_id, query))

    def get_writing_samples(self):
        return self._logged('get_writing_samples', {},
                            lambda: super(LoggedTools, self).get_writing_samples())

    def save_draft(self, thread_id, body, evidence=None):
        return self._logged('save_draft', {'thread_id': thread_id, 'body': body, 'evidence': evidence},
                            lambda: super(LoggedTools, self).save_draft(thread_id, body, evidence))

    def hold_draft(self, thread_id, reason):
        return self._logged('hold_draft', {'thread_id': thread_id, 'reason': reason},
                            lambda: super(LoggedTools, self).hold_draft(thread_id, reason))

    def set_label(self, message_id, label):
        return self._logged('set_label', {'message_id': message_id, 'label': label},
                            lambda: super(LoggedTools, self).set_label(message_id, label))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--log', required=True)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='reda-sec-'))
    import os
    os.environ.update({'REDA_DATA_DIR': str(work), 'REDA_MY_ADDRESSES': ME,
                       'REDA_ENV_FILE': str(work / 'none.env')})
    settings = Settings.from_env()
    identity = ident.build(personal=[ME], display_names=['홍길동'], titles=['부장'])
    db_path = work / 'state.db'
    tools = LoggedTools(build_gmail(), None, settings, identity,
                        state_opener=lambda: StateDB(db_path), log_path=args.log)
    build_server(tools).run('stdio')


if __name__ == '__main__':
    main()
