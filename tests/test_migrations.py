from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from migrations import MIGRATIONS, MigrationError, migrate
from server import Store


def add_updated_at(db):
    db.execute('ALTER TABLE planner ADD COLUMN updated_at TEXT')
    db.execute("UPDATE planner SET updated_at='migrated'")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'chores.sqlite3'
        self.steps={**MIGRATIONS,2:add_updated_at}

    def seed_legacy(self):
        # Exact schema and marker written by the previously deployed app.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE schema_version (version INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO schema_version VALUES(1)')
            db.execute('CREATE TABLE planner (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, state TEXT NOT NULL)')
            db.execute('INSERT INTO planner VALUES (1,7,?)',('{"checks":{"finished":true}}',))

    def rows(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchall()

    def backups(self):
        return list(self.path.parent.glob('migration-backups/*.sqlite3'))

    def test_fresh_database_initializes_and_restart_is_noop(self):
        self.assertEqual(migrate(self.path),1)
        self.assertEqual(migrate(self.path),1)
        self.assertEqual(self.rows('SELECT version FROM schema_version'),[(1,)])
        self.assertEqual(self.rows('SELECT * FROM planner'),[])
        self.assertEqual(self.backups(),[])

    def test_legacy_current_version_preserves_chart_without_backup(self):
        self.seed_legacy()
        before=self.rows('SELECT * FROM planner')
        self.assertEqual(migrate(self.path),1)
        self.assertEqual(self.rows('SELECT * FROM planner'),before)
        self.assertEqual(self.backups(),[])

    def test_upgrade_preserves_data_and_backs_up_original_schema(self):
        self.seed_legacy()
        self.assertEqual(migrate(self.path,self.steps),2)
        self.assertEqual(self.rows('SELECT revision,state,updated_at FROM planner'),[(7,'{"checks":{"finished":true}}','migrated')])
        self.assertEqual(self.rows('SELECT version FROM schema_version ORDER BY version'),[(1,),(2,)])
        backup=self.backups();self.assertEqual(len(backup),1)
        with closing(sqlite3.connect(backup[0])) as db:
            self.assertEqual(db.execute('SELECT * FROM planner').fetchall(),[(1,7,'{"checks":{"finished":true}}')])
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchall(),[(1,)])
        self.assertEqual(migrate(self.path,self.steps),2)
        self.assertEqual(len(self.backups()),1)

    def test_failed_multi_step_upgrade_rolls_back_all_changes(self):
        self.seed_legacy()
        def failing(db):
            db.execute('CREATE TABLE temporary_upgrade (value TEXT)')
            db.execute("UPDATE planner SET state='changed'")
            raise RuntimeError('Simulated migration failure')
        with self.assertRaisesRegex(RuntimeError,'Simulated'):
            migrate(self.path,{**self.steps,3:failing})
        self.assertEqual(self.rows('SELECT version FROM schema_version'),[(1,)])
        self.assertEqual(self.rows('SELECT * FROM planner'),[(1,7,'{"checks":{"finished":true}}')])
        self.assertNotIn('updated_at',[r[1] for r in self.rows('PRAGMA table_info(planner)')])
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name='temporary_upgrade'"),[])
        self.assertEqual(len(self.backups()),1)
        self.assertEqual(migrate(self.path,self.steps),2)

    def test_backup_failure_aborts_before_schema_changes(self):
        self.seed_legacy()
        with patch('migrations.migration_backup',side_effect=OSError('No space')):
            with self.assertRaisesRegex(OSError,'No space'):
                migrate(self.path,self.steps)
        self.assertEqual(self.rows('SELECT version FROM schema_version'),[(1,)])
        self.assertEqual(len(self.rows('PRAGMA table_info(planner)')),3)

    def test_newer_database_is_rejected_without_downgrading(self):
        self.seed_legacy();migrate(self.path,self.steps)
        before=self.rows('SELECT * FROM planner')
        with self.assertRaisesRegex(MigrationError,'newer than supported'):
            Store(self.path)
        self.assertEqual(self.rows('SELECT * FROM planner'),before)
        self.assertEqual(len(self.backups()),1)

    def test_concurrent_startup_applies_upgrade_once(self):
        self.seed_legacy()
        with ThreadPoolExecutor(max_workers=2) as pool:
            versions=list(pool.map(lambda _:migrate(self.path,self.steps),range(2)))
        self.assertEqual(versions,[2,2])
        self.assertEqual(len(self.backups()),1)

    def test_unversioned_existing_database_is_not_modified(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE unknown_data (value TEXT)')
        with self.assertRaisesRegex(MigrationError,'no schema version'):
            migrate(self.path)
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name='schema_version'"),[])

    def test_backup_includes_committed_wal_data(self):
        self.seed_legacy()
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("UPDATE planner SET revision=8,state='wal-committed'")
            writer.commit()
            migrate(self.path,self.steps)
        with closing(sqlite3.connect(self.backups()[0])) as db:
            self.assertEqual(db.execute('SELECT revision,state FROM planner').fetchone(),(8,'wal-committed'))


if __name__=='__main__':
    unittest.main()
