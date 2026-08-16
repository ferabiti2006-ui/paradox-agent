"""Read a Stellaris .sav and emit a player-only structured observation."""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .planet_catalog import (
    BUILDINGS,
    BUILDING_BASE_COSTS,
    BUILDING_TYPES,
    DISTRICTS,
    DISTRICT_BASE_COSTS,
    DISTRICT_TYPES,
    SUPPORTED_GAME_VERSION,
)
from .clausewitz import PdxObject, Value, parse_clausewitz


UINT32_NONE = 4_294_967_295
RESEARCH_AREAS = ("physics", "society", "engineering")


def _object(value: Value | None) -> PdxObject | None:
    return value if isinstance(value, PdxObject) else None


def _number(value: Value | None, default: int | float = 0) -> int | float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _integer_values(value: Value | None) -> list[int]:
    block = _object(value)
    if block is None:
        return []
    return [item for item in block.values if isinstance(item, int) and not isinstance(item, bool)]


def _indexed(value: Value | None) -> dict[int, PdxObject]:
    block = _object(value)
    if block is None:
        return {}
    result: dict[int, PdxObject] = {}
    for key, item in block.entries:
        if key.isdigit() and isinstance(item, PdxObject):
            result[int(key)] = item
    return result


def _name_key(block: PdxObject) -> str | None:
    name = block.get("name")
    if isinstance(name, str):
        return name
    name_block = _object(name)
    if name_block is not None:
        key = name_block.get("key")
        if isinstance(key, str):
            return key
    return None


def _plain_object(block: PdxObject | None) -> dict[str, int | float | str | bool]:
    if block is None:
        return {}
    result: dict[str, int | float | str | bool] = {}
    for key, value in block.entries:
        if not isinstance(value, PdxObject):
            result[key] = value
    return result


def _json_value(value: Value) -> Any:
    if not isinstance(value, PdxObject):
        return value
    result: dict[str, Any] = {}
    repeated: set[str] = set()
    for key, item in value.entries:
        converted = _json_value(item)
        if key in result:
            if key not in repeated:
                result[key] = [result[key]]
                repeated.add(key)
            result[key].append(converted)
        else:
            result[key] = converted
    if value.values:
        result["_values"] = [_json_value(item) for item in value.values]
    return result


def _player_country_id(root: PdxObject) -> int:
    player = root.object("player")
    if player is None:
        raise ValueError("Save has no player block")
    candidates = [item for item in player.values if isinstance(item, PdxObject)]
    if not candidates:
        candidates = [item for _, item in player.entries if isinstance(item, PdxObject)]
    for candidate in candidates:
        country = candidate.get("country")
        if isinstance(country, int):
            return country
    raise ValueError("Could not determine the player country ID")


def _resource_sum(block: PdxObject | None) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)

    def visit(value: Value) -> None:
        if not isinstance(value, PdxObject):
            return
        for key, item in value.entries:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                totals[key] += float(item)
            else:
                visit(item)

    if block is not None:
        visit(block)
    return {key: round(value, 5) for key, value in sorted(totals.items()) if value}


def _country_resources(country: PdxObject) -> dict[str, int | float | str | bool]:
    modules = country.object("modules")
    economy = modules.object("standard_economy_module") if modules else None
    return _plain_object(economy.object("resources") if economy else None)


def _monthly_balance(country: PdxObject) -> dict[str, float]:
    budget = country.object("budget")
    current = budget.object("current_month") if budget else None
    return _resource_sum(current.object("balance") if current else None)


