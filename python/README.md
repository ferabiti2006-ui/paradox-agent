# Python

Python will become the external controller for the Stellaris agent. It will:

1. read structured observations from the mod log and deeper state from saves;
2. enforce fog of war and create compact JSON observations;
3. validate model actions against a restricted action schema;
4. translate accepted actions into Stellaris input;
5. retain experiment logs and strategic memory.

The first utilities are dependency-free:

```powershell
python .\python\tools\validate_mod.py
python .\python\paradox_agent\log_parser.py path\to\game.log
python -m python.paradox_agent.save_parser path\to\autosave.sav -o observation.json
python -m python.paradox_agent.save_watcher "path\to\Stellaris\save games" -o current_state.json
python -m python.paradox_agent.controller current_state.json -o proposed_decision.json --log decisions.jsonl
python -m python.paradox_agent.game_adapter proposed_decision.json current_state.json
python -m unittest discover .\python\tests
```

The log parser converts complete `PARADOX_AGENT|` state blocks into JSON.
Schema 2 contains country economy/capacity totals, research totals, physical
colonies, fleets, and starbases. Exact current technology names, individual
building types, and exact construction queue items are not generically exposed
by Stellaris scripting; the save reader reconciles those details with this
lightweight monthly observation.

## Save parser

`save_parser.py` reads the zipped `meta` and `gamestate` documents directly;
it never changes the save. Its JSON observation currently includes:

- save version, date, active mods, and the human player's country ID;
- exact resource stockpiles and calculated current monthly resource balance;
- researched technologies, active research queues, alternatives, and stored research points;
- owned colonies with exact building/district IDs and economic totals;
- mobile fleets, their owned ships, and the player's ship designs;
- construction queues whose owner is the player country.

The exporter defaults to `player_owned_only`. It parses enough of the save to
resolve cross-references, but it does not expose foreign countries in the JSON.
That boundary is intentional: any later enemy observation must be derived from
Stellaris intel/sensor visibility rather than from omniscient save contents.

The save must be text-format (the normal non-ironman development setup). Run
the module from the repository root so the `python` package can be imported.

## Autosave watcher

`save_watcher.py` recursively watches the Stellaris save-games directory, so
normal per-empire folders require no special configuration. It selects the most
recent `.sav`, waits until its size and modification time have remained stable,
parses it, and atomically replaces `current_state.json`. A partial or invalid
save therefore cannot overwrite the controller's last valid observation.

The watcher remains active and publishes each new autosave once. Use `--once`
to wait for one stable save and exit, `--poll-interval` to change the scan rate,
or `--settle-seconds` to change the default two-second write-safety window.

## Controller V1

The first controller is deliberately deterministic and decision-only. It reads
the watcher output, rejects observations that violate `player_owned_only`,
evaluates the campaign objective, proposes restricted actions, validates every
action against the current state, atomically publishes a decision, and can append
the same record to a JSONL audit log.

V1 permits `CHOOSE_RESEARCH` for an idle area and a technology listed in
the relevant save alternatives, one fail-closed catalog-backed `BUILD_BUILDING` or
`BUILD_DISTRICT` for a verified owned colony, plus `WAIT` for 1–12 months. The
machine-readable contract is
`schemas/action_decision.schema.json`. The planetary API also permits exact,
allow-listed `UPGRADE_BUILDING` requests when a save contains authoritative
upgrade observations. Unknown action types and fields are
rejected, so a future model cannot emit arbitrary console commands. Decisions
are marked `not_executed` until the game-action adapter handles them. Run
continuously alongside the save watcher with:

```powershell
python -m python.paradox_agent.controller runtime\current_state.json `
  -o runtime\proposed_decision.json `
  --log runtime\decisions.jsonl `
  --watch
```

## Visual execution environment

Live UI execution uses OCR and image processing. Keep those dependencies in a
project-local environment:

```powershell
& "C:\Users\rapau\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" `
  -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r .\python\requirements-visual.txt
```

The observation watcher and decision-only controller can still use an ordinary
Python installation. Use `.\.venv\Scripts\python.exe` for visual tests and live
adapter execution.

## Game-action adapter V2

`game_adapter.py` implements a deliberately narrow, visually grounded input
bridge. It revalidates the decision against the exact observation fingerprint
and maps `CHOOSE_RESEARCH` to the normal Technology interface and
`BUILD_DISTRICT` and the allow-listed `BUILD_BUILDING` to an identified owned
colony in Stellaris 4.4.6. It does not
use the console, scripts, save edits, or resource-granting effects.

