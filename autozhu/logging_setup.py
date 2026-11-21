"""日志初始化。"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import sys
from pathlib import Path

from .config import LogConfig

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def setup_logging(config: LogConfig) -> None:
    level = getattr(logging, (config.level or "INFO").upper(), logging.INFO)

    handlers: list[logging.Handler] = []
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(stream=sys.stdout))

    if config.file:
        path = Path(config.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                path,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
        )

    # 无控制台（pythonw / 计划任务隐藏窗口）时也要能正常跑。
    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # 协议端/websockets 的调试日志太多，正常情况下不输出。
    logging.getLogger("websockets").setLevel(logging.WARNING)


def make_streams_utf8_safe() -> None:
    """管道/重定向到文件时强制 UTF-8，避免中文日志在 GBK 控制台里炸掉。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")
