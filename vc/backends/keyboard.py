from __future__ import annotations

import os
import sys
from typing import Protocol


class KeyboardBackend(Protocol):
    def tap(self, keys: tuple[str, ...]) -> None: ...


def keys_to_keyboard_send(keys: tuple[str, ...]) -> str:
    """将 ['ctrl','v'] 转为 keyboard 库的 'ctrl+v' 形式。"""
    if not keys:
        raise ValueError("空按键序列")
    normalized: list[str] = []
    for k in keys:
        k = k.strip().lower()
        if k in ("cmd", "command") and sys.platform == "win32":
            k = "ctrl"
        normalized.append(k)
    return "+".join(normalized)


class KeyboardTap:
    """使用 `keyboard` 库发送组合键（Windows 专用）。"""

    def __init__(self) -> None:
        import keyboard

        self._keyboard = keyboard

    def tap(self, keys: tuple[str, ...]) -> None:
        combo = keys_to_keyboard_send(keys)
        self._keyboard.send(combo)


# ---------------------------------------------------------------------------
# pynput 键名映射
# ---------------------------------------------------------------------------

def _build_pynput_key_map() -> dict:
    """构建 str → pynput Key/KeyCode 映射表（延迟导入）。"""
    from pynput.keyboard import Key, KeyCode

    m: dict = {
        "ctrl": Key.ctrl, "shift": Key.shift, "alt": Key.alt,
        "enter": Key.enter, "return": Key.enter,
        "tab": Key.tab, "esc": Key.esc, "escape": Key.esc,
        "space": Key.space, "backspace": Key.backspace,
        "delete": Key.delete, "del": Key.delete,
        "home": Key.home, "end": Key.end,
        "page_up": Key.page_up, "page_down": Key.page_down,
        "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
        "insert": Key.insert,
        "caps_lock": Key.caps_lock,
        "cmd": Key.cmd, "command": Key.cmd, "super": Key.cmd, "meta": Key.cmd,
        "windows": Key.cmd,
    }
    # F1–F20
    for i in range(1, 21):
        key_name = f"f{i}"
        try:
            m[key_name] = Key[key_name]
        except KeyError:
            pass
    return m


def _str_to_pynput_key(name: str, key_map: dict):
    """将字符串键名转为 pynput key 对象。"""
    from pynput.keyboard import KeyCode

    name = name.strip().lower()
    # macOS 上将 cmd 映射到实际 command key，其余平台 cmd→ctrl
    if name in ("cmd", "command"):
        if sys.platform != "darwin":
            name = "ctrl"
    if name in key_map:
        return key_map[name]
    if len(name) == 1:
        return KeyCode.from_char(name)
    # 尝试枚举
    from pynput.keyboard import Key
    try:
        return Key[name]
    except KeyError:
        return KeyCode.from_char(name)


class PynputKeyboardTap:
    """使用 pynput 发送组合键（Linux / macOS）。"""

    def __init__(self) -> None:
        from pynput.keyboard import Controller
        self._ctrl = Controller()
        self._key_map = _build_pynput_key_map()

    def tap(self, keys: tuple[str, ...]) -> None:
        if not keys:
            raise ValueError("空按键序列")
        pynput_keys = [_str_to_pynput_key(k, self._key_map) for k in keys]
        for k in pynput_keys:
            self._ctrl.press(k)
        for k in reversed(pynput_keys):
            self._ctrl.release(k)


def _is_wayland() -> bool:
    return sys.platform == "linux" and (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


# ---------------------------------------------------------------------------
# evdev UInput 后端（Linux Wayland / X11）
# ---------------------------------------------------------------------------

def _build_uinput_key_map() -> dict[str, int]:
    """构建 str → evdev KEY_* 常量映射表。"""
    from evdev import ecodes as e
    m: dict[str, int] = {
        "ctrl": e.KEY_LEFTCTRL, "shift": e.KEY_LEFTSHIFT,
        "alt": e.KEY_LEFTALT,
        "cmd": e.KEY_LEFTMETA, "command": e.KEY_LEFTMETA,
        "super": e.KEY_LEFTMETA, "meta": e.KEY_LEFTMETA,
        "enter": e.KEY_ENTER, "return": e.KEY_ENTER,
        "backspace": e.KEY_BACKSPACE, "tab": e.KEY_TAB,
        "esc": e.KEY_ESC, "escape": e.KEY_ESC,
        "space": e.KEY_SPACE,
        "delete": e.KEY_DELETE, "del": e.KEY_DELETE,
        "insert": e.KEY_INSERT,
        "home": e.KEY_HOME, "end": e.KEY_END,
        "page_up": e.KEY_PAGEUP, "page_down": e.KEY_PAGEDOWN,
        "up": e.KEY_UP, "down": e.KEY_DOWN,
        "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
        "caps_lock": e.KEY_CAPSLOCK,
        "print_screen": e.KEY_SYSRQ,
    }
    # F1–F20
    for i in range(1, 21):
        code = getattr(e, f"KEY_F{i}", None)
        if code is not None:
            m[f"f{i}"] = code
    # a–z, 0–9
    for c in "abcdefghijklmnopqrstuvwxyz":
        code = getattr(e, f"KEY_{c.upper()}", None)
        if code is not None:
            m[c] = code
    for c in "0123456789":
        code = getattr(e, f"KEY_{c}", None)
        if code is not None:
            m[c] = code
    return m


class UInputKeyboardTap:
    """Linux：通过 evdev UInput 在内核层注入按键，适用于 X11 和所有 Wayland 合成器。

    前提：当前用户有 /dev/uinput 写权限。
    修复方法：
      echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
      sudo udevadm control --reload-rules && sudo udevadm trigger
    （用户需在 input 组中，注销后重新登录生效）
    """

    def __init__(self) -> None:
        import time as _time
        from evdev import UInput, ecodes
        self._time = _time
        self._ecodes = ecodes
        self._key_map = _build_uinput_key_map()
        caps = {ecodes.EV_KEY: list(range(1, 256))}
        self._ui = UInput(events=caps, name="voice2control-vkbd")

    def tap(self, keys: tuple[str, ...]) -> None:
        e = self._ecodes
        codes = [self._resolve(k) for k in keys]
        for code in codes:
            self._ui.write(e.EV_KEY, code, 1)  # press
        self._ui.syn()
        self._time.sleep(0.02)
        for code in reversed(codes):
            self._ui.write(e.EV_KEY, code, 0)  # release
        self._ui.syn()

    def _resolve(self, key: str) -> int:
        key = key.strip().lower()
        if key in ("cmd", "command") and sys.platform != "darwin":
            key = "ctrl"
        code = self._key_map.get(key)
        if code is None:
            raise ValueError(f"未知按键名: {key!r}")
        return code


def build_keyboard_backend() -> KeyboardBackend:
    """按当前平台返回合适的 KeyboardBackend 实现。"""
    if sys.platform == "win32":
        return KeyboardTap()
    if sys.platform == "linux":
        try:
            return UInputKeyboardTap()
        except Exception as exc:
            raise PermissionError(
                "/dev/uinput 无写权限，无法注入按键。\n"
                "请执行以下命令（无需重启，立即生效）：\n"
                "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' "
                "| sudo tee /etc/udev/rules.d/99-uinput.rules\n"
                "  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
                "  sudo usermod -aG input $USER  # 然后重新登录\n"
                f"原始错误: {exc}"
            ) from exc
    return PynputKeyboardTap()
