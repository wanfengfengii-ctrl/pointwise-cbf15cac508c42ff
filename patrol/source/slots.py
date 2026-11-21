from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4

from patrol import Conflict, NotFound, StateError, require_text
from patrol.runs.catalog import RunEntry
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import SourceFrame
from patrol.source.origin import FrameOrigin


@dataclass(slots=True)
class _OccupiedSlot:
    origin: FrameOrigin
    settings: SamplingSettings
    words: list[int] = field(default_factory=list)
    frozen: SourceFrame | None = None
    takeover_order: str | None = None


class SlotBank:
    """Own one physical slot and its frame generations for a recorder session."""

    def __init__(self, device_id: str, slot_id: str, *, session_id: str | None = None) -> None:
        self.device_id = require_text(device_id, "device identifier")
        self.slot_id = require_text(slot_id, "slot identifier")
        self.session_id = uuid4().hex if session_id is None else require_text(session_id, "session identifier")
        self.lock = RLock()
        self._generation = 0
        self._occupied: _OccupiedSlot | None = None
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise StateError("the recorder is closed")

    def _for_run(self, run_id: str) -> _OccupiedSlot:
        occupied = self._occupied
        if occupied is None:
            raise StateError("the recorder slot is empty")
        if occupied.origin.run_id != run_id:
            raise StateError(f"the recorder slot belongs to run {occupied.origin.run_id!r}")
        return occupied

    def next_position(self, run_id: str) -> int:
        with self.lock:
            self._require_open()
            if self._occupied is None:
                return 1
            occupied = self._for_run(run_id)
            if occupied.frozen is not None:
                raise StateError("the recorder slot holds a frozen frame")
            return len(occupied.words) + 1

    def claim(self, run: RunEntry) -> FrameOrigin:
        """Claim a slot for a run whose first word has passed admission checks."""
        with self.lock:
            self._require_open()
            if run.stopped:
                raise StateError("a stopped run cannot claim a slot")
            if self._occupied is not None:
                occupied = self._for_run(run.run_id)
                if occupied.frozen is not None:
                    raise StateError("the recorder slot holds a frozen frame")
                return occupied.origin
            generation = self._generation + 1
            origin = FrameOrigin(
                self.device_id, self.slot_id, self.session_id, generation,
                run.run_id, run.point.point_id, run.point.location,
            )
            self._occupied = _OccupiedSlot(origin, run.settings)
            self._generation = generation
            return origin

    def append_word(self, run_id: str, word: int) -> int:
        """Append an admitted word while the caller holds the slot lock."""
        with self.lock:
            self._require_open()
            occupied = self._for_run(run_id)
            if occupied.frozen is not None:
                raise StateError("the recorder slot holds a frozen frame")
            occupied.words.append(word)
            return len(occupied.words)

    def freeze_current(self, run_id: str) -> SourceFrame:
        with self.lock:
            self._require_open()
            occupied = self._for_run(run_id)
            if occupied.frozen is not None:
                raise StateError("the recorder slot already holds a frozen frame")
            if not occupied.words:
                raise StateError("cannot stop an empty run")
            frame = SourceFrame(occupied.origin, occupied.settings, tuple(occupied.words))
            occupied.frozen = frame
            occupied.words.clear()
            return frame

    def read_current(self, frame_key: str | None = None) -> SourceFrame:
        with self.lock:
            self._require_open()
            frame = self._occupied.frozen if self._occupied is not None else None
            if frame is None or (frame_key is not None and frame.frame_key != frame_key):
                raise NotFound("the requested frozen frame is not in the current slot")
            return frame

    def register_takeover(self, frame_key: str, order_id: str) -> SourceFrame:
        """Bind a takeover order to the current frozen frame without releasing it."""
        with self.lock:
            frame = self.read_current(frame_key)
            occupied = self._occupied
            if occupied.takeover_order is None:
                occupied.takeover_order = order_id
            elif occupied.takeover_order != order_id:
                raise Conflict("the current frame already has a different takeover order")
            return frame

    def current_takeover_order(self) -> str | None:
        with self.lock:
            occupied = self._occupied
            if occupied is None or occupied.frozen is None:
                return None
            return occupied.takeover_order

    def clear_current(self, frame_key: str) -> None:
        require_text(frame_key, "frame key")
        with self.lock:
            self.read_current(frame_key)
            self._occupied = None

    def close(self) -> None:
        with self.lock:
            if self._occupied is not None:
                raise StateError("cannot close a recorder that still holds original samples")
            self._closed = True
