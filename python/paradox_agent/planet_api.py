"""Normalized, versioned planetary API and deterministic test governor.

This is the boundary intended for a future planner.  It exposes no Clausewitz
objects and accepts only exact, fingerprint-bound envelopes containing actions
that the existing validator already understands.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .actions import (
    BUILDING_TYPES,
    DISTRICT_TYPES,
    ActionValidationError,
    ValidatedAction,
    validate_action,
)
from .controller import make_decision, observation_fingerprint, validate_observation
from .planet_catalog import BUILDINGS


PLANET_API_SCHEMA = 1
PLANET_ACTION_TYPES = ("WAIT", "BUILD_DISTRICT", "BUILD_BUILDING")


class PlanetApiError(ValueError):
    """Raised when a request crosses the normalized planetary API boundary."""


def _planet(observation: Mapping[str, Any], planet_id: int) -> Mapping[str, Any]:
    planets = observation["player"].get("planets", [])
    if isinstance(planets, list):
        matches = [
            row
            for row in planets
            if isinstance(row, Mapping) and row.get("id") == planet_id
        ]
        if len(matches) == 1:
            return matches[0]
    raise PlanetApiError(f"planet {planet_id!r} is not one unique player colony")


def legal_planet_actions(
    observation: Mapping[str, Any], planet_id: int, *, include_wait: bool = True
) -> list[dict[str, Any]]:
    """Return exact action objects accepted by the authoritative validator."""

    state = validate_observation(observation)
    _planet(state, planet_id)
    candidates: list[dict[str, Any]] = []
    for district in DISTRICT_TYPES:
        candidates.append(
            {"type": "BUILD_DISTRICT", "planet_id": planet_id, "district": district}
        )
    for building in BUILDING_TYPES:
        candidates.append(
            {"type": "BUILD_BUILDING", "planet_id": planet_id, "building": building}
        )

    legal: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            legal.append(validate_action(candidate, state).to_dict())
        except ActionValidationError:
            continue
    if include_wait:
        legal.append({"type": "WAIT", "months": 1})
    return legal


def _known_value(value: Any, *, authoritative: bool) -> dict[str, Any]:
    return {"value": value, "authoritative": authoritative and value is not None}


def normalize_planet(
    observation: Mapping[str, Any], planet_id: int
) -> dict[str, Any]:
    """Produce concise planner-facing state for one owned colony."""

    state = validate_observation(observation)
    planet = _planet(state, planet_id)
    population = planet.get("population")
    if not isinstance(population, Mapping):
        population = {}
    district_capacity = planet.get("district_capacity")
    building_capacity = planet.get("building_capacity")
    districts = planet.get("district_availability")
    buildings = planet.get("building_availability")
    existing_buildings = planet.get("buildings")

    upgrade_rows: list[dict[str, Any]] = []
    if isinstance(existing_buildings, list):
        for row in existing_buildings:
            if not isinstance(row, Mapping):
                continue
            building_id = row.get("type")
            definition = BUILDINGS.get(building_id) if isinstance(building_id, str) else None
            if definition is None:
                continue
            for target in definition.upgrades:
                upgrade_rows.append(
                    {
                        "slot": row.get("position"),
                        "current_building": building_id,
                        "target_building": target,
                        "supported": False,
                        "reason": "UPGRADE_BUILDING visual targeting is not calibrated",
                    }
                )

    return {
        "schema": PLANET_API_SCHEMA,
        "observation_date": state["save"]["date"],
        "state_fingerprint": observation_fingerprint(state),
        "planet": {
            "id": planet.get("id"),
            "colony_id": planet.get("colony_id"),
            "name_key": planet.get("name_key"),
            "planet_class": planet.get("class"),
            "size": planet.get("size"),
            "designation": planet.get("designation"),
            "population": {
                "sapient": planet.get("pops"),
                "unemployed": _known_value(
                    population.get("unemployed"),
                    authoritative=population.get("authoritative") is True,
                ),
                "available_jobs": _known_value(
                    population.get("available_jobs"),
                    authoritative=population.get("authoritative") is True,
                ),
            },
            "housing": {
                "total": planet.get("housing"),
                "used": planet.get("housing_usage"),
                "available": _known_value(
                    planet.get("housing_balance"),
                    authoritative=planet.get("housing_balance") is not None,
                ),
            },
            "amenities": {
                "total": planet.get("amenities"),
                "used": planet.get("amenities_usage"),
                "available": _known_value(
                    planet.get("amenities_balance"),
                    authoritative=planet.get("amenities_balance") is not None,
                ),
            },
            "stability": planet.get("stability"),
            "crime_or_deviancy": planet.get("crime"),
            "districts": {
                "capacity": district_capacity,
                "counts": planet.get("district_counts", {}),
                "options": districts if isinstance(districts, Mapping) else {},
            },
            "buildings": {
                "capacity": building_capacity,
                "existing": existing_buildings if isinstance(existing_buildings, list) else [],
                "options": buildings if isinstance(buildings, Mapping) else {},
                "possible_upgrades": upgrade_rows,
            },
            "construction_queue": planet.get("construction_queue"),
            "economy": {
                "production": planet.get("production", {}),
                "upkeep": planet.get("upkeep", {}),
                "net_output": planet.get("net_output", {}),
            },
            # These are explicit unknowns, not empty authoritative lists.  A
            # future parser may populate them without changing this API shape.
            "modifiers": {"known": False, "items": []},
            "blockers": {"known": False, "items": []},
            "available_decisions": {"known": False, "items": []},
            "legal_actions": legal_planet_actions(state, planet_id),
        },
    }


def normalize_planets(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Produce a normalized snapshot containing every owned physical colony."""

    state = validate_observation(observation)
    rows = []
    for planet in state["player"].get("planets", []):
        if isinstance(planet, Mapping) and isinstance(planet.get("id"), int):
            rows.append(normalize_planet(state, planet["id"])["planet"])
    return {
        "schema": PLANET_API_SCHEMA,
        "observation_date": state["save"]["date"],
        "state_fingerprint": observation_fingerprint(state),
        "planets": rows,
    }


