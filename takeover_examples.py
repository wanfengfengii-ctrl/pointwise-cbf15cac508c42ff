import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from patrol import CapacityError, CommitError, Conflict, MediaWriteError, ReleaseDenied
from patrol.archive.intake import archive_frame
from patrol.archive.transactions import ArchiveStore
from patrol.archive.volumes import VolumeSpec
from patrol.handover.lookup import lookup_receipt
from patrol.runs.catalog import RunCatalog
from patrol.runs.lifecycle import open_run
from patrol.runs.points import PointRegistry
from patrol.source.freeze import freeze_frame, read_frozen_frame
from patrol.source.release import release_frame
from patrol.source.samples import record_word
from patrol.source.slots import SlotBank

VOLUMES = ("east", "middle", "west")
SETTINGS = {"unit": "mm/s", "minimum": -12, "maximum": 12, "peak_limit": 9}
WORDS = (4, 9, 2, 7)


def recorder(session):
    points = PointRegistry()
    points.register("P07", "东湾海堤堤顶")
    return points, RunCatalog(), SlotBank("DWB-01", "bay-1", session_id=session)


def capture(rig, run_id, words):
    points, runs, slots = rig
    open_run(points, runs, run_id, "P07", SETTINGS)
    for position, word in enumerate(words, 1):
        record_word(runs, slots, run_id, position, word)
    return freeze_frame(runs, slots, run_id)


def require_error(error_type, operation, *args):
    try:
        operation(*args)
    except error_type:
        return
    raise AssertionError(f"{operation.__name__} did not raise {error_type.__name__}")


def available(store):
    return tuple(store.volumes.available_words(name) for name in VOLUMES)


def station_with_old_work(root, remaining):
    specs = [VolumeSpec(name, free + 1) for name, free in zip(VOLUMES, remaining, strict=True)]
    store = ArchiveStore.open(root, specs)
    rig = recorder("previous-shift")
    old = {}
    for index, volume in enumerate(VOLUMES, 1):
        frame = capture(rig, f"old-{volume}", (index,))
        receipt = archive_frame(store, frame, volume)
        release_frame(rig[2], store, receipt.receipt_id)
        old[volume] = receipt.receipt_id, frame
    rig[2].close()
    assert available(store) == remaining
    return store, old


def verify_old_work(store, old, absent=None):
    for volume, (receipt_id, frame) in old.items():
        if volume != absent:
            assert lookup_receipt(store, receipt_id).frame == frame


def baseline():
    with TemporaryDirectory(prefix="patrol-takeover-v1-") as directory:
        store, old = station_with_old_work(Path(directory), (2, 2, 2))
        rig = recorder("limited-shift")
        frame = capture(rig, "D01", WORDS)
        for volume in VOLUMES:
            require_error(CapacityError, archive_frame, store, frame, volume)
        assert available(store) == (2, 2, 2)
        assert read_frozen_frame(rig[2], frame.frame_key) == frame
        verify_old_work(store, old)
        print(json.dumps({"whole_intake": "CapacityError on each volume", "net_words": available(store), "current_words": frame.words}))


def success(remaining, show=False):
    from patrol.archive.intake import archive_takeover
    from patrol.source.release import begin_takeover

    with TemporaryDirectory(prefix="patrol-takeover-") as directory:
        root = Path(directory)
        store, old = station_with_old_work(root / "limited", remaining)
        rig = recorder("limited-shift")
        slots = rig[2]
        frame = capture(rig, "D01", WORDS)
        spare = ArchiveStore.open(root / "following", [VolumeSpec(name, 8) for name in VOLUMES])
        ordinary = archive_frame(spare, frame, "east")
        assert begin_takeover(slots, frame.frame_key, "take-D01") == frame
        require_error(ReleaseDenied, release_frame, slots, spare, ordinary.receipt_id)
        receipt = archive_takeover(store, "take-D01", frame)
        balance = available(store)
        assert all(0 <= left <= free for left, free in zip(balance, remaining, strict=True))
        reopened = ArchiveStore.open(root / "limited")
        assert archive_takeover(reopened, "take-D01", frame).receipt_id == receipt.receipt_id
        assert available(reopened) == balance
        release_frame(slots, reopened, receipt.receipt_id)
        next_frame = capture(rig, "D02", (5, 6))
        begin_takeover(slots, next_frame.frame_key, "take-D02")
        require_error(Conflict, archive_takeover, reopened, "take-D01", next_frame)
        require_error(ReleaseDenied, release_frame, slots, reopened, receipt.receipt_id)
        assert read_frozen_frame(slots, next_frame.frame_key) == next_frame
        next_receipt = archive_takeover(spare, "take-D02", next_frame)
        release_frame(slots, spare, next_receipt.receipt_id)
        slots.close()

        for volume in VOLUMES:
            degraded = ArchiveStore.open(root / "limited", offline={volume})
            audit = lookup_receipt(degraded, receipt.receipt_id)
            assert audit.receipt_id == receipt.receipt_id and audit.frame == frame
            assert (audit.result.value, audit.result.position, audit.result.difference, audit.result.exceeded) == (9, 2, 0, False)
            verify_old_work(degraded, old, absent=volume)
            if show:
                print(json.dumps({"offline": volume, "receipt_id": audit.receipt_id, "words": audit.frame.words, "frame_key": audit.frame.frame_key, "peak": audit.result.value, "peak_position": audit.result.position, "remaining": balance}, ensure_ascii=False))


def verify_takeover():
    from patrol.archive.intake import archive_takeover
    from patrol.source.release import begin_takeover

    success((2, 2, 2))
    success((1, 3, 3))
    with TemporaryDirectory(prefix="patrol-short-") as directory:
        store, old = station_with_old_work(Path(directory), (1, 2, 4))
        rig = recorder("short-shift")
        frame = capture(rig, "D01", WORDS)
        begin_takeover(rig[2], frame.frame_key, "short")
        require_error(CapacityError, archive_takeover, store, "short", frame)
        assert available(store) == (1, 2, 4)
        assert read_frozen_frame(rig[2], frame.frame_key) == frame
        verify_old_work(store, old)

    for step, error in (("write", MediaWriteError), ("commit", CommitError)):
        with TemporaryDirectory(prefix="patrol-takeover-retry-") as directory:
            root = Path(directory)
            _, old = station_with_old_work(root, (2, 2, 2))
            rig = recorder("retry-shift")
            frame = capture(rig, "D01", WORDS)
            begin_takeover(rig[2], frame.frame_key, "retry")

            def fail(*_):
                raise OSError("takeover exercise " + step)

            hooks = {"before_media_write" if step == "write" else "before_archive_commit": fail}
            failing = ArchiveStore.open(root, **hooks)
            require_error(error, archive_takeover, failing, "retry", frame)
            assert read_frozen_frame(rig[2], frame.frame_key) == frame
            reopened = ArchiveStore.open(root)
            verify_old_work(reopened, old)
            receipt = archive_takeover(reopened, "retry", frame)
            balance = available(reopened)
            assert archive_takeover(reopened, "retry", frame).receipt_id == receipt.receipt_id
            assert available(reopened) == balance
            release_frame(rig[2], reopened, receipt.receipt_id)
            rig[2].close()
            assert lookup_receipt(ArchiveStore.open(root, offline={"middle"}), receipt.receipt_id).frame == frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("baseline", "verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "baseline":
        baseline()
    elif mode == "verify":
        from check_project import main as original_checks
        status = original_checks()
        if status:
            return status
        verify_takeover()
    else:
        from patrol.demo import main as ordinary_demo
        ordinary_demo()
        success((2, 2, 2), show=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
