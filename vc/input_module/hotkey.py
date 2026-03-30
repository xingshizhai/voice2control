from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from typing import Callable

from vc.config import HotkeyConfig

logger = logging.getLogger(__name__)

HotkeyEvent = tuple[str, ...]


# ---------------------------------------------------------------------------
# 环境检测
# ---------------------------------------------------------------------------

def _is_wayland_session() -> bool:
    """是否运行在 Wayland 会话（含带 XWayland 的情况）。"""
    return sys.platform == "linux" and (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


# ---------------------------------------------------------------------------
# pynput 后端（Linux X11 纯净模式 / macOS）
# ---------------------------------------------------------------------------

_MODIFIER_NORMALIZE = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd_l": "cmd", "cmd_r": "cmd",
}


def _pynput_key_to_str(key) -> str:
    try:
        from pynput.keyboard import Key
        if isinstance(key, Key):
            return _MODIFIER_NORMALIZE.get(key.name, key.name)
    except Exception:
        pass
    try:
        from pynput.keyboard import KeyCode
        if isinstance(key, KeyCode) and key.char:
            return key.char.lower()
    except Exception:
        pass
    return str(key).lower()


def _parse_combo(combo: str) -> frozenset[str]:
    parts = set()
    for p in combo.lower().split("+"):
        p = p.strip()
        if p in ("command", "windows", "super", "meta"):
            p = "cmd"
        if p == "cmd" and sys.platform != "darwin":
            p = "ctrl"
        parts.add(p)
    return frozenset(parts)


class _PynputHotkeyBackend:
    def __init__(self) -> None:
        self._pressed: set[str] = set()
        self._press_cbs: dict[str, list[Callable]] = {}
        self._release_cbs: dict[str, list[Callable]] = {}
        self._hotkeys: list[tuple[frozenset[str], Callable]] = []
        self._triggered: set[frozenset[str]] = set()
        self._listener = None

    def on_press_key(self, key_name: str, cb: Callable) -> None:
        self._press_cbs.setdefault(key_name.strip().lower(), []).append(cb)

    def on_release_key(self, key_name: str, cb: Callable) -> None:
        self._release_cbs.setdefault(key_name.strip().lower(), []).append(cb)

    def add_hotkey(self, combo: str, cb: Callable) -> None:
        self._hotkeys.append((_parse_combo(combo), cb))

    def _on_press(self, key) -> None:
        name = _pynput_key_to_str(key)
        self._pressed.add(name)
        for cb in self._press_cbs.get(name, []):
            try:
                cb(key)
            except Exception:
                logger.debug("pynput press 回调异常", exc_info=True)
        for combo, cb in self._hotkeys:
            if combo in self._triggered:
                continue
            if combo.issubset(self._pressed):
                self._triggered.add(combo)
                try:
                    cb()
                except Exception:
                    logger.debug("pynput 组合键回调异常", exc_info=True)

    def _on_release(self, key) -> None:
        name = _pynput_key_to_str(key)
        for cb in self._release_cbs.get(name, []):
            try:
                cb(key)
            except Exception:
                logger.debug("pynput release 回调异常", exc_info=True)
        self._pressed.discard(name)
        self._triggered = {c for c in self._triggered if name not in c}

    def start(self) -> None:
        from pynput.keyboard import Listener
        self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# ---------------------------------------------------------------------------
# evdev 后端（Linux Wayland）
# ---------------------------------------------------------------------------

