from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from python.paradox_agent.save_watcher import (
    AutosaveWatcher,
    SaveChangedDuringRead,
    newest_save,
)


def _touch(path: Path, content: str, modified_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(modified_ns, modified_ns))


class SaveWatcherTests(unittest.TestCase):
    def test_finds_newest_save_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "Empire One" / "autosave_1.sav"
            newer = root / "Empire Two" / "autosave_2.sav"
            _touch(older, "old", 1_000_000_000)
            _touch(newer, "new", 2_000_000_000)
            _touch(root / "not-a-save.txt", "ignored", 3_000_000_000)

            candidate = newest_save(root)

            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.path, newer.resolve())

    def test_waits_for_stable_save_then_publishes_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save = root / "autosave.sav"
            output = root / "state" / "current_state.json"
            _touch(save, "complete", 1_000_000_000)
            calls: list[Path] = []

            def parser(path: Path) -> dict[str, object]:
                calls.append(path)
                return {"save": {"file_name": path.name, "date": "2202.01.01"}}

            watcher = AutosaveWatcher(root, output, settle_seconds=2, parser=parser)
            self.assertIsNone(watcher.poll_once(now=10))
            self.assertIsNone(watcher.poll_once(now=11.9))
            published = watcher.poll_once(now=12)

            self.assertIsNotNone(published)
            self.assertEqual(calls, [save.resolve()])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["save"]["date"], "2202.01.01")
            self.assertIsNone(watcher.poll_once(now=20))
            self.assertEqual(len(calls), 1)

    def test_change_resets_settling_period(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save = root / "autosave.sav"
            _touch(save, "part", 1_000_000_000)
            watcher = AutosaveWatcher(root, root / "state.json", settle_seconds=2, parser=lambda _: {"ok": True})

            self.assertIsNone(watcher.poll_once(now=10))
            _touch(save, "complete", 2_000_000_000)
            self.assertIsNone(watcher.poll_once(now=12))
            self.assertIsNone(watcher.poll_once(now=13.9))
            self.assertIsNotNone(watcher.poll_once(now=14))

    def test_parse_failure_preserves_last_valid_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save = root / "autosave.sav"
            output = root / "state.json"
            _touch(save, "broken", 1_000_000_000)
            output.write_text('{"previous": true}\n', encoding="utf-8")

            def fail(_: Path) -> dict[str, object]:
                raise ValueError("incomplete save")

            watcher = AutosaveWatcher(root, output, settle_seconds=0, parser=fail)
            with self.assertRaisesRegex(ValueError, "incomplete save"):
                watcher.poll_once(now=10)

            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"previous": True})

    def test_does_not_regress_when_newest_autosave_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "autosave_1.sav"
            newer = root / "autosave_2.sav"
            output = root / "state.json"
            _touch(older, "old", 1_000_000_000)
            _touch(newer, "new", 2_000_000_000)
            parsed: list[str] = []

            def parser(path: Path) -> dict[str, object]:
                parsed.append(path.name)
                return {"save": path.name}

            watcher = AutosaveWatcher(root, output, settle_seconds=0, parser=parser)
            self.assertIsNotNone(watcher.poll_once(now=10))
            newer.unlink()

            self.assertIsNone(watcher.poll_once(now=11))
            self.assertEqual(parsed, ["autosave_2.sav"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"save": "autosave_2.sav"})

    def test_does_not_publish_if_save_changes_during_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save = root / "autosave.sav"
            output = root / "state.json"
            _touch(save, "part", 1_000_000_000)

            def change_save(path: Path) -> dict[str, object]:
                _touch(path, "finished", 2_000_000_000)
                return {"should_not": "publish"}

            watcher = AutosaveWatcher(root, output, settle_seconds=0, parser=change_save)
            with self.assertRaises(SaveChangedDuringRead):
                watcher.poll_once(now=10)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
