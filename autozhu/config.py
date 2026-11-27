"""配置加载与校验。

配置文件为 JSON（`config.json`），字段说明见 README。
支持用环境变量覆盖敏感项（避免 token 进入 git）：

* `QQ_AUTO_ZHU_TOKEN`  -> connection.token
* `QQ_AUTO_ZHU_URL`    -> connection.forward_url
* `QQ_AUTO_ZHU_CONFIG` -> 配置文件路径（由 main.py 读取）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

EXAMPLE_CONFIG_NAME = "config.example.json"


class ConfigError(Exception):
    """配置不合法。"""


@dataclass
class ConnectionConfig:
    """与协议端建立连接的方式。"""

    mode: str = "forward"          # forward: 本程序连协议端; reverse: 协议端连本程序
    forward_url: str = "ws://127.0.0.1:3001"
    reverse_host: str = "127.0.0.1"
    reverse_port: int = 8080
    token: str = ""
    proxy: str | None = None
    llbot_data_dir: str = ""       # LLBot 的 data 目录（可选，用于自动同步 token）


@dataclass
class ReactionConfig:
    """贴表情的业务规则。"""

    groups: dict[str, list[str]] = field(default_factory=dict)  # 群号 -> 表情 ID 列表，"*" 表示所有群
    keywords: list[str] = field(default_factory=list)           # 为空表示不限制
    blocked_users: list[str] = field(default_factory=list)      # 不贴表情的 QQ 号（屏蔽名单）
    ignore_self: bool = True
    dedupe_seconds: float = 300.0
    rate_limit_per_second: float = 1.0
    rate_limit_burst: float = 2.0
    timeout: float = 10.0
    retries: int = 2

    def emojis_for(self, group_id: int) -> list[str]:
        """返回某个群要贴的表情；\"*\" 表示所有群。"""
        exact = self.groups.get(str(group_id))
        if exact:
            return exact
        return self.groups.get("*", [])


@dataclass
class LogConfig:
    level: str = "INFO"
    file: str = ""


