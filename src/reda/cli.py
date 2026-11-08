"""ReDA 명령줄 진입점.

    reda gmail auth        브라우저로 Google 동의를 받아 토큰을 저장한다 (개발용)
    reda poll              새 메일을 분류하고 Gmail 라벨을 붙인다 (cron에서 15분마다)
    reda poll --dry-run    새 메일과 규칙 필터 결과를 출력만 한다 (LLM 호출 없음)
    reda classifier        지금 쓰는 분류기(제공자·모델)를 보여 준다. --try면 지어낸 메일로 시험
    reda golden export     골든 세트용 메일 목록을 CSV로 내보낸다 (정답은 사람이 채운다)
    reda golden eval       정답이 있는 메일을 분류해 놓침·확인필요 비율을 낸다
"""
import argparse
import base64
import os
import sys
from pathlib import Path

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
    from reda import identity as ident
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
                identity = ident.load(settings, service=service, state=state, persist=True)
                result = poll.run(service, state, settings, make_classifier(settings, identity),
                                  resync_query=args.query, max_results=args.max,
                                  identity=identity)
                drafts = (_run_drafts(service, state, settings, quiet=True)
                          if settings.auto_draft else None)
                _maybe_send_alerts(service, state, settings)
                _maybe_send_summary(service, state, settings)
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
    if drafts is not None:
        _print_drafts(drafts)
    return 0


def _maybe_send_alerts(service, state, settings):
    """새로 분류한 급한 메일(답장필요·확인필요, 긴급도 높음)을 바로 알린다. 실패해도 분류에는 영향 없음."""
    from reda import summary
    if not settings.urgent_alert:
        return
    try:
        sent = summary.send_alerts(service, state, settings)
        if sent:
            print(f'급한 메일 {sent}통을 알렸습니다.')
    except Exception as exc:
        print(f'급한 메일 알림 오류: {exc.__class__.__name__}: {exc}', file=sys.stderr)


def _maybe_send_summary(service, state, settings):
    """정한 시각이 지났고 오늘 아직 안 보냈으면 요약을 보낸다. 실패해도 분류에는 영향 없음."""
    from reda import summary
    if not summary.due(settings, state):
        return
    try:
        _send_summary(service, state, settings)
        print('출근 전 요약을 보냈습니다.')
    except Exception as exc:
        print(f'요약 보내기 오류: {exc.__class__.__name__}: {exc}', file=sys.stderr)