def _build_evdev_key_map() -> dict[int, str]:
    from evdev import ecodes
    m: dict[int, str] = {}
    for i in range(1, 13):
        code = getattr(ecodes, f"KEY_F{i}", None)
        if code is not None:
            m[code] = f"f{i}"
    specials: dict[str, str] = {
        "KEY_ESC": "esc", "KEY_ENTER": "enter", "KEY_TAB": "tab",
        "KEY_SPACE": "space", "KEY_BACKSPACE": "backspace",
        "KEY_INSERT": "insert", "KEY_DELETE": "delete",
        "KEY_HOME": "home", "KEY_END": "end",
        "KEY_PAGEUP": "page_up", "KEY_PAGEDOWN": "page_down",
        "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
        "KEY_PAUSE": "pause", "KEY_SCROLLLOCK": "scroll_lock",
        "KEY_SYSRQ": "print_screen", "KEY_CAPSLOCK": "caps_lock",
        "KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl",
        "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift",
        "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt",
        "KEY_LEFTMETA": "cmd", "KEY_RIGHTMETA": "cmd",
    }
    for attr, name in specials.items():
        code = getattr(ecodes, attr, None)
        if code is not None:
            m[code] = name
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        code = getattr(ecodes, f"KEY_{c}", None)
        if code is not None:
            m[code] = c.lower()
    for c in "0123456789":
        code = getattr(ecodes, f"KEY_{c}", None)
        if code is not None:
            m[code] = c
    return m


class _EvdevHotkeyBackend:
    """Linux Wayland：通过 evdev 直接读取 /dev/input/event* 实现全局热键。

    前提：用户在 input 组，否则无法打开设备文件。
    """

    def __init__(self) -> None:
        self._pressed: set[str] = set()
        self._press_cbs: dict[str, list[Callable]] = {}
        self._release_cbs: dict[str, list[Callable]] = {}
        self._hotkeys: list[tuple[frozenset[str], Callable]] = []
        self._triggered: set[frozenset[str]] = set()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._key_map: dict[int, str] = {}

    def on_press_key(self, key_name: str, cb: Callable) -> None:
        self._press_cbs.setdefault(key_name.strip().lower(), []).append(cb)

    def on_release_key(self, key_name: str, cb: Callable) -> None:
        self._release_cbs.setdefault(key_name.strip().lower(), []).append(cb)

    def add_hotkey(self, combo: str, cb: Callable) -> None:
        self._hotkeys.append((_parse_combo(combo), cb))

    def _find_keyboards(self) -> list:
        from evdev import InputDevice, list_devices, ecodes
        keyboards = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    keys = caps[ecodes.EV_KEY]
                    if ecodes.KEY_A in keys and ecodes.KEY_F8 in keys:
                        keyboards.append(dev)
            except PermissionError:
                pass
            except Exception:
                pass
        return keyboards

    def _handle(self, code: int, value: int) -> None:
        name = self._key_map.get(code, "")
        if not name:
            return
        if value == 1:  # press
            self._pressed.add(name)
            for cb in self._press_cbs.get(name, []):
                try:
                    cb(None)
                except Exception:
                    logger.debug("evdev press 回调异常", exc_info=True)
            for combo, cb in self._hotkeys:
                if combo in self._triggered:
                    continue
                if combo.issubset(self._pressed):
                    self._triggered.add(combo)
                    try:
                        cb()
                    except Exception:
                        logger.debug("evdev 组合键回调异常", exc_info=True)
        elif value == 0:  # release
            for cb in self._release_cbs.get(name, []):
                try:
                    cb(None)
                except Exception:
                    logger.debug("evdev release 回调异常", exc_info=True)
            self._pressed.discard(name)
            self._triggered = {c for c in self._triggered if name not in c}

    def _read_device(self, dev) -> None:
        from evdev import ecodes
        try:
            for event in dev.read_loop():
                if self._stop_event.is_set():
                    break
                if event.type == ecodes.EV_KEY:
                    self._handle(event.code, event.value)
        except Exception as e:
            logger.debug("evdev 读取设备 %s 中断: %s", dev.path, e)

    def start(self) -> None:
        self._key_map = _build_evdev_key_map()
        keyboards = self._find_keyboards()
        if not keyboards:
            raise PermissionError(
                "未找到可读取的键盘设备（/dev/input/event*）。\n"
                "请将当前用户加入 input 组后重新登录：\n"
                "  sudo usermod -aG input $USER\n"
                "  （注销并重新登录后生效）"
            )
        for dev in keyboards:
            t = threading.Thread(
                target=self._read_device, args=(dev,),
                daemon=True, name=f"evdev-{dev.path}",
            )
            self._threads.append(t)
            t.start()
        logger.info("evdev 已监听 %d 个键盘设备", len(keyboards))

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def register_hotkeys(
    cfg: HotkeyConfig,
    q: "queue.Queue[HotkeyEvent]",
) -> Callable[[], None]:
    """注册全局热键，事件放入队列。按平台自动选择后端。"""
    if sys.platform == "win32":
        return _register_with_keyboard(cfg, q)
    if _is_wayland_session():
        return _register_with_evdev(cfg, q)
    return _register_with_pynput(cfg, q)


