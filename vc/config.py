from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

DeliveryMode = Literal["paste_and_send", "paste_only", "review"]
ASRProvider = Literal["local", "dashscope"]


@dataclass(frozen=True)
class AsrConfig:
    provider_key: str = "default"
    base_url: str = ""
    provider: ASRProvider = "local"
    ws_path: str = "/"
    timeout_sec: float = 60.0
    mock: bool = False
    # 使用 wss:// 且服务为自签名证书时通常为 true；生产可改为 false 并配置 CA
    insecure_ssl: bool = True
    # 逆文本归一化（含标点恢复等）开关
    use_itn: bool = True
    # DashScope 配置（provider=dashscope 时生效）
    dashscope_model: str = "fun-asr-realtime"
    dashscope_api_key: str | None = None
    dashscope_api_key_env: str = "DASHSCOPE_API_KEY"
    dashscope_base_websocket_api_url: str | None = None
    dashscope_semantic_punctuation_enabled: bool = True


@dataclass(frozen=True)
class HotkeyConfig:
    push_to_talk: str = "f8"
    rerecord: str = "ctrl+shift+r"
    quit: str = "ctrl+q"
    recognition_enabled_on_start: bool = True


@dataclass(frozen=True)
class DeliveryAction:
    action: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryConfig:
    mode: DeliveryMode
    profile: str
    profiles: dict[str, tuple[DeliveryAction, ...]]
    restore_clipboard: bool = True
    auto_send_enter: bool = True
    key_tap_interval_ms: int = 40
    restore_clipboard_delay_ms: int = 180
    window_whitelist: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoryConfig:
    max_items: int = 50


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    max_seconds: float = 60.0


@dataclass(frozen=True)
class GuiConfig:
    minimize_to_tray_on_close: bool = True
    auto_start_listening: bool = True


@dataclass(frozen=True)
class LexiconConfig:
    enabled: bool = False
    db_path: str = "data/lexicon.db"
    domain: str = "default"


@dataclass(frozen=True)
class AppConfig:
    asr: AsrConfig
    hotkey: HotkeyConfig
    delivery: DeliveryConfig
    history: HistoryConfig
    audio: AudioConfig
    gui: GuiConfig
    lexicon: LexiconConfig


def _req_str(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"配置缺少或无效字符串: {key}")
    return v.strip()


def _parse_actions(raw: Any) -> tuple[DeliveryAction, ...]:
    if not isinstance(raw, list):
        raise ValueError("delivery.profiles.*.actions 必须为列表")
    out: list[DeliveryAction] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"actions[{i}] 必须为对象")
        action = item.get("action")
        keys = item.get("keys")
        if not isinstance(action, str) or not action.strip():
            raise ValueError(f"actions[{i}].action 无效")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError(f"actions[{i}].keys 必须为字符串列表")
        out.append(
            DeliveryAction(
                action=action.strip(),
                keys=tuple(k.strip().lower() for k in keys),
            )
        )
    return tuple(out)


def _parse_profiles(raw: Any) -> dict[str, tuple[DeliveryAction, ...]]:
    if not isinstance(raw, dict):
        raise ValueError("delivery.profiles 必须为对象")
    profiles: dict[str, tuple[DeliveryAction, ...]] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(body, dict):
            raise ValueError(f"profiles.{name} 必须为对象")
        profiles[name.strip()] = _parse_actions(body.get("actions"))
    return profiles


