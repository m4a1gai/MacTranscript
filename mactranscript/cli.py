"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import __version__, asr, diarize
from .align import assign_speakers, build_blocks, name_speakers
from .audio import SAMPLE_RATE, AudioError, decode, duration_of
from .render import render, timestamp


def log(message: str, *, quiet: bool = False) -> None:
    """进度信息写到 stderr，这样 `... -o -` 仍可直接管道传递。"""
    if not quiet:
        print(message, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mactranscript",
        description="在本机离线转写音频文件，输出带说话人标注和时间戳的 Markdown。",
        epilog="运行 'mactranscript setup' 可检查安装环境。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("audio", type=Path, help="输入文件（.m4a、.mp3、.wav 等）")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="Markdown 输出路径（默认：与输入同目录的 名称.md；填 '-' 则输出到标准输出）",
    )
    parser.add_argument(
        "-s", "--speakers", metavar="NAMES",
        help="按首次发言顺序排列的名字，以逗号分隔，例如 '张三,李四'",
    )
    parser.add_argument(
        "-n", "--num-speakers", default="2", metavar="N",
        help="预期的说话人数量；填 'auto' 交由模型判断（默认：2）",
    )
    parser.add_argument(
        "--language", default="en",
        help="语音所用语言的 ISO 代码，或填 'auto' 自动检测（默认：en）",
    )
    parser.add_argument(
        "--model", default=asr.DEFAULT_MODEL,
        help=f"Whisper 模型仓库（默认：{asr.DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--diarization-model", default=diarize.DEFAULT_MODEL,
        help=f"pyannote 管线（默认：{diarize.DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--device", default="auto", choices=("auto", "mps", "cpu"),
        help="说话人分离所用设备；Whisper 始终使用 GPU（默认：auto）",
    )
    parser.add_argument(
        "--prompt", metavar="TEXT",
        help="给 Whisper 的提示词，帮助它正确拼写人名或专业术语",
    )
    parser.add_argument(
        "--keep-unmatched", action="store_true",
        help="保留落在所有已检测语音之外的文本"
             "（默认关闭，因为这类文本通常是 Whisper 在静音处的幻觉）",
    )
    parser.add_argument(
        "--json", type=Path, metavar="PATH", help="同时把原始分段数据写成 JSON",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="不输出进度信息")
    parser.add_argument("--version", action="version", version=f"mactranscript {__version__}")
    return parser


def parse_num_speakers(raw: str) -> int | None:
    if raw.strip().lower() in ("auto", "0", ""):
        return None
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"--num-speakers 必须是整数或 'auto'，而不是 {raw!r}")
    if value < 1:
        raise SystemExit("--num-speakers 至少为 1")
    return value


