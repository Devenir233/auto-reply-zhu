"""业务逻辑：收到群消息后调用 set_msg_emoji_like 贴表情。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .config import Config, ReactionConfig
from .onebot import ActionFailed, ForwardDriver, NotConnected, OneBotLink, ReverseDriver

logger = logging.getLogger(__name__)

# 这些错误重试也没有意义：消息被撤回 / 过期，或协议端根本不支持该接口。
_PERMANENT_HINTS = (
    "msg not found",
    "message not found",
    "消息不存在",
    "消息已过期",
    "不支持",
    "not supported",
    "unsupported",
    "invalid message",
)


def is_permanent_failure(exc: ActionFailed) -> bool:
    if str(exc.retcode) == "1404":  # OneBot: 不支持的 API
        return True
    message = (exc.message or "").lower()
    return any(hint in message for hint in _PERMANENT_HINTS)


@dataclass
class Stats:
    reacted: int = 0
    failed: int = 0
    skipped: int = 0
    blocked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "reacted": self.reacted,
            "failed": self.failed,
            "skipped": self.skipped,
            "blocked": self.blocked,
        }


class TokenBucket:
    """简单的令牌桶限速器，避免短时间内大量贴表情触发风控。"""

    def __init__(self, rate_per_second: float, burst: float) -> None:
        self.rate = max(float(rate_per_second), 0.01)
        self.capacity = max(float(burst), 1.0)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self.rate)


class ReactionService:
    """把群消息事件转成贴表情请求。"""

    def __init__(
        self,
        link: OneBotLink,
        config: ReactionConfig,
        *,
        dry_run: bool = False,
    ) -> None:
        self.link = link
        self.config = config
        self.dry_run = dry_run
        self.stats = Stats()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self._seen: "OrderedDict[tuple[int, int], float]" = OrderedDict()
        self._bucket = TokenBucket(config.rate_limit_per_second, config.rate_limit_burst)
        self._blocked: set[str] = set(config.blocked_users)

    # ---- 事件入口（由连接层同步调用，只做入队）----------------------

    def on_event(self, event: dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        if event.get("message_type") != "group":
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.stats.skipped += 1
            logger.warning("事件队列已满，丢弃一条消息（协议端上报过快或处理被阻塞）")

    # ---- 工作协程 ---------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        # 用带超时的取队列，而不是纯 cancel：这样停服时能确定性地退出，
        # 不依赖「取消正在等待 queue.get() 的任务」这一在部分 3.10 环境会卡住的路径。
        while not stop.is_set():
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - 单条消息失败不应导致退出
                self.stats.failed += 1
                logger.exception("处理消息时出现未预期的异常")

    # ---- 核心逻辑 ---------------------------------------------------

    async def handle(self, event: dict[str, Any]) -> None:
        group_id = _as_int(event.get("group_id"))
        message_id = _as_int(event.get("message_id"))
        if group_id is None or message_id is None:
            return

        emoji_ids = self.config.emojis_for(group_id)
        if not emoji_ids:
            return

        user_id = _as_int(event.get("user_id")) or _as_int(
            (event.get("sender") or {}).get("user_id")
        )
        if self.config.ignore_self and user_id is not None and self.link.self_id == user_id:
            return

        if user_id is not None and str(user_id) in self._blocked:
            self.stats.blocked += 1
            logger.debug("用户在屏蔽名单中，跳过：%s（群 %s）", user_id, group_id)
            return

        text = message_text(event)
        if self.config.keywords:
            haystack = text.casefold()
            if not any(keyword.casefold() in haystack for keyword in self.config.keywords):
                return

        if self._is_duplicate(group_id, message_id):
            logger.debug("消息 %s 已处理过，跳过（去重窗口内）", message_id)
            return

        sender = event.get("sender") or {}
        nickname = sender.get("card") or sender.get("nickname") or str(user_id or "未知用户")

        for emoji_id in emoji_ids:
            await self._bucket.acquire()
            await self._react(group_id, message_id, emoji_id, nickname, text)

    async def _react(
        self,
        group_id: int,
        message_id: int,
        emoji_id: str,
        nickname: str,
        text: str,
    ) -> None:
        preview = _preview(text)
        if self.dry_run:
            logger.info(
                "[dry-run] 群 %s | %s | 消息 %s %s -> 表情 %s",
                group_id, nickname, message_id, preview, emoji_id,
            )
            self.stats.reacted += 1
            return

        attempts = self.config.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                await self.link.call(
                    "set_msg_emoji_like",
                    {"message_id": str(message_id), "emoji_id": str(emoji_id), "set": True},
                    timeout=self.config.timeout,
                )
            except NotConnected:
                logger.warning("当前未连接协议端，消息 %s 的贴表情请求已放弃", message_id)
                self.stats.failed += 1
                return
            except ActionFailed as exc:
                if is_permanent_failure(exc):
                    self.stats.failed += 1
                    logger.warning(
                        "消息 %s 贴表情失败（不可重试）：%s", message_id, exc.message or exc.retcode
                    )
                    return
                if attempt >= attempts:
                    self.stats.failed += 1
                    logger.error("消息 %s 贴表情失败（已重试 %d 次）：%s", message_id, attempt - 1, exc)
                    return
                delay = 0.5 * (2 ** (attempt - 1))
                logger.info("消息 %s 贴表情失败，%.1f 秒后重试：%s", message_id, delay, exc.message or exc.retcode)
                await asyncio.sleep(delay)
            else:
                self.stats.reacted += 1
                logger.info("✅ 群 %s | %s | 消息 %s %s -> 表情 %s", group_id, nickname, message_id, preview, emoji_id)
                return

    # ---- 去重 -------------------------------------------------------

    def _is_duplicate(self, group_id: int, message_id: int) -> bool:
        now = time.monotonic()
        window = self.config.dedupe_seconds
        while self._seen:
            _, created = next(iter(self._seen.items()))
            if now - created <= window:
                break
            self._seen.popitem(last=False)

        key = (group_id, message_id)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


class AutoZhuApp:
    """把连接层与业务层组装起来的应用。"""

    def __init__(self, config: Config, *, dry_run: bool = False) -> None:
        self.config = config
        self.link = OneBotLink(action_timeout=config.reaction.timeout)
        self.service = ReactionService(self.link, config.reaction, dry_run=dry_run)
        self.driver: ForwardDriver | ReverseDriver | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._started = False

    @property
    def connected(self) -> bool:
        return self.link.connected

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.link.add_event_handler(self.service.on_event)

        conn = self.config.connection
        if conn.mode == "forward":
            self.driver = ForwardDriver(self.link, conn.forward_url, conn.token, conn.proxy)
        else:
            self.driver = ReverseDriver(self.link, conn.reverse_host, conn.reverse_port, conn.token)

        self._tasks = [
            asyncio.create_task(self.driver.run(self._stop), name="onebot-driver"),
            asyncio.create_task(self.service.run(self._stop), name="reaction-worker"),
        ]

        # 反向模式下，等端口真正 listen 之后再返回，避免调用方立刻连接时被拒。
        if isinstance(self.driver, ReverseDriver):
            try:
                await asyncio.wait_for(self.driver.ready.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.error("反向 WebSocket 服务 5 秒内没有启动，请检查端口是否被占用")

    async def stop(self) -> None:
        self._stop.set()
        tasks, self._tasks = self._tasks, []
        if tasks:
            # 只强杀连接任务；worker 会自己看到 stop 事件后退出（最多 0.5 秒）。
            for task in tasks:
                if task.get_name() == "onebot-driver":
                    task.cancel()
            await asyncio.sleep(0)
            _done, pending = await asyncio.wait(tasks, timeout=5)
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=1)
            for task in tasks:
                if task.done():
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        task.result()
                else:
                    logger.warning("后台任务 %s 未能及时退出", task.get_name())
        self._started = False

    async def wait_connected(self, timeout: float = 15.0) -> bool:
        return await self.link.wait_connected(timeout)

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stop.wait()
        finally:
            await self.stop()


# ---- 工具函数 -------------------------------------------------------


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def message_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_message")
    if isinstance(raw, str) and raw:
        return raw
    message = event.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "text":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                text = data.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _preview(text: str, limit: int = 40) -> str:
    flat = " ".join(text.split())
    if not flat:
        return "（非文本消息）"
    return flat if len(flat) <= limit else flat[:limit] + "…"
