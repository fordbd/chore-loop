"""Ordered, transactional SQLite schema migrations run before serving requests.

Migration functions must use execute/executemany, never executescript or commit:
those can escape the runner's encompassing transaction.
"""
from contextlib import closing
from datetime import datetime, timezone
import logging
import json
from pathlib import Path
import sqlite3
import uuid

log = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    pass


def schema_v1(db):
    db.execute('CREATE TABLE planner (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, state TEXT NOT NULL)')


def schema_v2(db):
    """Introduce per-occurrence chore durations without changing existing estimates."""
    for row_id, raw in db.execute('SELECT id,state FROM planner').fetchall():
        state = json.loads(raw)
        if not isinstance(state, dict) or not isinstance(state.get('chores', []), list):
            raise MigrationError('Saved chart has invalid chore data.')
        changed = False
        for chore in state.get('chores', []):
            if not isinstance(chore, dict):
                raise MigrationError('Saved chart has invalid chore data.')
            if 'minutes' not in chore:
                chore['minutes'] = 10
                changed = True
            if type(chore['minutes']) is not int or not 1 <= chore['minutes'] <= 480:
                raise MigrationError('Saved chart has an invalid chore duration.')
        if changed:
            db.execute('UPDATE planner SET state=?, revision=revision+1 WHERE id=?', (json.dumps(state,separators=(',', ':')),row_id))


MIGRATIONS = {1: schema_v1, 2: schema_v2}
CURRENT_SCHEMA_VERSION = max(MIGRATIONS)


def schema_version(db):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
    if not exists:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        if tables:
            raise MigrationError('Existing database has no schema version; startup stopped without changing it.')
        return 0
    rows = db.execute('SELECT version FROM schema_version ORDER BY version').fetchall()
    versions = [row[0] for row in rows]
    if not versions or any(type(v) is not int or v < 1 for v in versions) or versions != list(range(1,versions[-1]+1)):
        raise MigrationError('Invalid schema version history; startup stopped without changing the database.')
    return versions[-1]


def migration_backup(path, before, after):
    """Capture committed data while the migration connection holds the writer lock."""
    path = Path(path)
    directory = path.parent/'migration-backups'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = directory/f'{path.stem}-v{before}-to-v{after}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3'
    partial = destination.with_suffix('.partial')
    try:
        partial.touch(mode=0o600, exist_ok=False)
        with closing(sqlite3.connect(path, timeout=30)) as source, closing(sqlite3.connect(partial)) as target:
            source.backup(target)
            if target.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise MigrationError('Pre-migration backup failed its integrity check.')
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    log.warning('Pre-migration backup saved: %s', destination)
    return destination


def migrate(path, migrations=None):
    steps = MIGRATIONS if migrations is None else migrations
    if not steps or sorted(steps) != list(range(1,max(steps)+1)):
        raise MigrationError('Migration definitions must be consecutive, starting at version 1.')
    target = max(steps)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db:
        # This serializes startup and excludes other writers until backup and upgrade finish.
        db.execute('BEGIN IMMEDIATE')
        try:
            before = schema_version(db)
            if before > target:
                raise MigrationError(f'Database schema v{before} is newer than supported v{target}. Use a compatible app image or restore a matching backup.')
            if before == target:
                db.rollback()
                return before
            if before:
                migration_backup(path, before, target)
            db.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)')
            for version in range(before+1,target+1):
                steps[version](db)
                if not db.in_transaction:
                    raise MigrationError('A migration ended the transaction unexpectedly.')
                db.execute('INSERT INTO schema_version(version) VALUES (?)',(version,))
            db.commit()
            log.warning('Database schema upgraded from v%s to v%s', before, target)
            return target
        except Exception:
            db.rollback()
            raise
