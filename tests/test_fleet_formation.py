"""舰位互换的无OCR操作链、图像定位和参考扫描回归；不连接模拟器。"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import FleetManagementError, GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.retire.fleet_management import FleetManagement, PICKER_CONFIRM
from module.runtime.fleet_formation import (SLOT_CENTERS, SUBMARINE_SLOT_CENTERS, FormationCardMerger,
    FormationDetailScanner, FormationDockScanner, PortraitMatcher, VisualDockScanner, choose_portrait,
    combine_references, detail_occupied, detail_portraits, menu_area, selected_count_changed,
    fleet_type_selected, selected_menu_fleet, selector_closed, slot_swap_plan, preserve_emotions,
    validate_position, validate_swap)


FIXTURES = Path(__file__).parent / 'fixtures/fleet_formation'


def frame(name):
    return np.array(Image.open(FIXTURES / f'{name}.png').convert('RGB'))


def runner():
    tool = FleetManagement.__new__(FleetManagement)
    tool.config = SimpleNamespace(cross_get=Mock(return_value={}), modified={}, save=Mock())
    tool.device = SimpleNamespace(image=frame('vanguard_top'), click=Mock(), screenshot=Mock(), swipe=Mock())
    tool.dock_filter = SimpleNamespace(reset_first=True)
    for method in ('fleet_enter', '_open_slot', '_close_picker', '_prepare_dock', 'dock_reset',
                   'ui_ensure', '_save_result', '_confirm_replacement', '_commit_formation'):
        setattr(tool, method, Mock())
    tool.appear = Mock(return_value=False)
    tool._scan_window = None
    return tool


def positions(main=(), front=()):
    return dict(zip(range(1, 7), list(main) + [False] * (3 - len(main)) +
                    list(front) + [False] * (3 - len(front))))


class GeometryTests(unittest.TestCase):
    def test_surface_and_submarine_positions_have_distinct_business_limits(self):
        self.assertEqual(sorted((1, 2, 3), key=lambda n: SLOT_CENTERS[n][0]), [3, 1, 2])
        self.assertEqual(sorted((4, 5, 6), key=lambda n: SLOT_CENTERS[n][0]), [4, 5, 6])
        self.assertEqual(sorted(SUBMARINE_SLOT_CENTERS, key=lambda n: SUBMARINE_SLOT_CENTERS[n][0]), [3, 1, 2])
        self.assertEqual(FleetManagement.slot_button(5).button, (748, 380, 766, 398))
        for fleet, slot, kind in ((0, 1, 'surface'), (7, 1, 'surface'), (True, 1, 'surface'),
                                 (1, False, 'surface'), (3, 1, 'submarine'), (1, 4, 'submarine')):
            with self.subTest(fleet=fleet, slot=slot, kind=kind), self.assertRaises(ValueError):
                validate_position(fleet, slot, kind)

    def test_native_menu_highlights_select_surface_and_submarine_without_number_ocr(self):
        self.assertEqual(selected_menu_fleet(frame('menu_surface')), 3)
        self.assertEqual(selected_menu_fleet(frame('menu_submarine'), 'submarine'), 2)
        for kind in ('surface', 'submarine'):
            self.assertIsNone(selected_menu_fleet(frame(f'closed_{kind}'), kind))
            self.assertTrue(selector_closed(frame(f'closed_{kind}'), kind))
            self.assertFalse(selector_closed(frame(f'menu_{kind}'), kind))
        self.assertEqual(menu_area(1), (152, 586, 353, 628))
        self.assertEqual(menu_area(6), (152, 316, 353, 358))

    def test_detail_columns_are_surface_one_to_six_and_submarine_one_to_three(self):
        surface = frame('detail_surface')
        submarine = frame('detail_submarine')
        self.assertEqual(set(detail_portraits(surface)), set(range(1, 7)))
        self.assertEqual(set(detail_portraits(submarine, 'submarine')), {1, 2, 3})
        self.assertTrue(all(detail_occupied(surface, slot) for slot in range(1, 7)))
        self.assertTrue(all(detail_occupied(submarine, slot) for slot in range(1, 4)))

    def test_native_type_buttons_select_only_the_brown_gold_category(self):
        for selected in ('surface', 'submarine'):
            for stage in ('menu', 'closed'):
                image = frame(f'{stage}_{selected}')
                for kind in ('surface', 'submarine'):
                    self.assertEqual(fleet_type_selected(image, kind), kind == selected)

    def test_slot_plans_change_only_two_positions_and_support_submarines(self):
        occupied = {1: positions([True] * 3, [True] * 3), 4: positions([True] * 3, [True] * 3)}
        expected = slot_swap_plan(occupied, 1, 4, 4, 5)
        self.assertEqual(expected[1][4], '4:5')
        self.assertEqual(expected[4][5], '1:4')
        self.assertEqual(expected[1][5], '1:5')
        self.assertEqual(expected[4][4], '4:4')
        sub = slot_swap_plan({1: {1: True, 2: True, 3: True}, 2: {1: True, 2: True, 3: True}},
                             1, 3, 2, 1, 'submarine')
        self.assertEqual(sub[1][3], '2:1')
        self.assertEqual(sub[2][1], '1:3')
        with self.assertRaises(ValueError):
            validate_swap(1, 3, 4, 5)

    def test_empty_destination_is_append_only_and_source_positions_compact(self):
        occupied = {1: positions([True], [True, True, True]), 4: positions([True], [True])}
        expected = slot_swap_plan(occupied, 1, 4, 4, 5)
        self.assertEqual([expected[1][s] for s in (4, 5, 6)], ['1:5', '1:6', None])
        self.assertEqual(expected[4][5], '1:4')
        with self.assertRaises(ValueError):
            slot_swap_plan(occupied, 1, 4, 4, 6)

    def test_counter_image_response_works_without_reading_its_digits(self):
        before = np.zeros((720, 1280, 3), np.uint8)
        after = before.copy()
        after[665:682, 589:605] = 255
        self.assertTrue(selected_count_changed(before, after))
        self.assertFalse(selected_count_changed(before, before))
        after[662:686, 615:647] = 120
        self.assertFalse(selected_count_changed(before, after))


class VisualTargetTests(unittest.TestCase):
    def test_each_native_detail_card_finds_its_dock_card_without_any_ocr(self):
        expected = {1: ('main_top', 2, 0), 2: ('main_top', 6, 0), 3: ('main_top', 5, 0),
                    4: ('vanguard_top', 1, 1), 5: ('vanguard_top', 6, 0), 6: ('vanguard_top', 0, 1)}
        portraits = detail_portraits(frame('detail_surface'))
        with (
            patch('module.runtime.fleet_formation.FleetNameScanner', side_effect=AssertionError('禁止舰名OCR')),
            patch('module.runtime.fleet_formation.LevelScanner', side_effect=AssertionError('禁止等级OCR')),
            patch('module.runtime.fleet_formation.FleetEmotionScanner', side_effect=AssertionError('禁止心情OCR')),
        ):
            scanner = VisualDockScanner()
            for slot, (name, column, row) in expected.items():
                image = frame(name)
                cards = scanner.scan(image)
                ys = sorted({card.y for card in cards})
                matcher = PortraitMatcher(portraits[slot])
                candidates = [(card, card.y, matcher.score(scanner.portrait(image, card))) for card in cards]
                picked, _ = choose_portrait(candidates, source_fleet=1)
                self.assertEqual((picked.col, picked.y), (column, ys[row]))
                self.assertGreaterEqual(max(item[2] for item in candidates), 30)

    def test_identical_portraits_use_badge_hint_and_unresolved_visual_twins_stop_before_click(self):
        cards = VisualDockScanner().scan(frame('vanguard_top'))
        one, two = cards[0], cards[1]
        one.fleet, two.fleet = 1, 2
        self.assertIs(choose_portrait([(one, 75, 50), (two, 75, 49)], 2)[0], two)
        one.fleet = two.fleet = -1
        with self.assertRaises(ValueError):
            choose_portrait([(one, 75, 50), (two, 75, 49)], 2)
        with self.assertRaises(ValueError):
            choose_portrait([(one, 75, 4)], 1)

    def test_slot_swap_uses_fresh_geometry_and_succeeds_when_all_ocr_constructors_are_broken(self):
        tool = runner()
        portraits = detail_portraits(frame('detail_surface'))
        occupied = dict.fromkeys(range(1, 7), True)
        source = (None, portraits, occupied)
        target_portraits = {slot: np.flipud(image).copy() for slot, image in portraits.items()}
        target = (None, target_portraits, occupied)
        expected = slot_swap_plan({1: occupied, 4: occupied}, 1, 4, 4, 5)
        originals = {f'{number}:{slot}': image for number, capture in ((1, source), (4, target))
                     for slot, image in capture[1].items()}
        after = {number: (None, {slot: originals[token] for slot, token in values.items()}, occupied)
                 for number, values in expected.items()}
        tool._capture_formation = Mock(side_effect=[source, target, after[1], after[4], after[1], after[4]])
        calls = 0

        def pages(scanner, **kwargs):
            nonlocal calls
            calls += 1
            image = frame('vanguard_top' if calls == 1 else 'vanguard_mid')
            tool.device.image = image
            tool._dock_offset = 0 if calls == 1 else 119
            cards = scanner.scan(image)
            yield cards, [(card, card.y + tool._dock_offset) for card in cards]

        tool._dock_pages = pages
        tool._save_swap_reference = Mock()
        tool.run = Mock(side_effect=GameStuckError('模拟交换后参考刷新未完成'))
        with patch('module.runtime.fleet_formation.FleetNameScanner', side_effect=AssertionError('舰名不可用')), \
                patch('module.runtime.fleet_formation.LevelScanner', side_effect=AssertionError('等级不可用')), \
                patch('module.runtime.fleet_formation.FleetEmotionScanner', side_effect=AssertionError('心情不可用')), \
                patch('module.runtime.fleet_formation.fleet_status', return_value=-1):
            result = tool.swap_slots(1, 4, 4, 5, update_emotion=True)
        self.assertTrue(result['changed'])
        self.assertFalse(result['reference_updated'])
        clicked = tool.device.click.call_args.args[0]
        self.assertEqual(clicked.button[1], 223)
        tool._open_slot.assert_called_once_with(5, 'surface')
        tool._confirm_replacement.assert_called_once()
        tool._commit_formation.assert_called_once()
        tool._save_swap_reference.assert_called_once_with(expected, 'surface')
        self.assertTrue(tool.dock_filter.reset_first)

    def test_reference_move_preserves_unrelated_fleets_and_does_not_guess_names(self):
        tool = runner()
        stored = {'vanguard': {'1': [{'name': '源参考', 'level': 105, 'slot': 4, 'emotion': None}],
                               '4': [{'name': '目标参考', 'level': 99, 'slot': 5, 'emotion': 0}],
                               '6': [{'name': '无关', 'slot': 4}]}}
        tool.config.cross_get.return_value = stored
        expected = {1: {1: None, 2: None, 3: None, 4: '4:5', 5: None, 6: None},
                    4: {1: None, 2: None, 3: None, 4: None, 5: '1:4', 6: None}}
        tool._save_swap_reference(expected, 'surface')
        saved = tool._save_result.call_args.args[0]
        self.assertEqual(saved['vanguard']['1'][0]['name'], '目标参考')
        self.assertEqual(saved['vanguard']['4'][0]['name'], '源参考')
        self.assertEqual(saved['vanguard']['6'], stored['vanguard']['6'])
        self.assertEqual(stored['vanguard']['1'][0]['name'], '源参考')

    def test_uncertain_click_invalidates_only_affected_records_even_if_cleanup_fails(self):
        tool = runner()
        portraits = detail_portraits(frame('detail_surface'))
        occupied = dict.fromkeys(range(1, 7), True)
        tool._capture_formation = Mock(return_value=(None, portraits, occupied))
        tool.config.cross_get.return_value = {'vanguard': {'1': [], '4': [], '6': [{'name': '保留'}]}}
        scanner = VisualDockScanner()
        cards = scanner.scan(tool.device.image)
        source = next(card for card in cards if card.col == 1 and card.y > 200)
        tool._visual_candidates = Mock(return_value=[(source, source.y, 50)])
        tool._dock_pages = Mock(return_value=iter([(cards, [])]))
        tool._dock_offset = 0
        tool._confirm_replacement.side_effect = GameStuckError('模拟点击后画面卡住')
        tool.appear.return_value = True
        tool.dock_reset.side_effect = GameTooManyClickError('模拟清理失败')
        with self.assertRaises(FleetManagementError):
            tool.swap_slots(1, 4, 4, 5)
        tool.device.click.assert_called_once()
        saved = tool._save_result.call_args.args[0]
        self.assertEqual(saved['vanguard'], {'6': [{'name': '保留'}]})
        self.assertTrue(tool.dock_filter.reset_first)

    def test_confirmation_never_uses_digit_ocr_or_submits_an_unchanged_counter(self):
        tool = runner()
        tool.loop = Mock(return_value=iter([None, None, None]))
        tool._formation_visible = Mock(return_value=False)
        tool.appear = Mock(return_value=True)
        tool.handle_popup_confirm = Mock(return_value=False)
        with patch('module.retire.dock.OCR_DOCK_SELECTED.ocr', side_effect=AssertionError('禁止数字OCR')):
            with self.assertRaises(FleetManagementError):
                FleetManagement._confirm_replacement(tool, tool.device.image.copy())
        self.assertNotIn(PICKER_CONFIRM, [call.args[0] for call in tool.device.click.call_args_list])

    def test_navigation_menu_does_not_need_ocr_or_current_fleet_number(self):
        tool = runner()
        tool.ui_ensure = Mock()
        tool._formation_visible = Mock(return_value=True)

        def images(**kwargs):
            for name in ('closed_surface', 'closed_surface', 'menu_surface', 'closed_surface'):
                tool.device.image = frame(name)
                yield tool.device.image

        tool.loop = images
        with patch('module.retire.fleet_management.Timer') as timer:
            timer.return_value.reached.return_value = True
            FleetManagement.fleet_enter(tool, 3)
        self.assertEqual(tool.device.click.call_count, 2)

    def test_native_scroll_geometry_covers_three_rows_without_reading_names(self):
        tool = runner()
        stage = 0

        def images(**kwargs):
            for _ in range(20):
                tool.device.image = frame('vanguard_top' if stage == 0 else 'vanguard_bottom')
                yield tool.device.image

        def drag(*args):
            nonlocal stage
            stage = 1

        from module.retire.assets import DOCK_CHECK
        tool.appear = lambda button, **kwargs: button is DOCK_CHECK
        tool.loop = images
        with patch('module.retire.mind_scan.MindCalculatorScan._drag_rows', side_effect=drag) as move:
            pages = list(tool._dock_pages(VisualDockScanner(), max_rows=3))
        self.assertEqual(len(pages), 2)
        self.assertEqual(tool._dock_offset, 231)
        self.assertEqual(len(pages[-1][1]), 21)
        move.assert_called_once()
        tool.device.swipe.assert_called_once()

    def test_direct_menu_and_arrow_navigation_verify_the_new_highlight(self):
        for method in ('menu', 'arrows'):
            with self.subTest(method=method):
                tool = runner()
                tool._formation_visible = Mock(return_value=True)
                sequence = ['closed_surface', 'closed_surface', 'menu_surface', 'closed_surface']
                if method == 'arrows':
                    sequence.append('closed_surface')
                sequence += ['menu_surface4', 'closed_surface']

                def images(**kwargs):
                    for name in sequence:
                        tool.device.image = frame(name)
                        yield tool.device.image

                tool.loop = images
                with patch('module.retire.fleet_management.Timer') as timer:
                    timer.return_value.reached.return_value = True
                    FleetManagement.fleet_enter(tool, 4, method=method)
                if method == 'arrows':
                    from module.equipment.assets import FLEET_NEXT
                    self.assertIn(FLEET_NEXT, [call.args[0] for call in tool.device.click.call_args_list])
                else:
                    self.assertIn(menu_area(4), [call.args[0].button for call in tool.device.click.call_args_list])


class ReferenceScanTests(unittest.TestCase):
    def test_disabling_morale_update_preserves_existing_slot_values_including_zero(self):
        current = {'vanguard': {'1': [{'slot': 4, 'name': '未知', 'emotion': None},
                                      {'slot': 5, 'name': '花月', 'emotion': None}]}}
        previous = {'vanguard': {'1': [{'slot': 4, 'name': '旧参考', 'emotion': 0},
                                       {'name': '缺少舰位的旧记录', 'emotion': 150}]}}
        preserve_emotions(current, previous)
        self.assertEqual(current['vanguard']['1'][0]['emotion'], 0)
        self.assertIsNone(current['vanguard']['1'][1]['emotion'])

    def test_ocr_failures_keep_all_real_detail_positions_and_unknown_reference_values(self):
        reader = FormationDetailScanner()
        reader.names.scan = Mock(side_effect=RequestHumanTakeover('模拟舰名模型不可用'))
        reader.levels.ocr = Mock(side_effect=ValueError('模拟等级失败'))
        ships = reader.scan(frame('detail_surface'))
        self.assertEqual(set(ships), set(range(1, 7)))
        self.assertTrue(all(ship['occupied'] for ship in ships.values()))
        self.assertTrue(all(ship['name'] == '' for ship in ships.values()))
        self.assertTrue(all(ship['slot'] == slot for slot, ship in ships.items()))

    def test_fixed_detail_positions_do_not_change_when_names_are_wrong(self):
        reader = FormationDetailScanner()
        reader.names.scan = Mock(return_value=['未知文本'] * 6)
        reader.levels.ocr = Mock(return_value=[0] * 6)
        surface = reader.scan(frame('detail_surface'))
        result = combine_references({}, {'surface': {1: surface}, 'submarine': {}})
        self.assertEqual([ship['slot'] for ship in result['main']['1']], [1, 2, 3])
        self.assertEqual([ship['slot'] for ship in result['vanguard']['1']], [4, 5, 6])
        self.assertTrue(all(ship['review'] for ship in result['vanguard']['1']))

    def test_six_native_dock_frames_preserve_32_members_without_duplicate_merging(self):
        scanner = FormationDockScanner()
        expected = {
            'vanguard': {1: {'花月', '文琴佐·焦贝蒂', '阿尔弗雷多·奥里亚尼'},
                         2: {'威廉·D·波特', '岛风', '莫斯科'}, 3: {'热心.改', '命运女神.改', '灵敏·META'},
                         4: {'唐斯.改', '杜威', '艾尔温'}, 5: {'阿瑞托莎', 'Z18', 'Z19'}, 6: {'Z20'}},
            'main': {1: {'拉斐尔', '独角兽.改', '瑞凤'}, 2: {'狮', '苏维埃同盟', '阿尔萨斯'},
                     3: {'雫', '穗香', '伊莉丝'}, 4: {'DEAD MASTER', '环', '优米雅·利斯菲尔德'},
                     5: {'雪不归', '金色暗影', '帕特莉夏·阿贝尔海姆'}, 6: {'博格'}},
        }
        for group in ('vanguard', 'main'):
            merger = FormationCardMerger()
            for index, stage in enumerate(('top', 'mid', 'bottom')):
                image = frame(f'{group}_{stage}')
                cards = scanner.scan(image, has_remove=index == 0)
                merger.add(image, cards)
            members = [card for card, _ in merger.slots if card.fleet > 0]
            self.assertEqual(len(members), 16)
            self.assertEqual({number: {card.ship['name'] for card in members if card.fleet == number}
                              for number in range(1, 7)}, expected[group])

    def test_morale_can_be_skipped_without_calling_its_ocr(self):
        reader = FormationDockScanner(update_emotion=False)
        reader.emotions.scan = Mock(side_effect=AssertionError('不应读取心情'))
        cards = reader.scan(frame('vanguard_top'))
        self.assertTrue(all(card.ship['emotion'] is None for card in cards))
        reader.emotions.scan.assert_not_called()

    def test_native_detail_slots_read_names_and_levels_as_reference(self):
        reader = FormationDetailScanner()
        ships = reader.scan(frame('detail_surface'))
        self.assertEqual([ships[slot]['name'] for slot in (1, 2, 3, 5, 6)],
                         ['拉斐尔', '瑞凤', '独角兽.改', '花月', '文琴佐·焦贝蒂'])
        self.assertEqual([ships[slot]['level'] for slot in range(1, 7)], [125, 125, 125, 105, 105, 105])
        sub = reader.scan(frame('detail_submarine'), 'submarine')
        self.assertEqual(list(sub), [1, 2, 3])
        self.assertEqual(sub[1]['name'], '伊404')
        self.assertEqual(sub[2]['name'], 'U-2501')

    def test_scan_starts_fleet_one_slot_four_and_never_requires_name_confirmation(self):
        tool = runner()
        tool.config.cross_get.return_value = False
        tool._scan_category = Mock(return_value={})
        tool._capture_formation = Mock(side_effect=lambda number, kind:
            (frame(f'detail_{kind}'), {}, {}))
        with (patch('module.retire.fleet_management.FormationDockScanner') as dock,
              patch('module.retire.fleet_management.FormationDetailScanner') as details):
            dock.return_value.matcher.names = ()
            details.return_value.scan.side_effect = lambda image, kind: {
                slot: dict(name='读错也保留', level=0, emotion=None, slot=slot, occupied=True, review=True)
                for slot in range(1, 7 if kind == 'surface' else 4)}
            tool.run(update_emotion=False)
        self.assertEqual(tool._open_slot.call_args_list[0].args, (4, 'surface'))
        self.assertEqual(tool._open_slot.call_args_list[2].args, (1, 'submarine'))
        saved = tool._save_result.call_args.args[0]
        self.assertEqual(len(saved['main']), 6)
        self.assertEqual(len(saved['submarine']), 2)
        self.assertEqual([ship['slot'] for ship in saved['submarine']['2']], [1, 2, 3])
        self.assertTrue(tool.dock_filter.reset_first)


if __name__ == '__main__':
    unittest.main()
