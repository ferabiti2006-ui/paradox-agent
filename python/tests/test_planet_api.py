from __future__ import annotations

import json
import unittest
from pathlib import Path

from python.paradox_agent.controller import observation_fingerprint
from python.paradox_agent.planet_api import (
    DeterministicPlanetGovernor,
    PlanetApiError,
    legal_planet_actions,
    make_planet_decision,
    normalize_planet,
    validate_planet_action_envelope,
)
from test_actions import building_observation
from test_controller import observation


def api_observation() -> dict[str, object]:
    state = observation()
    construction = building_observation()["player"]
    state["player"]["country_id"] = construction["country_id"]
    state["player"]["resources"]["minerals"] = 1200
    state["player"]["planets"] = construction["planets"]
    planet = state["player"]["planets"][0]
    planet.update(
        {
            "colony_id": 3,
            "name_key": "PARADOX_AGENT_TESTBED_PLANET",
            "class": "pc_continental",
            "size": 18,
            "designation": "col_capital",
            "pops": 20,
            "population": {
                "sapient": 20,
                "unemployed": 0,
                "available_jobs": 3,
                "authoritative": True,
            },
            "housing": 25,
            "housing_usage": 20,
            "housing_balance": 5,
            "amenities": 25,
            "amenities_usage": 20,
            "amenities_balance": 5,
            "stability": 70,
            "crime": 0,
            "buildings": [
                {"id": 10, "type": "building_research_lab_1", "position": 2}
            ],
            "district_counts": {
                "district_city": 2,
                "district_generator": 1,
                "district_mining": 1,
                "district_farming": 1,
            },
            "production": {"energy": 10},
            "upkeep": {"energy": 3},
            "net_output": {"energy": 7},
        }
    )
    return state


def add_building_option(state: dict[str, object], building: str) -> None:
    state["player"]["planets"][0]["building_availability"][building] = {
        "id": building,
        "built": 0,
        "buildable": True,
        "authoritative": True,
        "requirements_met": True,
        "cost": {"minerals": 400},
    }


class PlanetApiTests(unittest.TestCase):
    def test_schema_is_exact_and_restricted_to_supported_actions(self) -> None:
        path = Path(__file__).parents[1] / "schemas" / "planet_action.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"], 1)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(len(schema["properties"]["action"]["oneOf"]), 3)

    def test_legal_actions_are_exact_validator_inputs(self) -> None:
        actions = legal_planet_actions(api_observation(), 4)
        self.assertIn(
            {"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_research_lab_1"},
            actions,
        )
        self.assertIn(
            {"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"},
            actions,
        )
        self.assertEqual(actions[-1], {"type": "WAIT", "months": 1})
        self.assertNotIn("building_cheat", str(actions))

    def test_normalized_state_is_explicit_about_unknown_capabilities(self) -> None:
        snapshot = normalize_planet(api_observation(), 4)
        planet = snapshot["planet"]
        self.assertEqual(planet["id"], 4)
        self.assertEqual(planet["population"]["available_jobs"]["value"], 3)
        self.assertFalse(planet["blockers"]["known"])
        self.assertFalse(planet["available_decisions"]["known"])
        self.assertEqual(
            planet["buildings"]["possible_upgrades"][0]["target_building"],
            "building_research_lab_2",
        )
        self.assertFalse(planet["buildings"]["possible_upgrades"][0]["supported"])

    def test_envelope_rejects_stale_unknown_and_extra_fields(self) -> None:
        state = api_observation()
        request = {
            "schema": 1,
            "observation_date": state["save"]["date"],
            "state_fingerprint": observation_fingerprint(state),
            "action": {"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"},
        }
        self.assertEqual(validate_planet_action_envelope(request, state).type, "BUILD_DISTRICT")

        stale = dict(request, state_fingerprint="0" * 64)
        with self.assertRaisesRegex(PlanetApiError, "stale"):
            validate_planet_action_envelope(stale, state)
        with self.assertRaisesRegex(PlanetApiError, "unknown field"):
            validate_planet_action_envelope(dict(request, console_command="yes"), state)
        unsupported = dict(request)
        unsupported["action"] = {"type": "DEMOLISH_BUILDING", "planet_id": 4}
        with self.assertRaisesRegex(PlanetApiError, "unsupported planetary action"):
            validate_planet_action_envelope(unsupported, state)

    def test_validated_request_becomes_existing_adapter_decision(self) -> None:
        state = api_observation()
        action = {"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_mining"}
        request = {
            "schema": 1,
            "observation_date": state["save"]["date"],
            "state_fingerprint": observation_fingerprint(state),
            "action": action,
        }
        decision = make_planet_decision(request, state)
        self.assertEqual(decision["actions"], [action])
        self.assertEqual(decision["policy"], "planet_api_request_v1")
        self.assertEqual(decision["execution"]["status"], "not_executed")
        self.assertEqual(decision["state_fingerprint"], request["state_fingerprint"])

    def test_governor_addresses_amenities_then_housing(self) -> None:
        state = api_observation()
        add_building_option(state, "building_holo_theatres")
        state["player"]["planets"][0]["amenities_balance"] = -8
        governor = DeterministicPlanetGovernor()
        self.assertEqual(
            governor.propose_for_planet(state, 4),
            {"type": "BUILD_BUILDING", "planet_id": 4, "building": "building_holo_theatres"},
        )

        state["player"]["planets"][0]["building_availability"]["building_holo_theatres"]["buildable"] = False
        state["player"]["planets"][0]["housing_balance"] = -4
        self.assertEqual(
            governor.propose_for_planet(state, 4),
            {"type": "BUILD_DISTRICT", "planet_id": 4, "district": "district_city"},
        )

    def test_governor_waits_for_queue_or_reserve(self) -> None:
        state = api_observation()
        state["player"]["planets"][0]["population"]["unemployed"] = 4
        state["player"]["resources"]["minerals"] = 600
        self.assertEqual(
            DeterministicPlanetGovernor().propose_for_planet(state, 4),
            {"type": "WAIT", "months": 1},
        )
        state["player"]["resources"]["minerals"] = 1200
        state["player"]["planets"][0]["construction_queue"].update(
            {"active": True, "safe_to_build": False, "details": {"item": "district_city"}}
        )
        self.assertEqual(
            DeterministicPlanetGovernor().propose_for_planet(state, 4),
            {"type": "WAIT", "months": 1},
        )


if __name__ == "__main__":
    unittest.main()
