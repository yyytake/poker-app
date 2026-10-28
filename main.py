from game import Game
from player import Player



# ==============================
# 创建游戏
# ==============================

game = Game()



players = [
    Player("Alice"),
    Player("Bob"),
    Player("Charlie"),
    Player("David")
]


for player in players:
    game.add_player(player)



# ==============================
# 自动行动策略
# ==============================

def action_provider(player):

    # ==========================
    # Preflop 测试策略
    # ==========================

    if game.phase == "preflop":

        # David 第一次行动，加注
        if player.name == "David":
            if not player.has_acted:
                return {
                    "action": "raise",
                    "amount": 300
                }


        # Alice 跟注
        if player.name == "Alice":
            return {
                "action": "call"
            }


        # Bob 弃牌
        if player.name == "Bob":
            return {
                "action": "fold"
            }


        # Charlie 跟注
        if player.name == "Charlie":
            return {
                "action": "call"
            }



    # ==========================
    # 后续街道全部 check
    # ==========================

    if game.can_player_check(player):

        return {
            "action": "check"
        }


    if game.can_player_call(player):

        return {
            "action": "call"
        }


    return {
        "action": "fold"
    }


# ==============================
# 开始游戏
# ==============================

print("==============================")
print("===== 德州扑克开始 =====")
print("==============================")
print()


print("玩家:")


for player in game.players:

    print(
        player.name,
        "筹码:",
        player.chips
    )


print()



# ==============================
# 运行牌局
# ==============================

success = game.play_hand(action_provider)



if not success:

    print("牌局运行失败")

    print(
        "当前阶段:",
        game.phase
    )

    print(
        "当前玩家:",
        game.get_current_player()
    )

    exit()



# ==============================
# 牌局结束
# ==============================

print("==============================")
print("===== 牌局结束 =====")
print("==============================")
print()



print(
    "游戏阶段:",
    game.phase
)


print()



print("公共牌:")


for card in game.community_cards:

    print(
        card,
        end=" "
    )


print()
print()



# ==============================
# 玩家状态
# ==============================

print("玩家最终状态:")


for player in game.players:

    print(player)



print()



print(
    "底池:",
    game.pot
)


print()



# ==============================
# 玩家牌型
# ==============================

print("==============================")
print("玩家牌型:")
print("==============================")


for player, result in game.player_results.items():

    print()

    print(
        player.name,
        ":",
        result["name"]
    )


    print(
        "最佳五张:"
    )


    for card in result["cards"]:

        print(
            card,
            end=" "
        )


    print()



print()



# ==============================
# 赢家
# ==============================

print("==============================")
print("赢家:")
print("==============================")


for winner in game.winners:

    result = game.player_results[winner]


    print()

    print(
        winner.name
    )


    print(
        "牌型:",
        result["name"]
    )


    print(
        "最佳五张:"
    )


    for card in result["cards"]:

        print(
            card,
            end=" "
        )


    print()

print()


# ==============================
# 牌局日志
# ==============================

print("==============================")
print("牌局日志:")
print("==============================")


for log in game.game_log:

    print(log)
