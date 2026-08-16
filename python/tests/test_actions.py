from __future__ import annotations

import json
import unittest
from pathlib import Path

from python.paradox_agent.actions import ActionValidationError, validate_actions
from python.paradox_agent.planet_catalog import BUILDING_TYPES


OBSERVATION = {
    "player": {
        "research": {
            "active": {"physics": None, "society": None, "engineering": None},
            "alternatives": {
                "physics": ["tech_power"],
                "society": ["tech_food"],
                "engineering": ["tech_mining"],
            }
        }
    }
}


def district_observation() -> dict[str, object]:
    availability = {
        district: {
            "built": 0,
            "available": 3,
            "cap": 3,
            "buildable": True,
            "authoritative": True,
            "cost": {"minerals": 500 if district == "district_city" else 300},
        }
        for district in (
            "district_city",
            "district_generator",
            "district_mining",
            "district_farming",
        )
    }
    return {
        "player": {
            "country_id": 0,
            "resources": {"minerals": 1000},
            "planets": [
                {
                    "id": 4,
                    "owner_id": 0,
                    "district_capacity": {"used": 2, "available": 8, "maximum": 10},
                    "district_availability": availability,
                    "construction_queue": {
                        "known": True,
                        "active": False,
                        "safe_to_build": True,
                        "details": {},
                    },
                }
            ],
        }
    }


def building_observation() -> dict[str, object]:
    state = district_observation()
    planet = state["player"]["planets"][0]
    planet["building_capacity"] = {
        "used": 4,
        "available": 5,
        "maximum": 9,
        "authoritative": True,
    }
    planet["building_availability"] = {
        "building_research_lab_1": {
            "built": 1,
            "buildable": True,
            "authoritative": True,
            "cost": {"minerals": 400},
        }
    }
    return state


