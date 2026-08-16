"""Fail-closed visual skills for the Stellaris user interface.

The controller speaks in stable game identifiers.  This module resolves those
identifiers through Stellaris localisation, reads the live client with OCR,
and returns coordinates only after the expected screen and choices agree with
the save observation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence


AREA_NAMES = {
    "physics": "Physics",
    "society": "Society",
    "engineering": "Engineering",
}


class VisualSkillError(RuntimeError):
    """Raised when visual evidence is insufficient or contradicts the state."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]

    @property
    def center(self) -> tuple[int, int]:
        return (
            round(sum(point[0] for point in self.box) / len(self.box)),
            round(sum(point[1] for point in self.box) / len(self.box)),
        )


class ImageReader(Protocol):
    def read(self, image: Any) -> tuple[OcrLine, ...]: ...


def normalize_label(value: str) -> str:
    """Normalize OCR/localisation text for conservative fuzzy comparison."""

    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def label_similarity(left: str, right: str) -> float:
    left_normalized = normalize_label(left)
    right_normalized = normalize_label(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


class RapidOcrReader:
    """Lazy RapidOCR wrapper so decision-only commands stay dependency-light."""

    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR
        except (ImportError, OSError) as error:
            raise VisualSkillError(
                "visual execution requires the project OCR environment; "
                "install python/requirements-visual.txt and run with .venv\\Scripts\\python.exe"
            ) from error
        try:
            self._engine = RapidOCR()
        except BaseException as error:
            raise VisualSkillError(f"could not initialize RapidOCR: {error}") from error

    def read(self, image: Any) -> tuple[OcrLine, ...]:
        try:
            import numpy as np

            result = self._engine(np.asarray(image.convert("RGB")))
        except BaseException as error:
            raise VisualSkillError(f"OCR failed: {error}") from error
        if result.boxes is None or result.txts is None or result.scores is None:
            return ()
        return tuple(
            OcrLine(
                text=str(text),
                confidence=float(score),
                box=tuple((float(point[0]), float(point[1])) for point in box),
            )
            for box, text, score in zip(result.boxes, result.txts, result.scores)
        )


class StellarisLocalizer:
    """Resolve game IDs from the installed English localisation files."""

    ENTRY = re.compile(r'^\s*([A-Za-z0-9_.-]+):\d*\s+"((?:[^"\\]|\\.)*)"')

    def __init__(self, game_directory: str | Path | None = None) -> None:
        self.game_directory = self._find_game_directory(game_directory)
        self._entries: dict[str, str] | None = None

    @staticmethod
    def _find_game_directory(explicit: str | Path | None) -> Path:
        candidates: list[Path] = []
        if explicit is not None:
            candidates.append(Path(explicit))
        configured = os.environ.get("STELLARIS_GAME_DIR")
        if configured:
            candidates.append(Path(configured))
        candidates.extend(
            [
                Path(r"C:\Program Files (x86)\Steam\steamapps\common\Stellaris"),
                Path(r"C:\Program Files\Steam\steamapps\common\Stellaris"),
            ]
        )
        for candidate in candidates:
            if (candidate / "localisation" / "english").is_dir():
                return candidate
        raise VisualSkillError(
            "could not find Stellaris English localisation; set STELLARIS_GAME_DIR "
            "or pass --stellaris-dir"
        )

    def _load(self) -> dict[str, str]:
        if self._entries is not None:
            return self._entries
        entries: dict[str, str] = {}
        locations = [self.game_directory / "localisation" / "english"]
        project_mods = Path(__file__).resolve().parents[2] / "paradox-mod"
        if project_mods.is_dir():
            locations.extend(project_mods.glob("*/localisation/english"))
        for location in locations:
            for path in location.rglob("*.yml"):
                with path.open(encoding="utf-8-sig", errors="replace") as stream:
                    for line in stream:
                        match = self.ENTRY.match(line)
                        if not match:
                            continue
                        value = match.group(2).replace(r'\"', '"').replace(r"\\n", " ")
                        value = re.sub(r"§.", "", value)
                        entries[match.group(1)] = value.strip()
        self._entries = entries
        return entries

    def resolve(self, identifier: str) -> str:
        value = self._load().get(identifier)
        if not value:
            raise VisualSkillError(f"no English localisation found for {identifier}")
        # Resolve the common "$OTHER_KEY$" indirection conservatively.
        reference = re.fullmatch(r"\$([A-Za-z0-9_.-]+)\$", value)
        if reference:
            value = self._load().get(reference.group(1), value)
        if "$" in value or "[" in value or "]" in value:
            raise VisualSkillError(f"localisation for {identifier} is dynamic: {value!r}")
        return value

    def resolve_many(self, identifiers: Iterable[str]) -> tuple[str, ...]:
        return tuple(self.resolve(identifier) for identifier in identifiers)


class ResearchScreenAnalyzer:
    """Convert OCR lines into verified research-screen click targets."""

    minimum_ocr_confidence = 0.70
    minimum_label_similarity = 0.88

    def _reliable(self, lines: Sequence[OcrLine]) -> tuple[OcrLine, ...]:
        return tuple(line for line in lines if line.confidence >= self.minimum_ocr_confidence)

    def _matches(self, lines: Sequence[OcrLine], label: str) -> tuple[OcrLine, ...]:
        return tuple(
            line
            for line in self._reliable(lines)
            if label_similarity(line.text, label) >= self.minimum_label_similarity
        )

    def require_technology_screen(self, lines: Sequence[OcrLine]) -> None:
        if not self._matches(lines, "Technology"):
            raise VisualSkillError("Technology screen was not recognized")

    def require_game_date(self, lines: Sequence[OcrLine], expected_date: str) -> str:
        recognized: set[str] = set()
        for line in self._reliable(lines):
            match = re.fullmatch(r"\s*(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*", line.text)
            if match:
                recognized.add(
                    f"{int(match.group(1)):04d}.{int(match.group(2)):02d}.{int(match.group(3)):02d}"
                )
        if len(recognized) != 1:
            raise VisualSkillError(
                f"expected one readable on-screen game date; found {sorted(recognized)}"
            )
        actual_date = next(iter(recognized))
        if actual_date != expected_date:
            raise VisualSkillError(
                f"live game date {actual_date} does not match observation date {expected_date}; "
                "create a fresh save before executing"
            )
        return actual_date

    def has_choice_dialog(self, lines: Sequence[OcrLine]) -> bool:
        return any(
            self._matches(lines, f"{display_name} Research")
            for display_name in AREA_NAMES.values()
        )

    def locate_select(self, area: str, lines: Sequence[OcrLine]) -> tuple[int, int]:
        self.require_technology_screen(lines)
        if self.has_choice_dialog(lines):
            raise VisualSkillError("a research-choice dialog is already open")
        display_name = AREA_NAMES[area]
        area_lines = self._matches(lines, display_name)
        select_lines = self._matches(lines, "Select Research")
        if len(area_lines) != 1:
            raise VisualSkillError(f"expected one {display_name} section label; found {len(area_lines)}")
        area_y = area_lines[0].center[1]
        candidates = [
            line
            for line in select_lines
            if 25 <= line.center[1] - area_y <= 140
        ]
        if len(candidates) != 1:
            raise VisualSkillError(
                f"expected one Select Research button below {display_name}; found {len(candidates)}"
            )
        return candidates[0].center

    def locate_choice_target(
        self,
        area: str,
        target_name: str,
        expected_names: Sequence[str],
        lines: Sequence[OcrLine],
    ) -> tuple[tuple[int, int], dict[str, Any]]:
        self.require_technology_screen(lines)
        header = f"{AREA_NAMES[area]} Research"
        if len(self._matches(lines, header)) != 1:
            raise VisualSkillError(f"{header} choice dialog was not recognized")
        if target_name not in expected_names:
            raise VisualSkillError(f"{target_name} is not an expected {area} alternative")

        matched: dict[str, OcrLine] = {}
        for expected in expected_names:
            candidates = self._matches(lines, expected)
            # Prefer a short, title-like OCR line over description text that
            # happens to repeat the technology name.
            candidates = tuple(
                sorted(
                    candidates,
                    key=lambda line: (
                        abs(len(normalize_label(line.text)) - len(normalize_label(expected))),
                        -line.confidence,
                    ),
                )
            )
            if not candidates:
                raise VisualSkillError(
                    f"visible {area} choices do not match the save; missing {expected!r}"
                )
            best = candidates[0]
            if len(candidates) > 1:
                first_delta = abs(len(normalize_label(best.text)) - len(normalize_label(expected)))
                second_delta = abs(
                    len(normalize_label(candidates[1].text)) - len(normalize_label(expected))
                )
                if first_delta == second_delta:
                    raise VisualSkillError(f"ambiguous OCR matches for {expected!r}")
            matched[expected] = best

        target_line = matched[target_name]
        instruction = self._matches(lines, "Select a Technology to research")
        if len(instruction) != 1:
            raise VisualSkillError("research-choice instruction anchor was not recognized")
        # Click the body of the card, not the text itself.  X comes from the
        # centered dialog instruction; Y is below the verified card title.
        coordinate = (instruction[0].center[0], target_line.center[1] + 45)
        evidence = {
            "target_text": target_line.text,
            "target_ocr_confidence": round(target_line.confidence, 5),
            "recognized_expected_choices": {
                name: {
                    "text": line.text,
                    "confidence": round(line.confidence, 5),
                    "center": list(line.center),
                }
                for name, line in matched.items()
            },
        }
        return coordinate, evidence

    def verify_selected(
        self,
        area: str,
        target_name: str,
        lines: Sequence[OcrLine],
    ) -> dict[str, Any]:
        self.require_technology_screen(lines)
        if self._matches(lines, f"{AREA_NAMES[area]} Research"):
            raise VisualSkillError("research-choice dialog remained open after the click")
        matches = self._matches(lines, target_name)
        if len(matches) != 1:
            raise VisualSkillError(
                f"could not verify {target_name!r} as the active {area} research"
            )
        return {
            "active_text": matches[0].text,
            "active_ocr_confidence": round(matches[0].confidence, 5),
            "active_center": list(matches[0].center),
        }


class DistrictScreenAnalyzer:
    """Ground planet selection and district construction in conservative OCR evidence."""

    minimum_ocr_confidence = 0.70
    minimum_label_similarity = 0.88

    def _reliable(self, lines: Sequence[OcrLine]) -> tuple[OcrLine, ...]:
        return tuple(line for line in lines if line.confidence >= self.minimum_ocr_confidence)

    def _matches(self, lines: Sequence[OcrLine], label: str) -> tuple[OcrLine, ...]:
        return tuple(
            line
            for line in self._reliable(lines)
            if label_similarity(line.text, label) >= self.minimum_label_similarity
        )

    def require_game_date(self, lines: Sequence[OcrLine], expected_date: str) -> str:
        return ResearchScreenAnalyzer().require_game_date(lines, expected_date)

    def locate_planet_in_outliner(
        self,
        planet_name: str,
        planet_designation: str,
        lines: Sequence[OcrLine],
    ) -> tuple[int, int]:
        names = tuple(line for line in self._matches(lines, planet_name) if line.center[0] >= 1350)
        designations = tuple(
            line for line in self._matches(lines, planet_designation) if line.center[0] >= 1350
        )
        matches = tuple(
            name
            for name in names
            if any(
                abs(name.center[0] - designation.center[0]) <= 220
                and 10 <= designation.center[1] - name.center[1] <= 45
                for designation in designations
            )
        )
        if len(matches) != 1:
            raise VisualSkillError(
                f"expected one outliner colony row for {planet_name!r} with designation "
                f"{planet_designation!r}; found {len(matches)}"
            )
        return matches[0].center

    def require_planet_screen(self, planet_name: str, lines: Sequence[OcrLine]) -> OcrLine:
        # The system map also renders planet names near the center of the
        # client. Only the left-side planet-window header is valid identity
        # evidence; never accept a map label as proof that the colony UI is open.
        matches = tuple(line for line in self._matches(lines, planet_name) if line.center[0] < 650)
        if len(matches) != 1:
            raise VisualSkillError(
                f"visible planet identity does not match {planet_name!r}; found {len(matches)} headers"
            )
        return matches[0]

    def locate_district_target(
        self,
        planet_name: str,
        target_name: str,
        expected_names: Sequence[str],
        lines: Sequence[OcrLine],
    ) -> tuple[tuple[int, int], dict[str, Any]]:
        self.require_planet_screen(planet_name, lines)
        if target_name not in expected_names:
            raise VisualSkillError(f"{target_name!r} is not authoritative for this planet")
        matched: dict[str, OcrLine] = {}
        for expected in expected_names:
            candidates = tuple(
                line for line in self._matches(lines, expected) if line.center[0] < 1200
            )
            if len(candidates) != 1:
                raise VisualSkillError(
                    f"visible district options do not match the save; expected one {expected!r}, "
                    f"found {len(candidates)}"
                )
            matched[expected] = candidates[0]
        target = matched[target_name]
        build_candidates = tuple(
            line
            for line in self._matches(lines, "Build")
            if target.center[0] < line.center[0] < 1200
            and abs(line.center[1] - target.center[1]) <= 60
        )
        if not build_candidates:
            raise VisualSkillError(
                f"could not identify the Build control for {target_name!r}"
            )
        ordered_builds = sorted(
            build_candidates,
            key=lambda line: (
                abs(line.center[0] - target.center[0]),
                abs(line.center[1] - target.center[1]),
            ),
        )
        if len(ordered_builds) > 1:
            first_distance = abs(ordered_builds[0].center[0] - target.center[0])
            second_distance = abs(ordered_builds[1].center[0] - target.center[0])
            if first_distance == second_distance:
                raise VisualSkillError(
                    f"Build control for {target_name!r} is visually ambiguous"
                )
        build = ordered_builds[0]
        return build.center, {
            "planet_text": self.require_planet_screen(planet_name, lines).text,
            "district_label_text": target.text,
            "district_label_ocr_confidence": round(target.confidence, 5),
            "district_label_center": list(target.center),
            "target_text": build.text,
            "target_ocr_confidence": round(build.confidence, 5),
            "target_center": list(build.center),
            "recognized_expected_districts": {
                name: {
                    "text": line.text,
                    "confidence": round(line.confidence, 5),
                    "center": list(line.center),
                }
                for name, line in matched.items()
            },
            "target_occurrences_before": len(self._matches(lines, target_name)),
        }

    def verify_queued(
        self,
        planet_name: str,
        target_name: str,
        before_occurrences: int,
        lines: Sequence[OcrLine],
    ) -> dict[str, Any]:
        self.require_planet_screen(planet_name, lines)
        matches = self._matches(lines, target_name)
        target_normalized = normalize_label(target_name)
        constructing_matches = tuple(
            line
            for line in self._reliable(lines)
            if "constructing" in normalize_label(line.text).split()
            and target_normalized in normalize_label(line.text)
        )
        if len(matches) <= before_occurrences and not constructing_matches:
            raise VisualSkillError(
                f"could not visually verify {target_name!r} in the planet construction queue"
            )
        return {
            "queued_texts": [line.text for line in matches],
            "constructing_texts": [line.text for line in constructing_matches],
            "occurrences_before": before_occurrences,
            "occurrences_after": len(matches),
            "centers": [list(line.center) for line in matches],
        }


class DistrictVisualSkill:
    """Execute and visually verify one semantic ``BUILD_DISTRICT`` action."""

    def __init__(
        self,
        driver: Any,
        artifact_directory: str | Path,
        *,
        localizer: StellarisLocalizer,
        reader: ImageReader | None = None,
    ) -> None:
        self.driver = driver
        self.artifact_directory = Path(artifact_directory)
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        self.localizer = localizer
        self.reader = reader or RapidOcrReader()
        self.analyzer = DistrictScreenAnalyzer()

    def _observe(self, name: str) -> tuple[tuple[OcrLine, ...], str]:
        image = self.driver.capture_client_image()
        destination = self.artifact_directory / f"{name}.png"
        image.save(destination)
        return self.reader.read(image), str(destination.resolve())

    def build(
        self,
        *,
        action_index: int,
        planet_name_key: str,
        planet_designation_key: str,
        district_id: str,
        expected_district_ids: Sequence[str],
        observation_date: str,
        before_click: Callable[[str, tuple[int, int], dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        prefix = f"{action_index:02d}_district"
        planet_name = self.localizer.resolve(planet_name_key)
        planet_designation = self.localizer.resolve(planet_designation_key)
        district_name = self.localizer.resolve(district_id)
        expected_names = self.localizer.resolve_many(expected_district_ids)

        initial_lines, initial_path = self._observe(f"{prefix}_initial")
        self.analyzer.require_game_date(initial_lines, observation_date)
        try:
            self.analyzer.require_planet_screen(planet_name, initial_lines)
            selected_path = initial_path
            opened_from_outliner = False
        except VisualSkillError:
            coordinate = self.analyzer.locate_planet_in_outliner(
                planet_name, planet_designation, initial_lines
            )
            if before_click is not None:
                before_click(
                    "select_planet",
                    coordinate,
                    {
                        "initial_screenshot": initial_path,
                        "planet_name": planet_name,
                        "planet_designation": planet_designation,
                    },
                )
            self.driver.click_client(coordinate)
            selected_lines, selected_path = self._observe(f"{prefix}_planet_selected")
            self.analyzer.require_game_date(selected_lines, observation_date)
            self.analyzer.require_planet_screen(planet_name, selected_lines)
            opened_from_outliner = True

        before_lines, before_path = self._observe(f"{prefix}_before")
        self.analyzer.require_game_date(before_lines, observation_date)
        target, target_evidence = self.analyzer.locate_district_target(
            planet_name,
            district_name,
            expected_names,
            before_lines,
        )
        if before_click is not None:
            before_click(
                "build_target",
                target,
                {
                    "before_screenshot": before_path,
                    "district_name": district_name,
                    "target_evidence": target_evidence,
                },
            )
        self.driver.click_client(target)

        after_lines, after_path = self._observe(f"{prefix}_after")
        self.analyzer.require_game_date(after_lines, observation_date)
        verification = self.analyzer.verify_queued(
            planet_name,
            district_name,
            target_evidence["target_occurrences_before"],
            after_lines,
        )
        return {
            "planet_name": planet_name,
            "district_name": district_name,
            "expected_district_names": list(expected_names),
            "opened_from_outliner": opened_from_outliner,
            "target_coordinate": list(target),
            "screenshots": {
                "initial": initial_path,
                "planet_selected": selected_path,
                "before": before_path,
                "after": after_path,
            },
            "target_evidence": target_evidence,
            "verification": verification,
        }


class BuildingScreenAnalyzer(DistrictScreenAnalyzer):
    """Ground one allow-listed planetary building in OCR and image evidence."""

    def locate_empty_building_slots(
        self,
        planet_name: str,
        lines: Sequence[OcrLine],
        image: Any,
    ) -> tuple[tuple[tuple[int, int], ...], dict[str, Any]]:
        self.require_planet_screen(planet_name, lines)
        anchors = tuple(
            line for line in self._matches(lines, "Districts and Buildings")
            if line.center[0] < 1200
        )
        if len(anchors) != 1:
            raise VisualSkillError(
                f"expected one Districts and Buildings anchor; found {len(anchors)}"
            )
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise VisualSkillError("building-slot detection requires OpenCV and NumPy") from error

        pixels = np.asarray(image.convert("RGB"))
        red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
        mask = (
            (red <= 110)
            & (green >= 155)
            & (blue >= 120)
            & ((green.astype(int) - red.astype(int)) >= 60)
            & ((blue.astype(int) - red.astype(int)) >= 45)
        ).astype("uint8")
        count, _, stats, centers = cv2.connectedComponentsWithStats(mask, connectivity=8)
        anchor_y = anchors[0].center[1]
        candidates: list[tuple[int, int]] = []
        for index in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[index])
            center_x, center_y = (round(float(value)) for value in centers[index])
            if (
                8 <= width <= 18
                and 8 <= height <= 20
                and 15 <= area <= 120
                and 150 <= center_x < 850
                and anchor_y + 55 <= center_y <= anchor_y + 240
            ):
                candidates.append((center_x, center_y))
        candidates = sorted(set(candidates), key=lambda point: (point[1], point[0]))
        if not candidates:
            raise VisualSkillError("no visually grounded empty building slot was found")
        return tuple(candidates), {
            "anchor_text": anchors[0].text,
            "anchor_center": list(anchors[0].center),
            "slot_candidates": [list(point) for point in candidates],
        }

    def locate_empty_building_slot(
        self,
        planet_name: str,
        lines: Sequence[OcrLine],
        image: Any,
    ) -> tuple[tuple[int, int], dict[str, Any]]:
        """Compatibility helper returning the first grounded candidate."""

        candidates, evidence = self.locate_empty_building_slots(
            planet_name, lines, image
        )
        target = candidates[0]
        return target, {**evidence, "target_center": list(target)}

    def require_building_dialog(
        self, planet_name: str, lines: Sequence[OcrLine]
    ) -> OcrLine:
        self.require_planet_screen(planet_name, lines)
        dialog_anchors = tuple(
            line
            for label in (
                "Construct Building",
                "Select a Building to Construct",
                "Select Building",
            )
            for line in self._matches(lines, label)
        )
        if len(dialog_anchors) != 1:
            raise VisualSkillError(
                f"building-selection dialog was not recognized; found {len(dialog_anchors)} anchors"
            )
        return dialog_anchors[0]

    def locate_building_target(
        self,
        planet_name: str,
        target_name: str,
        lines: Sequence[OcrLine],
    ) -> tuple[tuple[int, int], dict[str, Any]]:
        dialog_anchor = self.require_building_dialog(planet_name, lines)
        candidates = tuple(self._matches(lines, target_name))
        if len(candidates) != 1:
            raise VisualSkillError(
                f"expected one visible building option for {target_name!r}; found {len(candidates)}"
            )
        target = candidates[0]
        return target.center, {
            "dialog_text": dialog_anchor.text,
            "dialog_center": list(dialog_anchor.center),
            "target_text": target.text,
            "target_ocr_confidence": round(target.confidence, 5),
            "target_center": list(target.center),
        }

    def verify_building_queued(
        self,
        planet_name: str,
        target_name: str,
        lines: Sequence[OcrLine],
    ) -> dict[str, Any]:
        self.require_planet_screen(planet_name, lines)
        target_normalized = normalize_label(target_name)
        constructing = tuple(
            line
            for line in self._reliable(lines)
            if "constructing" in normalize_label(line.text).split()
            and target_normalized in normalize_label(line.text)
        )
        if constructing:
            return {
                "verification_kind": "constructing_status",
                "constructing_texts": [line.text for line in constructing],
                "centers": [list(line.center) for line in constructing],
            }

        # Stellaris 4.4.6 renders a compact queue row without the word
        # "Constructing" and may clip the building name (for example,
        # ``Research L``). Ground that fallback in the unique colony
        # designation control and accept only a sufficiently long prefix in
        # the narrow queue-row region beneath it. This avoids confusing the
        # same building's picker entry or hover tooltip with queue evidence.
        designation_anchors = self._matches(lines, "Auto Designated")
        if len(designation_anchors) == 1:
            anchor = designation_anchors[0]
            queue_items = tuple(
                line
                for line in self._reliable(lines)
                if len(normalize_label(line.text)) >= 8
                and target_normalized.startswith(normalize_label(line.text))
                and abs(line.center[0] - anchor.center[0]) <= 100
                and 35 <= line.center[1] - anchor.center[1] <= 220
            )
            if len(queue_items) == 1:
                item = queue_items[0]
                return {
                    "verification_kind": "build_queue_row",
                    "designation_anchor": anchor.text,
                    "queue_texts": [item.text],
                    "centers": [list(item.center)],
                }

        raise VisualSkillError(
            f"could not visually verify constructing {target_name!r}"
        )


class BuildingVisualSkill:
    """Execute and visually verify one semantic ``BUILD_BUILDING`` action."""

    def __init__(
        self,
        driver: Any,
        artifact_directory: str | Path,
        *,
        localizer: StellarisLocalizer,
        reader: ImageReader | None = None,
    ) -> None:
        self.driver = driver
        self.artifact_directory = Path(artifact_directory)
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        self.localizer = localizer
        self.reader = reader or RapidOcrReader()
        self.analyzer = BuildingScreenAnalyzer()

    def _observe(self, name: str) -> tuple[tuple[OcrLine, ...], str, Any]:
        image = self.driver.capture_client_image()
        destination = self.artifact_directory / f"{name}.png"
        image.save(destination)
        return self.reader.read(image), str(destination.resolve()), image

    def build(
        self,
        *,
        action_index: int,
        planet_name_key: str,
        planet_designation_key: str,
        building_id: str,
        observation_date: str,
        before_click: Callable[[str, tuple[int, int], dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        prefix = f"{action_index:02d}_building"
        planet_name = self.localizer.resolve(planet_name_key)
        planet_designation = self.localizer.resolve(planet_designation_key)
        building_name = self.localizer.resolve(building_id)

        initial_lines, initial_path, _ = self._observe(f"{prefix}_initial")
        self.analyzer.require_game_date(initial_lines, observation_date)
        try:
            self.analyzer.require_planet_screen(planet_name, initial_lines)
            selected_path = initial_path
            opened_from_outliner = False
        except VisualSkillError:
            coordinate = self.analyzer.locate_planet_in_outliner(
                planet_name, planet_designation, initial_lines
            )
            if before_click is not None:
                before_click(
                    "select_planet",
                    coordinate,
                    {
                        "initial_screenshot": initial_path,
                        "planet_name": planet_name,
                        "planet_designation": planet_designation,
                    },
                )
            self.driver.click_client(coordinate)
            selected_lines, selected_path, _ = self._observe(f"{prefix}_planet_selected")
            self.analyzer.require_game_date(selected_lines, observation_date)
            self.analyzer.require_planet_screen(planet_name, selected_lines)
            opened_from_outliner = True

        before_lines, before_path, before_image = self._observe(f"{prefix}_before")
        self.analyzer.require_game_date(before_lines, observation_date)
        slots, slot_evidence = self.analyzer.locate_empty_building_slots(
            planet_name, before_lines, before_image
        )
        slot = None
        target = None
        target_evidence = None
        choice_path = None
        probe_paths: list[str] = []
        for probe_index, candidate in enumerate(slots):
            if before_click is not None:
                before_click(
                    f"probe_building_slot_{probe_index}",
                    candidate,
                    {
                        "before_screenshot": before_path,
                        "slot_evidence": slot_evidence,
                        "probe_index": probe_index,
                    },
                )
            self.driver.click_client(candidate)
            choice_lines, candidate_path, _ = self._observe(
                f"{prefix}_choices_{probe_index}"
            )
            probe_paths.append(candidate_path)
            self.analyzer.require_game_date(choice_lines, observation_date)
            self.analyzer.require_building_dialog(planet_name, choice_lines)
            try:
                candidate_target, candidate_evidence = self.analyzer.locate_building_target(
                    planet_name, building_name, choice_lines
                )
            except VisualSkillError as error:
                if "visible building option" not in str(error):
                    raise
                continue
            slot = candidate
            target = candidate_target
            target_evidence = candidate_evidence
            choice_path = candidate_path
            break
        if slot is None or target is None or target_evidence is None or choice_path is None:
            raise VisualSkillError(
                f"expected one visible building option for {building_name!r}; "
                f"not found in any of {len(slots)} grounded empty slots"
            )
        if before_click is not None:
            before_click(
                "choose_building",
                target,
                {
                    "choices_screenshot": choice_path,
                    "building_name": building_name,
                    "target_evidence": target_evidence,
                },
            )
        self.driver.click_client(target)

        after_lines, after_path, _ = self._observe(f"{prefix}_after")
        self.analyzer.require_game_date(after_lines, observation_date)
        verification = self.analyzer.verify_building_queued(
            planet_name, building_name, after_lines
        )
        return {
            "planet_name": planet_name,
            "building_name": building_name,
            "opened_from_outliner": opened_from_outliner,
            "slot_coordinate": list(slot),
            "target_coordinate": list(target),
            "screenshots": {
                "initial": initial_path,
                "planet_selected": selected_path,
                "before": before_path,
                "choices": choice_path,
                "slot_probes": probe_paths,
                "after": after_path,
            },
            "slot_evidence": slot_evidence,
            "target_evidence": target_evidence,
            "verification": verification,
        }


class ResearchVisualSkill:
    """Execute and visually verify semantic ``CHOOSE_RESEARCH`` actions."""

    def __init__(
        self,
        driver: Any,
        artifact_directory: str | Path,
        *,
        localizer: StellarisLocalizer,
        reader: ImageReader | None = None,
    ) -> None:
        self.driver = driver
        self.artifact_directory = Path(artifact_directory)
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        self.localizer = localizer
        self.reader = reader or RapidOcrReader()
        self.analyzer = ResearchScreenAnalyzer()

    def _observe(self, name: str) -> tuple[tuple[OcrLine, ...], str]:
        image = self.driver.capture_client_image()
        destination = self.artifact_directory / f"{name}.png"
        image.save(destination)
        return self.reader.read(image), str(destination.resolve())

    def ensure_open(self, expected_date: str) -> dict[str, Any]:
        lines, before_path = self._observe("00_initial")
        recognized_date = self.analyzer.require_game_date(lines, expected_date)
        if self.analyzer.has_choice_dialog(lines):
            raise VisualSkillError("start with no research-choice dialog open")
        try:
            self.analyzer.require_technology_screen(lines)
            return {
                "initial_screenshot": before_path,
                "opened_with_f4": False,
                "recognized_game_date": recognized_date,
            }
        except VisualSkillError:
            self.driver.open_research_view()
            lines, after_path = self._observe("00_technology_open")
            self.analyzer.require_game_date(lines, expected_date)
            self.analyzer.require_technology_screen(lines)
            if self.analyzer.has_choice_dialog(lines):
                raise VisualSkillError("F4 opened an unexpected research-choice dialog")
            return {
                "initial_screenshot": before_path,
                "technology_screenshot": after_path,
                "opened_with_f4": True,
                "recognized_game_date": recognized_date,
            }

    def choose(
        self,
        *,
        action_index: int,
        area: str,
        technology_id: str,
        expected_technology_ids: Sequence[str],
        observation_date: str,
        before_click: Callable[[str, tuple[int, int], dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        prefix = f"{action_index:02d}_{area}"
        target_name = self.localizer.resolve(technology_id)
        expected_names = self.localizer.resolve_many(expected_technology_ids)

        base_lines, base_path = self._observe(f"{prefix}_before")
        self.analyzer.require_game_date(base_lines, observation_date)
        select_coordinate = self.analyzer.locate_select(area, base_lines)
        if before_click is not None:
            before_click(
                "open_choices",
                select_coordinate,
                {"before_screenshot": base_path},
            )
        self.driver.click_client(select_coordinate)

        choice_lines, choice_path = self._observe(f"{prefix}_choices")
        target_coordinate, target_evidence = self.analyzer.locate_choice_target(
            area,
            target_name,
            expected_names,
            choice_lines,
        )
        if before_click is not None:
            before_click(
                "choose_target",
                target_coordinate,
                {
                    "choices_screenshot": choice_path,
                    "technology_name": target_name,
                    "target_evidence": target_evidence,
                },
            )
        self.driver.click_client(target_coordinate)

        after_lines, after_path = self._observe(f"{prefix}_after")
        self.analyzer.require_game_date(after_lines, observation_date)
        verification = self.analyzer.verify_selected(area, target_name, after_lines)
        return {
            "technology_name": target_name,
            "expected_choice_names": list(expected_names),
            "select_coordinate": list(select_coordinate),
            "target_coordinate": list(target_coordinate),
            "screenshots": {
                "before": base_path,
                "choices": choice_path,
                "after": after_path,
            },
            "target_evidence": target_evidence,
            "verification": verification,
        }