def validate_planet_action_envelope(
    request: Mapping[str, Any], observation: Mapping[str, Any]
) -> ValidatedAction:
    """Validate a stale-resistant planner request with exact fields."""

    if not isinstance(request, Mapping):
        raise PlanetApiError("planet action request must be an object")
    required = {"schema", "observation_date", "state_fingerprint", "action"}
    actual = set(request)
    if actual != required:
        messages = [f"missing field {key!r}" for key in sorted(required - actual)]
        messages.extend(f"unknown field {key!r}" for key in sorted(actual - required))
        raise PlanetApiError("; ".join(messages))
    if request.get("schema") != PLANET_API_SCHEMA:
        raise PlanetApiError(f"schema must equal {PLANET_API_SCHEMA}")

    state = validate_observation(observation)
    if request.get("observation_date") != state["save"]["date"]:
        raise PlanetApiError("planet action request is stale: game date does not match")
    if request.get("state_fingerprint") != observation_fingerprint(state):
        raise PlanetApiError("planet action request is stale: state fingerprint does not match")
    action = request.get("action")
    if not isinstance(action, Mapping):
        raise PlanetApiError("action must be an object")
    if action.get("type") not in PLANET_ACTION_TYPES:
        raise PlanetApiError(f"unsupported planetary action {action.get('type')!r}")
    try:
        return validate_action(action, state)
    except ActionValidationError as error:
        raise PlanetApiError(str(error)) from error


class _RequestedPlanetPolicy:
    """Adapter from one validated API action to the existing decision envelope."""

    name = "planet_api_request_v1"

    def __init__(self, action: ValidatedAction) -> None:
        self.action = action

    def propose(
        self, observation: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], float, dict[str, float]]:
        del observation
        return [self.action.to_dict()], 1.0, {
            "economy": 0.0,
            "survival": 0.0,
            "technology": 0.0,
            "military": 0.0,
            "territory": 0.0,
        }


