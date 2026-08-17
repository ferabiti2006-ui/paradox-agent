"""Execute restricted Stellaris actions through the visually verified UI.

Dry-run is the default. Live input requires both ``--execute`` and an exact
``--arm`` decision ID. V2 never opens the console and never trusts fixed click
coordinates: OCR anchors every live click and verifies the resulting screen.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .actions import ValidatedAction, validate_actions
from .controller import observation_fingerprint, validate_observation
from .save_watcher import write_json_atomic
from .visual_skills import (
    BuildingVisualSkill,
    DistrictVisualSkill,
    ResearchVisualSkill,
    StellarisLocalizer,
    VisualSkillError,
)


ADAPTER_NAME = "stellaris_visual_v3"
class AdapterError(RuntimeError):
    """Raised when an adapter safety check or UI operation fails."""


@dataclass(frozen=True)
class StellarisLayout:
    """Supported client geometry; click targets are discovered visually."""

    client_width: int = 1920
    client_height: int = 1080
    gui_scale: float = 1.0

@dataclass(frozen=True)
class PlannedAction:
    action_index: int
    type: str
    area: str | None
    technology_id: str | None
    alternative_index: int | None
    planet_id: int | None
    district: str | None
    building: str | None
    planet_name_key: str | None
    planet_designation_key: str | None
    slot: int | None = None
    expected_building: str | None = None
    target_building: str | None = None
    building_position: int | None = None
    building_ui_zone: str | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    adapter: str
    decision_id: str
    observation_date: str
    state_fingerprint: str
    expected_client_size: tuple[int, int]
    gui_scale: float
    open_view_key: str
    expected_alternatives: dict[str, tuple[str, ...]]
    expected_districts: dict[int, tuple[str, ...]]
    expected_buildings: dict[int, tuple[str, ...]]
    actions: tuple[PlannedAction, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AdapterError(f"{path} must contain a JSON object")
    return value


def build_execution_plan(
    decision: Mapping[str, Any],
    observation: Mapping[str, Any],
    layout: StellarisLayout = StellarisLayout(),
) -> ExecutionPlan:
    """Revalidate a decision and prepare semantic actions for visual grounding."""

    state = validate_observation(observation)
    decision_id = decision.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id:
        raise AdapterError("decision has no decision_id")
    fingerprint = observation_fingerprint(state)
    if decision.get("state_fingerprint") != fingerprint:
        raise AdapterError("decision is stale: state fingerprint does not match current observation")
    if decision.get("observation_date") != state["save"]["date"]:
        raise AdapterError("decision date does not match current observation")
    execution = decision.get("execution")
    if not isinstance(execution, Mapping) or execution.get("status") != "not_executed":
        raise AdapterError("decision is not in the not_executed state")
    if layout.gui_scale != 1.0:
        raise AdapterError("visual adapter is calibrated only for gui_scale=1.0")

    validated = validate_actions(decision.get("actions"), state)
    planned: list[PlannedAction] = []
    alternatives = state["player"]["research"]["alternatives"]
    for index, action in enumerate(validated):
        if action.type == "WAIT":
            planned.append(
                PlannedAction(
                    action_index=index,
                    type="WAIT",
                    area=None,
                    technology_id=None,
                    alternative_index=None,
                    planet_id=None,
                    district=None,
                    building=None,
                    planet_name_key=None,
                    planet_designation_key=None,
                )
            )
            continue
        if action.type == "CHOOSE_RESEARCH":
            area = action.parameters["area"]
            technology_id = action.parameters["technology_id"]
            option_index = alternatives[area].index(technology_id)
            planned.append(
                PlannedAction(
                    action_index=index,
                    type=action.type,
                    area=area,
                    technology_id=technology_id,
                    alternative_index=option_index,
                    planet_id=None,
                    district=None,
                    building=None,
                    planet_name_key=None,
                    planet_designation_key=None,
                )
            )
            continue
        planet_id = action.parameters["planet_id"]
        district = action.parameters.get("district")
        building = action.parameters.get("building")
        planet = next(
            row for row in state["player"]["planets"] if row.get("id") == planet_id
        )
        name_key = planet.get("name_key")
        if not isinstance(name_key, str) or not name_key:
            raise AdapterError(f"planet {planet_id} has no authoritative name key")
        designation_key = planet.get("designation")
        if not isinstance(designation_key, str) or not designation_key:
            raise AdapterError(f"planet {planet_id} has no authoritative designation key")
        slot = action.parameters.get("slot")
        expected_building = action.parameters.get("expected_building")
        target_building = action.parameters.get("target_building")
        building_position = None
        building_ui_zone = None
        if action.type == "UPGRADE_BUILDING":
            instances = planet.get("buildings")
            instance = next(
                (
                    row
                    for row in instances
                    if isinstance(row, Mapping) and row.get("id") == slot
                ),
                None,
            ) if isinstance(instances, list) else None
            if not isinstance(instance, Mapping):
                raise AdapterError(f"building slot {slot!r} disappeared before planning")
            building_position = instance.get("position")
            building_ui_zone = instance.get("ui_zone")
            if not isinstance(building_position, int) or isinstance(building_position, bool):
                raise AdapterError(f"building slot {slot!r} has no authoritative position")
            if not isinstance(building_ui_zone, str) or not building_ui_zone:
                raise AdapterError(f"building slot {slot!r} has no supported visual zone")
        planned.append(
            PlannedAction(
                action_index=index,
                type=action.type,
                area=None,
                technology_id=None,
                alternative_index=None,
                planet_id=planet_id,
                district=district,
                building=building,
                planet_name_key=name_key,
                planet_designation_key=designation_key,
                slot=slot,
                expected_building=expected_building,
                target_building=target_building,
                building_position=building_position,
                building_ui_zone=building_ui_zone,
            )
        )
    expected_districts: dict[int, tuple[str, ...]] = {}
    expected_buildings: dict[int, tuple[str, ...]] = {}
    for planet in state["player"].get("planets", []):
        if not isinstance(planet, Mapping) or not isinstance(planet.get("id"), int):
            continue
        availability = planet.get("district_availability")
        if isinstance(availability, Mapping):
            expected_districts[planet["id"]] = tuple(
                district
                for district, details in availability.items()
                if isinstance(details, Mapping) and details.get("authoritative") is True
            )
        building_availability = planet.get("building_availability")
        if isinstance(building_availability, Mapping):
            expected_buildings[planet["id"]] = tuple(
                building
                for building, details in building_availability.items()
                if isinstance(details, Mapping) and details.get("authoritative") is True
            )
    return ExecutionPlan(
        adapter=ADAPTER_NAME,
        decision_id=decision_id,
        observation_date=state["save"]["date"],
        state_fingerprint=fingerprint,
        expected_client_size=(layout.client_width, layout.client_height),
        gui_scale=layout.gui_scale,
        open_view_key="F4",
        expected_alternatives={
            area: tuple(technology_ids)
            for area, technology_ids in alternatives.items()
        },
        expected_districts=expected_districts,
        expected_buildings=expected_buildings,
        actions=tuple(planned),
    )


class WindowsStellarisDriver:
    """DPI-aware Win32 driver restricted to F4, capture, and left click."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SW_RESTORE = 9
    VK_F4 = 0x73
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    MAPVK_VK_TO_VSC = 0

    def __init__(self, expected_size: tuple[int, int], delay_seconds: float = 0.45) -> None:
        if sys.platform != "win32":
            raise AdapterError("live Stellaris UI execution is supported only on Windows")
        if delay_seconds < 0.2:
            raise AdapterError("input delay must be at least 0.2 seconds")
        self.expected_size = expected_size
        self.delay_seconds = delay_seconds
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_win32_signatures()
        self._enable_dpi_awareness()
        self.hwnd = self._find_unique_window()
        self._verify_client_size()

    def _configure_win32_signatures(self) -> None:
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.SetCursorPos.restype = wintypes.BOOL
        self.user32.SetProcessDPIAware.argtypes = []
        self.user32.SetProcessDPIAware.restype = wintypes.BOOL
        self.user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self.user32.MapVirtualKeyW.restype = wintypes.UINT
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT

    def _enable_dpi_awareness(self) -> None:
        # A false result commonly means the process was already made aware by
        # its host; either state is safe because no coordinates have been read.
        self.user32.SetProcessDPIAware()

    def _process_name(self, process_id: int) -> str | None:
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return None
        try:
            length = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(length.value)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return None
            return os.path.basename(buffer.value).lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def _find_unique_window(self) -> int:
        candidates: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd: int, _: int) -> bool:
            if not self.user32.IsWindowVisible(hwnd):
                return True
            process_id = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if self._process_name(process_id.value) == "stellaris.exe":
                candidates.append(hwnd)
            return True

        callback = callback_type(visit)
        if not self.user32.EnumWindows(callback, 0):
            raise AdapterError("could not enumerate Windows applications")
        if len(candidates) != 1:
            raise AdapterError(f"expected exactly one visible stellaris.exe window; found {len(candidates)}")
        return candidates[0]

    def _client_size(self) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self.user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise AdapterError("could not read the Stellaris client size")
        return rect.right - rect.left, rect.bottom - rect.top

    def _verify_client_size(self) -> None:
        actual = self._client_size()
        if actual != self.expected_size:
            raise AdapterError(f"Stellaris client is {actual[0]}x{actual[1]}; expected {self.expected_size[0]}x{self.expected_size[1]}")

    def activate(self) -> None:
        self.user32.ShowWindow(self.hwnd, self.SW_RESTORE)
        if not self.user32.SetForegroundWindow(self.hwnd):
            raise AdapterError("Windows refused to foreground Stellaris")
        time.sleep(self.delay_seconds)
        if self.user32.GetForegroundWindow() != self.hwnd:
            raise AdapterError("Stellaris is not the foreground window")

    def _require_foreground(self) -> None:
        if self.user32.GetForegroundWindow() != self.hwnd:
            raise AdapterError("Stellaris lost foreground focus; execution stopped")

    @staticmethod
    def _input_types() -> tuple[
        type[ctypes.Structure],
        type[ctypes.Structure],
        type[ctypes.Structure],
    ]:
        unsigned_pointer = wintypes.WPARAM

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", unsigned_pointer),
            ]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", unsigned_pointer),
            ]

        class HardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class InputValue(ctypes.Union):
            _fields_ = [
                ("mi", MouseInput),
                ("ki", KeyboardInput),
                ("hi", HardwareInput),
            ]

        class Input(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("type", wintypes.DWORD), ("value", InputValue)]

        return Input, KeyboardInput, MouseInput

    def _send_scan_key(self, virtual_key: int) -> None:
        input_type, keyboard_type, _ = self._input_types()
        scan_code = self.user32.MapVirtualKeyW(virtual_key, self.MAPVK_VK_TO_VSC)
        if not scan_code:
            raise AdapterError(f"could not map virtual key {virtual_key:#x} to a scan code")
        inputs = (input_type * 2)(
            input_type(
                type=self.INPUT_KEYBOARD,
                ki=keyboard_type(0, scan_code, self.KEYEVENTF_SCANCODE, 0, 0),
            ),
            input_type(
                type=self.INPUT_KEYBOARD,
                ki=keyboard_type(
                    0,
                    scan_code,
                    self.KEYEVENTF_SCANCODE | self.KEYEVENTF_KEYUP,
                    0,
                    0,
                ),
            ),
        )
        if self.user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(input_type)) != len(inputs):
            raise AdapterError("Windows did not send the complete keyboard input sequence")

    def _send_left_click(self) -> None:
        input_type, _, mouse_type = self._input_types()
        inputs = (input_type * 2)(
            input_type(
                type=self.INPUT_MOUSE,
                mi=mouse_type(0, 0, 0, self.MOUSEEVENTF_LEFTDOWN, 0, 0),
            ),
            input_type(
                type=self.INPUT_MOUSE,
                mi=mouse_type(0, 0, 0, self.MOUSEEVENTF_LEFTUP, 0, 0),
            ),
        )
        if self.user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(input_type)) != len(inputs):
            raise AdapterError("Windows did not send the complete mouse input sequence")

    def open_research_view(self) -> None:
        self._require_foreground()
        self._send_scan_key(self.VK_F4)
        time.sleep(self.delay_seconds)

    def click_client(self, coordinate: tuple[int, int]) -> None:
        self._require_foreground()
        point = wintypes.POINT(coordinate[0], coordinate[1])
        if not self.user32.ClientToScreen(self.hwnd, ctypes.byref(point)):
            raise AdapterError("could not map a Stellaris client coordinate")
        if not self.user32.SetCursorPos(point.x, point.y):
            raise AdapterError("could not position the cursor over Stellaris")
        self._send_left_click()
        time.sleep(self.delay_seconds)

    def capture_client_image(self) -> Any:
        """Capture and return the exact physical-pixel Stellaris client image."""

        self._require_foreground()
        try:
            from PIL import ImageGrab
        except ImportError as error:
            raise AdapterError("capture mode requires Pillow") from error
        origin = wintypes.POINT(0, 0)
        if not self.user32.ClientToScreen(self.hwnd, ctypes.byref(origin)):
            raise AdapterError("could not locate the Stellaris client on screen")
        width, height = self._client_size()
        return ImageGrab.grab(
            bbox=(origin.x, origin.y, origin.x + width, origin.y + height),
            all_screens=True,
        )

    def capture_client(self, output: str | Path) -> None:
        """Capture the current client for calibration without sending game input."""

        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image = self.capture_client_image()
        image.save(destination)


