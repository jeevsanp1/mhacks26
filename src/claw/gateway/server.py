"""FastAPI WebSocket gateway server."""

from __future__ import annotations

import asyncio
import hmac
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.websockets import WebSocketState

from claw.channels.dispatch import start_inbound
from claw.channels.voice import (
    ElevenLabsSpeech,
    ElevenLabsSpeechError,
    STREAM_TTS_SAMPLE_RATE,
    RealtimeTranscriber,
    StreamingSynthesizer,
    WaitFiller,
    pcm16_to_wav,
)
from claw.config import Settings, get_settings
from claw.events import AgentEvent
from claw.gateway import methods, protocol
from claw.heartbeat import HeartbeatService
from claw.routing import parse_inbound_params
from claw.runner import AgentRunner
from claw.scheduler import CronService

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Connection:
    def __init__(self, ws: WebSocket, conn_id: str) -> None:
        self.ws = ws
        self.conn_id = conn_id
        self.connected = False
        self._send_lock = asyncio.Lock()

    async def send(self, frame: dict[str, Any]) -> None:
        if self.ws.client_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            await self.ws.send_text(protocol.dumps(frame))


def _message_text(content: Any) -> str:
    """OpenAI message content is a string or a list of {type: text, text} parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


VAPI_FILLER_FIRST = "One moment, I'm on it. "
VAPI_FILLER_AGAIN = "Still working on it. "
VAPI_FILLER_FIRST_S = 3.0
VAPI_FILLER_REPEAT_S = 12.0


def create_app(
    settings: Settings | None = None,
    runner: AgentRunner | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    runner = runner or AgentRunner.create(settings)
    global_seq = {"n": 0}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.runner = runner
        app.state.connections = set()

        async def on_agent_event(event: AgentEvent) -> None:
            # Mirror streamed assistant tokens to the gateway's own terminal.
            if event.stream == "assistant":
                sys.stderr.write(str(event.data.get("delta") or ""))
                sys.stderr.flush()
            global_seq["n"] += 1
            frame = protocol.event("agent", event.to_payload(), seq=global_seq["n"])
            dead: list[Connection] = []
            for conn in list(app.state.connections):
                if not conn.connected:
                    continue
                try:
                    await conn.send(frame)
                except Exception:
                    dead.append(conn)
            for conn in dead:
                app.state.connections.discard(conn)

        unsub = runner.subscribe(on_agent_event)
        app.state._unsub = unsub

        async def on_heartbeat(payload: dict[str, Any]) -> None:
            global_seq["n"] += 1
            frame = protocol.event("heartbeat", payload, seq=global_seq["n"])
            dead: list[Connection] = []
            for conn in list(app.state.connections):
                if not conn.connected:
                    continue
                try:
                    await conn.send(frame)
                except Exception:
                    dead.append(conn)
            for conn in dead:
                app.state.connections.discard(conn)

        # OpenClaw-style: gateway owns cron + heartbeat
        cron = CronService(runner, poll_s=1.0)
        app.state.cron = cron
        await cron.start()
        heartbeat = HeartbeatService(runner, on_event=on_heartbeat)
        app.state.heartbeat = heartbeat
        await heartbeat.start()
        yield
        await heartbeat.stop()
        await cron.stop()
        unsub()

    app = FastAPI(title="claw gateway", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def chat() -> FileResponse:
        """Simple browser chat over the gateway WebSocket."""
        path = STATIC_DIR / "dashboard.html"
        return FileResponse(path, media_type="text/html; charset=utf-8")

    @app.get("/voice", response_class=HTMLResponse)
    async def voice_page() -> FileResponse:
        """Push-to-talk voice prototype over /ws/voice."""
        path = STATIC_DIR / "voice.html"
        return FileResponse(
            path, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"}
        )

    @app.get("/health")
    async def http_health() -> dict[str, Any]:
        return methods.handle_health(app.state.runner)

    @app.post("/v1/channel/inbound")
    async def http_channel_inbound(body: dict[str, Any]) -> dict[str, Any]:
        """HTTP ingress for channel adapters (Telegram/Slack/webhooks).

        Body: {channel, text|message, peer:{kind,id}|peerId, accountId?, wait?}
        """
        wait = bool(body.get("wait", True))
        if wait:
            return await methods.handle_channel_inbound_wait(
                body, runner=app.state.runner
            )
        return methods.handle_channel_inbound(body, runner=app.state.runner)

    @app.post("/vapi/{secret}/chat/completions")
    async def vapi_chat_completions(secret: str, body: dict[str, Any]) -> Any:
        """Vapi "custom LLM" endpoint: a phone call whose brain is the claw agent.

        Vapi owns telephony, STT and TTS; each caller turn arrives as an
        OpenAI-style chat completion request. Only the latest user message is
        used — claw keeps its own session history/memory — and the reply is
        streamed back as OpenAI chat.completion.chunk SSE. The secret is in the
        path because Vapi appends /chat/completions to the configured URL.
        """
        expected = app.state.settings.vapi_llm_secret
        if not expected or not hmac.compare_digest(secret, expected):
            raise HTTPException(status_code=404)

        text = next(
            (
                _message_text(m.get("content"))
                for m in reversed(body.get("messages") or [])
                if m.get("role") == "user"
            ),
            "",
        )
        if not text.strip():
            raise HTTPException(status_code=400, detail="no user message")

        call = body.get("call") or {}
        caller = (call.get("customer") or {}).get("number") or call.get("id") or "vapi-call"
        runner: AgentRunner = app.state.runner
        _, accepted = start_inbound(
            runner,
            parse_inbound_params(
                {"channel": "voice", "text": text, "peer": {"kind": "direct", "id": str(caller)}}
            ),
        )

        completion_id = f"chatcmpl-{accepted.run_id}"
        model = str(body.get("model") or "claw")
        created = int(time.time())

        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload)}\n\n"

        if not body.get("stream"):
            snap = await runner.wait(accepted.run_id)
            return JSONResponse(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": snap.output or ""},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        async def stream() -> Any:
            deltas: asyncio.Queue[str | None] = asyncio.Queue()

            async def on_event(event: AgentEvent) -> None:
                delta = event.data.get("delta")
                if (
                    event.run_id == accepted.run_id
                    and event.stream == "assistant"
                    and isinstance(delta, str)
                ):
                    deltas.put_nowait(delta)

            unsubscribe = runner.subscribe(on_event)
            streamed = False

            async def finish() -> str | None:
                snap = await runner.wait(accepted.run_id)
                deltas.put_nowait(None)
                return snap.output

            finisher = asyncio.create_task(finish())
            filler_due = VAPI_FILLER_FIRST_S
            filler_spoken = False
            try:
                yield chunk({"role": "assistant", "content": ""})
                while True:
                    # Long tool runs emit no text; Vapi fails the call if the
                    # stream stays silent, so speak a short filler while waiting.
                    try:
                        delta = await asyncio.wait_for(deltas.get(), timeout=filler_due)
                    except asyncio.TimeoutError:
                        yield chunk(
                            {
                                "content": VAPI_FILLER_AGAIN
                                if filler_spoken
                                else VAPI_FILLER_FIRST
                            }
                        )
                        filler_spoken = True
                        filler_due = VAPI_FILLER_REPEAT_S
                        continue
                    if delta is None:
                        break
                    if filler_spoken and not streamed:
                        delta = f" {delta}"
                    streamed = True
                    yield chunk({"content": delta})
                output = await finisher
                if not streamed:
                    reply = output or "Sorry, I didn't catch that."
                    yield chunk({"content": f" {reply}" if filler_spoken else reply})
                yield chunk({}, finish="stop")
                yield "data: [DONE]\n\n"
            finally:
                unsubscribe()
                finisher.cancel()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        conn = Connection(ws, conn_id=str(uuid.uuid4()))
        app.state.connections.add(conn)
        try:
            # First frame must be connect
            raw = await ws.receive_text()
            try:
                frame = protocol.parse_frame(raw)
            except Exception as exc:
                await conn.send(
                    protocol.res_err("?", "BAD_FRAME", f"invalid JSON: {exc}")
                )
                await ws.close(code=1003)
                return

            if frame.get("type") != "req" or frame.get("method") != "connect":
                req_id = str(frame.get("id") or "?")
                await conn.send(
                    protocol.res_err(
                        req_id,
                        "HANDSHAKE_REQUIRED",
                        "first frame must be connect",
                    )
                )
                await ws.close(code=1008)
                return

            req_id = str(frame.get("id") or "?")
            params = frame.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            try:
                hello = methods.handle_connect(
                    params, settings=app.state.settings, conn_id=conn.conn_id
                )
            except PermissionError as exc:
                await conn.send(protocol.res_err(req_id, "UNAUTHORIZED", str(exc)))
                await ws.close(code=1008)
                return

            conn.connected = True
            await conn.send(protocol.res_ok(req_id, hello))

            while True:
                raw = await ws.receive_text()
                try:
                    frame = protocol.parse_frame(raw)
                except Exception as exc:
                    await conn.send(
                        protocol.res_err("?", "BAD_FRAME", f"invalid JSON: {exc}")
                    )
                    continue

                if frame.get("type") != "req":
                    continue

                req_id = str(frame.get("id") or "?")
                method = frame.get("method")
                params = frame.get("params") or {}
                if not isinstance(params, dict):
                    params = {}

                try:
                    if method == "health":
                        payload = methods.handle_health(app.state.runner)
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "agent":
                        payload = methods.handle_agent(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "chat.history":
                        payload = methods.handle_chat_history(params, runner=app.state.runner)
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "agent.wait":
                        payload = await methods.handle_agent_wait(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "channel.inbound":
                        # Accept immediately (like agent); clients use agent.wait
                        payload = methods.handle_channel_inbound(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                        # Push a channel event with the resolved route
                        global_seq["n"] += 1
                        await conn.send(
                            protocol.event(
                                "channel",
                                {
                                    "phase": "accepted",
                                    **payload,
                                },
                                seq=global_seq["n"],
                            )
                        )
                    elif method == "cron.add":
                        payload = methods.handle_cron_add(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "cron.list":
                        payload = methods.handle_cron_list(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "cron.get":
                        payload = methods.handle_cron_get(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "cron.update":
                        payload = methods.handle_cron_update(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "cron.remove":
                        payload = methods.handle_cron_remove(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "cron.run":
                        payload = await methods.handle_cron_run(
                            params, runner=app.state.runner
                        )
                        await conn.send(protocol.res_ok(req_id, payload))
                    elif method == "connect":
                        await conn.send(
                            protocol.res_err(
                                req_id, "ALREADY_CONNECTED", "already connected"
                            )
                        )
                    else:
                        await conn.send(
                            protocol.res_err(
                                req_id,
                                "UNKNOWN_METHOD",
                                f"unknown method: {method}",
                            )
                        )
                except ValueError as exc:
                    await conn.send(
                        protocol.res_err(req_id, "BAD_REQUEST", str(exc))
                    )
                except Exception as exc:  # noqa: BLE001
                    await conn.send(
                        protocol.res_err(
                            req_id,
                            "INTERNAL",
                            f"{type(exc).__name__}: {exc}",
                        )
                    )
        except WebSocketDisconnect:
            pass
        finally:
            conn.connected = False
            app.state.connections.discard(conn)

    @app.websocket("/ws/voice")
    async def voice_websocket(ws: WebSocket) -> None:
        """Push-to-talk voice loop: audio in -> STT -> start_inbound -> TTS -> audio out.

        Reuses the exact same routing/runner pipeline as every other channel —
        see claw/channels/dispatch.py. The only new thing here is speech I/O.
        """
        await ws.accept()
        conn_id = str(uuid.uuid4())
        send_lock = asyncio.Lock()
        turn_lock = asyncio.Lock()  # replies (and their audio) go out in utterance order
        turn_tasks: set[asyncio.Task[None]] = set()

        async def send_json(frame: dict[str, Any]) -> None:
            if ws.client_state != WebSocketState.CONNECTED:
                return
            async with send_lock:
                await ws.send_text(protocol.dumps(frame))

        async def send_audio(data: bytes) -> None:
            if ws.client_state != WebSocketState.CONNECTED:
                return
            async with send_lock:
                await ws.send_bytes(data)

        try:
            speech = ElevenLabsSpeech(app.state.settings)
        except ElevenLabsSpeechError as exc:
            await send_json({"type": "error", "message": str(exc)})
            await ws.close(code=1011)
            return

        runner: AgentRunner = app.state.runner
        speaking: dict[str, StreamingSynthesizer | None] = {"tts": None}

        async def run_turn(utt_id: Any, transcript: str) -> None:
            async with turn_lock:
                audio_started = False

                async def start_audio(fmt: str) -> None:
                    nonlocal audio_started
                    if not audio_started:
                        audio_started = True
                        await send_json({"type": "audio_start", "id": utt_id, "format": fmt})

                async def on_audio(pcm: bytes) -> None:
                    await start_audio(f"pcm_{STREAM_TTS_SAMPLE_RATE}")
                    await send_audio(pcm)

                tts = speech.streaming_tts(on_audio)
                tts.start()
                speaking["tts"] = tts
                filler = WaitFiller()
                stop_filler = asyncio.Event()

                async def filler_watch() -> None:
                    while not stop_filler.is_set() and not tts.cancelled:
                        phrase = filler.due()
                        if phrase:
                            await send_json(
                                {"type": "filler", "id": utt_id, "text": phrase.strip()}
                            )
                            tts.feed(phrase)
                        try:
                            await asyncio.wait_for(stop_filler.wait(), timeout=0.25)
                        except asyncio.TimeoutError:
                            pass

                filler_task = asyncio.create_task(filler_watch())
                try:
                    _, accepted = start_inbound(
                        runner,
                        parse_inbound_params(
                            {
                                "channel": "voice",
                                "text": transcript,
                                "peer": {"kind": "direct", "id": conn_id},
                            }
                        ),
                    )

                    async def on_event(event: AgentEvent) -> None:
                        if event.run_id != accepted.run_id:
                            return
                        data = event.data
                        delta = data.get("delta")
                        if event.stream == "assistant" and isinstance(delta, str):
                            filler.note_speech()
                            await send_json({"type": "reply_delta", "id": utt_id, "delta": delta})
                            tts.feed(delta)
                        elif event.stream == "tool":
                            await send_json(
                                {
                                    "type": "tool",
                                    "id": utt_id,
                                    "phase": data.get("phase"),
                                    "name": data.get("toolName"),
                                    "toolCallId": data.get("toolCallId"),
                                    "args": data.get("args"),
                                    "result": data.get("result"),
                                }
                            )

                    unsubscribe = runner.subscribe(on_event)
                    try:
                        snap = await runner.wait(accepted.run_id)
                    finally:
                        unsubscribe()
                    reply_text = snap.output or "(no reply)"
                    await send_json(
                        {"type": "turn", "id": utt_id, "transcript": transcript, "reply": reply_text}
                    )
                    streamed = await tts.finish()
                    if not streamed and not tts.cancelled:
                        mp3 = await speech.synthesize(reply_text)
                        if not tts.cancelled:
                            await start_audio("mp3")
                            await send_audio(mp3)
                    if audio_started:
                        await send_json({"type": "audio_end", "id": utt_id})
                except ElevenLabsSpeechError as exc:
                    await send_json({"type": "error", "id": utt_id, "message": str(exc)})
                except (WebSocketDisconnect, RuntimeError):
                    pass
                finally:
                    stop_filler.set()
                    filler_task.cancel()
                    try:
                        await filler_task
                    except asyncio.CancelledError:
                        pass
                    await tts.close()
                    if speaking["tts"] is tts:
                        speaking["tts"] = None

        async def finish_utterance(utt_id: Any, transcript: str) -> None:
            transcript = transcript.strip()
            if not transcript:
                await send_json(
                    {"type": "error", "id": utt_id, "message": "couldn't hear anything — try again"}
                )
                return
            await send_json({"type": "transcript_final", "id": utt_id, "text": transcript})
            task = asyncio.create_task(run_turn(utt_id, transcript))
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)

        # Legacy clients send a compressed blob + end_utterance; streaming clients send
        # start_utterance, raw 16 kHz PCM16 chunks, then end_utterance.
        audio_buf = bytearray()
        streaming = False
        utt_id: Any = None
        transcriber: RealtimeTranscriber | None = None
        # Opening the upstream STT socket takes ~1-2 s, so streaming clients keep one
        # connected ahead of the next press; each transcriber reads its id from `box`.
        spare: tuple[RealtimeTranscriber, dict[str, Any]] | None = None

        def new_transcriber() -> tuple[RealtimeTranscriber, dict[str, Any]]:
            box: dict[str, Any] = {"id": None}

            async def on_partial(text: str) -> None:
                await send_json({"type": "transcript_partial", "id": box["id"], "text": text})

            rt = speech.realtime(on_partial)
            rt.start()
            return rt, box

        try:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break

                raw_bytes = message.get("bytes")
                if raw_bytes is not None:
                    audio_buf.extend(raw_bytes)
                    if streaming and transcriber is not None:
                        transcriber.send(raw_bytes)
                    continue

                raw_text = message.get("text")
                if raw_text is None:
                    continue
                try:
                    frame = protocol.parse_frame(raw_text)
                except Exception:
                    continue

                kind = frame.get("type")
                if kind == "prewarm":
                    if spare is None or not spare[0].alive:
                        spare = new_transcriber()
                    continue
                if kind == "start_utterance":
                    if speaking["tts"] is not None:
                        await speaking["tts"].cancel()  # barge-in: stop generating the old reply's audio
                    if transcriber is not None:
                        await transcriber.close()
                    audio_buf.clear()
                    streaming = True
                    utt_id = frame.get("id")
                    if spare is not None and spare[0].alive:
                        transcriber, box = spare
                    else:
                        if spare is not None:
                            await spare[0].close()
                        transcriber, box = new_transcriber()
                    box["id"] = utt_id
                    spare = new_transcriber()
                    continue

                if kind != "end_utterance":
                    continue
                if not audio_buf:
                    await send_json({"type": "error", "id": utt_id, "message": "no audio received"})
                    streaming = False
                    continue

                utterance = bytes(audio_buf)
                audio_buf.clear()
                current_id, was_streaming, rt = utt_id, streaming, transcriber
                streaming, utt_id, transcriber = False, None, None

                try:
                    transcript = ""
                    if rt is not None:
                        try:
                            transcript = await rt.finish()
                        except ElevenLabsSpeechError:
                            transcript = ""  # realtime unreachable: batch STT below
                    if not transcript.strip():
                        if was_streaming:
                            transcript = await speech.transcribe(
                                pcm16_to_wav(utterance), content_type="audio/wav"
                            )
                        else:
                            transcript = await speech.transcribe(utterance)
                    await finish_utterance(current_id, transcript)
                except ElevenLabsSpeechError as exc:
                    await send_json({"type": "error", "id": current_id, "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            if transcriber is not None:
                await transcriber.close()
            if spare is not None:
                await spare[0].close()
            for task in turn_tasks:
                task.cancel()

    return app


def run_gateway(
    settings: Settings | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    import uvicorn

    settings = settings or get_settings()
    host = host or settings.gateway_host
    port = port if port is not None else settings.gateway_port
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_level="info")
