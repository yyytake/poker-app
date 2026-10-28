from itertools import combinations
from collections import Counter


class HandEvaluator:

    def __init__(self):
        pass


    def get_rank_value(self, rank):
        """
        将牌面转换为数字。

        A = 14
        K = 13
        Q = 12
        J = 11
        T = 10
        """

        if rank == "A":
            return 14

        elif rank == "K":
            return 13

        elif rank == "Q":
            return 12

        elif rank == "J":
            return 11

        elif rank == "10":
            return 10

        else:
            return int(rank)



    def get_hand_rank(self, cards):
        """
        判断五张牌牌型。

        返回：

        {
            "name": 牌型名称,
            "rank": 牌型等级,
            "value": 比较数值
        }

        rank越大，牌型越强。

        """

        ranks = []


        for card in cards:

            value = self.get_rank_value(
                card.rank
            )

            ranks.append(value)



        counter = Counter(ranks)



        counts = sorted(
            counter.values(),
            reverse=True
        )

        # =====================
        # 同花顺判断
        # =====================

        suits = [
            card.suit
            for card in cards
        ]


        is_flush = len(
            set(suits)
        ) == 1


        unique_ranks = sorted(
            set(ranks)
        )


        straight_high = None


        if len(unique_ranks) == 5:

            if (
                unique_ranks[-1]
                - unique_ranks[0]
                == 4
            ):
                straight_high = unique_ranks[-1]


            elif unique_ranks == [
                2,3,4,5,14
            ]:
                straight_high = 5


        if is_flush and straight_high:


            if straight_high == 14:

                return {
                    "name": "Royal Flush",
                    "rank": 10,
                    "value": [
                        14
                    ]
                }


            return {
                "name": "Straight Flush",
                "rank": 9,
                "value": [
                    straight_high
                ]
            }

        # =====================
        # 四条判断
        # =====================

        if counts[0] == 4:

            four = max(
                counter,
                key=lambda x: counter[x]
            )


            kicker = max(
                x for x in ranks
                if x != four
            )


            return {
                "name": "Four of a Kind",
                "rank": 8,
                "value": [
                    four,
                    kicker
                ]
            }


        # =====================
        # 葫芦判断
        # =====================

        if counts[0] == 3 and counts[1] == 2:

            three = max(
                counter,
                key=lambda x: (
                    counter[x] == 3,
                    x
                )
            )


            pair = max(
                counter,
                key=lambda x: (
                    counter[x] == 2,
                    x
                )
            )


            return {
                "name": "Full House",
                "rank": 7,
                "value": [
                    three,
                    pair
                ]
            }

        # =====================
        # 同花判断
        # =====================

        suits = []

        for card in cards:
            suits.append(
                card.suit
            )


        is_flush = len(
            set(suits)
        ) == 1


        if is_flush:

            flush_cards = sorted(
                ranks,
                reverse=True
            )


            return {
                "name": "Flush",
                "rank": 6,
                "value": flush_cards
            }

        # =====================
        # 顺子判断
        # =====================

        unique_ranks = sorted(
            set(ranks)
        )


        straight_high = None


        if len(unique_ranks) == 5:

            # 普通顺子
            if (
                unique_ranks[-1]
                - unique_ranks[0]
                == 4
            ):
                straight_high = unique_ranks[-1]


            # A2345 特殊顺子
            elif unique_ranks == [
                2, 3, 4, 5, 14
            ]:
                straight_high = 5



        if straight_high:

            return {
                "name": "Straight",
                "rank": 5,
                "value": [
                    straight_high
                ]
            }

        # =====================
        # 三条
        # =====================

        if counts[0] == 3:

            three = max(
                counter,
                key=lambda x: counter[x]
            )


            kickers = [
                x for x in ranks
                if x != three
            ]

            kickers.sort(
                reverse=True
            )


            return {
                "name": "Three of a Kind",
                "rank": 4,
                "value": [
                    three,
                    *kickers
                ]
            }



        # =====================
        # 两对
        # =====================

        if counts[0] == 2 and counts[1] == 2:


            pairs = [
                x for x in counter
                if counter[x] == 2
            ]

            pairs.sort(
                reverse=True
            )


            kicker = [
                x for x in counter
                if counter[x] == 1
            ][0]


            return {
                "name": "Two Pair",
                "rank": 3,
                "value": [
                    pairs[0],
                    pairs[1],
                    kicker
                ]
            }



        # =====================
        # 一对
        # =====================

        if counts[0] == 2:


            pair = [
                x for x in counter
                if counter[x] == 2
            ][0]


            kickers = [
                x for x in ranks
                if x != pair
            ]

            kickers.sort(
                reverse=True
            )


            return {
                "name": "One Pair",
                "rank": 2,
                "value": [
                    pair,
                    *kickers
                ]
            }



        # =====================
        # 高牌
        # =====================

        ranks.sort(
            reverse=True
        )


        return {
            "name": "High Card",
            "rank": 1,
            "value": ranks
        }

    def compare_hands(self, hand1, hand2):
        """
        比较两个牌型。

        返回：
        1  hand1更强
        -1 hand2更强
        0 平局
        """

        if hand1["rank"] > hand2["rank"]:
            return 1

        elif hand1["rank"] < hand2["rank"]:
            return -1


        # 牌型相同时比较value

        if hand1["value"] > hand2["value"]:
            return 1

        elif hand1["value"] < hand2["value"]:
            return -1

        else:
            return 0



    def evaluate_seven_cards(self, cards):
        """
        评价七张牌。

        自动选择最强五张牌。

        输入：
        7张Card对象

        返回：
        最佳五张牌的评价结果
        """

        if len(cards) < 5:
            return None


        best_hand = None


        best_cards = None


        for five_cards in combinations(
            cards,
            5
        ):

            result = self.get_hand_rank(
                list(five_cards)
            )


            if best_hand is None:

                best_hand = result
                best_cards = five_cards


            else:

                compare = self.compare_hands(
                    result,
                    best_hand
                )


                if compare == 1:

                    best_hand = result
                    best_cards = five_cards



        # =====================
        # 最佳五张牌排序
        # =====================

        sorted_cards = sorted(
            list(best_cards),
            key=lambda card:
                self.get_rank_value(card.rank),
            reverse=True
        )


        best_hand["cards"] = sorted_cards


        return best_hand
