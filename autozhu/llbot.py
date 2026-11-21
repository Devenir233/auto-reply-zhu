"""读取 LLBot 的账号配置，自动同步 OneBot 11 的端口和 token。

LLBot（LuckyLilliaBot）把每个 QQ 号的配置存成
`<data 目录>/config_<QQ号>.json`，OneBot 11 的连接配置在 `ob11.connect[]`
里。换 QQ 号之后 token 会变，这里负责把它读出来。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import Config


class LLBotConfigError(Exception):
    """找不到/读不了 LLBot 配置。"""


@dataclass
class LLBotEndpoint:
    config_path: Path
    uin: str | None = None
    port: int | None = None
    token: str | None = None
    enabled: bool = False
    reason: str = ""


def read_llbot_endpoint(data_dir: str | Path) -> LLBotEndpoint:
    """从 LLBot 的 data 目录里找出当前账号的正向 WebSocket 配置。

    同一个目录可能同时存在多个 `config_<QQ号>.json`（换过号），
    以最近修改的那份为准。
    """
    directory = Path(data_dir).expanduser()
    if not directory.is_dir():
        raise LLBotConfigError(f"不是有效目录：{directory}")

    files = sorted(directory.glob("config_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise LLBotConfigError(f"{directory} 里没有 config_*.json（账号还没登录过？）")

    for path in files:
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue

        uin = path.stem.removeprefix("config_")
        ob11 = raw.get("ob11") or {}
        if not ob11.get("enable", False):
            return LLBotEndpoint(path, uin, reason="OneBot 11 总开关没开（WebUI -> OneBot 11）")

        connects = [c for c in (ob11.get("connect") or []) if isinstance(c, dict)]
        ws = [c for c in connects if c.get("type") == "ws"]
        enabled = [c for c in ws if c.get("enable")]
        if not enabled:
            hint = "存在但未启用" if ws else "不存在"
            return LLBotEndpoint(path, uin, reason=f"正向 WebSocket {hint}（WebUI -> OneBot 11 -> 添加 ws 连接）")

        current = enabled[0]
        try:
            port = int(current.get("port") or 3001)
        except (TypeError, ValueError):
            port = 3001
        return LLBotEndpoint(
            config_path=path,
            uin=uin,
            port=port,
            token=str(current.get("token") or ""),
            enabled=True,
        )

    raise LLBotConfigError(f"{directory} 里的 config_*.json 都读不了")


def apply_endpoint(config: Config, endpoint: LLBotEndpoint) -> bool:
    """把 LLBot 的端口/token 应用到内存配置，返回是否有变化。"""
    if not endpoint.enabled:
        return False

    changed = False
    if endpoint.token is not None and endpoint.token != config.connection.token:
        config.connection.token = endpoint.token
        changed = True

    if endpoint.port and config.connection.mode == "forward":
        parts = urlsplit(config.connection.forward_url)
        if parts.port != endpoint.port:
            netloc = f"{parts.hostname or '127.0.0.1'}:{endpoint.port}"
            config.connection.forward_url = urlunsplit(
                (parts.scheme or "ws", netloc, parts.path or "", parts.query, parts.fragment)
            )
            changed = True
    return changed
