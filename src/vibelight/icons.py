"""程序内绘制状态图标，无需外部图片文件。

用 Pillow 在内存中绘制状态图标：
- 纯色圆 + 可选深色描边底，直观对应红/黄/绿/空闲。
- make_icon(state, size=64, frame=True) -> PIL.Image
  - size：图标边长（像素）。托盘用 64，桌面悬浮灯用 120/180。
  - frame：True 画深色圆角矩形衬底（托盘风格），False 只画彩色圆 + 高光
    （桌面悬浮灯风格，更通透）。
"""
from __future__ import annotations

from PIL import Image, ImageDraw

SIZE = 64  # 默认尺寸（托盘）
# 颜色表：状态 -> 主色 RGB
_COLORS = {
    "red":   (232, 65, 60),    # 思考中
    "amber": (245, 180, 40),   # 需关注（授权）
    "green": (60, 200, 110),   # 已完成
    "idle":  (130, 130, 140),  # 空闲/未启动
}
_RING = (24, 24, 28)          # 外圈深色描边底


def make_icon(state: str, size: int = SIZE, frame: bool = True) -> Image.Image:
    """根据状态名绘制图标。未知状态回退为 idle。

    所有几何参数按 size/64 等比缩放，保证任意尺寸下视觉比例一致。
    """
    fill = _COLORS.get(state, _COLORS["idle"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 缩放因子：以 64 为基准
    k = size / 64.0

    # 可选外圈深色圆角矩形衬底（托盘风格）
    if frame:
        margin = 6 * k
        d.rounded_rectangle(
            [margin, margin, size - margin, size - margin],
            radius=14 * k,
            fill=_RING,
        )

    # 主体彩色圆
    cx = cy = size // 2
    r = 20 * k
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)

    # 左上高光，让灯看起来有立体感
    hr = 6 * k
    off = 10 * k
    d.ellipse(
        [cx - off - hr, cy - off - hr, cx - off + hr, cy - off + hr],
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
