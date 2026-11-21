"""自检：确认协议端连上了、账号登录了、群号/表情配置是对的。"""

from __future__ import annotations

import asyncio
from typing import Any

from .config import Config
from .reactor import AutoZhuApp


async def _try_call(app: AutoZhuApp, action: str, params: dict[str, Any] | None = None):
    """调用 API，把异常转换成 (False, 异常)，方便逐项报告。"""
    try:
        return True, await app.link.call(action, params, timeout=10)
    except Exception as exc:  # noqa: BLE001 - 自检需要展示所有失败原因
        return False, exc


async def run_doctor(app: AutoZhuApp, config: Config, *, test_group: int | None = None) -> int:
    problems = 0

    conn = config.connection
    if conn.mode == "forward":
        where = f"连接 {conn.forward_url}"
    else:
        where = f"监听 {conn.reverse_host}:{conn.reverse_port}"
    print(f"🔌 等待协议端（{where}）…")

    connected = await app.wait_connected(20)
    if connected:
        # 握手成功不代表鉴权成功：等协议端的 1403 回包到达后再判断。
        await asyncio.sleep(0.3)
        if app.link.token_rejected:
            print("❌ 协议端拒绝了连接：token 校验失败（retcode 1403）")
            print("   把 config.json 的 connection.token 改成协议端里配置的 token；本机自用也可以两边都留空")
            return 1

    if not connected:
        print("❌ 20 秒内没有建立连接")
        if conn.mode == "forward":
            print("   · 检查 LLBot 是否已打开 OneBot11 的“正向 WebSocket”（默认 127.0.0.1:3001）")
            print("   · 检查 connection.forward_url 与 token 是否和服务端一致")
        else:
            print("   · 检查 LLBot 的“反向 WebSocket”地址是否指向本机这个端口")
            print("   · 检查两端 token 是否一致")
        return 1
    print("✅ 连接正常")

    ok, version = await _try_call(app, "get_version_info")
    if ok and isinstance(version, dict):
        print(
            "🧩 协议端：{} {}（OneBot {}）".format(
                version.get("app_name", "未知"),
                version.get("app_version", "?"),
                version.get("protocol_version", "?"),
            )
        )
    else:
        problems += 1
        print(f"⚠️  get_version_info 调用失败：{version}")

    ok, info = await _try_call(app, "get_login_info")
    if ok and isinstance(info, dict):
        user_id = info.get("user_id")
        print(f"👤 登录账号：{user_id}（{info.get('nickname', '')}）")
        if user_id is not None:
            try:
                app.link.self_id = int(user_id)
            except (TypeError, ValueError):
                pass
    else:
        problems += 1
        print(f"⚠️  get_login_info 调用失败：{info}")

    ok, groups = await _try_call(app, "get_group_list")
    group_names: dict[str, str] = {}
    if ok and isinstance(groups, list):
        for item in groups:
            if not isinstance(item, dict) or "group_id" not in item:
                continue
            group_names[str(item["group_id"])] = str(item.get("group_name", ""))
        print(f"👥 当前账号共有 {len(group_names)} 个群")
    else:
        problems += 1
        print(f"⚠️  get_group_list 调用失败：{groups}")

    print("\n🎯 监听配置：")
    for group_id, emojis in config.reaction.groups.items():
        if group_id == "*":
            print(f"   · 所有群 -> 表情 {', '.join(emojis)}")
            continue
        if group_id in group_names:
            print(f"   · 群 {group_id}（{group_names[group_id]}）-> 表情 {', '.join(emojis)}")
        else:
            problems += 1
            print(f"   · 群 {group_id} ❌ 不在该账号的群列表中（群号写错或机器人不在群里）")

    if test_group is not None:
        problems += await _test_reaction(app, config, test_group)

    print("\n" + ("✅ 自检通过，可以开始工作了" if problems == 0 else f"⚠️  发现 {problems} 个问题"))
    return 0 if problems == 0 else 1


async def _test_reaction(app: AutoZhuApp, config: Config, group_id: int) -> int:
    emojis = config.emojis_for(group_id)
    if not emojis:
        print(f"\n🧪 群 {group_id} 没有配置表情，跳过实测")
        return 1

    ok, history = await _try_call(app, "get_group_msg_history", {"group_id": group_id, "count": 1})
    if not ok:
        print(f"\n🧪 无法获取群 {group_id} 的历史消息：{history}")
        return 1

    messages = history
    if isinstance(history, dict):
        messages = history.get("messages")
    if not isinstance(messages, list) or not messages:
        print(f"\n🧪 群 {group_id} 最近没有可用消息，跳过实测")
        return 1

    latest = messages[-1]
    message_id = latest.get("message_id") if isinstance(latest, dict) else None
    if message_id is None:
        print("\n🧪 历史消息里没有 message_id，跳过实测")
        return 1

    ok, result = await _try_call(
        app,
        "set_msg_emoji_like",
        {"message_id": str(message_id), "emoji_id": emojis[0], "set": True},
    )
    if ok:
        print(f"\n🧪 实测贴表情成功：消息 {message_id} -> {emojis[0]}")
        return 0
    print(f"\n🧪 实测贴表情失败：{result}")
    return 1
