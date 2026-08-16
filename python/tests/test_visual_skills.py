from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from python.paradox_agent.visual_skills import (
    BuildingScreenAnalyzer,
    BuildingVisualSkill,
    DistrictScreenAnalyzer,
    OcrLine,
    RapidOcrReader,
    ResearchScreenAnalyzer,
    StellarisLocalizer,
    VisualSkillError,
)


def line(text: str, x: int, y: int, confidence: float = 0.99) -> OcrLine:
    return OcrLine(
        text=text,
        confidence=confidence,
        box=((x - 20, y - 8), (x + 20, y - 8), (x + 20, y + 8), (x - 20, y + 8)),
    )


class ResearchScreenAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = ResearchScreenAnalyzer()

    def test_locates_area_relative_select_button(self) -> None:
        lines = (
            line("Technology", 130, 72),
            line("Physics", 100, 266),
            line("Select Research", 338, 342),
            line("Society", 110, 465),
            line("Select Research", 338, 542),
            line("Engineering", 125, 666),
            line("Select Research", 338, 742),
        )

        self.assertEqual(self.analyzer.locate_select("physics", lines), (338, 342))
        self.assertEqual(self.analyzer.locate_select("society", lines), (338, 542))
        self.assertEqual(self.analyzer.locate_select("engineering", lines), (338, 742))

    def test_refuses_select_when_choice_dialog_is_already_open(self) -> None:
        lines = (
            line("Technology", 130, 72),
            line("Physics Research", 750, 84),
            line("Physics", 100, 266),
            line("Select Research", 338, 342),
        )

        with self.assertRaisesRegex(VisualSkillError, "already open"):
            self.analyzer.locate_select("physics", lines)

    def test_matches_semantic_target_only_after_all_choices_match(self) -> None:
        lines = (
            line("Technology", 130, 72),
            line("Physics Research", 750, 84),
            line("Select a Technology to research", 952, 126),
            line("Quantum Computing", 760, 260),
            line("Improved Deflectors", 755, 410),
            line("Fusion Power", 726, 560),
            line("Unlocks Component: Improved Deflectors", 914, 460),
        )

        coordinate, evidence = self.analyzer.locate_choice_target(
            "physics",
            "Quantum Computing",
            ("Quantum Computing", "Improved Deflectors", "Fusion Power"),
            lines,
        )

        self.assertEqual(coordinate, (952, 305))
        self.assertEqual(evidence["target_text"], "Quantum Computing")

    def test_refuses_stale_visible_choices(self) -> None:
        lines = (
            line("Technology", 130, 72),
            line("Physics Research", 750, 84),
            line("Select a Technology to research", 952, 126),
            line("Quantum Computing", 760, 260),
            line("Improved Deflectors", 755, 410),
            line("Fusion Power", 726, 560),
        )

        with self.assertRaisesRegex(VisualSkillError, "missing 'Blue Lasers'"):
            self.analyzer.locate_choice_target(
                "physics",
                "Quantum Computing",
                ("Quantum Computing", "Blue Lasers", "Fusion Power"),
                lines,
            )

    def test_postcondition_requires_dialog_closed_and_active_title(self) -> None:
        lines = (line("Technology", 130, 72), line("Quantum Computing", 320, 344))

        evidence = self.analyzer.verify_selected("physics", "Quantum Computing", lines)

        self.assertEqual(evidence["active_text"], "Quantum Computing")

    def test_requires_live_screen_date_to_equal_observation(self) -> None:
        lines = (line("2202.08.14", 1800, 15),)

        self.assertEqual(self.analyzer.require_game_date(lines, "2202.08.14"), "2202.08.14")
        with self.assertRaisesRegex(VisualSkillError, "does not match"):
            self.analyzer.require_game_date(lines, "2202.07.01")


class DistrictScreenAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = DistrictScreenAnalyzer()
        self.base = (
            line("2202.08.14", 1800, 15),
            line("Axiom Prime", 280, 120),
            line("City District", 250, 330),
            line("Build", 390, 335),
            line("Generator District", 430, 330),
            line("Build", 560, 335),
            line("Mining District", 610, 330),
            line("Build", 740, 335),
            line("Agriculture District", 790, 330),
            line("Build", 920, 335),
        )

    def test_locates_planet_from_unique_outliner_identity(self) -> None:
        lines = (
            line("2202.08.14", 1800, 15),
            line("Axiom Prime", 1660, 390),
            line("Empire Capital", 1680, 415),
        )
        self.assertEqual(
            self.analyzer.locate_planet_in_outliner("Axiom Prime", "Empire Capital", lines),
            (1660, 390),
        )

    def test_uses_designation_to_reject_same_named_sector_header(self) -> None:
        lines = (
            line("Axiom Prime", 1660, 350),
            line("Axiom Prime", 1660, 390),
            line("Empire Capital", 1680, 415),
        )
        self.assertEqual(
            self.analyzer.locate_planet_in_outliner("Axiom Prime", "Empire Capital", lines),
            (1660, 390),
        )

    def test_rejects_visual_planet_identity_mismatch(self) -> None:
        lines = tuple(item for item in self.base if item.text != "Axiom Prime") + (
            line("Foreign World", 280, 120),
        )
        with self.assertRaisesRegex(VisualSkillError, "visible planet identity does not match"):
            self.analyzer.require_planet_screen("Axiom Prime", lines)

    def test_does_not_mistake_system_map_label_for_planet_header(self) -> None:
        lines = (
            line("2202.12.03", 1800, 15),
            line("Axiom Prime", 800, 715),
            line("Axiom Prime", 1700, 390),
        )
        with self.assertRaisesRegex(VisualSkillError, "visible planet identity does not match"):
            self.analyzer.require_planet_screen("Axiom Prime", lines)

    def test_rejects_visible_district_mismatch(self) -> None:
        lines = tuple(item for item in self.base if item.text != "Mining District")
        with self.assertRaisesRegex(VisualSkillError, "visible district options do not match"):
            self.analyzer.locate_district_target(
                "Axiom Prime",
                "Mining District",
                ("City District", "Generator District", "Mining District", "Agriculture District"),
                lines,
            )

    def test_targets_matching_build_control_not_district_label(self) -> None:
        coordinate, evidence = self.analyzer.locate_district_target(
            "Axiom Prime",
            "Mining District",
            ("City District", "Generator District", "Mining District", "Agriculture District"),
            self.base,
        )
        self.assertEqual(coordinate, (740, 335))
        self.assertEqual(evidence["district_label_center"], [610, 330])
        self.assertEqual(evidence["target_text"], "Build")

    def test_rejects_visual_postcondition_failure(self) -> None:
        with self.assertRaisesRegex(VisualSkillError, "construction queue"):
            self.analyzer.verify_queued("Axiom Prime", "Mining District", 1, self.base)

    def test_verifies_new_queue_occurrence(self) -> None:
        after = self.base + (line("Mining District", 1140, 740),)
        evidence = self.analyzer.verify_queued("Axiom Prime", "Mining District", 1, after)
        self.assertEqual(evidence["occurrences_after"], 2)

    def test_verifies_constructing_status_when_queue_uses_an_icon(self) -> None:
        after = self.base + (line("Constructing City District (0.00%)", 990, 670),)
        evidence = self.analyzer.verify_queued("Axiom Prime", "City District", 1, after)
        self.assertEqual(evidence["constructing_texts"], ["Constructing City District (0.00%)"])


class BuildingScreenAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = BuildingScreenAnalyzer()
        self.base = (
            line("2202.08.14", 1800, 15),
            line("Axiom Prime", 280, 120),
            line("Districts and Buildings", 250, 460),
        )

    def _image_with_slot(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1920, 1080), (10, 15, 18))
        draw = ImageDraw.Draw(image)
        draw.rectangle((395, 558, 405, 562), fill=(20, 235, 205))
        draw.rectangle((398, 553, 402, 567), fill=(20, 235, 205))
        return image

    def test_locates_image_grounded_empty_building_slot(self) -> None:
        coordinate, evidence = self.analyzer.locate_empty_building_slot(
            "Axiom Prime", self.base, self._image_with_slot()
        )
        self.assertEqual(coordinate, (400, 560))
        self.assertEqual(evidence["target_center"], [400, 560])

    def test_returns_all_grounded_empty_building_slots_for_safe_probing(self) -> None:
        from PIL import Image, ImageDraw

        image = self._image_with_slot()
        draw = ImageDraw.Draw(image)
        draw.rectangle((495, 558, 505, 562), fill=(20, 235, 205))
        draw.rectangle((498, 553, 502, 567), fill=(20, 235, 205))
        coordinates, evidence = self.analyzer.locate_empty_building_slots(
            "Axiom Prime", self.base, image
        )
        self.assertEqual(coordinates, ((400, 560), (500, 560)))
        self.assertEqual(evidence["slot_candidates"], [[400, 560], [500, 560]])

    def test_groups_city_archives_and_mixed_industry_slots(self) -> None:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1920, 1080), (10, 15, 18))
        draw = ImageDraw.Draw(image)

        def draw_live_sized_plus(x: int, y: int) -> None:
            # 125 pixels: the live Mixed Industry antialias mask was larger
            # than the legacy 120-pixel component ceiling.
            draw.rectangle((x - 7, y - 2, x + 7, y + 2), fill=(20, 235, 205))
            draw.rectangle((x - 2, y - 7, x + 2, y + 7), fill=(20, 235, 205))

        for coordinate in (
            (401, 567),
            (793, 564),
            (271, 632),
            (336, 632),
            (401, 632),
            (728, 631),
            (793, 631),
        ):
            draw_live_sized_plus(*coordinate)

        lines = self.base + (
            line("Archives", 520, 568),
            line("Mixed Industry", 540, 632),
        )
        coordinates, evidence = self.analyzer.locate_empty_building_slots(
            "Axiom Prime", lines, image
        )

        self.assertEqual(
            coordinates,
            (
                (793, 564),
                (401, 567),
                (728, 631),
                (793, 631),
                (271, 632),
                (336, 632),
                (401, 632),
            ),
        )
        self.assertEqual(len(evidence["slot_groups"]), 4)
        self.assertEqual(
            evidence["slot_groups"][1]["nearby_label"], "Archives"
        )
        self.assertEqual(
            evidence["slot_groups"][3]["nearby_label"], "Mixed Industry"
        )

    def test_rejects_filled_cyan_square_as_building_slot(self) -> None:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1920, 1080), (10, 15, 18))
        draw = ImageDraw.Draw(image)
        draw.rectangle((393, 553, 407, 567), fill=(20, 235, 205))
        with self.assertRaisesRegex(VisualSkillError, "empty building slot"):
            self.analyzer.locate_empty_building_slots("Axiom Prime", self.base, image)

    def test_rejects_missing_empty_building_slot(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (1920, 1080), (10, 15, 18))
        with self.assertRaisesRegex(VisualSkillError, "empty building slot"):
            self.analyzer.locate_empty_building_slot("Axiom Prime", self.base, image)

    def test_locates_unique_building_option(self) -> None:
        lines = self.base + (
            line("Construct Building", 1750, 168),
            line("Research Labs", 1660, 406),
        )
        coordinate, evidence = self.analyzer.locate_building_target(
            "Axiom Prime", "Research Labs", lines
        )
        self.assertEqual(coordinate, (1660, 406))
        self.assertEqual(evidence["target_text"], "Research Labs")

    def test_rejects_visible_building_mismatch(self) -> None:
        lines = self.base + (line("Construct Building", 1750, 168),)
        with self.assertRaisesRegex(VisualSkillError, "visible building option"):
            self.analyzer.locate_building_target("Axiom Prime", "Research Labs", lines)

    def test_verifies_constructing_building_status(self) -> None:
        lines = self.base + (line("Constructing Research Labs (0.00%)", 1000, 670),)
        evidence = self.analyzer.verify_building_queued(
            "Axiom Prime", "Research Labs", lines
        )
        self.assertEqual(evidence["verification_kind"], "constructing_status")
        self.assertEqual(evidence["constructing_texts"], ["Constructing Research Labs (0.00%)"])

    def test_verifies_compact_build_queue_row(self) -> None:
        lines = self.base + (
            line("Auto Designated", 1290, 411),
            line("Research Labs", 1666, 407),
            line("Research L", 1274, 510),
        )
        evidence = self.analyzer.verify_building_queued(
            "Axiom Prime", "Research Labs", lines
        )
        self.assertEqual(evidence["verification_kind"], "build_queue_row")
        self.assertEqual(evidence["queue_texts"], ["Research L"])

    def test_rejects_picker_option_as_compact_queue_row(self) -> None:
        lines = self.base + (
            line("Auto Designated", 1290, 411),
            line("Research Labs", 1666, 407),
        )
        with self.assertRaisesRegex(VisualSkillError, "could not visually verify"):
            self.analyzer.verify_building_queued("Axiom Prime", "Research Labs", lines)

    def test_rejects_building_postcondition_failure(self) -> None:
        with self.assertRaisesRegex(VisualSkillError, "could not visually verify"):
            self.analyzer.verify_building_queued("Axiom Prime", "Research Labs", self.base)

    def test_visual_skill_probes_grounded_slots_before_building_click(self) -> None:
        from PIL import Image

        driver = Mock()
        driver.capture_client_image.return_value = Image.new("RGB", (1920, 1080))
        reader = Mock()
        reader.read.return_value = self.base
        localizer = Mock()
        localizer.resolve.side_effect = lambda identifier: {
            "planet_key": "Axiom Prime",
            "designation_key": "Empire Capital",
            "building_holo_theatres": "Holo-Theatres",
        }[identifier]
        with tempfile.TemporaryDirectory() as directory:
            skill = BuildingVisualSkill(
                driver, directory, localizer=localizer, reader=reader
            )
            analyzer = Mock(spec=BuildingScreenAnalyzer)
            analyzer.locate_empty_building_slots.return_value = (
                ((400, 560), (500, 560)),
                {"slot_candidates": [[400, 560], [500, 560]]},
            )
            analyzer.locate_building_target.side_effect = [
                VisualSkillError("expected one visible building option for 'Holo-Theatres'; found 0"),
                ((1660, 406), {"target_text": "Holo-Theatres"}),
            ]
            analyzer.verify_building_queued.return_value = {
                "verification_kind": "constructing_status"
            }
            skill.analyzer = analyzer
            evidence = skill.build(
                action_index=0,
                planet_name_key="planet_key",
                planet_designation_key="designation_key",
                building_id="building_holo_theatres",
                observation_date="2202.08.14",
            )

        self.assertEqual(
            [call.args[0] for call in driver.click_client.call_args_list],
            [(400, 560), (500, 560), (1660, 406)],
        )
        self.assertEqual(evidence["slot_coordinate"], [500, 560])
        self.assertEqual(len(evidence["screenshots"]["slot_probes"]), 2)


