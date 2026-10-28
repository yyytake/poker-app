/* 德州扑克联机前端
 *
 * 通过 SSE 接收牌桌状态，
 * 通过 POST /api/action 提交行动。
 */

const PHASE_NAMES = {
    waiting: "等待中",
    preflop: "翻牌前",
    flop: "翻牌",
    turn: "转牌",
    river: "河牌",
    showdown: "摊牌",
};

const app = {
    token: null,
    room: null,
    name: null,
    state: null,
    source: null,
    wasMyTurn: false,
    ledgerOpen: false,
    // 牌桌聊天：面板是否展开、已经读到的时间点
    chatOpen: false,
    chatSeenAt: 0,
    // 打字消息的气泡：已弹到哪个时间点、是否已对账
    chatBubbleAt: 0,
    chatBubbleReady: false,
    // 牌堆当前落在牌桌上的位置（百分比）
    deckSpot: null,
    // 特效播放期间的时间戳，这期间不去动牌堆位置
    effectBusyUntil: 0,
    // 上一次渲染出的 HTML，用来避免无谓的 DOM 重建
    lastSeatsHtml: null,
    lastCommunityHtml: null,
    lastMyCardsHtml: null,
    lastChatHtml: null,
    // 快捷语音：已经播放过的事件、按钮清单是否要重建
    seenQuick: {},
    quickPrimed: false,
    quickListKey: null,
    // 互动道具：当前选中的目标、已经播过的事件
    presentTarget: null,
    seenPresents: {},
    presentPrimed: false,
    // 表情：格子是否展开、已经飘过的事件、是否对过账
    emojiOpen: false,
    emojiListKey: null,
    seenEmojis: {},
    emojiPrimed: false,
    // 弹幕：对账到第几手、日志读到第几条、是否已对账、正在滚动的条数
    danmuHand: null,
    danmuSeen: 0,
    danmuPrimed: false,
    danmuRunning: 0,
    // 音效：已经响过结算音的手牌号、已经播过音的座位
    soundResultKey: null,
    deadSeats: {},
    readySeats: {},
    // 初始页面房间列表上一次渲染的内容，
    // 用来避免每几秒刷新时无谓地重建
    lastRoomsHtml: null,
    // 声音设置面板开没开
    soundOpen: false,
};

/* ==============================
   工具
   ============================== */

function $(id) {
    return document.getElementById(id);
}

function saveSession() {
    localStorage.setItem(
        "poker_token", app.token || ""
    );
    localStorage.setItem(
        "poker_room", app.room || ""
    );
    localStorage.setItem(
        "poker_name", app.name || ""
    );
}

function clearSession() {
    localStorage.removeItem("poker_token");
    localStorage.removeItem("poker_room");
    localStorage.removeItem("poker_name");
}

// 统一的后端调用。
//
// 关键：无论服务端返回什么（JSON、500 页面、还是连接直接断掉），
// 都要给调用方一个 {ok:false, error}。
// 以前这里是 response.json() 且没有 catch，服务端一出问题
// 就变成「点了没反应」——连个提示都没有。
function api(path, body) {
    return fetch(path, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body || {}),
    })
        .then(function (response) {
            return response.text().then(function (text) {
                try {
                    return JSON.parse(text);
                } catch (error) {
                    return {
                        ok: false,
                        error:
                            "服务器响应异常（" +
                            response.status +
                            "），请稍后重试",
                    };
                }
            });
        })
        .catch(function () {
            return {
                ok: false,
                error: "网络连接失败，请检查网络后重试",
            };
        });
}

// 立刻拉一次最新状态。
// 操作被拒（多半是本地状态过期）时调用，
// 让界面马上跟上服务端。
function refreshState() {
    if (!app.token) return;

    fetch(
        "/api/state?token=" +
            encodeURIComponent(app.token)
    )
        .then(function (response) {
            return response.json();
        })
        .then(function (data) {
            if (data.ok) {
                render(data.state);
            }
        })
        .catch(function () {});
}

// 牌面照 PokerCard 的规格：点数在上、花色紧贴其下，都靠左上角
function cardHtml(card) {
    if (!card) return "";

    return (
        '<div class="card' +
        (card.red ? " red" : "") +
        '">' +
        '<span class="card-rank">' +
        escapeHtml(card.rank) +
        "</span>" +
        '<span class="card-suit">' +
        escapeHtml(card.suit) +
        "</span>" +
        "</div>"
    );
}

// 筹码数字加上千分位，更好读
function formatChips(value) {
    const n = Number(value);

    if (!isFinite(n)) return String(value);

    return n.toLocaleString("en-US");
}

