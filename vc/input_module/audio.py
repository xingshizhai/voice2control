from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class AudioRecorder:
    """在后台线程中采集 PCM（16-bit LE, mono），按 stop/cancel 结束。"""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        max_seconds: float = 60.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._max_seconds = max_seconds
        self._buf = bytearray()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                logger.warning("录音已在进行中，忽略重复 start")
                return
            self._buf.clear()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="audio-recorder")
            self._thread.start()

    def _run(self) -> None:
        try:
            import pyaudio
        except ImportError as e:
            logger.exception("需要安装 PyAudio")
            raise SystemExit("请安装 PyAudio：pip install pyaudio") from e

        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._sample_rate,
                input=True,
                frames_per_buffer=1024,
            )
            max_bytes = int(self._sample_rate * 2 * self._channels * self._max_seconds)
            while not self._stop.is_set():
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                except Exception:
                    break
                self._buf.extend(data)
                if len(self._buf) >= max_bytes:
                    logger.info("达到最大录音时长，自动停止")
                    break
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            try:
                pa.terminate()
            except Exception:
                pass

    def stop(self) -> bytes:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=15.0)
        self._thread = None
        return bytes(self._buf)

    def cancel(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=15.0)
        self._thread = None
        self._buf.clear()
