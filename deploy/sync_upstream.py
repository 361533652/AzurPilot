"""
上游官方仓库自动同步器。

在启动时检查并合并 upstream（官方仓库）的最新代码至当前分支，
并在无冲突时自动推送至个人的 Fork 仓库 (origin)。
若检测到代码冲突，将立即阻断启动并输出醒目的排查指引。
"""

import os
import subprocess
import sys
from typing import List, Optional, Tuple

# 兼容 Windows 控制台，防止 emoji/特殊符号打印导致 UnicodeEncodeError
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UPSTREAM_URL = "https://github.com/wess09/AzurPilot.git"
UPSTREAM_NAME = "upstream"


def run_git(cmd: List[str], check: bool = False, capture_output: bool = True) -> subprocess.CompletedProcess:
    """运行 Git 命令并统一编码。"""
    return subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def get_current_branch() -> str:
    """获取当前所在 Git 分支。"""
    res = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = res.stdout.strip()
    return branch or "master"


def ensure_upstream() -> bool:
    """检查并确保 upstream 远程仓库已配置。"""
    res = run_git(["git", "remote", "-v"])
    remotes = res.stdout
    if UPSTREAM_NAME not in remotes:
        print(f"[Sync] 未检测到 {UPSTREAM_NAME} 远程源，正在添加 {UPSTREAM_URL} ...")
        add_res = run_git(["git", "remote", "add", UPSTREAM_NAME, UPSTREAM_URL])
        return add_res.returncode == 0
    return True


def check_dirty_worktree() -> bool:
    """检查跟踪的文件是否有未暂存或未提交的修改。"""
    res1 = run_git(["git", "diff", "--quiet"])
    res2 = run_git(["git", "diff", "--cached", "--quiet"])
    return res1.returncode != 0 or res2.returncode != 0


def get_conflicted_files() -> List[str]:
    """获取发生合并冲突的文件列表。"""
    res = run_git(["git", "diff", "--name-only", "--diff-filter=U"])
    files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
    return files


def sync_with_upstream() -> bool:
    """
    执行上游同步与冲突检测。
    
    Returns:
        bool: True 表示同步完成或无需同步且环境安全；
              False 表示发生冲突已阻断。
    """
    print("=" * 60)
    print("🌊 [AzurPilot] 启动前检查：正在同步官方上游代码...")
    print("=" * 60)

    if not ensure_upstream():
        print(f"❌ [Sync] 配置 {UPSTREAM_NAME} 远程仓库失败，跳过同步继续启动。")
        return True

    branch = get_current_branch()
    print(f"📌 [Sync] 当前分支: {branch}")

    # 1. 检查工作区脏状态（仅检查已跟踪文件的改动）
    is_dirty = check_dirty_worktree()
    if is_dirty:
        print("⚠️ [Sync] 检测到工作区有未提交的代码改动，正在自动创建临时暂存...")
        stash_res = run_git(["git", "stash", "push", "-m", "auto-sync-pre-stash"])
        has_stash = "Saved working directory" in stash_res.stdout
    else:
        has_stash = False

    try:
        # 2. 拉取 upstream 最新 commit
        print(f"📥 [Sync] 正在拉取官方上游最新状态 (git fetch {UPSTREAM_NAME})...")
        fetch_res = run_git(["git", "fetch", UPSTREAM_NAME])
        if fetch_res.returncode != 0:
            print("⚠️ [Sync] 无法连接到上游 GitHub 仓库（可能网络波动），跳过本次同步并继续启动。")
            return True

        # 检查上游是否存在同名分支
        branch_check = run_git(["git", "rev-parse", "--verify", f"{UPSTREAM_NAME}/{branch}"])
        if branch_check.returncode != 0:
            print(f"ℹ️ [Sync] 上游不存在对应分支 {UPSTREAM_NAME}/{branch}，跳过合并。")
            return True

        # 3. 检查是否有新提交需要合并
        log_res = run_git(["git", "log", f"HEAD..{UPSTREAM_NAME}/{branch}", "--oneline"])
        new_commits = log_res.stdout.strip()
        if not new_commits:
            print(f"✅ [Sync] 当前分支已包含官方 {UPSTREAM_NAME}/{branch} 的所有最新代码，无需合并。")
            return True

        print(f"🚀 [Sync] 发现官方最新更新，正在合并至本地...")
        # 4. 执行 merge
        merge_res = run_git(
            ["git", "merge", f"{UPSTREAM_NAME}/{branch}", "--no-edit", "-m", f"merge: 同步官方 {UPSTREAM_NAME}/{branch} 更新"]
        )

        # 5. 检查冲突
        conflicts = get_conflicted_files()
        if merge_res.returncode != 0 or conflicts:
            print("\n" + "!" * 64)
            print("🚨 【AzurPilot 启动拦截】检测到与官方上游仓库存在代码冲突！")
            print("!" * 64)
            print("⚠️ 合并已被暂停，冲突文件列表如下：")
            for f in conflicts:
                print(f"   ❌ {f}")
            print("\n👉 已为您安全阻断启动，保护本地代码环境！")
            print("👉 请直接前往 DSH 会话告诉小肥鱼，小肥鱼将为您精准排查并双向兼容合并喵~♡")
            print("!" * 64 + "\n")
            return False

        print(f"🎉 [Sync] 成功合并官方更新！")

        # 6. 成功合并后推送到个人 Fork (origin)
        print(f"📤 [Sync] 正在将最新代码推送至个人 Fork (origin/{branch})...")
        push_res = run_git(["git", "push", "origin", branch])
        if push_res.returncode == 0:
            print(f"✨ [Sync] 个人 Fork 仓库已同步更新至最新！")
        else:
            print(f"⚠️ [Sync] 推送至 origin 失败（网络波动或权限问题），本地合并仍已生效。")

        return True

    finally:
        # 如果之前暂存了未提交的修改，恢复它
        if has_stash:
            print("🔄 [Sync] 正在恢复之前未提交的修改 (git stash pop)...")
            pop_res = run_git(["git", "stash", "pop"])
            pop_conflicts = get_conflicted_files()
            if pop_conflicts:
                print("⚠️ [Sync] 恢复工作区修改时产生局部冲突，请查看相应文件！")


if __name__ == "__main__":
    success = sync_with_upstream()
    if not success:
        sys.exit(1)
    sys.exit(0)
