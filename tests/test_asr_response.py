from __future__ import annotations

import wave
from io import BytesIO

import pytest

from voice_controller.asr import ASRError, DashScopeASRClient, parse_asr_response, pcm_s16le_to_wav


def test_pcm_s16le_to_wav_header() -> None:
    pcm = b"\x00\x01" * 1600
    wav = pcm_s16le_to_wav(pcm, 16000, 1)
    with wave.open(BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert len(wf.readframes(wf.getnframes())) == len(pcm)


def test_parse_success_with_text() -> None:
    assert parse_asr_response('{"text":"你好","status":"success"}') == "你好"


def test_parse_success_empty_text() -> None:
    assert parse_asr_response('{"text":"","status":"success"}') == ""


def test_parse_error_raises() -> None:
    with pytest.raises(ASRError, match="bad"):
        parse_asr_response('{"status":"error","error":"bad"}')


def test_parse_legacy_text_only() -> None:
    assert parse_asr_response('{"text":"仅文本"}') == "仅文本"


def test_dashscope_sentence_list_extract_text() -> None:
    sentence = [
        {"text": "它的模型"},
        {"text": "怎么"},
        {"text": "返回json呢？"},
    ]
    assert DashScopeASRClient._extract_text_from_sentence(sentence) == "它的模型怎么返回json呢？"
