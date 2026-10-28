from pot import Pot
from pot import Pot
from deck import Deck
from hand_evaluator import HandEvaluator
from hand_compare import HandCompare


class Game:
    def __init__(self):
        self.deck = Deck()
        self.players = []
        self.hand_evaluator = HandEvaluator()
        self.hand_compare = HandCompare()
        self.pot = 0
        self.pots = []
        self.refunds = []
        self.current_bet = 0
        self.community_cards = []
        self.phase = "waiting"
        self.winners = []
        self.player_results = {}
        # 是否因其他人全部弃牌而直接收池（无摊牌）。
        # 这种情况下赢家不需要亮出底牌。
        self.won_by_fold = False
        self.game_log = []
        self.current_player_index = 0
        self._button_index = 0
        self._button_explicitly_set = False
        self.small_blind = 50
        self.big_blind = 100
        self.last_raise_amount = self.big_blind

    @property
    def button_index(self):
        return self._button_index

    @button_index.setter
    def button_index(self, value):
        self._button_index = value
        self._button_explicitly_set = True

    def add_log(self, message):
        self.game_log.append(message)

    def add_player(self, player):
        self.players.append(player)

    def get_current_player(self):
        if not self.players:
            return None

        # 下标是裸值，玩家被移除后可能越界，取模兜住
        return self.players[
            self.current_player_index % len(self.players)
        ]

    def has_chips(self, player):
        """
        玩家是否仍然拥有筹码且未出局。
        """
        return (
            player.chips > 0
            and not player.eliminated
        )

    def is_seated(self, player):
        """
        玩家是否仍然坐在这局游戏中。

        已经出局的玩家不算。

        本手牌中因为 All-in 而筹码为 0 的玩家
        仍然算作在座，否则盲注座位会在
        下盲之后发生变化。
        """

        if player.eliminated:
            return False

        if player.chips > 0:
            return True

        return (
            player.all_in
            or player.current_bet > 0
        )

    def get_active_players(self):
        """
        仍可以行动的玩家：
        未弃牌、未 All-in、未出局。
        """
        return [
            player
            for player in self.players
            if not player.folded
            and not player.all_in
            and not player.eliminated
        ]

    def get_remaining_players(self):
        """
        仍在牌局中的玩家：未弃牌、未出局。
        All-in 玩家虽然不能行动，但仍然在牌局中。
        """
        return [
            player
            for player in self.players
            if not player.folded
            and not player.eliminated
        ]

    def get_players_with_chips(self):
        """
        仍然拥有筹码、尚未出局的玩家。
        """
        return [
            player
            for player in self.players
            if player.chips > 0
            and not player.eliminated
        ]

    def is_game_over(self):
        """
        整场游戏是否结束。

        只剩一名玩家拥有筹码时，游戏结束。
        """
        return (
            len(self.get_players_with_chips()) <= 1
        )

    def get_game_winner(self):
        """
        整场游戏的冠军：
        唯一仍然拥有筹码的玩家。
        """
        players = self.get_players_with_chips()

        if len(players) != 1:
            return None

        return players[0]

    def get_ordered_active_indexes(self):
        """
        从 Button 开始，按座位顺序返回所有
        仍有筹码玩家的座位号。

        如果 Button 座位上的玩家已经出局，
        则从 Button 之后第一个未出局的座位开始。
        """

        if len(self.players) < 2:
            return []

        # button_index 是裸下标：玩家被移除后可能越界。
        # 不取模就会 IndexError，而调用方把它吞掉了，
        # 结果就是 SB / BB 两个标记凭空消失。
        start_index = self.button_index % len(self.players)

        if not self.is_seated(
            self.players[start_index]
        ):

            start_index = None

            for offset in range(
                1,
                len(self.players) + 1
            ):

                index = (
                    self.button_index + offset
                ) % len(self.players)

                if self.is_seated(
                    self.players[index]
                ):

                    start_index = index
                    break

            if start_index is None:
                return []

        indexes = []

        for offset in range(len(self.players)):

            index = (
                start_index + offset
            ) % len(self.players)

            if self.is_seated(
                self.players[index]
            ):

                indexes.append(index)

        return indexes

    def get_small_blind_index(self):
        """
        小盲座位。

        Heads-up：
            Button 同时承担小盲。

        三人及以上：
            Button 之后第一个仍有筹码的座位。
        """

        indexes = self.get_ordered_active_indexes()

        if len(indexes) < 2:
            return None

        if len(indexes) == 2:
            return indexes[0]

        return indexes[1]

    def get_big_blind_index(self):
        """
        大盲座位。

        Heads-up：
            Button 的对家承担大盲。

        三人及以上：
            小盲之后第一个仍有筹码的座位。
        """

        indexes = self.get_ordered_active_indexes()

        if len(indexes) < 2:
            return None

        if len(indexes) == 2:
            return indexes[1]

        return indexes[2]

    def get_next_player_index(self, current_index):
        if not self.players:
            return None

        return (current_index + 1) % len(self.players)

    def is_able_to_act(self, player):
        """
        玩家是否具备行动能力。

        已经弃牌、已经 All-in、已经出局，
        或者没有筹码的玩家都不能行动。
        """

        if (
            player.folded
            or player.all_in
            or player.eliminated
        ):
            return False

        return player.chips > 0

    def set_first_preflop_player(self):
        """
        确定 Preflop 第一个行动的玩家。

        Heads-up：
            Button（同时是小盲）先行动。

        三人及以上：
            大盲之后第一个可以行动的玩家。

        注意：
        如果小盲因为 All-in 而无法行动，
        则行动权交给下一个可以行动的玩家。
        """

        if len(self.players) < 2:
            return False

        big_blind_index = self.get_big_blind_index()

        if big_blind_index is None:
            return False

        if (
            len(self.get_ordered_active_indexes())
            == 2
        ):

            small_blind_index = (
                self.get_small_blind_index()
            )

            if small_blind_index is not None:

                small_blind_player = (
                    self.players[
                        small_blind_index
                    ]
                )

                if self.is_able_to_act(
                    small_blind_player
                ):

                    self.current_player_index = (
                        small_blind_index
                    )

                    return True

        for offset in range(1, len(self.players) + 1):

            index = (
                big_blind_index + offset
            ) % len(self.players)

            player = self.players[index]

            if self.is_able_to_act(player):

                self.current_player_index = index

                return True

        return False

    def set_first_postflop_player(self):
        if not self.players:
            return False

        for offset in range(1, len(self.players) + 1):

            index = (
                self.button_index + offset
            ) % len(self.players)

            player = self.players[index]

            if (
                not player.folded
                and not player.all_in
                and not player.eliminated
            ):
                self.current_player_index = index
                return True

        # 所有未弃牌玩家都 All-in。
        # 此时没有可以行动的玩家，但这不是错误，
        # 牌局应该继续发下一街公共牌。
        return True

    def advance_to_next_player(self):
        if not self.players:
            return False

        start_index = self.current_player_index

        while True:

            self.current_player_index = (
                self.current_player_index + 1
            ) % len(self.players)

            current_player = self.get_current_player()

            if (
                not current_player.folded
                and not current_player.all_in
                and not current_player.eliminated
            ):
                return True

            if self.current_player_index == start_index:
                return False

    def add_bet_to_pot(self, amount):
        if amount <= 0:
            return False

        self.pot += amount
        return True

    def post_blind(self, player, amount):
        """
        下盲注。

        如果筹码不足以支付完整盲注，
        则将剩余筹码全部投入，并自动进入 All-in。
        """

        if player is None:
            return False

        if player.eliminated:
            return False

        if player.chips <= 0:
            return False

        actual_amount = min(
            amount,
            player.chips
        )

        if actual_amount <= 0:
            return False

        if not player.bet(actual_amount):
            return False

        self.add_bet_to_pot(actual_amount)

        if actual_amount > self.current_bet:
            self.current_bet = actual_amount

        if player.chips == 0:
            player.all_in = True

        self.add_log(
            f"{player.name} posted blind "
            f"{actual_amount}"
        )

        return True

    def post_blinds(self):
        if len(self.players) < 2:
            return False

        small_blind_index = (
            self.get_small_blind_index()
        )

        big_blind_index = (
            self.get_big_blind_index()
        )

        if (
            small_blind_index is None
            or big_blind_index is None
        ):
            return False

        small_blind_player = self.players[
            small_blind_index
        ]

        big_blind_player = self.players[
            big_blind_index
        ]

        small_blind_success = self.post_blind(
            small_blind_player,
            self.small_blind,
        )

        big_blind_success = self.post_blind(
            big_blind_player,
            self.big_blind,
        )

        return (
            small_blind_success
            and big_blind_success
        )

    def get_call_amount(self, player):
        amount = (
            self.current_bet
            - player.current_bet
        )

        return max(amount, 0)

    def get_min_raise_amount(self):
        return self.last_raise_amount

    def can_player_bet(self, player, amount):

        if player != self.get_current_player():
            return False

        if player.folded:
            return False

        if player.all_in:
            return False

        if amount <= 0:
            return False

        if amount > player.chips:
            return False

        if self.current_bet > 0:
            return False

        return True

    def player_bet(self, player, amount):

        if not self.can_player_bet(player, amount):
            return False

        if not player.bet(amount):
            return False

        self.pot += amount

        self.current_bet = amount

        player.has_acted = True

        self.add_log(
            f"{player.name} BET {amount}"
        )

        return True

    def can_player_call(self, player):

        if player != self.get_current_player():
            return False

        if player.folded or player.all_in:
            return False

        call_amount = self.get_call_amount(player)

        if call_amount <= 0:
            return False

        if player.chips <= 0:
            return False

        return True

    def player_call(self, player):

        if not self.can_player_call(player):
            return False

        old_current_bet = player.current_bet

        if not player.call(self.current_bet):
            return False

        actual_amount = (
            player.current_bet
            - old_current_bet
        )

        self.pot += actual_amount

        player.has_acted = True

        self.add_log(
            f"{player.name} CALL {actual_amount}"
        )

        return True

    def can_player_check(self, player):

        if player != self.get_current_player():
            return False

        if player.folded or player.all_in:
            return False

        return (
            player.current_bet
            == self.current_bet
        )

    def player_check(self, player):

        if not self.can_player_check(player):
            return False

        player.has_acted = True

        self.add_log(
            f"{player.name} CHECK"
        )

        return True

    def can_player_fold(self, player):

        if player != self.get_current_player():
            return False

        if player.folded or player.all_in:
            return False

        return True

    def player_fold(self, player):

        if not self.can_player_fold(player):
            return False

        player.fold()
        player.has_acted = True

        self.add_log(
            f"{player.name} FOLD"
        )

        return True

    def can_player_all_in(self, player):

        if player != self.get_current_player():
            return False

        if player.folded or player.all_in:
            return False

        if player.chips <= 0:
            return False

        return True

    def player_all_in(self, player):

        if not self.can_player_all_in(player):
            return False

        old_current_bet = self.current_bet

        amount = player.all_in_bet()

        if amount is None or amount <= 0:
            return False

        self.pot += amount

        if player.current_bet > self.current_bet:

            raise_amount = (
                player.current_bet
                - old_current_bet
            )

            self.current_bet = (
                player.current_bet
            )

            if raise_amount >= self.last_raise_amount:
                self.last_raise_amount = raise_amount
                self.reset_players_after_raise(
                    player
                )

        player.has_acted = True

        self.add_log(
            f"{player.name} ALL IN"
        )

        return True

    def can_player_raise(self, player, new_bet):

        if player != self.get_current_player():
            return False

        if player.folded or player.all_in:
            return False

        if player.chips <= 0:
            return False

        if new_bet <= self.current_bet:
            return False

        if new_bet <= player.current_bet:
            return False

        raise_amount = (
            new_bet
            - self.current_bet
        )

        min_raise = self.get_min_raise_amount()

        if raise_amount < min_raise:
            return False

        additional_amount = (
            new_bet
            - player.current_bet
        )

        if additional_amount > player.chips:
            return False

        return True

    def player_raise(self, player, new_bet):

        if not self.can_player_raise(
            player,
            new_bet
        ):
            return False

        additional_amount = (
            new_bet
            - player.current_bet
        )

        if not player.bet(additional_amount):
            return False

        self.pot += additional_amount

        old_current_bet = self.current_bet

        self.current_bet = new_bet

        raise_amount = (
            new_bet
            - old_current_bet
        )

        if raise_amount >= self.last_raise_amount:
            self.last_raise_amount = raise_amount

        player.has_acted = True

        self.add_log(
            f"{player.name} RAISE {new_bet}"
        )

        self.reset_players_after_raise(player)

        return True

    def reset_players_after_raise(self, raiser):

        for player in self.players:

            if player == raiser:
                continue

            if player.folded or player.all_in:
                continue

            player.has_acted = False

    def reset_betting_round_for_all_players(self):
        """
        开始新的下注轮。

        current_bet 表示“本下注轮”的投入，
        而 player.total_bet 才记录整手牌的累计投入。
        因此进入 flop / turn / river 时，所有玩家的
        current_bet 都必须重新从 0 开始。
        """

        for player in self.players:
            player.reset_betting_round()
            player.current_bet = 0

        self.current_bet = 0

    def is_betting_round_complete(self):
        """
        判断当前下注轮是否结束。

        Fold 玩家不参与判断。
        All-in 玩家不能继续行动，但其已经投入的
        current_bet 仍然可以要求其他玩家跟注。

        特别注意：
        如果只剩 1 名未 All-in 玩家，那么要分两种情况：

        1. 他的 current_bet < self.current_bet：
           他还没有跟齐 All-in 玩家的投入，必须行动。

        2. 他的 current_bet == self.current_bet：
           他已经跟齐，本下注轮结束。

        如果进入 flop / turn / river 后，对手已经 All-in，
        且本下注轮的 current_bet 已重置为 0，那么此时
        不应该再要求唯一的未 All-in 玩家“check/call”；
        下注轮应直接结束，继续发公共牌。
        """

        remaining_players = [
            player
            for player in self.players
            if not player.folded
            and not player.eliminated
        ]

        if len(remaining_players) <= 1:
            return True

        actionable_players = [
            player
            for player in remaining_players
            if not player.all_in
        ]

        # 所有人都 All-in，已经没有人可以行动。
        if len(actionable_players) == 0:
            return True

        # 只剩一名可以行动的玩家时，
        # 他必须已经行动过，并且跟齐当前下注，
        # 本下注轮才算结束。
        #
        # 特别注意：
        # 大盲玩家即使已经跟齐，也仍然拥有
        # check / raise 的选项，
        # 因此必须检查 has_acted。
        if len(actionable_players) == 1:
            player = actionable_players[0]

            if not player.has_acted:
                return False

            if player.current_bet != self.current_bet:
                return False

            return True

        # 仍有至少两名可以行动的玩家。
        for player in actionable_players:

            if not player.has_acted:
                return False

            if player.current_bet != self.current_bet:
                return False

        return True

    def finish_player_action(self):

        if not self.players:
            return "failed"

        if self.is_betting_round_complete():
            return "round_complete"

        if self.advance_to_next_player():
            return "next_player"

        return "failed"

    def process_player_action(
        self,
        player,
        action,
        amount=None
    ):

        if player != self.get_current_player():
            return False

        success = False

        if action == "check":
            success = self.player_check(player)

        elif action == "call":
            success = self.player_call(player)

        elif action == "raise":

            if amount is None:
                return False

            success = self.player_raise(
                player,
                amount
            )

        elif action == "fold":
            success = self.player_fold(player)

        elif action == "all_in":
            success = self.player_all_in(player)

        else:
            return False

        return success

    def run_betting_round(self, action_provider):

        while True:

            current_player = (
                self.get_current_player()
            )

            if current_player is None:
                return False

            if self.is_betting_round_complete():
                return True

            action_data = action_provider(
                current_player
            )

            if action_data is None:
                return False

            action = action_data.get("action")
            amount = action_data.get("amount")

            if not self.process_player_action(
                current_player,
                action,
                amount,
            ):
                return False

            result = self.finish_player_action()

            if result == "round_complete":
                return True

            if result == "next_player":
                continue

            return False

    def deal_hole_cards(self):

        if not self.players:
            return False

        for _ in range(2):

            for player in self.players:

                # 已经出局的玩家不再发牌
                if player.eliminated:
                    continue

                card = self.deck.deal()

                if card is None:
                    return False

                player.receive_card(card)

        return True

    def deal_community_cards(self, amount):

        if amount <= 0:
            return False

        if (
            len(self.community_cards)
            + amount
            > 5
        ):
            return False

        if len(self.deck.cards) < amount:
            return False

        new_cards = []

        for _ in range(amount):

            card = self.deck.deal()

            if card is None:
                return False

            self.community_cards.append(card)
            new_cards.append(card)

        total_cards = len(
            self.community_cards
        )

        if total_cards == 3:

            self.phase = "flop"

            self.add_log(
                "Flop: "
                + " ".join(
                    str(card)
                    for card in new_cards
                )
            )

        elif total_cards == 4:

            self.phase = "turn"

            self.add_log(
                "Turn: "
                + str(new_cards[0])
            )

        elif total_cards == 5:

            self.phase = "river"

            self.add_log(
                "River: "
                + str(new_cards[0])
            )

        return True

    def advance_to_next_street(self):

        if self.phase == "showdown":
            return False

        if not self.is_betting_round_complete():
            return False

        if self.phase == "preflop":
            amount = 3

        elif self.phase == "flop":
            amount = 1

        elif self.phase == "turn":
            amount = 1

        elif self.phase == "river":
            self.phase = "showdown"
            return True

        else:
            return False

        self.reset_betting_round_for_all_players()

        if not self.deal_community_cards(amount):
            return False

        return self.set_first_postflop_player()

    def advance_button(self):
        """
        将 Button 顺时针移动到下一名仍有筹码的玩家。

        第一手牌开始时不调用这个方法。
        上一手牌结束后，下一次 start_hand() 会自动调用它。
        """

        if len(self.players) < 2:
            return False

        # 同样先取模，避免越界
        start_index = self.button_index % len(self.players)

        for offset in range(
            1,
            len(self.players) + 1
        ):

            next_index = (
                start_index + offset
            ) % len(self.players)

            player = self.players[next_index]

            if player.chips > 0:
                self.button_index = next_index
                return True

        return False

    def mark_eliminated_players(self):
        """
        筹码为 0 的玩家正式出局。

        出局玩家不会再被发牌，
        也不会再参与后续盲注和行动。
        """

        for player in self.players:

            if player.chips <= 0:

                player.eliminated = True

    def start_hand(self, deck=None):
        """
        开始新的一手牌。

        deck:
            可选的固定牌堆，用于测试或复盘。
            不传则自动创建并洗牌。
        """

        # 上一手牌结束后筹码为 0 的玩家正式出局。
        self.mark_eliminated_players()

        # 从真正开始一手牌起，Button 已经是确定的。
        # 如果外部没有指定，则使用默认的 0 号座位。
        if not self._button_explicitly_set:
            self._button_index = 0
            self._button_explicitly_set = True

        self.game_log = []

        self.add_log(
            "Game started"
        )

        if len(self.get_players_with_chips()) < 2:
            return False

        if deck is not None:
            self.deck = deck
        else:
            self.deck = Deck()
            self.deck.shuffle()

        self.pot = 0
        self.pots = []
        self.refunds = []
        self.current_bet = 0
        self.last_raise_amount = self.big_blind
        self.community_cards = []
        self.phase = "preflop"
        self.winners = []
        self.player_results = {}
        self.won_by_fold = False

        for player in self.players:

            if self.has_chips(player):

                player.reset_for_new_hand()

        if not self.deal_hole_cards():
            return False

        if not self.post_blinds():
            return False

        return self.set_first_preflop_player()

    def evaluate_players(self, players=None):

        if players is None:
            players = self.get_remaining_players()

        results = {}

        for player in players:

            if player.folded:
                continue

            if player.eliminated:
                continue

            cards = (
                player.hand
                + self.community_cards
            )

            result = (
                self.hand_evaluator
                .evaluate_seven_cards(cards)
            )

            results[player] = result

        return results

    def determine_winner(self, players=None):

        if players is None:
            players = self.players

        results = self.evaluate_players(players)

        if not results:
            return []

        winners = []
        best_hand = None

        for player, hand in results.items():

            if best_hand is None:
                best_hand = hand
                winners = [player]
                continue

            comparison = (
                self.hand_compare.compare(
                    hand,
                    best_hand
                )
            )

            if comparison > 0:

                best_hand = hand
                winners = [player]

            elif comparison == 0:

                winners.append(player)

        return winners

    def check_for_single_winner(self):

        remaining_players = (
            self.get_remaining_players()
        )

        if len(remaining_players) == 1:
            return remaining_players[0]

        return None

    def build_side_pots(self):
        """
        根据整手牌的累计下注建立 Main Pot / Side Pot。

        重要规则：

        1. 所有下注过的玩家都必须计入 Pot 金额。
           即使玩家已经弃牌，其已经投入的筹码也不会消失。

        2. 弃牌玩家不能成为 Pot 的赢家，
           因此不会加入 pot.players，
           但他们投入的筹码仍然记录在
           pot.contributors 中，
           并且已经计入 pot.amount。

        3. 无人跟注的部分（某一层只有一个贡献者）
           会记录为 pot.refund_to，
           在分配时原路退回。
        """

        self.pots = []
        self.refunds = []

        # 所有下注过的玩家都参与底池金额计算。
        # 这里不能排除 folded 玩家。
        contributors = [
            player
            for player in self.players
            if player.total_bet > 0
            and not player.eliminated
        ]

        if not contributors:
            return []

        levels = sorted(
            set(
                player.total_bet
                for player in contributors
            )
        )

        previous = 0

        for level in levels:

            contribution = (
                level - previous
            )

            if contribution <= 0:
                continue

            # 这一层真正贡献筹码的玩家。
            # folded 玩家也包含在这里。
            level_contributors = [
                player
                for player in contributors
                if player.total_bet >= level
            ]

            pot_amount = (
                contribution
                * len(level_contributors)
            )

            pot = Pot()
            pot.amount = pot_amount

            # pot.contributors 记录这一层投入过筹码的
            # 所有玩家，包括已经弃牌的玩家。
            for player in level_contributors:
                pot.add_contributor(player)

            # 这一层只有一个贡献者，
            # 说明没有人跟注到这一层，
            # 这部分筹码需要原路退回。
            #
            # 注意：
            # 这里不直接修改玩家筹码，
            # 而是把退款记录成一个特殊底池，
            # 保证 build_side_pots() 可以被重复调用。
            if len(level_contributors) == 1:

                pot.refund_to = (
                    level_contributors[0]
                )

                self.refunds.append(pot)

            else:

                # 只有未弃牌的玩家具备赢家资格。
                for player in level_contributors:

                    if not player.folded:

                        pot.add_player(player)

                self.pots.append(pot)

            previous = level

        return self.pots

    def get_odd_chip_order(self, winners):
        """
        根据 Button 位置确定余数筹码的分配顺序。

        余数筹码从 Button 左侧的第一位座位开始，
        按照顺时针座位顺序逐个分配。

        例如：

            Alice(Button)
            Bob
            Charlie
            Dave

        如果 Bob、Charlie、Dave 是赢家，
        则余数分配顺序为：

            Bob -> Charlie -> Dave

        注意：
        winners 本身的列表顺序不影响余数分配顺序。
        """

        if not winners or not self.players:
            return []

        # 如果没有明确建立 Button 上下文，
        # 说明这是一个独立的底池分配测试。
        # 此时保留 winners 本身的顺序。
        if not self._button_explicitly_set:
            return list(winners)

        ordered_winners = []

        for offset in range(
            1,
            len(self.players) + 1
        ):

            index = (
                self.button_index
                + offset
            ) % len(self.players)

            player = self.players[index]

            if player in winners:
                ordered_winners.append(player)

        # 正常情况下 winners 中的所有玩家
        # 都应该存在于 self.players。
        #
        # 如果出现异常输入，仍然保留这些玩家，
        # 避免余数筹码因为找不到座位而丢失。
        for player in winners:

            if player not in ordered_winners:
                ordered_winners.append(player)

        return ordered_winners

    def distribute_pot(self, winners=None):
        """
        将当前底池分配给赢家。

        返回赢家列表，便于调用方展示结果。
        """

        if winners is None:
            winners = self.determine_winner()

        if not winners:
            return []

        if self.pot <= 0:
            return winners

        share = (
            self.pot
            // len(winners)
        )

        remainder = (
            self.pot
            % len(winners)
        )

        # 先给每个赢家相同的基础份额。
        for player in winners:
            player.chips += share

        # 再逐个分配余数。
        odd_chip_order = self.get_odd_chip_order(
            winners
        )

        for index in range(remainder):
            odd_chip_order[index].chips += 1

        self.pot = 0

        return winners

    def distribute_pots(self):
        """
        分配所有 Main Pot / Side Pot。

        pot.players 是具备赢家资格的玩家。
        无人跟注的退款底池由 pot.refund_to 指定。

        所有底池的赢家都会记录到 self.winners。
        """

        if not self.pots and not self.refunds:
            return False

        all_winners = []

        # 无人跟注的部分先原路退回。
        for pot in self.refunds:

            pot.refund_to.chips += pot.amount

            self.add_log(
                f"{pot.refund_to.name} 退回未被跟注的 "
                f"{pot.amount}"
            )

        for pot in self.pots:

            eligible_players = [
                player
                for player in pot.players
                if not player.folded
                and not player.eliminated
            ]

            # 极端情况：
            # 这一层的所有贡献者都已经弃牌。
            #
            # 弃牌等于放弃争夺底池的权利，
            # 因此这笔筹码不能退回给他们，
            # 而是交给仍然在牌局中的玩家争夺。
            if not eligible_players:

                eligible_players = (
                    self.get_remaining_players()
                )

            # 兜底：
            # 如果连牌局中都没有玩家了，
            # 才原路退回给贡献者，
            # 保证筹码不会丢失。
            if not eligible_players:

                eligible_players = [
                    player
                    for player in pot.contributors
                    if not player.eliminated
                ]

            winners = self.determine_winner(
                eligible_players
            )

            if not winners:
                continue

            for player in winners:

                if player not in all_winners:

                    all_winners.append(player)

            share = (
                pot.amount
                // len(winners)
            )

            remainder = (
                pot.amount
                % len(winners)
            )

            # 先平均分配。
            for player in winners:
                player.chips += share

            # 再逐个分配余数。
            #
            # 余数不是全部给 winners[0]，
            # 而是按照 Button 左侧开始的座位顺序，
            # 每个赢家依次获得 1 个。
            odd_chip_order = self.get_odd_chip_order(
                winners
            )

            for index in range(remainder):
                odd_chip_order[index].chips += 1

            self.add_log(
                f"Pot {pot.amount} 由 "
                + "、".join(
                    player.name
                    for player in winners
                )
                + " 获得"
            )

        self.winners = all_winners

        # 主池和边池已经全部发放完成，
        # 当前牌局的总底池也必须清零。
        self.pots = []
        self.refunds = []
        self.pot = 0

        return True

    def settle_showdown(self):

        # 摊牌时先记录所有仍在牌局中玩家的牌型，
        # 供界面展示。
        self.player_results = (
            self.evaluate_players()
        )

        # 摊牌时必须根据整手牌的累计下注建立主池和边池。
        # 每个底池再根据该底池具备资格的玩家分别判断赢家。
        self.build_side_pots()

        if not self.pots and not self.refunds:

            winners = self.determine_winner()

            if not winners:
                return False

            self.winners = winners

            return bool(
                self.distribute_pot(winners)
            )

        return self.distribute_pots()

    def play_hand(self, action_provider, deck=None):

        # 整场游戏已经结束，不能再开始新的手牌。
        if self.is_game_over():
            return False

        if not self.start_hand(deck=deck):
            return False

        if not self.run_betting_round(
            action_provider
        ):
            return False

        for _ in range(3):

            if not self.advance_to_next_street():
                return False

            if self.phase == "showdown":
                break

            if not self.run_betting_round(
                action_provider
            ):
                return False

        winner = self.check_for_single_winner()

        if winner is not None:

            # 其他玩家全部弃牌，
            # 唯一剩余的玩家直接获得整个底池。
            # 这是“不摊牌直接收池”，赢家无需亮底牌。
            winner.chips += self.pot
            self.pot = 0
            self.pots = []
            self.winners = [winner]
            self.phase = "showdown"
            self.won_by_fold = True

            # 不评估、不记录任何玩家的底牌，
            # 界面上就不会向所有人展示赢家的手牌。
            self.player_results = {}

            self.add_log(
                f"{winner.name} 获得底池"
            )

            self.mark_eliminated_players()

            self.advance_button()

            return True

        self.phase = "showdown"
        self.won_by_fold = False

        success = self.settle_showdown()

        if success:

            self.mark_eliminated_players()

            self.advance_button()

        return success

    def show_players(self):

        for player in self.players:
            print(player)

    def show_blind_info(self):

        if len(self.players) < 2:
            print(
                "玩家数量不足，无法显示盲注信息。"
            )
            return

        small_blind_player = self.players[
            self.get_small_blind_index()
        ]

        big_blind_player = self.players[
            self.get_big_blind_index()
        ]

        print(
            f"小盲注："
            f"{small_blind_player.name}"
            f"（{self.small_blind}筹码）"
        )

        print(
            f"大盲注："
            f"{big_blind_player.name}"
            f"（{self.big_blind}筹码）"
        )

    def show_current_player(self):

        current_player = (
            self.get_current_player()
        )

        if current_player is None:
            print("当前没有行动玩家。")
            return

        print(
            f"当前行动玩家："
            f"{current_player.name}"
        )

    def show_game_state(self):

        print(
            f"游戏阶段：{self.phase}"
        )

        print(
            f"底池：{self.pot}"
        )

        print(
            f"当前最高下注："
            f"{self.current_bet}"
        )

        print(
            f"公共牌："
            f"{self.community_cards}"
        )

        self.show_current_player()
