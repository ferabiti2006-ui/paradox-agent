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
- researched technologies, research alternatives, and stored research points;
- owned colonies with exact building/district IDs and economic totals;
- mobile fleets, their owned ships, and the player's ship designs;
- construction queues whose owner is the player country.

The exporter defaults to `player_owned_only`. It parses enough of the save to
resolve cross-references, but it does not expose foreign countries in the JSON.
That boundary is intentional: any later enemy observation must be derived from
Stellaris intel/sensor visibility rather than from omniscient save contents.

The save must be text-format (the normal non-ironman development setup). Run
the module from the repository root so the `python` package can be imported.

