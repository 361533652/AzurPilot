"""Cloud URL migration keeps the client request and response contracts."""
import json
import unittest
from unittest.mock import Mock, patch

from deploy.git import GitManager
from module.base.api_client import ApiClient
from module.handover.handover import HANDOVER_MAINTAIN_API
from module.server_checker import SERVER_API_BASE


class CloudApiEndpointsTest(unittest.TestCase):
    def test_cl1_keeps_legacy_post_body_at_new_origin(self):
        payload = {'device_id': 'a' * 32, 'instance_id': 'b' * 16,
                   'month': '2026-10', 'battle_count': 2, 'net_stamina_gain': 0}
        response = Mock(status_code=200, text='{"status":"success"}')
        with patch('module.base.api_client.requests.post', return_value=response) as request:
            success, status, _ = ApiClient._post_with_fallback('/api/telemetry', payload)
        self.assertTrue(success)
        self.assertEqual(status, 200)
        self.assertEqual(request.call_args.args[0], 'https://api.azurpilot.com/api/telemetry')
        self.assertEqual(request.call_args.kwargs['json'], payload)

    def test_announcement_keeps_root_id_contract(self):
        notice = {'announcementId': 'notice-test', 'title': 'test', 'content': 'body', 'url': ''}
        response = Mock(status_code=200, text=json.dumps(notice))
        with patch('module.base.api_client.requests.get', return_value=response) as request:
            self.assertEqual(ApiClient.get_announcement(), notice)
        self.assertEqual(request.call_args.args[0], 'https://api.azurpilot.com/api/get/announcement')

    def test_force_update_reads_boolean_from_new_origin(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                response = Mock(text=json.dumps(enabled))
                response.json.return_value = enabled
                with patch('deploy.git.requests.get', return_value=response) as request:
                    self.assertIs(GitManager.cloud_force_update_enabled(), enabled)
                self.assertEqual(request.call_args.args[0], 'https://api.azurpilot.com/api/force_update')

    def test_other_core_calls_use_same_origin(self):
        self.assertEqual(SERVER_API_BASE, 'https://api.azurpilot.com')
        self.assertEqual(HANDOVER_MAINTAIN_API, 'https://api.azurpilot.com/api/maintenance')


if __name__ == '__main__':
    unittest.main()
