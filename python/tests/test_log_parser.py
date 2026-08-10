from __future__ import annotations

import unittest

from python.paradox_agent.log_parser import parse_state_blocks


class ParseStateBlocksTests(unittest.TestCase):
    def test_parses_complete_snapshot_and_ignores_noise(self) -> None:
        lines = [
            "[00:00:00][other] unrelated\n",
            "[PARADOX_AGENT]|STATE_BEGIN|date=2200.02.01\n",
            "[PARADOX_AGENT]|COUNTRY|name=Test Empire|planets=1|energy=1000|energy_income=12.5\n",
            "[PARADOX_AGENT]|PLANET|name=Axiom Prime|pops=28|stability=61\n",
            "[PARADOX_AGENT]|STATE_END|date=2200.02.01\n",
        ]

        snapshots = parse_state_blocks(lines)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["date"], "2200.02.01")
        self.assertEqual(snapshots[0]["country"]["energy"], 1000)
        self.assertEqual(snapshots[0]["country"]["energy_income"], 12.5)
        self.assertEqual(snapshots[0]["planets"][0]["name"], "Axiom Prime")

    def test_drops_incomplete_snapshot(self) -> None:
        lines = [
            "[PARADOX_AGENT]|STATE_BEGIN|date=2200.02.01\n",
            "[PARADOX_AGENT]|COUNTRY|name=Test Empire|energy=1000\n",
        ]

        self.assertEqual(parse_state_blocks(lines), [])


if __name__ == "__main__":
    unittest.main()