// 聊天内容是玩家自己敲的，渲染前必须转义
function escapeHtml(value) {
    return String(
        value === null || value === undefined
            ? ""
            : value
    )
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function hiddenCardHtml() {
    return '<div class="card back">?</div>';
}

function buzz() {
    if (navigator.vibrate) {
        navigator.vibrate([120, 60, 120]);
    }
}

/* ==============================
   加入房间
   ============================== */

function showJoin() {
    $("joinScreen").classList.remove("hidden");
    $("gameScreen").classList.add("hidden");

    // 离开牌桌后作废渲染缓存，免得回来时内容「看起来没变」而不重建
    app.lastSeatsHtml = null;
    app.lastCommunityHtml = null;
    app.lastMyCardsHtml = null;
    app.lastChatHtml = null;

    // 重新进桌也要重新对账，否则会把上次的历史消息
    // 当成新消息再弹一遍气泡
    app.chatBubbleReady = false;
    app.chatBubbleAt = 0;

    // 名单缓存作废，让列表立刻重建一次
    app.lastRoomsHtml = null;

    loadRooms();
}

function showGame() {
    $("joinScreen").classList.add("hidden");
    $("gameScreen").classList.remove("hidden");

    preloadPresentAssets();
    showPresentHint();
}

// 只在第一次进桌时提示一次，免得没人知道名片能点
function showPresentHint() {
    let seen = null;

    try {
        seen = localStorage.getItem("poker_present_hint");
    } catch (error) {
        seen = "done";
    }

    if (seen) return;

    try {
        localStorage.setItem("poker_present_hint", "done");
    } catch (error) {
        // 存不了就算了，最多多提示几次
    }

    const tip = document.createElement("div");
    tip.className = "present-hint";
    tip.textContent =
        "点一下（或长按）别人的名片，可以送花 / 扔鸡蛋";
    document.body.appendChild(tip);

    setTimeout(function () {
        tip.remove();
    }, 6500);
}

// 一个玩家的胶囊：座位号 + 名字 + 状态
function rosterChipHtml(person, index) {
    let text = "在线";
    let cls = "on";

    if (!person.online) {
        text = "离线";
        cls = "off";
    } else if (person.out) {
        text = "出局";
        cls = "out";
    } else if (person.waiting) {
        text = "等位";
        cls = "wait";
    }

    return (
        '<span class="roster-chip ' +
        cls +
        '">' +
        '<span class="roster-seat">' +
        (index + 1) +
        "</span>" +
        escapeHtml(person.name) +
        '<span class="roster-state">' +
        text +
        "</span>" +
        "</span>"
    );
}

function roomItemHtml(room) {
    let mine = null;

    try {
        mine = localStorage.getItem("poker_room");
    } catch (error) {
        mine = null;
    }

    const roster = (room.names || [])
        .map(rosterChipHtml)
        .join("");

    return (
        '<div class="room-item' +
        (mine && String(room.code) === String(mine)
            ? " mine"
            : "") +
        '" data-code="' +
        room.code +
        '">' +
        '<div class="room-head">' +
        '<span class="room-code-name">房间 ' +
        room.code +
        "</span>" +
        '<span class="room-online">在线 ' +
        (room.online || 0) +
        "/" +
        (room.players || 0) +
        "</span>" +
        "</div>" +
        '<div class="room-meta">第 ' +
        room.hand_no +
        " 手" +
        (room.running ? " · 正在打" : " · 等人齐") +
        "</div>" +
        (roster
            ? '<div class="room-roster">' + roster + "</div>"
            : "") +
        "</div>"
    );
}

function loadRooms() {
    fetch("/api/rooms")
        .then(function (r) {
            return r.json();
        })
        .then(function (data) {
            const rooms = data.rooms || [];

            const html = rooms.length
                ? rooms.map(roomItemHtml).join("")
                : '<div class="room-empty">' +
                  "暂无房间，创建一个吧</div>";

            // 内容没变就不重建：这个列表每隔几秒自动刷新，
            // 重建会把正在点的那一项换掉，点着就空了
            if (app.lastRoomsHtml === html) return;

            app.lastRoomsHtml = html;

            $("roomList").innerHTML = html;

            document
                .querySelectorAll(".room-item")
                .forEach(function (item) {
                    item.onclick = function () {
                        $("roomInput").value =
                            item.dataset.code;
                    };
                });
        })
        .catch(function () {
            app.lastRoomsHtml = null;
            $("roomList").textContent = "加载失败";
        });
}

function join() {
    const name =
        $("nameInput").value.trim() || "玩家";

    const room = $("roomInput").value.trim();

    const body = {
        name: name,
        room: room || null,
        start_chips: parseInt(
            $("chipsInput").value,
            10
        ) || 1000,
        small_blind: parseInt(
            $("sbInput").value,
            10
        ) || 10,
    };

    const bb = parseInt($("bbInput").value, 10);

    if (bb) body.big_blind = bb;

    $("joinError").textContent = "";
    $("joinButton").disabled = true;

    api("/api/join", body)
        .then(function (data) {
            $("joinButton").disabled = false;

            if (!data.ok) {
                $("joinError").textContent =
                    data.error || "进入房间失败";
                return;
            }

            app.token = data.token;
            app.room = data.room;
            app.name = name;

            saveSession();

            showGame();
            openStream();
        })
        .catch(function () {
            $("joinButton").disabled = false;
            $("joinError").textContent =
                "连不上服务器";
        });
}

function tryResume() {
    const token = localStorage.getItem(
        "poker_token"
    );

    if (!token) {
        showJoin();
        return;
    }

    fetch("/api/state?token=" + token)
        .then(function (r) {
            if (!r.ok) throw new Error("gone");
            return r.json();
        })
        .then(function (data) {
            if (!data.ok) throw new Error("gone");

            app.token = token;
            app.name =
                localStorage.getItem("poker_name");

            showGame();
            openStream();
        })
        .catch(function () {
            clearSession();
            showJoin();
        });
}

/* ==============================
   SSE + 轮询兜底
   ============================== */

let pollTimer = null;
let gotSseData = false;
// 最后一次收到 SSE 推送的时间，
// 用来判断实时连接是不是“假活”
let lastSseAt = 0;

function startPolling() {
    if (pollTimer) return;

    pollTimer = setInterval(function () {
        fetch(
            "/api/state?token=" +
                encodeURIComponent(app.token)
        )
            .then(function (r) {
                return r.json();
            })
            .then(function (data) {
                if (data.ok) {
                    $("connState").classList.remove(
                        "off"
                    );
                    render(data.state);
                }
            })
            .catch(function () {
                $("connState").classList.add("off");
            });
    }, 2000);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

function openStream() {
    if (app.source) {
        app.source.close();
    }

    gotSseData = false;
    lastSseAt = Date.now();

    const source = new EventSource(
        "/api/events?token=" +
            encodeURIComponent(app.token)
    );

    app.source = source;

    source.onopen = function () {
        $("connState").classList.remove("off");
    };

    source.onerror = function () {
        $("connState").classList.add("off");
        // 实时推送不可用时，用轮询兜底
        startPolling();
    };

    source.onmessage = function (event) {
        $("connState").classList.remove("off");

        let state;

        try {
            state = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        gotSseData = true;
        lastSseAt = Date.now();
        stopPolling();

        render(state);
    };

    // 有些网络/代理会缓冲 SSE，
    // 如果迟迟收不到第一条推送，
    // 就切换到轮询模式。
    setTimeout(function () {
        if (!gotSseData) {
            startPolling();
        }
    }, 5000);
}

/* ==============================
   渲染
   ============================== */

/* ==============================
   渲染容错
   ============================== */

// 某一块渲染出错，不能把整个界面拖死。
//
// 以前 render() 是一条直链，任何一块抛异常，后面的都不执行；
// 而状态推送很频繁，每次都会在同一行再抛一次，
// 界面就永久停在旧样子上 —— 表现就是「报个错，之后再也开不了牌」。
// 最常见的原因是页面和脚本版本对不上（缓存里是旧首页、脚本是新版），
// 新脚本去操作旧页面里不存在的元素。
let renderErrorShown = false;

function safeRender(name, task) {
    try {
        task();
    } catch (error) {
        if (!renderErrorShown) {
            renderErrorShown = true;

            showRefreshHint(
                "界面加载不完整（" +
                    name +
                    "出错），点这里刷新"
            );
        }

        if (window.console && console.error) {
            console.error("[render:" + name + "]", error);
        }
    }
}

// 页面和脚本版本不一致时，唯一可靠的修法就是重新加载
function showRefreshHint(text) {
    const el = $("message");

    if (!el) return;

    el.textContent = "⚠ " + text;
    el.classList.add("message-warn");
    el.onclick = function () {
        location.reload();
    };
}

function render(state) {
    app.state = state;

    safeRender("房间信息", function () {
        $("roomCode").textContent = state.room;

        // 盲注已经标在各自名片上了，顶栏只留手数。
        // 不然手机宽度下会被截断成「第 1 手 · 10…」
        $("handInfo").textContent =
            "第 " + state.hand_no + " 手";

        $("handInfo").title =
            "盲注 " +
            state.small_blind +
            "/" +
            state.big_blind;
    });

    safeRender("公牌", function () {
        // 公共牌（没变化就不重建）
        const communityHtml = (state.community || [])
            .map(cardHtml)
            .join("");

        if (app.lastCommunityHtml !== communityHtml) {
            app.lastCommunityHtml = communityHtml;
            $("communityCards").innerHTML = communityHtml;
        }

        $("potInfo").textContent =
            "底池 " + formatChips(state.pot);

        $("phaseInfo").textContent =
            PHASE_NAMES[state.phase] || state.phase;
    });

    safeRender("座位", function () {
        renderSeats(state);
    });

    safeRender("我的底牌", function () {
        renderMyCards(state);
    });

    safeRender("操作按钮", function () {
        renderActions(state);
    });

    safeRender("控制栏", function () {
        renderControls(state);
    });

    safeRender("结算", function () {
        renderResult(state);
    });

    safeRender("聊天", function () {
        renderChat(state);
        renderChatBubbles(state);
    });

    safeRender("快捷语", function () {
        renderQuick(state);
    });

    safeRender("道具", function () {
        renderPresents(state);
    });

    safeRender("表情", function () {
        renderEmojis(state);
        renderEmojiGrid(state);
    });

    // 弹幕与音效：排在发牌动画之前，好让开局音先响
    safeRender("播报", function () {
        renderBroadcast(state);
    });

    safeRender("声音按钮", function () {
        paintSoundButton();
    });

    safeRender("托管", function () {
        renderAuto(state);
    });

    safeRender("道具花费", function () {
        renderPresentCosts(state);
    });

    // 都放在最后：先给牌堆找个空位，再放发牌动画
    safeRender("发牌动画", function () {
        placeDeck();
        animateDeal(state);
        updateTimer();
    });

    safeRender("提示与日志", function () {
        // 出错提示优先：这时候别被普通提示盖掉
        if (!renderErrorShown) {
            $("message").textContent = state.message || "";
        }

        $("logList").innerHTML = (state.log || [])
            .map(function (line) {
                return "<div>" + line + "</div>";
            })
            .join("");
    });

    // 轮到我时震动提醒
    safeRender("震动", function () {
        if (state.your_turn && !app.wasMyTurn) {
            buzz();
        }

        app.wasMyTurn = state.your_turn;
    });
}

function renderSeats(state) {
    const n = state.seats.length;

    const html = state.seats
        .map(function (seat, index) {
            const classes = ["seat"];

            if (seat.is_you) classes.push("me");
            if (seat.folded) classes.push("folded");
            if (state.acting_seat === index) {
                classes.push("acting");
            }
            if (seat.eliminated) classes.push("eliminated");

            let badges = "";

            // 盲注标记写在庄家标记前面，顺序固定为好认：
            // SB / BB / D
            if (seat.is_sb) {
                badges +=
                    '<span class="badge sb" title="小盲">SB</span>';
            }

            if (seat.is_bb) {
                badges +=
                    '<span class="badge bb" title="大盲">BB</span>';
            }

            if (index === state.button_index) {
                badges +=
                    '<span class="badge dealer" title="庄家">D</span>';
            }

            if (seat.all_in) {
                badges +=
                    '<span class="badge allin">ALL IN</span>';
            }

            if (seat.ready && !state.hand_running) {
                badges += '<span class="badge ready">✓</span>';
            }

            // 谁在托管，让同桌都看得见
            if (seat.auto > 0) {
                badges +=
                    '<span class="badge auto">托管</span>';
            }

            if (seat.eliminated) {
                badges +=
                    '<span class="badge out">出局</span>';
            }

            if (!seat.connected) {
                badges +=
                    '<span class="badge offline">离线</span>';
            }

            // 底牌：
            // 自己的底牌在牌桌下方大字显示，这里不重复；
            // 别人的底牌盖着，摊牌亮出后才显示点数。
            let cards = "";

            if (
                !seat.is_you &&
                seat.cards &&
                seat.cards.length
            ) {
                cards =
                    '<div class="seat-cards">' +
                    seat.cards.map(cardHtml).join("") +
                    "</div>";
            } else if (
                !seat.is_you &&
                state.hand_running &&
                !seat.eliminated &&
                !seat.waiting
            ) {
                cards =
                    '<div class="seat-cards">' +
                    hiddenCardHtml() +
                    hiddenCardHtml() +
                    "</div>";
            }

            // 本轮下注与成手牌名合并成一行，节省高度
            const infoParts = [];

            if (seat.bet > 0) {
                infoParts.push(
                    "本轮 " + formatChips(seat.bet)
                );
            }

            if (seat.hand_name) {
                infoParts.push(escapeHtml(seat.hand_name));
            }

            const infoLine = infoParts.length
                ? '<div class="seat-bet">' +
                  infoParts.join(" · ") +
                  "</div>"
                : "";

            const netClass =
                seat.net > 0
                    ? "delta-up"
                    : seat.net < 0
                    ? "delta-down"
                    : "";

            const netText =
                seat.net > 0
                    ? "+" + seat.net
                    : String(seat.net);

            const net =
                seat.net !== 0
                    ? '<span class="seat-net ' +
                      netClass +
                      '">' +
                      netText +
                      "</span>"
                    : "";

            return (
                '<div class="' +
                classes.join(" ") +
                '">' +
                cards +
                '<div class="seat-plate">' +
                '<div class="seat-name">' +
                '<span class="seat-label">' +
                seat.name +
                "</span>" +
                badges +
                "</div>" +
                '<div class="seat-chips">' +
                formatChips(seat.chips) +
                net +
                "</div>" +
                infoLine +
                "</div>" +
                "</div>"
            );
        })
        .join("");

    const container = $("seats");
    container.innerHTML = html;

    // 人多时整体缩小，避免互相压住
    container.classList.toggle("many", n >= 7);

    // 围绕牌桌呈环形排布，公牌留在中央；
    // 并且把自己那一家固定在正下方。
    const nodes = container.children;
    const step = 360 / Math.max(n, 1);

    let meIndex = -1;

    for (let i = 0; i < state.seats.length; i++) {
        if (state.seats[i].is_you) {
            meIndex = i;
            break;
        }
    }

    const base =
        meIndex >= 0 ? 90 - meIndex * step : 90;

    // 横屏时牌桌又宽又扁，环半径要单独调
    const landscape = isLandscapeUi();

    // 环形半径。
    //
    // 关键：公牌是固定像素大小（5 张共 146px），不随牌桌缩放，
    // 所以在窄屏手机上它占的比例远大于平板 —— 512 宽时只占 31%，
    // 390 宽时要占 42%，于是压到左右两侧的座位。
    //
    // 光把座位往外推是没用的（它们本来就贴着桌边了）。
    // 真正管用的是把环拉得「更竖长」：两侧座位会被推到偏上 /
    // 偏下的位置，纵向正好让开公牌那一条横带。
    const rx = landscape
        ? (n >= 7 ? 44 : 43)
        : (n >= 7 ? 36 : 40);

    const ry = landscape
        ? (n >= 7 ? 32 : 30)
        : 42;

    for (let i = 0; i < nodes.length; i++) {
        const angle =
            (base + i * step) * Math.PI / 180;

        const x = 50 + rx * Math.cos(angle);
        const y = 50 + ry * Math.sin(angle);

        nodes[i].style.left = x + "%";
        nodes[i].style.top = y + "%";
    }
}

function renderMyCards(state) {
    const me = state.seats.find(function (s) {
        return s.is_you;
    });

    const html =
        me && me.cards && me.cards.length
            ? me.cards.map(cardHtml).join("")
            : "";

    if (app.lastMyCardsHtml !== html) {
        app.lastMyCardsHtml = html;
        $("myCards").innerHTML = html;
    }
}

function setButton(id, enabled, text) {
    const button = $(id);

    button.disabled = !enabled;

    if (text !== undefined) {
        button.textContent = text;
    }
}

function renderActions(state) {
    const bar = $("actionBar");

    if (!state.your_turn || !state.legal) {
        bar.classList.add("hidden");
        $("raisePanel").classList.add("hidden");
        return;
    }

    bar.classList.remove("hidden");

    const legal = state.legal;

    const has = function (name) {
        return legal.actions.indexOf(name) >= 0;
    };

    setButton("foldButton", has("fold"));
    setButton("checkButton", has("check"));
    setButton(
        "callButton",
        has("call"),
        "跟注 " + legal.call_amount
    );
    setButton("raiseButton", has("raise"));
    setButton(
        "allInButton",
        has("all_in"),
        "ALL IN " + legal.chips
    );
}

function renderControls(state) {
    const control = $("controlBar");
    const waiting = $("waitingBar");

    const me = state.seats.find(function (s) {
        return s.is_you;
    });

    if (state.hand_running) {
        control.classList.add("hidden");
        $("buyInPanel").classList.add("hidden");
        waiting.classList.remove("hidden");

        waiting.textContent = state.your_turn
            ? "轮到你行动"
            : "等待 " +
              (state.acting_name || "其他玩家") +
              " 行动…";

        return;
    }

    waiting.classList.add("hidden");
    control.classList.remove("hidden");

    // 两手之间随时可以买入，
    // 输光的人买入后可以继续打。
    $("buyInButton").classList.remove("hidden");

    if (state.game_over) {
        $("nextHandButton").classList.add("hidden");
        $("readyButton").classList.add("hidden");
        waiting.classList.remove("hidden");
        waiting.textContent =
            "本局结束，冠军是 " +
            (state.champion || "？") +
            "！输光的人可以买入继续。";
        return;
    }

    // 第一手和之后每一手都一样：全部准备好后
    // 由系统自动发牌，不再由某个玩家控制开始按钮。
    $("nextHandButton").classList.add("hidden");

    // state.result 为 null 表示这一场还没开过牌。
    const isFirstHand = !state.result;

    const meEligible =
        me && !me.eliminated && me.chips > 0;

    if (meEligible) {
        $("readyButton").classList.remove("hidden");

        if (me.ready) {
            $("readyButton").textContent = "已准备 ✓";
            $("readyButton").classList.add("ready-done");
        } else {
            $("readyButton").textContent =
                isFirstHand ? "准备开始" : "准备下一手";
            $("readyButton").classList.remove(
                "ready-done"
            );
        }
    } else {
        $("readyButton").classList.add("hidden");
    }

    waiting.classList.remove("hidden");

    const everyoneReady =
        state.next_eligible > 0 &&
        state.next_ready >= state.next_eligible;

    if (me && (me.eliminated || me.chips === 0)) {
        waiting.textContent =
            "你已输光，点【买入】继续打下一手";
    } else if (everyoneReady) {
        waiting.textContent = "人已到齐，马上发牌…";
    } else {
        waiting.textContent =
            "已准备 " +
            state.next_ready +
            "/" +
            state.next_eligible +
            "，凑齐后自动发牌";
    }

    if (app.ledgerOpen) {
        loadLedger();
    }
}

/* ==============================
   长期账本
   ============================== */

function loadLedger() {
    fetch("/api/ledger")
        .then(function (r) {
            return r.json();
        })
        .then(function (data) {
            if (!data.ok) return;

            const rows = data.players || [];

            if (!rows.length) {
                $("ledgerTable").innerHTML =
                    '<div class="ledger-empty">暂无记录</div>';
                $("ledgerBuyins").innerHTML = "";
                return;
            }

            let html =
                '<div class="ledger-row head">' +
                "<span>玩家</span>" +
                "<span>筹码</span>" +
                "<span>买入</span>" +
                "<span>盈亏</span>" +
                "<span>手数</span>" +
                "</div>";

            rows.forEach(function (p) {
                const cls =
                    p.net > 0
                        ? "delta-up"
                        : p.net < 0
                        ? "delta-down"
                        : "";

                const loan = p.loan_total
                    ? " (贷" + p.loan_total + ")"
                    : "";

                const netText =
                    p.net > 0
                        ? "+" + p.net
                        : String(p.net);

                html +=
                    '<div class="ledger-row">' +
                    "<span>" +
                    p.name +
                    "</span>" +
                    "<span>" +
                    p.chips +
                    "</span>" +
                    "<span>" +
                    p.buy_in_total +
                    loan +
                    "</span>" +
                    '<span class="' +
                    cls +
                    '">' +
                    netText +
                    "</span>" +
                    "<span>" +
                    p.hands +
                    "</span>" +
                    "</div>";
            });

            $("ledgerTable").innerHTML = html;

            const list = (data.buyins || [])
                .slice(-8)
                .reverse()
                .map(function (b) {
                    // 负数就是归还，单独写出来
                    const what =
                        b.amount < 0
                            ? " 归还 " + -b.amount
                            : " 买入 " + b.amount;

                    return (
                        "<div>" +
                        b.time +
                        " · " +
                        b.name +
                        what +
                        (b.loan ? "（贷款）" : "") +
                        "</div>"
                    );
                })
                .join("");

            $("ledgerBuyins").innerHTML =
                '<div class="ledger-title small">' +
                "最近买入 / 归还</div>" +
                list;
        })
        .catch(function () {});
}

function renderResult(state) {
    const panel = $("resultPanel");

    if (!state.result) {
        panel.classList.add("hidden");
        return;
    }

    const result = state.result;

    // 结算音效：同一手只响一次
    const resultKey = String(result.hand_no);

    if (app.soundResultKey !== resultKey) {
        app.soundResultKey = resultKey;

        const meSeat = (state.seats || []).filter(
            function (s) {
                return s.is_you;
            }
        )[0];

        // 结果里没有 is_you，用名字回查
        const mine = meSeat
            ? (result.players || []).filter(function (p) {
                  return p.name === meSeat.name;
              })[0]
            : null;

        // 结果里找不到自己（中途离开等）就不出声
        if (mine) {
            playSfx(mine.delta > 0 ? "win" : "lose");
        }
    }

    const winners = (result.winners || [])
        .map(function (w) {
            return (
                w.name +
                (w.hand_name
                    ? "（" + w.hand_name + "）"
                    : "")
            );
        })
        .join("、");

    let html =
        '<div class="result-title">第 ' +
        result.hand_no +
        " 手结果 · " +
        (winners || "无人") +
        "</div>";

    html += result.players
        .map(function (p) {
            const deltaClass =
                p.delta > 0
                    ? "delta-up"
                    : p.delta < 0
                    ? "delta-down"
                    : "";

            const deltaText =
                p.delta > 0
                    ? "+" + p.delta
                    : String(p.delta);

            let cards;

            if (p.folded) {
                cards = "已弃牌";
            } else if (
                result.won_by_fold &&
                p.cards === null
            ) {
                // 其他人全部弃牌收池：赢家无需亮牌
                cards = "未亮牌";
            } else if (p.cards) {
                cards = p.cards
                    .map(cardHtml)
                    .join("");
            } else {
                cards = "";
            }

            const netClass =
                p.net > 0
                    ? "delta-up"
                    : p.net < 0
                    ? "delta-down"
                    : "";

            const netText =
                p.net > 0
                    ? "净+" + p.net
                    : "净" + p.net;

            return (
                '<div class="result-row">' +
                "<span>" +
                p.name +
                " " +
                cards +
                (p.hand_name
                    ? " " + p.hand_name
                    : "") +
                "</span>" +
                '<span class="' +
                deltaClass +
                '">' +
                deltaText +
                ' <span class="' +
                netClass +
                '">' +
                netText +
                "</span></span>" +
                "</div>"
            );
        })
        .join("");

    panel.innerHTML = html;
    panel.classList.remove("hidden");
}

/* ==============================
   操作
   ============================== */

function act(action, amount) {
    const body = {
        token: app.token,
        action: action,
    };

    if (amount !== undefined) {
        body.amount = amount;
    }

    $("raisePanel").classList.add("hidden");

    api("/api/action", body).then(function (data) {
        if (!data.ok) {
            alert(data.error || "操作失败");
            refreshState();
        }
    });
}

/* ==============================
   加注面板：关键注额标记 + 数值输入
   ============================== */

function clampNum(lo, hi, v) {
    return Math.max(lo, Math.min(hi, v));
}

function setRaiseValue(v) {
    const slider = $("raiseSlider");
    const lo = parseInt(slider.min, 10);
    const hi = parseInt(slider.max, 10);

    v = parseInt(v, 10);

    if (isNaN(v)) v = lo;

    v = clampNum(lo, hi, v);

    slider.value = v;
    $("raiseValue").textContent = v;
    $("raiseInput").value = v;
}

function openRaisePanel() {
    const legal = app.state.legal;

    if (!legal) return;

    const panel = $("raisePanel");
    const slider = $("raiseSlider");
    const st = app.state;

    const min = legal.raise_min;
    const max = legal.raise_max;
    const pot = st.pot || 0;
    const current = st.current_bet || 0;

    slider.min = min;
    slider.max = max;
    slider.step = 1;

    // 关键注额（均为“加注到”的总额）
    const presets = [
        { key: "min", label: "最小", value: min },
        {
            key: "half",
            label: "½池",
            value: clampNum(
                min,
                max,
                current + Math.round(pot / 2)
            ),
        },
        {
            key: "pot",
            label: "满池",
            value: clampNum(
                min,
                max,
                current + pot
            ),
        },
        { key: "max", label: "全下", value: max },
    ];

    // 在拖动条下方标注关键注额。
    // 底池小的时候「最小 / ½池 / 满池」会挤在同一个位置，
    // 所以太近的刻度直接跳过，并且首尾自动贴边对齐。
    const ticksEl = $("raiseTicks");
    const span = max - min;
    const ticks = [];
    let lastPos = -999;

    presets.forEach(function (p) {
        let pos = span > 0
            ? ((p.value - min) / span) * 100
            : 0;

        pos = Math.max(2, Math.min(98, pos));

        if (pos - lastPos < 16) return;

        lastPos = pos;

        let align = "";

        if (pos < 10) {
            align = " start";
        } else if (pos > 90) {
            align = " end";
        }

        ticks.push(
            '<span class="raise-tick' +
            align +
            '" style="left:' +
            pos +
            '%">' +
            p.label +
            "</span>"
        );
    });

    ticksEl.innerHTML = ticks.join("");

    // 快捷按钮：点击直接跳到该注额
    const quick = document.querySelectorAll(
        ".raise-quick button"
    );

    quick.forEach(function (b) {
        const key = b.dataset.preset;
        const p = presets.find(function (x) {
            return x.key === key;
        });

        if (!p) return;

        b.onclick = function () {
            setRaiseValue(p.value);
        };
    });

    setRaiseValue(min);
    panel.classList.remove("hidden");
}

/* ==============================
   思考时间倒计时
   ============================== */

function updateTimer() {
    const el = $("turnTimer");
    const st = app.state;

    if (!st || !st.action_deadline) {
        el.classList.add("hidden");
        return;
    }

    const remaining =
        st.action_deadline - Date.now() / 1000;

    if (remaining <= 0) {
        el.classList.add("hidden");
        return;
    }

    el.classList.remove("hidden");

    const total = st.timeout || 100;
    const pct = clampNum(
        0,
        100,
        (remaining / total) * 100
    );
    const secs = Math.ceil(remaining);

    el.innerHTML =
        '<span class="turn-timer-num">剩余 ' +
        secs +
        "s</span>" +
        '<div class="turn-timer-bar">' +
        '<div class="turn-timer-fill" style="width:' +
        pct +
        '%"></div></div>';

    if (secs <= 10) {
        el.classList.add("low");
    } else {
        el.classList.remove("low");
    }
}

/* ==============================
   发牌动画
   ============================== */

// 只有「第一手的第一张牌」这种肉眼可见的场景才值得动，
// 记录上一次的状态用来判断哪些牌是新出现的
const dealTracker = {
    primed: false,
    handNo: null,
    community: 0,
};

function prefersReducedMotion() {
    return (
        window.matchMedia &&
        window.matchMedia(
            "(prefers-reduced-motion: reduce)"
        ).matches
    );
}

/* 牌堆摆在哪儿：
   座位、公牌、底池、街牌标签的位置随人数一直在变，
   所以每次渲染都找一个不跟它们相撞的空位。 */

function deckBlockers() {
    const list = [];

    function push(rect) {
        if (rect && rect.width > 0 && rect.height > 0) {
            list.push(rect);
        }
    }

    document.querySelectorAll(".seat").forEach(
        function (seat) {
            push(seat.getBoundingClientRect());
        }
    );

    document.querySelectorAll(".community .card").forEach(
        function (card) {
            push(card.getBoundingClientRect());
        }
    );

    // 底池是个药丸，直接按它整个外形避让
    const pot = $("potInfo");

    if (pot) {
        push(pot.getBoundingClientRect());
    }

    // 街牌标签是整行的 div，只有文字本身算数，
    // 否则中间一大片都会被误判成占位
    const phase = $("phaseInfo");

    if (phase && phase.textContent) {
        const range = document.createRange();
        range.selectNodeContents(phase);
        push(range.getBoundingClientRect());
    }

    return list;
}

// 从「公牌上方居中」开始，由中心向外找空位
function deckCandidates() {
    const list = [];

    for (let y = 16; y <= 72; y += 5) {
        for (let x = 18; x <= 82; x += 6) {
            list.push([
                x,
                y,
                Math.abs(x - 50) +
                    Math.abs(y - 26) * 1.4,
            ]);
        }
    }

    list.sort(function (a, b) {
        return a[2] - b[2];
    });

    return list;
}

function placeDeck() {
    const deck = $("deck");
    const table = document.querySelector(".table");

    if (!deck || !table) return;

    // 特效正在飞的时候先不动牌堆：重新算位置要读十几个元素的位置，
    // 会强制页面重排，正好把飞出去的动画卡一下。
    // 只推迟、不永久跳过，下次刷新照样会重算。
    if (app.effectBusyUntil > Date.now()) return;

    const box = table.getBoundingClientRect();

    if (!box.width) return;

    const width = deck.offsetWidth || 26;
    const height = deck.offsetHeight || 36;
    const blockers = deckBlockers();

    function isFree(spot) {
        const left =
            box.left + box.width * spot[0] / 100 - width / 2;
        const top =
            box.top + box.height * spot[1] / 100 - height / 2;

        for (let i = 0; i < blockers.length; i++) {
            const b = blockers[i];

            if (
                left < b.right + 2 &&
                left + width > b.left - 2 &&
                top < b.bottom + 2 &&
                top + height > b.top - 2
            ) {
                return false;
            }
        }

        return true;
    }

    // 现在的位置还空着就别动，免得每次刷新都跳
    if (app.deckSpot && isFree(app.deckSpot)) {
        return;
    }

    const spots = deckCandidates();

    for (let i = 0; i < spots.length; i++) {
        if (isFree(spots[i])) {
            app.deckSpot = spots[i];
            deck.style.left = spots[i][0] + "%";
            deck.style.top = spots[i][1] + "%";
            deck.classList.remove("hidden");
            return;
        }
    }

    // 实在挤不下就不显示，也好过压在别人名片上
    app.deckSpot = null;
    deck.classList.add("hidden");
}

// 让一张牌从牌堆飞到它自己的位置上
function flyFromDeck(element, delay) {
    const deck = $("deck");

    if (!deck || !element || !element.animate) return;

    const box = element.getBoundingClientRect();
    const from = deck.getBoundingClientRect();

    // 人多时座位里的底牌会被隐藏，那就没有飞行的必要
    if (!box.width || !box.height) return;

    const dx =
        from.left + from.width / 2 -
        (box.left + box.width / 2);
    const dy =
        from.top + from.height / 2 -
        (box.top + box.height / 2);

    element.animate(
        [
            {
                transform:
                    "translate(" + dx + "px," + dy + "px) " +
                    "rotate(-18deg) scale(.72)",
                opacity: 0,
            },
            {
                transform:
                    "translate(" + dx * .45 + "px," +
                    dy * .45 + "px) rotate(-8deg) scale(.88)",
                opacity: 1,
                offset: .55,
            },
            {
                transform:
                    "translate(0,0) rotate(0deg) scale(1)",
                opacity: 1,
            },
        ],
        {
            duration: 360,
            delay: delay,
            easing: "cubic-bezier(.2,.85,.3,1)",
            // backwards：等待期间先停在牌堆上，看起来才像真发牌
            fill: "backwards",
        }
    );
}

// 发牌时牌堆抖一下
function pulseDeck() {
    const deck = $("deck");

    if (!deck) return;

    deck.classList.remove("shuffle");

    // 读一次宽度强制重排，保证动画能重复播放
    void deck.offsetWidth;

    deck.classList.add("shuffle");

    setTimeout(function () {
        deck.classList.remove("shuffle");
    }, 560);
}

// 底牌：绕桌发两轮，跟真人发牌一个顺序
function dealHoleCards(state) {
    const nodes = $("seats").children;
    const mine = $("myCards")
        .querySelectorAll(".card");

    const rounds = [[], []];

    state.seats.forEach(function (seat, index) {
        if (seat.is_you) return;
        if (seat.eliminated || seat.waiting) return;
        if (seat.left) return;

        const node = nodes[index];

        if (!node) return;

        const cards = node.querySelectorAll(
            ".seat-cards .card"
        );

        if (cards[0]) rounds[0].push(cards[0]);
        if (cards[1]) rounds[1].push(cards[1]);
    });

    if (mine[0]) rounds[0].push(mine[0]);
    if (mine[1]) rounds[1].push(mine[1]);

    rounds[0].concat(rounds[1]).forEach(
        function (element, index) {
            flyFromDeck(element, 120 + index * 90);
        }
    );
}

function animateDeal(state) {
    const screen = $("gameScreen");

    if (!screen || screen.classList.contains("hidden")) {
        return;
    }

    const handNo = state.hand_no;
    const live = (state.community || []).length;
    const newHand = handNo !== dealTracker.handNo;

    // 进桌后拿到的第一份状态只是「对账」，不放动画
    if (!dealTracker.primed) {
        dealTracker.primed = true;
        dealTracker.handNo = handNo;
        dealTracker.community = live;
        return;
    }

    // 切到后台、或者系统开了「减少动态效果」就不折腾
    if (document.hidden || prefersReducedMotion()) {
        dealTracker.handNo = handNo;
        dealTracker.community = live;
        return;
    }

    if (newHand) {
        dealTracker.handNo = handNo;
        dealTracker.community = 0;
    }

    if (newHand && state.hand_running) {
        dealHoleCards(state);
        pulseDeck();
        playSfx("draw");
    }

    // 公牌：翻牌 3 张、转牌 1 张、河牌 1 张
    if (live > dealTracker.community) {
        const cards = $("communityCards")
            .querySelectorAll(".card");
        const added = live - dealTracker.community;

        for (let i = 0; i < added; i++) {
            flyFromDeck(
                cards[dealTracker.community + i],
                i * 110
            );
        }

        pulseDeck();
        playSfx("chain");
    }

    dealTracker.community = live;
}

/* ==============================
   互动道具：长按名片送花 / 扔鸡蛋
   ============================== */

const PRESENT_ICON = {
    flower: "/static/anim/flower.png",
    wine: "/static/anim/wine.png",
    egg: "/static/anim/egg.png",
    shoe: "/static/anim/shoe.png",
};

const PRESENT_SOUND = {
    flower: ["flower1", "flower2"],
    wine: ["wine1", "wine2"],
    egg: ["egg1", "egg2"],
    shoe: ["shoe1", "shoe2"],
};

const PRESENT_NAME = {
    flower: "送花",
    wine: "送酒",
    egg: "扔鸡蛋",
    shoe: "扔拖鞋",
};

// 复用一个 Audio 实例，播的时候不用等加载
function preloadAudio(path) {
    if (!path || quickAudio[path]) return;

    const audio = new Audio();
    audio.preload = "auto";
    audio.src = path;

    quickAudio[path] = audio;
}

// 进桌预热，但只拉「马上要用」的那几个：
// 一次性发几十个请求会很吃流量、也抢带宽，
// 其余素材交给 playQuickSound 首次播放时按需加载并缓存。
function preloadPresentAssets() {
    // 道具图标必须预热，否则第一次扔东西时图片还在下载，
    // 屏幕上就是「隐形道具」，看起来像动画没播
    Object.keys(PRESENT_ICON).forEach(function (key) {
        const img = new Image();
        img.src = PRESENT_ICON[key];
    });

    [
        "fly1", "fly2",
        "flower1", "flower2",
        "wine1", "wine2",
        "egg1", "egg2",
        "shoe1", "shoe2",
    ].forEach(function (name) {
        preloadAudio(voicePath(name));
    });

    // 每手都要响的：开局 / 准备 / 发牌 / 我赢 / 我输
    ["gamestart", "ready", "draw", "win", "lose"].forEach(
        function (name) {
            preloadAudio(SFX[name]);
        }
    );
}

// 长按判定
let pressTimer = null;
let pressOrigin = null;
let pressSeat = -1;

function pickOne(list) {
    return list[Math.floor(Math.random() * list.length)];
}

function voicePath(name) {
    return "/static/voice/" + name + ".mp3";
}

function mySeatIndex() {
    const seats = (app.state && app.state.seats) || [];

    for (let i = 0; i < seats.length; i++) {
        if (seats[i].is_you) return i;
    }

    return -1;
}

// 道具飞到目标身上后的表现
function landPresent(event, layer, x, y) {
    const sounds = PRESENT_SOUND[event.item] || ["egg1"];

    playQuickSound(voicePath(pickOne(sounds)));

    const icon = PRESENT_ICON[event.item] || PRESENT_ICON.egg;

    const img = document.createElement("img");
    img.className = "hit-item";
    img.src = icon;
    img.style.left = x + "px";
    img.style.top = y + "px";
    layer.appendChild(img);

    if (img.animate) {
        const animation = img.animate(
            [
                {
                    transform: "translate(-50%,-50%) scale(.9)",
                    opacity: 1,
                },
                {
                    transform:
                        "translate(-50%,-50%) scale(1.7)",
                    opacity: 1,
                    offset: .35,
                },
                {
                    transform:
                        "translate(-50%,-50%) scale(2) rotate(14deg)",
                    opacity: 0,
                },
            ],
            { duration: 850, easing: "ease-out" }
        );

        animation.onfinish = function () {
            img.remove();
        };
    }

    setTimeout(function () {
        img.remove();
    }, 1500);

    shakeSeat(event.to, event.item);
}

// 被砸的人抖一下
function shakeSeat(index, item) {
    const seat = $("seats").children[index];

    if (!seat) return;

    const plate = seat.querySelector(".seat-plate");

    if (!plate || !plate.animate) return;

    // 花和酒是心意，晃得轻一点
    const soft = item === "flower" || item === "wine";
    const amount = soft ? 3 : 6;

    plate.animate(
        [
            { transform: "translateX(0) rotate(0deg)" },
            {
                transform:
                    "translateX(-" + amount + "px) rotate(-2deg)",
            },
            {
                transform:
                    "translateX(" + amount + "px) rotate(2deg)",
            },
            { transform: "translateX(-" + amount / 2 + "px)" },
            { transform: "translateX(0) rotate(0deg)" },
        ],
        { duration: 400, easing: "ease-in-out" }
    );
}

// 把一个道具从 from 座位扔到 to 座位
function throwPresent(event) {
    const layer = $("effects");
    const seats = $("seats").children;
    const from = seats[event.from];
    const to = seats[event.to];

    if (!layer || !from || !to) return;

    const layerBox = layer.getBoundingClientRect();
    const fromBox = from.getBoundingClientRect();
    const toBox = to.getBoundingClientRect();

    const startX =
        fromBox.left + fromBox.width / 2 - layerBox.left;
    const startY = fromBox.top - layerBox.top;

    const endX =
        toBox.left + toBox.width / 2 - layerBox.left;
    const endY =
        toBox.top + toBox.height / 2 - layerBox.top;

    // 拖鞋和鸡蛋是一串飞过去，花和酒是一个
    const count =
        event.item === "shoe" ? 3 : event.item === "egg" ? 2 : 1;

    // 出手时的破空声
    playQuickSound(voicePath(pickOne(["fly1", "fly2"])));

    // 这段时间里不重算牌堆位置，免得重排把飞行卡顿
    app.effectBusyUntil = Date.now() + 2400;

    for (let i = 0; i < count; i++) {
        launchOne({
            layer: layer,
            icon: PRESENT_ICON[event.item] || PRESENT_ICON.egg,
            x: startX,
            y: startY,
            dx: endX - startX,
            dy: endY - startY,
            delay: i * 110,
            spin:
                event.item === "shoe" || event.item === "wine"
                    ? 540
                    : 0,
            onLand:
                i === count - 1
                    ? function () {
                          landPresent(event, layer, endX, endY);
                      }
                    : null,
        });
    }
}

function launchOne(opts) {
    const img = document.createElement("img");
    img.className = "flying-item";
    img.src = opts.icon;
    img.style.left = opts.x + "px";
    img.style.top = opts.y + "px";
    // 动画开始前先藏着，免得在起点闪一下
    img.style.opacity = "0";

    opts.layer.appendChild(img);

    let launched = false;

    function go() {
        if (launched) return;
        launched = true;
        startFlight(img, opts);
    }

    // 图没解码完就飞，画面上会出现「看不见的道具」——
    // 尤其网络差时第一次扔几乎看不到。等它解码好再开始，
    // 但最多等 300ms，不能因为网络把动画卡住。
    if (img.decode) {
        img.decode().then(go).catch(go);
    } else {
        img.onload = go;
        img.onerror = go;
    }

    setTimeout(go, 300);
}

function startFlight(img, opts) {
    // 飞得久一点：太快的话手机上一眨眼就没了
    const duration = 950;

    if (!img.animate) {
        img.remove();

        if (opts.onLand) opts.onLand();

        return;
    }

    const animation = img.animate(
        [
            {
                transform:
                    "translate(-50%,-50%) scale(.4) rotate(0deg)",
                opacity: 0,
            },
            {
                transform:
                    "translate(-50%,-50%) scale(1) rotate(0deg)",
                opacity: 1,
                offset: .1,
            },
            {
                // 中途抬高一点，像真的抛物线
                transform:
                    "translate(calc(-50% + " +
                    (opts.dx * .5) + "px), calc(-50% + " +
                    (opts.dy * .5 - 30) + "px)) " +
                    "scale(1.06) rotate(" +
                    (opts.spin * .5) + "deg)",
                opacity: 1,
                offset: .55,
            },
            {
                transform:
                    "translate(calc(-50% + " + opts.dx +
                    "px), calc(-50% + " + opts.dy + "px)) " +
                    "scale(.9) rotate(" + opts.spin + "deg)",
                opacity: 1,
            },
        ],
        {
            duration: duration,
            delay: opts.delay,
            // 出手利落、接近时变慢
            easing: "cubic-bezier(.3,.1,.5,1)",
            fill: "backwards",
        }
    );

    let landed = false;

    function finish() {
        if (landed) return;

        landed = true;

        img.remove();

        if (opts.onLand) opts.onLand();
    }

    // 用 finished 比 onfinish 更可靠（动画被打断也能收尾）
    if (animation.finished) {
        animation.finished.then(finish).catch(finish);
    } else {
        animation.onfinish = finish;
    }

    // 兜底：切后台、卡顿等导致动画没走到头，也要把元素清掉
    setTimeout(finish, duration + opts.delay + 1600);
}

function renderPresents(state) {
    (state.presents || []).forEach(function (event) {
        if (app.seenPresents[event.key]) return;

        app.seenPresents[event.key] = true;

        // 刚进桌不补放历史
        if (!app.presentPrimed) return;

        // 网络差时事件可能晚到好几秒，窗口放宽一些，
        // 别让互动因为一个卡顿就整个没了
        if (Date.now() / 1000 - event.at > 60) return;

        throwPresent(event);
    });

    app.presentPrimed = true;
}

// 点一下名片：开；再点同一个：收
function togglePresent(index) {
    const open = !$("presentPanel")
        .classList.contains("hidden");

    if (open && app.presentTarget === index) {
        closePresent();
        return;
    }

    openPresent(index);
}

// 被送过的四种道具，用图标 + 次数展示
const PRESENT_LABEL = {
    flower: "🌸",
    wine: "🍷",
    egg: "🥚",
    shoe: "👞",
};

// 这个人一共被送过多少
function renderPresentStats(seat) {
    const box = $("presentStats");

    if (!box) return;

    const got = (seat && seat.presents_in) || {};

    box.innerHTML =
        '<span class="present-stats-title">被送过</span>' +
        Object.keys(PRESENT_LABEL)
            .map(function (item) {
                return (
                    '<span class="present-stat">' +
                    PRESENT_LABEL[item] +
                    "<b>" +
                    (got[item] || 0) +
                    "</b></span>"
                );
            })
            .join("");
}

// 每种道具的筹码收支标在按钮上：
// 按钮上只写自己要花多少，明细放提示里
function renderPresentCosts(state) {
    const table =
        (state && state.present_chips) || {};

    document
        .querySelectorAll(".present-cost")
        .forEach(function (el) {
            const item = el.getAttribute("data-cost");
            const info = table[item];

            if (!info) {
                el.textContent = "";
                return;
            }

            el.textContent = "-" + info.cost;

            el.parentNode.title =
                "你要花 " + info.cost +
                " 个筹码，对方 " +
                (info.gain > 0
                    ? "得到 " + info.gain
                    : "损失 " + Math.abs(info.gain)) +
                " 个";
        });
}

function openPresent(index) {
    const seats = (app.state && app.state.seats) || [];
    const seat = seats[index];

    if (!seat) return;

    // 自己的名片也能看统计，只是不能再给自己送东西
    const mine = !!seat.is_you;

    app.presentTarget = mine ? null : index;

    $("presentTitle").textContent = mine
        ? "我 · 剩 " + formatChips(seat.chips)
        : "对 " + seat.name + " · " +
          (seat.eliminated || seat.chips === 0
              ? "已出局"
              : "剩 " + formatChips(seat.chips));

    // 看自己的时候把四个按钮收起来，只留统计
    $("presentList").classList.toggle("hidden", mine);

    renderPresentStats(seat);
    renderPresentCosts(app.state);

    closeVoice();
    closeChat();
    $("presentPanel").classList.remove("hidden");
}

/* ==============================
   托管：交给 AI 代打
   ============================== */

function myAutoLeft() {
    const seats = (app.state && app.state.seats) || [];

    const mine = seats.filter(function (seat) {
        return seat.is_you;
    })[0];

    return mine && mine.auto > 0 ? mine.auto : 0;
}

function renderAuto(state) {
    const button = $("autoButton");

    if (!button) return;

    const running = !!(state && state.hand_running);
    const left = myAutoLeft();

    // 没在打牌就用不上
    button.classList.toggle("hidden", !running);
    button.classList.toggle("on", left > 0);

    button.textContent = left > 0
        ? "托管中 " +
          Math.ceil(left) +
          "s（点一下取消）"
        : "托管（让 AI 代打）";
}

function toggleAuto() {
    const on = myAutoLeft() <= 0;

    api("/api/auto", {
        token: app.token,
        on: on,
    }).then(function (data) {
        if (!data.ok) {
            $("message").textContent =
                data.error || "操作失败";
            return;
        }

        $("message").textContent = on
            ? "已托管，AI 代打中（最长 " +
              ((app.state && app.state.auto_max) || 100) +
              " 秒）"
            : "已取消托管";
    });
}

function setupAuto() {
    const button = $("autoButton");

    if (!button) return;

    button.onclick = toggleAuto;
}

function closePresent() {
    app.presentTarget = null;
    $("presentPanel").classList.add("hidden");
}

function sendPresent(item) {
    const target = app.presentTarget;

    if (target === null || target === undefined) return;

    api("/api/present", {
        token: app.token,
        seat: target,
        item: item,
    }).then(function (data) {
        if (!data.ok) {
            $("message").textContent =
                data.error || "发送失败";
            return;
        }

        closePresent();

        // 广播有可能比这个响应先到并已经播过了；
        // 那就不要再放一遍，否则自己这边会飞两次
        if (data.key && !app.seenPresents[data.key]) {
            app.seenPresents[data.key] = true;

            throwPresent({
                from: mySeatIndex(),
                to: target,
                item: item,
            });
        }
    });
}

function setupPresent() {
    const seats = $("seats");

    function cancelPress() {
        clearTimeout(pressTimer);
        pressTimer = null;
        pressOrigin = null;
    }

    seats.addEventListener("pointerdown", function (event) {
        const node = event.target.closest(".seat");

        if (!node) return;

        const index = Array.prototype.indexOf.call(
            seats.children,
            node
        );

        if (index < 0) return;

        pressSeat = index;
        pressOrigin = {
            x: event.clientX,
            y: event.clientY,
        };

        clearTimeout(pressTimer);

        // 比系统长按（一般 500ms）稍早一点触发，
        // 免得系统先弹菜单、把我们的手势取消掉
        pressTimer = setTimeout(function () {
            pressTimer = null;
            openPresent(pressSeat);
        }, 400);
    });

    seats.addEventListener("pointerup", cancelPress);
    seats.addEventListener("pointercancel", cancelPress);
    seats.addEventListener("pointerleave", cancelPress);

    // 手指划走就当没长按
    seats.addEventListener("pointermove", function (event) {
        if (!pressTimer || !pressOrigin) return;

        if (
            Math.abs(event.clientX - pressOrigin.x) > 12 ||
            Math.abs(event.clientY - pressOrigin.y) > 12
        ) {
            cancelPress();
        }
    });

    // 点一下名片也能打开：比长按稳妥得多，
    // 各机型对长按的手势拦截差别很大
    seats.addEventListener("click", function (event) {
        const node = event.target.closest(".seat");

        if (!node) return;

        const index = Array.prototype.indexOf.call(
            seats.children,
            node
        );

        if (index < 0) return;

        togglePresent(index);
    });

    // 长按别唤起系统菜单
    seats.addEventListener("contextmenu", function (event) {
        event.preventDefault();
    });

    $("presentClose").onclick = closePresent;

    $("presentPanel")
        .querySelector(".present-list")
        .addEventListener("click", function (event) {
            const button = event.target.closest("[data-item]");

            if (button) {
                sendPresent(button.getAttribute("data-item"));
            }
        });
}

/* ==============================
   快捷语音
   ============================== */

const quickAudio = {};
let beepContext = null;

// 音效文件还没放上去时，用一段合成提示音兜底，
// 这样功能本身先能用，不必等素材到位
function beepFallback() {
    try {
        const Ctx =
            window.AudioContext ||
            window.webkitAudioContext;

        if (!Ctx) return;

        if (!beepContext) beepContext = new Ctx();

        if (beepContext.state === "suspended") {
            beepContext.resume();
        }

        const now = beepContext.currentTime;
        const osc = beepContext.createOscillator();
        const gain = beepContext.createGain();

        osc.type = "triangle";
        osc.frequency.setValueAtTime(920, now);
        osc.frequency.exponentialRampToValueAtTime(
            560,
            now + .28
        );

        gain.gain.setValueAtTime(.0001, now);
        gain.gain.exponentialRampToValueAtTime(.22, now + .02);
        gain.gain.exponentialRampToValueAtTime(.0001, now + .34);

        osc.connect(gain);
        gain.connect(beepContext.destination);

        osc.start(now);
        osc.stop(now + .36);
    } catch (error) {
        // 出不出声都不能影响打牌
    }
}

/* ------------------------------
   背景音乐闪避（ducking）
   ------------------------------ */

// 语音播放期间把 BGM 压低，播完再抬回来。
// 另外手机浏览器在播放新音频时会抢占音频焦点、把 BGM 直接暂停掉，
// 所以每次出声前后都补一次恢复，避免音乐再也回不来。
const BGM_DUCK = 0.12;      // 闪避时的音量
const BGM_RESTORE = 1;      // 正常音量
const BGM_FADE_STEP = 25;   // 音量补间步长（毫秒）
const BGM_FADE_MS = 150;    // 淡入淡出总时长

let bgmVoiceCount = 0;      // 正在播放的语音条数
let bgmFadeTimer = null;
let bgmWatchdog = null;     // 语音期间盯着 BGM 有没有被掐掉
let bgmWatchdogStop = null; // 语音结束后再盯一会儿的延时器

// 被浏览器打断、且自动恢复没成功，等下一次用户操作再试
let bgmInterrupted = false;
let bgmStallSpot = 0;
let bgmStallTicks = 0;

function bgmWanted() {
    return localStorage.getItem("poker_bgm") !== "off";
}

// 刷新声音按钮的状态。被打断还没救回来时闪一下，
// 让人知道点一下就能恢复。
function bgmPaint() {
    const button = $("bgmButton");

    if (!button) return;

    // 点亮与否看三路开关（背景音乐 / 音效 / 语音）
    paintSoundButton();

    button.classList.toggle(
        "warn",
        bgmInterrupted && bgmWanted()
    );
}

// 浏览器把 BGM 暂停了就补一次播放。
// 夸克这类浏览器打断后可能要求「再来一次用户手势」才肯恢复，
// 所以失败时记下来，交给手势救援。
function keepBgmAlive() {
    const audio = $("bgm");

    if (!audio || !bgmWanted() || !audio.paused) return;

    const result = audio.play();

    if (!result || !result.catch) return;

    result.catch(function () {
        bgmInterrupted = true;
        bgmPaint();
    });
}

// 语音期间持续盯守：
//   · 被暂停 → 立刻拉回来
//   · paused 是 false、但播放位置不前进 → 被悄悄挂起了，
//     走一次 pause/play 重新抢回音频焦点
function bgmWatchdogTick() {
    const audio = $("bgm");

    if (!audio || !bgmWanted()) return;

    if (audio.paused) {
        bgmInterrupted = true;
        keepBgmAlive();
        return;
    }

    if (audio.currentTime === bgmStallSpot) {
        bgmStallTicks++;

        if (bgmStallTicks >= 4) {
            bgmStallTicks = 0;
            bgmInterrupted = true;

            try {
                audio.pause();
            } catch (error) {
                // 忽略：能恢复就行
            }

            keepBgmAlive();
            bgmPaint();
        }
    } else {
        bgmStallTicks = 0;
    }

    bgmStallSpot = audio.currentTime;
}

function startBgmWatchdog() {
    if (bgmWatchdog) return;

    bgmStallSpot = 0;
    bgmStallTicks = 0;
    bgmWatchdog = setInterval(bgmWatchdogTick, 300);
}

function stopBgmWatchdog() {
    if (!bgmWatchdog) return;

    clearInterval(bgmWatchdog);
    bgmWatchdog = null;
    bgmStallTicks = 0;
}

// 夸克打断后只认用户手势，所以把恢复挂在点击/触摸上。
// 打牌时本来就一直在点，通常一两秒内音乐就回来了。
function bgmGestureRescue() {
    function rescue() {
        if (!bgmInterrupted || !bgmWanted()) return;

        keepBgmAlive();
    }

    document.addEventListener("click", rescue, true);
    document.addEventListener("touchstart", rescue, true);
    document.addEventListener("keydown", rescue, true);

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) rescue();
    });
}

