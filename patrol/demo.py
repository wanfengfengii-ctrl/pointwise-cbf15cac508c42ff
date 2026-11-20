import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from patrol import InvalidInput
from patrol.archive.intake import archive_frame
from patrol.archive.transactions import ArchiveStore
from patrol.archive.volumes import VolumeSpec
from patrol.handover.closeout import close_run
from patrol.handover.lookup import lookup_receipt, lookup_run_receipt
from patrol.runs.catalog import RunCatalog
from patrol.runs.lifecycle import open_run
from patrol.runs.points import PointRegistry
from patrol.source.freeze import freeze_frame, read_frozen_frame
from patrol.source.release import release_frame
from patrol.source.samples import record_word
from patrol.source.slots import SlotBank


def main() -> None:
    settings = {"unit": "mm/s", "minimum": -12, "maximum": 12, "peak_limit": 9}
    points, runs = PointRegistry(), RunCatalog()
    slots = SlotBank("DWB-01", "bay-1", session_id="day-demo")
    points.register("P07", "东湾海堤堤顶")
    open_run(points, runs, "D01", "P07", settings)
    record_word(runs, slots, "D01", 1, 4)
    try:
        record_word(runs, slots, "D01", 2, 99)
    except InvalidInput:
        pass
    else:
        raise AssertionError("the out-of-range second word was accepted")
    record_word(runs, slots, "D01", 2, 9)
    record_word(runs, slots, "D01", 3, 9)
    record_word(runs, slots, "D01", 4, 2)
    frozen = freeze_frame(runs, slots, "D01")
    frame = read_frozen_frame(slots, frozen.frame_key)

    with TemporaryDirectory(prefix="patrol-demo-") as directory:
        root = Path(directory)
        specs = (VolumeSpec("east", 8), VolumeSpec("middle", 6), VolumeSpec("west", 5))
        station = ArchiveStore.open(root, specs)
        receipt = archive_frame(station, frame, "east")
        release_frame(slots, station, receipt.receipt_id)
        manifest = close_run(station, "D01", receipt.receipt_id)
        slots.close()
        reopened = ArchiveStore.open(root, offline={"west"})
        receipt_id = lookup_run_receipt(reopened, "D01")
        audit = lookup_receipt(reopened, receipt_id)
        if receipt_id != manifest.receipt_id or receipt_id != receipt.receipt_id:
            raise AssertionError("reopened shift did not locate its original receipt")

        expected = {"peak": 9, "first_position": 2, "difference": 0, "exceeded": False}
        actual = {
            "peak": receipt.result.value, "first_position": receipt.result.position,
            "difference": receipt.result.difference, "exceeded": receipt.result.exceeded,
        }
        expected_origin = {
            "device_id": "DWB-01", "slot_id": "bay-1", "session_id": "day-demo",
            "generation": 1, "run_id": "D01", "point_id": "P07", "location": "东湾海堤堤顶",
        }
        downstream_expected = {
            "words": [4, 9, 9, 2], "origin": expected_origin, "settings": settings, "result": expected,
        }
        downstream_actual = {
            "words": list(audit.frame.words),
            "origin": asdict(audit.frame.origin),
            "settings": asdict(audit.frame.settings),
            "result": {
                "peak": audit.result.value, "first_position": audit.result.position,
                "difference": audit.result.difference, "exceeded": audit.result.exceeded,
            },
        }
        if actual != expected or downstream_actual != downstream_expected:
            raise AssertionError("archive or source-independent audit differs from the hand-checked shift")
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v8",
            "input": {"samples": [4, 99, 9, 9, 2], "positions": [1, 2, 2, 3, 4], "settings": settings},
            "expected": expected,
            "actual": actual,
            "downstream_expected": downstream_expected,
            "downstream_actual": downstream_actual,
            "code_paths": [
                "patrol/source/samples.py", "patrol/archive/intake.py", "patrol/source/release.py",
                "patrol/quality/peaks.py", "patrol/handover/closeout.py", "patrol/handover/lookup.py",
            ],
            "oracle": "99 exceeds 12. The first 9 is word 2 and 9 minus 9 is zero. The manifest must resolve to the receipt returned before the recorder closed.",
        }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
