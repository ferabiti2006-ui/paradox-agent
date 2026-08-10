from __future__ import annotations

import unittest

from python.paradox_agent.log_parser import parse_state_blocks


class ParseStateBlocksTests(unittest.TestCase):
    def test_parses_complete_snapshot_and_ignores_noise(self) -> None:
        lines = [
            "[00:00:00][other] unrelated\n",
            "PARADOX_AGENT|STATE_BEGIN|schema=2|date=2200.02.01\n",
            "PARADOX_AGENT|COUNTRY|name=Test Empire|colonies=1|energy=1000|energy_income=12.5\n",
            "PARADOX_AGENT|RESEARCH|physics_income=20.5|techs_researched=8\n",
            "PARADOX_AGENT|PLANET|name=Axiom Prime|pop_amount=2800|stability=61\n",
            "PARADOX_AGENT|FLEET|name=1st Fleet|system=Axiom|civilian=0|power=145|ships=3\n",
            "PARADOX_AGENT|STARBASE|name=Axiom Station|modules=1|modules_with_construction=2\n",
            "PARADOX_AGENT|STATE_END|date=2200.02.01\n",
        ]

        snapshots = parse_state_blocks(lines)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["schema"], 2)
        self.assertEqual(snapshots[0]["date"], "2200.02.01")
        self.assertEqual(snapshots[0]["country"]["energy"], 1000)
        self.assertEqual(snapshots[0]["country"]["energy_income"], 12.5)
        self.assertEqual(snapshots[0]["planets"][0]["name"], "Axiom Prime")
        self.assertEqual(snapshots[0]["research"]["physics_income"], 20.5)
        self.assertEqual(snapshots[0]["fleets"][0]["power"], 145)
        self.assertEqual(snapshots[0]["fleets"][0]["civilian"], 0)
        self.assertEqual(snapshots[0]["starbases"][0]["modules_with_construction"], 2)

    def test_defaults_old_snapshots_to_schema_one(self) -> None:
        lines = [
            "PARADOX_AGENT|STATE_BEGIN|date=2200.02.01\n",
            "PARADOX_AGENT|COUNTRY|name=Legacy Empire|energy=1000\n",
            "PARADOX_AGENT|STATE_END|date=2200.02.01\n",
        ]

        snapshots = parse_state_blocks(lines)

        self.assertEqual(snapshots[0]["schema"], 1)
        self.assertIsNone(snapshots[0]["research"])
        self.assertEqual(snapshots[0]["fleets"], [])
        self.assertEqual(snapshots[0]["starbases"], [])

    def test_drops_incomplete_snapshot(self) -> None:
        lines = [
            "PARADOX_AGENT|STATE_BEGIN|date=2200.02.01\n",
            "PARADOX_AGENT|COUNTRY|name=Test Empire|energy=1000\n",
        ]

        self.assertEqual(parse_state_blocks(lines), [])


if __name__ == "__main__":
    unittest.main()
