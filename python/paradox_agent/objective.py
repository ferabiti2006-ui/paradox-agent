"""Transparent V1 campaign objective used by deterministic controller policies."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ObjectiveWeights:
    economy: float = 0.40
    survival: float = 0.25
    technology: float = 0.20
    military: float = 0.10
    territory: float = 0.05


@dataclass(frozen=True)
class ObjectiveEvaluation:
    score: float
    components: dict[str, float]
    weights: dict[str, float]
    survival_constraint_satisfied: bool
    risk_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk_flags"] = list(self.risk_flags)
        return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _positive_saturation(value: float, scale: float) -> float:
    return 1.0 - math.exp(-max(0.0, value) / scale)


def evaluate_objective(
    observation: Mapping[str, Any], weights: ObjectiveWeights = ObjectiveWeights()
) -> ObjectiveEvaluation:
    """Convert one observation into bounded, auditable objective components."""

    player = _mapping(observation.get("player"))
    resources = _mapping(player.get("resources"))
    balance = _mapping(player.get("monthly_balance"))
    planets = player.get("planets") if isinstance(player.get("planets"), list) else []
    fleets = player.get("fleets") if isinstance(player.get("fleets"), list) else []
    research = _mapping(player.get("research"))
    researched = research.get("researched") if isinstance(research.get("researched"), list) else []

    economy_resources = ("energy", "minerals", "food", "consumer_goods", "alloys")
    income_scores = [0.5 + 0.5 * value / (abs(value) + 20.0) for value in (_number(balance.get(key)) for key in economy_resources)]
    economy = sum(income_scores) / len(income_scores)

    stability_values = [
        min(1.0, max(0.0, _number(_mapping(planet).get("stability")) / 100.0))
        for planet in planets
    ]
    stability = sum(stability_values) / len(stability_values) if stability_values else 0.0
    reserve = sum(_positive_saturation(_number(resources.get(key)), 500.0) for key in economy_resources) / len(economy_resources)
    survival = 0.65 * stability + 0.35 * reserve

    technology = _positive_saturation(float(len(researched)), 50.0)
    fleet_power = sum(_number(_mapping(fleet).get("military_power")) for fleet in fleets)
    military = _positive_saturation(fleet_power, 1_000.0)
    territory = _positive_saturation(float(len(planets)), 5.0)

    risk_flags: list[str] = []
    if not planets:
        risk_flags.append("no_owned_colonies")
    for key in economy_resources:
        if _number(resources.get(key)) < 100 and _number(balance.get(key)) < 0:
            risk_flags.append(f"critical_{key}_deficit")
    if stability_values and min(stability_values) < 0.40:
        risk_flags.append("colony_stability_below_40")

    components = {
        "economy": round(economy, 6),
        "survival": round(survival, 6),
        "technology": round(technology, 6),
        "military": round(military, 6),
        "territory": round(territory, 6),
    }
    weight_map = asdict(weights)
    score = sum(components[name] * weight_map[name] for name in components)
    return ObjectiveEvaluation(
        score=round(score, 6),
        components=components,
        weights=weight_map,
        survival_constraint_satisfied=not risk_flags,
        risk_flags=tuple(risk_flags),
    )
