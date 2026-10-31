"""换船后宅联动的截图识别、逐次选中、失败跳过和出击顺序；不连接账号。"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.campaign.fleet_rotation import preflight_rotation
from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange
from module.dorm.assets import (DORM_FLEET_CANCEL, DORM_FLEET_CHECK, DORM_FLEET_CONFIRM,
                               DORM_FLEET_REST, DORM_FLEET_TRAIN, OCR_DORM_FLEET_AMOUNT)
from module.dorm.fleet_rotation import (DORM_PATH, DormArrangeError, DormCounter, DormFleetScanner,
                                       FleetRotationDorm, rest_after_rotation, rest_fleet)
from module.exception import GameStuckError
from module.ocr.al_ocr import AlOcr, OcrSettings
from module.retire.assets import DOCK_CHECK, DOCK_SELECTED
from module.ui.assets import DORM_CHECK
from module.ui.page import page_main
import tests.test_fleet_rotation as rotation_cases
import tests.test_fleet_training as training_cases
from tests.test_api import fixture


FIXTURES = Path(__file__).parent / 'fixtures/fleet_dorm'


def frame(name):
    return np.array(Image.open(FIXTURES / f'{name}.png').convert('RGB'))


def config(values=None, order='fleet1_mob_fleet2_boss'):
    values = values or {}
    return SimpleNamespace(cross_get=lambda path, default=None: deepcopy(values.get(path, default)),
                           Fleet_FleetOrder=order, Fleet_Fleet1=4, Fleet_Fleet2=5)


class DormRuleTests(unittest.TestCase):
    def test_generated_defaults_and_real_config_save_validate_only_the_new_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ConfigService(fixture(directory))
            before = service.get('testpilot')
            self.assertEqual({'Enabled': False, 'Floor': 'floor_1'}, before['values']['FleetInfo']['FleetDorm'])
            after = service.patch('testpilot', before['revision'],
                                  [ConfigChange(path=DORM_PATH + '.Enabled', value=True),
                                   ConfigChange(path=DORM_PATH + '.Floor', value='floor_2')])
            self.assertEqual({'Enabled': True, 'Floor': 'floor_2'}, after['values']['FleetInfo']['FleetDorm'])
            self.assertEqual(before['values']['FleetInfo']['FleetRotation'], after['values']['FleetInfo']['FleetRotation'])
            with self.assertRaises(ApiError):
                service.patch('testpilot', after['revision'],
                              [ConfigChange(path=DORM_PATH + '.Floor', value='floor_3')])

    def test_disabled_and_no_actual_swap_do_not_enter_dorm(self):
        manager = Mock(config=config())
        with patch('module.dorm.fleet_rotation.FleetRotationDorm') as factory:
            rest_after_rotation(manager, {4, 5})
            manager.config = config({DORM_PATH + '.Enabled': True})
            rest_after_rotation(manager, set())
        factory.assert_not_called()
        manager._commit_formation.assert_not_called()

    def test_priority_uses_actual_fleet_and_all_battle_mob_boss_roles(self):
        for order, expected in [('fleet1_all_fleet2_standby', 4), ('fleet1_standby_fleet2_all', 5),
                                ('fleet1_mob_fleet2_boss', 4), ('fleet1_boss_fleet2_mob', 5)]:
            cfg = config({DORM_PATH + '.Enabled': True}, order)
            self.assertEqual(expected, rest_fleet(cfg, {4, 5}))
        self.assertEqual(4, rest_fleet(config({DORM_PATH + '.Enabled': True}, 'fleet1_boss_fleet2_mob'), {4}))

    def test_partial_training_swap_does_not_arrange_an_incomplete_round(self):
        manager = Mock(config=config({DORM_PATH + '.Enabled': True,
                                      'FleetInfo.FleetTraining.State': {'pending': {'1:4': {}}}}))
        with patch('module.dorm.fleet_rotation.FleetRotationDorm') as factory:
            rest_after_rotation(manager, {4})
        factory.assert_not_called()
        manager._commit_formation.assert_not_called()

    def test_counter_rejects_unknown_truncated_and_over_capacity_values(self):
        counter = DormCounter(DOCK_SELECTED)
        for text in ('', '66', '7/6', '6/0', '0/0', '16/6', '6/6x', '6//6'):
            self.assertIsNone(counter.after_process(text))
        self.assertEqual((0, 6), counter.after_process('0/6'))
        self.assertEqual((4, 6), counter.after_process('4/6'))


class DormImageTests(unittest.TestCase):
    def test_selected_markers_and_fleet_four_after_scrolling(self):
        scanner = DormFleetScanner()
        image = frame('picker_selected')
        cards = scanner.scan(image)
        self.assertEqual([(col, 75) for col in range(1, 7)],
                         [(card.col, card.y) for card in cards if scanner.selected(image, card)])
        image = frame('picker_scrolled')
        cards = scanner.scan(image)
        self.assertEqual([(4, 175), (5, 175), (6, 175)],
                         [(card.col, card.y) for card in cards if card.fleet == 4])
        self.assertFalse(any(scanner.selected(image, card) for card in cards))

    def test_active_floor_and_real_panel_and_dock_counters(self):
        engine = AlOcr(name='azur_lane', settings=OcrSettings('onnx', 'cpu', False, 'standard'),
                       save_debug_images=False)
        with patch('module.ocr.ocr.OCR_MODEL', SimpleNamespace(azur_lane=engine)):
            for name, active, inactive in [('training', DORM_FLEET_TRAIN, DORM_FLEET_REST),
                                            ('rest', DORM_FLEET_REST, DORM_FLEET_TRAIN)]:
                image = frame(name)
                self.assertTrue(active.match_template_color(image, offset=(3, 3)))
                self.assertFalse(inactive.match_template_color(image, offset=(3, 3)))
                self.assertEqual((6, 6), DormCounter(OCR_DORM_FLEET_AMOUNT).ocr(image))
            self.assertEqual((6, 6), DormCounter(DOCK_SELECTED).ocr(frame('picker_selected')))


class DormSelectionTests(unittest.TestCase):
    def controller(self, pages, selected=()):
        tool = FleetRotationDorm.__new__(FleetRotationDorm)
        self.page, self.selected, self.clicks = 0, set(selected), []
        tool.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        tool.appear = Mock(return_value=True)
        tool.handle_popup_cancel = Mock(return_value=False)
        tool.loop = lambda **kwargs: iter(range(80))
        def click(button):
            x = button.area[0] - 25
            card = next(card for card in pages[self.page] if card.x == x)
            self.clicks.append(card.key)
            self.selected.symmetric_difference_update({card.key})
        def swipe(*args, **kwargs):
            self.page = min(self.page + 1, len(pages) - 1)
        tool.device.click, tool.device.swipe = Mock(side_effect=click), Mock(side_effect=swipe)
        scanner = Mock()
        scanner.scan.side_effect = lambda image: pages[self.page]
        scanner.selected.side_effect = lambda image, card: card.key in self.selected
        scroll = Mock(length=100)
        scroll.cal_position.side_effect = lambda main: self.page / max(1, len(pages) - 1)
        self.enterContext(patch('module.dorm.fleet_rotation.DormFleetScanner', return_value=scanner))
        self.enterContext(patch('module.dorm.fleet_rotation.Scroll', return_value=scroll))
        counter = self.enterContext(patch('module.dorm.fleet_rotation.DormCounter')).return_value
        counter.ocr.side_effect = lambda image: (len(self.selected), 6)
        return tool, counter

    @staticmethod
    def card(key, col, fleet):
        return SimpleNamespace(key=key, x=90 + col * 165, y=75, fleet=fleet)

    def test_clear_every_old_selection_before_choosing_the_target_number(self):
        cards = [self.card('old1', 0, 1), self.card('old2', 1, 5),
                 self.card('target1', 2, 4), self.card('target2', 3, 4)]
        tool, _ = self.controller([cards], selected=('old1', 'old2'))
        tool._selection(4, 0, 6, clear=True)
        self.assertEqual(set(), self.selected)
        tool._selection(4, 2, 6)
        self.assertEqual({'target1', 'target2'}, self.selected)
        self.assertEqual(['old1', 'old2', 'target1', 'target2'], self.clicks)

    def test_overlap_and_other_fleets_are_not_toggled_or_selected(self):
        first = self.card('target1', 0, 4)
        second = self.card('target2', 1, 4)
        other = self.card('other', 2, 5)
        tool, _ = self.controller([[first, other], [first, second, other]])
        tool._selection(4, 2, 6)
        self.assertEqual(['target1', 'target2'], self.clicks)
        tool.device.swipe.assert_called_once()

    def test_click_without_verified_count_change_is_not_repeated(self):
        tool, _ = self.controller([[self.card('target', 0, 4)]])
        tool.device.click.side_effect = None
        with patch('module.dorm.fleet_rotation.Timer') as timer:
            timer.return_value.start.return_value.reached.return_value = True
            with self.assertRaisesRegex(DormArrangeError, '未确认已选数量变化'):
                tool._selection(4, 1, 6)
        tool.device.click.assert_called_once()

    def test_unknown_count_does_not_clear_or_select_any_card(self):
        tool, counter = self.controller([[self.card('target', 0, 4)]])
        counter.ocr.return_value, counter.ocr.side_effect = None, None
        with self.assertRaises(DormArrangeError):
            tool._selection(4, 1, 6)
        tool.device.click.assert_not_called()


class DormFlowTests(unittest.TestCase):
    def tool(self, *, capacity=6, floor='floor_2'):
        tool = FleetRotationDorm.__new__(FleetRotationDorm)
        formation = {category: {'4': [dict(slot=slot, occupied=True) for slot in slots]}
                     for category, slots in [('main', (1, 2, 3)), ('vanguard', (4, 5, 6))]}
        tool.config = config({DORM_PATH + '.Floor': floor, 'FleetInfo.FleetInfo.Result': formation})
        tool.device = SimpleNamespace(image=frame('picker_selected'))
        tool.loop = lambda **kwargs: iter(range(8))
        for name in ('ui_ensure', '_enter_floor', '_open_picker', 'dock_favourite_set',
                     'dock_filter_set', '_top', '_selection', '_confirm', '_leave'):
            setattr(tool, name, Mock())
        self.enterContext(patch('module.dorm.fleet_rotation.server.server', 'cn'))
        counter = self.enterContext(patch('module.dorm.fleet_rotation.DormCounter')).return_value
        counter.ocr.return_value = (capacity, capacity)
        return tool

    def test_both_floors_clear_then_select_vanguard_and_main_including_fixed_slots(self):
        for floor in ('floor_1', 'floor_2'):
            with self.subTest(floor=floor):
                tool = self.tool(floor=floor)
                self.assertTrue(tool.run(4))
                tool._enter_floor.assert_called_once_with(floor)
                self.assertEqual([((4, 0, 6), {'clear': True}), ((4, 3, 6), {}), ((4, 6, 6), {})],
                                 tool._selection.call_args_list)
                self.assertEqual(['all', 'vanguard', 'main'],
                                 [call.kwargs['index'] for call in tool.dock_filter_set.call_args_list])
                tool._confirm.assert_called_once_with(floor, 6)
                tool._leave.assert_called_once()

    def test_capacity_locked_floor_and_unconfirmed_selection_skip_and_continue(self):
        tool = self.tool(capacity=5)
        self.assertFalse(tool.run(4))
        tool._selection.assert_not_called()
        tool._confirm.assert_not_called()
        tool._leave.assert_called_once()
        for method in ('_enter_floor', '_selection', '_confirm'):
            tool = self.tool()
            getattr(tool, method).side_effect = DormArrangeError('无法确认')
            self.assertFalse(tool.run(4))
            tool._leave.assert_called_once()

    def test_real_device_errors_are_not_hidden_as_a_dorm_skip(self):
        tool = self.tool()
        tool._enter_floor.side_effect = GameStuckError('设备交互失败')
        with self.assertRaises(GameStuckError):
            tool.run(4)

    def test_skip_cancels_picker_then_closes_panel_before_returning_home(self):
        tool = FleetRotationDorm.__new__(FleetRotationDorm)
        tool.loop = lambda **kwargs: iter(range(8))
        stages = iter(('picker', 'panel', 'dorm'))
        stage = {'value': ''}
        def next_stage(**kwargs):
            for current in stages:
                stage['value'] = current
                yield None
        tool.loop = next_stage
        tool.is_in_main = Mock(return_value=False)
        tool.handle_popup_cancel = Mock(return_value=False)
        tool.appear = lambda button, **kwargs: ((stage['value'] == 'picker' and button == DOCK_CHECK)
                                                or (stage['value'] == 'dorm' and button == DORM_CHECK))
        tool.appear_then_click, tool.ui_ensure = Mock(return_value=True), Mock()
        tool._leave()
        self.assertEqual([DORM_FLEET_CANCEL, DORM_FLEET_CHECK],
                         [call.args[0] for call in tool.appear_then_click.call_args_list])
        tool.ui_ensure.assert_called_once_with(page_main)


class DormIntegrationTests(unittest.TestCase):
    def fixture(self, training=False):
        case = (training_cases.TrainingIntegrationTests if training else rotation_cases.RotationIntegrationTests)()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.manager.config, case.manager.device = case.config, case.runner.device
        case.config.cross_set(DORM_PATH + '.Enabled', True)
        return case

    def test_morale_swap_finishes_all_slots_and_equipment_before_dorm_and_original_task(self):
        case = self.fixture()
        events = []
        rotate = case.manager.rotate_slot.side_effect
        def swap(*args):
            events.append('swap')
            return rotate(*args)
        case.manager.rotate_slot.side_effect = swap
        case.campaign.ensure_campaign_ui.side_effect = lambda **kwargs: events.append('task')
        case.config.cross_set('FleetInfo.FleetEquipment.Enabled', True)
        with patch('module.campaign.fleet_rotation.FleetRotationEquipment') as equipment, \
                patch('module.dorm.fleet_rotation.FleetRotationDorm') as dorm:
            equipment.return_value.apply_pending.side_effect = lambda: events.append('equipment')
            dorm.return_value.run.side_effect = lambda fleet: events.append(f'dorm:{fleet}')
            self.assertTrue(preflight_rotation(case.runner, 5, 'normal'))
        self.assertEqual(['swap', 'swap', 'swap', 'equipment', 'dorm:1', 'task'], events)
        case.manager._commit_formation.assert_called_once()

    def test_training_without_morale_uses_the_same_dorm_hook_and_no_swap_does_not_enter(self):
        case = self.fixture(training=True)
        case.configure(mood=False, level=True)
        with patch('module.dorm.fleet_rotation.FleetRotationDorm') as dorm:
            self.assertTrue(preflight_rotation(case.runner, 5, 'normal'))
            dorm.assert_not_called()
            case.profile[case.names[1, 4]]['level'] = 120
            case.runner._fleet_rotation_context['training_checked_at'] = None
            from module.runtime.fleet_training import INTERVAL_PATH
            case.config.cross_set(INTERVAL_PATH, 0)
            self.assertTrue(preflight_rotation(case.runner, 5, 'normal'))
            dorm.return_value.run.assert_called_once_with(1)

    def test_skipping_dorm_does_not_repeat_or_undo_swaps_or_block_task(self):
        case = self.fixture()
        with patch('module.dorm.fleet_rotation.FleetRotationDorm') as dorm:
            dorm.return_value.run.return_value = False
            self.assertTrue(preflight_rotation(case.runner, 5, 'normal'))
        self.assertEqual(3, case.manager.rotate_slot.call_count)
        self.assertFalse(case.state()['1'].get('rescan'))
        case.campaign.ensure_campaign_ui.assert_called_once()
        case.campaign.map_get_info.assert_called_once()
