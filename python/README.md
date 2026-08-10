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
python -m unittest discover .\python\tests
```

The log parser converts complete `PARADOX_AGENT|` state blocks into JSON.
Schema 2 contains country economy/capacity totals, research totals, physical
colonies, fleets, and starbases. Exact current technology names, individual
building types, and exact construction queue items are not generically exposed
by Stellaris scripting; a later save reader will reconcile those details with
this lightweight monthly observation.