// 手写补间：移动端对 audio.volume 的动画支持不一致，
// 用定时器改最稳
function fadeBgmTo(target) {
    const audio = $("bgm");

    if (!audio) return;

    if (bgmFadeTimer) {
        clearInterval(bgmFadeTimer);
        bgmFadeTimer = null;
    }

    const from = audio.volume;
    const diff = target - from;

    if (Math.abs(diff) < 0.01) {
        audio.volume = target;
        return;
    }

    const steps = Math.max(
        2,
        Math.round(BGM_FADE_MS / BGM_FADE_STEP)
    );

    let done = 0;

    bgmFadeTimer = setInterval(function () {
        done++;

        if (done >= steps) {
            clearInterval(bgmFadeTimer);
            bgmFadeTimer = null;
            audio.volume = target;
            return;
        }

        audio.volume = from + diff * (done / steps);
    }, BGM_FADE_STEP);
}

// 语音开播：计数 +1，压低音乐并开始盯守
function duckBgm() {
    bgmVoiceCount++;

    if (!bgmWanted()) return;

    // 取消之前排好的「停止盯守」
    if (bgmWatchdogStop) {
        clearTimeout(bgmWatchdogStop);
        bgmWatchdogStop = null;
    }

    startBgmWatchdog();
    keepBgmAlive();
    fadeBgmTo(BGM_DUCK);
}

