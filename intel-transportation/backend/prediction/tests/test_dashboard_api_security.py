import asyncio
import json
import unittest
from unittest.mock import patch

from starlette.requests import Request

from backend import dashboard_api


class DashboardApiSecurityTests(unittest.TestCase):
    def test_cors_origins_are_explicit(self):
        self.assertEqual(
            dashboard_api._parse_cors_origins("https://a.example, https://b.example"),
            ("https://a.example", "https://b.example"),
        )
        with self.assertRaisesRegex(ValueError, "通配符"):
            dashboard_api._parse_cors_origins("*")

    def test_production_requires_prediction_api_key(self):
        with (
            patch.object(dashboard_api, "APP_ENV", "production"),
            patch.object(dashboard_api, "PREDICTION_API_KEY", ""),
            self.assertRaisesRegex(RuntimeError, "PREDICTION_API_KEY"),
        ):
            dashboard_api._validate_runtime_security()

    def test_internal_error_detail_is_hidden_by_default(self):
        scope = {"type": "http", "method": "GET", "path": "/boom", "headers": []}
        request = Request(scope)
        with patch.object(dashboard_api, "API_EXPOSE_INTERNAL_ERRORS", False):
            response = asyncio.run(
                dashboard_api.unhandled_exception_handler(request, RuntimeError("secret"))
            )
        body = json.loads(response.body)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("detail", body)
        self.assertNotIn("secret", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
