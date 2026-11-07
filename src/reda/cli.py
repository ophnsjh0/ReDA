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
    from reda.gmail import client
    if not args.dry_run:
        print('분류·라벨 기능은 아직 없습니다 (구현계획 2단계). --dry-run으로 실행하세요.',
              file=sys.stderr)
        return 2
    service = client.get_gmail_service(_settings())
    messages = client.list_new_messages(service, query=args.query, max_results=args.max)
    print(f'분류 라벨이 없는 메일 {len(messages)}통')
    for ref in messages:
        meta = client.get_message_metadata(service, ref['id'])
        print(f"- {client.get_header(meta, 'Date')} | {client.get_header(meta, 'From')}"
              f" | {client.get_header(meta, 'Subject')}")
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
