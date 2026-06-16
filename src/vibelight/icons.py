"""程序内绘制托盘图标，无需外部图片文件。

用 Pillow 在内存中绘制 64x64 的状态图标：
- 纯色圆 + 深色描边，直观对应红/黄/绿/空闲。
- 提供 make_icon(state) -> PIL.Image，供 pystray 使用。
"""
from __future__ import annotations

from PIL import Image, ImageDraw

SIZE = 64
# 颜色表：状态 -> (主色 RGB, 是否高亮)
_COLORS = {
    "red":   (232, 65, 60),    # 思考中
    "amber": (245, 180, 40),   # 需关注（授权）
    "green": (60, 200, 110),   # 已完成
    "idle":  (130, 130, 140),  # 空闲/未启动
}
_RING = (24, 24, 28)          # 外圈深色描边
_HIGHLIGHT = (255, 255, 255)  # 内部高光


def make_icon(state: str) -> Image.Image:
    """根据状态名绘制图标。未知状态回退为 idle。"""
    fill = _COLORS.get(state, _COLORS["idle"])
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 外圈深色描边（圆角矩形底）
    margin = 6
    d.rounded_rectangle(
        [margin, margin, SIZE - margin, SIZE - margin],
        radius=14,
        fill=_RING,
    )

    # 主体彩色圆
    cx = cy = SIZE // 2
    r = 20
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)

    # 左上高光，让灯看起来有立体感
    hr = 6
    d.ellipse(
        [cx - 10 - hr, cy - 10 - hr, cx - 10 + hr, cy - 10 + hr],
        fill=(255, 255, 255, 90),
    )
    return img


if __name__ == "__main__":
    # 直接运行时把四态图标导出成 PNG，方便预览
    import os
    out_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
    os.makedirs(out_dir, exist_ok=True)
    for name in _COLORS:
        make_icon(name).save(os.path.join(out_dir, f"{name}.png"))
    print(f"导出图标到 {os.path.abspath(out_dir)}")
