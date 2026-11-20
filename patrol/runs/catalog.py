from dataclasses import dataclass, replace
from threading import RLock

from patrol import Conflict, NotFound, StateError, require_text
from patrol.runs.points import Point
from patrol.runs.settings import SamplingSettings


@dataclass(frozen=True, slots=True)
class RunEntry:
    run_id: str
    point: Point
    settings: SamplingSettings
    frozen_frame_key: str | None = None

    @property
    def stopped(self) -> bool:
        return self.frozen_frame_key is not None


class RunCatalog:
    def __init__(self) -> None:
        self.lock = RLock()
        self._entries: dict[str, RunEntry] = {}

    def find(self, run_id: str) -> RunEntry | None:
        require_text(run_id, "run identifier")
        with self.lock:
            return self._entries.get(run_id)

    def require_active(self, run_id: str) -> RunEntry:
        entry = self.find(run_id)
        if entry is None:
            raise NotFound(f"unknown run {run_id!r}")
        if entry.stopped:
            raise StateError(f"run {run_id!r} has stopped")
        return entry

    def insert(self, entry: RunEntry) -> RunEntry:
        require_text(entry.run_id, "run identifier")
        with self.lock:
            if entry.run_id in self._entries:
                raise Conflict(f"run {entry.run_id!r} already exists")
            self._entries[entry.run_id] = entry
            return entry

    def set_stopped(self, run_id: str, frame_key: str) -> RunEntry:
        require_text(frame_key, "frame key")
        with self.lock:
            entry = self.require_active(run_id)
            stopped = replace(entry, frozen_frame_key=frame_key)
            self._entries[run_id] = stopped
            return stopped
