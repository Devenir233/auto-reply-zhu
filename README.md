# auto-reply-zhu

[![tests](https://github.com/Devenir233/auto-reply-zhu/actions/workflows/tests.yml/badge.svg)](https://github.com/Devenir233/auto-reply-zhu/actions/workflows/tests.yml)

给指定 QQ 群的每条消息自动贴 **㊗️**（或任意 QQ 表情）。

基于 **OneBot 11** 标准协议，配套 [LLBot](https://github.com/LuckyLilliaDev/LuckyLilliaBot) 作为协议端
（NapCatQQ 已停止维护）。原理与完整配置见 [README.detailed.md](README.detailed.md)。

---

## 前置条件

- Python **3.10+**（Windows / Linux / macOS）
- 一个能登录 QQ 的协议端：推荐 **LLBot**（LLOneBot 的继任者，仍在维护）

## 三步跑起来

### 1. 启动 LLBot 并登录 QQ

到 [LLBot Releases](https://github.com/LuckyLilliaDev/LuckyLilliaBot/releases) 下载
`LuckyLillia-CLI-win-x64.zip`，解压后运行 `LuckyLillia.exe`，用手机扫码登录，窗口保持打开。

### 2. 打开 OneBot 11 正向 WebSocket

浏览器访问 <http://127.0.0.1:3080> → **OneBot 11** → 添加一条 **正向 WebSocket（ws）**：

| 项 | 值 |
|---|---|
| 地址 | `127.0.0.1` |
| 端口 | `3001` |
| token | 随便设一个，例如 `my-token` |
| 启用 | ✅ |

### 3. 运行本程序

```bash
git clone https://github.com/Devenir233/auto-reply-zhu.git
cd auto-reply-zhu
pip install -r requirements.txt
python main.py
```

首次运行会自动生成 `config.json`，改两处后再运行一次：

```json
{
  "connection": {
    "token": "my-token"
  },
  "reaction": {
    "groups": { "你的群号": ["12951"] },
    "blocked_users": ["要屏蔽的QQ号"]
  }
}
```

- `12951` 是 ㊗️ 的表情 ID，可以换成别的
- `"groups"` 的键填群号；填 `"*"` 表示所有群
- 想一次贴多个表情：`["12951", "178"]`

看到 `✅ 已连接 OneBot 实现` 后，群里发条消息就会自动贴上表情。

### 屏蔽某些人（可选）

不想给某些人贴表情，把他们的 QQ 号加到 `reaction.blocked_users`：

```json
{
  "reaction": {
    "groups": { "你的群号": ["12951"] },
    "blocked_users": ["10001", "10002"]
  }
}
```

- 名单**全局生效**：这些人在所有被监听的群里都不会被贴表情
- 填 QQ 号即可（纯数字，字符串或数字都行），其他人不受影响
- 留空或删掉这个字段 = 不屏蔽任何人

## 自检

```bash
python main.py --doctor                        # 检查连接 / 账号 / 群号 / token
python main.py --doctor --test-reaction 123456 # 给该群最近一条消息真贴一次
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `python main.py` | 运行 |
| `python main.py --doctor` | 自检后退出 |
| `python main.py --dry-run` | 只打印，不真的贴表情 |
| `python main.py --sync-token` | 换 QQ 号后，从 LLBot 配置同步 token/端口 |
| `python main.py --log-level DEBUG` | 临时提高日志级别 |
| `python main.py -c 配置路径` | 指定配置文件 |

## 后台常驻（Windows）

- 双击 `启动.bat`：前台运行，窗口别关（没装 Python 或不在 PATH 里会直接提示）
- 或装成计划任务（登录后自动后台运行，日志在 `logs/auto-zhu.log`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\安装开机自启.ps1"
Start-ScheduledTask -TaskName "Auto Zhu"
```

取消自启：`powershell -NoProfile -ExecutionPolicy Bypass -File ".\卸载开机自启.ps1"`；
临时停止：`Stop-ScheduledTask -TaskName "Auto Zhu"` 或双击 `停止.bat`
（`停止.bat` 会同时结束计划任务和由 `启动.bat` 启动的前台实例）；
用 `启动.bat` 启动的实例才能被 `停止.bat` 精确识别，直接 `python main.py` 的请用 Ctrl+C。

## 换 QQ 号

新号登录 LLBot 后，在 WebUI 里给它开启 OneBot 11 正向 WebSocket，然后：

```bash
python main.py --sync-token   # 自动读取新号的端口和 token
python main.py --doctor       # 确认新号在目标群里
```

> 需要先在 `config.json` 的 `connection.llbot_data_dir` 填一次 LLBot 的 data 目录
> （默认 `<LLBot>/bin/llbot/data`）。

## 注意事项

- LLBot 必须保持运行；它掉线时本程序会自动重连等待，不会退出
- 同一时间只运行一个本程序实例，否则同一条消息会被贴两次
- 第三方协议端都有风控/封号风险，建议小号并使用默认限速

---

详细原理、全部配置项、反向 WebSocket 部署、FAQ → [README.detailed.md](README.detailed.md)

## License

[MIT](LICENSE)
