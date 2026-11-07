"""ReDA 명령줄 진입점.

    reda gmail auth        브라우저로 Google 동의를 받아 토큰을 저장한다 (개발용)
    reda poll              새 메일을 분류하고 Gmail 라벨을 붙인다 (cron에서 15분마다)
    reda poll --dry-run    새 메일과 규칙 필터 결과를 출력만 한다 (LLM 호출 없음)
"""
import argparse
import os
import sys

from dotenv import find_dotenv, load_dotenv

from reda import __version__


def _load_env():
    """REDA_ENV_FILE이 있으면 그 파일을, 없으면 현재 폴더부터 위로 찾은 .env를 읽는다."""
    path = os.environ.get('REDA_ENV_FILE') or find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


def _settings():
    from reda.config import Settings
    return Settings.from_env()


def cmd_gmail_auth(args):
    from reda.gmail.client import get_gmail_service
    settings = _settings()
    service = get_gmail_service(settings, interactive=True)
    profile = service.users().getProfile(userId='me').execute()
    print(f"Gmail 연결 완료: {profile['emailAddress']}")
    print(f'토큰 저장 위치: {settings.gmail_token_file}')
    return 0


def cmd_poll(args):
    """새 메일을 분류하고 라벨을 붙인다. --dry-run이면 아무것도 바꾸지 않고 규칙 필터 결과만 출력한다."""
    if args.dry_run:
        return _poll_dry_run(args)
    from reda import poll
    from reda.classify import ClassifierUnavailable, make_classifier
    from reda.gmail import client
    from reda.lock import AlreadyRunning, exclusive
    from reda.state import StateDB
    settings = _settings()
    try:
        with exclusive(settings.data_dir / 'poll.lock'):
            service = client.get_gmail_service(settings)
            with StateDB.open(settings) as state:
                result = poll.run(service, state, settings, make_classifier(settings),
                                  resync_query=args.query, max_results=args.max)
    except AlreadyRunning:
        print('이전 실행이 아직 끝나지 않아 이번 실행은 건너뜁니다.')
        return 0
    except ClassifierUnavailable as exc:
        print(f'오류: {exc}. 메일은 처리하지 않았고 다음 실행에서 다시 가져옵니다.',
              file=sys.stderr)
        return 1
    how = '검색 쿼리로 처음부터' if result.full_resync else 'History API'
    print(f'동기화: {how} (다음 시작점 {result.history_id})')
    print(f'라벨 {result.labeled}통 (규칙 {result.by_rule}, 분류기 {result.by_classifier}),'
          f' 건너뜀 {result.skipped}, 실패 {result.failed}')
    print(f'측정 기록: 라벨 변경 {result.label_events}건, 놓친 답장 {result.missed_replies}건')
    return 0


def _poll_dry_run(args):
    """지난 동기화 이후의 새 메일과 규칙 필터 결과를 출력한다. 상태 DB와 Gmail을 바꾸지 않고,
    LLM도 부르지 않는다."""
    from reda import rules
    from reda.gmail import client, history
    from reda.mail import RULE_HEADERS, Mail
    from reda.state import StateDB
    settings = _settings()
    service = client.get_gmail_service(settings)
    with StateDB.open(settings) as state:
        start = state.history_id
        changes = history.fetch_changes(service, start, resync_query=args.query,
                                        max_results=args.max)
        new = [m for m in changes.new_messages if not state.is_processed(m['id'])]

    how = '검색 쿼리로 처음부터' if changes.full_resync else f'historyId {start} 이후'
    print(f'동기화 방식: {how} (다음 시작점 {changes.history_id})')
    print(f'새 메일 {len(new)}통')
    for ref in new:
        mail = Mail.from_gmail(client.get_message_metadata(service, ref['id'], RULE_HEADERS))
        hit = rules.check(mail, settings)
        verdict = f'참고 ({hit.reason})' if hit else '분류기로'
        print(f'- {mail.date} | {mail.sender} | {mail.subject} → {verdict}')
    if changes.label_changes:
        print(f'분류 라벨 변경 {len(changes.label_changes)}건')
        for c in changes.label_changes:
            print(f'- {c.message_id}: {c.label} {"추가" if c.action == "added" else "제거"}')
    if changes.sent_messages:
        print(f'보낸 메일 {len(changes.sent_messages)}통')
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog='reda', description='ReDA (Reply Draft Agent)')
    parser.add_argument('--version', action='version', version=f'reda {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)

    gmail = sub.add_parser('gmail', help='Gmail 연결 관리')
    gmail_sub = gmail.add_subparsers(dest='gmail_command', required=True)
    auth = gmail_sub.add_parser('auth', help='브라우저로 Google 동의를 받아 토큰 저장 (개발용)')
    auth.set_defaults(func=cmd_gmail_auth)

    poll = sub.add_parser('poll', help='새 메일 가져오기')
    poll.add_argument('--dry-run', action='store_true', help='라벨을 붙이지 않고 목록과 규칙 필터 결과만 출력')
    poll.add_argument('--query', default='in:inbox newer_than:2d', help='처음 동기화할 때 쓰는 Gmail 검색 쿼리')
    poll.add_argument('--max', type=int, default=50, help='최대 메일 수')
    poll.set_defaults(func=cmd_poll)
    return parser


def main(argv=None):
    _load_env()
    args = build_parser().parse_args(argv)
    from reda.gmail.client import GmailAuthError
    try:
        return args.func(args)
    except GmailAuthError as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
