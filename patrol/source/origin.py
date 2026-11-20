import json
from dataclasses import dataclass

from patrol import InvalidInput, require_text


@dataclass(frozen=True, slots=True)
class FrameOrigin:
    device_id: str
    slot_id: str
    session_id: str
    generation: int
    run_id: str
    point_id: str
    location: str

    def __post_init__(self) -> None:
        for name in ("device_id", "slot_id", "session_id", "run_id", "point_id", "location"):
            require_text(getattr(self, name), name)
        if type(self.generation) is not int or self.generation < 1:
            raise InvalidInput("frame generation must be a positive integer")

    @property
    def frame_key(self) -> str:
        identity = (
            self.device_id,
            self.slot_id,
            self.session_id,
            hex(self.generation),
            self.run_id,
            self.point_id,
        )
        return json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
