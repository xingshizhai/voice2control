from __future__ import annotations

import subprocess
import sys


def get_foreground_window_title() -> str:
    """返回当前前台窗口标题；失败或不支持时返回空字符串。"""
    if sys.platform == "win32":
        return _get_title_windows()
    if sys.platform == "darwin":
        return _get_title_macos()
    return _get_title_linux()


def _get_title_windows() -> str:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _get_title_macos() -> str:
    """通过 osascript 获取 macOS 当前前台应用名称。"""
    try:
        result = subprocess.run(
            [
                "osascript", "-e",
                'tell app "System Events" to get name of first process'
                ' whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=1,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_title_linux() -> str:
    """通过 xdotool 获取 Linux X11 当前前台窗口标题。
    Wayland 环境或 xdotool 未安装时返回空字符串。
    """
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass  # xdotool 未安装
    except Exception:
        pass
    return ""
