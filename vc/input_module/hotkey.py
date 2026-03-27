from __future__ import annotations

import logging
import queue
from typing import Callable

from vc.config import HotkeyConfig

logger = logging.getLogger(__name__)

HotkeyEvent = tuple[str, ...]


def register_hotkeys(
    cfg: HotkeyConfig,
    q: "queue.Queue[HotkeyEvent]",
) -> Callable[[], None]:
    """注册全局热键，事件放入队列：('ptt','down'|'up')、('cancel',)、('quit',)、('rerecord',)。"""
    import keyboard

    def ptt_down(_: object) -> None:
        q.put(("ptt", "down"))

    def ptt_up(_: object) -> None:
        q.put(("ptt", "up"))

    def on_esc(_: object) -> None:
        q.put(("cancel",))

    keyboard.on_press_key(cfg.push_to_talk, ptt_down)
    keyboard.on_release_key(cfg.push_to_talk, ptt_up)
    keyboard.on_press_key("esc", on_esc)

    def on_quit() -> None:
        q.put(("quit",))

    def on_rerecord() -> None:
        q.put(("rerecord",))

    keyboard.add_hotkey(cfg.quit, on_quit, suppress=False)
    keyboard.add_hotkey(cfg.rerecord, on_rerecord, suppress=False)

    logger.info(
        "热键已注册：Push-to-talk=%s 退出=%s 重录=%s",
        cfg.push_to_talk,
        cfg.quit,
        cfg.rerecord,
    )

    def unhook() -> None:
        keyboard.unhook_all()

    return unhook
