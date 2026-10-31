"""
上游官方仓库自动同步器。

在启动时把 upstream（官方仓库）的最新代码合并进当前分支，并把结果推送到
个人的 Fork 仓库（origin）。若检测到代码冲突，立即阻断启动并输出排查指引。

本模块只在启动链路最前端运行，刻意不实例化 DeployConfig：配置对象的初始化
可能触发配置重写，而这里只需要读写 Git 元数据。

关键约定：本地领先 origin 时不得执行覆盖式更新（见 is_local_ahead_of_origin
与 deploy/installer.py 的配合）。否则一次推送失败就会让刚合并进来的上游提交
被 `git reset --hard origin/<分支>` 抹掉，白合一轮。
"""

import subprocess
import sys
from typing import List, Optional

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
ORIGIN_NAME = "origin"
STASH_MESSAGE = "auto-sync-pre-stash"


def run_git(
    cmd: List[str], check: bool = False, capture_output: bool = True
) -> subprocess.CompletedProcess:
    """运行 Git 命令并统一编码。

    Args:
        cmd: 完整命令行列表，首元素为 ``git``。
        check: 是否在非零退出码时抛出 CalledProcessError。
        capture_output: 是否捕获标准输出与标准错误。

    Returns:
        subprocess.CompletedProcess: 命令执行结果。
    """
    return subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def get_current_branch() -> str:
    """获取当前所在的 Git 分支名。

    Returns:
        str: 分支名；处于游离头指针时返回 ``HEAD``。
    """
    res = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = res.stdout.strip()
    return branch or "master"


def get_remote_names() -> List[str]:
    """列出已配置的远程仓库名称。

    Returns:
        List[str]: 远程仓库名列表，命令失败时为空列表。
    """
    res = run_git(["git", "remote"])
    if res.returncode != 0:
        return []
    return [name.strip() for name in res.stdout.splitlines() if name.strip()]


def ensure_upstream() -> bool:
    """确保 upstream 远程仓库已配置为官方地址。

    Returns:
        bool: 已存在或添加成功返回 True。
    """
    if UPSTREAM_NAME in get_remote_names():
        return True
    print(f"[Sync] 未检测到 {UPSTREAM_NAME} 远程源，正在添加 {UPSTREAM_URL} ...")
    return run_git(["git", "remote", "add", UPSTREAM_NAME, UPSTREAM_URL]).returncode == 0


def check_dirty_worktree() -> bool:
    """检查已跟踪文件是否存在未提交的改动。

    仅看已跟踪文件：未跟踪文件不会参与合并，也不需要暂存。

    Returns:
        bool: 存在未提交改动返回 True。
    """
    res1 = run_git(["git", "diff", "--quiet"])
    res2 = run_git(["git", "diff", "--cached", "--quiet"])
    return res1.returncode != 0 or res2.returncode != 0


