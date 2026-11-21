"""OneBot 11 连接层。

支持两种接入方向：

* **正向 WebSocket**：本程序作为客户端连接协议端（LLBot 的“正向 WS”端口，默认 3001）。
* **反向 WebSocket**：本程序监听端口，协议端主动连进来（适合 NAT / 服务器部署，
  也免去“程序必须先于协议端启动”的困扰）。

协议端可以是 LLBot（LLOneBot 后继）、Lagrange.OneBot、NapCat 的社区分支等
任何兼容 OneBot 11 的实现。
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import time
from http import HTTPStatus
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import ServerConnection, serve as ws_serve
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], "Awaitable[None] | None"]

# OneBot 11 的 retcode：0 成功，1 异步受理（部分实现会这么返回）。
_SUCCESS_CODES = {0, 1}


class OneBotError(Exception):
    """OneBot 相关错误基类。"""


class NotConnected(OneBotError):
    """尚未连接到 OneBot 实现。"""


class ActionFailed(OneBotError):
    """协议端调用了 API 但返回失败。"""

    def __init__(self, action: str, retcode: Any, message: str = "") -> None:
        self.action = action
        self.retcode = retcode
        self.message = message or ""
        super().__init__(f"{action} 调用失败 (retcode={retcode})：{self.message or '未知原因'}")


class OneBotLink:
    """一条（逻辑上的）OneBot 11 连接：收发事件、调用 API。"""

    def __init__(self, action_timeout: float = 10.0) -> None:
        self.action_timeout = action_timeout
        self.self_id: int | None = None
        self.token_rejected = False
        self._ws: ServerConnection | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._handlers: list[EventHandler] = []
        self._echo = 0
        self._connected = asyncio.Event()

    # ---- 状态 -------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def add_event_handler(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    # ---- 连接生命周期（由 driver 调用）------------------------------

    def _bind(self, ws: ServerConnection) -> None:
        previous, self._ws = self._ws, ws
        self.token_rejected = False
        self._connected.set()
        if previous is not None and previous is not ws:
            logger.warning("检测到新的 OneBot 连接，关闭上一条旧连接")
            asyncio.create_task(_close_quietly(previous))

    def _unbind(self, ws: ServerConnection) -> None:
        if self._ws is ws:
            self._ws = None
            self._connected.clear()
            self._fail_pending(NotConnected("OneBot 连接已断开"))

    def _fail_pending(self, exc: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    # ---- API 调用 ---------------------------------------------------

    async def call(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        ws = self._ws
        if ws is None:
            raise NotConnected("尚未连接到 OneBot 实现")

        self._echo += 1
        echo = f"autozhu-{self._echo}"
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future

        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
            return await asyncio.wait_for(
                future, timeout if timeout is not None else self.action_timeout
            )
        except asyncio.TimeoutError as exc:
            raise OneBotError(f"{action} 调用超时（{timeout or self.action_timeout} 秒）") from exc
        finally:
            self._pending.pop(echo, None)

    # ---- 收包 -------------------------------------------------------

    async def read_loop(self, ws: ServerConnection) -> None:
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw).decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.debug("忽略无法解析的数据：%.200r", raw)
                    continue
                if isinstance(payload, dict):
                    await self._dispatch(payload)
        except ConnectionClosed as exc:
            logger.info("与 OneBot 实现的连接已断开：%s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - 防御性
            logger.exception("读取 OneBot 数据时出现异常")

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        echo = payload.get("echo")
        if echo is not None:
            future = self._pending.get(str(echo))
            if future is not None and not future.done():
                retcode = payload.get("retcode")
                status = payload.get("status")
                failed = status == "failed" or (
                    retcode is not None and retcode not in _SUCCESS_CODES
                )
                if failed:
                    future.set_exception(
                        ActionFailed(
                            str(payload.get("action") or ""),
                            retcode,
                            str(payload.get("message") or payload.get("wording") or ""),
                        )
                    )
                else:
                    future.set_result(payload.get("data"))
            return

        if payload.get("status") == "failed" and str(payload.get("retcode")) in {"1403", "401"}:
            self.token_rejected = True
            logger.debug("协议端返回鉴权失败（retcode=%s）", payload.get("retcode"))
            return

        self_id = payload.get("self_id")
        if self_id is not None:
            with contextlib.suppress(TypeError, ValueError):
                self.self_id = int(self_id)

        if "post_type" not in payload:
            return
        for handler in list(self._handlers):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover - 单个 handler 出错不应影响收包
                logger.exception("事件处理器抛出异常")


class ForwardDriver:
    """作为客户端连接协议端的 WebSocket 服务。"""

    def __init__(
        self,
        link: OneBotLink,
        url: str,
        token: str = "",
        proxy: str | None = None,
    ) -> None:
        self.link = link
        self.url = url
        self.token = token
        self.proxy = proxy

    async def run(self, stop: asyncio.Event) -> None:
        delay = 1.0
        hinted = False
        while not stop.is_set():
            ws = None
            rejected = False
            try:
                headers = {"User-Agent": "auto-zhu/2.0"}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                async with ws_connect(
                    self.url,
                    additional_headers=headers,
                    proxy=self.proxy,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=None,
                ) as ws:
                    self.link._bind(ws)
                    connected_at = time.monotonic()
                    logger.info("✅ 已连接 OneBot 实现：%s", self.url)
                    await self.link.read_loop(ws)
                    rejected = self.link.token_rejected
                    if time.monotonic() - connected_at > 3:
                        delay = 1.0
            except asyncio.CancelledError:
                raise
            except InvalidStatus as exc:
                code = getattr(getattr(exc, "response", None), "status_code", "?")
                rejected = code == 401
                logger.error(
                    "❌ 连接被协议端拒绝（HTTP %s），通常是 token 不一致或未开启该 WS 服务", code
                )
                delay = 10.0
            except (OSError, InvalidHandshake, asyncio.TimeoutError) as exc:
                logger.warning("连接失败：%s", exc or type(exc).__name__)
            except Exception:  # pragma: no cover - 防御性
                logger.exception("连接过程中出现未预期的异常")
            finally:
                if ws is not None:
                    self.link._unbind(ws)

            if stop.is_set():
                break

            if rejected:
                delay = max(delay, 30.0)
                if not hinted:
                    hinted = True
                    logger.error(
                        "协议端要求鉴权，但本程序的 token 为空或不匹配（retcode 1403）。\n"
                        "  修复：把 config.json 的 connection.token 改成协议端里配置的 token\n"
                        "  （LLBot：WebUI -> OneBot 11 -> 正向 WebSocket 的 token），\n"
                        "  或者两边都留空（仅限本机自用）。改完重启本程序即可。"
                    )
            logger.info("%.0f 秒后重试连接…", delay)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), delay)
            delay = min(delay * 2, 30.0)


class ReverseDriver:
    """监听端口，等待协议端（LLBot 的“反向 WS”）连入。"""

    def __init__(
        self,
        link: OneBotLink,
        host: str = "127.0.0.1",
        port: int = 8080,
        token: str = "",
    ) -> None:
        self.link = link
        self.host = host
        self.port = port
        self.token = token
        self.bound_port: int | None = None

    def _process_request(self, connection: ServerConnection, request: Any) -> Any:
        if not self.token:
            return None
        supplied = _extract_token(request)
        if supplied is None or not hmac.compare_digest(supplied, self.token):
            logger.warning("拒绝 token 校验失败的反向连接：%s", getattr(request, "path", "?"))
            return connection.respond(HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _handle(self, ws: ServerConnection) -> None:
        self.link._bind(ws)
        logger.info("✅ OneBot 实现已接入：%s", getattr(ws, "remote_address", "未知地址"))
        try:
            await self.link.read_loop(ws)
        finally:
            self.link._unbind(ws)

    async def run(self, stop: asyncio.Event) -> None:
        async with ws_serve(
            self._handle,
            self.host,
            self.port,
            process_request=self._process_request if self.token else None,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as server:
            sockets = getattr(server, "sockets", None)
            if sockets:
                self.bound_port = sockets[0].getsockname()[1]
            logger.info(
                "✅ 反向 WebSocket 服务已启动：ws://%s:%s/（等待 OneBot 实现接入）",
                self.host,
                self.bound_port or self.port,
            )
            await stop.wait()


# ---- 工具函数 -------------------------------------------------------


def _extract_token(request: Any) -> str | None:
    headers = getattr(request, "headers", None)
    if headers is not None:
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth:
            return auth.split("Bearer ")[-1].strip()
    path = getattr(request, "path", "") or ""
    query = parse_qs(urlsplit(path).query)
    values = query.get("access_token")
    return values[0].strip() if values else None


async def _close_quietly(ws: ServerConnection) -> None:
    with contextlib.suppress(Exception):
        await ws.close()
