"""Restricted action contract and state-aware validation for the controller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .planet_catalog import (
    BUILDING_TYPES,
    DISTRICT_TYPES,
)


ACTION_SCHEMA = 1
RESEARCH_AREAS = ("physics", "society", "engineering")
SUPPORTED_ACTIONS = ("WAIT", "CHOOSE_RESEARCH", "BUILD_DISTRICT", "BUILD_BUILDING")


class ActionValidationError(ValueError):
    """Raised when a proposed decision crosses the restricted action boundary."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class ValidatedAction:
    """One normalized action that passed syntax and observation checks."""

    type: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.parameters}


def _exact_keys(action: Mapping[str, Any], required: set[str]) -> list[str]:
    actual = set(action)
    errors = [f"missing field {key!r}" for key in sorted(required - actual)]
    errors.extend(f"unknown field {key!r}" for key in sorted(actual - required))
    return errors


def _research_alternatives(observation: Mapping[str, Any], area: str) -> list[str]:
    try:
        alternatives = observation["player"]["research"]["alternatives"][area]
    except (KeyError, TypeError):
        return []
    if not isinstance(alternatives, list):
        return []
    return [item for item in alternatives if isinstance(item, str)]


def _active_research(observation: Mapping[str, Any], area: str) -> str | None:
    try:
        active = observation["player"]["research"]["active"][area]
    except (KeyError, TypeError):
        return None
    return active if isinstance(active, str) and active else None


def _planet(observation: Mapping[str, Any], planet_id: Any) -> Mapping[str, Any] | None:
    try:
        planets = observation["player"]["planets"]
    except (KeyError, TypeError):
        return None
    if not isinstance(planets, list):
        return None
    for planet in planets:
        if isinstance(planet, Mapping) and planet.get("id") == planet_id:
            return planet
    return None


def _district_option(planet: Mapping[str, Any], district: str) -> Mapping[str, Any] | None:
    availability = planet.get("district_availability")
    if not isinstance(availability, Mapping):
        return None
    option = availability.get(district)
    return option if isinstance(option, Mapping) else None


def _building_option(planet: Mapping[str, Any], building: str) -> Mapping[str, Any] | None:
    availability = planet.get("building_availability")
    if not isinstance(availability, Mapping):
        return None
    option = availability.get(building)
    return option if isinstance(option, Mapping) else None


def _validate_resource_cost(
    item: str,
    cost: Any,
    player: Any,
) -> list[str]:
    errors: list[str] = []
    resources = player.get("resources") if isinstance(player, Mapping) else None
    if not isinstance(cost, Mapping) or not cost:
        return [f"resource cost for {item!r} is not established confidently"]
    if not isinstance(resources, Mapping):
        return ["player resource stockpiles are not established confidently"]
    for resource, amount in cost.items():
        stockpile = resources.get(resource)
        if (
            not isinstance(resource, str)
            or not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or amount < 0
        ):
            errors.append(f"resource cost for {item!r} is malformed")
            break
        if (
            not isinstance(stockpile, (int, float))
            or isinstance(stockpile, bool)
            or stockpile < amount
        ):
            errors.append(f"insufficient {resource} for {item!r}: requires {amount}")
    return errors


