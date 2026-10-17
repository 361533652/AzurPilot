"""隔离验证 Quick Tunnel 的身份、配对鉴权及进程恢复，不连接真实游戏。"""

import asyncio
import json
import secrets
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from module.api.protocol import ApiError
from module.api.socket import Gateway
from module.runtime.remote_cloudflare import CloudflareRemoteAccessProvider, RemoteIdentity, registry_url


def test_device_identity_survives_restart_and_client_revocation():
    with tempfile.TemporaryDirectory() as directory:
        store = RemoteIdentity(directory)
        first = store.load()
        assert RemoteIdentity(directory).load() == first
        client_id, token = store.add_client()
        assert store.accepts(token)
        assert token not in store.path.read_text(encoding='utf-8')
        assert not store.accepts(secrets.token_urlsafe(32))
        gateway = Gateway(SimpleNamespace(configs=SimpleNamespace(directory=Path(directory))), 'webui-password')
        gateway.authenticate('paired', token)
        gateway.authenticate('owner', 'webui-password')
        store.remove_client(client_id)
        with pytest.raises(ApiError, match='访问密码不正确'):
            gateway.authenticate('revoked', token)


def test_invalid_registry_and_corrupted_identity_fail_closed():
    for url in ('http://example.com', 'https://user:password@example.com', 'file:///tmp',
                'https://example.com?key=secret', 'https://example.com#fragment'):
        with pytest.raises(ValueError):
            registry_url(url)
    assert registry_url('http://127.0.0.1:18882/') == 'http://127.0.0.1:18882'
    with tempfile.TemporaryDirectory() as directory:
        store = RemoteIdentity(directory)
        store.load()
        store.path.write_text('{invalid', encoding='utf-8')
        with pytest.raises(json.JSONDecodeError):
            store.load()
        assert store.path.read_text(encoding='utf-8') == '{invalid'


def test_failed_pair_creation_removes_unused_local_credential():
    with tempfile.TemporaryDirectory() as directory:
        provider = CloudflareRemoteAccessProvider()
        provider.address = 'https://example.com/d/test/'
        async def unavailable(*args):
            raise ValueError('HTTP 503')
        with patch.object(provider, 'identity', return_value=RemoteIdentity(directory)), \
                patch.object(provider, 'request', side_effect=unavailable):
            with pytest.raises(ValueError):
                provider.create_pairing()
        assert RemoteIdentity(directory).load()['clients'] == []


def test_stop_interrupts_reconnect_backoff_and_start_is_idempotent():
    provider = CloudflareRemoteAccessProvider()
    attempted = threading.Event()
    async def unavailable():
        attempted.set()
        raise ValueError('模拟注册暂不可用')
    with patch.object(provider, '_tunnel', side_effect=unavailable):
        provider.start()
        thread = provider.thread
        provider.start()
        assert provider.thread is thread
        assert attempted.wait(3)
        provider.stop()
        assert not provider.is_alive()
        assert provider.state == 'stopped'


def test_quick_tunnel_registration_and_cleanup_use_current_url():
    class FakeProcess:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def __aiter__(self):
            async def events():
                yield SimpleNamespace(data=b'https://new-url.trycloudflare.com')
                await asyncio.Future()
            return events()
        async def stream_chunker(self, stream, channel):
            return b''

    async def scenario(directory):
        provider = CloudflareRemoteAccessProvider()
        registered = asyncio.Event()
        requests = []
        async def request(method, path, data=None):
            requests.append((method, path, data))
            if method == 'POST':
                registered.set()
                return {'url': 'https://registry.example/d/device/'}
            return {'ok': True}
        config = SimpleNamespace(Password='strong-password', WebuiSSLCert=None, WebuiHost='127.0.0.1', WebuiPort=25548)
        # pyflared 导入会初始化个人目录；隔离测试将它定向至夹具目录。
        with patch('platformdirs.user_data_dir', return_value=directory):
            import pyflared
        with patch.object(pyflared, 'run_quick_tunnel', return_value=FakeProcess()) as quick, \
                patch('module.runtime.setting.State.deploy_config', config), \
                patch.object(provider, 'identity', return_value=RemoteIdentity(directory)), \
                patch.object(provider, 'request', side_effect=request):
            task = asyncio.create_task(provider._tunnel())
            await asyncio.wait_for(registered.wait(), 3)
            assert provider.address == 'https://registry.example/d/device/'
            assert requests[0][2]['tunnel_url'] == 'https://new-url.trycloudflare.com'
            assert provider.get_connection_state() == 'cloudflare_ready'
            quick.assert_called_once_with('http://127.0.0.1:25548')
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert requests[-1][0] == 'DELETE'
            assert provider.address == ''
    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(scenario(directory))
