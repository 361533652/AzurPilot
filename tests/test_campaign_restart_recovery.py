"""异常重启续接的离线状态回放；不访问模拟器或真实配置。"""

import copy
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from alas import AzurLaneAutoScript
from module.campaign.campaign_base import CampaignBase
from module.campaign.recovery import CampaignRecovery, campaign_recovery_scope, recovery_settings
from module.campaign.run import CampaignRun
from module.combat.assets import BATTLE_STATUS_A, BATTLE_STATUS_S, EXP_INFO_A, EXP_INFO_S, OPTS_INFO_D
from module.combat.result import CombatResult
from module.exception import CampaignEnd, GameNotRunningError, GameStuckError, ScriptEnd
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_OFF, AUTO_SEARCH_MAP_OPTION_ON, AUTO_SEARCH_MENU_EXIT
from module.handler.login import LoginHandler
from module.map.assets import FLEET_NUM_1, FLEET_NUM_2, MAP_OFFENSIVE, WITHDRAW
from module.ui.page import page_campaign
from tests.test_auto_search_defeat import ScreenReplay, retreat_frames, screen


def make_config():
    return SimpleNamespace(
        config_name='isolated-recovery', task=SimpleNamespace(command='Main'),
        Campaign_Name='16-4', Campaign_Event='campaign_main', Campaign_Mode='normal',
        Campaign_ContinueAfterRestart=True, Campaign_UseClearMode=True, Campaign_UseAutoSearch=True,
        MAP_IS_ONE_TIME_STAGE=False, modified={}, update=Mock(),
        data={'Main': {'Campaign': {'Name': '16-4', 'ContinueAfterRestart': True},
                       'Emotion': {'Mode': 'calculate', 'Fleet1Value': 148}},
              'Alas': {'Emulator': {'Serial': 'isolated-device', 'ServerName': 'cn'}},
              'General': {'PublicEmotion': {'Enable': False}}})


def make_saved_runner():
    campaign = object.__new__(CampaignBase)
    campaign.config = make_config()
    campaign.map_is_clear_mode = True
    campaign.map_is_auto_search = True
    campaign._auto_search_phase = 'moving'
    campaign._recovery_map_generation = 1
    campaign.combat_result = CombatResult()
    runner = object.__new__(CampaignRun)
    runner.config = make_config()
    runner.device = Mock()
    runner.campaign = campaign
    runner.name = 'campaign_16_4'
    runner.folder = 'campaign_main'
    return runner


class RecoveryCredentialTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.campaign.recovery.logger'))
        self.enterContext(patch('module.statistics.resource_tracking.observe_campaign_end'))

    def test_capture_requires_all_opt_in_and_live_map_flags(self):
        for owner, name in (
                ('config', 'Campaign_ContinueAfterRestart'), ('config', 'Campaign_UseClearMode'),
                ('config', 'Campaign_UseAutoSearch'), ('campaign', 'map_is_clear_mode'),
                ('campaign', 'map_is_auto_search')):
            runner = make_saved_runner()
            target = runner.campaign.config if owner == 'config' else runner.campaign
            setattr(target, name, False)
            with self.subTest(name=name):
                self.assertIsNone(CampaignRecovery.capture(runner))
        self.assertIsNotNone(CampaignRecovery.capture(make_saved_runner()))

    def test_no_checkpoint_special_map_or_pending_write_cannot_resume(self):
        for attr, value in (('_auto_search_phase', None), ('AUTO_SEARCH_WITHDRAW', True)):
            runner = make_saved_runner()
            setattr(runner.campaign, attr, value)
            self.assertIsNone(CampaignRecovery.capture(runner))
        runner = make_saved_runner()
        runner.campaign.config.MAP_IS_ONE_TIME_STAGE = True
        self.assertIsNone(CampaignRecovery.capture(runner))
        runner = make_saved_runner()
        runner.campaign.combat_result.emotion_pending = True
        self.assertIsNone(CampaignRecovery.capture(runner))

    def test_credential_is_one_task_one_config_one_device_and_expires(self):
        runner = make_saved_runner()
        recovery = CampaignRecovery.capture(runner)
        self.assertFalse(recovery.ready)
        self.assertTrue(recovery.matches(make_config()))
        for path in ('task', 'stage', 'morale', 'public', 'device', 'runtime_disable'):
            config = make_config()
            if path == 'task':
                config.task.command = 'Main2'
            elif path == 'stage':
                config.data['Main']['Campaign']['Name'] = '15-4'
            elif path == 'morale':
                config.data['Main']['Emotion']['Fleet1Value'] = 100
            elif path == 'public':
                config.data['General']['PublicEmotion']['Enable'] = True
            elif path == 'device':
                config.data['Alas']['Emulator']['Serial'] = 'another-device'
            else:
                config.Campaign_ContinueAfterRestart = False
            with self.subTest(path=path):
                self.assertFalse(recovery.matches(config))
        with patch('module.campaign.recovery.monotonic', return_value=recovery.created + 601):
            self.assertFalse(recovery.matches(make_config()))

    def test_retry_limit_survives_repeated_capture_but_next_map_has_new_budget(self):
        runner = make_saved_runner()
        recovery = CampaignRecovery.capture(runner)
        for attempt in range(1, 4):
            recovery.attempts = attempt
            replacement = CampaignRecovery.capture(runner, recovery)
            if attempt == 3:
                self.assertIsNone(replacement)
            else:
                self.assertEqual(attempt, replacement.attempts)
                recovery = replacement
        runner.campaign._recovery_map_generation += 1
        self.assertEqual(0, CampaignRecovery.capture(runner, recovery).attempts)

    def test_only_device_failures_capture_and_restart_must_finish_first(self):
        script = object.__new__(AzurLaneAutoScript)
        script.config = make_config()
        script.device = Mock()
        runner = make_saved_runner()
        runner._run = Mock(side_effect=GameNotRunningError('offline'))
        with campaign_recovery_scope(script), patch('module.campaign.run.CampaignRun', return_value=runner):
            with self.assertRaises(GameNotRunningError):
                script._run_campaign()
        recovery = script._campaign_recovery
        self.assertIsNotNone(recovery)
        self.assertFalse(recovery.ready)
        script.delay_due_restart = Mock(return_value=False)
        script.delay_next_restart = Mock()
        with patch('module.handler.login.LoginHandler') as login:
            login.return_value.app_restart.side_effect = GameStuckError('login failed')
            with self.assertRaises(GameStuckError):
                script.restart()
            self.assertFalse(recovery.ready)
            login.return_value.app_restart.side_effect = None
            script.restart()
            self.assertTrue(recovery.ready)
            self.assertEqual(runner.campaign.auto_search_recovery_state,
                             login.return_value.campaign_recovery_check)

    def test_manual_or_incomplete_restart_does_not_reuse_runner(self):
        script = object.__new__(AzurLaneAutoScript)
        script.config = make_config()
        script.device = Mock()
        script._channel_float_done = True
        old = make_saved_runner()
        recovery = CampaignRecovery.capture(old)
        script._campaign_recovery = recovery
        with patch('module.runtime.preview.set_task'), patch('module.campaign.run.CampaignRun') as constructor:
            self.assertTrue(script._run('main'))
            constructor.return_value.run.assert_called_once_with(
                name='16-4', folder='campaign_main', mode='normal')

    def test_completed_restart_reuses_original_runner_once(self):
        script = object.__new__(AzurLaneAutoScript)
        script.config = make_config()
        script.device = Mock()
        script._channel_float_done = True
        runner = make_saved_runner()
        runner._run = Mock(side_effect=[GameNotRunningError('offline'), None])
        runner.run = Mock(wraps=runner.run)
        script.delay_due_restart = Mock(return_value=False)
        script.delay_next_restart = Mock()
        with campaign_recovery_scope(script), patch('module.runtime.preview.set_task'), \
                patch('module.campaign.run.CampaignRun', return_value=runner) as constructor:
            with self.assertRaises(GameNotRunningError):
                script._run_campaign()
            recovery = script._campaign_recovery
            recovery.prepare = Mock(return_value=True)
            with patch('module.handler.login.LoginHandler'):
                script.restart()
            self.assertTrue(script._run('main'))
            self.assertEqual(1, constructor.call_count)
        self.assertEqual(2, runner.run.call_count)
        self.assertTrue(runner._run.call_args.kwargs['resume'])
        recovery.prepare.assert_called_once_with(script.config, script.device)
        self.assertIsNone(script._campaign_recovery)

    def test_repeated_login_failures_discard_recovery_hook_at_limit(self):
        script = object.__new__(AzurLaneAutoScript)
        script.config = make_config()
        script.device = Mock()
        script.delay_due_restart = Mock(return_value=False)
        script.delay_next_restart = Mock()
        script._campaign_recovery = CampaignRecovery.capture(make_saved_runner())
        logins = []

        def login_handler(*args, **kwargs):
            login = SimpleNamespace(app_restart=Mock(side_effect=GameStuckError('login failed')))
            logins.append(login)
            return login

        with patch('module.handler.login.LoginHandler', side_effect=login_handler):
            for _ in range(4):
                with self.assertRaises(GameStuckError):
                    script.restart()
        self.assertIsNone(script._campaign_recovery)
        self.assertTrue(all('campaign_recovery_check' in vars(login) for login in logins[:3]))
        self.assertNotIn('campaign_recovery_check', vars(logins[3]))

    def test_other_task_discards_credential(self):
        script = object.__new__(AzurLaneAutoScript)
        script.config_name = 'isolated-recovery'
        script._campaign_recovery = CampaignRecovery.capture(make_saved_runner())
        script._run = Mock(return_value=True)
        with patch('module.statistics.resource_flow.task_session'):
            script.run('commission')
        self.assertIsNone(script._campaign_recovery)

    def test_resuming_task_defers_main_screen_float_wait(self):
        script = object.__new__(AzurLaneAutoScript)
        script.config = make_config()
        script.device = Mock()
        script._channel_float_done = False
        script.handle_channel_float = Mock()
        script.main = Mock()
        script._campaign_recovery = CampaignRecovery.capture(make_saved_runner())
        script._campaign_recovery.ready = True
        script._campaign_recovery.resume = Mock()
        with patch('module.runtime.preview.set_task'):
            self.assertTrue(script._run('main'))
        script.main.assert_not_called()
        script._resuming_campaign_recovery = None
        script.handle_channel_float.assert_not_called()

    def test_settings_snapshot_does_not_alias_mutable_config(self):
        config = make_config()
        snapshot = recovery_settings(config)
        config.data['Main']['Emotion']['Fleet1Value'] = 3
        self.assertNotEqual(snapshot, recovery_settings(config))

    def test_prepare_reloads_real_bound_config_without_losing_map_state(self):
        from tests.test_farming_combat_config import make_config as bound_config
        self.enterContext(patch('module.config.config.current_time', return_value=datetime(2026, 10, 21, 12)))
        config = bound_config('Main', Campaign={'Name': '16-4', 'ContinueAfterRestart': True})
        config.override(Campaign_Name='campaign_16_4', Campaign_Event='campaign_main')
        runner = CampaignRun(config=config, device=Mock())
        runner.load_campaign('campaign_16_4')
        runner.run_count, runner.run_limit = 4, 6
        campaign = runner.campaign
        campaign.map_is_auto_search = campaign.map_is_clear_mode = True
        campaign.map_is_2x_book = True
        campaign._auto_search_phase = 'executing'
        campaign._auto_search_fleet_confirmed = True
        campaign.battle_count = 2
        campaign._map_battle = 9
        campaign.combat_result = CombatResult(fleet_index=1, emotion_reduce=True, emotion_reduced=True)
        for cfg in (runner.config, campaign.config):
            # 使用真实 load/bind，但读写全部隔离在内存，不写 template 或用户配置。
            cfg.modified.clear()
            snapshot = copy.deepcopy(cfg.data)
            cfg.read_file = Mock(return_value=snapshot)
            cfg.save = Mock()
        recovery = CampaignRecovery.capture(runner)
        self.assertTrue(recovery.matches(config))
        recovery.find_state = Mock(return_value='combat')
        self.assertTrue(recovery.prepare(config, runner.device))
        self.assertEqual((4, 6, 2, 9), (runner.run_count, runner.run_limit,
                                      campaign.battle_count, campaign._map_battle))
        self.assertTrue(campaign.combat_result.emotion_reduced)
        self.assertTrue(campaign.emotion.map_is_2x_book)
        self.assertEqual('campaign_16_4', campaign.config.Campaign_Name)


class RestartBattleReplayTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.combat.auto_search_combat.logger'))
        self.enterContext(patch('module.combat.combat.logger'))
        self.enterContext(patch('module.campaign.campaign_base.logger'))

    def make_replay(self, frames, phase='executing', *, reduced=True, rank=None):
        replay = ScreenReplay(frames)
        campaign = replay.make_campaign(cls=CampaignBase)
        campaign._auto_search_phase = phase
        campaign._auto_search_fleet_confirmed = True
        campaign._map_battle = 9
        campaign.battle_count = 2
        campaign.fleet_show_index = 1
        campaign.lv_get = Mock()
        campaign.handle_map_cat_attack = Mock(return_value=False)
        campaign.is_in_map = Mock(side_effect=lambda: 'MAP' in campaign.device.image)
        campaign.combat_result = CombatResult(fleet_index=1, emotion_reduce=True,
                                              emotion_reduced=reduced, rank=rank)
        campaign.auto_search_watch_oil = Mock(return_value=True)
        campaign.auto_search_watch_coin = Mock(return_value=True)
        campaign.match_template_color = Mock(side_effect=replay.appear)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: replay.time))
        return replay, campaign

    def resume(self, campaign, state):
        self.assertTrue(campaign.prepare_auto_search_recovery(state))
        campaign.auto_search_execute_a_battle()

    def test_in_combat_keeps_existing_charge_and_counts_battle_once(self):
        replay, campaign = self.make_replay([
            screen('COMBAT'), screen('COMBAT'), screen(BATTLE_STATUS_S),
            screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)])
        self.resume(campaign, 'combat')
        campaign.emotion.reduce.assert_not_called()
        self.assertEqual(3, campaign.battle_count)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)

    def test_loading_then_result_records_missing_base_cost_once(self):
        _, campaign = self.make_replay([
            screen('LOADING'), screen('COMBAT'), screen(BATTLE_STATUS_S),
            screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)], phase='loading', reduced=False)
        self.resume(campaign, 'loading')
        campaign.emotion.reduce.assert_called_once_with(1)
        self.assertEqual(3, campaign.battle_count)

    def test_resumed_loading_can_skip_pause_and_land_on_result(self):
        _, campaign = self.make_replay([
            screen('LOADING'), screen(BATTLE_STATUS_S), screen(EXP_INFO_S),
            screen(AUTO_SEARCH_MAP_OPTION_ON)], phase='loading', reduced=False)
        self.resume(campaign, 'loading')
        campaign.emotion.reduce.assert_called_once_with(1)
        self.assertEqual(3, campaign.battle_count)

    def test_result_without_seen_pause_charges_base_and_penalty_once(self):
        _, campaign = self.make_replay([
            screen(BATTLE_STATUS_A), screen(BATTLE_STATUS_A), screen(EXP_INFO_A),
            screen(AUTO_SEARCH_MAP_OPTION_ON)], phase='loading', reduced=False)
        self.resume(campaign, 'status')
        self.assertEqual([call(1), call(1, shipwreck=True)], campaign.emotion.reduce.call_args_list)
        self.assertEqual(3, campaign.battle_count)

    def test_repeated_non_s_result_keeps_already_recorded_penalty(self):
        _, campaign = self.make_replay([
            screen(BATTLE_STATUS_A), screen(EXP_INFO_A), screen(AUTO_SEARCH_MAP_OPTION_ON)],
            phase='status', rank='A')
        campaign.combat_result.shipwreck_reduced = True
        self.resume(campaign, 'status')
        campaign.emotion.reduce.assert_not_called()
        self.assertEqual(3, campaign.battle_count)

    def test_completed_stage_uses_existing_end_without_another_battle(self):
        replay, campaign = self.make_replay([screen(AUTO_SEARCH_MENU_EXIT)], phase='status', rank='S')
        self.assertTrue(campaign.prepare_auto_search_recovery('menu'))
        with self.assertRaises(CampaignEnd):
            campaign.auto_search_execute_a_battle()
        self.assertEqual(2, campaign.battle_count)
        self.assertEqual([], replay.clicks)
        campaign.emotion.reduce.assert_not_called()

    def test_defeat_respects_existing_withdraw_stop_limit(self):
        replay, campaign = self.make_replay(retreat_frames(screen(OPTS_INFO_D)), phase='status')
        campaign._defeat_count = 2
        self.assertTrue(campaign.prepare_auto_search_recovery('status'))
        with self.assertRaisesRegex(ScriptEnd, 'DefeatWithdraw=withdraw_stop'):
            campaign.auto_search_execute_a_battle()
        self.assertEqual(3, campaign._defeat_count)
        campaign.emotion.reduce.assert_called_once_with(1, shipwreck=True)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)

    def test_map_off_resumes_moving_with_existing_combat_pipeline(self):
        replay, campaign = self.make_replay([
            screen('MAP', WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1),
            screen('MAP', AUTO_SEARCH_MAP_OPTION_ON, FLEET_NUM_1),
            screen('COMBAT'), screen('COMBAT'), screen(BATTLE_STATUS_S),
            screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)], phase='moving', reduced=False)
        campaign.combat_result = CombatResult()
        self.resume(campaign, 'map')
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_OFF.name, BATTLE_STATUS_S.name, EXP_INFO_S.name], replay.clicks)
        campaign.emotion.check_fleet.assert_called_once_with(1, after_restart=True)
        campaign.emotion.reduce.assert_called_once_with(1)
        self.assertEqual(3, campaign.battle_count)

    def test_interrupted_battle_on_map_restarts_auto_search_without_recharging(self):
        for phase, reduced, fleet_index in (
                ('loading', False, 1), ('executing', True, 1), ('status', True, 1), ('status', True, 2)):
            with self.subTest(phase=phase, reduced=reduced, fleet=fleet_index):
                fleet = FLEET_NUM_1 if fleet_index == 1 else FLEET_NUM_2
                replay, campaign = self.make_replay([
                    screen('MAP', WITHDRAW, MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_OFF, fleet),
                    screen('MAP', MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_ON, fleet),
                    screen('COMBAT'), screen('COMBAT'), screen(BATTLE_STATUS_S),
                    screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)], phase=phase, reduced=reduced)
                result = campaign.combat_result
                result.fleet_index = fleet_index
                campaign.fleet_show_index = fleet_index
                self.resume(campaign, 'map')
                self.assertIs(result, campaign.combat_result)
                self.assertEqual('S', result.rank)
                self.assertEqual(3, campaign.battle_count)
                self.assertEqual([AUTO_SEARCH_MAP_OPTION_OFF.name, BATTLE_STATUS_S.name, EXP_INFO_S.name],
                                 replay.clicks)
                self.assertEqual([] if reduced else [call(fleet_index)], campaign.emotion.reduce.call_args_list)

    def test_interrupted_battle_map_on_is_not_toggled_off(self):
        replay, campaign = self.make_replay([
            screen('MAP', WITHDRAW, MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_ON, FLEET_NUM_1),
            screen('COMBAT'), screen('COMBAT'), screen(BATTLE_STATUS_S),
            screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON)], phase='status')
        self.resume(campaign, 'map')
        self.assertEqual(3, campaign.battle_count)
        campaign.emotion.reduce.assert_not_called()
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_ON.name, replay.clicks)

    def test_interrupted_battle_map_retry_still_obeys_defeat_stop(self):
        replay, campaign = self.make_replay([
            screen('MAP', WITHDRAW, MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1),
            screen('MAP', MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_ON, FLEET_NUM_1),
            screen('COMBAT'), screen('COMBAT'), *retreat_frames(screen(OPTS_INFO_D))], phase='status')
        campaign._defeat_count = 2
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        with self.assertRaisesRegex(ScriptEnd, 'DefeatWithdraw=withdraw_stop'):
            campaign.auto_search_execute_a_battle()
        self.assertEqual(3, campaign._defeat_count)
        self.assertEqual(2, campaign.battle_count)
        campaign.emotion.reduce.assert_called_once_with(1, shipwreck=True)
        self.assertEqual(1, replay.clicks.count(AUTO_SEARCH_MAP_OPTION_OFF.name))

    def test_interrupted_map_recovery_does_not_invent_result_before_retry(self):
        _, campaign = self.make_replay([
            screen('MAP', WITHDRAW, MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1)], phase='status')
        result = campaign.combat_result
        campaign.auto_search_moving = Mock(side_effect=GameStuckError('retry interrupted'))
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        with self.assertRaises(GameStuckError):
            campaign.auto_search_execute_a_battle()
        self.assertEqual(2, campaign.battle_count)
        self.assertIs(result, campaign.combat_result)
        self.assertIsNone(result.rank)
        self.assertFalse(result.defeated)
        campaign.emotion.reduce.assert_not_called()

    def test_interrupted_map_requires_same_fleet_and_visible_attack(self):
        for buttons in ((FLEET_NUM_1,), (MAP_OFFENSIVE, FLEET_NUM_2), (MAP_OFFENSIVE,)):
            with self.subTest(buttons=buttons):
                replay, campaign = self.make_replay([
                    screen('MAP', WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF, *buttons)], phase='status')
                self.assertFalse(campaign.prepare_auto_search_recovery('map'))
                self.assertEqual([], replay.clicks)
                campaign.emotion.reduce.assert_not_called()

    def test_interrupted_map_keeps_stop_condition_without_enabling(self):
        replay, campaign = self.make_replay(retreat_frames(
            screen('MAP', WITHDRAW, MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1)), phase='status')
        campaign._restart_stop_requested = True
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        with self.assertRaisesRegex(ScriptEnd, '原有停止条件已触发'):
            campaign.auto_search_execute_a_battle()
        self.assertEqual(2, campaign.battle_count)
        self.assertIn(WITHDRAW.name, replay.clicks)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
        campaign.emotion.reduce.assert_not_called()

    def test_interrupted_map_changed_fleet_during_retry_does_not_reuse_charge(self):
        _, campaign = self.make_replay([
            screen('MAP', WITHDRAW, MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1),
            screen('MAP', MAP_OFFENSIVE, AUTO_SEARCH_MAP_OPTION_ON, FLEET_NUM_2),
            screen('LOADING')], phase='status')
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        with self.assertRaisesRegex(GameStuckError, '舰队改变'):
            campaign.auto_search_execute_a_battle()
        self.assertEqual(2, campaign.battle_count)
        self.assertEqual(1, campaign.combat_result.fleet_index)
        campaign.emotion.reduce.assert_not_called()

    def test_on_or_ambiguous_off_does_not_click(self):
        replay, campaign = self.make_replay([
            screen('MAP', AUTO_SEARCH_MAP_OPTION_ON, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1)], phase='moving')
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        self.assertFalse(campaign.handle_auto_search_map_option())
        self.assertEqual([], replay.clicks)
        campaign.match_template_color.assert_not_called()
        replay.device.image = screen('MAP', WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1)
        campaign.match_template_color.return_value = False
        campaign.match_template_color.side_effect = None
        self.assertFalse(campaign.handle_auto_search_map_option())
        self.assertEqual([], replay.clicks)

    def test_recorded_result_on_map_finishes_pending_count_without_recharging(self):
        replay, campaign = self.make_replay([
            screen('MAP', WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1),
            screen('MAP', AUTO_SEARCH_MAP_OPTION_ON, FLEET_NUM_1)], phase='status', rank='S')
        self.resume(campaign, 'map')
        campaign.emotion.reduce.assert_not_called()
        self.assertEqual(3, campaign.battle_count)
        self.assertEqual([AUTO_SEARCH_MAP_OPTION_OFF.name], replay.clicks)

    def test_low_emotion_withdraws_before_reporting_existing_delay(self):
        replay, campaign = self.make_replay([
            screen('MAP', WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF, FLEET_NUM_1)], phase='moving')
        campaign.emotion.check_fleet.side_effect = ScriptEnd('emotion delay')
        campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        with self.assertRaisesRegex(ScriptEnd, 'emotion delay'):
            campaign.handle_auto_search_map_option()
        campaign.withdraw.assert_called_once_with()
        self.assertEqual([], replay.clicks)

    def test_unknown_result_missing_fleet_or_pending_write_is_rejected(self):
        for phase, state, rank, fleet, pending in (
                ('executing', 'map', None, True, False),
                ('status', 'combat', 'S', True, False),
                ('executing', 'combat', None, False, False),
                ('executing', 'status', None, True, True)):
            _, campaign = self.make_replay([screen('MAP')], phase=phase, rank=rank)
            campaign._auto_search_fleet_confirmed = fleet
            campaign.combat_result.emotion_pending = pending
            with self.subTest(phase=phase, state=state, pending=pending):
                self.assertFalse(campaign.prepare_auto_search_recovery(state))
                campaign.emotion.reduce.assert_not_called()

    def test_missing_button_reaches_timeout_without_clicking(self):
        replay, campaign = self.make_replay([screen('MAP', WITHDRAW)], phase='moving')
        self.assertTrue(campaign.prepare_auto_search_recovery('map'))
        for _ in range(60):
            replay.time += 1
            campaign.handle_auto_search_map_option()
        with self.assertRaises(GameStuckError):
            replay.time += 1
            campaign.handle_auto_search_map_option()
        self.assertEqual([], replay.clicks)

    def test_partial_emotion_write_is_not_retried(self):
        _, campaign = self.make_replay([screen('COMBAT')], reduced=False)
        campaign.emotion.reduce.side_effect = OSError('disk failed')
        with self.assertRaises(OSError):
            campaign._auto_search_reduce_emotion(True)
        self.assertTrue(campaign.combat_result.emotion_pending)
        self.assertFalse(campaign.prepare_auto_search_recovery('combat'))

    def test_unsafe_map_fallback_confirms_off_before_standard_withdraw(self):
        replay, campaign = self.make_replay([
            screen('MAP', WITHDRAW, AUTO_SEARCH_MAP_OPTION_OFF),
            screen('STAGE')], phase='executing')
        campaign.handle_in_stage = Mock(side_effect=lambda: 'STAGE' in replay.device.image)
        campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))
        with self.assertRaises(CampaignEnd):
            campaign.auto_search_withdraw(strict=True)
        campaign.withdraw.assert_called_once_with()
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_ON.name, replay.clicks)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)

    def test_existing_stop_condition_settles_then_withdraws_without_enabling(self):
        replay, campaign = self.make_replay(retreat_frames(
            screen(BATTLE_STATUS_S), screen(EXP_INFO_S)), phase='status', rank='S')
        campaign._restart_stop_requested = True
        self.assertTrue(campaign.prepare_auto_search_recovery('status'))
        with self.assertRaisesRegex(ScriptEnd, '原有停止条件已触发'):
            campaign.auto_search_execute_a_battle()
        self.assertIn(EXP_INFO_S.name, replay.clicks)
        self.assertIn(WITHDRAW.name, replay.clicks)
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)
        self.assertEqual(2, campaign.battle_count)
        campaign.emotion.reduce.assert_not_called()

    def test_resumed_campaign_does_not_enter_map_or_reset_progress(self):
        replay, campaign = self.make_replay([
            screen('COMBAT'), screen('COMBAT'), screen(BATTLE_STATUS_S),
            screen(EXP_INFO_S), screen(AUTO_SEARCH_MAP_OPTION_ON), screen(AUTO_SEARCH_MENU_EXIT)])
        campaign.ENTRANCE = '16-4'
        campaign._resume_after_restart = True
        campaign.map_get_info = Mock()
        campaign.enter_map = Mock()
        campaign.map_init = Mock()
        campaign.lv_reset = Mock()
        self.assertTrue(campaign.prepare_auto_search_recovery('combat'))
        self.assertTrue(campaign.run())
        self.assertEqual(3, campaign.battle_count)
        campaign.map_get_info.assert_not_called()
        campaign.enter_map.assert_not_called()
        campaign.map_init.assert_not_called()
        campaign.lv_reset.assert_not_called()
        campaign.emotion.check_reduce.assert_not_called()
        self.assertNotIn(AUTO_SEARCH_MAP_OPTION_OFF.name, replay.clicks)

    def test_resumed_round_uses_original_completion_and_remaining_run_count(self):
        runner = object.__new__(CampaignRun)
        runner.config = SimpleNamespace(
            StopCondition_RunCount=2, modified={}, override=Mock(), task_switched=Mock(return_value=False))
        runner.device = Mock(has_cached_image=True)
        runner.name = 'campaign_16_4'
        runner.folder = 'campaign_main'
        runner.run_count = 4
        runner.run_limit = 6
        runner._resume_map = True
        runner.campaign = Mock()
        runner.campaign.config = SimpleNamespace(
            Campaign_Mode='normal', modified={}, MAP_IS_ONE_TIME_STAGE=False)
        runner.campaign.event_time_limit_triggered.return_value = False
        runner.triggered_stop_condition = Mock(return_value=False)
        runner.after_campaign_run = Mock()
        runner.load_campaign = Mock()
        runner._run('16-4', total=5, resume=True)
        self.assertEqual(5, runner.run_count)
        self.assertEqual(1, runner.config.StopCondition_RunCount)
        self.assertEqual(6, runner.run_limit)
        runner.campaign.run.assert_called_once_with()
        runner.campaign.withdraw.assert_not_called()
        runner.campaign.ensure_campaign_ui.assert_not_called()
        runner.load_campaign.assert_not_called()
        runner.after_campaign_run.assert_called_once_with()