def make_planet_decision(
    request: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Create the existing adapter-ready decision from a validated API request."""

    action = validate_planet_action_envelope(request, observation)
    return make_decision(observation, _RequestedPlanetPolicy(action))  # type: ignore[arg-type]


@dataclass(frozen=True)
class GovernorConfig:
    mineral_reserve: int = 500
    severe_housing_shortage: int = -3
    severe_amenity_shortage: int = -5
    unemployment_threshold: int = 2


class DeterministicPlanetGovernor:
    """Conservative policy used to exercise the action layer, not optimize it."""

    name = "deterministic_planet_governor_v1"

    def __init__(self, config: GovernorConfig = GovernorConfig()) -> None:
        self.config = config

    @staticmethod
    def _find(
        actions: Sequence[Mapping[str, Any]], action_type: str, field: str, value: str
    ) -> dict[str, Any] | None:
        return next(
            (
                dict(action)
                for action in actions
                if action.get("type") == action_type and action.get(field) == value
            ),
            None,
        )

    def propose_for_planet(
        self, observation: Mapping[str, Any], planet_id: int
    ) -> dict[str, Any]:
        state = validate_observation(observation)
        snapshot = normalize_planet(state, planet_id)["planet"]
        actions = snapshot["legal_actions"]
        minerals = state["player"].get("resources", {}).get("minerals")
        if not isinstance(minerals, (int, float)) or isinstance(minerals, bool):
            return {"type": "WAIT", "months": 1}

        def affordable_with_reserve(candidate: Mapping[str, Any] | None) -> bool:
            if candidate is None:
                return False
            option_group = (
                snapshot["buildings"]["options"]
                if candidate["type"] == "BUILD_BUILDING"
                else snapshot["districts"]["options"]
            )
            identifier = candidate.get("building", candidate.get("district"))
            option = option_group.get(identifier, {}) if isinstance(option_group, Mapping) else {}
            cost = option.get("cost", {}) if isinstance(option, Mapping) else {}
            mineral_cost = cost.get("minerals") if isinstance(cost, Mapping) else None
            return isinstance(mineral_cost, (int, float)) and minerals - mineral_cost >= self.config.mineral_reserve

        amenities = snapshot["amenities"]["available"]
        if amenities["authoritative"] and amenities["value"] <= self.config.severe_amenity_shortage:
            action = self._find(actions, "BUILD_BUILDING", "building", "building_holo_theatres")
            if affordable_with_reserve(action):
                return action

        housing = snapshot["housing"]["available"]
        if housing["authoritative"] and housing["value"] <= self.config.severe_housing_shortage:
            action = self._find(actions, "BUILD_DISTRICT", "district", "district_city")
            if affordable_with_reserve(action):
                return action

        unemployed = snapshot["population"]["unemployed"]
        if unemployed["authoritative"] and unemployed["value"] >= self.config.unemployment_threshold:
            balances = state["player"].get("monthly_balance", {})
            district_order = (
                "district_generator",
                "district_mining",
                "district_farming",
            )
            resource_for = {
                "district_generator": "energy",
                "district_mining": "minerals",
                "district_farming": "food",
            }
            district_order = tuple(
                sorted(
                    district_order,
                    key=lambda district: (
                        balances.get(resource_for[district], 0)
                        if isinstance(balances, Mapping)
                        else 0,
                        district,
                    ),
                )
            )
            for district in district_order:
                action = self._find(actions, "BUILD_DISTRICT", "district", district)
                if affordable_with_reserve(action):
                    return action

        return {"type": "WAIT", "months": 1}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="Player-only save observation JSON")
    parser.add_argument("--planet-id", type=int, help="Emit only this owned planet")
    parser.add_argument("--govern", action="store_true", help="Also emit the governor's proposal")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    with args.state.open(encoding="utf-8-sig") as stream:
        state = json.load(stream)
    result = (
        normalize_planet(state, args.planet_id)
        if args.planet_id is not None
        else normalize_planets(state)
    )
    if args.govern:
        if args.planet_id is None:
            raise PlanetApiError("--govern requires --planet-id")
        result["governor_action"] = DeterministicPlanetGovernor().propose_for_planet(
            state, args.planet_id
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