// 语音结束：计数 -1，全播完了才抬回来
function unduckBgm() {
    bgmVoiceCount = Math.max(0, bgmVoiceCount - 1);

    // 还有人在说话，先别抬
    if (bgmVoiceCount > 0) return;

    const audio = $("bgm");

    // 用户把音乐关了就不恢复播放，但音量要复位，
    // 免得下次打开时是压低后的音量
    if (!bgmWanted()) {
        if (audio) audio.volume = BGM_RESTORE;
        return;
    }

    keepBgmAlive();
    fadeBgmTo(BGM_RESTORE);

    // 有些浏览器是在语音播完之后才掐掉 BGM 的，
    // 所以这里错开时间再确认两次
    setTimeout(keepBgmAlive, 250);
    setTimeout(keepBgmAlive, 900);

    // 语音停了再盯一会儿，之后收工
    if (bgmWatchdogStop) clearTimeout(bgmWatchdogStop);

    bgmWatchdogStop = setTimeout(function () {
        bgmWatchdogStop = null;
        stopBgmWatchdog();
    }, 3000);
}

// duck 为 true 表示这是语音这类长音，期间需要压低 BGM；
// 道具音、牌局音效都很短，压低反而会听着发抖，所以不闪避。
//
// channel 决定受哪个开关控制：
//   "sfx"（默认）= 游戏音效，含道具音
//   "voice"       = 快捷语音
function playQuickSound(url, duck, channel) {
    if (!url) return;

    // 用户把这一路关掉了就不出声
    if (!soundOn(channel || "sfx")) return;

    let audio = quickAudio[url];

    if (!audio) {
        audio = new Audio(url);
        audio.preload = "auto";
        quickAudio[url] = audio;
    }

    keepBgmAlive();

    try {
        audio.currentTime = 0;
    } catch (error) {
        // 还没加载完时设置会抛，忽略即可
    }

    let settled = false;

    function finish() {
        if (settled) return;
        settled = true;

        audio.onended = null;

        if (duck) unduckBgm();
    }

    if (duck) {
        duckBgm();

        audio.onended = finish;

        // 兜底：切后台或文件损坏时 onended 可能不触发，
        // 不能让音乐一直闷着
        setTimeout(
            finish,
            (audio.duration && isFinite(audio.duration)
                ? audio.duration * 1000
                : 6000) + 800
        );
    }

    const played = audio.play();

    if (played && played.catch) {
        // 文件缺失或浏览器不许出声，退回提示音
        played.catch(function () {
            beepFallback();
            finish();
        });
    }

    // 浏览器是在新音频真正开始播之后才抢焦点的，
    // 所以稍后再确认一次 BGM 还活着
    if (duck) {
        setTimeout(keepBgmAlive, 350);
    }
}

