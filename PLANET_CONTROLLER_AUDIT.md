# Stellaris 4.4.6 Planet Controller audit

Audit date: 2026-08-16. The repository, installed Stellaris 4.4.6 data files,
recorded 1920x1080 UI evidence, and available real testbed saves were inspected.

## Capability matrix

| Planet action | Save-readable? | Preconditions known? | UI executable? | Later-save verifiable? | Status | Recommended order |
|---|---:|---:|---:|---:|---|---:|
| Read normalized owned-planet state | Yes | N/A | N/A | Yes | Supported | Done |
| Build standard City/Generator/Mining/Agriculture district | Yes | Yes, from testbed availability, capacity, queue, cost model, resources, ownership, date/fingerprint | Yes, OCR-grounded | Yes, exact queue ID | Supported | Done |
| Build allow-listed normal building | Yes | Yes, from catalog plus per-planet testbed trigger, slots, queue, technology, resources, ownership, date/fingerprint | Yes, unique localized label after non-destructively probing image-grounded compatible slots | Yes, exact queue ID | Supported | Done |
| Wait without input | Yes | Yes | No input required | N/A | Supported | Done |
| Upgrade a building | Current structure/slot and definition upgrade edge are readable | Not yet: exact modified upgrade cost and per-planet upgrade legality are not exported | Not calibrated; existing building-slot targeting and upgrade button/dialog are not grounded | Yes, exact slot/type transition should be verifiable | Intentionally unsupported | 1 |
| Demolish a building | Exact structure and nominal position are readable | Mostly, but slot identity in 4.4 zone/building data needs stronger correlation | Not calibrated; destructive confirmation is not grounded | Yes, exact removal can be checked | Intentionally unsupported; requires explicit destructive flag | 2 |
| Demolish a district | Exact type/count is readable | Target type/count known, but an exact UI row and all downstream zone effects are not modeled | Not calibrated | Count reduction is readable, but zone side effects also need verification | Intentionally unsupported; requires explicit destructive flag | 3 |
| Clear a blocker | Raw deposits exist in saves but are not normalized/classified as blockers | No: exact blocker identity, technology, modified cost, and queue state are not exported | UI control exists but is not calibrated | Likely, after blocker/deposit parsing | Intentionally unsupported | 4 |
| Change planet designation | Current designation is readable | No authoritative legal-designation list yet | UI labels exist but selection window is not calibrated | Yes, `final_designation` is readable | Intentionally unsupported | 5 |
| Enact a planet decision | Some effects/modifiers are save-readable | No generic authoritative availability/cost/effect model | Decisions list exists but is not calibrated | Decision-specific only | Intentionally unsupported; future per-decision allow-list | 6 |
| Prioritize or disable/enable a job | Aggregate unemployment/free jobs are readable; exact job controls are not normalized | No exact current priority/disabled counts | Population panel controls exist but are not calibrated | Possibly, after exact job-state parsing | Intentionally unsupported | 7 |
| Select/stop biological growth or robotic assembly | Partial population state only | No authoritative target/legality model | Not calibrated | Not established | Intentionally unsupported | 8 |
| Resettle a pop | Pops exist in the save but no stable planner-facing pop target is implemented | No full source/destination/cost/rights model | Separate resettlement UI is complex and not calibrated | Potentially, but exact pop identity is difficult | Intentionally unsupported | 9 |
| Planetary automation controls | Some settings may be save-readable but are not parsed | No | Not calibrated | Not established | Unsupported; conflicts with deterministic controller ownership | Later |
| Rename planet | Name is readable | Yes | Not calibrated | Yes | Unsupported; low strategic value | Later |
| Cancel construction | Exact queue is readable | Yes in principle | Not calibrated and destructive | Yes | Unsupported; requires destructive safeguards | Later |

Normal 4.4.6 planets do **not** expose the old general
`district_industrial` construction action. Industry is represented through the
4.4 district/zone system. Planet-specific district sets (habitats, ring worlds,
arcologies, hive/machine worlds, arks, resorts, and special worlds) remain
unsupported until each set has authoritative `num_free_districts` observations,
cost modeling, unique localized UI targeting, and recorded visual tests.

## Supported construction catalog

The catalog is version-pinned to `Pegasus v4.4.6` and is checked against the
installed game files by `python/tools/validate_game_catalog.py`.

Districts:

- `district_city`
- `district_generator`
- `district_mining`
- `district_farming`

Buildings:

- `building_research_lab_1` — Research Labs
- `building_holo_theatres` — Holo-Theatres
- `building_foundry_1` — Alloy Foundries
- `building_factory_1` — Civilian Industries
- `building_bureaucratic_1` — Administrative Offices

The Python catalog records definition file, category, ordinary non-nomadic
cost, prerequisites, upgrade edges, and policy role. The observation mod mirrors
the installed potential/prerequisite conditions for a human non-nomadic empire.
Nomadic and wilderness cost models fail closed. Wooden-planet district costs
also fail closed because they use a different resource model.

## Normalized state contract

`python.paradox_agent.planet_api.normalize_planet` returns:

```text
schema, observation_date, state_fingerprint
planet:
  id, colony_id, name_key, planet_class, size, designation
  population: sapient, unemployed, available_jobs
  housing: total, used, available
  amenities: total, used, available
  stability, crime_or_deviancy
  districts: capacity, counts, options
  buildings: capacity, existing, options, possible_upgrades
  construction_queue
  economy: production, upkeep, net_output
  modifiers: known, items
  blockers: known, items
  available_decisions: known, items
  legal_actions
```

Unknown data is represented with `known: false` or `authoritative: false`; an
empty list is never silently presented as an authoritative empty set.

## Legal actions and request envelope

Legal actions are exact objects that can be sent back through the validator;
they contain no explanatory fields that would later be rejected as unknown.
The stale-resistant request envelope is:

```json
{
  "schema": 1,
  "observation_date": "2204.01.10",
  "state_fingerprint": "<64 lowercase hex characters>",
  "action": {
    "type": "BUILD_DISTRICT",
    "planet_id": 4,
    "district": "district_mining"
  }
}
```

`validate_planet_action_envelope` rejects unknown envelope fields, unknown
action fields, unsupported action types, stale dates/fingerprints, foreign or
unknown planets, unaffordable actions, unknown legality, non-empty queues, and
all other errors already enforced by the action validator. The execution
decision retains the same date and fingerprint checks, so the validator remains
authoritative even after a legal-action snapshot was produced.
`make_planet_decision` converts the validated request into the existing
adapter-ready decision envelope without changing or bypassing validation.

## Deterministic governor boundary

`DeterministicPlanetGovernor` is a deliberately small interface exercise. It:

- waits if authoritative inputs are absent, the queue is occupied, or the
  mineral reserve would be breached;
- addresses a severe amenity deficit with Holo-Theatres when legal;
- addresses a severe housing deficit with a City district when legal;
- responds to authoritative unemployment with the legal basic-resource district
  corresponding to the weakest empire monthly balance;
- otherwise waits.

It does not advance time or bypass the controller/adapter. Its result must still
pass the same action validator and visual/save-verification pipeline.

## Autonomous-run boundary

The components needed to select a safe single-planet action are present. A
multi-year autonomous integration run is **not** claimed: safe time progression,
save scheduling, receipt reconciliation across repeated cycles, and recovery
orchestration are not yet implemented as a durable run coordinator. Existing
real saves predate the new observation variables, so the new catalog entries
correctly remain unavailable until the updated testbed mod is installed and a
new monthly observation is saved.
