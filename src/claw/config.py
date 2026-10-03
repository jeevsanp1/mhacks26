"""Environment-based configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from claw.queue import DEFAULT_QUEUE_MODE, normalize_queue_mode
from claw.routing import (
    DEFAULT_AGENT_ID,
    DEFAULT_DM_SCOPE,
    DEFAULT_GROUP_SCOPE,
    normalize_dm_scope,
    normalize_group_scope,
)

load_dotenv()

PROTOCOL_VERSION = 1
DEFAULT_MODEL = "openai:gpt-4.1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18789
DEFAULT_STATE_DIR = Path(".claw")
DEFAULT_HEARTBEAT_EVERY = "0m"  # off unless CLAW_HEARTBEAT is set (e.g. 30m)
DEFAULT_ELEVENLABS_VOICE_ID = "0BltqDEeOFspLMBF75TG"
DEFAULT_ELEVENLABS_STT_MODEL = "scribe_v2"
DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"


@dataclass(frozen=True)
class Settings:
    model: str
    workspace: Path
    state_dir: Path
    gateway_host: str
    gateway_port: int
    gateway_token: str | None
    queue_mode: str = DEFAULT_QUEUE_MODE
    heartbeat_every: str = DEFAULT_HEARTBEAT_EVERY
    heartbeat_session: str = "main"
    agent_id: str = DEFAULT_AGENT_ID
    dm_scope: str = DEFAULT_DM_SCOPE
    group_scope: str = DEFAULT_GROUP_SCOPE
    identity_links: dict[str, list[str]] | None = None
    spacetime_uri: str | None = None
    spacetime_db: str | None = None
    spacetime_token: str | None = None
    spacetime_worker_id: str = "claw-worker"
    computer_use: bool = True
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = DEFAULT_ELEVENLABS_VOICE_ID
    elevenlabs_stt_model: str = DEFAULT_ELEVENLABS_STT_MODEL
    elevenlabs_tts_model: str = DEFAULT_ELEVENLABS_TTS_MODEL

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def agent_home(self) -> Path:
        """Bootstrap + MEMORY.md live here (separate from the coding workspace)."""
        return self.state_dir / "workspace"

    @property
    def is_test_model(self) -> bool:
        return self.model.strip().lower() in {"test", "test-model"}

    @property
    def spacetime_enabled(self) -> bool:
        return bool(self.spacetime_uri and self.spacetime_db)


def _parse_identity_links(raw: str | None) -> dict[str, list[str]] | None:
    """Parse CLAW_IDENTITY_LINKS JSON: {"me": ["telegram:123", "slack:U1"]}."""
    import json

    text = (raw or "").strip()
    if not text:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("CLAW_IDENTITY_LINKS must be a JSON object")
    out: dict[str, list[str]] = {}
    for key, aliases in data.items():
        if isinstance(aliases, list):
            out[str(key)] = [str(a) for a in aliases]
        elif isinstance(aliases, str):
            out[str(key)] = [aliases]
    return out or None


def get_settings() -> Settings:
    token = os.getenv("GATEWAY_TOKEN", "").strip() or None
    workspace = Path(os.getenv("CLAW_WORKSPACE", ".")).expanduser().resolve()
    state_dir = Path(os.getenv("CLAW_STATE_DIR", str(DEFAULT_STATE_DIR))).expanduser()
    if not state_dir.is_absolute():
        state_dir = (Path.cwd() / state_dir).resolve()
    queue_raw = os.getenv("CLAW_QUEUE_MODE", DEFAULT_QUEUE_MODE)
    try:
        queue_mode = normalize_queue_mode(queue_raw)
    except ValueError:
        queue_mode = DEFAULT_QUEUE_MODE
    try:
        dm_scope = normalize_dm_scope(os.getenv("CLAW_DM_SCOPE", DEFAULT_DM_SCOPE))
    except ValueError:
        dm_scope = DEFAULT_DM_SCOPE
    try:
        group_scope = normalize_group_scope(
            os.getenv("CLAW_GROUP_SCOPE", DEFAULT_GROUP_SCOPE)
        )
    except ValueError:
        group_scope = DEFAULT_GROUP_SCOPE
    try:
        identity_links = _parse_identity_links(os.getenv("CLAW_IDENTITY_LINKS"))
    except Exception:
        identity_links = None
    spacetime_uri = os.getenv("CLAW_SPACETIME_URI", "").strip() or None
    spacetime_db = os.getenv("CLAW_SPACETIME_DB", "").strip() or None
    agent_id = os.getenv("CLAW_AGENT_ID", DEFAULT_AGENT_ID).strip() or DEFAULT_AGENT_ID
    computer_raw = os.getenv("CLAW_COMPUTER_USE", "1").strip().lower()
    computer_use = computer_raw not in {"0", "false", "no", "off"}
    return Settings(
        model=os.getenv("CLAW_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        workspace=workspace,
        state_dir=state_dir,
        gateway_host=os.getenv("GATEWAY_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
        gateway_port=int(os.getenv("GATEWAY_PORT", str(DEFAULT_PORT))),
        gateway_token=token,
        queue_mode=queue_mode,
        heartbeat_every=(
            os.getenv("CLAW_HEARTBEAT", DEFAULT_HEARTBEAT_EVERY).strip()
            or DEFAULT_HEARTBEAT_EVERY
        ),
        heartbeat_session=(
            os.getenv("CLAW_HEARTBEAT_SESSION", "main").strip() or "main"
        ),
        agent_id=agent_id,
        dm_scope=dm_scope,
        group_scope=group_scope,
        identity_links=identity_links,
        spacetime_uri=spacetime_uri,
        spacetime_db=spacetime_db,
        spacetime_token=os.getenv("CLAW_SPACETIME_TOKEN", "").strip() or None,
        spacetime_worker_id=(
            os.getenv("CLAW_SPACETIME_WORKER_ID", "").strip()
            or f"claw-{os.getpid()}"
        ),
        computer_use=computer_use,
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", "").strip() or None,
        elevenlabs_voice_id=(
            os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID).strip()
            or DEFAULT_ELEVENLABS_VOICE_ID
        ),
        elevenlabs_stt_model=(
            os.getenv("ELEVENLABS_STT_MODEL", DEFAULT_ELEVENLABS_STT_MODEL).strip()
            or DEFAULT_ELEVENLABS_STT_MODEL
        ),
        elevenlabs_tts_model=(
            os.getenv("ELEVENLABS_TTS_MODEL", DEFAULT_ELEVENLABS_TTS_MODEL).strip()
            or DEFAULT_ELEVENLABS_TTS_MODEL
        ),
    )
