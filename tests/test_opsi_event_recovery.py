"""侵蚀一随机事件恢复：虚拟截图循环与临时配置，不连接模拟器。"""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.base.button import Button
from module.config.config_updater import ConfigUpdater
from module.config.deep import deep_get, deep_set
from module.os.event_recovery import MAP_EVENT_TYPES, PENDING_EVENT_PATH, MapEventRecovery
from module.exception import GameStuckError
from module.os.map import OSMap
from tests.test_opsi_akashi_interaction import RecoveryHarness, View
from tests.test_opsi_cl1_speed import VirtualClock, WalkStateStub
from tests.test_opsi_fixed_patrol_escalation import FixedPatrolStub


def event_grid(kind, location=(1, 7)):
    return SimpleNamespace(location=location, button=(270, 584, 345, 668), **{kind: True})


class EventWalkHarness(WalkStateStub):
    def __init__(self, clock, **kwargs):
        super().__init__(clock, **kwargs)
        self.menu = False
        self.fleet_selector = SimpleNamespace(bar_opened=lambda: self.menu)
        self._recover_event_page = Mock(return_value=False)
        area = (270, 584, 345, 628)
        self._prepare_event_click = Mock(return_value=Button(area=area, color=(), button=area, name='TEST_EVENT'))


class TestEventEntrance(unittest.TestCase):
    def setUp(self):
        self.clock = VirtualClock()
        timer = patch('module.base.timer.time', self.clock)
        timer.start()
        self.addCleanup(timer.stop)

    def test_no_progress_limits_target_clicks_and_returns_for_escalation(self):
        handler = EventWalkHarness(self.clock)
        self.assertEqual(handler.wait_until_walk_stable(event_target=(1, 7)), '')
        self.assertEqual(handler.device.click.call_count, 3)
        self.assertGreaterEqual(self.clock.now, 112)
        self.assertLess(self.clock.now, 112.5)
        self.assertFalse(handler._walk_event_completed)
        budgets = [call.kwargs['camera_budget'] for call in handler._prepare_event_click.call_args_list]
        self.assertTrue(all(budget is budgets[0] for budget in budgets))

    def test_fully_blocked_grid_returns_without_clicking(self):
        handler = EventWalkHarness(self.clock)
        handler._prepare_event_click.return_value = None
        self.assertEqual(handler.wait_until_walk_stable(event_target=(1, 7)), '')
        handler.device.click.assert_not_called()
        self.assertFalse(handler._walk_event_completed)

    def test_open_menu_is_closed_before_target_click(self):
        handler = EventWalkHarness(self.clock, events={2: 'map_get_items'})
        handler.menu = True

        def recover():
            if handler.menu:
                handler.menu = False
                return True
            return False

        handler._recover_event_page.side_effect = recover
        handler.wait_until_walk_stable(event_target=(1, 7), allow_event_finish=True)
        self.assertEqual(handler.device.click.call_count, 1)
        self.assertTrue(handler._walk_event_completed)
        self.assertGreaterEqual(handler.frame, 3)

    def test_page_changed_during_relocation_is_not_clicked(self):
        handler = EventWalkHarness(self.clock, events={1: 'map_archives'})

        def prepare(*args, **kwargs):
            handler.is_in_map.return_value = False
            return Button(area=(300, 300, 320, 320), color=(), button=(300, 300, 320, 320), name='OLD')

        handler._prepare_event_click.side_effect = prepare
        original = handler.handle_map_event

        def event(**kwargs):
            if handler.frame == 2:
                handler.is_in_map.return_value = True
            return original(**kwargs)

        handler.handle_map_event = event
        handler.wait_until_walk_stable(event_target=(1, 7), allow_event_finish=True)
        handler.device.click.assert_not_called()
        self.assertTrue(handler._walk_event_completed)

    def test_story_and_confirm_popup_are_not_completion(self):
        for event in ('story_skip', 'depart_confirm', 'os_game_tips', 'ash_popup'):
            with self.subTest(event=event):
                self.clock.now = 100
                handler = EventWalkHarness(self.clock, events={1: event})
                handler.wait_until_walk_stable(event_target=(1, 7), allow_event_finish=True)
                self.assertFalse(handler._walk_event_completed)

    def test_reward_followed_by_new_story_must_not_complete_early(self):
        handler = EventWalkHarness(self.clock, events={1: 'map_get_items', 2: 'story_skip'})
        handler.wait_until_walk_stable(event_target=(1, 7), allow_event_finish=True)
        self.assertFalse(handler._walk_event_completed)

    def test_movement_progress_does_not_consume_the_no_progress_budget(self):
        handler = EventWalkHarness(self.clock, events={45: 'map_get_items'})

        def moving_frames(**kwargs):
            for frame in range(60):
                handler.frame = frame
                self.clock.now = 100 + 0.35 * frame
                handler.view.backend.homo_loca = (52 + 8 * (frame // 10), 58)
                yield None
            raise AssertionError('事件完成后仍未收尾')

        handler.loop = moving_frames
        handler.wait_until_walk_stable(event_target=(1, 7), allow_event_finish=True)
        self.assertTrue(handler._walk_event_completed)
        self.assertGreater(self.clock.now, 112)
        self.assertLessEqual(handler.device.click.call_count, 3)

    def test_menu_appearing_during_relocation_cannot_be_clicked_through(self):
        handler = EventWalkHarness(self.clock, events={3: 'map_get_items'})
        original = handler._prepare_event_click.return_value

        def prepare(*args, **kwargs):
            if handler.frame == 0:
                handler.menu = True
            return original

        def recover():
            if handler.menu:
                handler.menu = False
                return True
            return False

        handler._prepare_event_click.side_effect = prepare
        handler._recover_event_page.side_effect = recover
        handler.wait_until_walk_stable(event_target=(1, 7), allow_event_finish=True)
        self.assertEqual(handler.device.click.call_count, 1)
        self.assertTrue(handler._walk_event_completed)


class TestPendingEvent(unittest.TestCase):
    def handler(self, kind, owner='OpsiHazard1Leveling'):
        handler = RecoveryHarness(owner)
        handler._is_siren_research_enabled = True
        handler.fleet_selector = SimpleNamespace(get=lambda: 1)
        handler.fleet_set = Mock()
        handler.os_auto_search_run = Mock()
        if owner == 'OpsiHazard1Leveling':
            handler._remember_pending_event(kind, event_grid(kind))
        else:
            handler.save_pending()
            state = handler._get_pending_event()
            handler.config.cross_set(PENDING_EVENT_PATH, dict(state, kind=kind))
        return handler

    def test_old_akashi_record_migrates_without_changing_its_identity(self):
        old = {}
        record = dict(server='cn', reset='2026-11-01T00:00:00', zone=22, fleet=2, target=[1, 7])
        deep_set(old, 'OpsiHazard1Leveling.OpsiHazard1Leveling.PendingAkashiRecovery', record)
        new = ConfigUpdater().config_update(old)
        self.assertEqual(deep_get(new, PENDING_EVENT_PATH), dict(record, kind='is_akashi', phase='interact'))
        self.assertNotIn('kind', record)

    def test_existing_generic_record_wins_over_old_migration(self):
        old = {}
        deep_set(old, 'OpsiHazard1Leveling.OpsiHazard1Leveling.PendingAkashiRecovery', {'zone': 22})
        state = {'kind': 'is_logging_tower'}
        deep_set(old, PENDING_EVENT_PATH, state)
        new = ConfigUpdater().config_redirect(old, copy.deepcopy(old))
        self.assertEqual(deep_get(new, PENDING_EVENT_PATH), state)

    def test_each_event_is_saved_before_its_first_interaction(self):
        for kind in MAP_EVENT_TYPES:
            with self.subTest(kind=kind):
                handler = self.handler(kind)
                state = handler._get_pending_event()
                self.assertEqual(state['kind'], kind)
                self.assertEqual(state['target'], [1, 7])
                handler._try_map_event_grid = Mock(return_value=False)
                handler._event_recovery_active = True
                self.assertFalse(handler._visit_map_event(kind, event_grid(kind)))
                self.assertEqual(handler._get_pending_event(), state)

    def test_unrelated_event_or_same_kind_at_another_grid_does_not_clear_pending(self):
        handler = self.handler('is_logging_tower')
        state = handler._get_pending_event()
        handler._try_map_event_grid = Mock(return_value=True)
        self.assertFalse(handler._visit_map_event('is_exploration_reward', event_grid('is_exploration_reward')))
        self.assertFalse(handler._visit_map_event('is_logging_tower', event_grid('is_logging_tower', (2, 7))))
        handler._try_map_event_grid.assert_not_called()
        self.assertEqual(handler._get_pending_event(), state)

    def test_unknown_question_can_only_be_refined_at_its_original_grid(self):
        handler = self.handler('is_question')
        handler._remember_pending_event('is_logging_tower', event_grid('is_logging_tower', (3, 7)))
        self.assertEqual(handler._get_pending_event()['kind'], 'is_question')
        handler._remember_pending_event('is_logging_tower', event_grid('is_logging_tower'))
        self.assertEqual(handler._get_pending_event()['kind'], 'is_logging_tower')

    def test_already_open_shop_refines_question_without_a_screen_coordinate(self):
        handler = self.handler('is_question')
        handler._remember_pending_event('is_akashi', Button(area=(0, 0, 1, 1), color=(), button=(0, 0, 1, 1)))
        self.assertEqual(handler._get_pending_event()['kind'], 'is_akashi')
        handler._finish_pending_event('is_akashi')
        self.assertIsNone(handler._get_pending_event())

    def test_only_final_rewards_or_archives_complete_ordinary_events(self):
        for kind in ('is_logging_tower', 'is_exploration_reward', 'is_exploration_container', 'is_question'):
            for completed in (False, True):
                with self.subTest(kind=kind, completed=completed):
                    handler = self.handler(kind)

                    def walk(**kwargs):
                        handler._walk_event_completed = completed
                        self.assertEqual(kwargs['event_target'], (1, 7))
                        return 'event'

                    handler.wait_until_walk_stable = walk
                    self.assertEqual(handler._try_map_event_grid(kind, event_grid(kind)), completed)
                    self.assertEqual(handler._get_pending_event() is None, completed)
                    self.assertEqual(kind in handler._solved_map_event, completed)

    def test_container_explicit_story_option_does_not_turn_it_into_a_siren_device(self):
        handler = self.handler('is_exploration_container')

        def walk(**kwargs):
            handler.is_siren_device_confirmed = True
            handler.siren_device_mode = None
            handler._walk_event_completed = True
            return 'event'

        handler.wait_until_walk_stable = walk
        self.assertTrue(handler._try_map_event_grid('is_exploration_container', event_grid('is_exploration_container')))
        handler.os_auto_search_run.assert_not_called()
        self.assertEqual(handler._solved_map_event, {'is_exploration_container'})
        self.assertIsNone(handler._get_pending_event())

    def test_battle_completion_also_requires_arriving_at_the_original_event_grid(self):
        for arrived in (True, False):
            with self.subTest(arrived=arrived):
                handler = self.handler('is_exploration_container')
                handler.wait_until_walk_stable = Mock(return_value='event_combat')
                handler.update = Mock()
                handler.view[(1, 7)] = SimpleNamespace(is_current_fleet=arrived)
                self.assertEqual(handler._try_map_event_grid(
                    'is_exploration_container', event_grid('is_exploration_container')), arrived)
                self.assertEqual(handler._get_pending_event() is None, arrived)

    def test_unknown_question_refined_to_disabled_device_is_skipped(self):
        handler = self.handler('is_question')
        handler._is_siren_research_enabled = False
        self.assertTrue(handler._visit_map_event('is_scanning_device', event_grid('is_scanning_device')))
        self.assertIsNone(handler._get_pending_event())

    def test_akashi_approach_uses_the_same_bounded_safe_entrance_loop(self):
        handler = self.handler('is_akashi')
        handler.convert_radar_to_local = Mock(return_value=SimpleNamespace(distance_to=lambda grid: 4))
        handler.wait_until_walk_stable = Mock(return_value='akashi')
        self.assertTrue(handler._try_akashi_grid(event_grid('is_akashi')))
        self.assertEqual(handler.wait_until_walk_stable.call_args.kwargs['event_target'], (1, 7))

    def test_siren_disabled_preserves_skip_policy_without_recovery(self):
        handler = self.handler('is_scanning_device')
        handler._is_siren_research_enabled = False
        handler._try_map_event_grid = Mock()
        self.assertTrue(handler._visit_map_event('is_scanning_device', event_grid('is_scanning_device')))
        handler._try_map_event_grid.assert_not_called()
        self.assertIsNone(handler._get_pending_event())

    def test_siren_modes_keep_their_followup_and_clear_only_after_returning_to_map(self):
        for mode, runs in (('enemy', 3), ('resource', 1), ('collected', 0), ('unknown', 1)):
            with self.subTest(mode=mode):
                handler = self.handler('is_scanning_device')
                handler.config.cross_set('OpsiHazard1Leveling.OpsiSirenBug.Siren_Fleet', 2)

                def walk(**kwargs):
                    handler.is_siren_device_confirmed = True
                    handler.siren_device_mode = mode
                    return 'event'

                def search(**kwargs):
                    self.assertEqual(handler._get_pending_event()['phase'], 'followup')

                handler.wait_until_walk_stable = walk
                handler.os_auto_search_run.side_effect = search
                self.assertTrue(handler._try_map_event_grid('is_scanning_device', event_grid('is_scanning_device')))
                self.assertEqual(handler.os_auto_search_run.call_count, runs)
                self.assertIsNone(handler._get_pending_event())
                self.assertEqual([call.args[0] for call in handler.fleet_set.call_args_list], [2, 1] if mode == 'enemy' else [])

    def test_interrupted_siren_followup_resumes_remaining_runs_before_scanning_or_new_search(self):
        handler = self.handler('is_scanning_device')
        state = handler._get_pending_event()
        state.update(phase='followup', mode='enemy', remaining=3)
        handler.config.cross_set(PENDING_EVENT_PATH, state)
        handler.os_auto_search_run.side_effect = [None, GameStuckError('interrupted')]
        with self.assertRaises(GameStuckError):
            handler._complete_siren_event(state)
        self.assertEqual(handler._get_pending_event()['remaining'], 2)
        handler.os_auto_search_run.reset_mock(side_effect=True)
        with patch('module.os.event_recovery.record_cl1_akashi_encounter') as count:
            self.assertTrue(handler._resume_pending_event())
            count.assert_not_called()
        self.assertEqual(handler.os_auto_search_run.call_count, 2)
        handler.map_rescan.assert_not_called()
        self.assertIsNone(handler._get_pending_event())

    def test_each_event_resumes_in_hazard1_context_without_counting_a_cat(self):
        for kind in MAP_EVENT_TYPES:
            for owner in ('OpsiHazard1Leveling', 'OpsiScheduling', 'OpsiPreventActionPointOverflow'):
                with self.subTest(kind=kind, owner=owner):
                    handler = self.handler(kind, owner)

                    def rescan(**kwargs):
                        self.assertEqual(handler.config.task.command, 'OpsiHazard1Leveling')
                        handler._solved_map_event.add(kind)
                        handler._finish_pending_event(kind)

                    handler.map_rescan.side_effect = rescan
                    with patch('module.os.event_recovery.record_cl1_akashi_encounter') as count:
                        self.assertTrue(handler._resume_pending_event())
                        self.assertEqual(count.call_count, int(kind == 'is_akashi'))
                    self.assertEqual(handler.config.task.command, owner)

    def test_any_missing_event_after_restart_remains_pending_and_raises(self):
        for kind in MAP_EVENT_TYPES:
            with self.subTest(kind=kind):
                handler = self.handler(kind)
                with self.assertRaises(GameStuckError):
                    handler._resume_pending_event()
                self.assertIsNotNone(handler._get_pending_event())
                handler._execute_fixed_patrol_scan.assert_called_once_with(
                    ExecuteFixedPatrolScan=True, event_recovery=True)

    def test_recovery_budget_is_shared_across_rescans_for_each_event(self):
        for kind in MAP_EVENT_TYPES:
            with self.subTest(kind=kind):
                handler = self.handler(kind)
                for _ in range(2):
                    handler._remember_pending_event(kind, event_grid(kind))
                    with self.assertRaises(GameStuckError):
                        handler._recover_pending_event()
                handler._goto_pending_event_with_other_fleets.assert_called_once()
                handler._execute_fixed_patrol_scan.assert_called_once()

    def test_map_scan_prioritizes_pending_identity_over_an_unrelated_reward(self):
        handler = self.handler('is_logging_tower')
        reward = event_grid('is_exploration_reward', (2, 7))
        tower = event_grid('is_logging_tower')
        handler.view = SimpleNamespace(center_loca=(4, 3), select=lambda **kwargs: [g for g in (reward, tower)
                                                               if getattr(g, next(iter(kwargs)), False)])
        handler._visit_map_event = Mock(return_value=True)
        self.assertTrue(OSMap.map_rescan_current(handler))
        handler._visit_map_event.assert_called_once_with('is_logging_tower', tower, drop=None)

    def test_other_fleets_retry_each_event_and_restore_the_original_fleet(self):
        for kind in MAP_EVENT_TYPES:
            with self.subTest(kind=kind):
                handler = self.handler(kind)
                handler.device = SimpleNamespace(screenshot=Mock())
                handler.view = View()
                handler.view[(1, 7)] = event_grid(kind)
                handler.update = Mock()
                handler._try_map_event_grid = Mock(side_effect=[False, True])
                self.assertTrue(MapEventRecovery._goto_pending_event_with_other_fleets(handler))
                self.assertEqual([call.args[0] for call in handler.fleet_set.call_args_list], [2, 3, 1])
                self.assertEqual(handler.update.call_count, 2)

    def test_old_position_without_new_event_evidence_is_not_clicked(self):
        handler = self.handler('is_logging_tower')
        handler.device = SimpleNamespace(screenshot=Mock())
        handler.update = Mock()
        handler.view = View()
        handler.view[(1, 7)] = SimpleNamespace(location=(1, 7))
        handler._radar_question_to_local = Mock(return_value=None)
        handler._try_map_event_grid = Mock()
        self.assertFalse(MapEventRecovery._goto_pending_event_with_other_fleets(handler))
        handler._try_map_event_grid.assert_not_called()
        self.assertIsNotNone(handler._get_pending_event())

    def test_emergency_movement_bypasses_disabled_switch_and_low_ap_for_each_event(self):
        for kind in MAP_EVENT_TYPES:
            with self.subTest(kind=kind):
                handler = FixedPatrolStub(enabled=False, current_ap=0)
                handler._get_pending_event = Mock(return_value={'kind': kind})
                OSMap._execute_fixed_patrol_scan(handler, ExecuteFixedPatrolScan=True, event_recovery=True)
                self.assertEqual(handler.move_calls, 1)
                self.assertFalse(handler.enabled)
                self.assertEqual(handler.ap_reads, 0)

    def test_event_found_during_rescan_still_blocks_post_battle_refresh(self):
        from contextlib import nullcontext
        from module.os.tasks.hazard_leveling import OpsiHazard1Leveling

        handler = SimpleNamespace(
            config=SimpleNamespace(OpsiHazard1Leveling_DebugClip=False),
            _get_pending_event=Mock(side_effect=[None, {'kind': 'is_logging_tower'}]),
            run_strategic_search=Mock(return_value=True), map_rescan=Mock(),
            _forced_move_enabled=Mock(return_value=False), handle_after_auto_search=Mock(),
        )
        with patch('module.base.debug_clip.clip_recording', return_value=nullcontext()), patch(
            'module.base.debug_clip.cleanup_clips_if_due'
        ), self.assertRaises(GameStuckError):
            OpsiHazard1Leveling._cl1_run_battle(handler)
        handler.map_rescan.assert_called_once()
        handler.handle_after_auto_search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
