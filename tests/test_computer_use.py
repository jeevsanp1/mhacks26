from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import BinaryContent, ToolReturn

from claw import computer_use as cu


class _FakeImage:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.size = (width, height)

    def resize(self, size, resample=None):  # noqa: ANN001
        return _FakeImage(size[0], size[1])

    def thumbnail(self, size, resample=None):  # noqa: ANN001
        max_w, max_h = size
        scale = min(max_w / self.width, max_h / self.height, 1.0)
        self.width = max(1, int(self.width * scale))
        self.height = max(1, int(self.height * scale))
        self.size = (self.width, self.height)

    def save(self, buf, format="PNG", optimize=True):  # noqa: ANN001
        buf.write(b"\x89PNG\r\n\x1a\nfake")


class _FakeMSS:
    monitors = [
        {"left": 0, "top": 0, "width": 2000, "height": 1000},
        {"left": 0, "top": 0, "width": 2000, "height": 1000},
    ]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def grab(self, mon):  # noqa: ANN001
        w, h = int(mon["width"]), int(mon["height"])
        return SimpleNamespace(size=(w, h), bgra=b"\x00" * (w * h * 4))


class _FakePyAutoGUI:
    FAILSAFE = True
    PAUSE = 0.0
    _pos = (10, 20)
    moves: list[tuple]
    clicks: list[tuple]
    drags: list[tuple]
    scrolls: list[tuple]
    written: list[str]
    hotkeys: list[tuple]

    def __init__(self):
        self.moves = []
        self.clicks = []
        self.drags = []
        self.scrolls = []
        self.written = []
        self.hotkeys = []

    def size(self):
        return (1000, 500)

    def position(self):
        return self._pos

    def moveTo(self, x, y, duration=0):  # noqa: ANN001
        self.moves.append((x, y, duration))
        self._pos = (x, y)

    def click(self, x=None, y=None, clicks=1, button="left"):  # noqa: ANN001
        self.clicks.append((x, y, clicks, button))

    def dragTo(self, x, y, duration=0.1, button="left"):  # noqa: ANN001
        self.drags.append((x, y, duration, button))

    def scroll(self, clicks):  # noqa: ANN001
        self.scrolls.append(("v", clicks))

    def hscroll(self, clicks):  # noqa: ANN001
        self.scrolls.append(("h", clicks))

    def write(self, text, interval=0.0):  # noqa: ANN001
        self.written.append(text)

    typewrite = write

    def press(self, key):  # noqa: ANN001
        self.hotkeys.append((key,))

    def hotkey(self, *keys):  # noqa: ANN001
        self.hotkeys.append(keys)


@pytest.fixture
def fake_stack(monkeypatch: pytest.MonkeyPatch) -> _FakePyAutoGUI:
    pag = _FakePyAutoGUI()
    image_mod = SimpleNamespace(
        frombytes=lambda *a, **k: _FakeImage(2000, 1000),
        Resampling=SimpleNamespace(LANCZOS=1),
    )
    mss_mod = SimpleNamespace(mss=lambda: _FakeMSS())

    def _import():
        return mss_mod, pag, image_mod

    monkeypatch.setattr(cu, "_import_stack", _import)
    monkeypatch.setattr(cu, "_last_geometry", None)
    monkeypatch.setattr(cu.time, "sleep", lambda *_a, **_k: None)
    return pag


def test_model_to_screen_scales() -> None:
    assert cu.model_to_screen(
        640,
        360,
        model_width=1280,
        model_height=720,
        screen_width=2560,
        screen_height=1440,
    ) == (1280, 720)


def test_unknown_action() -> None:
    out = cu.computer_action("teleport")
    assert "error" in out
    assert "unknown action" in out


def test_screenshot_returns_binary(fake_stack: _FakePyAutoGUI) -> None:
    out = cu.computer_action("screenshot")
    assert isinstance(out, ToolReturn)
    assert "screenshot" in str(out.return_value).lower()
    assert out.content is not None
    assert len(out.content) == 1
    assert isinstance(out.content[0], BinaryContent)
    assert out.content[0].media_type == "image/png"
    assert out.content[0].data.startswith(b"\x89PNG")


def test_left_click_maps_coordinates(fake_stack: _FakePyAutoGUI) -> None:
    # Establish geometry via screenshot (model will be thumbnailed from 1000x500).
    cu.computer_action("screenshot")
    out = cu.computer_action(
        "left_click",
        coordinate=[500, 250],
        include_screenshot=False,
    )
    assert isinstance(out, str)
    assert fake_stack.clicks
    x, y, clicks, button = fake_stack.clicks[-1]
    assert button == "left"
    assert clicks == 1
    # After normalize to 1000x500 logical, thumbnail keeps 1000x500 (<1280).
    assert (x, y) == (500, 250)


def test_key_normalization_and_hotkey(fake_stack: _FakePyAutoGUI) -> None:
    out = cu.computer_action("key", text="cmd+c", include_screenshot=False)
    assert "pressed" in out
    assert fake_stack.hotkeys[-1] == ("command", "c")


def test_scroll_down(fake_stack: _FakePyAutoGUI) -> None:
    cu.computer_action("screenshot")
    out = cu.computer_action(
        "scroll",
        coordinate=[100, 100],
        scroll_direction="down",
        scroll_amount=2,
        include_screenshot=False,
    )
    assert "scrolled down" in out
    assert ("v", -2) in fake_stack.scrolls


def test_truncate_binary_in_events() -> None:
    from claw.events import _truncate

    blob = BinaryContent(data=b"abc" * 100, media_type="image/png")
    assert _truncate(blob) == "<image/png 300 bytes>"
    assert _truncate([blob]) == ["<image/png 300 bytes>"]
