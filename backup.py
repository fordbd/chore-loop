"""Online-safe SQLite backup streamed to stdout for host-side storage."""
import os
import sys
import tempfile
from pathlib import Path
from server import Store

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)/'backup.sqlite3'
    Store(os.environ.get('DB_PATH','/data/chores.sqlite3')).backup(path)
    with path.open('rb') as backup:
        while chunk := backup.read(65536):
            sys.stdout.buffer.write(chunk)
