"""Account milestones survive retirement, migration and unconfirmed readings."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from module.runtime import dock_data, fleet_tech as tech
from module.runtime.fleet_database import FleetDatabase, FleetDatabaseConflict, FleetTechnologyConflict


class AccountProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = FleetDatabase(Path(temporary.name) / 'pilot.json')

    def revision(self):
        return dock_data.read_snapshot(self.database.load())['revision']

    def record(self):
        return dock_data.technology_record(self.database.load())

    def patch_tech(self, changes):
        self.database.patch_technology(changes, {}, tech.revision(self.record()))

    def row(self, gid=10102):
        return tech.progress(self.record(), gid)

    def v3(self, cards, legacy=()):
        self.database.path.parent.mkdir()
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            connection.executescript(Path(__file__).with_name('fixtures').joinpath('fleet_schema_v3.sql').read_text())
            connection.execute("INSERT INTO fleet_info (id, revision, updated_at) VALUES (1, ?, 'before')", ('a' * 64,))
            for index, (gid, level) in enumerate(cards):
                connection.execute('INSERT INTO ships (position, group_type, name, level, rarity, base_rarity, '
                    'extra_exp, excluded, review, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (index, gid, 'migration copy', level, 'R', 'R', None if index % 2 else index,
                     index % 2, 0, f'original:{index}'))
            for values in legacy:
                connection.execute('INSERT INTO technology (group_type, max_observed_level, manual_fields, full_star) '
                                   'VALUES (?, ?, ?, ?)', values)
            connection.execute('PRAGMA user_version=3')
            return connection.execute('SELECT * FROM ships ORDER BY id').fetchall()

    def test_migration_retains_991_card_ids_fields_and_combines_duplicate_evidence(self):
        before = self.v3([(10102, 50), (10102, 120)] + [(10103, 100)] * 989)
        state = self.database.load()
        self.assertEqual(len(state['snapshot']['ships']), 991)
        with closing(sqlite3.connect(self.database.path)) as connection:
            after = connection.execute('SELECT id, position, group_type, name, level, rarity, base_rarity, '
                'extra_exp, excluded, review, source FROM ships ORDER BY id').fetchall()
            self.assertEqual(before, after)
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertTrue(self.row()['lv120'])
        self.assertTrue(self.row()['full_star'])
        self.assertEqual(self.row()['current_count'], 2)
        migrated_bytes = self.database.path.read_bytes()
        self.database.load()
        self.assertEqual(self.database.path.read_bytes(), migrated_bytes)

    def test_migration_failure_rolls_back_schema_cards_and_evidence(self):
        before = self.v3([(10102, 120)])
        with patch.object(self.database, '_set_progress', side_effect=RuntimeError('storage failed')):
            with self.assertRaisesRegex(RuntimeError, 'storage failed'):
                self.database.load()
        with closing(sqlite3.connect(self.database.path)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(connection.execute('SELECT * FROM ships ORDER BY id').fetchall(), before)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='technology_progress'").fetchone())
        self.assertTrue(self.row()['lv120'])

    def test_explicit_migrated_unknown_is_not_restored_by_incidental_edit(self):
        self.v3([(10102, 120)], [('10102', 120, 1 | (1 << 2), None)])
        self.assertIsNone(self.row()['full_star'])
        self.assertEqual(self.row()['conflicts']['full_star'], [1])
        self.database.patch_cards([dict(id=1, extra_exp=10)], self.revision())
        self.assertIsNone(self.row()['full_star'])
        self.database.patch_cards([dict(id=1, excluded=True)], self.revision())
        self.assertIsNone(self.row()['full_star'])

    def test_research_and_meta_levels_do_not_prove_full_stars(self):
        self.database.patch_cards([dict(group_type=29901, name='海王星', level=125),
                                   dict(group_type=970101, name='猎人·META', level=125),
                                   dict(group_type=10517, name='新泽西', level=125)], self.revision())
        for gid in (29901, 970101, 10517):
            self.assertIsNone(self.row(gid)['full_star'])
            self.assertTrue(self.row(gid)['lv120'])
            self.assertEqual(self.row(gid)['status'], 'unknown')
        cards = self.database.load()['snapshot']['ships']
        self.database.patch_cards([dict(id=row['id'], breakthrough=3) for row in cards], self.revision())
        self.assertTrue(self.row(29901)['full_star'])
        self.assertTrue(self.row(970101)['full_star'])
        self.assertTrue(self.row(10517)['full_star'])

    def test_explicit_breakthrough_overrides_level_inference_and_allows_correction(self):
        self.database.patch_cards([dict(group_type=10102, name='杜威', level=125)], self.revision())
        self.assertTrue(self.row()['full_star'])
        for stage in (0, 1, 2):
            with self.subTest(stage=stage):
                self.database.patch_cards([dict(id=1, breakthrough=stage)], self.revision())
                self.assertEqual(self.row()['evidence']['full_star'], [])
                # Current state does not erase history; correcting the separate
                # account milestone is explicit and no longer conflicts.
                self.patch_tech([dict(group_type=10102, full_star=False)])
                self.database.patch_cards([dict(id=1, extra_exp=stage)], self.revision())
                self.assertFalse(self.row()['full_star'])
        with self.assertRaises(FleetTechnologyConflict):
            self.database.patch_cards([dict(id=1, breakthrough=3)], self.revision())
        self.patch_tech([dict(group_type=10102, full_star=True)])
        self.database.patch_cards([dict(id=1, breakthrough=3)], self.revision())
        self.assertEqual(self.row()['evidence']['full_star'], [1])

    def test_retirement_preserves_progress_and_specific_proof_correction_stays_corrected(self):
        self.database.patch_cards([dict(group_type=10102, name='杜威', level=120)], self.revision())
        card_id = self.database.load()['snapshot']['ships'][0]['id']
        with self.assertRaises(FleetTechnologyConflict) as error:
            self.patch_tech([dict(group_type=10102, lv120=False)])
        self.assertEqual(error.exception.conflicts[0]['ship_ids'], [card_id])
        self.database.patch_cards([dict(id=card_id, delete=True)], self.revision())
        self.assertTrue(self.row()['lv120'])
        self.assertEqual(self.row()['current_count'], 0)
        self.assertEqual(self.row()['historical_evidence']['lv120'][0]['ship_id'], card_id)
        self.patch_tech([dict(group_type=10102, lv120=None)])
        self.database.patch_cards([dict(group_type=10103, name='卡辛', level=125)], self.revision())
        self.assertIsNone(self.row()['lv120'])

    def test_identity_correction_promotes_new_type_and_retains_old_history(self):
        self.database.patch_cards([dict(group_type=10102, name='杜威', level=120)], self.revision())
        self.database.patch_cards([dict(id=1, group_type=10103, name='卡辛')], self.revision())
        self.assertTrue(self.row(10103)['lv120'])
        self.assertTrue(self.row(10102)['lv120'])
        self.assertEqual(self.row(10102)['current_count'], 0)

    def test_unconfirmed_import_does_not_change_stats_or_overwrite_existing_pending(self):
        before = tech.report(self.record())
        self.database.stage_import([dict(name='杜威', level=120)], self.revision())
        staged = self.database.load()
        self.assertEqual(tech.report(self.record()), before)
        self.assertEqual(staged['pending_changes'][0]['milestones'], ['full_star', 'lv120', 'owned'])
        with self.assertRaises(FleetDatabaseConflict):
            self.database.stage_import([dict(name='卡辛', level=125)], self.revision())
        self.assertEqual(self.database.load(), staged)
        self.database.review([dict(id=row['id'], accept=False) for row in staged['pending_changes']],
                             self.revision(), staged['pending_revision'])
        self.assertEqual(tech.report(self.record()), before)

    def test_count_mismatch_and_partial_read_cannot_propose_removals(self):
        self.database.patch_cards([dict(group_type=10102, name='杜威', level=100),
                                   dict(group_type=10103, name='卡辛', level=120)], self.revision())
        for proof in (dict(physical_count=2, expected_count=2),
                      dict(physical_count=1, expected_count=1, min_level=100)):
            metadata = dict(min_level=1, max_level=125, reached_bottom=True, filters_clear=True,
                            physical_count=1, expected_count=1, updated_at='proof')
            metadata.update(proof)
            snapshot = dock_data.build_snapshot([dict(group_type=10102, name='杜威', level=101)], **metadata)
            self.database.save_scan(snapshot, self.revision())
            state = self.database.load()
            self.assertFalse(any(row['operation'] == 'remove' for row in state['pending_changes']))
            self.assertFalse(state['pending_read']['complete'])
            self.database.review([dict(id=row['id'], accept=False) for row in state['pending_changes']],
                                 self.revision(), state['pending_revision'])

    def test_explicit_unknown_and_zero_experience_remain_distinct_during_import(self):
        self.database.patch_cards([dict(group_type=10102, name='杜威', level=100, extra_exp=0)], self.revision())
        self.database.stage_import([dict(group_type=10102, name='杜威', level=101)], self.revision())
        state = self.database.load()
        self.database.review([dict(id=row['id'], accept=True) for row in state['pending_changes']],
                             self.revision(), state['pending_revision'])
        self.assertEqual(self.database.load()['snapshot']['ships'][0]['extra_exp'], 0)
        self.database.patch_cards([dict(id=1, extra_exp=None)], self.revision())
        self.assertIsNone(self.database.load()['snapshot']['ships'][0]['extra_exp'])


if __name__ == '__main__':
    unittest.main()
