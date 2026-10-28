#!/bin/bash
#
# 一键对外开桌：本地服务 + 公网隧道
#
# 用法：
#     bash web/start_public.sh
#
# 启动后会打印一个 https://xxx.trycloudflare.com 地址，
# 病友用手机（4G 或任意 WiFi）打开这个地址就能进桌，
# 不需要连同一个 WiFi。
#
# 注意：每次重新启动，公网地址都会变，
# 需要把新地址发给牌友。
#

cd "$(dirname "$0")/.." || exit 1

PORT="${POKER_PORT:-8000}"
BIN="$HOME/.local/bin/cloudflared"
LOG=/tmp/cf_tunnel.log

echo "======================================"
echo " 德州扑克 · 对外开桌"
echo "======================================"
echo ""

# ------------------------------------------
# 1. 保证本地服务在跑
# ------------------------------------------

if curl -s -m 3 -o /dev/null \
        "http://127.0.0.1:${PORT}/api/rooms"; then

    echo "本地服务已经在运行（端口 ${PORT}）"

else

    echo "启动本地服务（端口 ${PORT}）..."

    setsid nohup python3 web/server.py "${PORT}" \
        > /tmp/poker_srv.log 2>&1 < /dev/null &

    for _ in $(seq 1 20); do

        if curl -s -m 2 -o /dev/null \
                "http://127.0.0.1:${PORT}/api/rooms";
        then
            break
        fi

        sleep 1

    done

fi

# ------------------------------------------
# 2. 准备 cloudflared
# ------------------------------------------

if [ ! -f "$BIN" ]; then

    echo "首次使用，正在下载 cloudflared ..."

    mkdir -p "$HOME/.local/bin"

    # 直连慢的时候优先走镜像
    curl -sL --max-time 120 -o "$BIN" \
        "https://gh-proxy.com/https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
        || curl -sL --max-time 300 -o "$BIN" \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

    chmod +x "$BIN"

fi

# ------------------------------------------
# 3. 开隧道（先关掉旧的，保证只有一个地址）
# ------------------------------------------

pkill -f "cloudflared tunnel" > /dev/null 2>&1
sleep 1

: > "$LOG"

setsid nohup "$BIN" tunnel \
    --url "http://127.0.0.1:${PORT}" \
    --no-autoupdate \
    > "$LOG" 2>&1 < /dev/null &

echo "正在建立公网隧道 ..."

URL=""

for _ in $(seq 1 30); do

    # -a 把日志当文本处理，
    # 避免 cloudflared 日志里的特殊字符
    # 导致 grep 匹配不到
    URL=$(grep -aoE \
        "https://[a-zA-Z0-9.-]+\.trycloudflare\.com" \
        "$LOG" | head -1)

    if [ -n "$URL" ]; then
        break
    fi

    sleep 2

done

echo ""

if [ -z "$URL" ]; then

    echo "没能拿到公网地址，日志如下："
    tail -20 "$LOG"
    exit 1

fi

mkdir -p data
echo "$URL" > data/tunnel_url.txt

echo "======================================"
echo " 把这个地址发给病友："
echo ""
echo "   $URL"
echo ""
echo " 自己本机也可以访问："
echo "   http://localhost:${PORT}"
echo "======================================"
echo ""
echo "地址也已保存到 data/tunnel_url.txt"
echo ""
echo "停止对外访问： pkill -f 'cloudflared tunnel'"
echo ""
