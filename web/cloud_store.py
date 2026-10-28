"""
账本的云端存储。

把长期账本（data/ledger.json）镜像到 GitHub Gist。
这样在 Render 这类“磁盘是临时的”平台上，
服务休眠 / 重新部署后账本也不会丢。

只需要两个环境变量：

    POKER_GIST_ID       Gist 的 id（Gist 网址最后一段）
    POKER_GIST_TOKEN    有 gist 权限的 GitHub Token

可选：

    POKER_GIST_FILE     Gist 里的文件名，默认 ledger.json

没配置这两个变量时，程序自动退回“只用本地文件”，
行为和以前完全一样。

附带的两个小命令（在项目根目录执行）：

    # 自动建一个私密 Gist，并打印要填的环境变量
    POKER_GIST_TOKEN=xxx python3 web/cloud_store.py init

    # 验证读写是否正常
    POKER_GIST_ID=xxx POKER_GIST_TOKEN=xxx \
        python3 web/cloud_store.py check
"""

import json
import os
import urllib.request


API = "https://api.github.com"
USER_AGENT = "poker-ledger/1.0"
DEFAULT_FILENAME = "ledger.json"
DEFAULT_DESCRIPTION = "德州扑克 · 长期账本"


class RemoteUnavailable(Exception):
    """远端存储暂时不可用。"""


class NullStore:
    """没有配置远端时的占位实现。"""

    enabled = False
    name = "local"

    def fetch(self):
        return None

    def push(self, data):
        return False


class GitHubGistStore:
    """把账本存到一个私密 Gist 里。"""

    enabled = True
    name = "gist"

    def __init__(
        self,
        token,
        gist_id,
        filename=DEFAULT_FILENAME
    ):

        self.token = token
        self.gist_id = gist_id
        self.filename = filename

    # ==========================
    # 底层请求
    # ==========================

    def _request(
        self,
        method,
        url,
        payload=None,
        timeout=10
    ):

        body = None

        if payload is not None:

            body = json.dumps(payload).encode(
                "utf-8"
            )

        request = urllib.request.Request(
            url,
            data=body,
            method=method
        )

        request.add_header(
            "Authorization",
            "Bearer " + self.token
        )

        request.add_header(
            "Accept",
            "application/vnd.github+json"
        )

        request.add_header(
            "X-GitHub-Api-Version",
            "2022-11-28"
        )

        request.add_header(
            "User-Agent",
            USER_AGENT
        )

        if body is not None:

            request.add_header(
                "Content-Type",
                "application/json"
            )

        try:

            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

        except Exception as error:

            raise RemoteUnavailable(
                str(error)
            )

        if not raw:
            return {}

        return json.loads(raw)

    # ==========================
    # 读 / 写
    # ==========================

    def fetch(self):

        info = self._request(
            "GET",
            f"{API}/gists/{self.gist_id}"
        )

        files = info.get("files") or {}

        entry = files.get(self.filename)

        if not entry:
            return None

        content = entry.get("content")

        if content is None and entry.get("raw_url"):

            with urllib.request.urlopen(
                entry["raw_url"],
                timeout=10
            ) as response:

                content = response.read().decode(
                    "utf-8"
                )

        if not content:
            return None

        try:

            parsed = json.loads(content)

        except ValueError:

            return None

        if not isinstance(parsed, dict):
            return None

        return parsed

    def push(self, data):

        payload = {
            "files": {
                self.filename: {
                    "content": json.dumps(
                        data,
                        ensure_ascii=False,
                        indent=2
                    )
                }
            }
        }

        self._request(
            "PATCH",
            f"{API}/gists/{self.gist_id}",
            payload
        )

        return True


def build_store_from_env():

    token = os.environ.get(
        "POKER_GIST_TOKEN",
        ""
    ).strip()

    gist_id = os.environ.get(
        "POKER_GIST_ID",
        ""
    ).strip()

    filename = (
        os.environ.get(
            "POKER_GIST_FILE",
            DEFAULT_FILENAME
        ).strip()
        or DEFAULT_FILENAME
    )

    if token and gist_id:

        return GitHubGistStore(
            token,
            gist_id,
            filename
        )

    return NullStore()


# ==============================
# 命令行小工具
# ==============================

def _create_gist(token, filename):

    payload = {
        "description": DEFAULT_DESCRIPTION,
        "public": False,
        "files": {
            filename: {"content": "{}"}
        },
    }

    request = urllib.request.Request(
        API + "/gists",
        data=json.dumps(payload).encode("utf-8"),
        method="POST"
    )

    request.add_header(
        "Authorization",
        "Bearer " + token
    )

    request.add_header(
        "Accept",
        "application/vnd.github+json"
    )

    request.add_header(
        "X-GitHub-Api-Version",
        "2022-11-28"
    )

    request.add_header(
        "User-Agent",
        USER_AGENT
    )

    request.add_header(
        "Content-Type",
        "application/json"
    )

    with urllib.request.urlopen(
        request,
        timeout=15
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def main():

    import sys

    args = sys.argv[1:]

    token = os.environ.get(
        "POKER_GIST_TOKEN",
        ""
    ).strip()

    filename = (
        os.environ.get(
            "POKER_GIST_FILE",
            DEFAULT_FILENAME
        ).strip()
        or DEFAULT_FILENAME
    )

    command = args[0] if args else ""

    if command == "init":

        if not token:

            print("请先设置 POKER_GIST_TOKEN")
            return

        try:

            info = _create_gist(token, filename)

        except Exception as error:

            print("创建失败：", error)
            return

        gist_id = info.get("id") or ""

        print()
        print("已创建私密 Gist：")
        print("  Gist ID :", gist_id)
        print("  网址    :", info.get("html_url"))
        print()
        print("在 Render 的 Environment 里添加：")
        print("  POKER_GIST_ID    =", gist_id)
        print("  POKER_GIST_TOKEN =", "(你刚才用的 token)")
        print()
        return

    if command == "push-local":

        store = build_store_from_env()

        if not store.enabled:

            print(
                "缺少 POKER_GIST_ID / "
                "POKER_GIST_TOKEN"
            )
            return

        local_path = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.abspath(__file__)
                )
            ),
            "data",
            "ledger.json"
        )

        try:

            with open(
                local_path,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

        except Exception as error:

            print(
                "读不到本地账本 %s：%s"
                % (local_path, error)
            )
            return

        if not isinstance(data, dict):

            print("本地账本格式不对")
            return

        # 保证版本号至少为 1，
        # 让云端认为这是有效数据。
        try:

            data["revision"] = max(
                int(data.get("revision", 0)),
                1
            )

        except (TypeError, ValueError):

            data["revision"] = 1

        try:

            store.push(data)

        except Exception as error:

            print("上传失败：", error)
            return

        players = data.get("players", {})

        print(
            "已把本地账本上传到云端，玩家数：",
            len(players)
        )
        return

    if command == "check":

        store = build_store_from_env()

        if not store.enabled:

            print(
                "缺少 POKER_GIST_ID / "
                "POKER_GIST_TOKEN"
            )
            return

        try:

            data = store.fetch()

        except Exception as error:

            print("读取失败：", error)
            return

        players = (data or {}).get("players", {})

        print("远端读取成功，玩家数：", len(players))

        try:

            store.push(
                data
                or {"players": {}, "buyins": []}
            )

        except Exception as error:

            print("写入失败：", error)
            return

        print("远端写入成功，配置可用 ✓")
        return

    print(__doc__.strip())


if __name__ == "__main__":

    main()
