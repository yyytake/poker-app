"""
联机房间的核心逻辑。

这一层只负责：

1. 把浏览器里的玩家映射到引擎里的 Player。
2. 在后台线程里驱动一整手牌。
3. 生成每个玩家各自可见的牌桌状态（隐藏别人的底牌）。
4. 通过 SSE 把状态推送给所有浏览器。

引擎本身的规则完全由 game.py 决定，
这里不做任何规则判断，只做合法性查询。
"""

import os
import queue
import random
import threading
import time
import uuid

from ai import AutoPlayer
from ai import describe as describe_action
from game import Game
from player import Player


# 托管 AI。只用来算，内部除了随机数发生器没有别的状态，
# 多线程共用没问题。
AUTO_PLAYER = AutoPlayer()


# 玩家思考时间上限（秒）。
# 超时后自动过牌，不能过牌就自动弃牌。
ACTION_TIMEOUT = float(
    os.environ.get(
        "POKER_ACTION_TIMEOUT",
        "100"
    )
)

# 一手结束后，系统自动发下一手前的最短等待时间（秒）。
# 让所有人有时间看清这一手的结果。
MIN_NEXT_DELAY = float(
    os.environ.get(
        "POKER_MIN_NEXT_DELAY",
        "5"
    )
)

# 一手结束后，即使有人迟迟不点“准备”，
# 超过这个时间系统也会自动发下一手，
# 避免牌桌卡住。
AUTO_NEXT_MAX_WAIT = float(
    os.environ.get(
        "POKER_AUTO_NEXT_MAX_WAIT",
        "45"
    )
)

# 房间没有任何人连接后保留的时间（秒）
EMPTY_ROOM_TTL = 30 * 60

# 多久没收到任何心跳（SSE 或轮询）就认为人已经不在。
# 手机用轮询兜底时不会建立 SSE，
# 必须靠心跳超时来判断在线，否则会被误判为“离线”而自动代打。
CONNECT_TTL = float(
    os.environ.get(
        "POKER_CONNECT_TTL",
        "12"
    )
)

# 离线多久之后自动移出房间（秒）。
# 手机上最常见的是「人早走了、座位还占着」，
# 别人既等不到他，也开不了下一手。
OFFLINE_KICK = float(
    os.environ.get(
        "POKER_OFFLINE_KICK",
        "120"
    )
)

# 牌桌聊天：单条消息最长多少字、每桌最多保留多少条
CHAT_MAX_LEN = int(
    os.environ.get("POKER_CHAT_MAX_LEN", "80")
)

CHAT_KEEP = int(
    os.environ.get("POKER_CHAT_KEEP", "60")
)

# 快捷语音：点一下就发出去的一句话 + 对应音效。
# 音效文件放在 web/static/voice/ 下，按下面顺序命名即可。
QUICK_PHRASES = [
    {
        "id": "q1",
        "text": "你可以打得再烂一点吗？",
        "sound": "/static/voice/fk_m13.mp3",
    },
    {
        "id": "q2",
        "text": "我从未见过如此厚颜无耻之人",
        "sound": "/static/voice/fk_m17.mp3",
    },
    {
        "id": "q3",
        "text": "菜就多练",
        "sound": "/static/voice/3.mp3",
    },
    {
        "id": "q4",
        "text": "哥们，给力点行吗？",
        "sound": "/static/voice/fk_m14.mp3",
    },
    {
        "id": "q5",
        "text": "这波，不亏",
        "sound": "/static/voice/fk_m19.mp3",
    },
    {
        "id": "q6",
        "text": "请收下我的膝盖",
        "sound": "/static/voice/fk_m20.mp3",
    },
    {
        "id": "q7",
        "text": "你咋不上天呢？",
        "sound": "/static/voice/fk_m21.mp3",
    },
    {
        "id": "q8",
        "text": "见证奇迹的时刻到了",
        "sound": "/static/voice/fk_m23.mp3",
    },
    {
        "id": "q9",
        "text": "能不能快一点啊，兵贵神速啊",
        "sound": "/static/voice/fk_m1.mp3",
    },
]

# 每桌保留多少条快捷语音事件（客户端据此去重，只播一次）
QUICK_KEEP = int(
    os.environ.get("POKER_QUICK_KEEP", "12")
)

# 表情：直接用 /static/emoji/<序号>.png，共 EMOJI_COUNT 张
EMOJI_COUNT = int(
    os.environ.get("POKER_EMOJI_COUNT", "59")
)

# 每桌保留多少条表情事件
EMOJI_KEEP = int(
    os.environ.get("POKER_EMOJI_KEEP", "12")
)

# 互动道具：长按别人的名片可以送花 / 送酒 / 扔鸡蛋 / 扔拖鞋
PRESENT_ITEMS = ("flower", "wine", "egg", "shoe")

# 每种道具的筹码收支：
#   cost = 送的人花掉多少
#   gain = 收的人拿到多少（负数就是损失）
# 扣掉的筹码视为收回庄家，不再流转给别人。
PRESENT_CHIPS = {
    "flower": {"cost": 1, "gain": 1},
    "wine": {"cost": 5, "gain": 5},
    "egg": {"cost": 1, "gain": -1},
    "shoe": {"cost": 5, "gain": -5},
}

# 托管：单次最长多少秒，到点自动取消
AUTO_PLAY_MAX = float(
    os.environ.get("POKER_AUTO_PLAY_MAX", "100")
)

# 托管 AI 出手前停顿多久（秒）。
# 出手太快的话，牌桌上一闪就过去了，看着像没打；
# 调成 0 就是立即出手（跑测试时用）。
AI_ACTION_DELAY = float(
    os.environ.get("POKER_AI_DELAY", "0.8")
)



PRESENT_KEEP = int(
    os.environ.get("POKER_PRESENT_KEEP", "12")
)

# 引擎给的牌型名是英文，界面上统一显示中文
HAND_NAME_CN = {
    "Royal Flush": "皇家同花顺",
    "Straight Flush": "同花顺",
    "Four of a Kind": "四条",
    "Full House": "葫芦",
    "Flush": "同花",
    "Straight": "顺子",
    "Three of a Kind": "三条",
    "Two Pair": "两对",
    "One Pair": "一对",
    "High Card": "高牌",
}


def hand_name_cn(name):
    """
    把引擎给的牌型名翻成中文。

    摊牌、结算面板、赢家提示都走这里，
    界面上一律不出现英文牌型名。认不出来就原样返回。
    """

    if not name:
        return name

    return HAND_NAME_CN.get(name, name)


