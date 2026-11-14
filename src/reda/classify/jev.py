"""TypeSafe Jev 분류기 (https://docs.typesafe.ai, 프로젝트 skill `typesafe-ai`).

Jev는 글을 쓰지 않고 정해 둔 질문에 확률로 답한다. 질문(v2)은 결정 대신 판단을 하나씩 묻고
(요청이 있나, 공유용 전달인가, 자동 통지인가, 마무리 인사인가, 특정인을 부르나, 얼마나 급한가),
분류는 코드의 판정표가 정한다. 질문·기준값·판정표는 `jev_questions.py` 한 곳에 있다.

설정: REDA_CLASSIFIER=jev, REDA_CLASSIFIER_MODEL(비우면 DEFAULT_MODEL), REDA_JEV_MIN_PROBABILITY
(예로 볼 기준, 기본 0.7. 아니오 기준은 1 - 이 값), 자격 증명 TYPESAFE_API_KEY.
모델은 버전으로 고정한다(결정 2-9). 별칭 jev-latest는 새 버전이 나오면 답이 바뀐다.
"""
import json
import os
import time
import urllib.error
import urllib.request

from reda.classify import jev_questions as jq
from reda.classify import model_for
from reda.classify.base import (Classification, ClassifierBusy, ClassifierRequestRejected,
                                ClassifierUnavailable)
from reda.classify.mailtext import structured_state
from reda.gmail.client import LABEL_CHECK
from reda.identity import PERSONAL_TO, Identity, greets_me

DEFAULT_MODEL = 'jev-1.14.0'
ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
TIMEOUT = 30
RETRY_STATUS = (429, 529)          # 사용량 제한, 과부하: 잠시 뒤 다시 시도
FATAL_STATUS = (401, 403, 404)     # 키·권한·모델: 메일마다 같은 오류
REJECTED_STATUS = (400, 413, 422)  # 요청 거부: 이 메일 탓인지 설정 탓인지 처리 흐름이 판단
RETRIES = 3


def build_questions():
    return jq.QUESTIONS


def http_post(url, payload, headers, timeout=TIMEOUT):
    """(status, body dict). 표준 라이브러리만 쓴다. 연결 실패는 ClassifierBusy."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                     headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b'{}')
        except ValueError:
            body = {}
        return exc.code, body
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ClassifierBusy(f'Jev API 연결 실패 ({exc.__class__.__name__})') from exc


class JevClassifier:
    def __init__(self, settings, *, api_key=None, post=http_post, sleep=time.sleep,
                 identity=None):
        self._settings = settings
        # 수신 방식·보낸 사람 구분에 쓰는 주소 정보. 이름은 Jev에 보내지 않는다
        self._identity = identity or Identity.from_settings(settings)
        self._model = model_for(settings)
        self._yes = settings.jev_min_probability
        self._api_key = api_key if api_key is not None else os.environ.get('TYPESAFE_API_KEY', '')
        if not self._api_key:
            raise ClassifierUnavailable('TYPESAFE_API_KEY가 설정되지 않음')
        self._post, self._sleep = post, sleep
        self.name = f'jev/{self._model}'

    def _request(self, payload):
        headers = {'Authorization': f'Bearer {self._api_key}',
                   'Content-Type': 'application/json'}
        for attempt in range(RETRIES):
            if attempt:
                self._sleep(2 ** (attempt - 1))
            status, body = self._post(ENDPOINT, payload, headers)
            if status == 200:
                return body
            if status in FATAL_STATUS:
                raise ClassifierUnavailable(f'Jev API 설정 오류 (HTTP {status}, 모델 {self._model})')
            if status in REJECTED_STATUS:
                raise ClassifierRequestRejected(f'Jev API가 요청을 거부함 (HTTP {status})')
            if status not in RETRY_STATUS:
                break
        # 사용량 제한·과부하가 계속되거나 서버 오류: 모든 메일에 같이 걸리므로 실행을 멈춘다
        raise ClassifierBusy(f'Jev API 일시 장애 (HTTP {status})')

    def payload(self, mail):
        state = structured_state(mail, self._settings, self._identity, reader=jq.READER)
        return {'model': self._model, 'state': state, 'questions': build_questions()}

    def classify(self, mail):
        body = self._request(self.payload(mail))
        group_or_cc = self._identity.recipient_mode(mail) != PERSONAL_TO
        return self.interpret(body, subject=mail.subject, group_or_cc=group_or_cc,
                              greets_me=greets_me(mail.body, self._identity))

    def interpret(self, body, *, subject='', group_or_cc=False, greets_me=False):
        values = jq.answers_to_values(body)
        model = body.get('model', self._model) if isinstance(body, dict) else self._model
        if values is None:
            return Classification(LABEL_CHECK, '보통', 'Jev 응답 형식 오류')
        category, reason = jq.decide(values, group_or_cc=group_or_cc, greets_me=greets_me,
                                     yes=self._yes)
        todo = jq.todo(values, yes=self._yes)
        usage = body.get('usage') or {}
        detail = {'model': model, **{k: round(values[k], 4) for k in jq.NOULS},
                  'urgency_score': round(values['urgency']['score'], 4),
                  'urgency_p': [round(p, 4) for p in values['urgency']['p']],
                  'input_tokens': usage.get('input_tokens')}
        return Classification(
            category, jq.urgency_label(values['urgency'], subject), f'Jev {model}: {reason}',
            confidence=values['asks_reply'],
            greets_specific=values['greets_specific_person'],
            urgency_score=values['urgency']['score'],
            todo=todo,
            detail={**detail, 'todo': todo})


def make(settings, identity=None):
    return JevClassifier(settings, identity=identity)