// 在发言者名片上方弹出文字
// 在发言者名片上方弹一个气泡。
// kind 为 "chat" 时是打字消息（深色消息框，像聊天），
// 否则是快捷语音（金色，像喊话）。
function showBubble(seatIndex, text, kind) {
    const seats = $("seats").children;
    const seat = seats[seatIndex];
    const layer = $("bubbles");

    if (!seat || !layer) return;

    const isChat = kind === "chat";

    const bubble = document.createElement("div");
    bubble.className = isChat ? "chat-bubble" : "quick-bubble";
    bubble.textContent = text;
    bubble.dataset.seat = String(seatIndex);

    // 同一张名片上只留一个气泡：不然文字消息和快捷语音
    // 会叠在同一个位置，两个都看不清
    const stale = layer.querySelectorAll(
        '[data-seat="' + seatIndex + '"]'
    );

    for (let i = 0; i < stale.length; i++) {
        stale[i].remove();
    }

    // 气泡弹出期间也不动牌堆，避免重排造成掉帧
    app.effectBusyUntil = Date.now() + 1400;

    bubble.style.top = seat.style.top;

    layer.appendChild(bubble);

    // 座位在牌桌左右两侧时，气泡会被牌桌边缘裁掉。
    // 所以量出真实宽度后往中间推一点，保证整条都能看见。
    const seatBox = seat.getBoundingClientRect();
    const layerBox = layer.getBoundingClientRect();
    const half = bubble.offsetWidth / 2;
    const center =
        seatBox.left + seatBox.width / 2 - layerBox.left;

    let x = center;

    if (x - half < 4) {
        x = half + 4;
    } else if (x + half > layerBox.width - 4) {
        x = layerBox.width - half - 4;
    }

    bubble.style.left = x + "px";

    if (bubble.animate) {
        const animation = bubble.animate(
            [
                {
                    opacity: 0,
                    transform:
                        "translate(-50%,-100%) scale(.55)",
                },
                {
                    opacity: 1,
                    transform:
                        "translate(-50%,-100%) scale(1.08)",
                    offset: .12,
                },
                {
                    opacity: 1,
                    transform:
                        "translate(-50%,-100%) scale(1)",
                    offset: .22,
                },
                {
                    opacity: 1,
                    transform:
                        "translate(-50%,-100%) scale(1)",
                    offset: .78,
                },
                {
                    opacity: 0,
                    transform:
                        "translate(-50%,-100%) " +
                        "translateY(-18px) scale(.94)",
                },
            ],
            {
                // 打字消息往往更长，多给一点阅读时间
                duration: isChat ? 3000 : 2600,
                easing: "ease-out",
            }
        );

        animation.onfinish = function () {
            bubble.remove();
        };
    }

    // 兜底：动画被中途打断也要清干净
    setTimeout(function () {
        bubble.remove();
    }, isChat ? 4000 : 3200);
}