@dataclass
class Config:
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    reaction: ReactionConfig = field(default_factory=ReactionConfig)
    logging: LogConfig = field(default_factory=LogConfig)
    source: Path | None = None

    # ---- 加载 -------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise ConfigError(f"找不到配置文件：{path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是合法的 JSON（{path}）：{exc}") from exc
        return cls.from_dict(raw, source=path)

    @classmethod
    def from_dict(cls, raw: Any, source: Path | None = None) -> "Config":
        """从字典构建配置（图形界面保存时复用同一套校验）。"""
        if not isinstance(raw, dict):
            raise ConfigError("配置文件根节点必须是对象")

        cfg = cls(
            connection=_build(ConnectionConfig, raw.get("connection"), "connection"),
            reaction=_build(ReactionConfig, raw.get("reaction"), "reaction"),
            logging=_build(LogConfig, raw.get("logging"), "logging"),
            source=source,
        )
        cfg._apply_env()
        cfg._normalize()
        cfg._validate()
        return cfg

    # ---- 环境变量 ---------------------------------------------------

    def _apply_env(self) -> None:
        token = os.environ.get("QQ_AUTO_ZHU_TOKEN")
        if token:
            self.connection.token = token
        url = os.environ.get("QQ_AUTO_ZHU_URL")
        if url:
            self.connection.forward_url = url

    # ---- 规整 -------------------------------------------------------

    def _normalize(self) -> None:
        self.connection.mode = str(self.connection.mode).strip().lower()
        self.connection.forward_url = str(self.connection.forward_url).strip()
        self.connection.reverse_host = str(self.connection.reverse_host).strip()
        self.connection.token = str(self.connection.token or "").strip()
        self.connection.llbot_data_dir = str(self.connection.llbot_data_dir or "").strip()

        groups: dict[str, list[str]] = {}
        for key, value in (self.reaction.groups or {}).items():
            name = str(key).strip()
            if name != "*" and not name.isdigit():
                raise ConfigError(f"群号必须是数字或 \"*\"，而不是 {key!r}")
            emojis = value if isinstance(value, (list, tuple)) else [value]
            cleaned = [str(item).strip() for item in emojis if str(item).strip()]
            if not cleaned:
                raise ConfigError(f"群 {name} 没有配置任何表情 ID")
            groups[name] = cleaned
        self.reaction.groups = groups

        self.reaction.keywords = [str(k) for k in (self.reaction.keywords or []) if str(k)]

        blocked: list[str] = []
        for item in self.reaction.blocked_users or []:
            value = str(item).strip()
            if not value:
                continue
            if not value.isdigit():
                raise ConfigError(
                    f"reaction.blocked_users 里必须是 QQ 号（纯数字），而不是 {item!r}"
                )
            blocked.append(value)
        # 去重但保留顺序，方便日志和自检输出
        self.reaction.blocked_users = list(dict.fromkeys(blocked))
        self.logging.level = str(self.logging.level or "INFO").upper()
        self.logging.file = str(self.logging.file or "")

    # ---- 校验 -------------------------------------------------------

    def _validate(self) -> None:
        conn, react = self.connection, self.reaction

        if conn.mode not in ("forward", "reverse"):
            raise ConfigError("connection.mode 只能是 \"forward\" 或 \"reverse\"")

        if conn.mode == "forward":
            if not conn.forward_url.startswith(("ws://", "wss://")):
                raise ConfigError("connection.forward_url 必须以 ws:// 或 wss:// 开头")
        else:
            if not 1 <= int(conn.reverse_port) <= 65535:
                raise ConfigError("connection.reverse_port 必须在 1-65535 之间")

        if not react.groups:
            raise ConfigError("reaction.groups 至少要配置一个群，例如 {\"562204345\": [\"12951\"]}")

        for emoji in {e for emojis in react.groups.values() for e in emojis}:
            if not emoji.isdigit():
                raise ConfigError(f"表情 ID 必须是数字（QQ 表情 ID，如 12951 表示 ㊗️），而不是 {emoji!r}")

        if react.rate_limit_per_second <= 0:
            raise ConfigError("reaction.rate_limit_per_second 必须大于 0")
        if react.rate_limit_burst < 1:
            raise ConfigError("reaction.rate_limit_burst 不能小于 1")
        if react.retries < 0:
            raise ConfigError("reaction.retries 不能为负数")
        if react.timeout <= 0:
            raise ConfigError("reaction.timeout 必须大于 0")

    # ---- 保存 -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def _plain(value: Any) -> Any:
            if is_dataclass(value):
                return {f: _plain(getattr(value, f)) for f in value.__dataclass_fields__}
            return value

        return {name: _plain(getattr(self, name)) for name in ("connection", "reaction", "logging")}

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else self.source
        if target is None:
            raise ConfigError("没有可保存的配置路径")
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    # ---- 便捷方法 ---------------------------------------------------

    def emojis_for(self, group_id: int) -> list[str]:
        return self.reaction.emojis_for(group_id)

    def describe(self) -> str:
        if self.connection.mode == "forward":
            target = f"正向 WebSocket -> {self.connection.forward_url}"
        else:
            target = f"反向 WebSocket 监听 {self.connection.reverse_host}:{self.connection.reverse_port}"
        groups = "，".join(
            f"{gid}=>{','.join(ids)}" for gid, ids in self.reaction.groups.items()
        )
        text = f"{target} | 群: {groups}"
        if self.reaction.blocked_users:
            text += f" | 屏蔽: {len(self.reaction.blocked_users)} 人"
        return text


def _build(cls: type, data: Any, section: str):
    """按 dataclass 字段挑选已知键，未知键给出提示而不是静默忽略。"""
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"{section} 必须是对象")
    known = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{section} 中存在未知字段：{', '.join(sorted(unknown))}")
    try:
        return cls(**data)
    except TypeError as exc:  # pragma: no cover - 由上面的 unknown 检查兜底
        raise ConfigError(f"{section} 配置有误：{exc}") from exc


def example_config_text() -> str:
    """返回随仓库分发的示例配置内容。"""
    path = Path(__file__).resolve().parent.parent / EXAMPLE_CONFIG_NAME
    return path.read_text(encoding="utf-8")
