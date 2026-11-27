#!/usr/bin/env python3
"""Auto Zhu —— 给指定的 QQ 群消息自动贴表情（㊗️ 等）。

快速开始：
    pip install -r requirements.txt
    python main.py            # 运行
    python main.py --doctor   # 自检（确认协议端、账号、群号都正确）
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from autozhu import __version__
from autozhu.config import Config, ConfigError, EXAMPLE_CONFIG_NAME
from autozhu.doctor import run_doctor
from autozhu.llbot import LLBotConfigError, LLBotEndpoint, apply_endpoint, read_llbot_endpoint
from autozhu.logging_setup import make_streams_utf8_safe, setup_logging
from autozhu.reactor import AutoZhuApp

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"

logger = logging.getLogger("autozhu")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="auto-zhu",
        description="给指定 QQ 群的消息自动贴表情（OneBot 11）",
    )
    parser.add_argument("-c", "--config", type=Path, help="配置文件路径（默认 ./config.json）")
    parser.add_argument("--doctor", action="store_true", help="连上后自检账号/群号/接口，然后退出")
    parser.add_argument(
        "--test-reaction",
        type=int,
        metavar="GROUP_ID",
        help="自检时向该群最近一条消息实际贴一次表情，然后退出",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将要贴的表情，不真的调用接口")
    parser.add_argument("--gui", action="store_true", help="打开图形化设置页面（本地网页，改完保存即可）")
    parser.add_argument(
        "--gui-port",
        type=int,
        default=8090,
        metavar="PORT",
        help="设置页面监听的本地端口（默认 8090）",
    )
    parser.add_argument(
        "--sync-token",
        action="store_true",
        help="从 LLBot 的账号配置里同步端口/token 到 config.json，然后退出",
    )
    parser.add_argument(
        "--llbot-data",
        type=Path,
        metavar="DIR",
        help="LLBot 的 data 目录（默认读 config.json 里的 connection.llbot_data_dir）",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="覆盖配置文件里的日志级别",
    )
    parser.add_argument("--version", action="version", version=f"auto-zhu {__version__}")
    return parser.parse_args(argv)


def resolve_config_path(arg_path: Path | None) -> Path:
    if arg_path is not None:
        return arg_path
    env_path = os.environ.get("QQ_AUTO_ZHU_CONFIG")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG


def ensure_config_exists(path: Path) -> bool:
    """首次运行时从示例配置生成 config.json，并提示用户修改。"""
    if path.exists():
        return True
    example = ROOT / EXAMPLE_CONFIG_NAME
    if not example.exists():
        raise ConfigError(f"找不到 {example}，无法生成默认配置")
    path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"📝 已生成默认配置文件：{path}")
    print("   请把 reaction.groups 改成要监听的群号（可改表情 ID），再重新运行。")
    return False


def sync_from_llbot(config: Config, data_dir: str | Path | None = None) -> LLBotEndpoint | None:
    """按需从 LLBot 的 config_<QQ号>.json 同步端口和 token。"""
    directory = data_dir or config.connection.llbot_data_dir
    if not directory:
        return None

    try:
        endpoint = read_llbot_endpoint(directory)
    except LLBotConfigError as exc:
        logger.warning("读取 LLBot 配置失败：%s", exc)
        return None

    if not endpoint.enabled:
        logger.warning("LLBot 的 OneBot 11 还没准备好：%s", endpoint.reason)
        return endpoint

    if apply_endpoint(config, endpoint):
        logger.info(
            "已从 LLBot 配置同步：QQ %s | 端口 %s",
            endpoint.uin,
            endpoint.port,
        )
    return endpoint


async def connection_hint(app: AutoZhuApp, config: Config) -> None:
    """连不上时给一次性的排查提示，避免用户面对空白日志。"""
    await asyncio.sleep(10)
    if app.connected:
        return
    if config.connection.mode == "forward":
        logger.warning(
            "已经 10 秒还没连上 %s：请确认协议端已启动并开启了 OneBot11 正向 WebSocket，token 一致",
            config.connection.forward_url,
        )
    else:
        logger.warning(
            "反向 WebSocket 已在 %s:%s 监听，但还没有协议端接入：请在 LLBot 里添加反向 WebSocket 指向本机",
            config.connection.reverse_host,
            config.connection.reverse_port,
        )


async def run_settings_ui(config_path: Path, port: int) -> int:
    """启动本地设置页面，并用默认浏览器打开。"""
    import webbrowser

    from autozhu.webui import SettingsServer

    server = SettingsServer(config_path, port=port)
    url = server.start()
    print(f"🌐 设置页面已启动：{url}", flush=True)
    print("   改完点「保存」写入 config.json，重启 Auto Zhu 后生效。按 Ctrl+C 关闭本页面。", flush=True)
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - 没有浏览器的极端情况
        print("   （没能自动打开浏览器，请手动访问上面的地址）", flush=True)

    try:
        await asyncio.Event().wait()
    finally:
        server.stop()
    return 0


async def amain(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    if not ensure_config_exists(config_path):
        return 1

    if args.gui:
        return await run_settings_ui(config_path, args.gui_port)

    config = Config.load(config_path)
    if args.log_level:
        config.logging.level = args.log_level
    if args.llbot_data is not None:
        config.connection.llbot_data_dir = str(args.llbot_data)
    setup_logging(config.logging)

    endpoint = sync_from_llbot(config, args.llbot_data)

    if args.sync_token:
        if endpoint is None:
            print("❌ 没有可同步的 LLBot 配置：请用 --llbot-data 指定 LLBot 的 data 目录，")
            print("   或在 config.json 里设置 connection.llbot_data_dir")
            return 1
        if not endpoint.enabled:
            print(f"❌ LLBot 里这个账号的 OneBot 11 还没开好：{endpoint.reason}")
            print(f"   配置文件：{endpoint.config_path}")
            print("   请打开 http://127.0.0.1:3080 配置后再试")
            return 1
        config.save()
        print(f"✅ 已同步到 config.json：QQ {endpoint.uin} | 端口 {endpoint.port} | token {'已写入' if endpoint.token else '（空）'}")
        return 0

    logger.info("Auto Zhu v%s 启动", __version__)
    logger.info("接入方式：%s", config.describe())
    if not config.connection.token:
        logger.warning(
            "connection.token 为空：如果协议端开启了 token 鉴权，连接会被 1403 拒绝；"
            "请把两端 token 配成一致"
        )
    if args.dry_run:
        logger.warning("dry-run 模式：只打印，不会真的贴表情")

    app = AutoZhuApp(config, dry_run=args.dry_run)

    if args.doctor or args.test_reaction is not None:
        await app.start()
        hint = asyncio.create_task(connection_hint(app, config))
        try:
            return await run_doctor(app, config, test_group=args.test_reaction)
        finally:
            hint.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hint
            await app.stop()

    await app.start()
    hint = asyncio.create_task(connection_hint(app, config))
    try:
        await app.run_forever()
    finally:
        hint.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hint
        await app.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    make_streams_utf8_safe()
    args = parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except ConfigError as exc:
        print(f"❌ 配置错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n👋 已停止")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
