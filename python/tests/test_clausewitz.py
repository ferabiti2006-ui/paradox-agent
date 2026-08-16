from __future__ import annotations

import unittest

from python.paradox_agent.clausewitz import PdxObject, parse_clausewitz


class ClausewitzParserTests(unittest.TestCase):
    def test_preserves_repeated_keys_and_anonymous_values(self) -> None:
        parsed = parse_clausewitz(
            'date="2202.01.01" enabled=yes numbers={ 1 2 3 } '
            'tech={ technology="tech_one" level=1 technology="tech_two" level=2 }'
        )

        self.assertEqual(parsed.get("date"), "2202.01.01")
        self.assertIs(parsed.get("enabled"), True)
        numbers = parsed.object("numbers")
        self.assertEqual(numbers.values, [1, 2, 3])
        tech = parsed.object("tech")
        self.assertEqual(tech.get_all("technology"), ["tech_one", "tech_two"])

    def test_parses_anonymous_nested_blocks(self) -> None:
        parsed = parse_clausewitz('player={ { name="unknown" country=0 } }')
        player = parsed.object("player")
        self.assertIsInstance(player.values[0], PdxObject)
        self.assertEqual(player.values[0].get("country"), 0)


if __name__ == "__main__":
    unittest.main()
