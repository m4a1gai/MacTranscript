"""词级说话人对齐与渲染的测试。

不需要加载任何模型，因此可以在极短时间内跑完：
    ./.venv/bin/python tests/test_align.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mactranscript.align import (  # noqa: E402
    assign_speakers,
    build_blocks,
    name_speakers,
)
from mactranscript.asr import Word  # noqa: E402
from mactranscript.diarize import Turn  # noqa: E402
from mactranscript.render import render, timestamp  # noqa: E402

A, B = "SPEAKER_00", "SPEAKER_01"


def test_按最大重叠归属说话人():
    words = [Word(" hello", 0.0, 1.0), Word(" there", 5.0, 6.0)]
    turns = [Turn(0.0, 2.0, A), Turn(4.0, 7.0, B)]
    assert [w.speaker for w in assign_speakers(words, turns)] == [A, B]


def test_跨边界的词归给占比更大的一方():
    # 这个词有 0.8 秒落在 A 的轮次里，0.2 秒落在 B 的轮次里。
    words = [Word(" boundary", 1.2, 2.2)]
    turns = [Turn(0.0, 2.0, A), Turn(2.0, 4.0, B)]
    assert assign_speakers(words, turns)[0].speaker == A


def test_空隙中的词归给最近的轮次():
    words = [Word(" orphan", 2.4, 2.6)]
    turns = [Turn(0.0, 2.0, A), Turn(5.0, 7.0, B)]
    assert assign_speakers(words, turns)[0].speaker == A


def test_单个短跳变被平滑掉():
    words = [
        Word(" one", 0.0, 0.5),
        Word(" two", 0.5, 0.9),     # 0.4 秒的抖动，被错误归给了 B
        Word(" three", 0.9, 1.4),
    ]
    turns = [Turn(0.0, 0.5, A), Turn(0.5, 0.9, B), Turn(0.9, 1.4, A)]
    assert [w.speaker for w in assign_speakers(words, turns)] == [A, A, A]


def test_较长的插话不会被平滑掉():
    words = [
        Word(" one", 0.0, 0.5),
        Word(" interrupting", 0.6, 2.0),  # 1.4 秒是真实插话，不是抖动
        Word(" three", 2.1, 2.6),
    ]
    turns = [Turn(0.0, 0.5, A), Turn(0.6, 2.0, B), Turn(2.1, 2.6, A)]
    assert [w.speaker for w in assign_speakers(words, turns)] == [A, B, A]


def test_没有轮次即视为没有语音():
    # Whisper 会在静音处产生幻觉；以 pyannote 未检测到语音为准。
    words = [Word(" Thank", 0.0, 0.5), Word(" you.", 0.5, 1.0)]
    assert build_blocks(assign_speakers(words, [])) == []


def test_没有轮次时可以强制保留文本():
    words = [Word(" hello", 0.0, 1.0), Word(" world", 1.0, 2.0)]
    blocks = build_blocks(assign_speakers(words, [], drop_unmatched=False))
    assert len(blocks) == 1
    assert blocks[0].text == "hello world"


def test_远离所有语音的孤立词被丢弃():
    words = [Word(" real", 0.0, 1.0), Word(" hallucinated", 60.0, 61.0)]
    blocks = build_blocks(assign_speakers(words, [Turn(0.0, 2.0, A)]))
    assert [b.text for b in blocks] == ["real"]


def test_紧贴轮次边缘的词会被保留():
    # 超出轮次 0.5 秒：属于被切断的词，而不是幻觉。
    words = [Word(" edge", 2.3, 2.5)]
    blocks = build_blocks(assign_speakers(words, [Turn(0.0, 2.0, A)]))
    assert [b.text for b in blocks] == ["edge"]


def test_同一说话人的连续词合并为一个段落():
    words = [Word(" a", 0.0, 0.4), Word(" b", 0.5, 0.9), Word(" c", 1.0, 1.4)]
    blocks = build_blocks(assign_speakers(words, [Turn(0.0, 2.0, A)]))
    assert len(blocks) == 1
    assert blocks[0].text == "a b c"
    assert blocks[0].start == 0.0 and blocks[0].end == 1.4


def test_长停顿即使同一说话人也会分段():
    words = [Word(" before", 0.0, 0.5), Word(" after", 30.0, 30.5)]
    blocks = build_blocks(assign_speakers(words, [Turn(0.0, 40.0, A)]))
    assert [b.text for b in blocks] == ["before", "after"]


def test_说话人切换时开启新段落():
    words = [Word(" hi", 0.0, 1.0), Word(" bye", 3.0, 4.0)]
    turns = [Turn(0.0, 2.0, A), Turn(2.5, 5.0, B)]
    blocks = build_blocks(assign_speakers(words, turns))
    assert [b.speaker for b in blocks] == [A, B]


def test_名字按首次发言顺序分配():
    words = [Word(" second", 3.0, 4.0), Word(" first", 0.0, 1.0)]
    # B 在第 0 秒开口，A 在第 3 秒，因此 B 必须对应「张三」。
    turns = [Turn(0.0, 2.0, B), Turn(2.5, 5.0, A)]
    blocks = build_blocks(assign_speakers(sorted(words, key=lambda w: w.start), turns))
    names = name_speakers(blocks, ["张三", "李四"])
    assert names[B] == "张三" and names[A] == "李四"


def test_默认名字带编号():
    blocks = build_blocks(assign_speakers([Word(" x", 0.0, 1.0)], [Turn(0.0, 2.0, A)]))
    assert name_speakers(blocks, None) == {A: "说话人 1"}


def test_时间戳格式含小时():
    assert timestamp(0) == "00:00:00"
    assert timestamp(61.4) == "00:01:01"
    assert timestamp(3725) == "01:02:05"


def test_渲染结果包含说话人时间与正文():
    words = [Word(" hello", 0.0, 1.0), Word(" there", 3.0, 4.0)]
    turns = [Turn(0.0, 2.0, A), Turn(2.5, 5.0, B)]
    blocks = build_blocks(assign_speakers(words, turns))
    md = render(
        blocks,
        name_speakers(blocks, ["张三", "李四"]),
        source=Path("chat.m4a"),
        audio_seconds=5.0,
        asr_model="whisper",
        diarization_model="pyannote",
    )
    assert "# 转写稿：chat.m4a" in md
    assert "**张三** · `00:00:00 → 00:00:01`" in md
    assert "**李四** · `00:00:03 → 00:00:04`" in md
    assert "hello" in md and "there" in md


def test_渲染能处理全静音():
    md = render([], {}, source=Path("quiet.m4a"), audio_seconds=1.0,
                asr_model="w", diarization_model="p")
    assert "未检测到语音" in md


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  失败 {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} 项通过")
    sys.exit(1 if failures else 0)
