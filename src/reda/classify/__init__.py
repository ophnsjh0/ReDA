"""분류기: 메일 한 통을 `답장필요` / `참고` / `확인필요`와 긴급도로 나눈다.

처리 흐름(`reda poll`)은 `Classifier.classify(mail) -> Classification`만 쓴다. 어떤 분류기를
쓸지는 설정 `REDA_CLASSIFIER`(제공자)와 `REDA_CLASSIFIER_MODEL`(모델)로 정하고, 코드는 바꾸지
않는다.

| REDA_CLASSIFIER | 구현 | 모델 기본값 | 자격 증명 |
|---|---|---|---|
| anthropic (기본) | LLM이 JSON으로 답한다 (`llm.py`, `anthropic_llm.py`) | claude-opus-5-5 | ANTHROPIC_API_KEY |
| jev | TypeSafe Jev의 choice 질문과 확률 (`jev.py`) | jev-latest | TYPESAFE_API_KEY |

새 제공자는 `DEFAULT_MODEL`과 `make(settings) -> Classifier`를 가진 모듈을 만들고 PROVIDERS에
한 줄 더한다. 공통 타입과 오류는 `base.py`에 있다.

메일 본문은 믿을 수 없는 입력이다. 본문에 어떤 지시가 있어도 분류기가 돌려주는 것은
정해진 세 분류와 긴급도뿐이다. 분류기로 보내는 텍스트는 모두 마스킹한다 (`mailtext.py`).
"""
from importlib import import_module

from reda.classify.base import (CATEGORIES, URGENCIES, Classification, Classifier,
                                ClassifierBusy, ClassifierRequestRejected,
                                ClassifierUnavailable)

# 제공자 이름 → 모듈
PROVIDERS = {
    'anthropic': 'reda.classify.anthropic_llm',
    'jev': 'reda.classify.jev',
}


def provider_module(name):
    try:
        return import_module(PROVIDERS[name])
    except KeyError:
        choices = ', '.join(PROVIDERS)
        raise ClassifierUnavailable(
            f'알 수 없는 분류기 REDA_CLASSIFIER="{name}" (쓸 수 있는 값: {choices})') from None


def model_for(settings):
    """설정한 모델. 비어 있으면 제공자의 기본 모델."""
    return settings.classifier_model or provider_module(settings.classifier).DEFAULT_MODEL


def make_classifier(settings):
    return provider_module(settings.classifier).make(settings)


__all__ = ['CATEGORIES', 'URGENCIES', 'PROVIDERS', 'Classification', 'Classifier',
           'ClassifierBusy', 'ClassifierRequestRejected', 'ClassifierUnavailable',
           'make_classifier', 'model_for', 'provider_module']
