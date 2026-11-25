from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autozhu.config import Config, ConfigError
from main import ensure_config_exists


def write_config(tmp: str, payload: dict) -> Path:
    path = Path(tmp) / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


BASE = {
    "connection": {"mode": "forward", "forward_url": "ws://127.0.0.1:3001", "token": "abc"},
    "reaction": {"groups": {"123456": ["12951"]}},
}


class ConfigLoadTest(unittest.TestCase):
    def test_loads_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load(write_config(tmp, BASE))
        self.assertEqual(cfg.connection.mode, "forward")
        self.assertEqual(cfg.reaction.emojis_for(123456), ["12951"])
        self.assertEqual(cfg.reaction.emojis_for(999), [])

    def test_string_emoji_is_normalized(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["groups"] = {"123456": 12951}
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load(write_config(tmp, payload))
        self.assertEqual(cfg.reaction.groups["123456"], ["12951"])

    def test_wildcard_group_matches_everything(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["groups"] = {"*": ["12951"]}
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load(write_config(tmp, payload))
        self.assertEqual(cfg.reaction.emojis_for(1), ["12951"])

    def test_blocked_users_are_normalized_and_deduped(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["blocked_users"] = [123456, " 654321 ", "123456", ""]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load(write_config(tmp, payload))
        self.assertEqual(cfg.reaction.blocked_users, ["123456", "654321"])

    def test_rejects_non_numeric_blocked_user(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["blocked_users"] = ["not-a-qq-number"]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(write_config(tmp, payload))

    def test_rejects_unknown_field(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["oops"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(write_config(tmp, payload))

    def test_rejects_bad_mode(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["connection"]["mode"] = "carrier-pigeon"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(write_config(tmp, payload))

    def test_rejects_empty_groups(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["groups"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(write_config(tmp, payload))

    def test_rejects_non_numeric_emoji(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["reaction"]["groups"] = {"123456": ["祝"]}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(write_config(tmp, payload))

    def test_rejects_bad_url(self) -> None:
        payload = json.loads(json.dumps(BASE))
        payload["connection"]["forward_url"] = "http://127.0.0.1:3001"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                Config.load(write_config(tmp, payload))

    def test_env_var_overrides_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(tmp, BASE)
            with mock.patch.dict(os.environ, {"QQ_AUTO_ZHU_TOKEN": "from-env"}, clear=False):
                cfg = Config.load(path)
        self.assertEqual(cfg.connection.token, "from-env")


class FirstRunTest(unittest.TestCase):
    def test_generates_config_from_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            self.assertFalse(ensure_config_exists(target))
            self.assertTrue(target.exists())
            loaded = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("connection", loaded)
            self.assertIn("reaction", loaded)
            self.assertTrue(ensure_config_exists(target))
