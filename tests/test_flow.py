from __future__ import annotations

import asyncio
import socket
import unittest

from websockets.exceptions import InvalidStatus

from autozhu.config import Config
from autozhu.logging_setup import make_streams_utf8_safe
from autozhu.reactor import AutoZhuApp
from tests.mock_onebot import MockBot

SECRET = "test-token"

make_streams_utf8_safe()


async def wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_config(mode: str = "forward") -> Config:
    config = Config()
    config.connection.mode = mode
    config.connection.token = SECRET
    config.reaction.groups = {"562204345": ["12951"]}
    config.reaction.rate_limit_per_second = 200
    config.reaction.rate_limit_burst = 200
    config.reaction.timeout = 5
    return config


def group_event(
    message_id: int = 1001,
    group_id: int = 562204345,
    user_id: int = 10086,
    text: str = "hello",
    self_id: int = 1,
) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": self_id,
        "message_id": message_id,
        "group_id": group_id,
        "user_id": user_id,
        "raw_message": text,
        "message": [{"type": "text", "data": {"text": text}}],
        "sender": {"user_id": user_id, "nickname": "测试用户", "card": ""},
    }


class ForwardModeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = await MockBot().start_server()
        self.config = make_config("forward")
        self.config.connection.forward_url = f"ws://127.0.0.1:{self.bot.port}"
        self.app = AutoZhuApp(self.config)
        await self.app.start()
        self.assertTrue(await self.app.wait_connected(5), "客户端应在 5 秒内连上")

    async def asyncTearDown(self) -> None:
        await self.app.stop()
        await self.bot.close()

    async def test_reacts_to_group_message(self) -> None:
        await self.bot.send_event(group_event())
        actions = await self.bot.wait_for_actions("set_msg_emoji_like")
        params = actions[0]["params"]
        self.assertEqual(params["message_id"], "1001")
        self.assertEqual(params["emoji_id"], "12951")
        self.assertIs(params["set"], True)

    async def test_ignores_other_groups(self) -> None:
        await self.bot.send_event(group_event(group_id=999999))
        await asyncio.sleep(0.3)
        self.assertEqual(self.bot.actions, [])

    async def test_ignores_self_messages(self) -> None:
        await self.bot.send_event(group_event(self_id=42, user_id=42))
        await asyncio.sleep(0.3)
        self.assertEqual(self.bot.actions, [])

    async def test_deduplicates_same_message(self) -> None:
        await self.bot.send_event(group_event(message_id=2002))
        await self.bot.wait_for_actions("set_msg_emoji_like", count=1)
        await self.bot.send_event(group_event(message_id=2002))
        await asyncio.sleep(0.4)
        matched = [a for a in self.bot.actions if a.get("action") == "set_msg_emoji_like"]
        self.assertEqual(len(matched), 1)

    async def test_retries_transient_failure(self) -> None:
        attempts = {"n": 0}

        def responder(payload: dict):
            if payload.get("action") != "set_msg_emoji_like":
                return None
            attempts["n"] += 1
            if attempts["n"] < 3:
                return {"status": "failed", "retcode": 1200, "message": "操作过于频繁"}
            return None

        self.bot.responder = responder
        await self.bot.send_event(group_event(message_id=3003))
        actions = await self.bot.wait_for_actions("set_msg_emoji_like", count=3)
        self.assertEqual(len(actions), 3)
        self.assertTrue(
            await wait_until(lambda: self.app.service.stats.reacted == 1),
            f"重试成功后应记为成功：{self.app.service.stats}",
        )

    async def test_gives_up_on_unsupported_api(self) -> None:
        self.bot.responder = lambda payload: (
            {"status": "failed", "retcode": 1404, "message": "不支持的 API"}
            if payload.get("action") == "set_msg_emoji_like"
            else None
        )
        await self.bot.send_event(group_event(message_id=4004))
        await self.bot.wait_for_actions("set_msg_emoji_like", count=1)
        await asyncio.sleep(0.4)
        matched = [a for a in self.bot.actions if a.get("action") == "set_msg_emoji_like"]
        self.assertEqual(len(matched), 1, "1404 属于不可重试错误，不应重试")

    async def test_keyword_filter(self) -> None:
        self.config.reaction.keywords = ["早安"]
        await self.bot.send_event(group_event(message_id=5005, text="今天天气不错"))
        await asyncio.sleep(0.3)
        self.assertEqual(self.bot.actions, [])

        await self.bot.send_event(group_event(message_id=5006, text="早安各位"))
        await self.bot.wait_for_actions("set_msg_emoji_like")

    async def test_reconnects_after_disconnect(self) -> None:
        self.assertIsNotNone(self.bot.ws)
        await self.bot.ws.close()
        await asyncio.sleep(0.3)
        self.assertTrue(await self.app.wait_connected(10), "断线后应自动重连")
        await self.bot.send_event(group_event(message_id=9009))
        actions = await self.bot.wait_for_actions("set_msg_emoji_like")
        self.assertEqual(actions[0]["params"]["message_id"], "9009")

    async def test_marks_token_rejection_frame(self) -> None:
        await self.bot.send_event(
            {"status": "failed", "retcode": 1403, "echo": None, "message": "token验证失败"}
        )
        self.assertTrue(
            await wait_until(lambda: self.app.link.token_rejected),
            "协议端返回 1403 时应标记 token 被拒绝",
        )

    async def test_dry_run_does_not_call_api(self) -> None:
        await self.app.stop()
        self.app = AutoZhuApp(self.config, dry_run=True)
        await self.app.start()
        self.assertTrue(await self.app.wait_connected(5))
        await self.bot.send_event(group_event(message_id=6006))
        await asyncio.sleep(0.4)
        self.assertEqual(self.bot.actions, [])
        self.assertEqual(self.app.service.stats.reacted, 1)


class ReverseModeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.config = make_config("reverse")
        self.config.connection.reverse_port = free_port()
        self.app = AutoZhuApp(self.config)
        await self.app.start()
        # start() 返回时反向监听必须已经就绪
        self.assertEqual(self.app.driver.bound_port, self.config.connection.reverse_port)
        self.url = f"ws://127.0.0.1:{self.config.connection.reverse_port}/"
        self.bot: MockBot | None = None

    async def asyncTearDown(self) -> None:
        if self.bot is not None:
            await self.bot.close()
        await self.app.stop()

    async def test_accepts_reverse_connection_and_reacts(self) -> None:
        self.bot = await MockBot().connect(self.url, SECRET)
        self.assertTrue(await self.app.wait_connected(5))
        await self.bot.send_event(group_event(message_id=7007))
        actions = await self.bot.wait_for_actions("set_msg_emoji_like")
        self.assertEqual(actions[0]["params"]["message_id"], "7007")

    async def test_rejects_wrong_token(self) -> None:
        with self.assertRaises(InvalidStatus) as ctx:
            await MockBot().connect(self.url, "wrong-token")
        self.assertEqual(ctx.exception.response.status_code, 401)


class NoGroupConfigTest(unittest.IsolatedAsyncioTestCase):
    async def test_wildcard_group(self) -> None:
        bot = await MockBot().start_server()
        config = make_config("forward")
        config.connection.forward_url = f"ws://127.0.0.1:{bot.port}"
        config.reaction.groups = {"*": ["12951"]}
        app = AutoZhuApp(config)
        await app.start()
        try:
            self.assertTrue(await app.wait_connected(5))
            await bot.send_event(group_event(group_id=123456, message_id=8008))
            actions = await bot.wait_for_actions("set_msg_emoji_like")
            self.assertEqual(actions[0]["params"]["emoji_id"], "12951")
        finally:
            await app.stop()
            await bot.close()
