from __future__ import annotations

import os
from pathlib import Path

import pytest

from claw.agent import reset_agent_cache
from claw.config import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("CLAW_MODEL", "test")
    monkeypatch.setenv("CLAW_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("CLAW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    reset_agent_cache()
    return Settings(
        model="test",
        workspace=(tmp_path / "workspace").resolve(),
        state_dir=(tmp_path / "state").resolve(),
        gateway_host="127.0.0.1",
        gateway_port=0,
        gateway_token=None,
        queue_mode="followup",
        heartbeat_every="0m",
        heartbeat_session="main",
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep tests offline by default
    monkeypatch.setenv("CLAW_MODEL", "test")
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "1")
    os.environ.setdefault("CLAW_MODEL", "test")
    monkeypatch.delenv("CLAW_SPACETIME_URI", raising=False)
    monkeypatch.delenv("CLAW_SPACETIME_DB", raising=False)
    monkeypatch.delenv("CLAW_SPACETIME_TOKEN", raising=False)