def _send_summary(service, state, settings, *, hours=None):
    from datetime import datetime, timezone

    from reda import summary
    since, period = summary.since_for(state, hours=hours)
    collected = summary.collect(state, since=since)
    summary.fill_headers(service, collected)
    text = summary.render(collected, period=period)
    summary.deliver(settings, text)
    state.set_sync(summary.SYNC_KEY, datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return text


def cmd_summary(args):
    """출근 전 요약. 기본은 화면에만 보여 준다. --send면 메신저로 보내고 '지난 요약' 시점을 옮긴다."""
    from reda import summary
    from reda.gmail import client
    from reda.state import StateDB
    settings = _settings()
    service = client.get_gmail_service(settings)
    with StateDB.open(settings) as state:
        if args.send:
            try:
                text = _send_summary(service, state, settings, hours=args.hours)
            except Exception as exc:
                print(f'오류: {exc.__class__.__name__}: {exc}', file=sys.stderr)
                return 1
            print(text)
            print(f'\n→ {settings.summary_profile} 프로필의 {settings.summary_to}로 보냈습니다.')
            return 0
        since, period = summary.since_for(state, hours=args.hours)
        collected = summary.collect(state, since=since)
        summary.fill_headers(service, collected)
        print(summary.render(collected, period=period))
    return 0


def cmd_alert(args):
    """급한 메일 알림 미리 보기. 최근 N시간에 분류한 급한 메일로 알림 글을 만든다.
    --send면 그 글을 메신저로 보낸다 (시험용, 자동 알림의 기준점은 옮기지 않는다)."""
    from datetime import datetime, timedelta, timezone

    from reda import summary
    from reda.gmail import client
    from reda.state import StateDB
    settings = _settings()
    service = client.get_gmail_service(settings)
    after = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat(timespec='seconds')
    with StateDB.open(settings) as state:
        items, _ = summary.urgent(state, after=after)
        if not items:
            print(f'최근 {args.hours}시간에 분류한 급한 메일이 없습니다 (답장필요·확인필요 중 긴급도 높음).')
            return 0
        summary.fill_headers(service, summary.Summary(since=after, items=items))
        text = summary.render_alert(items)
        print(text)
        if args.send:
            try:
                summary.deliver(settings, text)
            except Exception as exc:
                print(f'오류: {exc.__class__.__name__}: {exc}', file=sys.stderr)
                return 1
            print(f'\n→ {settings.summary_profile} 프로필의 {settings.summary_to}로 보냈습니다.')
    return 0


def _print_drafts(done):
    if done.details:
        print(f'자동 초안: 초안 {done.drafted}, 보류 {done.held}, 건너뜀 {done.skipped},'
              f' 실패 {done.failed}')
        for message_id, status, detail in done.details:
            print(f'- {message_id}: {status} {detail[:120]}')


def _run_drafts(service, state, settings, *, quiet=False):
    """자동 초안. 실패해도 분류 결과에는 영향을 주지 않는다."""
    from reda import autodraft
    try:
        done = autodraft.draft_pending(service, state, settings)
    except Exception as exc:
        print(f'자동 초안 오류: {exc.__class__.__name__}: {exc}', file=sys.stderr)
        return None
    if not quiet:
        _print_drafts(done)
    return done


def cmd_draft_pending(args):
    """초안을 기다리는 답장필요 메일을 초안 에이전트에게 맡긴다 (Gmail 임시보관함에 저장, 발송 없음)."""
    from reda import autodraft
    from reda.gmail import client
    from reda.lock import AlreadyRunning, exclusive
    from reda.state import StateDB
    settings = _settings()
    try:
        with exclusive(settings.data_dir / 'poll.lock'):
            service = client.get_gmail_service(settings)
            with StateDB.open(settings) as state:
                if args.dry_run:
                    preview = autodraft.draft_pending(service, state, settings, dry_run=True)
                    for message_id, status, reason in preview.details:
                        subject = client.get_header(client.get_message_metadata(
                            service, message_id, ('Subject',)), 'Subject')
                        what = '맡김' if status == 'would_draft' else f'건너뜀 ({reason})'
                        print(f'- {message_id} | {subject[:60]} → {what}')
                    if not preview.details:
                        print('초안을 기다리는 메일 없음')
                    return 0
                done = _run_drafts(service, state, settings)
    except AlreadyRunning:
        print('이전 실행이 아직 끝나지 않아 이번 실행은 건너뜁니다.')
        return 0
    if done is None:
        return 1
    if not done.details:
        print('초안을 기다리는 메일 없음')
    return 1 if done.failed else 0


def _poll_dry_run(args):
    """지난 동기화 이후의 새 메일과 규칙 필터 결과를 출력한다. 상태 DB와 Gmail을 바꾸지 않고,
    LLM도 부르지 않는다."""
    from reda import identity as ident
    from reda import rules
    from reda.gmail import client, history
    from reda.mail import Mail
    from reda.state import StateDB
    settings = _settings()
    service = client.get_gmail_service(settings)
    with StateDB.open(settings) as state:
        start = state.history_id
        changes = history.fetch_changes(service, start, resync_query=args.query,
                                        max_results=args.max)
        new = [m for m in changes.new_messages if not state.is_processed(m['id'])]
        identity = ident.load(settings, service=service, state=state)

    how = '검색 쿼리로 처음부터' if changes.full_resync else f'historyId {start} 이후'
    print(f'동기화 방식: {how} (다음 시작점 {changes.history_id})')
    print(f'새 메일 {len(new)}통')
    for ref in new:
        # 규칙 필터가 인사말을 보므로 본문까지 가져온다
        mail = Mail.from_gmail(client.get_message(service, ref['id']))
        hit = rules.check(mail, settings, identity)
        mode = ident.MODE_LABELS[identity.recipient_mode(mail)]
        verdict = f'참고 ({hit.reason})' if hit else f'분류기로 ({mode})'
        print(f'- {mail.date} | {mail.sender} | {mail.subject} → {verdict}')
    if changes.label_changes:
        print(f'분류 라벨 변경 {len(changes.label_changes)}건')
        for c in changes.label_changes:
            print(f'- {c.message_id}: {c.label} {"추가" if c.action == "added" else "제거"}')
    if changes.sent_messages:
        print(f'보낸 메일 {len(changes.sent_messages)}통')
    return 0


SAMPLE_MAIL = {
    'From': '이차장 <lee@customer-a.example.com>',
    'Subject': '[긴급] VIP 세션이 30초마다 끊깁니다',
    'body': '안녕하세요. 어제 저녁부터 L4 장비 192.0.2.10에서 VIP 세션이 30초마다 끊깁니다.\n'
            '원인 확인과 조치 방법을 오늘 중으로 회신 부탁드립니다.',
}


def cmd_classifier(args):
    """설정한 분류기를 보여 주고, --try면 지어낸 고객 문의 한 통을 분류해 본다 (Gmail은 쓰지 않음)."""
    from reda.classify import ClassifierUnavailable, PROVIDERS, make_classifier, model_for
    from reda.mail import Mail
    settings = _settings()
    try:
        model = model_for(settings)
    except ClassifierUnavailable as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1
    print(f'분류기: {settings.classifier} (쓸 수 있는 값: {", ".join(PROVIDERS)})')
    print(f'모델: {model}{"" if settings.classifier_model else " (제공자 기본값)"}')
    if settings.classifier == 'anthropic':
        print(f'effort: {settings.classifier_effort or "(보내지 않음)"}')
    if settings.classifier == 'jev':
        yes = settings.jev_min_probability
        print(f'판단 기준: {yes:.2f} 이상이면 예, {1 - yes:.2f} 이하면 아니오, 그 사이는 확인필요'
              ' (질문·판정표: classify/jev_questions.py)')
    if not args.try_:
        return 0
    mine = settings.my_addresses[0] if settings.my_addresses else 'me@corp.example.com'
    message = {'id': 'sample', 'threadId': 'sample', 'internalDate': '0', 'labelIds': [],
               'payload': {'mimeType': 'text/plain', 'headers': [
                   {'name': 'From', 'value': SAMPLE_MAIL['From']},
                   {'name': 'To', 'value': mine},
                   {'name': 'Subject', 'value': SAMPLE_MAIL['Subject']}],
                   'body': {'data': base64.urlsafe_b64encode(
                       SAMPLE_MAIL['body'].encode()).decode()}}}
    try:
        result = make_classifier(settings).classify(Mail.from_gmail(message))
    except Exception as exc:  # 설정 오류든 일시 장애든 traceback 대신 한 줄로 알린다
        print(f'오류: {exc.__class__.__name__}: {exc}', file=sys.stderr)
        return 1
    extra = f', 확률 {result.confidence:.2f}' if result.confidence is not None else ''
    print(f'시험 메일 → {result.category} / 긴급도 {result.urgency}{extra}: {result.reason}')
    return 0


def cmd_profile(args):
    """수신 방식과 인사말 판정에 쓰는 내 주소·그룹·이름 프로필을 보여 준다 (아무것도 바꾸지 않음)."""
    from reda import identity as ident
    from reda.gmail import client
    from reda.state import StateDB
    settings = _settings()
    service = client.get_gmail_service(settings)
    with StateDB.open(settings) as state:
        identity = ident.load(settings, service=service, state=state)
        rows = {r['key']: r for r in state.profile_rows()}

    def source(env_value, key, fallback):
        if env_value:
            return '설정(.env, 개발용 덮어쓰기)'
        if key in rows and rows[key]['value']:
            return f'상태 DB (처음 출처: {rows[key]["source"]})'
        return fallback

    print(f'개인 주소: {", ".join(sorted(identity.personal)) or "(없음)"}'
          f'  ← {source(settings.my_addresses, "email", "Gmail 계정 주소")}')
    print(f'그룹 주소: {", ".join(sorted(identity.groups)) or "(없음)"}'
          f'  ← {source(settings.my_groups, "groups", "없음")}'
          ' (List-Id·그룹이 바꾼 From으로 알아내는 그룹은 메일마다 더한다)')
    print(f'표시 이름 출처: '
          f'{source(settings.my_name, "display_name", "Gmail 보내는 이름 또는 최근 보낸 메일")}')
    print(f'전체 이름: {", ".join(identity.full_names) or "(없음)"}')
    print(f'이름: {", ".join(identity.given_names) or "(없음)"}')
    print(f'성: {", ".join(identity.surnames) or "(없음)"}')
    print(f'직함: {", ".join(identity.titles) or "(없음: REDA_MY_TITLES로 넣으면 \"성 + 직함\" 호칭도 알아본다)"}'
          + (f'  ← {source(settings.my_titles, "titles", "")}' if identity.titles else ''))
    if not identity.has_names:
        print('이름이 없어 인사말로 담당자를 가리지 못한다. 그룹으로 받은 답장필요는 확인필요가 된다.')
    return 0


def cmd_golden_export(args):
    from reda import golden
    from reda import identity as ident
    from reda.gmail import client
    from reda.state import StateDB
    args.file = args.file or golden.DEFAULT_PATH
    settings = _settings()
    service = client.get_gmail_service(settings)
    with StateDB.open(settings) as state:          # 읽기만 한다
        identity = ident.load(settings, service=service, state=state)
    total, added = golden.export(service, settings, args.file, query=args.query,
                                 max_results=args.max, identity=identity)
    print(f'{args.file}: 전체 {total}통 (이번에 더한 메일 {added}통)')
    print('`정답` 칸에 답장필요 또는 참고를 적는다 (r/f로 줄여 써도 된다). 비워 둔 행은 평가하지 않는다.')
    return 0


def cmd_golden_eval(args):
    from reda import golden
    from reda import identity as ident
    from reda.classify import ClassifierUnavailable, make_classifier
    from reda.gmail import client
    from reda.state import StateDB
    args.file = args.file or golden.DEFAULT_PATH
    if not args.file.exists():
        print(f'오류: 골든 세트 파일이 없음: {args.file}. 먼저 `uv run reda golden export`로 만든다',
              file=sys.stderr)
        return 1
    settings = _settings()
    service = client.get_gmail_service(settings)
    result_path = args.file.with_name(args.file.stem + '_result.csv')
    with StateDB.open(settings) as state:          # 읽기만 한다
        identity = ident.load(settings, service=service, state=state)
    try:
        report = golden.evaluate(service, settings, make_classifier(settings, identity),
                                 args.file, result_path=result_path, identity=identity,
                                 include_unanswered=args.all)
    except ClassifierUnavailable as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1
    print(golden.format_report(report))
    print(f'메일별 결과: {result_path}')
    return 1 if report.stopped else 0


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

    classifier = sub.add_parser('classifier', help='지금 쓰는 분류기 확인')
    classifier.add_argument('--try', dest='try_', action='store_true',
                            help='지어낸 고객 문의 한 통을 분류해 본다 (분류기 API를 부른다)')
    classifier.set_defaults(func=cmd_classifier)

    summary = sub.add_parser('summary', help='출근 전 요약 보기 (--send면 메신저로 보냄)')
    summary.add_argument('--hours', type=int, default=None, help='최근 N시간 (기본: 지난 요약 이후, 처음이면 24시간)')
    summary.add_argument('--send', action='store_true', help='메신저로 보내고 지난 요약 시점을 옮긴다')
    summary.set_defaults(func=cmd_summary)

    alert = sub.add_parser('alert', help='급한 메일 알림 미리 보기 (--send면 메신저로 보냄, 시험용)')
    alert.add_argument('--hours', type=int, default=24, help='최근 N시간에 분류한 메일 (기본 24)')
    alert.add_argument('--send', action='store_true', help='메신저로 보낸다 (자동 알림 기준점은 그대로)')
    alert.set_defaults(func=cmd_alert)

    drafts = sub.add_parser('draft-pending', help='답장필요 메일의 초안을 에이전트에게 맡김 (발송 없음)')
    drafts.add_argument('--dry-run', action='store_true', help='맡길 메일만 보여 준다 (에이전트를 부르지 않음)')
    drafts.set_defaults(func=cmd_draft_pending)

    profile = sub.add_parser('profile', help='내 주소·그룹·이름 프로필 확인 (팀 메일 담당자 판정에 씀)')
    profile.set_defaults(func=cmd_profile)

    golden = sub.add_parser('golden', help='골든 세트로 분류 정확도 재기')
    golden_sub = golden.add_subparsers(dest='golden_command', required=True)
    export = golden_sub.add_parser('export', help='메일 목록을 CSV로 내보내기 (Gmail을 바꾸지 않음)')
    export.add_argument('--query', default='in:inbox newer_than:14d', help='Gmail 검색 쿼리')
    export.add_argument('--max', type=int, default=150, help='최대 메일 수')
    export.set_defaults(func=cmd_golden_export)
    evaluate = golden_sub.add_parser('eval', help='정답이 있는 메일을 분류해 보기 (분류기 API를 부른다)')
    evaluate.add_argument('--all', action='store_true',
                          help='정답이 없는 메일도 분류해 결과 파일에 남긴다 (정답 달기 전 미리 보기)')
    evaluate.set_defaults(func=cmd_golden_eval)
    for p in (export, evaluate):
        p.add_argument('--file', type=Path, default=None,
                       help='CSV 경로 (기본: 저장소의 tests/private/golden.csv, git 제외)')
    return parser


def main(argv=None):
    _load_env()
    args = build_parser().parse_args(argv)
    from reda.config import ConfigError
    from reda.gmail.client import GmailAuthError
    try:
        return args.func(args)
    except (GmailAuthError, ConfigError) as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
