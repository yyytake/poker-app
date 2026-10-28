class Pot:

    def __init__(self):
        # 底池金额
        self.amount = 0

        # 有资格争夺这个底池的玩家（未弃牌）
        self.players = []

        # 为这个底池投入过筹码的所有玩家
        # 包含已经弃牌的贡献者
        self.contributors = []

        # 无人跟注、需要原路退回的玩家
        self.refund_to = None

    def add_amount(self, amount):
        if amount <= 0:
            return False

        self.amount += amount
        return True

    def add_player(self, player):
        """
        添加有资格争夺这个底池的玩家。
        """
        if player not in self.players:
            self.players.append(player)

    def add_contributor(self, player):
        """
        添加为这个底池投入过筹码的玩家。
        """
        if player not in self.contributors:
            self.contributors.append(player)

    def can_win(self, player):
        return (
            player in self.players
            and not player.folded
        )

    def __str__(self):

        names = [
            player.name
            for player in self.players
        ]

        contributor_names = [
            player.name
            for player in self.contributors
        ]

        return (
            f"Pot({self.amount}), "
            f"Players:{names}, "
            f"Contributors:{contributor_names}"
        )