class RecoveryNavigationAndConfigTests(unittest.TestCase):
    def test_all_server_assets_distinguish_toggle_results_and_login(self):
        # 每个子进程先设服务器再加载资源，避免同进程 Button 的缓存跨服污染。
        code = r'''
import sys
import module.config.server as server
server.server = sys.argv[1]
from types import SimpleNamespace
from unittest.mock import Mock
from module.base.utils import load_image
from module.combat.auto_search_combat import AutoSearchCombat
from module.combat.assets import BATTLE_STATUS_S, BATTLE_STATUS_A, BATTLE_STATUS_B, EXP_INFO_S, OPTS_INFO_D
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_ON, AUTO_SEARCH_MAP_OPTION_OFF, LOGIN_CHECK
from module.map.assets import FLEET_NUM_1, MAP_OFFENSIVE, WITHDRAW
from module.combat.result import CombatResult
from module.ui.page import page_main, page_main_white
c = object.__new__(AutoSearchCombat)
c.config = SimpleNamespace(SERVER=server.server, BUTTON_OFFSET=(30, 30))
c.device = Mock()
c.interval_timer = {}
for button in (BATTLE_STATUS_S, BATTLE_STATUS_A, BATTLE_STATUS_B, EXP_INFO_S, OPTS_INFO_D):
    c.device.image = load_image(button.file)
    assert c.auto_search_recovery_state() == 'status', button.name
for button in (LOGIN_CHECK, page_main.check_button, page_main_white.check_button):
    c.device.image = load_image(button.file)
    assert c.auto_search_recovery_state() is None, button.name
for button, on in ((AUTO_SEARCH_MAP_OPTION_ON, True), (AUTO_SEARCH_MAP_OPTION_OFF, False)):
    c.device.image = load_image(button.file)
    if not on:
        # 资源图只有按钮区域；组合已有撤退资源以提供关闭状态的地图证据。
        x1, y1, x2, y2 = WITHDRAW.area
        c.device.image[y1:y2, x1:x2] = load_image(WITHDRAW.file)[y1:y2, x1:x2]
    assert c.auto_search_recovery_state() == 'map', button.name
    assert c.is_auto_search_running() == on, button.name
    assert c.match_template_color(AUTO_SEARCH_MAP_OPTION_OFF, offset=(5, 5), threshold=10) != on, button.name
for button in (FLEET_NUM_1, MAP_OFFENSIVE):
    x1, y1, x2, y2 = button.area
    c.device.image[y1:y2, x1:x2] = load_image(button.file)[y1:y2, x1:x2]
c._auto_search_phase = 'status'
c._auto_search_fleet_confirmed = True
c.combat_result = CombatResult(fleet_index=1, emotion_reduce=True, emotion_reduced=True)
assert c.prepare_auto_search_recovery('map'), server.server
assert c.combat_result.rank is None
assert c.combat_result.emotion_reduced
'''
        for lang in ('cn', 'en', 'jp', 'tw'):
            with self.subTest(server=lang):
                result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, lang],
                                        capture_output=True, text=True, encoding='utf-8', timeout=30,
                                        env=dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1'),
                                        cwd=Path(__file__).resolve().parents[1])
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_login_returns_pending_settlement_before_generic_handlers(self):
        login = object.__new__(LoginHandler)
        login.device = Mock()
        login.campaign_recovery_check = Mock(return_value='status')
        login.is_in_main = Mock(return_value=False)
        login.handle_popup_confirm = Mock()
        login.ui_page_main_popups = Mock()
        with patch('module.handler.login.logger'):
            self.assertTrue(login._handle_app_login())
        login.handle_popup_confirm.assert_not_called()
        login.ui_page_main_popups.assert_not_called()

    def test_navigation_uses_graph_until_map_appears_without_chapter_ocr(self):
        runner = make_saved_runner()
        campaign = runner.campaign
        campaign.ui_current = page_campaign
        campaign.auto_search_recovery_state = Mock(side_effect=[None, None, 'map'])
        campaign.is_in_stage = Mock(return_value=False)
        campaign.loop = Mock(return_value=iter(range(3)))
        campaign.device = Mock()
        campaign.appear = Mock(return_value=False)
        campaign.handle_story_skip = Mock(return_value=False)
        campaign.handle_combat_low_emotion = Mock(return_value=False)
        campaign.withdraw = Mock()
        campaign.get_chapter_index = Mock()
        recovery = CampaignRecovery.capture(runner)
        self.assertEqual('map', recovery.find_state())
        campaign.withdraw.assert_not_called()
        campaign.get_chapter_index.assert_not_called()

    def test_generated_default_translations_and_visible_scope(self):
        root = Path(__file__).resolve().parents[1]
        args = json.loads((root / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        visible = set()
        for task, groups in args.items():
            if 'ContinueAfterRestart' not in groups.get('Campaign', {}):
                continue
            option = groups['Campaign']['ContinueAfterRestart']
            self.assertIs(option['value'], False)
            self.assertEqual('checkbox', option['type'])
            if option.get('display') != 'hide':
                visible.add(task)
        self.assertEqual({'Main', 'Main2', 'Main3', 'Event', 'Event2', 'Event3',
                          'EventA', 'EventB', 'EventC', 'EventD', 'EventSp',
                          'GemsFarming', 'ThreeOilLowCost'}, visible)
        for lang in ('zh-CN', 'zh-MIAO', 'en-US', 'ja-JP', 'zh-TW'):
            data = json.loads((root / f'module/config/i18n/{lang}.json').read_text(encoding='utf-8'))
            for field in ('name', 'help'):
                self.assertNotIn('Campaign.ContinueAfterRestart.', data['Campaign']['ContinueAfterRestart'][field])


if __name__ == '__main__':
    unittest.main()
