"""FastAPI WebSocket gateway server."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from starlette.websockets import WebSocketState

from claw.channels.dispatch import dispatch_inbound
from claw.channels.voice import ElevenLabsSpeech, ElevenLabsSpeechError
from claw.config import Settings, get_settings
from claw.events import AgentEvent
from claw.gateway import methods, protocol
from claw.heartbeat import HeartbeatService
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
        return FileResponse(path, media_type="text/html; charset=utf-8")

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
        """Push-to-talk voice loop: audio in -> STT -> dispatch_inbound -> TTS -> audio out.

        Reuses the exact same routing/runner pipeline as every other channel —
        see claw/channels/dispatch.py. The only new thing here is speech I/O.
        """
        await ws.accept()
        conn_id = str(uuid.uuid4())

        try:
            speech = ElevenLabsSpeech(app.state.settings)
        except ElevenLabsSpeechError as exc:
            await ws.send_text(protocol.dumps({"type": "error", "message": str(exc)}))
            await ws.close(code=1011)
            return

        audio_buf = bytearray()
        try:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break

                raw_bytes = message.get("bytes")
                if raw_bytes is not None:
                    audio_buf.extend(raw_bytes)
                    continue

                raw_text = message.get("text")
                if raw_text is None:
                    continue
                try:
                    frame = protocol.parse_frame(raw_text)
                except Exception:
                    continue

                if frame.get("type") != "end_utterance":
                    continue
                if not audio_buf:
                    await ws.send_text(
                        protocol.dumps({"type": "error", "message": "no audio received"})
                    )
                    continue

                utterance = bytes(audio_buf)
                audio_buf.clear()

                try:
                    transcript = await speech.transcribe(utterance)
                    if not transcript.strip():
                        await ws.send_text(
                            protocol.dumps(
                                {"type": "error", "message": "couldn't hear anything — try again"}
                            )
                        )
                        continue

                    result = await dispatch_inbound(
                        app.state.runner,
                        {
                            "channel": "voice",
                            "text": transcript,
                            "peer": {"kind": "direct", "id": conn_id},
                        },
                        wait=True,
                    )
                    reply_text = (result.get("outbound") or {}).get("text") or "(no reply)"

                    await ws.send_text(
                        protocol.dumps(
                            {"type": "turn", "transcript": transcript, "reply": reply_text}
                        )
                    )

                    audio_reply = await speech.synthesize(reply_text)
                    await ws.send_bytes(audio_reply)
                except ElevenLabsSpeechError as exc:
                    await ws.send_text(protocol.dumps({"type": "error", "message": str(exc)}))
        except WebSocketDisconnect:
            pass

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
