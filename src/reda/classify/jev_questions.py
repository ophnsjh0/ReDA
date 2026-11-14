"""Jev 질문, 기준값, 판정표 (결정 2-8 v2). 문구와 기준을 고칠 때는 이 파일만 고친다.

설계 원칙 (프로젝트 skill `typesafe-ai`, docs.typesafe.ai)
- 판단은 Jev가, 결정은 코드가 한다. Jev에게 "답장해야 하나?"(결정)를 묻지 않고, 결정에 필요한
  판단을 질문 하나에 하나씩 묻는다. 결정은 아래 `decide`의 판정표가 한다.
- noul(참일 확률)은 0.7 이상이면 예, 0.3 이하면 아니오, 그 사이는 판단이 안 선 것으로 본다.
- 질문은 한 요청에 함께 보낸다. 서로의 답을 보지 못하므로 필요한 맥락은 state나 질문에 둔다.
- 인사말이 누구를 부르는지는 코드가 내 이름으로 찾는다 (reda.identity). Jev에는 내 이름을 보내지 않고
  "특정한 한 사람을 부르는가"만 묻는다 (결정 2-6).

문구를 바꿀 때는 특정 메일을 맞추려고 고치지 말고(문서가 말하는 "prompt gymnastics"), 정의를 더
분명하게 하거나 state를 고친다. 고친 뒤에는 골든 세트로 다시 잰다.
"""
from reda.gmail.client import LABEL_CHECK, LABEL_FYI, LABEL_REPLY

# 기준값 기본값. 예(yes) 기준은 설정 REDA_JEV_MIN_PROBABILITY로 바꿀 수 있고, 아니오 기준은 1 - 예
YES = 0.7

READER = ('받는 사람은 기술지원(TAC) 엔지니어다. 고객사와 제조사의 네트워크 장비(L4, 방화벽, 무선 등) '
          '장애, 설정, 케이스 문의에 답한다. 같은 회사 동료나 팀 메일(그룹 주소)로도 메일을 받는다.')

