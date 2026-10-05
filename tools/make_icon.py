"""生成 app 图标。

用 matplotlib 画（它已经随 pyannote 装好了），省得为了一张图再添依赖。
画成 1024×1024 的 PNG，交给 make_app.sh 用 sips/iconutil 转成 .icns。
"""

from __future__ import annotations

import sys

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

# 波形柱的高度。中间高两边低，缩到 16×16 也还能看出是「声音」。
BARS = [0.16, 0.30, 0.50, 0.72, 0.42, 0.62, 0.34, 0.20]


def main(out: str) -> None:
    fig = plt.figure(figsize=(10.24, 10.24), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # macOS 风格的圆角方块
    squircle = FancyBboxPatch(
        (0.055, 0.055), 0.89, 0.89,
        boxstyle="round,pad=0,rounding_size=0.21",
        linewidth=0, facecolor="none",
    )
    ax.add_patch(squircle)

    # 竖向渐变，上浅下深
    gradient = np.linspace(0, 1, 512).reshape(-1, 1)
    image = ax.imshow(
        gradient,
        extent=(0.055, 0.945, 0.055, 0.945),
        origin="lower",
        aspect="auto",
        cmap=LinearSegmentedColormap.from_list("blue", ["#0a53c4", "#3f9dff"]),
    )
    image.set_clip_path(squircle)

    # 白色波形
    width = 0.062
    gap = 0.0295
    total = len(BARS) * width + (len(BARS) - 1) * gap
    x = (1 - total) / 2
    for height in BARS:
        bar = FancyBboxPatch(
            (x, 0.5 - height / 2), width, height,
            boxstyle=f"round,pad=0,rounding_size={width / 2:.4f}",
            linewidth=0, facecolor="white",
        )
        ax.add_patch(bar)
        x += width + gap

    fig.savefig(out, transparent=True)
    plt.close(fig)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
