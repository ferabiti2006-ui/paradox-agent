"""Restricted, decision-only Stellaris controller with a deterministic V1 policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from .actions import (
    ACTION_SCHEMA,
    BUILDING_TYPES,
    DISTRICT_TYPES,
    RESEARCH_AREAS,
    ActionValidationError,
    validate_action,
    validate_actions,
)
from .objective import evaluate_objective
from .save_watcher import write_json_atomic


POLICY_NAME = "rule_based_v1"
TECH_KEYWORD_PRIORITY = (
    "research",
    "science",
    "physics",
    "society",
    "engineering",
    "eco",
    "mining",
    "industry",
    "alloy",
    "energy",
    "power",
    "farming",
    "unity",
    "armor",
    "laser",
    "fleet",
)


class ObservationError(ValueError):
    """Raised when controller input violates its observation boundary."""


def validate_observation(observation: Any) -> Mapping[str, Any]:
    """Require a save-parser observation with the player-only visibility policy."""

    if not isinstance(observation, Mapping):
        raise ObservationError("observation must be a JSON object")
    if observation.get("source") != "stellaris_save":
        raise ObservationError("controller requires source='stellaris_save'")
    visibility = observation.get("visibility")
    if not isinstance(visibility, Mapping):
        raise ObservationError("observation has no visibility policy")
    if visibility.get("policy") != "player_owned_only":
        raise ObservationError("controller accepts only player_owned_only observations")
    if visibility.get("foreign_countries_included") is not False:
        raise ObservationError("observation may contain foreign-country omniscient data")
    if not isinstance(observation.get("player"), Mapping):
        raise ObservationError("observation has no player object")
    save = observation.get("save")
    if not isinstance(save, Mapping) or not isinstance(save.get("date"), str):
        raise ObservationError("observation has no save date")
    return observation


def observation_fingerprint(observation: Mapping[str, Any]) -> str:
    """Return the canonical state identity shared with the game adapter."""

    encoded = json.dumps(observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _priority(technology_id: str) -> tuple[int, str]:
    lowered = technology_id.lower()
    for index, keyword in enumerate(TECH_KEYWORD_PRIORITY):
        if keyword in lowered:
            return index, technology_id
    return len(TECH_KEYWORD_PRIORITY), technology_id


class RuleBasedPolicy:
    """Deterministic baseline policy to test the controller without an LLM."""

    name = POLICY_NAME

    def propose(self, observation: Mapping[str, Any]) -> tuple[list[dict[str, Any]], float, dict[str, float]]:
        player = observation["player"]
        research = player.get("research", {}) if isinstance(player, Mapping) else {}
        alternatives = research.get("alternatives", {}) if isinstance(research, Mapping) else {}
        active = research.get("active", {}) if isinstance(research, Mapping) else {}
        actions: list[dict[str, Any]] = []
        for area in RESEARCH_AREAS:
            if isinstance(active, Mapping) and isinstance(active.get(area), str) and active[area]:
                continue
            choices = alternatives.get(area, []) if isinstance(alternatives, Mapping) else []
            legal_choices = [choice for choice in choices if isinstance(choice, str) and choice]
            if legal_choices:
                actions.append(
                    {
                        "type": "CHOOSE_RESEARCH",
                        "area": area,
                        "technology_id": min(legal_choices, key=_priority),
                    }
                )
        if actions:
            return actions, 0.90, {
                "economy": 0.05,
                "survival": 0.02,
                "technology": 0.40,
                "military": 0.02,
                "territory": 0.00,
            }
        planets = player.get("planets", []) if isinstance(player, Mapping) else []
        if isinstance(planets, list):
            candidates = sorted(
                (planet for planet in planets if isinstance(planet, Mapping)),
                key=lambda planet: planet.get("id")
                if isinstance(planet.get("id"), int)
                else sys.maxsize,
            )
            for planet in candidates:
                planet_id = planet.get("id")
                for building in BUILDING_TYPES:
                    proposal = {
                        "type": "BUILD_BUILDING",
                        "planet_id": planet_id,
                        "building": building,
                    }
                    try:
                        validate_action(proposal, observation)
                    except ActionValidationError:
                        continue
                    return [proposal], 0.95, {
                        "economy": 0.25,
                        "survival": 0.02,
                        "technology": 0.10,
                        "military": 0.00,
                        "territory": 0.00,
                    }
                for district in DISTRICT_TYPES:
                    proposal = {
                        "type": "BUILD_DISTRICT",
                        "planet_id": planet_id,
                        "district": district,
                    }
                    try:
                        validate_action(proposal, observation)
                    except ActionValidationError:
                        continue
                    return [proposal], 0.95, {
                        "economy": 0.20,
                        "survival": 0.02,
                        "technology": 0.00,
                        "military": 0.00,
                        "territory": 0.00,
                    }
        return [{"type": "WAIT", "months": 1}], 1.0, {
            "economy": 0.00,
            "survival": 0.00,
            "technology": 0.00,
            "military": 0.00,
            "territory": 0.00,
        }


def make_decision(
    observation: Mapping[str, Any], policy: RuleBasedPolicy | None = None
) -> dict[str, Any]:
    """Create and validate one non-executing controller decision."""

    state = validate_observation(observation)
    selected_policy = policy or RuleBasedPolicy()
    fingerprint = observation_fingerprint(state)
    proposed, confidence, expected_effect = selected_policy.propose(state)
    actions = [action.to_dict() for action in validate_actions(proposed, state)]
    identity_source = json.dumps(
        {"state": fingerprint, "policy": selected_policy.name, "actions": actions},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    decision_id = hashlib.sha256(identity_source).hexdigest()[:24]
    objective = evaluate_objective(state)
    return {
        "schema": ACTION_SCHEMA,
        "decision_id": decision_id,
        "observation_date": state["save"]["date"],
        "state_fingerprint": fingerprint,
        "manager": "routine",
        "policy": selected_policy.name,
        "objective": objective.to_dict(),
        "actions": actions,
        "confidence": confidence,
        "expected_effect": expected_effect,
        "execution": {
            "status": "not_executed",
            "reason": "game_action_adapter_not_enabled",
        },
    }


def process_state(
    state_path: str | Path,
    output_path: str | Path,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read current state, publish a validated decision, and append an audit record."""

    source = Path(state_path)
    with source.open(encoding="utf-8-sig") as stream:
        observation = json.load(stream)
    decision = make_decision(observation)
    write_json_atomic(output_path, decision)
    if log_path is not None:
        audit_path = Path(log_path)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
    return decision


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="Current-state JSON produced by save_watcher")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("proposed_decision.json"),
        help="Validated decision output (default: ./proposed_decision.json)",
    )
    parser.add_argument("--log", type=Path, help="Optional append-only JSONL decision log")
    parser.add_argument("--watch", action="store_true", help="Process every changed state until Ctrl+C")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between state checks")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if args.poll_interval <= 0:
        print("--poll-interval must be greater than zero", file=sys.stderr)
        return 2

    last_fingerprint: str | None = None
    try:
        while True:
            try:
                raw = args.state.read_bytes()
                fingerprint = hashlib.sha256(raw).hexdigest()
                if fingerprint != last_fingerprint:
                    decision = process_state(args.state, args.output, args.log)
                    last_fingerprint = fingerprint
                    print(
                        f"Proposed {len(decision['actions'])} action(s) for {decision['observation_date']} "
                        f"[{decision['decision_id']}] (not executed)",
                        file=sys.stderr,
                    )
            except (OSError, json.JSONDecodeError, ObservationError, ValueError) as error:
                print(f"State not ready: {error}", file=sys.stderr)
            if not args.watch:
                return 0 if last_fingerprint is not None else 1
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("Controller stopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
