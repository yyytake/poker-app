/* 家庭德州扑克 —— Service Worker
 *
 * 策略：
 *   · /api/ 一律放行不接管 —— 实时状态和 SSE 长连接，
 *     缓存了会出大问题（牌局状态卡住、连接假死）
 *   · 页面：网络优先，3 秒还没回来就先用缓存顶上。
 *     后端是免费档、闲置会休眠，冷启动要 30~50 秒，
 *     这段时间先显示上次的界面，而不是一片白
 *   · JS / CSS：网络优先 —— 保证每次改动都能立刻生效，
 *     不搞「缓存优先」那种会让你怀疑没更新的行为
 *   · 图片 / 音频：缓存优先 —— 表情 59 张、音效一堆，
 *     这些几乎不变，省流量也省时间
 *
 * 换版本时改 CACHE 名字即可，activate 里会清掉旧的。
 */

// 换版本时改这个号：activate 里会把旧缓存整个删掉。
// 升到 v2 是为了清掉可能存着「旧首页 + 新 app.js」的旧缓存。
const CACHE = "poker-v2";

const MEDIA_RE = /\.(?:png|jpe?g|gif|svg|webp|ico|mp3|wav|m4a)$/i;

self.addEventListener("install", function () {
    // 不等用户关光标签页，直接进入待命
    self.skipWaiting();
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches
            .keys()
            .then(function (keys) {
                return Promise.all(
                    keys.map(function (key) {
                        if (key !== CACHE) {
                            return caches.delete(key);
                        }
                        return null;
                    })
                );
            })
            .then(function () {
                return self.clients.claim();
            })
    );
});

function put(request, response) {
    // 有些响应（比如带 no-store 的）可能存不进去，
    // 存不了就算了，不能影响正常使用
    if (!response || !response.ok) return;

    const copy = response.clone();

    caches
        .open(CACHE)
        .then(function (cache) {
            return cache.put(request, copy);
        })
        .catch(function () {});
}

function fromCache(request) {
    return caches.match(request).then(function (hit) {
        return hit || null;
    });
}

// 缓存优先：命中就直接用，没命中再上网并顺手存下来
function cacheFirst(request) {
    return fromCache(request).then(function (hit) {
        if (hit) return hit;

        return fetch(request).then(function (response) {
            put(request, response);
            return response;
        });
    });
}

// 网络优先 + 超时兜底。
// 注意：只有手里真握着缓存才会提前顶上，
// 否则宁可让请求继续等（冷启动可能要 30 秒），
// 绝不能因为超时就给用户看「连不上」。
function networkFirst(request, timeoutMs) {
    return new Promise(function (resolve) {
        let settled = false;

        function settle(response) {
            if (settled) return;
            settled = true;
            resolve(response);
        }

        const timer = setTimeout(function () {
            fromCache(request).then(function (hit) {
                if (hit) settle(hit);
            });
        }, timeoutMs);

        fetch(request)
            .then(function (response) {
                clearTimeout(timer);
                put(request, response);
                settle(response);
            })
            .catch(function () {
                clearTimeout(timer);

                fromCache(request).then(function (hit) {
                    if (hit) {
                        settle(hit);
                        return;
                    }

                    settle(
                        request.mode === "navigate"
                            ? offlinePage()
                            : new Response("", { status: 504 })
                    );
                });
            });
    });
}

function offlinePage() {
    const html =
        "<!DOCTYPE html><meta charset='utf-8'>" +
        "<meta name='viewport' content='width=device-width," +
        "initial-scale=1,viewport-fit=cover'>" +
        "<title>连不上服务器</title>" +
        "<body style='margin:0;min-height:100vh;display:flex;" +
        "align-items:center;justify-content:center;" +
        "background:#141009;color:#f2e7cf;" +
        "font-family:system-ui,sans-serif;text-align:center'>" +
        "<div><div style='font-size:56px'>&#9824;</div>" +
        "<div style='margin-top:14px;font-size:17px;" +
        "font-weight:700'>连不上服务器</div>" +
        "<div style='margin-top:8px;font-size:13px;" +
        "color:#9fc5b4'>检查一下网络，然后下拉刷新</div></div></body>";

    return new Response(html, {
        status: 503,
        headers: {
            "Content-Type": "text/html; charset=utf-8",
        },
    });
}

self.addEventListener("fetch", function (event) {
    const request = event.request;

    // 只管同源的 GET
    if (request.method !== "GET") return;

    let url;

    try {
        url = new URL(request.url);
    } catch (error) {
        return;
    }

    if (url.origin !== self.location.origin) return;

    // 接口和 SSE 一律不碰
    if (url.pathname.indexOf("/api/") === 0) return;

    if (MEDIA_RE.test(url.pathname)) {
        event.respondWith(cacheFirst(request));
        return;
    }

    // 页面给 3 秒，JS / CSS 放宽一些（它们更怕拿到旧版本）
    event.respondWith(
        networkFirst(
            request,
            request.mode === "navigate" ? 3000 : 8000
        )
    );
});
