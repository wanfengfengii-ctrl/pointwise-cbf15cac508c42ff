from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from patrol import InvalidInput, require_text
from patrol.runs.catalog import RunCatalog
from patrol.runs.lifecycle import mark_run_stopped
from patrol.runs.settings import SamplingSettings
from patrol.source.origin import FrameOrigin

if TYPE_CHECKING:
    from patrol.source.slots import SlotBank


@dataclass(frozen=True, slots=True)
class SourceFrame:
    origin: FrameOrigin
    settings: SamplingSettings
    words: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.origin, FrameOrigin) or not isinstance(self.settings, SamplingSettings):
            raise InvalidInput("a frozen frame requires a frame origin and frozen sampling settings")
        if not isinstance(self.words, tuple) or not self.words:
            raise InvalidInput("a frozen frame requires a nonempty tuple of words")
        if any(not self.settings.accepts_word(word) for word in self.words):
            raise InvalidInput("a frozen frame contains a word outside its frozen sampling settings")

    @property
    def frame_key(self) -> str:
        return self.origin.frame_key


def freeze_frame(runs: RunCatalog, slots: SlotBank, run_id: str) -> SourceFrame:
    with runs.lock, slots.lock:
        runs.require_active(run_id)
        frame = slots.freeze_current(run_id)
        mark_run_stopped(runs, run_id, frame.frame_key)
        return frame


def read_frozen_frame(slots: SlotBank, frame_key: str) -> SourceFrame:
    return slots.read_current(require_text(frame_key, "frame key"))
