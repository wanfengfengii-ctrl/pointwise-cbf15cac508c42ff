from dataclasses import dataclass
from threading import RLock

from patrol import Conflict, NotFound, require_text


@dataclass(frozen=True, slots=True)
class Point:
    point_id: str
    location: str

    def __post_init__(self) -> None:
        require_text(self.point_id, "point identifier")
        require_text(self.location, "point location")


class PointRegistry:
    def __init__(self) -> None:
        self._points: dict[str, Point] = {}
        self._lock = RLock()

    def register(self, point_id: str, location: str) -> Point:
        point = Point(point_id, location)
        with self._lock:
            current = self._points.get(point.point_id)
            if current is not None:
                if current != point:
                    raise Conflict(f"point {point_id!r} already has a different location")
                return current
            self._points[point_id] = point
            return point

    def require(self, point_id: str) -> Point:
        require_text(point_id, "point identifier")
        with self._lock:
            try:
                return self._points[point_id]
            except KeyError as exc:
                raise NotFound(f"unknown point {point_id!r}") from exc
