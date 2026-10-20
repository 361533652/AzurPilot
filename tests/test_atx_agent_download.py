"""ATX 下载回归测试：隔离网络、缓存目录和设备，不操作真实模拟器。"""

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from urllib3.exceptions import ProtocolError, ReadTimeoutError

from module.device.method import utils
from deploy import patch as deploy_patch
from deploy.Windows import patch as windows_patch


class TestAtxAgentDownload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.downloaders = [('运行时', utils.u2.init.__dict__, utils.mirror_download)]
        source = Path(utils.u2.init.__file__).read_text(encoding='utf-8')
        # 允许在已运行过新版部署补丁的虚拟环境中再次执行测试。
        start = source.find("    if url.startswith('https://atx.azurpilot.com/'):")
        if start >= 0:
            end = source.index('    github_host = "https://github.com"', start)
            source = source[:start] + source[end:]
        mirror_return = "return 'https://atx.azurpilot.com/%s' % (name.format(v=__atx_agent_version__))"
        github_return = (
            "return GITHUB_BASEURL + '/atx-agent/releases/download/%s/%s' % (\n"
            "            __atx_agent_version__, name.format(v=__atx_agent_version__))"
        )
        source = source.replace(mirror_return, github_return)

        for patcher in (deploy_patch, windows_patch):
            for legacy in (False, True):
                name = f'{patcher.__name__}（旧补丁={legacy}）'
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    init_file = root / 'uiautomator2' / 'init.py'
                    init_file.parent.mkdir()
                    (root / 'uiautomator2cache' / 'cache').mkdir(parents=True)
                    initial = source.replace(github_return, mirror_return) if legacy else source
                    init_file.write_text(initial, encoding='utf-8')
                    with (
                        patch.object(patcher, 'site_package_file', side_effect=lambda *parts: str(root.joinpath(*parts))),
                        patch.object(patcher, 'logger'),
                    ):
                        patcher.patch_uiautomator2()
                        patched = init_file.read_text(encoding='utf-8')
                        patcher.patch_uiautomator2()
                        if init_file.read_text(encoding='utf-8') != patched:
                            raise AssertionError(f'{name} 重复执行改变了补丁内容')

                    namespace = {'__name__': '_test_u2_init', '__file__': str(init_file)}
                    exec(compile(patched, str(init_file), 'exec'), namespace)
                    cls.downloaders.append((name, namespace, namespace['mirror_download']))

    def setUp(self):
        self.version = utils.u2.version.__atx_agent_version__
        self.filename = f'atx-agent_{self.version}_linux_arm64.tar.gz'
        self.mirror_url = f'https://atx.azurpilot.com/{self.filename}'
        self.github_url = f'https://github.com/openatx/atx-agent/releases/download/{self.version}/{self.filename}'

    def test_mirror_success_does_not_request_github(self):
        for name, namespace, downloader in self.downloaders:
            with self.subTest(downloader=name):
                download = Mock(return_value='cached.tar.gz')
                logger = Mock()
                with patch.dict(namespace, cache_download=download):
                    result = downloader(self.mirror_url, self.filename, logger=logger)
                self.assertEqual(result, 'cached.tar.gz')
                download.assert_called_once_with(
                    self.mirror_url, self.filename,
                    storepath=namespace['gen_cachepath'](self.mirror_url), logger=logger,
                )
                logger.warning.assert_not_called()

    def test_mirror_failure_falls_back_directly_to_github_with_shared_cache(self):
        errors = (
            utils.requests.HTTPError('镜像返回 404'),
            utils.requests.ConnectionError('镜像连接失败'),
            utils.requests.ReadTimeout('镜像读取超时'),
            ProtocolError('镜像下载中断'),
            ReadTimeoutError(None, self.mirror_url, '镜像流读取超时'),
            AssertionError('下载长度不符'),
            FileNotFoundError('下载文件缺失'),
            TypeError('缺少 Content-Length'),
            ValueError('Content-Length 无效'),
        )
        for name, namespace, downloader in self.downloaders:
            for error in errors:
                with self.subTest(downloader=name, error=type(error).__name__):
                    download = Mock(side_effect=[error, 'cached.tar.gz'])
                    logger = Mock()
                    with patch.dict(namespace, cache_download=download):
                        self.assertEqual(downloader(self.mirror_url, self.filename, logger=logger), 'cached.tar.gz')
                    storepath = namespace['gen_cachepath'](self.mirror_url)
                    self.assertEqual(download.call_args_list, [
                        call(self.mirror_url, self.filename, storepath=storepath, logger=logger),
                        call(self.github_url, self.filename, storepath=storepath, logger=logger),
                    ])
                    logger.warning.assert_called_once()

    def test_github_download_is_cached_for_later_installations(self):
        payload = b'cached-atx-archive'
        for name, namespace, downloader in self.downloaders:
            with self.subTest(downloader=name), tempfile.TemporaryDirectory() as directory:
                response = utils.requests.Response()
                response.status_code = 200
                response.headers['Content-Length'] = str(len(payload))
                response.raw = io.BytesIO(payload)
                with (
                    patch.dict(namespace, appdir=directory, DownloadBar=Mock()),
                    patch.object(utils.requests, 'get', side_effect=[
                        utils.requests.ConnectionError('镜像连接失败'), response,
                    ]) as get,
                ):
                    path = downloader(self.mirror_url, logger=Mock())
                    self.assertEqual(Path(path).read_bytes(), payload)
                    self.assertEqual(downloader(self.mirror_url, logger=Mock()), path)
                self.assertEqual([c.args[0] for c in get.call_args_list], [self.mirror_url, self.github_url])

    def test_local_permission_error_does_not_retry_another_source(self):
        for name, namespace, downloader in self.downloaders:
            with self.subTest(downloader=name):
                download = Mock(side_effect=PermissionError('缓存目录不可写'))
                with patch.dict(namespace, cache_download=download):
                    with self.assertRaises(PermissionError):
                        downloader(self.mirror_url, logger=Mock())
                download.assert_called_once()

    def test_both_sources_fail_propagates_github_error(self):
        for name, namespace, downloader in self.downloaders:
            with self.subTest(downloader=name):
                github_error = utils.requests.ConnectionError('GitHub 连接失败')
                download = Mock(side_effect=[utils.requests.HTTPError('镜像返回 503'), github_error])
                with patch.dict(namespace, cache_download=download):
                    with self.assertRaises(utils.requests.ConnectionError) as caught:
                        downloader(self.mirror_url, logger=Mock())
                self.assertIs(caught.exception, github_error)
                self.assertEqual(download.call_count, 2)

    def test_other_github_assets_keep_existing_download_order(self):
        url = 'https://github.com/openatx/other/releases/download/v1/app.apk'
        for name, namespace, downloader in self.downloaders:
            with self.subTest(downloader=name):
                download = Mock(side_effect=[utils.requests.HTTPError('其他镜像失败'), 'app.apk'])
                with patch.dict(namespace, cache_download=download):
                    self.assertEqual(downloader(url, logger=Mock()), 'app.apk')
                self.assertEqual([c.args[0] for c in download.call_args_list], [
                    url.replace('https://github.com', 'https://tool.appetizer.io'), url,
                ])

    def test_abi_selection_and_install_use_fallback_archive(self):
        architectures = {
            'armeabi-v7a': 'armv7', 'arm64-v8a': 'arm64', 'armeabi': 'armv6',
            'x86': '386', 'x86_64': '386',
        }
        for name, namespace, _ in self.downloaders:
            initer_class = utils.PatchedIniter if name == '运行时' else namespace['Initer']
            initer = object.__new__(initer_class)
            initer.logger = Mock()
            initer._device = Mock()
            for abi, architecture in architectures.items():
                with self.subTest(downloader=name, abi=abi):
                    initer.abis = [abi]
                    self.assertEqual(initer.atx_agent_url,
                                     f'https://atx.azurpilot.com/atx-agent_{self.version}_linux_{architecture}.tar.gz')
            initer.abis = ['unknown', 'arm64-v8a', 'armeabi-v7a']
            with self.subTest(downloader=name), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / self.filename
                payload = b'fake-atx-agent'
                with tarfile.open(archive, 'w:gz') as tar:
                    member = tarfile.TarInfo('atx-agent')
                    member.size = len(payload)
                    tar.addfile(member, io.BytesIO(payload))
                download = Mock(side_effect=[utils.requests.HTTPError('镜像返回 404'), str(archive)])
                # 上游 push_url 未关闭 TarFile，测试负责释放句柄以便 Windows 清理临时目录。
                opened = []
                tar_open = tarfile.open

                def open_archive(*args, **kwargs):
                    result = tar_open(*args, **kwargs)
                    opened.append(result)
                    return result

                with (
                    patch.dict(namespace, cache_download=download),
                    patch.object(tarfile, 'open', side_effect=open_archive),
                ):
                    try:
                        initer._install_atx_agent()
                    finally:
                        for opened_archive in opened:
                            opened_archive.close()
                initer._device.sync.push.assert_called_once_with(
                    os.path.join(directory, 'atx-agent'), '/data/local/tmp/atx-agent', mode=0o755,
                )
                self.assertEqual((Path(directory) / 'atx-agent').read_bytes(), payload)
                self.assertEqual([c.args[0] for c in download.call_args_list], [self.mirror_url, self.github_url])


if __name__ == '__main__':
    unittest.main()
