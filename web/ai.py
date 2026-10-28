# -*- coding: utf-8 -*-
"""
托管用的 AI。

以前的托管只有两条路：能过牌就过牌，否则弃牌 ——
等于从不主动下注：翻牌前白送盲注，听牌也不跟，别人
一加注就跑。这里换成一套简单但讲道理的判断：

  1. 估牌力：翻牌前看两张底牌（Chen 公式的思路），
     翻牌后看「自己的底牌 + 公共牌」里最强的五张
  2. 算赔率：跟注要花的钱占多大比例，牌力高过它才值得跟
  3. 牌力够就主动下注 / 加注，很弱的牌偶尔诈唬，听牌给点加成
  4. 对手越多，同样的牌越不值钱

最后一道保险：算出来的动作一定会跟「引擎允许的动作」核对，
保证不会提交非法动作把牌局卡住（核对在 Room 里做）。
"""

import random


# 牌型等级 -> 大致胜率（对着一个随机手牌）
# 等级见 hand_evaluator.get_hand_rank
RANK_STRENGTH = {
    1: 0.18,    # 高牌
    2: 0.42,    # 一对
    3: 0.62,    # 两对
    4: 0.76,    # 三条
    5: 0.84,    # 顺子
    6: 0.90,    # 同花
    7: 0.95,    # 葫芦
    8: 0.98,    # 四条
    9: 1.00,    # 同花顺
}

ACTION_CN = {
    "check": "过牌",
    "call": "跟注",
    "fold": "弃牌",
    "all_in": "全下",
    "raise": "加注",
}


def describe(action):
    """把动作翻成中文，给牌桌上的提示用。"""

    if not action:
        return "弃牌"

    kind = action.get("action")

    if kind == "raise":

        amount = action.get("amount")

        if amount:
            return "加注到 " + str(amount)

        return "加注"

    return ACTION_CN.get(kind, "弃牌")


