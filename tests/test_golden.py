"""골든 세트 도구 (내보내기, 정답 보존, 놓침·확인필요 비율 계산). 지어낸 메일만 쓴다."""
from fakes import FakeGmail, make_message

from reda import golden
from reda.classify import Classification
from reda.config import Settings

ME = 'me@corp.example.com'


class TableClassifier:
    name = 'test/table'

    def __init__(self, table):
        self.table = table

    def classify(self, mail):
        return Classification(self.table[mail.id], '보통', '시험')


def settings(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REDA_MY_ADDRESSES', ME)
    return Settings.from_env()


def mails():
    return {
        'c1': make_message('c1', From='lee@customer-a.example.com', To=ME, Subject='장애 문의'),
        'c2': make_message('c2', From='park@customer-b.example.com', To=ME, Subject='설정 문의'),
        'c3': make_message('c3', From='kim@customer-c.example.com', To=ME, Subject='견적 요청'),
        'n1': make_message('n1', From='noreply@vendor.example.com', To=ME, Subject='공지'),
    }


def test_export_writes_rows_and_keeps_existing_answers(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path)
    msgs = mails()
    gmail = FakeGmail(messages=msgs, query_messages=[{'id': i} for i in msgs])
    path = tmp_path / 'private' / 'golden.csv'

    assert golden.export(gmail, s, path) == (4, 4)
    rows = {r['message_id']: r for r in golden.read(path)}
    assert rows['n1']['규칙결과'].startswith('참고 (자동 발송 주소')
    assert rows['c1']['규칙결과'] == ''
    assert oct(path.stat().st_mode & 0o777) == '0o600'

    rows['c1']['정답'] = '답장필요'
    golden.write(path, rows.values())
    assert golden.export(gmail, s, path) == (4, 0)
    assert {r['message_id']: r for r in golden.read(path)}['c1']['정답'] == '답장필요'
    assert gmail.modified == []                     # Gmail을 바꾸지 않는다


def test_evaluate_counts_misses_and_check_needed(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path)
    msgs = mails()
    gmail = FakeGmail(messages=msgs)
    path = tmp_path / 'golden.csv'
    answers = {'c1': '답장필요', 'c2': 'r', 'c3': '답장필요', 'n1': 'f'}
    golden.write(path, [{'message_id': i, '정답': a, '날짜': '', '보낸사람': '', '제목': ''}
                        for i, a in answers.items()])
    classifier = TableClassifier({'c1': '답장필요', 'c2': '참고', 'c3': '확인필요'})

    report = golden.evaluate(gmail, s, classifier, path, result_path=tmp_path / 'result.csv')
    assert report.total == 4 and report.by_rule == 1            # n1은 규칙으로 끝남
    assert report.reply_total == 3
    assert [m['message_id'] for m in report.misses] == ['c2']   # 참고로 빠진 것만 놓침
    assert report.miss_rate == 1 / 3
    assert report.check_rate == 1 / 4
    text = golden.format_report(report)
    assert '놓침: 1/3' in text and '확인필요 비율: 25.0%' in text
    result = {r['message_id']: r for r in golden.read(tmp_path / 'result.csv')}
    assert result['c3']['예측'] == '확인필요' and result['c1']['출처'] == 'classifier:test/table'


def test_unanswered_rows_are_skipped(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path)
    path = tmp_path / 'golden.csv'
    golden.write(path, [{'message_id': 'c1', '정답': ''}, {'message_id': 'c2', '정답': '모름'}])
    report = golden.evaluate(FakeGmail(messages=mails()), s, TableClassifier({}), path)
    assert report.total == 0


def test_formula_like_cells_are_neutralized(tmp_path):
    path = tmp_path / 'golden.csv'
    golden.write(path, [{'message_id': 'm1', '제목': '=HYPERLINK("http://evil.example.com")',
                         '보낸사람': '@attacker', '정답': '참고'}])
    row = golden.read(path)[0]
    assert row['제목'].startswith("'=") and row['보낸사람'] == "'@attacker"
    assert row['message_id'] == 'm1' and row['정답'] == '참고'


def test_report_is_split_by_recipient_mode(monkeypatch, tmp_path):
    monkeypatch.setenv('REDA_MY_GROUPS', 'tac-team@corp.example.com')
    s = settings(monkeypatch, tmp_path)
    msgs = mails()
    msgs['g1'] = make_message('g1', From='kim@customer-d.example.com',
                              To='tac-team@corp.example.com', Subject='팀 문의', body='안녕하세요.')
    path = tmp_path / 'golden.csv'
    golden.write(path, [{'message_id': 'c1', '정답': 'r'}, {'message_id': 'g1', '정답': 'f'}])
    classifier = TableClassifier({'c1': '답장필요', 'g1': '답장필요'})
    report = golden.evaluate(FakeGmail(messages=msgs), s, classifier, path,
                             result_path=tmp_path / 'result.csv')
    # 그룹으로만 받은 요청은 담당자 단서가 없어 참고가 된다
    assert report.by_mode['개인 To'][('답장필요', '답장필요')] == 1
    assert report.by_mode['그룹 To'][('참고', '참고')] == 1
    text = golden.format_report(report)
    assert '- 개인 To: 1 / 1 / 0 / 0 / 0' in text and '- 그룹 To: 1 / 0 / 0 / 0 / 0' in text
    result = {r['message_id']: r for r in golden.read(tmp_path / 'result.csv')}
    assert result['g1']['수신방식'] == '그룹 To' and result['g1']['대상판정'] == 'none'


def test_evaluate_keeps_results_when_classifier_stops(monkeypatch, tmp_path):
    """리뷰 지적: 도중에 일시 장애가 나도 그때까지 분류한 결과를 버리지 않는다."""
    from reda.classify import ClassifierBusy

    class BusyAfterFirst(TableClassifier):
        def classify(self, mail):
            if mail.id != 'c1':
                raise ClassifierBusy('429')
            return super().classify(mail)

    s = settings(monkeypatch, tmp_path)
    path = tmp_path / 'golden.csv'
    golden.write(path, [{'message_id': 'c1', '정답': 'r'}, {'message_id': 'c2', '정답': 'r'},
                        {'message_id': 'c3', '정답': 'r'}])
    report = golden.evaluate(FakeGmail(messages=mails()), s, BusyAfterFirst({'c1': '답장필요'}),
                             path, result_path=tmp_path / 'result.csv')
    assert report.total == 1 and 'ClassifierBusy' in report.stopped
    assert [r['message_id'] for r in golden.read(tmp_path / 'result.csv')] == ['c1']
    assert '도중에 멈춤' in golden.format_report(report)


def test_evaluate_all_classifies_unanswered_rows(monkeypatch, tmp_path):
    s = settings(monkeypatch, tmp_path)
    path = tmp_path / 'golden.csv'
    golden.write(path, [{'message_id': 'c1', '정답': 'r'}, {'message_id': 'c2', '정답': ''},
                        {'message_id': 'n1', '정답': ''}])
    report = golden.evaluate(FakeGmail(messages=mails()), s,
                             TableClassifier({'c1': '답장필요', 'c2': '확인필요'}), path,
                             result_path=tmp_path / 'result.csv', include_unanswered=True)
    assert report.total == 1                                    # 정확도는 정답 있는 행만
    assert report.predicted[(False, '확인필요')] == 1 and report.predicted[(True, '참고')] == 1
    rows = {r['message_id']: r for r in golden.read(tmp_path / 'result.csv')}
    assert set(rows) == {'c1', 'c2', 'n1'} and rows['c2']['정답'] == ''
    assert float(rows['c1']['우선순위']) > 0 and rows['n1']['우선순위'] == '0.0'
    assert '분류한 메일 전체' in golden.format_report(report)
