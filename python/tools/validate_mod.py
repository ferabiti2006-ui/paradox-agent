"""Fast structural checks for the Paradox Agent Stellaris mod.

This does not replace loading the mod in Stellaris. It catches missing files,
unbalanced braces, accidental duplicate event IDs, and descriptor drift before
the manual game test.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED_FILES = (
    "descriptor.mod",
    "map/setup_scenarios/paradox_agent_testbed.txt",
    "common/solar_system_initializers/paradox_agent_testbed_system.txt",
    "common/on_actions/paradox_agent_testbed_on_actions.txt",
    "events/paradox_agent_testbed_events.txt",
    "prescripted_countries/paradox_agent_testbed_empire.txt",
    "localisation/english/paradox_agent_testbed_l_english.yml",
)


def strip_comments_and_strings(text: str) -> str:
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return re.sub(r"#.*", "", text)


def validate_braces(path: Path) -> list[str]:
    clean = strip_comments_and_strings(path.read_text(encoding="utf-8-sig"))
    depth = 0
    errors: list[str] = []
    for line_number, line in enumerate(clean.splitlines(), start=1):
        depth += line.count("{")
        depth -= line.count("}")
        if depth < 0:
            errors.append(f"{path}: extra closing brace near line {line_number}")
            depth = 0
    if depth:
        errors.append(f"{path}: {depth} unclosed brace(s)")
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    mod_root = repo_root / "paradox-mod" / "paradox_agent_testbed"
    errors: list[str] = []

    for relative in REQUIRED_FILES:
        path = mod_root / relative
        if not path.is_file():
            errors.append(f"Missing required file: {path}")

    for path in mod_root.rglob("*.txt"):
        errors.extend(validate_braces(path))
    errors.extend(validate_braces(mod_root / "descriptor.mod"))

    event_path = mod_root / "events" / "paradox_agent_testbed_events.txt"
    if event_path.is_file():
        event_text = event_path.read_text(encoding="utf-8-sig")
        ids = re.findall(r"^\s*id\s*=\s*([\w.]+)\s*$", event_text, re.MULTILINE)
        duplicates = sorted({event_id for event_id in ids if ids.count(event_id) > 1})
        if duplicates:
            errors.append(f"Duplicate event IDs: {', '.join(duplicates)}")

    descriptor = mod_root / "descriptor.mod"
    if descriptor.is_file() and 'supported_version="4.4.*"' not in descriptor.read_text(
        encoding="utf-8-sig"
    ):
        errors.append("descriptor.mod must target Stellaris 4.4.*")

    if errors:
        print("Mod validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Mod validation passed: {mod_root}")
    print(f"Checked {len(REQUIRED_FILES)} required files and all script braces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

