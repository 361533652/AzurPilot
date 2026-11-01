"""上游同步器的超时与冷却回归。

覆盖启动被慢速网络挂死的场景：git fetch 拉取 GitHub 可能超过 2 分钟，
同步发生在启动链路上，必须有墙钟超时与进程树回收，
否则界面会无限停在「正在启动」。
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import psutil

from deploy.sync_upstream import (
    FETCH_COOLDOWN_MINUTES,
    _fetch_is_recent,
    _mark_fetched,
    run_git,
)


class RunGitTimeoutTests(unittest.TestCase):
    """run_git 的超时与进程回收。"""

    # 用解释器自身构造挂起进程，跨平台且不依赖外部命令。
    SLEEP_CODE = "import time,os; print('CHILD-PID', os.getpid(), flush=True); time.sleep(600)"

    def test_timeout_returns_124_and_kills_child(self):
        """超时按 124 返回，并结束子进程，不留下挂死进程。"""
        started = time.monotonic()
        res = run_git([sys.executable, "-c", self.SLEEP_CODE], timeout=3)
        elapsed = time.monotonic() - started

        self.assertEqual(res.returncode, 124)
        self.assertLess(elapsed, 20, "超时后应尽快返回，不应等待子进程睡满 600 秒")

        # 从输出里拿到孙进程 PID，确认已被回收。
        child_pid = None
        for line in (res.stdout or "").splitlines():
            if line.startswith("CHILD-PID"):
                child_pid = int(line.split()[1])
        if child_pid is not None:
            self.assertFalse(
                psutil.pid_exists(child_pid),
                f"超时后子进程 {child_pid} 仍存活，进程树未被回收",
            )

    def test_no_timeout_runs_normally(self):
        """不传 timeout 时保持原有行为。"""
        res = run_git([sys.executable, "-c", "print('ok')"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("ok", res.stdout)


class FetchCooldownTests(unittest.TestCase):
    """冷却期内跳过网络 fetch。"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        # 时间戳文件在 config/ 下，切到临时目录即可隔离。
        Path("config").mkdir(exist_ok=True)
        self.stamp = Path("config") / "sync-upstream-last-fetch"

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_no_stamp_not_recent(self):
        """从未拉取过时应执行网络 fetch。"""
        self.assertFalse(_fetch_is_recent())

    def test_mark_then_recent(self):
        """成功拉取后处于冷却期内，跳过 fetch。"""
        _mark_fetched()
        self.assertTrue(self.stamp.exists())
        self.assertTrue(_fetch_is_recent())

    def test_expired_stamp_not_recent(self):
        """超过冷却期后仍需拉取。"""
        self.stamp.write_text(str(time.time() - (FETCH_COOLDOWN_MINUTES * 60 + 5)))
        self.assertFalse(_fetch_is_recent())

    def test_garbage_stamp_not_recent(self):
        """时间戳损坏时按未拉取处理，不抛异常。"""
        self.stamp.write_text("not-a-number", encoding="utf-8")
        self.assertFalse(_fetch_is_recent())


if __name__ == "__main__":
    unittest.main()
