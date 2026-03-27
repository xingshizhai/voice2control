from __future__ import annotations

from typing import Protocol


class ClipboardBackend(Protocol):
    def get_text(self) -> str | None: ...

    def set_text(self, text: str) -> None: ...


class PyperclipClipboard:
    def __init__(self) -> None:
        import pyperclip

        self._pyperclip = pyperclip

    def get_text(self) -> str | None:
        try:
            t = self._pyperclip.paste()
        except Exception:
            return None
        if t is None:
            return None
        return str(t)

    def set_text(self, text: str) -> None:
        self._pyperclip.copy(text)
