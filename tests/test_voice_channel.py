from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from claw.channels.voice import (
    ElevenLabsSpeech,
    ElevenLabsSpeechError,
    StreamingSynthesizer,
    WaitFiller,
)
from claw.config import Settings
from claw.gateway.server import create_app
from claw.runner import AgentRunner

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/0BltqDEeOFspLMBF75TG"


@pytest.fixture(autouse=True)
def no_elevenlabs_websockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Realtime STT / streaming TTS sockets are unreachable, exercising the HTTP fallbacks."""

    async def fail_connect(*args: object, **kwargs: object) -> None:
        raise OSError("websocket unavailable in tests")

    monkeypatch.setattr("claw.channels.voice.connect", fail_connect)


def receive_reply_audio(ws: object, utt_id: object) -> bytes:
    start = ws.receive_json()  # type: ignore[attr-defined]
    assert start == {"type": "audio_start", "id": utt_id, "format": "mp3"}
    audio = ws.receive_bytes()  # type: ignore[attr-defined]
    assert ws.receive_json() == {"type": "audio_end", "id": utt_id}  # type: ignore[attr-defined]
    return audio


def test_wait_filler_fires_after_silence_and_resets_on_speech() -> None:
    now = {"t": 0.0}
    filler = WaitFiller(
        first_after_s=2.0,
        every_s=5.0,
        max_per_turn=3,
        phrases=("Give me a second. ", "One moment. "),
        clock=lambda: now["t"],
    )
    assert filler.due() is None
    now["t"] = 2.0
    assert filler.due() == "Give me a second. "
    assert filler.due() is None  # just spoke; clock hasn't advanced
    now["t"] = 7.0
    assert filler.due() == "One moment. "
    filler.note_speech()
    now["t"] = 9.0
    assert filler.due() is None  # recent speech resets the quiet timer
    now["t"] = 14.0
    assert filler.due() == "Give me a second. "
    now["t"] = 20.0
    assert filler.due() is None  # max_per_turn reached


def test_streaming_synthesizer_sends_whole_words_without_markdown() -> None:
    async def on_audio(_: bytes) -> None:
        pass

    tts = StreamingSynthesizer("key", "voice", "model", on_audio)
    for delta in ["**Hel", "lo** wor", "ld, how", " are you"]:
        tts.feed(delta)
    sent = []
    while not tts._outbox.empty():
        sent.append(tts._outbox.get_nowait())
    assert sent == ["Hello ", "world, ", "how are "]
    assert tts._pending == "you"


def test_elevenlabs_speech_requires_api_key(settings: Settings) -> None:
    with pytest.raises(ElevenLabsSpeechError):
        ElevenLabsSpeech(settings)


def test_voice_websocket_routes_through_real_agent_pipeline(settings: Settings) -> None:
    voice_settings = dataclasses.replace(settings, elevenlabs_api_key="fake-key-for-test")
    runner = AgentRunner.create(voice_settings)
    app = create_app(voice_settings, runner)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(STT_URL).mock(
            return_value=httpx.Response(200, json={"text": "what time is it"})
        )
        mock.post(TTS_URL).mock(
            return_value=httpx.Response(
                200, content=b"FAKE_AUDIO_BYTES", headers={"content-type": "audio/mpeg"}
            )
        )

        with TestClient(app) as client:
            with client.websocket_connect("/ws/voice") as ws:
                ws.send_bytes(b"fake-webm-bytes")
                ws.send_text('{"type":"end_utterance"}')

                final = ws.receive_json()
                assert final == {"type": "transcript_final", "id": None, "text": "what time is it"}

                turn = ws.receive_json()
                while turn["type"] in ("reply_delta", "tool", "filler"):
                    turn = ws.receive_json()
                assert turn["type"] == "turn"
                assert turn["transcript"] == "what time is it"
                assert turn["reply"]

                assert receive_reply_audio(ws, None) == b"FAKE_AUDIO_BYTES"

    # Confirms it went through the real routing/session pipeline, not a bypass
    # (default dm_scope="main" routes a direct voice peer to the main session).
    session = runner.store.load("agent:main:main")
    assert len(session) > 0


def test_voice_websocket_streaming_falls_back_to_batch_stt(settings: Settings) -> None:
    voice_settings = dataclasses.replace(settings, elevenlabs_api_key="fake-key-for-test")
    app = create_app(voice_settings)

    with respx.mock(assert_all_called=True) as mock:
        stt = mock.post(STT_URL).mock(
            return_value=httpx.Response(200, json={"text": "hello there"})
        )
        mock.post(TTS_URL).mock(return_value=httpx.Response(200, content=b"AUDIO"))

        with TestClient(app) as client:
            with client.websocket_connect("/ws/voice") as ws:
                ws.send_text('{"type":"start_utterance","id":7}')
                ws.send_bytes(b"\x00\x00" * 1600)
                ws.send_text('{"type":"end_utterance"}')

                final = ws.receive_json()
                assert final == {"type": "transcript_final", "id": 7, "text": "hello there"}
                turn = ws.receive_json()
                while turn["type"] in ("reply_delta", "tool", "filler"):
                    assert turn["id"] == 7
                    turn = ws.receive_json()
                assert turn["type"] == "turn" and turn["id"] == 7
                assert receive_reply_audio(ws, 7) == b"AUDIO"

        assert b"RIFF" in stt.calls.last.request.content  # PCM was wrapped as WAV


def test_voice_websocket_handles_empty_transcript(settings: Settings) -> None:
    voice_settings = dataclasses.replace(settings, elevenlabs_api_key="fake-key-for-test")
    app = create_app(voice_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(STT_URL).mock(return_value=httpx.Response(200, json={"text": "   "}))

        with TestClient(app) as client:
            with client.websocket_connect("/ws/voice") as ws:
                ws.send_bytes(b"fake-webm-bytes")
                ws.send_text('{"type":"end_utterance"}')

                frame = ws.receive_json()
                assert frame["type"] == "error"


def test_vapi_custom_llm_routes_call_through_claw_agent(settings: Settings) -> None:
    vapi_settings = dataclasses.replace(settings, vapi_llm_secret="s3cret")
    runner = AgentRunner.create(vapi_settings)
    app = create_app(vapi_settings, runner)
    body = {
        "model": "claw",
        "stream": True,
        "messages": [
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": "what time is it"},
        ],
        "call": {"id": "call-1", "customer": {"number": "+13135550100"}},
    }

    with TestClient(app) as client:
        assert client.post("/vapi/wrong/chat/completions", json=body).status_code == 404

        resp = client.post("/vapi/s3cret/chat/completions", json=body)
        assert resp.status_code == 200
        lines = [ln[6:] for ln in resp.text.splitlines() if ln.startswith("data: ")]
        assert lines[-1] == "[DONE]"
        chunks = [json.loads(ln) for ln in lines[:-1]]
        assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        assert any(c["choices"][0]["delta"].get("content") for c in chunks)

        plain = client.post("/vapi/s3cret/chat/completions", json={**body, "stream": False})
        assert plain.json()["choices"][0]["message"]["content"]

    assert len(runner.store.load("agent:main:main")) > 0


def test_vapi_stream_speaks_filler_while_agent_is_silent(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claw.gateway import server as gateway_server

    monkeypatch.setattr(gateway_server, "VAPI_FILLER_FIRST_S", 0.0)
    monkeypatch.setattr(gateway_server, "VAPI_FILLER_REPEAT_S", 60.0)
    vapi_settings = dataclasses.replace(settings, vapi_llm_secret="s3cret")
    app = create_app(vapi_settings, AgentRunner.create(vapi_settings))
    body = {
        "model": "claw",
        "stream": True,
        "messages": [{"role": "user", "content": "what time is it"}],
        "call": {"id": "call-2"},
    }

    with TestClient(app) as client:
        resp = client.post("/vapi/s3cret/chat/completions", json=body)
    lines = [ln[6:] for ln in resp.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    texts = [
        c["choices"][0]["delta"].get("content")
        for c in map(json.loads, lines[:-1])
    ]
    texts = [t for t in texts if t]
    assert texts[0] == gateway_server.VAPI_FILLER_FIRST
    assert len(texts) > 1 and texts[1].startswith(" ")
