"""Parse structured Paradox Agent records from a Stellaris game log."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


MARKER = "[PARADOX_AGENT]|"


def _value(text: str) -> str | int | float:
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _record(line: str) -> tuple[str, dict[str, Any]] | None:
    marker_index = line.find(MARKER)
    if marker_index < 0:
        return None

    fields = line[marker_index + len(MARKER) :].strip().split("|")
    if not fields or not fields[0]:
        return None

    payload: dict[str, Any] = {}
    for field in fields[1:]:
        if "=" not in field:
            continue
        key, value = field.split("=", 1)
        payload[key] = _value(value)
    return fields[0], payload


def parse_state_blocks(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Return only complete STATE_BEGIN..STATE_END observation blocks."""

    snapshots: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in lines:
        parsed = _record(line)
        if parsed is None:
            continue
        record_type, payload = parsed

        if record_type == "STATE_BEGIN":
            current = {"date": payload.get("date"), "country": None, "planets": []}
        elif record_type == "COUNTRY" and current is not None:
            current["country"] = payload
        elif record_type == "PLANET" and current is not None:
            current["planets"].append(payload)
        elif record_type == "STATE_END" and current is not None:
            if current["country"] is not None:
                snapshots.append(current)
            current = None

    return snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_log", type=Path, help="Path to Stellaris game.log")
    args = parser.parse_args()

    with args.game_log.open(encoding="utf-8-sig", errors="replace") as stream:
        snapshots = parse_state_blocks(stream)

    print(json.dumps(snapshots, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

