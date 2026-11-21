"""一个最小可用的 OneBot 11 协议端，用于端到端测试（不需要真的 QQ）。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import ServerConnection, serve as ws_serve


class MockBot:
    """既能当“正向 WS 服务端”，也能当“反向 WS 客户端”。"""

    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.ws: ServerConnection | None = None
        self.server: Any = None
        self.port: int | None = None
        self.responder: Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any] | None] | None = None
        self.on_connect: Callable[[Any], Awaitable[None]] | None = None
        self._reader: asyncio.Task[Any] | None = None
        self._action_event = asyncio.Event()

    # ---- 作为服务端（app 走正向 WS 时用）--------------------------

    async def start_server(self, host: str = "127.0.0.1") -> "MockBot":
        async def handler(ws: ServerConnection) -> None:
            self.ws = ws
            if self.on_connect is not None:
                await self.on_connect(ws)
            await self._read_loop(ws)

        self.server = await ws_serve(handler, host, 0)
        sockets = self.server.sockets
        self.port = sockets[0].getsockname()[1] if sockets else None
        return self

    # ---- 作为客户端（app 走反向 WS 时用）--------------------------

    async def connect(self, url: str, token: str = "") -> "MockBot":
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.ws = await ws_connect(url, additional_headers=headers, proxy=None)
        self._reader = asyncio.create_task(self._read_loop(self.ws))
        return self

    # ---- 公共 -------------------------------------------------------

    async def _read_loop(self, ws: ServerConnection) -> None:
        try:
            async for raw in ws:
                payload = json.loads(raw)
                self.actions.append(payload)
                response: dict[str, Any] = {
                    "status": "ok",
                    "retcode": 0,
                    "data": None,
                    "echo": payload.get("echo"),
                }
                if self.responder is not None:
                    override = self.responder(payload)
                    if asyncio.iscoroutine(override):
                        override = await override
                    if override:
                        response.update(override)
                await ws.send(json.dumps(response))
                self._action_event.set()
        except Exception:
            pass

    async def send_event(self, event: dict[str, Any]) -> None:
        assert self.ws is not None, "还没有连接"
        await self.ws.send(json.dumps(event, ensure_ascii=False))

    async def wait_for_actions(self, action: str, count: int = 1, timeout: float = 5.0) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            matched = [item for item in self.actions if item.get("action") == action]
            if len(matched) >= count:
                return matched
            self._action_event.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._action_event.wait(), deadline - loop.time())
        matched = [item for item in self.actions if item.get("action") == action]
        raise AssertionError(f"等待 {action} 超时，已收到：{[a.get('action') for a in self.actions]}")

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