Live clicks never use the nominal option index as a coordinate. The adapter:

1. makes the Windows process DPI-aware and captures the physical-pixel game client;
2. recognizes the Technology screen and the requested research area with OCR;
3. resolves technology IDs through the installed Stellaris English localisation;
4. requires the on-screen game date to exactly equal the observation date;
5. requires every visible technology choice to agree with the fresh save;
6. clicks the uniquely recognized technology card; and
7. requires the choice dialog to close and the requested technology to appear
   as active research.

For districts, the read-only testbed effect persists total free district slots
and `num_free_districts` for the four standard district IDs into the save. The
parser joins those values to the owned planet and its exact planet construction
queue. Missing availability variables, a non-empty/ambiguous queue, an
unsupported game version, inadequate minerals, or a non-player planet all make
validation fail closed. The visual skill uniquely selects the planet through
the outliner, verifies its header and all authoritative district labels, clicks
the matching Build control, and requires either a new OCR occurrence or an
explicit `Constructing <district>` status in the visible planet queue. That
visual result remains provisional until a newer parsed save
contains the exact district ID in the target planet queue.

For buildings, the initial 4.4.6 allow-list contains Research Labs,
Holo-Theatres, Alloy Foundries, Civilian Industries, and Administrative Offices.
`planet_catalog.py` centralizes their installed definition metadata, and
`tools/validate_game_catalog.py` checks that metadata and the localized English
UI labels against the installed game. The observation mod mirrors each
building's relevant 4.4.6 potential and technology prerequisites for ordinary
human non-nomadic empires, while the parser supplies authoritative free slots,
current counts, cost, prerequisites, upgrade relationships, and the shared
planet construction queue. The visual skill identifies every cyan empty-slot
control relative to the OCR-grounded `Districts and Buildings` panel and safely
probes those non-destructive slot controls until the requested unique localized
building option is visible. It never clicks a building unless that exact label
is recognized, and then requires an explicit constructing status.
Final success still requires a subsequent save whose construction item resolves
to the exact building ID. Unsupported empire/planet cost models fail closed.

For upgrades, the controller uses the save building instance ID as `slot` and
requires the exact expected current building and target edge. The catalog pins
four tier-one to tier-two upgrade definitions, including strategic-resource
costs and technologies, while the testbed mod exports conservative per-planet
legality flags. The visual skill establishes the correct planet, date, zone,
and zone-relative occupied position; rejects a visible empty slot; opens
Building Details; confirms the exact localized current building; and only then
clicks a unique Upgrade control. A later save must show the target type in the
same authoritative building instance before the receipt becomes
`save_verified`.

## Normalized Planetary Action API

`planet_api.py` exposes concise owned-planet snapshots, exact legal action
objects, a versioned fingerprint/date-bound request envelope, and a conservative
deterministic governor. Unknown blocker, modifier, and decision data is explicit
rather than treated as an empty authoritative list. Generate a snapshot with:

```powershell
python -m python.paradox_agent.planet_api runtime\current_state.json --planet-id 4
```

The full capability audit and unsupported-action rationale are in
`PLANET_CONTROLLER_AUDIT.md`.

Before/choice/after screenshots and OCR confidence are written beside the
execution receipt. A disagreement, missing title, unexpected dialog, stale
choice list, or ambiguous match stops execution without another click.

The default command is a dry run and sends no input. Live execution additionally
requires `--execute` and `--arm` with the exact decision ID printed by the dry
run. V2 currently requires an English 1920x1080 physical Stellaris client and game GUI
scale 1.0. Before live execution, pause Stellaris and close any modal dialogs.
Every attempt is journaled to a unique receipt before each click, and an existing
receipt blocks a repeat attempt. A successful action is marked
`visual_verified_pending_save` until a subsequent autosave confirms it.

```powershell
# Dry run: no capture and no input.
& ".\.venv\Scripts\python.exe" -m python.paradox_agent.game_adapter `
  runtime\proposed_decision.json runtime\current_state.json

# Live execution: replace DECISION_ID with the exact dry-run ID.
& ".\.venv\Scripts\python.exe" -m python.paradox_agent.game_adapter `
  runtime\proposed_decision.json runtime\current_state.json `
  --execute --arm DECISION_ID

# After saving in Stellaris, promote the visual receipt only if the save agrees.
& ".\.venv\Scripts\python.exe" -m python.paradox_agent.game_adapter `
  runtime\proposed_decision.json runtime\current_state.json `
  --verify-save --receipt runtime\execution_receipts\DECISION_ID.json
```

