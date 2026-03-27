from __future__ import annotations

from collections import deque


class TextHistory:
    def __init__(self, max_items: int = 50) -> None:
        self._max = max(1, max_items)
        self._q: deque[str] = deque(maxlen=self._max)

    def push(self, text: str) -> None:
        t = text.strip()
        if not t:
            return
        self._q.append(t)

    def last(self) -> str | None:
        return self._q[-1] if self._q else None

    def clear(self) -> None:
        self._q.clear()
