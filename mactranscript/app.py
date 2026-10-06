"""原生窗口界面。

和 `ui` 模式跑的是同一个本地服务、同一套界面，区别只是把页面装进一个
WKWebView 原生窗口里，而不是丢给浏览器：有自己的 Dock 图标、菜单栏和
Cmd-Q，看不到地址栏，也不会在浏览器里多出一个标签页。

服务依旧只监听 127.0.0.1，窗口关闭时一并停掉。
"""

from __future__ import annotations

import sys

from . import __version__, web

WIDTH, HEIGHT = 1000, 860
MIN_WIDTH, MIN_HEIGHT = 720, 560


def run(argv: list[str] | None = None) -> int:
    """打开原生窗口。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="mactranscript app", description="在原生窗口中打开界面。")
    parser.add_argument("--debug", action="store_true",
                        help="启用网页检查器（右键 → 检查元素）")
    args = parser.parse_args(argv or [])

    try:
        import webview
    except ImportError:
        print(
            "错误：缺少 pywebview，无法打开原生窗口。\n"
            "请运行：./install.sh（或 .venv/bin/pip install pywebview）\n"
            "也可以改用浏览器模式：./ui.sh",
            file=sys.stderr,
        )
        return 1

    port = web.find_port()
    if port is None:
        print("错误：找不到空闲端口（8765-8776 都被占用）。", file=sys.stderr)
        return 1

    httpd = web.start_background(port)

    # app=1 让页面知道自己在原生窗口里：隐藏「停止服务」按钮，
    # 因为关窗口就等于退出，再留一个按钮只会让人困惑。
    window = webview.create_window(
        "MacTranscript",
        f"http://127.0.0.1:{port}/?app=1",
        width=WIDTH,
        height=HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
    )

    def on_closed() -> None:
        # shutdown() 会等 serve_forever 退出；它跑在后台线程，这里直接调即可。
        httpd.shutdown()
        # 关窗时就把录音删掉，不指望解释器能正常走完退出流程。
        web.cleanup()

    window.events.closed += on_closed

    # Cmd-Q 或从 Dock 退出时，webview 不会走到上面的 closed 回调，
    # 进程被直接终止。挂一个 Cocoa 终止通知，赶在那之前把录音删掉。
    # observer 必须留着引用：一旦被回收，通知就不会再送达。
    observer = None
    try:
        import AppKit

        observer = AppKit.NSNotificationCenter.defaultCenter().\
            addObserverForName_object_queue_usingBlock_(
                "NSApplicationWillTerminateNotification", None, None,
                lambda _note: web.cleanup(),
            )
    except Exception:  # noqa: BLE001 - 挂不上就退回启动时清理，不影响使用
        pass

    try:
        webview.start(debug=args.debug)
    finally:
        del observer  # 到这里通知已经没有意义了
        httpd.server_close()
        web.cleanup()
    return 0
