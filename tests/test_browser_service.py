"""浏览器服务探测与一键启动：全部打桩，不真的启动 npm，也不碰 CDP。"""

import json
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, urlopen

from jobagent import browser_service
from jobagent.review_server import create_server


def post(url):
    request = Request(
        url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    return urlopen(request)


class BrowserServiceModuleTests(unittest.TestCase):
    def test_endpoint_from_config_defaults_and_normalizes(self):
        self.assertEqual(
            browser_service.endpoint_from_config({}), browser_service.DEFAULT_ENDPOINT
        )
        self.assertEqual(
            browser_service.endpoint_from_config(
                {"boss": {"cdp_endpoint": "http://127.0.0.1:9/"}}
            ),
            "http://127.0.0.1:9",
        )

    def test_cdp_not_ready_on_closed_port(self):
        # 端口没人监听时必须判为不可用，而不是抛错
        self.assertIsNone(browser_service.cdp_version("http://127.0.0.1:9", timeout=0.3))
        self.assertFalse(browser_service.cdp_ready("http://127.0.0.1:9"))
        self.assertFalse(browser_service.port_listening("http://127.0.0.1:9", timeout=0.3))

    def test_ensure_reuses_healthy_instance_without_starting(self):
        with patch.object(browser_service, "cdp_ready", return_value=True), patch.object(
            browser_service, "start_browser_service"
        ) as starter:
            result = browser_service.ensure_browser_service({})
        starter.assert_not_called()  # 健康实例必须复用，绝不重启
        self.assertTrue(result["running"])
        self.assertFalse(result["started"])

    def test_ensure_starts_then_reports_not_ready(self):
        with patch.object(browser_service, "cdp_ready", return_value=False), patch.object(
            browser_service, "start_browser_service"
        ) as starter, patch.object(browser_service, "MORSE_DIR", Path(".")):
            result = browser_service.ensure_browser_service({}, wait_seconds=0)
        starter.assert_called_once()
        self.assertTrue(result["started"])
        self.assertFalse(result["running"])

    def test_ensure_reports_missing_directory_without_starting(self):
        with patch.object(browser_service, "cdp_ready", return_value=False), patch.object(
            browser_service, "MORSE_DIR", Path("Z:/definitely-missing")
        ), patch.object(browser_service, "start_browser_service") as starter:
            result = browser_service.ensure_browser_service({})
        starter.assert_not_called()
        self.assertFalse(result["running"])

    def test_ensure_reports_start_failure(self):
        with patch.object(browser_service, "cdp_ready", return_value=False), patch.object(
            browser_service, "MORSE_DIR", Path(".")
        ), patch.object(
            browser_service, "start_browser_service", side_effect=RuntimeError("no npm")
        ):
            result = browser_service.ensure_browser_service({})
        self.assertFalse(result["running"])
        self.assertIn("no npm", result["detail"])


class BrowserServiceEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        database = Path(self.tmp.name) / "jobs.db"
        sqlite3.connect(database).close()  # migrate_review_database 需要文件存在
        self.server = create_server(
            database,
            host="127.0.0.1",
            port=0,
            config={"boss": {"cdp_endpoint": "http://127.0.0.1:9"}},
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_status_endpoint_never_starts_anything(self):
        with patch.object(browser_service, "cdp_ready", return_value=False), patch.object(
            browser_service, "start_browser_service"
        ) as starter:
            with urlopen(f"{self.base}/api/browser/status") as response:
                payload = json.loads(response.read().decode("utf-8"))
        starter.assert_not_called()  # 读状态绝不能顺带启动服务
        self.assertFalse(payload["browser"]["running"])
        self.assertEqual(payload["browser"]["endpoint"], "http://127.0.0.1:9")

    def test_status_endpoint_reports_running(self):
        with patch.object(browser_service, "cdp_ready", return_value=True):
            with urlopen(f"{self.base}/api/browser/status") as response:
                payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["browser"]["running"])

    def test_start_endpoint_invokes_service(self):
        fake = {
            "endpoint": "http://127.0.0.1:9",
            "running": True,
            "started": True,
            "detail": "已启动浏览器服务，CDP 就绪",
        }
        with patch.object(
            browser_service, "ensure_browser_service", return_value=fake
        ) as ensure:
            with post(f"{self.base}/api/browser/start") as response:
                payload = json.loads(response.read().decode("utf-8"))
        ensure.assert_called_once()
        self.assertTrue(payload["browser"]["running"])
        self.assertTrue(payload["browser"]["started"])

    def test_start_endpoint_reports_failure_without_500(self):
        with patch.object(
            browser_service, "ensure_browser_service", side_effect=RuntimeError("npm 挂了")
        ):
            with post(f"{self.base}/api/browser/start") as response:
                self.assertEqual(response.status, 200)
                payload = json.loads(response.read().decode("utf-8"))
        self.assertFalse(payload["browser"]["running"])
        self.assertIn("npm", payload["browser"]["detail"])


if __name__ == "__main__":
    unittest.main()
