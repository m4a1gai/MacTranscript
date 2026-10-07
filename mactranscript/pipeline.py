"""把各环节串成一条可复用的流程，供命令行与网页界面共用。

抽出来的原因是两个入口需要完全相同的处理顺序和参数含义；进度回调让
调用方自己决定怎么展示（终端打印，或推给浏览器）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import asr, diarize
from .align import assign_speakers, build_blocks, name_speakers
from .audio import SAMPLE_RATE, decode, duration_of
from .render import render

# 各环节在总进度中的占比。依据 M2 Pro 实测：分离与转写耗时基本相当。
STAGE_WEIGHTS = {
    "decode": 0.03,
    "diarize": 0.45,
    "transcribe": 0.45,
    "align": 0.07,
}
STAGE_ORDER = ["decode", "diarize", "transcribe", "align"]

# pyannote 内部的已知步骤，用于把它的 hook 换算成百分比。
DIARIZE_STEPS = ["segmentation", "speaker_counting", "embeddings", "discrete_diarization"]

# 进度回调：(环节, 说明, 总体进度 0..1)
ProgressFn = Callable[[str, str, float], None]

# 说话人名字的分隔符。中文输入法下打出来的是全角逗号和顿号，只认半角
# 逗号的话，「我，教授」会被当成一个人的名字。
NAME_SEPARATORS = r"[,，、;；\s]+"


def parse_speaker_names(raw: str | None) -> list[str] | None:
    """把用户输入的名字串切成列表；没写就返回 None。"""
    if not raw:
        return None
    names = [n.strip() for n in re.split(NAME_SEPARATORS, raw) if n.strip()]
    return names or None


@dataclass
class Result:
    """一次完整转写的产出。"""

    markdown: str
    speaker_names: dict[str, str]
    audio_seconds: float
    elapsed: float
    language: str | None
    dropped_words: int
    word_count: int
    blocks: list = field(default_factory=list)
    turns: list = field(default_factory=list)


def _overall(stage: str, inner: float) -> float:
    """把某环节内部的进度换算成总体进度。"""
    done = sum(STAGE_WEIGHTS[s] for s in STAGE_ORDER[: STAGE_ORDER.index(stage)])
    return min(1.0, done + STAGE_WEIGHTS[stage] * max(0.0, min(1.0, inner)))


def run(
    audio_path: str | Path,
    *,
    num_speakers: int | None = 2,
    speaker_names: list[str] | None = None,
    language: str | None = "en",
    model: str = asr.DEFAULT_MODEL,
    diarization_model: str = diarize.DEFAULT_MODEL,
    device: str = "auto",
    prompt: str | None = None,
    keep_unmatched: bool = False,
    on_progress: ProgressFn | None = None,
    verbose: bool = False,
) -> Result:
    """跑完整条流程，返回 Markdown 及配套数据。

    会原样抛出 AudioError 与 DiarizationError，由调用方决定如何呈现。
    """
    source = Path(audio_path)
    started = time.time()

    def emit(stage: str, detail: str, inner: float = 1.0) -> None:
        if on_progress:
            on_progress(stage, detail, _overall(stage, inner))

    # 1. 只解码一次，同一份采样交给两个模型。
    emit("decode", f"正在解码 {source.name}", 0.0)
    audio = decode(source)
    seconds = duration_of(audio)
    emit("decode", f"共 {seconds:.0f} 秒音频", 1.0)

    # 2. 先做说话人分离：配置类问题会在这一步暴露，比在耗时的转写之后
    #    才失败要友好得多。
    emit("diarize", "正在加载分离模型", 0.0)
    seen: list[str] = []

    def hook(step_name, _artifact, file=None, total=None, completed=None):
        """把 pyannote 的内部步骤换算成进度。"""
        if step_name not in seen:
            seen.append(step_name)
        index = seen.index(step_name)
        inner = (completed / total) if (total and completed is not None) else 0.0
        span = max(len(DIARIZE_STEPS), len(seen))
        emit("diarize", f"正在识别说话人（{step_name}）", (index + inner) / span)

    turns = diarize.diarize(
        audio,
        SAMPLE_RATE,
        num_speakers=num_speakers,
        model=diarization_model,
        device=device,
        verbose=verbose,
        # 终端下交给 pyannote 自己的 ProgressHook 画进度条；只有网页界面
        # （verbose=False 且提供了回调）才需要把内部步骤转成百分比，
        # 否则这些事件会在终端里刷成几十行重复文本。
        hook=None if verbose else (hook if on_progress else None),
    )
    found = len({t.speaker for t in turns})
    emit("diarize", f"得到 {len(turns)} 个轮次，共 {found} 位说话人", 1.0)

    # 3. 说了什么。mlx-whisper 没有进度回调，所以这里只报告开始与结束，
    #    由界面按实测吞吐（约 16 倍速）自行推算剩余时间。
    emit("transcribe", "正在转写", 0.0)
    words, raw = asr.transcribe(
        audio,
        model=model,
        language=language,
        initial_prompt=prompt,
        verbose=verbose,
    )
    emit("transcribe", f"共 {len(words)} 个词", 1.0)

    # 4. 合并两条时间线。
    emit("align", "正在对齐并生成 Markdown", 0.0)
    labelled = assign_speakers(words, turns, drop_unmatched=not keep_unmatched)
    dropped = sum(1 for w in labelled if w.speaker is None)
    blocks = build_blocks(labelled)
    names = name_speakers(blocks, speaker_names)
    elapsed = time.time() - started

    markdown = render(
        blocks,
        names,
        source=source,
        audio_seconds=seconds,
        asr_model=model,
        diarization_model=diarization_model,
        elapsed=elapsed,
    )
    emit("align", f"完成，共 {len(blocks)} 个段落", 1.0)

    return Result(
        markdown=markdown,
        speaker_names=names,
        audio_seconds=seconds,
        elapsed=elapsed,
        language=raw.get("language"),
        dropped_words=dropped,
        word_count=len(words),
        blocks=blocks,
        turns=turns,
    )
