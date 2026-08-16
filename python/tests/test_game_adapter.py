from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
import json
from unittest.mock import patch

from python.paradox_agent.controller import make_decision
from python.paradox_agent.game_adapter import (
    ADAPTER_NAME,
    AdapterError,
    StellarisLayout,
    build_execution_plan,
    execute_plan,
    verify_receipt_from_save,
)

from test_actions import building_observation, district_observation
from test_controller import observation


def build_district_state() -> dict[str, object]:
    state = observation()
    state["player"]["research"]["active"] = {
        "physics": "tech_lasers_2",
        "society": "tech_fleet_size",
        "engineering": "tech_armor_2",
    }
    district_player = district_observation()["player"]
    state["player"]["country_id"] = district_player["country_id"]
    state["player"]["resources"].update(district_player["resources"])
    state["player"]["planets"] = district_player["planets"]
    state["player"]["planets"][0]["name_key"] = "PARADOX_AGENT_TESTBED_PLANET"
    state["player"]["planets"][0]["designation"] = "col_capital"
    return state


def build_building_state() -> dict[str, object]:
    state = observation()
    state["player"]["research"]["active"] = {
        "physics": "tech_lasers_2",
        "society": "tech_fleet_size",
        "engineering": "tech_armor_2",
    }
    building_player = building_observation()["player"]
    state["player"]["country_id"] = building_player["country_id"]
    state["player"]["resources"].update(building_player["resources"])
    state["player"]["planets"] = building_player["planets"]
    state["player"]["planets"][0]["name_key"] = "PARADOX_AGENT_TESTBED_PLANET"
    state["player"]["planets"][0]["designation"] = "col_capital"
    return state


def district_receipt(decision: dict[str, object]) -> dict[str, object]:
    action = decision["actions"][0]
    return {
        "schema": 2,
        "adapter": ADAPTER_NAME,
        "decision_id": decision["decision_id"],
        "status": "visual_verified_pending_save",
        "actions": [
            {
                "type": action["type"],
                "planet_id": action["planet_id"],
                "district": action["district"],
                "status": "visual_verified_pending_save",
            }
        ],
    }