def load_app_config(path: Path | str) -> AppConfig:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("根节点必须为映射")

    asr_raw = data.get("asr") or {}
    if not isinstance(asr_raw, dict):
        raise ValueError("asr 必须为对象")
    asr_timeout = float(asr_raw.get("timeout_sec", 60.0))
    asr_mock = bool(asr_raw.get("mock", False))

    # 新结构：asr.active_provider + asr.providers
    providers_raw = asr_raw.get("providers")
    active_provider = str(asr_raw.get("active_provider") or "default").strip()
    selected_raw: dict[str, Any]
    if providers_raw is not None:
        if not isinstance(providers_raw, dict) or not providers_raw:
            raise ValueError("asr.providers 必须为非空对象")
        if active_provider not in providers_raw:
            raise ValueError(f"asr.active_provider 未在 asr.providers 中定义: {active_provider}")
        selected_any = providers_raw.get(active_provider)
        if not isinstance(selected_any, dict):
            raise ValueError(f"asr.providers.{active_provider} 必须为对象")
        selected_raw = selected_any
    else:
        # 兼容旧结构：asr.* 直接是单 provider 配置
        selected_raw = asr_raw
        active_provider = "default"

    asr_provider = str(selected_raw.get("provider") or "local").strip().lower()
    if asr_provider not in ("local", "dashscope"):
        raise ValueError("asr.provider 必须为 local | dashscope")
    base_url = str(selected_raw.get("base_url") or "").strip()
    if asr_provider == "local" and not base_url:
        raise ValueError("local provider 必须配置 base_url")
    if not base_url:
        base_url = "ws://127.0.0.1:6006"

    asr = AsrConfig(
        provider_key=active_provider,
        provider=asr_provider,  # type: ignore[arg-type]
        base_url=base_url,
        ws_path=str(selected_raw.get("ws_path") or "/").strip() or "/",
        timeout_sec=float(selected_raw.get("timeout_sec", asr_timeout)),
        mock=asr_mock,
        insecure_ssl=bool(selected_raw.get("insecure_ssl", True)),
        use_itn=bool(selected_raw.get("use_itn", True)),
        dashscope_model=str(selected_raw.get("dashscope_model") or "fun-asr-realtime").strip(),
        dashscope_api_key=(
            str(selected_raw.get("dashscope_api_key")).strip()
            if selected_raw.get("dashscope_api_key") is not None
            else None
        ),
        dashscope_api_key_env=str(
            selected_raw.get("dashscope_api_key_env") or "DASHSCOPE_API_KEY",
        ).strip(),
        dashscope_base_websocket_api_url=(
            str(selected_raw.get("dashscope_base_websocket_api_url")).strip()
            if selected_raw.get("dashscope_base_websocket_api_url") is not None
            else None
        ),
        dashscope_semantic_punctuation_enabled=bool(
            selected_raw.get("dashscope_semantic_punctuation_enabled", True),
        ),
    )

    hk_raw = data.get("hotkey") or {}
    if not isinstance(hk_raw, dict):
        raise ValueError("hotkey 必须为对象")
    hotkey = HotkeyConfig(
        push_to_talk=str(hk_raw.get("push_to_talk") or "f8").strip().lower(),
        rerecord=str(hk_raw.get("rerecord") or "ctrl+shift+r").strip().lower(),
        quit=str(hk_raw.get("quit") or "ctrl+q").strip().lower(),
        recognition_enabled_on_start=bool(hk_raw.get("recognition_enabled_on_start", True)),
    )

    del_raw = data.get("delivery") or {}
    if not isinstance(del_raw, dict):
        raise ValueError("delivery 必须为对象")
    mode = str(del_raw.get("mode") or "paste_and_send").strip().lower()
    if mode not in ("paste_and_send", "paste_only", "review"):
        raise ValueError("delivery.mode 必须为 paste_and_send | paste_only | review")
    profile = str(del_raw.get("profile") or "cursor_win").strip()
    profiles = _parse_profiles(del_raw.get("profiles"))
    if profile not in profiles:
        raise ValueError(f"delivery.profile 未找到: {profile}")
    delivery = DeliveryConfig(
        mode=mode,  # type: ignore[arg-type]
        profile=profile,
        profiles=profiles,
        restore_clipboard=bool(del_raw.get("restore_clipboard", True)),
        auto_send_enter=bool(del_raw.get("auto_send_enter", True)),
        key_tap_interval_ms=int(del_raw.get("key_tap_interval_ms", 40)),
        restore_clipboard_delay_ms=int(del_raw.get("restore_clipboard_delay_ms", 180)),
        window_whitelist=tuple(
            str(x).strip()
            for x in (del_raw.get("window_whitelist") or [])
            if str(x).strip()
        ),
    )

    hist_raw = data.get("history") or {}
    if not isinstance(hist_raw, dict):
        raise ValueError("history 必须为对象")
    history = HistoryConfig(max_items=int(hist_raw.get("max_items", 50)))

    aud_raw = data.get("audio") or {}
    if not isinstance(aud_raw, dict):
        raise ValueError("audio 必须为对象")
    audio = AudioConfig(
        sample_rate=int(aud_raw.get("sample_rate", 16000)),
        channels=int(aud_raw.get("channels", 1)),
        max_seconds=float(aud_raw.get("max_seconds", 60.0)),
    )
    gui_raw = data.get("gui") or {}
    if not isinstance(gui_raw, dict):
        raise ValueError("gui 必须为对象")
    gui = GuiConfig(
        minimize_to_tray_on_close=bool(gui_raw.get("minimize_to_tray_on_close", True)),
        auto_start_listening=bool(gui_raw.get("auto_start_listening", True)),
    )
    lex_raw = data.get("lexicon") or {}
    if not isinstance(lex_raw, dict):
        raise ValueError("lexicon 必须为对象")
    lexicon = LexiconConfig(
        enabled=bool(lex_raw.get("enabled", False)),
        db_path=str(lex_raw.get("db_path") or "data/lexicon.db").strip() or "data/lexicon.db",
        domain=str(lex_raw.get("domain") or "default").strip() or "default",
    )

    return AppConfig(
        asr=asr,
        hotkey=hotkey,
        delivery=delivery,
        history=history,
        audio=audio,
        gui=gui,
        lexicon=lexicon,
    )


def load_app_config_with_env(path: Path | str) -> AppConfig:
    """支持环境变量覆盖：VOICE_CONTROLLER_MOCK_ASR=1 时强制 ASR mock。"""
    cfg = load_app_config(path)
    if os.environ.get("VOICE_CONTROLLER_MOCK_ASR", "").strip() in ("1", "true", "yes"):
        return AppConfig(
            asr=AsrConfig(
                provider_key=cfg.asr.provider_key,
                provider=cfg.asr.provider,
                base_url=cfg.asr.base_url,
                ws_path=cfg.asr.ws_path,
                timeout_sec=cfg.asr.timeout_sec,
                mock=True,
                insecure_ssl=cfg.asr.insecure_ssl,
                use_itn=cfg.asr.use_itn,
                dashscope_model=cfg.asr.dashscope_model,
                dashscope_api_key=cfg.asr.dashscope_api_key,
                dashscope_api_key_env=cfg.asr.dashscope_api_key_env,
                dashscope_base_websocket_api_url=cfg.asr.dashscope_base_websocket_api_url,
                dashscope_semantic_punctuation_enabled=cfg.asr.dashscope_semantic_punctuation_enabled,
            ),
            hotkey=cfg.hotkey,
            delivery=cfg.delivery,
            history=cfg.history,
            audio=cfg.audio,
            gui=cfg.gui,
            lexicon=cfg.lexicon,
        )
    return cfg
