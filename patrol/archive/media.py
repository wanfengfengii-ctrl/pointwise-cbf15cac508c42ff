from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from patrol import InvalidInput, MediaWriteError, MediumUnavailable

_OBJECT_ID = re.compile(r"[0-9a-f]{32}")
_MANAGED_FILE = re.compile(r"patrol-([0-9a-f]{32})\.(raw|part)")


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LocalMedium:
    def __init__(
        self,
        name: str,
        directory: Path,
        *,
        online: bool = True,
        before_write: Callable[[str, str], None] | None = None,
    ) -> None:
        self.name = name
        self.directory = directory
        self._enabled = online
        self._before_write = before_write

    @property
    def online(self) -> bool:
        return self._enabled and self.directory.is_dir()

    def require_online(self) -> None:
        if not self.online:
            raise MediumUnavailable(f"medium {self.name!r} is offline")

    def _path(self, object_id: str, extension: str) -> Path:
        if not isinstance(object_id, str) or _OBJECT_ID.fullmatch(object_id) is None:
            raise InvalidInput("invalid archive object identifier")
        return self.directory / f"patrol-{object_id}.{extension}"

    def object_path(self, object_id: str) -> Path:
        return self._path(object_id, "raw")

    def stage_object(self, object_id: str, payload: bytes) -> None:
        self.require_online()
        pending = self._path(object_id, "part")
        try:
            with pending.open("xb") as stream:
                if self._before_write is not None:
                    self._before_write(self.name, object_id)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise MediaWriteError(f"could not stage object on medium {self.name!r}") from exc

    def publish_object(self, object_id: str) -> None:
        self.require_online()
        target = self.object_path(object_id)
        try:
            if target.exists():
                raise FileExistsError(target)
            os.replace(self._path(object_id, "part"), target)
            sync_directory(self.directory)
        except OSError as exc:
            raise MediaWriteError(f"could not publish object on medium {self.name!r}") from exc

    def read_object(self, object_id: str) -> bytes:
        self.require_online()
        try:
            return self.object_path(object_id).read_bytes()
        except OSError as exc:
            raise MediumUnavailable(f"object {object_id!r} is unreadable on medium {self.name!r}") from exc

    def candidate_ids(self) -> set[str]:
        self.require_online()
        candidates = set()
        try:
            for entry in self.directory.iterdir():
                match = _MANAGED_FILE.fullmatch(entry.name)
                if match is not None and entry.is_file():
                    candidates.add(match[1])
        except OSError as exc:
            raise MediumUnavailable(f"could not list medium {self.name!r}") from exc
        return candidates

    def discard_candidate(self, object_id: str) -> None:
        """Remove a candidate the station has established is unreferenced."""
        self.require_online()
        removed = False
        try:
            for extension in ("part", "raw"):
                try:
                    self._path(object_id, extension).unlink()
                except FileNotFoundError:
                    continue
                removed = True
            if removed:
                sync_directory(self.directory)
        except OSError as exc:
            raise MediaWriteError(f"could not remove candidate from medium {self.name!r}") from exc