class AutoPlayer:

    def __init__(self, rng=None):

        self.rng = rng or random.Random()

    # ==============================
    # 牌力
    # ==============================

    def strength(self, game, player):
        """这副牌有多强，0~1。"""

        if not player.hand:
            return 0.0

        made = None

        if game.community_cards:
            made = self.made_strength(game, player)

        if made is None:

            made = self.preflop_strength(
                player.hand,
                game.hand_evaluator
            )

        return self.draw_bonus(game, player, made)

    def preflop_strength(self, cards, evaluator):
        """
        两张底牌的强度。

        按 Chen 公式的路子算：最高牌定基础分，对子翻倍，
        同花加分，间隔越大扣得越多。
        """

        values = sorted(
            [
                evaluator.get_rank_value(card.rank)
                for card in cards
            ],
            reverse=True
        )

        if len(values) < 2:
            return 0.0

        high = values[0]
        low = values[1]

        if high == 14:
            score = 10.0
        elif high == 13:
            score = 8.0
        elif high == 12:
            score = 7.0
        elif high == 11:
            score = 6.0
        else:
            score = high / 2.0

        is_pair = high == low

        if is_pair:
            score = max(score * 2.0, 5.0)

        if cards[0].suit == cards[1].suit:
            score += 2.0

        if not is_pair:

            gap = high - low - 1

            if gap == 1:
                score -= 1.0
            elif gap == 2:
                score -= 2.0
            elif gap == 3:
                score -= 4.0
            elif gap >= 4:
                score -= 5.0

            # 两张都不大但挨得紧，有凑顺子的潜力
            if high < 12 and gap <= 1:
                score += 1.0

        # Chen 的原始分大致落在 -1.5 ~ 20
        return max(
            0.0,
            min(1.0, (score + 1.5) / 21.5)
        )

    def made_strength(self, game, player):
        """
        翻牌后：取最强五张的牌型，换算成大致胜率。
        """

        cards = (
            list(player.hand)
            + list(game.community_cards)
        )

        if len(cards) < 5:
            return None

        result = game.hand_evaluator.evaluate_seven_cards(
            cards
        )

        if not result:
            return None

        rank = result.get("rank") or 1

        strength = RANK_STRENGTH.get(rank, 0.20)

        value = result.get("value") or []
        top = value[0] if value else 0

        mine = set(
            game.hand_evaluator.get_rank_value(
                card.rank
            )
            for card in player.hand
        )

        # 用的是公共牌凑出来的牌型，谁都有，不能当强牌
        if rank == 2 and top not in mine:
            strength = 0.30

        elif rank == 3 and top not in mine:
            strength = 0.46

        # 高牌 / 一对：顶张越大越值钱
        if rank <= 2:
            strength += (top / 14.0) * 0.06

        return max(0.0, min(1.0, strength))

    def draw_bonus(self, game, player, strength):
        """
        听牌加成。

        还没成牌、但差一张就成（同花听牌 / 两头顺），
        给一点加成，让它愿意在有赔率时跟一手、
        偶尔也能半诈唬一下。
        """

        if strength >= 0.60:
            return strength

        if not game.community_cards:
            return strength

        cards = (
            list(player.hand)
            + list(game.community_cards)
        )

        ranks = set(
            game.hand_evaluator.get_rank_value(
                card.rank
            )
            for card in cards
        )

        # A 当 1 用，方便算 A2345
        if 14 in ranks:
            ranks.add(1)

        bonus = 0.0

        # 同花听牌：四张同花，而且我手里至少有一张
        suits = {}

        for card in cards:

            suits[card.suit] = (
                suits.get(card.suit, 0) + 1
            )

        for suit, count in suits.items():

            if count != 4:
                continue

            mine = any(
                card.suit == suit
                for card in player.hand
            )

            # 只有公共牌四张同花，别人也在等，价值低
            if mine:
                bonus = max(bonus, 0.17)
            else:
                bonus = max(bonus, 0.05)

            break

        # 顺子听牌：五张的窗口里已经有四张
        best = 0.0

        for start in range(2, 11):

            window = set(
                range(start, start + 5)
            )

            have = len(window & ranks)

            if have != 4:
                continue

            missing = (window - ranks).pop()

            # 缺两头的那张 = 两头顺，缺中间 = 卡顺
            if missing in (start, start + 4):
                best = max(best, 0.14)
            else:
                best = max(best, 0.06)

        bonus = max(bonus, best)

        return max(0.0, min(1.0, strength + bonus))

    # ==============================
    # 决策
    # ==============================

    def decide(self, game, player, legal=None):
        """
        给一个动作。

        legal 是房间算好的「当前允许的动作」，用它来判合法性。
        """

        legal = legal or {}

        actions = list(legal.get("actions") or [])

        if not actions:
            return {"action": "fold"}

        can_check = "check" in actions
        can_call = "call" in actions
        can_raise = "raise" in actions

        call_amount = int(legal.get("call_amount") or 0)

        pot = max(0, int(game.pot or 0))

        strength = self.strength(game, player)

        # 对手越多，这副牌越不值钱
        opponents = len([
            p for p in game.players
            if not p.folded and not p.eliminated
        ]) - 1

        if opponents > 1:
            strength = max(
                0.0,
                strength - 0.03 * (opponents - 1)
            )

        # 跟注要花的钱占总底池的比例：牌力高过它，长期才划算
        price = 0.0

        if not can_check and call_amount > 0:

            pool = pot + call_amount

            if pool > 0:
                price = call_amount / float(pool)

        # ---- 不用出钱：过牌，或者主动下注 ----
        if can_check:

            if not can_raise:
                return {"action": "check"}

            # 牌力越强越可能下注；很弱的牌偶尔开枪诈唬
            if strength >= 0.85:
                chance = 0.92
            elif strength >= 0.65:
                chance = 0.72
            elif strength >= 0.45:
                chance = 0.38
            elif strength >= 0.30:
                chance = 0.14
            else:
                chance = 0.09

            if self.rng.random() > chance:
                return {"action": "check"}

            return self.make_raise(
                game,
                player,
                legal,
                strength,
                pot,
                bluff=strength < 0.32
            )

        # ---- 要花钱：先算赔率 ----
        if can_call:

            # 牌力盖过赔率，就跟；够强的直接改成加注
            if strength >= price + 0.05:

                # 前面还没人真正加过（只有盲注那么点），
                # 好牌就该主动抬一手，不然一路跟注太被动了
                threshold = 0.78

                if game.current_bet <= game.big_blind:
                    threshold = 0.58

                if (
                    can_raise
                    and strength >= threshold
                    and self.rng.random() < 0.65
                ):

                    return self.make_raise(
                        game,
                        player,
                        legal,
                        strength,
                        pot
                    )

                return {"action": "call"}

            # 跟注不贵（两个大盲以内）且牌不算太差，就跟
            if (
                strength >= 0.30
                and call_amount <= game.big_blind * 2
            ):
                return {"action": "call"}

            # 便宜到像是白送，牌不太差就跟进去看看
            if strength >= 0.22 and price <= 0.14:
                return {"action": "call"}

            # 手里本来就没多少钱了，跟一把也无所谓
            if (
                strength >= 0.30
                and player.chips <= call_amount
            ):
                return {"action": "all_in"} \
                    if "all_in" in actions \
                    else {"action": "call"}

            return {"action": "fold"}

        # 既不能过牌也不能跟注：只剩弃牌，除非能加注
        if can_raise and strength >= 0.55:

            return self.make_raise(
                game,
                player,
                legal,
                strength,
                pot
            )

        if "all_in" in actions and strength >= 0.70:
            return {"action": "all_in"}

        return {"action": "fold"}

    def make_raise(
        self,
        game,
        player,
        legal,
        strength,
        pot,
        bluff=False
    ):
        """
        算一个加注金额。

        金额区间由引擎给（raise_min ~ raise_max，都是
        「这手牌总共下到多少」），这里保证落在里面。
        """

        actions = legal.get("actions") or []

        raise_min = int(legal.get("raise_min") or 0)
        raise_max = int(legal.get("raise_max") or 0)

        if "raise" not in actions or raise_max < raise_min:

            if "call" in actions:
                return {"action": "call"}

            if "check" in actions:
                return {"action": "check"}

            return {"action": "fold"}

        if bluff:
            # 诈唬打小一点，别把家当押上去
            target = raise_min + int(pot * 0.35)
        else:
            target = int(
                game.current_bet
                + max(
                    pot * self.rng.uniform(0.45, 0.75),
                    game.big_blind
                )
            )

        cap = raise_max

        # 不是特别强的牌，最多投手里三分之一的筹码，
        # 免得 AI 动不动就一把梭
        if strength < 0.90:

            cap = min(
                cap,
                player.current_bet
                + max(
                    int(player.chips / 3.0),
                    raise_min
                )
            )

        amount = max(
            raise_min,
            min(target, cap, raise_max)
        )


        # 最后跟引擎核对一次，不合法就退成跟注 / 过牌
        if not game.can_player_raise(player, amount):

            if "call" in actions:
                return {"action": "call"}

            if "check" in actions:
                return {"action": "check"}

            return {"action": "fold"}

        return {
            "action": "raise",
            "amount": int(amount)
        }
