"""Claude API로 분류 응답을 받는다. 응답 형식은 structured outputs(JSON 스키마)로 고정한다."""
import anthropic

from reda.classify import OUTPUT_SCHEMA, ClassifierUnavailable


class AnthropicComplete:
    def __init__(self, model, effort='low', client=None):
        # 자격 증명은 SDK 기본 순서로 찾는다 (ANTHROPIC_API_KEY 등)
        self._client = client or anthropic.Anthropic()
        self._model = model
        self._effort = effort

    def __call__(self, system, user):
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=[{'role': 'user', 'content': user}],
                output_config={
                    'effort': self._effort,
                    'format': {'type': 'json_schema', 'schema': OUTPUT_SCHEMA},
                },
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                anthropic.NotFoundError) as exc:
            raise ClassifierUnavailable(f'Claude API 설정 오류: {exc.__class__.__name__}') from exc
        if response.stop_reason == 'refusal':
            return '{}'  # 형식 오류로 처리되어 확인필요가 된다
        return next((b.text for b in response.content if b.type == 'text'), '')
