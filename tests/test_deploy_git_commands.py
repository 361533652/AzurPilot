"""Run the deploy shell path against disposable repositories, without networking."""

import shutil
import subprocess
from unittest.mock import Mock

import pytest

from deploy.config import ExecutionError as DeployExecutionError
from deploy.git import GitManager as DeployGitManager
from deploy.http import BROWSER_USER_AGENT, GIT_HTTP_OPTIONS
from deploy.Windows.config import ExecutionError as WindowsExecutionError
from deploy.Windows.git import GitManager as WindowsGitManager


MANAGERS = (DeployGitManager, WindowsGitManager)


@pytest.mark.parametrize('manager_class', MANAGERS)
@pytest.mark.parametrize('absolute_executable', [False, True])
@pytest.mark.parametrize('output', [False, True])
def test_deploy_executor_accepts_quoted_git_http_options(
        tmp_path, monkeypatch, manager_class, absolute_executable, output):
    monkeypatch.chdir(tmp_path)
    manager = object.__new__(manager_class)
    executable = shutil.which('git') if absolute_executable else 'git'
    command = f'"{executable}" {GIT_HTTP_OPTIONS} -c http.userAgent=git/2.28.7.windows.3 init'

    assert manager.execute(command, output=output)
    assert (tmp_path / '.git' / 'HEAD').is_file()


@pytest.mark.parametrize('manager_class', MANAGERS)
def test_deploy_executor_keeps_full_browser_header(tmp_path, monkeypatch, manager_class):
    monkeypatch.chdir(tmp_path)
    manager = object.__new__(manager_class)
    target = tmp_path / 'browser header.txt'
    command = (f'"{shutil.which("git")}" {GIT_HTTP_OPTIONS} -c http.userAgent=third-party-ua '
               f'config --get-urlmatch http.userAgent https://git.nanoda.work/git/AzurPilot > "{target}"')

    assert manager.execute(command)
    assert target.read_text().strip() == BROWSER_USER_AGENT


@pytest.mark.parametrize('manager_class', MANAGERS)
def test_fetch_uses_real_deploy_executor(tmp_path, monkeypatch, manager_class):
    executable = shutil.which('git')
    source, destination = tmp_path / 'source repository', tmp_path / 'destination'
    subprocess.run([executable, 'init', '--bare', str(source)], check=True, capture_output=True)
    tree = subprocess.run([executable, '-C', str(source), 'mktree'], input='', text=True,
                          check=True, capture_output=True).stdout.strip()
    commit = subprocess.run(
        [executable, '-C', str(source), '-c', 'user.name=Deploy Test', '-c', 'user.email=test@example.invalid',
         'commit-tree', tree], input='fixture\n', text=True, check=True, capture_output=True,
    ).stdout.strip()
    subprocess.run([executable, '-C', str(source), 'update-ref', 'refs/heads/test', commit],
                   check=True, capture_output=True)
    # Bundles exercise fetch without a network or an external upload-pack shell.
    bundle = tmp_path / 'source repository.bundle'
    subprocess.run([executable, '-C', str(source), 'bundle', 'create', str(bundle), 'test'],
                   check=True, capture_output=True)
    subprocess.run([executable, 'init', str(destination)], check=True, capture_output=True)
    subprocess.run([executable, '-C', str(destination), 'remote', 'add', 'origin', str(bundle)],
                   check=True, capture_output=True)
    monkeypatch.chdir(destination)
    manager = object.__new__(manager_class)
    manager.git = executable
    manager.config = {}

    manager._fetch_with_retry('origin', 'test', max_retry=1)

    fetched = subprocess.run([executable, 'rev-parse', 'FETCH_HEAD'], check=True,
                             capture_output=True, text=True).stdout.strip()
    assert fetched == commit


@pytest.mark.parametrize('manager_class, error_class', [
    (DeployGitManager, DeployExecutionError), (WindowsGitManager, WindowsExecutionError),
])
def test_init_failure_preserves_existing_repository_metadata(tmp_path, monkeypatch, manager_class, error_class):
    monkeypatch.chdir(tmp_path)
    repository = tmp_path / '.git'
    repository.mkdir()
    for name in ('config', 'index', 'HEAD', 'ORIG_HEAD'):
        (repository / name).write_text('preserve-' + name)
    manager = object.__new__(manager_class)
    manager.git = 'git'

    def fail(command, allow_failure=False):
        if allow_failure:
            return False
        raise error_class(10, 'command could not start')

    manager.execute = Mock(side_effect=fail)
    with pytest.raises(error_class):
        manager.git_repository_init('https://git.nanoda.work/git/AzurPilot')

    manager.execute.assert_called_once_with('"git" init')
    for name in ('config', 'index', 'HEAD', 'ORIG_HEAD'):
        assert (repository / name).read_text() == 'preserve-' + name


@pytest.mark.parametrize('manager_class', MANAGERS)
def test_local_repository_commands_do_not_carry_http_options(tmp_path, monkeypatch, manager_class):
    monkeypatch.chdir(tmp_path)
    manager = Mock(spec=manager_class)
    manager.git = 'git'
    manager.git_config = Mock()
    manager.git_config.check.return_value = True
    # Exercise the Windows pull fallback too; no command actually runs here.
    manager.execute.side_effect = lambda command, **kwargs: ' checkout ' not in command

    manager_class.git_repository_init(manager, 'https://git.nanoda.work/git/AzurPilot')

    for call in manager.execute.call_args_list:
        command = call.args[0]
        if ' pull ' in command:
            assert GIT_HTTP_OPTIONS in command
        else:
            assert GIT_HTTP_OPTIONS not in command
    manager._fetch_with_retry.assert_called_once_with('origin', 'master')
