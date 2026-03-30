from __future__ import annotations

from pathlib import Path

import pytest

from vc.config import load_app_config


def test_load_example_config(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    example = root / "config.example.yaml"
    cfg = load_app_config(example)
    assert cfg.asr.base_url.startswith(("ws://", "wss://"))
    assert cfg.asr.provider_key
    assert cfg.delivery.profile in cfg.delivery.profiles
    assert "cursor_win" in cfg.delivery.profiles
    assert cfg.hotkey.trigger_mode in ("push_to_talk", "toggle")
    assert isinstance(cfg.vad.enabled, bool)
    assert isinstance(cfg.vad.silence_threshold_ms, int)
    assert isinstance(cfg.gui.minimize_to_tray_on_close, bool)
    assert isinstance(cfg.gui.auto_start_listening, bool)
    assert isinstance(cfg.gui.show_startup_guide, bool)
    assert isinstance(cfg.gui.show_floating_status, bool)
    assert cfg.gui.floating_status_position in ("bottom_right", "bottom_left")
    assert isinstance(cfg.gui.floating_status_font_size, int)
    assert isinstance(cfg.gui.floating_status_opacity, int)
    assert cfg.gui.floating_status_mode in ("always", "recording_only")
    assert isinstance(cfg.lexicon.enabled, bool)


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


def test_gui_minimize_to_tray_parse(tmp_path: Path) -> None:
    p = tmp_path / "gui.yaml"
    p.write_text(
        """
asr:
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
gui:
  minimize_to_tray_on_close: false
  auto_start_listening: false
  show_startup_guide: false
  show_floating_status: false
  floating_status_position: bottom_left
  floating_status_font_size: 14
  floating_status_opacity: 180
  floating_status_mode: recording_only
  floating_status_x: 300
  floating_status_y: 720
""",
        encoding="utf-8",
    )
    cfg = load_app_config(p)
    assert cfg.gui.minimize_to_tray_on_close is False
    assert cfg.gui.auto_start_listening is False
    assert cfg.gui.show_startup_guide is False
    assert cfg.gui.show_floating_status is False
    assert cfg.gui.floating_status_position == "bottom_left"
    assert cfg.gui.floating_status_font_size == 14
    assert cfg.gui.floating_status_opacity == 180
    assert cfg.gui.floating_status_mode == "recording_only"
    assert cfg.gui.floating_status_x == 300
    assert cfg.gui.floating_status_y == 720


def test_hotkey_trigger_mode_parse(tmp_path: Path) -> None:
    p = tmp_path / "hk.yaml"
    p.write_text(
        """
asr:
  base_url: "ws://127.0.0.1:6006"
hotkey:
  trigger_mode: toggle
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
vad:
  enabled: true
  silence_threshold_ms: 1200
  energy_threshold: 450
  check_window_ms: 300
""",
        encoding="utf-8",
    )
    cfg = load_app_config(p)
    assert cfg.hotkey.trigger_mode == "toggle"
    assert cfg.vad.enabled is True
    assert cfg.vad.silence_threshold_ms == 1200
