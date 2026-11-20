from dataclasses import replace

from patrol import CapacityError, CommitError, Conflict, MediaWriteError, NotFound, ReleaseDenied
from patrol.archive.intake import archive_frame
from patrol.archive.receipts import ReceiptRegistry
from patrol.archive.transactions import ArchiveStore
from patrol.handover.lookup import lookup_receipt
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import read_frozen_frame
from patrol.source.release import release_frame
from tests import StationCase


class ArchiveTests(StationCase):
    def test_peak_tie_uses_first_word(self):
        station = self.open_station()
        frame = self.frame("D01", (4, 9, 9, 2))
        receipt = archive_frame(station, frame, "east")
        result = receipt.result
        self.assertEqual((result.value, result.position, result.limit, result.difference), (9, 2, 9, 0))
        self.assertIs(result.exceeded, False)
        audited = lookup_receipt(station, receipt.receipt_id)
        self.assertEqual((audited.result.value, audited.result.position), (9, 2))
        self.assertEqual(audited.frame.words, (4, 9, 9, 2))

    def test_signed_excess(self):
        station = self.open_station()
        settings = SamplingSettings("mm/s", -12, 12, -4)
        frame = self.frame("D01", (-8, -3, -5), settings)
        receipt = archive_frame(station, frame, "east")
        result = receipt.result
        self.assertEqual((result.value, result.position, result.limit, result.difference), (-3, 2, -4, 1))
        self.assertIs(result.exceeded, True)
        self.assertEqual(result.unit, "mm/s")

    def test_multibyte_signed_words(self):
        station = self.open_station((4, 6, 5))
        settings = SamplingSettings("mm/s", -512, 511, 300)
        frame = self.frame("D01", (-129, 128, -500, 300), settings)
        receipt = archive_frame(station, frame, "east")
        medium = station.volumes.medium("east")
        self.assertEqual(medium.read_object(receipt.object_id), bytes.fromhex("ff7f0080fe0c012c"))
        self.assertEqual(station.volumes.available_words("east"), 0)
        audited = lookup_receipt(station, receipt.receipt_id)
        self.assertEqual(audited.frame.words, (-129, 128, -500, 300))
        self.assertEqual((audited.result.value, audited.result.position), (300, 4))

    def test_short_volume_preserves_source(self):
        station = self.open_station((3, 6, 5))
        frame = self.frame("D01", (4, 9, 9, 2))
        with self.assertRaises(CapacityError):
            archive_frame(station, frame, "east")
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key), frame)
        self.assertEqual(station.volumes.available_words("east"), 3)
        self.assertIsNone(ReceiptRegistry(station).by_frame(frame.frame_key))
        self.assertEqual(station.volumes.medium("east").candidate_ids(), set())

    def test_committed_occupancy(self):
        station = self.open_station((4, 6, 5))
        _, old = self.archive_and_release(station, "D01", (7,))
        frame = self.frame("D02", (4, 9, 2, 7))
        with self.assertRaises(CapacityError):
            archive_frame(station, frame, "east")
        self.assertEqual(station.volumes.available_words("east"), 3)
        self.assertEqual(lookup_receipt(station, old.receipt_id).frame.words, (7,))
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key).words, (4, 9, 2, 7))
        self.assertIsNone(ReceiptRegistry(station).by_frame(frame.frame_key))

    def test_exact_capacity(self):
        station = self.open_station((5, 6, 5))
        self.archive_and_release(station, "D01", (7,))
        frame = self.frame("D02", (4, 9, 2, 7))
        self.assertEqual(station.volumes.available_words("east"), 4)
        receipt = archive_frame(station, frame, "east")
        self.assertEqual(station.volumes.available_words("east"), 0)
        self.assertEqual(lookup_receipt(station, receipt.receipt_id).frame.words, (4, 9, 2, 7))

    def test_whole_copy_boundary(self):
        station = self.open_station((2, 2, 2))
        frame = self.frame("D01", (4, 9, 2, 7))
        for volume in ("east", "middle", "west"):
            with self.subTest(volume=volume):
                with self.assertRaises(CapacityError):
                    archive_frame(station, frame, volume)
                self.assertEqual(station.volumes.available_words(volume), 2)
                self.assertEqual(station.volumes.medium(volume).candidate_ids(), set())
        self.assertIsNone(ReceiptRegistry(station).by_frame(frame.frame_key))
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key).words, (4, 9, 2, 7))

    def test_write_failure(self):
        station = self.open_station()
        _, old = self.archive_and_release(station, "D01", (7,))
        frame = self.frame("D02", (4, 9, 2, 7))
        observed = []

        def fail(volume, object_id):
            observed.append((volume, object_id))
            raise OSError("simulated medium write failure")

        station = ArchiveStore.open(self.root, before_media_write=fail)
        with self.assertRaises(MediaWriteError):
            archive_frame(station, frame, "east")
        self.assertEqual([volume for volume, _ in observed], ["east"])
        self.assertEqual(station.volumes.medium("east").candidate_ids(), {old.object_id})
        self.assertEqual(station.volumes.available_words("east"), 7)
        self.assertIsNone(ReceiptRegistry(station).by_frame(frame.frame_key))
        self.assertEqual(lookup_receipt(station, old.receipt_id).frame.words, (7,))
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key), frame)
        with self.assertRaises(ReleaseDenied):
            release_frame(self.slots, station, f"whole-{observed[0][1]}")
        reopened = ArchiveStore.open(self.root)
        issued = archive_frame(reopened, frame, "east")
        self.assertEqual(archive_frame(reopened, frame, "west"), issued)
        self.assertEqual(reopened.volumes.available_words("east"), 3)
        self.assertEqual(reopened.volumes.medium("east").candidate_ids(), {old.object_id, issued.object_id})

    def test_retry_after_commit_failure(self):
        attempts = []

        def fail_once():
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("simulated commit failure")

        station = self.open_station(before_archive_commit=fail_once)
        frame = self.frame("D01", (4, 9, 2, 7))
        with self.assertRaises(CommitError):
            archive_frame(station, frame, "east")
        self.assertIsNone(ReceiptRegistry(station).by_frame(frame.frame_key))
        self.assertEqual(station.volumes.available_words("east"), 8)
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key), frame)
        receipt = archive_frame(station, frame, "east")
        self.assertEqual(archive_frame(station, frame, "east"), receipt)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(station.volumes.available_words("east"), 4)
        self.assertEqual(lookup_receipt(station, receipt.receipt_id).frame.words, (4, 9, 2, 7))

    def test_reopen_retry(self):
        station = self.open_station()
        _, old = self.archive_and_release(station, "D01", (7,))
        frame = self.frame("D02", (4, 9, 2, 7))
        seen = []

        def fail_commit():
            medium = station.volumes.medium("east")
            new_ids = medium.candidate_ids() - {old.object_id}
            self.assertEqual(len(new_ids), 1)
            candidate = new_ids.pop()
            seen.append(candidate)
            self.assertEqual(medium.read_object(candidate), b"\x04\x09\x02\x07")
            raise OSError("simulated commit failure after media publish")

        station = ArchiveStore.open(self.root, before_archive_commit=fail_commit)
        with self.assertRaises(CommitError):
            archive_frame(station, frame, "east")
        reopened = ArchiveStore.open(self.root)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(ReceiptRegistry(reopened).by_frame(frame.frame_key))
        with self.assertRaises(NotFound):
            lookup_receipt(reopened, f"whole-{seen[0]}")
        self.assertEqual(lookup_receipt(reopened, old.receipt_id).frame.words, (7,))
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key), frame)
        issued = archive_frame(reopened, frame, "east")
        self.assertEqual(archive_frame(reopened, frame, "middle"), issued)
        self.assertEqual(reopened.volumes.available_words("east"), 3)
        with reopened.read_transaction() as db:
            rows = db.execute("SELECT receipt_id FROM whole_receipts WHERE frame_key = ?", (frame.frame_key,))
            self.assertEqual([row[0] for row in rows], [issued.receipt_id])

    def test_reopen_prunes_only_uncommitted_objects(self):
        station = self.open_station()
        _, old = self.archive_and_release(station, "D01", (7,))
        medium = station.volumes.medium("east")
        partial = "1" * 32
        published = "2" * 32
        medium.stage_object(partial, b"\x03")
        medium.stage_object(published, b"\x05")
        medium.publish_object(published)
        untouched = medium.directory / "operator-notes.txt"
        untouched.write_text("shift notes", encoding="utf-8")
        reopened = ArchiveStore.open(self.root)
        self.assertEqual(reopened.volumes.medium("east").candidate_ids(), {old.object_id})
        self.assertEqual(untouched.read_text(encoding="utf-8"), "shift notes")
        self.assertEqual(lookup_receipt(reopened, old.receipt_id).frame.words, (7,))
        self.assertEqual(reopened.volumes.available_words("east"), 7)

    def test_literal_retry_reuses_receipt(self):
        station = self.open_station()
        frame = self.frame("D01", (4, 9, 2, 7))
        receipt = archive_frame(station, frame, "east")
        self.assertEqual(archive_frame(station, frame, "middle"), receipt)
        self.assertEqual(ReceiptRegistry(station).by_frame(frame.frame_key), receipt)
        self.assertEqual(station.volumes.available_words("east"), 4)
        self.assertEqual(station.volumes.available_words("middle"), 6)

    def test_full_volume_retry(self):
        station = self.open_station((4, 6, 5))
        frame = self.frame("D01", (4, 9, 2, 7))
        first = archive_frame(station, frame, "east")
        self.assertEqual(station.volumes.available_words("east"), 0)
        self.assertEqual(archive_frame(station, frame, "east"), first)
        self.assertEqual(station.volumes.medium("east").candidate_ids(), {first.object_id})

    def test_changed_content(self):
        station = self.open_station()
        frame = self.frame("D01", (4, 9, 2, 7))
        receipt = archive_frame(station, frame, "east")
        variants = (
            replace(frame, words=(5, 9, 2, 7)),
            replace(frame, settings=replace(frame.settings, peak_limit=8)),
        )
        for variant in variants:
            with self.subTest(frame=variant), self.assertRaises(Conflict):
                archive_frame(station, variant, "east")
        self.assertEqual(ReceiptRegistry(station).by_frame(frame.frame_key), receipt)
        self.assertEqual(lookup_receipt(station, receipt.receipt_id).frame, frame)
        self.assertEqual(station.volumes.available_words("east"), 4)

    def test_previous_receipt_cannot_clear_new_frame(self):
        station = self.open_station()
        first, old_receipt = self.archive_and_release(station, "D01", (4, 9, 2))
        second = self.frame("D02", (4, 9, 2))
        self.assertEqual(second.origin.slot_id, first.origin.slot_id)
        self.assertEqual(second.origin.generation, 2)
        with self.assertRaises(ReleaseDenied):
            release_frame(self.slots, station, old_receipt.receipt_id)
        self.assertEqual(read_frozen_frame(self.slots, second.frame_key), second)
        next_receipt = archive_frame(station, second, "middle")
        release_frame(self.slots, station, next_receipt.receipt_id)
        with self.assertRaises(NotFound):
            read_frozen_frame(self.slots, second.frame_key)

    def test_uncommitted_receipt(self):
        frame = self.frame("D01", (4, 9, 2, 7))
        attempted = []

        def try_before_commit():
            object_id, = station.volumes.medium("east").candidate_ids()
            candidate_receipt = f"whole-{object_id}"
            attempted.append(candidate_receipt)
            self.assertIsNone(ReceiptRegistry(station).get_committed(candidate_receipt))
            with self.assertRaises(ReleaseDenied):
                release_frame(self.slots, station, candidate_receipt)
            self.assertEqual(read_frozen_frame(self.slots, frame.frame_key), frame)

        station = self.open_station(before_archive_commit=try_before_commit)
        with self.assertRaises(ReleaseDenied):
            release_frame(self.slots, station, "whole-" + "0" * 32)
        receipt = archive_frame(station, frame, "east")
        self.assertEqual(attempted, [receipt.receipt_id])
        release_frame(self.slots, station, receipt.receipt_id)
        with self.assertRaises(NotFound):
            read_frozen_frame(self.slots, frame.frame_key)
