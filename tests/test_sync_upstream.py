"""上游同步的依赖变化判定回归。

覆盖导致 WebUI 启动失败的场景：合并 upstream 引入新依赖后，必须被识别为
「依赖声明有变化」，从而触发启动前依赖同步。
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.sync_upstream import dependencies_changed


def run_git(repo, *args):
    """在指定仓库执行 git 命令。

    Args:
        repo: 仓库目录。
        *args: 传给 git 的参数。
    """
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class DependenciesChangedTests(unittest.TestCase):
    """在临时仓库里验证依赖声明变化的识别。"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        run_git(self.repo, "init")
        run_git(self.repo, "config", "user.email", "test@example.com")
        run_git(self.repo, "config", "user.name", "test")
        self.write("pyproject.toml", "initial\n")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "initial")
        self.base = self.head()
        # dependencies_changed 通过进程工作目录执行 git，测试需切到临时仓库。
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def write(self, name, content):
        """写入文件。

        Args:
            name: 相对文件名。
            content: 文件内容。
        """
        (Path(self.repo) / name).write_text(content, encoding="utf-8")

    def commit(self, message):
        """提交当前全部改动。

        Args:
            message: 提交说明。
        """
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", message)

    def head(self):
        """读取当前 HEAD。

        Returns:
            str: 提交 SHA。
        """
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return res.stdout.strip()

    def test_pyproject_change_detected(self):
        """pyproject.toml 变化应判定为依赖声明变化。"""
        self.write("pyproject.toml", "niquests>=3.21.2,<4\n")
        self.commit("add niquests")
        self.assertTrue(dependencies_changed(self.base))

    def test_uv_lock_change_detected(self):
        """uv.lock 变化应判定为依赖声明变化。"""
        self.write("uv.lock", "lock-content\n")
        self.commit("update lock")
        self.assertTrue(dependencies_changed(self.base))

    def test_unrelated_change_ignored(self):
        """只改业务代码时不应触发依赖同步。"""
        self.write("module.py", "VALUE = 1\n")
        self.commit("change module")
        self.assertFalse(dependencies_changed(self.base))

    def test_no_change_ignored(self):
        """没有新提交时不应触发依赖同步。"""
        self.assertFalse(dependencies_changed(self.base))

    def test_missing_base_returns_false(self):
        """合并前 HEAD 读取失败时按无变化处理，不阻断启动。"""
        self.assertFalse(dependencies_changed(""))


if __name__ == "__main__":
    unittest.main()
