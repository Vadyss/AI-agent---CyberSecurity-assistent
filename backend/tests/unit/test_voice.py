# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import logging

import httpx
import pytest

from orchestrator.config import Settings
from orchestrator.events import EventEmitter
from orchestrator.run_store import RunState
from orchestrator.voice import VoiceService
from orchestrator.ws import WebSocketHub


class RecordingEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, run, event_type, phase, data):
        assert run.audio is not None
        self.events.append((event_type, phase, data))


def settings(**changes):
    return replace(Settings(), elevenlabs_api_key="test-tts-secret", elevenlabs_voice_id="voice123", **changes)


@pytest.mark.parametrize("api_key,voice_id", [("", ""), ("", "voice123"), ("secret", ""), (" ", "voice123")])
async def test_voice_disabled_without_both_settings(api_key, voice_id):
    def forbidden(request):
        raise AssertionError("Disabled voice attempted a request")
    service = VoiceService(replace(Settings(), elevenlabs_api_key=api_key, elevenlabs_voice_id=voice_id),
                           RecordingEmitter(), transport=httpx.MockTransport(forbidden))
    assert not service.enabled
    run = RunState("run_aabb", "test")
    await service.speak(run, "Summary.")
    assert run.audio is None
    await service.close()
    assert service.client.is_closed


async def test_official_endpoint_headers_body_audio_and_event():
    observed = []
    async def handler(request):
        observed.append(request)
        return httpx.Response(200, content=b"ID3fake-mp3", headers={"Content-Type": "audio/mpeg"})
    emitter = RecordingEmitter()
    service = VoiceService(settings(elevenlabs_model_id="eleven_multilingual_v2"), emitter,
                           transport=httpx.MockTransport(handler))
    assert service.enabled
    run = RunState("run_aabb", "test")
    await service.speak(run, "English summary.")
    assert run.audio == b"ID3fake-mp3"
    assert emitter.events == [("voice_ready", "approval", {"audio_url": "/runs/run_aabb/audio"})]
    request = observed[0]
    assert str(request.url) == "https://api.elevenlabs.io/v1/text-to-speech/voice123?output_format=mp3_44100_128"
    assert request.headers["xi-api-key"] == "test-tts-secret"
    assert request.headers["accept"] == "audio/mpeg"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"text": "English summary.", "model_id": "eleven_multilingual_v2"}
    assert request.extensions["timeout"]["read"] == 30
    await service.close()


async def test_voice_text_is_clipped_to_contract_limit():
    observed = []
    async def handler(request):
        observed.append(json.loads(request.content)["text"])
        return httpx.Response(200, content=b"audio")
    service = VoiceService(settings(), RecordingEmitter(), transport=httpx.MockTransport(handler))
    await service.speak(RunState("run_aabb", "test"), "x\x00" * 1500)
    assert len(observed[0]) == 1000 and "\x00" not in observed[0]
    await service.close()


@pytest.mark.parametrize("kind", ["http", "timeout", "connection", "empty"])
async def test_failure_does_not_emit_or_change_run_and_never_logs_secret(kind, caplog):
    async def handler(request):
        if kind == "http":
            return httpx.Response(401, text="test-tts-secret in untrusted body")
        if kind == "timeout":
            raise httpx.ReadTimeout("test-tts-secret in error")
        if kind == "connection":
            raise httpx.ConnectError("test-tts-secret in error")
        return httpx.Response(200, content=b"")
    emitter = RecordingEmitter()
    service = VoiceService(settings(), emitter, transport=httpx.MockTransport(handler))
    run = RunState("run_aabb", "test", status="awaiting_approval")
    with caplog.at_level(logging.WARNING):
        await service.speak(run, "Summary.")
    assert run.status == "awaiting_approval" and run.audio is None and emitter.events == []
    assert "test-tts-secret" not in caplog.text
    assert "The audio summary is unavailable" in caplog.text
    await service.close()


async def test_audio_stream_stops_before_over_limit_response_is_consumed():
    chunks_read = []
    class AudioStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for size in [4_000_000, 1_000_001, 100]:
                chunks_read.append(size)
                yield b"x" * size
    async def handler(request):
        return httpx.Response(200, stream=AudioStream())
    emitter = RecordingEmitter()
    service = VoiceService(settings(), emitter, transport=httpx.MockTransport(handler))
    run = RunState("run_aabb", "test")
    await service.speak(run, "Summary.")
    assert chunks_read == [4_000_000, 1_000_001]
    assert run.audio is None and emitter.events == []
    await service.close()


async def test_late_voice_after_terminal_uses_real_event_emitter():
    async def handler(request):
        return httpx.Response(200, content=b"audio")
    emitter = EventEmitter(WebSocketHub())
    run = RunState("run_aabb", "test")
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    await emitter.emit(run, "summary", "approval", {"text": "Summary.", "stats": run.stats.snapshot()})
    await emitter.emit(run, "run_failed", "done", {"reason_code": "INTERNAL_ERROR", "reason": "Error."})
    service = VoiceService(settings(), emitter, transport=httpx.MockTransport(handler))
    await service.speak(run, "Summary.")
    assert run.events[-1]["type"] == "voice_ready" and run.events[-1]["phase"] == "approval"
    assert run.status == "failed" and run.phase == "done"
    assert run.audio == b"audio"
    await service.close()


async def test_cancellation_propagates_for_clean_shutdown():
    started = asyncio.Event()
    async def handler(request):
        started.set()
        await asyncio.Event().wait()
    emitter = RecordingEmitter()
    service = VoiceService(settings(), emitter, transport=httpx.MockTransport(handler))
    run = RunState("run_aabb", "test")
    task = asyncio.create_task(service.speak(run, "Summary."))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert run.audio is None and emitter.events == []
    await service.close()
