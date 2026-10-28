"""
德州扑克联机服务端。

只使用 Python 标准库，不需要安装任何依赖。

启动：

    python3 web/server.py

然后同一 WiFi 下的病友用手机浏览器打开：

    http://<你的电脑IP>:8000

状态推送使用 SSE（EventSource），
浏览器断线会自动重连。
"""

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs
from urllib.parse import urlparse


# 让 web/ 下的模块可以 import 上一层的引擎
BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

WEB_DIR = os.path.join(
    BASE_DIR,
    "web"
)

STATIC_DIR = os.path.join(
    WEB_DIR,
    "static"
)

from ledger import Ledger  # noqa: E402
from room import RoomManager  # noqa: E402


# 端口优先级：
#   命令行参数 > POKER_PORT > PORT（云平台普遍用 PORT） > 8000
DEFAULT_PORT = int(
    os.environ.get("POKER_PORT")
    or os.environ.get("PORT")
    or "8000"
)

DEFAULT_START_CHIPS = int(
    os.environ.get(
        "POKER_START_CHIPS",
        "1000"
    )
)

DEFAULT_SMALL_BLIND = int(
    os.environ.get(
        "POKER_SMALL_BLIND",
        "10"
    )
)


# 长期筹码账本（按名字记账，保存在 data/ledger.json）
LEDGER = Ledger()

MANAGER = RoomManager(ledger=LEDGER)


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    # PNG 之前漏了，会退化成 application/octet-stream。
    # PWA 安装时会校验图标类型，必须给对
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    # PWA 清单
    ".webmanifest": "application/manifest+json; charset=utf-8",
    # 背景音乐：给出正确的 MIME，
    # 否则 iOS Safari 可能拒绝播放
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}


def local_ips():

    ips = set()

    try:

        hostname = socket.gethostname()

        for info in socket.getaddrinfo(
            hostname,
            None
        ):

            ip = info[4][0]

            if ":" in ip:
                continue

            if ip.startswith("127."):
                continue

            ips.add(ip)

    except Exception:

        pass

    # WSL 里 hostname -I 更可靠
    try:

        import subprocess

        output = subprocess.check_output(
            ["hostname", "-I"],
            text=True
        )

        for ip in output.split():

            if "." in ip:
                ips.add(ip)

    except Exception:

        pass

    return sorted(ips)


# 首页引用的 JS / CSS 带上版本号，改一次文件换一个号。
#
# 为什么需要：service worker 在网络慢时会用缓存里的旧首页顶上，
# 而 app.js 的超时更长、往往能拿到新的，于是出现
# 「新 JS 配旧 HTML」的半新半旧页面 —— 新 JS 去操作旧页面里
# 不存在的元素就会报错。带上版本号以后，旧首页引用的
# 一定是旧版资源，不会混搭。
_asset_version = None


def asset_version():

    global _asset_version

    if _asset_version is not None:
        return _asset_version

    stamp = 0

    for name in ("index.html", "app.js", "style.css"):

        try:

            stamp = max(
                stamp,
                int(
                    os.path.getmtime(
                        os.path.join(STATIC_DIR, name)
                    )
                ),
            )

        except OSError:

            continue

    _asset_version = str(stamp)

    return _asset_version


def versioned_html(raw):
    """给首页里的 app.js / style.css 加上版本号。"""

    version = asset_version()

    text = raw.decode("utf-8")

    for name in ("app.js", "style.css"):

        text = text.replace(
            '/static/%s"' % name,
            '/static/%s?v=%s"' % (name, version),
        )

    return text.encode("utf-8")


