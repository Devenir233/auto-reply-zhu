from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from autozhu.config import Config
from autozhu.webui import SettingsServer

BASE = {
    "connection": {"mode": "forward", "forward_url": "ws://127.0.0.1:3001", "token": "t"},
    "reaction": {"groups": {"123456": ["12951"]}},
    "logging": {"level": "INFO"},
}


class SettingsServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.path.write_text(json.dumps(BASE), encoding="utf-8")
        self.server = SettingsServer(self.path, port=0)
        self.url = self.server.start()
        self.token = self.server.token

    def tearDown(self) -> None:
        self.server.stop()
        self.tmp.cleanup()

    def _request(self, path: str, method: str = "GET", payload=None, token: bool = True):
        headers = {}
        if token:
            headers["X-AutoZhu-Token"] = self.token
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url.rstrip("/") + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            return exc.code, body

    def test_page_renders_form_and_embeds_token(self) -> None:
        status, body = self._request("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn("Auto Zhu 设置", body)
        self.assertIn("blocked", body)
        self.assertIn(self.token, body)

    def test_api_requires_token(self) -> None:
        status, body = self._request("/api/config", token=False)
        self.assertEqual(status, 401)
        self.assertIn("unauthorized", body)

        post_status, _ = self._request("/api/config", method="POST", payload=BASE, token=False)
        self.assertEqual(post_status, 401)

    def test_get_config(self) -> None:
        status, body = self._request("/api/config")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["config"]["reaction"]["groups"], {"123456": ["12951"]})
        self.assertEqual(data["config"]["connection"]["token"], "t")

    def test_save_writes_config(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["groups"] = {"654321": ["12951", "178"]}
        payload["reaction"]["blocked_users"] = ["10001", "10002"]
        payload["reaction"]["keywords"] = ["早安"]

        status, body = self._request("/api/config", method="POST", payload=payload)
        self.assertEqual(status, 200, body)
        self.assertIn("已保存", body)

        saved = Config.load(self.path)
        self.assertEqual(saved.reaction.groups, {"654321": ["12951", "178"]})
        self.assertEqual(saved.reaction.blocked_users, ["10001", "10002"])
        self.assertEqual(saved.reaction.keywords, ["早安"])

    def test_save_rejects_invalid_config_and_keeps_file(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["groups"] = {"123456": ["not-a-number"]}
        status, body = self._request("/api/config", method="POST", payload=payload)
        self.assertEqual(status, 400)
        self.assertIn("表情 ID", body)

        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["reaction"]["groups"], {"123456": ["12951"]})

    def test_save_rejects_broken_json(self) -> None:
        request = urllib.request.Request(
            self.url.rstrip("/") + "/api/config",
            data=b"{not json",
            method="POST",
            headers={
                "X-AutoZhu-Token": self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status, body = response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read().decode("utf-8")
            exc.close()

        self.assertEqual(status, 400)
        self.assertIn("JSON", body)

    def test_unknown_path_returns_404(self) -> None:
        status, _ = self._request("/nope")
        self.assertEqual(status, 404)
