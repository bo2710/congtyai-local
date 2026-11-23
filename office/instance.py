"""Prevent two workers from recovering the same database through different ports."""
import os
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def one_worker(directory):
    path=Path(directory)
    path.mkdir(parents=True,exist_ok=True)
    handle=open(path/'.worker.lock','a+b')
    try:
        if os.name=='nt':
            import msvcrt
            if not handle.tell():
                handle.write(b'0');handle.flush()
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise RuntimeError('Đã có Văn phòng AI đang dùng thư mục dữ liệu này.') from exc
        else:
            import fcntl
            try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc:raise RuntimeError('Đã có Văn phòng AI đang dùng thư mục dữ liệu này.') from exc
        yield
    finally:
        handle.close()
