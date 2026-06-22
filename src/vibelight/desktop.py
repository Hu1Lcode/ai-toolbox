"""桌面悬浮灯 UI 后端（pywin32）。

一个无边框、置顶、分层的像素风交通灯窗口，常驻桌面：
- 120x120 像素，默认在屏幕右上角（留 20px 边距）。
- 红黄绿三灯竖排，对应状态灯亮，其余灯灰（灭）。
- 可拖动到任意位置，松手后位置写入 config.json，下次启动恢复。
- 右键菜单：刷新状态 / 切换到托盘模式 / 退出。
- 状态变化时通过 engine.on_update 回调驱动重绘。

跨线程要点：
- engine 的轮询线程会调 on_update，但 Win32 GUI 只能在主线程操作。
- on_update 里用 win32gui.PostMessage 把更新请求转到主线程，
  主线程 WndProc 收到 WM_APP_UPDATE 后执行真正的重绘。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from ctypes import wintypes

import win32api
import win32con
import win32gui
from PIL import Image

from . import icons, store
from .engine import StatusEngine, _fmt_detail
from .store import LABELS

# 窗口尺寸（像素）
WINDOW_SIZE = 120
# 整体透明度（0-255，240 略低于不透明，柔和）
ALPHA = 240
# 屏幕边距（默认位置离屏幕边缘的距离）
SCREEN_MARGIN = 20

# 自定义消息：engine 线程 → 主线程请求重绘
WM_APP_UPDATE = win32con.WM_APP + 1
# 自定义消息：右键菜单请求切换到托盘
WM_APP_SWITCH_TO_TRAY = win32con.WM_APP + 2

# 右键菜单命令 ID
MENU_REFRESH = 1001
MENU_SWITCH_TRAY = 1002
MENU_QUIT = 1003


def _pil_to_bitmap_info(pil_img: Image.Image):
    """把 RGBA PIL.Image 转成 win32 BITMAPINFO + 像素字节，供 UpdateLayeredWindow 用。

    返回 (BITMAPINFOHEADER bytes, pixel_bytes)。像素按 32bpp BGRA 排列，
    alpha 需做 premultiplied（UpdateLayeredWindow 要求）。
    """
    # 确保是 RGBA
    img = pil_img.convert("RGBA")
    w, h = img.size
    # 取出像素，PIL 给的是 RGBA，需要转 BGRA 并做 alpha 预乘
    px = img.tobytes()  # RGBA 排列
    # 转成 BGRA + premultiplied alpha
    out = bytearray(len(px))
    for i in range(0, len(px), 4):
        r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        # premultiplied
        r = r * a // 255
        g = g * a // 255
        b = b * a // 255
        # BGRA
        out[i] = b
        out[i + 1] = g
        out[i + 2] = r
        out[i + 3] = a
    # BITMAPINFOHEADER: 40 bytes
    bmi = (
        b"\x28\x00\x00\x00"  # biSize = 40
        + w.to_bytes(4, "little")  # biWidth
        + (-h).to_bytes(4, "little", signed=True)  # biHeight (负=自上而下 DIB)
        + b"\x01\x00"  # biPlanes = 1
        + b"\x20\x00"  # biBitCount = 32
        + b"\x00\x00\x00\x00"  # biCompression = BI_RGB
        + (w * h * 4).to_bytes(4, "little")  # biSizeImage
        + b"\x00\x00\x00\x00"  # biXPelsPerMeter
        + b"\x00\x00\x00\x00"  # biYPelsPerMeter
        + b"\x00\x00\x00\x00"  # biClrUsed
        + b"\x00\x00\x00\x00"  # biClrImportant
    )
    return bmi, bytes(out)


# ---- GDI/User32 ctypes 辅助：CreateDIBSection + UpdateLayeredWindow ----
# 用 use_last_error=True 才能让 ctypes.get_last_error() 拿到真实 Win32 错误码
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

# BOOL CreateDIBSection(HDC, BITMAPINFO*, UINT, VOID**, HANDLE, DWORD)
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
ULW_ALPHA = 2


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


# BOOL UpdateLayeredWindow(HWND, HDC, POINT*, SIZE*, HDC, POINT*, COLORREF,
#                          BLENDFUNCTION*, DWORD)
_user32.UpdateLayeredWindow.restype = wintypes.BOOL
_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
    ctypes.POINTER(SIZE), wintypes.HDC,
    ctypes.POINTER(POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]


def _create_dib_section(hdc: int, bmi_bytes: bytes):
    """用 ctypes 调 CreateDIBSection，返回 (hbitmap, pixel_ptr_int)。

    pixel_ptr 是像素缓冲的整型地址，可用 ctypes.memmove 写入。
    """
    bmi_buf = ctypes.create_string_buffer(bmi_bytes)
    ppv = ctypes.c_void_p(0)
    hbmp = _gdi32.CreateDIBSection(
        hdc, bmi_buf, 0, ctypes.byref(ppv), None, 0
    )
    if not hbmp:
        raise ctypes.WinError(ctypes.get_last_error())
    return hbmp, ppv.value


def _default_position() -> tuple[int, int]:
    """屏幕右上角默认位置（留边距）。"""
    screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    x = screen_w - WINDOW_SIZE - SCREEN_MARGIN
    y = SCREEN_MARGIN
    return x, y


def _load_position() -> tuple[int, int]:
    """从 config.json 读取记忆的位置，无则用默认。"""
    cfg = store.load_config()
    pos = cfg.get("desktop_pos")
    if isinstance(pos, list) and len(pos) == 2:
        try:
            return int(pos[0]), int(pos[1])
        except (ValueError, TypeError):
            pass
    return _default_position()


def _save_position(x: int, y: int) -> None:
    """把当前位置写入 config.json。"""
    store.update_config(desktop_pos=[x, y])


class DesktopApp:
    def __init__(self) -> None:
        self._engine = StatusEngine()
        self._hwnd = None
        self._hdc = None
        self._memdc = None
        self._hbitmap = None
        self._pixel_ptr = 0
        self._old_bitmap = None
        self._current_state = "idle"
        self._current_tip = ""
        self._dragging = False
        self._class_atom = None

    # ---------- engine 回调（在轮询线程里调用） ----------
    def _on_update(self, agg: str, data: dict, tip: str) -> None:
        """engine 状态变化时调用。只记录数据 + PostMessage 到主线程，不直接操作 GUI。"""
        self._current_state = agg
        self._current_tip = tip
        if self._hwnd is not None:
            try:
                win32gui.PostMessage(self._hwnd, WM_APP_UPDATE, 0, 0)
            except Exception as e:
                print(f"[desktop] PostMessage 失败: {e}", flush=True)

    # ---------- Win32 窗口过程 ----------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        """窗口过程。处理绘制、拖动、右键菜单、自定义更新消息。"""
        if msg == win32con.WM_PAINT:
            self._on_paint(hwnd)
            return 0

        if msg == win32con.WM_LBUTTONDOWN:
            # 开始拖动：让 Windows 以为点了标题栏
            self._dragging = True
            win32gui.ReleaseCapture()
            win32gui.SendMessage(
                hwnd, win32con.WM_NCLBUTTONDOWN, win32con.HTCAPTION, lparam
            )
            return 0

        if msg == win32con.WM_LBUTTONUP:
            self._dragging = False
            return 0

        if msg == win32con.WM_EXITSIZEMOVE:
            # 拖动结束，保存位置
            rect = win32gui.GetWindowRect(hwnd)
            _save_position(rect[0], rect[1])
            return 0

        if msg == win32con.WM_RBUTTONUP:
            self._show_context_menu(hwnd, lparam)
            return 0

        if msg == win32con.WM_COMMAND:
            cmd = win32api.LOWORD(wparam)
            if cmd == MENU_REFRESH:
                self._engine.force_refresh()
            elif cmd == MENU_SWITCH_TRAY:
                win32gui.PostMessage(self._hwnd, WM_APP_SWITCH_TO_TRAY, 0, 0)
            elif cmd == MENU_QUIT:
                self._engine.stop()
                win32gui.DestroyWindow(hwnd)
            return 0

        if msg == WM_APP_UPDATE:
            # engine 线程请求的重绘（现在在主线程，可以安全操作 GUI）
            self._repaint()
            return 0

        if msg == WM_APP_SWITCH_TO_TRAY:
            # 切换到托盘模式：先停 engine，销毁窗口，让 main 走 tray
            self._engine.stop()
            win32gui.DestroyWindow(hwnd)
            return 0

        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _on_paint(self, hwnd) -> None:
        """WM_PAINT 响应：用 UpdateLayeredWindow 贴图。"""
        # UpdateLayeredWindow 实际不依赖 WM_PAINT，分层窗口靠它直接合成。
        # 但 Windows 仍可能发 WM_PAINT，这里走 UpdateLayeredWindow 即可。
        self._repaint()

    def _repaint(self) -> None:
        """把当前状态的图标贴到分层窗口。

        用 CreateDIBSection 创建 32bpp DIB（直接拿到可写像素指针），
        ctypes.memmove 把 premultiplied BGRA 像素拷进去，
        再 UpdateLayeredWindow 合成到屏幕。
        """
        if self._hwnd is None:
            return
        try:
            img = icons.make_traffic_light(self._current_state, size=WINDOW_SIZE)
            bmi, pixels = _pil_to_bitmap_info(img)

            # 首次：创建兼容 DC 和 DIB section
            if self._memdc is None:
                self._hdc = win32gui.GetDC(self._hwnd)
                self._memdc = win32gui.CreateCompatibleDC(self._hdc)
                self._hbitmap, self._pixel_ptr = _create_dib_section(self._hdc, bmi)
                self._old_bitmap = win32gui.SelectObject(self._memdc, self._hbitmap)
            else:
                # 后续重绘：DIB 大小不变，直接把新像素 memmove 进去
                ctypes.memmove(self._pixel_ptr, pixels, len(pixels))

            # UpdateLayeredWindow 合成（用 ctypes 版避免 pywin32 参数校验问题）
            rect = win32gui.GetWindowRect(self._hwnd)
            pt_pos = POINT(rect[0], rect[1])
            size = SIZE(WINDOW_SIZE, WINDOW_SIZE)
            pt_zero = POINT(0, 0)
            blend = BLENDFUNCTION(
                BlendOp=AC_SRC_OVER,
                BlendFlags=0,
                SourceConstantAlpha=ALPHA,
                AlphaFormat=AC_SRC_ALPHA,
            )
            ok = _user32.UpdateLayeredWindow(
                self._hwnd, self._hdc,
                ctypes.byref(pt_pos), ctypes.byref(size),
                self._memdc, ctypes.byref(pt_zero),
                0, ctypes.byref(blend), ULW_ALPHA
            )
            if not ok:
                err = ctypes.get_last_error()
                raise ctypes.WinError(err)
        except Exception as e:
            print(f"[desktop] _repaint 失败: {e}", flush=True)

    def _show_context_menu(self, hwnd, lparam) -> None:
        """弹出右键菜单。"""
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_REFRESH, "刷新状态")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_SWITCH_TRAY, "切换到托盘模式")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_QUIT, "退出")
        # 菜单显示位置：鼠标点击处
        x = win32api.LOWORD(lparam)
        y = win32api.HIWORD(lparam)
        # 让菜单能响应命令
        win32gui.SetForegroundWindow(hwnd)
        win32gui.TrackPopupMenu(
            menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
            x, y, 0, hwnd, None
        )
        win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    # ---------- 窗口创建 ----------
    def _create_window(self) -> None:
        """注册窗口类并创建分层置顶窗口。"""
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "VibeLightDesktop"
        wc.lpfnWndProc = self._wnd_proc
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.hCursor = win32gui.LoadCursor(None, win32con.IDC_ARROW)
        # 分层窗口自己画背景，hbrBackground 不设（pywin32 不接受 None，
        # 用 0 表示不使用系统画刷）
        wc.hbrBackground = 0
        self._class_atom = win32gui.RegisterClass(wc)

        # 窗口样式：弹出式无边框 + 可见
        style = win32con.WS_POPUP | win32con.WS_VISIBLE
        # 扩展样式：分层（支持半透明）+ 置顶 + 工具窗口（不在任务栏显示）
        ex_style = (
            win32con.WS_EX_LAYERED
            | win32con.WS_EX_TOPMOST
            | win32con.WS_EX_TOOLWINDOW
        )

        x, y = _load_position()
        self._hwnd = win32gui.CreateWindowEx(
            ex_style,
            wc.lpszClassName,
            "VibeLight",
            style,
            x, y, WINDOW_SIZE, WINDOW_SIZE,
            None, None, wc.hInstance, None
        )
        # 注意：用 UpdateLayeredWindow 的 ULW_ALPHA 做逐像素 alpha 时，
        # 不要再调 SetLayeredWindowAttributes（两者会冲突）。
        # 整体透明度由 BLENDFUNCTION.SourceConstantAlpha 控制。

    # ---------- 启动 ----------
    def run(self) -> None:
        """创建窗口 + 启动 engine + 跑消息循环。"""
        self._create_window()
        # 先画一帧（idle 状态）
        self._repaint()

        # 注册回调并启动 engine
        self._engine.on_update(self._on_update)
        self._engine.run_forever()

        # 阻塞主线程跑 Win32 消息循环
        win32gui.PumpMessages()

        # 消息循环退出后清理
        self._cleanup()

    def _cleanup(self) -> None:
        """释放 GDI 资源。"""
        try:
            if self._memdc is not None and self._old_bitmap is not None:
                win32gui.SelectObject(self._memdc, self._old_bitmap)
                win32gui.DeleteDC(self._memdc)
            if self._hbitmap is not None:
                _gdi32.DeleteObject(self._hbitmap)
            if self._hdc is not None and self._hwnd is not None:
                win32gui.ReleaseDC(self._hwnd, self._hdc)
        except Exception:
            pass


def main() -> int:
    app = DesktopApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
