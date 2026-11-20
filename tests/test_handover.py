from patrol import AuditError, CloseoutDenied, Conflict, NotFound, StateError
from patrol.archive.transactions import ArchiveStore
from patrol.archive.volumes import VolumeSpec
from patrol.handover.closeout import close_run
from patrol.handover.lookup import lookup_receipt, lookup_run_receipt
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import read_frozen_frame
from patrol.source.origin import FrameOrigin
from tests import StationCase


class HandoverTests(StationCase):
    def test_reopen_without_source(self):
        station = self.open_station()
        frame, receipt = self.archive_and_release(station, "D01", (4, 9, 9, 2))
        manifest = close_run(station, "D01", receipt.receipt_id)
        self.slots.close()
        with self.assertRaises(StateError):
            read_frozen_frame(self.slots, frame.frame_key)
        reopened = ArchiveStore.open(self.root)
        resolved = lookup_run_receipt(reopened, "D01")
        self.assertEqual(resolved, manifest.receipt_id)
        self.assertEqual(resolved, receipt.receipt_id)
        audit = lookup_receipt(reopened, resolved)
        self.assertEqual(audit.frame.words, (4, 9, 9, 2))
        self.assertEqual(audit.frame.origin, FrameOrigin(
            "DWB-01", "bay-1", "test-shift", 1, "D01", "P07", "东湾海堤堤顶",
        ))
        self.assertEqual(audit.frame.settings, SamplingSettings("mm/s", -12, 12, 9))
        self.assertEqual((audit.result.value, audit.result.position, audit.result.difference), (9, 2, 0))
        self.assertIs(audit.result.exceeded, False)

    def test_cross_run_close(self):
        station = self.open_station()
        with self.assertRaises(CloseoutDenied):
            close_run(station, "D01", "whole-" + "0" * 32)
        with self.assertRaises(NotFound):
            lookup_run_receipt(station, "D01")
        _, first = self.archive_and_release(station, "D01", (4, 9, 2))
        with self.assertRaises(CloseoutDenied):
            close_run(station, "D02", first.receipt_id)
        initial = close_run(station, "D01", first.receipt_id)
        self.assertEqual(close_run(station, "D01", first.receipt_id), initial)
        _, second = self.archive_and_release(station, "D02", (8,), volume="west")
        with self.assertRaises(CloseoutDenied):
            close_run(station, "D01", second.receipt_id)
        with self.assertRaises(NotFound):
            lookup_run_receipt(station, "D02")
        self.assertEqual(lookup_run_receipt(station, "D01"), first.receipt_id)

    def test_repeat_initialization(self):
        station = self.open_station((11, 3, 4))
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        close_run(station, "D01", receipt.receipt_id)
        self.slots.close()
        specs = (VolumeSpec("east", 11), VolumeSpec("middle", 3), VolumeSpec("west", 4))
        ArchiveStore.open(self.root, specs)
        reopened = ArchiveStore.open(self.root, reversed(specs))
        self.assertEqual(reopened.volumes.specifications(), specs)
        self.assertEqual(reopened.volumes.available_words("east"), 8)
        self.assertEqual(lookup_run_receipt(reopened, "D01"), receipt.receipt_id)
        self.assertEqual(lookup_receipt(reopened, receipt.receipt_id).frame.words, (4, 9, 2))
        with self.assertRaises(Conflict):
            self.open_station((10, 3, 4))
        after_conflict = ArchiveStore.open(self.root)
        self.assertEqual(after_conflict.volumes.specifications(), specs)
        self.assertEqual(after_conflict.volumes.available_words("east"), 8)
        self.assertEqual(lookup_run_receipt(after_conflict, "D01"), receipt.receipt_id)
        self.assertEqual(lookup_receipt(after_conflict, receipt.receipt_id).frame.words, (4, 9, 2))

    def test_medium_availability(self):
        station = self.open_station()
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        _, other = self.archive_and_release(station, "D02", (-4,), volume="west")
        self.slots.close()
        other_offline = ArchiveStore.open(self.root, offline={"middle", "west"})
        self.assertEqual(lookup_receipt(other_offline, receipt.receipt_id).frame.words, (4, 9, 2))
        home_offline = ArchiveStore.open(self.root, offline={"east"})
        with self.assertRaises(AuditError):
            lookup_receipt(home_offline, receipt.receipt_id)
        self.assertEqual(lookup_receipt(home_offline, other.receipt_id).frame.words, (-4,))
        self.assertEqual(lookup_receipt(ArchiveStore.open(self.root), receipt.receipt_id).frame.words, (4, 9, 2))

    def test_missing_media_directory_stays_offline_on_reopen(self):
        station = self.open_station()
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        path = station.volumes.medium("east").directory
        saved = self.root / "east-unmounted"
        path.rename(saved)
        reopened = self.open_station()
        self.assertFalse(path.exists())
        self.assertFalse(reopened.volumes.medium("east").online)
        self.assertEqual(reopened.volumes.available_words("east"), 5)
        with self.assertRaises(AuditError):
            lookup_receipt(reopened, receipt.receipt_id)
        saved.rename(path)
        restored = ArchiveStore.open(self.root)
        self.assertEqual(lookup_receipt(restored, receipt.receipt_id).frame.words, (4, 9, 2))

    def test_same_length_damage(self):
        station = self.open_station()
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        close_run(station, "D01", receipt.receipt_id)
        self.slots.close()
        path = station.volumes.medium("east").object_path(receipt.object_id)
        self.assertEqual(path.read_bytes(), b"\x04\x09\x02")
        path.write_bytes(b"\x05\x09\x02")
        reopened = ArchiveStore.open(self.root)
        self.assertEqual(lookup_run_receipt(reopened, "D01"), receipt.receipt_id)
        with self.assertRaises(AuditError):
            lookup_receipt(reopened, receipt.receipt_id)

    def test_wrong_payload_length(self):
        station = self.open_station()
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        path = station.volumes.medium("east").object_path(receipt.object_id)
        for data in (b"", b"\x04\x09", b"\x04\x09\x02\x07"):
            with self.subTest(data=data):
                path.write_bytes(data)
                with self.assertRaises(AuditError):
                    lookup_receipt(station, receipt.receipt_id)

    def test_missing_object(self):
        station = self.open_station()
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        station.volumes.medium("east").object_path(receipt.object_id).unlink()
        with self.assertRaises(AuditError):
            lookup_receipt(ArchiveStore.open(self.root), receipt.receipt_id)

    def test_result_mismatch(self):
        station = self.open_station()
        _, receipt = self.archive_and_release(station, "D01", (4, 9, 9, 2))
        close_run(station, "D01", receipt.receipt_id)
        self.slots.close()
        with station.write_transaction() as db:
            updated = db.execute(
                "UPDATE whole_receipts SET peak_position = 3 WHERE receipt_id = ?", (receipt.receipt_id,),
            )
            self.assertEqual(updated.rowcount, 1)
        reopened = ArchiveStore.open(self.root)
        original_id = lookup_run_receipt(reopened, "D01")
        with self.assertRaises(AuditError):
            lookup_receipt(reopened, original_id)

    def test_unknown_receipt(self):
        station = self.open_station()
        with self.assertRaises(NotFound):
            lookup_receipt(station, "whole-" + "0" * 32)
        with self.assertRaises(NotFound):
            lookup_run_receipt(station, "D99")

    def test_frozen_context(self):
        station = self.open_station()
        _, first = self.archive_and_release(station, "D01", (4, 9, 9, 2))
        later = SamplingSettings("cm/s", -12, 12, 5)
        _, second = self.archive_and_release(station, "D02", (4, 9, 2), volume="middle", settings=later)
        self.slots.close()
        reopened = ArchiveStore.open(self.root)
        earlier_audit = lookup_receipt(reopened, first.receipt_id)
        later_audit = lookup_receipt(reopened, second.receipt_id)
        self.assertEqual(earlier_audit.frame.words, (4, 9, 9, 2))
        self.assertEqual((earlier_audit.result.unit, earlier_audit.result.limit), ("mm/s", 9))
        self.assertEqual((earlier_audit.result.value, earlier_audit.result.position), (9, 2))
        self.assertEqual(earlier_audit.result.difference, 0)
        self.assertIs(earlier_audit.result.exceeded, False)
        self.assertEqual((later_audit.result.unit, later_audit.result.limit, later_audit.result.difference), ("cm/s", 5, 4))
        self.assertIs(later_audit.result.exceeded, True)
