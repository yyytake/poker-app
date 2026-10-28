package com.family.poker;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.pm.ApplicationInfo;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.TextView;

/**
 * 就是个 WebView 壳。
 *
 * 这么做（而不是把前端打进包里）有两个好处：
 *   · 服务端一改，家人不用重新装 APK，刷新就生效
 *   · 装出来的包只有几百 KB
 *
 * 另外顺带绕开了夸克那套浏览器策略 —— WebView 不是浏览器，
 * 不会因为播放语音就把背景音乐掐掉。
 */
public class MainActivity extends Activity {

    /** 牌桌地址。换服务器时改这里。 */
    private static final String HOME =
        "https://poker-app-5hrc.onrender.com/";

    /** 超过这个时间还没加载完，就提示可能在唤醒服务器 */
    private static final long SLOW_HINT_MS = 4000;

    private WebView web;
    private View splash;
    private TextView splashTitle;
    private TextView splashSub;
    private Button retry;

    private final Handler handler =
        new Handler(Looper.getMainLooper());

    private boolean loadFailed = false;

    private final Runnable slowHint = new Runnable() {
        @Override
        public void run() {
            splashSub.setText(
                "服务器可能在休眠，首次打开要等一会儿"
            );
        }
    };

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        splash = findViewById(R.id.splash);
        splashTitle = findViewById(R.id.splashTitle);
        splashSub = findViewById(R.id.splashSub);
        retry = findViewById(R.id.retry);

        WebSettings settings = web.getSettings();

        settings.setJavaScriptEnabled(true);
        // localStorage 里存着登录态，不能关
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);

        // 关键：不加这句，浏览器的自动播放策略会拦掉
        // 背景音乐和快捷语音，点了也没声音
        settings.setMediaPlaybackRequiresUserGesture(false);

        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);

        // 不跟随系统字体大小，免得把牌桌排版撑乱
        settings.setTextZoom(100);

        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(
            WebSettings.MIXED_CONTENT_NEVER_ALLOW
        );

        web.setBackgroundColor(0xFF141009);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        // 长按名片是「送花 / 扔鸡蛋」，别弹出系统的选择菜单
        web.setLongClickable(false);
        web.setHapticFeedbackEnabled(false);

        if (isDebuggable()) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        web.setWebChromeClient(new WebChromeClient());

        web.setWebViewClient(new WebViewClient() {

            @Override
            public void onPageStarted(
                WebView view, String url,
                android.graphics.Bitmap icon
            ) {
                loadFailed = false;
            }

            @Override
            public void onPageFinished(
                WebView view, String url
            ) {
                // 出错页也会触发 onPageFinished，
                // 别把错误界面顶掉
                if (loadFailed) return;

                handler.removeCallbacks(slowHint);
                splash.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(
                WebView view,
                WebResourceRequest request,
                WebResourceError error
            ) {
                if (request.isForMainFrame()) {
                    showError();
                }
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onReceivedError(
                WebView view, int code,
                String description, String failingUrl
            ) {
                // 老版本 Android 走这个重载
                showError();
            }
        });

        retry.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startLoading();
            }
        });

        startLoading();
    }

    private boolean isDebuggable() {
        return (getApplicationInfo().flags
            & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
    }

    private void startLoading() {
        loadFailed = false;

        splash.setVisibility(View.VISIBLE);
        splashTitle.setText("正在连接…");
        splashSub.setText("");
        retry.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(HOME);

        handler.removeCallbacks(slowHint);
        handler.postDelayed(slowHint, SLOW_HINT_MS);
    }

    private void showError() {
        loadFailed = true;

        handler.removeCallbacks(slowHint);

        splash.setVisibility(View.VISIBLE);
        splashTitle.setText("连不上服务器");
        splashSub.setText("检查一下网络，然后点下面重试");
        retry.setVisibility(View.VISIBLE);
        web.setVisibility(View.INVISIBLE);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // 牌桌内部没有「后退」概念，所以能退就退，
        // 退到头再按才退出 App
        if (keyCode == KeyEvent.KEYCODE_BACK
            && web != null
            && web.canGoBack()) {
            web.goBack();
            return true;
        }

        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);

        if (web != null) {
            // 从布局里摘下来再销毁，避免 WebView 泄漏
            ViewGroup parent = (ViewGroup) web.getParent();
            if (parent != null) parent.removeView(web);

            web.destroy();
            web = null;
        }

        super.onDestroy();
    }
}