class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    # ==========================
    # 基础工具
    # ==========================

    def log_message(self, format, *args):
        # 安静一点，只输出关键请求
        message = format % args

        if "/api/" in message:
            sys.stderr.write(
                "[http] %s - %s\n"
                % (
                    self.address_string(),
                    message,
                )
            )

    def send_json(
        self,
        data,
        status=200
    ):

        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.send_header(
            "Cache-Control",
            "no-store"
        )
        self.end_headers()

        self.wfile.write(body)

    def send_file(self, path, content_type=None):
        """发送静态文件。content_type 传了就覆盖自动推断的结果。"""

        if not os.path.isfile(path):

            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        if content_type is None:

            extension = os.path.splitext(path)[1]

            content_type = CONTENT_TYPES.get(
                extension,
                "application/octet-stream"
            )

        with open(path, "rb") as file:

            body = file.read()

        self.send_response(200)
        self.send_header(
            "Content-Type",
            content_type
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        # 用 no-cache 而不是 no-store：
        # 对用户来说都是「每次都拿最新的」，但 no-store 会让
        # 浏览器和 service worker 都存不下，PWA 的离线兜底就废了
        self.send_header(
            "Cache-Control",
            "no-cache"
        )
        self.end_headers()

        self.wfile.write(body)

    def send_index(self):
        """发首页，并把里面的 JS / CSS 换成带版本号的地址。"""

        path = os.path.join(
            STATIC_DIR,
            "index.html"
        )

        try:

            with open(path, "rb") as file:

                body = versioned_html(file.read())

        except OSError:

            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.send_header(
            "Cache-Control",
            "no-cache"
        )
        self.end_headers()

        self.wfile.write(body)

    def read_json_body(self):

        length = int(
            self.headers.get(
                "Content-Length",
                0
            )
        )

        if length <= 0:
            return {}

        raw = self.rfile.read(length)

        try:

            return json.loads(
                raw.decode("utf-8")
            )

        except Exception:

            return {}

    # ==========================
    # 路由
    # ==========================

    def do_GET(self):

        # 兜底：任何没预料到的异常都要回一个响应，
        # 不能让连接直接断掉 —— 前端拿不到响应就只能
        # 显示「网络失败」，看不出到底出了什么事。
        try:

            self.handle_get()

        except Exception as error:

            self.fail_request(error)

    def handle_get(self):

        parsed = urlparse(self.path)

        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):

            # 首页在这里发送：顺便给里面的 JS / CSS 打上版本号
            self.send_index()

            return

        # service worker 必须挂在根路径：作用域是按它自己的
        # 路径算的，放在 /static/ 下就只能管 /static/
        if path == "/sw.js":

            self.send_file(
                os.path.join(STATIC_DIR, "sw.js"),
                content_type=(
                    "application/javascript; charset=utf-8"
                ),
            )
            return

        if path == "/manifest.webmanifest":

            self.send_file(
                os.path.join(
                    STATIC_DIR,
                    "manifest.webmanifest"
                ),
                content_type=(
                    "application/manifest+json;"
                    " charset=utf-8"
                ),
            )
            return

        if path == "/favicon.ico":

            self.send_file(
                os.path.join(STATIC_DIR, "icon-192.png"),
                content_type="image/png",
            )
            return

        if path == "/api/rooms":

            self.send_json(
                {
                    "ok": True,
                    "rooms": (
                        MANAGER.list_rooms()
                    ),
                }
            )
            return

        if path == "/healthz":

            self.send_json(
                {"ok": True, "status": "healthy"}
            )
            return

        if path == "/api/ledger":

            self.send_json(
                {
                    "ok": True,
                    "players": LEDGER.stats(),
                    "buyins": (
                        LEDGER.recent_buyins()
                    ),
                }
            )
            return

        if path == "/api/state":

            token = query.get(
                "token",
                [""]
            )[0]

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            # 拉一次状态也算一次心跳：
            # 手机连不上 SSE 时会退化成轮询，
            # 不能因此把它判成“离线”而自动代打。
            room.touch(seat.token)

            self.send_json(
                {
                    "ok": True,
                    "state": room.state_for(
                        seat.token
                    ),
                }
            )
            return

        if path == "/api/events":

            token = query.get(
                "token",
                [""]
            )[0]

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            self.stream_events(
                room,
                seat
            )
            return

        # 静态文件
        if path.startswith("/static/"):

            # 保留子目录（voice/、anim/ 都在子目录里），
            # 同时挡掉 ../ 之类的越权路径
            name = path[len("/static/"):]

            parts = [
                part
                for part in name.split("/")
                if part not in ("", ".", "..")
            ]

            self.send_file(
                os.path.join(
                    STATIC_DIR,
                    *parts
                )
            )
            return

        self.send_json(
            {"ok": False, "error": "not found"},
            status=404,
        )

    def do_POST(self):

        try:

            self.handle_post()

        except Exception as error:

            self.fail_request(error)

    def fail_request(self, error):
        """兜底错误响应：让前端总能拿到一条能显示的消息。"""

        try:
            sys.stderr.write(
                "[error] %s\n" % (error,)
            )
        except Exception:
            pass

        try:

            self.send_json(
                {
                    "ok": False,
                    "error": "服务器出错了，请稍后重试",
                },
                status=500,
            )

        except Exception:
            # 响应头可能已经发出去了（比如 SSE 流中途中止），
            # 这种情况没法再补一个 JSON，只能放弃
            pass

    def handle_post(self):

        parsed = urlparse(self.path)

        path = parsed.path

        body = self.read_json_body()

        if path == "/api/join":

            self.handle_join(body)
            return

        if path == "/api/present":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            room.touch(seat.token)

            ok, message, event = room.send_present(
                seat.token,
                body.get("seat"),
                body.get("item", ""),
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                    "key": (
                        event["key"]
                        if event is not None
                        else None
                    ),
                }
            )
            return

        if path == "/api/auto":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            # 能操作说明人肯定在，顺手记一次心跳
            room.touch(seat.token)

            ok, message = room.set_auto(
                seat.token,
                bool(body.get("on")),
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                }
            )
            return

        if path == "/api/emoji":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            room.touch(seat.token)

            ok, message, event = room.send_emoji(
                seat.token,
                body.get("index"),
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                    "key": (
                        event["key"]
                        if event is not None
                        else None
                    ),
                }
            )
            return

        if path == "/api/quick":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            room.touch(seat.token)

            ok, message, event = room.send_quick(
                seat.token,
                body.get("phrase", ""),
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                    "key": (
                        event["key"]
                        if event is not None
                        else None
                    ),
                }
            )
            return

        if path == "/api/chat":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            # 能发消息说明人肯定在，顺手记一次心跳
            room.touch(seat.token)

            ok, message = room.add_chat(
                seat.token,
                body.get("text", ""),
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                }
            )
            return

        if path == "/api/action":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            # 能提交动作说明人肯定在，顺手记一次心跳
            room.touch(seat.token)

            ok, message = room.submit_action(
                seat.token,
                body.get("action"),
                body.get("amount"),
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                }
            )
            return

        if path == "/api/next":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            ok, message = room.start_hand(
                seat.token
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                }
            )
            return

        if path == "/api/ready":

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            ready = bool(body.get("ready", True))

            ok, message = room.set_ready(
                seat.token,
                ready,
            )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                }
            )
            return

        if path in (
            "/api/rebuy",
            "/api/return",
            "/api/leave",
            "/api/reset",
        ):

            token = body.get("token", "")

            result = self.find_room(token)

            if result is None:

                self.send_json(
                    {
                        "ok": False,
                        "error": "座位不存在",
                    },
                    status=404,
                )
                return

            room, seat = result

            if path == "/api/rebuy":

                ok, message = room.rebuy(
                    seat.token,
                    body.get("amount"),
                    bool(body.get("loan")),
                )

            elif path == "/api/return":

                ok, message = room.return_chips(
                    seat.token,
                    body.get("amount"),
                )

            elif path == "/api/leave":

                ok, message = room.leave(
                    seat.token
                )

            else:

                ok, message = room.reset_game(
                    seat.token
                )

            self.send_json(
                {
                    "ok": ok,
                    "error": (
                        None if ok else message
                    ),
                }
            )
            return

        self.send_json(
            {"ok": False, "error": "not found"},
            status=404,
        )

    # ==========================
    # 具体实现
    # ==========================

    def find_room(self, token):

        if not token:
            return None

        for room in MANAGER.rooms.values():

            seat = room.seat_by_token(token)

            if seat is not None:
                return room, seat

        return None

    def handle_join(self, body):

        name = (
            body.get("name", "")
            .strip()
        )

        if not name:
            name = "玩家"

        if len(name) > 12:
            name = name[:12]

        code = (
            str(body.get("room", "")).strip()
            or None
        )

        start_chips = int(
            body.get(
                "start_chips",
                DEFAULT_START_CHIPS
            )
        )

        small_blind = int(
            body.get(
                "small_blind",
                DEFAULT_SMALL_BLIND
            )
        )

        big_blind = int(
            body.get(
                "big_blind",
                small_blind * 2
            )
        )

        start_chips = max(100, min(
            start_chips,
            100000
        ))

        small_blind = max(
            1,
            min(small_blind, 1000)
        )

        big_blind = max(
            small_blind * 2,
            min(big_blind, 2000)
        )

        room = MANAGER.create_room(
            code,
            small_blind=small_blind,
            big_blind=big_blind,
            start_chips=start_chips,
        )

        # 同名座位一律复用（接管）。
        #
        # 以前只在「旧座位已被判定离线」时才复用，于是旧座位
        # 还连着的时候（换了设备、页面没关、刚断线但心跳还没到点）
        # 就会再新建一个座位 —— 反复进出，牌桌上就同时出现
        # 好几个同名的人。
        for seat in room.seats:

            if seat.name != name:
                continue

            # 上次点了离开但还没轮到移除：改主意了，留下
            seat.left = False
            seat.connected = True
            seat.last_seen = time.time()
            seat.offline_since = None

            room.broadcast()

            self.send_json(
                {
                    "ok": True,
                    "token": seat.token,
                    "room": room.code,
                    "seat": room.seats.index(seat),
                    "rejoined": True,
                }
            )
            return

        seat = room.add_seat(name)

        room.broadcast()

        self.send_json(
            {
                "ok": True,
                "token": seat.token,
                "room": room.code,
                "seat": room.seats.index(seat),
                "rejoined": False,
            }
        )

    def stream_events(self, room, seat):

        listener_queue = room.subscribe(
            seat.token
        )

        # 用“流计数”而不是简单置位：
        # 浏览器自动重连时会短暂存在两条流，
        # 旧流结束时不能把新流误标成离线。
        room.add_stream(
            seat.token
        )

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/event-stream; charset=utf-8"
        )
        self.send_header(
            "Cache-Control",
            "no-cache"
        )
        self.send_header(
            "Connection",
            "keep-alive"
        )
        self.send_header(
            "X-Accel-Buffering",
            "no"
        )
        # 显式声明 chunked 分帧。
        # 否则响应没有长度信息，
        # 经过 cloudflared 这类代理时
        # 会被一直缓冲到连接结束，
        # 手机端就收不到任何实时推送。
        self.send_header(
            "Transfer-Encoding",
            "chunked"
        )
        self.end_headers()

        try:

            # 先立刻推送一次当前状态
            self.write_event(
                room.state_for(seat.token)
            )

            while True:

                # 流还活着，持续刷新心跳时间
                room.touch(seat.token)

                try:

                    state = (
                        listener_queue.get(
                            timeout=15
                        )
                    )

                except Exception:

                    # 心跳，保持连接
                    self.write_chunk(
                        b": ping\n\n"
                    )
                    continue

                self.write_event(state)

        except Exception:

            pass

        finally:

            room.unsubscribe(
                listener_queue
            )

            # 只有所有流都结束了才算离线
            room.remove_stream(
                seat.token
            )

    def write_chunk(self, data):

        self.wfile.write(
            f"{len(data):X}\r\n".encode(
                "ascii"
            )
        )

        self.wfile.write(data)

        self.wfile.write(b"\r\n")

        self.wfile.flush()

    def write_event(self, state):

        payload = json.dumps(
            state,
            ensure_ascii=False
        )

        data = (
            "data: "
            + payload
            + "\n\n"
        )

        self.write_chunk(
            data.encode("utf-8")
        )


