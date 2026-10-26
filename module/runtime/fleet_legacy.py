"""Pure conversion for the user's explicit one-time local legacy-data import."""
import copy
from datetime import datetime
from functools import lru_cache

from module.config.deep import deep_get
from module.config.fleet_legacy import LEGACY_PATHS
from module.runtime import dock_data, fleet_ledger as ledger, fleet_tech as tech, mind_analysis, mind_calculator as mind


@lru_cache(maxsize=1)
def _known():
    return frozenset(str(row['group_type']) for row in tech.catalog()['ships'])


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} 必须为对象')
    return value


def _integer(value, low=1, high=125):
    return type(value) is int and low <= value <= high


def _history(value):
    value = _object(value, '历史记录')
    known = _known()
    result = {}
    for field in ('observed', 'current'):
        if field not in value:
            continue
        entries = _object(value[field], field)
        if any(gid not in known or not _integer(level) for gid, level in entries.items()):
            raise ValueError(f'{field} 包含无效群组或等级')
        result[field] = copy.deepcopy(entries)
    if 'absent' in value:
        if not isinstance(value['absent'], list) or any(str(gid) not in known for gid in value['absent']):
            raise ValueError('缺失记录包含无效群组')
        result['absent'] = [str(gid) for gid in value['absent']]
    return result


def _row(value):
    value = copy.deepcopy(_object(value, '舰船条目'))
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 100:
        raise ValueError('舰名无效')
    if not _integer(value.get('level'), 0, 125):
        raise ValueError('舰船等级无效')
    for field in ('excluded', 'review'):
        if field in value and type(value[field]) is not bool:
            raise ValueError(f'{field} 必须为布尔值')
    for field in ('rarity', 'base_rarity'):
        if field in value and value[field] not in ('', 'N', 'R', 'SR', 'SSR', 'UR'):
            raise ValueError('舰船稀有度无效')
    if 'source' in value and (not isinstance(value['source'], str) or len(value['source']) > 200):
        raise ValueError('舰船来源无效')
    if value.get('group_type') is not None and (type(value['group_type']) is not int
            or str(value['group_type']) not in _known()):
        raise ValueError('舰船群组无效')
    if 'full_star' not in value and 'breakthrough' in value:
        stage = value.pop('breakthrough')
        if stage is not None and not _integer(stage, 0, 3):
            raise ValueError('旧突破状态无效')
        value['full_star'] = stage == 3 if stage is not None else None
    info = mind.find_ship(value['name'])
    if value.get('group_type') is None and info and not value.get('review'):
        value['group_type'] = info['group_type']
    value.setdefault('review', value.get('group_type') is None or value['level'] == 0)
    return value


def _rows(value):
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError('舰船清单必须为数组且不超过 5000 条')
    return [_row(row) for row in value]


def _options(record):
    manual, nations = record.get('manual', {}), record.get('nations', {})
    _object(manual, '人工科技')
    _object(nations, '阵营等级')
    public = tech.catalog()
    known = {str(row['group_type']) for row in public['ships']}
    allowed = {'owned', 'full_star', 'lv120', 'level', 'level_toggle'}
    for gid, entry in manual.items():
        if gid not in known or not isinstance(entry, dict) or entry.keys() - allowed:
            raise ValueError('人工科技字段或群组无效')
        for field, value in entry.items():
            if field in ('owned', 'full_star', 'lv120'):
                valid = value is None or type(value) is bool
            elif field == 'level':
                valid = value is None or _integer(value)
            else:
                valid = (isinstance(value, dict) and value.keys() == {'low', 'high'}
                         and _integer(value['low'], 1, 119) and _integer(value['high'], 120, 125))
            if not valid:
                raise ValueError('人工科技字段值无效')
    for nation, level in nations.items():
        if nation not in public['nations'] or not (level is None or _integer(level, 0, len(public['nations'][nation]))):
            raise ValueError('阵营科技等级无效')
    return copy.deepcopy(dict(manual={gid: {field: value is True if field in tech.MILESTONES else value
                                           for field, value in entry.items()}
                                     for gid, entry in manual.items() if entry}, nations=nations))


def _merge_options(target, source):
    for gid, values in source.get('manual', {}).items():
        existing = target.setdefault('manual', {}).setdefault(gid, {})
        grades = {'level', 'lv120', 'level_toggle'}
        preserve_grade = bool(grades & existing.keys())
        for field, value in values.items():
            if not (preserve_grade and field in grades):
                existing.setdefault(field, copy.deepcopy(value))
    for nation, value in source.get('nations', {}).items():
        target.setdefault('nations', {}).setdefault(nation, value)


