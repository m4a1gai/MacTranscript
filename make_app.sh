#!/usr/bin/env bash
# 生成 MacTranscript.app —— 双击即可启动网页界面，无需开终端。
#
# 项目的绝对路径会写进启动器里，所以 app 可以拖到「应用程序」或 Dock。
# 若之后移动了项目目录，重新跑一次这个脚本即可。
set -euo pipefail
cd "$(dirname "$0")"
PROJECT="$(pwd)"
APP="$PROJECT/MacTranscript.app"

echo "==> 正在生成 $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ---------- Info.plist ----------
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                  <string>MacTranscript</string>
  <key>CFBundleDisplayName</key>           <string>MacTranscript</string>
  <key>CFBundleIdentifier</key>            <string>local.mactranscript</string>
  <key>CFBundleExecutable</key>            <string>MacTranscript</string>
  <key>CFBundleIconFile</key>              <string>AppIcon</string>
  <key>CFBundlePackageType</key>           <string>APPL</string>
  <key>CFBundleVersion</key>               <string>1.0.0</string>
  <key>CFBundleShortVersionString</key>    <string>1.0.0</string>
  <key>LSMinimumSystemVersion</key>        <string>12.0</string>
  <key>NSHighResolutionCapable</key>       <true/>
  <key>LSMultipleInstancesProhibited</key> <true/>
</dict>
</plist>
PLIST

# ---------- 启动器 ----------
# 用带引号的 heredoc，确保脚本原样写入；项目路径随后替换进去。
cat > "$APP/Contents/MacOS/MacTranscript" <<'LAUNCHER'
#!/bin/bash
# 由 make_app.sh 生成，请勿直接编辑。
PROJECT="__PROJECT__"
LOG="$HOME/Library/Logs/MacTranscript.log"
PY="$PROJECT/.venv/bin/python"

# 从 Finder 启动拿不到登录 shell 的 PATH，GUI 会话的默认 PATH 里没有
# Homebrew。代码里已有兜底查找，这里再补一层，让子进程也能直接用。
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$PATH"

fail() {
  osascript -e "display dialog \"$1\" buttons {\"好\"} default button 1 \
    with title \"MacTranscript\" with icon stop" >/dev/null 2>&1
  exit 1
}

[ -x "$PY" ] || fail "找不到运行环境：\n$PROJECT/.venv\n\n请先在项目目录里运行 ./install.sh"

# 先把会导致秒退的问题挡在前面 —— exec 之后就没机会弹窗了
"$PY" -c "import webview" 2>/dev/null \
  || fail "缺少 pywebview，无法打开窗口。\n\n请在项目目录运行 ./install.sh"

mkdir -p "$(dirname "$LOG")"
cd "$PROJECT" || fail "项目目录不存在：$PROJECT"

# exec 让 python 取代 shell，使窗口进程就是这个 app 本身：
# 关窗即退出，Cmd-Q 正常，Dock 里只有一个图标。
exec "$PY" -m mactranscript app >>"$LOG" 2>&1
LAUNCHER

# 把真实路径写进去（用 | 作分隔符，免得路径里的 / 干扰）
sed -i '' "s|__PROJECT__|$PROJECT|" "$APP/Contents/MacOS/MacTranscript"
chmod +x "$APP/Contents/MacOS/MacTranscript"

# ---------- 图标 ----------
if [ -x .venv/bin/python ] && .venv/bin/python -c "import matplotlib" 2>/dev/null; then
  TMP="$(mktemp -d)"
  .venv/bin/python tools/make_icon.py "$TMP/icon.png"
  mkdir -p "$TMP/AppIcon.iconset"
  for size in 16 32 64 128 256 512; do
    sips -z $size $size "$TMP/icon.png" \
      --out "$TMP/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) "$TMP/icon.png" \
      --out "$TMP/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
  rm -rf "$TMP"
  echo "==> 图标已生成"
else
  echo "==> 跳过图标（matplotlib 不可用），功能不受影响"
fi

touch "$APP"   # 让 Finder 立刻刷新图标

cat <<TIP

==> 完成

  双击打开：$APP

  想放到启动台或 Dock，把它拖进「应用程序」文件夹即可
  （项目目录如果移动了，重新运行一次 ./make_app.sh）

TIP
