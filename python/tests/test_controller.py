from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from python.paradox_agent.controller import ObservationError, make_decision, process_state
from test_actions import building_observation, district_observation


def observation() -> dict[str, object]:
    return {
        "schema": 1,
        "source": "stellaris_save",
        "save": {"date": "2202.01.01"},
        "player": {
            "resources": {"energy": 500, "minerals": 500, "food": 500, "consumer_goods": 500, "alloys": 500},
            "monthly_balance": {"energy": 10, "minerals": 10, "food": 10, "consumer_goods": 10, "alloys": 10},
            "research": {
                "researched": [{"id": "tech_start", "level": 1}],
                "active": {"physics": None, "society": None, "engineering": None},
                "alternatives": {
                    "physics": ["tech_lasers_2", "tech_physics_1"],
                    "society": ["tech_fleet_size", "tech_farming_1"],
                    "engineering": ["tech_armor_2", "tech_space_mining_1"],
                },
            },
            "planets": [{"stability": 70}],
            "fleets": [{"military_power": 100}],
        },
        "visibility": {"policy": "player_owned_only", "foreign_countries_included": False},
    }


class ControllerTests(unittest.TestCase):
    def test_rule_policy_proposes_one_legal_research_per_area(self) -> None:
        decision = make_decision(observation())

        self.assertEqual(decision["policy"], "rule_based_v1")
        self.assertEqual([action["area"] for action in decision["actions"]], ["physics", "society", "engineering"])
        self.assertEqual(decision["actions"][0]["technology_id"], "tech_physics_1")
        self.assertEqual(decision["actions"][1]["technology_id"], "tech_farming_1")
        self.assertEqual(decision["actions"][2]["technology_id"], "tech_space_mining_1")
        self.assertEqual(decision["execution"]["status"], "not_executed")
        self.assertTrue(decision["objective"]["survival_constraint_satisfied"])

    def test_waits_when_no_research_choice_is_available(self) -> None:
        state = observation()
        state["player"]["research"]["alternatives"] = {area: [] for area in ("physics", "society", "engineering")}

        self.assertEqual(make_decision(state)["actions"], [{"type": "WAIT", "months": 1}])

    def test_does_not_replace_active_research(self) -> None:
        state = observation()
        state["player"]["research"]["active"] = {
            "physics": "tech_lasers_2",
            "society": "tech_fleet_size",
            "engineering": "tech_armor_2",
        }

        self.assertEqual(make_decision(state)["actions"], [{"type": "WAIT", "months": 1}])

    def test_rule_policy_proposes_one_legal_district_when_research_is_active(self) -> None:
        state = observation()
        state["player"]["research"]["active"] = {
            "physics": "tech_lasers_2",
            "society": "tech_fleet_size",
            "engineering": "tech_armor_2",
        }
        district_state = district_observation()["player"]
        state["player"]["country_id"] = district_state["country_id"]
        state["player"]["planets"] = district_state["planets"]

        self.assertEqual(
            make_decision(state)["actions"],
            [{"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_city"}],
        )

    def test_rule_policy_prefers_one_legal_building_when_research_is_active(self) -> None:
        state = observation()
        state["player"]["research"]["active"] = {
            "physics": "tech_lasers_2",
            "society": "tech_fleet_size",
            "engineering": "tech_armor_2",
        }
        building_state = building_observation()["player"]
        state["player"]["country_id"] = building_state["country_id"]
        state["player"]["planets"] = building_state["planets"]

        self.assertEqual(
            make_decision(state)["actions"],
            [{"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"}],
        )

    def test_rejects_omniscient_observation(self) -> None:
        state = observation()
        state["visibility"]["foreign_countries_included"] = True

        with self.assertRaises(ObservationError):
            make_decision(state)

    def test_processes_state_atomically_and_appends_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "current_state.json"
            output_path = root / "proposed_decision.json"
            log_path = root / "decisions.jsonl"
            state_path.write_text(json.dumps(observation()), encoding="utf-8")

            decision = process_state(state_path, output_path, log_path)

            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), decision)
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records, [decision])


if __name__ == "__main__":
    unittest.main()