def card_json(card):
    return {
        "rank": card.rank,
        "suit": card.suit,
        "text": str(card),
        "red": card.suit in ("♥", "♦"),
    }


class Seat:

    def __init__(self, token, name):

        self.token = token
        self.name = name

        # 引擎里的玩家对象
        self.player = None

        self.connected = False
        self.last_seen = time.time()

        # 当前有几条活跃的 SSE 流。
        # 手机浏览器会自动重连，可能同时存在两条：
        # 只有当数量归零才算真正离线。
        self.streams = 0

        # 中途加入、还没入座的玩家
        self.waiting = False

        # 主动离开，下一手开始前移除
        self.left = False

        # 一手结束后是否已点“准备”，
        # 全部准备好后由系统自动发下一手。
        self.ready = False

        # 托管：到期时间戳（epoch 秒），0 表示没托管
        self.auto_until = 0.0

        # 从什么时候开始掉线的，用来判断要不要自动移出房间
        self.offline_since = None

        # 收到的互动道具计数（本场累计）
        self.presents_in = {
            item: 0 for item in PRESENT_ITEMS
        }


class Room:

    def __init__(
        self,
        code,
        small_blind=10,
        big_blind=20,
        start_chips=1000,
        ledger=None,
    ):

        self.code = code
        self.start_chips = start_chips

        # 长期账本，记录每个人的买入与盈亏
        self.ledger = ledger

        self.game = Game()
        self.game.small_blind = small_blind
        self.game.big_blind = big_blind

        self.seats = []
        self.listeners = []

        self.lock = threading.RLock()

        self.hand_running = False
        self.hand_no = 0

        self.waiting_token = None
        self.pending_action = None
        self.action_event = threading.Event()

        # 当前行动玩家的思考截止时间（epoch 秒）。
        # 用于前端显示真实剩余思考时间。
        self.action_deadline = None

        # 一手结束后用于“全部准备好再自动发牌”的计时器
        self._auto_next_timer = None
        self.result_time = 0

        # 正在驱动这一手的工作线程。
        # 靠它判断「标志说在打牌、线程却已经没了」的卡死状态。
        self._worker = None

        self.message = "等待开始"
        self.last_result = None
        self.chips_before = {}

        self.empty_since = None

        # 牌桌聊天记录（只留最近若干条，避免内存无限增长）
        self.chat = []

        # 快捷语音事件（转瞬即逝，客户端只播没见过的那些）
        self.quick = []

        # 互动道具事件（送花 / 扔鸡蛋…）
        self.presents = []

        # 表情事件（点头像旁的表情按钮发出去）
        self.emojis = []

    # ==============================
    # 座位管理
    # ==============================

    def add_seat(self, name):

        with self.lock:

            seat = Seat(
                uuid.uuid4().hex,
                name
            )

            self.seats.append(seat)

            # 牌局进行中：
            # 先坐着观战，下一手再加入，
            # 避免打乱座位顺序和 Button。
            if self.hand_running:

                seat.waiting = True

            else:

                self._attach_seat(seat)

            return seat

    def _attach_seat(self, seat):

        chips = self.start_chips

        # 账本里还有筹码就直接沿用，
        # 实现跨场次的长期筹码。
        if self.ledger is not None:

            chips = self.ledger.ensure_start(
                seat.name,
                self.start_chips
            )

        player = Player(seat.name, chips)

        seat.player = player
        seat.waiting = False

        self.game.add_player(player)

    def seat_by_token(self, token):

        for seat in self.seats:

            if seat.token == token:
                return seat

        return None

    def seat_by_player(self, player):

        for seat in self.seats:

            if seat.player is player:
                return seat

        return None

    def active_seats(self):

        return [
            seat
            for seat in self.seats
            if not seat.left
        ]

    def is_alive(self, seat):
        """
        这个座位的人是否还在。

        SSE 连着算在；
        没连 SSE 时，只要最近还有心跳（轮询 / 操作）也算在。
        手机浏览器常常连不上 SSE、只能靠轮询兜底，
        不能因此就把它判成“离线”而自动代打。
        """

        if seat is None:
            return False

        # 还有活跃的 SSE 流，人肯定在
        if getattr(seat, "streams", 0) > 0:
            return True

        if seat.connected:
            return True

        return (
            time.time() - seat.last_seen
        ) <= CONNECT_TTL

    def touch(self, token):
        """记录一次心跳（拉状态、提交动作都会调用）。"""

        seat = self.seat_by_token(token)

        if seat is None:
            return None

        was_alive = self.is_alive(seat)

        seat.last_seen = time.time()

        # 从“掉线”恢复过来时广播一次，
        # 让其他人马上看到“离线”标记消失。
        if not was_alive:
            self.broadcast()

        return seat

    def add_stream(self, token):
        """
        新增一条 SSE 流。

        浏览器自动重连时会短暂出现两条流，
        用计数避免旧流结束时把新的连线误标成离线。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return None

        with self.lock:

            seat.streams = (
                getattr(seat, "streams", 0) + 1
            )

            seat.connected = True
            seat.last_seen = time.time()

        self.broadcast()

        return seat

    def remove_stream(self, token):
        """一条 SSE 流结束；全部结束才算离线。"""

        seat = self.seat_by_token(token)

        if seat is None:
            return None

        with self.lock:

            seat.streams = max(
                0,
                getattr(seat, "streams", 0) - 1
            )

            seat.last_seen = time.time()

            offline = seat.streams == 0

            if offline:
                seat.connected = False

        if offline:
            # 人可能不在了，重新评估要不要自动发下一手
            self._try_auto_next()

        self.broadcast()

        return seat

    def mark_connected(self, token, connected):

        seat = self.seat_by_token(token)

        if seat is None:
            return None

        seat.connected = connected
        seat.last_seen = time.time()

        # 重新连线可能改变自动发牌所需的“准备”条件
        if not connected:
            self._try_auto_next()

        return seat

    def leave(self, token):

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        if self.hand_running:

            seat.left = True
            seat.connected = False
            seat.streams = 0

            self.message = (
                f"{seat.name} 将在下一手离开"
            )

            self.broadcast()

            # 离开会改“可参与”人数，尝试自动发牌
            self._try_auto_next()

            return True, "已标记离开"

        # 牌局没在进行，直接移除
        if seat.player is not None:

            if seat.player in self.game.players:

                self.game.players.remove(
                    seat.player
                )

        self.seats.remove(seat)

        self.message = f"{seat.name} 已离开"

        self.broadcast()

        return True, "已离开"

    def _drop_seat(self, seat):
        """把座位从房间和引擎里一起摘掉。"""

        if (
            seat.player is not None
            and seat.player in self.game.players
        ):

            self.game.players.remove(seat.player)

        if seat in self.seats:

            self.seats.remove(seat)

    def enforce_offline(self):
        """
        离线太久的人自动移出房间。

        牌局进行中只做「标记离开」（下一手开始时移除），
        免得中途摘座位打断正在打的这一手。

        返回是否有座位被处理。
        """

        now = time.time()
        taken = []

        with self.lock:

            for seat in list(self.seats):

                if self.is_alive(seat):

                    seat.offline_since = None
                    continue

                if seat.offline_since is None:

                    # 刚开始掉线，先记住时间
                    seat.offline_since = now
                    continue

                if now - seat.offline_since < OFFLINE_KICK:
                    continue

                if seat.left:
                    continue

                if self.hand_running:

                    seat.left = True
                    seat.connected = False
                    seat.streams = 0

                    taken.append(
                        seat.name + "（下一手移出）"
                    )
                    continue

                self._drop_seat(seat)
                taken.append(seat.name)

            if taken:

                self.message = (
                    "离线太久已移出：" + "、".join(taken)
                )

        if not taken:
            return False

        self.broadcast()
        self._try_auto_next()

        return True

    def rebuy(
        self,
        token,
        amount=None,
        loan=False
    ):
        """
        买入筹码。

        每两手牌之间随时可以买入，
        输光的人买入后可以继续打。

        amount:
            买入金额，不传则按起始筹码买入。

        loan:
            是否记为贷款。
            贷款同样计入累计买入，
            因此会体现在净盈亏里。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        if self.hand_running:
            return False, "本手还没结束，等这手打完再买入"

        player = seat.player

        if player is None:
            return False, "尚未入座"

        if amount is None:
            amount = self.start_chips

        amount = int(amount)

        if amount <= 0:
            return False, "买入金额必须大于 0"

        if amount > 1000000:
            return False, "买入金额太大了"

        player.chips += amount

        # 买入之后重新回到牌局
        player.eliminated = False

        if self.ledger is not None:

            self.ledger.record_buyin(
                seat.name,
                amount,
                loan
            )

        tag = "（贷款）" if loan else ""

        self.message = (
            f"{seat.name} 买入 {amount}{tag}，"
            f"当前筹码 {player.chips}"
        )

        self.broadcast()

        # 买入后可能凑齐“已准备”的条件，尝试自动发牌
        self._try_auto_next()

        return True, "买入成功"

    def return_chips(self, token, amount):
        """
        主动归还筹码（金额自己定）。

        账务口径跟买入完全一样，只是方向相反：
        筹码和累计买入等额一起减，所以净盈亏
        （筹码 − 累计买入）保持不变 —— 这是把筹码还回庄家，
        不算这个人输掉。规则也和买入对齐：一手打完才能操作。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        if self.hand_running:
            return (
                False,
                "本手还没结束，等这手打完再归还",
            )

        player = seat.player

        if player is None:
            return False, "尚未入座"

        if amount is None:
            amount = self.start_chips

        amount = int(amount)

        if amount <= 0:
            return False, "归还数量必须大于 0"

        if amount > player.chips:
            return (
                False,
                f"最多只能归还 {player.chips}",
            )

        player.chips -= amount

        if self.ledger is not None:

            self.ledger.record_return(
                seat.name, amount
            )

        self.message = (
            f"{seat.name} 归还 {amount}，"
            f"当前筹码 {player.chips}"
        )

        self.broadcast()

        return True, "已归还"

    def reset_game(self, token):

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        if self.hand_running:
            return False, "牌局进行中"

        # 清空长期账本，重新开始记账
        if self.ledger is not None:

            self.ledger.clear()

        for seat_item in self.active_seats():

            player = seat_item.player

            if player is None:
                continue

            player.chips = self.start_chips
            player.eliminated = False

            if self.ledger is not None:

                self.ledger.record_buyin(
                    seat_item.name,
                    self.start_chips
                )

        self.hand_no = 0
        self.last_result = None
        self.game.winners = []
        self.game.player_results = {}

        self.message = "账本已清空，重新记账"

        self.broadcast()

        return True, "已重置"

    # ==============================
    # 牌局流程
    # ==============================

    def start_hand(self, token):

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        return self._begin_hand()

    def _begin_hand(self):
        """真正开始一手牌（由玩家点击或系统自动调用）。"""

        with self.lock:

            if self.hand_running:
                return False, "牌局正在进行"

            self._apply_pending_changes()

            players = [
                item.player
                for item in self.active_seats()
                if item.player is not None
                and item.player.chips > 0
                and not item.player.eliminated
            ]

            if len(players) < 2:
                return False, "至少需要 2 名玩家"

            if self.game.is_game_over():
                return False, "本局已结束，请重置筹码"

            # 从这里到线程起来之间，任何一步出错都必须把
            # hand_running 退回去。否则这个标志会永远停在 True，
            # 房间就再也开不了牌、买不了筹码、点不了准备，
            # 只能重建房间。这类「报错后一直无法开局」就是这么来的。
            try:

                self.hand_running = True
                self.hand_no += 1
                self.last_result = None
                self.message = f"第 {self.hand_no} 手开始"

                # 新一手开始，清空准备状态
                for seat in self.seats:
                    seat.ready = False

                self.action_deadline = None
                self._cancel_auto_next()

                self.chips_before = {
                    item.token: (
                        item.player.chips
                        if item.player is not None
                        else 0
                    )
                    for item in self.active_seats()
                }

            except Exception as error:

                self._abort_hand_start(error)

                return False, self.message

        try:

            thread = threading.Thread(
                target=self._hand_worker,
                daemon=True
            )

            self._worker = thread
            thread.start()

        except Exception as error:

            with self.lock:

                self._abort_hand_start(error)

            self.broadcast()

            return False, self.message

        self.broadcast()

        return True, "开始"

    def _abort_hand_start(self, error):
        """开局失败：把状态退回去，别让 hand_running 卡住。"""

        self.hand_running = False
        self.hand_no = max(0, self.hand_no - 1)
        self.action_deadline = None
        self._worker = None
        self.message = f"开局失败：{error}"

    def _heal_stuck_hand(self):
        """
        自愈：标志说这一手还在打，但驱动它的线程已经结束了。

        正常情况下只有线程自己在收尾时才会复位 hand_running，
        所以「线程没了、标志还在」就是异常退出的残留状态。
        不处理的话房间会永久卡死，这里主动复位。
        """

        if not self.hand_running:
            return False

        worker = self._worker

        # 线程还活着，就是正常的一手，别动
        if worker is not None and worker.is_alive():
            return False

        self.hand_running = False
        self.waiting_token = None
        self.pending_action = None
        self.action_deadline = None
        self._worker = None
        self.message = "上一手异常中断，已自动恢复"

        return True

    def heal(self):
        """给定时清理用的自愈入口；恢复了就广播一次。"""

        with self.lock:

            if not self._heal_stuck_hand():
                return False

        self.broadcast()

        return True

    def _seat_index_of_player_index(self, player_index):
        """
        引擎里的玩家号 → 房间里的座位号。

        找不到返回 None。有人离开时两边会错位，
        所以一律按玩家对象反查。
        """

        game = self.game

        if (
            player_index is None
            or player_index < 0
            or player_index >= len(game.players)
        ):
            return None

        seat = self.seat_by_player(
            game.players[player_index]
        )

        if seat is None:
            return None

        return self.seats.index(seat)

    # ==============================
    # 准备 / 自动下一手
    # ==============================

    def set_ready(self, token, ready):
        """玩家标记是否准备好进入下一手。"""

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        with self.lock:

            seat.ready = bool(ready)
            self.message = (
                f"{seat.name} "
                + ("已准备" if ready else "取消准备")
            )

        self.broadcast()

        # 状态变化可能影响自动发牌
        self._try_auto_next()

        return True, "ok"

    def _eligible_next(self):
        """下一手会实际参与（需要点准备）的座位。"""

        return [
            seat
            for seat in self.active_seats()
            if seat.player is not None
            and not seat.player.eliminated
            and seat.player.chips > 0
        ]

    def _cancel_auto_next(self):

        timer = self._auto_next_timer

        if timer is not None and timer.is_alive():
            timer.cancel()

        self._auto_next_timer = None

    def _schedule_auto_next(self, delay):

        if (
            self._auto_next_timer is not None
            and self._auto_next_timer.is_alive()
        ):
            return

        timer = threading.Timer(
            delay,
            self._try_auto_next
        )

        timer.daemon = True
        self._auto_next_timer = timer
        timer.start()

    def _try_auto_next(self):
        """
        若所有“会参与的玩家”都准备好了，系统自动发牌
        （第一手同样如此），而不再由某个人点击按钮。
        """

        with self.lock:

            # 上一手可能是异常中断的，先复位再判断，
            # 否则自动发牌这条路会跟着一起卡住
            self._heal_stuck_hand()

            if self.hand_running:
                return

            if self.game.is_game_over():
                return

            eligible = self._eligible_next()

            if not eligible:
                return

            # 已经不在的玩家视为已准备，避免卡住牌桌
            all_ready = all(
                (seat.ready or not self.is_alive(seat))
                for seat in eligible
            )

            # last_result 为 None 说明这一场还没开过牌，
            # 也就是第一手。
            first_hand = self.last_result is None

            if all_ready:

                if first_hand:
                    # 第一手没有“上一手结果”要看，
                    # 人到齐就开。
                    self._begin_hand()
                    return

                elapsed = time.time() - self.result_time

                if elapsed < MIN_NEXT_DELAY:
                    self._schedule_auto_next(
                        MIN_NEXT_DELAY - elapsed
                    )
                    return

                self._begin_hand()
                return

            # 第一手：等大家都点准备，不强制开始，
            # 避免人还没到齐就先发牌。
            if first_hand:
                return

            # 还没都准备好：超过最长等待也直接发，
            # 防止有人一直不点准备导致牌桌卡死。
            elapsed = time.time() - self.result_time

            if elapsed >= AUTO_NEXT_MAX_WAIT:
                self._begin_hand()
                return

            self._schedule_auto_next(5)

    def _player_at(self, index):
        """按下标取引擎玩家，越界就取模，空桌返回 None。"""

        game = self.game

        if not game.players:
            return None

        return game.players[index % len(game.players)]

    def _sync_player_order(self):
        """
        让引擎的玩家顺序跟房间座位顺序对齐。

        两边的列表都只是 append / remove，全靠「顺序碰巧一致」
        维持对应；一旦有人中途离开、或观战的人入座，
        两边就会错位，屏幕上的表现就是：
            · D / SB / BB 三个标记不挨着
            · 下注不是沿座位依次来，而是跳着走
        所以每手开始前统一按座位顺序重排一次，从根上消掉。
        """

        game = self.game

        ordered = [
            seat.player
            for seat in self.seats
            if seat.player is not None
            and seat.player in game.players
        ]

        # 引擎里有、座位里没有的（正常不该出现）挂到末尾，
        # 免得把人弄丢
        for player in game.players:

            if player not in ordered:
                ordered.append(player)

        game.players[:] = ordered

    def _apply_pending_changes(self):

        game = self.game

        # 重排之前先记住「庄家是谁」「轮到谁」，
        # 重排之后再按人换算成新下标
        was_button = self._player_at(game.button_index)
        was_current = self._player_at(game.current_player_index)

        # 移除已经离开的玩家
        remaining = []

        for seat in self.seats:

            if seat.left:

                if (
                    seat.player is not None
                    and seat.player in game.players
                ):

                    game.players.remove(
                        seat.player
                    )

                continue

            remaining.append(seat)

        self.seats = remaining

        # 让等待中的玩家入座
        for seat in self.seats:

            if seat.waiting or seat.player is None:

                self._attach_seat(seat)

        # 座位变了，把引擎的玩家顺序重新对齐到座位顺序
        self._sync_player_order()

        if was_button is not None and was_button in game.players:

            game.button_index = game.players.index(
                was_button
            )

        if was_current is not None and was_current in game.players:

            game.current_player_index = game.players.index(
                was_current
            )

        elif game.players:

            game.current_player_index = (
                game.current_player_index
                % len(game.players)
            )

    def _hand_worker(self):

        try:

            success = self.game.play_hand(
                self.action_provider
            )

        except Exception as error:

            success = False

            self.message = (
                f"牌局异常：{error}"
            )

        with self.lock:

            self.hand_running = False
            self.waiting_token = None
            self.pending_action = None
            self.action_deadline = None
            self._cancel_auto_next()

            if success:

                self.last_result = (
                    self._build_result()
                )

                # 每一手结束都把筹码写回长期账本
                self._sync_ledger()

            else:

                self.last_result = None

                if self.message.startswith(
                    "轮到"
                ):
                    self.message = (
                        "本手牌未能正常结束"
                    )

        # 记录本手结束时刻，供自动发下一手计时
        self.result_time = time.time()

        self.broadcast()

        # 结束且未到游戏结束时，尝试自动发下一手
        if success and not self.game.is_game_over():
            self._try_auto_next()

    def action_provider(self, player):

        seat = self.seat_by_player(player)

        if seat is None:

            return {"action": "fold"}

        with self.lock:

            self.waiting_token = seat.token
            self.pending_action = None
            self.action_event.clear()

            # 记录思考截止时间，供前端显示真实剩余秒数
            self.action_deadline = (
                time.time() + ACTION_TIMEOUT
            )

        # 托管中：交给 AI，不等玩家
        if self.is_auto(seat):

            with self.lock:

                self.waiting_token = None
                self.action_deadline = None

            # 稍微停一下：AI 出手太快，牌桌上一闪就过去了
            if AI_ACTION_DELAY > 0:

                time.sleep(
                    round(
                        AI_ACTION_DELAY
                        * (0.6 + random.random()),
                        2
                    )
                )

            action = self._auto_action(player)

            self.message = (
                f"{seat.name} 托管中，AI "
                + describe_action(action)
            )

            self.broadcast()

            return action

        self.message = f"轮到 {seat.name} 行动"

        self.broadcast()

        # 人已经不在（很久没心跳）就不等，直接按超时规则处理
        if not self.is_alive(seat):

            with self.lock:
                self.action_deadline = None

            return self._timeout_action(player)

        # 等待玩家行动。
        # 每秒回头确认一次人还在不在：
        # 手机用轮询兜底时，一旦关掉页面心跳就会停，
        # 这时立刻自动行动，不必干等满 100 秒。
        arrived = False
        deadline = time.time() + ACTION_TIMEOUT

        while time.time() < deadline:

            if self.action_event.wait(1.0):
                arrived = True
                break

            if not self.is_alive(seat):
                break

        with self.lock:

            action = self.pending_action

            self.pending_action = None
            self.waiting_token = None
            self.action_deadline = None

        if not arrived or action is None:

            action = self._timeout_action(player)

            self.message = (
                f"{seat.name} 超时，自动"
                + (
                    "过牌"
                    if action["action"] == "check"
                    else "弃牌"
                )
            )

        return action

    def _sync_ledger(self):
        """
        把当前筹码和手数写回长期账本。
        """

        if self.ledger is None:
            return

        for seat in self.seats:

            if seat.left:
                continue

            if seat.player is None:
                continue

            self.ledger.sync_chips(
                seat.name,
                seat.player.chips
            )

            self.ledger.add_hand(seat.name)

        self.ledger.save_silent()

    def _auto_action(self, player):
        """
        托管中的决策：交给 AI（估牌力、看赔率、该加就加）。
        """

        legal = self._legal_actions(player)

        try:

            action = AUTO_PLAYER.decide(
                self.game,
                player,
                legal
            )

        except Exception:

            # AI 自己出岔子也不能把牌局卡住
            action = None

        return self._sanitize_action(
            action,
            legal,
            player
        )

    def _timeout_action(self, player):
        """
        超时 / 掉线时的处理：能过牌就过牌，否则弃牌。

        这里刻意不用 AI —— 人是没来得及动，
        不该由程序替他下重注。
        """

        if self.game.can_player_check(player):

            return {"action": "check"}

        return {"action": "fold"}

    def _sanitize_action(self, action, legal, player):
        """
        把动作校正成引擎一定接受的动作。

        AI 万一给出了非法的东西（金额越界之类），
        宁可退回跟注 / 过牌，也不能让整手牌崩掉。
        """

        actions = legal.get("actions") or []

        kind = (
            action.get("action")
            if isinstance(action, dict)
            else None
        )

        if kind == "raise":

            amount = action.get("amount")

            if (
                "raise" in actions
                and amount is not None
                and legal["raise_min"]
                <= int(amount)
                <= legal["raise_max"]
                and self.game.can_player_raise(
                    player, int(amount)
                )
            ):

                return {
                    "action": "raise",
                    "amount": int(amount)
                }

            kind = None

        if kind == "all_in" and "all_in" in actions:
            return {"action": "all_in"}

        if kind == "call" and "call" in actions:
            return {"action": "call"}

        if kind == "check" and "check" in actions:
            return {"action": "check"}

        if kind == "fold" and "fold" in actions:
            return {"action": "fold"}

        # 兜底：能过牌就过牌，其次跟注，最后弃牌
        if "check" in actions:
            return {"action": "check"}

        if "call" in actions:
            return {"action": "call"}

        return {"action": "fold"}

    def is_auto(self, seat):
        """这个人现在是不是托管中。"""

        return (
            seat is not None
            and seat.auto_until > time.time()
        )

    def set_auto(self, token, on):
        """
        开 / 关托管。

        托管期间由 AI 代打，单次最长 AUTO_PLAY_MAX 秒，
        到点自动失效（再点一次就是续上）。
        返回 (是否成功, 错误信息)。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        with self.lock:

            if not on:

                seat.auto_until = 0.0

                return True, None

            seat.auto_until = time.time() + AUTO_PLAY_MAX

            # 正好轮到他：立刻把 AI 的决策塞进去唤醒等待，
            # 不然要等下一次行动才生效
            if (
                self.waiting_token == seat.token
                and seat.player is not None
                and not self.action_event.is_set()
            ):

                self.pending_action = self._auto_action(
                    seat.player
                )

                self.action_event.set()

        self.message = f"{seat.name} 已托管，AI 代打"

        self.broadcast()

        return True, None

    def submit_action(
        self,
        token,
        action,
        amount=None
    ):

        with self.lock:

            if not self.hand_running:
                return False, "当前没有进行中的牌局"

            if self.waiting_token != token:
                return False, "还没轮到你行动"

            seat = self.seat_by_token(token)

            if seat is None:
                return False, "座位不存在"

            player = seat.player

            if player is None:
                return False, "尚未入座"

            if player != self.game.get_current_player():
                return False, "还没轮到你行动"

            legal = self._legal_actions(
                player
            )

            if action not in legal["actions"]:
                return False, "当前不能执行这个动作"

            if action == "raise":

                if amount is None:
                    return False, "加注必须指定金额"

                amount = int(amount)

                if (
                    amount < legal["raise_min"]
                    or amount > legal["raise_max"]
                ):
                    return False, "加注金额不合法"

            self.pending_action = {
                "action": action,
                "amount": (
                    int(amount)
                    if amount is not None
                    else None
                ),
            }

            self.action_event.set()

        return True, "已提交"

    # ==============================
    # 状态
    # ==============================

    def _legal_actions(self, player):

        game = self.game

        legal = ["fold"]

        if game.can_player_check(player):
            legal.append("check")

        if game.can_player_call(player):
            legal.append("call")

        if game.can_player_all_in(player):
            legal.append("all_in")

        raise_min = (
            game.current_bet
            + game.get_min_raise_amount()
        )

        raise_max = (
            player.current_bet
            + player.chips
        )

        if raise_min <= raise_max:

            legal.append("raise")

        return {
            "actions": legal,
            "call_amount": (
                game.get_call_amount(player)
            ),
            "raise_min": raise_min,
            "raise_max": raise_max,
            "chips": player.chips,
        }

    def state_for(self, token):

        with self.lock:

            # 有人来拿状态时先自愈一次：
            # 万一手上的标志卡在「打牌中」，这里立刻放开
            self._heal_stuck_hand()

            game = self.game

            you = self.seat_by_token(token)

            showdown = (
                game.phase == "showdown"
            )

            # 小盲 / 大盲座位（只在牌局进行中有意义）
            sb_seat_index = None
            bb_seat_index = None

            if self.hand_running:

                try:

                    sb_seat_index = (
                        self._seat_index_of_player_index(
                            game.get_small_blind_index()
                        )
                    )

                    bb_seat_index = (
                        self._seat_index_of_player_index(
                            game.get_big_blind_index()
                        )
                    )

                except Exception:

                    # 盲注位置只是展示信息，算不出来就不标，
                    # 不能因此让整份状态取不出来
                    sb_seat_index = None
                    bb_seat_index = None

            seats_state = []

            for index, seat in enumerate(
                self.seats
            ):

                player = seat.player

                cards = None

                if player is not None:

                    if seat is you:

                        cards = [
                            card_json(card)
                            for card in player.hand
                        ]

                    elif (
                        showdown
                        and not game.won_by_fold
                        and not player.folded
                        and player.hand
                    ):

                        cards = [
                            card_json(card)
                            for card in player.hand
                        ]

                hand_name = None

                if (
                    showdown
                    and player is not None
                    and player in game.player_results
                    and not player.folded
                ):

                    hand_name = hand_name_cn(
                        game.player_results[player][
                            "name"
                        ]
                    )

                if player is None:

                    # 中途加入、还没轮到入座的人：
                    # 引擎里还没有他，但账本里是有筹码的。
                    # 以前这里直接写 0，再减掉累计买入，
                    # 名片上就成了「0 / -6000」这种负数。
                    if self.ledger is not None:

                        chips = self.ledger.get_chips(
                            seat.name
                        )

                    else:

                        chips = self.start_chips

                    bet = 0
                    bet_total = 0
                    folded = False
                    all_in = False
                    eliminated = False

                else:

                    chips = player.chips
                    bet = player.current_bet

                    # 本手牌累计投入。
                    # 展示净盈亏时要把它算回去，
                    # 否则玩家一下注就会显示成亏损。
                    bet_total = player.total_bet

                    folded = player.folded
                    all_in = player.all_in
                    eliminated = player.eliminated

                # 长期账本里的统计
                buy_in_total = 0
                loan_total = 0
                hands = 0

                if self.ledger is not None:

                    entry = self.ledger.get_entry(
                        seat.name
                    )

                    buy_in_total = entry[
                        "buy_in_total"
                    ]

                    loan_total = entry[
                        "loan_total"
                    ]

                    hands = entry["hands"]

                seats_state.append(
                    {
                        "seat": index,
                        "name": seat.name,
                        "chips": chips,
                        "bet": bet,
                        "bet_total": bet_total,
                        "folded": folded,
                        "all_in": all_in,
                        "eliminated": eliminated,
                        "connected": self.is_alive(seat),
                        "waiting": seat.waiting,
                        "left": seat.left,
                        "is_you": seat is you,
                        "ready": seat.ready,
                        "cards": cards,
                        "hand_name": hand_name,
                        # 名片上标 SB / BB 用
                        "is_sb": index == sb_seat_index,
                        "is_bb": index == bb_seat_index,
                        # 被送过的道具计数（点名片查看）
                        "presents_in": dict(seat.presents_in),
                        # 托管剩余秒数，0 表示没托管
                        "auto": max(
                            0.0, seat.auto_until - time.time()
                        ),
                        "buy_in_total": buy_in_total,
                        "loan_total": loan_total,
                        "hands": hands,
                        # 净盈亏 = 手上筹码
                        #        + 本手已投入
                        #        - 累计买入
                        "net": (
                            chips
                            + bet_total
                            - buy_in_total
                        ),
                    }
                )

            # 庄家座位。
            # 房间里的座位号和引擎里的玩家号
            # 可能因为有人离开而错位，
            # 因此这里按玩家对象反查座位号。
            button_seat_index = None

            if game.players:

                button_player = game.players[
                    game.button_index
                    % len(game.players)
                ]

                button_seat = (
                    self.seat_by_player(
                        button_player
                    )
                )

                if button_seat is not None:

                    button_seat_index = (
                        self.seats.index(
                            button_seat
                        )
                    )

            state = {
                "room": self.code,
                "phase": game.phase,
                "hand_no": self.hand_no,
                "pot": game.pot,
                "current_bet": game.current_bet,
                "community": [
                    card_json(card)
                    for card in game.community_cards
                ],
                # 算不出来就留空，绝不要退到座位 0 ——
                # 那会给错误的人戴上 D，看着反倒像位置错了
                "button_index": button_seat_index,
                "small_blind": game.small_blind,
                "big_blind": game.big_blind,
                "start_chips": self.start_chips,
                "seats": seats_state,
                "hand_running": self.hand_running,
                "message": self.message,
                "result": self.last_result,
                "game_over": game.is_game_over(),
                # 留宽一些：前端靠「读到第几条」来播弹幕，
                # 一手之内不能被截断，否则序号会错位
                "log": game.game_log[-200:],
            }

            # 当前行动玩家
            acting_seat = None

            if (
                self.hand_running
                and self.waiting_token
                is not None
            ):

                acting = self.seat_by_token(
                    self.waiting_token
                )

                if acting is not None:

                    acting_seat = (
                        self.seats.index(acting)
                    )

            state["acting_seat"] = acting_seat
            state["acting_name"] = (
                self.seats[acting_seat].name
                if acting_seat is not None
                else None
            )

            state["your_turn"] = (
                self.waiting_token is not None
                and self.waiting_token == token
            )

            # 你可以执行的动作
            state["legal"] = None

            if (
                state["your_turn"]
                and you is not None
                and you.player is not None
            ):

                state["legal"] = (
                    self._legal_actions(
                        you.player
                    )
                )

            # 倒计时
            state["timeout"] = ACTION_TIMEOUT

            # 当前行动玩家的思考截止时间（epoch 秒）。
            # 前端据此显示真实剩余秒数，无需频繁轮询。
            state["action_deadline"] = (
                self.action_deadline
                if (
                    self.hand_running
                    and self.waiting_token is not None
                )
                else None
            )

            # 自动下一手相关：可参与人数与已准备人数
            if not self.hand_running:

                eligible = self._eligible_next()

                state["next_eligible"] = len(eligible)
                state["next_ready"] = sum(
                    1
                    for s in eligible
                    if s.ready or not self.is_alive(s)
                )
                state["auto_next"] = not self.game.is_game_over()

            else:

                state["next_eligible"] = 0
                state["next_ready"] = 0
                state["auto_next"] = False

            if self.game.is_game_over():

                state["champion"] = (
                    self._champion_name()
                )

            # 牌桌聊天（最近若干条）
            state["chat"] = list(self.chat)

            # 快捷语音：可选的句子 + 最近的事件
            state["quick_phrases"] = QUICK_PHRASES
            state["quick"] = list(self.quick)

            # 互动道具的最近事件
            state["presents"] = list(self.presents)

            # 每种道具的筹码收支，前端按钮上要标出来
            state["present_chips"] = PRESENT_CHIPS

            # 托管单次最长时间
            state["auto_max"] = AUTO_PLAY_MAX

            # 表情：可选张数 + 最近的事件
            state["emoji_count"] = EMOJI_COUNT
            state["emojis"] = list(self.emojis)

            return state

    def _champion_name(self):

        winner = self.game.get_game_winner()

        if winner is None:
            return None

        return winner.name

    def _build_result(self):

        game = self.game

        items = []

        for seat in self.seats:

            player = seat.player

            if player is None:
                continue

            before = self.chips_before.get(
                seat.token,
                0
            )

            delta = player.chips - before

            hand_name = None
            best_cards = None

            if (
                player in game.player_results
                and not player.folded
            ):

                result = game.player_results[
                    player
                ]

                hand_name = hand_name_cn(result["name"])

                best_cards = [
                    card_json(card)
                    for card in result["cards"]
                ]

            buy_in_total = 0

            if self.ledger is not None:

                buy_in_total = (
                    self.ledger.get_entry(
                        seat.name
                    )["buy_in_total"]
                )

            items.append(
                {
                    "seat": self.seats.index(
                        seat
                    ),
                    "name": seat.name,
                    "hand_name": hand_name,
                    "cards": (
                        [
                            card_json(card)
                            for card in player.hand
                        ]
                        if (
                            not player.folded
                            and not game.won_by_fold
                        )
                        else None
                    ),
                    "best_cards": best_cards,
                    "delta": delta,
                    "chips": player.chips,
                    "folded": player.folded,
                    "net": (
                        player.chips
                        + player.total_bet
                        - buy_in_total
                    ),
                }
            )

        winners = []

        for player in game.winners:

            seat = self.seat_by_player(player)

            winners.append(
                {
                    "name": player.name,
                    "seat": (
                        self.seats.index(seat)
                        if seat is not None
                        else None
                    ),
                    "hand_name": (
                        hand_name_cn(
                            game.player_results[player][
                                "name"
                            ]
                        )
                        if player in game.player_results
                        else None
                    ),
                }
            )

        return {
            "hand_no": self.hand_no,
            "winners": winners,
            "players": items,
            "won_by_fold": game.won_by_fold,
            "board": [
                card_json(card)
                for card in game.community_cards
            ],
        }

    # ==============================
    # 推送
    # ==============================

    def _push_chat(self, entry):
        """
        往牌桌聊天记录里追加一条。

        调用方需已持有 self.lock。记录里混有打字消息、
        表情和快捷语音，靠 entry["kind"] 区分渲染方式。
        """

        self.chat.append(entry)

        if len(self.chat) > CHAT_KEEP:

            self.chat = self.chat[-CHAT_KEEP:]

    def add_chat(self, token, text):
        """
        发一条牌桌消息。

        返回 (是否成功, 错误信息)。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在"

        text = " ".join(
            str(text or "").split()
        )

        if not text:
            return False, "内容不能为空"

        if len(text) > CHAT_MAX_LEN:
            text = text[:CHAT_MAX_LEN]

        with self.lock:

            self._push_chat({
                "kind": "text",
                "name": seat.name,
                "text": text,
                "at": time.time(),
            })

        # 让所有人立刻看到新消息
        self.broadcast()

        return True, None

    def send_emoji(self, token, index):
        """
        发一个表情。返回 (是否成功, 错误信息, 事件)。

        表情会飘在发送者名片上，同时记进牌桌聊天记录。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在", None

        try:
            index = int(index)
        except (TypeError, ValueError):
            return False, "表情不存在", None

        if index < 0 or index >= EMOJI_COUNT:
            return False, "表情不存在", None

        with self.lock:

            event = {
                # 客户端靠这个 key 去重，保证每条只飘一次
                "key": uuid.uuid4().hex,
                "seat": self.seats.index(seat),
                "name": seat.name,
                "index": index,
                "at": time.time(),
            }

            self.emojis.append(event)

            if len(self.emojis) > EMOJI_KEEP:

                self.emojis = self.emojis[-EMOJI_KEEP:]

            # 同一时刻同时写进聊天记录，时间戳复用事件本身的
            self._push_chat({
                "kind": "emoji",
                "name": seat.name,
                "index": index,
                "at": event["at"],
            })

        self.broadcast()

        return True, None, event

    def send_quick(self, token, phrase_id):
        """
        发一条快捷语音。

        除了本机的音效，其他人靠这条广播各自播放并弹出文字特效。
        返回 (是否成功, 错误信息, 事件)。发的人拿到事件后
        可以立刻本机回显，不用等广播绕一圈。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在", None

        phrase = None

        for item in QUICK_PHRASES:

            if item["id"] == phrase_id:
                phrase = item
                break

        if phrase is None:
            return False, "没有这条快捷语音", None

        with self.lock:

            event = {
                # 客户端靠这个 key 去重，保证每条只播一次
                "key": uuid.uuid4().hex,
                "seat": self.seats.index(seat),
                "name": seat.name,
                "text": phrase["text"],
                "sound": phrase["sound"],
                "at": time.time(),
            }

            self.quick.append(event)

            if len(self.quick) > QUICK_KEEP:

                self.quick = self.quick[-QUICK_KEEP:]

            # 同一时刻同时写进聊天记录，时间戳复用事件本身的
            self._push_chat({
                "kind": "quick",
                "name": seat.name,
                "text": phrase["text"],
                "at": event["at"],
            })

        self.broadcast()

        return True, None, event

    def send_present(self, token, target_index, item):
        """
        长按别人名片送出的互动。

        返回 (是否成功, 错误信息, 事件)，发的人可以立刻本机回显。
        """

        seat = self.seat_by_token(token)

        if seat is None:
            return False, "座位不存在", None

        if item not in PRESENT_ITEMS:
            return False, "没有这个道具", None

        try:
            target_index = int(target_index)
        except (TypeError, ValueError):
            return False, "目标不对", None

        with self.lock:

            seats = self.seats

            if target_index < 0 or target_index >= len(seats):
                return False, "目标不对", None

            target = seats[target_index]

            if target is seat:
                return False, "不能对自己用", None

            if target.left:
                return False, "对方已经离桌", None

            sender = seat.player
            receiver = target.player

            if sender is None or receiver is None:
                return False, "有人还没入座", None

            chips = PRESENT_CHIPS[item]
            cost = chips["cost"]

            if sender.chips < cost:
                return (
                    False,
                    f"筹码不够，这个要 {cost} 个",
                    None,
                )

            # 送的人扣掉成本，收的人按道具涨或跌。
            # 收的人可能只剩一点，不能扣成负数。
            sender.chips -= cost
            receiver.chips = max(
                0, receiver.chips + chips["gain"]
            )

            # 立刻写回长期账本，免得中途重连又拿回旧筹码
            if self.ledger is not None:

                self.ledger.sync_chips(
                    seat.name, sender.chips
                )

                self.ledger.sync_chips(
                    target.name, receiver.chips
                )

            # 记一笔「这个人被送过多少」，名片上可以查看
            target.presents_in[item] = (
                target.presents_in.get(item, 0) + 1
            )

            event = {
                "key": uuid.uuid4().hex,
                "from": seats.index(seat),
                "from_name": seat.name,
                "to": target_index,
                "to_name": target.name,
                "item": item,
                "at": time.time(),
            }

            self.presents.append(event)

            if len(self.presents) > PRESENT_KEEP:

                self.presents = (
                    self.presents[-PRESENT_KEEP:]
                )

        self.broadcast()

        return True, None, event

    def subscribe(self, token):

        listener_queue = queue.Queue()

        with self.lock:

            self.listeners.append(
                (token, listener_queue)
            )

        return listener_queue

    def unsubscribe(self, listener_queue):

        with self.lock:

            self.listeners = [
                item
                for item in self.listeners
                if item[1] is not listener_queue
            ]

    def broadcast(self):

        with self.lock:

            listeners = list(self.listeners)

        for token, listener_queue in listeners:

            try:

                listener_queue.put(
                    self.state_for(token)
                )

            except Exception:

                pass

    def is_empty(self):

        for seat in self.seats:

            if self.is_alive(seat):
                return False

        return True


class RoomManager:

    def __init__(self, ledger=None):

        self.rooms = {}
        self.lock = threading.Lock()

        self.ledger = ledger

    def get_room(self, code):

        with self.lock:

            return self.rooms.get(code)

    def create_room(
        self,
        code=None,
        small_blind=10,
        big_blind=20,
        start_chips=1000,
    ):

        with self.lock:

            if code is None:

                while True:

                    code = str(
                        random.randint(
                            1000,
                            9999
                        )
                    )

                    if code not in self.rooms:
                        break

            if code in self.rooms:
                return self.rooms[code]

            room = Room(
                code,
                small_blind=small_blind,
                big_blind=big_blind,
                start_chips=start_chips,
                ledger=self.ledger,
            )

            self.rooms[code] = room

            return room

    def list_rooms(self):
        """
        大厅用的房间列表。

        除了房间号和手数，还带上「几个在线、都是谁」——
        初始页面就是大家等人齐的地方，得能一眼看到。
        """

        with self.lock:

            result = []

            for code, room in self.rooms.items():

                players = []

                for seat in room.active_seats():

                    player = seat.player

                    players.append(
                        {
                            "name": seat.name,
                            # 跟牌桌里判定「离线」用的是同一套逻辑
                            "online": room.is_alive(seat),
                            "out": bool(
                                player is not None
                                and getattr(
                                    player,
                                    "eliminated",
                                    False,
                                )
                            ),
                            "waiting": seat.waiting,
                        }
                    )

                # 没人了的房间是残留（要等回收才删），
                # 列在大厅里只会让人点进去发现是空的
                if not players:
                    continue

                result.append(
                    {
                        "code": code,
                        "players": len(players),
                        "online": len(
                            [
                                p
                                for p in players
                                if p["online"]
                            ]
                        ),
                        "names": players,
                        "hand_no": room.hand_no,
                        "running": room.hand_running,
                    }
                )

            # 人多的房间排前面，好找
            result.sort(
                key=lambda r: (
                    -r["online"],
                    r["code"],
                )
            )

            return result

    def cleanup(self):

        now = time.time()

        with self.lock:

            rooms = list(self.rooms.values())

        # 先自愈：房间里可能残留「打牌中」的假状态，
        # 这里恢复了会广播，干等的人也能立刻接着开局
        for room in rooms:

            try:

                room.heal()

            except Exception:

                # 自愈本身出问题不能影响清理
                pass

            # 离线太久的人自动移出
            try:

                room.enforce_offline()

            except Exception:

                # 踢人出问题也不能影响清理
                pass

        with self.lock:

            for code in list(self.rooms.keys()):

                room = self.rooms[code]

                if room.is_empty():

                    if room.empty_since is None:
                        room.empty_since = now

                    elif (
                        now - room.empty_since
                        > EMPTY_ROOM_TTL
                    ):

                        del self.rooms[code]

                else:

                    room.empty_since = None
