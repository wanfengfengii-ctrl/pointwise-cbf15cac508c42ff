from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from sqlite3 import Connection
from typing import TYPE_CHECKING

from patrol import Conflict, InvalidInput, NotFound, StorageError
from patrol.archive.media import LocalMedium, sync_directory

if TYPE_CHECKING:
    from patrol.archive.transactions import ArchiveStore

_VOLUME_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


@dataclass(frozen=True, slots=True, order=True)
class VolumeSpec:
    name: str
    capacity_words: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _VOLUME_NAME.fullmatch(self.name) is None:
            raise InvalidInput("invalid medium name")
        if type(self.capacity_words) is not int or not 0 <= self.capacity_words <= 2**63 - 1:
            raise InvalidInput("medium capacity must be a nonnegative SQLite integer word count")


class VolumeCatalog:
    def __init__(self, store: ArchiveStore) -> None:
        self._store = store

    @staticmethod
    def _normalize(specs: Iterable[VolumeSpec]) -> tuple[VolumeSpec, ...]:
        requested = tuple(specs)
        if len(requested) != 3 or any(not isinstance(spec, VolumeSpec) for spec in requested):
            raise InvalidInput("station initialization requires three medium specifications")
        if len({spec.name for spec in requested}) != 3:
            raise InvalidInput("station media must have distinct names")
        return tuple(sorted(requested))

    def initialize(
        self, db: Connection, specs: Iterable[VolumeSpec] | None,
    ) -> tuple[VolumeSpec, ...]:
        requested = None if specs is None else self._normalize(specs)
        db.execute("""
            CREATE TABLE IF NOT EXISTS volumes (
                name TEXT PRIMARY KEY,
                capacity_words INTEGER NOT NULL CHECK (capacity_words >= 0)
            )
        """)
        existing = self.specifications(db=db)
        if existing:
            if len(existing) != 3:
                raise StorageError("the saved station medium configuration is incomplete")
            if requested is not None and requested != existing:
                raise Conflict("requested medium configuration differs from the initialized station")
            return existing
        if requested is None:
            raise NotFound("the station has no saved medium configuration")
        directory = self._store.root / "media"
        try:
            directory.mkdir(exist_ok=True)
            for spec in requested:
                (directory / spec.name).mkdir(exist_ok=True)
            sync_directory(directory)
            sync_directory(self._store.root)
        except OSError as exc:
            raise StorageError("could not initialize the station media directories") from exc
        db.executemany(
            "INSERT INTO volumes(name, capacity_words) VALUES (?, ?)",
            ((spec.name, spec.capacity_words) for spec in requested),
        )
        return requested

    def specifications(self, *, db: Connection | None = None) -> tuple[VolumeSpec, ...]:
        if db is None:
            with self._store.read_transaction() as reader:
                return self.specifications(db=reader)
        rows = db.execute("SELECT name, capacity_words FROM volumes ORDER BY name")
        return tuple(VolumeSpec(row["name"], row["capacity_words"]) for row in rows)

    def medium(self, name: str, *, db: Connection | None = None) -> LocalMedium:
        if not isinstance(name, str) or _VOLUME_NAME.fullmatch(name) is None:
            raise InvalidInput("invalid medium name")
        if db is None:
            with self._store.read_transaction() as reader:
                return self.medium(name, db=reader)
        if db.execute("SELECT 1 FROM volumes WHERE name = ?", (name,)).fetchone() is None:
            raise NotFound(f"unknown medium {name!r}")
        return LocalMedium(
            name,
            self._store.root / "media" / name,
            online=name not in self._store.offline,
            before_write=self._store.before_media_write,
        )

    def available_words(self, name: str, *, db: Connection | None = None) -> int:
        if not isinstance(name, str) or _VOLUME_NAME.fullmatch(name) is None:
            raise InvalidInput("invalid medium name")
        if db is None:
            with self._store.read_transaction() as reader:
                return self.available_words(name, db=reader)
        row = db.execute("""
            SELECT v.capacity_words, COALESCE(SUM(o.word_count), 0) AS occupied
            FROM volumes AS v
            LEFT JOIN whole_objects AS o ON o.volume_id = v.name
            WHERE v.name = ?
            GROUP BY v.name, v.capacity_words
        """, (name,)).fetchone()
        if row is None:
            raise NotFound(f"unknown medium {name!r}")
        available = row["capacity_words"] - row["occupied"]
        if available < 0:
            raise StorageError(f"committed occupancy exceeds capacity on medium {name!r}")
        return available
