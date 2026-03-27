from __future__ import annotations

import logging
import time
from typing import Callable

from voice_controller.backends.clipboard import ClipboardBackend
from voice_controller.backends.keyboard import KeyboardBackend
from voice_controller.config import DeliveryConfig
from voice_controller.window_focus import get_foreground_window_title

logger = logging.getLogger(__name__)


class Deliverer:
    def __init__(
        self,
        cfg: DeliveryConfig,
        clipboard: ClipboardBackend,
        keyboard: KeyboardBackend,
        window_title_provider: Callable[[], str] | None = None,
    ) -> None:
        self._cfg = cfg
        self._clipboard = clipboard
        self._keyboard = keyboard
        self._window_title_provider = window_title_provider or get_foreground_window_title

    def _window_allowed(self) -> bool:
        if not self._cfg.window_whitelist:
            return True
        title = self._window_title_provider().strip()
        if not title:
            logger.warning("无法读取前台窗口标题，已拦截投递（启用了 window_whitelist）")
            return False
        title_lower = title.lower()
        for kw in self._cfg.window_whitelist:
            if kw.lower() in title_lower:
                return True
        logger.warning("前台窗口不在白名单，已拦截投递: %s", title)
        return False

    def deliver(self, text: str) -> None:
        t = text.strip()
        if not t:
            logger.warning("识别结果为空，跳过投递")
            return
        if not self._window_allowed():
            return

        if self._cfg.mode == "review":
            self._clipboard.set_text(t)
            logger.warning(
                "谨慎模式：文本已写入剪贴板，请手动聚焦输入框后使用 Ctrl+V 粘贴（未自动按键）",
            )
            return

        actions = self._cfg.profiles[self._cfg.profile]
        if self._cfg.mode == "paste_only":
            actions = tuple(a for a in actions if a.action != "send")
            if not actions:
                logger.warning("paste_only 模式下无可用动作，请检查 profile")
                return
        if not self._cfg.auto_send_enter:
            actions = tuple(
                a
                for a in actions
                if a.action != "send" and tuple(k.lower() for k in a.keys) != ("enter",)
            )
            if not actions:
                logger.warning("auto_send_enter=false 后无可用动作，请检查 profile")
                return
        backup: str | None = None
        if self._cfg.restore_clipboard:
            try:
                backup = self._clipboard.get_text()
            except Exception:
                backup = None

        try:
            self._clipboard.set_text(t)
            for act in actions:
                logger.debug("执行投递动作: %s -> %s", act.action, "+".join(act.keys))
                self._keyboard.tap(act.keys)
                if self._cfg.key_tap_interval_ms > 0:
                    time.sleep(self._cfg.key_tap_interval_ms / 1000.0)
        except Exception:
            logger.exception("投递失败")
            raise
        finally:
            if self._cfg.restore_clipboard:
                try:
                    if self._cfg.restore_clipboard_delay_ms > 0:
                        time.sleep(self._cfg.restore_clipboard_delay_ms / 1000.0)
                    self._clipboard.set_text(backup if backup is not None else "")
                except Exception:
                    logger.debug("恢复剪贴板失败", exc_info=True)