class ActionValidationTests(unittest.TestCase):
    def test_action_schema_is_valid_json_and_disallows_unknown_fields(self) -> None:
        schema_path = Path(__file__).parents[1] / "schemas" / "action_decision.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(schema["properties"]["schema"]["const"], 1)
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["chooseResearchAction"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["buildDistrictAction"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["buildBuildingAction"]["additionalProperties"])

    def test_accepts_legal_build_building(self) -> None:
        actions = validate_actions(
            [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
            building_observation(),
        )
        self.assertEqual(
            actions[0].to_dict(),
            {"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"},
        )

    def test_accepts_every_catalog_building_when_authoritatively_legal(self) -> None:
        for building in BUILDING_TYPES:
            with self.subTest(building=building):
                state = building_observation()
                state["player"]["planets"][0]["building_availability"] = {
                    building: {
                        "built": 0,
                        "buildable": True,
                        "authoritative": True,
                        "requirements_met": True,
                        "cost": {"minerals": 400},
                    }
                }
                action = validate_actions(
                    [{"type": "BUILD_BUILDING", "planet_id": 4, "building": building}],
                    state,
                )[0]
                self.assertEqual(action.parameters["building"], building)

    def test_rejects_unmet_building_technology_requirement(self) -> None:
        state = building_observation()
        option = state["player"]["planets"][0]["building_availability"]["building_research_lab_1"]
        option.update(
            {
                "requirements_met": False,
                "prerequisites": ["tech_basic_science_lab_1"],
                "buildable": False,
            }
        )
        with self.assertRaisesRegex(ActionValidationError, "technology requirements"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
                state,
            )

    def test_rejects_unknown_building(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "building must be one of"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_cheat"}],
                building_observation(),
            )

    def test_rejects_building_on_unknown_or_foreign_planet(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "not a known player colony"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 99, "building": "building_research_lab_1"}],
                building_observation(),
            )
        state = building_observation()
        state["player"]["planets"][0]["owner_id"] = 7
        with self.assertRaisesRegex(ActionValidationError, "not owned by the player"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
                state,
            )

    def test_rejects_no_building_slot_or_unavailable_building(self) -> None:
        state = building_observation()
        state["player"]["planets"][0]["building_capacity"]["available"] = 0
        state["player"]["planets"][0]["building_availability"]["building_research_lab_1"]["buildable"] = False
        with self.assertRaisesRegex(ActionValidationError, "no available building slots"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
                state,
            )

    def test_rejects_building_with_insufficient_resources(self) -> None:
        state = building_observation()
        state["player"]["resources"]["minerals"] = 399
        with self.assertRaisesRegex(ActionValidationError, "insufficient minerals"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
                state,
            )

    def test_rejects_unsafe_or_duplicate_building_queue(self) -> None:
        state = building_observation()
        state["player"]["planets"][0]["construction_queue"].update(
            {"active": True, "safe_to_build": False, "details": {"item": "district_city"}}
        )
        with self.assertRaisesRegex(ActionValidationError, "not empty and safe"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
                state,
            )
        state["player"]["planets"][0]["construction_queue"]["details"] = {
            "item": "building_research_lab_1"
        }
        with self.assertRaisesRegex(ActionValidationError, "already represented"):
            validate_actions(
                [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
                state,
            )

    def test_accepts_legal_build_district(self) -> None:
        actions = validate_actions(
            [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}],
            district_observation(),
        )
        self.assertEqual(
            actions[0].to_dict(),
            {"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"},
        )

    def test_rejects_unknown_district(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "district must be one of"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_cheat"}],
                district_observation(),
            )

    def test_rejects_unknown_planet(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "not a known player colony"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 99, "district": "district_mining"}],
                district_observation(),
            )

    def test_rejects_foreign_planet(self) -> None:
        state = district_observation()
        state["player"]["planets"][0]["owner_id"] = 7
        with self.assertRaisesRegex(ActionValidationError, "not owned by the player"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}],
                state,
            )

    def test_rejects_no_district_capacity(self) -> None:
        state = district_observation()
        state["player"]["planets"][0]["district_capacity"]["available"] = 0
        with self.assertRaisesRegex(ActionValidationError, "no available district capacity"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}],
                state,
            )

    def test_rejects_insufficient_resources(self) -> None:
        state = district_observation()
        state["player"]["resources"]["minerals"] = 299
        with self.assertRaisesRegex(ActionValidationError, "insufficient minerals"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}],
                state,
            )

    def test_rejects_invalid_district_for_planet(self) -> None:
        state = district_observation()
        option = state["player"]["planets"][0]["district_availability"]["district_farming"]
        option.update({"available": 0, "cap": 0, "buildable": False})
        with self.assertRaisesRegex(ActionValidationError, "cannot legally be built"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_farming"}],
                state,
            )

    def test_rejects_unsafe_construction_queue(self) -> None:
        state = district_observation()
        queue = state["player"]["planets"][0]["construction_queue"]
        queue.update({"active": True, "safe_to_build": False, "details": {"item": "building_lab"}})
        with self.assertRaisesRegex(ActionValidationError, "not empty and safe"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}],
                state,
            )

    def test_rejects_duplicate_district_in_queue(self) -> None:
        state = district_observation()
        queue = state["player"]["planets"][0]["construction_queue"]
        queue.update(
            {"active": True, "safe_to_build": False, "details": {"item": "district_mining"}}
        )
        with self.assertRaisesRegex(ActionValidationError, "already represented"):
            validate_actions(
                [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}],
                state,
            )

    def test_accepts_available_research(self) -> None:
        actions = validate_actions(
            [{"type": "CHOOSE_RESEARCH", "area": "physics", "technology_id": "tech_power"}],
            OBSERVATION,
        )
        self.assertEqual(actions[0].to_dict()["technology_id"], "tech_power")

    def test_rejects_unavailable_research(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "not an available physics alternative"):
            validate_actions(
                [{"type": "CHOOSE_RESEARCH", "area": "physics", "technology_id": "tech_cheat"}],
                OBSERVATION,
            )

    def test_rejects_replacing_active_research(self) -> None:
        observation = {
            "player": {
                "research": {
                    "active": {"physics": "tech_lasers"},
                    "alternatives": {"physics": ["tech_power"]},
                }
            }
        }

        with self.assertRaisesRegex(ActionValidationError, "physics research is already active"):
            validate_actions(
                [{"type": "CHOOSE_RESEARCH", "area": "physics", "technology_id": "tech_power"}],
                observation,
            )

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "unknown field 'console_command'"):
            validate_actions(
                [{"type": "WAIT", "months": 1, "console_command": "effect add_resource"}],
                OBSERVATION,
            )

    def test_wait_cannot_be_combined_with_another_action(self) -> None:
        with self.assertRaisesRegex(ActionValidationError, "WAIT cannot be combined"):
            validate_actions(
                [
                    {"type": "WAIT", "months": 1},
                    {"type": "CHOOSE_RESEARCH", "area": "physics", "technology_id": "tech_power"},
                ],
                OBSERVATION,
            )


if __name__ == "__main__":
    unittest.main()