class InstalledGameIntegrationTests(unittest.TestCase):
    def test_resolves_real_446_technology_localisation(self) -> None:
        game = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Stellaris")
        if not game.is_dir():
            self.skipTest("Stellaris installation is not available")

        localizer = StellarisLocalizer(game)

        self.assertEqual(localizer.resolve("tech_physics_1"), "Quantum Computing")
        self.assertEqual(localizer.resolve("tech_fusion_power"), "Fusion Power")
        self.assertEqual(localizer.resolve("district_mining"), "Mining District")
        self.assertEqual(localizer.resolve("PARADOX_AGENT_TESTBED_PLANET"), "Axiom Prime")

    def test_resolves_every_supported_planetary_ui_label_uniquely(self) -> None:
        game = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Stellaris")
        if not game.is_dir():
            self.skipTest("Stellaris installation is not available")
        from python.paradox_agent.planet_catalog import BUILDING_TYPES, DISTRICT_TYPES

        localizer = StellarisLocalizer(game)
        labels = [localizer.resolve(identifier) for identifier in DISTRICT_TYPES + BUILDING_TYPES]
        self.assertEqual(len(labels), len(set(labels)))

    def test_reads_and_calibrates_recorded_stellaris_screens(self) -> None:
        root = Path(__file__).resolve().parents[2]
        base_path = root / "runtime" / "technology_screen.png"
        choices_path = root / "runtime" / "physics_choices.png"
        if not base_path.is_file() or not choices_path.is_file():
            self.skipTest("recorded calibration screenshots are not available")
        try:
            from PIL import Image

            reader = RapidOcrReader()
        except (ImportError, VisualSkillError) as error:
            self.skipTest(f"visual dependencies are not installed: {error}")

        analyzer = ResearchScreenAnalyzer()
        with Image.open(base_path) as image:
            base_lines = reader.read(image)
        with Image.open(choices_path) as image:
            choice_lines = reader.read(image)

        physics_select = analyzer.locate_select("physics", base_lines)
        target, evidence = analyzer.locate_choice_target(
            "physics",
            "Quantum Computing",
            ("Quantum Computing", "Improved Deflectors", "Fusion Power"),
            choice_lines,
        )

        self.assertTrue(300 <= physics_select[0] <= 370)
        self.assertTrue(320 <= physics_select[1] <= 365)
        self.assertTrue(900 <= target[0] <= 1000)
        self.assertTrue(285 <= target[1] <= 325)
        self.assertGreater(evidence["target_ocr_confidence"], 0.95)


if __name__ == "__main__":
    unittest.main()

