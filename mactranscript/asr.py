"""语音识别：Whisper 经 MLX 在 Apple Silicon GPU 上运行。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

# large-v3-turbo 是 Apple Silicon 上精度与速度的平衡点：接近 large-v3 的
# 质量，但算力开销只有一小部分。其他可选模型见 README。
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


@dataclass
class Word:
    """一个识别出的词，连同它被说出的时间区间。"""

    text: str      # 保留 Whisper 给出的前导空格，便于 "".join() 还原原文
    start: float
    end: float
    speaker: str | None = None


def transcribe(
    audio: np.ndarray,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = "en",
    initial_prompt: str | None = None,
    verbose: bool = False,
) -> tuple[list[Word], dict]:
    """转写已解码的音频缓冲区，并返回逐词时间戳。

    逐词时间戳正是说话人归属能够精确的关键：Whisper 的单个 segment 经常
    横跨一次说话人切换，而单个词不会。
    """
    import mlx_whisper

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model,
        language=language,
        word_timestamps=True,
        initial_prompt=initial_prompt,
        # 抑制 Whisper 在长段静音处凭空编造文本的倾向。
        hallucination_silence_threshold=2.0,
        # False 显示进度条，None 则完全不输出。
        verbose=False if verbose else None,
    )
    return list(_iter_words(result)), result


def _iter_words(result: dict) -> Iterator[Word]:
    """把 Whisper 的 segment 树铺平成单一的带时间戳词流。"""
    for segment in result.get("segments", []):
        words = segment.get("words") or []
        if words:
            for w in words:
                text = w.get("word") or ""
                if not text.strip():
                    continue
                yield Word(text, float(w["start"]), float(w["end"]))
        else:
            # 少见情况：某个 segment 没有逐词时间戳。此时保留文本而不是
            # 丢弃它，并让它沿用该 segment 较粗的时间区间。
            text = (segment.get("text") or "").strip()
            if text:
                yield Word(" " + text, float(segment["start"]), float(segment["end"]))
