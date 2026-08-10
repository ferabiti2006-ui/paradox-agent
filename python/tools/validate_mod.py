"""Structural and regression checks for the Paradox Agent Stellaris mod."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED_FILES = (
    "descriptor.mod",
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


def require_pattern(
    errors: list[str], text: str, pattern: str, message: str
) -> None:
    if not re.search(pattern, text, re.MULTILINE):
        errors.append(message)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    mod_root = repo_root / "paradox-mod" / "paradox_agent_testbed"
    errors: list[str] = []

    for relative in REQUIRED_FILES:
        path = mod_root / relative
        if not path.is_file():
            errors.append(f"Missing required file: {path}")

    forbidden_initializer = (
        mod_root
        / "common"
        / "solar_system_initializers"
        / "paradox_agent_testbed_system.txt"
    )
    if forbidden_initializer.exists():
        errors.append("Custom home-system initializer must not ship with the testbed")

    forbidden_scenario = (
        mod_root
        / "map"
        / "setup_scenarios"
        / "paradox_agent_testbed.txt"
    )
    if forbidden_scenario.exists():
        errors.append("Static one-system scenario must remain disabled for 4.4.6")

    for path in mod_root.rglob("*.txt"):
        errors.extend(validate_braces(path))
    descriptor = mod_root / "descriptor.mod"
    if descriptor.is_file():
        errors.extend(validate_braces(descriptor))
        if 'supported_version="4.4.*"' not in descriptor.read_text(encoding="utf-8-sig"):
            errors.append("descriptor.mod must target Stellaris 4.4.*")

    empire_path = (
        mod_root
        / "prescripted_countries"
        / "paradox_agent_testbed_empire.txt"
    )
    if empire_path.is_file():
        empire = empire_path.read_text(encoding="utf-8-sig")
        if re.search(r"^\s*initializer\s*=", empire, re.MULTILINE):
            errors.append("The test empire must not force a special solar initializer")
        require_pattern(
            errors,
            empire,
            r'^\s*origin\s*=\s*"origin_default"\s*$',
            "The test empire must use the default Prosperous Unification origin",
        )
        require_pattern(
            errors,
            empire,
            r'^\s*planet_class\s*=\s*"pc_continental"\s*$',
            "The test empire must use a continental homeworld",
        )
        if "ethic_gestalt_consciousness" in empire:
            errors.append("The test empire must not be Gestalt")

    on_actions_path = (
        mod_root / "common" / "on_actions" / "paradox_agent_testbed_on_actions.txt"
    )
    if on_actions_path.is_file():
        on_actions = on_actions_path.read_text(encoding="utf-8-sig")
        if "on_game_start_country" in on_actions:
            errors.append("Bridge events must not run during galaxy initialization")
        if "on_monthly_pulse_country" not in on_actions:
            errors.append("Bridge must be attached to the monthly country pulse")

    event_path = mod_root / "events" / "paradox_agent_testbed_events.txt"
    if event_path.is_file():
        events = event_path.read_text(encoding="utf-8-sig")
        ids = re.findall(r"^\s*id\s*=\s*([\w.]+)\s*$", events, re.MULTILINE)
        duplicates = sorted({event_id for event_id in ids if ids.count(event_id) > 1})
        if duplicates:
            errors.append(f"Duplicate event IDs: {', '.join(duplicates)}")
        if "[PARADOX_AGENT]" in events:
            errors.append("Log markers must not use localization-style square brackets")
        if events.count('log = "PARADOX_AGENT|') != 4:
            errors.append("Expected four plain Paradox Agent log markers")
        if "export_trigger_value_to_variable" in events:
            errors.append("Initial bridge must use only vanilla-proven resource exports")

    localization = (
        mod_root
        / "localisation"
        / "english"
        / "paradox_agent_testbed_l_english.yml"
    )
    if localization.is_file():
        raw = localization.read_bytes()
        if not raw.startswith(b"\xef\xbb\xbf"):
            errors.append("English localization must have a UTF-8 BOM")

    if errors:
        print("Mod validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Mod validation passed: {mod_root}")
    print(f"Checked {len(REQUIRED_FILES)} required files and regression invariants.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
