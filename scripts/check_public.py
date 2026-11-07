#!/usr/bin/env python3
"""공개 저장소 위생 검사: 커밋하려는 내용에 운영 정보나 비밀이 있으면 막는다.

이 저장소는 공개(public)다. 서버 주소·계정 이름 같은 운영 값은 git에 올라가지 않는
`.claude/sensitive-patterns.local`(한 줄에 정규식 하나)에서 읽는다. 이 스크립트에는
운영 값을 적지 않는다.

사용:
  --hook        Claude Code PreToolUse hook. stdin의 JSON에서 Bash 명령을 읽고,
                `git commit`이 들어 있을 때만 검사한다. 막을 때는 종료 코드 2.
  --pre-commit  git pre-commit hook. staged 변경만 검사한다. 막을 때는 종료 코드 1.
  (인자 없음)   staged·수정·새 파일을 모두 검사하고 결과를 출력한다.

발견한 값 자체는 출력하지 않고 "파일:줄 — 종류"만 출력한다.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN_FILE = ROOT / '.claude' / 'sensitive-patterns.local'

# 커밋하면 안 되는 파일
BLOCKED_PATHS = [
    (re.compile(r'(^|/)\.env$'), '.env 파일'),
    (re.compile(r'(^|/)token\.json$'), 'OAuth 토큰 파일'),
    (re.compile(r'(^|/)credentials\.json$'), 'OAuth 클라이언트 파일'),
    (re.compile(r'\.local\.md$'), '운영 메모(*.local.md)'),
    (re.compile(r'^진행내역/'), '진행내역'),
    (re.compile(r'^tests/private/'), '실제 메일 평가 세트'),
    (re.compile(r'sensitive-patterns\.local$'), '운영 값 목록'),
    (re.compile(r'(^|/)settings\.local\.json$'), '개인 Claude 설정'),
    (re.compile(r'\.(pem|key|p12)$'), '키 파일'),
]

# 형태로 알아볼 수 있는 비밀
SECRET_PATTERNS = [
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), '개인 키'),
    (re.compile(r'sk-ant-[A-Za-z0-9_-]{10,}'), 'Anthropic API 키'),
    (re.compile(r'\bsk-[A-Za-z0-9]{20,}'), 'API 키(sk-)'),
    (re.compile(r'GOCSPX-[A-Za-z0-9_-]{10,}'), 'Google OAuth client secret'),
    (re.compile(r'ya29\.[A-Za-z0-9_-]{20,}'), 'Google access token'),
    (re.compile(r'1//[0-9A-Za-z_-]{30,}'), 'Google refresh token'),
    (re.compile(r'"(refresh_token|client_secret|access_token)"\s*:\s*"[^"]{8,}'), 'JSON 비밀 필드'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), 'AWS 키'),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'), 'GitHub 토큰'),
    (re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}'), 'Slack 토큰'),
]

# 문서용 대역(RFC 5737)과 루프백 등 공개해도 되는 IPv4
RE_IPV4 = re.compile(r'(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])')
ALLOWED_IPV4_PREFIXES = ('192.0.2.', '198.51.100.', '203.0.113.', '127.', '0.0.0.0',
                         '255.255.255.')

RE_GIT_COMMIT = re.compile(r'\bgit\b[^|;&\n]*\bcommit\b')


def load_local_patterns():
    if not PATTERN_FILE.exists():
        return []
    patterns = []
    for raw in PATTERN_FILE.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            patterns.append((re.compile(line), '운영 정보'))
        except re.error:
            print(f'[check_public] 잘못된 정규식을 건너뜀: {PATTERN_FILE.name}', file=sys.stderr)
    return patterns


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True,
                          check=False).stdout


def added_lines_from_diff(diff_text):
    """unified diff에서 (파일, 줄 번호, 추가된 줄)을 뽑는다."""
    path, line_no = None, 0
    for line in diff_text.splitlines():
        if line.startswith('+++ '):
            target = line[4:]
            path = None if target == '/dev/null' else target[2:] if target.startswith('b/') else target
        elif line.startswith('@@'):
            match = re.search(r'\+(\d+)', line)
            line_no = int(match.group(1)) if match else 0
        elif path and line.startswith('+') and not line.startswith('+++'):
            yield path, line_no, line[1:]
            line_no += 1
        elif path and not line.startswith('-'):
            line_no += 1


def untracked_lines():
    for name in git('ls-files', '--others', '--exclude-standard', '-z').split('\0'):
        if not name:
            continue
        file = ROOT / name
        try:
            if file.stat().st_size > 2_000_000:
                continue
            text = file.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            yield name, number, line


def changed_paths(staged_only):
    names = set(git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').split('\0'))
    if not staged_only:
        names |= set(git('diff', '--name-only', '--diff-filter=ACMR', '-z').split('\0'))
        names |= set(git('ls-files', '--others', '--exclude-standard', '-z').split('\0'))
    names.discard('')
    return sorted(names)


def scan(staged_only):
    findings = []
    for name in changed_paths(staged_only):
        for pattern, label in BLOCKED_PATHS:
            if pattern.search(name):
                findings.append(f'{name} — 커밋하면 안 되는 파일({label})')

    patterns = SECRET_PATTERNS + load_local_patterns()
    sources = [added_lines_from_diff(git('diff', '--cached', '-U0', '--no-color'))]
    if not staged_only:
        sources.append(added_lines_from_diff(git('diff', '-U0', '--no-color')))
        sources.append(untracked_lines())

    seen = set()
    for source in sources:
        for path, number, text in source:
            labels = [label for pattern, label in patterns if pattern.search(text)]
            labels += ['실제 IPv4 주소' for ip in RE_IPV4.findall(text)
                       if _is_real_ipv4(ip)][:1]
            for label in labels:
                key = (path, number, label)
                if key not in seen:
                    seen.add(key)
                    findings.append(f'{path}:{number} — {label}')
    return findings


def _is_real_ipv4(ip):
    parts = ip.split('.')
    if any(int(p) > 255 for p in parts):
        return False  # 버전 번호 등
    return not ip.startswith(ALLOWED_IPV4_PREFIXES)


def report(findings):
    lines = ['공개 저장소 위생 검사에서 커밋을 막았습니다 (값은 표시하지 않음):']
    lines += [f'  - {f}' for f in findings[:30]]
    if len(findings) > 30:
        lines.append(f'  … 외 {len(findings) - 30}건')
    lines.append('운영 정보는 *.local.md로 옮기고, 테스트 IP는 문서용 대역(192.0.2.x 등)을 쓰세요.')
    return '\n'.join(lines)


def main(argv):
    if '--hook' in argv:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError:
            return 0
        command = (payload.get('tool_input') or {}).get('command', '')
        if not RE_GIT_COMMIT.search(command):
            return 0
        # `git add ... && git commit`처럼 한 명령에 섞여 오면 hook 시점에는 아직 staged가
        # 아니므로, 수정·새 파일까지 넓게 검사한다.
        findings = scan(staged_only=False)
        if findings:
            print(report(findings), file=sys.stderr)
            return 2
        return 0
    if '--pre-commit' in argv:
        findings = scan(staged_only=True)
        if findings:
            print(report(findings), file=sys.stderr)
            return 1
        return 0
    findings = scan(staged_only=False)
    print(report(findings) if findings else '문제 없음')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