def cleanup_worker():

    while True:

        time.sleep(60)

        try:

            MANAGER.cleanup()

        except Exception:

            pass


def main():

    port = DEFAULT_PORT

    if len(sys.argv) > 1:

        port = int(sys.argv[1])

    try:

        server = ThreadingHTTPServer(
            ("0.0.0.0", port),
            Handler
        )

    except OSError as error:

        print()
        print(
            f"端口 {port} 启动失败：{error}"
        )
        print()
        print(
            "  多半是服务已经在运行了，"
            "可以直接打开："
        )
        print(
            f"  http://127.0.0.1:{port}"
        )
        print()
        print(
            "  想换一个端口："
        )
        print(
            f"  python3 web/server.py {port + 1}"
        )
        print()
        print(
            "  想先停掉旧服务："
        )
        print(
            "  pkill -f 'web/server.py'"
        )
        print()

        return

    thread = threading.Thread(
        target=cleanup_worker,
        daemon=True
    )

    thread.start()

    print()
    print("=" * 42)
    print("德州扑克联机服务已启动")
    print("=" * 42)
    print()

    for ip in local_ips():

        print(
            "  同病房的病友请访问："
            f"http://{ip}:{port}"
        )

    print()
    print(
        "  本机访问："
        f"http://127.0.0.1:{port}"
    )
    print()

    print(
        "  提示：WSL2 在 NAT 网络后面，"
    )
    print(
        "  手机打不开时，需要在 Windows"
    )
    print(
        "  管理员 PowerShell 里执行一次转发："
    )
    print()

    for ip in local_ips():

        print(
            "    netsh interface portproxy"
            " add v4tov4"
            f" listenport={port}"
            " listenaddress=0.0.0.0"
            f" connectport={port}"
            f" connectaddress={ip}"
        )

    print()
    print(
        "  也可以直接运行 局域网转发.ps1"
    )
    print()
    print("  停止服务：Ctrl + C")
    print()

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print("\n服务已停止")

    finally:

        server.server_close()


if __name__ == "__main__":

    main()
