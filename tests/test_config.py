from __future__ import annotations

from pathlib import Path

import pytest

from voice_controller.config import load_app_config


def test_load_example_config(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    example = root / "config.example.yaml"
    cfg = load_app_config(example)
    assert cfg.asr.base_url.startswith(("ws://", "wss://"))
    assert cfg.asr.provider_key
    assert cfg.delivery.profile in cfg.delivery.profiles
    assert "cursor_win" in cfg.delivery.profiles


def test_profile_missing_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """
asr:
  base_url: "wss://localhost:1/"
  ws_path: "/x"
hotkey: {}
delivery:
  mode: paste_and_send
  profile: nope
  profiles:
    a:
      actions: []
history: {}
audio: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="profile"):
        load_app_config(p)


def test_dashscope_provider_allows_empty_base_url(tmp_path: Path) -> None:
    p = tmp_path / "dash.yaml"
    p.write_text(
        """
asr:
  active_provider: "ali"
  providers:
    ali:
      provider: dashscope
      dashscope_model: "fun-asr-realtime"
hotkey: {}
delivery:
  mode: paste_only
  profile: p
  profiles:
    p:
      actions:
        - action: paste
          keys: ["ctrl", "v"]
history: {}
audio: {}
""",
        encoding="utf-8",
    )
    cfg = load_app_config(p)
    assert cfg.asr.provider == "dashscope"


def test_active_provider_missing_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
asr:
  active_provider: "x"
  providers:
    y:
      provider: local
      base_url: "ws://127.0.0.1:6006"
hotkey: {}
delivery:
  mode: paste_only
  profile: p
  profiles:
    p:
      actions:
        - action: paste
          keys: ["ctrl", "v"]
history: {}
audio: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="active_provider"):
        load_app_config(p)
