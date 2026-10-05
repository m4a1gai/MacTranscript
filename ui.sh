#!/usr/bin/env bash
# 启动本地网页界面，并自动打开浏览器。
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "错误：缺少 .venv 目录，请先运行 ./install.sh" >&2
  exit 1
fi

exec ./.venv/bin/python -m mactranscript ui "$@"
