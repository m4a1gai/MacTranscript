"""完全在本机运行的语音转 Markdown 流程，面向 Apple Silicon。

用 Whisper（经 MLX，跑在 GPU 上）识别文字，用 pyannote 判断是谁说的。
除首次下载模型外，没有任何数据离开这台机器。
"""

__version__ = "1.0.0"
