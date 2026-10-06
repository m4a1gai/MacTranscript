"""音频解码。下游所有环节统一使用 16 kHz 单声道 float32。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

# Whisper 和 pyannote 都是在 16 kHz 上训练的；在这里一次性重采样，
# 两个库就都不必再去碰原始容器格式。
SAMPLE_RATE = 16_000


# 从 Finder 双击启动的 app 继承不到登录 shell 的 PATH —— GUI 会话的默认
# PATH 里没有 Homebrew 目录，于是 shutil.which 会白白找不到 ffmpeg。
# 所以除了 PATH，还要去这几个常见安装位置看一眼。
EXTRA_TOOL_DIRS = (
    "/opt/homebrew/bin",  # Apple Silicon 版 Homebrew
    "/usr/local/bin",     # Intel 版 Homebrew
    "/opt/local/bin",     # MacPorts
)


class AudioError(RuntimeError):
    """文件找不到或无法解码时抛出。"""


def find_tool(name: str) -> str | None:
    """先按 PATH 找，再退回到常见安装位置。找不到返回 None。"""
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_TOOL_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_ffmpeg() -> str:
    exe = find_tool("ffmpeg")
    if exe is None:
        raise AudioError(
            "找不到 ffmpeg。\n"
            "请执行：brew install ffmpeg"
        )
    return exe


def decode(path: str | Path) -> np.ndarray:
    """把任意 ffmpeg 可读的音频文件解码为 16 kHz 单声道 float32。

    这里直接从 ffmpeg 管道读取原始采样，而不写临时 WAV 文件；同一份数组
    会交给两个模型使用，因此文件只需解码一次。
    """
    src = Path(path).expanduser()
    if not src.exists():
        raise AudioError(f"音频文件不存在：{src}")
    if src.is_dir():
        raise AudioError(f"{src} 是一个目录，不是音频文件。")

    cmd = [
        find_ffmpeg(),
        "-nostdin",
        "-threads", "0",
        "-i", str(src),
        "-f", "f32le",            # 原始 32 位浮点，小端
        "-acodec", "pcm_f32le",
        "-ac", "1",               # 混为单声道
        "-ar", str(SAMPLE_RATE),
        "-loglevel", "error",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise AudioError(f"ffmpeg 无法解码 {src.name}：\n{detail}")

    # frombuffer 返回只读视图，复制一份以便 torch.from_numpy 正常使用。
    audio = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    if audio.size == 0:
        raise AudioError(f"{src.name} 解码后没有任何采样 —— 它确实包含音频吗？")
    return audio


def probe_duration(path: str | Path) -> float | None:
    """用 ffprobe 快速读出时长，不解码整个文件。取不到就返回 None。"""
    exe = find_tool("ffprobe")
    if exe is None:
        return None
    proc = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
    )
    try:
        return float(proc.stdout.decode().strip())
    except (ValueError, AttributeError):
        return None


def duration_of(audio: np.ndarray) -> float:
    """已解码缓冲区的长度，单位为秒。"""
    return len(audio) / SAMPLE_RATE
