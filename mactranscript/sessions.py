"""转录记录的持久化。

每次转录完成就落盘。界面关掉、应用退出、甚至中途崩溃，之前的成果都还在，
不必重跑一遍几分钟的转写。

存在「应用程序支持」目录而不是临时目录：临时目录会被系统清理，也会被
web.sweep_orphaned() 当成上传残留删掉。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

APP_NAME = "MacTranscript"


def root() -> Path:
    """记录存放目录。可用环境变量覆盖，便于测试。"""
    override = os.environ.get("MACTRANSCRIPT_HOME")
    base = Path(override) if override else (
        Path.home() / "Library" / "Application Support" / APP_NAME
    )
    path = base / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(name: str) -> str:
    """把文件名压成安全的目录名片段，中文照样保留。"""
    stem = Path(name).stem[:40]
    cleaned = re.sub(r"[^\w一-鿿-]+", "-", stem, flags=re.UNICODE)
    return cleaned.strip("-") or "recording"


def save(name: str, markdown: str, payload: dict, meta: dict) -> dict:
    """写入一条记录，返回它的元信息。"""
    created = datetime.now()
    session_id = f"{created:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    folder = root() / session_id
    folder.mkdir(parents=True, exist_ok=True)

    record = {
        "id": session_id,
        "name": name,
        # 精确到微秒。只存到秒的话，同一秒内保存的两条记录时间戳完全相同，
        # 列表排序就会变成不确定的；毫秒也不够 —— 一次保存不到 1 毫秒。
        "created": created.isoformat(timespec="microseconds"),
        **meta,
    }
    (folder / "transcript.md").write_text(markdown, encoding="utf-8")
    (folder / "data.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "meta.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def _read_meta(folder: Path) -> dict | None:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta["id"] = folder.name  # 以目录名为准，防止手工改动过 meta
    return meta


def listing() -> list[dict]:
    """全部记录，最新的在前。"""
    items = [m for folder in root().iterdir() if folder.is_dir()
             for m in [_read_meta(folder)] if m]
    # 以 id 作二级键，时间戳万一仍然相同（比如旧记录只有秒精度）也能稳定排序。
    items.sort(key=lambda m: (m.get("created", ""), m.get("id", "")), reverse=True)
    return items


def _folder(session_id: str) -> Path | None:
    """定位记录目录，并挡住路径穿越。"""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", session_id or ""):
        return None
    folder = root() / session_id
    return folder if folder.is_dir() else None


def load(session_id: str) -> dict | None:
    """读出一条完整记录：元信息 + Markdown + 结构化数据。"""
    folder = _folder(session_id)
    if folder is None:
        return None
    meta = _read_meta(folder)
    if meta is None:
        return None
    try:
        meta["markdown"] = (folder / "transcript.md").read_text(encoding="utf-8")
        meta["payload"] = json.loads((folder / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta


def rename_speakers(session_id: str, mapping: dict[str, str]) -> dict | None:
    """改写说话人的展示名，并按新名字重新生成成稿。

    分离模型只能分出「有几个人、各自在什么时候说话」，它分不出谁是谁 ——
    所以默认按首次发言顺序编号。听过之后发现认反了是很正常的事，这里让
    结果可以事后纠正，而不必重跑一遍转写。

    原始标签（SPEAKER_00…）保持不变，只改展示名，因此可以反复调整。
    """
    from .align import Block
    from .render import render

    record = load(session_id)
    if record is None:
        return None

    payload = record["payload"]
    names = dict(payload.get("speaker_names") or {})
    for raw, display in mapping.items():
        if raw in names and str(display).strip():
            names[raw] = str(display).strip()
    payload["speaker_names"] = names

    blocks = [
        Block(b["speaker"], float(b["start"]), float(b["end"]), b["text"])
        for b in payload.get("blocks", [])
    ]
    markdown = render(
        blocks,
        names,
        source=Path(record["name"]),
        audio_seconds=record.get("duration_seconds") or 0.0,
        asr_model=record.get("asr_model", ""),
        diarization_model=record.get("diarization_model", ""),
        elapsed=record.get("elapsed"),
    )

    meta = {k: v for k, v in record.items() if k not in ("markdown", "payload")}
    meta["speakers"] = list(dict.fromkeys(names.values()))

    folder = root() / session_id
    (folder / "transcript.md").write_text(markdown, encoding="utf-8")
    (folder / "data.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    meta["markdown"] = markdown
    meta["payload"] = payload
    return meta


def delete(session_id: str) -> bool:
    folder = _folder(session_id)
    if folder is None:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True