class GameAdapterTests(unittest.TestCase):
    def test_prepares_semantic_building_without_fixed_coordinates(self) -> None:
        state = build_building_state()
        plan = build_execution_plan(make_decision(state), state)

        self.assertEqual(plan.actions[0].type, "BUILD_BUILDING")
        self.assertEqual(plan.actions[0].planet_id, 4)
        self.assertEqual(plan.actions[0].building, "building_research_lab_1")
        self.assertEqual(plan.expected_buildings[4], ("building_research_lab_1",))
        self.assertFalse(hasattr(plan.actions[0], "click_coordinate"))

    def test_prepares_any_authoritatively_allowed_catalog_building(self) -> None:
        state = build_building_state()
        planet = state["player"]["planets"][0]
        planet["building_availability"] = {
            "building_holo_theatres": {
                "built": 0,
                "buildable": True,
                "authoritative": True,
                "requirements_met": True,
                "cost": {"minerals": 400},
            }
        }
        decision = make_decision(state)
        plan = build_execution_plan(decision, state)
        self.assertEqual(plan.actions[0].building, "building_holo_theatres")
        self.assertEqual(plan.expected_buildings[4], ("building_holo_theatres",))

    def test_prepares_semantic_research_without_fixed_coordinates(self) -> None:
        state = observation()
        decision = make_decision(state)

        plan = build_execution_plan(decision, state)

        self.assertEqual(plan.open_view_key, "F4")
        self.assertEqual(plan.expected_client_size, (1920, 1080))
        self.assertEqual(plan.actions[0].alternative_index, 1)
        self.assertEqual(
            plan.expected_alternatives["physics"],
            ("tech_lasers_2", "tech_physics_1"),
        )
        self.assertFalse(hasattr(plan.actions[0], "select_coordinate"))

    def test_refuses_stale_decision(self) -> None:
        state = observation()
        decision = make_decision(state)
        state["player"]["resources"]["energy"] = 501

        with self.assertRaisesRegex(AdapterError, "stale"):
            build_execution_plan(decision, state)

    def test_refuses_uncalibrated_gui_scale(self) -> None:
        state = observation()
        decision = make_decision(state)

        with self.assertRaisesRegex(AdapterError, "gui_scale=1.0"):
            build_execution_plan(decision, state, StellarisLayout(gui_scale=1.25))

    def test_prepares_semantic_district_without_fixed_coordinates(self) -> None:
        state = build_district_state()
        decision = make_decision(state)

        plan = build_execution_plan(decision, state)

        self.assertEqual(plan.actions[0].type, "BUILD_DISTRICT")
        self.assertEqual(plan.actions[0].planet_id, 4)
        self.assertEqual(plan.actions[0].planet_name_key, "PARADOX_AGENT_TESTBED_PLANET")
        self.assertEqual(plan.actions[0].district, "district_city")
        self.assertFalse(hasattr(plan.actions[0], "click_coordinate"))

    def test_refuses_game_date_mismatch(self) -> None:
        state = build_district_state()
        decision = make_decision(state)
        decision["observation_date"] = "2202.01.02"

        with self.assertRaisesRegex(AdapterError, "decision date does not match"):
            build_execution_plan(decision, state)

    def test_duplicate_receipt_prevents_execution(self) -> None:
        state = build_district_state()
        plan = build_execution_plan(make_decision(state), state)
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "duplicate execution"):
                execute_plan(plan, receipt_path)

    def test_successful_district_visual_execution_is_provisional(self) -> None:
        state = build_district_state()
        plan = build_execution_plan(make_decision(state), state)
        with tempfile.TemporaryDirectory() as directory, patch(
            "python.paradox_agent.game_adapter.WindowsStellarisDriver"
        ) as driver_type, patch(
            "python.paradox_agent.game_adapter.StellarisLocalizer"
        ), patch(
            "python.paradox_agent.game_adapter.ResearchVisualSkill"
        ), patch(
            "python.paradox_agent.game_adapter.DistrictVisualSkill"
        ) as district_skill_type:
            district_skill_type.return_value.build.return_value = {
                "screenshots": {"before": "before.png", "after": "after.png"},
                "verification": {"occurrences_after": 2},
            }
            receipt_path = Path(directory) / "receipt.json"
            receipt = execute_plan(plan, receipt_path)

        driver_type.return_value.activate.assert_called_once()
        self.assertEqual(receipt["status"], "visual_verified_pending_save")
        self.assertEqual(receipt["actions"][0]["status"], "visual_verified_pending_save")

    def test_successful_building_visual_execution_is_provisional(self) -> None:
        state = build_building_state()
        plan = build_execution_plan(make_decision(state), state)
        with tempfile.TemporaryDirectory() as directory, patch(
            "python.paradox_agent.game_adapter.WindowsStellarisDriver"
        ) as driver_type, patch(
            "python.paradox_agent.game_adapter.StellarisLocalizer"
        ), patch(
            "python.paradox_agent.game_adapter.ResearchVisualSkill"
        ), patch(
            "python.paradox_agent.game_adapter.DistrictVisualSkill"
        ), patch(
            "python.paradox_agent.game_adapter.BuildingVisualSkill"
        ) as building_skill_type:
            building_skill_type.return_value.build.return_value = {
                "screenshots": {"before": "before.png", "after": "after.png"},
                "verification": {"constructing_texts": ["Constructing Research Labs"]},
            }
            receipt_path = Path(directory) / "receipt.json"
            receipt = execute_plan(plan, receipt_path)

        driver_type.return_value.activate.assert_called_once()
        self.assertEqual(receipt["status"], "visual_verified_pending_save")
        self.assertEqual(receipt["actions"][0]["building"], "building_research_lab_1")

    def test_save_verification_promotes_matching_visual_receipt(self) -> None:
        before = observation()
        decision = make_decision(before)
        after = observation()
        after["save"]["date"] = "2202.01.02"
        after["save"]["file_name"] = "after.sav"
        after["player"]["research"]["active"] = {
            action["area"]: action["technology_id"] for action in decision["actions"]
        }
        receipt = {
            "schema": 2,
            "adapter": ADAPTER_NAME,
            "decision_id": decision["decision_id"],
            "status": "visual_verified_pending_save",
            "actions": [
                {
                    "type": action["type"],
                    "area": action["area"],
                    "technology_id": action["technology_id"],
                    "status": "visual_verified_pending_save",
                }
                for action in decision["actions"]
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            verified = verify_receipt_from_save(decision, after, receipt_path)

        self.assertEqual(verified["status"], "save_verified")
        self.assertTrue(all(row["status"] == "save_verified" for row in verified["actions"]))
        self.assertEqual(verified["save_verification"]["file_name"], "after.sav")

    def test_save_verification_refuses_a_mismatch_without_promoting_receipt(self) -> None:
        before = observation()
        decision = make_decision(before)
        after = observation()
        receipt = {
            "schema": 2,
            "adapter": ADAPTER_NAME,
            "decision_id": decision["decision_id"],
            "status": "visual_verified_pending_save",
            "actions": [
                {
                    "type": action["type"],
                    "area": action["area"],
                    "technology_id": action["technology_id"],
                    "status": "visual_verified_pending_save",
                }
                for action in decision["actions"]
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            with self.assertRaisesRegex(AdapterError, "save did not confirm"):
                verify_receipt_from_save(decision, after, receipt_path)
            unchanged = json.loads(receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(unchanged["status"], "visual_verified_pending_save")

    def test_save_verification_promotes_queued_district(self) -> None:
        before = build_district_state()
        decision = make_decision(before)
        after = build_district_state()
        after["save"]["date"] = "2202.01.02"
        after["save"]["file_name"] = "after_district.sav"
        queue = after["player"]["planets"][0]["construction_queue"]
        queue.update(
            {
                "active": True,
                "safe_to_build": False,
                "details": {"items": [{"buildable": "district_city", "progress": 0.1}]},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(district_receipt(decision)), encoding="utf-8")
            verified = verify_receipt_from_save(decision, after, receipt_path)

        self.assertEqual(verified["status"], "save_verified")
        self.assertEqual(verified["actions"][0]["status"], "save_verified")

    def test_save_reconciles_only_postclick_district_visual_failure(self) -> None:
        before = build_district_state()
        decision = make_decision(before)
        after = build_district_state()
        after["save"]["date"] = "2202.01.02"
        after["save"]["file_name"] = "reconciled.sav"
        after["player"]["planets"][0]["construction_queue"].update(
            {
                "active": True,
                "safe_to_build": False,
                "details": {"items": [{"buildable_district": {"district": "district_city"}}]},
            }
        )
        receipt = district_receipt(decision)
        receipt["status"] = "manual_review_required"
        receipt["error"] = "VisualSkillError: could not visually verify 'City District'"
        receipt["actions"][0].update(
            {
                "status": "attempting_build_target",
                "next_click": {"stage": "build_target", "coordinate": [792, 514]},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            verified = verify_receipt_from_save(decision, after, receipt_path)

        self.assertEqual(verified["status"], "save_verified")
        self.assertTrue(verified["save_verification"]["reconciled_visual_postcondition"])

    def test_save_verification_refuses_subsequent_save_without_district(self) -> None:
        before = build_district_state()
        decision = make_decision(before)
        after = build_district_state()
        after["save"]["date"] = "2202.01.02"
        after["save"]["file_name"] = "unrelated.sav"
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(district_receipt(decision)), encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "construction queue does not contain"):
                verify_receipt_from_save(decision, after, receipt_path)
            unchanged = json.loads(receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(unchanged["status"], "visual_verified_pending_save")

    def test_save_verification_promotes_queued_building(self) -> None:
        before = build_building_state()
        decision = make_decision(before)
        after = build_building_state()
        after["save"]["date"] = "2202.01.02"
        after["save"]["file_name"] = "after_building.sav"
        after["player"]["planets"][0]["construction_queue"].update(
            {
                "active": True,
                "safe_to_build": False,
                "details": {
                    "items": [
                        {"buildable_building": {"building": "building_research_lab_1"}}
                    ]
                },
            }
        )
        action = decision["actions"][0]
        receipt = {
            "schema": 2,
            "adapter": ADAPTER_NAME,
            "decision_id": decision["decision_id"],
            "status": "visual_verified_pending_save",
            "actions": [
                {
                    "type": action["type"],
                    "planet_id": action["planet_id"],
                    "building": action["building"],
                    "status": "visual_verified_pending_save",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            verified = verify_receipt_from_save(decision, after, receipt_path)

        self.assertEqual(verified["status"], "save_verified")
        self.assertEqual(verified["actions"][0]["status"], "save_verified")

    def test_save_verification_refuses_subsequent_save_without_building(self) -> None:
        before = build_building_state()
        decision = make_decision(before)
        after = build_building_state()
        after["save"]["date"] = "2202.01.02"
        after["save"]["file_name"] = "unrelated_building.sav"
        action = decision["actions"][0]
        receipt = {
            "schema": 2,
            "adapter": ADAPTER_NAME,
            "decision_id": decision["decision_id"],
            "status": "visual_verified_pending_save",
            "actions": [
                {
                    "type": action["type"],
                    "planet_id": action["planet_id"],
                    "building": action["building"],
                    "status": "visual_verified_pending_save",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "construction queue does not contain"):
                verify_receipt_from_save(decision, after, receipt_path)


if __name__ == "__main__":
    unittest.main()
