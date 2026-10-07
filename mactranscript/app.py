"""原生窗口界面。

和 `ui` 模式跑的是同一个本地服务、同一套界面，区别只是把页面装进一个
WKWebView 原生窗口里，而不是丢给浏览器：有自己的 Dock 图标、菜单栏和
Cmd-Q，看不到地址栏，也不会在浏览器里多出一个标签页。

服务依旧只监听 127.0.0.1，窗口关闭时一并停掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__, web

WIDTH, HEIGHT = 1000, 860
MIN_WIDTH, MIN_HEIGHT = 720, 560


class Bridge:
    """暴露给页面的 Python 接口（window.pywebview.api）。

    WKWebView 不处理 Content-Disposition 附件下载 —— 点「下载」只会让
    webview 导航到那个地址，页面被整个替换掉，文件也没存下来。所以原生
    窗口里改走系统的保存面板。
    """

    def __init__(self) -> None:
        self.window = None

    def save_file(self, filename: str, content: str) -> str | None:
        """弹出系统保存面板，把内容写到用户选的位置。"""
        import webview

        if self.window is None:
            return None
        # 新版用 FileDialog.SAVE，旧版只有 SAVE_DIALOG。
        save_dialog = getattr(webview, "FileDialog", None)
        save_dialog = save_dialog.SAVE if save_dialog else webview.SAVE_DIALOG
        target = self.window.create_file_dialog(
            save_dialog,
            directory=str(Path.home() / "Downloads"),
            save_filename=filename,
        )
        if not target:
            return None  # 用户取消了
        path = Path(target[0] if isinstance(target, (list, tuple)) else target)
        path.write_text(content, encoding="utf-8")
        return str(path)


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
    bridge = Bridge()
    window = webview.create_window(
        "MacTranscript",
        f"http://127.0.0.1:{port}/?app=1",
        js_api=bridge,
        width=WIDTH,
        height=HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
    )
    bridge.window = window

    def on_closing() -> bool:
        """转录中途关窗会白跑一次，先问一句。

        已完成的转录都已落盘，关窗不会丢；只有进行中的这次会作废。
        """
        if not web.has_running_jobs():
            return True
        return bool(window.create_confirmation_dialog(
            "MacTranscript",
            "转录还在进行中，现在退出会丢失这一次的结果。\n已完成的记录不受影响。确定退出吗？",
        ))

    def on_closed() -> None:
        # shutdown() 会等 serve_forever 退出；它跑在后台线程，这里直接调即可。
        httpd.shutdown()
        # 关窗时就把录音删掉，不指望解释器能正常走完退出流程。
        web.cleanup()

    window.events.closing += on_closing
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
