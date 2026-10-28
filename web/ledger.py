"""
长期筹码账本。

打牌的人比较固定，所以按“名字”长期记录每个人：

    - 当前筹码（跨场次保留，服务重启也在）
    - 累计买入（包含标记为贷款的买入）
    - 其中贷款
    - 净盈亏 = 当前筹码 - 累计买入
    - 打过的手数

数据保存在 data/ledger.json。
用环境变量 POKER_LEDGER_FILE 可以指定别的位置（测试用）。

可选：把账本镜像到 GitHub Gist，
这样在 Render 这类“磁盘是临时的”平台上，
服务休眠 / 重新部署后账本也不会丢。
配置方式见 web/cloud_store.py。
"""

import atexit
import json
import os
import threading
import time

from cloud_store import build_store_from_env


BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DEFAULT_PATH = (
    os.environ.get("POKER_LEDGER_FILE")
    or os.path.join(
        BASE_DIR,
        "data",
        "ledger.json"
    )
)

# 把连续写入合并成一次上传，避免频繁调用远端接口
REMOTE_DEBOUNCE = float(
    os.environ.get(
        "POKER_REMOTE_DEBOUNCE",
        "3"
    )
)

# 上传失败后的重试间隔（秒）
REMOTE_RETRY = float(
    os.environ.get(
        "POKER_REMOTE_RETRY",
        "30"
    )
)

# 启动时读取远端账本的重试次数
REMOTE_LOAD_RETRY = int(
    os.environ.get(
        "POKER_REMOTE_LOAD_RETRY",
        "3"
    )
)


def now_text():

    return time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime()
    )


