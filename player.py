class Player:
    def __init__(self, name, chips=1000):
        self.name = name
        self.chips = chips
        self.hand = []

        # 当前下注轮投入
        self.current_bet = 0

        # 整手牌累计投入
        self.total_bet = 0

        # 玩家状态
        self.folded = False
        self.has_acted = False
        self.can_raise = True

        # 是否全押
        self.all_in = False

        # 是否正式出局
        self.eliminated = False


    def receive_card(self, card):
        """
        接收一张手牌。
        """
        self.hand.append(card)



    def bet(self, amount):
        """
        普通下注。

        如果下注后筹码恰好为 0，
        自动进入 all-in 状态。
        """

        if amount <= 0:
            return False

        if amount > self.chips:
            return False

        self.chips -= amount

        self.current_bet += amount

        self.total_bet += amount

        # 只要筹码全部投入，就属于 All-in。
        if self.chips == 0:
            self.all_in = True

        return True



    def all_in_bet(self):
        """
        将剩余全部筹码投入。
        """

        if self.chips <= 0:
            return False

        amount = self.chips

        self.chips = 0

        self.current_bet += amount

        self.total_bet += amount

        self.all_in = True

        return amount



    def call(self, current_bet):
        """
        跟注。

        如果筹码不足以完成跟注，
        自动将剩余筹码全部投入，形成 All-in。
        """

        amount = (
            current_bet
            - self.current_bet
        )

        if amount <= 0:
            return False

        if amount > self.chips:
            self.all_in_bet()
            return True

        return self.bet(amount)



    def raise_bet(self, new_bet):
        """
        加注到指定下注额。
        """

        amount = (
            new_bet
            - self.current_bet
        )

        if amount <= 0:
            return False

        return self.bet(amount)



    def fold(self):
        """
        弃牌。
        """
        self.folded = True



    def reset_betting_round(self):
        """
        重置下注轮状态。
        """

        self.current_bet = 0

        self.has_acted = False

        self.can_raise = True



    def reset_for_new_hand(self):
        """
        开始新一手牌时重置。
        """

        self.hand = []

        self.current_bet = 0

        self.total_bet = 0

        self.folded = False

        self.has_acted = False

        self.can_raise = True

        self.all_in = False



    def __str__(self):
        """
        玩家显示信息。
        """

        return (
            f"{self.name}：{self.chips}筹码，"
            f"手牌：{self.hand}"
        )
