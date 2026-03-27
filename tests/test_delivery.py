from __future__ import annotations

from dataclasses import dataclass

from vc.config import DeliveryAction, DeliveryConfig
from vc.output_module.delivery import Deliverer


@dataclass
class FakeClipboard:
    current: str | None = None

    def get_text(self) -> str | None:
        return self.current

    def set_text(self, text: str) -> None:
        self.current = text


class FakeKeyboard:
    def __init__(self) -> None:
        self.taps: list[tuple[str, ...]] = []

    def tap(self, keys: tuple[str, ...]) -> None:
        self.taps.append(keys)


def test_paste_only_skips_send() -> None:
    clip = FakeClipboard()
    kb = FakeKeyboard()
    actions = (
        DeliveryAction("paste", ("ctrl", "v")),
        DeliveryAction("send", ("enter",)),
    )
    cfg = DeliveryConfig(
        mode="paste_only",
        profile="p",
        profiles={"p": actions},
        restore_clipboard=False,
    )
    d = Deliverer(cfg, clip, kb)
    d.deliver("hello")
    assert kb.taps == [("ctrl", "v")]
    assert clip.current == "hello"


def test_paste_and_send_order() -> None:
    clip = FakeClipboard()
    kb = FakeKeyboard()
    actions = (
        DeliveryAction("paste", ("ctrl", "v")),
        DeliveryAction("send", ("enter",)),
    )
    cfg = DeliveryConfig(
        mode="paste_and_send",
        profile="p",
        profiles={"p": actions},
        restore_clipboard=False,
    )
    d = Deliverer(cfg, clip, kb)
    d.deliver("你好")
    assert kb.taps == [("ctrl", "v"), ("enter",)]
    assert clip.current == "你好"


def test_window_whitelist_blocks_when_not_matched() -> None:
    clip = FakeClipboard()
    kb = FakeKeyboard()
    actions = (DeliveryAction("paste", ("ctrl", "v")),)
    cfg = DeliveryConfig(
        mode="paste_only",
        profile="p",
        profiles={"p": actions},
        restore_clipboard=False,
        window_whitelist=("Cursor",),
    )
    d = Deliverer(cfg, clip, kb, window_title_provider=lambda: "Notepad")
    d.deliver("test")
    assert kb.taps == []


def test_window_whitelist_allows_when_matched() -> None:
    clip = FakeClipboard()
    kb = FakeKeyboard()
    actions = (DeliveryAction("paste", ("ctrl", "v")),)
    cfg = DeliveryConfig(
        mode="paste_only",
        profile="p",
        profiles={"p": actions},
        restore_clipboard=False,
        window_whitelist=("Cursor",),
    )
    d = Deliverer(cfg, clip, kb, window_title_provider=lambda: "Cursor - AI Chat")
    d.deliver("test")
    assert kb.taps == [("ctrl", "v")]


def test_auto_send_enter_false_skips_send_action() -> None:
    clip = FakeClipboard()
    kb = FakeKeyboard()
    actions = (
        DeliveryAction("paste", ("ctrl", "v")),
        DeliveryAction("send", ("enter",)),
    )
    cfg = DeliveryConfig(
        mode="paste_and_send",
        profile="p",
        profiles={"p": actions},
        restore_clipboard=False,
        auto_send_enter=False,
    )
    d = Deliverer(cfg, clip, kb)
    d.deliver("hello")
    assert kb.taps == [("ctrl", "v")]
