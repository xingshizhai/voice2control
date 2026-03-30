from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from array import array
from typing import Callable

from vc.asr_module.client import ASRError, build_asr_client
from vc.backends.clipboard import PyperclipClipboard
from vc.backends.keyboard import KeyboardTap
from vc.config import AppConfig
from vc.core_module.history import TextHistory
from vc.input_module.audio import AudioRecorder
from vc.input_module.hotkey import HotkeyEvent, register_hotkeys
from vc.lexicon_module.service import LexiconCorrector
from vc.output_module.delivery import Deliverer
from vc.platform_module.shutdown_handlers import install_graceful_shutdown

logger = logging.getLogger(__name__)

_MIN_PCM_BYTES = 3200


class VoicePipeline:
    def __init__(
        self,
        cfg: AppConfig,
        on_state: Callable[[str], None] | None = None,
        on_transcript: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._queue: queue.Queue[HotkeyEvent] = queue.Queue()
        self._state: str = "idle"
        self._recognition_enabled = cfg.hotkey.recognition_enabled_on_start
        self._stop_event = threading.Event()
        self._ptt_is_down = False
        self._silence_started_at: float | None = None
        self._on_state = on_state
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._recorder = AudioRecorder(
            sample_rate=cfg.audio.sample_rate,
            channels=cfg.audio.channels,
            max_seconds=cfg.audio.max_seconds,
        )
        self._asr = build_asr_client(cfg.asr)
        self._clipboard = PyperclipClipboard()
        self._keyboard = KeyboardTap()
        self._deliverer = Deliverer(cfg.delivery, self._clipboard, self._keyboard)
        self._history = TextHistory(cfg.history.max_items)
        self._lexicon = LexiconCorrector(cfg.lexicon)
        self._unhook: Callable[[], None] | None = None

    def _emit_state(self, state: str) -> None:
        self._state = state
        if self._on_state:
            try:
                self._on_state(state)
            except Exception:
                logger.debug("状态回调失败", exc_info=True)

    def _emit_transcript(self, text: str) -> None:
        if self._on_transcript:
            try:
                self._on_transcript(text)
            except Exception:
                logger.debug("文本回调失败", exc_info=True)

    def _emit_error(self, message: str) -> None:
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:
                logger.debug("错误回调失败", exc_info=True)

    def request_stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put(("quit",), block=False)
        except Exception:
            pass

    def set_recognition_enabled(self, enabled: bool) -> None:
        try:
            self._queue.put(("set_enabled", "1" if enabled else "0"), block=False)
        except Exception:
            pass

    def run(self) -> None:
        self._unhook = register_hotkeys(self._cfg.hotkey, self._queue)
        shutdown_cleanup = install_graceful_shutdown(self._queue)
        if self._cfg.hotkey.trigger_mode == "toggle":
            logger.info("语音管道已启动。按 %s 开始录音，再按一次结束并识别投递。", self._cfg.hotkey.push_to_talk)
        else:
            logger.info("语音管道已启动。按住 %s 说话，松开后识别并投递。", self._cfg.hotkey.push_to_talk)
        if self._cfg.vad.enabled and self._cfg.hotkey.trigger_mode == "toggle":
            logger.info(
                "VAD 自动结束已启用：静音阈值=%dms 能量阈值=%d",
                self._cfg.vad.silence_threshold_ms,
                self._cfg.vad.energy_threshold,
            )
        logger.info("配置 recognition_enabled_on_start=%s", self._recognition_enabled)
        if self._recognition_enabled:
            self._emit_state("idle")
            logger.info("识别状态：已启用")
        else:
            self._emit_state("disabled")
            logger.info("识别状态：已禁用（可在图形界面中手动启用）")
        try:
            while not self._stop_event.is_set():
                try:
                    evt = self._queue.get(timeout=0.2)
                except queue.Empty:
                    self._maybe_auto_stop_recording_by_vad()
                    continue
                if not self._dispatch(evt):
                    break
        finally:
            shutdown_cleanup()
            if self._unhook:
                self._unhook()
                self._unhook = None
            self._emit_state("stopped")

    def _dispatch(self, evt: HotkeyEvent) -> bool:
        kind = evt[0]
        if kind == "quit":
            logger.info("退出")
            return False
        if kind == "cancel":
            if self._state == "recording":
                self._recorder.cancel()
                self._silence_started_at = None
                self._emit_state("idle")
                logger.info("已取消录音")
            return True
        if kind == "rerecord":
            logger.info("重录（当前轮若仍在录音可先 Esc 取消）")
            return True
        if kind == "set_enabled":
            enabled = len(evt) > 1 and evt[1] == "1"
            self._recognition_enabled = enabled
            if self._recognition_enabled:
                logger.info("识别已启用")
                if self._state != "recording":
                    self._emit_state("idle")
            else:
                logger.info("识别已禁用")
                if self._state == "recording":
                    self._recorder.cancel()
                self._emit_state("disabled")
            return True
        if kind != "ptt":
            return True
        phase = evt[1] if len(evt) > 1 else ""
        if phase == "down":
            if self._ptt_is_down:
                return True
            self._ptt_is_down = True
            if not self._recognition_enabled:
                logger.debug("识别已禁用，忽略按下录音热键")
                return True
            if self._cfg.hotkey.trigger_mode == "toggle":
                if self._state == "idle":
                    self._recorder.start()
                    self._silence_started_at = None
                    self._emit_state("recording")
                    logger.info("开始录音（toggle）")
                elif self._state == "recording":
                    self._finalize_recording("toggle 手动结束")
                return True
            if self._state != "idle":
                return True
            self._recorder.start()
            self._silence_started_at = None
            self._emit_state("recording")
            logger.info("开始录音")
            return True
        if phase == "up":
            self._ptt_is_down = False
            if self._cfg.hotkey.trigger_mode == "toggle":
                return True
            if self._state != "recording":
                return True
            self._finalize_recording("松开热键")
            return True
        return True

    def _finalize_recording(self, reason: str) -> None:
        if self._state != "recording":
            return
        pcm = self._recorder.stop()
        self._silence_started_at = None
        self._emit_state("recognizing")
        if len(pcm) < _MIN_PCM_BYTES:
            logger.warning("录音过短，已忽略（%s）", reason)
            self._emit_state("idle")
            return
        logger.info("录音结束：%s", reason)
        self._process_audio(pcm)
        self._emit_state("idle")

    def _maybe_auto_stop_recording_by_vad(self) -> None:
        if not self._cfg.vad.enabled or self._cfg.hotkey.trigger_mode != "toggle":
            return
        if self._state != "recording":
            self._silence_started_at = None
            return
        pcm = self._recorder.snapshot()
        if not pcm:
            return
        if self._is_recent_audio_silent(pcm):
            now = time.perf_counter()
            if self._silence_started_at is None:
                self._silence_started_at = now
                return
            silent_ms = int((now - self._silence_started_at) * 1000)
            if silent_ms >= self._cfg.vad.silence_threshold_ms:
                self._finalize_recording(f"VAD 静音 {silent_ms}ms 自动结束")
        else:
            self._silence_started_at = None

    def _is_recent_audio_silent(self, pcm: bytes) -> bool:
        # PCM 16-bit little-endian，取末尾窗口计算平均绝对能量。
        channels = max(1, self._cfg.audio.channels)
        window_samples = int(self._cfg.audio.sample_rate * (self._cfg.vad.check_window_ms / 1000.0)) * channels
        if window_samples <= 0:
            return False
        sample_count = len(pcm) // 2
        if sample_count < window_samples:
            return False
        tail = pcm[-window_samples * 2 :]
        samples = array("h")
        samples.frombytes(tail)
        if not samples:
            return False
        avg_energy = sum(abs(int(v)) for v in samples) / len(samples)
        return avg_energy < self._cfg.vad.energy_threshold

    def _process_audio(self, pcm: bytes) -> None:
        t0 = time.perf_counter()
        try:
            text = self._asr.transcribe(
                pcm,
                self._cfg.audio.sample_rate,
                self._cfg.audio.channels,
            )
        except ASRError as e:
            logger.error("ASR 失败: %s", e)
            self._emit_error(str(e))
            return
        except Exception:
            logger.exception("ASR 异常")
            self._emit_error("ASR 异常")
            return
        dt = time.perf_counter() - t0
        logger.info("识别完成 (%.2fs)，长度=%d 字", dt, len(text.strip()))
        if not text.strip():
            logger.warning("识别结果为空")
            return
        corrected_text, replaced_count = self._lexicon.correct(text)
        if replaced_count > 0:
            logger.info("词库纠正命中 %d 处", replaced_count)
            text = corrected_text
        self._emit_transcript(text)
        try:
            self._emit_state("delivering")
            self._deliverer.deliver(text)
        except Exception:
            logger.exception("投递失败")
            self._emit_error("投递失败")
            return
        self._history.push(text)
        logger.info("本轮完成")


def warn_if_unsupported_platform() -> None:
    if sys.platform != "win32":
        logger.warning(
            "当前版本仅在 Windows 上完整验证热键与键盘投递；其他系统可能需管理员权限或不可用。",
        )
