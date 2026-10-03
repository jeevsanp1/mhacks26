"""ElevenLabs speech I/O — turns audio into text and text into audio.

This is intentionally thin: claw's own agent loop, memory, and tools are
unchanged. The voice gateway endpoint (see claw/gateway/server.py) transcribes
an utterance, routes it through the normal channel/dispatch pipeline like any
other channel, then synthesizes the reply.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
import wave
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import ClientConnection, connect

from claw.config import Settings

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
REALTIME_STT_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
REALTIME_STT_MODEL = "scribe_v2_realtime"
PCM_SAMPLE_RATE = 16_000
LANGUAGE_CODE = "en"  # English-only: no auto-detection for STT, no accent drift in TTS
STREAM_TTS_URL_TEMPLATE = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
STREAM_TTS_SAMPLE_RATE = 24_000
_MARKDOWN_NOISE = re.compile(r"[*`#]+")

# Spoken while the agent is quiet (tooling / slow first token). Trailing space
# keeps ElevenLabs stream-input happy (it wants chunks to end on a word boundary).
VOICE_FILLERS: tuple[str, ...] = (
    "Give me a second. ",
    "One moment. ",
    "Hang on. ",
    "Just a sec. ",
    "Still working on that. ",
)
FILLER_FIRST_AFTER_S = 2.0
FILLER_EVERY_S = 7.0
FILLER_MAX_PER_TURN = 3


class WaitFiller:
    """Decide when to speak a short filler during a long silent stretch."""

    def __init__(
        self,
        *,
        first_after_s: float = FILLER_FIRST_AFTER_S,
        every_s: float = FILLER_EVERY_S,
        max_per_turn: int = FILLER_MAX_PER_TURN,
        phrases: Sequence[str] = VOICE_FILLERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._first_after_s = first_after_s
        self._every_s = every_s
        self._max = max_per_turn
        self._phrases = tuple(phrases) or VOICE_FILLERS
        self._clock = clock
        self._last_activity = self._clock()
        self._said = 0
        self._idx = 0

    def note_speech(self) -> None:
        """Call when the model produces audible reply text (not tools)."""
        self._last_activity = self._clock()

    def due(self) -> str | None:
        if self._said >= self._max:
            return None
        quiet = self._clock() - self._last_activity
        need = self._first_after_s if self._said == 0 else self._every_s
        if quiet < need:
            return None
        phrase = self._phrases[self._idx % len(self._phrases)]
        self._idx += 1
        self._said += 1
        self._last_activity = self._clock()
        return phrase


def pcm16_to_wav(pcm: bytes, sample_rate: int = PCM_SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


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

    def realtime(self, on_partial: Callable[[str], Awaitable[None]]) -> RealtimeTranscriber:
        return RealtimeTranscriber(self._api_key, on_partial)

    def streaming_tts(self, on_audio: Callable[[bytes], Awaitable[None]]) -> StreamingSynthesizer:
        return StreamingSynthesizer(self._api_key, self._voice_id, self._tts_model, on_audio)

    async def transcribe(self, audio_bytes: bytes, *, content_type: str = "audio/webm") -> str:
        filename = "utterance.wav" if content_type == "audio/wav" else "utterance.webm"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                STT_URL,
                headers={"xi-api-key": self._api_key},
                data={"model_id": self._stt_model, "language_code": LANGUAGE_CODE},
                files={"file": (filename, audio_bytes, content_type)},
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
                json={"text": text, "model_id": self._tts_model, "language_code": LANGUAGE_CODE},
            )
        if resp.status_code != 200:
            raise ElevenLabsSpeechError(f"TTS failed: {resp.status_code} {resp.text}")
        return resp.content


class RealtimeTranscriber:
    """One utterance over ElevenLabs' realtime STT WebSocket (16 kHz PCM16 in, partials out).

    Uses manual commit: audio streams in while the user holds the button, and
    `finish()` commits on release and returns the final transcript. The upstream
    connection is opened in the background; audio sent before it is up is queued.
    """

    CONNECT_ATTEMPTS = 3
    CONNECT_TIMEOUT_S = 3.0
    KEEPALIVE_EVERY_S = 5.0
    KEEPALIVE_MAX_S = 120.0

    def __init__(self, api_key: str, on_partial: Callable[[str], Awaitable[None]]) -> None:
        self._api_key = api_key
        self._on_partial = on_partial
        self._ws: ClientConnection | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._outbox: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue()
        self._committed: list[str] = []
        self._partial = ""
        self._done = asyncio.Event()
        self.error: str | None = None

    @property
    def alive(self) -> bool:
        return not self._done.is_set()

    @property
    def text(self) -> str:
        return " ".join(p for p in [*self._committed, self._partial] if p).strip()

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._run()))

    def send(self, pcm: bytes) -> None:
        self._outbox.put_nowait((pcm, False))

    async def finish(self, timeout_s: float = 8.0) -> str:
        """Commit and return the final transcript (best partial text on timeout)."""
        # A short tail of silence carries the commit so it never rides an empty chunk.
        self._outbox.put_nowait((b"\x00\x00" * (PCM_SAMPLE_RATE // 10), True))
        try:
            await asyncio.wait_for(self._done.wait(), timeout_s)
        except asyncio.TimeoutError:
            self.error = self.error or "timed out waiting for transcript"
        finally:
            await self.close()
        if self.error and not self.text:
            raise ElevenLabsSpeechError(f"realtime STT failed: {self.error}")
        return self.text

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _connect(self) -> ClientConnection:
        query = urlencode(
            {
                "model_id": REALTIME_STT_MODEL,
                "audio_format": f"pcm_{PCM_SAMPLE_RATE}",
                "commit_strategy": "manual",
                "language_code": LANGUAGE_CODE,
            }
        )
        last_exc: Exception | None = None
        for _ in range(self.CONNECT_ATTEMPTS):
            try:
                return await connect(
                    f"{REALTIME_STT_URL}?{query}",
                    additional_headers={"xi-api-key": self._api_key},
                    open_timeout=self.CONNECT_TIMEOUT_S,
                )
            except Exception as exc:
                last_exc = exc
        raise ElevenLabsSpeechError(f"could not connect: {last_exc!r}")

    async def _run(self) -> None:
        try:
            self._ws = await self._connect()
            self._tasks.append(asyncio.create_task(self._read()))
            idle_since = asyncio.get_running_loop().time()
            got_audio = False
            while not self._done.is_set():
                try:
                    pcm, commit = await asyncio.wait_for(
                        self._outbox.get(), self.KEEPALIVE_EVERY_S
                    )
                    got_audio = True
                except asyncio.TimeoutError:
                    # Upstream drops sessions after ~15 s without audio; a prewarmed
                    # spare trickles silence to stay open (bounded so idle tabs let go).
                    idle = asyncio.get_running_loop().time() - idle_since
                    if got_audio or idle > self.KEEPALIVE_MAX_S:
                        continue
                    pcm, commit = b"\x00\x00" * (PCM_SAMPLE_RATE // 20), False
                await self._ws.send(
                    json.dumps(
                        {
                            "message_type": "input_audio_chunk",
                            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
                            "commit": commit,
                            "sample_rate": PCM_SAMPLE_RATE,
                        }
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = self.error or str(exc)
            self._done.set()

    async def _read(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                kind = msg.get("message_type")
                if kind == "partial_transcript":
                    self._partial = (msg.get("text") or "").strip()
                    await self._on_partial(self.text)
                elif kind in {"committed_transcript", "committed_transcript_with_timestamps"}:
                    self._committed.append((msg.get("text") or "").strip())
                    self._partial = ""
                    self._done.set()
                elif "error" in msg:
                    self.error = f"{kind}: {msg['error']}"
                    self._done.set()
        except Exception as exc:  # connection dropped mid-utterance
            self.error = self.error or str(exc)
        finally:
            self._done.set()


class StreamingSynthesizer:
    """Text-in/audio-out over ElevenLabs' stream-input TTS WebSocket.

    Feed reply deltas as the model produces them; raw PCM16 (24 kHz) chunks are
    handed to `on_audio` as soon as ElevenLabs generates them. Connect early
    (before the first token) so the handshake overlaps model latency.
    """

    CONNECT_ATTEMPTS = 3
    CONNECT_TIMEOUT_S = 3.0
    # First audio after ~50 chars instead of the default 120 (API minimum is 50).
    CHUNK_LENGTH_SCHEDULE = [50, 90, 140, 200]

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str,
        on_audio: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._on_audio = on_audio
        self._ws: ClientConnection | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._pending = ""
        self._done = asyncio.Event()
        self.audio_sent = False
        self.cancelled = False
        self.error: str | None = None

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._run()))

    def feed(self, text: str) -> None:
        """Queue text, sending only whole words (ElevenLabs expects chunks to end in a space)."""
        self._pending += _MARKDOWN_NOISE.sub("", text)
        cut = max(self._pending.rfind(" "), self._pending.rfind("\n"))
        if cut >= 0:
            chunk, self._pending = self._pending[: cut + 1], self._pending[cut + 1 :]
            if chunk.strip():
                self._outbox.put_nowait(chunk)

    async def finish(self, timeout_s: float = 30.0) -> bool:
        """Flush remaining text, wait for the last audio; returns True if any audio streamed."""
        if self._pending.strip():
            self._outbox.put_nowait(self._pending + " ")
        self._pending = ""
        self._outbox.put_nowait(None)
        try:
            await asyncio.wait_for(self._done.wait(), timeout_s)
        except asyncio.TimeoutError:
            self.error = self.error or "timed out waiting for audio"
        finally:
            await self.close()
        return self.audio_sent

    async def cancel(self) -> None:
        self.cancelled = True
        self._done.set()
        await self.close()

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _connect(self) -> ClientConnection:
        query = urlencode(
            {
                "model_id": self._model_id,
                "output_format": f"pcm_{STREAM_TTS_SAMPLE_RATE}",
                "inactivity_timeout": 60,
                "language_code": LANGUAGE_CODE,
            }
        )
        url = f"{STREAM_TTS_URL_TEMPLATE.format(voice_id=self._voice_id)}?{query}"
        last_exc: Exception | None = None
        for _ in range(self.CONNECT_ATTEMPTS):
            try:
                return await connect(
                    url,
                    additional_headers={"xi-api-key": self._api_key},
                    open_timeout=self.CONNECT_TIMEOUT_S,
                )
            except Exception as exc:
                last_exc = exc
        raise ElevenLabsSpeechError(f"could not connect: {last_exc!r}")

    async def _run(self) -> None:
        try:
            self._ws = await self._connect()
            await self._ws.send(
                json.dumps(
                    {
                        "text": " ",
                        "generation_config": {
                            "chunk_length_schedule": self.CHUNK_LENGTH_SCHEDULE
                        },
                    }
                )
            )
            self._tasks.append(asyncio.create_task(self._read()))
            while not self._done.is_set():
                chunk = await self._outbox.get()
                await self._ws.send(json.dumps({"text": chunk if chunk is not None else ""}))
                if chunk is None:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = self.error or str(exc)
            self._done.set()

    async def _read(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                audio = msg.get("audio")
                if audio and not self.cancelled:
                    self.audio_sent = True
                    await self._on_audio(base64.b64decode(audio))
                if msg.get("isFinal"):
                    break
                if msg.get("error"):
                    self.error = str(msg["error"])
                    break
        except Exception as exc:
            self.error = self.error or str(exc)
        finally:
            self._done.set()
