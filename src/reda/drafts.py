"""답장 초안 만들기와 저장 (3단계 3번). 에이전트가 쓴 본문을 받아 코드가 마무리한다.

코드가 정하는 것 (에이전트가 바꿀 수 없다)
- 받는 사람: 원래 메일의 Reply-To(없으면 From). 참조는 원래 메일의 받는사람·참조에서 내 주소를 뺀 것
  (결정 3-4: 원래 Cc 유지). 에이전트는 주소를 넘기지 않는다. 다른 사람 주소로 가는 초안을 막는다
- 근거: 이번에 보여 준 메일(스레드, 같은 고객사 이전 메일)만 근거로 인정하고 링크는 코드가 붙인다.
  지어낸 근거를 막는다
- 마스킹 되돌리기: 보여 줄 때 쓴 Masker로 [IP_1] 같은 표식을 원래 값으로 바꾼다. 되돌릴 수 없는 표식은
  `[확인 필요]`로 바꾼다
- 초안 끝의 참고 블록

발송 기능은 없다. 저장만 한다.
"""
import re

from reda.context import gmail_link
from reda.gmail import client
from reda.mail import parse_addresses

REFERENCE_HEADER = '---- [참고 — 보내기 전 삭제] ----'
CHECK = '[확인 필요]'
MAX_BODY_CHARS = 20000
MAX_EVIDENCE = 20
_TOKEN = re.compile(r'\[[A-Z]+_\d+\]')


class DraftError(ValueError):
    """에이전트에게 돌려줄 오류. 메시지를 보고 고쳐서 다시 부를 수 있게 쓴다."""


def unmask(text, masker):
    """보여 줄 때 쓴 표식을 원래 값으로. 모르는 표식(지어낸 것)은 [확인 필요]."""
    mapping = masker.mapping
    return _TOKEN.sub(lambda m: mapping.get(m.group(0), CHECK), text or '')


def reference_block(evidence, ctx):
    """evidence: [{'message_id': ..., 'what': ...}]. 보여 준 메일만 받는다."""
    if len(evidence) > MAX_EVIDENCE:
        raise DraftError(f'근거는 {MAX_EVIDENCE}개까지')
    lines = [REFERENCE_HEADER]
    unknown = []
    for item in evidence:
        message_id = str((item or {}).get('message_id', '')).strip()
        what = unmask(str((item or {}).get('what', '')).strip(), ctx.masker)[:300]
        info = ctx.known.get(message_id)
        if not info:
            unknown.append(message_id or '(빈 값)')
            continue
        lines.append(f'- {what} — {info["date"]}, {info["from"]}, 「{info["subject"]}」 '
                     f'{gmail_link(info["thread_id"])}')
    if unknown:
        raise DraftError('근거로 쓸 수 없는 메일 ID: ' + ', '.join(unknown) +
                         '. get_thread나 search_customer_history 결과에 나온 message_id만 쓴다')
    if len(lines) == 1:
        lines.append('- 근거로 쓴 메일 없음. 본문의 [확인 필요]를 직접 확인한다')
    return '\n'.join(lines)


def compose(body, evidence, ctx):
    body = (body or '').strip()
    if not body:
        raise DraftError('본문이 비어 있음')
    if len(body) > MAX_BODY_CHARS:
        raise DraftError(f'본문이 너무 김 ({len(body)}자, 최대 {MAX_BODY_CHARS}자)')
    if REFERENCE_HEADER in body:
        raise DraftError('참고 블록은 코드가 붙인다. 본문에서 빼고 evidence로 넘긴다')
    return unmask(body, ctx.masker) + '\n\n' + reference_block(evidence or [], ctx)


def reply_cc(message, identity):
    """원래 메일의 받는사람·참조에서 내 주소와 답장 받는 사람을 뺀 것 (결정 3-4)."""
    mine = set(identity.personal)
    to_addr = parse_addresses(client.get_header(message, 'Reply-To')
                              or client.get_header(message, 'From'))
    seen, cc = set(mine) | set(to_addr), []
    for addr in parse_addresses(client.get_header(message, 'To'), client.get_header(message, 'Cc')):
        if addr not in seen:
            seen.add(addr)
            cc.append(addr)
    return ', '.join(cc)


def save(service, state, ctx, body, evidence, identity):
    """초안을 원래 스레드에 저장한다. (draft_id, 저장한 본문)."""
    if state.drafts_for_thread(ctx.thread_id):
        raise DraftError('이 스레드에는 이미 ReDA가 만든 초안이 있음. 사용자가 확인하기 전에는 '
                         '새로 만들지 않는다')
    text = compose(body, evidence, ctx)
    draft = client.create_reply_draft(service, ctx.reply_to, text,
                                      cc=reply_cc(ctx.reply_to, identity))
    state.record_draft(draft['id'], ctx.reply_to['id'], ctx.thread_id,
                       [e.get('message_id') for e in evidence or []])
    return draft['id'], text
