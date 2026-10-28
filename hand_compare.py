class HandCompare:

    def compare(self, hand1, hand2):
        """
        比较两手牌。

        返回：

        1:
            hand1胜

        0:
            平局

        -1:
            hand2胜
        """

        # 先比较牌型等级

        if hand1["rank"] > hand2["rank"]:

            return 1


        elif hand1["rank"] < hand2["rank"]:

            return -1



        # 牌型相同
        # 比较value

        value1 = hand1["value"]

        value2 = hand2["value"]


        if value1 > value2:

            return 1


        elif value1 < value2:

            return -1


        else:

            return 0
