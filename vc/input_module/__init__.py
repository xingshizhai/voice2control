"""Input modules such as audio and hotkeys."""

from vc.input_module.audio import AudioRecorder
from vc.input_module.hotkey import HotkeyEvent, register_hotkeys

__all__ = ["AudioRecorder", "HotkeyEvent", "register_hotkeys"]
