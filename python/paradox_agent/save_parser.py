"""Read a Stellaris .sav and emit a player-only structured observation."""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

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

    return {
        "researched": researched,
        "alternatives": alternatives,
        "stored_points": stored,
        "auto_research": {
            area: bool(status.get(f"auto_researching_{area}", False)) if status else False
            for area in RESEARCH_AREAS
        },
    }


def _planet_observations(root: PdxObject, country: PdxObject, player_id: int) -> list[dict[str, Any]]:
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
        district_rows = []
        for district_id in _integer_values(colony.get("districts")):
            district = districts.get(district_id)
            if district is not None:
                district_rows.append({
                    "id": district_id,
                    "type": district.get("type"),
                    "level": district.get("level", 1),
                })

        raw_pops = _number(colony.get("num_sapient_pops"))
        raw_amenities = _number(colony.get("amenities"))
        raw_amenities_usage = _number(colony.get("amenities_usage"))
        raw_housing = _number(colony.get("total_housing"))
        raw_housing_usage = _number(colony.get("housing_usage"))
        observations.append({
            "id": planet_id,
            "colony_id": colony_id,
            "name_key": _name_key(planet),
            "class": planet.get("planet_class"),
            "size": planet.get("planet_size"),
            "designation": colony.get("final_designation"),
            "stability": colony.get("stability"),
            "crime": colony.get("crime"),
            "pops": raw_pops / 100,
            "pops_raw": raw_pops,
            "amenities": raw_amenities / 100,
            "amenities_usage": raw_amenities_usage / 100,
            "housing": raw_housing / 100,
            "housing_usage": raw_housing_usage / 100,
            "buildings": building_rows,
            "districts": district_rows,
            "production": _plain_object(colony.object("produces")),
            "upkeep": _plain_object(colony.object("upkeep")),
            "net_output": _plain_object(colony.object("profits")),
            "planet_build_queue_id": planet.get("build_queue"),
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

    planets = _planet_observations(root, country, player_id)
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
            "version": meta.get("version", root.get("version")),
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
            "research": _research(country),
            "planets": planets,
            "fleets": _fleet_observations(root, country),
            "ship_designs": _design_observations(root, country),
            "construction_queues": _construction_queues(root, player_id),
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

