"""上游同步的依赖变化判定回归。

覆盖导致 WebUI 启动失败的场景：合并 upstream 引入新依赖后，必须被识别为
「依赖声明有变化」，从而触发启动前依赖同步。
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.sync_upstream import check_upstream, dependencies_changed, push_to_origin


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


class CheckUpstreamTests(unittest.TestCase):
    """验证启动期只检查不合并。

    启动期合并一旦撞上冲突，会把冲突标记写进源码，前端 tsc 报 TS1185 直接
    构建失败、WebUI 起不来。所以 check_upstream 必须只读：只 fetch 与统计落后
    提交数，绝不改动 HEAD 与工作区。
    """

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._cwd)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        self.repo = root / "work"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True,
                       capture_output=True, text=True, encoding="utf-8")
        subprocess.run(["git", "init", "-b", "master", str(self.repo)], check=True,
                       capture_output=True, text=True, encoding="utf-8")
        for key, value in (("user.email", "test@example.com"), ("user.name", "test")):
            run_git(self.repo, "config", key, value)
        run_git(self.repo, "remote", "add", "origin", str(self.remote))
        run_git(self.repo, "remote", "add", "upstream", str(self.remote))
        (self.repo / "shared.txt").write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "base")
        run_git(self.repo, "push", "-u", "origin", "master")
        # 远端（同时充当 upstream）改同一文件，制造真正的冲突前提。
        other = root / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)], check=True,
                       capture_output=True, text=True, encoding="utf-8")
        run_git(other, "config", "user.email", "test@example.com")
        run_git(other, "config", "user.name", "test")
        (other / "shared.txt").write_text("upstream-changed\n", encoding="utf-8")
        run_git(other, "add", ".")
        run_git(other, "commit", "-m", "upstream change")
        run_git(other, "push", "origin", "master")
        # 本地也改同一文件。
        (self.repo / "shared.txt").write_text("local-changed\n", encoding="utf-8")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "local change")
        self.head = self.git("rev-parse", "HEAD")
        os.chdir(self.repo)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def test_check_never_merges_or_dirties_worktree(self):
        """即使上游改到同一文件，检查也只报告有新提交，不改 HEAD、不产生冲突标记。"""
        check_upstream(force=True)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        content = (self.repo / "shared.txt").read_text(encoding="utf-8")
        self.assertNotIn("<<<<<<<", content)
        self.assertEqual(content.strip(), "local-changed")
        # 只读检查不会创建 MERGE_HEAD。
        self.assertFalse((self.repo / ".git" / "MERGE_HEAD").exists())


class PushToOriginTests(unittest.TestCase):
    """验证推送被非快进拒绝时会先合并 Fork 历史再重试。

    场景：Fork 上出现了本地没有的提交（网页直接编辑、另一台机器推送），
    普通 push 会永久失败，必须先把远端提交合并进来。
    """

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._cwd)
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        self.repo = root / "work"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True,
                       capture_output=True, text=True, encoding="utf-8")
        subprocess.run(["git", "init", "-b", "master", str(self.repo)], check=True,
                       capture_output=True, text=True, encoding="utf-8")
        for key, value in (("user.email", "test@example.com"), ("user.name", "test")):
            run_git(self.repo, "config", key, value)
        run_git(self.repo, "remote", "add", "origin", str(self.remote))
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "base")
        run_git(self.repo, "push", "-u", "origin", "master")
        # 模拟另一处修改并推到 Fork：本地此时落后，远端 ahead。
        other = root / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)], check=True,
                       capture_output=True, text=True, encoding="utf-8")
        run_git(other, "config", "user.email", "test@example.com")
        run_git(other, "config", "user.name", "test")
        (other / "remote.txt").write_text("remote\n", encoding="utf-8")
        run_git(other, "add", ".")
        run_git(other, "commit", "-m", "remote change")
        run_git(other, "push", "origin", "master")
        # 本地再做一个自己的提交：两边分叉，push 必然非快进。
        (self.repo / "local.txt").write_text("local\n", encoding="utf-8")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "local change")
        run_git(self.repo, "fetch", "origin")
        os.chdir(self.repo)

    def test_non_fast_forward_push_merges_remote_history(self):
        """Fork 上有本地没有的提交时，推送应通过合并后重试成功。"""
        self.assertTrue(push_to_origin("master"))
        # 合并后本地历史应同时包含两边内容。
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.repo, check=True,
                             capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("local change", log)
        self.assertIn("remote change", log)
        # 推送后 Fork 与本地一致。
        remote_head = subprocess.run(["git", "rev-parse", "master"], cwd=self.remote, check=True,
                                     capture_output=True, text=True, encoding="utf-8").stdout.strip()
        local_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                                    capture_output=True, text=True, encoding="utf-8").stdout.strip()
        self.assertEqual(remote_head, local_head)


if __name__ == "__main__":
    unittest.main()
