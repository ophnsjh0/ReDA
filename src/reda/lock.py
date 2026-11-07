"""동시 실행 방지. cron이 15분마다 부르는데 앞 실행이 끝나지 않았으면 새 실행은 바로 끝낸다."""
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(Exception):
    pass


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(str(path)) from None
        yield
    finally:
        os.close(fd)  # 닫으면 잠금도 풀린다
