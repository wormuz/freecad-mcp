"""Camera framing for the screenshots that tools return.

Covers the addon-side orientation logic and the MCP-side plumbing that carries
framing options from a tool call down to the screenshot call.
"""

from collections.abc import Iterator
import importlib.util
from pathlib import Path
import sys
import types

import pytest


ADDON_DIR = Path(__file__).resolve().parents[1] / "addon" / "FreeCADMCP"
VIEW_MANAGER_PATH = ADDON_DIR / "rpc_server" / "view_manager.py"


class FakeView:
    """Records how it was oriented, so tests can assert on the camera calls."""

    def __init__(self) -> None:
        self.view_directions: list[tuple[float, float, float]] = []
        self.named_calls: list[str] = []

    def setViewDirection(self, direction) -> None:
        self.view_directions.append(tuple(direction))

    def viewIsometric(self) -> None:
        self.named_calls.append("Isometric")

    def viewBottom(self) -> None:
        self.named_calls.append("Bottom")


@pytest.fixture
def view_manager(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Load view_manager with FreeCAD, Qt and gui_dispatch stubbed out."""
    freecad = types.ModuleType("FreeCAD")
    freecad.Console = types.SimpleNamespace(
        PrintWarning=lambda _message: None,
        PrintMessage=lambda _message: None,
        PrintError=lambda _message: None,
    )
    freecad_gui = types.ModuleType("FreeCADGui")
    gui_dispatch = types.SimpleNamespace(_flush_gui_events=lambda *_a, **_kw: None)

    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "FreeCAD", freecad)
        patch.setitem(sys.modules, "FreeCADGui", freecad_gui)
        patch.setitem(sys.modules, "rpc_server.gui_dispatch", gui_dispatch)
        spec = importlib.util.spec_from_file_location(
            "_view_manager_test", VIEW_MANAGER_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


def test_camera_direction_overrides_the_named_view(
    view_manager: types.ModuleType,
) -> None:
    """An explicit vector wins, so a caller is not limited to the nine presets."""
    view = FakeView()
    view_manager.apply_view_orientation(view, "Isometric", [1.0, -2.0, 0.5])
    assert view.view_directions == [(1.0, -2.0, 0.5)]
    assert view.named_calls == []


def test_named_view_still_applies_without_a_direction(
    view_manager: types.ModuleType,
) -> None:
    view = FakeView()
    view_manager.apply_view_orientation(view, "Bottom")
    assert view.named_calls == ["Bottom"]
    assert view.view_directions == []


def test_camera_direction_accepts_ints_and_tuples(
    view_manager: types.ModuleType,
) -> None:
    """JSON-RPC hands over plain lists of ints; they must not be rejected."""
    view = FakeView()
    view_manager.apply_camera_direction(view, (0, 0, 1))
    assert view.view_directions == [(0.0, 0.0, 1.0)]


@pytest.mark.parametrize(
    "bad_direction",
    [
        [0, 0, 0],  # zero vector points nowhere
        [1, 2],  # too few components
        [1, 2, 3, 4],  # too many components
        ["x", "y", "z"],  # not numbers
        None,
    ],
)
def test_invalid_camera_direction_is_rejected(
    view_manager: types.ModuleType, bad_direction: object
) -> None:
    """A bad vector must fail loudly, not silently leave the camera as-is."""
    view = FakeView()
    with pytest.raises(ValueError):
        view_manager.apply_camera_direction(view, bad_direction)
    assert view.view_directions == []


class RecordingConnection:
    """Captures the positional arguments each screenshot request arrives with."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_active_screenshot(self, *args) -> str:
        self.calls.append(args)
        return "fake-base64-png"

    def execute_code(self, _code: str) -> dict:
        return {"success": True, "message": "done"}


def test_execute_code_forwards_every_framing_option() -> None:
    """Framing must reach the screenshot call, or tools can only ever auto-fit."""
    from freecad_mcp.operations.core import CameraOptions, execute_code_operation

    freecad = RecordingConnection()
    execute_code_operation(
        freecad,
        only_text_feedback=False,
        code="pass",
        include_screenshot=True,
        camera=CameraOptions("Bottom", 640, 480, "Body", [0, 0, 1]),
    )
    assert freecad.calls == [("Bottom", 640, 480, "Body", [0, 0, 1])]


def test_screenshot_defaults_stay_isometric_auto_fit() -> None:
    """Omitting framing keeps the previous behaviour for existing callers."""
    from freecad_mcp.operations.core import execute_code_operation

    freecad = RecordingConnection()
    execute_code_operation(
        freecad,
        only_text_feedback=False,
        code="pass",
        include_screenshot=True,
    )
    assert freecad.calls == [("Isometric", None, None, None, None)]


def test_opting_out_of_the_screenshot_skips_the_capture() -> None:
    """include_screenshot=False must not pay the render or the token cost."""
    from freecad_mcp.operations.core import CameraOptions, execute_code_operation

    freecad = RecordingConnection()
    result = execute_code_operation(
        freecad,
        only_text_feedback=False,
        code="pass",
        include_screenshot=False,
        camera=CameraOptions(width=640),
    )
    assert freecad.calls == []
    assert all(item.type == "text" for item in result)