def _validate_build_district(
    proposed: Mapping[str, Any], observation: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    errors = _exact_keys(proposed, {"type", "planet_id", "district"})
    parameters: dict[str, Any] = {}
    planet_id = proposed.get("planet_id")
    district = proposed.get("district")
    if not isinstance(planet_id, int) or isinstance(planet_id, bool) or planet_id < 0:
        errors.append("planet_id must be a non-negative integer")
    else:
        parameters["planet_id"] = planet_id
    if district not in DISTRICT_TYPES:
        errors.append(f"district must be one of {DISTRICT_TYPES}")
    else:
        parameters["district"] = district

    planet = _planet(observation, planet_id) if isinstance(planet_id, int) else None
    if planet is None:
        errors.append(f"planet {planet_id!r} is not a known player colony")
        return errors, parameters

    player = observation.get("player")
    player_id = player.get("country_id") if isinstance(player, Mapping) else None
    if not isinstance(player_id, int) or planet.get("owner_id") != player_id:
        errors.append(f"planet {planet_id!r} is not owned by the player")

    capacity = planet.get("district_capacity")
    available = capacity.get("available") if isinstance(capacity, Mapping) else None
    if not isinstance(available, int):
        errors.append("district capacity is not established confidently")
    elif available <= 0:
        errors.append("planet has no available district capacity")

    queue = planet.get("construction_queue")
    if not isinstance(queue, Mapping) or queue.get("known") is not True:
        errors.append("planet construction queue is not established confidently")
    elif queue.get("safe_to_build") is not True:
        if isinstance(district, str) and district in str(queue.get("details", {})):
            errors.append("requested district is already represented in the construction queue")
        else:
            errors.append("planet construction queue is not empty and safe")

    if isinstance(district, str) and district in DISTRICT_TYPES:
        option = _district_option(planet, district)
        if option is None or option.get("authoritative") is not True:
            errors.append(f"availability for {district!r} is not established confidently")
        else:
            free = option.get("available")
            if not isinstance(free, int) or free <= 0 or option.get("buildable") is not True:
                errors.append(f"district {district!r} cannot legally be built on planet {planet_id!r}")
            errors.extend(_validate_resource_cost(district, option.get("cost"), player))
    return errors, parameters


def _validate_build_building(
    proposed: Mapping[str, Any], observation: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    errors = _exact_keys(proposed, {"type", "planet_id", "building"})
    parameters: dict[str, Any] = {}
    planet_id = proposed.get("planet_id")
    building = proposed.get("building")
    if not isinstance(planet_id, int) or isinstance(planet_id, bool) or planet_id < 0:
        errors.append("planet_id must be a non-negative integer")
    else:
        parameters["planet_id"] = planet_id
    if building not in BUILDING_TYPES:
        errors.append(f"building must be one of {BUILDING_TYPES}")
    else:
        parameters["building"] = building

    planet = _planet(observation, planet_id) if isinstance(planet_id, int) else None
    if planet is None:
        errors.append(f"planet {planet_id!r} is not a known player colony")
        return errors, parameters

    player = observation.get("player")
    player_id = player.get("country_id") if isinstance(player, Mapping) else None
    if not isinstance(player_id, int) or planet.get("owner_id") != player_id:
        errors.append(f"planet {planet_id!r} is not owned by the player")

    capacity = planet.get("building_capacity")
    available = capacity.get("available") if isinstance(capacity, Mapping) else None
    if not isinstance(available, int):
        errors.append("building capacity is not established confidently")
    elif available <= 0:
        errors.append("planet has no available building slots")

    queue = planet.get("construction_queue")
    if not isinstance(queue, Mapping) or queue.get("known") is not True:
        errors.append("planet construction queue is not established confidently")
    elif queue.get("safe_to_build") is not True:
        if isinstance(building, str) and building in str(queue.get("details", {})):
            errors.append("requested building is already represented in the construction queue")
        else:
            errors.append("planet construction queue is not empty and safe")

    if isinstance(building, str) and building in BUILDING_TYPES:
        option = _building_option(planet, building)
        if option is None or option.get("authoritative") is not True:
            errors.append(f"availability for {building!r} is not established confidently")
        else:
            if option.get("requirements_met") is False:
                missing = option.get("prerequisites")
                errors.append(
                    f"technology requirements for {building!r} are not met: {missing!r}"
                )
            if option.get("buildable") is not True:
                errors.append(f"building {building!r} cannot legally be built on planet {planet_id!r}")
            errors.extend(_validate_resource_cost(building, option.get("cost"), player))
    return errors, parameters


def validate_action(
    proposed: Mapping[str, Any], observation: Mapping[str, Any]
) -> ValidatedAction:
    """Validate one action against the strict V1 contract and current state."""

    action_type = proposed.get("type")
    if not isinstance(action_type, str):
        raise ActionValidationError(("field 'type' must be a string",))
    if action_type not in SUPPORTED_ACTIONS:
        raise ActionValidationError((f"unsupported action type {action_type!r}",))

    errors: list[str] = []
    parameters: dict[str, Any] = {}
    if action_type == "WAIT":
        errors.extend(_exact_keys(proposed, {"type", "months"}))
        months = proposed.get("months")
        if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 12:
            errors.append("WAIT months must be an integer from 1 through 12")
        else:
            parameters["months"] = months

    elif action_type == "CHOOSE_RESEARCH":
        errors.extend(_exact_keys(proposed, {"type", "area", "technology_id"}))
        area = proposed.get("area")
        technology_id = proposed.get("technology_id")
        if area not in RESEARCH_AREAS:
            errors.append(f"research area must be one of {RESEARCH_AREAS}")
        if not isinstance(technology_id, str) or not technology_id:
            errors.append("technology_id must be a non-empty string")
        elif area in RESEARCH_AREAS and technology_id not in _research_alternatives(observation, area):
            errors.append(f"technology {technology_id!r} is not an available {area} alternative")
        if area in RESEARCH_AREAS and _active_research(observation, area) is not None:
            errors.append(f"{area} research is already active")
        if area in RESEARCH_AREAS:
            parameters["area"] = area
        if isinstance(technology_id, str) and technology_id:
            parameters["technology_id"] = technology_id

    elif action_type == "BUILD_DISTRICT":
        district_errors, parameters = _validate_build_district(proposed, observation)
        errors.extend(district_errors)

    elif action_type == "BUILD_BUILDING":
        building_errors, parameters = _validate_build_building(proposed, observation)
        errors.extend(building_errors)

    if errors:
        raise ActionValidationError(errors)
    return ValidatedAction(action_type, parameters)


def validate_actions(
    proposed: Any, observation: Mapping[str, Any]
) -> list[ValidatedAction]:
    """Validate a decision's action list, including cross-action constraints."""

    if not isinstance(proposed, list) or not proposed:
        raise ActionValidationError(("actions must be a non-empty list",))
    if len(proposed) > len(RESEARCH_AREAS):
        raise ActionValidationError(("a V1 decision may contain at most three actions",))

    validated: list[ValidatedAction] = []
    errors: list[str] = []
    for index, action in enumerate(proposed):
        if not isinstance(action, Mapping):
            errors.append(f"actions[{index}] must be an object")
            continue
        try:
            validated.append(validate_action(action, observation))
        except ActionValidationError as error:
            errors.extend(f"actions[{index}]: {message}" for message in error.errors)

    types = [action.type for action in validated]
    if "WAIT" in types and len(validated) != 1:
        errors.append("WAIT cannot be combined with another action")
    if "BUILD_DISTRICT" in types and len(validated) != 1:
        errors.append("BUILD_DISTRICT must be the only action in a decision")
    if "BUILD_BUILDING" in types and len(validated) != 1:
        errors.append("BUILD_BUILDING must be the only action in a decision")
    areas = [action.parameters["area"] for action in validated if action.type == "CHOOSE_RESEARCH"]
    if len(areas) != len(set(areas)):
        errors.append("a decision cannot choose research twice for the same area")

    if errors:
        raise ActionValidationError(errors)
    return validated
