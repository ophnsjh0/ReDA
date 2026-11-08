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


class ConfigError(ValueError):
    """설정 값이 잘못됨. 메시지에 고칠 항목 이름을 담는다."""


def _addresses(name):
    values = tuple(a.lower() for a in _list(name))
    bad = [a for a in values if a.count('@') != 1 or a.startswith('@') or a.endswith('@')]
    if bad:
        raise ConfigError(f'{name}에 메일 주소가 아닌 값이 {len(bad)}개 있음 '
                          f'(예: "{bad[0][:3]}…"). user@domain 형식으로 적는다')
    return values


def _float(name, default):
    try:
        return float(_env(name, str(default)))
    except ValueError:
        raise ConfigError(f'{name}는 숫자여야 함') from None


def _int(name, default):
    try:
        value = int(_env(name, str(default)))
    except ValueError:
        raise ConfigError(f'{name}는 정수여야 함') from None
    if value < 0:
        raise ConfigError(f'{name}는 0 이상이어야 함')
    return value


def _probability(name, default):
    value = _float(name, default)
    if not 0.0 <= value <= 1.0:          # nan도 여기서 걸린다
        raise ConfigError(f'{name}는 0과 1 사이 값이어야 함 (예: 0.7)')
    return value


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
    # 분류기. 제공자(anthropic / jev)와 모델은 설정으로만 바꾼다 (reda/classify/__init__.py 표)
    classifier: str
    classifier_model: str          # 비우면 제공자 기본 모델
    classifier_effort: str         # anthropic만. 비우면 보내지 않는다
    jev_min_probability: float     # jev만. 판단(noul)을 예로 볼 기준. 아니오 기준은 1 - 이 값
    # 내가 속한 그룹(팀 메일) 주소. 그룹 주소로만 받은 요청은 담당자 단서가 있어야 답장필요다 (결정 2-5).
    # 헤더(List-Id, 그룹이 바꾼 From)로 알아낼 수 있는 그룹은 적지 않아도 된다
    my_groups: tuple[str, ...] = ()
    # 이름 프로필 (결정 2-6). 개발용 설정이다. 비우면 표시 이름은 Gmail 보내는 이름(sendAs)에서 가져온다.
    # 배포 때는 메일함에서 자동으로 채우고 메신저로 확인받는다 (결정 6-3)
    my_name: str = ''
    my_titles: tuple[str, ...] = ()      # 직함 (예: 부장). '이름 + 직함' 호칭 판정에 쓴다
    my_aliases: tuple[str, ...] = ()     # 그 밖에 불리는 이름 (영문 이름 등)
    # 내가 직접 답하는 그룹. 이 그룹으로 온 요청은 담당 단서가 없어도 확인필요 (없으면 참고)
    my_owned_groups: tuple[str, ...] = ()
    # 자동 초안 (결정 3-2). auto_draft면 `reda poll` 끝에 `reda draft-pending`을 이어서 돈다 (개발용 Mac).
    # VPS에서는 Hermes cron이 두 명령을 따로 부른다
    auto_draft: bool = False
    draft_profile: str = 'reda-dev'       # 초안을 쓸 Hermes 프로필
    draft_max_per_run: int = 2            # 한 번에 맡기는 메일 수 (15분 실행 안에 끝나게)
    draft_timeout: int = 300              # 메일 한 통에 기다리는 초
    hermes_bin: str = ''                  # 비우면 PATH와 ~/.local/bin에서 찾는다

    @classmethod
    def from_env(cls):
        data_dir = _path('REDA_DATA_DIR', '~/.reda')
        return cls(
            data_dir=data_dir,
            gmail_credentials_file=_path('REDA_GMAIL_CREDENTIALS_FILE', data_dir / 'credentials.json'),
            gmail_token_file=_path('REDA_GMAIL_TOKEN_FILE', data_dir / 'token.json'),
            my_addresses=_addresses('REDA_MY_ADDRESSES'),
            customer_domains=_mapping('REDA_CUSTOMER_DOMAINS'),
            exclude_subjects=_list('REDA_EXCLUDE_SUBJECTS'),
            reference_docs_dir=_path('REDA_REFERENCE_DOCS_DIR', data_dir / 'reference_docs'),
            reference_db=_path('REDA_REFERENCE_DB', data_dir / 'references.db'),
            embedding_model=_env('REDA_EMBEDDING_MODEL', 'text-embedding-3-small'),
            openai_api_key=_env('OPENAI_API_KEY'),
            classifier=_env('REDA_CLASSIFIER', 'anthropic').lower(),
            classifier_model=_env('REDA_CLASSIFIER_MODEL'),
            classifier_effort=os.environ.get('REDA_CLASSIFIER_EFFORT', 'low').strip(),
            jev_min_probability=_probability('REDA_JEV_MIN_PROBABILITY', 0.7),
            my_groups=_addresses('REDA_MY_GROUPS'),
            my_name=_env('REDA_MY_NAME'),
            my_titles=_list('REDA_MY_TITLES'),
            my_aliases=_list('REDA_MY_ALIASES'),
            my_owned_groups=_addresses('REDA_MY_OWNED_GROUPS'),
            auto_draft=_env('REDA_AUTO_DRAFT', 'false').lower() in ('1', 'true', 'yes', 'on'),
            draft_profile=_env('REDA_DRAFT_PROFILE', 'reda-dev'),
            draft_max_per_run=_int('REDA_DRAFT_MAX_PER_RUN', 2),
            draft_timeout=_int('REDA_DRAFT_TIMEOUT', 300),
            hermes_bin=_env('REDA_HERMES_BIN'),
        )