def cmd_setup() -> int:
    """检查所需环境是否齐备，并说明缺什么该怎么补。"""
    print(f"mactranscript {__version__} - 安装环境检查\n")
    problems: list[str] = []

    def row(label: str, ok: bool, detail: str) -> None:
        print(f"  {'ok ' if ok else 'XX '} {label:<18} {detail}")

    # ffmpeg
    ffmpeg = shutil.which("ffmpeg")
    row("ffmpeg", bool(ffmpeg), ffmpeg or "未找到")
    if not ffmpeg:
        problems.append("安装 ffmpeg：brew install ffmpeg")

    # Apple Silicon GPU
    try:
        import torch

        mps = torch.backends.mps.is_available()
        row("Metal GPU (MPS)", mps, f"torch {torch.__version__}, mps={mps}")
        if not mps:
            problems.append("Metal 不可用，所有计算将在 CPU 上运行（速度较慢）。")
    except Exception as exc:  # noqa: BLE001
        row("Metal GPU (MPS)", False, f"torch 导入失败：{exc}")
        problems.append("重新安装依赖：./install.sh")

    # Whisper（经 MLX）
    try:
        import mlx_whisper  # noqa: F401

        row("mlx-whisper", True, "就绪")
    except Exception as exc:  # noqa: BLE001
        row("mlx-whisper", False, str(exc))
        problems.append("重新安装依赖：./install.sh")

    # pyannote
    try:
        import pyannote.audio

        row("pyannote.audio", True, pyannote.audio.__version__)
    except Exception as exc:  # noqa: BLE001
        row("pyannote.audio", False, str(exc))
        problems.append("重新安装依赖：./install.sh")

    # Hugging Face 令牌，以及它是否真的能读取受限管线
    token = diarize.resolve_token()
    row("HF 令牌", bool(token), "已找到" if token else "缺失")
    if not token:
        problems.append(diarize.TOKEN_HELP)
    else:
        # 仅用 model_info() 是不够的：即使权重受限，仓库元数据也是公开的，
        # 所以这里向 Hub 请求一个真正的访问权限结论。
        try:
            diarize.check_access(diarize.DEFAULT_MODEL, token)
            row("模型访问权限", True, diarize.DEFAULT_MODEL)
        except diarize.DiarizationError as exc:
            row("模型访问权限", False, "被拒绝")
            problems.append(str(exc))

    if problems:
        print("\n需要处理：\n")
        for item in problems:
            print("  " + item.replace("\n", "\n  ") + "\n")
        return 1

    print("\n环境已就绪。试试：./transcribe.sh 你的录音.m4a")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 单独识别这个动词，使 `transcribe.sh setup` 无需引入子命令的复杂度。
    if argv and argv[0] in ("setup", "check", "doctor"):
        return cmd_setup()

    args = build_parser().parse_args(argv)
    quiet = args.quiet
    num_speakers = parse_num_speakers(args.num_speakers)
    names = [n.strip() for n in args.speakers.split(",") if n.strip()] if args.speakers else None
    to_stdout = str(args.output) == "-"
    started = time.time()

    # 1. 只解码一次，然后把同一份采样交给两个模型。
    log(f"[1/4] 正在解码 {args.audio.name} ……", quiet=quiet)
    try:
        audio = decode(args.audio)
    except AudioError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    seconds = duration_of(audio)
    log(f"      共 {timestamp(seconds)} 音频，{SAMPLE_RATE} Hz 单声道", quiet=quiet)

    # 2. 先做「谁在何时说话」。把它放在前面，是因为环境配置问题会在这一步
    #    暴露；在耗时数分钟的转写之前失败，对使用者更友好。
    log(f"[2/4] 正在识别说话人（{args.diarization_model}）……", quiet=quiet)
    try:
        turns = diarize.diarize(
            audio,
            SAMPLE_RATE,
            num_speakers=num_speakers,
            model=args.diarization_model,
            device=args.device,
            verbose=not quiet,
        )
    except diarize.DiarizationError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1
    found = sorted({t.speaker for t in turns})
    log(f"      得到 {len(turns)} 个轮次，共 {len(found)} 位说话人", quiet=quiet)
    if not turns:
        log(
            "      ! 未检测到语音；如仍要强制转写，请加 --keep-unmatched",
            quiet=quiet,
        )

    # 3. 说了什么。
    log(f"[3/4] 正在转写（{args.model}）……", quiet=quiet)
    try:
        words, raw = asr.transcribe(
            audio,
            model=args.model,
            language=None if args.language == "auto" else args.language,
            initial_prompt=args.prompt,
            verbose=not quiet,
        )
    except Exception as exc:  # noqa: BLE001 - 如实呈现模型或下载错误
        print(f"错误：转写失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    log(f"      共 {len(words)} 个词", quiet=quiet)

    # 4. 合并两条时间线并写出结果。
    log("[4/4] 正在对齐并生成 Markdown ……", quiet=quiet)
    labelled = assign_speakers(words, turns, drop_unmatched=not args.keep_unmatched)
    dropped = sum(1 for w in labelled if w.speaker is None)
    if dropped:
        log(
            f"      已丢弃 {dropped} 个无对应语音的词"
            f"（如需保留请加 --keep-unmatched）",
            quiet=quiet,
        )
    blocks = build_blocks(labelled)
    speaker_names = name_speakers(blocks, names)
    elapsed = time.time() - started

    markdown = render(
        blocks,
        speaker_names,
        source=args.audio,
        audio_seconds=seconds,
        asr_model=args.model,
        diarization_model=args.diarization_model,
        elapsed=elapsed,
    )

    if to_stdout:
        sys.stdout.write(markdown)
    else:
        out = args.output or args.audio.with_suffix(".md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        log(
            f"\n已写入 {out}（{len(blocks)} 个段落，{len(speaker_names)} 位说话人）",
            quiet=quiet,
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "source": str(args.audio),
                    "duration_seconds": seconds,
                    "language": raw.get("language"),
                    "speaker_names": speaker_names,
                    "turns": [vars(t) for t in turns],
                    "blocks": [vars(b) for b in blocks],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        log(f"已写入 {args.json}", quiet=quiet)

    return 0
