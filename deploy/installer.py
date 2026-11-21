from deploy.patch import pre_checks

pre_checks()

import sys

from deploy.adb import AdbManager
from deploy.alas import AlasManager
from deploy.app import AppManager
from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager


class Installer(GitManager, PipManager, AdbManager, AppManager, AlasManager):
    """集成安装器，统筹 Git 更新、进程清理、依赖安装、前端构建及 ADB 服务。"""

    def install(self):
        """执行完整的安装与环境部署流程。

        失败时向 stderr 输出 ``AZURPILOT_ERROR:{code}:{message}`` 标记行
        并以分类错误码退出，供启动器结构化识别。
        """
        from deploy.atomic import atomic_failure_cleanup
        from deploy.sync_upstream import check_upstream, is_local_ahead_of_origin
        atomic_failure_cleanup('./config')
        # 启动期只检查不合并：合并若撞上冲突会把冲突标记写进源码，前端 tsc 报
        # TS1185 直接构建失败，WebUI 起不来。合并改由 WebUI 更新器手动触发。
        try:
            check_upstream()
        except Exception as exc:
            print(f"[Installer] 上游检查失败，继续启动：{exc}")
        try:
            if is_local_ahead_of_origin():
                # 本地领先 origin 说明上一步没能把合并结果推上去。git_install 会执行
                # `git reset --hard origin/<分支>`，那会把刚合并进来的上游提交一并抹掉，
                # 等于白合一轮。这里保留本地提交，交给下次启动的同步流程补推。
                print(
                    "[Installer] 本地领先 origin（推送未完成），已保留合并结果，"
                    "跳过本次覆盖式 Git 更新。"
                )
            else:
                self.git_install()
            self.alas_kill()
            self.pip_install()
            self.app_update()
            self.adb_install()
        except ExecutionError as e:
            code = getattr(e, 'code', 1)
            print(f'AZURPILOT_ERROR:{code}:{e}', file=sys.stderr, flush=True)
            exit(code)


if __name__ == '__main__':
    Installer().install()
