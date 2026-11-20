from collections.abc import Mapping

from patrol import Conflict
from patrol.runs.catalog import RunCatalog, RunEntry
from patrol.runs.points import PointRegistry
from patrol.runs.settings import SamplingSettings, freeze_settings


def open_run(
    points: PointRegistry,
    runs: RunCatalog,
    run_id: str,
    point_id: str,
    settings: SamplingSettings | Mapping[str, object],
) -> RunEntry:
    point = points.require(point_id)
    frozen = freeze_settings(settings)
    with runs.lock:
        current = runs.find(run_id)
        if current is None:
            return runs.insert(RunEntry(run_id, point, frozen))
        if current.point != point or current.settings != frozen:
            raise Conflict(f"run {run_id!r} was opened with different settings or point")
        return current


def mark_run_stopped(runs: RunCatalog, run_id: str, frame_key: str) -> RunEntry:
    return runs.set_stopped(run_id, frame_key)
