"""转录记录持久化的测试。

全程在临时目录里跑，不会碰到真实的「应用程序支持」目录：
    ./.venv/bin/python tests/test_sessions.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须在导入 sessions 之前设置，root() 读的就是它。
os.environ["MACTRANSCRIPT_HOME"] = tempfile.mkdtemp(prefix="mactranscript-test-")

from mactranscript import sessions  # noqa: E402

PAYLOAD = {"blocks": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0, "text": "你好"}],
           "speaker_names": {"SPEAKER_00": "我"}}
META = {"duration_seconds": 33.4, "speakers": ["我", "教授"],
        "block_count": 6, "word_count": 89}

TWO = {
    "speaker_names": {"SPEAKER_00": "说话人 1", "SPEAKER_01": "说话人 2"},
    "blocks": [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0, "text": "先开口的"},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0, "text": "后开口的"},
    ],
}
TWO_META = {"duration_seconds": 4.0, "elapsed": 1.0, "asr_model": "w",
            "diarization_model": "p", "speakers": ["说话人 1", "说话人 2"],
            "block_count": 2, "word_count": 6}


def _save(name="会议.m4a"):
    return sessions.save(name, "# 转写稿", PAYLOAD, META)


def test_保存后能在列表里看到():
    record = _save()
    assert record["id"] in [m["id"] for m in sessions.listing()]
    assert record["name"] == "会议.m4a"


def test_读回完整内容():
    record = _save()
    loaded = sessions.load(record["id"])
    assert loaded["markdown"] == "# 转写稿"
    assert loaded["payload"]["speaker_names"] == {"SPEAKER_00": "我"}
    assert loaded["speakers"] == ["我", "教授"]


def test_列表按时间倒序():
    first = _save("早.m4a")
    second = _save("晚.m4a")
    ids = [m["id"] for m in sessions.listing()]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_删除后就查不到了():
    record = _save()
    assert sessions.delete(record["id"]) is True
    assert sessions.load(record["id"]) is None
    assert record["id"] not in [m["id"] for m in sessions.listing()]


def test_删除不存在的记录返回False():
    assert sessions.delete("20260101-000000-abcdef") is False


def test_挡住路径穿越():
    # id 必须严格匹配格式，否则不去碰文件系统。
    for evil in ("../../etc", "..", "/etc/passwd", "", "whatever"):
        assert sessions.load(evil) is None
        assert sessions.delete(evil) is False


def test_中文文件名能作为目录名():
    record = _save("季度会议 2026.m4a")
    assert sessions.load(record["id"])["name"] == "季度会议 2026.m4a"


def test_改名后成稿与元信息一起更新():
    record = sessions.save("会议.m4a", "旧稿", dict(TWO), dict(TWO_META))
    updated = sessions.rename_speakers(record["id"], {"SPEAKER_00": "教授", "SPEAKER_01": "我"})
    assert updated["speakers"] == ["教授", "我"]
    assert "**教授** · `00:00:00 → 00:00:02`" in updated["markdown"]
    assert "**我** · `00:00:02 → 00:00:04`" in updated["markdown"]


def test_改名会落盘():
    record = sessions.save("会议.m4a", "旧稿", dict(TWO), dict(TWO_META))
    sessions.rename_speakers(record["id"], {"SPEAKER_00": "教授", "SPEAKER_01": "我"})
    reloaded = sessions.load(record["id"])
    assert reloaded["speakers"] == ["教授", "我"]
    assert reloaded["payload"]["speaker_names"]["SPEAKER_00"] == "教授"
    assert "教授" in reloaded["markdown"]


def test_可以反复改名():
    # 原始标签不变，所以改完还能再改 —— 认错两次也救得回来。
    record = sessions.save("会议.m4a", "旧稿", dict(TWO), dict(TWO_META))
    sessions.rename_speakers(record["id"], {"SPEAKER_00": "甲"})
    updated = sessions.rename_speakers(record["id"], {"SPEAKER_00": "乙"})
    assert updated["payload"]["speaker_names"]["SPEAKER_00"] == "乙"


def test_只改传入的那一个():
    record = sessions.save("会议.m4a", "旧稿", dict(TWO), dict(TWO_META))
    updated = sessions.rename_speakers(record["id"], {"SPEAKER_01": "我"})
    assert updated["payload"]["speaker_names"] == {"SPEAKER_00": "说话人 1", "SPEAKER_01": "我"}


def test_空名字与未知标签被忽略():
    record = sessions.save("会议.m4a", "旧稿", dict(TWO), dict(TWO_META))
    updated = sessions.rename_speakers(
        record["id"], {"SPEAKER_00": "   ", "SPEAKER_99": "查无此人"})
    assert updated["payload"]["speaker_names"] == TWO["speaker_names"]


def test_给不存在的记录改名返回None():
    assert sessions.rename_speakers("20260101-000000-abcdef", {"X": "Y"}) is None
    assert sessions.rename_speakers("../../etc", {"X": "Y"}) is None


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
