# MacTranscript

把两人对话的录音转成带说话人标注和时间戳的 Markdown 转写稿 —— 全程在你自己的
Mac 上完成。转写不需要任何 API key，不上传录音，不依赖云服务。

```markdown
**张三** · `00:00:00 → 00:00:07`

我们先看一下季度数据。营收是 420 万，同比增长 11%。

**李四** · `00:00:07 → 00:00:10`

比我预期的要高。主要是什么带来的增长？
```

## 工作原理

两个开源预训练模型，各自负责它擅长的那一半：

| 环节 | 模型 | 运行于 |
| --- | --- | --- |
| 解码为 16 kHz 单声道 | `ffmpeg` | CPU |
| 语音转文字 + 逐词时间戳 | Whisper `large-v3-turbo`（经 [MLX](https://github.com/ml-explore/mlx)） | Apple GPU（Metal） |
| 判断谁在何时说话 | [pyannote](https://github.com/pyannote/pyannote-audio) `speaker-diarization-community-1` | Apple GPU，可回退 CPU |
| 合并两条时间线 | 词级重叠匹配 | CPU |

让说话人标注落在正确位置的关键是**逐词时间戳**。Whisper 的单个 segment 经常
直接横跨一次说话人切换，因此本流程向 Whisper 索取每个词的时间，把**每个词**
归给与它重叠最多的那个 pyannote 轮次，再把词重新组合成段落。短于 0.6 秒的
单词级说话人跳变会被当作分离模型的抖动而平滑掉。

## 环境要求

- Apple Silicon Mac（M1 及以上）—— MLX 使用 Metal GPU
- macOS，并已安装 [Homebrew](https://brew.sh)
- Python 3.10+
- 约 4 GB 磁盘空间用于存放模型权重

## 安装

```bash
git clone https://github.com/m4a1gai/MacTranscript.git && cd MacTranscript
./install.sh
```

该脚本会安装 ffmpeg（若缺失）、创建 `.venv`，并把 PyTorch、MLX 和 pyannote
装进去。

### 一次性的 Hugging Face 配置

pyannote 的权重是免费开源的，但仓库为**受限访问**（gated）：下载前需要先同意
一次它的条款。

1. 在 <https://huggingface.co/join> 注册免费账号
2. 打开 <https://huggingface.co/pyannote/speaker-diarization-community-1>，
   点击 **Agree and access repository**（可能需要填一个简短表单）
3. 在 <https://huggingface.co/settings/tokens> 创建一个 **Read** 类型的令牌
4. 保存到本机：

```bash
./.venv/bin/hf auth login      # 或者：export HF_TOKEN=hf_...
```

一次性检查全部环境：

```bash
./transcribe.sh setup
```

```
  ok  ffmpeg             /opt/homebrew/bin/ffmpeg
  ok  Metal GPU (MPS)    torch 2.14.1, mps=True
  ok  mlx-whisper        就绪
  ok  pyannote.audio     4.0.7
  ok  HF 令牌              已找到
  ok  模型访问权限          pyannote/speaker-diarization-community-1
```

令牌**仅**用于首次下载模型。权重缓存完成后，断网也能正常转写。

## 使用：双击 App（推荐）

`./install.sh` 会生成 **MacTranscript.app**，双击即可 —— 界面就在应用自己的
窗口里，不会跳转浏览器，也看不到地址栏。有独立的 Dock 图标、菜单栏和 Cmd-Q。

把它拖进「应用程序」文件夹，就能从启动台或 Dock 打开。

- 重复双击不会开第二个窗口，只会把已有窗口切到前台
- 关闭窗口或按 Cmd-Q 即退出，本地服务随之停止
- 窗口是 WKWebView 承载的，因此拖放、文件选择都走 macOS 原生面板
- 启动日志在 `~/Library/Logs/MacTranscript.log`

项目目录移动过之后，重新生成一次：

```bash
./make_app.sh
```

> App 没有经过 Apple 签名。因为是你本机构建的，不会被 Gatekeeper 拦截；
> 但如果你把它拷到别的机器，首次打开需要在「系统设置 › 隐私与安全性」里放行。

## 使用：浏览器界面

如果更习惯在浏览器里用（比如想开多个标签页对照），还有这个入口：

```bash
./ui.sh
```

浏览器会自动打开 <http://127.0.0.1:8765>。把音频拖进去、选好人数和语言，就能
看到分步进度，完成后直接预览转写稿，并下载 Markdown 或 JSON。

界面顶部会实时显示环境自检（ffmpeg、GPU、令牌、模型权限）；缺什么就把对应的
处理办法直接写在页面上，不用回到终端看报错。

服务只监听 `127.0.0.1`，同一网络里的其他机器访问不到；音频存在系统临时目录，
退出时自动清理。按 Control-C 停止。

端口被占用时换一个：

```bash
./ui.sh --port 8780
./ui.sh --no-open        # 不自动打开浏览器
```

## 使用：命令行

```bash
./transcribe.sh interview.m4a
```

结果会写到输入文件旁边的 `interview.md`。常用变体：

```bash
# 指定说话人名字，顺序按谁先开口
./transcribe.sh interview.m4a --speakers "张三,李四"

# 指定输出位置，或直接输出到标准输出
./transcribe.sh interview.m4a -o notes/interview.md
./transcribe.sh interview.m4a -o - | pbcopy

# 超过两个人，或者交给模型自己判断
./transcribe.sh standup.m4a --num-speakers 4
./transcribe.sh panel.m4a   --num-speakers auto

# 转写中文录音
./transcribe.sh 会议.m4a --language zh

# 帮助 Whisper 正确拼写人名和术语
./transcribe.sh call.m4a --prompt "Acme Corp, Kubernetes, 李雪"

# 同时导出机器可读的结构化数据
./transcribe.sh call.m4a --json call.json
```

### 参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `-o, --output` | `名称.md` | 输出路径，填 `-` 表示标准输出 |
| `-s, --speakers` | `说话人 1、2…` | 按首次发言顺序排列的名字 |
| `-n, --num-speakers` | `2` | 预期说话人数量，或 `auto` |
| `--language` | `en` | 语言 ISO 代码，或 `auto` 自动检测 |
| `--model` | `mlx-community/whisper-large-v3-turbo` | Whisper 权重 |
| `--diarization-model` | `pyannote/speaker-diarization-community-1` | pyannote 管线 |
| `--device` | `auto` | 说话人分离用 `mps` 或 `cpu` |
| `--prompt` | — | 给 Whisper 的词汇提示 |
| `--keep-unmatched` | 关闭 | 保留落在已检测语音之外的文本 |
| `--json` | — | 同时导出原始轮次数据为 JSON |
| `-q, --quiet` | 关闭 | 不输出进度信息 |

输入支持任何 ffmpeg 能读的格式：`.m4a`、`.mp3`、`.wav`、`.aac`、`.mov` 等。

## 精度与速度

在 M2 Pro（10 核，16 GB）上使用默认模型、权重已缓存，对一段 3 分 20 秒的
双人录音实测：

| 环节 | 耗时 | 吞吐 |
| --- | --- | --- |
| 解码 | <1 秒 | — |
| 说话人分离（Metal GPU） | 11 秒 | 约 17.6 倍速 |
| Whisper 转写（Metal GPU） | 12 秒 | 约 16.2 倍速 |
| **合计** | **24 秒** | **约 8.4 倍速** |

也就是说，一小时的录音大约 7 分钟跑完。两个环节的开销基本相当，分离并不是
瓶颈 —— 但它对设备很敏感：同一段音频退到 CPU 上要 141 秒（1.4 倍速），
比 Metal 慢 12 倍，所以除非排查问题，不要主动加 `--device cpu`。

精度方面，用两种 TTS 音色合成、说话人边界已知的样本测得：6 个轮次全部正确
切分，说话人身份无混淆，**边界平均绝对误差 0.023 秒**（最大 0.047 秒），
词级说话人归属 88/88 全对。

首次运行还会下载约 1.5 GB 的 Whisper 权重和约 0.5 GB 的 pyannote 权重；
之后模型从磁盘加载只需几秒。

如果想要不同的取舍，可以更换 Whisper 模型 —— MLX 社区提供了多个版本：

| `--model` | 体积 | 说明 |
| --- | --- | --- |
| `mlx-community/whisper-large-v3-turbo` | 约 1.5 GB | **默认** —— 单位时间精度最优 |
| `mlx-community/whisper-large-v3-mlx` | 约 3 GB | 精度略好，但明显更慢 |
| `mlx-community/whisper-medium-mlx` | 约 1.5 GB | 更快，专有名词偏弱 |
| `mlx-community/whisper-small-mlx` | 约 0.5 GB | 仅适合快速草稿 |

### 静音与模型幻觉

Whisper 会在长段静音处编造文本 —— 诸如「Thank you.」「[BLANK_AUDIO]」——
而且给出**很高**的置信度，所以用它自己的分数无法过滤掉这类内容。本流程改用
pyannote 的语音活动检测来裁决：距离任何已检测语音超过 2 秒的文本会被丢弃；
完全没有检测到语音的录音会明确输出「未检测到语音」，而不是凭空生成一位
说话人。

如果你确实有一段很安静、但认为 pyannote 漏检了的录音，加上
`--keep-unmatched` 即可关闭该过滤。

### 改善说话人区分效果的建议

- 传入真实人数（`--num-speakers 2`），这是一个很强的先验。
- 大量抢话、同时说话会降低分离效果 —— 两人同时说出的一个词只能归给其中一方。
- 单声道录音没有问题。如果每个人各有一条独立音轨，就完全不需要说话人分离了。

## 测试

```bash
./.venv/bin/python tests/test_align.py
```

覆盖了对齐环节的各种边界情况：跨轮次边界的词、落在空隙中的词、抖动平滑、
段落合并，以及说话人命名。

## 常见问题

**`未找到 Hugging Face 令牌`** —— 见上文的一次性配置。本流程会在**转写之前**
就检查这一项，因此不会让你等完一次漫长的转写才在最后失败。

**`你的令牌有效，但无权读取 …`** —— 令牌本身没问题，只是还没同意模型条款。
点开它打印出的链接即可。

**`在 PATH 中找不到 ffmpeg`** —— 执行 `brew install ffmpeg`。

**`Metal 后端失败，正在回退到 CPU 重试`** —— 这只是一条提示：分离计算图中
某个算子在你的 macOS 版本上尚未支持 MPS，于是改在 CPU 上重跑。加
`--device cpu` 可直接跳过这次重试。

**说话人分离很慢** —— 先确认它跑在 GPU 上（`./transcribe.sh setup` 里
`Metal GPU (MPS)` 应为 ok）。退到 CPU 会慢一个数量级。另外目前没有流式模式，
整段音频会一次性处理完。

## 隐私

首次运行之后，程序不再访问网络。音频在内存中解码并以数组形式交给两个模型 ——
不写任何临时文件，录音也不会离开这台机器。想验证的话：先完成一次权重缓存，
然后关掉 Wi-Fi 再跑一次。

## 代码结构

三个入口（MacTranscript.app 原生窗口、`ui.sh` 浏览器、`transcribe.sh` 命令行）
共用 `pipeline.py` 里的同一条流程，因此两者的处理顺序、参数含义和输出完全一致。

```
mactranscript/
  audio.py      ffmpeg -> 16 kHz 单声道 float32
  asr.py        Whisper（经 MLX），带逐词时间戳
  diarize.py    pyannote 管线、令牌处理、设备回退
  align.py      词级说话人归属、分段、命名
  render.py     Markdown 输出
  pipeline.py   四个环节的编排，命令行与界面共用
  cli.py        参数解析、环境自检
  web.py        本地服务（仅用标准库）
  app.py        原生窗口（WKWebView）
  static/
    index.html  界面本体
tools/
  make_icon.py  生成 app 图标
tests/
  test_align.py 对齐与渲染测试（无需加载模型）
```

## 授权

本项目代码可自由使用。模型各有自己的条款：Whisper 为 MIT 协议，pyannote
管线为 MIT 协议但附带需在 Hugging Face 上接受的受限访问协议。商用前请先
查阅各自条款。
