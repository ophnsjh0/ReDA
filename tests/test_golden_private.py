"""실제 메일 골든 세트의 마지막 평가 결과로 2단계 분류 정확도 기준을 확인한다.

`tests/private/golden_result.csv`(git 제외, `reda golden eval`이 만든다)가 없거나, 정답이 달린 행이
하나도 없으면(`eval --all` 미리 보기만 한 경우) 건너뛴다. 정답이 있는데 100통이 안 되면 실패다.
"""
from pathlib import Path

import pytest

from reda import golden

RESULT = Path(__file__).parent / 'private' / 'golden_result.csv'

pytestmark = pytest.mark.skipif(not RESULT.exists(), reason='평가 세트 없음 (tests/private/)')


def test_golden_set_meets_stage2_criteria():
    rows = [r for r in golden.read(RESULT) if golden.normalize_answer(r.get('정답'))]
    if not rows:
        pytest.skip('평가 세트에 정답이 아직 없음 (tests/private/golden.csv의 정답 칸)')
    assert len(rows) >= 100, f'정답이 달린 메일이 {len(rows)}통 (기준 100통 이상)'
    replies = [r for r in rows if r['정답'] == '답장필요']
    misses = [r for r in replies if r['예측'] == '참고']
    checks = [r for r in rows if r['예측'] == '확인필요']
    assert replies, '정답이 답장필요인 메일이 없다'
    assert len(misses) / len(replies) <= 0.05, f'답장필요 놓침 {len(misses)}/{len(replies)}'
    assert len(checks) / len(rows) <= 0.20, f'확인필요 {len(checks)}/{len(rows)}'
