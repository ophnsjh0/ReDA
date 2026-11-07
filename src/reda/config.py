"""ReDA 설정. 환경 변수(.env)에서 읽는다.

사용자(Hermes 프로필)마다 REDA_DATA_DIR을 따로 둔다. Gmail 토큰과 문서 검색 DB가
이 폴더에 생기므로, 폴더가 곧 사용자 한 명의 데이터 경계다.
"""
import os
from dataclasses import dataclass
from pathlib import Path


def _env(name, default=''):
    """환경 변수를 읽는다. 비어 있으면(KEY=) 기본값을 쓴다."""
    value = os.environ.get(name, '').strip()
    return value if value else default


def _path(name, default):
    return Path(_env(name, str(default))).expanduser()


def _list(name, default=''):
    return tuple(v.strip() for v in os.environ.get(name, default).split(',') if v.strip())


def _mapping(name, default=''):
    """'a.com=고객사A,b.co.kr=고객사B' → {'a.com': '고객사A', 'b.co.kr': '고객사B'}"""
    pairs = {}
    for item in _list(name, default):
        key, sep, value = item.partition('=')
        if sep and key.strip() and value.strip():
            pairs[key.strip().lower()] = value.strip()
    return pairs


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gmail_credentials_file: Path
    gmail_token_file: Path
    # 사용자 본인 주소. 수신/참조 판정(참조 전용 메일 거르기)에 쓴다.
    my_addresses: tuple[str, ...]
    # 고객사 도메인 → 고객사 이름. (제조사 도메인 목록은 2단계 규칙 필터에서 정한다)
    customer_domains: dict[str, str]
    # 제목에 들어 있으면 규칙 필터에서 '참고'로 거르는 키워드
    exclude_subjects: tuple[str, ...]
    reference_docs_dir: Path
    reference_db: Path
    embedding_model: str
    openai_api_key: str
    # 분류기(LLM 임시 구현). Jev 사양을 받으면 바뀐다. API 키는 ANTHROPIC_API_KEY.
    classifier_model: str
    classifier_effort: str

    @classmethod
    def from_env(cls):
        data_dir = _path('REDA_DATA_DIR', '~/.reda')
        return cls(
            data_dir=data_dir,
            gmail_credentials_file=_path('REDA_GMAIL_CREDENTIALS_FILE', data_dir / 'credentials.json'),
            gmail_token_file=_path('REDA_GMAIL_TOKEN_FILE', data_dir / 'token.json'),
            my_addresses=tuple(a.lower() for a in _list('REDA_MY_ADDRESSES')),
            customer_domains=_mapping('REDA_CUSTOMER_DOMAINS'),
            exclude_subjects=_list('REDA_EXCLUDE_SUBJECTS'),
            reference_docs_dir=_path('REDA_REFERENCE_DOCS_DIR', data_dir / 'reference_docs'),
            reference_db=_path('REDA_REFERENCE_DB', data_dir / 'references.db'),
            embedding_model=_env('REDA_EMBEDDING_MODEL', 'text-embedding-3-small'),
            openai_api_key=_env('OPENAI_API_KEY'),
            classifier_model=_env('REDA_CLASSIFIER_MODEL', 'claude-opus-5-5'),
            classifier_effort=_env('REDA_CLASSIFIER_EFFORT', 'low'),
        )
