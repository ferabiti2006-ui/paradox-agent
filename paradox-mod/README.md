# Paradox Agent Testbed

This folder contains the Stellaris side of the project. Version 0.1 targets
Stellaris **4.4.x (Pegasus)** and establishes a stable integration baseline:

- one player empire;
- one colonized homeworld;
- a vanilla Tiny galaxy with no AI, fallen, marauder, or pre-FTL empires;
- a hidden monthly event that writes structured state records to `game.log`.

The game remains the simulator. The mod exposes observations; later milestones
will let the Python controller validate and submit a restricted set of legal
actions.

## Install

From PowerShell, run:

```powershell
.\paradox-mod\install_mod.ps1
```

The script copies the development mod into the current Windows Documents
folder under `Paradox Interactive\Stellaris\mod` and creates the launcher
descriptor. It never edits the vanilla game installation.

## First manual test

1. Fully close Stellaris, then start the Paradox Launcher and enable **Paradox Agent Testbed**.
2. Start a new game with **Paradox Agent Test Empire**. Do not randomize or
   substitute another empire for this test.
3. Select the vanilla **Tiny** galaxy size.
4. Set **AI Empires**, **Advanced AI Starts**, **Fallen Empires**, and
   **Marauder Empires** to zero. Set **Pre-FTL Civilizations** to 0x.
5. Start the game and allow at least one month to pass.
6. Inspect `Documents\Paradox Interactive\Stellaris\logs\game.log`.
7. Search for `PARADOX_AGENT|`.

Expected schema-2 markers include `BRIDGE_READY`, `STATE_BEGIN`, `COUNTRY`,
`RESEARCH`, `PLANET`, `FLEET`, `STARBASE`, and `STATE_END`.

The monthly bridge uses only Stellaris-native scripted effects and iterators
documented by the running 4.4.6 build. It exports:

- empire resources, population, systems, colonies, and capacity totals;
- research income, stored research, options, and researched-tech count;
- planet population, jobs, housing, amenities, stability, crime, districts,
  buildings, and a building-construction flag;
- fleet names, locations, power, size, and ship counts;
- starbase names, locations, modules, buildings, and construction-inclusive
  totals.

Exact technology names, arbitrary building/district type lists, and exact queue
item names are not generically enumerable through Stellaris scripting. Those
details will be added through save parsing rather than hard-coded into the mod.

## Why the one-system scenario is temporarily disabled

Stellaris 4.4.6 crashes in galaxy generation when the test mod supplies a
single-system static scenario: its startup effects attempt to add buildings
before the homeworld is colonizable. The stable baseline therefore leaves
galaxy generation entirely to vanilla Stellaris. Once the observation bridge
is confirmed working, galaxy reduction can be reintroduced in smaller,
separately tested steps.
