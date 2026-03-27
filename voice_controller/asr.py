from __future__ import annotations

import io
import json
import logging
import ssl
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import wave
from typing import Any, Protocol

from voice_controller.config import AsrConfig

logger = logging.getLogger(__name__)


class ASRError(Exception):
    pass


class ASRClient(Protocol):
    def transcribe(self, pcm: bytes, sample_rate: int, channels: int = 1) -> str: ...


class MockASRClient:
    """离线测试：不访问网络。"""

    def transcribe(self, pcm: bytes, sample_rate: int, channels: int = 1) -> str:
        if not pcm:
            return ""
        return "[mock] 语音已采集（mock 模式不调用真实 ASR）"


def pcm_s16le_to_wav(pcm: bytes, sample_rate: int, channels: int) -> bytes:
    """将 s16le PCM 封装为标准 WAV（见 docs/asr-service-api.md）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _build_ws_url(cfg: AsrConfig) -> str:
    base = cfg.base_url.rstrip("/")
    path = cfg.ws_path.strip()
    if not path.startswith("/"):
        path = "/" + path
    url = base if path == "/" else (base + path)

    parts = urlsplit(url)
    qs = dict(parse_qsl(parts.query, keep_blank_values=True))
    qs["use_itn"] = "true" if cfg.use_itn else "false"
    new_query = urlencode(qs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _connection_error_hint(exc: Exception) -> str:
    """为常见网络错误补充排查提示（不改变原始异常）。"""
    msg = str(exc).lower()
    hints: list[str] = []
    if "10061" in str(exc) or "actively refused" in msg or "积极拒绝" in str(exc):
        hints.append(
            "目标地址拒绝连接：该 IP/端口上没有服务在监听，或 ASR 未启动。"
        )
    if "10060" in str(exc) or "timed out" in msg or "超时" in str(exc):
        hints.append("连接超时：检查网络、防火墙或增大 timeout_sec。")
    if "10051" in str(exc) or "unreachable" in msg:
        hints.append("网络不可达：检查 IP 是否正确、是否同网段/VPN。")
    if not hints:
        hints.append("请核对 config.yaml 中 asr.base_url、ws_path，并确认 ASR 服务已运行。")
    return " ".join(hints)


def parse_asr_response(raw: str) -> str:
    """解析 ASR 服务返回的 JSON：成功返回 text（可为空）；status=error 抛 ASRError。"""
    raw = raw.strip()
    if not raw:
        raise ASRError("ASR 返回空响应")
    try:
        obj: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ASRError(f"ASR 返回非 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ASRError("ASR 返回 JSON 非对象")

    status = obj.get("status")
    if status == "error":
        msg = obj.get("error")
        raise ASRError(str(msg) if msg else "ASR status=error")
    if status == "success":
        t = obj.get("text")
        return str(t) if t is not None else ""

    if "text" in obj:
        t = obj.get("text")
        return str(t) if t is not None else ""

    raise ASRError("ASR 响应中无可用 text 字段")


class WebSocketASRClient:
    """连接局域网内 ASR 服务的 WebSocket：单次 Binary 发送完整 WAV，接收 JSON（见 docs/asr-service-api.md）。"""

    def __init__(self, cfg: AsrConfig) -> None:
        self._cfg = cfg

    def transcribe(self, pcm: bytes, sample_rate: int, channels: int = 1) -> str:
        if not pcm:
            return ""

        try:
            from websocket import ABNF, create_connection
        except ImportError as e:
            raise ASRError("需要 websocket-client：pip install websocket-client") from e

        wav_data = pcm_s16le_to_wav(
            pcm,
            sample_rate=sample_rate,
            channels=channels,
        )
        url = _build_ws_url(self._cfg)
        logger.debug("ASR WebSocket 连接: %s，WAV 字节=%d", url, len(wav_data))

        sslopt: dict[str, Any] | None = None
        if url.lower().startswith("wss://") and self._cfg.insecure_ssl:
            sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        try:
            ws = create_connection(
                url,
                timeout=self._cfg.timeout_sec,
                sslopt=sslopt,
            )
        except Exception as e:
            raise ASRError(
                f"无法连接 ASR 服务: {e} | {_connection_error_hint(e)}",
            ) from e

        try:
            if hasattr(ws, "settimeout"):
                ws.settimeout(self._cfg.timeout_sec)
            ws.send(wav_data, opcode=ABNF.OPCODE_BINARY)
            raw = ws.recv()
        except Exception as e:
            raise ASRError(f"ASR 请求失败: {e}") from e
        finally:
            try:
                ws.close()
            except Exception:
                pass

        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        return parse_asr_response(text)


class DashScopeASRClient:
    """阿里云百炼 DashScope ASR（文件识别模式，适配当前按住说话流程）。"""

    def __init__(self, cfg: AsrConfig) -> None:
        self._cfg = cfg

    @staticmethod
    def _extract_text_from_sentence(sentence: Any) -> str:
        """兼容 DashScope 不同版本 sentence 结构（dict / list[dict] / str）。"""
        if sentence is None:
            return ""
        if isinstance(sentence, str):
            return sentence
        if isinstance(sentence, dict):
            t = sentence.get("text")
            return str(t) if t is not None else ""
        if isinstance(sentence, list):
            texts: list[str] = []
            for item in sentence:
                if isinstance(item, dict):
                    t = item.get("text")
                    if t:
                        texts.append(str(t))
                elif isinstance(item, str) and item.strip():
                    texts.append(item.strip())
            return "".join(texts).strip()
        return ""

    def transcribe(self, pcm: bytes, sample_rate: int, channels: int = 1) -> str:
        if not pcm:
            return ""
        try:
            import dashscope
            from dashscope.audio.asr import Recognition
        except ImportError as e:
            raise ASRError("需要 dashscope：pip install dashscope") from e

        key = (self._cfg.dashscope_api_key or "").strip() or None
        if not key and self._cfg.dashscope_api_key_env:
            import os

            key = os.environ.get(self._cfg.dashscope_api_key_env)
        if not key:
            raise ASRError(
                "未读取到 DashScope API Key，请在当前激活 provider 下设置 "
                "asr.providers.<name>.dashscope_api_key，"
                f"或设置环境变量: {self._cfg.dashscope_api_key_env}",
            )

        dashscope.api_key = key
        if self._cfg.dashscope_base_websocket_api_url:
            dashscope.base_websocket_api_url = self._cfg.dashscope_base_websocket_api_url

        wav_data = pcm_s16le_to_wav(pcm, sample_rate=sample_rate, channels=channels)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_data)
            wav_path = tmp.name

        try:
            recognition = Recognition(
                model=self._cfg.dashscope_model,
                format="wav",
                sample_rate=sample_rate,
                semantic_punctuation_enabled=self._cfg.dashscope_semantic_punctuation_enabled,
                callback=None,
            )
            result = recognition.call(wav_path)
        except Exception as e:
            raise ASRError(f"DashScope 请求失败: {e}") from e
        finally:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except Exception:
                pass

        # SDK 返回对象形态可能随版本变化，做宽松提取
        try:
            if hasattr(result, "status_code") and getattr(result, "status_code") not in (200, None):
                msg = getattr(result, "message", "dashscope status_code 非 200")
                raise ASRError(str(msg))
            if hasattr(result, "get_sentence"):
                sentence = result.get_sentence()
                text = self._extract_text_from_sentence(sentence)
                if text:
                    return text
                return ""
            if isinstance(result, dict):
                t = result.get("text") or result.get("output", {}).get("text")
                return str(t) if t is not None else ""
            return str(result) if result is not None else ""
        except ASRError:
            raise
        except Exception as e:
            raise ASRError(f"DashScope 返回解析失败: {e}") from e


def build_asr_client(cfg: AsrConfig) -> ASRClient:
    if cfg.mock:
        return MockASRClient()
    if cfg.provider == "dashscope":
        return DashScopeASRClient(cfg)
    return WebSocketASRClient(cfg)
