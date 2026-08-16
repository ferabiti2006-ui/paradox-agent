"""Verify the pinned planet catalog against installed Stellaris 4.4.6 files."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from python.paradox_agent.clausewitz import PdxObject, parse_clausewitz
from python.paradox_agent.planet_catalog import BUILDINGS, DISTRICTS
from python.paradox_agent.visual_skills import StellarisLocalizer, VisualSkillError


def find_game() -> Path:
    candidates = []
    configured = os.environ.get("STELLARIS_GAME_DIR")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        (
            Path(r"C:\Program Files (x86)\Steam\steamapps\common\Stellaris"),
            Path(r"C:\Program Files\Steam\steamapps\common\Stellaris"),
        )
    )
    for candidate in candidates:
        if (candidate / "common" / "buildings").is_dir():
            return candidate
    raise FileNotFoundError("could not locate an installed Stellaris game directory")


def _variables(root: PdxObject) -> dict[str, int | float]:
    return {
        key: value
        for key, value in root.entries
        if key.startswith("@")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }


def _resolve(value: Any, variables: dict[str, int | float]) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return variables.get(value)
    return None


def _definition(path: Path, identifier: str) -> tuple[PdxObject, dict[str, int | float]]:
    root = parse_clausewitz(path.read_text(encoding="utf-8-sig", errors="replace"))
    value = root.get(identifier)
    if not isinstance(value, PdxObject):
        raise ValueError(f"{identifier} is missing from {path}")
    return value, _variables(root)


def validate_catalog(game: Path) -> list[str]:
    errors: list[str] = []
    common = game / "common"
    global_variables: dict[str, int | float] = {}
    for path in (common / "scripted_variables").glob("*.txt"):
        root = parse_clausewitz(path.read_text(encoding="utf-8-sig", errors="replace"))
        global_variables.update(_variables(root))

    for identifier, expected in DISTRICTS.items():
        path = common / "districts" / expected.definition_file
        try:
            definition, local_variables = _definition(path, identifier)
            variables = {**global_variables, **local_variables}
            resources = definition.object("resources")
            costs = resources.get_all("cost") if resources else []
            mineral_costs = []
            for cost in costs:
                if isinstance(cost, PdxObject):
                    resolved = _resolve(cost.get("minerals"), variables)
                    if resolved is not None and resolved > 0:
                        mineral_costs.append(resolved)
            actual = int(mineral_costs[0]) if mineral_costs else None
            if actual != expected.cost.get("minerals"):
                errors.append(
                    f"{identifier}: catalog cost {dict(expected.cost)!r} != installed mineral cost {actual!r}"
                )
        except (OSError, ValueError) as error:
            errors.append(str(error))

    for identifier, expected in BUILDINGS.items():
        path = common / "buildings" / expected.definition_file
        try:
            definition, local_variables = _definition(path, identifier)
            variables = {**global_variables, **local_variables}
            resources = definition.object("resources")
            inline_scripts = resources.get_all("inline_script") if resources else []
            ordinary_cost = None
            for inline in inline_scripts:
                if not isinstance(inline, PdxObject):
                    continue
                if inline.get("script") != "buildings/nomadic_cost_switcher":
                    continue
                if inline.get("REGULAR_RESOURCE") != "minerals":
                    continue
                ordinary_cost = _resolve(inline.get("COST"), variables)
                break
            prerequisites = definition.object("prerequisites")
            upgrades = definition.object("upgrades")
            actual_prerequisites = tuple(
                value for value in (prerequisites.values if prerequisites else []) if isinstance(value, str)
            )
            actual_upgrades = tuple(
                value for value in (upgrades.values if upgrades else []) if isinstance(value, str)
            )
            if ordinary_cost != expected.cost.get("minerals"):
                errors.append(
                    f"{identifier}: catalog cost {dict(expected.cost)!r} != installed ordinary cost {ordinary_cost!r}"
                )
            if definition.get("category") != expected.category:
                errors.append(
                    f"{identifier}: category {expected.category!r} != installed {definition.get('category')!r}"
                )
            if actual_prerequisites != expected.prerequisites:
                errors.append(
                    f"{identifier}: prerequisites {expected.prerequisites!r} != installed {actual_prerequisites!r}"
                )
            if actual_upgrades != expected.upgrades:
                errors.append(
                    f"{identifier}: upgrades {expected.upgrades!r} != installed {actual_upgrades!r}"
                )
        except (OSError, ValueError) as error:
            errors.append(str(error))

    try:
        localizer = StellarisLocalizer(game)
        labels = {
            identifier: localizer.resolve(identifier)
            for identifier in (*DISTRICTS, *BUILDINGS)
        }
        duplicates = sorted(
            label for label in set(labels.values()) if list(labels.values()).count(label) > 1
        )
        if duplicates:
            errors.append(f"supported UI labels are not unique: {duplicates!r}")
    except VisualSkillError as error:
        errors.append(str(error))

    observation_path = (
        Path(__file__).resolve().parents[2]
        / "paradox-mod"
        / "paradox_agent_testbed"
        / "common"
        / "scripted_effects"
        / "paradox_agent_observation_effects.txt"
    )
    observation = observation_path.read_text(encoding="utf-8-sig")
    for identifier in BUILDINGS:
        variable = f"paradox_agent_can_build_{identifier}"
        if variable not in observation:
            errors.append(f"observation mod does not export {variable}")
    if "paradox_agent_standard_district_cost_model" not in observation:
        errors.append("observation mod does not guard the standard district cost model")
    return errors


def main() -> int:
    try:
        game = find_game()
    except FileNotFoundError as error:
        print(f"Game catalog validation failed: {error}")
        return 1
    errors = validate_catalog(game)
    if errors:
        print("Game catalog validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Game catalog validation passed against: {game}")
    print(f"Checked {len(DISTRICTS)} districts and {len(BUILDINGS)} buildings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