def _research(country: PdxObject) -> dict[str, Any]:
    status = country.object("tech_status")
    researched: list[dict[str, int | str]] = []
    pending_technology: str | None = None
    if status is not None:
        for key, value in status.entries:
            if key == "technology" and isinstance(value, str):
                pending_technology = value
            elif key == "level" and pending_technology is not None and isinstance(value, int):
                researched.append({"id": pending_technology, "level": value})
                pending_technology = None

    alternatives: dict[str, list[str]] = {}
    alternatives_block = status.object("alternatives") if status else None
    for area in RESEARCH_AREAS:
        area_block = alternatives_block.object(area) if alternatives_block else None
        alternatives[area] = [item for item in (area_block.values if area_block else []) if isinstance(item, str)]

    stored: dict[str, int | float] = {}
    stored_block = status.object("stored_techpoints") if status else None
    if stored_block:
        numbers = [item for item in stored_block.values if isinstance(item, (int, float)) and not isinstance(item, bool)]
        stored = {area: numbers[index] for index, area in enumerate(RESEARCH_AREAS) if index < len(numbers)}

    queues: dict[str, list[dict[str, str]]] = {}
    active: dict[str, str | None] = {}
    for area in RESEARCH_AREAS:
        queue_block = status.object(f"{area}_queue") if status else None
        queue: list[dict[str, str]] = []
        if queue_block is not None:
            candidates = [item for item in queue_block.values if isinstance(item, PdxObject)]
            candidates.extend(item for _, item in queue_block.entries if isinstance(item, PdxObject))
            for item in candidates:
                technology_id = item.get("technology")
                if not isinstance(technology_id, str):
                    continue
                row = {"technology_id": technology_id}
                selected_on = item.get("date")
                if isinstance(selected_on, str):
                    row["selected_on"] = selected_on
                queue.append(row)
        queues[area] = queue
        active[area] = queue[0]["technology_id"] if queue else None

    return {
        "researched": researched,
        "active": active,
        "queues": queues,
        "alternatives": alternatives,
        "stored_points": stored,
        "auto_research": {
            area: bool(status.get(f"auto_researching_{area}", False)) if status else False
            for area in RESEARCH_AREAS
        },
    }


def _authoritative_integer_variable(block: PdxObject, name: str) -> int | None:
    variables = block.object("variables")
    value = variables.get(name) if variables else None
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ):
        rounded = int(value)
        if value == rounded:
            return rounded
    return None


def _authoritative_fixed_point_variable(block: PdxObject, name: str) -> float | None:
    """Read trigger values persisted by Stellaris in hundredths of one unit."""

    variables = block.object("variables")
    value = variables.get(name) if variables else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value) / 100, 5)
    return None


def _queue_for_planet(
    queues: list[dict[str, Any]], queue_id: Any, planet_id: int
) -> dict[str, Any]:
    matches = [
        queue
        for queue in queues
        if queue.get("id") == queue_id
        and queue.get("type") == "planet"
        and queue.get("location_id") == planet_id
    ]
    if len(matches) != 1:
        return {
            "id": queue_id,
            "known": False,
            "active": None,
            "safe_to_build": False,
            "details": None,
        }
    queue = matches[0]
    details = queue.get("details")
    empty = isinstance(details, dict) and not details
    return {
        "id": queue_id,
        "known": True,
        "active": not empty,
        "safe_to_build": empty,
        "details": details,
    }


