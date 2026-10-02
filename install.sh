#!/usr/bin/env bash
# 一次性安装：创建本地虚拟环境，并装好两个模型的运行时依赖。
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 检查前置条件"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "    ! 本流程面向 Apple Silicon 版 macOS；MLX 无法在当前平台运行。" >&2
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "==> 使用 Homebrew 安装 ffmpeg"
    brew install ffmpeg
  else
    echo "    ! 缺少 ffmpeg，且未找到 Homebrew。" >&2
    echo "      请先从 https://brew.sh 安装 Homebrew，然后重新运行 ./install.sh" >&2
    exit 1
  fi
fi

PYTHON="${PYTHON:-python3}"
echo "==> 创建虚拟环境 .venv（$("$PYTHON" -V)）"
"$PYTHON" -m venv .venv

echo "==> 安装 Python 依赖（会拉取约 2 GB 的 PyTorch 与 MLX）"
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt

echo
./.venv/bin/python -m mactranscript setup || true

cat <<'TIP'

==> 接下来

  先一次性同意 pyannote 的模型条款（免费账号，约一分钟）：
    https://huggingface.co/pyannote/speaker-diarization-community-1

  在 https://huggingface.co/settings/tokens 创建 Read 令牌并保存：
    ./.venv/bin/hf auth login

  然后就可以转写了：
    ./transcribe.sh recording.m4a

TIP