def _snapshot(rows, original, source, *, dock=False):
    low, high = original.get('min_level', 1), original.get('max_level', 125)
    if not _integer(low) or not _integer(high) or low > high:
        raise ValueError('旧扫描等级范围无效')
    updated = original.get('updated_at', '')
    if not isinstance(updated, str):
        raise ValueError('旧扫描时间无效')
    for field in ('reached_bottom', 'filters_clear', 'complete'):
        if field in original and type(original[field]) is not bool:
            raise ValueError('旧扫描证明无效')
    if dock:
        for field in ('physical_count', 'expected_count'):
            value = original.get(field)
            if value is not None and not _integer(value, 0, 5000):
                raise ValueError('旧扫描数量无效')
    result = dock_data.build_snapshot(rows, min_level=low, max_level=high,
        reached_bottom=original.get('reached_bottom', False) if dock else False,
        filters_clear=original.get('filters_clear', False) if dock else False,
        physical_count=original.get('physical_count', len(rows)) if dock else len(rows),
        expected_count=original.get('expected_count') if dock else None, updated_at=updated)
    result['import_source'] = source
    if isinstance(original.get('revision'), str):
        result['legacy_revision'] = original['revision']
    original_history = _history(original.get('history', {}))
    observed = result['history']['observed'].copy()
    result['history'].update(original_history)
    for gid, level in observed.items():
        result['history'].setdefault('observed', {})[gid] = max(result['history']['observed'].get(gid, 0), level)
    return result


def _timestamp(snapshot):
    try:
        return datetime.fromisoformat(snapshot['updated_at']).timestamp()
    except (ValueError, OverflowError, OSError):
        return float('-inf')


def _revision(snapshot):
    snapshot.pop('revision', None)
    snapshot['revision'] = tech.revision(snapshot)
    return snapshot


def convert_legacy(data):
    """Convert supplied local data into one current ledger and independent settings."""
    _object(data, '旧配置')
    sources = {}
    for path in LEGACY_PATHS:
        value = deep_get(data, path, {})
        _object(value, path)
        if value:
            sources[path] = copy.deepcopy(value)
    snapshots, technology, observed = [], {}, {}
    records = [sources[path] for path in (LEGACY_PATHS[4], LEGACY_PATHS[3]) if path in sources]
    for record in records:
        _merge_options(technology, _options(record))
        history = _history(record)
        for field in ('observed', 'current'):
            for gid, level in history.get(field, {}).items():
                observed[gid] = max(observed.get(gid, 0), level)
        if 'last_scan' in record:
            scan = _object(record['last_scan'], '旧科技扫描')
            if not isinstance(scan.get('ships', []), list):
                raise ValueError('旧科技扫描清单无效')
            rows = []
            catalog = {row['group_type']: row for row in tech.catalog()['ships']}
            for item in scan.get('ships', []):
                if (not isinstance(item, (list, tuple)) or len(item) != 2 or type(item[0]) is not int
                        or item[0] not in catalog or not _integer(item[1])):
                    raise ValueError('旧科技群组等级无效')
                info = catalog[item[0]]
                rarity = {'PR': 'SSR', 'DR': 'UR'}.get(info['rarity'], info['rarity'])
                rows.append(dict(name=info['name'], group_type=item[0], level=item[1], rarity=rarity,
                                 base_rarity=rarity, review=False, source='旧科技扫描'))
            rows.extend(_rows(record.get('review_ships', [])))
            result = _snapshot(rows, scan, 'legacy_technology')
            for field in ('observed', 'current'):
                for gid, level in result['history'].get(field, {}).items():
                    observed[gid] = max(observed.get(gid, 0), level)
            result['history'].update(history)
            snapshots.append(result)
        elif history:
            result = _snapshot([], record, 'legacy_technology_history')
            for field in ('observed', 'current'):
                for gid, level in result['history'].get(field, {}).items():
                    observed[gid] = max(observed.get(gid, 0), level)
            result['history'].update(history)
            snapshots.append(result)
    saved = sources.get(LEGACY_PATHS[1], {})
    mind_rows = None
    if 'ships' in saved:
        mind_rows = _rows(saved['ships'])
        snapshots.append(_snapshot(mind_rows, saved, 'legacy_mind'))
    snapshots.sort(key=_timestamp)
    dock = sources.get(LEGACY_PATHS[0])
    if dock:
        if 'ships' not in dock:
            raise ValueError('旧共享船坞缺少舰船数组')
        snapshots.append(_snapshot(_rows(dock['ships']), dock, 'legacy_dock', dock=True))
    for snapshot in snapshots:
        for gid, level in snapshot['history'].get('observed', {}).items():
            observed[gid] = max(observed.get(gid, 0), level)
    if snapshots:
        snapshots[-1]['history'].setdefault('observed', {}).update(observed)
        _revision(snapshots[-1])
    state = dict(snapshot=snapshots[-1] if snapshots else None, mind={}, technology={})
    analysis = sources.get(LEGACY_PATHS[2])
    if analysis:
        _object(analysis.get('overrides', {}), '心智覆盖')
        for key, values in analysis.get('overrides', {}).items():
            if not isinstance(key, str) or not isinstance(values, dict) or values.keys() - set(mind_analysis.FIELDS):
                raise ValueError('心智覆盖字段无效')
            _row({'name': '待核对舰船', 'level': 1, **values})
        if not isinstance(analysis.get('removed', []), list) or any(not isinstance(key, str) for key in analysis.get('removed', [])):
            raise ValueError('心智删除记录无效')
        _rows(analysis.get('extra', []))
        options = {key: copy.deepcopy(analysis[key])
                   for key in ('overrides', 'removed', 'extra', 'updated_at', 'snapshot_revision')
                   if key in analysis}
        if (snapshots and options.get('snapshot_revision')
                and options['snapshot_revision'] == snapshots[-1].get('legacy_revision')):
            options['snapshot_revision'] = snapshots[-1]['revision']
    elif mind_rows is not None:
        submitted = [{field: row.get(field, '' if field in ('rarity', 'base_rarity', 'source') else False)
                      for field in mind_analysis.FIELDS} for row in mind_rows]
        options = mind_analysis.save_settings(state, submitted, saved.get('updated_at', ''))
    else:
        options = {}
    useful_mind = any(options.get(key) for key in ('overrides', 'removed', 'extra'))
    if not snapshots and not useful_mind and not any(technology.get(key) for key in ('manual', 'nations')):
        raise ValueError('当前配置中没有可导入的旧舰船数据')
    return dict(snapshot=state['snapshot'], mind=options, technology=technology)