def _planet_observations(
    root: PdxObject,
    country: PdxObject,
    player_id: int,
    construction_queues: list[dict[str, Any]],
    researched_technology_ids: set[str],
    *,
    supported_cost_version: bool,
) -> list[dict[str, Any]]:
    owned_colonies = set(_integer_values(country.get("owned_planets")))
    planets_container = root.object("planets")
    planets = _indexed(planets_container.get("planet") if planets_container else None)
    colonies = _indexed(root.get("colony"))
    buildings = _indexed(root.get("buildings"))
    districts = _indexed(root.get("districts"))
    observations: list[dict[str, Any]] = []

    for planet_id, planet in planets.items():
        colony_id = planet.get("colony")
        if not isinstance(colony_id, int) or colony_id not in owned_colonies:
            continue
        if planet.get("owner") != player_id:
            continue
        colony = colonies.get(colony_id)
        if colony is None:
            continue

        building_rows = []
        for building_id in _integer_values(colony.get("buildings_cache")):
            building = buildings.get(building_id)
            if building is not None:
                building_rows.append({
                    "id": building_id,
                    "type": building.get("type"),
                    "position": building.get("position"),
                })
        building_counts = {building_type: 0 for building_type in BUILDING_TYPES}
        for row in building_rows:
            building_type = row.get("type")
            if building_type in building_counts:
                building_counts[building_type] += 1
        district_rows = []
        for district_id in _integer_values(colony.get("districts")):
            district = districts.get(district_id)
            if district is not None:
                district_rows.append({
                    "id": district_id,
                    "type": district.get("type"),
                    "level": district.get("level", 1),
                })

        district_counts = {district_type: 0 for district_type in DISTRICT_TYPES}
        for row in district_rows:
            district_type = row.get("type")
            level = row.get("level")
            if district_type in district_counts and isinstance(level, int) and not isinstance(level, bool):
                district_counts[district_type] += level
        used_districts = sum(
            row["level"]
            for row in district_rows
            if isinstance(row.get("level"), int) and not isinstance(row.get("level"), bool)
        )
        available_districts = _authoritative_integer_variable(
            planet, "paradox_agent_free_district_slots"
        )
        district_availability: dict[str, dict[str, Any]] = {}
        standard_district_cost_model = _authoritative_integer_variable(
            planet, "paradox_agent_standard_district_cost_model"
        )
        for district_type in DISTRICT_TYPES:
            available = _authoritative_integer_variable(
                planet, f"paradox_agent_free_{district_type}"
            )
            cost = DISTRICT_BASE_COSTS[district_type] if supported_cost_version else None
            definition = DISTRICTS[district_type]
            district_availability[district_type] = {
                "id": district_type,
                "category": definition.category,
                "built": district_counts[district_type],
                "available": available,
                "cap": district_counts[district_type] + available
                if available is not None
                else None,
                "buildable": available is not None
                and available > 0
                and cost is not None
                and standard_district_cost_model == 1,
                "authoritative": available is not None
                and cost is not None
                and standard_district_cost_model == 1,
                "cost": dict(cost) if cost is not None else None,
                "cost_basis": "installed_definition_manifest_4.4.6"
                if cost is not None
                else "unsupported_game_version",
                "definition_file": definition.definition_file,
                "cost_model": "ordinary_non_wooden_planet"
                if standard_district_cost_model == 1
                else "unsupported_or_unknown",
            }

        available_building_slots = _authoritative_integer_variable(
            planet, "paradox_agent_free_building_slots"
        )
        building_availability: dict[str, dict[str, Any]] = {}
        for building_type in BUILDING_TYPES:
            can_build = _authoritative_integer_variable(
                planet, f"paradox_agent_can_build_{building_type}"
            )
            cost = BUILDING_BASE_COSTS[building_type] if supported_cost_version else None
            authoritative = (
                available_building_slots is not None
                and can_build is not None
                and cost is not None
            )
            definition = BUILDINGS[building_type]
            building_availability[building_type] = {
                "id": building_type,
                "category": definition.category,
                "policy_role": definition.policy_role,
                "built": building_counts[building_type],
                "buildable": authoritative
                and available_building_slots > 0
                and can_build == 1,
                "authoritative": authoritative,
                "cost": dict(cost) if cost is not None else None,
                "cost_basis": "installed_definition_manifest_4.4.6"
                if cost is not None
                else "unsupported_game_version",
                "prerequisites": list(definition.prerequisites),
                "requirements_met": all(
                    technology in researched_technology_ids
                    for technology in definition.prerequisites
                ),
                "upgrades": list(definition.upgrades),
                "definition_file": definition.definition_file,
            }

        queue_id = planet.get("build_queue")
        planet_queue = _queue_for_planet(construction_queues, queue_id, planet_id)

        raw_pops = _number(colony.get("num_sapient_pops"))
        raw_amenities = _number(colony.get("amenities"))
        raw_amenities_usage = _number(colony.get("amenities_usage"))
        raw_housing = _number(colony.get("total_housing"))
        raw_housing_usage = _number(colony.get("housing_usage"))
        unemployed = _authoritative_fixed_point_variable(
            planet, "paradox_agent_unemployed"
        )
        free_jobs = _authoritative_fixed_point_variable(planet, "paradox_agent_free_jobs")
        free_housing = _authoritative_fixed_point_variable(
            planet, "paradox_agent_free_housing"
        )
        free_amenities = _authoritative_fixed_point_variable(
            planet, "paradox_agent_free_amenities"
        )
        observations.append({
            "id": planet_id,
            "colony_id": colony_id,
            "owner_id": player_id,
            "name": _name_key(planet),
            "name_key": _name_key(planet),
            "class": planet.get("planet_class"),
            "size": planet.get("planet_size"),
            "designation": colony.get("final_designation"),
            "stability": colony.get("stability"),
            "crime": colony.get("crime"),
            "pops": raw_pops / 100,
            "pops_raw": raw_pops,
            "population": {
                "sapient": raw_pops / 100,
                "unemployed": unemployed,
                "available_jobs": free_jobs,
                "authoritative": unemployed is not None and free_jobs is not None,
            },
            "amenities": raw_amenities / 100,
            "amenities_usage": raw_amenities_usage / 100,
            "housing": raw_housing / 100,
            "housing_usage": raw_housing_usage / 100,
            "housing_balance": free_housing,
            "amenities_balance": free_amenities,
            "buildings": building_rows,
            "building_counts": building_counts,
            "building_capacity": {
                "used": len(building_rows),
                "available": available_building_slots,
                "maximum": len(building_rows) + available_building_slots
                if available_building_slots is not None
                else None,
                "authoritative": available_building_slots is not None,
            },
            "building_availability": building_availability,
            "districts": district_rows,
            "district_counts": district_counts,
            "district_capacity": {
                "used": used_districts,
                "available": available_districts,
                "maximum": used_districts + available_districts
                if available_districts is not None
                else None,
                "authoritative": available_districts is not None,
            },
            "district_availability": district_availability,
            "production": _plain_object(colony.object("produces")),
            "upkeep": _plain_object(colony.object("upkeep")),
            "net_output": _plain_object(colony.object("profits")),
            "planet_build_queue_id": planet.get("build_queue"),
            "construction_queue": planet_queue,
            "army_build_queue_id": colony.get("army_build_queue"),
        })
    return observations


