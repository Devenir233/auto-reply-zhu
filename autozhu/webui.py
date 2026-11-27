"""本地网页版图形设置界面。

启动后监听 127.0.0.1，用浏览器打开即可编辑 config.json；
保存时走和命令行完全相同的校验逻辑。页面只在本机可访问，
接口还需要页面内嵌的随机 token，避免其它网页跨站提交。
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config, ConfigError

logger = logging.getLogger(__name__)

MAX_BODY = 1 << 20  # 1MB，足够大也不会被灌爆


PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auto Zhu 设置</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 28px 16px 56px;
    font-family: "Segoe UI", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
    background: #f4f6f9; color: #1f2329; line-height: 1.55;
  }
  header, main { max-width: 880px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 6px; }
  h2 { font-size: 15px; margin: 0 0 14px; color: #33507a; }
  .sub { color: #6b7785; font-size: 13px; }
  .sub code { background: #e8ecf2; padding: 2px 6px; border-radius: 4px; }
  .card {
    background: #fff; border: 1px solid #e3e8ef; border-radius: 12px;
    padding: 18px 20px; margin-top: 16px;
    box-shadow: 0 1px 2px rgba(16, 24, 40, .04);
  }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px 18px; }
  label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: #475467; }
  label.check { flex-direction: row; align-items: center; gap: 8px; }
  input[type=text], input[type=password], input[type=number], select, textarea {
    width: 100%; padding: 8px 10px; border: 1px solid #cfd8e3; border-radius: 8px;
    background: #fff; color: #1f2329; font: inherit; font-size: 13px;
  }
  textarea { resize: vertical; min-height: 64px; }
  input:focus, select:focus, textarea:focus { outline: 2px solid #7aa7ff; outline-offset: 1px; border-color: #7aa7ff; }
  .hint { margin: 10px 0 0; color: #6b7785; font-size: 12.5px; }
  .group-row { display: grid; grid-template-columns: 1fr 1.4fr auto; gap: 10px; margin-bottom: 8px; }
  button {
    font: inherit; font-size: 13px; padding: 8px 14px; border-radius: 8px;
    border: 1px solid #cfd8e3; background: #fff; color: #1f2329; cursor: pointer;
  }
  button:hover { background: #f2f5f9; }
  button.primary { background: #2f6fed; border-color: #2f6fed; color: #fff; }
  button.primary:hover { background: #255dd0; }
  button.danger { color: #b42318; border-color: #f1c0bb; }
  button.ghost { background: transparent; }
  .actions { position: sticky; bottom: 0; background: rgba(244,246,249,.92); backdrop-filter: blur(6px);
    padding: 14px 0; display: flex; align-items: center; gap: 12px; margin-top: 18px; }
  #status { font-size: 13px; }
  #status.ok { color: #067647; }
  #status.err { color: #b42318; }
  @media (prefers-color-scheme: dark) {
    body { background: #14171c; color: #e6e9ee; }
    .card { background: #1c2027; border-color: #2b313b; }
    h2 { color: #9dbcf7; }
    .sub { color: #98a2b3; }
    input, select, textarea { background: #12161b; color: #e6e9ee; border-color: #3a424e; }
    button { background: #1c2027; color: #e6e9ee; border-color: #3a424e; }
    button:hover { background: #262c35; }
    .actions { background: rgba(20,23,28,.92); }
  }
</style>
</head>
<body>
<header>
  <h1>Auto Zhu 设置</h1>
  <div class="sub">配置文件：<code>__CONFIG_PATH__</code> · 保存后需要重启 Auto Zhu 才会生效</div>
</header>
<main>
  <section class="card">
    <h2>连接</h2>
    <div class="grid">
      <label>模式
        <select id="mode">
          <option value="forward">正向 WebSocket（本程序连协议端）</option>
          <option value="reverse">反向 WebSocket（协议端连本程序）</option>
        </select>
      </label>
      <label>正向地址<input id="forward_url" placeholder="ws://127.0.0.1:3001"></label>
      <label>反向监听地址<input id="reverse_host" placeholder="127.0.0.1"></label>
      <label>反向端口<input id="reverse_port" type="number" min="1" max="65535"></label>
      <label>Token<input id="token" type="text" placeholder="与协议端一致，可留空"></label>
      <label>LLBot data 目录（可选）<input id="llbot_data_dir" placeholder="...\bin\llbot\data"></label>
    </div>
    <p class="hint">正向模式填 LLBot 的 OneBot 11 正向 WebSocket 地址（默认 ws://127.0.0.1:3001）；token 要和 LLBot 里填的一致。</p>
  </section>

  <section class="card">
    <h2>贴表情的群</h2>
    <p class="hint">群号填数字；填 * 表示所有群。表情 ID 可填多个，用逗号分隔（12951 = ㊗️）。</p>
    <div id="groups"></div>
    <button id="addGroup" type="button" class="ghost">+ 添加一个群</button>
  </section>

  <section class="card">
    <h2>过滤</h2>
    <label>关键词（可选，逗号分隔；留空表示不限制）<input id="keywords" placeholder="早安, 晚安"></label>
    <label style="margin-top:12px">屏蔽名单（QQ 号，逗号/空格/换行分隔）<textarea id="blocked" rows="3" placeholder="10001, 10002"></textarea></label>
    <label class="check" style="margin-top:12px"><input type="checkbox" id="ignore_self"> 不响应机器人自己发的消息</label>
  </section>

  <section class="card">
    <h2>频率与重试</h2>
    <div class="grid">
      <label>每秒最多贴几个<input id="rate" type="number" step="0.1" min="0.1"></label>
      <label>突发上限<input id="burst" type="number" min="1"></label>
      <label>调用超时（秒）<input id="timeout" type="number" min="1"></label>
      <label>失败重试次数<input id="retries" type="number" min="0"></label>
      <label>去重窗口（秒）<input id="dedupe" type="number" min="0"></label>
    </div>
  </section>

  <section class="card">
    <h2>日志</h2>
    <div class="grid">
      <label>级别
        <select id="level">
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
      </label>
      <label>日志文件（可选）<input id="logfile" placeholder="logs/auto-zhu.log"></label>
    </div>
  </section>

  <div class="actions">
    <button id="save" class="primary" type="button">保存</button>
    <button id="reload" class="ghost" type="button">重新加载</button>
    <span id="status"></span>
  </div>
</main>
<script>
(function () {
  var TOKEN = "__TOKEN__";
  var loaded = null;

  function $(id) { return document.getElementById(id); }
  function toInt(value, fallback) { var n = parseInt(value, 10); return isNaN(n) ? fallback : n; }
  function toNum(value, fallback) { var n = parseFloat(value); return isNaN(n) ? fallback : n; }

  function setStatus(text, kind) {
    var el = $("status");
    el.textContent = text || "";
    el.className = kind || "";
  }

  function splitList(text) {
    var t = text || "";
    var out = [];
    var buf = "";
    for (var i = 0; i < t.length; i++) {
      var ch = t.charAt(i);
      if (ch === "," || ch === "\uFF0C" || ch <= " ") {
        if (buf) { out.push(buf); buf = ""; }
      } else {
        buf += ch;
      }
    }
    if (buf) { out.push(buf); }
    return out;
  }

  function api(method, url, body) {
    var opts = { method: method, headers: { "X-AutoZhu-Token": TOKEN } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (resp) {
      return resp.json().then(function (data) { return { status: resp.status, data: data }; });
    });
  }

  function addGroupRow(key, emojis) {
    var row = document.createElement("div");
    row.className = "group-row";

    var gid = document.createElement("input");
    gid.type = "text";
    gid.className = "gid";
    gid.placeholder = "群号或 *";
    gid.value = key || "";

    var emo = document.createElement("input");
    emo.type = "text";
    emo.className = "emoji";
    emo.placeholder = "12951, 178";
    emo.value = (emojis || []).join(", ");

    var del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "删除";
    del.onclick = function () { row.parentNode.removeChild(row); };

    row.appendChild(gid);
    row.appendChild(emo);
    row.appendChild(del);
    $("groups").appendChild(row);
  }

  function renderGroups(groups) {
    $("groups").innerHTML = "";
    var keys = Object.keys(groups || {});
    if (keys.length === 0) { addGroupRow("", []); return; }
    keys.forEach(function (key) { addGroupRow(key, groups[key]); });
  }

  function collectGroups() {
    var result = {};
    var rows = $("groups").querySelectorAll(".group-row");
    for (var i = 0; i < rows.length; i++) {
      var gid = rows[i].querySelector(".gid").value.trim();
      if (!gid) { continue; }
      var emojis = splitList(rows[i].querySelector(".emoji").value);
      if (emojis.length === 0) {
        throw new Error("群 " + gid + " 还没填表情 ID");
      }
      result[gid] = emojis;
    }
    if (Object.keys(result).length === 0) {
      throw new Error("至少要配置一个群");
    }
    return result;
  }

  function fill(cfg) {
    loaded = cfg;
    $("mode").value = cfg.connection.mode;
    $("forward_url").value = cfg.connection.forward_url || "";
    $("reverse_host").value = cfg.connection.reverse_host || "";
    $("reverse_port").value = cfg.connection.reverse_port;
    $("token").value = cfg.connection.token || "";
    $("llbot_data_dir").value = cfg.connection.llbot_data_dir || "";
    $("keywords").value = (cfg.reaction.keywords || []).join(", ");
    $("blocked").value = (cfg.reaction.blocked_users || []).join(", ");
    $("ignore_self").checked = !!cfg.reaction.ignore_self;
    $("rate").value = cfg.reaction.rate_limit_per_second;
    $("burst").value = cfg.reaction.rate_limit_burst;
    $("timeout").value = cfg.reaction.timeout;
    $("retries").value = cfg.reaction.retries;
    $("dedupe").value = cfg.reaction.dedupe_seconds;
    $("level").value = cfg.logging.level;
    $("logfile").value = cfg.logging.file || "";
    renderGroups(cfg.reaction.groups);
  }

  function load() {
    setStatus("加载中…", "");
    return api("GET", "/api/config").then(function (res) {
      if (!res.data.ok) { throw new Error(res.data.error || "读取失败"); }
      fill(res.data.config);
      setStatus("已加载", "ok");
    }).catch(function (err) {
      setStatus("读取配置失败：" + err.message, "err");
    });
  }

  function save() {
    var groups;
    try {
      groups = collectGroups();
    } catch (err) {
      setStatus(err.message, "err");
      return;
    }
    var payload = {
      connection: {
        mode: $("mode").value,
        forward_url: $("forward_url").value.trim(),
        reverse_host: $("reverse_host").value.trim(),
        reverse_port: toInt($("reverse_port").value, 8080),
        token: $("token").value,
        proxy: loaded && loaded.connection ? loaded.connection.proxy : null,
        llbot_data_dir: $("llbot_data_dir").value.trim()
      },
      reaction: {
        groups: groups,
        keywords: splitList($("keywords").value),
        blocked_users: splitList($("blocked").value),
        ignore_self: $("ignore_self").checked,
        dedupe_seconds: toNum($("dedupe").value, 300),
        rate_limit_per_second: toNum($("rate").value, 1),
        rate_limit_burst: toNum($("burst").value, 2),
        timeout: toNum($("timeout").value, 10),
        retries: toInt($("retries").value, 2)
      },
      logging: {
        level: $("level").value,
        file: $("logfile").value.trim()
      }
    };
    setStatus("保存中…", "");
    return api("POST", "/api/config", payload).then(function (res) {
      if (!res.data.ok) { throw new Error(res.data.error || "保存失败"); }
      setStatus("已保存到 config.json，重启 Auto Zhu 后生效", "ok");
    }).catch(function (err) {
      setStatus("保存失败：" + err.message, "err");
    });
  }

  $("addGroup").onclick = function () { addGroupRow("", []); };
  $("reload").onclick = function () { load(); };
  $("save").onclick = function () { save(); };
  load();
})();
</script>
</body>
</html>
'''


class SettingsServer:
    """在 127.0.0.1 上提供设置页面的小型 HTTP 服务。"""

    def __init__(self, config_path: str | Path, host: str = "127.0.0.1", port: int = 8090) -> None:
        self.config_path = Path(config_path)
        self.host = host
        self.port = int(port)
        self.token = secrets.token_urlsafe(18)
        self.httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        if self.httpd is None:
            raise RuntimeError("服务还没启动")
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> str:
        handler = _make_handler(self)
        last_error: Exception | None = None
        for candidate in range(self.port, self.port + 10):
            try:
                self.httpd = ThreadingHTTPServer((self.host, candidate), handler)
                break
            except OSError as exc:  # 端口被占用就往后试
                last_error = exc
        if self.httpd is None:
            raise OSError(f"端口 {self.port}~{self.port + 9} 都被占用了") from last_error

        self.httpd.daemon_threads = True
        thread = threading.Thread(target=self.httpd.serve_forever, name="settings-ui", daemon=True)
        thread.start()
        return self.url

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None


def _make_handler(server: SettingsServer):
    expected_token = server.token

    class SettingsHandler(BaseHTTPRequestHandler):
        server_version = "AutoZhuSettings/1.0"

        def log_message(self, fmt: str, *args) -> None:  # 页面访问日志降级为 debug
            logger.debug("settings-ui: " + fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-AutoZhu-Token", "")
            return bool(supplied) and hmac.compare_digest(supplied, expected_token)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
            if self.path in ("/", "/index.html"):
                page = PAGE.replace("__TOKEN__", server.token).replace(
                    "__CONFIG_PATH__", html.escape(str(server.config_path))
                )
                self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/favicon.ico":
                self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                return
            if self.path.split("?")[0] == "/api/config":
                if not self._authorized():
                    self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    return
                try:
                    config = Config.load(server.config_path)
                except ConfigError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                    return
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "config": config.to_dict(), "path": str(server.config_path)},
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/api/config":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "空的请求体"})
                return
            if length > MAX_BODY:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "请求体太大"})
                return

            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"JSON 解析失败：{exc}"})
                return

            try:
                config = Config.from_dict(payload, source=server.config_path)
            except ConfigError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            try:
                config.save()
            except OSError as exc:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": f"写入 {server.config_path} 失败：{exc}"},
                )
                return

            logger.info("设置页面已保存配置：%s", server.config_path)
            self._json(HTTPStatus.OK, {"ok": True, "message": "已保存"})

    return SettingsHandler
