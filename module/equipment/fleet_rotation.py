"""智能换船的可选装备交接，复用紧急委托的装备码状态流程。"""

from module.equipment.assets import (EQUIPMENT_CODE_ENTRANCE, EQUIPMENT_CODE_PAGE_CHECK,
                                     FLEET_DETAIL, FLEET_DETAIL_CHECK)
from module.equipment.equipment import Equipment
from module.exception import FleetManagementError
from module.logger import logger
from module.retire.fleet_management import position_button
from module.runtime.fleet_equipment import EQUIPMENT_PATH, SHIP_TYPES, parse_codes, recognize_ship_type
from module.runtime.fleet_formation import (DETAIL_LEFTS, bottom_fleet_index, detail_occupied,
                                           fleet_type_selected, selector_closed)
from module.ui.assets import BACK_ARROW


class FleetRotationEquipment(Equipment):
    """只处理本轮目标舰位，待完成记录独立于换船失败记录保存。"""

    def __init__(self, manager):
        super().__init__(config=manager.config, device=manager.device)
        self.manager = manager
        try:
            self.codes = parse_codes(self.config.cross_get(EQUIPMENT_PATH + '.Codes', '{}'))
        except ValueError as exc:
            raise FleetManagementError(str(exc)) from exc
        if self.config.SERVER != 'cn':
            raise FleetManagementError('自动换装备暂仅支持国服舰种模板')
        if not self.equipment_code_supported():
            raise FleetManagementError('当前控制方式不支持装备码自动输入')

    def _details(self, fleet):
        """进入已确认的指定舰队详情，不因装备操作重设船坞排序。

        Pages:
            in: Any
            out: 指定水面舰队详情
        """
        self.manager.finish_rotation()
        if not (self.manager._formation_visible() and fleet_type_selected(self.device.image, 'surface')
                and bottom_fleet_index(self.device.image) == fleet and selector_closed(self.device.image)):
            self.manager.fleet_enter(fleet)
        self.ui_click(FLEET_DETAIL, appear_button=self.manager._formation_visible,
                      check_button=FLEET_DETAIL_CHECK, retry_wait=2)
        self._equipment_fleet = fleet

    def recover_page(self):
        """中断停在已知装备页时先返回母港，交给原有完整舰队扫描。

        Pages:
            in: 装备码页、角色详情或其他原有可导航页面
            out: 已知装备页返回母港；其他页面不操作
        """
        if not self.device.has_cached_image:
            self.device.screenshot()
        if self.appear(EQUIPMENT_CODE_PAGE_CHECK):
            self._code_exit()
        if self.appear(EQUIPMENT_CODE_ENTRANCE):
            self._back_to_details()
        if self.appear(FLEET_DETAIL_CHECK):
            self._back_to_formation()
            self.manager._commit_formation()

    def _ship(self, slot):
        """点击详情固定舰位进入角色详情，空位不操作。

        Pages:
            in: 舰队详情
            out: 角色详情；空位保持舰队详情
        """
        category = 'main' if slot <= 3 else 'vanguard'
        records = self.config.cross_get(self.manager.RESULT_PATH, {}).get(category, {}).get(
            str(self._equipment_fleet), [])
        if not any(ship.get('slot') == slot and ship.get('occupied', True) for ship in records):
            return False
        for _ in self.loop(timeout=10):
            if self.appear(FLEET_DETAIL_CHECK) and detail_occupied(self.device.image, slot):
                break
        else:
            raise FleetManagementError(f'未确认详情舰位{slot}已加载，不操作装备')
        left = DETAIL_LEFTS[slot - 1]
        button = position_button((left + 30, 220, left + 125, 400), f'换装舰位{slot}')
        self.ui_click(button, appear_button=FLEET_DETAIL_CHECK, check_button=EQUIPMENT_CODE_ENTRANCE,
                      retry_wait=2)
        return True

    def _back_to_details(self):
        self.ui_back(check_button=FLEET_DETAIL_CHECK)

    def _back_to_formation(self):
        self.ui_click(BACK_ARROW, appear_button=FLEET_DETAIL_CHECK,
                      check_button=self.manager._formation_visible, retry_wait=2)

    def clear_outgoing(self, fleet, slots):
        """先记下所有待恢复舰位，再依次清空全部换出舰船，最后才交给换船。

        Pages:
            in: prepare_rotation完成的选船页
            out: 原目标舰队编队页
        """
        pending = dict(self.config.cross_get(EQUIPMENT_PATH + '.State', {}))
        pending.update({f'{fleet}:{slot}': {'fleet': fleet, 'slot': slot} for slot in slots})
        self.config.cross_set(EQUIPMENT_PATH + '.State', pending)
        self._details(fleet)
        for slot in slots:
            if self._ship(slot):
                logger.attr('换出舰船卸装', f'{fleet}-{slot}')
                self._code_enter()
                if not self._code_apply(code=None):
                    raise FleetManagementError('换出舰船卸装失败，保留待完成换装记录')
                self._back_to_details()
        self._back_to_formation()

    def apply_pending(self):
        """按当前舰位实际舰种补完换装，失败不清记录，成功舰位逐一移除。

        Pages:
            in: Any；换船完成或中断后重扫
            out: 最后处理舰队的编队页
        """
        pending = dict(self.config.cross_get(EQUIPMENT_PATH + '.State', {}))
        for fleet in sorted({job['fleet'] for job in pending.values()}):
            self._details(fleet)
            for key, job in list(pending.items()):
                if job['fleet'] != fleet:
                    continue
                if self._ship(job['slot']):
                    kind, previous = None, None
                    for _ in self.loop(skip_first=False, timeout=10):
                        if self.appear(EQUIPMENT_CODE_ENTRANCE):
                            candidate = recognize_ship_type(self.device.image)
                            if candidate and candidate == previous:
                                kind = candidate
                                break
                            previous = candidate
                    if not kind:
                        raise FleetManagementError('未能唯一识别角色详情舰种，保留待完成换装记录')
                    code = self.codes.get(kind)
                    if code:
                        logger.attr('换入舰船装备方案', f"{fleet}-{job['slot']} {SHIP_TYPES[kind]}")
                        self._code_enter()
                        if not self._code_apply(code=None):
                            raise FleetManagementError('换入舰船清空装备失败，保留待完成换装记录')
                        self._code_enter()
                        if not self._code_apply(code=code):
                            raise FleetManagementError('换入舰船装备码应用失败，保留待完成换装记录')
                    else:
                        logger.info(f'{SHIP_TYPES[kind]}未填写装备码，保留换入舰船当前装备并继续')
                    self._back_to_details()
                pending.pop(key)
                self.config.cross_set(EQUIPMENT_PATH + '.State', pending)
            self._back_to_formation()
