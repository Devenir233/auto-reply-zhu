from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from autozhu.config import Config
from autozhu.llbot import LLBotConfigError, apply_endpoint, read_llbot_endpoint


def write_account(
    directory: Path,
    uin: str,
    *,
    ob11_enable: bool = True,
    ws_enable: bool = True,
    port: int = 3001,
    token: str = "abc",
    mtime: float | None = None,
) -> Path:
    payload = {
        "ob11": {
            "enable": ob11_enable,
            "connect": [
                {"type": "ws", "enable": ws_enable, "host": "127.0.0.1", "port": port, "token": token},
                {"type": "ws-reverse", "enable": False, "url": "", "token": ""},
            ],
        }
    }
    path = directory / f"config_{uin}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class ReadLLBotEndpointTest(unittest.TestCase):
    def test_reads_enabled_forward_ws(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_account(Path(tmp), "10000001", port=3001, token="test-token")
            endpoint = read_llbot_endpoint(tmp)
        self.assertTrue(endpoint.enabled)
        self.assertEqual(endpoint.uin, "10000001")
        self.assertEqual(endpoint.port, 3001)
        self.assertEqual(endpoint.token, "test-token")
        self.assertEqual(endpoint.config_path, path)

    def test_reports_disabled_ob11(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_account(Path(tmp), "123", ob11_enable=False)
            endpoint = read_llbot_endpoint(tmp)
        self.assertFalse(endpoint.enabled)
        self.assertIn("总开关", endpoint.reason)

    def test_reports_disabled_ws(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_account(Path(tmp), "123", ws_enable=False)
            endpoint = read_llbot_endpoint(tmp)
        self.assertFalse(endpoint.enabled)
        self.assertIn("正向 WebSocket", endpoint.reason)

    def test_prefers_newest_account_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            write_account(Path(tmp), "111", token="old", mtime=now - 1000)
            write_account(Path(tmp), "222", token="new", mtime=now)
            endpoint = read_llbot_endpoint(tmp)
        self.assertEqual(endpoint.uin, "222")
        self.assertEqual(endpoint.token, "new")

    def test_missing_dir_raises(self) -> None:
        with self.assertRaises(LLBotConfigError):
            read_llbot_endpoint(Path(tempfile.gettempdir()) / "definitely-not-here-xyz")

    def test_no_config_files_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LLBotConfigError):
                read_llbot_endpoint(tmp)


class ApplyEndpointTest(unittest.TestCase):
    def test_updates_token_and_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_account(Path(tmp), "123", port=3005, token="tok")
            endpoint = read_llbot_endpoint(tmp)
        config = Config()
        config.connection.forward_url = "ws://127.0.0.1:3001"
        self.assertTrue(apply_endpoint(config, endpoint))
        self.assertEqual(config.connection.token, "tok")
        self.assertEqual(config.connection.forward_url, "ws://127.0.0.1:3005")

    def test_reverse_mode_keeps_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_account(Path(tmp), "123", port=3005, token="tok")
            endpoint = read_llbot_endpoint(tmp)
        config = Config()
        config.connection.mode = "reverse"
        apply_endpoint(config, endpoint)
        self.assertEqual(config.connection.forward_url, "ws://127.0.0.1:3001")

    def test_no_change_when_same(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_account(Path(tmp), "123", port=3001, token="tok")
            endpoint = read_llbot_endpoint(tmp)
        config = Config()
        config.connection.token = "tok"
        self.assertFalse(apply_endpoint(config, endpoint))
