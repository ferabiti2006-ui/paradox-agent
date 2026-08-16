"""Watch Stellaris saves and atomically publish the newest parsed observation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .save_parser import parse_save


Parser = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True)
class SaveSignature:
    """The file attributes used to decide whether a save is unchanged."""

    path: Path
    size: int
    modified_ns: int


@dataclass(frozen=True)
class PublishedObservation:
    """Details of an observation successfully written by one poll."""

    save: Path
    output: Path
    observation: dict[str, Any]


class SaveChangedDuringRead(RuntimeError):
    """Raised when Stellaris changes a save while it is being parsed."""


def newest_save(save_directory: str | Path) -> SaveSignature | None:
    """Return the newest .sav below *save_directory*, including empire folders."""

    directory = Path(save_directory)
    candidates: list[SaveSignature] = []
    for path in directory.rglob("*.sav"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            # A rotating autosave can disappear between directory scan and stat.
            continue
        candidates.append(
            SaveSignature(path=path.resolve(), size=stat.st_size, modified_ns=stat.st_mtime_ns)
        )
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate.modified_ns, str(candidate.path)))


def _signature(path: Path) -> SaveSignature:
    stat = path.stat()
    return SaveSignature(path=path.resolve(), size=stat.st_size, modified_ns=stat.st_mtime_ns)


def write_json_atomic(output: str | Path, observation: dict[str, Any]) -> None:
    """Write JSON beside the destination, then replace the destination atomically."""

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(observation, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


class AutosaveWatcher:
    """Poll a save directory and publish each stable newest save once."""

    def __init__(
        self,
        save_directory: str | Path,
        output: str | Path,
        *,
        settle_seconds: float = 2.0,
        parser: Parser = parse_save,
    ) -> None:
        if settle_seconds < 0:
            raise ValueError("settle_seconds must be non-negative")
        self.save_directory = Path(save_directory)
        self.output = Path(output)
        self.settle_seconds = settle_seconds
        self.parser = parser
        self._pending: SaveSignature | None = None
        self._pending_since: float | None = None
        self._published: SaveSignature | None = None

    def poll_once(self, *, now: float | None = None) -> PublishedObservation | None:
        """Inspect once, publishing only when the newest save has settled."""

        observed_at = time.monotonic() if now is None else now
        candidate = newest_save(self.save_directory)
        if candidate is None:
            self._pending = None
            self._pending_since = None
            return None

        if candidate == self._published:
            return None
        if self._published is not None and candidate.modified_ns < self._published.modified_ns:
            # Autosave rotation can briefly remove the newest slot. Keep the
            # controller on its last valid state instead of publishing an older one.
            self._pending = None
            self._pending_since = None
            return None

        if candidate != self._pending:
            self._pending = candidate
            self._pending_since = observed_at
            if self.settle_seconds > 0:
                return None

        assert self._pending_since is not None
        if observed_at - self._pending_since < self.settle_seconds:
            return None

        observation = self.parser(candidate.path)
        try:
            after_parse = _signature(candidate.path)
        except OSError as error:
            self._pending = None
            self._pending_since = None
            raise SaveChangedDuringRead(f"Save disappeared while parsing: {candidate.path}") from error
        if after_parse != candidate:
            self._pending = after_parse
            self._pending_since = observed_at
            raise SaveChangedDuringRead(f"Save changed while parsing: {candidate.path}")

        write_json_atomic(self.output, observation)
        self._published = candidate
        return PublishedObservation(candidate.path, self.output, observation)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "save_directory",
        type=Path,
        help="Stellaris save-games directory (empire subdirectories are searched)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("current_state.json"),
        help="Current-state JSON path (default: ./current_state.json)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between directory scans (default: 1)",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Seconds a save must remain unchanged before parsing (default: 2)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Wait for one stable save, publish it, and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if not args.save_directory.is_dir():
        print(f"Save directory does not exist: {args.save_directory}", file=sys.stderr)
        return 2
    if args.poll_interval <= 0:
        print("--poll-interval must be greater than zero", file=sys.stderr)
        return 2
    if args.settle_seconds < 0:
        print("--settle-seconds must be non-negative", file=sys.stderr)
        return 2

    watcher = AutosaveWatcher(
        args.save_directory,
        args.output,
        settle_seconds=args.settle_seconds,
    )
    print(f"Watching {args.save_directory.resolve()}", file=sys.stderr)
    try:
        while True:
            try:
                published = watcher.poll_once()
            except (OSError, ValueError, zipfile.BadZipFile, SaveChangedDuringRead) as error:
                # Keep the last valid output in place and retry on the next poll.
                print(f"Save not ready: {error}", file=sys.stderr)
            else:
                if published is not None:
                    date = published.observation.get("save", {}).get("date", "unknown date")
                    print(
                        f"Published {published.save.name} ({date}) -> {published.output.resolve()}",
                        file=sys.stderr,
                    )
                    if args.once:
                        return 0
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("Watcher stopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
