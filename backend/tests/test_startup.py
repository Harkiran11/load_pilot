import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from support import use_migrated_test_database


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        use_migrated_test_database()

    @patch('startup.load_dotenv')
    @patch('startup.call_external_api', new_callable=AsyncMock)
    def test_calls_external_api_once_before_serving_requests(self, call_api, load_env):
        with patch.dict(os.environ, {'EXTERNAL_API_URL': 'https://example.com/api'}):
            with TestClient(app) as client:
                call_api.assert_awaited_once_with('https://example.com/api')
                self.assertEqual(client.get('/health').status_code, 200)
                self.assertEqual(client.get('/health').status_code, 200)
                call_api.assert_awaited_once()

    @patch('startup.load_dotenv')
    @patch('startup.call_external_api', new_callable=AsyncMock)
    def test_skips_external_api_when_url_is_blank(self, call_api, load_env):
        with patch.dict(os.environ, {'EXTERNAL_API_URL': ''}):
            with TestClient(app) as client:
                self.assertEqual(client.get('/health').status_code, 200)

        call_api.assert_not_awaited()
