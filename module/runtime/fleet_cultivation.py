"""Read-only cultivation projections for explicitly selected physical cards."""
import json
from functools import lru_cache
from pathlib import Path

from module.api.protocol import ApiError
from module.runtime.mind_calculator import catalog, find_ship


@lru_cache(maxsize=1)
def rules():
    return json.loads((Path(__file__).parents[2] / 'assets/ship/cultivation.json').read_text())


def project(cards, ship_ids, target_level=120, assumptions=()):
    by_id = {card['id']: card for card in cards}
    if len(set(ship_ids)) != len(ship_ids) or any(ship_id not in by_id for ship_id in ship_ids):
        raise ApiError('INVALID_PARAMS', '所选舰船已不存在或包含重复副本，请重新选择')
    overrides = {item['ship_id']: item for item in assumptions}
    if len(overrides) != len(assumptions) or not set(overrides) <= set(ship_ids):
        raise ApiError('INVALID_PARAMS', '试算条件必须对应已选择的独立舰船')
    data, rows = rules(), []
    for ship_id in ship_ids:
        card = by_id[ship_id]
        assumption = overrides.get(ship_id, {})
        level = assumption.get('level') or card['level']
        info = find_ship(card['name'])
        if not info:
            # The confirmed group disambiguates names shared by different ships.
            matches = [item for item in catalog()['ships'].values()
                       if item['group_type'] == card.get('group_type')
                       and card['name'] in (item['name'], item.get('raw_name'))]
            info = matches[0] if len(matches) == 1 else None
        rarity = info['base_rarity'] if info else None
        row = dict(id=ship_id, name=card['name'], current_level=card['level'], level=level,
                   target_level=target_level, hypothetical=bool(assumption),
                   mind=None, mind2=None, gold=None,
                   status='unknown', notes=[], stages=[])
        if (card.get('review') or not info or card.get('group_type') != info['group_type']
                or card.get('base_rarity') != rarity or not 1 <= level <= 125
                or rarity not in ('N', 'R', 'SR', 'SSR', 'UR')):
            row['notes'].append('身份、等级或基础稀有度待核对，暂不计入资源合计')
            rows.append(row)
            continue
        if info.get('group') == '布里':
            row['notes'].append('本计算暂不支持布里的养成费用，未计入资源合计')
            rows.append(row)
            continue
        row['status'] = 'exact'
        cap = assumption.get('awakened_to')
        inferred_cap = next((n for n in (100, 105, 110, 115, 120, 125) if n >= level), 125)
        maximum_cap = min(125, inferred_cap + (5 if level == inferred_cap else 0))
        if cap is not None and not inferred_cap <= cap <= maximum_cap:
            raise ApiError('INVALID_PARAMS', f'{card["name"]} 的已觉醒上限与试算等级不符')
        cap = cap or inferred_cap
        row['mind'] = row['mind2'] = row['gold'] = 0
        for threshold in (100, 105, 110, 115, 120):
            if cap <= threshold < target_level:
                cost = data['awaken'][str(threshold)][rarity]
                row['stages'].append(threshold)
                for key in ('mind', 'mind2', 'gold'):
                    row[key] += cost[key]
        if assumption.get('awakened_to') is None and level in (100, 105, 110, 115, 120) and level < target_level:
            row['status'] = 'estimated'
            row['notes'].append('处于觉醒边界，资源按尚未觉醒估算；可设置已觉醒上限')
        rows.append(row)
    return dict(ships=rows, target_level=target_level, included=len(rows),
                unknown=sum(row['status'] == 'unknown' for row in rows),
                estimated=sum(row['status'] == 'estimated' for row in rows),
                mind=sum(row['mind'] or 0 for row in rows), mind2=sum(row['mind2'] or 0 for row in rows),
                gold=sum(row['gold'] or 0 for row in rows), rules_sha256=data['source_sha256'])