class Ledger:

    def __init__(
        self,
        path=None,
        store=None
    ):

        self.path = path or DEFAULT_PATH
        self.lock = threading.RLock()

        # 远端存储：没配置时是 NullStore，
        # 行为与以前完全一样。
        self.store = (
            store
            if store is not None
            else build_store_from_env()
        )

        # 只有成功读到过远端账本才允许上传，
        # 避免“启动时读取失败 → 用空账本覆盖云端”。
        self._remote_ready = not self.store.enabled
        self._pushed_revision = 0
        self._pending = None
        self._dirty = threading.Event()
        self._remote_error = None

        self.data = {
            "players": {},
            "buyins": [],
            "revision": 0,
        }

        self.load()

        if self.store.enabled:

            worker = threading.Thread(
                target=self._push_loop,
                daemon=True
            )

            worker.start()

            atexit.register(self.flush_remote)

    # ==========================
    # 持久化
    # ==========================

    @staticmethod
    def _revision(data):

        try:

            return int(data.get("revision", 0))

        except (AttributeError, TypeError, ValueError):

            return 0

    def _read_local(self):

        try:

            with open(
                self.path,
                "r",
                encoding="utf-8"
            ) as file:

                loaded = json.load(file)

        except Exception:

            return None

        if isinstance(loaded, dict):
            return loaded

        return None

    def _read_remote(self):

        "读到（哪怕是空的）就算成功，返回 None 表示失败。"

        for attempt in range(
            max(1, REMOTE_LOAD_RETRY)
        ):

            try:

                data = self.store.fetch()

            except Exception as error:

                self._remote_error = str(error)

                if attempt + 1 < REMOTE_LOAD_RETRY:
                    time.sleep(1)

                continue

            self._remote_error = None

            return data if isinstance(
                data, dict
            ) else {}

        return None

    def load(self):

        local = self._read_local()

        remote = None

        if self.store.enabled:

            remote = self._read_remote()

            self._remote_ready = remote is not None

        # 两边都有时用版本号更大的那份，
        # 避免把新数据覆盖成旧数据。
        use_remote = False

        if remote is not None:

            if local is None:

                use_remote = True

            elif (
                self._revision(remote)
                > self._revision(local)
            ):

                use_remote = True

        data = remote if use_remote else local

        with self.lock:

            self.data = (
                data if isinstance(data, dict)
                else {}
            )

            self.data.setdefault("players", {})
            self.data.setdefault("buyins", [])
            self.data.setdefault("revision", 0)

        self._pushed_revision = self._revision(
            self.data
        )

        self._pending = json.loads(
            json.dumps(self.data)
        )

        # 远端来的数据顺手在本地留一份缓存
        if use_remote:

            self._write_local(self._pending)

    def _write_local(self, data):

        try:

            directory = os.path.dirname(
                self.path
            )

            if directory:

                os.makedirs(
                    directory,
                    exist_ok=True
                )

            temp_path = self.path + ".tmp"

            with open(
                temp_path,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

            os.replace(temp_path, self.path)

        except Exception:

            pass

    def save(self):

        with self.lock:

            self.data["revision"] = (
                self._revision(self.data) + 1
            )

            self.data["saved_at"] = time.time()

            snapshot = json.loads(
                json.dumps(self.data)
            )

            self._pending = snapshot

        self._write_local(snapshot)

        if self.store.enabled:

            self._dirty.set()

    # ==========================
    # 远端同步
    # ==========================

    def _push_loop(self):

        while True:

            self._dirty.wait()

            # 把这一波连续写入合并成一次上传
            time.sleep(REMOTE_DEBOUNCE)

            self._dirty.clear()

            if self._push_now():
                continue

            # 失败：退避后重试
            self._dirty.set()

            time.sleep(REMOTE_RETRY)

    def _push_now(self):

        if not self.store.enabled:
            return True

        # 没成功读到过远端就不要写，
        # 否则可能用空账本覆盖云端。
        if not self._remote_ready:
            return False

        with self.lock:

            snapshot = self._pending

            revision = self._revision(
                self.data
            )

        if snapshot is None:
            return True

        if revision <= self._pushed_revision:
            return True

        try:

            self.store.push(snapshot)

        except Exception as error:

            self._remote_error = str(error)

            return False

        self._pushed_revision = revision
        self._remote_error = None

        return True

    def flush_remote(self):

        try:

            self._push_now()

        except Exception:

            pass

    # ==========================
    # 玩家记录
    # ==========================

    def _entry(self, name):

        players = self.data["players"]

        entry = players.get(name)

        if entry is None:

            entry = {
                "name": name,
                "chips": 0,
                "buy_in_total": 0,
                "loan_total": 0,
                "hands": 0,
                "updated": None,
            }

            players[name] = entry

        return entry

    def get_entry(self, name):

        with self.lock:

            entry = self._entry(name)

            return dict(entry)

    def get_chips(self, name):

        with self.lock:

            return self._entry(name)["chips"]

    def ensure_start(
        self,
        name,
        start_chips
    ):
        """
        玩家进场。

        如果账本里已经有筹码，直接沿用；
        如果没有筹码（第一次来，或者上次输光了），
        自动以起始筹码买入一次，保证可以马上开打。

        返回这一手开始时的筹码。
        """

        with self.lock:

            entry = self._entry(name)

            if entry["chips"] <= 0:

                entry["chips"] = start_chips
                entry["buy_in_total"] += (
                    start_chips
                )
                entry["updated"] = now_text()

                self.data["buyins"].append(
                    {
                        "name": name,
                        "amount": start_chips,
                        "loan": False,
                        "time": now_text(),
                        "auto": True,
                    }
                )

                self.save()

            return entry["chips"]

    def record_buyin(
        self,
        name,
        amount,
        loan=False
    ):
        """
        记录一次买入。

        买入的筹码直接进入该玩家的筹码，
        金额计入累计买入；
        如果是贷款，同时计入贷款总额。

        净盈亏 = 当前筹码 - 累计买入，
        因此贷款的金额会体现在净盈亏里。
        """

        with self.lock:

            amount = int(amount)

            if amount <= 0:
                return False

            entry = self._entry(name)

            entry["chips"] += amount
            entry["buy_in_total"] += amount

            if loan:
                entry["loan_total"] += amount

            entry["updated"] = now_text()

            self.data["buyins"].append(
                {
                    "name": name,
                    "amount": amount,
                    "loan": bool(loan),
                    "time": now_text(),
                    "auto": False,
                }
            )

            self.save()

            return True

    def sync_chips(self, name, chips):

        with self.lock:

            entry = self._entry(name)

            entry["chips"] = int(chips)
            entry["updated"] = now_text()

            self.save()

    def record_return(self, name, amount):
        """
        归还筹码：把多出来的筹码收回庄家。

        筹码和累计买入**等额**一起减，
        所以净盈亏（筹码 − 累计买入）保持不变 ——
        这是还回去，不算这个人输掉。
        """

        amount = int(amount)

        if amount <= 0:
            return False

        with self.lock:

            entry = self._entry(name)

            entry["chips"] = max(
                0, entry["chips"] - amount
            )

            entry["buy_in_total"] = max(
                0, entry["buy_in_total"] - amount
            )

            entry["updated"] = now_text()

            # 也记进买入流水（负数），
            # 账本的「最近买入」里就能看到归还记录
            self.data.setdefault(
                "buyins", []
            ).append(
                {
                    "name": name,
                    "amount": -amount,
                    "loan": False,
                    "time": now_text(),
                    "auto": False,
                }
            )

            self.data.setdefault(
                "returns", []
            ).append(
                {
                    "name": name,
                    "amount": amount,
                    "time": now_text(),
                }
            )

            self.save()

        return True

    def add_hand(self, name):

        with self.lock:

            entry = self._entry(name)

            entry["hands"] += 1

    def save_silent(self):

        self.save()

    # ==========================
    # 统计
    # ==========================

    def stats(self):

        with self.lock:

            result = []

            for name, entry in self.data[
                "players"
            ].items():

                net = (
                    entry["chips"]
                    - entry["buy_in_total"]
                )

                result.append(
                    {
                        "name": name,
                        "chips": entry["chips"],
                        "buy_in_total": (
                            entry["buy_in_total"]
                        ),
                        "loan_total": (
                            entry["loan_total"]
                        ),
                        "net": net,
                        "hands": entry["hands"],
                        "updated": entry[
                            "updated"
                        ],
                    }
                )

            result.sort(
                key=lambda item: item["net"],
                reverse=True
            )

            return result

    def recent_buyins(self, limit=30):

        with self.lock:

            return list(
                self.data["buyins"]
            )[-limit:]

    def clear(self):

        with self.lock:

            # 保留版本号继续递增，
            # 保证清空后的数据比云端更新，
            # 不会被旧数据覆盖回来。
            revision = self._revision(
                self.data
            )

            self.data = {
                "players": {},
                "buyins": [],
                "revision": revision,
            }

            self.save()