def _new_receipt(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        "schema": 2,
        "adapter": ADAPTER_NAME,
        "decision_id": plan.decision_id,
        "observation_date": plan.observation_date,
        "state_fingerprint": plan.state_fingerprint,
        "status": "pending",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "actions": [
            {
                "action_index": action.action_index,
                "type": action.type,
                "area": action.area,
                "technology_id": action.technology_id,
                "alternative_index": action.alternative_index,
                "planet_id": action.planet_id,
                "district": action.district,
                "building": action.building,
                "planet_name_key": action.planet_name_key,
                "planet_designation_key": action.planet_designation_key,
                "slot": action.slot,
                "expected_building": action.expected_building,
                "target_building": action.target_building,
                "building_position": action.building_position,
                "building_ui_zone": action.building_ui_zone,
                "status": "pending",
            }
            for action in plan.actions
        ],
    }


def execute_plan(
    plan: ExecutionPlan,
    receipt_path: str | Path,
    *,
    delay_seconds: float = 0.45,
    stellaris_directory: str | Path | None = None,
    visual_artifact_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Execute a plan once with OCR preconditions and visual postconditions."""

    receipt_file = Path(receipt_path)
    if receipt_file.exists():
        raise AdapterError(f"receipt already exists; refusing possible duplicate execution: {receipt_file}")
    # Window discovery, resolution validation, and foreground activation do not
    # change the campaign. Complete them before creating a one-attempt receipt,
    # so a missing/minimized game can be corrected and safely retried.
    driver = WindowsStellarisDriver(plan.expected_client_size, delay_seconds)
    artifact_directory = visual_artifact_directory or (
        receipt_file.parent / f"{plan.decision_id}_visual"
    )
    # OCR initialization can take several seconds and may let the invoking app
    # reclaim focus. Initialize every non-input dependency first, then activate
    # Stellaris immediately before the one-attempt receipt and first capture.
    localizer = StellarisLocalizer(stellaris_directory)
    research_skill = ResearchVisualSkill(
        driver,
        artifact_directory,
        localizer=localizer,
    )
    district_skill = DistrictVisualSkill(
        driver,
        artifact_directory,
        localizer=localizer,
    )
    building_skill = (
        BuildingVisualSkill(driver, artifact_directory, localizer=localizer)
        if any(action.type in {"BUILD_BUILDING", "UPGRADE_BUILDING"} for action in plan.actions)
        else None
    )
    driver.activate()
    receipt = _new_receipt(plan)
    receipt["visual_artifact_directory"] = str(Path(artifact_directory).resolve())
    write_json_atomic(receipt_file, receipt)
    try:
        research_actions = [action for action in plan.actions if action.type == "CHOOSE_RESEARCH"]
        if research_actions:
            receipt["technology_screen"] = research_skill.ensure_open(plan.observation_date)
            write_json_atomic(receipt_file, receipt)
        for action in plan.actions:
            row = receipt["actions"][action.action_index]
            if action.type == "WAIT":
                row["status"] = "completed_no_input"
                write_json_atomic(receipt_file, receipt)
                continue
            row["status"] = "attempting"
            write_json_atomic(receipt_file, receipt)

            def journal_click(
                stage: str,
                coordinate: tuple[int, int],
                evidence: dict[str, Any],
            ) -> None:
                row["status"] = f"attempting_{stage}"
                row["next_click"] = {"stage": stage, "coordinate": list(coordinate)}
                row.setdefault("visual_journal", {})[stage] = evidence
                write_json_atomic(receipt_file, receipt)

            if action.type == "CHOOSE_RESEARCH":
                assert action.area is not None and action.technology_id is not None
                evidence = research_skill.choose(
                    action_index=action.action_index,
                    area=action.area,
                    technology_id=action.technology_id,
                    expected_technology_ids=plan.expected_alternatives[action.area],
                    observation_date=plan.observation_date,
                    before_click=journal_click,
                )
            elif action.type == "BUILD_DISTRICT":
                assert (
                    action.planet_id is not None
                    and action.district is not None
                    and action.planet_name_key is not None
                    and action.planet_designation_key is not None
                )
                evidence = district_skill.build(
                    action_index=action.action_index,
                    planet_name_key=action.planet_name_key,
                    planet_designation_key=action.planet_designation_key,
                    district_id=action.district,
                    expected_district_ids=plan.expected_districts[action.planet_id],
                    observation_date=plan.observation_date,
                    before_click=journal_click,
                )
            elif action.type == "BUILD_BUILDING":
                assert building_skill is not None
                assert (
                    action.planet_id is not None
                    and action.building is not None
                    and action.planet_name_key is not None
                    and action.planet_designation_key is not None
                )
                evidence = building_skill.build(
                    action_index=action.action_index,
                    planet_name_key=action.planet_name_key,
                    planet_designation_key=action.planet_designation_key,
                    building_id=action.building,
                    observation_date=plan.observation_date,
                    before_click=journal_click,
                )
            elif action.type == "UPGRADE_BUILDING":
                assert building_skill is not None
                assert (
                    action.planet_id is not None
                    and action.slot is not None
                    and action.expected_building is not None
                    and action.target_building is not None
                    and action.building_position is not None
                    and action.building_ui_zone is not None
                    and action.planet_name_key is not None
                    and action.planet_designation_key is not None
                )
                evidence = building_skill.upgrade(
                    action_index=action.action_index,
                    planet_name_key=action.planet_name_key,
                    planet_designation_key=action.planet_designation_key,
                    position=action.building_position,
                    ui_zone=action.building_ui_zone,
                    expected_building_id=action.expected_building,
                    target_building_id=action.target_building,
                    observation_date=plan.observation_date,
                    before_click=journal_click,
                )
            else:
                raise AdapterError(f"unsupported planned action {action.type!r}")
            row.pop("next_click", None)
            row["visual_evidence"] = evidence
            row["status"] = "visual_verified_pending_save"
            write_json_atomic(receipt_file, receipt)
        receipt["status"] = (
            "visual_verified_pending_save"
            if any(action.type != "WAIT" for action in plan.actions)
            else "completed_no_input"
        )
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(receipt_file, receipt)
        return receipt
    except BaseException as error:
        receipt["status"] = "manual_review_required"
        receipt["error"] = f"{type(error).__name__}: {error}"
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(receipt_file, receipt)
        raise


def _stellaris_date(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise AdapterError("save verification requires a Stellaris date")
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as error:
        raise AdapterError(f"invalid Stellaris date {value!r}") from error
    if len(parts) != 3:
        raise AdapterError(f"invalid Stellaris date {value!r}")
    return parts


def _contains_exact_value(value: Any, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, Mapping):
        return any(_contains_exact_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_value(item, expected) for item in value)
    return False


def verify_receipt_from_save(
    decision: Mapping[str, Any],
    observation: Mapping[str, Any],
    receipt_path: str | Path,
) -> dict[str, Any]:
    """Promote a visual receipt only when a later save proves every action."""

    state = validate_observation(observation)
    decision_id = decision.get("decision_id")
    if not isinstance(decision_id, str) or not decision_id:
        raise AdapterError("decision has no decision_id")
    receipt_file = Path(receipt_path)
    receipt = _load_json(receipt_file)
    if receipt.get("decision_id") != decision_id:
        raise AdapterError("receipt belongs to a different decision")
    if receipt.get("adapter") != ADAPTER_NAME:
        raise AdapterError("receipt was not produced by the visual V2 adapter")
    receipt_status = receipt.get("status")
    if receipt_status not in {"visual_verified_pending_save", "manual_review_required"}:
        raise AdapterError("receipt is not awaiting save verification")

    decision_date = decision.get("observation_date")
    save_date = state["save"]["date"]
    if _stellaris_date(save_date) < _stellaris_date(decision_date):
        raise AdapterError("verification save predates the decision")

    proposed_actions = decision.get("actions")
    receipt_actions = receipt.get("actions")
    if not isinstance(proposed_actions, list) or not isinstance(receipt_actions, list):
        raise AdapterError("decision or receipt has no action list")
    if len(proposed_actions) != len(receipt_actions):
        raise AdapterError("receipt action count does not match the decision")
    recovering_visual_postcondition = receipt_status == "manual_review_required"
    if recovering_visual_postcondition:
        recoverable = all(
            isinstance(proposed, Mapping)
            and isinstance(row, Mapping)
            and (
                (
                    proposed.get("type") == "BUILD_DISTRICT"
                    and row.get("status") == "attempting_build_target"
                    and isinstance(row.get("next_click"), Mapping)
                    and row["next_click"].get("stage") == "build_target"
                )
                or (
                    proposed.get("type") == "BUILD_BUILDING"
                    and row.get("status") == "attempting_choose_building"
                    and isinstance(row.get("next_click"), Mapping)
                    and row["next_click"].get("stage") == "choose_building"
                )
                or (
                    proposed.get("type") == "UPGRADE_BUILDING"
                    and row.get("status") == "attempting_upgrade_building"
                    and isinstance(row.get("next_click"), Mapping)
                    and row["next_click"].get("stage") == "upgrade_building"
                )
            )
            for proposed, row in zip(proposed_actions, receipt_actions)
        )
        if not recoverable or "could not visually verify" not in str(receipt.get("error", "")):
            raise AdapterError("manual-review receipt is not eligible for save reconciliation")

    research = state["player"].get("research", {})
    active = research.get("active", {}) if isinstance(research, Mapping) else {}
    mismatches: list[str] = []
    for index, (proposed, row) in enumerate(zip(proposed_actions, receipt_actions)):
        if not isinstance(proposed, Mapping) or not isinstance(row, dict):
            raise AdapterError(f"action {index} is malformed")
        for field in (
            "type",
            "area",
            "technology_id",
            "planet_id",
            "district",
            "building",
            "slot",
            "expected_building",
            "target_building",
        ):
            if field in proposed and row.get(field) != proposed.get(field):
                raise AdapterError(f"receipt action {index} does not match decision field {field}")
        if proposed.get("type") == "WAIT":
            row["status"] = "save_verified_no_input"
            continue
        if proposed.get("type") == "CHOOSE_RESEARCH":
            area = proposed.get("area")
            technology_id = proposed.get("technology_id")
            observed = active.get(area) if isinstance(active, Mapping) else None
            if observed != technology_id:
                mismatches.append(f"{area}: expected {technology_id!r}, save has {observed!r}")
            else:
                row["status"] = "save_verified"
            continue
        if proposed.get("type") == "BUILD_DISTRICT":
            planet_id = proposed.get("planet_id")
            district = proposed.get("district")
            planets = state["player"].get("planets", [])
            planet = next(
                (
                    candidate
                    for candidate in planets
                    if isinstance(candidate, Mapping) and candidate.get("id") == planet_id
                ),
                None,
            )
            queue = planet.get("construction_queue") if isinstance(planet, Mapping) else None
            details = queue.get("details") if isinstance(queue, Mapping) else None
            if not isinstance(district, str) or not _contains_exact_value(details, district):
                mismatches.append(
                    f"planet {planet_id}: construction queue does not contain {district!r}"
                )
            else:
                row["status"] = "save_verified"
            continue
        if proposed.get("type") == "BUILD_BUILDING":
            planet_id = proposed.get("planet_id")
            building = proposed.get("building")
            planets = state["player"].get("planets", [])
            planet = next(
                (
                    candidate
                    for candidate in planets
                    if isinstance(candidate, Mapping) and candidate.get("id") == planet_id
                ),
                None,
            )
            queue = planet.get("construction_queue") if isinstance(planet, Mapping) else None
            details = queue.get("details") if isinstance(queue, Mapping) else None
            if not isinstance(building, str) or not _contains_exact_value(details, building):
                mismatches.append(
                    f"planet {planet_id}: construction queue does not contain {building!r}"
                )
            else:
                row["status"] = "save_verified"
            continue
        if proposed.get("type") == "UPGRADE_BUILDING":
            planet_id = proposed.get("planet_id")
            slot = proposed.get("slot")
            target = proposed.get("target_building")
            planets = state["player"].get("planets", [])
            planet = next(
                (
                    candidate
                    for candidate in planets
                    if isinstance(candidate, Mapping) and candidate.get("id") == planet_id
                ),
                None,
            )
            buildings = planet.get("buildings") if isinstance(planet, Mapping) else None
            observed = next(
                (
                    candidate.get("type")
                    for candidate in buildings
                    if isinstance(candidate, Mapping) and candidate.get("id") == slot
                ),
                None,
            ) if isinstance(buildings, list) else None
            if not isinstance(target, str) or observed != target:
                mismatches.append(
                    f"planet {planet_id} slot {slot}: expected upgraded {target!r}, save has {observed!r}"
                )
            else:
                row["status"] = "save_verified"
            continue
        raise AdapterError(f"cannot save-verify unsupported action {proposed.get('type')!r}")

    if mismatches:
        raise AdapterError("save did not confirm all actions: " + "; ".join(mismatches))
    if observation_fingerprint(state) == decision.get("state_fingerprint"):
        raise AdapterError("verification save is not newer than the decision observation")

    receipt["status"] = "save_verified"
    receipt["save_verification"] = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "file_name": state["save"].get("file_name"),
        "date": save_date,
        "state_fingerprint": observation_fingerprint(state),
        "reconciled_visual_postcondition": recovering_visual_postcondition,
    }
    write_json_atomic(receipt_file, receipt)
    return receipt


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decision", type=Path, help="Validated controller decision JSON")
    parser.add_argument("state", type=Path, help="Exact current-state JSON used by the decision")
    parser.add_argument("--execute", action="store_true", help="Send the planned UI input; omitted means dry-run")
    parser.add_argument(
        "--verify-save",
        action="store_true",
        help="Verify an existing visual receipt against this post-action state",
    )
    parser.add_argument("--capture", type=Path, help="Capture the foregrounded Stellaris client and send no input")
    parser.add_argument("--arm", help="Exact decision ID required with --execute")
    parser.add_argument("--receipt", type=Path, help="Execution receipt path")
    parser.add_argument("--width", type=int, default=1920, help="Expected physical client width")
    parser.add_argument("--height", type=int, default=1080, help="Expected physical client height")
    parser.add_argument("--gui-scale", type=float, default=1.0, help="Stellaris GUI scale")
    parser.add_argument("--delay", type=float, default=0.45, help="Delay between UI inputs")
    parser.add_argument("--stellaris-dir", type=Path, help="Stellaris installation directory")
    parser.add_argument(
        "--visual-artifacts",
        type=Path,
        help="Directory for before/choice/after screenshots",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        decision = _load_json(args.decision)
        observation = _load_json(args.state)
        if args.verify_save:
            if args.execute or args.capture is not None:
                raise AdapterError("--verify-save cannot be combined with --execute or --capture")
            decision_id = decision.get("decision_id")
            default_receipt = args.decision.parent / "execution_receipts" / f"{decision_id}.json"
            receipt_path = args.receipt or default_receipt
            receipt = verify_receipt_from_save(decision, observation, receipt_path)
            print(f"Save verified every action: {receipt_path.resolve()}", file=sys.stderr)
            print(json.dumps(receipt, indent=2, ensure_ascii=False))
            return 0
        layout = StellarisLayout(args.width, args.height, args.gui_scale)
        plan = build_execution_plan(decision, observation, layout)
        if args.capture is not None:
            if args.execute:
                raise AdapterError("--capture cannot be combined with --execute")
            driver = WindowsStellarisDriver(plan.expected_client_size, args.delay)
            driver.activate()
            driver.capture_client(args.capture)
            print(f"Captured Stellaris without input: {args.capture.resolve()}", file=sys.stderr)
            return 0
        if not args.execute:
            print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
            print("DRY RUN: no input sent to Stellaris", file=sys.stderr)
            return 0
        if args.arm != plan.decision_id:
            raise AdapterError("--execute requires --arm with the exact decision ID")
        receipt_path = args.receipt or args.decision.parent / "execution_receipts" / f"{plan.decision_id}.json"
        receipt = execute_plan(
            plan,
            receipt_path,
            delay_seconds=args.delay,
            stellaris_directory=args.stellaris_dir,
            visual_artifact_directory=args.visual_artifacts,
        )
        print(
            f"Input visually verified; awaiting save verification: {receipt_path.resolve()}",
            file=sys.stderr,
        )
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError, AdapterError, VisualSkillError) as error:
        print(f"Adapter refused execution: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

