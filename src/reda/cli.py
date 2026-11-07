"""ReDA 명령줄 진입점.

    reda gmail auth        브라우저로 Google 동의를 받아 토큰을 저장한다 (개발용)
    reda poll --dry-run    분류 라벨이 없는 새 메일을 출력만 한다
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
    """지난 동기화 이후의 변경을 가져온다. --dry-run이면 상태 DB를 바꾸지 않고 출력만 한다."""
    from reda.gmail import client, history
    from reda.state import StateDB
    if not args.dry_run:
        print('분류·라벨 기능은 아직 없습니다 (구현계획 2단계). --dry-run으로 실행하세요.',
              file=sys.stderr)
        return 2
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
        meta = client.get_message_metadata(service, ref['id'])
        print(f"- {client.get_header(meta, 'Date')} | {client.get_header(meta, 'From')}"
              f" | {client.get_header(meta, 'Subject')}")
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
    poll.add_argument('--dry-run', action='store_true', help='라벨을 붙이지 않고 목록만 출력')
    poll.add_argument('--query', default='in:inbox newer_than:2d', help='Gmail 검색 쿼리')
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
