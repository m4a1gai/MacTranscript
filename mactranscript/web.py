"""本地网页界面。

只用标准库实现，因此装好依赖后不需要再多装任何东西。服务只监听
127.0.0.1，音频文件原封不动留在本机的临时目录里。

上传走的是「裸请求体」而不是 multipart：浏览器可以直接把 File 对象当成
请求体发出来，服务端按 Content-Length 读完即可，省掉一个解析器 ——
顺带避开了 Python 3.13 起已移除的 cgi 模块。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, asr, diarize, pipeline
from .audio import AudioError, find_tool, probe_duration

STATIC = Path(__file__).parent / "static"

# 单次上传的上限，防止手滑把几十 GB 的文件塞进内存里的队列。
MAX_UPLOAD_BYTES = 2 * 1024**3  # 2 GiB

# 从 .app 启动时没有终端可以按 Control-C，所以界面上要有一个停止入口。
_server: ThreadingHTTPServer | None = None

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
# 目录名里带上 PID：进程被强杀后，下次启动就能准确认出哪些是孤儿目录，
# 不必靠「超过几小时就删」这种可能误伤并发实例的猜测。
_uploads = Path(tempfile.mkdtemp(prefix=f"mactranscript-{os.getpid()}-"))
atexit.register(lambda: shutil.rmtree(_uploads, ignore_errors=True))


def _job_update(job_id: str, **fields) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _job_snapshot(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _run_job(job_id: str, path: Path, options: dict) -> None:
    """在后台线程里跑完一条流程，并把进度写进任务状态。"""

    def on_progress(stage: str, detail: str, percent: float) -> None:
        _job_update(job_id, stage=stage, detail=detail, percent=percent)

    try:
        result = pipeline.run(
            path,
            num_speakers=options["num_speakers"],
            speaker_names=options["speaker_names"],
            language=options["language"],
            model=options["model"],
            device=options["device"],
            prompt=options["prompt"],
            keep_unmatched=options["keep_unmatched"],
            on_progress=on_progress,
        )
    except (AudioError, diarize.DiarizationError) as exc:
        _job_update(job_id, state="error", error=str(exc), percent=0.0)
        return
    except Exception as exc:  # noqa: BLE001 - 界面上要看到真实原因
        _job_update(
            job_id,
            state="error",
            error=f"{type(exc).__name__}: {exc}",
            trace=traceback.format_exc(limit=5),
            percent=0.0,
        )
        return

    _job_update(
        job_id,
        state="done",
        percent=1.0,
        stage="done",
        detail=f"完成，共 {len(result.blocks)} 个段落",
        markdown=result.markdown,
        audio_seconds=result.audio_seconds,
        elapsed=result.elapsed,
        language=result.language,
        dropped_words=result.dropped_words,
        word_count=result.word_count,
        speakers=list(dict.fromkeys(result.speaker_names.values())),
        # 界面直接拿这个渲染，说话人已经换成展示名称。
        display_blocks=[
            {
                "speaker": result.speaker_names.get(b.speaker, b.speaker),
                "start": b.start,
                "end": b.end,
                "text": b.text,
            }
            for b in result.blocks
        ],
        payload={
            "source": path.name,
            "duration_seconds": result.audio_seconds,
            "language": result.language,
            "speaker_names": result.speaker_names,
            "turns": [vars(t) for t in result.turns],
            "blocks": [vars(b) for b in result.blocks],
        },
    )


def _environment() -> dict:
    """界面启动时显示的环境自检结果。"""
    info: dict = {"version": __version__, "ok": True, "problems": []}

    info["ffmpeg"] = bool(find_tool("ffmpeg"))
    if not info["ffmpeg"]:
        info["ok"] = False
        info["problems"].append("缺少 ffmpeg，请执行：brew install ffmpeg")

    try:
        import torch

        info["mps"] = bool(torch.backends.mps.is_available())
        info["torch"] = torch.__version__
    except Exception as exc:  # noqa: BLE001
        info["mps"] = False
        info["ok"] = False
        info["problems"].append(f"PyTorch 不可用：{exc}")

    token = diarize.resolve_token()
    info["token"] = bool(token)
    if not token:
        info["ok"] = False
        info["problems"].append(diarize.TOKEN_HELP)
    else:
        try:
            diarize.check_access(diarize.DEFAULT_MODEL, token)
            info["model_access"] = True
        except diarize.DiarizationError as exc:
            info["model_access"] = False
            info["ok"] = False
            info["problems"].append(str(exc))

    info["asr_model"] = asr.DEFAULT_MODEL
    info["diarization_model"] = diarize.DEFAULT_MODEL
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = f"MacTranscript/{__version__}"

    # 默认实现会把每个请求都打到 stderr，本地界面轮询很频繁，太吵。
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass

    # ---------- 工具 ----------

    def _send(self, code: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # 这是本机工具，明确禁止缓存，免得改完界面看到旧版本。
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        path = route.path

        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/api/env":
            self._json(HTTPStatus.OK, _environment())
        elif path.startswith("/api/jobs/"):
            self._job_route(path)
        else:
            self._error(HTTPStatus.NOT_FOUND, "没有这个地址")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        if route.path == "/api/quit":
            self._json(HTTPStatus.OK, {"stopping": True})
            # 必须另起线程：shutdown() 会等 serve_forever 退出，
            # 在当前请求线程里调用会死锁。
            if _server is not None:
                threading.Thread(target=_server.shutdown, daemon=True).start()
            return
        if route.path != "/api/jobs":
            self._error(HTTPStatus.NOT_FOUND, "没有这个地址")
            return
        self._create_job(parse_qs(route.query))

    # ---------- 各处理器 ----------

    def _serve_static(self, name: str, content_type: str) -> None:
        target = STATIC / name
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, f"缺少静态文件 {name}")
            return
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    def _job_route(self, path: str) -> None:
        parts = path[len("/api/jobs/"):].split("/")
        job = _job_snapshot(parts[0])
        if job is None:
            self._error(HTTPStatus.NOT_FOUND, "任务不存在")
            return

        # 下载成品：/api/jobs/<id>/file?fmt=md|json
        if len(parts) > 1 and parts[1] == "file":
            if job["state"] != "done":
                self._error(HTTPStatus.CONFLICT, "任务尚未完成")
                return
            fmt = parse_qs(urlparse(self.path).query).get("fmt", ["md"])[0]
            stem = Path(job["name"]).stem
            if fmt == "json":
                body = json.dumps(job["payload"], indent=2,
                                  ensure_ascii=False).encode("utf-8")
                filename, ctype = f"{stem}.json", "application/json; charset=utf-8"
            else:
                body = job["markdown"].encode("utf-8")
                filename, ctype = f"{stem}.md", "text/markdown; charset=utf-8"
            # filename* 用 RFC 5987 编码，中文文件名才不会乱码。
            quoted = filename.encode("utf-8").hex()
            quoted = "".join(f"%{quoted[i:i+2]}" for i in range(0, len(quoted), 2))
            self._send(HTTPStatus.OK, body, ctype,
                       {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
            return

        # 状态查询：markdown 正文只在完成后带上，轮询时不必反复传。
        light = {k: v for k, v in job.items() if k not in ("payload", "markdown")}
        if job["state"] == "done":
            light["markdown"] = job["markdown"]
        self._json(HTTPStatus.OK, light)

    def _create_job(self, query: dict[str, list[str]]) -> None:
        def one(key: str, default: str = "") -> str:
            return (query.get(key, [default])[0] or "").strip()

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._error(HTTPStatus.BAD_REQUEST, "请求体是空的")
            return
        if length > MAX_UPLOAD_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        f"文件超过 {MAX_UPLOAD_BYTES // 1024**3} GiB 上限")
            return

        name = Path(one("name", "recording.m4a")).name or "recording.m4a"
        job_id = uuid.uuid4().hex[:12]
        # 每个任务一个子目录，这样文件保留原名 —— 转写稿标题和 JSON 里的
        # source 用的都是这个名字，不能掺进内部 id。
        target = _uploads / job_id / name
        target.parent.mkdir(parents=True, exist_ok=True)

        # 分块落盘，避免把整个文件读进内存。
        remaining = length
        try:
            with target.open("wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 512, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"写入临时文件失败：{exc}")
            return
        if remaining > 0:
            target.unlink(missing_ok=True)
            self._error(HTTPStatus.BAD_REQUEST, "上传中断，文件不完整")
            return

        raw_speakers = one("speakers")
        raw_count = one("num_speakers", "2")
        language = one("language", "en")
        options = {
            "num_speakers": None if raw_count in ("auto", "0", "") else int(raw_count),
            "speaker_names": [n.strip() for n in raw_speakers.split(",") if n.strip()] or None,
            "language": None if language == "auto" else language,
            "model": one("model") or asr.DEFAULT_MODEL,
            "device": one("device", "auto") or "auto",
            "prompt": one("prompt") or None,
            "keep_unmatched": one("keep_unmatched") == "1",
        }

        with _jobs_lock:
            _jobs[job_id] = {
                "id": job_id,
                "name": name,
                "state": "running",
                "stage": "decode",
                "detail": "正在排队",
                "percent": 0.0,
                "bytes": length,
                "audio_seconds": probe_duration(target),
                "created": time.time(),
            }

        threading.Thread(target=_run_job, args=(job_id, target, options),
                         daemon=True).start()
        self._json(HTTPStatus.ACCEPTED, {"id": job_id})


def find_port(start: int = 8765, tries: int = 12) -> int | None:
    """找一个空闲端口；全被占用时返回 None。"""
    import socket

    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def cleanup() -> None:
    """删掉本次运行的上传目录。"""
    shutil.rmtree(_uploads, ignore_errors=True)


def sweep_orphaned() -> None:
    """删掉已退出进程遗留的上传目录。

    从 Dock 右键退出或按 Cmd-Q 时，macOS 会直接终止进程，Python 的清理
    逻辑根本来不及跑，用户的录音就留在 /var/folders 里了。启动时按目录名
    里的 PID 判断：进程没了就删，还活着就留着（可能是并发的另一个实例）。
    """
    for path in Path(tempfile.gettempdir()).glob("mactranscript-*"):
        if path == _uploads or not path.is_dir():
            continue
        parts = path.name.split("-")
        if len(parts) < 3 or not parts[1].isdigit():
            continue  # 旧版本留下的，名字里没有 PID
        try:
            os.kill(int(parts[1]), 0)  # 不发信号，只探测进程在不在
        except ProcessLookupError:
            shutil.rmtree(path, ignore_errors=True)
        except (PermissionError, ValueError):
            pass  # 进程还在，或名字对不上，都别动


def make_server(port: int) -> ThreadingHTTPServer:
    """在回环地址上建服务，并记下实例供 /api/quit 使用。"""
    global _server

    sweep_orphaned()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _server = httpd
    return httpd


def start_background(port: int) -> ThreadingHTTPServer:
    """在后台线程里跑服务并立即返回 —— 原生窗口要占用主线程。"""
    httpd = make_server(port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve(argv: list[str] | None = None) -> int:
    """启动本地网页界面。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="mactranscript ui", description="启动本地网页界面。")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认：8765）")
    parser.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args(argv or [])

    # 只绑定回环地址：同一网络里的其他机器无法访问。
    try:
        httpd = make_server(args.port)
    except OSError as exc:
        print(f"错误：无法监听 127.0.0.1:{args.port} —— {exc}")
        print("端口可能已被占用，换一个：./ui.sh --port 8780")
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    print(f"MacTranscript {__version__} 网页界面已启动")
    print(f"  打开：{url}")
    print(f"  临时文件：{_uploads}")
    print("  按 Control-C 停止\n")

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止……")
    finally:
        httpd.server_close()
        cleanup()
    return 0
