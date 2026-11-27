# auto-reply-zhu · 详细说明

给指定 QQ 群的每条消息自动贴 ㊗️。本文讲原理、架构、全部配置项与排错；
只想跑起来请看 [README.md](README.md)。

- 目标仓库：<https://github.com/Devenir233/auto-reply-zhu>
- 原项目：[wyx6669036/QQ-auto-zhu](https://github.com/wyx6669036/QQ-auto-zhu)（MIT）
- 当前版本：2.2.0

---

## 1. 背景：为什么要换掉 NapCatQQ

1.x 版本依赖 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 的 WebSocket 服务。
但现在打开那个仓库，只剩一个 `README.md`，内容是：

```
# NapCatQQ
## Ending
到这里告一段落。
```

代码、Release 全部删除，项目已经结束。也就是说再改配置也没用，必须换协议端。

新方案换成 **OneBot 11 标准协议**：

```
QQ 客户端 ←→ LLBot（协议端，负责和 QQ 服务器通信）
                 │ OneBot 11（WebSocket，JSON）
                 ▼
            main.py（本程序：过滤 / 去重 / 限速 / 重试）
                 │ action: set_msg_emoji_like
                 └──► LLBot ──► QQ 消息上出现表情
```

好处是 **协议端可替换**：LLBot、Lagrange.OneBot、NapCat 的社区分支……
只要实现了 OneBot 11 的 `set_msg_emoji_like`，本程序都能直接跑。

### 为什么不用官方 QQ 机器人开放平台

- 官方**群机器人**默认只能收到 **@ 自己** 的消息，收不到群里所有消息
- 开放平台没有面向群消息的「表情表态」接口

因此「给群里每条消息贴 ㊗️」只能走第三方协议端。

---

## 2. 原理

### 2.1 事件：OneBot 11 的群消息上报

协议端收到群消息后，会通过 WebSocket 推送一个 JSON：

```json
{
  "post_type": "message",
  "message_type": "group",
  "sub_type": "normal",
  "self_id": 10000,
  "message_id": 12345,
  "group_id": 562204345,
  "user_id": 10086,
  "raw_message": "大家好",
  "message": [{ "type": "text", "data": { "text": "大家好" } }],
  "sender": { "user_id": 10086, "nickname": "小明", "card": "" }
}
```

本程序只关心 `post_type=message && message_type=group`，再从里面取
`group_id / message_id / user_id / raw_message`。

### 2.2 调 API：贴表情

对 **群消息** 调用 `set_msg_emoji_like`：

```json
{
  "action": "set_msg_emoji_like",
  "params": {
    "message_id": "12345",
    "emoji_id": "12951",
    "set": true
  },
  "echo": "autozhu-1"
}
```

- `echo` 是请求 ID，协议端会在回复里带回，用来做**请求/响应配对**
  （WebSocket 是全双工的，不能假设「发完下一条就是回复」）
- 成功：`{"status":"ok","retcode":0,"data":null,"echo":"autozhu-1"}`
- 失败：`{"status":"failed","retcode":1200,"message":"操作过于频繁","echo":"autozhu-1"}`
- 鉴权失败（token 不对）：部分实现会**不握手**，部分实现握手后回 `retcode=1403` 再断开
  （LLBot 是后者，所以本程序专门识别这种「没有 echo 的 1403」）

`emoji_id` 是 QQ 表情 ID，`12951` 即 ㊗️。

### 2.3 两种连接方向

| 方向 | 谁主动 | 适合 |
|---|---|---|
| **正向 WebSocket** | 本程序连协议端（默认 `ws://127.0.0.1:3001`） | 本机自用，最简单 |
| **反向 WebSocket** | 协议端连本程序（默认监听 `8080`） | 服务器、NAT 后、程序比协议端先启动 |

两种方式都支持 token：

- 正向：本程序在握手时带 `Authorization: Bearer <token>`
- 反向：LLBot 连入时带同样的 header（也接受 `?access_token=`），
  token 不对本程序直接返回 **HTTP 401** 并断开

### 2.4 可靠性设计

| 机制 | 做法 | 解决什么 |
|---|---|---|
| 自动重连 | 指数退避 1s → 30s；连接稳定 3 秒才重置退避 | 协议端重启、网络抖动 |
| 鉴权失败识别 | 收到 1403 只提示一次，重试间隔拉长到 30s | token 写错时刷屏 |
| 去重 | `(group_id, message_id)` + 时间窗（默认 300s） | 重连/离线补发导致重复贴 |
| 限速 | 令牌桶，默认 1 个/秒、突发 2 | 短时间大量贴表情被风控 |
| 重试 | 可重试错误退避重试（默认 2 次）；`msg not found`、`1404` 直接放弃 | 偶发失败不丢，永久失败不空转 |
| 事件队列 | 读事件与贴表情解耦，队列上限 2000 | 贴表情慢时不阻塞收包 |

### 2.5 进程模型

单进程、单事件循环：

```
asyncio
├── driver 任务     连接层（正向重连 / 反向监听），读包、配对 echo
├── worker 任务     从队列取事件，过滤 → 限速 → 调 API → 重试
└── 心跳/提示        连接提示、日志
```

事件由 driver 入队，worker 串行处理，保证同一群的消息按顺序贴表情；
不同消息之间通过限速器节流。

---

## 3. 安装与配置

### 3.1 环境

- Python 3.10+
- `websockets >= 14`（`requirements.txt` 已声明）

```bash
pip install -r requirements.txt
```

### 3.2 全部配置项（`config.json`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `connection.mode` | `"forward"` | `forward` 本程序连协议端；`reverse` 协议端连本程序 |
| `connection.forward_url` | `"ws://127.0.0.1:3001"` | 正向模式地址 |
| `connection.reverse_host` | `"127.0.0.1"` | 反向监听地址，`0.0.0.0` 为所有网卡 |
| `connection.reverse_port` | `8080` | 反向监听端口 |
| `connection.token` | `""` | 与协议端一致的 token |
| `connection.proxy` | `null` | 正向连接走代理时填，如 `"http://127.0.0.1:7890"` |
| `connection.llbot_data_dir` | `""` | LLBot 的 `data` 目录，用于自动同步 token |
| `reaction.groups` | —— | `{"群号": ["表情ID"]}`，`"*"` 表示所有群 |
| `reaction.keywords` | `[]` | 只对包含任一关键词的消息贴；空表示不限制 |
| `reaction.blocked_users` | `[]` | 屏蔽名单：这些 QQ 号发的消息不贴表情（全局生效） |
| `reaction.ignore_self` | `true` | 不响应机器人自己发的消息 |
| `reaction.dedupe_seconds` | `300` | 同一消息去重窗口 |
| `reaction.rate_limit_per_second` | `1.0` | 令牌桶速率（个/秒） |
| `reaction.rate_limit_burst` | `2` | 允许的突发数量 |
| `reaction.timeout` | `10` | 单次 API 超时（秒） |
| `reaction.retries` | `2` | 可重试错误的重试次数 |
| `logging.level` | `"INFO"` | `DEBUG/INFO/WARNING/ERROR` |
| `logging.file` | `""` | 写文件时填路径，自动建目录、5MB×3 轮转 |

屏蔽名单示例（这些人的消息会被直接跳过，不影响其他人）：

```json
{ "reaction": { "blocked_users": ["10001", "10002"] } }
```

环境变量（适合不把 token 写进文件）：

| 变量 | 覆盖 |
|---|---|
| `QQ_AUTO_ZHU_TOKEN` | `connection.token` |
| `QQ_AUTO_ZHU_URL` | `connection.forward_url` |
| `QQ_AUTO_ZHU_CONFIG` | 配置文件路径 |

### 3.3 反向 WebSocket 部署

```json
{
  "connection": {
    "mode": "reverse",
    "reverse_host": "0.0.0.0",
    "reverse_port": 8080,
    "token": "my-token"
  }
}
```

在 LLBot 里添加 **反向 WebSocket**：`ws://<本程序所在机器>:8080/`，token 一致。
本程序先启动监听，LLBot 主动连入；LLBot 断线会自动重连。

### 3.4 图形化配置（可选）

不想手改 JSON 可以打开内置的设置页面：

```bash
python main.py --gui              # 默认 http://127.0.0.1:8090/
python main.py --gui --gui-port 8888
```

Windows 上直接双击 `配置.bat` 即可（它会打开页面，改完点「保存」写回 `config.json`）。

实现上是一个只监听 **127.0.0.1** 的小型 HTTP 服务（`autozhu/webui.py`，标准库实现，无额外依赖）：

| 接口 | 说明 |
|---|---|
| `GET /` | 设置页面（表单 + 原生 JS，无外部资源） |
| `GET /api/config` | 读取当前配置（需要页面内嵌的随机 token） |
| `POST /api/config` | 用和命令行完全相同的校验逻辑保存配置 |

安全设计：
- 只绑定回环地址，局域网访问不到
- 每次启动生成随机 token，写在页面里、接口用 `X-AutoZhu-Token` 头校验，其他网页无法跨站提交
- 保存前走 `Config.from_dict()` 全量校验，非法配置会返回 400 并在页面上标红，不会写坏文件
- 保存只改 `config.json`，需要重启 Auto Zhu 才生效（避免运行中配置和实际行为不一致）

---

## 4. 换 QQ 号：自动同步 token

LLBot 按 QQ 号分文件保存配置：`<LLBot>/bin/llbot/data/config_<QQ号>.json`，
OneBot 11 的连接配置在 `ob11.connect[]`。换号后 token/端口都可能变。

```json
{ "connection": { "llbot_data_dir": "D:/LLBot/bin/llbot/data" } }
```

配好这一项后：

- 每次启动自动读取**最近修改**的那份 `config_*.json`，同步端口和 token
- 也可以手动：`python main.py --sync-token`（会写回 `config.json`）
- 如果新号还没开 OneBot 11，会明确提示「正向 WebSocket 不存在/未启用」

---

## 5. 兼容的协议端

| 协议端 | 状态 | 说明 |
|---|---|---|
| **LLBot（LuckyLilliaBot）** | ✅ 推荐 | LLOneBot 继任者，活跃维护，Win/Linux/Docker |
| Lagrange.OneBot | ✅ 可用 | 需自备签名服务，配置好 WS 后填 `forward_url` |
| NapCatQQ | ❌ 已停更 | 仓库已清空、无 Release |
| NapCat 社区分支 | ⚠️ 视情况 | 协议一致即可用，维护情况自辨 |

判断标准只有一个：支持 `set_msg_emoji_like`。

---

## 6. 常见问题

**Q：报 `HTTP 401` 或 `retcode=1403 token验证失败`？**
两端 token 不一致。LLBot 的 WebUI → OneBot 11 → 正向 WebSocket 里看到的值，
必须和 `config.json` 的 `connection.token` 相同；本机自用也可以两边都留空。
（LLBot 的 token 也可在 `<LLBot>/bin/llbot/data/config_<QQ号>.json` 的 `ob11.connect[].token` 里看到。）

**Q：一直「10 秒还没连上」？**
确认 LLBot 正在运行、OneBot 11 连接是**启用**状态（默认关闭）、端口没被占用。
换号后还要确认是新号那条连接。

**Q：`msg not found`？**
消息已撤回/过期，或该消息不可贴表情。程序会跳过且不重试，属正常现象。

**Q：`不支持的 API`（retcode 1404）？**
协议端版本太老，没有 `set_msg_emoji_like`，升级 LLBot。

**Q：同一条消息贴了两次？**
确认只跑了一个实例；把 LLBot 的 `reportOfflineMessage` 关掉（默认关）；
去重窗口 `dedupe_seconds` 也能兜底。

**Q：会被封号吗？**
会。第三方协议端都有风控/封号风险。用小号、降低 `rate_limit_per_second`、别 24 小时挂机。

**Q：换网络后不能用了？**
本程序连的是 `127.0.0.1`，换网络本身不影响；通常是协议端掉了（QQ 掉线）。
重新启动 LLBot 并确认登录状态，本程序会自动重连。

---

## 7. 开发与测试

```bash
python -m unittest discover -s tests -t . -v
```

测试不依赖真实 QQ：`tests/mock_onebot.py` 是一个最小 OneBot 11 协议端，
覆盖 50 个用例：正向/反向连接、token 拒绝、断线重连、去重、关键词过滤、屏蔽名单、图形设置页面的接口校验、
限速重试、1404 不重试、LLBot 配置同步、日志轮转、CLI 自检等。

```
main.py                 命令行入口
autozhu/config.py       配置加载/校验/保存
autozhu/onebot.py       OneBot 11 连接层（正向 / 反向 WS、echo 配对）
autozhu/reactor.py      贴表情业务（过滤/去重/限速/重试）
autozhu/llbot.py        读取 LLBot 配置，自动同步 token
autozhu/doctor.py       自检
autozhu/webui.py        图形化设置页面（本地 HTTP 服务）
autozhu/logging_setup.py 日志（控制台 + 轮转文件，兼容无控制台运行）
tests/                  离线集成测试（含 mock 协议端）
配置.bat                打开图形设置页面
启动.bat / 停止.bat / 安装开机自启.ps1 / 卸载开机自启.ps1  Windows 快捷脚本
```

CI：`.github/workflows/tests.yml`，在 Python 3.10 / 3.12 上跑全量测试。

---

## 8. 安全与合规

- 本程序只是 OneBot 11 客户端，不修改 QQ 客户端，不保存账号密码
- `config.json` 已被 `.gitignore` 忽略，token 不要提交
- 请只在**自己有权管理**的账号与群中使用，遵守腾讯服务条款

## 9. 许可证

[MIT](LICENSE) · 基于 [wyx6669036/QQ-auto-zhu](https://github.com/wyx6669036/QQ-auto-zhu) 重写