QUESTIONS = {
    # 회신이 필요한가. 할 일을 알리기만 하는 메일(제출 기한, 교육, 점검 일정 안내)은 여기서 아니오이고
    # 아래 assigns_task로 따로 잡는다 (2026-11-08 v2.1: "일을 해 달라"까지 묶었더니 안내 메일이
    # 답장필요로 갔다)
    'asks_reply': {
        'type': 'noul',
        'instructions': '`mail.latest_message`에서 보낸 사람이 받는 사람의 회신(답변, 자료, 의견, '
                        '가능 여부 확인)을 기다리는가?',
        'criteria': {
            'true': '받는 사람이 메일로 답해야 보낸 사람이 일을 진행할 수 있다. 예: "원인 확인 후 회신 '
                    '부탁드립니다", "가능한 일정 알려 주세요", "로그 분석 결과 공유 부탁드립니다"',
            'false': '회신을 기다리지 않는다. 예: 결과 보고, 공지, 제출 기한·교육·점검 일정처럼 할 일을 '
                     '알리기만 하는 안내, 감사 인사, 설문·만족도 조사·피드백 요청처럼 여러 사람에게 '
                     '보낸 요청',
        },
    },
    'is_share_forward': {
        'type': 'noul',
        'instructions': '보낸 사람이 다른 사람이 쓴 메일을 참고하라고 전달하거나 공유한 메일인가?',
        'criteria': {
            'true': '다른 메일을 넘겨주는 것이 이 메일의 목적이다. 예: "전달드립니다", "공유드립니다", '
                    '"참고 바랍니다"와 함께 전달된 원문이 붙어 있다',
            'false': '보낸 사람이 직접 쓴 질문, 요청, 보고가 이 메일의 목적이다',
        },
    },
    # 2026-11-08 v2.2: 여러 사람에게 보낸 설문·피드백 요청·제출 기한 안내가 회신 요청으로 잡혔다
    'is_broadcast': {
        'type': 'noul',
        'instructions': '이 메일은 여러 사람에게 같은 내용으로 보낸 공지, 안내, 설문, 홍보인가?',
        'criteria': {
            'true': '받는 사람 한 명을 두고 쓴 글이 아니다. 예: 전사·팀 공지, 제출 기한 안내, 교육 안내, '
                    '만족도 설문, 피드백 요청 메일, 제품·행사 홍보',
            'false': '받는 사람(또는 그 팀)이 맡은 특정한 건을 두고 쓴 글. 예: 특정 장애·케이스·견적에 '
                     '대한 문의나 답변',
        },
    },
    'is_automated': {
        'type': 'noul',
        'instructions': '이 메일은 사람이 아니라 시스템이 자동으로 만든 메일인가?',
        'criteria': {
            'true': '시스템이 만든 알림, 접수·상태 변경 통지, 보안 공지, 설문 요청, 뉴스레터',
            'false': '사람이 직접 쓴 메일. 케이스 관리 시스템을 거쳐 왔더라도 담당자가 직접 쓴 글이다',
        },
    },
    'is_closing': {
        'type': 'noul',
        'instructions': '`mail.latest_message`가 감사나 확인 인사로 대화를 마무리할 뿐인가?',
        'criteria': {
            'true': '"감사합니다", "확인했습니다", "잘 받았습니다" 같은 인사뿐이고 새 질문이나 요청이 없다',
            'false': '새 질문, 요청, 정보가 들어 있다',
        },
    },
    'greets_specific_person': {
        'type': 'noul',
        'instructions': '`mail.latest_message`의 인사말이 특정한 한 사람을 이름이나 직함으로 부르는가?',
        'criteria': {
            'true': '"김철수 과장님", "길동님", "Dear Mr. Kim"처럼 한 사람을 부른다',
            'false': '"안녕하세요"만 있거나, "담당자님", "여러분", "팀원 여러분"처럼 특정한 사람을 '
                     '부르지 않거나, 인사말이 없다',
        },
    },
    # 답장과 따로 보는 "할 일·일정" (참고 메일에도 붙는다. 9단계 일정 제안에 쓴다)
    'assigns_task': {
        'type': 'noul',
        'instructions': '`mail.latest_message`가 받는 사람에게 회신 말고 해야 할 일(작업, 점검, 방문, '
                        '참석, 준비)을 맡기거나 알리는가?',
        'criteria': {
            'true': '받는 사람이 할 일이 있다. 예: "11월 12일 14시 정기 점검에 참석 부탁드립니다", '
                    '"장비 회수 일정이 확정되었습니다", "교육 대상자로 지정되었습니다"',
            'false': '받는 사람이 할 일이 없다. 예: 정보 공유, 결과 보고, 다른 사람이 맡은 일의 진행 상황',
        },
    },
    'has_schedule': {
        'type': 'noul',
        'instructions': '`mail.latest_message`에 날짜나 시간이 정해진 일정(점검, 회의, 방문, 교육, 마감)이 '
                        '있는가?',
        'criteria': {
            'true': '"11월 12일 14:00", "다음 주 화요일 오전", "11/20까지"처럼 날짜나 시간이 적힌 일정이 있다',
            'false': '날짜나 시간이 정해진 일정이 없다',
        },
    },
    'urgency': {
        'type': 'score',
        'instructions': '받는 사람이 이 메일을 얼마나 급하게 처리해야 하는가?',
        'criteria': [
            '처리할 일이 없다. 정보 공유, 공지, 일정 안내, 결과 보고',
            '기한이 없는 일반 문의나 요청',
            '며칠 안에 처리해 달라는 요청, 또는 정해진 날짜가 있는 작업 준비',
            '장애나 서비스 중단이 진행 중이거나, 보안 사고이거나, 오늘 안에 처리해 달라는 요청',
        ],
    },
}
NOULS = tuple(k for k, q in QUESTIONS.items() if q['type'] == 'noul')


