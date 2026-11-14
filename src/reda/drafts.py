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


# 초안에서 절대 되돌리지 않는 표식. 원래 값을 초안(=상대방에게 갈 답장)에 넣지 않는다
NEVER_RESTORE = ('SECRET', 'ACCOUNT', 'RRN', 'CARD', 'BANK')
HIDDEN = '[확인 필요: 가린 값]'


def unmask(text, masker, allowed=None):
    """보여 줄 때 쓴 표식을 원래 값으로 되돌린다. 되돌리는 것은 IP·전화·메일 주소뿐이고,
    allowed(답장하는 스레드에서 나온 표식)에 든 것만이다.

    - 비밀번호·계정·주민·카드·계좌 번호 → [확인 필요: 가린 값] (사람이 직접 넣는다)
    - 이전 메일 검색에서 나온 값, 지어낸 표식 → [확인 필요]
    allowed=None이면 Masker가 아는 IP·전화·메일 주소 표식을 모두 되돌린다 (테스트용)
    """
    mapping = masker.mapping

    def repl(match):
        token = match.group(0)
        kind = token[1:].split('_', 1)[0]
        if kind in NEVER_RESTORE and token in mapping:
            return HIDDEN
        if token not in mapping or (allowed is not None and token not in allowed):
            return CHECK
        return mapping[token]
    return _TOKEN.sub(repl, text or '')


def reference_block(evidence, ctx):
    """evidence 항목 두 가지 (기획안 예시 ①②의 참고 블록 형식, `·` 줄):
    - {'message_id': ..., 'what': ...}: 근거 메일. 이번에 보여 준 메일만 받고 링크는 코드가 붙인다
    - {'note': ...}: 메모 (예: "소프트웨어 버전은 이전 메일에서 찾지 못해 [확인 필요]로 표시함").
      링크를 붙이지 않으므로 근거로 위장할 수 없다
    """
    if len(evidence) > MAX_EVIDENCE:
        raise DraftError(f'근거는 {MAX_EVIDENCE}개까지')
    lines = [REFERENCE_HEADER]
    unknown = []
    for item in evidence:
        item = item or {}
        message_id = str(item.get('message_id', '') or '').strip()
        if not message_id and item.get('note'):
            lines.append('· ' + unmask(str(item['note']).strip(), ctx.masker,
                                       ctx.thread_tokens)[:300])
            continue
        what = unmask(str(item.get('what', '')).strip(), ctx.masker, ctx.thread_tokens)[:300]
        info = ctx.known.get(message_id)
        if not info:
            unknown.append(message_id or '(빈 값)')
            continue
        lines.append(f'· {what} — {info["date"]}, {info["from"]}, 「{info["subject"]}」 '
                     f'{gmail_link(info["thread_id"])}')
    if unknown:
        raise DraftError('근거로 쓸 수 없는 메일 ID: ' + ', '.join(unknown) +
                         '. get_thread나 search_customer_history 결과에 나온 message_id만 쓴다')
    if len(lines) == 1:
        lines.append('· 근거로 쓴 메일 없음. 본문의 [확인 필요]를 직접 확인한다')
    return '\n'.join(lines)


def compose(body, evidence, ctx):
    body = (body or '').strip()
    if not body:
        raise DraftError('본문이 비어 있음')
    if len(body) > MAX_BODY_CHARS:
        raise DraftError(f'본문이 너무 김 ({len(body)}자, 최대 {MAX_BODY_CHARS}자)')
    if REFERENCE_HEADER in body:
        raise DraftError('참고 블록은 코드가 붙인다. 본문에서 빼고 evidence로 넘긴다')
    return unmask(body, ctx.masker, ctx.thread_tokens) + '\n\n' + reference_block(evidence or [], ctx)


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
    if state.draft_for_message(ctx.reply_to['id']):
        raise DraftError('이 메일에는 이미 ReDA가 만든 초안이 있음. 사용자가 확인하기 전에는 '
                         '새로 만들지 않는다')
    text = compose(body, evidence, ctx)
    draft = client.create_reply_draft(service, ctx.reply_to, text,
                                      cc=reply_cc(ctx.reply_to, identity))
    state.record_draft(draft['id'], ctx.reply_to['id'], ctx.thread_id,
                       [e.get('message_id') for e in evidence or [] if e.get('message_id')])
    return draft['id'], text


def body_checks(text):
    """본문(참고 블록 앞)에 남은 [확인 필요] 수. 사용자가 채워야 할 곳이다."""
    body = text.split(REFERENCE_HEADER, 1)[0]
    return body.count(CHECK) + body.count(HIDDEN)