def _make_callbacks(cfg: HotkeyConfig, q: "queue.Queue[HotkeyEvent]"):
    def ptt_down(_=None): q.put(("ptt", "down"))
    def ptt_up(_=None): q.put(("ptt", "up"))
    def on_esc(_=None): q.put(("cancel",))
    def on_quit(): q.put(("quit",))
    def on_rerecord(): q.put(("rerecord",))
    return ptt_down, ptt_up, on_esc, on_quit, on_rerecord


def _register_with_keyboard(
    cfg: HotkeyConfig,
    q: "queue.Queue[HotkeyEvent]",
) -> Callable[[], None]:
    import keyboard
    ptt_down, ptt_up, on_esc, on_quit, on_rerecord = _make_callbacks(cfg, q)
    keyboard.on_press_key(cfg.push_to_talk, ptt_down)
    keyboard.on_release_key(cfg.push_to_talk, ptt_up)
    keyboard.on_press_key("esc", on_esc)
    keyboard.add_hotkey(cfg.quit, on_quit, suppress=False)
    keyboard.add_hotkey(cfg.rerecord, on_rerecord, suppress=False)
    logger.info("热键已注册（keyboard/Windows）：PTT=%s", cfg.push_to_talk)
    return keyboard.unhook_all


def _register_with_pynput(
    cfg: HotkeyConfig,
    q: "queue.Queue[HotkeyEvent]",
) -> Callable[[], None]:
    ptt_down, ptt_up, on_esc, on_quit, on_rerecord = _make_callbacks(cfg, q)
    backend = _PynputHotkeyBackend()
    backend.on_press_key(cfg.push_to_talk, ptt_down)
    backend.on_release_key(cfg.push_to_talk, ptt_up)
    backend.on_press_key("esc", on_esc)
    backend.add_hotkey(cfg.quit, on_quit)
    backend.add_hotkey(cfg.rerecord, on_rerecord)
    backend.start()
    logger.info("热键已注册（pynput/X11）：PTT=%s", cfg.push_to_talk)
    return backend.stop


def _register_with_evdev(
    cfg: HotkeyConfig,
    q: "queue.Queue[HotkeyEvent]",
) -> Callable[[], None]:
    """Wayland：优先使用 evdev；若无权限则降级到 pynput 并输出警告。"""
    ptt_down, ptt_up, on_esc, on_quit, on_rerecord = _make_callbacks(cfg, q)
    backend = _EvdevHotkeyBackend()
    backend.on_press_key(cfg.push_to_talk, ptt_down)
    backend.on_release_key(cfg.push_to_talk, ptt_up)
    backend.on_press_key("esc", on_esc)
    backend.add_hotkey(cfg.quit, on_quit)
    backend.add_hotkey(cfg.rerecord, on_rerecord)
    try:
        backend.start()
        logger.info("热键已注册（evdev/Wayland）：PTT=%s", cfg.push_to_talk)
        return backend.stop
    except PermissionError as e:
        logger.warning(
            "evdev 权限不足，降级到 pynput（全局热键在 Wayland 下可能不可靠）。\n%s", e
        )
        return _register_with_pynput(cfg, q)
