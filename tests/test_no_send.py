"""발송 차단 장치 검사.

ReDA는 메일 발송 기능을 만들지 않는다 (CLAUDE.md 설계 제약). gmail.modify 권한에는 발송 권한도
들어 있어서, 코드에 발송 호출이 없다는 사실이 발송을 막는 장치다. 이 테스트가 그 사실을 지킨다.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src'

# Gmail API의 발송 경로: users().messages().send(...), users().drafts().send(...)
FORBIDDEN_METHODS = {'send'}


def _python_files():
    files = sorted(SRC.rglob('*.py'))
    assert files, f'소스 파일을 찾지 못했습니다: {SRC}'
    return files


def test_no_send_calls_in_source():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in FORBIDDEN_METHODS):
                offenders.append(f'{path.relative_to(SRC)}:{node.lineno}')
    assert not offenders, f'발송 호출이 있습니다: {offenders}'


def test_no_send_scope_strings():
    """발송 전용 OAuth 범위(gmail.send)를 요청하지 않는다."""
    offenders = [str(p.relative_to(SRC)) for p in _python_files()
                 if 'auth/gmail.send' in p.read_text(encoding='utf-8')]
    assert not offenders, f'gmail.send 범위가 있습니다: {offenders}'
