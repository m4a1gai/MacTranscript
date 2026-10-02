"""把对齐后的说话人分段渲染成 Markdown。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .align import Block


def timestamp(seconds: float) -> str:
    """把时间位置格式化为 HH:MM:SS。"""
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _duration(seconds: float) -> str:
    """便于阅读的时长，例如「12 分 04 秒」。"""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} 小时 {minutes:02d} 分 {secs:02d} 秒"
    if minutes:
        return f"{minutes} 分 {secs:02d} 秒"
    return f"{secs} 秒"


def render(
    blocks: list[Block],
    names: dict[str, str],
    *,
    source: Path,
    audio_seconds: float,
    asr_model: str,
    diarization_model: str,
    elapsed: float | None = None,
) -> str:
    """生成完整的 Markdown 转写稿。"""
    speakers = list(dict.fromkeys(names.values()))  # 去重，并保持出场顺序

    lines: list[str] = [
        f"# 转写稿：{source.name}",
        "",
        f"- **源文件：** `{source.name}`",
        f"- **时长：** {_duration(audio_seconds)}",
        f"- **说话人：** {len(speakers)} 位（{'、'.join(speakers)}）",
        f"- **语音识别：** `{asr_model}`",
        f"- **说话人分离：** `{diarization_model}`",
        f"- **生成时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        f" —— 全程在本机离线完成",
    ]
    if elapsed is not None:
        speed = audio_seconds / elapsed if elapsed > 0 else 0
        lines.append(f"- **处理耗时：** {_duration(elapsed)}（{speed:.1f} 倍速）")

    lines += ["", "---", ""]

    if not blocks:
        lines += ["_本段录音中未检测到语音。_", ""]
        return "\n".join(lines)

    for block in blocks:
        speaker = names.get(block.speaker, block.speaker)
        span = f"{timestamp(block.start)} → {timestamp(block.end)}"
        lines += [f"**{speaker}** · `{span}`", "", block.text, ""]

    return "\n".join(lines)
