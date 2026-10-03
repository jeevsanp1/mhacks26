from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from claw.channels.voice import ElevenLabsSpeech, ElevenLabsSpeechError
from claw.config import Settings
from claw.gateway.server import create_app
from claw.runner import AgentRunner

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"


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

                turn = ws.receive_json()
                assert turn["type"] == "turn"
                assert turn["transcript"] == "what time is it"
                assert turn["reply"]

                audio = ws.receive_bytes()
                assert audio == b"FAKE_AUDIO_BYTES"

    # Confirms it went through the real routing/session pipeline, not a bypass
    # (default dm_scope="main" routes a direct voice peer to the main session).
    session = runner.store.load("agent:main:main")
    assert len(session) > 0


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