def _cards(converted):
    """Fold old calculator edits into proposed ordinary card fields, never a shadow store."""
    snapshot = copy.deepcopy(converted.get('snapshot'))
    rows = copy.deepcopy(snapshot.get('ships', [])) if snapshot else []
    aliases = tech.catalog()['aliases']
    for row in rows:
        row.setdefault('name', '未识别舰船')
        row.setdefault('group_type', aliases.get(mind.normalize_name(row['name'])))
    options = converted.get('mind', {})
    for key, override in options.get('overrides', {}).items():
        if key.startswith('group:'):
            gid = int(key.split(':')[1])
            matches = [row for row in rows if row.get('group_type') == gid]
            if matches:
                target = max(matches, key=lambda row: row.get('level', 0))
                target.update({field: value for field, value in override.items() if field in ledger.CARD_FIELDS})
    for key in options.get('removed', []):
        if key.startswith('group:'):
            gid = int(key.split(':')[1])
            for row in rows:
                if row.get('group_type') == gid:
                    row['excluded'] = True
    for raw in options.get('extra', []):
        row = copy.deepcopy(raw)
        info = mind.find_ship(row.get('name', ''))
        row['group_type'] = info['group_type'] if info else aliases.get(mind.normalize_name(row.get('name', '')))
        rows.append(row)
    # Review-specific fixes only bind to the exact old snapshot they were made against.
    if snapshot and options.get('snapshot_revision') == snapshot.get('revision'):
        for key, override in options.get('overrides', {}).items():
            if key.startswith('review:'):
                for row in rows:
                    if mind_analysis.ship_key(row) == key:
                        row.update({field: value for field, value in override.items() if field in ledger.CARD_FIELDS})
    return rows


def _milestones(group_type, observed=None, manual=None):
    """Convert evidence once; legacy unknown corrections become incomplete."""
    manual = manual or {}
    level = manual.get('level', observed)
    values = {key: False for key in tech.MILESTONES}
    if level:
        for key in tech.positive_facts(dict(group_type=int(group_type), level=level)):
            values[key] = True
    for key in tech.MILESTONES:
        if key in manual:
            values[key] = manual[key] is True
    if 'owned' in manual and manual['owned'] is not True:
        values.update(owned=False, full_star=False, lv120=False)
    if values['full_star'] is True or values['lv120'] is True:
        values['owned'] = True
    return values


def sqlite_payload(converted):
    """Normalize old JSON fields once, before they enter SQLite's review tables."""
    rows = _cards(converted)
    observed = copy.deepcopy((converted.get('snapshot') or {}).get('history', {}).get('observed', {}))
    for row in rows:
        if row.get('group_type') and not row.get('review') and 1 <= row.get('level', 0) <= 125:
            gid = str(row['group_type'])
            observed[gid] = max(observed.get(gid, 0), row['level'])
    options = converted.get('technology', {})
    manual = options.get('manual', {})
    technology = {}
    for gid in observed.keys() | manual.keys():
        entry = manual.get(gid, {})
        values = _milestones(gid, observed.get(gid), entry)
        technology[gid] = {key: value for key, value in values.items() if value is True or key in entry}
    return dict(ships=rows, technology=technology, nations=copy.deepcopy(options.get('nations', {})))