function showQuickBubble(event) {
    showBubble(event.seat, event.text, "quick");
}

function showQuick(event) {
    showQuickBubble(event);
    // 语音期间需要压低背景音乐，并且受「快捷语音」开关控制
    playQuickSound(event.sound, true, "voice");
}

function renderQuick(state) {
    const phrases = state.quick_phrases || [];
    const events = state.quick || [];

    // 按钮清单只在变化时重建
    const listKey = phrases
        .map(function (item) {
            return item.id;
        })
        .join(",");

    if (app.quickListKey !== listKey) {
        app.quickListKey = listKey;

        $("voiceList").innerHTML = phrases
            .map(function (item) {
                return (
                    '<button class="voice-item" data-phrase="' +
                    escapeHtml(item.id) +
                    '">' +
                    escapeHtml(item.text) +
                    "</button>"
                );
            })
            .join("");
    }

    events.forEach(function (event) {
        if (app.seenQuick[event.key]) return;

        app.seenQuick[event.key] = true;

        // 刚进桌时不补播历史
        if (!app.quickPrimed) return;

        // 隔太久的不补播（网络卡顿时事件会晚到，窗口放宽些）
        if (Date.now() / 1000 - event.at > 60) return;

        showQuick(event);
    });

    app.quickPrimed = true;
}

function sendQuick(phraseId) {
    const phrases =
        (app.state && app.state.quick_phrases) || [];
    const phrase = phrases.filter(function (item) {
        return item.id === phraseId;
    })[0];

    api("/api/quick", {
        token: app.token,
        phrase: phraseId,
    }).then(function (data) {
        if (!data.ok) {
            $("message").textContent =
                data.error || "发送失败";
            return;
        }

        // 收起面板，好让人看清牌桌上的特效
        closeVoice();

        // 广播有可能已经先到并播过了，那就不要再放一遍
        if (phrase && data.key && !app.seenQuick[data.key]) {
            app.seenQuick[data.key] = true;

            const me = (app.state &&
                app.state.seats || []).filter(
                function (seat) {
                    return seat.is_you;
                }
            )[0];

            showQuick({
                seat: me ? app.state.seats.indexOf(me) : 0,
                text: phrase.text,
                sound: phrase.sound,
            });
        }
    });
}

function openVoice() {
    closeChat();
    closePresent();
    $("voicePanel").classList.remove("hidden");
}

function closeVoice() {
    $("voicePanel").classList.add("hidden");
}

function setupVoice() {
    $("voiceButton").onclick = function () {
        if ($("voicePanel").classList.contains("hidden")) {
            openVoice();
        } else {
            closeVoice();
        }
    };

    $("voiceClose").onclick = closeVoice;

    $("voiceList").onclick = function (event) {
        const button = event.target.closest("[data-phrase]");

        if (button) {
            sendQuick(button.getAttribute("data-phrase"));
        }
    };
}

/* ==============================
   表情：在聊天面板里点一下就飘出来
   ============================== */

function emojiPath(index) {
    return "/static/emoji/" + index + ".png";
}

// 表情格子只在张数变化时重建
function renderEmojiGrid(state) {
    const grid = $("emojiGrid");

    // 旧版页面里没有这个元素，直接跳过。
    // 不判空就会抛异常，把同一次渲染后面的活儿全带停。
    if (!grid) return;

    const count = (state && state.emoji_count) || 0;
    const key = String(count);

    if (app.emojiListKey === key) return;

    app.emojiListKey = key;

    let html = "";

    for (let i = 0; i < count; i++) {
        html +=
            '<button class="emoji-item" data-emoji="' +
            i +
            '"><img src="' +
            emojiPath(i) +
            '" alt="" loading="lazy"></button>';
    }

    grid.innerHTML = html;
}

// 表情飘在发送者名片上方
function showEmoji(event) {
    const seats = $("seats").children;
    const seat = seats[event.seat];
    const layer = $("bubbles");

    if (!seat || !layer) return;

    const wrap = document.createElement("div");
    wrap.className = "emoji-pop";

    const img = document.createElement("img");
    img.src = emojiPath(event.index);
    img.alt = "";
    wrap.appendChild(img);

    // 飘表情期间不动牌堆，避免重排掉帧
    app.effectBusyUntil = Date.now() + 1500;

    // 跟座位用同一套百分比坐标，天然对得上
    wrap.style.left = seat.style.left;
    wrap.style.top = seat.style.top;

    layer.appendChild(wrap);

    const frames = [
        {
            opacity: 0,
            transform: "translate(-50%,-100%) scale(.4)",
        },
        {
            opacity: 1,
            transform:
                "translate(-50%,-100%) scale(1.15)",
            offset: .18,
        },
        {
            opacity: 1,
            transform: "translate(-50%,-135%) scale(1)",
            offset: .64,
        },
        {
            opacity: 0,
            transform: "translate(-50%,-175%) scale(.9)",
        },
    ];

    if (wrap.animate) {
        const animation = wrap.animate(frames, {
            duration: 1900,
            easing: "ease-out",
        });

        animation.onfinish = function () {
            wrap.remove();
        };
    }

    // 兜底：动画被中途打断也要清干净
    setTimeout(function () {
        wrap.remove();
    }, 2500);
}

function renderEmojis(state) {
    const events = (state && state.emojis) || [];

    events.forEach(function (event) {
        if (app.seenEmojis[event.key]) return;

        app.seenEmojis[event.key] = true;

        // 刚进桌不补放历史
        if (!app.emojiPrimed) return;

        // 隔太久的不补放（网络卡顿时事件会晚到）
        if (Date.now() / 1000 - event.at > 60) return;

        showEmoji(event);
    });

    app.emojiPrimed = true;
}

function sendEmoji(index) {
    api("/api/emoji", {
        token: app.token,
        index: index,
    }).then(function (data) {
        if (!data.ok) {
            $("message").textContent =
                data.error || "发送失败";
            return;
        }

        toggleEmojiGrid(false);

        // 广播可能已经先到并飘过了，那就别飘第二遍
        if (data.key && !app.seenEmojis[data.key]) {
            app.seenEmojis[data.key] = true;

            showEmoji({
                seat: mySeatIndex(),
                index: index,
            });
        }
    });
}

