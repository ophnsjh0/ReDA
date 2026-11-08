"""Claude API로 분류 응답을 받는다. 응답 형식은 structured outputs(JSON 스키마)로 고정한다.

설정: REDA_CLASSIFIER=anthropic, REDA_CLASSIFIER_MODEL(비우면 DEFAULT_MODEL),
REDA_CLASSIFIER_EFFORT(low/medium/high, 비우면 보내지 않는다), 자격 증명 ANTHROPIC_API_KEY.
"""
import os

import anthropic

from reda.classify import model_for
from reda.classify.base import ClassifierBusy, ClassifierRequestRejected, ClassifierUnavailable
from reda.classify.llm import OUTPUT_SCHEMA, LLMClassifier

DEFAULT_MODEL = 'claude-opus-5-5'


class AnthropicComplete:
    def __init__(self, model, effort='low', client=None):
        # SDK는 키가 없어도 클라이언트를 만들고 첫 요청에서야 TypeError를 낸다. 미리 확인한다
        if client is None and not (os.environ.get('ANTHROPIC_API_KEY')
                                   or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
            raise ClassifierUnavailable('ANTHROPIC_API_KEY가 설정되지 않음')
        self._client = client or anthropic.Anthropic()
        self._model = model
        self._effort = effort

    def __call__(self, system, user):
        output_config = {'format': {'type': 'json_schema', 'schema': OUTPUT_SCHEMA}}
        if self._effort:
            output_config['effort'] = self._effort
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=[{'role': 'user', 'content': user}],
                output_config=output_config,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                anthropic.NotFoundError) as exc:
            # 키·권한·모델 이름: 메일마다 같은 오류가 난다
            raise ClassifierUnavailable(
                f'Claude API 설정 오류 ({exc.__class__.__name__}, 모델 {self._model})') from exc
        except (anthropic.BadRequestError, anthropic.UnprocessableEntityError) as exc:
            # 이 메일 탓인지(입력) 설정 탓인지(모델이 지원하지 않는 옵션) 처리 흐름이 판단한다
            raise ClassifierRequestRejected(
                f'Claude API가 요청을 거부함 ({exc.__class__.__name__}, 모델 {self._model})') from exc
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError) as exc:
            raise ClassifierBusy(f'Claude API 일시 장애 ({exc.__class__.__name__})') from exc
        if response.stop_reason == 'refusal':
            return '{}'  # 형식 오류로 처리되어 확인필요가 된다
        return next((b.text for b in response.content if b.type == 'text'), '')


def make(settings):
    model = model_for(settings)
    return LLMClassifier(AnthropicComplete(model, settings.classifier_effort), settings,
                         name=f'anthropic/{model}')
