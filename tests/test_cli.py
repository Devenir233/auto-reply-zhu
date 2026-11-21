from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.mock_onebot import MockBot

ROOT = Path(__file__).resolve().parent.parent
GROUP_ID = 562204345


def doctor_responder(payload: dict):
    action = payload.get("action")
    if action == "get_version_info":
        return {"data": {"app_name": "LLBot", "app_version": "8.3.2", "protocol_version": "v11"}}
    if action == "get_login_info":
        return {"data": {"user_id": 10000, "nickname": "测试机器人"}}
    if action == "get_group_list":
        return {"data": [{"group_id": GROUP_ID, "group_name": "测试群"}]}
    if action == "get_group_msg_history":
        return {"data": {"messages": [{"message_id": 4242, "raw_message": "hi"}]}}
    return None


class DoctorCliTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = await MockBot().start_server()
        self.bot.responder = doctor_responder
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "connection": {
                        "mode": "forward",
                        "forward_url": f"ws://127.0.0.1:{self.bot.port}",
                        "token": "",
                    },
                    "reaction": {"groups": {str(GROUP_ID): ["12951"]}},
                    "logging": {"level": "INFO"},
                }
            ),
            encoding="utf-8",
        )

    async def asyncTearDown(self) -> None:
        await self.bot.close()
        self.tmp.cleanup()

    async def _run(self, *args: str) -> tuple[int, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "main.py",
            "--config",
            str(self.config_path),
            *args,
            cwd=str(ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        return process.returncode or 0, output.decode("utf-8", "replace")

    async def test_doctor_passes(self) -> None:
        code, output = await self._run("--doctor")
        self.assertEqual(code, 0, output)
        self.assertIn("✅ 自检通过", output)
        self.assertIn("LLBot 8.3.2", output)
        self.assertIn("测试群", output)

    async def test_test_reaction_hits_latest_message(self) -> None:
        code, output = await self._run("--doctor", "--test-reaction", str(GROUP_ID))
        self.assertEqual(code, 0, output)
        self.assertIn("实测贴表情成功", output)
        actions = await self.bot.wait_for_actions("set_msg_emoji_like")
        self.assertEqual(actions[0]["params"]["message_id"], "4242")
        self.assertEqual(actions[0]["params"]["emoji_id"], "12951")

    async def test_doctor_reports_token_rejection(self) -> None:
        async def reject(ws) -> None:
            await ws.send(
                json.dumps({"status": "failed", "retcode": 1403, "echo": None, "message": "token验证失败"})
            )

        self.bot.on_connect = reject
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["connection"]["token"] = "will-be-rejected"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

        code, output = await self._run("--doctor")
        self.assertEqual(code, 1, output)
        self.assertIn("token 校验失败", output)

    async def test_sync_token_reads_llbot_config(self) -> None:
        llbot_dir = Path(self.tmp.name) / "llbot-data"
        llbot_dir.mkdir()
        (llbot_dir / "config_999999.json").write_text(
            json.dumps(
                {
                    "ob11": {
                        "enable": True,
                        "connect": [
                            {"type": "ws", "enable": True, "host": "127.0.0.1", "port": 3007, "token": "new-token"}
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["connection"]["token"] = ""
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

        code, output = await self._run("--sync-token", "--llbot-data", str(llbot_dir))
        self.assertEqual(code, 0, output)
        self.assertIn("已同步", output)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["connection"]["token"], "new-token")
        self.assertEqual(saved["connection"]["forward_url"], "ws://127.0.0.1:3007")

    async def test_version_flag(self) -> None:
        code, output = await self._run("--version")
        self.assertEqual(code, 0)
        self.assertIn("auto-zhu", output)

    async def test_reports_unknown_group(self) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["reaction"]["groups"] = {"999999999": ["12951"]}
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

        code, output = await self._run("--doctor")
        self.assertEqual(code, 1, output)
        self.assertIn("不在该账号的群列表中", output)