function toggleEmojiGrid(open) {
    const grid = $("emojiGrid");
    const toggle = $("emojiToggle");

    if (!grid || !toggle) return;

    app.emojiOpen =
        open === undefined ? !app.emojiOpen : !!open;

    grid.classList.toggle("hidden", !app.emojiOpen);

    toggle.classList.toggle("on", app.emojiOpen);

    // 展开表情时把聊天列表压到底，免得挡住最新消息
    if (app.emojiOpen) {
        const list = $("chatList");

        if (list) {
            list.scrollTop = list.scrollHeight;
        }
    }
}

function setupEmoji() {
    const grid = $("emojiGrid");
    const toggle = $("emojiToggle");

    // 旧版页面里没有这两个元素，跳过就好，
    // 不能因为缺了这两个元素把整个启动流程打断
    if (!grid || !toggle) return;

    toggle.onclick = function () {
        toggleEmojiGrid();
    };

    grid.onclick = function (event) {
        const button =
            event.target.closest("[data-emoji]");

        if (button) {
            sendEmoji(
                Number(button.getAttribute("data-emoji"))
            );
        }
    };
}

/* ==============================
   音效：开局 / 发牌 / 结算 / 出局
   ============================== */

const SFX = {
    gamestart: "/static/sfx/gamestart.mp3",
    ready: "/static/sfx/ready.mp3",
    win: "/static/sfx/win.mp3",
    lose: "/static/sfx/lose.mp3",
    draw: "/static/sfx/draw.mp3",
    chain: "/static/sfx/chain.mp3",
    hit: "/static/sfx/normal_damage.mp3",
};

function playSfx(name) {
    const url = SFX[name];

    if (url) playQuickSound(url, false, "sfx");
}

/* ==============================
   弹幕：牌桌底部的滚动播报
   ============================== */

// 引擎日志里值得播报的行 → 播报文案
const DANMU_RULES = [
    {
        test: function (line) {
            return line.indexOf("ALL IN") >= 0;
        },
        text: function (line) {
            return line.replace(" ALL IN", " 全下！");
        },
    },
    {
        test: function (line) {
            return line.indexOf("获得底池") >= 0;
        },
        text: function (line) {
            return "🏆 " + line;
        },
    },
    {
        test: function (line) {
            return line.indexOf("平分") >= 0;
        },
        text: function (line) {
            return "🤝 " + line;
        },
    },
    {
        test: function (line) {
            return /^(Flop|Turn|River):/.test(line);
        },
        text: function (line) {
            const cut = line.indexOf(":");
            const stage = line.slice(0, cut);
            const cards = line.slice(cut + 1).trim();

            const name = {
                Flop: "翻牌",
                Turn: "转牌",
                River: "河牌",
            }[stage];

            return (name || stage) + " " + cards;
        },
    },
];

function danmu(text) {
    const layer = $("danmu");

    if (!layer || !text) return;

    const item = document.createElement("div");
    item.className = "danmu-item";
    item.textContent = text;

    layer.appendChild(item);

    // 照 Danmu.qml 的算法定速度：(容器宽 + 文字宽) × 5 毫秒
    const distance = layer.clientWidth + item.offsetWidth;
    const duration = Math.max(2800, distance * 5);

    app.danmuRunning++;

    function done() {
        if (item.parentNode) {
            item.remove();
            app.danmuRunning--;
        }
    }

    if (item.animate) {
        const animation = item.animate(
            [
                { transform: "translateX(0)" },
                {
                    transform:
                        "translateX(-" + distance + "px)",
                },
            ],
            { duration: duration, easing: "linear" }
        );

        animation.onfinish = done;
    }

    // 兜底：动画被中途打断也要清掉
    setTimeout(done, duration + 1200);
}

// 座位状态变化时的音效与播报：
//   · 有人出局 → 一声闷响 + 弹幕
//   · 有人点「准备」→ 一声短促提示
function syncSeatFlags(state, playSound) {
    (state.seats || []).forEach(function (seat, index) {
        const out = !!seat.eliminated;

        if (out && !app.deadSeats[index]) {
            app.deadSeats[index] = true;

            if (playSound) {
                playSfx("hit");
                danmu(seat.name + " 出局");
            }
        }

        if (!out) {
            delete app.deadSeats[index];
        }

        // 准备状态每手开头会被清空，所以这里的
        // 「假 → 真」就是玩家真的按了一次准备
        const ready = !!seat.ready;

        if (ready && !app.readySeats[index]) {
            app.readySeats[index] = true;

            if (playSound) playSfx("ready");
        }

        if (!ready) {
            delete app.readySeats[index];
        }
    });
}

function renderBroadcast(state) {
    const log = (state && state.log) || [];
    const hand = state.hand_no;

    // 换了一手牌：引擎的日志会清空重来，计数跟着归零
    if (
        hand !== null &&
        hand !== undefined &&
        app.danmuHand !== hand
    ) {
        app.danmuHand = hand;
        app.danmuSeen = 0;
    }

    // 进桌后的第一份状态只用来对账，不补播历史
    if (!app.danmuPrimed) {
        app.danmuPrimed = true;
        app.danmuSeen = log.length;
        syncSeatFlags(state, false);
        return;
    }

    // 日志变短说明被清空了，重新对齐
    if (log.length < app.danmuSeen) {
        app.danmuSeen = 0;
    }

    const fresh = log.slice(app.danmuSeen);
    app.danmuSeen = log.length;

    fresh.forEach(function (line) {
        if (line === "Game started") {
            playSfx("gamestart");
            return;
        }

        for (let i = 0; i < DANMU_RULES.length; i++) {
            if (DANMU_RULES[i].test(line)) {
                danmu(DANMU_RULES[i].text(line));
                return;
            }
        }
    });

    syncSeatFlags(state, true);
}

/* ==============================
   牌桌聊天
   ============================== */

// 打字消息也做成从名片上方弹出的气泡。
// 表情、快捷语音各有自己的特效，这里只管 kind 为 text 的。
function renderChatBubbles(state) {
    const messages = (state && state.chat) || [];
    const seats = (state && state.seats) || [];

    // 刚进桌只对账，不补播历史
    if (!app.chatBubbleReady) {
        app.chatBubbleReady = true;

        messages.forEach(function (item) {
            if (item.at > app.chatBubbleAt) {
                app.chatBubbleAt = item.at;
            }
        });

        return;
    }

    messages.forEach(function (item) {
        // 用时间戳判断新旧：聊天记录是个滚动窗口，
        // 靠数组长度会漏（满 60 条后长度就不再涨了）
        if (item.at <= app.chatBubbleAt) return;

        app.chatBubbleAt = item.at;

        if (item.kind && item.kind !== "text") return;

        let index = -1;

        for (let i = 0; i < seats.length; i++) {
            if (seats[i].name === item.name) {
                index = i;
                break;
            }
        }

        // 人已经离开牌桌就不弹了
        if (index < 0) return;

        showBubble(index, item.text, "chat");
    });
}

// 聊天记录里一条的正文：打字 / 表情 / 快捷语音
function chatBodyHtml(item) {
    if (item.kind === "emoji") {
        return (
            '<img class="chat-emoji" src="' +
            emojiPath(item.index) +
            '" alt="表情" loading="lazy">'
        );
    }

    if (item.kind === "quick") {
        return (
            '<span class="chat-quick">🔊 ' +
            escapeHtml(item.text) +
            "</span>"
        );
    }

    return (
        '<span class="chat-text">' +
        escapeHtml(item.text) +
        "</span>"
    );
}

function chatItemHtml(item) {
    return (
        '<div class="chat-item">' +
        '<span class="chat-name">' +
        escapeHtml(item.name) +
        "</span>" +
        chatBodyHtml(item) +
        "</div>"
    );
}

function renderChat(state) {
    const messages = (state && state.chat) || [];
    const list = $("chatList");

    const lastAt = messages.length
        ? messages[messages.length - 1].at
        : 0;

    // 只有用户本来就在底部时才自动跟随，
    // 免得翻看旧消息时被拽回来
    const atBottom =
        list.scrollTop + list.clientHeight >=
        list.scrollHeight - 24;

    const html = messages.length
        ? messages.map(chatItemHtml).join("")
        : '<div class="chat-empty">' +
          "还没有消息，说点什么吧</div>";

    // 内容没变就不重建：否则每次状态推送都会重设 innerHTML，
    // 里面的表情图被反复创建，看着会闪
    if (app.lastChatHtml !== html) {
        app.lastChatHtml = html;
        list.innerHTML = html;
    }

    if (app.chatOpen && atBottom) {
        list.scrollTop = list.scrollHeight;
    }

    if (app.chatOpen) {
        app.chatSeenAt = lastAt;
    }

    const unread = messages.filter(function (item) {
        return item.at > app.chatSeenAt;
    }).length;

    const badge = $("chatBadge");

    if (unread > 0 && !app.chatOpen) {
        badge.textContent =
            unread > 9 ? "9+" : String(unread);
        badge.classList.remove("hidden");
    } else {
        badge.classList.add("hidden");
    }
}

function openChat() {
    closeVoice();
    closePresent();

    app.chatOpen = true;
    $("chatPanel").classList.remove("hidden");

    const messages =
        (app.state && app.state.chat) || [];

    app.chatSeenAt = messages.length
        ? messages[messages.length - 1].at
        : 0;

    renderChat(app.state || {});

    const list = $("chatList");
    list.scrollTop = list.scrollHeight;

    $("chatInput").focus();
}

function closeChat() {
    app.chatOpen = false;
    $("chatPanel").classList.add("hidden");

    // 收起聊天时把表情格子也一起收掉
    toggleEmojiGrid(false);
}

function sendChat() {
    const input = $("chatInput");
    const text = input.value.trim();

    if (!text) return;

    input.value = "";

    api("/api/chat", {
        token: app.token,
        text: text,
    }).then(function (data) {
        if (!data.ok) {
            // 失败就把内容还给用户，别让人白打一遍
            input.value = text;
            $("message").textContent =
                data.error || "发送失败";
        }
    });
}

function setupChat() {
    $("chatButton").onclick = function () {
        if (app.chatOpen) {
            closeChat();
        } else {
            openChat();
        }
    };

    $("chatClose").onclick = closeChat;
    $("chatSend").onclick = sendChat;

    $("chatInput").onkeydown = function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            sendChat();
        }
    };
}

