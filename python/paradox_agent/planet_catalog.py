"""Version-pinned planetary construction catalog for Stellaris 4.4.6.

The catalog is intentionally small.  Each entry was extracted from the installed
4.4.6 game definitions named in ``definition_file``.  The observation mod still
evaluates the game's planet/country triggers; this module supplies stable IDs,
costs, prerequisites, upgrade relationships, and planner metadata only.

Adding an entry here is not sufficient to expose it.  A supported building must
also have a matching authoritative ``paradox_agent_can_build_*`` observation
variable and remain visually distinguishable by its localized name.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


SUPPORTED_GAME_VERSION = "Pegasus v4.4.6"


@dataclass(frozen=True)
class DistrictDefinition:
    id: str
    cost: Mapping[str, int]
    category: str
    definition_file: str


@dataclass(frozen=True)
class BuildingDefinition:
    id: str
    cost: Mapping[str, int]
    category: str
    prerequisites: tuple[str, ...]
    upgrades: tuple[str, ...]
    definition_file: str
    policy_role: str


def _cost(**resources: int) -> Mapping[str, int]:
    return MappingProxyType(resources)


# These are all of the ordinary, directly buildable districts on a standard
# 4.4.6 planet.  Industry is provided by district zones in 4.4.6, not by the
# pre-4.4 ``district_industrial`` action.
DISTRICTS: Mapping[str, DistrictDefinition] = MappingProxyType(
    {
        "district_city": DistrictDefinition(
            "district_city", _cost(minerals=500), "urban", "00_urban_districts.txt"
        ),
        "district_generator": DistrictDefinition(
            "district_generator", _cost(minerals=300), "rural", "02_rural_districts.txt"
        ),
        "district_mining": DistrictDefinition(
            "district_mining", _cost(minerals=300), "rural", "02_rural_districts.txt"
        ),
        "district_farming": DistrictDefinition(
            "district_farming", _cost(minerals=300), "rural", "02_rural_districts.txt"
        ),
    }
)


# Safe initial building allow-list.  All entries have a static 400-mineral cost
# for a non-nomadic regular empire in the installed definitions.  The
# observation effect explicitly excludes nomadic/wilderness empires so the
# nomadic cost-switching inline script can never invalidate this cost model.
BUILDINGS: Mapping[str, BuildingDefinition] = MappingProxyType(
    {
        "building_research_lab_1": BuildingDefinition(
            "building_research_lab_1",
            _cost(minerals=400),
            "research",
            ("tech_basic_science_lab_1",),
            ("building_research_lab_2",),
            "05_research_buildings.txt",
            "research",
        ),
        "building_holo_theatres": BuildingDefinition(
            "building_holo_theatres",
            _cost(minerals=400),
            "amenity",
            ("tech_holo_entertainment",),
            ("building_hyper_entertainment_forum",),
            "07_amenity_buildings.txt",
            "amenities",
        ),
        "building_foundry_1": BuildingDefinition(
            "building_foundry_1",
            _cost(minerals=400),
            "manufacturing",
            ("tech_basic_industry",),
            ("building_foundry_2",),
            "04_manufacturing_buildings.txt",
            "alloys",
        ),
        "building_factory_1": BuildingDefinition(
            "building_factory_1",
            _cost(minerals=400),
            "manufacturing",
            ("tech_basic_industry",),
            ("building_factory_2",),
            "04_manufacturing_buildings.txt",
            "consumer_goods",
        ),
        "building_bureaucratic_1": BuildingDefinition(
            "building_bureaucratic_1",
            _cost(minerals=400),
            "unity",
            ("tech_planetary_government",),
            (),
            "08_unity_buildings.txt",
            "unity",
        ),
    }
)


DISTRICT_TYPES = tuple(DISTRICTS)
BUILDING_TYPES = tuple(BUILDINGS)
DISTRICT_BASE_COSTS = {
    identifier: dict(definition.cost) for identifier, definition in DISTRICTS.items()
}
BUILDING_BASE_COSTS = {
    identifier: dict(definition.cost) for identifier, definition in BUILDINGS.items()
}