def _owned_fleet_ids(country: PdxObject) -> list[int]:
    manager = country.object("fleets_manager")
    owned = manager.object("owned_fleets") if manager else None
    result: list[int] = []
    if owned:
        for item in owned.values:
            row = _object(item)
            fleet = row.get("fleet") if row else None
            if isinstance(fleet, int):
                result.append(fleet)
    return result


def _ship_summary(ship_id: int, ship: PdxObject) -> dict[str, Any]:
    implementation = ship.object("ship_design_implementation")
    coordinate = ship.object("coordinate")
    return {
        "id": ship_id,
        "name_key": _name_key(ship),
        "design_id": implementation.get("design") if implementation else None,
        "leader_id": ship.get("leader"),
        "system_id": coordinate.get("origin") if coordinate else None,
        "hitpoints": ship.get("hitpoints"),
        "max_hitpoints": ship.get("max_hitpoints"),
        "armor": ship.get("armor_hitpoints"),
        "max_armor": ship.get("max_armor_hitpoints"),
        "shields": ship.get("shield_hitpoints"),
        "max_shields": ship.get("max_shield_hitpoints"),
    }


def _fleet_observations(root: PdxObject, country: PdxObject) -> list[dict[str, Any]]:
    fleets = _indexed(root.get("fleet"))
    ships = _indexed(root.get("ships"))
    observations: list[dict[str, Any]] = []
    for fleet_id in _owned_fleet_ids(country):
        fleet = fleets.get(fleet_id)
        if fleet is None:
            continue
        settings = fleet.object("settings")
        if settings is None or settings.get("mobile") is not True:
            continue
        movement = fleet.object("movement_manager")
        coordinate = movement.object("coordinate") if movement else None
        ship_ids = _integer_values(fleet.get("ships"))
        observations.append({
            "id": fleet_id,
            "name_key": _name_key(fleet),
            "class": fleet.get("ship_class"),
            "civilian": settings.get("civilian") is True,
            "system_id": coordinate.get("origin") if coordinate else None,
            "movement_state": movement.get("state") if movement else None,
            "military_power": fleet.get("military_power"),
            "hitpoints": fleet.get("hit_points"),
            "ships": [_ship_summary(ship_id, ships[ship_id]) for ship_id in ship_ids if ship_id in ships],
        })
    return observations