function setupControls() {

    $("raiseSlider").oninput = function () {
        setRaiseValue($("raiseSlider").value);
    };

    // 手动输入时不再夹紧、也不改写输入框。
    // 以前每敲一个键就 setRaiseValue 一次：想输 1000，
    // 刚敲下 "1" 就被弹回最小加注，根本没法手输。
    // 合不合法留到点「确定」那一刻再判断。
    $("raiseInput").oninput = function () {
        const lo = parseInt($("raiseSlider").min, 10);
        const hi = parseInt($("raiseSlider").max, 10);
        const raw = $("raiseInput").value;
        const v = parseInt(raw, 10);

        if (!isNaN(v) && v >= lo && v <= hi) {

            // 合法：顺手把滑块和上面的大字跟上
            $("raiseSlider").value = v;
            $("raiseValue").textContent = v;
            return;
        }

        // 还没输完 / 超范围：只让大字跟着走，
        // 输入框原样留着，等确定了再说
        $("raiseValue").textContent =
            raw === "" ? lo : raw;
    };
    $("advancedToggle").onclick = function () {
        $("advancedPanel").classList.toggle(
            "hidden"
        );
    };

    $("joinButton").onclick = join;

    $("foldButton").onclick = function () {
        act("fold");
    };

    $("checkButton").onclick = function () {
        act("check");
    };

    $("callButton").onclick = function () {
        act("call");
    };

    $("raiseButton").onclick = openRaisePanel;

    $("raiseCancel").onclick = function () {
        $("raisePanel").classList.add("hidden");
    };

    $("raiseConfirm").onclick = function () {
        const lo = parseInt($("raiseSlider").min, 10);
        const hi = parseInt($("raiseSlider").max, 10);

        // 以手输框为准（没手输过时它一直跟着滑块走），
        // 点确定这一刻才判断合不合法
        const v = parseInt($("raiseInput").value, 10);

        if (isNaN(v) || v < lo || v > hi) {

            $("message").textContent =
                "加注要到 " + lo + " ~ " + hi + " 之间";

            return;
        }

        act("raise", v);
    };

    $("allInButton").onclick = function () {
        if (!confirm("确定要全下吗？")) return;
        act("all_in");
    };

    $("readyButton").onclick = function () {
        const me = app.state.seats.find(function (s) {
            return s.is_you;
        });

        const willReady = !(me && me.ready);

        api("/api/ready", {
            token: app.token,
            ready: willReady,
        }).then(function (data) {
            if (!data.ok) {
                alert(data.error || "操作失败");
            }
        });
    };

    $("nextHandButton").onclick = function () {
        api("/api/next", { token: app.token }).then(
            function (data) {
                if (!data.ok) {
                    alert(
                        data.error || "无法开始"
                    );
                }
            }
        );
    };

    $("buyInButton").onclick = function () {
        const panel = $("buyInPanel");

        if (!panel.classList.contains("hidden")) {
            panel.classList.add("hidden");
            return;
        }

        $("buyInAmount").value =
            (app.state && app.state.start_chips) ||
            1000;

        $("buyInLoan").checked = false;

        panel.classList.remove("hidden");
    };

    $("buyInCancel").onclick = function () {
        $("buyInPanel").classList.add("hidden");
    };

    const quickButtons = document.querySelectorAll(
        ".buyin-quick button"
    );

    quickButtons.forEach(function (button) {
        button.onclick = function () {
            const current =
                parseInt(
                    $("buyInAmount").value,
                    10
                ) || 0;

            $("buyInAmount").value =
                current +
                parseInt(
                    button.dataset.amount,
                    10
                );
        };
    });

    $("buyInConfirm").onclick = function () {
        const amount = parseInt(
            $("buyInAmount").value,
            10
        );

        if (!amount || amount <= 0) {
            alert("请输入买入金额");
            return;
        }

        api("/api/rebuy", {
            token: app.token,
            amount: amount,
            loan: $("buyInLoan").checked,
        }).then(function (data) {
            if (!data.ok) {
                alert(data.error || "买入失败");
                return;
            }

            $("buyInPanel").classList.add("hidden");
        });
    };

    // 主动归还筹码：金额自己填，和买入同一个输入框
    $("buyInReturn").onclick = function () {
        const amount = parseInt(
            $("buyInAmount").value,
            10
        );

        if (!amount || amount <= 0) {
            alert("请输入归还数量");
            return;
        }

        if (
            !confirm(
                "确认归还 " +
                    amount +
                    " 个筹码？\n" +
                    "筹码和累计买入会一起减，" +
                    "盈亏不受影响。"
            )
        ) {
            return;
        }

        api("/api/return", {
            token: app.token,
            amount: amount,
        }).then(function (data) {
            if (!data.ok) {
                alert(data.error || "归还失败");
                return;
            }

            $("buyInPanel").classList.add("hidden");
        });
    };

    $("ledgerButton").onclick = function () {
        const panel = $("ledgerPanel");

        panel.classList.toggle("hidden");

        app.ledgerOpen = !panel.classList.contains(
            "hidden"
        );

        if (app.ledgerOpen) {
            loadLedger();
        }
    };

    $("leaveButton").onclick = function () {
        if (!confirm("离开牌桌？")) return;

        api("/api/leave", {
            token: app.token,
        }).then(function () {
            if (app.source) app.source.close();

            clearSession();
            location.reload();
        });
    };

    $("copyButton").onclick = function () {
        const code = app.room || "";

        if (navigator.clipboard) {
            navigator.clipboard.writeText(code);
        }

        $("copyButton").textContent = "已复制";
        setTimeout(function () {
            $("copyButton").textContent =
                "复制房间号";
        }, 1200);
    };
}

/* ==============================
   启动
   ============================== */

/* ==============================
   背景音乐
   ============================== */

function setupBgm() {
    const audio = $("bgm");
    const button = $("bgmButton");

    if (!audio || !button) return;

    function tryPlay() {
        if (!bgmWanted()) return;

        const result = audio.play();

        if (result && result.catch) {
            result.catch(function () {});
        }
    }

    // 顶栏那个按钮改成打开声音设置（三个开关都在里面），
    // 绑定放在 setupSound 里统一处理。

    audio.onplay = function () {
        // 真的响起来了才算救回来
        bgmInterrupted = false;
        bgmPaint();
    };

    audio.onpause = function () {
        bgmPaint();

        // 不是我们关的（localStorage 还是 on）说明是被浏览器抢了焦点，
        // 立刻反抢
        if (bgmWanted()) {
            bgmInterrupted = true;
            bgmPaint();
            keepBgmAlive();
        }
    };

    bgmPaint();

    // 浏览器要求先有一次用户操作才允许出声，
    // 所以在第一次点击时补一次尝试。
    document.addEventListener("click", function first() {
        document.removeEventListener("click", first);
        tryPlay();
    });

    bgmGestureRescue();
}

/* ==============================
   声音：背景音乐 / 游戏音效 / 快捷语音
   ============================== */

// 三个通道各自独立开关，都存在本地
function soundOn(channel) {
    return (
        localStorage.getItem("poker_" + channel) !==
        "off"
    );
}

function setSound(channel, on) {
    localStorage.setItem(
        "poker_" + channel,
        on ? "on" : "off"
    );
}

// 三路里有任何一路开着，顶栏按钮就点亮
function paintSoundButton() {
    const button = $("bgmButton");

    if (!button) return;

    button.classList.toggle(
        "on",
        soundOn("bgm") ||
            soundOn("sfx") ||
            soundOn("voice")
    );
}

function paintSoundPanel() {
    const bgm = $("soundBgm");
    const sfx = $("soundSfx");
    const voice = $("soundVoice");

    if (bgm) bgm.checked = soundOn("bgm");
    if (sfx) sfx.checked = soundOn("sfx");
    if (voice) voice.checked = soundOn("voice");
}

function toggleSoundPanel(open) {
    const panel = $("soundPanel");

    if (!panel) return;

    app.soundOpen =
        open === undefined ? !app.soundOpen : !!open;

    panel.classList.toggle("hidden", !app.soundOpen);

    if (app.soundOpen) {
        paintSoundPanel();
    }
}

function setupSound() {
    const button = $("bgmButton");
    const close = $("soundClose");
    const bgm = $("soundBgm");
    const sfx = $("soundSfx");
    const voice = $("soundVoice");

    if (!button) return;

    // 点顶栏那个 ♪ 直接开声音设置
    button.onclick = function () {
        toggleSoundPanel();
    };

    if (close) {
        close.onclick = function () {
            toggleSoundPanel(false);
        };
    }

    if (bgm) {
        bgm.onchange = function () {
            const on = bgm.checked;

            setSound("bgm", on);

            const audio = $("bgm");

            if (on) {
                bgmInterrupted = false;

                if (audio) {
                    // 可能正停在闪避后的低音量上，开回来时复位
                    if (bgmVoiceCount === 0) {
                        audio.volume = BGM_RESTORE;
                    }

                    const played = audio.play();

                    if (played && played.catch) {
                        played.catch(function () {});
                    }
                }
            } else if (audio) {
                audio.pause();
            }

            bgmPaint();
        };
    }

    if (sfx) {
        sfx.onchange = function () {
            const on = sfx.checked;

            setSound("sfx", on);

            // 打开时放一声，让人马上知道生效了
            if (on) playSfx("ready");

            paintSoundButton();
        };
    }

    if (voice) {
        voice.onchange = function () {
            setSound("voice", voice.checked);
            paintSoundButton();
        };
    }

    paintSoundPanel();
    paintSoundButton();
}

/* ==============================
   PWA：离线兜底 + 加到桌面
   ============================== */

function setupPwa() {
    // 已经装成 App 的（独立窗口运行）就不用再教一遍了
    const installed =
        (window.navigator.standalone === true) ||
        (window.matchMedia &&
            window.matchMedia(
                "(display-mode: standalone)"
            ).matches);

    const hint = $("installHint");

    if (!installed && hint) {
        hint.classList.remove("hidden");
    }

    if (!("serviceWorker" in navigator)) return;

    function register() {
        navigator.serviceWorker
            .register("/sw.js")
            .catch(function () {
                // 只有 https / localhost 才允许注册，
                // 失败不影响正常使用，静默处理
            });
    }

    if (document.readyState === "complete") {
        register();
    } else {
        window.addEventListener("load", register);
    }
}


/* ==============================
   横屏 / 竖屏
   ============================== */

function isLandscapeUi() {
    return document.body.classList.contains(
        "landscape-ui"
    );
}

// 手机自然横过来（没开方向锁）时，自动切成横屏布局。
// 原来还有个「手动强制旋转」的按钮，已经去掉了。
function updateOrientation() {
    const landscape =
        window.innerWidth > window.innerHeight &&
        window.innerHeight <= 620;

    document.body.classList.toggle(
        "landscape-ui",
        landscape
    );

    // 布局换了，座位得重新排一遍
    if (app.state) {
        render(app.state);
    }
}

// 逐个初始化：任何一个出错都不能拦住后面的。
//
// 以前这里是一条直链，中间抛个异常，排在后面的就全不执行；
// 而脚本最后一行正是 tryResume()，于是整个页面再也连不上
// 服务端 —— 表现就是「弹个报错，然后一直无法开局」。
// 最常见的诱因是页面和脚本版本对不上（缓存里是旧 HTML、
// 脚本却是新的），新脚本去操作旧页面里不存在的元素。
function safeSetup(name, task) {
    try {
        task();
    } catch (error) {
        if (window.console && console.error) {
            console.error("[setup:" + name + "]", error);
        }

        showRefreshHint(
            "界面加载不完整（" + name + "出错），点这里刷新"
        );
    }
}

[
    ["操作按钮", setupControls],
    ["背景音乐", setupBgm],
    ["聊天", setupChat],
    ["快捷语音", setupVoice],
    ["互动道具", setupPresent],
    ["表情", setupEmoji],
    ["声音设置", setupSound],
    ["托管", setupAuto],
    ["离线兜底", setupPwa],
].forEach(function (pair) {
    safeSetup(pair[0], pair[1]);
});

// 屏幕尺寸变了（转屏、缩放）就重新给牌堆找位置
window.addEventListener("resize", function () {
    app.deckSpot = null;
    placeDeck();
});
updateOrientation();

// 手机横竖屏切换后重排座位
window.addEventListener("resize", function () {
    updateOrientation();
});

// 倒计时每秒平滑刷新（即使没有新的推送）
setInterval(updateTimer, 250);

// SSE 静默看门狗：
// 有些代理会把 SSE 连接“挂住”——既不推数据也不报错，
// 这样 onerror 不触发、轮询兜底也不会启动，界面就冻住了。
// 超过 5 秒没收到推送就切到轮询。
setInterval(function () {
    if (!app.token) return;

    if (Date.now() - lastSseAt > 5000) {
        startPolling();
    }
}, 2000);

// 初始页面每 4 秒刷新一次房间列表：
// 在等人齐的时候，得让在线人数自己动起来。
// 只在初始页面跑，进桌之后就不用再问了。
setInterval(function () {
    const join = $("joinScreen");

    if (join && !join.classList.contains("hidden")) {
        loadRooms();
    }
}, 4000);

// 脚本出错时显示到提示区，方便排查
window.addEventListener("error", function (event) {
    // 只报真正的脚本错误，忽略资源加载失败之类
    if (!event || !event.message) return;

    // 已经提示过刷新就别再覆盖，否则用户还没来得及点就被改掉
    if (renderErrorShown) return;

    const el = $("message");

    if (el) {
        el.textContent = "⚠ 页面脚本出错：" + event.message;
    }
});

// 恢复上次填的名字和房间号
safeSetup("恢复输入", function () {
    const savedName = localStorage.getItem("poker_name");

    if (savedName) {
        $("nameInput").value = savedName;
    }

    const savedRoom = localStorage.getItem("poker_room");

    if (savedRoom) {
        $("roomInput").value = savedRoom;
    }
});

// 最关键的一步：恢复连接。
// 必须独立执行 —— 前面任何一步出错都不能拦住它，
// 否则页面就永远连不上，什么都点不动。
safeSetup("恢复连接", tryResume);
