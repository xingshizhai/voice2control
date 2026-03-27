from __future__ import annotations

import logging
import signal
import sys
from queue import Queue
from typing import Any, Callable

logger = logging.getLogger(__name__)


def install_graceful_shutdown(q: "Queue[tuple[str, ...]]") -> Callable[[], None]:
    """让 Ctrl+C / 控制台关闭 能结束主循环：向队列放入 ('quit',)。

    Windows 上 `keyboard` 全局钩子常干扰控制台默认 Ctrl+C，故用 SetConsoleCtrlHandler。
    """

    def request_quit() -> None:
        try:
            q.put(("quit",), block=False)
        except Exception:
            pass

    old_sigint: Any = None
    old_sigterm: Any = None

    def _on_signal(signum: int, frame: Any) -> None:
        request_quit()

    # 非 Windows：SIGINT / SIGTERM
    if sys.platform != "win32":
        old_sigint = signal.signal(signal.SIGINT, _on_signal)
        if hasattr(signal, "SIGTERM"):
            try:
                old_sigterm = signal.signal(signal.SIGTERM, _on_signal)
            except (ValueError, OSError):
                pass
        logger.debug("已注册 SIGINT/SIGTERM → 退出队列")

        def cleanup() -> None:
            if old_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, old_sigint)
                except (ValueError, OSError):
                    pass
            if old_sigterm is not None:
                try:
                    signal.signal(signal.SIGTERM, old_sigterm)
                except (ValueError, OSError):
                    pass

        return cleanup

    # Windows：控制台 Ctrl+C / Break / 关闭
    win_handler: Any = None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        @PHANDLER_ROUTINE
        def ctrl_handler(ctrl_type: int) -> bool:
            # 0 CTRL_C_EVENT, 1 CTRL_BREAK_EVENT, 2 CTRL_CLOSE_EVENT
            if ctrl_type in (0, 1, 2):
                request_quit()
                return True
            return False

        win_handler = ctrl_handler
        if kernel32.SetConsoleCtrlHandler(win_handler, True):
            logger.debug("已注册 SetConsoleCtrlHandler → 退出队列")
        else:
            win_handler = None
            logger.warning("SetConsoleCtrlHandler 注册失败，可改用配置中的退出热键（如 ctrl+q）")
    except Exception:
        logger.warning("控制台退出处理注册失败，请使用热键退出", exc_info=True)

    # SetConsoleCtrlHandler 失败时再尝试 SIGINT（避免与控制台处理重复注册）
    if win_handler is None:
        try:
            old_sigint = signal.signal(signal.SIGINT, _on_signal)
        except (ValueError, OSError):
            old_sigint = None

    def cleanup_win() -> None:
        if win_handler is not None:
            try:
                import ctypes

                ctypes.windll.kernel32.SetConsoleCtrlHandler(win_handler, False)
            except Exception:
                try:
                    import ctypes

                    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
                except Exception:
                    pass
        if old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, old_sigint)
            except (ValueError, OSError):
                pass

    return cleanup_win