def get_conflicted_files() -> List[str]:
    """获取处于合并冲突状态的文件列表。

    Returns:
        List[str]: 冲突文件相对路径列表。
    """
    res = run_git(["git", "diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def has_origin_remote() -> bool:
    """判断是否配置了 origin 远程仓库。

    Returns:
        bool: 存在 origin 返回 True。
    """
    return ORIGIN_NAME in get_remote_names()


def is_local_ahead_of_origin(branch: Optional[str] = None) -> bool:
    """判断本地分支是否存在尚未推送到 origin 的提交。

    调用方用它决定能否执行覆盖式的 `git reset --hard origin/<分支>`：
    本地领先时该重置会丢弃尚未推送的提交（包括刚合并进来的上游更新），
    因此必须跳过。

    Returns:
        bool: 本地领先 origin 返回 True；未配置 origin 时返回 False，
              让上层继续执行能够自动补齐远程仓库的初始化流程。
    """
    if not has_origin_remote():
        return False
    branch = branch or get_current_branch()
    remote_ref = f"{ORIGIN_NAME}/{branch}"
    if run_git(["git", "rev-parse", "--verify", "--quiet", remote_ref]).returncode != 0:
        # origin 上还没有这个分支：本地提交全部算领先，推送时会新建分支。
        return (
            run_git(["git", "rev-parse", "--verify", "--quiet", "HEAD"]).returncode == 0
        )
    res = run_git(["git", "log", f"{remote_ref}..HEAD", "--oneline"])
    return bool(res.stdout.strip())


def push_to_origin(branch: Optional[str] = None) -> bool:
    """把当前分支推送到 origin，无需推送时视为成功。

    Args:
        branch: 目标分支名，缺省使用当前分支。

    Returns:
        bool: 已推送、无需推送或推送成功返回 True；推送失败返回 False。
    """
    if not has_origin_remote():
        print(f"⚠️ [Sync] 未配置 {ORIGIN_NAME} 远程源，跳过推送（更新保留在本地）。")
        return False

    branch = branch or get_current_branch()
    if not is_local_ahead_of_origin(branch):
        print(f"✅ [Sync] {ORIGIN_NAME}/{branch} 已是最新，无需推送。")
        return True

    print(f"📤 [Sync] 本地领先 {ORIGIN_NAME}/{branch}，正在推送...")
    push_res = run_git(["git", "push", ORIGIN_NAME, branch])
    if push_res.returncode == 0:
        print(f"✨ [Sync] 个人 Fork 仓库已同步更新至最新！")
        return True
    detail = (push_res.stderr or push_res.stdout or "").strip().splitlines()
    if detail:
        print(f"⚠️ [Sync] 推送至 {ORIGIN_NAME} 失败：{detail[-1]}")
    print(
        f"⚠️ [Sync] 推送未完成（网络波动或权限问题），"
        f"更新已保留在本地，下次启动会自动重试推送。"
    )
    return False


def get_head_revision() -> str:
    """读取当前 HEAD 的完整 SHA。

    Returns:
        str: 提交 SHA；读取失败时返回空字符串。
    """
    return run_git(["git", "rev-parse", "HEAD"]).stdout.strip()


def dependencies_changed(before: str, after: str = "HEAD") -> bool:
    """判断两次提交之间是否改动了 Python 依赖声明。

    只有依赖声明发生变化时才值得重新同步环境：一方面新依赖会导致
    WebUI 导入失败，另一方面启动前的依赖同步失败会直接拦住启动，
    不应让与依赖无关的代码合并触发这个门槛。

    Args:
        before: 合并前的提交 SHA。
        after: 合并后的提交引用，默认 HEAD。

    Returns:
        bool: 改动涉及 pyproject.toml 或 uv.lock 时返回 True。
    """
    if not before:
        return False
    res = run_git(["git", "diff", "--name-only", before, after])
    if res.returncode != 0:
        return False
    changed = {line.strip() for line in res.stdout.splitlines() if line.strip()}
    return bool(changed & {"pyproject.toml", "uv.lock"})


def mark_dependency_sync_pending() -> None:
    """标记「启动 WebUI 前必须重新同步依赖」。

    合并会改写工作区代码，可能引入尚未安装的依赖（例如新增的 niquests）。
    父监督器只在存在该标记时才执行启动前依赖同步，因此合并后必须主动标记，
    否则 WebUI 会按旧环境导入新代码并崩溃。标记由监督器在同步成功后清除。
    """
    try:
        from module.runtime.setting import mark_dependency_sync_pending as mark
    except Exception as exc:  # 早期启动阶段导入失败不应阻断启动
        print(f"⚠️ [Sync] 无法导入依赖同步标记接口，依赖可能未同步：{exc}")
        return
    try:
        mark()
        print("📦 [Sync] 本次合并改动了依赖声明，已标记启动前重新同步依赖。")
    except OSError as exc:
        print(f"⚠️ [Sync] 写入依赖同步标记失败，依赖可能未同步：{exc}")


def _print_conflict_report(conflicts: List[str]) -> None:
    """打印醒目的冲突阻断报告。

    Args:
        conflicts: 冲突文件列表。
    """
    print("\n" + "!" * 64)
    print("🚨 【AzurPilot 启动拦截】检测到与官方上游仓库存在代码冲突！")
    print("!" * 64)
    print("⚠️ 合并已被暂停，冲突文件列表如下：")
    for path in conflicts:
        print(f"   ❌ {path}")
    print("\n👉 已为您安全阻断启动，保护本地代码环境！")
    print("👉 请直接前往 DSH 会话告诉小肥鱼，小肥鱼将为您精准排查并双向兼容合并喵~♡")
    print("!" * 64 + "\n")


def sync_with_upstream() -> bool:
    """执行上游同步、冲突检测与 Fork 推送。

    Returns:
        bool: True 表示同步完成或无需同步且环境安全；
              False 表示发生冲突已阻断启动。
    """
    print("=" * 60)
    print("🌊 [AzurPilot] 启动前检查：正在同步官方上游代码...")
    print("=" * 60)

    if not ensure_upstream():
        print(f"❌ [Sync] 配置 {UPSTREAM_NAME} 远程仓库失败，跳过同步继续启动。")
        return True

    branch = get_current_branch()
    print(f"📌 [Sync] 当前分支: {branch}")

    has_stash = False
    merge_conflicted = False
    if check_dirty_worktree():
        print("⚠️ [Sync] 检测到工作区有未提交的代码改动，正在自动创建临时暂存...")
        stash_res = run_git(["git", "stash", "push", "-m", STASH_MESSAGE])
        has_stash = "Saved working directory" in stash_res.stdout

    try:
        print(f"📥 [Sync] 正在拉取官方上游最新状态 (git fetch {UPSTREAM_NAME})...")
        fetch_res = run_git(["git", "fetch", UPSTREAM_NAME])
        if fetch_res.returncode != 0:
            print("⚠️ [Sync] 无法连接到上游仓库（可能网络波动），本次跳过合并。")
        else:
            print(f"📥 [Sync] 正在拉取个人 Fork 最新状态 (git fetch {ORIGIN_NAME})...")
            if has_origin_remote():
                run_git(["git", "fetch", ORIGIN_NAME])

            upstream_ref = f"{UPSTREAM_NAME}/{branch}"
            if run_git(["git", "rev-parse", "--verify", "--quiet", upstream_ref]).returncode != 0:
                print(f"ℹ️ [Sync] 上游不存在对应分支 {upstream_ref}，跳过合并。")
            else:
                new_commits = run_git(
                    ["git", "log", f"HEAD..{upstream_ref}", "--oneline"]
                ).stdout.strip()
                if not new_commits:
                    print(
                        f"✅ [Sync] 当前分支已包含官方 {upstream_ref} 的所有最新代码，无需合并。"
                    )
                else:
                    print("🚀 [Sync] 发现官方最新更新，正在合并至本地...")
                    head_before = get_head_revision()
                    merge_res = run_git(
                        [
                            "git",
                            "merge",
                            upstream_ref,
                            "--no-edit",
                            "-m",
                            f"merge: 同步官方 {upstream_ref} 更新",
                        ]
                    )
                    conflicts = get_conflicted_files()
                    if merge_res.returncode != 0 or conflicts:
                        merge_conflicted = True
                        _print_conflict_report(conflicts)
                        return False
                    print("🎉 [Sync] 成功合并官方更新！")
                    # 合并会引入新依赖，而启动器的依赖同步发生在本次合并之前，
                    # 必须标记让父监督器在创建 WebUI 前重新同步，否则导入即崩溃。
                    if dependencies_changed(head_before):
                        mark_dependency_sync_pending()

        # 合并与否都要尝试推送：上一次推送失败时，合并结果仍留在本地，
        # 这里负责在下次启动把它补推到 Fork。
        push_to_origin(branch)
        return True

    finally:
        # 冲突未解决时不能 pop：工作区正处于合并中间态，恢复了也无法编译。
        # 暂存条目会完整保留，排错时可用 `git stash list` 找回。
        if has_stash and not merge_conflicted:
            print("🔄 [Sync] 正在恢复之前未提交的修改 (git stash pop)...")
            run_git(["git", "stash", "pop"])
            if get_conflicted_files():
                print("⚠️ [Sync] 恢复工作区修改时产生局部冲突，请查看相应文件！")
        elif has_stash and merge_conflicted:
            print(
                "📦 [Sync] 未提交的改动已保留在 git stash 中（合并冲突未解决，暂不恢复）。"
            )


if __name__ == "__main__":
    sys.exit(0 if sync_with_upstream() else 1)
