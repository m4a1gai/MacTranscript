"""说话人分离（pyannote）：判断谁在什么时候说话。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable

import numpy as np

# pyannote 当前的社区版管线。它在 Hugging Face 上是「受限访问」（gated）的，
# 需要一次性同意条款并提供读取令牌 —— 详见 `transcribe.sh setup`。
DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"

TOKEN_HELP = f"""未找到 Hugging Face 令牌，而 pyannote 的管线是受限访问的。

一次性设置（约一分钟）：
  1. 在 https://huggingface.co/join 注册一个免费账号
  2. 打开 https://huggingface.co/{DEFAULT_MODEL}
     点击「Agree and access repository」同意条款
  3. 在 https://huggingface.co/settings/tokens 创建一个读取（read）令牌
  4. 保存令牌：.venv/bin/hf auth login
     （或在 shell 中 export HF_TOKEN=hf_...）

该令牌仅用于首次下载模型；转写过程本身完全离线。"""

ACCESS_HELP = """你的 Hugging Face 令牌有效，但无权读取 {model}。

该仓库为受限访问，需要先同意一次它的条款：
  https://huggingface.co/{model}
点击「Agree and access repository」，然后重新运行。"""

INVALID_TOKEN_HELP = """找到了 Hugging Face 令牌，但 Hub 拒绝了它。

该令牌可能已过期、被撤销或复制不完整。请在
  https://huggingface.co/settings/tokens
重新创建一个读取令牌，然后执行：.venv/bin/hf auth login"""


class DiarizationError(RuntimeError):
    """令牌缺失、仓库受限或管线运行失败时抛出。"""


@dataclass
class Turn:
    """归属于某一位说话人的一段连续语音。"""

    start: float
    end: float
    speaker: str


def resolve_token(explicit: str | None = None) -> str | None:
    """依次从命令行参数、环境变量、hf CLI 缓存中查找 HF 令牌。"""
    if explicit:
        return explicit
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:
        return None


def pick_device(requested: str = "auto") -> str:
    """把 auto 解析为可用的 Metal GPU，否则回退到 CPU。"""
    if requested != "auto":
        return requested
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def _token_is_valid(token: str | None) -> bool:
    """向 Hub 询问该令牌属于谁。返回 False 表示令牌被拒绝。"""
    try:
        from huggingface_hub import HfApi

        HfApi().whoami(token=token)
        return True
    except Exception:  # noqa: BLE001
        return False


def check_access(model: str, token: str | None) -> None:
    """当 `model` 确定不可访问时抛出 DiarizationError。

    这里刻意宽容：凡不是 Hub 给出的明确结论（比如网络错误）都放行，
    这样「已缓存权重但处于离线状态」的机器仍然可以正常工作。
    """
    if not token:
        raise DiarizationError(TOKEN_HELP)

    from huggingface_hub import HfApi
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    try:
        HfApi().auth_check(model, token=token)
    except GatedRepoError as exc:
        # 这里的 401 要么是令牌失效，要么是尚未同意条款。
        if _token_is_valid(token):
            raise DiarizationError(ACCESS_HELP.format(model=model)) from exc
        raise DiarizationError(INVALID_TOKEN_HELP) from exc
    except RepositoryNotFoundError as exc:
        raise DiarizationError(f"不存在这个模型仓库：{model}") from exc
    except DiarizationError:
        raise
    except Exception:  # noqa: BLE001 - 离线或临时故障，交给后续加载判断
        return


def load_pipeline(model: str = DEFAULT_MODEL, token: str | None = None):
    """获取（并缓存）分离管线，同时给出易读的鉴权错误提示。

    先尝试加载，这样已缓存权重的机器完全不需要联网；只有加载失败时
    才去询问 Hub 究竟哪里出了问题。
    """
    from pyannote.audio import Pipeline

    tok = resolve_token(token)
    try:
        pipeline = Pipeline.from_pretrained(model, token=tok)
    except Exception as exc:  # noqa: BLE001 - 下面会带上指引重新抛出
        check_access(model, tok)  # 会抛出具体且可操作的错误
        raise DiarizationError(
            f"无法加载 {model}。\n{type(exc).__name__}: {exc}"
        ) from exc

    # 较早版本的 pyannote 会吞掉鉴权失败并返回 None。
    if pipeline is None:
        check_access(model, tok)
        raise DiarizationError(f"无法加载 {model}（pyannote 没有返回任何内容）。")
    return pipeline


def diarize(
    audio: np.ndarray,
    sample_rate: int,
    *,
    num_speakers: int | None = 2,
    model: str = DEFAULT_MODEL,
    token: str | None = None,
    device: str = "auto",
    verbose: bool = False,
    hook: Callable | None = None,
) -> list[Turn]:
    """把已解码的音频切分为若干说话人轮次。

    `num_speakers=None` 时由 pyannote 自行估计人数；传入真实人数
    （例如访谈场景的 2）能明显提高边界的准确度。

    `hook` 为 pyannote 的进度回调，签名是
    `(step_name, artifact, file=None, total=None, completed=None)`；
    传入它可以把内部步骤的进度转出去（网页界面用它画进度条）。
    若未传且 verbose 为真，则退回终端用的 ProgressHook。
    """
    import torch

    pipeline = load_pipeline(model, token)

    # 直接把内存中的波形交给 pyannote，可绕过它自己的文件解码路径，
    # 因此音频只解码一次，也不会写任何临时文件。
    payload = {
        "waveform": torch.from_numpy(audio).unsqueeze(0),  # (声道, 采样点)
        "sample_rate": sample_rate,
    }

    def run(dev: str):
        pipeline.to(torch.device(dev))
        if hook is not None:
            return pipeline(payload, num_speakers=num_speakers, hook=hook)
        if verbose:
            from pyannote.audio.pipelines.utils.hook import ProgressHook

            with ProgressHook() as progress:
                return pipeline(payload, num_speakers=num_speakers, hook=progress)
        return pipeline(payload, num_speakers=num_speakers)

    device = pick_device(device)
    try:
        output = run(device)
    except Exception as exc:  # noqa: BLE001 - MPS 的算子缺口值得回退到 CPU 重试
        if device != "mps":
            raise DiarizationError(f"说话人分离失败：{exc}") from exc
        print(
            f"  ! Metal 后端失败（{type(exc).__name__}），正在回退到 CPU 重试……",
            file=sys.stderr,
        )
        try:
            output = run("cpu")
        except Exception as cpu_exc:  # noqa: BLE001
            raise DiarizationError(f"说话人分离失败：{cpu_exc}") from cpu_exc

    # pyannote 4.x 返回 DiarizeOutput 包装对象；3.x 直接返回 Annotation。
    annotation = getattr(output, "speaker_diarization", output)

    turns = [
        Turn(float(segment.start), float(segment.end), str(label))
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: (t.start, t.end))
    return turns