def answers_to_values(body):
    """Jev 응답 → {'asks_reply': 0.92, ..., 'urgency': {'score': 1.4, 'p': [..4개..]}}.
    형식이 틀리면 None."""
    answers = body.get('answers') if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        return None
    values = {}
    try:
        for key in NOULS:
            values[key] = float(answers[key]['noul'])
        urgency = answers['urgency']
        probs = urgency.get('probabilities') or {}
        values['urgency'] = {
            'score': float(urgency['score']),
            'p': [float(probs.get(str(i), 0.0)) for i in range(len(QUESTIONS['urgency']['criteria']))],
        }
    except (KeyError, TypeError, ValueError):
        return None
    return values


def urgency_label(urgency, subject=''):
    """수준별 확률로 정한다 (점수 평균은 가운데로 끌려가므로). 제목의 [긴급]은 코드가 본다."""
    p = urgency['p']
    if '[긴급]' in (subject or '') or p[3] >= 0.5:
        return '높음'
    if p.index(max(p)) == 0:
        return '낮음'
    return '보통'


def _fmt(values):
    short = {'asks_reply': '회신', 'is_share_forward': '공유', 'is_broadcast': '공지',
             'is_automated': '자동',
             'is_closing': '마무리', 'greets_specific_person': '특정인'}
    return ' · '.join(f'{short[k]} {values[k]:.2f}' for k in short)


TODO_SCHEDULE, TODO_TASK = '일정', '할 일'


def todo(values, *, yes=YES):
    """답장과 따로 보는 할 일 표시: '일정'(날짜가 있는 할 일) / '할 일' / ''.

    - 시스템 자동 메일(홍보, 알림)의 일정은 내 일정이 아니다
    - 날짜가 정해진 일정이 있고 할 일이 아니라고 분명하지 않으면 일정으로 본다 (확정된 점검 일정 통보처럼
      "할 일"로는 애매하게 나와도 캘린더에 넣을 일이 있다)
    """
    no = 1 - yes
    if values['is_automated'] >= yes:
        return ''
    if values['assigns_task'] >= yes:
        return TODO_SCHEDULE if values['has_schedule'] >= yes else TODO_TASK
    if values['has_schedule'] >= yes and values['assigns_task'] > no:
        return TODO_SCHEDULE
    return ''


def decide(values, *, group_or_cc, greets_me=False, yes=YES):
    """판정표. (분류, 이유). 담당자 판정(그룹 메일)은 이 뒤에 reda.identity.resolve가 한다.

    group_or_cc: 개인 주소가 받는사람(To)에 없다 (그룹·참조·숨은참조로 받음). 코드가 아는 사실.
    greets_me: 인사말이 나를 부른다 (코드가 내 이름으로 찾음). 그러면 공유·공지라도 내 메일로 본다.
    할 일이 있는 공지·공유 메일은 `참고`이면서 할 일 표시(`todo`)가 붙는다.
    """
    no = 1 - yes
    asks = values['asks_reply']
    summary = _fmt(values)
    if not greets_me:
        if values['is_share_forward'] >= yes and group_or_cc:
            return LABEL_FYI, f'팀에 공유된 메일 ({summary})'
        # 회신을 분명히 요청한 공지(고객사가 여러 담당자에게 보낸 일정 확인 요청 등)는 내리지 않는다.
        # 공지 판단으로 답장필요를 놓치는 것이 공지를 확인필요로 보내는 것보다 나쁘다
        if values['is_broadcast'] >= yes and asks < yes:
            return LABEL_FYI, f'여러 사람에게 보낸 공지·안내 ({summary})'
    if asks <= no:
        return LABEL_FYI, f'회신 요청 없음 ({summary})'
    if asks < yes:
        return LABEL_CHECK, f'회신을 기다리는지 판단이 안 섬 ({summary})'
    if values['is_closing'] >= yes:
        return LABEL_FYI, f'마무리 인사 ({summary})'
    if values['is_automated'] >= yes:
        return LABEL_CHECK, f'자동 통지 안의 요청 ({summary})'
    return LABEL_REPLY, f'회신 요청 ({summary})'
