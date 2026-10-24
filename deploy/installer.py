from deploy.patch import pre_checks

pre_checks()

from deploy.adb import AdbManager
from deploy.alas import AlasManager
from deploy.app import AppManager
from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager


class Installer(GitManager, PipManager, AdbManager, AppManager, AlasManager):
    """集成安装器，统筹 Git 更新、进程清理、依赖安装、前端构建及 ADB 服务。"""

    def install(self):
        """执行完整的安装与环境部署流程。"""
        from deploy.atomic import atomic_failure_cleanup
        from deploy.sync_upstream import is_local_ahead_of_origin, sync_with_upstream
        atomic_failure_cleanup('./config')
        if not sync_with_upstream():
            print("[Installer] 上游同步发生代码冲突，安装与启动流程已阻断。")
            exit(1)
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
        except ExecutionError:
            exit(1)


if __name__ == '__main__':
    Installer().install()
