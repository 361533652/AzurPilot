"""原生截图、触控与应用管理的离线回归，不连接模拟器或下载 atx-agent。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

# 先安装项目的 pkg_resources 兼容补丁，不构造设备或加载用户配置。
from module.device.device import Device  # noqa: F401
from module.daemon.benchmark import Benchmark
from module.device.app_control import AppControl
from requests.exceptions import HTTPError


class TestNativeAppControl(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.device.app_control.logger'))
        self.enterContext(patch('module.device.method.adb.logger'))

    def device(self, method):
        device = AppControl.__new__(AppControl)
        device.config = SimpleNamespace(
            Emulator_ControlMethod=method, Emulator_Serial='127.0.0.1:16416',
        )
        device.serial = device.config.Emulator_Serial
        device.package = 'com.bilibili.azurlane'
        device.adb_shell = Mock()
        # 模拟日志中的不可用 agent；原生应用管理不应触发此分支。
        for action in ('app_current', 'app_start', 'app_stop', 'dump_hierarchy'):
            setattr(device, f'{action}_uiautomator2', Mock(side_effect=HTTPError('403 Forbidden')))
        return device

    def test_native_foreground_check_works_without_atx_agent(self):
        for method in ('MaaTouch', 'nemu_ipc'):
            with self.subTest(method=method):
                device = self.device(method)
                device.adb_shell.return_value = (
                    'mCurrentFocus=Window{123abc u0 '
                    'com.bilibili.azurlane/com.manjuu.azurlane.MainActivity}'
                )

                self.assertTrue(device.app_is_running())

                device.adb_shell.assert_called_once_with(['dumpsys', 'window', 'windows'])
                device.app_current_uiautomator2.assert_not_called()

    def test_native_foreground_check_keeps_activity_fallback(self):
        for method in ('MaaTouch', 'nemu_ipc'):
            with self.subTest(method=method):
                device = self.device(method)
                device.adb_shell.side_effect = [
                    'mCurrentFocus=null',
                    'ACTIVITY com.example.launcher/.MainActivity abc123 pid=10\n'
                    'ACTIVITY com.bilibili.azurlane/com.manjuu.azurlane.MainActivity def456 pid=20',
                ]

                self.assertTrue(device.app_is_running())

                self.assertEqual(device.adb_shell.call_args_list, [
                    call(['dumpsys', 'window', 'windows']), call(['dumpsys', 'activity', 'top']),
                ])
                device.app_current_uiautomator2.assert_not_called()

    def test_native_foreground_check_does_not_assume_game_is_running(self):
        device = self.device('MaaTouch')
        device.adb_shell.return_value = 'mCurrentFocus=Window{123abc u0 com.example.launcher/.MainActivity}'

        self.assertFalse(device.app_is_running())

    def test_native_start_stop_and_hierarchy_do_not_require_atx_agent(self):
        for method in ('MaaTouch', 'nemu_ipc'):
            with self.subTest(method=method):
                device = self.device(method)
                device.app_start_adb = Mock()
                device.dump_hierarchy_adb = Mock(return_value=object())
                device._hierarchy_interval = Mock()

                device.app_start()
                device.app_stop()
                hierarchy = device.dump_hierarchy()

                device.app_start_adb.assert_called_once_with()
                device.adb_shell.assert_called_once_with(['am', 'force-stop', device.package])
                self.assertIs(hierarchy, device.dump_hierarchy_adb.return_value)
                for action in ('app_current', 'app_start', 'app_stop', 'dump_hierarchy'):
                    getattr(device, f'{action}_uiautomator2').assert_not_called()

    def test_agent_based_controls_keep_existing_app_routing(self):
        for method in ('uiautomator2', 'minitouch', 'scrcpy'):
            with self.subTest(method=method):
                device = self.device(method)
                device.app_current_uiautomator2.side_effect = None
                device.app_current_uiautomator2.return_value = device.package

                self.assertTrue(device.app_is_running())

                device.app_current_uiautomator2.assert_called_once_with()
                device.adb_shell.assert_not_called()


class TestScreenshotBenchmarkCompatibility(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.daemon.benchmark.logger'))
        self.bench = Benchmark.__new__(Benchmark)
        self.bench.config = SimpleNamespace(
            Benchmark_DeviceType='emulator', Benchmark_TestScene='screenshot',
            Emulator_ScreenshotMethod='auto', Emulator_ControlMethod='MaaTouch',
        )
        self.bench.device = SimpleNamespace(
            serial='127.0.0.1:16416', sdk_ver=35, is_mumu_family=True,
            is_chinac_phone_cloud=False, ldopengl_available=Mock(return_value=False),
        )

    def test_mumu15_auto_selects_working_screenshot_without_agent_download(self):
        clock = [0.0]

        def capture(name, cost):
            def screenshot():
                clock[0] += cost
            return Mock(__name__=name, side_effect=screenshot)

        methods = {
            'ADB': capture('screenshot_adb', 0.4),
            'ADB_nc': capture('screenshot_adb_nc', 0.1),
            'uiautomator2': Mock(__name__='screenshot_uiautomator2', side_effect=HTTPError('403 Forbidden')),
            'DroidCast': capture('screenshot_droidcast', 0.02),
            'DroidCast_raw': capture('screenshot_droidcast_raw', 0.01),
        }
        self.bench.device.screenshot_methods = methods

        with patch('module.daemon.benchmark.time.perf_counter', side_effect=lambda: clock[0]):
            self.assertEqual(self.bench.run_simple_screenshot_benchmark(), 'ADB_nc')

        self.assertEqual(methods['ADB'].call_count, 3)
        self.assertEqual(methods['ADB_nc'].call_count, 3)
        for method in ('uiautomator2', 'DroidCast', 'DroidCast_raw'):
            methods[method].assert_not_called()
        self.assertEqual(self.bench.config.Emulator_ControlMethod, 'MaaTouch')

    def test_auto_respects_droidcast_sdk_boundaries(self):
        self.bench.benchmark = Mock(return_value=('ADB_nc', 'minitouch'))
        for sdk, supported in ((22, False), (23, True), (32, True), (33, False), (35, False)):
            with self.subTest(sdk=sdk):
                self.bench.device.sdk_ver = sdk
                self.bench.run_simple_screenshot_benchmark()
                candidates = self.bench.benchmark.call_args.args[0]
                self.assertEqual('DroidCast' in candidates, supported)
                self.assertEqual('DroidCast_raw' in candidates, supported)

    def test_older_mumu_and_other_emulators_keep_u2_auto_candidate(self):
        self.bench.benchmark = Mock(return_value=('ADB_nc', 'minitouch'))
        for sdk, is_mumu in ((32, True), (35, False)):
            with self.subTest(sdk=sdk, is_mumu=is_mumu):
                self.bench.device.sdk_ver = sdk
                self.bench.device.is_mumu_family = is_mumu
                self.bench.run_simple_screenshot_benchmark()
                self.assertIn('uiautomator2', self.bench.benchmark.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
