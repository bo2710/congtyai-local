"""Make a consistent SQLite backup for a Windows upgrade or recovery."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

def copy_database(source, target):
    source=Path(source).resolve()
    target=Path(target).resolve()
    if not source.is_file():raise ValueError(f'Không tìm thấy dữ liệu: {source}')
    if target.exists() or target==source:raise ValueError('Chọn tệp đích mới.')
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+'.incomplete')
    if temp.exists():raise ValueError(f'Đã có bản chưa hoàn tất: {temp}')
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original, closing(sqlite3.connect(temp)) as backup:
            original.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Bản sao không vượt kiểm tra toàn vẹn SQLite.')
        temp.rename(target)
    finally:
        if temp.exists():temp.unlink()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('target')
    args=parser.parse_args()
    copy_database(args.source,args.target)
    print('Bản sao SQLite đã kiểm toàn vẹn:',Path(args.target).resolve())
