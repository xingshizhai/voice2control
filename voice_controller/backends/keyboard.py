from __future__ import annotations

from typing import Protocol


class KeyboardBackend(Protocol):
    def tap(self, keys: tuple[str, ...]) -> None: ...


def keys_to_keyboard_send(keys: tuple[str, ...]) -> str:
    """将 ['ctrl','v'] 转为 keyboard 库的 'ctrl+v' 形式。"""
    import sys

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
    """使用 `keyboard` 库发送组合键（首版面向 Windows）。"""

    def __init__(self) -> None:
        import keyboard

        self._keyboard = keyboard

    def tap(self, keys: tuple[str, ...]) -> None:
        combo = keys_to_keyboard_send(keys)
        self._keyboard.send(combo)
