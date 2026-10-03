"""Host desktop computer-use tool: screenshots + pointer/keyboard control.

Controls the machine running the gateway/agent via mss (capture) and pyautogui
(pointer/keyboard). Coordinates are in the screenshot's pixel space; the tool
maps them back to the OS logical screen before acting.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent, ToolReturn

from claw.memory import ClawDeps

ComputerResult = str | ToolReturn

ACTIONS = (
    "screenshot",
    "cursor_position",
    "monitors",
    "mouse_move",
    "left_click",
    "right_click",
    "middle_click",
    "double_click",
    "triple_click",
    "left_click_drag",
    "scroll",
    "type",
    "key",
    "wait",
)

MAX_IMAGE_DIM = 1280
DEFAULT_MONITOR = 1  # mss: 0 = virtual all, 1+ = physical monitors

# Last capture geometry so pointer actions can map coords without re-grabbing.
_last_geometry: dict[str, int] | None = None


@dataclass(frozen=True)
class CaptureResult:
    png: bytes
    model_width: int
    model_height: int
    screen_width: int
    screen_height: int
    monitor_index: int


def _remember_geometry(shot: CaptureResult) -> CaptureResult:
    global _last_geometry
    _last_geometry = {
        "model_width": shot.model_width,
        "model_height": shot.model_height,
        "screen_width": shot.screen_width,
        "screen_height": shot.screen_height,
        "monitor_index": shot.monitor_index,
    }
    return shot


def _geometry_or_capture(*, monitor: int, max_dim: int) -> dict[str, int]:
    if (
        _last_geometry is not None
        and _last_geometry.get("monitor_index") == monitor
    ):
        return _last_geometry
    capture_screen(monitor=monitor, max_dim=max_dim)
    assert _last_geometry is not None
    return _last_geometry


def _import_stack() -> tuple[Any, Any, Any]:
    try:
        import mss
        import pyautogui
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - exercised via unit tests
        raise RuntimeError(
            "computer-use deps missing; install with: pip install mss pillow pyautogui"
        ) from exc
    return mss, pyautogui, Image


def _configure_pyautogui(pyautogui: Any) -> None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05


def screen_size() -> tuple[int, int]:
    _, pyautogui, _ = _import_stack()
    _configure_pyautogui(pyautogui)
    w, h = pyautogui.size()
    return int(w), int(h)


def list_monitors() -> list[dict[str, Any]]:
    mss, _, _ = _import_stack()
    with mss.mss() as sct:
        out: list[dict[str, Any]] = []
        for i, mon in enumerate(sct.monitors):
            out.append(
                {
                    "index": i,
                    "left": int(mon["left"]),
                    "top": int(mon["top"]),
                    "width": int(mon["width"]),
                    "height": int(mon["height"]),
                    "note": "all monitors (virtual)" if i == 0 else f"monitor {i}",
                }
            )
        return out


def capture_screen(
    *,
    monitor: int = DEFAULT_MONITOR,
    max_dim: int = MAX_IMAGE_DIM,
) -> CaptureResult:
    """Grab a monitor, normalize to logical screen size, downscale for the model."""
    mss, pyautogui, Image = _import_stack()
    _configure_pyautogui(pyautogui)
    screen_w, screen_h = int(pyautogui.size()[0]), int(pyautogui.size()[1])

    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor < 0 or monitor >= len(monitors):
            raise ValueError(
                f"monitor {monitor} out of range (0..{len(monitors) - 1})"
            )
        raw = sct.grab(monitors[monitor])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    # Normalize Retina/HiDPI physical pixels → logical OS points (pyautogui space).
    if monitor == 1 and (img.width != screen_w or img.height != screen_h):
        img = img.resize((screen_w, screen_h), Image.Resampling.LANCZOS)
    elif monitor != 1:
        # Non-primary: treat capture pixels as the coordinate space.
        screen_w, screen_h = img.width, img.height

    model_w, model_h = img.width, img.height
    if max(model_w, model_h) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        model_w, model_h = img.width, img.height

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return _remember_geometry(
        CaptureResult(
            png=buf.getvalue(),
            model_width=model_w,
            model_height=model_h,
            screen_width=screen_w,
            screen_height=screen_h,
            monitor_index=monitor,
        )
    )


def model_to_screen(
    x: float,
    y: float,
    *,
    model_width: int,
    model_height: int,
    screen_width: int,
    screen_height: int,
) -> tuple[int, int]:
    if model_width <= 0 or model_height <= 0:
        raise ValueError("invalid model dimensions")
    sx = int(round(x * screen_width / model_width))
    sy = int(round(y * screen_height / model_height))
    sx = max(0, min(screen_width - 1, sx))
    sy = max(0, min(screen_height - 1, sy))
    return sx, sy


def _parse_coordinate(coordinate: Sequence[int | float] | None) -> tuple[float, float] | None:
    if coordinate is None:
        return None
    if len(coordinate) != 2:
        raise ValueError("coordinate must be [x, y]")
    return float(coordinate[0]), float(coordinate[1])


def _tool_return_with_shot(
    status: str,
    shot: CaptureResult,
) -> ToolReturn:
    meta = (
        f"{status}\n"
        f"screenshot={shot.model_width}x{shot.model_height} "
        f"(screen={shot.screen_width}x{shot.screen_height}, "
        f"monitor={shot.monitor_index}). "
        "Coordinates in the image match this screenshot space."
    )
    return ToolReturn(
        meta,
        content=[BinaryContent(data=shot.png, media_type="image/png")],
    )


def computer_action(
    action: str,
    *,
    coordinate: Sequence[int | float] | None = None,
    start_coordinate: Sequence[int | float] | None = None,
    text: str | None = None,
    scroll_direction: Literal["up", "down", "left", "right"] | None = None,
    scroll_amount: int = 3,
    duration: float = 0.5,
    monitor: int = DEFAULT_MONITOR,
    include_screenshot: bool = True,
    max_dim: int = MAX_IMAGE_DIM,
) -> ComputerResult:
    """Execute one computer-use action on the host desktop."""
    act = (action or "").strip().lower()
    if act not in ACTIONS:
        return f"error: unknown action {action!r}; choose one of: {', '.join(ACTIONS)}"

    try:
        mss, pyautogui, _Image = _import_stack()
        _ = mss
        _configure_pyautogui(pyautogui)
    except RuntimeError as exc:
        return f"error: {exc}"

    try:
        if act == "monitors":
            return str(list_monitors())

        if act == "cursor_position":
            x, y = pyautogui.position()
            # Report in model space of a fresh capture so the agent can align.
            shot = capture_screen(monitor=monitor, max_dim=max_dim)
            mx = int(round(x * shot.model_width / shot.screen_width))
            my = int(round(y * shot.model_height / shot.screen_height))
            return (
                f"cursor at screen=({int(x)}, {int(y)}) "
                f"screenshot_space=({mx}, {my}) "
                f"screenshot={shot.model_width}x{shot.model_height}"
            )

        if act == "wait":
            time.sleep(max(0.0, min(float(duration), 5.0)))
            if include_screenshot:
                return _tool_return_with_shot(
                    f"waited {duration:.2f}s",
                    capture_screen(monitor=monitor, max_dim=max_dim),
                )
            return f"waited {duration:.2f}s"

        if act == "screenshot":
            return _tool_return_with_shot(
                "screenshot captured",
                capture_screen(monitor=monitor, max_dim=max_dim),
            )

        # Map screenshot-space coords using last capture geometry when available.
        geom = _geometry_or_capture(monitor=monitor, max_dim=max_dim)
        coord = _parse_coordinate(coordinate)
        start = _parse_coordinate(start_coordinate)

        def to_screen_xy(pt: tuple[float, float]) -> tuple[int, int]:
            return model_to_screen(
                pt[0],
                pt[1],
                model_width=geom["model_width"],
                model_height=geom["model_height"],
                screen_width=geom["screen_width"],
                screen_height=geom["screen_height"],
            )

        status: str

        if act == "mouse_move":
            if coord is None:
                return "error: mouse_move requires coordinate=[x, y]"
            x, y = to_screen_xy(coord)
            pyautogui.moveTo(x, y, duration=min(max(duration, 0.0), 2.0))
            status = f"moved pointer to screenshot{list(coord)} → screen({x}, {y})"

        elif act in {
            "left_click",
            "right_click",
            "middle_click",
            "double_click",
            "triple_click",
        }:
            button = {
                "left_click": "left",
                "right_click": "right",
                "middle_click": "middle",
                "double_click": "left",
                "triple_click": "left",
            }[act]
            clicks = {"double_click": 2, "triple_click": 3}.get(act, 1)
            if coord is not None:
                x, y = to_screen_xy(coord)
                pyautogui.click(x=x, y=y, clicks=clicks, button=button)
                status = (
                    f"{act} at screenshot{list(coord)} → screen({x}, {y})"
                )
            else:
                pyautogui.click(clicks=clicks, button=button)
                status = f"{act} at current pointer position"

        elif act == "left_click_drag":
            if coord is None:
                return "error: left_click_drag requires coordinate=[x, y] (end point)"
            end = to_screen_xy(coord)
            if start is not None:
                sx, sy = to_screen_xy(start)
                pyautogui.moveTo(sx, sy)
            pyautogui.dragTo(
                end[0],
                end[1],
                duration=min(max(duration, 0.1), 3.0),
                button="left",
            )
            status = (
                f"dragged to screenshot{list(coord)} → screen{end}"
                + (f" from screenshot{list(start)}" if start else "")
            )

        elif act == "scroll":
            direction = (scroll_direction or "down").lower()
            if direction not in {"up", "down", "left", "right"}:
                return "error: scroll_direction must be up|down|left|right"
            amount = max(1, min(int(scroll_amount), 20))
            if coord is not None:
                x, y = to_screen_xy(coord)
                pyautogui.moveTo(x, y)
            clicks = amount if direction in {"up", "left"} else -amount
            if direction in {"left", "right"}:
                pyautogui.hscroll(clicks)
            else:
                pyautogui.scroll(clicks)
            status = f"scrolled {direction} by {amount}"

        elif act == "type":
            if not text:
                return "error: type requires text"
            # clipboard paste path for unicode / long strings is more reliable
            if len(text) > 40 or any(ord(c) > 127 for c in text) or "\n" in text:
                pyautogui.write = getattr(pyautogui, "write", pyautogui.typewrite)
                try:
                    import pyperclip  # type: ignore

                    old = pyperclip.paste()
                    pyperclip.copy(text)
                    pyautogui.hotkey("command" if _is_mac() else "ctrl", "v")
                    time.sleep(0.05)
                    pyperclip.copy(old)
                except Exception:
                    pyautogui.write(text, interval=0.02)
            else:
                pyautogui.write(text, interval=0.02)
            status = f"typed {len(text)} characters"

        elif act == "key":
            if not text:
                return "error: key requires text (e.g. 'Return', 'ctrl+c', 'cmd+space')"
            keys = _normalize_keys(text)
            if len(keys) == 1:
                pyautogui.press(keys[0])
            else:
                pyautogui.hotkey(*keys)
            status = f"pressed {'+'.join(keys)}"

        else:  # pragma: no cover
            return f"error: unhandled action {act}"

        if include_screenshot:
            # Small settle so UI can update before the follow-up shot.
            time.sleep(0.15)
            return _tool_return_with_shot(
                status,
                capture_screen(monitor=monitor, max_dim=max_dim),
            )
        return status
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}"


def _is_mac() -> bool:
    import sys

    return sys.platform == "darwin"


def _normalize_keys(spec: str) -> list[str]:
    """Map common key names to pyautogui tokens."""
    aliases = {
        "cmd": "command",
        "super": "command" if _is_mac() else "win",
        "meta": "command" if _is_mac() else "win",
        "control": "ctrl",
        "return": "enter",
        "esc": "escape",
        "page_down": "pagedown",
        "page_up": "pageup",
        "arrow_up": "up",
        "arrow_down": "down",
        "arrow_left": "left",
        "arrow_right": "right",
    }
    parts = [p.strip() for p in spec.replace("-", "+").split("+") if p.strip()]
    out: list[str] = []
    for p in parts:
        key = aliases.get(p.lower(), p.lower())
        out.append(key)
    return out


def register_computer_use_tool(agent: Agent[ClawDeps, str]) -> None:
    @agent.tool
    def computer(
        ctx: RunContext[ClawDeps],
        action: str,
        coordinate: list[int] | None = None,
        start_coordinate: list[int] | None = None,
        text: str | None = None,
        scroll_direction: str | None = None,
        scroll_amount: int = 3,
        duration: float = 0.5,
        monitor: int = DEFAULT_MONITOR,
        include_screenshot: bool = True,
    ) -> ComputerResult:
        """Control the host desktop with screenshots and pointer/keyboard actions.

        Use this to see and operate the computer running the gateway. Typical loop:
        1) action='screenshot' to observe
        2) click/move/type/key based on UI in the image
        3) read the returned screenshot and continue

        Actions:
          screenshot | cursor_position | monitors |
          mouse_move | left_click | right_click | middle_click |
          double_click | triple_click | left_click_drag | scroll |
          type | key | wait

        Coordinates are [x, y] in the *screenshot image* pixel space (not CSS
        or window-local). After pointer/keyboard actions a fresh screenshot is
        returned by default (include_screenshot=true).

        Examples:
          computer(action="screenshot")
          computer(action="left_click", coordinate=[640, 360])
          computer(action="type", text="hello")
          computer(action="key", text="cmd+space")
          computer(action="scroll", coordinate=[640, 360], scroll_direction="down")
        """
        _ = ctx
        direction: Literal["up", "down", "left", "right"] | None = None
        if scroll_direction is not None:
            d = scroll_direction.strip().lower()
            if d in {"up", "down", "left", "right"}:
                direction = d  # type: ignore[assignment]
            else:
                return "error: scroll_direction must be up|down|left|right"
        return computer_action(
            action,
            coordinate=coordinate,
            start_coordinate=start_coordinate,
            text=text,
            scroll_direction=direction,
            scroll_amount=scroll_amount,
            duration=duration,
            monitor=monitor,
            include_screenshot=include_screenshot,
        )
