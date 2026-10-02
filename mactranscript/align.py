"""把两个模型的结果缝合起来：为每个词归属到对应的说话人轮次。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .asr import Word
from .diarize import Turn

# 短于此阈值、且前后都是同一位说话人的「跳变」，视为分离模型的抖动，
# 而不是真正的插话。
JITTER_MAX_SECONDS = 0.6

# 同一说话人的相邻分段，间隔小于此值就合并为一个段落，
# 这样句子中间的一次换气不会把转写稿切断。
MERGE_GAP_SECONDS = 1.0

# 距离任何语音轮次都超过此值的词会被丢弃。Whisper 会在长段静音处编造
# 文本（「Thank you.」「[BLANK_AUDIO]」之类），而且给出很高的置信度，
# 因此无法用它自己的分数来过滤 —— 但 pyannote 的语音活动检测可以。
UNMATCHED_MAX_SECONDS = 2.0


@dataclass
class Block:
    """成稿中的一个段落：一位说话人的一次连续发言。"""

    speaker: str
    start: float
    end: float
    text: str


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _distance(word: Word, turn: Turn) -> float:
    """词与轮次之间相隔的秒数；两者重叠时为 0.0。"""
    if word.end < turn.start:
        return turn.start - word.end
    if word.start > turn.end:
        return word.start - turn.end
    return 0.0


def assign_speakers(
    words: list[Word],
    turns: list[Turn],
    *,
    drop_unmatched: bool = True,
) -> list[Word]:
    """为每个词标注与其重叠最多的那位说话人。

    落在轮次之间短小空隙里的词（换气、被切分器截断的词）归给时间上最近
    的轮次。滞留在长段静音中的词则不作标注并被丢弃，除非把
    `drop_unmatched` 设为 False。
    """
    if not words:
        return []

    if not turns:
        # pyannote 在任何位置都没有检测到语音。此时相信它，而不是 Whisper。
        for word in words:
            word.speaker = None if drop_unmatched else "SPEAKER_00"
        return words

    for word in words:
        best_turn, best_overlap = None, 0.0
        for turn in turns:
            if turn.start > word.end:
                break  # 轮次已按时间排序；更靠后的不可能再重叠
            shared = _overlap(word.start, word.end, turn.start, turn.end)
            if shared > best_overlap:
                best_turn, best_overlap = turn, shared

        if best_turn is None:
            # 完全没有重叠：挂到最近的轮次上；若最近的语音远得不合理，
            # 就把这个词丢掉。
            nearest = min(turns, key=lambda t: _distance(word, t))
            if drop_unmatched and _distance(word, nearest) > UNMATCHED_MAX_SECONDS:
                word.speaker = None
                continue
            best_turn = nearest
        word.speaker = best_turn.speaker

    return _smooth(words)


def _smooth(words: list[Word]) -> list[Word]:
    """消除几乎肯定属于抖动的单词级说话人跳变。"""
    for i in range(1, len(words) - 1):
        prev, cur, nxt = words[i - 1], words[i], words[i + 1]
        if (
            prev.speaker is not None
            and cur.speaker is not None
            and prev.speaker == nxt.speaker
            and cur.speaker != prev.speaker
            and (cur.end - cur.start) < JITTER_MAX_SECONDS
        ):
            cur.speaker = prev.speaker
    return words


def build_blocks(words: list[Word]) -> list[Block]:
    """把已标注的词流归并成按说话人划分的段落。"""
    blocks: list[Block] = []
    for word in words:
        if word.speaker is None:
            continue  # 滞留在静音中，几乎肯定是模型幻觉
        if (
            blocks
            and blocks[-1].speaker == word.speaker
            and word.start - blocks[-1].end <= MERGE_GAP_SECONDS
        ):
            blocks[-1].end = max(blocks[-1].end, word.end)
            blocks[-1].text += word.text
        else:
            blocks.append(Block(word.speaker, word.start, word.end, word.text))

    for block in blocks:
        block.text = _tidy(block.text)
    return [b for b in blocks if b.text]


def _tidy(text: str) -> str:
    """整理拼接 Whisper 词元时产生的多余空白。"""
    return re.sub(r"\s+", " ", text).strip()


def name_speakers(blocks: list[Block], names: list[str] | None = None) -> dict[str, str]:
    """把 pyannote 的原始标签映射为展示名称，按首次发言顺序排列。

    这样「说话人 1」始终是最先开口的那个人，使传入的
    `--speakers Alice,Bob` 结果可预期，而不是随机对应。
    """
    order: list[str] = []
    for block in blocks:
        if block.speaker not in order:
            order.append(block.speaker)

    mapping: dict[str, str] = {}
    for index, label in enumerate(order):
        if names and index < len(names):
            mapping[label] = names[index]
        else:
            mapping[label] = f"说话人 {index + 1}"
    return mapping