def _design_observations(root: PdxObject, country: PdxObject) -> list[dict[str, Any]]:
    collection = country.object("ship_design_collection")
    design_ids = _integer_values(collection.get("ship_design") if collection else None)
    designs = _indexed(root.get("ship_design"))
    result = []
    for design_id in design_ids:
        design = designs.get(design_id)
        if design is None:
            continue
        growth = design.object("growth_stages")
        stages = [item for item in (growth.values if growth else []) if isinstance(item, PdxObject)]
        stage = stages[-1] if stages else None
        sections = stage.get_all("section") if stage else []
        components: list[dict[str, Any]] = []
        section_templates: list[str] = []
        for section_value in sections:
            section = _object(section_value)
            if section is None:
                continue
            template = section.get("template")
            if isinstance(template, str):
                section_templates.append(template)
            for component_value in section.get_all("component"):
                component = _object(component_value)
                if component:
                    components.append({"slot": component.get("slot"), "template": component.get("template")})
        required = [item for item in (stage.get_all("required_component") if stage else []) if isinstance(item, str)]
        result.append({
            "id": design_id,
            "name_key": _name_key(design),
            "ship_size": stage.get("ship_size") if stage else None,
            "auto_generated": design.get("auto_gen_design") is True,
            "sections": section_templates,
            "components": components,
            "required_components": required,
        })
    return result


def _construction_queues(root: PdxObject, player_id: int) -> list[dict[str, Any]]:
    construction = root.object("construction")
    manager = construction.object("queue_mgr") if construction else None
    queues = _indexed(manager.get("queues") if manager else None)
    item_manager = construction.object("item_mgr") if construction else None
    construction_items = _indexed(item_manager.get("items") if item_manager else None)
    result = []
    for queue_id, queue in queues.items():
        if queue.get("owner") != player_id or queue.get("disabled") is True:
            continue
        location = queue.object("location")
        details = {
            key: _json_value(value)
            for key, value in queue.entries
            if key not in {"owner", "location", "simultaneous", "type", "disabled"}
        }
        item_ids = _integer_values(queue.get("items"))
        if item_ids:
            details["items"] = [
                {"id": item_id, **_json_value(construction_items[item_id])}
                for item_id in item_ids
                if item_id in construction_items
            ]
        result.append({
            "id": queue_id,
            "type": queue.get("type"),
            "location_type": location.get("type") if location else None,
            "location_id": location.get("id") if location else None,
            "disabled": queue.get("disabled") is True,
            "details": details,
        })
    return result


def parse_save(path: str | Path) -> dict[str, Any]:
    """Parse one Stellaris save into a player-only observation dictionary."""

    save_path = Path(path)
    with zipfile.ZipFile(save_path) as archive:
        meta_text = archive.read("meta").decode("utf-8-sig", errors="replace")
        game_text = archive.read("gamestate").decode("utf-8-sig", errors="replace")
    meta = parse_clausewitz(meta_text)
    root = parse_clausewitz(game_text)
    player_id = _player_country_id(root)
    countries = _indexed(root.get("country"))
    country = countries.get(player_id)
    if country is None:
        raise ValueError(f"Player country {player_id} is missing from the save")

    construction_queues = _construction_queues(root, player_id)
    research = _research(country)
    researched_technology_ids = {
        row["id"]
        for row in research["researched"]
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    version = meta.get("version", root.get("version"))
    planets = _planet_observations(
        root,
        country,
        player_id,
        construction_queues,
        researched_technology_ids,
        supported_cost_version=version == SUPPORTED_GAME_VERSION,
    )
    capital_colony_id = country.get("capital")
    capital_planet_id = next(
        (planet["id"] for planet in planets if planet["colony_id"] == capital_colony_id),
        None,
    )

    observation = {
        "schema": 1,
        "source": "stellaris_save",
        "save": {
            "file_name": save_path.name,
            "version": version,
            "date": meta.get("date", root.get("date")),
            "name": meta.get("name", root.get("name")),
            "mods": [item for item in (meta.object("mods").values if meta.object("mods") else []) if isinstance(item, str)],
        },
        "player": {
            "country_id": player_id,
            "name_key": _name_key(country),
            "capital_colony_id": capital_colony_id,
            "capital_planet_id": capital_planet_id,
            "starting_system_id": country.get("starting_system"),
            "resources": _country_resources(country),
            "monthly_balance": _monthly_balance(country),
            "research": research,
            "planets": planets,
            "fleets": _fleet_observations(root, country),
            "ship_designs": _design_observations(root, country),
            "construction_queues": construction_queues,
        },
        "visibility": {
            "policy": "player_owned_only",
            "foreign_countries_included": False,
        },
    }
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path, help="Path to a Stellaris .sav file")
    parser.add_argument("--output", "-o", type=Path, help="Write JSON here instead of stdout")
    args = parser.parse_args()
    observation = parse_save(args.save)
    rendered = json.dumps(observation, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
