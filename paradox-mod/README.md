# Paradox Agent Testbed

This folder contains the Stellaris side of the project. Version 0.1 targets
Stellaris **4.4.x (Pegasus)** and establishes the smallest useful integration
test:

- one player empire;
- one generated star system;
- one colonized homeworld;
- no AI, fallen, marauder, or pre-FTL empires;
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

1. Start the Paradox Launcher and enable **Paradox Agent Testbed**.
2. Start a new game with **Paradox Agent Test Empire**.
3. Select the **Paradox Agent Testbed (1 System)** galaxy size.
4. Start the game and allow at least one month to pass.
5. Inspect `Documents\Paradox Interactive\Stellaris\logs\game.log`.
6. Search for `[PARADOX_AGENT]`.

Expected markers include `BRIDGE_READY`, `STATE_BEGIN`, `COUNTRY`, `PLANET`,
and `STATE_END`.

## Important limitation

The static scenario requests exactly one generated system. Some DLC or vanilla
startup scripts may still create isolated special systems. They are outside the
test empire's playable economy and will be handled only if they interfere with
repeatability.

