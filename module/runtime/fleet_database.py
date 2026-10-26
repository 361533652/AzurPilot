"""玩家编辑的舰船数据；设备读取仅产生待确认更新。"""
import copy
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from module.runtime import fleet_tech as tech
from module.runtime import fleet_ledger as ledger


class FleetDatabaseConflict(ValueError):
    """The canonical ledger or pending reading changed during an edit."""


class FleetDatabaseMigrationRequired(ValueError):
    """An older database needs one transactional upgrade."""


class FleetTechnologyConflict(FleetDatabaseConflict):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        ids = sorted({ship_id for item in conflicts for ship_id in item['ship_ids']})
        super().__init__('科技纠错与当前舰船事实冲突，请先核对关联舰船：' + ', '.join(map(str, ids)))


class FleetDatabaseAlreadyImported(ValueError):
    """The optional local legacy reading was already staged."""


class FleetDatabase:
    SCHEMA_VERSION = 8
    BUSY_TIMEOUT_MS = 10000

    def __init__(self, config_path):
        config_path = Path(config_path)
        self.path = config_path.parent / 'fleet' / (config_path.stem + '.sqlite3')

    @staticmethod
    def _check_file(path, *, directory=False):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or path.is_junction():
            raise ValueError(f'Unsafe fleet database path: {path}')
        return True

    def _check_paths(self):
        if not self._check_file(self.path.parent, directory=True):
            return False
        exists = self._check_file(self.path)
        # SQLite opens these itself; refuse existing links before opening SQLite.
        for suffix in ('-journal', '-wal', '-shm'):
            self._check_file(Path(str(self.path) + suffix))
        return exists

    def _connect(self, *, write, create=True):
        exists = self._check_paths()
        if not exists and (not write or not create):
            return None
        if write and create:
            self.path.parent.mkdir(mode=0o700, exist_ok=True)
            if not self._check_paths():
                self._initialize_file()
            self._check_paths()
        uri = self.path.absolute().as_uri() + ('?mode=rw' if write else '?mode=ro')
        connection = sqlite3.connect(uri, uri=True, timeout=self.BUSY_TIMEOUT_MS / 1000,
                                     isolation_level=None)
        try:
            connection.execute(f'PRAGMA busy_timeout={self.BUSY_TIMEOUT_MS}')
            connection.execute('PRAGMA foreign_keys=ON')
            if not write:
                connection.execute('PRAGMA query_only=ON')
        except BaseException:
            connection.close()
            raise
        return connection

    def _initialize_file(self):
        # Publish an initialized database atomically. A report racing the first
        # writer sees either missing storage or a valid empty schema, never a
        # zero-byte SQLite file or a partially created schema.
        descriptor, filename = tempfile.mkstemp(prefix=f'.{self.path.name}.', dir=self.path.parent)
        os.close(descriptor)
        temporary = Path(filename)
        try:
            connection = sqlite3.connect(temporary, isolation_level=None)
            try:
                connection.execute('BEGIN IMMEDIATE')
                self._schema(connection, create=True)
                connection.commit()
            finally:
                connection.close()
            try:
                os.link(temporary, self.path, follow_symlinks=False)
            except FileExistsError:
                # Another process initialized the same instance first.
                self._check_paths()
        finally:
            temporary.unlink()

    def _schema(self, connection, *, create=False):
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == self.SCHEMA_VERSION:
            return
        if version in (3, 4, 5, 6, 7):
            if connection.execute('PRAGMA query_only').fetchone()[0]:
                raise FleetDatabaseMigrationRequired()
            if version == 3:
                self._migrate_v3(connection)
            elif version == 4:
                self._migrate_v4(connection)
            # v3 initializes the new table above; v4/v5 already saved account
            # progress separately. Never reseed an existing independent table.
            if version < 6:
                connection.execute('ALTER TABLE fleet_info ADD COLUMN technology_initialized INTEGER '
                                   'NOT NULL DEFAULT 1 CHECK(technology_initialized IN (0, 1))')
            if version < 6 and self._info(connection)['pending_source'] == 'scan' and connection.execute(
                    'SELECT 1 FROM pending_technology LIMIT 1').fetchone():
                # Earlier dock readings also proposed technology inferred from
                # card levels. Retire those proposals without changing progress.
                connection.execute('DELETE FROM pending_technology')
                self._prune_pending(connection)
            if version < 7:
                self._migrate_v6(connection)
            connection.execute('ALTER TABLE technology_progress ADD COLUMN awakening_pending INTEGER '
                               'NOT NULL DEFAULT 0 CHECK(awakening_pending IN (0, 1))')
            connection.execute(f'PRAGMA user_version={self.SCHEMA_VERSION}')
            return
        if version != 0 or not create:
            raise ValueError(f'Unsupported fleet database schema version: {version}; '
                             '请先备份旧试验版舰船数据库，再重新扫描或显式导入旧配置以建立舰船数据')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise ValueError('Unversioned fleet database contains existing tables')
        # Execute statements individually: executescript would commit the caller's transaction.
        for statement in Path(__file__).with_name('fleet_schema.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('INSERT INTO fleet_info (id) VALUES (1)')
        connection.execute(f'PRAGMA user_version={self.SCHEMA_VERSION}')

    def _migrate_v3(self, connection):
        for field, check in (('reading_count', 'reading_count >= 0'),
                             ('reading_review_count', 'reading_review_count >= 0'),
                             ('reading_complete', 'reading_complete IN (0, 1)')):
            connection.execute(f'ALTER TABLE fleet_info ADD COLUMN {field} INTEGER CHECK({check})')
        # Keep every original card ID and all legacy technology evidence intact.
        for table in ('ships', 'pending_ships'):
            connection.execute(f'ALTER TABLE {table} ADD COLUMN full_star INTEGER CHECK(full_star IN (0, 1))')
            connection.execute(f"ALTER TABLE {table} ADD COLUMN form TEXT CHECK(form IN ('base', 'retrofit'))")
        connection.execute("CREATE TABLE technology_progress (group_type TEXT PRIMARY KEY NOT NULL, "
                           "owned INTEGER CHECK(owned IN (0,1)), full_star INTEGER CHECK(full_star IN (0,1)), "
                           "lv120 INTEGER CHECK(lv120 IN (0,1)), source TEXT NOT NULL, updated_at TEXT NOT NULL) STRICT")
        connection.execute("CREATE TABLE technology_evidence (group_type TEXT NOT NULL, "
                           "milestone TEXT NOT NULL CHECK(milestone IN ('owned','full_star','lv120')), "
                           "ship_id INTEGER NOT NULL, level INTEGER NOT NULL, full_star INTEGER CHECK(full_star IN (0,1)), "
                           "source TEXT NOT NULL, confirmed_at TEXT NOT NULL, "
                           "PRIMARY KEY(group_type,milestone,ship_id)) STRICT")
        self._fill_star_states(connection)
        legacy = {row['group_type']: row for row in self._rows(connection, 'SELECT * FROM technology')}
        merged = {gid: tech.legacy_milestones(gid, row['max_observed_level'], self._manual(row))
                  for gid, row in legacy.items()}
        for card in self._cards(connection):
            gid = str(card['group_type'])
            manual = self._manual(legacy[gid]) if gid in legacy else {}
            facts = tech.positive_facts(card)
            if not facts:
                continue
            values = merged.setdefault(gid, {key: False for key in tech.MILESTONES})
            for key in facts:
                if key not in manual and manual.get('owned') is not False:
                    values[key] = True
                self._write_evidence(connection, card, key, 'migration: current ship')
        # Legacy unknown corrections become incomplete; positive proof survives.
        for gid, values in merged.items():
            self._set_progress(connection, gid, values, source='migration: legacy and current evidence')

    def _fill_star_states(self, connection):
        for table in ('ships', 'pending_ships'):
            for row in self._rows(connection, f'SELECT * FROM {table}'):
                connection.execute(f'UPDATE {table} SET full_star=? WHERE id=?',
                                   (tech.full_star_state(row), row['id']))

    def _migrate_v4(self, connection):
        # Keep the field's bit position in pending_ships unchanged. Only its value
        # changes from an exact stage to nullable boolean; IDs and targets survive.
        for table in ('ships', 'pending_ships', 'technology_evidence'):
            connection.execute(f'ALTER TABLE {table} ADD COLUMN full_star INTEGER CHECK(full_star IN (0, 1))')
            connection.execute(f'UPDATE {table} SET full_star=CASE WHEN breakthrough IS NULL THEN NULL '
                               'WHEN breakthrough=3 THEN 1 ELSE 0 END')
            connection.execute(f'ALTER TABLE {table} DROP COLUMN breakthrough')
        self._fill_star_states(connection)
        info = self._info(connection)
        if info['revision'] is not None:
            self._touch(connection)
        if info['pending_revision'] is not None:
            connection.execute('UPDATE fleet_info SET pending_revision=? WHERE id=1', (tech.revision(uuid4().hex),))

    def _migrate_v6(self, connection):
        # Rebuild only account milestones: physical-card uncertainty and all
        # historical evidence keep their original values and identities.
        connection.execute('CREATE TABLE technology_progress_v7 ('
                           'group_type TEXT PRIMARY KEY NOT NULL, '
                           'owned INTEGER NOT NULL DEFAULT 0 CHECK(owned IN (0, 1)), '
                           'full_star INTEGER NOT NULL DEFAULT 0 CHECK(full_star IN (0, 1)), '
                           'lv120 INTEGER NOT NULL DEFAULT 0 CHECK(lv120 IN (0, 1)), '
                           'source TEXT NOT NULL, updated_at TEXT NOT NULL) STRICT')
        connection.execute('INSERT INTO technology_progress_v7 '
                           'SELECT group_type, CASE WHEN owned=1 OR full_star=1 OR lv120=1 THEN 1 ELSE 0 END, '
                           'COALESCE(full_star, 0), COALESCE(lv120, 0), source, updated_at '
                           'FROM technology_progress')
        connection.execute('DROP TABLE technology_progress')
        connection.execute('ALTER TABLE technology_progress_v7 RENAME TO technology_progress')
        if self._info(connection)['revision'] is not None:
            self._touch(connection)
        if self._info(connection)['pending_revision'] is not None:
            connection.execute('UPDATE fleet_info SET pending_revision=? WHERE id=1', (tech.revision(uuid4().hex),))

    @staticmethod
    def _rows(connection, query, args=()):
        cursor = connection.execute(query, args)
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor]

    def _info(self, connection):
        return self._rows(connection, 'SELECT * FROM fleet_info WHERE id=1')[0]

    def _cards(self, connection):
        rows = self._rows(connection, 'SELECT id, ' + ', '.join(ledger.CARD_FIELDS) + ' FROM ships ORDER BY position')
        for row in rows:
            for field in ('review', 'excluded', 'full_star'):
                row[field] = None if row[field] is None else bool(row[field])
        return rows

    def _snapshot(self, connection):
        info = self._info(connection)
        if info['revision'] is None:
            return None
        rows = self._cards(connection)
        observed = dict(connection.execute('SELECT group_type, max_observed_level FROM technology '
                                           'WHERE max_observed_level IS NOT NULL'))
        current = {}
        for row in rows:
            if not row['review'] and row['group_type'] is not None and 1 <= row['level'] <= 125:
                key = str(row['group_type'])
                current[key] = max(current.get(key, 0), row['level'])
        return dict(version=1, revision=info['revision'], ships=rows, history=dict(
            observed=observed, current=current, absent=[]), updated_at=info['updated_at'],
            min_level=1, max_level=125, reached_bottom=False, filters_clear=False,
            physical_count=len(rows), expected_count=None, complete=False,
            review_count=sum(row['review'] for row in rows), source='ledger')

    @staticmethod
    def _manual(row):
        result = {field: (bool(row[field]) if field in ('owned', 'full_star', 'lv120') and row[field] is not None
                          else row[field]) for bit, field in enumerate(ledger.MANUAL_FIELDS[:-1], 1)
                  if row['manual_fields'] & (1 << bit)}
        if row['manual_fields'] & (1 << len(ledger.MANUAL_FIELDS)):
            result['level_toggle'] = ({} if row['low_level'] is None and row['high_level'] is None
                                      else dict(low=row['low_level'], high=row['high_level']))
        return result

    def _options(self, connection, scope='technology'):
        if scope != 'technology':
            return {}
        values = dict(manual={}, nations={})
        for row in self._rows(connection, 'SELECT * FROM technology WHERE manual_fields != 0'):
            values['manual'][row['group_type']] = self._manual(row)
        values['nations'] = dict(connection.execute('SELECT nation_key, level FROM nation_technology'))
        mask = self._info(connection)['technology_fields']
        output = {key: values[key] for index, key in enumerate(('manual', 'nations')) if mask & (1 << index)}
        progress = {row.pop('group_type'): row for row in self._rows(connection, 'SELECT * FROM technology_progress')}
        for row in progress.values():
            for key in (*tech.MILESTONES, 'awakening_pending'):
                row[key] = bool(row[key])
        if progress:
            output['progress'] = progress
        evidence = {}
        for row in self._rows(connection, 'SELECT * FROM technology_evidence ORDER BY group_type, milestone, ship_id'):
            gid, milestone = row.pop('group_type'), row.pop('milestone')
            row['full_star'] = None if row['full_star'] is None else bool(row['full_star'])
            evidence.setdefault(gid, {}).setdefault(milestone, []).append(row)
        if evidence:
            output['evidence'] = evidence
        return output

    @staticmethod
    def _legacy_imported(connection):
        return connection.execute('SELECT imported_at IS NOT NULL FROM fleet_info WHERE id=1').fetchone()[0] == 1

    def _load(self, connection, *, include_import_status=False):
        info = self._info(connection)
        value = dict(snapshot=self._snapshot(connection), technology=self._options(connection), mind={},
                     pending_revision=info['pending_revision'], pending_changes=self._differences(connection))
        if info['reading_count'] is not None:
            value['pending_read'] = dict(source=info['pending_source'], updated_at=info['pending_updated_at'],
                                        observed_count=info['reading_count'], review_count=info['reading_review_count'],
                                        complete=bool(info['reading_complete']))
        if include_import_status:
            value['legacy_imported'] = self._legacy_imported(connection)
        return value

    def load(self, *, include_import_status=False):
        try:
            return self._read_only(include_import_status=include_import_status)
        except FleetDatabaseMigrationRequired:
            with self._transaction():
                pass
            return self._read_only(include_import_status=include_import_status)
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_READONLY_ROLLBACK:
                raise
        # A crashed writer may leave a hot rollback journal. Only this recovery
        # condition opens existing storage for writing, never creates a missing file.
        connection = self._connect(write=True, create=False)
        if connection is not None:
            try:
                connection.execute('BEGIN IMMEDIATE')
                connection.rollback()
            finally:
                connection.close()
        return self.load(include_import_status=include_import_status)

    def _read_only(self, *, include_import_status=False):
        connection = self._connect(write=False)
        if connection is None:
            value = dict(snapshot=None, mind={}, technology={}, pending_revision=None, pending_changes=[])
            if include_import_status:
                value['legacy_imported'] = False
            return value
        try:
            connection.execute('BEGIN')
            self._schema(connection)
            return self._load(connection, include_import_status=include_import_status)
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        connection = self._connect(write=True)
        try:
            connection.execute('BEGIN IMMEDIATE')
            self._schema(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _revision(self, connection):
        return self._info(connection)['revision'] or tech.revision({})

    def _check_revision(self, connection, expected):
        if self._revision(connection) != expected:
            raise FleetDatabaseConflict('舰船数据已更新，请重新载入后操作')

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def _touch(self, connection):
        connection.execute('UPDATE fleet_info SET revision=?, updated_at=? WHERE id=1', (tech.revision(uuid4().hex), self._now()))

    def _write_options(self, connection, scope, options):
        if scope != 'technology':
            return
        if 'progress' in options:
            for gid, entry in options['progress'].items():
                self._set_progress(connection, gid, entry, source=entry.get('source', 'manual'))
            connection.execute('UPDATE fleet_info SET technology_initialized=1 WHERE id=1')
        connection.execute('UPDATE technology SET manual_fields=0, owned=NULL, full_star=NULL, '
                           'lv120=NULL, level=NULL, low_level=NULL, high_level=NULL')
        for gid, entry in options.get('manual', {}).items():
            self._write_technology(connection, 'technology', gid, None, entry)
        connection.execute('DELETE FROM technology WHERE max_observed_level IS NULL AND manual_fields=0')
        connection.execute('DELETE FROM nation_technology')
        for key, value in options.get('nations', {}).items():
            ledger.integer(value, '阵营等级', nullable=True)
            connection.execute('INSERT INTO nation_technology VALUES (?, ?)', (key, value))
        mask = sum(1 << index for index, key in enumerate(('manual', 'nations')) if key in options)
        connection.execute('UPDATE fleet_info SET technology_fields=? WHERE id=1', (mask,))

    @staticmethod
    def _write_technology(connection, table, gid, observed, entry):
        if not isinstance(gid, str) or not gid.isdecimal():
            raise ValueError('舰船群组必须是数字编号')
        ledger.integer(observed, '累计科技等级', 1, 125, nullable=True)
        ledger.integer(entry.get('level'), '人工等级', 1, 125, nullable=True)
        for field in ('owned', 'full_star', 'lv120'):
            if entry.get(field) is not None and type(entry[field]) is not bool:
                raise ValueError('人工科技状态必须是布尔值或空值')
        pair = entry.get('level_toggle', {})
        if not isinstance(pair, dict):
            raise ValueError('等级切换端点无效')
        if pair:
            ledger.integer(pair.get('low'), '低等级端点', 1, 119)
            ledger.integer(pair.get('high'), '高等级端点', 120, 125)
        mask = (1 | sum(1 << bit for bit, field in enumerate(ledger.MANUAL_FIELDS, 1) if field in entry)) if entry else 0
        connection.execute(f'INSERT INTO {table} (group_type, max_observed_level, manual_fields, owned, '
                           'full_star, lv120, level, low_level, high_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT(group_type) DO UPDATE SET max_observed_level='
                           f'CASE WHEN excluded.max_observed_level IS NULL THEN {table}.max_observed_level '
                           f'ELSE MAX(COALESCE({table}.max_observed_level, 0), excluded.max_observed_level) END, '
                           'manual_fields=excluded.manual_fields, owned=excluded.owned, full_star=excluded.full_star, '
                           'lv120=excluded.lv120, level=excluded.level, low_level=excluded.low_level, high_level=excluded.high_level',
                           (gid, observed, mask, entry.get('owned'), entry.get('full_star'), entry.get('lv120'),
                            entry.get('level'), pair.get('low'), pair.get('high')))

    @contextmanager
    def edit(self, *, import_legacy=False):
        if import_legacy:
            raise ValueError('读取旧数据请使用待确认更新入口')
        with self._transaction() as connection:
            state = self._load(connection)
            original = copy.deepcopy(state['technology'])
            yield state
            if original != state['technology']:
                self._write_options(connection, 'technology', state['technology'])
                self._touch(connection)

    @staticmethod
    def _insert_card(connection, values):
        position = connection.execute('SELECT COALESCE(MAX(position), -1)+1 FROM ships').fetchone()[0]
        return connection.execute('INSERT INTO ships (position, ' + ', '.join(ledger.CARD_FIELDS) + ') VALUES ('
                                  + ', '.join('?' for _ in range(len(ledger.CARD_FIELDS) + 1)) + ')',
                                  (position, *(values[field] for field in ledger.CARD_FIELDS))).lastrowid

    @staticmethod
    def _update_card(connection, ship_id, values):
        connection.execute('UPDATE ships SET ' + ', '.join(f'{field}=?' for field in ledger.CARD_FIELDS)
                           + ' WHERE id=?', (*(values[field] for field in ledger.CARD_FIELDS), ship_id))

    def _set_progress(self, connection, gid, values, *, source):
        values = dict(values)
        if 'awakening_pending' in values and type(values['awakening_pending']) is not bool:
            raise ValueError('满级待觉醒标记必须为布尔值')
        for key in tech.MILESTONES:
            if key in values and type(values[key]) is not bool:
                raise ValueError('科技里程碑必须为布尔值')
            values.setdefault(key, False)
        connection.execute('INSERT INTO technology_progress '
                           '(group_type, owned, full_star, lv120, source, updated_at) VALUES (?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT(group_type) DO UPDATE SET owned=excluded.owned, '
                           'full_star=excluded.full_star, lv120=excluded.lv120, source=excluded.source, '
                           'updated_at=excluded.updated_at',
                           (str(gid), *(values.get(key) for key in tech.MILESTONES), source, self._now()))
        # Omitted manual flags survive inferred milestone writes and legacy migrations.
        if 'awakening_pending' in values:
            connection.execute('UPDATE technology_progress SET awakening_pending=? WHERE group_type=?',
                               (values['awakening_pending'], str(gid)))

    def _write_evidence(self, connection, card, milestone, source):
        connection.execute('INSERT INTO technology_evidence '
                           '(group_type, milestone, ship_id, level, full_star, source, confirmed_at) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?) '
                           'ON CONFLICT(group_type,milestone,ship_id) DO UPDATE SET level=excluded.level, '
                           'full_star=excluded.full_star, source=excluded.source, confirmed_at=excluded.confirmed_at',
                           (str(card['group_type']), milestone, card['id'], card['level'],
                            card.get('full_star'), source, self._now()))

    def _initialize_technology(self, connection):
        """Seed account facts once, after the first confirmed ledger is built."""
        if self._info(connection)['technology_initialized']:
            return
        progress = self._options(connection).get('progress', {})
        inferred = {}
        for card in self._cards(connection):
            facts = tech.positive_facts(card)
            if not facts:
                continue
            gid = str(card['group_type'])
            # Even an explicitly incomplete saved row is an independent decision.
            if gid in progress:
                continue
            values = inferred.setdefault(gid, {})
            for key in facts:
                values[key] = True
                self._write_evidence(connection, card, key, 'initial ledger')
        for gid, values in inferred.items():
            self._set_progress(connection, gid, values, source='initial ledger')
        connection.execute('UPDATE fleet_info SET technology_initialized=1 WHERE id=1')

    def patch_technology(self, changes, nations, expected_revision):
        with self._transaction() as connection:
            from module.runtime import dock_data
            record = dock_data.technology_record(self._load(connection))
            if tech.revision(record) != expected_revision:
                raise FleetDatabaseConflict('科技记录已更新，请重新载入后编辑')
            known = {row['group_type']: row for row in tech.catalog()['ships']}
            seen = set()
            for change in changes:
                gid = change['group_type']
                if gid not in known or gid in seen:
                    raise ValueError('舰船群组不存在或重复')
                seen.add(gid)
                if change.keys() - {'group_type', *tech.MILESTONES, 'awakening_pending'}:
                    raise ValueError('只允许编辑科技里程碑；等级请在我的舰船维护')
                values = dict(record['progress'].get(str(gid), {}))
                values.update({key: change[key] for key in (*tech.MILESTONES, 'awakening_pending') if key in change})
                # Enabling fills omitted prerequisites. Clearing a prerequisite
                # also clears the milestones that require it for this ship type.
                if 'owned' not in change and any(change.get(key) is True for key in ('full_star', 'lv120')):
                    values['owned'] = True
                if ('full_star' not in change and change.get('lv120') is True
                        and known[gid]['full_star_rule'] == 'ordinary_level_cap'):
                    values['full_star'] = True
                if change.get('owned') is False:
                    values.update(full_star=False, lv120=False)
                if change.get('full_star') is False and known[gid]['full_star_rule'] == 'ordinary_level_cap':
                    values['lv120'] = False
                self._set_progress(connection, gid, values, source='manual correction')
            if changes:
                connection.execute('UPDATE fleet_info SET technology_initialized=1 WHERE id=1')
            for key, value in nations.items():
                if key not in tech.catalog()['nations'] or (value is not None and
                        (type(value) is not int or not 0 <= value <= len(tech.catalog()['nations'][key]))):
                    raise ValueError('阵营科技等级无效')
                connection.execute('INSERT INTO nation_technology VALUES (?, ?) ON CONFLICT(nation_key) '
                                   'DO UPDATE SET level=excluded.level', (key, value))
                connection.execute('UPDATE fleet_info SET technology_fields=technology_fields | 2 WHERE id=1')
            if changes or nations:
                self._touch(connection)

    def merge_ship_technology(self, expected_revision):
        """Explicitly union saved milestones with confirmed current ship facts."""
        with self._transaction() as connection:
            from module.runtime import dock_data
            record = dock_data.technology_record(self._load(connection))
            if tech.revision(record) != expected_revision:
                raise FleetDatabaseConflict('科技记录或舰船数据已更新，请重新载入后合并')
            progress = record['progress']
            changed = {}
            for card in self._cards(connection):
                facts = tech.positive_facts(card)
                if not facts:
                    continue
                gid = str(card['group_type'])
                values = changed.get(gid, progress.get(gid, {}))
                additions = facts - {key for key in tech.MILESTONES if values.get(key) is True}
                if not additions:
                    continue
                values = changed.setdefault(gid, dict(values))
                for key in additions:
                    values[key] = True
                    self._write_evidence(connection, card, key, 'explicit union: current ship')
            if changed:
                for gid, values in changed.items():
                    self._set_progress(connection, gid, values, source='explicit union: current ship')
                connection.execute('UPDATE fleet_info SET technology_initialized=1 WHERE id=1')
                self._touch(connection)

    def patch_cards(self, changes, expected_revision):
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            current = {row['id']: row for row in self._cards(connection)}
            seen = set()
            for change in changes:
                ship_id = change.get('id')
                if ship_id is not None:
                    ledger.integer(ship_id, '内部序号', 1)
                    if ship_id not in current or ship_id in seen:
                        raise ValueError('内部序号不存在或重复')
                    seen.add(ship_id)
                if change.get('delete'):
                    if ship_id is None or change.keys() - {'id', 'delete'}:
                        raise ValueError('删除只允许指定内部序号')
                    connection.execute('DELETE FROM ships WHERE id=?', (ship_id,))
                else:
                    values = ledger.card(change, current.get(ship_id))
                    if ship_id is None:
                        self._insert_card(connection, values)
                    else:
                        self._update_card(connection, ship_id, values)
            if changes:
                self._initialize_technology(connection)
                self._touch(connection)

    def _differences(self, connection):
        cards = {row['id']: row for row in self._cards(connection)}
        output = []
        for row in self._rows(connection, 'SELECT * FROM pending_ships ORDER BY id'):
            values = {field: bool(row[field]) if field in ('excluded', 'review', 'full_star') and row[field] is not None else row[field]
                      for field in ledger.CARD_FIELDS}
            before = cards.get(row['ship_id'])
            operation = row['operation']
            fields = [field for bit, field in enumerate(ledger.CARD_FIELDS) if row['fields'] & (1 << bit)]
            if operation == 'remove':
                if before is None:
                    continue
                delta = ['delete']
            elif operation == 'update' and before:
                delta = ledger.changed(before, values, fields)
                if not delta:
                    continue
            else:
                if operation == 'update':
                    operation = 'resolve'
                delta = fields or list(ledger.CARD_FIELDS)
            eligible = {value[0] for value in connection.execute(
                'SELECT ship_id FROM pending_targets WHERE proposal_id=?', (row['id'],))}
            candidates = [dict(id=value['id'], name=value['name'], level=value['level']) for value in cards.values()
                          if value['id'] in eligible and value['group_type'] == values['group_type']]
            output.append(dict(id=f'ship:{row["id"]}', kind='ship', operation=operation, ship_id=row['ship_id'],
                               group_type=values['group_type'], name=values['name'], before=before,
                               after=None if operation == 'remove' else values, fields=delta,
                               candidates=candidates if operation == 'resolve' else [],
                               milestones=[]))
        current = self._options(connection)
        names = {str(row['group_type']): row['name'] for row in tech.catalog()['ships']}
        for row in self._rows(connection, 'SELECT * FROM pending_technology ORDER BY group_type'):
            gid = row['group_type']
            before = current.get('progress', {}).get(gid, {key: False for key in tech.MILESTONES})
            manual = self._manual(row)
            inferred = tech.legacy_milestones(gid, row['max_observed_level'], manual)
            after = {key: value for key, value in inferred.items()
                     if value is True or key in manual}
            fields = [key for key in after if before.get(key) != after[key]]
            if fields:
                output.append(dict(id=f'technology:{gid}', kind='technology', operation='update', ship_id=None,
                                   group_type=int(gid), name=names.get(gid, gid), before=before,
                                   after=after, fields=fields, candidates=[]))
        nations = current.get('nations', {})
        for key, value in connection.execute('SELECT nation_key, level FROM pending_nations ORDER BY nation_key'):
            if key not in nations or nations[key] != value:
                output.append(dict(id=f'nation:{key}', kind='nation', operation='update', ship_id=None,
                                   group_type=None, name=key, before=dict(level=nations.get(key)),
                                   after=dict(level=value), fields=['level'], candidates=[]))
        return output

    def _stage(self, connection, rows, *, complete, observed, options, source, updated_at):
        if source != 'legacy':
            rows = [{key: value for key, value in row.items()
                     if not (key in ('extra_exp', 'full_star', 'form') and value is None)} for row in rows]
        if self._info(connection)['pending_revision'] is not None:
            raise FleetDatabaseConflict('仍有待确认更新，请先采纳或忽略后再读取新数据')
        for table in ('pending_ships', 'pending_technology', 'pending_nations'):
            connection.execute(f'DELETE FROM {table}')
        for proposal in ledger.proposals(self._cards(connection), rows, complete=complete):
            mask = sum(1 << ledger.CARD_FIELDS.index(field) for field in proposal['fields'])
            values = proposal['values']
            cursor = connection.execute('INSERT INTO pending_ships (operation, ship_id, fields, '
                               + ', '.join(ledger.CARD_FIELDS) + ') VALUES ('
                               + ', '.join('?' for _ in range(len(ledger.CARD_FIELDS) + 3)) + ')',
                               (proposal['operation'], proposal['ship_id'], mask,
                                *(values[field] for field in ledger.CARD_FIELDS)))
            connection.executemany('INSERT INTO pending_targets VALUES (?, ?)',
                                   ((cursor.lastrowid, target) for target in proposal.get('candidates', [])))
        for gid in observed.keys() | options.get('manual', {}).keys():
            self._write_technology(connection, 'pending_technology', gid, observed.get(gid),
                                   options.get('manual', {}).get(gid, {}))
        for key, value in options.get('nations', {}).items():
            ledger.integer(value, '阵营等级', nullable=True)
            connection.execute('INSERT INTO pending_nations VALUES (?, ?)', (key, value))
        connection.execute('UPDATE fleet_info SET reading_count=?, reading_review_count=?, reading_complete=? WHERE id=1',
                           (len(rows), sum(ledger.card(row)['review'] for row in rows), int(complete)))
        has_changes = bool(self._differences(connection))
        connection.execute('UPDATE fleet_info SET pending_revision=?, pending_source=?, pending_updated_at=? WHERE id=1',
                           (tech.revision(uuid4().hex) if has_changes else None, source, updated_at))
        self._prune_pending(connection)

    def _prune_pending(self, connection):
        visible = {row['id'] for row in self._differences(connection)}
        for table, prefix, key in (('pending_ships', 'ship', 'id'), ('pending_technology', 'technology', 'group_type'),
                                   ('pending_nations', 'nation', 'nation_key')):
            for value, in connection.execute(f'SELECT {key} FROM {table}').fetchall():
                if f'{prefix}:{value}' not in visible:
                    connection.execute(f'DELETE FROM {table} WHERE {key}=?', (value,))
        connection.execute('UPDATE fleet_info SET pending_revision=? WHERE id=1', (tech.revision(uuid4().hex) if visible else None,))

    def save_scan(self, snapshot, expected_revision):
        """A completed reading stages differences, never overwrites the player's ledger."""
        metadata = {field: snapshot.get(field, default) for field, default in (
            ('min_level', 1), ('max_level', 125), ('reached_bottom', False), ('filters_clear', False),
            ('physical_count', len(snapshot['ships'])), ('expected_count', None), ('updated_at', ''))}
        ledger.integer(metadata['min_level'], '最低等级', 1, 125)
        ledger.integer(metadata['max_level'], '最高等级', metadata['min_level'], 125)
        ledger.integer(metadata['physical_count'], '卡片数量')
        ledger.integer(metadata['expected_count'], '船坞数量', nullable=True)
        for field in ('reached_bottom', 'filters_clear'):
            if type(metadata[field]) is not bool:
                raise ValueError('扫描完整性证明必须为布尔值')
        rows = [row for row in snapshot['ships']
                if not (type(row.get('level')) is int and 1 <= row['level'] <= 125
                        and not metadata['min_level'] <= row['level'] <= metadata['max_level'])]
        derived = tech.merge_scan({}, rows, **metadata)
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            self._stage(connection, rows, complete=derived['last_scan']['complete'],
                        observed={}, options={}, source='scan', updated_at=metadata['updated_at'])

    def stage_import(self, rows, expected_revision):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
            raise ValueError('导入舰船数量必须介于 1 到 5000')
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            self._stage(connection, rows, complete=False, observed={}, options={},
                        source='import', updated_at=self._now())

    def stage_legacy(self, converted, expected_revision):
        rows = ledger.legacy_cards(converted)
        observed = copy.deepcopy((converted.get('snapshot') or {}).get('history', {}).get('observed', {}))
        for row in rows:
            if row.get('group_type') and not row.get('review') and 1 <= row.get('level', 0) <= 125:
                key = str(row['group_type'])
                observed[key] = max(observed.get(key, 0), row['level'])
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            if self._legacy_imported(connection):
                raise FleetDatabaseAlreadyImported('本实例旧数据已经读取')
            self._stage(connection, rows, complete=False, observed=observed, options=converted.get('technology', {}),
                        source='legacy', updated_at=self._now())
            connection.execute('UPDATE fleet_info SET imported_at=? WHERE id=1', (self._now(),))

    def review(self, decisions, expected_revision, pending_revision):
        with self._transaction() as connection:
            self._check_revision(connection, expected_revision)
            if pending_revision is None or self._info(connection)['pending_revision'] != pending_revision:
                raise FleetDatabaseConflict('待采纳数据已变化，请重新载入')
            differences = {row['id']: row for row in self._differences(connection)}
            seen, used_targets, accepted, accepted_ship = set(), set(), False, False
            for decision in decisions:
                key = decision['id']
                if key in seen or key not in differences:
                    raise ValueError('差异不存在或重复')
                seen.add(key)
                if type(decision.get('accept')) is not bool:
                    raise ValueError('必须明确选择采纳或忽略')
                row = differences[key]
                if decision['accept']:
                    if row['operation'] == 'remove' and len(decisions) != 1:
                        raise ValueError('移除建议必须单独确认，不能批量采纳')
                    accepted = True
                    if row['kind'] == 'ship':
                        accepted_ship = True
                        target = row['ship_id']
                        if row['operation'] == 'resolve':
                            if 'ship_id' not in decision:
                                raise ValueError('同名舰船需要选择现有内部序号或明确新增')
                            target = decision['ship_id']
                            if target is not None and target not in {item['id'] for item in row['candidates']}:
                                raise ValueError('选择的舰船记录不在候选范围内')
                        elif 'ship_id' in decision:
                            raise ValueError('只有需要匹配的差异才能指定目标')
                        if target is not None:
                            ledger.integer(target, '目标内部序号', 1)
                            if target in used_targets:
                                raise ValueError('不能把多张卡片更新到同一内部序号')
                            used_targets.add(target)
                            connection.execute('DELETE FROM pending_targets WHERE ship_id=?', (target,))
                        if row['operation'] == 'remove':
                            connection.execute('DELETE FROM ships WHERE id=?', (target,))
                        elif target is None:
                            self._insert_card(connection, ledger.card(row['after']))
                        else:
                            current = next((item for item in self._cards(connection) if item['id'] == target), None)
                            if current is None:
                                raise FleetDatabaseConflict('目标舰船已被修改，请重新载入')
                            pending = self._rows(connection, 'SELECT fields FROM pending_ships WHERE id=?', (int(key.split(':')[1]),))[0]
                            values = {field: row['after'][field] for bit, field in enumerate(ledger.CARD_FIELDS)
                                      if pending['fields'] & (1 << bit)}
                            self._update_card(connection, target, ledger.card(values, current))
                    elif row['kind'] == 'technology':
                        gid = str(row['group_type'])
                        values = dict(self._options(connection).get('progress', {}).get(gid, {}))
                        values.update({field: row['after'][field] for field in row['fields']})
                        self._set_progress(connection, gid, values, source='confirmed import')
                        connection.execute('UPDATE fleet_info SET technology_initialized=1 WHERE id=1')
                    else:
                        connection.execute('INSERT INTO nation_technology VALUES (?, ?) ON CONFLICT(nation_key) '
                                           'DO UPDATE SET level=excluded.level', (row['name'], row['after']['level']))
                        connection.execute('UPDATE fleet_info SET technology_fields=technology_fields | 2 WHERE id=1')
                table, column = {'ship': ('pending_ships', 'id'), 'technology': ('pending_technology', 'group_type'),
                                 'nation': ('pending_nations', 'nation_key')}[row['kind']]
                connection.execute(f'DELETE FROM {table} WHERE {column}=?', (key.split(':', 1)[1],))
            if accepted:
                if accepted_ship:
                    self._initialize_technology(connection)
                self._touch(connection)
            self._prune_pending(connection)
