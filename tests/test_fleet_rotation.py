"""智能换船规则、真实配置事务、出击前联动与中断恢复；不连接设备。"""

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.api.config_service import ConfigService
from module.api.protocol import ApiError, ConfigChange
from module.campaign.fleet_rotation import preflight_rotation
from module.combat.emotion import Emotion
from module.config.config import AzurLaneConfig
from module.exception import CampaignEnd, FleetManagementError, GameStuckError, ScriptEnd
from module.runtime.fleet_rotation import (ROTATION_PATH, begin_round, calibration_changes,
                                         minimum_morale, parse_rules, supported)
from tests.test_api import fixture


RULE = {'slots': [3, 4, 5], 'sources': [3, 4, 5, 6]}


class RuleTests(unittest.TestCase):
    def test_defaults_and_strict_rule_validation(self):
        self.assertEqual(parse_rules('{}'), {})
        self.assertEqual(parse_rules({'1': {'slots': [5, 3], 'sources': [6, 3]}}),
                         {'1': {'slots': [3, 5], 'sources': [3, 6]}})
        for value in ('[]', 'invalid', {'7': RULE}, {'1': {'slots': [True]}},
                      {'1': {'sources': [1]}}, {'1': {'slots': [3, 3]}}, {'1': {'unknown': []}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_rules(value)

    def test_one_source_per_round_cycles_and_resumes_completed_slots(self):
        state = {}
        order = []
        for _ in range(5):
            progress = begin_round(RULE, state)
            order.append(progress['source'])
            state = {'last_source': progress['source']}
        self.assertEqual(order, [3, 4, 5, 6, 3])
        progress = begin_round(RULE, {'last_source': 6})
        progress['done'] = [3, 4]
        self.assertEqual(begin_round(RULE, progress)['done'], [3, 4])
        progress['pending'] = {'slot': 5}
        with self.assertRaises(ValueError):
            begin_round(RULE, progress)

    def test_scope_excludes_difficult_other_task_and_archives(self):
        for task in ('Main', 'Main2', 'Main3', 'Event', 'Event2', 'EventA', 'EventD'):
            self.assertTrue(supported(task, 'normal', 'campaign_main' if task.startswith('Main') else 'event_20261022_cn'))
        for task, mode, folder in (('Main', 'hard', 'campaign_main'), ('GemsFarming', 'normal', 'campaign_main'),
                                   ('WarArchives', 'normal', 'war_archives_20261022_cn'), ('OpsiDaily', 'normal', 'event_a')):
            self.assertFalse(supported(task, mode, folder))
        for stage in ('c1', 'd3_3', 'ht2', 'ex', 'ex_ex'):
            self.assertFalse(supported('Event', 'normal', 'event_20261022_cn', stage))
        for stage in ('sp', 'sp3', 'ex_sp'):
            self.assertTrue(supported('Event', 'normal', 'event_20261022_cn', stage))

    def test_calibration_keeps_unknown_as_unknown_and_does_not_map_hard_event_default_mode(self):
        fields = {'Fleet1Recover': 'not_in_dormitory', 'Fleet1Oath': False, 'Fleet1Onsen': False}
        data = {task: {'Campaign': {'Mode': 'normal', 'Name': name}, 'Fleet': {'Fleet1': 1},
                       'Emotion': fields} for task, name in (('Main', '12-4'), ('Event', 'a1'), ('EventD', 'd3'))}
        changes = calibration_changes(data, {1: {3: None, 4: 150}}, datetime(2026, 10, 22))
        self.assertIsNone(changes['Main.Emotion.Fleet1RecoveryState'])
        self.assertNotIn('Main.Emotion.Fleet1Value', changes)
        self.assertNotIn('EventD.Emotion.Fleet1RecoveryState', changes)
        valid = calibration_changes(data, {1: {3: 100, 4: 0}}, datetime(2026, 10, 22))
        self.assertEqual(valid['Main.Emotion.Fleet1Value'], 0)
        self.assertEqual(valid['Event.Emotion.Fleet1Value'], 0)

    def test_minimum_includes_unchanged_slots_zero_and_unknown(self):
        self.assertEqual(minimum_morale({1: 150, 3: 2, 4: 149}), 2)
        self.assertEqual(minimum_morale({4: 0, 5: 150}), 0)
        self.assertIsNone(minimum_morale({3: None, 4: 150}))
        self.assertIsNone(minimum_morale({}))


class RotationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.service = ConfigService(fixture(directory))
        self.path = self.service.path('testpilot')
        for target in ('module.config.config.filepath_config', 'module.config.config_updater.filepath_config'):
            self.stack.enter_context(patch(target, return_value=str(self.path)))
        self.config = AzurLaneConfig('testpilot', task='Main')
        self.config.cross_set_many({ROTATION_PATH + '.Enabled': True, ROTATION_PATH + '.Rules': json.dumps({'1': RULE}),
                                    'Main.Fleet.FleetOrder': 'fleet1_all_fleet2_standby'})
        self.config.set_record(Emotion_Fleet1Value=2, Emotion_Fleet2Value=150)
        self.campaign = SimpleNamespace(config=self.config, emotion=Emotion(self.config),
                                        ensure_campaign_ui=Mock(), map_get_info=Mock())
        self.runner = SimpleNamespace(config=self.config, campaign=self.campaign, folder='campaign_main',
                                      stage='12-4', device=object())
        self.manager = Mock()
        self.manager.highest_morale_slot.side_effect = lambda source, fleet, slot: 1 if slot <= 3 else 4
        self.manager.read_fleet_morale.side_effect = self.readings
        self.factory = self.stack.enter_context(patch('module.campaign.fleet_rotation.FleetManagement', return_value=self.manager))

    def readings(self, fleet):
        progress = self.config.cross_get(ROTATION_PATH + '.State', {}).get('1', {})
        return {slot: 150 if fleet == 1 and progress.get('source') else 2 for slot in range(1, 7)}

    def state(self):
        return self.service.read_json(self.path)['FleetInfo']['FleetRotation']['State']

    def test_real_config_round_saves_three_swaps_and_only_one_reserve_then_calibrates(self):
        self.config.cross_set_many({'Event.Fleet.Fleet1': 1, 'Event.Campaign.Mode': 'normal', 'Event.Campaign.Name': 'a1',
                                    'Main2.Fleet.Fleet1': 3, 'Main2.Campaign.Mode': 'normal',
                                    'Main3.Fleet.Fleet1': 1, 'Main3.Campaign.Mode': 'hard'})
        hard_before = deepcopy(self.service.read_json(self.path)['Main3']['Emotion'])
        self.assertTrue(preflight_rotation(self.runner, 5, 'normal'))
        self.assertEqual(self.manager.swap_slots.call_args_list[0].args, (3, 1, 1, 3))
        self.assertEqual([call.args for call in self.manager.swap_slots.call_args_list],
                         [(3, 1, 1, 3), (3, 4, 1, 4), (3, 4, 1, 5)])
        self.assertEqual(self.state()['1']['last_source'], 3)
        data = self.service.read_json(self.path)
        self.assertEqual(data['Main']['Emotion']['Fleet1Value'], 150)
        self.assertEqual(data['Event']['Emotion']['Fleet1Value'], 150)
        self.assertEqual(data['Main2']['Emotion']['Fleet1Value'], 2)
        self.assertEqual(data['Main3']['Emotion'], hard_before)
        self.assertIsInstance(data['Main']['Emotion']['Fleet1RecoveryState'], dict)
        self.campaign.ensure_campaign_ui.assert_called_once_with(name='12-4', mode='normal', skip_first_screenshot=False)
        self.campaign.map_get_info.assert_called_once()
        # 回到出击流程后仍执行真实 Emotion 的预检，而不是绕过情绪控制。
        self.campaign.emotion.check_reduce(5)

    def test_repeated_low_morale_rotations_use_3_4_5_6_then_3(self):
        order = []
        for _ in range(5):
            self.config.set_record(Emotion_Fleet1Value=2)
            self.manager.swap_slots.reset_mock()
            preflight_rotation(self.runner, 5, 'normal')
            order.append(self.manager.swap_slots.call_args_list[0].args[0])
            self.assertEqual(self.manager.swap_slots.call_count, 3)
        self.assertEqual(order, [3, 4, 5, 6, 3])

    def test_new_event_task_loads_last_reserve_from_disk_and_uses_the_next_one(self):
        preflight_rotation(self.runner, 5, 'normal')
        self.config.cross_set_many({'Event.Campaign.Mode': 'normal', 'Event.Campaign.Name': 'a1',
                                    'Event.Fleet.FleetOrder': 'fleet1_all_fleet2_standby'})
        event_config = AzurLaneConfig('testpilot', task='Event')
        event_config.set_record(Emotion_Fleet1Value=2)
        self.campaign.config = event_config
        self.campaign.emotion = Emotion(event_config)
        self.runner.config = event_config
        self.runner.folder, self.runner.stage = 'event_20261022_cn', 'a1'
        self.manager.swap_slots.reset_mock()
        # 模拟设备读取当前任务的最新持久化进度，而不是原Main对象的旧快照。
        self.manager.read_fleet_morale.side_effect = lambda fleet: {
            slot: 150 if fleet == 1 and event_config.cross_get(ROTATION_PATH + '.State', {}).get('1', {}).get('source')
            else 2 for slot in range(1, 7)}
        preflight_rotation(self.runner, 5, 'normal')
        self.assertEqual(self.manager.swap_slots.call_args_list[0].args[0], 4)
        self.assertEqual(self.state()['1']['last_source'], 4)

    def test_one_round_still_low_waits_in_original_emotion_check_instead_of_swapping_again(self):
        self.manager.read_fleet_morale.side_effect = lambda fleet: dict.fromkeys(range(1, 7), 2)
        preflight_rotation(self.runner, 5, 'normal')
        with self.assertRaises(ScriptEnd):
            self.campaign.emotion.check_reduce(5)
        self.assertEqual(self.manager.swap_slots.call_count, 3)
        self.assertEqual(self.state()['1']['last_source'], 3)

    def test_real_campaign_run_and_rotation_service_share_the_actual_emotion_preflight(self):
        from module.campaign.campaign_base import CampaignBase
        campaign = CampaignBase.__new__(CampaignBase)
        campaign.__dict__.update(vars(self.campaign))
        self.runner.campaign = campaign
        campaign._map_battle = 5
        campaign.ENTRANCE = SimpleNamespace(area=None, button=(1, 2, 3, 4))
        campaign._fleet_rotation_preflight = lambda battle: preflight_rotation(self.runner, battle, 'normal')
        campaign.enter_map = Mock()
        campaign.map_is_auto_search = True
        campaign.MAP = object()
        campaign.lv_reset = campaign.lv_get = Mock()
        campaign.auto_search_execute_a_battle = Mock(side_effect=CampaignEnd)
        campaign.AUTO_SEARCH_WITHDRAW = False
        self.assertTrue(campaign.run())
        self.assertEqual(self.manager.swap_slots.call_count, 3)
        self.assertEqual(campaign.emotion.fleet_1.value, 150)
        campaign.enter_map.assert_called_once()

    def test_disabled_and_hard_modes_do_not_construct_game_controller(self):
        self.config.cross_set(ROTATION_PATH + '.Enabled', False)
        self.assertFalse(preflight_rotation(self.runner, 5, 'normal'))
        self.config.cross_set(ROTATION_PATH + '.Enabled', True)
        self.assertFalse(preflight_rotation(self.runner, 5, 'hard'))
        self.runner.folder = 'war_archives_20261022_cn'
        self.assertFalse(preflight_rotation(self.runner, 5, 'normal'))
        self.factory.assert_not_called()

    def test_healthy_morale_keeps_fleet_and_does_not_advance_reserve(self):
        self.config.set_record(Emotion_Fleet1Value=150)
        self.assertFalse(preflight_rotation(self.runner, 5, 'normal'))
        self.manager.swap_slots.assert_not_called()
        self.manager.read_fleet_morale.assert_not_called()
        self.assertEqual(self.state(), {})
        self.factory.assert_not_called()

    def test_low_task_ledger_triggers_swap_without_pre_read_ocr_veto(self):
        self.manager.read_fleet_morale.side_effect = lambda fleet: dict.fromkeys(range(1, 7), 150)
        self.assertTrue(preflight_rotation(self.runner, 5, 'normal'))
        self.assertEqual(self.manager.swap_slots.call_count, 3)
        # 只在互换后补读双方，不允许前置OCR覆盖已触发的低心情条件。
        self.assertEqual([call.args[0] for call in self.manager.read_fleet_morale.call_args_list], [1, 3])

    def test_unknown_optional_reference_in_ignore_mode_does_not_block_original_farming(self):
        self.config.cross_set('Main.Emotion.Mode', 'ignore')
        self.config.set_record(Emotion_Fleet1Value=150)
        self.manager.read_fleet_morale.side_effect = lambda fleet: {1: None}
        self.assertTrue(preflight_rotation(self.runner, 5, 'normal'))
        self.manager.swap_slots.assert_not_called()
        self.campaign.emotion.check_reduce(5)

    def test_ignore_calculation_mode_uses_fresh_measurement_instead_of_stale_healthy_value(self):
        self.config.cross_set('Main.Emotion.Mode', 'ignore')
        self.config.set_record(Emotion_Fleet1Value=150)
        self.assertTrue(preflight_rotation(self.runner, 5, 'normal'))
        self.assertEqual(self.manager.swap_slots.call_count, 3)

    def test_double_book_cost_and_runtime_book_flag_survive_emotion_refresh(self):
        self.config.cross_set('Main.Emotion.Fleet1Control', 'prevent_red_face')
        self.config.set_record(Emotion_Fleet1Value=15)
        self.assertFalse(preflight_rotation(self.runner, 5, 'normal'))
        self.campaign.emotion.map_is_2x_book = True
        self.assertTrue(preflight_rotation(self.runner, 5, 'normal'))
        self.assertTrue(self.campaign.emotion.map_is_2x_book)

    def test_zero_battle_role_does_not_rotate_even_if_its_morale_is_low(self):
        self.config.cross_set('Main.Fleet.FleetOrder', 'fleet1_mob_fleet2_boss')
        self.assertFalse(preflight_rotation(self.runner, 1, 'normal'))
        self.manager.read_fleet_morale.assert_not_called()
        self.manager.swap_slots.assert_not_called()

    def test_interrupted_swap_remains_pending_and_is_never_automatically_repeated(self):
        self.manager.swap_slots.side_effect = GameStuckError('模拟进程中断，无法确认交换结果')
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        self.assertEqual(self.state()['1']['pending'], {'slot': 3, 'source_slot': 1})
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        self.manager.swap_slots.assert_called_once()

    def test_other_task_using_an_unfinished_reserve_does_not_farm_with_stale_morale(self):
        progress = begin_round(RULE, {})
        progress['pending'] = {'slot': 3, 'source_slot': 1}
        self.config.cross_set_many({ROTATION_PATH + '.State': {'1': progress}, 'Main.Fleet.Fleet1': 3})
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        self.factory.assert_not_called()

    def test_disabling_during_a_round_stops_after_current_swap_and_saves_completed_slots(self):
        self.manager.swap_slots.side_effect = lambda *args: self.config.cross_set(ROTATION_PATH + '.Enabled', False)
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.swap_slots.assert_called_once_with(3, 1, 1, 3)
        self.assertEqual(self.state()['1']['done'], [3])
        self.assertEqual(self.state()['1']['source'], 3)
        self.assertIsNone(self.state()['1']['pending'])

    def test_completed_swaps_with_unknown_final_morale_retry_only_measurement(self):
        original = self.readings
        self.manager.read_fleet_morale.side_effect = lambda fleet: ({1: None, 4: 150} if fleet == 1 and
            self.config.cross_get(ROTATION_PATH + '.State', {}).get('1', {}).get('source') else original(fleet))
        with self.assertRaises(FleetManagementError):
            preflight_rotation(self.runner, 5, 'normal')
        self.assertEqual(self.state()['1']['done'], [3, 4, 5])
        self.manager.swap_slots.reset_mock()
        self.manager.read_fleet_morale.side_effect = original
        preflight_rotation(self.runner, 5, 'normal')
        self.manager.swap_slots.assert_not_called()
        self.assertEqual(self.state()['1']['last_source'], 3)

    def test_api_persists_switch_rules_and_reset_but_rejects_invalid_rule_or_state_write(self):
        changes = [ConfigChange(path=ROTATION_PATH + '.Enabled', value=False),
                   ConfigChange(path=ROTATION_PATH + '.Rules', value=json.dumps({'1': RULE}))]
        saved = self.service.patch('testpilot', None, changes)['values']['FleetInfo']['FleetRotation']
        self.assertFalse(saved['Enabled'])
        self.assertEqual(json.loads(saved['Rules'])['1'], RULE)
        for path, value in ((ROTATION_PATH + '.Rules', '{"1":{"sources":[1]}}'),
                            (ROTATION_PATH + '.State', {'1': {'last_source': 6}})):
            with self.assertRaises(ApiError):
                self.service.patch('testpilot', None, [ConfigChange(path=path, value=value)])
        self.service.patch('testpilot', None, [ConfigChange(path=ROTATION_PATH + '.State', value={})])
        self.assertEqual(self.state(), {})
        self.assertNotIn('FleetRotation', self.service.args)


class CampaignEntryTests(unittest.TestCase):
    def test_real_campaign_base_checks_rotation_before_emotion_and_enter_map(self):
        from module.campaign.campaign_base import CampaignBase
        campaign = CampaignBase.__new__(CampaignBase)
        campaign.config = SimpleNamespace(Campaign_Mode='normal')
        campaign.ENTRANCE = SimpleNamespace(area=None, button=(1, 2, 3, 4))
        campaign._map_battle = 5
        order = []
        campaign.map_get_info = lambda: order.append('map')
        campaign._fleet_rotation_preflight = lambda battle: order.append(('rotate', battle))
        campaign.emotion = SimpleNamespace(check_reduce=lambda battle: order.append(('emotion', battle)))
        campaign.enter_map = lambda *args, **kwargs: order.append('enter')
        campaign.map_is_auto_search = True
        campaign.MAP = object()
        campaign.lv_reset = campaign.lv_get = Mock()
        campaign.auto_search_execute_a_battle = Mock(side_effect=CampaignEnd)
        campaign.AUTO_SEARCH_WITHDRAW = False
        self.assertTrue(campaign.run())
        self.assertEqual(order, ['map', ('rotate', 5), ('emotion', 5), 'enter'])


if __name__ == '__main__':
    unittest.main()
