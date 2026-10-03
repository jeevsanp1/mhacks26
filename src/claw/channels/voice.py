"""ElevenLabs speech I/O — turns audio into text and text into audio.

This is intentionally thin: claw's own agent loop, memory, and tools are
unchanged. The voice gateway endpoint (see claw/gateway/server.py) transcribes
an utterance, routes it through the normal channel/dispatch pipeline like any
other channel, then synthesizes the reply.
"""

from __future__ import annotations

import httpx

from claw.config import Settings

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsSpeechError(RuntimeError):
    pass


class ElevenLabsSpeech:
    """Batch Speech-to-Text / Text-to-Speech over ElevenLabs' HTTP API."""

    def __init__(self, settings: Settings) -> None:
        if not settings.elevenlabs_api_key:
            raise ElevenLabsSpeechError(
                "ELEVENLABS_API_KEY is not set — required for the voice channel"
            )
        self._api_key = settings.elevenlabs_api_key
        self._voice_id = settings.elevenlabs_voice_id
        self._stt_model = settings.elevenlabs_stt_model
        self._tts_model = settings.elevenlabs_tts_model

    async def transcribe(self, audio_bytes: bytes, *, content_type: str = "audio/webm") -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                STT_URL,
                headers={"xi-api-key": self._api_key},
                data={"model_id": self._stt_model},
                files={"file": ("utterance.webm", audio_bytes, content_type)},
            )
        if resp.status_code != 200:
            raise ElevenLabsSpeechError(f"STT failed: {resp.status_code} {resp.text}")
        data = resp.json()
        text = data.get("text")
        if not isinstance(text, str):
            raise ElevenLabsSpeechError(f"STT response missing text: {data}")
        return text

    async def synthesize(self, text: str) -> bytes:
        url = TTS_URL_TEMPLATE.format(voice_id=self._voice_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": self._tts_model},
            )
        if resp.status_code != 200:
            raise ElevenLabsSpeechError(f"TTS failed: {resp.status_code} {resp.text}")
        return resp.content
